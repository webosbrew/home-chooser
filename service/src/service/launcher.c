/* SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "apps.h"
#include "fs.h"
#include "luna.h"
#include "json.h"
#include "log.h"
#include "policy.h"
#include "nameowner.h"
#include "service/methods.h"
#include "service/launcher.h"
#include "service/reply.h"

/* ---- the launcher API (docs/launcher-api.md) ----
 * The launcher methods below are for apps that declare the permission group,
 * and uninstallConfirmed for the confirmation. The caller is the application id the hub reports. */


#define TOKEN_TTL_US (120 * G_USEC_PER_SEC)
#define TOKEN_SLOTS 4

typedef struct {
	char token[HC_TOKEN_LEN + 1];
	char id[130];
	char requester[130];
	char folder[512];
	gint64 expires;
	bool live;
} UninstallToken;

static UninstallToken g_tokens[TOKEN_SLOTS];
static int g_token_next;

void op_free(LOp *op)
{
	LSMessageUnref(op->msg);
	j_release(&op->items);
	g_free(op->target);
	g_free(op->target_title);
	g_free(op->target_folder);
	g_free(op->lookup_text);
	g_free(op);
}

/* Who is calling. An app's calls carry its application id. A native launcher's do not (the hub stamps
 * an id only on calls from the web, QML and node containers), so it is identified by the service name
 * its own handle is registered under, and only when that name is the id of an app that the platform
 * installed as a native home-category app and whose own executable alone may register that name
 * (nameowner.c). The hub's answer about the app comes later, in consent_done. */
typedef enum { WHO_APP, WHO_NATIVE, WHO_REFUSED } Who;

static Who launcher_who(LSMessage *msg, const char *method, char *caller, size_t callerlen)
{
	const char *app = LSMessageGetApplicationID(msg);
	if (hc_caller_id(app, caller, callerlen)) return WHO_APP;
	const char *svc = LSMessageGetSenderServiceName(msg);
	if (!app || !app[0]) {
		if (svc && svc[0] && hc_app_id_ok(svc) && strlen(svc) < callerlen) {
			char why[256];
			if (hc_name_owned(hc_fs_prefix(), svc, why, sizeof why)) {
				g_strlcpy(caller, svc, callerlen);
				return WHO_NATIVE;
			}
			LOGE("REFUSED", "%s from service name '%s' with no application id: not a native launcher's own name: %s", method, svc, why);
		} else {
			LOGE("REFUSED", "%s from application id '(none)', service name '%s'", method, svc ? svc : "(none)");
		}
		send_error_for(msg, method, "forbidden", "This method is for apps; the caller has no application id");
		return WHO_REFUSED;
	}
	LOGE("REFUSED", "%s from application id '%s', service name '%s'", method, app, svc ? svc : "(none)");
	send_error_for(msg, method, "forbidden", "The caller has a malformed application id");
	return WHO_REFUSED;
}

/* Root and (need_home) the caller being the selected home. Replies and returns false on refusal. */
static bool launcher_policy(LSMessage *msg, const char *method, bool need_root, bool need_home, const char *caller, bool native)
{
	if (need_root && geteuid() != 0) {
		LOGI("NOT_ROOT", "%s refused: uid %d", method, (int)geteuid());
		send_error_for(msg, method, "notRoot", "The service is not running as root; open Home Chooser once to set it up");
		return false;
	}
	if (need_home) {
		char *choice = read_choice();
		bool home = hc_is_selected_home(caller, choice);
		g_free(choice);
		if (!home) {
			LOGE("REFUSED", "%s from %s, which is not the selected home", method, caller);
			send_error_for(msg, method, "notHome", "Only the app selected as Home may call this method");
			return false;
		}
	}
	LOGI("CALL", "%s from %s (%s)", method, caller, native ? "native service name" : "application id");
	return true;
}

