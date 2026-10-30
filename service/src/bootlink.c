/* SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "fs.h"
#include "proc.h"
#include "json.h"
#include "log.h"
#include "policy.h"
#include "service/methods.h"
#include "service/reply.h"

bool on_link(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	if (!gate(msg, "link", true)) return true;
	static const char *const scripts[] = {HC_ENTRY_SCRIPT, HC_HOMEKEY_SCRIPT, HC_WATCH_SCRIPT, HC_ACCESS_SCRIPT, NULL};
	if (!mkdir_p(HC_INIT_DIR)) {
		send_error(msg, "linkFailed", "Could not create %s: %s", HC_INIT_DIR, strerror(errno));
		return true;
	}
	for (int i = 0; scripts[i]; i++) {
		if (chmod(scripts[i], 0755) != 0) {
			send_error(msg, "linkFailed", "Could not make %s executable: %s", scripts[i], strerror(errno));
			return true;
		}
	}
    /* Only legacy releases retain a persistent watcher restore copy. 6-9 volatile
     * filters revert at reboot without an installed recovery binary. */
    char *release_text = NULL;
    slurp(HC_OSRELEASE_REAL, 65536, &release_text, NULL);
    if (!release_text) slurp(HC_OSRELEASE, 65536, &release_text, NULL);
    GRegex *rx = g_regex_new("(?m)^VERSION_ID=[\"']?([0-9]+)", 0, 0, NULL);
    GMatchInfo *mi = NULL;
    int major = 0;
    if (release_text && g_regex_match(rx, release_text, 0, &mi)) {
        char *v = g_match_info_fetch(mi, 1); major = atoi(v); g_free(v);
    }
    g_match_info_free(mi); g_regex_unref(rx); g_free(release_text);
    bool live = major >= 6 && major <= 9;
    const char *target = live ? HC_ENTRY_SCRIPT : HC_BOOT_SCRIPT;
    if (!live) {
        GString *out = g_string_new(NULL), *err = g_string_new(NULL);
        const char *const stage[] = {"/bin/sh", HC_ENTRY_SCRIPT, "stage", NULL};
        int rc = run_argv(stage, 20, out, err, NULL);
        g_string_free(out, TRUE); g_string_free(err, TRUE);
        if (rc || access(target, X_OK)) { send_error(msg, "linkFailed", "Could not stage the legacy boot restore copy"); return true; }
    }
	/* a link is replaced, a regular file from an earlier install too */
	if (unlink(HC_INIT_LINK) != 0 && errno != ENOENT) {
		send_error(msg, "linkFailed", "Could not replace %s: %s", HC_INIT_LINK, strerror(errno));
		return true;
	}
	if (symlink(target, HC_INIT_LINK) != 0) {
		send_error(msg, "linkFailed", "Could not link %s: %s", HC_INIT_LINK, strerror(errno));
		return true;
	}
	LOGI("LINKED", "%s -> %s", HC_INIT_LINK, target);
	send_ok(msg);
	return true;
}

bool on_unlink(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	if (!gate(msg, "unlink", true)) return true;
	bool ok = true;
	if (unlink(HC_INIT_LINK) != 0 && errno != ENOENT) ok = false;
	if (!ok) {
		send_error(msg, "unlinkFailed", "Could not remove the boot entry: %s", strerror(errno));
		return true;
	}
    GString *out = g_string_new(NULL), *err = g_string_new(NULL);
    const char *const unstage[] = {"/bin/sh", HC_ENTRY_SCRIPT, "unstage", NULL};
    int rc = run_argv(unstage, 20, out, err, NULL);
    g_string_free(out, TRUE); g_string_free(err, TRUE);
    if (rc) { send_error(msg, "unlinkFailed", "Could not remove legacy boot restore copy"); return true; }
	LOGI("UNLINKED", "%s", HC_INIT_LINK);
	send_ok(msg);
	return true;
}

/* entry: none | file | link | dangling, and the link target. */
bool on_link_status(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	if (!gate(msg, "linkStatus", true)) return true;
	struct stat st;
	char target[512] = "";
	const char *entry = "none";
	if (lstat(HC_INIT_LINK, &st) == 0) {
		if (S_ISLNK(st.st_mode)) {
			ssize_t n = readlink(HC_INIT_LINK, target, sizeof target - 1);
			target[n > 0 ? n : 0] = '\0';
			entry = stat(HC_INIT_LINK, &st) == 0 ? "link" : "dangling";
		} else {
			entry = "file";
		}
	}
	jvalue_ref o = jobject_create();
	put_bool(o, "returnValue", true);
	put_str(o, "entry", entry);
	put_str(o, "target", target);
	send_reply(msg, o);
	return true;
}
