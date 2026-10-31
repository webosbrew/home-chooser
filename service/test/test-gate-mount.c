/* SPDX-License-Identifier: Apache-2.0 */
/* The daemon's home gate mount/unmount decisions, with the bind/umount calls and mountinfo
 * faked through HCGateOps. M identifiers name the cases in docs/home-gate.md. */
#define _GNU_SOURCE
#include "gate-mount.h"
#include "fs.h"
#include "json.h"
#include "policy.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

static unsigned checks, failures;
#define CHECK(id, expr) do { checks++; if (!(expr)) { failures++; fprintf(stderr, "FAIL %s line %d: %s\n", id, __LINE__, #expr); } } while (0)

#define JAIL "/mnt/lg/%s/var/palm/jail/com.palm.devmode.openssh/usr/bin/com.webos.app.home"
static const char *jails[] = {"user", "flash/data", "cmn_data"};

typedef struct {
    char *root, *mountinfo, *binary, *dir, *target, *dev;
    GString *ops, *calls;
    const char *fail_bind, *fail_unbind; /* a path whose call fails */
    int fail_errno;
    gboolean invisible, running, no_reply;
    int app_installed;
    unsigned serial;
} Fx;
static Fx *fx;

static char *escape(const char *s)
{
    GString *o = g_string_new(NULL);
    for (; *s; s++) {
        if (*s == ' ' || *s == '\t' || *s == '\n' || *s == '\\') g_string_append_printf(o, "\\%03o", (unsigned char)*s);
        else g_string_append_c(o, *s);
    }
    return g_string_free(o, FALSE);
}
static void add_mount_dev(const char *root, const char *point, const char *dev)
{
    char *text = NULL, *r = escape(root), *p = escape(point);
    g_file_get_contents(fx->mountinfo, &text, NULL, NULL);
    char *next = g_strdup_printf("%s%u 22 %s %s %s rw,relatime shared:22 - tmpfs tmpfs rw\n", text ? text : "", 100 + fx->serial++, dev, r, p);
    g_file_set_contents(fx->mountinfo, next, -1, NULL);
    g_free(text); g_free(next); g_free(r); g_free(p);
}
/* A bind of a tmpfs file shows its path within that tmpfs as root, and the tmpfs device. */
static void add_mount(const char *root, const char *point) { add_mount_dev(root, point, fx->dev); }
static void add_jail_copies(void)
{
    char *copy = g_build_filename(fx->dir, HC_GATE_NAME, NULL);
    for (unsigned i = 0; i < G_N_ELEMENTS(jails); i++) {
        char *p = g_strdup_printf(JAIL, jails[i]); add_mount(copy, p); g_free(p);
    }
    g_free(copy);
}
static void drop_mount(const char *point)
{
    char *text = NULL, *p = escape(point);
    g_file_get_contents(fx->mountinfo, &text, NULL, NULL);
    char **lines = g_strsplit(text ? text : "", "\n", -1);
    int last = -1;
    for (int i = 0; lines[i]; i++) {
        char **f = g_strsplit(lines[i], " ", 6);
        if (g_strv_length(f) >= 5 && !strcmp(f[4], p)) last = i;
        g_strfreev(f);
    }
    GString *out = g_string_new(NULL);
    for (int i = 0; lines[i]; i++) if (i != last && *lines[i]) g_string_append_printf(out, "%s\n", lines[i]);
    g_file_set_contents(fx->mountinfo, out->str, -1, NULL);
    g_string_free(out, TRUE); g_strfreev(lines); g_free(text); g_free(p);
}
static const char *tag(const char *path) { return !strcmp(path, fx->target) ? "T" : strstr(path, "/jail/") ? "J" : "?"; }
static int fake_bind(const char *source, const char *target, void *context)
{
    (void)context;
    g_string_append_printf(fx->ops, "bind %s>%s;", strrchr(source, '/') + 1, tag(target));
    if (fx->fail_bind && !strcmp(target, fx->fail_bind)) { errno = EPERM; return -1; }
    if (!fx->invisible) add_mount(source, target);
    return 0;
}
static int fake_unbind(const char *target, void *context)
{
    (void)context;
    g_string_append_printf(fx->ops, "unbind %s;", tag(target));
    if (fx->fail_unbind && !strcmp(target, fx->fail_unbind)) { errno = fx->fail_errno; return -1; }
    drop_mount(target); return 0;
}
static char *fake_call(const char *uri, const char *payload, int timeout, void *context)
{
    (void)context;
    CHECK("M bounded call", timeout > 0 && timeout <= 10);
    g_string_append_printf(fx->calls, "%s %s;", strrchr(uri, '/') + 1, payload);
    if (fx->no_reply) return NULL;
    if (g_str_has_suffix(uri, "/running"))
        return g_strdup(fx->running ? "{\"returnValue\":true,\"running\":[{\"id\":\"com.webos.app.livetv\"},{\"id\":\"com.webos.app.home\",\"processid\":\"2469\"}]}"
            : "{\"returnValue\":true,\"running\":[{\"id\":\"com.webos.app.livetv\"}]}");
    if (g_str_has_suffix(uri, "/closeByAppId")) {
        jvalue_ref p = parse_object(payload);
        char *id = get_string(p, "id");
        bool force = false; jboolean_get(jobject_get(p, key_buf("forceMode")), &force);
        CHECK("M close only stock Home, forced", !g_strcmp0(id, HC_STOCK_HOME_ID) && force);
        g_free(id); j_release(&p);
        return g_strdup("{\"returnValue\":true}");
    }
    if (g_str_has_suffix(uri, "/getAppInfo")) return g_strdup(fx->app_installed ? "{\"returnValue\":true,\"appInfo\":{}}" : "{\"returnValue\":false}");
    CHECK("M known method", FALSE);
    return NULL;
}

