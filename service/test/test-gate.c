/* SPDX-License-Identifier: Apache-2.0 */
/* The home gate's decision table through real bounded luna-send children (fake-luna-send.py)
 * under qemu with firmware pbnjson/GLib. G identifiers name the rows of docs/home-gate.md. */
#define _GNU_SOURCE
#include "gate/gate.h"
#include "fs.h"
#include "json.h"
#include "log.h"
#include "policy.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

PmLogContext hc_log;
static unsigned checks, failures;
#define CHECK(id, expr) do { checks++; if (!(expr)) { failures++; fprintf(stderr, "FAIL %s line %d: %s\n", id, __LINE__, #expr); } } while (0)

#define UNDEF "{\"event\":\"launch\",\"reason\":\"undefined\",\"appId\":\"com.webos.app.home\",\"parameters\":{},\"interfaceVersion\":2,\"interfaceMethod\":\"registerApp\",\"@system_native_app\":true}"
#define HOTKEY "{\"event\":\"launch\",\"reason\":\"hotKey\",\"appId\":\"com.webos.app.home\",\"parameters\":{\"launchType\":\"homeKey\",\"activateType\":\"home\"},\"interfaceVersion\":2,\"interfaceMethod\":\"registerApp\",\"@system_native_app\":true}"
#define PRELOAD "{\"event\":\"launch\",\"reason\":\"undefined\",\"appId\":\"com.webos.app.home\",\"parameters\":{},\"interfaceVersion\":2,\"interfaceMethod\":\"registerApp\",\"@system_native_app\":true,\"preload\":\"full\"}"
#define CARD "{\"my.home\":{\"id\":\"my.home\",\"defaultWindowType\":\"card\"}}"
#define OVERLAY "{\"my.home\":{\"id\":\"my.home\",\"defaultWindowType\":\"overlay\"}}"

typedef struct {
    const char *name, *envelope, *choice, *apps, *state; /* state: extra JSON members */
    gboolean no_flag;
    char expect; /* L launched, S gate released (stock Home relaunched), N nothing */
    const char *target;
    gboolean foreground_asked;
} Case;

