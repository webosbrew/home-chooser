/* SPDX-License-Identifier: Apache-2.0 */
/* Home gate mount lifecycle on webOS 6-9 (docs/home-gate.md). */
#include "internal.h"
#include "gate-mount.h"

static HCGateOps gate_ops(HCWatch *w)
{
    HCGateOps ops = hc_gate_ops(w->ops.call, w->ops.context);
    ops.gate_binary = w->c.gate_binary; ops.dir = w->c.gate_dir; ops.proc = w->c.proc; ops.skip_close = TRUE;
    return ops;
}
static gboolean gate_retry(gpointer context);
static gboolean gate_settled(gpointer context);
/* One attempt to close a stock Home that is the real binary (docs/home-gate.md). Retried on the
 * preload cadence, bounded, and at each gate sync, until `running` or closeByAppId confirms. */
static void gate_close(HCWatch *w)
{
    HCGateOps ops = gate_ops(w);
    GString *log = g_string_new(NULL);
    gboolean closed = FALSE;
    ops.closed = &closed;
    if (hc_gate_close_stock(&ops, log)) w->gate_pending = FALSE;
    watch_log(w, "home gate: %s%s", log->str, w->gate_pending ? "close unconfirmed" : "stock Home close confirmed");
    g_string_free(log, TRUE);
    /* A Home that was really running and is now closed: SAM may have nothing to put in front (6.5.2
     * boot). Look once after SAM's idle relaunch had its chance. */
    if (closed && !w->gate_settle_source) w->gate_settle_source = g_timeout_add(w->c.gate_settle_ms, gate_settled, w);
    if (w->gate_pending && !w->gate_source && w->gate_attempts < w->c.preload_tries)
        w->gate_source = g_timeout_add(w->c.preload_retry_ms, gate_retry, w);
}
static gboolean gate_retry(gpointer context)
{
    HCWatch *w = context;
    w->gate_source = 0; w->gate_attempts++;
    if (!w->gate_pending || !w->gate_active) return G_SOURCE_REMOVE;
    w->depth++;
    gate_close(w);
    w->depth--;
    if (w->ops.wake) w->ops.wake(w->ops.context);
    return G_SOURCE_REMOVE;
}

/* Once per close. Still no foreground app: launch stock Home's id, so the gate runs and decides
 * (overlay: last input, full-screen: the launcher). Never when something is in front, or when the
 * gate is no longer mounted. */
static gboolean gate_settled(gpointer context)
{
    HCWatch *w = context;
    w->gate_settle_source = 0;
    if (w->status >= 0 || !w->gate_active || w->gate_released) return G_SOURCE_REMOVE;
    w->depth++;
    jvalue_ref r = watch_call(w, SAM "getForegroundAppInfo", "{}", 5);
    char *front = reply_ok(r) ? get_string(r, "appId") : NULL;
    if (!r || !reply_ok(r)) watch_log(w, "home gate: foreground unknown after the stock Home close; nothing launched");
    else if (front && *front) watch_log(w, "home gate: %s in front after the stock Home close; nothing launched", front);
    else {
        jvalue_ref l = watch_id_call(w, SAM "launch", STOCK, 5);
        watch_log(w, "home gate: nothing in front after the stock Home close; launched %s so the gate runs: %s", STOCK, l ? jvalue_stringify(l) : "no reply");
        j_release(&l);
    }
    g_free(front); j_release(&r);
    w->depth--;
    if (w->ops.wake) w->ops.wake(w->ops.context);
    return G_SOURCE_REMOVE;
}

/* choice NULL unmounts. 0 ok or nothing to do, 1 failed. */
int gate_apply(HCWatch *w, const char *choice)
{
    if (w->major < 6 || w->major > 9) return 0;
    gboolean was_active = w->gate_active;
    int failed;
    HCGateResult result = HC_GATE_UNCHANGED;
    if (w->ops.gate) {
        failed = w->ops.gate(choice, w->ops.context);
        w->gate_active = choice && !failed;
    } else {
        HCGateOps ops = gate_ops(w);
        GString *log = g_string_new(NULL);
        result = hc_gate_sync(&ops, choice, log);
        /* A release is logged once, not on every later check. */
        if (result != HC_GATE_UNCHANGED && (result != HC_GATE_RELEASED || !w->gate_released))
            watch_log(w, "home gate: %s", result == HC_GATE_RELEASED && !log->len ?
                "the gate restored stock Home itself; not mounted again until the choice changes or a reboot" : log->str);
        w->gate_released = result == HC_GATE_RELEASED;
        w->gate_active = result == HC_GATE_MOUNTED ||
            (result == HC_GATE_UNCHANGED && choice && g_file_test(ops.gate_binary, G_FILE_TEST_IS_REGULAR));
        g_string_free(log, TRUE);
        failed = result == HC_GATE_FAILED;
    }
    if (!w->gate_active || result == HC_GATE_RELEASED) {
        /* Unmounted or released: nothing left to close, and a pending retry must not fire. */
        w->gate_pending = FALSE;
        if (w->gate_source) { g_source_remove(w->gate_source); w->gate_source = 0; }
        if (w->gate_settle_source) { g_source_remove(w->gate_settle_source); w->gate_settle_source = 0; }
    } else if (!was_active || result == HC_GATE_MOUNTED) { w->gate_pending = TRUE; w->gate_attempts = 0; }
    if (w->gate_pending) gate_close(w);
    return failed;
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
