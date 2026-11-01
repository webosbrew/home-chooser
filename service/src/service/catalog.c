/* SPDX-License-Identifier: Apache-2.0 */
/* listApps, listLaunchPoints, listInputs and getIcons (docs/launcher-api.md). Every call reads the
 * platform afresh; the service keeps no copy of a list or an icon. */
#include <limits.h>
#include <string.h>
#include "apps.h"
#include "fs.h"
#include "json.h"
#include "log.h"
#include "policy.h"
#include "service/catalog.h"
#include "service/launcher.h"
#include "service/methods.h"
#include "service/reply.h"

static const char *const LIST_METHODS[] = {"listApps", "listLaunchPoints", "listInputs"};
static const char *const LIST_URIS[] = {
	SAM_URI "/listApps", SAM_URI "/listLaunchPoints", EIM_URI "/getAllInputStatus"
};
/* The array each platform reply carries, and the name the launcher reply gives it. */
static const char *const PLATFORM_KEYS[] = {"apps", "launchPoints", "devices"};
static const char *const REPLY_KEYS[] = {"apps", "launchPoints", "inputs"};
static const char *const WHO[] = {"application manager", "application manager", "input manager"};
static const char *const BAD_ARRAY[] = {
	"The application manager did not return an app array",
	"The application manager did not return a launch point array",
	"The input manager did not return a device array"
};

const char *list_method(HCListKind kind) { return LIST_METHODS[kind]; }
const char *list_uri(HCListKind kind) { return LIST_URIS[kind]; }

char *list_payload(HCListKind kind, bool subscribe)
{
	jvalue_ref q = jobject_create();
	if (kind == HC_LIST_APPS) {
		/* the reply is cut to LIST_FIELDS; asking for just those keeps it small */
		jvalue_ref props = jarray_create(NULL);
		for (int i = 0; LIST_FIELDS[i]; i++) jarray_append(props, jstring_create(LIST_FIELDS[i]));
		jobject_set(q, key_buf("properties"), props);
	}
	if (subscribe) put_bool(q, "subscribe", true);
	char *text = g_strdup(jvalue_stringify(q));
	j_release(&q);
	return text;
}

static bool empty_string(jvalue_ref v)
{
	return jis_string(v) && jstring_get_fast(v).m_len == 0;
}

/* Launch points: the published fields; SAM's "" for an absent value is omitted like null. */
static jvalue_ref pick_launch_points(jvalue_ref arr)
{
	jvalue_ref out = jarray_create(NULL);
	for (ssize_t i = 0; i < jarray_size(arr); i++) {
		jvalue_ref lp = jarray_get(arr, i);
		if (!jis_object(lp)) continue;
		jvalue_ref o = jobject_create();
		for (int f = 0; hc_launch_point_fields[f]; f++) {
			jvalue_ref v = jobject_get(lp, key_buf(hc_launch_point_fields[f]));
			if (v && jis_valid(v) && !jis_null(v) && !empty_string(v))
				jobject_set(o, key_buf(hc_launch_point_fields[f]), jvalue_duplicate(v));
		}
		jarray_append(out, o);
	}
	return out;
}

static void copy_typed(jvalue_ref out, const char *dest, jvalue_ref in, const char *src, bool (*is)(jvalue_ref))
{
	jvalue_ref v = jobject_get(in, key_buf(src));
	if (v && jis_valid(v) && is(v)) jobject_set(out, key_buf(dest), jvalue_duplicate(v));
}

/* eim devices -> inputs: id is the input app (what a launcher launches), inputId eim's own id. */
static jvalue_ref pick_inputs(jvalue_ref arr)
{
	jvalue_ref out = jarray_create(NULL);
	for (ssize_t i = 0; i < jarray_size(arr); i++) {
		jvalue_ref d = jarray_get(arr, i);
		char *app = jis_object(d) ? get_string(d, "appId") : NULL;
		char *input = jis_object(d) ? get_string(d, "id") : NULL;
		if (hc_app_id_ok(app) && input && input[0]) {
			jvalue_ref o = jobject_create();
			put_str(o, "id", app);
			put_str(o, "inputId", input);
			copy_typed(o, "label", d, "label", jis_string);
			copy_typed(o, "port", d, "port", jis_number);
			copy_typed(o, "connected", d, "connected", jis_boolean);
			copy_typed(o, "activate", d, "activate", jis_boolean);
			char *prefix = get_string(d, "iconPrefix");
			char *icon = get_string(d, "icon");
			if (prefix && icon && icon[0]) {
				char *path = g_strconcat(prefix, icon, NULL);
				put_str(o, "icon", path);
				g_free(path);
			}
			g_free(prefix);
			g_free(icon);
			jarray_append(out, o);
		}
		g_free(app);
		g_free(input);
	}
	return out;
}

