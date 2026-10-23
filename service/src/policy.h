/* Pure policy of the Home Chooser service: who may call, which paths and calls are allowed, which
 * commands run with which argv. No luna-service2, no JSON, so it builds and tests on the host. */
#ifndef HC_POLICY_H
#define HC_POLICY_H

#include <stdbool.h>
#include <stddef.h>

#define HC_APP_ID "org.webosbrew.homechooser"
#define HC_SERVICE_ID "org.webosbrew.homechooser.service"
#ifndef HC_APP_DIR
#define HC_APP_DIR "/media/developer/apps/usr/palm/applications/" HC_APP_ID
#endif
#define HC_STATE_DIR "/var/lib/webosbrew/homechooser"
#define HC_HOME_FILE HC_STATE_DIR "/home"
#define HC_KEYFILTER_FLAG HC_STATE_DIR "/keyfilter"
#define HC_GENERATED_FLAG HC_STATE_DIR "/keyfilter-generated"
#define HC_OVERRIDE_FILE "/var/webos-profile/override/" HC_APP_ID ".json"
#define HC_INIT_DIR "/var/lib/webosbrew/init.d"
#define HC_INIT_LINK HC_INIT_DIR "/homechooser"
#define HC_OLD_INIT_LINK HC_INIT_DIR "/homechooser-keyfilter"
#define HC_ENTRY_SCRIPT HC_APP_DIR "/backport/homechooser"
#define HC_KEYFILTER_SCRIPT HC_APP_DIR "/backport/homechooser-keyfilter"
#define HC_WATCH_SCRIPT HC_APP_DIR "/backport/homechooser-home-watch"
#define HC_OSRELEASE "/etc/os-release"
#define HC_OSRELEASE_REAL "/usr/lib/os-release"
#define HC_WATCH_LOG "/tmp/homechooser-home.log"

/* Sender: the Home Chooser app, as luna-service2 reports its application id. The id may carry an
 * instance suffix after a space ("org.webosbrew.homechooser 1234"). NULL and every other id: no. */
bool hc_sender_ok(const char *app_id);

/* Exact-match allow-list for readFile / writeFile / removeFile. */
bool hc_file_allowed(const char *path);
/* readFile also reads /etc/os-release (the app needs the release; a web app cannot read it). */
bool hc_file_readable(const char *path);
/* /etc/os-release is a symlink to ../usr/lib/os-release on webOS 6 and later. The service resolves
 * it with realpath() and reads it only when the result (a path with no symlinks) is one of the two. */
bool hc_osrelease_target_ok(const char *resolved);

/* How a forwarded call is checked beyond its URI. */
typedef enum {
	HC_CALL_DENIED = 0,
	HC_CALL_PLAIN,         /* payload forwarded as is */
	HC_CALL_SETTINGS_GET,  /* category general, keys [defaultApps] only */
	HC_CALL_SETTINGS_SET   /* category general, settings {defaultApps} only */
} hc_call_kind;

hc_call_kind hc_call_lookup(const char *uri);

/* The settings restriction: category must be "general" and the key list (the "keys" array of a
 * get, the key names of "settings" of a set) exactly [defaultApps]. */
bool hc_settings_ok(const char *category, const char *const *keys, size_t nkeys);

/* A command the service may run, by name. argv[0] is absolute; no shell. */
typedef struct {
	const char *name;
	const char *argv[6];
	int timeout_s;
	bool tolerant;    /* a non-zero exit is an answer, not an error */
	bool native_tail; /* not run: the last lines of HC_WATCH_LOG */
} hc_command;

const hc_command *hc_command_lookup(const char *name);

/* App icon: joins folder and icon (an absolute icon is taken as is), refusing relative folders,
 * ".." components, NULs and a result that does not fit. */
bool hc_icon_path(const char *folder, const char *icon, char *out, size_t outlen);
/* True when path is dir or lies below it (component-wise). */
bool hc_path_within(const char *path, const char *dir);
/* MIME type for an icon file name, NULL when its extension is not an image type. */
const char *hc_icon_mime(const char *path);

/* Standard base64 of len bytes into out (outlen must be at least 4 * ((len + 2) / 3) + 1). */
bool hc_base64(const unsigned char *in, size_t len, char *out, size_t outlen);

/* Offset into buf where the last n lines start (a trailing newline does not start a line). */
size_t hc_last_lines(const char *buf, size_t len, int n);

#endif
