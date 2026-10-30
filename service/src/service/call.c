/* SPDX-License-Identifier: Apache-2.0 */
#include "apps.h"
#include "luna.h"
#include "json.h"
#include "log.h"
#include "policy.h"
#include "service/methods.h"
#include "service/reply.h"

typedef struct {
	LSMessage *msg;
	char *uri;
	char *icon_app;
} Pending;

static void pending_free(Pending *p)
{
	LSMessageUnref(p->msg);
	g_free(p->uri);
	g_free(p->icon_app);
	g_free(p);
}

static void forward_reply(Pending *p, jvalue_ref parsed)
{
	jvalue_ref o = jobject_create();
	put_bool(o, "returnValue", true);
	jobject_set(o, key_buf("reply"), jvalue_duplicate(parsed));
	send_reply(p->msg, o);
}

static void start_call(Pending *p, const char *payload, void (*cb)(Pending *, jvalue_ref))
{
	GString *out = g_string_new(NULL);
	int code = luna_run(p->uri, payload, 1, CALL_TIMEOUT_S, NULL, out);
	jvalue_ref parsed = code == 0 ? parse_object(out->str) : NULL;
	if (!parsed) {
		LOGE("CALL_FAILED", "%s (exit %d)", p->uri, code);
		send_error(p->msg, code == -2 ? "timeout" : "badReply", "No JSON reply from %s (exit %d)", p->uri, code);
	} else {
		cb(p, parsed);
	}
	j_release(&parsed);
	g_string_free(out, TRUE);
	pending_free(p);
}

/* The member names of an object / the strings of an array, up to max; returns the count or -1. */
static int collect_keys(jvalue_ref v, bool names, char **out, int max)
{
	int n = 0;
	if (names) {
		if (!jis_object(v)) return -1;
		jobject_iter it;
		jobject_key_value kv;
		jobject_iter_init(&it, v);
		while (jobject_iter_next(&it, &kv)) {
			if (n >= max) return -1;
			raw_buffer b = jstring_get_fast(kv.key);
			out[n++] = g_strndup(b.m_str, b.m_len);
		}
	} else {
		if (!jis_array(v)) return -1;
		ssize_t len = jarray_size(v);
		for (ssize_t i = 0; i < len; i++) {
			jvalue_ref s = jarray_get(v, i);
			if (n >= max || !jis_string(s)) return -1;
			raw_buffer b = jstring_get_fast(s);
			out[n++] = g_strndup(b.m_str, b.m_len);
		}
	}
	return n;
}

static bool settings_payload_ok(hc_call_kind kind, jvalue_ref payload)
{
	char *category = get_string(payload, "category");
	char *keys[8] = {0};
	jvalue_ref list = jobject_get(payload, key_buf(kind == HC_CALL_SETTINGS_GET ? "keys" : "settings"));
	int n = jis_valid(list) ? collect_keys(list, kind == HC_CALL_SETTINGS_SET, keys, 8) : -1;
	bool ok = n >= 0 && hc_settings_ok(category, (const char *const *)keys, (size_t)n);
	for (int i = 0; i < 8; i++) g_free(keys[i]);
	g_free(category);
	return ok;
}

bool on_call(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	if (!gate(msg, "call", true)) return true;
	jvalue_ref req = parse_object(LSMessageGetPayload(msg));
	char *uri = req ? get_string(req, "uri") : NULL;
	if (!uri) {
		send_error(msg, "badRequest", "call needs a uri");
		j_release(&req);
		return true;
	}
	hc_call_kind kind = hc_call_lookup(uri);
	jvalue_ref inner = jobject_get(req, key_buf("payload"));
	if (!jis_valid(inner) || jis_null(inner)) inner = NULL;
	if (kind == HC_CALL_DENIED) {
		LOGE("CALL_DENIED", "%s", uri);
		send_error(msg, "notAllowed", "%s is not on the list of calls this service makes", uri);
	} else if (inner && !jis_object(inner)) {
		send_error(msg, "badRequest", "payload must be an object");
	} else if ((kind == HC_CALL_SETTINGS_GET || kind == HC_CALL_SETTINGS_SET) && (!inner || !settings_payload_ok(kind, inner))) {
		LOGE("CALL_DENIED", "%s with a payload outside general/defaultApps", uri);
		send_error(msg, "notAllowed", "%s is limited to category general, key defaultApps", uri);
	} else {
		Pending *p = g_new0(Pending, 1);
		p->msg = msg;
		LSMessageRef(msg);
		p->uri = uri;
		uri = NULL;
		LOGI("CALL_FORWARD", "%s", p->uri);
		start_call(p, inner ? jvalue_stringify(inner) : "{}", forward_reply);
	}
	g_free(uri);
	j_release(&req);
	return true;
}

static void icon_reply(Pending *p, jvalue_ref info)
{
	char *buf = NULL;
	size_t n = 0;
	const char *mime = NULL;
	IconResult result = load_app_icon(info, "icon", ICON_MAX_BYTES, &buf, &n, &mime);
	if (result == ICON_NONE) {
		LOGI("ICON_NONE", "%s", p->icon_app);
		send_error(p->msg, "noIcon", "No readable icon for %s", p->icon_app);
	} else if (result == ICON_TOO_BIG) {
		send_error(p->msg, "tooBig", "Could not read the icon of %s", p->icon_app);
	} else {
		char *b64 = icon_base64(buf, n);
		jvalue_ref o = jobject_create();
		put_bool(o, "returnValue", true);
		put_str(o, "mime", mime);
		put_str(o, "data", b64);
		send_reply(p->msg, o);
		g_free(b64);
	}
	g_free(buf);
}

bool on_read_icon(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	if (!gate(msg, "readIcon", true)) return true;
	jvalue_ref req = parse_object(LSMessageGetPayload(msg));
	char *app = req ? get_string(req, "appId") : NULL;
	if (!hc_app_id_ok(app)) {
		send_error(msg, "badRequest", "readIcon needs an appId");
	} else {
		Pending *p = g_new0(Pending, 1);
		p->msg = msg;
		LSMessageRef(msg);
		p->uri = g_strdup("luna://com.webos.applicationManager/getAppInfo");
		p->icon_app = app;
		app = NULL;
		jvalue_ref q = jobject_create();
		jobject_set(q, key_buf("id"), jstring_create(p->icon_app));
		start_call(p, jvalue_stringify(q), icon_reply);
		j_release(&q);
	}
	g_free(app);
	j_release(&req);
	return true;
}
