/* SPDX-License-Identifier: Apache-2.0 */
/* Pure policy of the Home Chooser service: who may call, which paths and calls are allowed, which
 * commands run with which argv. No luna-service2, no JSON, so it builds and tests on the host. */
#ifndef HC_POLICY_H
#define HC_POLICY_H

#include <stdbool.h>
#include <stddef.h>

#define HC_APP_ID "org.webosbrew.homechooser"
#define HC_SERVICE_ID "org.webosbrew.homechooser.service"
#define HC_SERVICE_DIR "/media/developer/apps/usr/palm/services/" HC_SERVICE_ID
#ifndef HC_APP_DIR
#define HC_APP_DIR "/media/developer/apps/usr/palm/applications/" HC_APP_ID
#endif
#define HC_STATE_DIR "/var/lib/webosbrew/homechooser"
#define HC_HOME_FILE HC_STATE_DIR "/home"
#define HC_HOMEKEY_FLAG HC_STATE_DIR "/homekey"
#define HC_OVERRIDE_FILE "/var/webos-profile/override/" HC_APP_ID ".json"
#define HC_INIT_DIR "/var/lib/webosbrew/init.d"
#define HC_INIT_LINK HC_INIT_DIR "/homechooser"
#define HC_ENTRY_SCRIPT HC_SERVICE_DIR "/scripts/homechooser"
#define HC_BOOT_DIR HC_STATE_DIR "/boot"
#define HC_BOOT_SCRIPT HC_BOOT_DIR "/homechooser"
#define HC_HOMEKEY_SCRIPT HC_SERVICE_DIR "/scripts/homechooser-homekey"
#define HC_ACCESS_SCRIPT HC_SERVICE_DIR "/scripts/homechooser-access"
#define HC_OSRELEASE "/etc/os-release"
#define HC_OSRELEASE_REAL "/usr/lib/os-release"
#define HC_WATCH_LOG "/tmp/homechooser-home.log"
/* Home gate, webOS 6-9 (docs/home-gate.md). /tmp is tmpfs on every 6-9 rootfs, so the gate copy
 * and its release marker are gone after a reboot. */
#define HC_GATE_NAME "homechooser-home-gate"
#define HC_GATE_BINARY HC_SERVICE_DIR "/" HC_GATE_NAME
#define HC_GATE_TARGET "/usr/bin/" HC_STOCK_HOME_ID
#define HC_GATE_DIR "/tmp/homechooser-runtime/home-gate"

/* The launcher-facing API (docs/launcher-api.md). */
#define HC_API_VERSION 1
#define HC_GROUP "org.webosbrew.homechooser.applications"
#define HC_HBC_APP_ID "org.webosbrew.hbchannel"
#define HC_STOCK_HOME_ID "com.webos.app.home"
/* Every privileged luna call the service makes through luna-send runs as this identity (`luna-send
 * -a`), never as a bare root luna-send: a bare one only has the `all` group, which the ACG check
 * treats as an ordinary group, so methods in groups such as appinstalld.internal may be denied and
 * createAlert is refused. Device-unverified per release. */
#define HC_LUNA_IDENTITY "com.webos.applicationManager"
#define HC_TOKEN_LEN 32

/* Sender: the Home Chooser app, as luna-service2 reports its application id. The id may carry an
 * instance suffix after a space ("org.webosbrew.homechooser 1234"). NULL and every other id: no. */
bool hc_sender_ok(const char *app_id);
/* Alert callbacks are sent by Surface Manager using our createAlert caller identity. */

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
	const char *argv[8];
	int timeout_s;
	bool tolerant;    /* a non-zero exit is an answer, not an error */
	bool native_tail; /* not run: the last lines of HC_WATCH_LOG */
} hc_command;

const hc_command *hc_command_lookup(const char *name);

/* App icon: joins folder and icon (an absolute icon is taken as is), refusing relative folders,
 * ".." components, NULs and a result that does not fit. */
bool hc_icon_path(const char *folder, const char *icon, char *out, size_t outlen);
/* Resolve an appinfo icon under the real app folder; rejects traversal and symlink escapes. */
bool hc_icon_resolve(const char *folder, const char *icon, char *out, size_t outlen);
/* True when path is dir or lies below it (component-wise). */
bool hc_path_within(const char *path, const char *dir);
/* MIME type for an icon file name, NULL when its extension is not an image type. */
const char *hc_icon_mime(const char *path);

