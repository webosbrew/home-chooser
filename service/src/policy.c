/* SPDX-License-Identifier: Apache-2.0 */
#include "policy.h"

#include <string.h>
#include <stdio.h>
#include <strings.h>
#include <ctype.h>
#include <limits.h>
#include <stdlib.h>

bool hc_sender_ok(const char *app_id)
{
#ifdef HC_ALLOW_ANY_SENDER /* dev build only, see service/CMakeLists.txt */
	(void)app_id;
	return true;
#endif
	size_t n = strlen(HC_APP_ID);
	if (!app_id || strncmp(app_id, HC_APP_ID, n) != 0) return false;
	return app_id[n] == '\0' || app_id[n] == ' ';
}

static const char *const FILES[] = {HC_HOME_FILE, HC_HOMEKEY_FLAG, HC_PREVIOUS_FILE, HC_OVERRIDE_FILE, NULL};

bool hc_file_allowed(const char *path)
{
	if (!path) return false;
	for (int i = 0; FILES[i]; i++)
		if (strcmp(path, FILES[i]) == 0) return true;
	return false;
}

bool hc_file_readable(const char *path)
{
	return hc_file_allowed(path) || (path && strcmp(path, HC_OSRELEASE) == 0);
}

bool hc_osrelease_target_ok(const char *resolved)
{
	return resolved && (strcmp(resolved, HC_OSRELEASE) == 0 || strcmp(resolved, HC_OSRELEASE_REAL) == 0);
}

static const struct {
	const char *uri;
	hc_call_kind kind;
} CALLS[] = {
	{"luna://com.webos.applicationManager/listApps", HC_CALL_PLAIN},
	{"luna://com.webos.applicationManager/getAppInfo", HC_CALL_PLAIN},
	{"luna://com.webos.applicationManager/setDefaultApp", HC_CALL_PLAIN},
	{"luna://com.webos.applicationManager/launchDefaultApp", HC_CALL_PLAIN},
	{"luna://com.webos.applicationManager/closeByAppId", HC_CALL_PLAIN},
	{"luna://com.webos.settingsservice/getSystemSettings", HC_CALL_SETTINGS_GET},
	{"luna://com.webos.settingsservice/setSystemSettings", HC_CALL_SETTINGS_SET},
	{NULL, HC_CALL_DENIED}
};

hc_call_kind hc_call_lookup(const char *uri)
{
	if (!uri) return HC_CALL_DENIED;
	for (int i = 0; CALLS[i].uri; i++)
		if (strcmp(uri, CALLS[i].uri) == 0) return CALLS[i].kind;
	return HC_CALL_DENIED;
}

bool hc_settings_ok(const char *category, const char *const *keys, size_t nkeys)
{
	if (!category || strcmp(category, "general") != 0) return false;
	if (nkeys != 1 || !keys || !keys[0]) return false;
	return strcmp(keys[0], "defaultApps") == 0;
}

static const hc_command COMMANDS[] = {
	{"watch", {"/bin/sh", HC_ENTRY_SCRIPT, "watch", NULL}, 20, false, false},
	{"homekeyStatus", {"/bin/sh", HC_HOMEKEY_SCRIPT, "status", NULL}, 20, false, false},
	{"homekeyPrepare", {"/bin/sh", HC_HOMEKEY_SCRIPT, "prepare", NULL}, 120, false, false},
	{"homekeyClear", {"/bin/sh", HC_HOMEKEY_SCRIPT, "clear", NULL}, 20, false, false},
	/* All hook commands use configd volatile reload; no GUI process is restarted. */
	{"homekeyApply", {"/bin/sh", HC_HOMEKEY_SCRIPT, "apply", NULL}, 90, false, false},
	{"homekeyStop", {"/bin/sh", HC_HOMEKEY_SCRIPT, "stop", NULL}, 90, false, false},
	{"homekeyRefresh", {"/bin/sh", HC_HOMEKEY_SCRIPT, "refresh", NULL}, 90, false, false},
	/* the launcher API's permission group and API permissions (service/scripts/homechooser-access) */
	{"accessApply", {"/bin/sh", HC_ACCESS_SCRIPT, "apply", NULL}, 30, false, false},
	{"accessRemove", {"/bin/sh", HC_ACCESS_SCRIPT, "remove", NULL}, 30, false, false},
	{"accessStatus", {"/bin/sh", HC_ACCESS_SCRIPT, "status", NULL}, 10, true, false},
	{"stopWatcher", {"/bin/systemctl", "stop", "homechooser-home", NULL}, 20, true, false},
	{"watcherActive", {"/bin/systemctl", "is-active", "homechooser-home", NULL}, 10, true, false},
	/* Exact profile calls use the same identity as every other privileged luna call. */
	{"profileGet", {"/usr/bin/luna-send", "-a", HC_LUNA_IDENTITY, "-n", "1", "luna://com.webos.service.systemprofile/getProfiles", "{\"names\":[\"defaultApps\",\"idleApp\"]}", NULL}, 20, false, false},
	{"profileRescan", {"/usr/bin/luna-send", "-a", HC_LUNA_IDENTITY, "-n", "1", "luna://com.webos.service.systemprofile/debug/rescan", "{}", NULL}, 30, false, false},
	{"watchLog", {NULL}, 0, true, true},
	{NULL, {NULL}, 0, false, false}
};

