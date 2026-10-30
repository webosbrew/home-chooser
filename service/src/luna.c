/* SPDX-License-Identifier: Apache-2.0 */
#include "json.h"
#include "log.h"
#include "luna.h"
#include "policy.h"
#include "proc.h"

void call_start(const char *uri, const char *payload, void (*done)(Call *, jvalue_ref), void *ctx)
{
	Call c = {.ctx = ctx};
	GString *out = g_string_new(NULL);
	int code = luna_run(uri, payload, 1, CALL_TIMEOUT_S, NULL, out);
	c.status = code;
	jvalue_ref parsed = code == 0 ? parse_object(out->str) : NULL;
	if (!parsed) LOGE("CALL_FAILED", "%s (exit %d)", uri, code);
	done(&c, parsed);
	j_release(&parsed);
	g_string_free(out, TRUE);
}

/* A blocking luna-send as HC_LUNA_IDENTITY. Returns the exit status (-2 timeout, -1 spawn failure,
 * 126 uri not on the list). stdout is the reply text. */
int luna_run(const char *uri, const char *payload, int replies, int timeout_s, int (*done)(const char *), GString *out)
{
	const char *argv[8];
	GString *err = g_string_new(NULL);
	if (!hc_luna_argv(uri, payload, replies, argv)) {
		g_string_free(err, TRUE);
		return 126;
	}
	LOGI("RUN", "luna-send -a %s %s", HC_LUNA_IDENTITY, uri);
	int code = run_argv(argv, timeout_s, out, err, done);
	/* JSON replies retain UTF-8 titles and paths. */
	if (err->len) LOGI("RUN_STDERR", "%s: %s", uri, err->str);
	g_string_free(err, TRUE);
	return code;
}

struct HCLuna { LSHandle *handle; GMainLoop *loop; gboolean disconnected; };
struct HCSubscription {
    HCLuna *client;
    LSMessageToken token;
    HCSubscriptionReply reply;
    void *context;
    gboolean ended;
};

static void disconnected(LSHandle *handle, void *context)
{
    (void)handle;
    HCLuna *c = context;
    c->disconnected = TRUE;
    /* A disconnected client cannot recover its settings subscription. */
    g_main_loop_quit(c->loop);
}

HCLuna *hc_luna_open(GMainLoop *loop)
{
    HCLuna *c = g_new0(HCLuna, 1);
    c->loop = loop;
    LSError e; LSErrorInit(&e);
    if (!LSRegisterApplicationService(NULL, HC_LUNA_IDENTITY, &c->handle, &e) ||
        !LSGmainAttach(c->handle, loop, &e) ||
        !LSSetDisconnectHandler(c->handle, disconnected, c, &e)) {
        LOGE("WATCH_LUNA", "%s", e.message ? e.message : "client registration failed");
        LSErrorFree(&e);
        hc_luna_close(c);
        return NULL;
    }
    return c;
}

void hc_luna_close(HCLuna *c)
{
    if (!c) return;
    LSError e; LSErrorInit(&e);
    if (c->handle) LSUnregister(c->handle, &e);
    LSErrorFree(&e);
    g_free(c);
}

typedef struct { GMainLoop *loop; char *text; } ClientCall;
static bool client_reply(LSHandle *handle, LSMessage *message, void *context)
{
    (void)handle;
    ClientCall *call = context;
    call->text = g_strdup(LSMessageGetPayload(message));
    g_main_loop_quit(call->loop);
    return true;
}
static gboolean client_timeout(gpointer context)
{
    ClientCall *call = context;
    g_main_loop_quit(call->loop);
    return G_SOURCE_REMOVE;
}

char *hc_luna_call(const char *uri, const char *payload, int timeout_s, void *context)
{
    HCLuna *c = context;
    if (c->disconnected || !hc_luna_uri_allowed(uri)) return NULL;
    ClientCall call = {g_main_loop_new(NULL, FALSE), NULL};
    LSError e; LSErrorInit(&e);
    LSMessageToken token = LSMESSAGE_TOKEN_INVALID;
    if (LSCallOneReply(c->handle, uri, payload, client_reply, &call, &token, &e)) {
        /* Own the deadline locally: even a hub which never answers is bounded. */
        GSource *deadline = g_timeout_source_new(timeout_s * 1000);
        g_source_set_callback(deadline, client_timeout, &call, NULL);
        g_source_attach(deadline, NULL);
        g_main_loop_run(call.loop);
        g_source_destroy(deadline); g_source_unref(deadline);
        LSCallCancel(c->handle, token, &e);
    }
    LSErrorFree(&e);
    g_main_loop_unref(call.loop);
    return call.text;
}

static bool subscription_reply(LSHandle *handle, LSMessage *message, void *context)
{
    (void)handle;
    HCSubscription *s = context;
    const char *text = LSMessageGetPayload(message);
    gboolean ended = LSMessageIsHubErrorMessage(message);
    jvalue_ref r = parse_object(text);
    bool subscribed;
    if (r && jboolean_get(jobject_get(r, key_buf("subscribed")), &subscribed) == CONV_OK && !subscribed)
        ended = TRUE;
    j_release(&r);
    if (!s->ended) s->reply(text, ended, s->context);
    s->ended = ended;
    return true;
}

HCSubscription *hc_luna_subscribe(HCLuna *c, const char *uri, const char *payload,
    HCSubscriptionReply reply, void *context)
{
    if (!hc_luna_uri_allowed(uri)) return NULL;
    HCSubscription *s = g_new0(HCSubscription, 1);
    s->client = c; s->reply = reply; s->context = context;
    LSError e; LSErrorInit(&e);
    gboolean ok = LSCall(c->handle, uri, payload, subscription_reply, s, &s->token, &e);
    if (!ok) LOGE("WATCH_SUBSCRIBE", "%s: %s", uri, e.message ? e.message : "failed");
    LSErrorFree(&e);
    if (!ok) { g_free(s); return NULL; }
    return s;
}

void hc_luna_cancel(HCSubscription *s)
{
    if (!s) return;
    LSError e; LSErrorInit(&e);
    LSCallCancel(s->client->handle, s->token, &e);
    LSErrorFree(&e);
    g_free(s);
}