/* A native caller's eligibility, from the platform's appinfo of the app with that id. Replies on refusal. */
static bool native_eligible(LOp *op, jvalue_ref info)
{
	char *type = info && jis_object(info) ? get_string(info, "type") : NULL;
	char *folder = info && jis_object(info) ? get_string(info, "folderPath") : NULL;
	char expected[256];
	g_snprintf(expected, sizeof expected, "%s%s", HC_DEV_APPS_DIR, op->caller);
	bool home = false;
	jvalue_ref cats = info && jis_object(info) ? jobject_get(info, key_buf("appCategory")) : NULL;
	if (cats && jis_valid(cats) && jis_array(cats)) {
		for (ssize_t i = 0; i < jarray_size(cats) && i < 64; i++) {
			jvalue_ref c = jarray_get(cats, i);
			if (!jis_string(c)) continue;
			raw_buffer b = jstring_get_fast(c);
			if (b.m_len == 4 && memcmp(b.m_str, "home", 4) == 0) home = true;
		}
	}
	const char *why = NULL;
	if (!info || !jis_object(info)) why = "the platform has no appinfo for it";
	else if (!type || strcmp(type, "native") != 0) why = "its type is not native";
	else if (!home) why = "its appCategory does not contain home";
	else if (!folder || strcmp(folder, expected) != 0) why = "its folder is not the developer apps folder of that id";
	bool ok = !why;
	if (!ok) {
		LOGE("REFUSED", "%s from service name '%s' with no application id: %s", op->method, op->caller, why);
		send_error_for(op->msg, op->method, "forbidden", "This method is for apps; the caller has no application id");
	}
	g_free(type);
	g_free(folder);
	return ok;
}

static void consent_done(Call *c, jvalue_ref reply)
{
	LOp *op = c->ctx;
	if (op->native) {
		jvalue_ref ni = reply && reply_ok(reply) ? jobject_get(reply, key_buf("appInfo")) : NULL;
		if (!ni || !jis_valid(ni) || !jis_object(ni)) ni = NULL;
		if (!native_eligible(op, ni) || !launcher_policy(op->msg, op->method, op->need_root, op->need_home, op->caller, true)) {
			op_free(op);
			return;
		}
	}
	if (op->need_consent && (!reply || !reply_ok(reply))) {
		char *sam_text = reply ? get_string(reply, "errorText") : NULL;
		const char *code = !reply ? hc_unusable_reply_code(c->status) : "lookupFailed";
		char *message = g_strdup_printf("Could not read the caller's appinfo%s%s", sam_text ? ": " : "", sam_text ? sam_text : "");
		send_error_for(op->msg, op->method, code, message);
		g_free(message);
		g_free(sam_text);
		op_free(op);
		return;
	}
	jvalue_ref info = reply ? jobject_get(reply, key_buf("appInfo")) : NULL;
	if (!info || !jis_valid(info) || !jis_object(info)) info = reply;
	jvalue_ref perms = info ? jobject_get(info, key_buf("requiredPermissions")) : NULL;
	char *folder = info ? get_string(info, "folderPath") : NULL;
	char *file = NULL;
	jvalue_ref file_json = NULL;
	bool appinfo_unreadable = false;
	if ((!perms || !jis_valid(perms) || !jis_array(perms)) && folder && folder[0] == '/' && !strstr(folder, "..")) {
		/* getAppInfo did not carry the appinfo's own fields: read the file the app was installed with */
		char *path = g_strdup_printf("%s/appinfo.json", folder);
		ssize_t n = slurp(path, 65536, &file, NULL);
		g_free(path);
		file_json = n >= 0 ? parse_object(file) : NULL;
		appinfo_unreadable = !file_json;
		perms = file_json ? jobject_get(file_json, key_buf("requiredPermissions")) : NULL;
	} else if (!perms || !jis_valid(perms) || !jis_array(perms)) {
		appinfo_unreadable = true;
	}
	if (op->need_consent && appinfo_unreadable) {
		send_error_for(op->msg, op->method, "lookupFailed", "Could not read the caller's installed appinfo.json");
		j_release(&file_json);
		g_free(file);
		g_free(folder);
		op_free(op);
		return;
	}
	GPtrArray *list = g_ptr_array_new_with_free_func(g_free);
	if (perms && jis_valid(perms) && jis_array(perms)) {
		ssize_t len = jarray_size(perms);
		for (ssize_t i = 0; i < len && i < 256; i++) {
			jvalue_ref s = jarray_get(perms, i);
			if (!jis_string(s)) continue;
			raw_buffer b = jstring_get_fast(s);
			g_ptr_array_add(list, g_strndup(b.m_str, b.m_len));
		}
	}
	op->consent = hc_perms_grant((const char *const *)list->pdata, list->len);
	g_ptr_array_free(list, TRUE);
	j_release(&file_json);
	g_free(file);
	g_free(folder);
	if (op->need_consent && !op->consent) {
		LOGE("REFUSED", "%s from %s, whose appinfo does not request %s", op->method, op->caller, HC_GROUP);
		char *message = g_strdup_printf("The app's appinfo.json must list \"%s\" in requiredPermissions", HC_GROUP);
		send_error_for(op->msg, op->method, "noPermission", message);
		g_free(message);
		op_free(op);
		return;
	}
	op->next(op);
}

