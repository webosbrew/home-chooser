/* SPDX-License-Identifier: Apache-2.0 */
/* Watcher revival: on an incoming call, check now and then whether the watcher should run and does not, and
 * start it. The logic is plain C over an environment of callbacks, so the host tests fake the clock, the
 * stored choice and the commands; the real environment is service/revive-glue.c. */
#ifndef HC_REVIVE_H
#define HC_REVIVE_H
#include <stdbool.h>

/* At most one check per this many seconds in one service process. */
#define HC_REVIVE_INTERVAL_S 30

typedef struct {
	long (*now)(void *ctx);                 /* monotonic seconds */
	bool (*is_root)(void *ctx);
	bool (*choice_stored)(void *ctx);       /* the file the boot entry starts the watcher for */
	/* Runs the entry script's command ("active" or "watch") without waiting; done(code) is called later,
	 * from the main loop, with the exit status (negative when it could not run). */
	void (*run)(void *ctx, const char *command, void (*done)(void *self, int code), void *self);
	void (*revived)(void *ctx);             /* log: the watcher was down and is started */
	void (*failed)(void *ctx, int code);    /* log: the start did not work */
	void *ctx;
} hc_revive_env;

typedef struct {
	const hc_revive_env *env;
	long next_at;
	bool started;                           /* next_at is set */
	int phase;                              /* 0 idle, 1 asking, 2 starting */
	unsigned epoch, run_epoch;              /* a hold bumps epoch and abandons a check in flight */
} hc_revive;

void hc_revive_init(hc_revive *r, const hc_revive_env *env);
/* Called after every call that does real work; cheap, never blocks. */
void hc_revive_touch(hc_revive *r);
/* The app is stopping the watcher on purpose (a restore, an uninstall): no revival for the next seconds. */
void hc_revive_hold(hc_revive *r, int seconds);
#endif