const hc_command *hc_command_lookup(const char *name)
{
	if (!name) return NULL;
	for (int i = 0; COMMANDS[i].name; i++)
		if (strcmp(name, COMMANDS[i].name) == 0) return &COMMANDS[i];
	return NULL;
}

/* True when s has a ".." component. */
static bool has_dotdot(const char *s)
{
	const char *p = s;
	while (*p) {
		const char *e = strchr(p, '/');
		size_t n = e ? (size_t)(e - p) : strlen(p);
		if (n == 2 && p[0] == '.' && p[1] == '.') return true;
		if (!e) break;
		p = e + 1;
	}
	return false;
}

bool hc_icon_path(const char *folder, const char *icon, char *out, size_t outlen)
{
	if (!folder || !icon || !out || folder[0] != '/' || !icon[0]) return false;
	if (has_dotdot(folder) || has_dotdot(icon)) return false;
	int n;
	if (icon[0] == '/') {
		n = (int)strlen(icon);
		if ((size_t)n >= outlen) return false;
		memcpy(out, icon, (size_t)n + 1);
		return true;
	}
	size_t fl = strlen(folder);
	while (fl > 1 && folder[fl - 1] == '/') fl--;
	size_t il = strlen(icon);
	if (fl + 1 + il >= outlen) return false;
	memcpy(out, folder, fl);
	out[fl] = '/';
	memcpy(out + fl + 1, icon, il + 1);
	return true;
}

bool hc_path_within(const char *path, const char *dir)
{
	if (!path || !dir) return false;
	size_t dl = strlen(dir);
	while (dl > 1 && dir[dl - 1] == '/') dl--;
	if (strncmp(path, dir, dl) != 0) return false;
	if (dl == 1) return path[0] == '/';
	return path[dl] == '/' || path[dl] == '\0';
}

bool hc_icon_resolve(const char *folder, const char *icon, char *out, size_t outlen)
{
	char joined[1024], real_file[PATH_MAX], real_dir[PATH_MAX];
	if (!out || !outlen || !hc_icon_path(folder, icon, joined, sizeof joined)) return false;
	if (!realpath(folder, real_dir) || !realpath(joined, real_file)) return false;
	if (!hc_path_within(real_file, real_dir) || strlen(real_file) >= outlen) return false;
	strcpy(out, real_file);
	return true;
}

const char *hc_icon_mime(const char *path)
{
	static const struct { const char *ext, *mime; } T[] = {
		{"png", "image/png"}, {"jpg", "image/jpeg"}, {"jpeg", "image/jpeg"}, {"gif", "image/gif"},
		{"svg", "image/svg+xml"}, {"webp", "image/webp"}, {NULL, NULL}
	};
	const char *dot = path ? strrchr(path, '.') : NULL;
	if (!dot || strchr(dot, '/')) return NULL;
	for (int i = 0; T[i].ext; i++)
		if (strcasecmp(dot + 1, T[i].ext) == 0) return T[i].mime;
	return NULL;
}

