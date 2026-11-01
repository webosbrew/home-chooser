/* SPDX-License-Identifier: Apache-2.0 */
/* Subscriptions of listApps, listLaunchPoints, listInputs, listRunning and getForegroundApp.
 *
 * Per method, one upstream luna-send -i child (luna-child.c) subscribes to the platform while the method
 * has a subscriber; it starts with the first subscriber and is killed after the last one cancels. The
 * upstream's replies are used only as a signal: each change (debounced) starts one fresh, non-blocking
 * read of the whole state (luna-send -n 1), and that full reply goes to every subscriber through the
 * hub's subscription catalog. Nothing is stored between replies: a new subscriber's first reply is its
 * own fresh read (catalog.c). A dead upstream is restarted with a doubling backoff (1 s to 60 s) and its
 * first reply after a restart counts as a change, since something may have changed while it was down. */
#include <string.h>
#include "json.h"
#include "log.h"
#include "luna.h"
#include "service/catalog.h"

#define DEBOUNCE_MS 200
#define BACKOFF_MAX_S 60

typedef struct {
	HCListKind kind;
	char key[48];
	HCSubscription *up;   /* the upstream child */
	bool primed;          /* its first reply since it started has arrived */
	bool restarted;       /* it was restarted: its first reply is a change */
	unsigned backoff_s;
	guint retry;          /* upstream restart */
	guint debounce;       /* pending read */
	HCSubscription *read; /* read in flight */
	guint read_timer;
	bool read_ended;
	bool dirty;           /* a change arrived while a read was in flight */
	char *text;           /* the read's reply */
} Source;

static Source g_src[HC_LIST_COUNT];
static HCLuna *g_luna;
static char *g_program;
static LSHandle *g_sh;
static guint g_check;

static Source *source(HCListKind kind)
{
	Source *s = &g_src[kind];
	if (!s->key[0]) {
		s->kind = kind;
		s->backoff_s = 1;
		g_snprintf(s->key, sizeof s->key, "launcher/%s", list_method(kind));
	}
	return s;
}

unsigned subs_count(LSHandle *sh, HCListKind kind)
{
	return LSSubscriptionGetHandleSubscribersCount(sh ? sh : g_sh, source(kind)->key);
}

bool subs_upstream_running(HCListKind kind)
{
	return source(kind)->up != NULL;
}

bool subs_room(LSHandle *sh, HCListKind kind)
{
	return subs_count(sh, kind) < HC_SUBSCRIBERS_MAX;
}

void subs_set_program(const char *program)
{
	g_free(g_program);
	g_program = g_strdup(program);
}

static void start_upstream(Source *s);
static void schedule_read(Source *s, unsigned delay_ms);

/* A child that has ended is freed from an idle callback, never inside its own reply callback. */
static gboolean free_child(gpointer data)
{
	hc_luna_cancel(data);
	return G_SOURCE_REMOVE;
}

static gboolean restart_upstream(gpointer data)
{
	Source *s = data;
	s->retry = 0;
	s->restarted = true;
	start_upstream(s);
	return G_SOURCE_REMOVE;
}

static void schedule_restart(Source *s)
{
	if (s->retry || subs_count(NULL, s->kind) == 0) return;
	LOGI("SUBSCRIBE_RETRY", "%s: upstream restart in %u s", s->key, s->backoff_s);
	s->retry = g_timeout_add_seconds(s->backoff_s, restart_upstream, s);
	s->backoff_s = MIN(s->backoff_s * 2, BACKOFF_MAX_S);
}

static void upstream_reply(const char *text, gboolean ended, void *context)
{
	Source *s = context;
	if (ended) {
		LOGE("SUBSCRIBE_LOST", "%s: the upstream subscription ended", s->key);
		g_idle_add(free_child, s->up);
		s->up = NULL;
		s->primed = false;
		schedule_restart(s);
		return;
	}
	jvalue_ref reply = parse_object(text);
	bool ok = reply && reply_ok(reply);
	j_release(&reply);
	if (!ok) return;
	if (!s->primed) {
		s->primed = true;
		if (!s->restarted) return; /* the state its subscribers were just given */
	}
	schedule_read(s, DEBOUNCE_MS);
}

static void start_upstream(Source *s)
{
	if (s->up) return;
	if (!g_luna) g_luna = hc_luna_open(NULL, g_program);
	char *payload = list_payload(s->kind, true);
	s->up = g_luna ? hc_luna_subscribe(g_luna, list_uri(s->kind), payload, upstream_reply, s) : NULL;
	g_free(payload);
	s->primed = false;
	if (s->up) LOGI("SUBSCRIBE_UP", "%s: upstream %s", s->key, list_uri(s->kind));
	else schedule_restart(s);
}

/* ---- the fresh read after a change ---- */