void launcher_consent(LOp *op)
{
	jvalue_ref q = jobject_create();
	put_str(q, "id", op->caller);
	call_start(SAM_URI "/getAppInfo", jvalue_stringify(q), consent_done, op);
	j_release(&q);
}

LOp *launcher_open(LSHandle *sh, LSMessage *msg, const char *method, bool need_root, bool need_home,
	bool need_consent, void (*next)(LOp *))
{
	char caller[130];
	Who who = launcher_who(msg, method, caller, sizeof caller);
	if (who == WHO_REFUSED) return NULL;
	if (who == WHO_APP && !launcher_policy(msg, method, need_root, need_home, caller, false)) return NULL;
	LOp *op = g_new0(LOp, 1);
	op->native = who == WHO_NATIVE;
	op->need_root = need_root;
	op->need_home = need_home;
	op->msg = msg;
	LSMessageRef(msg);
	op->sh = sh;
	op->method = method;
	op->next = next;
	op->need_consent = need_consent;
	op->target_removable = -1;
	g_strlcpy(op->caller, caller, sizeof op->caller);
	return op;
}

bool wants_subscribe(LSMessage *msg)
{
	jvalue_ref req = parse_object(LSMessageGetPayload(msg));
	jvalue_ref v = req ? jobject_get(req, key_buf("subscribe")) : NULL;
	bool on = false;
	if (v && jis_valid(v) && jis_boolean(v)) jboolean_get(v, &on);
	j_release(&req);
	return on;
}

bool platform_failure(LOp *op, Call *c, jvalue_ref reply, const char *who)
{
	if (reply && reply_ok(reply)) return false;
	char *text = reply ? get_string(reply, "errorText") : NULL;
	const char *code = !reply ? hc_unusable_reply_code(c->status) : hc_sam_refusal_code(false, text);
	char *message = g_strdup_printf(reply ? "The %s refused the call%s%s" : "No reply from the %s%s%s", who,
		text ? ": " : "", text ? text : "");
	send_error_for(op->msg, op->method, code, message);
	g_free(message);
	g_free(text);
	return true;
}

/* Starts a launcher request: gate, optional id parameter, then the consent check. */
static void launcher_begin(LSMessage *msg, const char *method, bool need_root, bool need_home, bool need_consent, bool need_id, void (*next)(LOp *))
{
	LOp *op = launcher_open(NULL, msg, method, need_root, need_home, need_consent, next);
	if (!op) return;
	if (need_id) {
		jvalue_ref req = parse_object(LSMessageGetPayload(msg));
		op->target = req ? get_string(req, "id") : NULL;
		jvalue_ref f = req && strcmp(method, "closeApp") == 0 ? jobject_get(req, key_buf("force")) : NULL;
		bool force = false;
		if (f && jis_valid(f) && jis_boolean(f)) jboolean_get(f, &force);
		op->force = force;
		if (strcmp(method, "getAppIcon") == 0) {
			jvalue_ref size = req ? jobject_get(req, key_buf("size")) : NULL;
			char *name = size && jis_valid(size) ? get_string(req, "size") : NULL;
			if (size && jis_valid(size) && (!name || (strcmp(name, "icon") && strcmp(name, "largeIcon")))) {
				send_error_for(msg, method, "badRequest", "size must be icon or largeIcon");
				g_free(name);
				j_release(&req);
				op_free(op);
				return;
			}
			op->icon_large = name && strcmp(name, "largeIcon") == 0;
			g_free(name);
		}
		j_release(&req);
		if (!hc_app_id_ok(op->target)) {
			send_error_for(msg, method, "badRequest", "This method needs an id: the app id");
			op_free(op);
			return;
		}
	}
	launcher_consent(op);
}