jvalue_ref list_reply(HCListKind kind, jvalue_ref reply, bool subscribed)
{
	jvalue_ref arr = reply ? jobject_get(reply, key_buf(PLATFORM_KEYS[kind])) : NULL;
	if (!arr || !jis_valid(arr) || !jis_array(arr)) return NULL;
	jvalue_ref o = jobject_create();
	put_bool(o, "returnValue", true);
	put_int(o, "apiVersion", HC_API_VERSION);
	if (subscribed) put_bool(o, "subscribed", true);
	jobject_set(o, key_buf(REPLY_KEYS[kind]), kind == HC_LIST_APPS ? pick_fields(arr, LIST_FIELDS) :
		kind == HC_LIST_LAUNCH_POINTS ? pick_launch_points(arr) : pick_inputs(arr));
	return o;
}

/* ---- the list methods ---- */

static HCListKind op_kind(LOp *op)
{
	for (int k = 0; k < HC_LIST_COUNT; k++)
		if (strcmp(op->method, LIST_METHODS[k]) == 0) return (HCListKind)k;
	return HC_LIST_APPS;
}

static void list_done(Call *c, jvalue_ref reply)
{
	LOp *op = c->ctx;
	HCListKind kind = op_kind(op);
	if (!platform_failure(op, c, reply, WHO[kind])) {
		jvalue_ref o = list_reply(kind, reply, op->subscribe);
		if (!o) {
			send_error_for(op->msg, op->method, "badReply", BAD_ARRAY[kind]);
		} else {
			send_reply(op->msg, o);
			if (op->subscribe) subs_add(op->sh, kind, op->msg);
		}
	}
	op_free(op);
}

static void list_next(LOp *op)
{
	HCListKind kind = op_kind(op);
	if (op->subscribe && !subs_room(op->sh, kind)) {
		LOGE("REFUSED", "%s subscription from %s: %d subscribers already", op->method, op->caller, HC_SUBSCRIBERS_MAX);
		send_error_for(op->msg, op->method, "tooManySubscriptions", "This list has the most subscribers it takes; cancel one first");
		op_free(op);
		return;
	}
	/* The first reply, subscription or not, is a fresh read of the whole list. */
	char *payload = list_payload(kind, false);
	call_start(LIST_URIS[kind], payload, list_done, op);
	g_free(payload);
}

static void list_begin(LSHandle *sh, LSMessage *msg, HCListKind kind)
{
	LOp *op = launcher_open(sh, msg, LIST_METHODS[kind], true, false, true, list_next);
	if (!op) return;
	op->subscribe = wants_subscribe(msg);
	launcher_consent(op);
}

bool on_launcher_list_apps(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)ctx;
	list_begin(sh, msg, HC_LIST_APPS);
	return true;
}

bool on_launcher_list_launch_points(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)ctx;
	list_begin(sh, msg, HC_LIST_LAUNCH_POINTS);
	return true;
}

bool on_launcher_list_inputs(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)ctx;
	list_begin(sh, msg, HC_LIST_INPUTS);
	return true;
}

/* ---- getIcons ---- */

enum { ITEM_APP, ITEM_LAUNCH_POINT, ITEM_INPUT };

/* items: 1 to HC_ICON_ITEMS_MAX objects, each with exactly one of id (an app id), launchPointId or
 * inputId (non-empty strings of at most 256 bytes), and an optional size (icon or largeIcon; not
 * for an input). Returns the normalized items [{kind, key, size}], or NULL. */