bool hc_base64(const unsigned char *in, size_t len, char *out, size_t outlen)
{
	static const char A[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
	if (outlen < 4 * ((len + 2) / 3) + 1) return false;
	size_t o = 0;
	for (size_t i = 0; i < len; i += 3) {
		unsigned v = (unsigned)in[i] << 16;
		if (i + 1 < len) v |= (unsigned)in[i + 1] << 8;
		if (i + 2 < len) v |= in[i + 2];
		out[o++] = A[(v >> 18) & 63];
		out[o++] = A[(v >> 12) & 63];
		out[o++] = i + 1 < len ? A[(v >> 6) & 63] : '=';
		out[o++] = i + 2 < len ? A[v & 63] : '=';
	}
	out[o] = '\0';
	return true;
}

size_t hc_last_lines(const char *buf, size_t len, int n)
{
	if (n <= 0) return len;
	size_t end = len;
	if (end > 0 && buf[end - 1] == '\n') end--;
	int seen = 0;
	for (size_t i = end; i > 0; i--) {
		if (buf[i - 1] == '\n' && ++seen == n) return i;
	}
	return 0;
}

/* ---- launcher API ---- */

bool hc_app_id_ok(const char *id)
{
	size_t n = id ? strlen(id) : 0;
	if (n == 0 || n > 128) return false;
	for (size_t i = 0; i < n; i++) {
		char c = id[i];
		bool alnum = (c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');
		if (!alnum && c != '.' && c != '-' && c != '_') return false;
	}
	return true;
}

bool hc_caller_id(const char *raw, char *out, size_t outlen)
{
	if (!raw || !out || !raw[0]) return false;
	size_t n = strcspn(raw, " ");
	if (n == 0 || n > 128 || n >= outlen) return false;
	memcpy(out, raw, n);
	out[n] = '\0';
	if (!hc_app_id_ok(out)) {
		out[0] = '\0';
		return false;
	}
	return true;
}

bool hc_perms_grant(const char *const *perms, size_t n)
{
	for (size_t i = 0; perms && i < n; i++)
		if (perms[i] && strcmp(perms[i], HC_GROUP) == 0) return true;
	return false;
}

bool hc_is_selected_home(const char *caller, const char *choice_text)
{
	if (!caller || !caller[0] || !choice_text) return false;
	while (*choice_text == ' ' || *choice_text == '\t' || *choice_text == '\r' || *choice_text == '\n') choice_text++;
	size_t n = strlen(choice_text);
	while (n > 0 && (choice_text[n - 1] == ' ' || choice_text[n - 1] == '\t' || choice_text[n - 1] == '\r' || choice_text[n - 1] == '\n')) n--;
	return n == strlen(caller) && strncmp(choice_text, caller, n) == 0;
}

hc_folder_kind hc_folder_kind_of(const char *folder)
{
	if (!folder || folder[0] != '/' || has_dotdot(folder)) return HC_FOLDER_OTHER;
	/* strictly below the applications directory: the directory itself is no app */
	static const struct { const char *dir; hc_folder_kind kind; } D[] = {
		{"/media/developer/apps/usr/palm/applications", HC_FOLDER_DEV},
		{"/media/cryptofs/apps/usr/palm/applications", HC_FOLDER_STORE},
		{NULL, HC_FOLDER_OTHER}
	};
	for (int i = 0; D[i].dir; i++) {
		size_t dl = strlen(D[i].dir);
		if (strncmp(folder, D[i].dir, dl) == 0 && folder[dl] == '/' && folder[dl + 1]) return D[i].kind;
	}
	return HC_FOLDER_OTHER;
}

hc_remove_verdict hc_remove_check(const char *id, const char *caller, int removable, const char *folder)
{
	if (!hc_app_id_ok(id)) return HC_REMOVE_BADID;
	if (strcmp(id, HC_APP_ID) == 0) return HC_REMOVE_SELF;
	if (strcmp(id, HC_HBC_APP_ID) == 0) return HC_REMOVE_HBC;
	if (caller && strcmp(id, caller) == 0) return HC_REMOVE_CALLER;
	if (removable == 0 || hc_folder_kind_of(folder) == HC_FOLDER_OTHER) return HC_REMOVE_SYSTEM;
	return HC_REMOVE_OK;
}

const char *hc_remove_verdict_text(hc_remove_verdict v)
{
	switch (v) {
	case HC_REMOVE_OK: return "ok";
	case HC_REMOVE_BADID: return "not a valid app id";
	case HC_REMOVE_SELF: return "Home Chooser cannot be uninstalled through its own API";
	case HC_REMOVE_HBC: return "Homebrew Channel cannot be uninstalled through this API";
	case HC_REMOVE_CALLER: return "an app cannot uninstall itself through this API";
	case HC_REMOVE_SYSTEM: return "this is a system app or is not removable";
	}
	return "refused";
}

bool hc_sam_app_missing(const char *error_text)
{
	if (!error_text) return false;
	char lower[512];
	size_t n = strlen(error_text);
	if (n >= sizeof lower) n = sizeof lower - 1;
	for (size_t i = 0; i < n; i++) lower[i] = (char)tolower((unsigned char)error_text[i]);
	lower[n] = '\0';
	return strstr(lower, "no such app") || strstr(lower, "app not found") ||
		strstr(lower, "application not found") || strstr(lower, "app does not exist") ||
		strstr(lower, "application does not exist") || strstr(lower, "app is not installed") ||
		strstr(lower, "app not installed") || strstr(lower, "application is not installed") ||
		strstr(lower, "application not installed");
}

bool hc_sam_app_ambiguous(const char *error_text)
{
	if (!error_text) return false;
	char lower[512];
	size_t n = strlen(error_text);
	if (n >= sizeof lower) n = sizeof lower - 1;
	for (size_t i = 0; i < n; i++) lower[i] = (char)tolower((unsigned char)error_text[i]);
	lower[n] = '\0';
	return strstr(lower, "invalid appid specified or unsupported application type") != NULL;
}

const char *hc_unusable_reply_code(int exit_status)
{
	return exit_status == -2 ? "timeout" : "badReply";
}

const char *hc_sam_refusal_code(bool target_lookup, const char *error_text)
{
	return target_lookup && hc_sam_app_missing(error_text) ? "notFound" : "refused";
}

const char *hc_remove_verdict_code(hc_remove_verdict verdict)
{
	if (verdict == HC_REMOVE_SYSTEM) return "notRemovable";
	if (verdict == HC_REMOVE_BADID) return "badRequest";
	return "protected";
}

bool hc_remove_step(int step, const char *id, const char *folder, hc_remove_call *out)
{
	if (!out || !hc_app_id_ok(id)) return false;
	memset(out, 0, sizeof *out);
	out->timeout_s = 90;
	if (step == 0) {
		out->uri = "luna://com.webos.applicationManager/removeLaunchPoint";
		snprintf(out->payload, sizeof out->payload, "{\"launchPointId\":\"%s_default\"}", id);
		out->timeout_s = 30;
		return true;
	}
	if (step == 1) {
		hc_folder_kind k = hc_folder_kind_of(folder);
		if (k == HC_FOLDER_OTHER) return false;
		out->uri = k == HC_FOLDER_DEV ? "luna://com.webos.appInstallService/dev/remove" : "luna://com.webos.appInstallService/remove";
		snprintf(out->payload, sizeof out->payload, "{\"id\":\"%s\",\"subscribe\":true}", id);
		out->progress = true;
		return true;
	}
	return false;
}

int hc_progress_state(const char *text)
{
	if (!text) return 0;
	int state = 0;
	const char *p = text;
	while ((p = strstr(p, "\"status\"")) != NULL) {
		p += 8;
		while (*p == ' ' || *p == '\t' || *p == '\n') p++;
		if (*p != ':') continue;
		p++;
		while (*p == ' ' || *p == '\t' || *p == '\n') p++;
		int v = 0, digits = 0;
		while (*p >= '0' && *p <= '9' && digits < 6) { v = v * 10 + (*p - '0'); p++; digits++; }
		if (digits && v == 31) return 1;
		if (digits && v == 25) state = -1;
	}
	if (state == 0 && strstr(text, "\"returnValue\":false")) state = -1;
	if (state == 0 && strstr(text, "\"returnValue\": false")) state = -1;
	return state;
}

int hc_remove_reply_class(const char *text)
{
	if (!text || !text[0]) return -1;
	bool is_false = strstr(text, "\"returnValue\":false") || strstr(text, "\"returnValue\": false");
	if (!is_false) return strstr(text, "\"returnValue\"") ? 1 : -1;
	return strstr(text, "is not removable") ? 0 : -1;
}

const char *const hc_launcher_list_fields[] = {
	"id", "title", "icon", "largeIcon", "folderPath", "version", "vendor", "type", "removable", "visible",
	"appCategory", NULL
};

static const char *const LUNA_URIS[] = {
	"luna://com.webos.service.preloadmanager/setPreloadPolicy",
	"luna://com.webos.applicationManager/getAppLifeEvents",
	"luna://com.webos.service.systemprofile/getProfiles",
	"luna://com.webos.service.systemprofile/debug/rescan",
	"luna://com.webos.service.config/getConfigs",
	"luna://com.webos.service.config/setConfigs",
	"luna://com.webos.surfacemanager/getCurrentState",
	"luna://com.webos.service.eim/getLastInput",
	"luna://com.webos.notification/createAlert",
	"luna://com.webos.notification/createToast",
	"luna://com.webos.service.tvpower/power/reboot",
	"luna://com.webos.applicationManager/launch",
	"luna://com.webos.applicationManager/removeLaunchPoint",
	"luna://com.webos.applicationManager/listApps",
	"luna://com.webos.applicationManager/running",
	"luna://com.webos.applicationManager/getAppInfo",
	"luna://com.webos.applicationManager/getForegroundAppInfo",
	"luna://com.webos.applicationManager/closeByAppId",
	"luna://com.webos.appInstallService/remove",
	"luna://com.webos.appInstallService/dev/remove",
	NULL
};

bool hc_luna_uri_allowed(const char *uri)
{
	if (hc_call_lookup(uri) != HC_CALL_DENIED) return true;
	for (int i = 0; uri && LUNA_URIS[i]; i++)
		if (strcmp(uri, LUNA_URIS[i]) == 0) return true;
	return false;
}

bool hc_luna_argv(const char *uri, const char *payload, int replies, const char **argv)
{
	if (!argv || !payload || !hc_luna_uri_allowed(uri)) return false;
	argv[0] = "/usr/bin/luna-send";
	argv[1] = "-a";
	argv[2] = HC_LUNA_IDENTITY;
	argv[3] = "-n";
	argv[4] = replies > 1 ? "20" : "1";
	argv[5] = uri;
	argv[6] = payload;
	argv[7] = NULL;
	return true;
}

bool hc_token_ok(const char *token)
{
	if (!token || strlen(token) != HC_TOKEN_LEN) return false;
	for (int i = 0; i < HC_TOKEN_LEN; i++)
		if (!((token[i] >= '0' && token[i] <= '9') || (token[i] >= 'a' && token[i] <= 'f'))) return false;
	return true;
}

/* ---- reboot prompt ---- */

bool hc_reboot_needed(const char *text)
{
	if (!text) return false;
	const char *want = "reboot=needed";
	size_t wl = strlen(want);
	const char *p = text;
	while (*p) {
		const char *end = strchr(p, '\n');
		size_t len = end ? (size_t)(end - p) : strlen(p);
		const char *a = p, *b = p + len;
		while (a < b && (*a == ' ' || *a == '\t' || *a == '\r')) a++;
		while (b > a && (b[-1] == ' ' || b[-1] == '\t' || b[-1] == '\r')) b--;
		if ((size_t)(b - a) == wl && memcmp(a, want, wl) == 0) return true;
		if (!end) break;
		p = end + 1;
	}
	return false;
}

const char *hc_reboot_message(const char *reason)
{
	if (!reason) return NULL;
	if (strcmp(reason, "apply") == 0) return "Restart the TV to finish setting up the Home button.";
	if (strcmp(reason, "remove") == 0) return "Restart the TV to finish removing the Home button patch.";
	return NULL;
}