static bool sam_failure(LOp *op, Call *c, jvalue_ref reply, bool target_lookup)
{
	if (reply && reply_ok(reply)) return false;
	char *sam_text = reply ? get_string(reply, "errorText") : NULL;
	const char *code = !reply ? hc_unusable_reply_code(c->status) :
		hc_sam_refusal_code(target_lookup, sam_text);
	char *message = g_strdup_printf("%s%s%s", reply ? "The application manager refused the call" :
		"No reply from the application manager", sam_text ? ": " : "", sam_text ? sam_text : "");
	send_error_for(op->msg, op->method, code, message);
	g_free(message);
	g_free(sam_text);
	return true;
}

static void target_absence_done(Call *c, jvalue_ref reply)
{
	LOp *op = c->ctx;
	if (!sam_failure(op, c, reply, false)) {
		jvalue_ref apps = jobject_get(reply, key_buf("apps"));
		if (!apps || !jis_valid(apps) || !jis_array(apps)) {
			send_error_for(op->msg, op->method, "badReply", "The application manager did not return an app array");
		} else {
			bool present = false, usable = true;
			for (ssize_t i = 0; i < jarray_size(apps); i++) {
				jvalue_ref app = jarray_get(apps, i);
				char *id = jis_object(app) ? get_string(app, "id") : NULL;
				if (!id) usable = false;
				else if (strcmp(id, op->target) == 0) present = true;
				g_free(id);
			}
			if (!usable) {
				send_error_for(op->msg, op->method, "badReply", "The application manager returned an app without an id");
			} else {
				char *message = g_strdup_printf("%s: %s", present ?
					"The application manager refused the target lookup" : "No such app",
					op->lookup_text);
				send_error_for(op->msg, op->method, present ? "refused" : "notFound", message);
				g_free(message);
			}
		}
	}
	op_free(op);
}

/* SAM's combined missing/unsupported error needs a second lookup to tell the conditions apart. */
static bool check_ambiguous_target(LOp *op, jvalue_ref reply)
{
	if (!reply || reply_ok(reply)) return false;
	char *sam_text = get_string(reply, "errorText");
	if (!hc_sam_app_ambiguous(sam_text)) {
		g_free(sam_text);
		return false;
	}
	op->lookup_text = sam_text;
	call_start(SAM_URI "/listApps", "{\"properties\":[\"id\"]}", target_absence_done, op);
	return true;
}

/* listRunning and getForegroundApp are in catalog.c, with the other methods that subscribe. */

static void appinfo_done(Call *c, jvalue_ref reply)
{
	LOp *op = c->ctx;
	if (check_ambiguous_target(op, reply)) return;
	if (!sam_failure(op, c, reply, true)) {
		jvalue_ref info = appinfo_object(reply);
		if (!info || !jis_object(info)) {
			send_error_for(op->msg, op->method, "badReply", "The application manager returned no app info");
		} else if (strcmp(op->method, "getAppInfo") == 0) {
			jvalue_ref o = jobject_create();
			put_bool(o, "returnValue", true);
			put_int(o, "apiVersion", HC_API_VERSION);
			jobject_set(o, key_buf("app"), pick_app(info, op->target));
			send_reply(op->msg, o);
		} else {
			char *bytes = NULL;
			size_t length = 0;
			const char *mime = NULL;
			IconResult result = load_app_icon(reply, op->icon_large ? "largeIcon" : "icon",
				ICON_MAX_BYTES, &bytes, &length, &mime);
			if (result == ICON_NONE) {
				send_error_for(op->msg, op->method, "noIcon", "The app has no readable icon of the requested size");
			} else if (result == ICON_TOO_BIG) {
				send_error_for(op->msg, op->method, "iconTooLarge", "The icon exceeds 1048576 bytes");
			} else {
				char *data = icon_base64(bytes, length);
				jvalue_ref o = jobject_create();
				put_bool(o, "returnValue", true);
				put_int(o, "apiVersion", HC_API_VERSION);
				put_str(o, "mimeType", mime);
				put_str(o, "data", data);
				put_int(o, "byteLength", (int)length);
				send_reply(op->msg, o);
				g_free(data);
			}
			g_free(bytes);
		}
	}
	op_free(op);
}

static void appinfo_next(LOp *op)
{
	jvalue_ref q = jobject_create();
	put_str(q, "id", op->target);
	call_start(SAM_URI "/getAppInfo", jvalue_stringify(q), appinfo_done, op);
	j_release(&q);
}

bool on_launcher_get_app_info(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	launcher_begin(msg, "getAppInfo", true, false, true, true, appinfo_next);
	return true;
}