static jvalue_ref parse_items(jvalue_ref req)
{
	jvalue_ref items = req ? jobject_get(req, key_buf("items")) : NULL;
	if (!items || !jis_valid(items) || !jis_array(items)) return NULL;
	ssize_t n = jarray_size(items);
	if (n < 1 || n > HC_ICON_ITEMS_MAX) return NULL;
	jvalue_ref out = jarray_create(NULL);
	static const char *const KEYS[] = {"id", "launchPointId", "inputId"};
	for (ssize_t i = 0; i < n; i++) {
		jvalue_ref it = jarray_get(items, i);
		int kind = -1, count = 0;
		char *key = NULL;
		for (int k = 0; jis_object(it) && k < 3; k++) {
			jvalue_ref v = jobject_get(it, key_buf(KEYS[k]));
			if (!v || !jis_valid(v)) continue;
			count++;
			kind = k;
			g_free(key);
			key = get_string(it, KEYS[k]);
		}
		bool ok = count == 1 && key && key[0] && strlen(key) <= 256 && (kind != ITEM_APP || hc_app_id_ok(key));
		jvalue_ref size = ok ? jobject_get(it, key_buf("size")) : NULL;
		char *size_name = size && jis_valid(size) ? get_string(it, "size") : NULL;
		if (ok && size && jis_valid(size))
			ok = kind != ITEM_INPUT && size_name && (!strcmp(size_name, "icon") || !strcmp(size_name, "largeIcon"));
		if (ok) {
			jvalue_ref o = jobject_create();
			put_int(o, "kind", kind);
			put_str(o, "key", key);
			put_str(o, "size", size_name ? size_name : "icon");
			jarray_append(out, o);
		}
		g_free(key);
		g_free(size_name);
		if (!ok) {
			j_release(&out);
			return NULL;
		}
	}
	return out;
}

typedef struct { jvalue_ref reply; int status; } Fetched;

static void keep_reply(Call *c, jvalue_ref reply)
{
	Fetched *f = c->ctx;
	f->status = c->status;
	f->reply = reply ? jvalue_copy(reply) : NULL;
}

/* One fresh read of a list for the whole batch; replies and returns NULL on a failure. */
static jvalue_ref fetch_list(LOp *op, HCListKind kind, const char *payload)
{
	Fetched f = {0};
	call_start(LIST_URIS[kind], payload, keep_reply, &f);
	Call c = {.ctx = op, .status = f.status};
	if (platform_failure(op, &c, f.reply, WHO[kind])) {
		j_release(&f.reply);
		return NULL;
	}
	jvalue_ref arr = jobject_get(f.reply, key_buf(PLATFORM_KEYS[kind]));
	if (!arr || !jis_valid(arr) || !jis_array(arr)) {
		send_error_for(op->msg, op->method, "badReply", BAD_ARRAY[kind]);
		j_release(&f.reply);
		return NULL;
	}
	return f.reply;
}

static jvalue_ref find_by(jvalue_ref reply, const char *array, const char *field, const char *value)
{
	jvalue_ref arr = reply ? jobject_get(reply, key_buf(array)) : NULL;
	for (ssize_t i = 0; arr && jis_array(arr) && i < jarray_size(arr); i++) {
		jvalue_ref o = jarray_get(arr, i);
		char *v = jis_object(o) ? get_string(o, field) : NULL;
		bool hit = v && strcmp(v, value) == 0;
		g_free(v);
		if (hit) return o;
	}
	return NULL;
}

static jvalue_ref item_error(const char *code, const char *text)
{
	jvalue_ref o = jobject_create();
	put_str(o, "errorCode", code);
	put_str(o, "errorText", text);
	return o;
}

/* Reads a launch point's or an input's icon file, already resolved inside its allowed directory. */
static IconResult read_icon_file(const char *resolved, char **bytes, size_t *length, const char **mime)
{
	bool too_big = false;
	*mime = hc_icon_mime(resolved);
	ssize_t n = *mime ? slurp(resolved, ICON_MAX_BYTES, bytes, &too_big) : -1;
	if (n >= 0) {
		*length = (size_t)n;
		return ICON_OK;
	}
	return too_big ? ICON_TOO_BIG : ICON_NONE;
}