static HCGateOps ops(void)
{
    HCGateOps o = hc_gate_ops(fake_call, NULL);
    o.gate_binary = fx->binary; o.dir = fx->dir; o.target = fx->target; o.mountinfo = fx->mountinfo;
    o.bind = fake_bind; o.unbind = fake_unbind;
    return o;
}
static HCGateResult gsync(const char *choice)
{
    g_string_truncate(fx->ops, 0); g_string_truncate(fx->calls, 0);
    HCGateOps o = ops();
    GString *log = g_string_new(NULL);
    HCGateResult r = hc_gate_sync(&o, choice, log);
    g_string_free(log, TRUE); return r;
}
static gboolean ops_are(const char *expected)
{
    if (strcmp(fx->ops->str, expected)) fprintf(stderr, "  ops: %s\n  want: %s\n", fx->ops->str, expected);
    return !strcmp(fx->ops->str, expected);
}
static gboolean mounts_empty(void)
{
    char *text = NULL; g_file_get_contents(fx->mountinfo, &text, NULL, NULL);
    gboolean empty = !text || !strstr(text, "app.home"); g_free(text); return empty;
}
static char *marker(void) { return g_build_filename(fx->dir, "released", NULL); }
/* What the gate does on its fallback: marker first, then a lazy unmount of the target only. */
static void gate_releases(const char *choice)
{
    char *m = marker(); g_file_set_contents(m, choice, -1, NULL); g_free(m);
    drop_mount(fx->target);
}
static void setup(const char *base, const char *name)
{
    static unsigned n;
    fx = g_new0(Fx, 1);
    char *dir = g_strdup_printf("%s/%u-%s", base, n++, name);
    fx->root = dir; mkdir_p(dir);
    fx->mountinfo = g_build_filename(dir, "mountinfo", NULL);
    fx->binary = g_build_filename(dir, "package", HC_GATE_NAME, NULL);
    fx->dir = g_build_filename(dir, "runtime", "home-gate", NULL);
    fx->target = g_build_filename(dir, "usr bin", HC_STOCK_HOME_ID, NULL); /* a space exercises mountinfo escapes */
    char *pkg = g_path_get_dirname(fx->binary), *bin = g_path_get_dirname(fx->target), *rt = g_path_get_dirname(fx->dir);
    mkdir_p(pkg); mkdir_p(bin); mkdir_p(rt);
    struct stat st; stat(rt, &st);
    fx->dev = g_strdup_printf("%u:%u", major(st.st_dev), minor(st.st_dev));
    g_free(pkg); g_free(bin); g_free(rt);
    g_file_set_contents(fx->binary, "gate v1", -1, NULL);
    g_file_set_contents(fx->target, "stock home", -1, NULL);
    g_file_set_contents(fx->mountinfo, "63 52 0:23 / / rw,relatime shared:1 - overlay webos rw\n25 63 0:20 / /tmp rw shared:5 - tmpfs tmpfs rw\n", -1, NULL);
    fx->ops = g_string_new(NULL); fx->calls = g_string_new(NULL); fx->running = TRUE; fx->app_installed = 1;
}
static void teardown(void)
{
    char *rm = g_strdup_printf("rm -rf '%s'", fx->root); if (system(rm)) {} g_free(rm);
    g_free(fx->root); g_free(fx->mountinfo); g_free(fx->binary); g_free(fx->dir); g_free(fx->target); g_free(fx->dev);
    g_string_free(fx->ops, TRUE); g_string_free(fx->calls, TRUE); g_free(fx); fx = NULL;
}
static char *staged(void) { return g_build_filename(fx->dir, HC_GATE_NAME, NULL); }

