/* SPDX-License-Identifier: Apache-2.0 */
/* Home gate mount lifecycle on webOS 6-9 (docs/home-gate.md). */
#include "internal.h"
#include "gate-mount.h"

/* choice NULL unmounts. 0 ok or nothing to do, 1 failed. */
int gate_apply(HCWatch *w, const char *choice)
{
    if (w->major < 6 || w->major > 9) return 0;
    if (w->ops.gate) return w->ops.gate(choice, w->ops.context);
    HCGateOps ops = hc_gate_ops(w->ops.call, w->ops.context);
    ops.gate_binary = w->c.gate_binary; ops.dir = w->c.gate_dir;
    GString *log = g_string_new(NULL);
    HCGateResult result = hc_gate_sync(&ops, choice, log);
    /* A release is logged once, not on every later check. */
    if (result != HC_GATE_UNCHANGED && (result != HC_GATE_RELEASED || !w->gate_released))
        watch_log(w, "home gate: %s", result == HC_GATE_RELEASED && !log->len ?
            "the gate restored stock Home itself; not mounted again until the choice changes or a reboot" : log->str);
    w->gate_released = result == HC_GATE_RELEASED;
    w->gate_active = result == HC_GATE_MOUNTED ||
        (result == HC_GATE_UNCHANGED && choice && g_file_test(ops.gate_binary, G_FILE_TEST_IS_REGULAR));
    g_string_free(log, TRUE);
    return result == HC_GATE_FAILED;
}

/* Mounted only while the redirect is live for an installed, non-stock choice: the Home key
 * filter is baked for it, the flag is set and the watcher is running. */
void gate_sync(HCWatch *w)
{
    if (w->major < 6 || w->major > 9) return;
    char *id = w->status < 0 ? watch_choice(w) : NULL;
    char *flag = g_build_filename(w->c.state_dir, "homekey", NULL);
    gboolean want = id && !stock_id(id) && !w->missing && eq(w->baked, id) && g_file_test(flag, G_FILE_TEST_EXISTS);
    gate_apply(w, want ? id : NULL);
    g_free(id); g_free(flag);
}
