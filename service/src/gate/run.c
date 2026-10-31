/* SPDX-License-Identifier: Apache-2.0 */
#define _GNU_SOURCE
#include "gate/gate.h"
#include "gate-mount.h"
#include "fs.h"
#include "json.h"
#include "log.h"
#include "policy.h"
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/mount.h>
#include <unistd.h>

HCGateConfig hc_gate_config(void)
{
    HCGateConfig c = {"/usr/bin/luna-send", HC_STATE_DIR, HC_GATE_TARGET, HC_GATE_DIR, HC_WATCH_LOG, 2, 3, NULL, NULL};
    return c;
}

static void gate_log(const HCGateConfig *c, const char *format, ...) G_GNUC_PRINTF(2, 3);
static void gate_log(const HCGateConfig *c, const char *format, ...)
{
    va_list ap; va_start(ap, format);
    char *message = g_strdup_vprintf(format, ap); va_end(ap);
    LOGI("HOME_GATE", "%s", message);
    GDateTime *now = g_date_time_new_now_local();
    char *stamp = g_date_time_format(now, "%Y-%m-%d %H:%M:%S");
    char *line = g_strdup_printf("%s home gate: %s\n", stamp, message);
    int fd = open(c->log, O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0644);
    if (fd >= 0) { ssize_t ignored = write(fd, line, strlen(line)); (void)ignored; close(fd); }
    g_free(line); g_free(stamp); g_date_time_unref(now); g_free(message);
}

/* reply, when given, gets SAM's answer as text ("no reply" when luna-send gave none: timeout, error). */
static gboolean launch(const HCGateConfig *c, const char *identity, const char *id, char **reply)
{
    jvalue_ref p = jobject_create(); put_str(p, "id", id);
    jvalue_ref r = hc_gate_call(c, identity, "luna://com.webos.applicationManager/launch", jvalue_stringify(p), c->launch_timeout_s);
    gboolean ok = reply_ok(r);
    if (reply) *reply = g_strdup(r ? jvalue_stringify(r) : "no reply");
    j_release(&r); j_release(&p); return ok;
}

/* Stock Home only works from /usr/bin/com.webos.app.home: ls-hubd gives its LS2 role by exe path.
 * So the gate removes itself and has SAM start the real binary. The marker, written first, tells
 * the daemon not to mount again for this choice; a new choice, an apply or a reboot re-arms. */
static int release(const HCGateConfig *c, const char *choice)
{
    char *marker = hc_gate_marker(c->dir);
    if (!mkdir_p(c->dir) || !write_atomic(marker, choice ? choice : "", choice ? strlen(choice) : 0))
        gate_log(c, "cannot write %s", marker);
    g_free(marker);
    /* Lazy: this process runs from the bound file. */
    int rc = c->unmount ? c->unmount(c->target, c->context) : umount2(c->target, MNT_DETACH);
    if (rc && errno != EINVAL) {
        /* Still mounted: a launch would only start the gate again. */
        gate_log(c, "cannot unmount %s: %s; stock Home not started", c->target, g_strerror(errno));
        return 1;
    }
    char *reply = NULL;
    gboolean ok = launch(c, HC_LUNA_IDENTITY, HC_STOCK_HOME_ID, &reply);
    /* SAM's answer is not proof either way: the real Home has come up after a "no reply" or a false. */
    gate_log(c, "gate removed from %s%s; asked SAM to start stock Home: %s%s", c->target, rc ? " (was not mounted)" : "",
        ok ? "accepted" : "not confirmed, ", ok ? "" : reply);
    g_free(reply);
    return 0;
}

int hc_gate_main(const HCGateConfig *c, int argc, char **argv)
{
    HCGateDecision d = hc_gate_decide(c, argc > 1 ? argv[1] : NULL);
    int rc = 0;
    if (d.action == HC_GATE_LAUNCH) {
        /* As com.webos.app.home, the caller SAM expects for a launch made by Home. */
        if (launch(c, HC_STOCK_HOME_ID, d.target, NULL)) gate_log(c, "%s: opened %s", d.why, d.target);
        else {
            gate_log(c, "%s: launch of %s failed, restoring stock Home", d.why, d.target);
            d.action = HC_GATE_RELEASE;
        }
    } else gate_log(c, "%s: %s", d.why, d.action == HC_GATE_NOTHING ? "nothing opened" : "restoring stock Home");
    if (d.action == HC_GATE_RELEASE) rc = release(c, d.choice);
    hc_gate_decision_clear(&d);
    return rc;
}
