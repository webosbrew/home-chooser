/* SPDX-License-Identifier: Apache-2.0 */
#define _GNU_SOURCE
#include "gate-mount.h"
#include "fs.h"
#include "json.h"
#include "policy.h"
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

#define SAM "luna://com.webos.applicationManager/"
#define MAX_POINTS 32

/* target: 0 none, 1 ours, 2 foreign. copies: other mount points of our gate (propagation peers). */
typedef struct {
    int target;
    unsigned copies;
    char *copy[MAX_POINTS];
} Mounts;

static int real_bind(const char *source, const char *target, void *context)
{
    (void)context; return mount(source, target, NULL, MS_BIND, NULL);
}
/* Lazy: a running gate keeps its inode; the path is released at once. */
static int real_unbind(const char *target, void *context)
{
    (void)context; return umount2(target, MNT_DETACH);
}
HCGateOps hc_gate_ops(char *(*call)(const char *, const char *, int, void *), void *context)
{
    HCGateOps o = {HC_GATE_BINARY, HC_GATE_DIR, HC_GATE_TARGET, "/proc/self/mountinfo", NULL, NULL, call, context};
    return o;
}
char *hc_gate_marker(const char *dir) { return g_build_filename(dir, "released", NULL); }
static char *staged(const HCGateOps *o) { return g_build_filename(o->dir, HC_GATE_NAME, NULL); }

/* mountinfo escapes space, tab, newline and backslash as \ooo. */
static char *unescape(const char *s)
{
    char *out = g_malloc(strlen(s) + 1), *p = out;
    while (*s) {
        if (s[0] == '\\' && s[1] >= '0' && s[1] <= '3' && s[2] >= '0' && s[2] <= '7' && s[3] >= '0' && s[3] <= '7') {
            *p++ = (char)((s[1] - '0') * 64 + (s[2] - '0') * 8 + (s[3] - '0')); s += 4;
        } else *p++ = *s++;
    }
    *p = 0; return out;
}
/* The tmpfs the copy lives on, as mountinfo's major:minor. NULL when it cannot be read. */
static char *gate_device(const HCGateOps *o)
{
    struct stat st;
    char *parent = g_path_get_dirname(o->dir);
    gboolean ok = !stat(parent, &st);
    g_free(parent);
    return ok ? g_strdup_printf("%u:%u", major(st.st_dev), minor(st.st_dev)) : NULL;
}
static void mounts_clear(Mounts *m)
{
    for (unsigned i = 0; i < m->copies; i++) g_free(m->copy[i]);
    m->copies = 0;
}
/* Ours: the bind root is our staged copy (by name) on our tmpfs (by device). The device check keeps
 * a same-named file elsewhere from counting; without a readable device the name decides. */
