#include "policy.h"

#include <string.h>
#include <strings.h>

bool oh_sender_ok(const char *app_id)
{
#ifdef OH_ALLOW_ANY_SENDER /* dev build only, see the Makefile */
	(void)app_id;
	return true;
#endif
	size_t n = strlen(OH_APP_ID);
	if (!app_id || strncmp(app_id, OH_APP_ID, n) != 0) return false;
	return app_id[n] == '\0' || app_id[n] == ' ';
}

static const char *const FILES[] = {OH_HOME_FILE, OH_KEYFILTER_FLAG, OH_OVERRIDE_FILE, NULL};

bool oh_file_allowed(const char *path)
{
	if (!path) return false;
	for (int i = 0; FILES[i]; i++)
		if (strcmp(path, FILES[i]) == 0) return true;
	return false;
}

bool oh_file_readable(const char *path)
{
	return oh_file_allowed(path) || (path && strcmp(path, OH_OSRELEASE) == 0);
}

bool oh_osrelease_target_ok(const char *resolved)
{
	return resolved && (strcmp(resolved, OH_OSRELEASE) == 0 || strcmp(resolved, OH_OSRELEASE_REAL) == 0);
}

static const struct {
	const char *uri;
	oh_call_kind kind;
} CALLS[] = {
	{"luna://com.webos.applicationManager/listApps", OH_CALL_PLAIN},
	{"luna://com.webos.applicationManager/getAppInfo", OH_CALL_PLAIN},
	{"luna://com.webos.applicationManager/setDefaultApp", OH_CALL_PLAIN},
	{"luna://com.webos.applicationManager/launchDefaultApp", OH_CALL_PLAIN},
	{"luna://com.webos.applicationManager/closeByAppId", OH_CALL_PLAIN},
	{"luna://com.webos.settingsservice/getSystemSettings", OH_CALL_SETTINGS_GET},
	{"luna://com.webos.settingsservice/setSystemSettings", OH_CALL_SETTINGS_SET},
	{"luna://com.webos.service.systemprofile/getProfiles", OH_CALL_PLAIN},
	{"luna://com.webos.service.systemprofile/debug/rescan", OH_CALL_PLAIN},
	{NULL, OH_CALL_DENIED}
};

oh_call_kind oh_call_lookup(const char *uri)
{
	if (!uri) return OH_CALL_DENIED;
	for (int i = 0; CALLS[i].uri; i++)
		if (strcmp(uri, CALLS[i].uri) == 0) return CALLS[i].kind;
	return OH_CALL_DENIED;
}

bool oh_settings_ok(const char *category, const char *const *keys, size_t nkeys)
{
	if (!category || strcmp(category, "general") != 0) return false;
	if (nkeys != 1 || !keys || !keys[0]) return false;
	return strcmp(keys[0], "defaultApps") == 0;
}

static const oh_command COMMANDS[] = {
	{"restartSam", {"/bin/systemctl", "restart", "sam", NULL}, 60, false, false},
	{"watch", {"/bin/sh", OH_ENTRY_SCRIPT, "watch", NULL}, 20, false, false},
	{"keyfilterStatus", {"/bin/sh", OH_KEYFILTER_SCRIPT, "status", NULL}, 20, false, false},
	{"keyfilterApply", {"/bin/sh", OH_KEYFILTER_SCRIPT, "apply", NULL}, 90, false, false},
	{"keyfilterStop", {"/bin/sh", OH_KEYFILTER_SCRIPT, "stop", NULL}, 90, false, false},
	{"keyfilterRefresh", {"/bin/sh", OH_KEYFILTER_SCRIPT, "refresh", NULL}, 90, false, false},
	{"stopWatcher", {"/bin/systemctl", "stop", "openhouse-home", NULL}, 20, true, false},
	{"watcherActive", {"/bin/systemctl", "is-active", "openhouse-home", NULL}, 10, true, false},
	{"watchLog", {NULL}, 0, true, true},
	{NULL, {NULL}, 0, false, false}
};

const oh_command *oh_command_lookup(const char *name)
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

bool oh_icon_path(const char *folder, const char *icon, char *out, size_t outlen)
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

bool oh_path_within(const char *path, const char *dir)
{
	if (!path || !dir) return false;
	size_t dl = strlen(dir);
	while (dl > 1 && dir[dl - 1] == '/') dl--;
	if (strncmp(path, dir, dl) != 0) return false;
	if (dl == 1) return path[0] == '/';
	return path[dl] == '/' || path[dl] == '\0';
}

const char *oh_icon_mime(const char *path)
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

bool oh_base64(const unsigned char *in, size_t len, char *out, size_t outlen)
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

size_t oh_last_lines(const char *buf, size_t len, int n)
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
