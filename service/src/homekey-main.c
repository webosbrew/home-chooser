/* SPDX-License-Identifier: Apache-2.0 */
#include <stdio.h>
#include <string.h>
#include <unistd.h>
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

/* Boot and watcher share one process and one private Luna client; no RPC registration. */
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
    int rc = hc_key_run(&ops, action, argc > 3 && !strcmp(argv[3], "stock"), out);
    fputs(out->str, stdout); fflush(stdout); LOGI("HOMEKEY", "%s: %s", action, out->str);
    g_string_free(out, TRUE);
    return rc;
}