static Mounts read_mounts(const HCGateOps *o)
{
    Mounts m = {0};
    char *text = NULL, *dev = gate_device(o);
    const char *suffix = "/usr/bin/" HC_STOCK_HOME_ID;
    if (!g_file_get_contents(o->mountinfo, &text, NULL, NULL)) { g_free(dev); return m; }
    char **lines = g_strsplit(text, "\n", -1);
    for (int i = 0; lines[i]; i++) {
        char **f = g_strsplit(lines[i], " ", 6);
        if (g_strv_length(f) >= 5) {
            char *root = unescape(f[3]), *point = unescape(f[4]);
            const char *base = strrchr(root, '/');
            gboolean ours = base && !strcmp(base + 1, HC_GATE_NAME) && (!dev || !strcmp(f[2], dev));
            /* The last entry for a mount point is the visible one. */
            if (!strcmp(point, o->target)) m.target = ours ? 1 : 2;
            else if (ours && g_str_has_suffix(point, suffix) && m.copies < MAX_POINTS) m.copy[m.copies++] = g_strdup(point);
            g_free(root); g_free(point);
        }
        g_strfreev(f);
    }
    g_strfreev(lines); g_free(text); g_free(dev); return m;
}
static int do_bind(const HCGateOps *o, const char *source, const char *target)
{
    return o->bind ? o->bind(source, target, o->context) : real_bind(source, target, o->context);
}
static int do_unbind(const HCGateOps *o, const char *target)
{
    return o->unbind ? o->unbind(target, o->context) : real_unbind(target, o->context);
}
static void remove_files(const HCGateOps *o)
{
    char *copy = staged(o), *marker = hc_gate_marker(o->dir);
    unlink(copy); unlink(marker); rmdir(o->dir);
    g_free(copy); g_free(marker);
}
/* A tmpfs copy: the bind never pins the package, so uninstall cannot leave it pointing there. */
static gboolean stage_gate(const HCGateOps *o)
{
    char *bytes = NULL, *copy = staged(o), *temp = g_strconcat(copy, ".new", NULL);
    gsize size = 0;
    gboolean ok = mkdir_p(o->dir) && chmod(o->dir, 0700) == 0 &&
        g_file_get_contents(o->gate_binary, &bytes, &size, NULL) && size > 0 &&
        write_atomic(temp, bytes, size) && chmod(temp, 0755) == 0 && rename(temp, copy) == 0;
    if (!ok) unlink(temp);
    g_free(bytes); g_free(copy); g_free(temp); return ok;
}
static gboolean same_bytes(const char *a, const char *b)
{
    char *x = NULL, *y = NULL;
    gsize nx = 0, ny = 0;
    gboolean same = g_file_get_contents(a, &x, &nx, NULL) && g_file_get_contents(b, &y, &ny, NULL) &&
        nx == ny && !memcmp(x, y, nx);
    g_free(x); g_free(y); return same;
}
static gboolean stock_running(const HCGateOps *o)
{
    char *text = o->call ? o->call(SAM "running", "{}", 5, o->context) : NULL;
    jvalue_ref r = parse_object(text), list = r ? jobject_get(r, key_buf("running")) : NULL;
    gboolean found = FALSE;
    for (ssize_t i = 0; list && jis_array(list) && i < jarray_size(list) && !found; i++) {
        char *id = get_string(jarray_get(list, i), "id");
        found = !g_strcmp0(id, HC_STOCK_HOME_ID); g_free(id);
    }
    j_release(&r); g_free(text); return found;
}
/* Also an instance the preload manager started before the mount. */
static void close_stock(const HCGateOps *o, GString *log)
{
    if (!stock_running(o)) return;
    char *text = o->call(SAM "closeByAppId", "{\"id\":\"" HC_STOCK_HOME_ID "\",\"forceMode\":true}", 5, o->context);
    g_string_append_printf(log, "closeByAppId %s: %s; ", HC_STOCK_HOME_ID, text ? g_strstrip(text) : "no reply");
    g_free(text);
}

/* The target first (while ours, stacked included), then every propagated copy. On a shared root
 * the target's unmount propagates to its peers; any copy left is unmounted by its own path. Only
 * mount points are touched, never files under them (the devmode jail copies included). */
static HCGateResult unmount_gate(const HCGateOps *o, gboolean files, GString *log)
{
    Mounts m = read_mounts(o);
    HCGateResult result = HC_GATE_UNCHANGED;
    for (int n = 0; m.target == 1 && n < 8; n++) {
        if (do_unbind(o, o->target)) {
            g_string_append_printf(log, "cannot unmount the gate from %s: %s", o->target, g_strerror(errno));
            mounts_clear(&m); return HC_GATE_FAILED;
        }
        result = HC_GATE_UNMOUNTED;
        mounts_clear(&m); m = read_mounts(o);
    }
    for (int n = 0; m.target != 1 && m.copies && n < 4; n++) {
        for (unsigned i = 0; i < m.copies; i++) {
            if (do_unbind(o, m.copy[i]) && errno != EINVAL && errno != ENOENT) {
                g_string_append_printf(log, "cannot unmount the gate copy %s: %s", m.copy[i], g_strerror(errno));
                mounts_clear(&m); return HC_GATE_FAILED;
            }
            result = HC_GATE_UNMOUNTED;
        }
        g_string_append_printf(log, "unmounted %u propagated copies; ", m.copies);
        mounts_clear(&m); m = read_mounts(o);
    }
    gboolean left = m.target == 1 || m.copies;
    mounts_clear(&m);
    if (left) { g_string_append(log, "gate mounts remain after unmounting"); return HC_GATE_FAILED; }
    if (files) remove_files(o);
    if (result == HC_GATE_UNMOUNTED) g_string_append(log, "home gate unmounted, stock Home restored");
    return result;
}