/* Standard base64 of len bytes into out (outlen must be at least 4 * ((len + 2) / 3) + 1). */
bool hc_base64(const unsigned char *in, size_t len, char *out, size_t outlen);

/* Offset into buf where the last n lines start (a trailing newline does not start a line). */
size_t hc_last_lines(const char *buf, size_t len, int n);

/* ---- launcher API ---- */

/* An app id: 1 to 128 characters of letters, digits, '.', '-' and '_'. */
bool hc_app_id_ok(const char *id);
/* The caller's app id from LSMessageGetApplicationID: NULL, empty and invalid ids are refused (a
 * service or luna-send caller has none); an instance suffix after a space is dropped. */
bool hc_caller_id(const char *raw, char *out, size_t outlen);
/* True when the caller asked for the group: perms (the appinfo's requiredPermissions) holds it. */
bool hc_perms_grant(const char *const *perms, size_t n);
/* True when caller is the app named by the choice file's text (trimmed; empty is nobody). */
bool hc_is_selected_home(const char *caller, const char *choice_text);
/* The app fields launcher listApps asks sam for and forwards, NULL-terminated. Additive within an
 * apiVersion: a field may be added, never removed or renamed. appCategory lets a launcher find the
 * other launchers. */
extern const char *const hc_launcher_list_fields[];

typedef enum {
	HC_REMOVE_OK = 0,
	HC_REMOVE_BADID,
	HC_REMOVE_SELF,    /* Home Chooser */
	HC_REMOVE_HBC,     /* Homebrew Channel */
	HC_REMOVE_CALLER,  /* the app that asks */
	HC_REMOVE_SYSTEM   /* removable false, a system folder, or not provably a user app */
} hc_remove_verdict;

/* removable: 1 / 0 from the app info, -1 when it has no such field. */
hc_remove_verdict hc_remove_check(const char *id, const char *caller, int removable, const char *folder);
const char *hc_remove_verdict_text(hc_remove_verdict v);
/* True only for a SAM error that explicitly says the target app is absent. */
bool hc_sam_app_missing(const char *error_text);
/* SAM uses this text for both a missing id and an unsupported installed app type. */
bool hc_sam_app_ambiguous(const char *error_text);
/* Launcher failure classification shared by the handlers. */
const char *hc_unusable_reply_code(int exit_status);
const char *hc_sam_refusal_code(bool target_lookup, const char *error_text);
const char *hc_remove_verdict_code(hc_remove_verdict verdict);

/* Folders user apps live in: developer-mode (dev/remove) and store (appInstallService/remove). */
typedef enum { HC_FOLDER_OTHER = 0, HC_FOLDER_DEV, HC_FOLDER_STORE } hc_folder_kind;
hc_folder_kind hc_folder_kind_of(const char *folder);

/* Calls the service makes through luna-send with HC_LUNA_IDENTITY. */
typedef struct {
	const char *uri;
	char payload[320];
	int timeout_s;
	bool progress; /* a stream that ends with status 31 (done) or 25 (failed) */
} hc_remove_call;

/* The removal steps for an app, in order: 0 removeLaunchPoint (LG's own route, with its
 * removability check), 1 appInstallService/dev/remove or /remove by the folder. false past the end. */
bool hc_remove_step(int step, const char *id, const char *folder, hc_remove_call *out);
/* 1 done, -1 failed, 0 not yet: scans a progress stream for "status": 31 / 25 and returnValue false. */
int hc_progress_state(const char *text);
/* 1 accepted, 0 refused by LG ("is not removable"), -1 failed otherwise (false returnValue, no reply). */
int hc_remove_reply_class(const char *text);

/* argv for a luna-send run: {luna-send, -a, identity, -n, N, uri, payload}. Only URIs on the
 * list below. argv needs 8 slots. false for an unlisted uri. */
bool hc_luna_argv(const char *uri, const char *payload, int replies, const char **argv);
bool hc_luna_uri_allowed(const char *uri);

/* A one-shot uninstall token: exactly HC_TOKEN_LEN lowercase hex digits. */
bool hc_token_ok(const char *token);

/* The Home key hook worker's `status` output holds a line that is exactly `reboot=needed` (CR and
 * blanks at the line ends are ignored; a substring of a longer line does not count). NULL: no. */
bool hc_reboot_needed(const char *status_text);
/* The text of the restart prompt for rebootPrompt's reason ("apply" or "remove"); NULL otherwise. */
const char *hc_reboot_message(const char *reason);

#endif
