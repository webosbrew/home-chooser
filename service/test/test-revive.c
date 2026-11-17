/* SPDX-License-Identifier: Apache-2.0 */
/* Watcher revival: rate limit, the should-run condition, root only, no double start, active and inactive paths. */
#include <stdio.h>
#include <string.h>
#include "revive.h"

static int failures;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); failures++; } } while (0)

typedef struct {
	long now;
	bool root, stored;
	char ran[8][16];
	int runs;
	void (*pending)(void *, int);
	void *pending_self;
	int revived, failed, failed_code;
} Fake;

static long f_now(void *c) { return ((Fake *)c)->now; }
static bool f_root(void *c) { return ((Fake *)c)->root; }
static bool f_stored(void *c) { return ((Fake *)c)->stored; }
static void f_run(void *c, const char *cmd, void (*done)(void *, int), void *self)
{
	Fake *f = c;
	snprintf(f->ran[f->runs++], sizeof f->ran[0], "%s", cmd);
	f->pending = done;
	f->pending_self = self;
}
static void f_revived(void *c) { ((Fake *)c)->revived++; }
static void f_failed(void *c, int code) { Fake *f = c; f->failed++; f->failed_code = code; }

/* The command finishes: its status goes to the callback, from the main loop in the real environment. */
static void finish(Fake *f, int code)
{
	void (*d)(void *, int) = f->pending;
	f->pending = NULL;
	d(f->pending_self, code);
}

static void setup(Fake *f, hc_revive_env *env, hc_revive *r)
{
	memset(f, 0, sizeof *f);
	f->root = f->stored = true;
	f->now = 1000;
	*env = (hc_revive_env){f_now, f_root, f_stored, f_run, f_revived, f_failed, f};
	hc_revive_init(r, env);
}

int main(void)
{
	Fake f;
	hc_revive_env env;
	hc_revive r;

	/* up: one active check, nothing else, nothing logged */
	setup(&f, &env, &r);
	hc_revive_touch(&r);
	CHECK(f.runs == 1 && !strcmp(f.ran[0], "active"));
	finish(&f, 0);
	CHECK(f.runs == 1 && f.revived == 0 && f.failed == 0);

	/* down: active says no (3), the start runs, success is logged once */
	setup(&f, &env, &r);
	hc_revive_touch(&r);
	finish(&f, 3);
	CHECK(f.runs == 2 && !strcmp(f.ran[1], "watch"));
	finish(&f, 0);
	CHECK(f.revived == 1 && f.failed == 0);

	/* a start that fails is reported with its status and is not retried before the interval */
	setup(&f, &env, &r);
	hc_revive_touch(&r);
	finish(&f, 3);
	finish(&f, 1);
	CHECK(f.revived == 0 && f.failed == 1 && f.failed_code == 1);
	hc_revive_touch(&r);
	CHECK(f.runs == 2);

	/* rate limit: one check per interval, then again after it */
	setup(&f, &env, &r);
	hc_revive_touch(&r);
	finish(&f, 0);
	for (int i = 0; i < 50; i++) { f.now++; if (f.now < 1000 + HC_REVIVE_INTERVAL_S) hc_revive_touch(&r); }
	CHECK(f.runs == 1);
	f.now = 1000 + HC_REVIVE_INTERVAL_S;
	hc_revive_touch(&r);
	CHECK(f.runs == 2 && !strcmp(f.ran[1], "active"));
	finish(&f, 0);

	/* no double start: while a command is in flight, calls past the interval start nothing */
	setup(&f, &env, &r);
	hc_revive_touch(&r);
	f.now += 1000;
	hc_revive_touch(&r);
	hc_revive_touch(&r);
	CHECK(f.runs == 1);
	finish(&f, 3);
	f.now += 1000;
	hc_revive_touch(&r);
	CHECK(f.runs == 2 && !strcmp(f.ran[1], "watch"));
	finish(&f, 0);
	CHECK(f.revived == 1);

	/* no stored choice: never a check, never a start (restoring or uninstalling removed it) */
	setup(&f, &env, &r);
	f.stored = false;
	hc_revive_touch(&r);
	CHECK(f.runs == 0);
	/* the choice removed while active was running: the start is dropped */
	setup(&f, &env, &r);
	hc_revive_touch(&r);
	f.stored = false;
	finish(&f, 3);
	CHECK(f.runs == 1 && f.revived == 0);

	/* not root: silent, and the interval is not spent */
	setup(&f, &env, &r);
	f.root = false;
	hc_revive_touch(&r);
	CHECK(f.runs == 0);
	f.root = true;
	hc_revive_touch(&r);
	CHECK(f.runs == 1);
	finish(&f, 0);

	/* a hold (stopWatcher) stops revival for its time and abandons a check in flight */
	setup(&f, &env, &r);
	hc_revive_touch(&r);
	hc_revive_hold(&r, 60);
	finish(&f, 3);
	CHECK(f.runs == 1 && f.revived == 0);
	f.now += 59;
	hc_revive_touch(&r);
	CHECK(f.runs == 1);
	f.now += 1;
	hc_revive_touch(&r);
	CHECK(f.runs == 2);
	finish(&f, 0);

	if (!failures) puts("test-revive: all checks passed");
	return failures != 0;
}