static HCGateResult mount_gate(const HCGateOps *o, GString *log)
{
    Mounts m = read_mounts(o);
    int target = m.target;
    mounts_clear(&m);
    char *copy = staged(o);
    if (target == 1) {
        gboolean current = same_bytes(copy, o->gate_binary);
        if (current) { g_free(copy); return HC_GATE_UNCHANGED; }
        /* A package update brought a new gate: swap it. */
        g_string_append(log, "gate binary changed, remounting it; ");
        if (unmount_gate(o, FALSE, log) == HC_GATE_FAILED) { g_free(copy); return HC_GATE_FAILED; }
        g_string_append(log, "; ");
        m = read_mounts(o); target = m.target; mounts_clear(&m);
    }
    if (target == 2) {
        g_string_append_printf(log, "%s is already a foreign mount; home gate not mounted", o->target);
        g_free(copy); return HC_GATE_FAILED;
    }
    gboolean ok = stage_gate(o);
    if (!ok) g_string_append_printf(log, "cannot stage %s in %s", o->gate_binary, o->dir);
    else if (do_bind(o, copy, o->target)) {
        g_string_append_printf(log, "cannot bind the gate over %s: %s", o->target, g_strerror(errno));
        ok = FALSE;
    }
    g_free(copy);
    m = read_mounts(o); target = m.target; mounts_clear(&m);
    if (ok && target != 1) { g_string_append(log, "mount not visible after binding"); ok = FALSE; }
    if (!ok) {
        g_string_append(log, "; rolling back: ");
        unmount_gate(o, TRUE, log);
        return HC_GATE_FAILED;
    }
    g_string_append_printf(log, "home gate mounted over %s; ", o->target);
    close_stock(o, log);
    return HC_GATE_MOUNTED;
}

static HCGateResult sync_locked(const HCGateOps *o, const char *choice, GString *log)
{
    if (choice && !g_file_test(o->gate_binary, G_FILE_TEST_IS_REGULAR)) {
        g_string_append_printf(log, "no gate binary at %s; ", o->gate_binary);
        choice = NULL;
    }
    if (!choice) return unmount_gate(o, TRUE, log);
    char *marker = hc_gate_marker(o->dir), *released = NULL;
    gboolean has = g_file_get_contents(marker, &released, NULL, NULL);
    if (has) g_strstrip(released);
    if (has && !strcmp(released, choice)) {
        /* Released for this choice: no remount until the choice changes, a reboot or an apply.
         * Clean what the gate's own lazy unmount may have left (propagated copies). */
        HCGateResult r = unmount_gate(o, FALSE, log);
        g_free(marker); g_free(released);
        return r == HC_GATE_FAILED ? r : HC_GATE_RELEASED;
    }
    if (has) { unlink(marker); g_string_append_printf(log, "choice changed since the gate released %s; ", released); }
    g_free(marker); g_free(released);
    return mount_gate(o, log);
}

HCGateResult hc_gate_sync(const HCGateOps *o, const char *choice, GString *log)
{
    /* The watcher and a one-off --homekey run may sync at the same time. */
    char *parent = g_path_get_dirname(o->dir), *lockpath = g_strconcat(o->dir, ".lock", NULL);
    int lock = mkdir_p(parent) ? open(lockpath, O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0600) : -1;
    g_free(parent); g_free(lockpath);
    unsigned tries = 0;
    while (lock >= 0 && flock(lock, LOCK_EX | LOCK_NB) && ++tries < 50) g_usleep(100000);
    if (lock < 0 || tries >= 50) {
        g_string_append(log, "home gate lock unavailable");
        if (lock >= 0) close(lock);
        return HC_GATE_FAILED;
    }
    HCGateResult result = sync_locked(o, choice, log);
    close(lock); return result;
}

void hc_gate_rearm(const HCGateOps *o)
{
    char *marker = hc_gate_marker(o->dir); unlink(marker); g_free(marker);
}

char *hc_gate_wanted(const char *state_dir, char *(*call)(const char *, const char *, int, void *), void *context)
{
    char *flag = g_build_filename(state_dir, "homekey", NULL), *home = g_build_filename(state_dir, "home", NULL);
    char *choice = NULL;
    gboolean want = g_file_test(flag, G_FILE_TEST_EXISTS) && g_file_get_contents(home, &choice, NULL, NULL);
    if (want) g_strstrip(choice);
    want = want && hc_app_id_ok(choice) && strcmp(choice, HC_STOCK_HOME_ID) && strcmp(choice, "com.webos.app.fullhome");
    if (want) {
        char *payload = g_strdup_printf("{\"id\":\"%s\"}", choice);
        char *text = call("luna://com.webos.applicationManager/getAppInfo", payload, 5, context);
        jvalue_ref r = parse_object(text);
        want = reply_ok(r);
        j_release(&r); g_free(text); g_free(payload);
    }
    g_free(flag); g_free(home);
    if (!want) g_clear_pointer(&choice, g_free);
    return choice;
}

int hc_gate_major(const char *osrelease)
{
    char *text = NULL;
    if (!g_file_get_contents(osrelease, &text, NULL, NULL)) return 0;
    GRegex *rx = g_regex_new("(?m)^VERSION(?:_ID)?=[\"']?([0-9]+)", 0, 0, NULL);
    GMatchInfo *match = NULL;
    int major = 0;
    if (g_regex_match(rx, text, 0, &match)) {
        char *v = g_match_info_fetch(match, 1); major = atoi(v); g_free(v);
    }
    g_match_info_free(match); g_regex_unref(rx); g_free(text); return major;
}
