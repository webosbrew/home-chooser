/* SPDX-License-Identifier: Apache-2.0 */
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include "gate-mount.h"
#include "homekey.h"
#include "log.h"
#include "luna.h"
#include "policy.h"
#include "roles.h"
#include "watcher/watcher.h"

static char *homekey_call(const char *uri, const char *payload, int timeout, void *context)
{
    (void)context;
    GString *out = g_string_new(NULL);
    int rc = luna_run(uri, payload, 1, timeout, NULL, out);
    if (rc) { g_string_free(out, TRUE); return NULL; }
    return g_string_free(out, FALSE);
}

/* The 6-9 home gate follows a live filter change at once; a watcher may not see a disable. */
static void gate_follow(const char *action, gboolean stock, int rc, gboolean active)
{
    gboolean redirect = !strcmp(action, "apply") || !strcmp(action, "refresh");
    if (!redirect && strcmp(action, "stop") && strcmp(action, "clear")) return;
    int major = hc_gate_major(HC_OSRELEASE);
    if (major < 6 || major > 9) return;
    char *choice = redirect && !stock && rc == 0 && active ? hc_gate_wanted(HC_STATE_DIR, homekey_call, NULL) : NULL;
    HCGateOps ops = hc_gate_ops(homekey_call, NULL);
    /* A change of Home made in Home Chooser (the app wrote the previous-launcher file first): the panel may
     * be on screen, so the watcher closes stock Home later (watcher/previous.c), never this run. */
    ops.skip_close = g_file_test(HC_PREVIOUS_FILE, G_FILE_TEST_EXISTS);
    /* An explicit apply re-arms a gate that released itself. */
    if (!strcmp(action, "apply")) hc_gate_rearm(&ops);
    GString *log = g_string_new(NULL);
    HCGateResult result = hc_gate_sync(&ops, choice, log);
    if (result != HC_GATE_UNCHANGED && result != HC_GATE_RELEASED) LOGI("HOME_GATE", "%s: %s", action, log->str);
    g_string_free(log, TRUE); g_free(choice);
}

/* Daemon boot/watch and one-off Home-key commands never register an LS2 client. */
int hc_homekey_main(int argc, char **argv)
{
    if (geteuid() != 0) return 1;
    bool boot = !strcmp(argv[1], "--boot");
    if (boot) return hc_watcher_main();
    const char *action = boot ? "boot" : argc > 2 ? argv[2] : "status";
    if (strcmp(action, "boot") && strcmp(action, "status") && strcmp(action, "prepare") &&
        strcmp(action, "apply") && strcmp(action, "refresh") && strcmp(action, "stop") && strcmp(action, "clear")) return 2;
    HCKeyOps ops = {HC_STATE_DIR, "/usr/lib/qt5/qml/KeyFilters/systemUi.js", HC_OSRELEASE,
        homekey_call, NULL, 30, 1000};
    GString *out = g_string_new(NULL);
    gboolean stock = argc > 3 && !strcmp(argv[3], "stock");
    int rc = hc_key_run(&ops, action, stock, out);
    /* An unknown stock shape returns 0 with active=no: no filter, so no gate either. */
    gboolean active = strstr(out->str, "active=yes") != NULL;
    fputs(out->str, stdout); fflush(stdout); LOGI("HOMEKEY", "%s: %s", action, out->str);
    g_string_free(out, TRUE);
    gate_follow(action, stock, rc, active);
    return rc;
}