bool on_launcher_get_app_icon(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	launcher_begin(msg, "getAppIcon", true, false, true, true, appinfo_next);
	return true;
}

/* getInfo: for any app; says what the caller may do. */
static void info_next(LOp *op)
{
	char *choice = read_choice();
	jvalue_ref o = jobject_create();
	put_bool(o, "returnValue", true);
	put_int(o, "apiVersion", HC_API_VERSION);
	put_str(o, "version", HC_VERSION);
	put_bool(o, "root", geteuid() == 0);
	put_str(o, "permission", HC_GROUP);
	put_bool(o, "permitted", op->consent);
	put_bool(o, "isSelectedHome", hc_is_selected_home(op->caller, choice));
	send_reply(op->msg, o);
	g_free(choice);
	op_free(op);
}

bool on_launcher_get_info(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	launcher_begin(msg, "getInfo", false, false, false, false, info_next);
	return true;
}

/* closeApp */
static void close_done(Call *c, jvalue_ref reply)
{
	LOp *op = c->ctx;
	if (reply && reply_ok(reply)) {
		jvalue_ref o = jobject_create();
		put_bool(o, "returnValue", true);
		put_int(o, "apiVersion", HC_API_VERSION);
		put_str(o, "id", op->target);
		send_reply(op->msg, o);
	} else {
		char *sam_text = reply ? get_string(reply, "errorText") : NULL;
		const char *code = !reply ? hc_unusable_reply_code(c->status) : hc_sam_refusal_code(false, sam_text);
		char *message = g_strdup_printf("%s%s%s", reply ? "The application manager did not close the app" : "No reply from the application manager", sam_text ? ": " : "", sam_text ? sam_text : "");
		send_error_for(op->msg, op->method, code, message);
		g_free(message);
		g_free(sam_text);
	}
	op_free(op);
}

static void close_next(LOp *op)
{
	jvalue_ref q = jobject_create();
	put_str(q, "id", op->target);
	if (op->force) put_bool(q, "forceMode", true);
	call_start(SAM_URI "/closeByAppId", jvalue_stringify(q), close_done, op);
	j_release(&q);
}

bool on_launcher_close_by_id(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	launcher_begin(msg, "closeApp", true, true, true, true, close_next);
	return true;
}

/* uninstall: look the app up, apply the refusal list, then ask the user. */
static void new_token(char *out)
{
	unsigned char raw[HC_TOKEN_LEN / 2];
	int fd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
	size_t got = 0;
	while (fd >= 0 && got < sizeof raw) {
		ssize_t r = read(fd, raw + got, sizeof raw - got);
		if (r < 0 && errno == EINTR) continue;
		if (r <= 0) break;
		got += (size_t)r;
	}
	if (fd >= 0) close(fd);
	if (got < sizeof raw) { /* no entropy: do not hand out a guessable token */
		out[0] = '\0';
		return;
	}
	for (size_t i = 0; i < sizeof raw; i++) snprintf(out + 2 * i, 3, "%02x", raw[i]);
}

static int never_stop(const char *text) { (void)text; return 0; }

