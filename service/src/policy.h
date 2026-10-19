/* Pure policy of the Open House service: who may call, which paths and calls are allowed, which
 * commands run with which argv. No luna-service2, no JSON, so it builds and tests on the host. */
#ifndef OH_POLICY_H
#define OH_POLICY_H

#include <stdbool.h>
#include <stddef.h>

#define OH_APP_ID "org.webosbrew.openhouse"
#define OH_SERVICE_ID "org.webosbrew.openhouse.service"
#ifndef OH_APP_DIR
#define OH_APP_DIR "/media/developer/apps/usr/palm/applications/" OH_APP_ID
#endif
#define OH_STATE_DIR "/var/lib/webosbrew/openhouse"
#define OH_HOME_FILE OH_STATE_DIR "/home"
#define OH_KEYFILTER_FLAG OH_STATE_DIR "/keyfilter"
#define OH_OVERRIDE_FILE "/var/webos-profile/override/" OH_APP_ID ".json"
#define OH_INIT_DIR "/var/lib/webosbrew/init.d"
#define OH_INIT_LINK OH_INIT_DIR "/openhouse"
#define OH_OLD_INIT_LINK OH_INIT_DIR "/openhouse-keyfilter"
#define OH_ENTRY_SCRIPT OH_APP_DIR "/backport/openhouse"
#define OH_KEYFILTER_SCRIPT OH_APP_DIR "/backport/openhouse-keyfilter"
#define OH_WATCH_SCRIPT OH_APP_DIR "/backport/openhouse-home-watch"
#define OH_OSRELEASE "/etc/os-release"
#define OH_OSRELEASE_REAL "/usr/lib/os-release"
#define OH_WATCH_LOG "/tmp/openhouse-home.log"

/* Sender: the Open House app, as luna-service2 reports its application id. The id may carry an
 * instance suffix after a space ("org.webosbrew.openhouse 1234"). NULL and every other id: no. */
bool oh_sender_ok(const char *app_id);

/* Exact-match allow-list for readFile / writeFile / removeFile. */
bool oh_file_allowed(const char *path);
/* readFile also reads /etc/os-release (the app needs the release; a web app cannot read it). */
bool oh_file_readable(const char *path);
/* /etc/os-release is a symlink to ../usr/lib/os-release on webOS 6 and later. The service resolves
 * it with realpath() and reads it only when the result (a path with no symlinks) is one of the two. */
bool oh_osrelease_target_ok(const char *resolved);

/* How a forwarded call is checked beyond its URI. */
typedef enum {
	OH_CALL_DENIED = 0,
	OH_CALL_PLAIN,         /* payload forwarded as is */
	OH_CALL_SETTINGS_GET,  /* category general, keys [defaultApps] only */
	OH_CALL_SETTINGS_SET   /* category general, settings {defaultApps} only */
} oh_call_kind;

oh_call_kind oh_call_lookup(const char *uri);

/* The settings restriction: category must be "general" and the key list (the "keys" array of a
 * get, the key names of "settings" of a set) exactly [defaultApps]. */
bool oh_settings_ok(const char *category, const char *const *keys, size_t nkeys);

/* A command the service may run, by name. argv[0] is absolute; no shell. */
typedef struct {
	const char *name;
	const char *argv[6];
	int timeout_s;
	bool tolerant;    /* a non-zero exit is an answer, not an error */
	bool native_tail; /* not run: the last lines of OH_WATCH_LOG */
} oh_command;

const oh_command *oh_command_lookup(const char *name);

/* App icon: joins folder and icon (an absolute icon is taken as is), refusing relative folders,
 * ".." components, NULs and a result that does not fit. */
bool oh_icon_path(const char *folder, const char *icon, char *out, size_t outlen);
/* True when path is dir or lies below it (component-wise). */
bool oh_path_within(const char *path, const char *dir);
/* MIME type for an icon file name, NULL when its extension is not an image type. */
const char *oh_icon_mime(const char *path);

/* Standard base64 of len bytes into out (outlen must be at least 4 * ((len + 2) / 3) + 1). */
bool oh_base64(const unsigned char *in, size_t len, char *out, size_t outlen);

/* Offset into buf where the last n lines start (a trailing newline does not start a line). */
size_t oh_last_lines(const char *buf, size_t len, int n);

#endif