static const Case cases[] = {
    {"G1 full-screen, idle relaunch, nothing in front", UNDEF, "my.home", CARD, "\"foreground\":\"\"", FALSE, 'L', "my.home", FALSE},
    {"G1 full-screen, explicit launch over an app", UNDEF, "my.home", CARD, "\"foreground\":\"netflix\"", FALSE, 'L', "my.home", FALSE},
    {"G1 full-screen, Home key", HOTKEY, "my.home", CARD, "\"foreground\":\"\"", FALSE, 'L', "my.home", FALSE},
    {"G1 window type missing is full-screen", UNDEF, "my.home", "{\"my.home\":{\"id\":\"my.home\"}}", "\"foreground\":\"\"", FALSE, 'L', "my.home", FALSE},
    {"G2 overlay, Home key, nothing in front", HOTKEY, "my.home", OVERLAY, "\"foreground\":\"\"", FALSE, 'L', "my.home", FALSE},
    {"G3 overlay, nothing in front: last input", UNDEF, "my.home", OVERLAY, "\"foreground\":\"\",\"lastInput\":\"com.webos.app.hdmi2\"", FALSE, 'L', "com.webos.app.hdmi2", TRUE},
    {"G3 overlay, no lastSourceAppId: Live TV", UNDEF, "my.home", OVERLAY, "\"foreground\":\"\"", FALSE, 'L', "com.webos.app.livetv", TRUE},
    {"G3 overlay, empty lastSourceAppId: Live TV", UNDEF, "my.home", OVERLAY, "\"foreground\":\"\",\"lastInput\":\"\"", FALSE, 'L', "com.webos.app.livetv", TRUE},
    {"G3 overlay, eim refuses: Live TV", UNDEF, "my.home", OVERLAY, "\"foreground\":\"\",\"lastInput\":\"com.webos.app.hdmi2\",\"refuse\":[\"getLastInput\"]", FALSE, 'L', "com.webos.app.livetv", TRUE},
    {"G3 overlay, last input is stock Home: Live TV", UNDEF, "my.home", OVERLAY, "\"foreground\":\"\",\"lastInput\":\"com.webos.app.home\"", FALSE, 'L', "com.webos.app.livetv", TRUE},
    {"G4 overlay over a full-screen app", UNDEF, "my.home", OVERLAY, "\"foreground\":\"com.webos.app.livetv\"", FALSE, 'L', "my.home", TRUE},
    {"G5 overlay, foreground refused", UNDEF, "my.home", OVERLAY, "\"refuse\":[\"getForegroundAppInfo\"]", FALSE, 'S', NULL, TRUE},
    {"G5 overlay, foreground hangs", UNDEF, "my.home", OVERLAY, "\"hang\":[\"getForegroundAppInfo\"]", FALSE, 'S', NULL, TRUE},
    {"G5 no choice file", UNDEF, NULL, CARD, "\"foreground\":\"\"", FALSE, 'S', NULL, FALSE},
    {"G5 empty choice", UNDEF, "  \n", CARD, "\"foreground\":\"\"", FALSE, 'S', NULL, FALSE},
    {"G5 invalid choice", UNDEF, "a b", CARD, "\"foreground\":\"\"", FALSE, 'S', NULL, FALSE},
    {"G5 LG Home chosen", HOTKEY, "com.webos.app.home", CARD, "\"foreground\":\"\"", FALSE, 'S', NULL, FALSE},
    {"G5 LG full home chosen", UNDEF, "com.webos.app.fullhome", CARD, "\"foreground\":\"\"", FALSE, 'S', NULL, FALSE},
    {"G5 redirect disabled", HOTKEY, "my.home", CARD, "\"foreground\":\"\"", TRUE, 'S', NULL, FALSE},
    {"G5 launcher not installed", HOTKEY, "my.home", "{}", "\"foreground\":\"\"", FALSE, 'S', NULL, FALSE},
    {"G5 getAppInfo hangs", HOTKEY, "my.home", CARD, "\"hang\":[\"getAppInfo\"]", FALSE, 'S', NULL, FALSE},
    {"G5 launch refused", UNDEF, "my.home", CARD, "\"foreground\":\"\",\"refuse\":[\"launch\"]", FALSE, 'S', NULL, FALSE},
    {"G5 launch hangs", UNDEF, "my.home", CARD, "\"foreground\":\"\",\"hang\":[\"launch\"]", FALSE, 'S', NULL, FALSE},
    {"G5 no envelope", NULL, "my.home", CARD, "\"foreground\":\"\"", FALSE, 'S', NULL, FALSE},
    {"G5 garbage envelope", "not json", "my.home", CARD, "\"foreground\":\"\"", FALSE, 'S', NULL, FALSE},
    {"G6 preload launch opens nothing", PRELOAD, "my.home", CARD, "\"foreground\":\"\"", FALSE, 'N', NULL, FALSE},
};

static char *root, *program, *state_dir, *gate_dir, *log_path;
static const char *target = "/usr/bin/com.webos.app.home";
static int unmounts, unmount_errno;
static char *unmount_path;

static void write_file(const char *p, const char *text)
{
    char *dir = g_path_get_dirname(p); mkdir_p(dir); g_free(dir);
    CHECK("fixture file", g_file_set_contents(p, text, -1, NULL));
}
static char *fixture(const char *name) { return g_build_filename(root, name, NULL); }
static jvalue_ref state_read(void)
{
    char *p = fixture("state.json"), *text = NULL;
    g_file_get_contents(p, &text, NULL, NULL);
    jvalue_ref s = parse_object(text); g_free(p); g_free(text); return s;
}
/* Records the self-unmount; the gate must not touch the real /usr/bin path under test. */
static int fake_unmount(const char *path, void *context)
{
    (void)context; unmounts++;
    g_free(unmount_path); unmount_path = g_strdup(path);
    if (unmount_errno) { errno = unmount_errno; return -1; }
    return 0;
}
static gboolean no_children(void)
{
    int status; errno = 0;
    return waitpid(-1, &status, WNOHANG) == -1 && errno == ECHILD;
}
/* Every call: fixed -a identity, -n 1. A launcher launch as com.webos.app.home; stock Home's
 * relaunch and every query as the privileged identity. */