static void uninstall_ask(LOp *op)
{
	hc_remove_call remove;
	if (!hc_remove_step(1, op->target, op->target_folder, &remove)) {
		LOGE("REFUSED", "uninstallApp %s: no direct removal endpoint for %s", op->target,
			op->target_folder ? op->target_folder : "");
		send_error_for(op->msg, op->method, "protected", "No supported removal endpoint for this app");
		return;
	}

	/* The alert action goes to LG's endpoint chosen from the verified install folder. */
	char *question = g_strdup_printf("Uninstall %s?", op->target_title ? op->target_title : op->target);
	jvalue_ref a = jobject_create();
	put_str(a, "sourceId", HC_APP_ID);
	put_str(a, "title", "Home Chooser");
	put_str(a, "message", question);
	jvalue_ref buttons = jarray_create(NULL);
	jvalue_ref yes = jobject_create();
	put_str(yes, "label", "Uninstall");
	put_str(yes, "onclick", remove.uri);
	jvalue_ref yes_params = parse_object(remove.payload);
	jobject_set(yes, key_buf("params"), yes_params);
	jarray_append(buttons, yes);
	jvalue_ref no = jobject_create();
	put_str(no, "label", "Cancel");
	put_bool(no, "focus", true);
	jarray_append(buttons, no);
	jobject_set(a, key_buf("buttons"), buttons);
	GString *out = g_string_new(NULL);
	int code = luna_run("luna://com.webos.notification/createAlert", jvalue_stringify(a), 1, 8, never_stop, out);
	bool alert = code == 0 && hc_remove_reply_class(out->str) == 1;
	LOGI("UNINSTALL_ASK", "%s by %s: alert %s for %s (exit %d): %s", op->target, op->caller,
		alert ? "shown" : "refused", remove.uri, code, out->str);
	g_string_free(out, TRUE);
	j_release(&a);
	g_free(question);

	const char *how = "alert";
	if (!alert) {
		/* Alerts are refused to root on some releases: Home Chooser's own screen asks instead. */
		UninstallToken *t = &g_tokens[g_token_next++ % TOKEN_SLOTS];
		memset(t, 0, sizeof *t);
		new_token(t->token);
		if (!hc_token_ok(t->token)) {
			LOGE("REFUSED", "uninstallApp %s: could not make a confirmation token", op->target);
			send_error_for(op->msg, op->method, "internal", "Could not make a confirmation token");
			return;
		}
		g_strlcpy(t->id, op->target, sizeof t->id);
		g_strlcpy(t->requester, op->caller, sizeof t->requester);
		g_strlcpy(t->folder, op->target_folder ? op->target_folder : "", sizeof t->folder);
		t->expires = g_get_monotonic_time() + TOKEN_TTL_US;
		t->live = true;
		jvalue_ref l = jobject_create();
		put_str(l, "id", HC_APP_ID);
		jvalue_ref params = jobject_create();
		put_str(params, "confirmUninstall", op->target);
		put_str(params, "token", t->token);
		jobject_set(l, key_buf("params"), params);
		out = g_string_new(NULL);
		code = luna_run("luna://com.webos.applicationManager/launch", jvalue_stringify(l), 1, 15, never_stop, out);
		bool launched = code == 0 && hc_remove_reply_class(out->str) == 1;
		LOGI("UNINSTALL_ASK", "%s: launch the confirmation screen (exit %d): %s", op->target, code, out->str);
		g_string_free(out, TRUE);
		j_release(&l);
		if (!launched) {
			t->live = false;
			send_error_for(op->msg, op->method, "confirmFailed", "Could not ask the user: neither the alert nor the Home Chooser screen could be shown");
			return;
		}
		how = "screen";
	}
	jvalue_ref o = jobject_create();
	put_bool(o, "returnValue", true);
	put_int(o, "apiVersion", HC_API_VERSION);
	put_bool(o, "pending", true);
	put_str(o, "id", op->target);
	put_str(o, "confirmation", how);
	send_reply(op->msg, o);
}

static void uninstall_info_done(Call *c, jvalue_ref reply)
{
	LOp *op = c->ctx;
	if (check_ambiguous_target(op, reply)) return;
	jvalue_ref info = reply ? jobject_get(reply, key_buf("appInfo")) : NULL;
	if (!info || !jis_valid(info) || !jis_object(info)) info = reply;
	if (!reply || !reply_ok(reply) || !info) {
		char *sam_text = reply ? get_string(reply, "errorText") : NULL;
		const char *code = !reply ? hc_unusable_reply_code(c->status) : hc_sam_refusal_code(true, sam_text);
		char *message = g_strdup_printf("%s%s%s", !reply ? "No reply from the application manager" :
			strcmp(code, "notFound") == 0 ? "No such app" : "The application manager refused the lookup",
			sam_text ? ": " : "", sam_text ? sam_text : "");
		send_error_for(op->msg, op->method, code, message);
		g_free(message);
		g_free(sam_text);
	} else {
		jvalue_ref rem = jobject_get(info, key_buf("removable"));
		bool removable = true;
		int rflag = -1;
		if (rem && jis_valid(rem) && jis_boolean(rem) && jboolean_get(rem, &removable) == CONV_OK) rflag = removable ? 1 : 0;
		op->target_folder = get_string(info, "folderPath");
		op->target_title = get_string(info, "title");
		hc_remove_verdict v = hc_remove_check(op->target, op->caller, rflag, op->target_folder);
		if (v != HC_REMOVE_OK) {
			LOGE("REFUSED", "uninstallApp %s by %s: %s", op->target, op->caller, hc_remove_verdict_text(v));
			send_error_for(op->msg, op->method, hc_remove_verdict_code(v), hc_remove_verdict_text(v));
		} else {
			uninstall_ask(op);
		}
	}
	op_free(op);
}