static jvalue_ref icon_entry(jvalue_ref item, jvalue_ref apps, jvalue_ref lps, jvalue_ref inputs, size_t *budget)
{
	int32_t kind = 0;
	jnumber_get_i32(jobject_get(item, key_buf("kind")), &kind);
	char *key = get_string(item, "key");
	char *size = get_string(item, "size");
	char *bytes = NULL, resolved[PATH_MAX];
	size_t length = 0;
	const char *mime = NULL;
	IconResult result = ICON_NONE;
	jvalue_ref found = NULL;
	if (kind == ITEM_APP) {
		found = find_by(apps, "apps", "id", key);
		if (found) result = load_app_icon(found, size, ICON_MAX_BYTES, &bytes, &length, &mime);
	} else if (kind == ITEM_LAUNCH_POINT) {
		found = find_by(lps, "launchPoints", "launchPointId", key);
		char *app = found ? get_string(found, "id") : NULL;
		char *path = found ? get_string(found, size) : NULL;
		if (path && hc_launch_point_icon_resolve(hc_fs_prefix(), app, path, resolved, sizeof resolved))
			result = read_icon_file(resolved, &bytes, &length, &mime);
		g_free(app);
		g_free(path);
	} else {
		found = find_by(inputs, "devices", "id", key);
		char *prefix = found ? get_string(found, "iconPrefix") : NULL;
		char *icon = found ? get_string(found, "icon") : NULL;
		char *path = prefix && icon ? g_strconcat(prefix, icon, NULL) : NULL;
		if (path && hc_input_icon_resolve(hc_fs_prefix(), path, resolved, sizeof resolved))
			result = read_icon_file(resolved, &bytes, &length, &mime);
		g_free(prefix);
		g_free(icon);
		g_free(path);
	}
	jvalue_ref o;
	if (!found) {
		o = item_error("notFound", kind == ITEM_APP ? "No such app" : kind == ITEM_LAUNCH_POINT ?
			"No such launch point" : "No such input");
	} else if (result == ICON_NONE) {
		o = item_error("noIcon", "No readable icon of the requested size");
	} else if (result == ICON_TOO_BIG) {
		o = item_error("iconTooLarge", "The icon exceeds 262144 bytes");
	} else if (length > *budget) {
		o = item_error("overBudget", "The reply is full; ask for this icon again");
	} else {
		*budget -= length;
		char *data = icon_base64(bytes, length);
		char *url = g_strconcat("data:", mime, ";base64,", data, NULL);
		o = jobject_create();
		put_str(o, "mimeType", mime);
		put_str(o, "dataUrl", url);
		put_int(o, "byteLength", (int)length);
		g_free(url);
		g_free(data);
	}
	g_free(bytes);
	g_free(key);
	g_free(size);
	return o;
}

static void icons_next(LOp *op)
{
	bool need[3] = {false, false, false};
	for (ssize_t i = 0; i < jarray_size(op->items); i++) {
		int32_t kind = 0;
		jnumber_get_i32(jobject_get(jarray_get(op->items, i), key_buf("kind")), &kind);
		need[kind] = true;
	}
	jvalue_ref apps = NULL, lps = NULL, inputs = NULL;
	bool ok = true;
	if (need[ITEM_APP]) {
		ok = (apps = fetch_list(op, HC_LIST_APPS, "{\"properties\":[\"id\",\"folderPath\",\"icon\",\"largeIcon\"]}")) != NULL;
	}
	if (ok && need[ITEM_LAUNCH_POINT]) ok = (lps = fetch_list(op, HC_LIST_LAUNCH_POINTS, "{}")) != NULL;
	if (ok && need[ITEM_INPUT]) ok = (inputs = fetch_list(op, HC_LIST_INPUTS, "{}")) != NULL;
	if (ok) {
		size_t budget = HC_ICON_BATCH_BYTES;
		jvalue_ref icons = jarray_create(NULL);
		for (ssize_t i = 0; i < jarray_size(op->items); i++)
			jarray_append(icons, icon_entry(jarray_get(op->items, i), apps, lps, inputs, &budget));
		jvalue_ref o = jobject_create();
		put_bool(o, "returnValue", true);
		put_int(o, "apiVersion", HC_API_VERSION);
		jobject_set(o, key_buf("icons"), icons);
		send_reply(op->msg, o);
	}
	j_release(&apps);
	j_release(&lps);
	j_release(&inputs);
	op_free(op);
}

bool on_launcher_get_icons(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)ctx;
	LOp *op = launcher_open(sh, msg, "getIcons", true, false, true, icons_next);
	if (!op) return true;
	jvalue_ref req = parse_object(LSMessageGetPayload(msg));
	op->items = parse_items(req);
	j_release(&req);
	if (!op->items) {
		send_error_for(msg, op->method, "badRequest", "items must be 1 to 64 objects, each with one of id, "
			"launchPointId or inputId, and size icon or largeIcon (not for an input)");
		op_free(op);
		return true;
	}
	launcher_consent(op);
	return true;
}