static void check_calls(const char *name, gboolean *foreground)
{
    char *p = fixture("calls.jsonl"), *text = NULL;
    g_file_get_contents(p, &text, NULL, NULL);
    char **lines = g_strsplit(text ? text : "", "\n", -1);
    *foreground = FALSE;
    for (int i = 0; lines[i]; i++) {
        jvalue_ref e = parse_object(lines[i]);
        if (!e) continue;
        char *uri = get_string(e, "uri"), *id = get_string(jobject_get(e, key_buf("payload")), "id");
        jvalue_ref args = jobject_get(e, key_buf("args"));
        raw_buffer who = jstring_get_fast(jarray_get(args, 1)), n = jstring_get_fast(jarray_get(args, 2));
        gboolean as_home = g_str_has_suffix(uri, "/launch") && g_strcmp0(id, HC_STOCK_HOME_ID);
        const char *want = as_home ? HC_STOCK_HOME_ID : HC_LUNA_IDENTITY;
        gboolean ok = who.m_len == strlen(want) && !memcmp(who.m_str, want, who.m_len);
        if (!ok) fprintf(stderr, "  %s: %s\n", name, lines[i]);
        CHECK("G7 caller identity", ok);
        CHECK("G7 one reply", n.m_len == 2 && !memcmp(n.m_str, "-n", 2));
        if (g_str_has_suffix(uri, "/getForegroundAppInfo")) *foreground = TRUE;
        g_free(uri); g_free(id); j_release(&e);
    }
    g_strfreev(lines); g_free(text); g_free(p);
}
static char *launched_at(jvalue_ref launched, int i, int field)
{
    raw_buffer b = jstring_get_fast(jarray_get(jarray_get(launched, i), field));
    return g_strndup(b.m_str, b.m_len);
}
/* The release launch is sent by a detached child: wait until its call is logged, then a moment for it
 * to finish, so it cannot rewrite state.json under the next case. */
static void settle_release(int want)
{
    char *p = fixture("calls.jsonl");
    for (int i = 0; i < 60; i++) {
        char *text = NULL; int seen = 0;
        if (g_file_get_contents(p, &text, NULL, NULL))
            for (char **l = g_strsplit(text, "\n", -1), **it = l; *it; it++) seen += strstr(*it, "/launch") && strstr(*it, "\"id\": \"com.webos.app.home\"");
        g_free(text);
        if (seen >= want) break;
        g_usleep(50000);
    }
    g_usleep(400000); g_free(p);
}
static HCGateConfig config(void)
{
    HCGateConfig c = hc_gate_config();
    c.luna_send = program; c.state_dir = state_dir; c.dir = gate_dir; c.log = log_path; c.unmount = fake_unmount;
    return c;
}

static void run_case(const Case *k)
{
    char *home = g_build_filename(state_dir, "home", NULL), *flag = g_build_filename(state_dir, "homekey", NULL);
    char *calls = fixture("calls.jsonl"), *state = fixture("state.json"), *marker = g_build_filename(gate_dir, "released", NULL);
    unlink(home); unlink(flag); unlink(calls); unlink(marker);
    if (k->choice) write_file(home, k->choice);
    if (!k->no_flag) write_file(flag, "");
    char *json = g_strdup_printf("{\"apps\":%s,%s}", k->apps, k->state);
    write_file(state, json);
    unmounts = 0; unmount_errno = 0;
    HCGateConfig c = config();
    char *argv[] = {"/usr/bin/com.webos.app.home", (char *)k->envelope, NULL};
    gint64 start = g_get_monotonic_time();
    int rc = hc_gate_main(&c, k->envelope ? 2 : 1, argv);
    gint64 elapsed = g_get_monotonic_time() - start;
    if (k->expect == 'S') settle_release(1);
    jvalue_ref s = state_read(), launched = jobject_get(s, key_buf("launched"));
    int n = jis_array(launched) ? jarray_size(launched) : 0;
    gboolean foreground;
    check_calls(k->name, &foreground);
    if (rc != 0) fprintf(stderr, "  %s: rc %d\n", k->name, rc);
    CHECK("G8 exit 0", rc == 0);
    CHECK("G8 bounded", elapsed < 10 * G_USEC_PER_SEC);
    /* The release never waits for SAM; only a hanging first launch (3 s) or query (2 s) may take time. */
    if (k->expect == 'S' && !strstr(k->state, "hang")) CHECK("G10 release does not wait for a reply", elapsed < G_USEC_PER_SEC);
    CHECK("G8 children reaped", no_children());
    char *first = n ? launched_at(launched, 0, 1) : NULL;
    if (k->expect == 'L') {
        if (n != 1 || g_strcmp0(first, k->target)) fprintf(stderr, "  %s: launched %d %s\n", k->name, n, first);
        CHECK("G launch target", n == 1 && !g_strcmp0(first, k->target));
        CHECK("G launch keeps the gate", unmounts == 0 && !g_file_test(marker, G_FILE_TEST_EXISTS));
    } else if (k->expect == 'S') {
        gboolean launch_refused = strstr(k->state, "\"launch\"") != NULL;
        if (unmounts != 1) fprintf(stderr, "  %s: unmounts %d\n", k->name, unmounts);
        CHECK("G release unmounts the gate itself", unmounts == 1 && !g_strcmp0(unmount_path, target));
        CHECK("G release relaunches stock Home", launch_refused ? n == 0 : n == 1 && !g_strcmp0(first, HC_STOCK_HOME_ID));
        char *text = NULL;
        CHECK("G release marker", g_file_get_contents(marker, &text, NULL, NULL));
        char *valid = k->choice ? g_strstrip(g_strdup(k->choice)) : NULL;
        gboolean expect_choice = valid && hc_app_id_ok(valid);
        if (text && strcmp(text, expect_choice ? valid : "")) fprintf(stderr, "  %s: marker '%s'\n", k->name, text);
        CHECK("G release marker names the choice", text && !strcmp(text, expect_choice ? valid : ""));
        g_free(text); g_free(valid);
    } else CHECK("G6 nothing", n == 0 && unmounts == 0);
    CHECK("G foreground only for an overlay", foreground == k->foreground_asked);
    g_free(first); j_release(&s); g_free(json); g_free(home); g_free(flag); g_free(calls); g_free(state); g_free(marker);
}