static void read_finish(Source *s, bool timed_out)
{
	if (s->read_timer) g_source_remove(s->read_timer);
	s->read_timer = 0;
	hc_luna_cancel(s->read);
	s->read = NULL;
	s->read_ended = false;
	jvalue_ref reply = !timed_out ? parse_object(s->text) : NULL;
	jvalue_ref out = reply && reply_ok(reply) ? list_reply(s->kind, reply, true) : NULL;
	g_clear_pointer(&s->text, g_free);
	j_release(&reply);
	if (!out) {
		LOGE("SUBSCRIBE_READ", "%s: %s", s->key, timed_out ? "the read timed out" : "no usable list");
		schedule_read(s, s->backoff_s * 1000);
		s->backoff_s = MIN(s->backoff_s * 2, BACKOFF_MAX_S);
		return;
	}
	s->backoff_s = 1;
	LSError e;
	LSErrorInit(&e);
	if (!LSSubscriptionReply(g_sh, s->key, jvalue_stringify(out), &e)) {
		LOGE("REPLY_FAILED", "%s: %s", s->key, e.message ? e.message : "?");
		LSErrorFree(&e);
	}
	j_release(&out);
	if (s->dirty) {
		s->dirty = false;
		schedule_read(s, DEBOUNCE_MS);
	}
}

static gboolean read_done(gpointer data)
{
	Source *s = data;
	if (s->read_ended) read_finish(s, false); /* not when stop() came first */
	return G_SOURCE_REMOVE;
}

static void read_reply(const char *text, gboolean ended, void *context)
{
	Source *s = context;
	if (!ended) {
		if (!s->text) s->text = g_strdup(text);
		return;
	}
	if (s->read_ended) return;
	s->read_ended = true;
	if (s->read_timer) g_source_remove(s->read_timer);
	s->read_timer = 0;
	g_idle_add(read_done, s);
}

static gboolean read_timeout(gpointer data)
{
	Source *s = data;
	s->read_timer = 0;
	read_finish(s, true);
	return G_SOURCE_REMOVE;
}

static gboolean read_start(gpointer data)
{
	Source *s = data;
	s->debounce = 0;
	if (s->read) {
		s->dirty = true;
		return G_SOURCE_REMOVE;
	}
	char *payload = list_payload(s->kind, false);
	s->read = g_luna ? hc_luna_once(g_luna, list_uri(s->kind), payload, read_reply, s) : NULL;
	g_free(payload);
	if (s->read) {
		s->read_timer = g_timeout_add_seconds(CALL_TIMEOUT_S, read_timeout, s);
	} else {
		LOGE("SUBSCRIBE_READ", "%s: could not start the read", s->key);
		schedule_read(s, s->backoff_s * 1000);
		s->backoff_s = MIN(s->backoff_s * 2, BACKOFF_MAX_S);
	}
	return G_SOURCE_REMOVE;
}

static void schedule_read(Source *s, unsigned delay_ms)
{
	if (s->debounce || subs_count(NULL, s->kind) == 0) return;
	s->debounce = g_timeout_add(delay_ms, read_start, s);
}

/* ---- subscribers ---- */

static void stop(Source *s)
{
	if (s->up) LOGI("SUBSCRIBE_DOWN", "%s: no subscribers", s->key);
	if (s->up) hc_luna_cancel(s->up);
	s->up = NULL;
	if (s->read) hc_luna_cancel(s->read);
	s->read = NULL;
	guint *timers[] = {&s->retry, &s->debounce, &s->read_timer};
	for (unsigned i = 0; i < G_N_ELEMENTS(timers); i++) {
		if (*timers[i]) g_source_remove(*timers[i]);
		*timers[i] = 0;
	}
	g_clear_pointer(&s->text, g_free);
	s->primed = s->restarted = s->dirty = s->read_ended = false;
	s->backoff_s = 1;
}

static gboolean check_subscribers(gpointer data)
{
	(void)data;
	g_check = 0;
	for (int k = 0; k < HC_LIST_COUNT; k++) {
		Source *s = source((HCListKind)k);
		if (subs_count(NULL, s->kind) == 0 && (s->up || s->retry || s->read || s->debounce)) stop(s);
	}
	return G_SOURCE_REMOVE;
}

/* The hub calls this for a cancelled subscription and for a subscriber that went away. */
static bool on_cancel(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)msg; (void)ctx;
	if (!g_check) g_check = g_idle_add(check_subscribers, NULL);
	return true;
}

void subs_init(LSHandle *sh)
{
	g_sh = sh;
	LSError e;
	LSErrorInit(&e);
	if (!LSSubscriptionSetCancelFunction(sh, on_cancel, NULL, &e)) {
		LOGE("REGISTER_FAILED", "subscription cancel: %s", e.message ? e.message : "?");
		LSErrorFree(&e);
	}
}

void subs_add(LSHandle *sh, HCListKind kind, LSMessage *msg)
{
	Source *s = source(kind);
	if (sh) g_sh = sh;
	LSError e;
	LSErrorInit(&e);
	if (!LSSubscriptionAdd(g_sh, s->key, msg, &e)) {
		LOGE("SUBSCRIBE_FAILED", "%s: %s", s->key, e.message ? e.message : "?");
		LSErrorFree(&e);
		return;
	}
	if (!s->up && !s->retry) start_upstream(s);
}

void subs_shutdown(void)
{
	for (int k = 0; k < HC_LIST_COUNT; k++) stop(source((HCListKind)k));
	if (g_check) g_source_remove(g_check);
	g_check = 0;
	hc_luna_close(g_luna);
	g_luna = NULL;
}