static void uninstall_next(LOp *op)
{
	jvalue_ref q = jobject_create();
	put_str(q, "id", op->target);
	call_start(SAM_URI "/getAppInfo", jvalue_stringify(q), uninstall_info_done, op);
	j_release(&q);
}

bool on_launcher_uninstall(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	launcher_begin(msg, "uninstallApp", true, true, true, true, uninstall_next);
	return true;
}

/* uninstallConfirmed {token}: fallback confirmation on Home Chooser's own screen. */
static int stop_progress(const char *text) { return hc_progress_state(text) != 0; }

bool on_uninstall_confirmed(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	const char *app = LSMessageGetApplicationID(msg);
	if (app && app[0] && !hc_sender_ok(app)) {
		LOGE("REFUSED", "uninstallConfirmed from application id '%s'", app);
		send_error(msg, "forbidden", "Only the confirmation may call this method");
		return true;
	}
	if (geteuid() != 0) {
		LOGE("REFUSED", "uninstallConfirmed while service uid is %d", (int)geteuid());
		send_error(msg, "notRoot", "The service is not running as root");
		return true;
	}
	jvalue_ref req = parse_object(LSMessageGetPayload(msg));
	char *token = req ? get_string(req, "token") : NULL;
	j_release(&req);
	UninstallToken *t = NULL;
	for (int i = 0; hc_token_ok(token) && i < TOKEN_SLOTS; i++) {
		if (g_tokens[i].live && strcmp(g_tokens[i].token, token) == 0) t = &g_tokens[i];
	}
	g_free(token);
	if (!t || g_get_monotonic_time() > t->expires) {
		if (t) t->live = false;
		LOGE("REFUSED", "uninstallConfirmed with an unknown or expired token");
		send_error(msg, "badToken", "The confirmation is unknown, used or expired");
		return true;
	}
	t->live = false; /* one shot */
	char *choice = read_choice();
	bool still = hc_is_selected_home(t->requester, choice);
	g_free(choice);
	if (!still) {
		LOGE("REFUSED", "uninstallConfirmed after the requesting launcher stopped being Home");
		send_error(msg, "notHome", "The app that asked is no longer the selected Home");
		return true;
	}
	LOGI("UNINSTALL", "uninstallConfirmed accepted token for %s", t->id);
	char id[130], folder[512];
	g_strlcpy(id, t->id, sizeof id);
	g_strlcpy(folder, t->folder, sizeof folder);
	const char *route = NULL;
	const char *failure = "The removal failed";
	bool refused = false;
	for (int step = 0; step < 2 && !route; step++) {
		hc_remove_call rc;
		if (!hc_remove_step(step, id, folder, &rc)) break;
		GString *out = g_string_new(NULL);
		int code = luna_run(rc.uri, rc.payload, rc.progress ? 20 : 1, rc.timeout_s, rc.progress ? stop_progress : never_stop, out);
		int state = rc.progress ? hc_progress_state(out->str) : hc_remove_reply_class(out->str);
		LOGI("UNINSTALL", "%s step %d %s (exit %d, state %d): %s", id, step, rc.uri, code, state, out->str);
		g_string_free(out, TRUE);
		if (state == 1) {
			route = step == 0 ? "launchPoint" : "appInstallService";
		} else if (state == 0 && !rc.progress) {
			refused = true; /* LG says it is not removable: no other route */
			break;
		}
	}
	if (route) {
		LOGI("UNINSTALL", "uninstallConfirmed removed %s through %s", id, route);
		jvalue_ref o = jobject_create();
		put_bool(o, "returnValue", true);
		put_str(o, "id", id);
		put_str(o, "route", route);
		send_reply(msg, o);
	} else if (refused) {
		LOGE("REFUSED", "uninstallConfirmed: platform says %s is not removable", id);
		send_error_for(msg, "uninstallConfirmed", "notRemovable", "The platform says this app is not removable");
	} else {
		LOGE("REFUSED", "uninstallConfirmed: platform removal failed for %s", id);
		send_error_for(msg, "uninstallConfirmed", "removeFailed", failure);
	}
	return true;
}