static void test_release_guards(void)
{
    HCGateConfig c = config();
    char *home = g_build_filename(state_dir, "home", NULL), *calls = fixture("calls.jsonl"), *state = fixture("state.json");
    write_file(home, "com.webos.app.home"); write_file(state, "{\"foreground\":\"\"}"); unlink(calls);
    char *argv[] = {"/usr/bin/com.webos.app.home", UNDEF, NULL};
    unmounts = 0; unmount_errno = EBUSY;
    CHECK("G9 unmount failure exits 1", hc_gate_main(&c, 2, argv) == 1 && unmounts == 1);
    jvalue_ref s = state_read();
    CHECK("G9 no relaunch while still mounted", !jis_array(jobject_get(s, key_buf("launched"))));
    j_release(&s);
    unmounts = 0; unmount_errno = EINVAL;
    gint64 start = g_get_monotonic_time();
    CHECK("G9 not mounted: stock Home launched", hc_gate_main(&c, 2, argv) == 0 && unmounts == 1);
    CHECK("G10 returns without waiting", g_get_monotonic_time() - start < G_USEC_PER_SEC);
    settle_release(1);
    /* The request comes from a child in its own session, so a kill of the gate's group misses it. */
    char *text = NULL; gboolean apart = FALSE;
    if (g_file_get_contents(calls, &text, NULL, NULL))
        for (char **l = g_strsplit(text, "\n", -1), **it = l; *it; it++) {
            jvalue_ref e = parse_object(*it);
            if (e && g_str_has_suffix(get_string(e, "uri"), "/launch")) {
                int64_t group = 0; jnumber_get_i64(jobject_get(e, key_buf("group")), &group);
                apart = group != getpgrp();
            }
            j_release(&e);
        }
    g_free(text);
    CHECK("G10 release request leaves the gate's process group", apart);
    s = state_read();
    CHECK("G9 launched once", jarray_size(jobject_get(s, key_buf("launched"))) == 1);
    j_release(&s); g_free(home); g_free(calls); g_free(state);
}

int main(int argc, char **argv)
{
    if (argc != 3) return 2;
    setbuf(stdout, NULL);
    root = g_build_filename(argv[1], "gate", NULL);
    char *rm = g_strdup_printf("rm -rf '%s'", root); if (system(rm)) return 2; g_free(rm);
    mkdir_p(root);
    program = fixture("luna-send"); state_dir = fixture("state"); gate_dir = fixture("runtime/home-gate");
    log_path = fixture("home.log");
    char *source = NULL;
    CHECK("fixture source", g_file_get_contents(argv[2], &source, NULL, NULL));
    write_file(program, source); chmod(program, 0755); g_free(source);
    for (unsigned i = 0; i < G_N_ELEMENTS(cases); i++) run_case(&cases[i]);
    test_release_guards();
    char *log = NULL;
    g_file_get_contents(log_path, &log, NULL, NULL);
    CHECK("G10 decisions logged", log && strstr(log, "home gate: Home key") && strstr(log, "opened my.home") &&
        strstr(log, "LG Home chosen") && strstr(log, "restoring stock Home") && strstr(log, "gate removed from") && strstr(log, "no reply awaited") && !strstr(log, "not confirmed"));
    g_free(log);
    printf("%u passed, %u failed (home gate)\n", checks - failures, failures);
    return failures ? 1 : 0;
}