static void test_lifecycle(const char *base)
{
    setup(base, "lifecycle");
    CHECK("M1 mounted", gsync("my.home") == HC_GATE_MOUNTED);
    CHECK("M1 only the gate is bound; no private stock copy", ops_are("bind " HC_GATE_NAME ">T;"));
    char *copy = staged(), *bytes = NULL; struct stat st;
    CHECK("M1 gate staged on tmpfs, not the package", g_file_get_contents(copy, &bytes, NULL, NULL) && !strcmp(bytes, "gate v1"));
    CHECK("M1 staged executable", !stat(copy, &st) && (st.st_mode & 0777) == 0755);
    CHECK("M1 private runtime dir", !stat(fx->dir, &st) && (st.st_mode & 0777) == 0700);
    CHECK("M1 running stock Home closed (preloaded one too)", strstr(fx->calls->str, "running {};") && strstr(fx->calls->str, "closeByAppId"));
    g_free(bytes);
    CHECK("M2 idempotent", gsync("my.home") == HC_GATE_UNCHANGED && ops_are("") && !fx->calls->len);
    g_file_set_contents(fx->binary, "gate v2", -1, NULL);
    CHECK("M3 updated gate remounted", gsync("my.home") == HC_GATE_MOUNTED && ops_are("unbind T;bind " HC_GATE_NAME ">T;"));
    bytes = NULL; g_file_get_contents(copy, &bytes, NULL, NULL);
    CHECK("M3 new bytes staged", !g_strcmp0(bytes, "gate v2")); g_free(bytes);
    CHECK("M4 unmounted", gsync(NULL) == HC_GATE_UNMOUNTED && ops_are("unbind T;"));
    CHECK("M4 no luna calls on unmount", !fx->calls->len);
    CHECK("M4 runtime files removed", !g_file_test(copy, G_FILE_TEST_EXISTS) && !g_file_test(fx->dir, G_FILE_TEST_EXISTS));
    CHECK("M4 mountinfo clean", mounts_empty());
    CHECK("M5 unmount idempotent", gsync(NULL) == HC_GATE_UNCHANGED && ops_are(""));
    fx->running = FALSE;
    CHECK("M6 stock Home not running: no close", gsync("my.home") == HC_GATE_MOUNTED && strstr(fx->calls->str, "running") && !strstr(fx->calls->str, "closeByAppId"));
    gsync(NULL); fx->no_reply = TRUE;
    CHECK("M6 no running answer, no close", gsync("my.home") == HC_GATE_MOUNTED && !strstr(fx->calls->str, "closeByAppId"));
    fx->no_reply = FALSE;
    unlink(fx->binary);
    CHECK("M7 package gone: unmount", gsync("my.home") == HC_GATE_UNMOUNTED && ops_are("unbind T;"));
    CHECK("M7 no binary, nothing mounted", gsync("my.home") == HC_GATE_UNCHANGED && ops_are(""));
    g_free(copy); teardown();
}
static void test_propagation(const char *base)
{
    setup(base, "jail-copies"); gsync("my.home"); add_jail_copies();
    CHECK("M8 copies counted as ours: still mounted", gsync("my.home") == HC_GATE_UNCHANGED && ops_are(""));
    CHECK("M8 target then every propagated copy", gsync(NULL) == HC_GATE_UNMOUNTED && ops_are("unbind T;unbind J;unbind J;unbind J;"));
    CHECK("M8 no copies left", mounts_empty());
    teardown();

    setup(base, "copies-only"); add_jail_copies();
    CHECK("M9 copies without the target removed", gsync(NULL) == HC_GATE_UNMOUNTED && ops_are("unbind J;unbind J;unbind J;") && mounts_empty());
    teardown();

    setup(base, "not-ours"); char *p = g_strdup_printf(JAIL, "user");
    add_mount_dev("/elsewhere/" HC_GATE_NAME, p, "254:1");        /* same name, another device */
    add_mount("/some/other-file", p);                              /* our device, another file */
    char *other = g_build_filename(fx->root, "usr/bin/other", NULL), *copy = staged();
    add_mount(copy, other);                                        /* our gate, unrelated mount point */
    CHECK("M10 others' mounts untouched", gsync(NULL) == HC_GATE_UNCHANGED && ops_are(""));
    g_free(p); g_free(other); g_free(copy); teardown();

    setup(base, "copy-busy"); gsync("my.home"); add_jail_copies();
    char *busy = g_strdup_printf(JAIL, "flash/data"); fx->fail_unbind = busy; fx->fail_errno = EBUSY;
    CHECK("M11 a copy that cannot be unmounted fails", gsync(NULL) == HC_GATE_FAILED);
    fx->fail_errno = EINVAL;
    CHECK("M11 a copy that stays mounted fails after retries", gsync(NULL) == HC_GATE_FAILED);
    fx->fail_unbind = NULL;
    CHECK("M11 then cleaned", gsync(NULL) == HC_GATE_UNMOUNTED && mounts_empty());
    g_free(busy); teardown();

    setup(base, "stacked"); gsync("my.home"); copy = staged(); add_mount(copy, fx->target);
    CHECK("M12 stacked gates all unmounted", gsync(NULL) == HC_GATE_UNMOUNTED && ops_are("unbind T;unbind T;") && mounts_empty());
    g_free(copy); teardown();
}
static void test_release(const char *base)
{
    setup(base, "released"); gsync("my.home"); add_jail_copies(); gate_releases("my.home");
    CHECK("M13 released: copies cleaned, not remounted", gsync("my.home") == HC_GATE_RELEASED && ops_are("unbind J;unbind J;unbind J;"));
    CHECK("M13 released stays released", gsync("my.home") == HC_GATE_RELEASED && ops_are("") && !fx->calls->len);
    char *m = marker();
    CHECK("M13 marker kept", g_file_test(m, G_FILE_TEST_EXISTS));
    CHECK("M14 choice change re-mounts", gsync("other.home") == HC_GATE_MOUNTED && ops_are("bind " HC_GATE_NAME ">T;") && !g_file_test(m, G_FILE_TEST_EXISTS));
    gate_releases("other.home");
    CHECK("M15 LG Home chosen clears the release", gsync(NULL) == HC_GATE_UNCHANGED && !g_file_test(m, G_FILE_TEST_EXISTS));
    CHECK("M15 a launcher chosen again mounts", gsync("other.home") == HC_GATE_MOUNTED);
    gate_releases("other.home");
    HCGateOps o = ops(); hc_gate_rearm(&o);
    CHECK("M16 an apply re-arms", gsync("other.home") == HC_GATE_MOUNTED);
    g_free(m); teardown();
}
static void test_failures(const char *base)
{
    setup(base, "foreign"); add_mount_dev("/other/thing", fx->target, "8:1");
    CHECK("M17 foreign mount refused", gsync("my.home") == HC_GATE_FAILED && ops_are(""));
    CHECK("M17 foreign mount never unmounted", gsync(NULL) == HC_GATE_UNCHANGED && ops_are(""));
    teardown();

    setup(base, "bind-fails"); fx->fail_bind = fx->target;
    CHECK("M18 bind failure", gsync("my.home") == HC_GATE_FAILED && ops_are("bind " HC_GATE_NAME ">T;"));
    CHECK("M18 nothing left", mounts_empty() && !g_file_test(fx->dir, G_FILE_TEST_EXISTS) && !fx->calls->len);
    teardown();

    setup(base, "invisible"); fx->invisible = TRUE;
    CHECK("M19 unverified mount fails", gsync("my.home") == HC_GATE_FAILED && mounts_empty() && !fx->calls->len);
    teardown();

    setup(base, "unbind-fails"); gsync("my.home"); fx->fail_unbind = fx->target; fx->fail_errno = EBUSY;
    CHECK("M20 unmount failure", gsync(NULL) == HC_GATE_FAILED && ops_are("unbind T;") && !mounts_empty());
    teardown();
}
static void test_wanted(const char *base)
{
    setup(base, "wanted");
    char *state = g_build_filename(fx->root, "state", NULL), *home = g_build_filename(state, "home", NULL), *flag = g_build_filename(state, "homekey", NULL);
    mkdir_p(state);
    char *id = hc_gate_wanted(state, fake_call, NULL);
    CHECK("M21 no choice", !id);
    g_file_set_contents(home, "my.home\n", -1, NULL);
    id = hc_gate_wanted(state, fake_call, NULL); CHECK("M21 no flag", !id);
    g_file_set_contents(flag, "", -1, NULL);
    id = hc_gate_wanted(state, fake_call, NULL);
    CHECK("M21 launcher chosen", !g_strcmp0(id, "my.home") && strstr(fx->calls->str, "getAppInfo {\"id\":\"my.home\"}")); g_free(id);
    fx->app_installed = 0;
    id = hc_gate_wanted(state, fake_call, NULL); CHECK("M21 launcher not installed", !id);
    fx->app_installed = 1; g_string_truncate(fx->calls, 0);
    const char *stock[] = {"com.webos.app.home", "com.webos.app.fullhome", "bad id", "", NULL};
    for (int i = 0; stock[i]; i++) {
        g_file_set_contents(home, stock[i], -1, NULL);
        id = hc_gate_wanted(state, fake_call, NULL); CHECK("M21 stock or invalid choice", !id);
    }
    CHECK("M21 no lookup for stock", !fx->calls->len);
    char *os = g_build_filename(fx->root, "os-release", NULL);
    const char *releases[] = {"VERSION_ID=\"7.6.0\"\n", "NAME=webOS\nVERSION=9.2.1\n", "VERSION_ID=11\n", "junk\n"};
    int majors[] = {7, 9, 11, 0};
    for (int i = 0; i < 4; i++) {
        g_file_set_contents(os, releases[i], -1, NULL);
        CHECK("M22 release major", hc_gate_major(os) == majors[i]);
    }
    CHECK("M22 missing release", hc_gate_major("/nonexistent/os-release") == 0);
    g_free(os); g_free(state); g_free(home); g_free(flag); teardown();
}
static void test_constants(void)
{
    CHECK("M23 runtime on /tmp", g_str_has_prefix(HC_GATE_DIR, "/tmp/"));
    CHECK("M23 target", !strcmp(HC_GATE_TARGET, "/usr/bin/com.webos.app.home"));
    CHECK("M23 installed with the service", !strcmp(HC_GATE_BINARY, HC_SERVICE_DIR "/homechooser-home-gate"));
    HCGateOps o = hc_gate_ops(fake_call, NULL);
    CHECK("M23 production ops", !strcmp(o.dir, HC_GATE_DIR) && !strcmp(o.mountinfo, "/proc/self/mountinfo") && !o.bind && !o.unbind);
    char *m = hc_gate_marker(HC_GATE_DIR);
    CHECK("M23 marker in the gate dir", !strcmp(m, HC_GATE_DIR "/released")); g_free(m);
}

int main(int argc, char **argv)
{
    if (argc != 2) return 2;
    setbuf(stdout, NULL);
    char *base = g_build_filename(argv[1], "cases", NULL);
    char *rm = g_strdup_printf("rm -rf '%s'", base); if (system(rm)) return 2; g_free(rm);
    test_lifecycle(base); test_propagation(base); test_release(base); test_failures(base); test_wanted(base); test_constants();
    g_free(base);
    printf("%u passed, %u failed (home gate mounts)\n", checks - failures, failures);
    return failures ? 1 : 0;
}
