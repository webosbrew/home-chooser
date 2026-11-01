/* SPDX-License-Identifier: Apache-2.0 */
/* Policy regression tests use real firmware pbnjson/GLib under qemu-arm.
 * W identifiers map every old shell scenario in docs/research/native-watcher-port.md
 * (removed from the tree; see git history). */
#include "watcher/internal.h"
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <sys/time.h>

static unsigned checks, failures;
#define CHECK(item, expr) do { checks++; if (!(expr)) { failures++; fprintf(stderr, "FAIL %s line %d: %s\n", item, __LINE__, #expr); } } while (0)
typedef struct {
    HCWatch *w;
    HCWatchConfig c;
    GPtrArray *paths, *calls;
    GHashTable *apps;
    jvalue_ref record;
    const char *profiles, *foreground, *window, *title;
    int major, key_calls, stock_calls, key_fail, access_fail, applies, removes;
    int sets, defaults, scans, alerts, toasts, closes, preload_on, preload_off;
    int set_fail, default_fail, alert_fail, toast_fail, preload_fail;
    int silent, flap, wait_action, wait_count, title_gets;
    int gate_on, gate_off, gate_fail, gate_last, gate_off_with_choice, gate_off_at_wait, stock_at_wait;
    int running_dead, close_dead, running_calls, launches; /* calls left unanswered; `running` calls seen */
    const char *home_pid; /* NULL: stock Home not running */
    const char *last_input; /* getLastInput: NULL no answer, "!refuse" returnValue false, "!none" no id */
    int launch_fail, last_input_calls; /* launch_fail 1: refused, 2: no reply */
    GString *launched; /* ids of every launch, each followed by a space */
    gboolean live, string_record, absent_record, ignore_write;
    gint64 clock;
    GArray *write_times, *waits;
} Fixture;
static const char *base;
static unsigned serial;
static char *path(Fixture *f, const char *root, const char *name) {
    char *p = g_build_filename(root, name, NULL); g_ptr_array_add(f->paths, p); return p;
}
static void file(const char *p, const char *text) {
    char *dir = g_path_get_dirname(p); mkdir_p(dir); g_free(dir);
    CHECK("fixture file", g_file_set_contents(p, text, -1, NULL));
}
static void choice(Fixture *f, const char *id) { file(f->w->choice_file, id); }
static void record(Fixture *f, const char *json) {
    j_release(&f->record); f->record = parse_object(json);
}
static void installed(Fixture *f, const char *id, int state) {
    g_hash_table_replace(f->apps, g_strdup(id), GINT_TO_POINTER(state));
}
static jvalue_ref response(gboolean value) {
    jvalue_ref r = jobject_create(); put_bool(r, "returnValue", value); return r;
}
static char *fake_call(const char *uri, const char *payload, int timeout, void *context) {
    Fixture *f = context;
    CHECK("W06 bounded call", timeout > 0 && timeout <= 10);
    jvalue_ref p = parse_object(payload), r = NULL;
    CHECK("W06 valid JSON", jis_object(p));
    char *entry = g_strdup_printf("%s %s", uri, payload); g_ptr_array_add(f->calls, entry);
    const char *method = strrchr(uri, '/') + 1;
    if (eq(method, "getAppInfo")) {
        char *id = get_string(p, "id");
        int state = GPOINTER_TO_INT(g_hash_table_lookup(f->apps, id));
        if (eq(id, "my.home")) f->title_gets++;
        if (f->silent) state = 0;
        if (f->flap && eq(id, "my.home")) { f->flap--; state = -1; }
        if (state) {
            r = response(state == 1);
            if (state == 1) {
                jvalue_ref info = jobject_create(); put_str(info, "id", id);
                if (f->title) put_str(info, "title", f->title);
                if (f->window) put_str(info, "defaultWindowType", f->window);
                jobject_set(r, key_buf("appInfo"), info);
            }
        }
        g_free(id);
    } else if (eq(method, "getSystemSettings")) {
        r = response(!f->absent_record);
        if (f->absent_record) put_str(r, "errorText", "There is no matched result from DB");
        else {
            jvalue_ref settings = jobject_create();
            jobject_set(settings, key_buf("defaultApps"), f->string_record ? jstring_create(jvalue_stringify(f->record)) : jvalue_duplicate(f->record));
            jobject_set(r, key_buf("settings"), settings);
        }
    } else if (eq(method, "setSystemSettings")) {
        f->sets++; g_array_append_val(f->write_times, f->clock);
        r = response(!f->set_fail);
        if (f->live && !f->set_fail && !f->ignore_write) {
            j_release(&f->record); f->record = jvalue_duplicate(member(member(p, "settings"), "defaultApps"));
            f->absent_record = FALSE;
        }
    } else if (eq(method, "setDefaultApp")) {
        f->defaults++; g_array_append_val(f->write_times, f->clock);
        r = response(!f->default_fail);
        if (f->default_fail == 1) put_str(r, "errorText", "Unknown METHOD setDefaultApp");
        else if (f->default_fail) put_str(r, "errorText", "Denied");
        if (f->live && !f->default_fail) {
            char *id = get_string(p, "appId"); put_str(f->record, "home", id); g_free(id);
        }
    } else if (eq(method, "getProfiles")) r = parse_object(f->profiles);
    else if (eq(method, "rescan")) { f->scans++; r = response(TRUE); }
    else if (eq(method, "createAlert")) {
        f->alerts++;
        CHECK("W18 alert identity", eq(HC_APP_ID, "org.webosbrew.homechooser"));
        char *title = get_string(p, "title"); CHECK("W18 alert title", eq(title, "Home Chooser")); g_free(title);
        CHECK("W18 alert button", jarray_size(member(p, "buttons")) == 1);
        r = f->alert_fail == -1 ? NULL : response(!f->alert_fail);
        if (f->alert_fail > 0) f->alert_fail--;
    } else if (eq(method, "createToast")) {
        f->toasts++; r = f->toast_fail == -1 ? NULL : response(!f->toast_fail);
        if (f->toast_fail > 0) f->toast_fail--;
    } else if (eq(method, "setPreloadPolicy")) {
        bool on = false; jvalue_ref app = member(p, "application"); jboolean_get(member(app, "isEnabled"), &on);
        char *id = get_string(app, "id"); CHECK("W14 stock preload", eq(id, STOCK)); g_free(id);
        CHECK("W14 volatile preload", !jis_valid(member(p, "permanent")));
        if (on) f->preload_on++; else f->preload_off++;
        r = response(!f->preload_fail); if (f->preload_fail > 0) f->preload_fail--;
    } else if (eq(method, "getForegroundAppInfo")) {
        if (f->foreground) { r = response(TRUE); put_str(r, "appId", f->foreground); }
    } else if (eq(method, "running")) {
        f->running_calls++;
        if (f->running_dead) f->running_dead--;
        else {
            r = response(TRUE); jvalue_ref list = jarray_create(NULL);
            if (f->home_pid) { jvalue_ref e = jobject_create(); put_str(e, "id", STOCK); put_str(e, "processid", f->home_pid); jarray_append(list, e); }
            jobject_set(r, key_buf("running"), list);
        }
    } else if (eq(method, "launch")) {
        char *id = get_string(p, "id"); CHECK("G20 launch only stock Home", f->major >= 10 || eq(id, STOCK));
        g_string_append_printf(f->launched, "%s ", id); g_free(id);
        f->launches++; r = f->launch_fail == 2 ? NULL : response(!f->launch_fail);
    } else if (eq(method, "getLastInput")) {
        f->last_input_calls++;
        if (eq(f->last_input, "!refuse")) r = response(FALSE);
        else if (f->last_input) { r = response(TRUE); if (!eq(f->last_input, "!none")) put_str(r, "lastSourceAppId", f->last_input); }
    } else if (eq(method, "closeByAppId")) {
        f->closes++; r = f->close_dead ? NULL : response(TRUE); if (f->close_dead) f->close_dead--;
        char *id = get_string(p, "id"); CHECK("W16 close only stock", eq(id, STOCK)); g_free(id);
        bool force = false; jboolean_get(member(p, "forceMode"), &force); CHECK("W16 force close", force);
    } else CHECK("known Luna method", FALSE);
    char *out = r ? g_strdup(jvalue_stringify(r)) : NULL;
    j_release(&r); j_release(&p); return out;
}
static int fake_key(gboolean stock, void *context) {
    Fixture *f = context;
    if (stock) f->stock_calls++; else f->key_calls++;
    if (f->key_fail) { f->key_fail--; return 1; } return 0;
}
static int fake_access(const char *action, void *context) {
    Fixture *f = context;
    if (eq(action, "apply")) f->applies++; else f->removes++;
    return f->access_fail;
}
static int fake_gate(const char *choice, void *context) {
    Fixture *f = context;
    if (choice) { f->gate_on++; char *id = watch_choice(f->w); CHECK("G11 gate for the stored choice", eq(choice, id)); g_free(id); }
    else { f->gate_off++; if (g_file_test(f->w->choice_file, G_FILE_TEST_EXISTS)) f->gate_off_with_choice++; }
    f->gate_last = choice ? 1 : 0;
    return f->gate_fail;
}
static gint64 now(void *context) { return ((Fixture *)context)->clock; }
static void wait_ms(unsigned ms, void *context) {
    Fixture *f = context; f->wait_count++; f->clock += ms / 1000; g_array_append_val(f->waits, ms);
    f->gate_off_at_wait = f->gate_off; f->stock_at_wait = f->stock_calls;
    int action = f->wait_action; f->wait_action = 0;
    if (action == 1) { installed(f, HC_APP_ID, -1); watch_remove_tree(f->c.app_dir); }
    if (action == 2) { installed(f, "my.home", 1); }
    if (action == 3) { installed(f, HC_APP_ID, 1); mkdir_p(f->c.app_dir); }
    if (action == 4) choice(f, "other.home");
    if (action == 5) record(f, "{\"home\":\"other.home\"}");
    if (action == 6) hc_watch_stop(f->w, 0, "stopped by signal");
    if (action == 7) unlink(f->w->choice_file);
    if (action == 8) installed(f, "my.home", -1);
    if (action == 9) record(f, "{\"home\":\"my.home\"}");
    if (action == 10) { installed(f, HC_APP_ID, -1); watch_remove_tree(f->c.app_dir); f->wait_action = 3; }
}
static Fixture *setup(int major, const char *id) {
    Fixture *f = g_new0(Fixture, 1); f->paths = g_ptr_array_new_with_free_func(g_free);
    f->calls = g_ptr_array_new_with_free_func(g_free); f->apps = g_hash_table_new_full(g_str_hash, g_str_equal, g_free, NULL);
    f->write_times = g_array_new(FALSE, FALSE, sizeof(gint64)); f->waits = g_array_new(FALSE, FALSE, sizeof(unsigned));
    f->major = major; f->clock = 1000; f->live = TRUE; f->launched = g_string_new(NULL);
    f->title = "Pretty Home";
    f->profiles = "{\"returnValue\":true,\"profiles\":{\"defaultApps\":{\"_source\":\"factory\",\"categories\":{\"home\":\"com.webos.app.home\",\"kids\":\"k.app\"},\"other\":{\"a\":[1,2]},\"_hidden\":true},"
        "\"idleApp\":{\"_configuration\":\"idleApp.json\",\"support\":true,\"appId\":\"com.webos.app.home\",\"keep\":\"me\"}}}";
    f->c = hc_watch_config();
    char *name = g_strdup_printf("case-%u", serial++), *root = path(f, base, name); g_free(name);
    f->c.state_dir = path(f, root, "state"); f->c.osrelease = path(f, root, "os-release");
    f->c.app_dir = path(f, root, "hc"); f->c.override = path(f, root, "override/o.json");
    f->c.init_link = path(f, root, "init.d/homechooser"); f->c.log = path(f, root, "watch.log");
    f->c.runtime = path(f, root, "rt"); f->c.app_dirs = f->c.install_dirs = path(f, root, "apps");
    f->c.preload_retry_ms = f->c.preload_settle_ms = f->c.gate_settle_ms = 0; f->c.notify_retry_ms = 3000;
    f->c.gate_binary = path(f, root, "service/homechooser-home-gate"); f->c.gate_dir = path(f, root, "rt/home-gate");
    f->gate_last = -1; f->c.proc = path(f, root, "proc");
    mkdir_p(root); mkdir_p(f->c.state_dir); mkdir_p(f->c.app_dir);
    char *os = g_strdup_printf("VERSION_ID=\"%d.0.0\"\n", major); file(f->c.osrelease, os); g_free(os);
    char *init_dir = g_path_get_dirname(f->c.init_link); mkdir_p(init_dir); g_free(init_dir);
    CHECK("fixture init link", !symlink("/missing-package/homechooser", f->c.init_link));
    const char *names[] = {"homekey", "hook/generated", "hook-stale", "boot/homechooser", "keyfilters-stock.json", "keyfilter.lock", NULL};
    for (int i = 0; names[i]; i++) file(path(f, f->c.state_dir, names[i]), "x");
    installed(f, HC_APP_ID, 1); installed(f, "my.home", 1); installed(f, "other.home", 1);
    installed(f, "gone.app", -1); installed(f, STOCK, 1); installed(f, FULLHOME, -1);
    record(f, "{\"home\":\"my.home\",\"kidsApp\":\"k.app\"}");
    HCWatchOps ops = {.call = fake_call, .key = fake_key, .access = fake_access, .gate = fake_gate, .now = now, .wait = wait_ms, .context = f};
    f->w = hc_watch_new(&f->c, &ops);
    if (id) choice(f, id);
    return f;
}
static void teardown(Fixture *f) {
    hc_watch_free(f->w); watch_remove_tree(g_ptr_array_index(f->paths, 0));
    g_ptr_array_free(f->paths, TRUE); g_ptr_array_free(f->calls, TRUE); g_hash_table_destroy(f->apps);
    j_release(&f->record); g_array_free(f->write_times, TRUE); g_array_free(f->waits, TRUE);
    g_string_free(f->launched, TRUE); g_free(f);
}
static void pump(void) {
    for (int i = 0; i < 50 && g_main_context_pending(NULL); i++) g_main_context_iteration(NULL, FALSE);
}
static void start(Fixture *f) { hc_watch_start(f->w); pump(); }
static void event(Fixture *f, char tag, const char *text) { hc_watch_event(f->w, tag, text); pump(); }
static void settings(Fixture *f, const char *json) {
    char *p = g_strdup_printf("{\"returnValue\":true,\"settings\":{\"defaultApps\":%s}}", json);
    event(f, 'S', p); g_free(p);
}
static gboolean home_is(Fixture *f, const char *expected) {
    char *s = get_string(f->record, "home"); gboolean same = eq(s, expected); g_free(s); return same;
}
static gboolean contains(Fixture *f, const char *text) {
    for (unsigned i = 0; i < f->calls->len; i++) if (strstr(g_ptr_array_index(f->calls, i), text)) return TRUE;
    return FALSE;
}
static void test_choices(void) {
    const char *good[] = {"my.home", "my.home\n", "my.home\r\n", "  my.home  \n", "\t my.home\r\n\n", "\n\nmy.home"};
    for (unsigned i = 0; i < G_N_ELEMENTS(good); i++) {
        Fixture *f = setup(8, good[i]); char *id = watch_choice(f->w);
        CHECK("W01 trim", eq(id, "my.home")); g_free(id); start(f);
        settings(f, "{\"home\":\"other\"}"); CHECK("W08 merged choice", home_is(f, "my.home")); teardown(f);
    }
    const char *bad[] = {NULL, "", "  \r\n", "a b", "x\"y", "x;y", "a/b", "a$b", "$(id)", "a\nb"};
    for (unsigned i = 0; i < G_N_ELEMENTS(bad); i++) {
        Fixture *f = setup(8, bad[i]); start(f);
        CHECK("W01 bad choice clean exit", hc_watch_status(f->w) == 0);
        CHECK("W01 bad choice no writes", f->sets + f->defaults == 0); teardown(f);
    }
    Fixture *f = setup(8, "my.home"); start(f); unlink(f->w->choice_file); settings(f, "{}");
    CHECK("W01 choice removed exit", hc_watch_status(f->w) == 0); CHECK("W01 no write after removal", !f->sets); teardown(f);
    const char *releases[] = {"VERSION=\"8.2\"\nVERSION_ID=\"9.1\"\n", "VERSION=\"webOS 8.2\"\n", "PRETTY_NAME=\"webOS 11\"\n", "broken\n"};
    int majors[] = {9, 8, 11, 0};
    for (unsigned i = 0; i < G_N_ELEMENTS(releases); i++) {
        f = setup(8, NULL); file(f->c.osrelease, releases[i]); HCWatch *w = hc_watch_new(&f->c, &f->w->ops);
        CHECK("W01 release priority", w->major == majors[i]); hc_watch_free(w); teardown(f);
    }
}
static void test_record(void) {
    for (int major = 5; major <= 12; major++) {
        Fixture *f = setup(major, "my.home"); start(f); settings(f, "{\"home\":\"my.home\"}");
        CHECK("W08 matching no write", f->sets + f->defaults == 0);
        settings(f, "{\"home\":\"other\"}");
        CHECK("W08 version method", major < 9 ? f->sets == 1 && !f->defaults : f->defaults == 1 && !f->sets);
        CHECK("W08 other members", contains(f, "setDefaultApp") || contains(f, "\"kidsApp\":\"k.app\""));
        CHECK("W12 refresh range", f->key_calls == (major >= 6 && major <= 9)); teardown(f);
    }
    const char *records[] = {"{\"kidsApp\":\"k.app\"}", "{\"home\":null,\"kidsApp\":\"k.app\"}", "{\"home\":\"\",\"kidsApp\":\"k.app\"}"};
    for (unsigned i = 0; i < G_N_ELEMENTS(records); i++) {
        Fixture *f = setup(8, "my.home"); record(f, records[i]); start(f); settings(f, records[i]);
        CHECK("W08 unset replaced", home_is(f, "my.home")); CHECK("W08 one home", f->sets == 1); teardown(f);
    }
    Fixture *f = setup(8, "my.home"); f->string_record = TRUE; record(f, "{\"home\":\"other\",\"kidsApp\":\"k.app\"}"); start(f);
    event(f, 'S', "{\n\"settings\":{\"defaultApps\":\"{\\\"home\\\":\\\"other\\\"}\"}}\n");
    CHECK("W08 string JSON", f->sets == 1 && home_is(f, "my.home")); teardown(f);
    f = setup(6, "my.home"); f->absent_record = TRUE; start(f); event(f, 'S', "{\"returnValue\":false,\"errorText\":\"There is no matched result from DB\"}");
    CHECK("W08 absent record", f->sets == 1 && home_is(f, "my.home")); teardown(f);
    f = setup(8, "my.home"); record(f, "{\"home\":\"other\",\"nested\":{\"a\":1}}"); start(f); settings(f, "{\"home\":\"other\"}");
    CHECK("W08 nonflat refused", !f->sets); event(f, 'S', "noise"); event(f, 'S', "{\"subscribed\":true}"); CHECK("W06 noise ignored", !f->sets); teardown(f);
    for (int mode = 1; mode <= 2; mode++) {
        f = setup(11, "my.home"); f->default_fail = mode; start(f); settings(f, "{\"home\":\"other\"}");
        CHECK("W08 fallback only unknown", f->defaults == 1 && f->sets == (mode == 1)); teardown(f);
    }
}
static void test_backoff(void) {
    Fixture *f = setup(8, "my.home"); record(f, "{\"home\":\"other\"}"); f->live = FALSE; start(f);
    const gint64 times[] = {1000, 1010, 1030, 1070, 1150, 1310, 1610, 1910, 2210, 2510};
    for (unsigned i = 0; i < G_N_ELEMENTS(times); i++) { settings(f, "{\"home\":\"other\"}"); CHECK("W09 doubling/cap", g_array_index(f->write_times, gint64, i) == times[i]); }
    CHECK("W09 storm attempts", f->sets == 10);
    f->clock += 1000; settings(f, "{}"); CHECK("W09 streak reset", f->w->streak == 0); teardown(f);
    f = setup(8, "my.home"); start(f); settings(f, "{}"); settings(f, "{}");
    CHECK("W09 stale no wait", f->sets == 1 && !f->wait_count); teardown(f);
    f = setup(8, "my.home"); f->live = FALSE; record(f, "{\"home\":\"other\"}"); start(f); settings(f, "{}"); f->wait_action = 5; settings(f, "{}");
    CHECK("W09 reread after wait", f->sets == 2); teardown(f);
    f = setup(8, "my.home"); f->live = FALSE; record(f, "{\"home\":\"other\"}"); start(f); settings(f, "{}"); f->wait_action = 9; settings(f, "{}");
    CHECK("W09 corrected during wait", f->sets == 1); teardown(f);
    for (int action = 4; action <= 7; action++) {
        f = setup(8, "my.home"); f->live = FALSE; record(f, "{\"home\":\"other\"}"); start(f); settings(f, "{}"); f->wait_action = action;
        settings(f, "{}");
        if (action == 4) CHECK("W09 changed choice used", contains(f, "\"home\":\"other.home\""));
        if (action == 6 || action == 7) CHECK("W09 no write after stop", f->sets == 1 && hc_watch_status(f->w) == 0);
        teardown(f);
    }
}
static void test_profiles(void) {
    for (int major = 5; major <= 12; major++) {
        Fixture *f = setup(major, "my.home"); start(f);
        CHECK("W10 version gate", g_file_test(f->c.override, G_FILE_TEST_EXISTS) == (major >= 10));
        if (major >= 10) {
            char *s = watch_read(f->c.override); jvalue_ref p = parse_object(s);
            CHECK("W10 metadata removed", !strstr(s, "_source") && !strstr(s, "_hidden"));
            CHECK("W10 categories/other preserved", strstr(s, "k.app") && jis_object(member(member(p, "defaultApps"), "other")));
            CHECK("W10 rescan once", f->scans == 1); check_override(f->w, "my.home"); CHECK("W10 good no rescan", f->scans == 1);
            file(f->c.override, "{\"defaultApps\":{\"categories\":{\"home\":\"stale.app\"}}}"); check_override(f->w, "my.home");
            CHECK("W10 wrong repaired", f->scans == 2); j_release(&p); g_free(s);
        }
        teardown(f);
    }
    Fixture *f = setup(10, "my.home"); f->profiles = "{\"defaultApps\":{\"x\":1}}"; start(f);
    CHECK("W10 missing categories refused", !g_file_test(f->c.override, G_FILE_TEST_EXISTS)); teardown(f);
}
/* The override's idleApp: "" when absent, else "<support>/<appId or ->/<keep or ->". */
static char *idle_text(Fixture *f) {
    char *home = NULL; jvalue_ref idle = NULL; override_read(f->w, &home, &idle); g_free(home);
    if (!idle) return g_strdup("");
    bool support = false; jboolean_get(member(idle, "support"), &support);
    char *app = get_string(idle, "appId"), *keep = get_string(idle, "keep");
    char *s = g_strdup_printf("%s/%s/%s%s", support ? "true" : "false", app ? app : "-", keep ? keep : "-",
        jis_valid(member(idle, "_configuration")) ? "/meta" : "");
    g_free(app); g_free(keep); j_release(&idle); return s;
}
#define IDLE_IS(item, f, expected) do { char *s_ = idle_text(f); CHECK(item, eq(s_, expected)); if (!eq(s_, expected)) fprintf(stderr, "  idleApp %s\n", s_); g_free(s_); } while (0)
/* I01-I08: webOS 10 and later, the override carries idleApp (docs/settings-app.md). */
static void test_idle_app(void) {
    for (int major = 10; major <= 12; major++) {
        Fixture *f = setup(major, "my.home"); start(f);
        IDLE_IS("I01 full-screen names the launcher, keeps other keys, drops metadata", f, "true/my.home/me");
        CHECK("I01 asked for idleApp", contains(f, "\"names\":[\"defaultApps\",\"idleApp\"]"));
        int scans = f->scans; check_override(f->w, "my.home"); CHECK("I02 good no rescan", f->scans == scans);
        file(f->c.override, "{\"defaultApps\":{\"categories\":{\"home\":\"my.home\"}},\"idleApp\":{\"support\":true,\"appId\":\"stale.app\"}}");
        check_override(f->w, "my.home"); CHECK("I02 wrong idleApp repaired", f->scans == scans + 1); IDLE_IS("I02 repaired value", f, "true/my.home/me");
        file(f->c.override, "{\"defaultApps\":{\"categories\":{\"home\":\"my.home\"}}}");
        check_override(f->w, "my.home"); CHECK("I02 missing idleApp repaired", f->scans == scans + 2); IDLE_IS("I02 added", f, "true/my.home/me");
        teardown(f);
        f = setup(major, "my.home"); f->window = "overlay"; start(f);
        IDLE_IS("I03 overlay: support false, no appId", f, "false/-/me");
        scans = f->scans; check_override(f->w, "my.home"); CHECK("I03 overlay ok", f->scans == scans);
        f->window = NULL; check_override(f->w, "my.home"); CHECK("I03 window change rewrites", f->scans == scans + 1);
        IDLE_IS("I03 now full-screen", f, "true/my.home/me"); teardown(f);
        /* I04: never an idleApp for an id getAppInfo does not know, or an empty one. */
        f = setup(major, "my.home"); start(f);
        check_override(f->w, "gone.app"); IDLE_IS("I04 unknown id: no idleApp", f, "");
        file(f->c.override, "{}"); check_override(f->w, ""); IDLE_IS("I04 empty id: no idleApp", f, "");
        f->silent = 1; file(f->c.override, "{}"); check_override(f->w, "my.home"); IDLE_IS("I04 no answer: no idleApp", f, "");
        teardown(f);
        /* I05: the launcher goes missing: the whole override goes; it comes back with the launcher. */
        f = setup(major, "my.home"); start(f); installed(f, "my.home", -1); event(f, 'A', "{}");
        CHECK("I05 missing launcher drops the override", !g_file_test(f->c.override, G_FILE_TEST_EXISTS));
        installed(f, "my.home", 1); event(f, 'A', "{}"); IDLE_IS("I05 back again", f, "true/my.home/me"); teardown(f);
        /* I06: uninstall drops it too. */
        f = setup(major, "my.home"); start(f); installed(f, HC_APP_ID, -1); watch_remove_tree(f->c.app_dir); event(f, 'A', "{}");
        CHECK("I06 uninstall drops the override", hc_watch_status(f->w) == 0 && !g_file_test(f->c.override, G_FILE_TEST_EXISTS)); teardown(f);
    }
    for (int mode = 0; mode < 4; mode++) {
        /* I07: leftover repair also reads idleApp. */
        Fixture *f = setup(10, NULL); record(f, "{\"home\":\"my.home\"}");
        const char *idle[] = {"{\"support\":true,\"appId\":\"gone.app\"}", "{\"support\":false,\"appId\":\"gone.app\"}",
            "{\"support\":true,\"appId\":\"my.home\"}", "{\"appId\":\"gone.app\"}"};
        char *text = g_strdup_printf("{\"defaultApps\":{\"categories\":{\"home\":\"my.home\"}},\"idleApp\":%s}", idle[mode]);
        file(f->c.override, text); g_free(text); start(f);
        gboolean gone = mode == 0 || mode == 3;
        CHECK("I07 idleApp leftover", g_file_test(f->c.override, G_FILE_TEST_EXISTS) == !gone);
        CHECK("I07 record untouched", !f->sets && !f->defaults && home_is(f, "my.home"));
        CHECK("I07 confirmed once", f->wait_count == (gone ? 1 : 0)); teardown(f);
    }
}
static void test_remove_return(void) {
    for (int major = 5; major <= 12; major++) for (int full = 0; full <= 1; full++) {
        Fixture *f = setup(major, "my.home"); installed(f, FULLHOME, full ? 1 : -1); start(f);
        installed(f, "my.home", -1); event(f, 'A', "{\"apps\":[]}");
        CHECK("W11 missing stored", eq(f->w->missing, "my.home"));
        CHECK("W11 stock by release", home_is(f, major < 10 ? (full ? FULLHOME : STOCK) : NULL));
        CHECK("W11 hook stock range", f->stock_calls == (major >= 6 && major <= 9));
        CHECK("W17 one removal alert", f->alerts == 1); CHECK("W11 choice kept", g_file_test(f->w->choice_file, G_FILE_TEST_EXISTS));
        int writes = f->sets + f->defaults; settings(f, "{}"); event(f, 'T', NULL);
        CHECK("W11 missing not reasserted", f->sets + f->defaults == writes && f->alerts == 1);
        installed(f, "my.home", 1); event(f, 'A', "{}");
        CHECK("W12 installed again", !f->w->missing && home_is(f, "my.home")); CHECK("W12 counters reset", !f->w->last_write && !f->w->streak);
        CHECK("W12 override return", major < 10 || g_file_test(f->c.override, G_FILE_TEST_EXISTS));
        installed(f, "my.home", -1); event(f, 'T', NULL); CHECK("W17 second removal", f->alerts == 2); teardown(f);
    }
    Fixture *f = setup(8, "my.home"); f->flap = 1; start(f); CHECK("W05 transient no revert", !f->sets && !f->alerts); teardown(f);
    f = setup(8, "my.home"); start(f); f->silent = 1; event(f, 'A', "{}"); CHECK("W05 silent no revert", !f->sets && !f->alerts); teardown(f);
    f = setup(8, "my.home"); installed(f, "my.home", -1); file(path(f, f->c.app_dirs, "my.home/appinfo.json"), "{\"id\":\"my.home\"}"); start(f);
    CHECK("W05 disk guards startup", !f->sets); event(f, 'T', NULL); CHECK("W05 disk not guard later", f->sets == 1); teardown(f);
    f = setup(10, "my.home"); installed(f, "my.home", -1); start(f); CHECK("W17 missing at start silent", f->sets == 1 && !f->alerts); teardown(f);
}
static void test_baked(void) {
    for (int major = 5; major <= 12; major++) {
        Fixture *f = setup(major, "my.home"); start(f); settings(f, "{\"home\":\"my.home\"}");
        CHECK("W12 unchanged choice", f->key_calls == (major >= 6 && major <= 9));
        choice(f, "other.home"); settings(f, "{\"home\":\"my.home\"}");
        CHECK("W12 changed choice", f->key_calls == 2 * (major >= 6 && major <= 9)); teardown(f);
    }
    Fixture *f = setup(8, "my.home"); f->key_fail = 1; start(f); CHECK("W12 failed not cached", !f->w->baked); event(f, 'A', "{}");
    CHECK("W12 failure retried", eq(f->w->baked, "my.home") && f->key_calls == 2);
    installed(f, "gone.app", -1); choice(f, "gone.app"); settings(f, "{}"); CHECK("W12 absent not baked", !eq(f->w->baked, "gone.app")); teardown(f);
    f = setup(8, "my.home"); start(f); installed(f, "my.home", -1); event(f, 'A', "{}"); choice(f, "other.home"); settings(f, "{}");
    CHECK("W12 changed missing choice", !f->w->missing && home_is(f, "other.home")); teardown(f);
}
static void test_uninstall(void) {
    for (int major = 5; major <= 12; major++) for (int mode = 0; mode < 4; mode++) {
        Fixture *f = setup(major, mode & 1 ? NULL : "my.home");
        if (mode >= 2) { installed(f, HC_APP_ID, -1); watch_remove_tree(f->c.app_dir); }
        start(f);
        if (mode < 2) { installed(f, HC_APP_ID, -1); watch_remove_tree(f->c.app_dir); event(f, 'A', "{}"); }
        if ((mode & 1) && mode < 2) { /* exited without a choice; next boot runs restore */
            hc_watch_free(f->w); HCWatchOps ops = {.call=fake_call,.key=fake_key,.access=fake_access,.now=now,.wait=wait_ms,.context=f};
            f->w = hc_watch_new(&f->c, &ops); start(f);
        }
        CHECK("W13 clean exit", hc_watch_status(f->w) == 0);
        CHECK("W13 stock record", home_is(f, major < 10 ? STOCK : NULL));
        CHECK("W13 access removed", f->removes == 1);
        CHECK("W13 files removed", !g_file_test(f->w->choice_file, G_FILE_TEST_EXISTS) && !g_file_test(f->c.init_link, G_FILE_TEST_IS_SYMLINK));
        CHECK("W13 boot/cache/flag removed", !g_file_test(path(f, f->c.state_dir, "boot"), G_FILE_TEST_EXISTS) && !g_file_test(path(f, f->c.state_dir, "homekey"), G_FILE_TEST_EXISTS));
        CHECK("W13 key filter snapshot and lock removed", !g_file_test(path(f, f->c.state_dir, "keyfilters-stock.json"), G_FILE_TEST_EXISTS) && !g_file_test(path(f, f->c.state_dir, "keyfilter.lock"), G_FILE_TEST_EXISTS));
        CHECK("W18 one uninstall toast", f->toasts == 1 && !f->alerts);
        CHECK("W13 stock refresh", f->stock_calls == (major >= 6 && major <= 9));
        teardown(f);
    }
    for (int mode = 0; mode < 4; mode++) {
        Fixture *f = setup(10, "my.home"); start(f);
        if (mode == 0) f->access_fail = 1;
        if (mode == 1) f->set_fail = 1;
        if (mode == 2) f->ignore_write = TRUE;
        if (mode == 3) { hc_watch_free(f->w); f->major = 8; file(f->c.osrelease, "VERSION_ID=8\n"); HCWatchOps ops={.call=fake_call,.key=fake_key,.access=fake_access,.wait=wait_ms,.now=now,.context=f}; f->w=hc_watch_new(&f->c,&ops); f->key_fail=1; }
        installed(f, HC_APP_ID, -1); watch_remove_tree(f->c.app_dir); event(f, 'T', NULL);
        CHECK("W13 failure restart", hc_watch_status(f->w) == 1);
        CHECK("W13 recovery retained", g_file_test(f->w->choice_file, G_FILE_TEST_EXISTS) && g_file_test(f->c.init_link, G_FILE_TEST_IS_SYMLINK));
        CHECK("W18 failure no toast", !f->toasts); teardown(f);
    }
    Fixture *f = setup(8, "my.home"); start(f); installed(f, "my.home", -1); f->wait_action=1; event(f,'A',"{}");
    CHECK("W03 simultaneous removal toast only", !f->alerts && f->toasts==1 && f->sets==1); teardown(f);
    f=setup(8,"my.home"); start(f); installed(f,HC_APP_ID,-1); watch_remove_tree(f->c.app_dir); f->wait_action=3; event(f,'A',"{}");
    CHECK("W03 temporary package update", hc_watch_status(f->w)==-1 && !f->sets && !f->removes); teardown(f);
    f=setup(8,NULL); watch_remove_tree(f->c.app_dir); f->wait_action=3; start(f);
    CHECK("W03 no-choice package update", !f->sets && !f->removes && hc_watch_status(f->w)==0); teardown(f);
    f=setup(8,"my.home"); record(f,"{\"home\":\"com.webos.app.home\"}"); unlink(f->c.init_link); file(f->c.init_link,"user file");
    installed(f,HC_APP_ID,-1); start(f); CHECK("W13 regular init file retained",g_file_test(f->c.init_link,G_FILE_TEST_IS_REGULAR)); teardown(f);
}
static void test_leftover(void) {
    for (int major=5;major<=12;major++) {
        Fixture *f=setup(major,NULL); record(f,"{\"home\":\"gone.app\",\"kidsApp\":\"k.app\"}");
        file(f->c.override,"{\"defaultApps\":{\"categories\":{\"home\":\"gone.app\"}}}"); start(f);
        CHECK("W04 leftover clean exit",hc_watch_status(f->w)==0);
        CHECK("W04 stock/null by version",home_is(f,major<10?STOCK:NULL));
        CHECK("W04 no notice",!f->alerts&&!f->toasts); CHECK("W04 two confirmations",f->wait_count==1);
        CHECK("W04 override version",g_file_test(f->c.override,G_FILE_TEST_EXISTS)==(major<10)); teardown(f);
    }
    const char *ids[]={STOCK,FULLHOME,"my.home"};
    for(unsigned i=0;i<G_N_ELEMENTS(ids);i++) {
        Fixture *f=setup(8,NULL); put_str(f->record,"home",ids[i]); start(f); CHECK("W04 installed/stock untouched",!f->sets); teardown(f);
    }
    for(int mode=0;mode<6;mode++) {
        Fixture *f=setup(11,NULL); record(f,"{\"home\":\"gone.app\"}");
        if(mode==0) f->silent=1;
        if(mode==1) file(path(f,f->c.app_dirs,"gone.app/appinfo.json"),"{}");
        if(mode==2) f->wait_action=4;
        if(mode==3) f->wait_action=5;
        if(mode==4) { record(f,"{\"home\":\"my.home\"}"); file(f->c.override,"{\"defaultApps\":{\"categories\":{\"home\":\"gone.app\"}}}"); }
        if(mode==5) file(f->c.override,"{\"defaultApps\":{\"categories\":{\"home\":\"my.home\"}}}");
        start(f);
        CHECK("W04 guard/independent repair",mode==5 ? f->sets==1 && g_file_test(f->c.override,G_FILE_TEST_EXISTS) : !f->sets);
        if(mode==2) CHECK("W04 choice appeared takes over",hc_watch_status(f->w)==-1);
        if(mode==4) CHECK("W04 override alone removed",!g_file_test(f->c.override,G_FILE_TEST_EXISTS));
        teardown(f);
    }
}
static void test_restore_races(void) {
    for (int major = 5; major <= 12; major++) {
        for (int order = 0; order < 4; order++) {
            Fixture *f = setup(major, "my.home"); start(f);
            if (order == 0) { installed(f,"my.home",-1); f->wait_action=1; event(f,'A',"{}"); }
            if (order == 1) { installed(f,HC_APP_ID,-1); f->wait_action=8; event(f,'A',"{}"); }
            if (order == 2) { installed(f,"my.home",-1); event(f,'A',"{}"); installed(f,HC_APP_ID,-1); event(f,'T',NULL); }
            if (order == 3) { installed(f,HC_APP_ID,-1); installed(f,"my.home",-1); event(f,'A',"{}"); }
            CHECK("W03 both removal orders clean",hc_watch_status(f->w)==0 && f->removes==1 && !g_file_test(f->w->choice_file,G_FILE_TEST_EXISTS));
            CHECK("W18 simultaneous toast only",f->alerts==(order==2) && f->toasts==1);
            CHECK("W13 stock write once",f->sets==1); teardown(f);
        }
        /* The launcher removed, Home Chooser removed seconds later (inside the launcher's confirmation):
         * the platform is stock once the launcher is confirmed gone, not one Home Chooser confirmation later. */
        Fixture *f = setup(major, "my.home"); start(f);
        gint64 t0 = f->clock; int scans0 = f->scans, waits0 = f->wait_count;
        installed(f, "my.home", -1); f->wait_action = 1; event(f, 'A', "{}");
        CHECK("W03 back-to-back removal: two confirmations", f->wait_count - waits0 == 2 && f->clock - t0 == 30);
        CHECK("W03 back-to-back removal: stock after the launcher's confirmation",
              f->write_times->len == 1 && g_array_index(f->write_times, gint64, 0) - t0 == 15);
        CHECK("W03 back-to-back removal: override dropped and rescanned then", major < 10 || f->scans == scans0 + 1);
        CHECK("W03 back-to-back removal: clean restore, toast only", hc_watch_status(f->w) == 0 && f->removes == 1
              && !f->alerts && f->toasts == 1 && home_is(f, major < 10 ? STOCK : NULL));
        CHECK("W03 back-to-back removal: stock keyFilters and no home gate before Home Chooser's wait",
              major < 6 || major > 9 || (f->stock_at_wait == 1 && f->gate_off_at_wait >= 1 && f->gate_last == 0));
        teardown(f);
        /* Home Chooser only looked gone (an update): the launcher's removal is reported after the wait. */
        f = setup(major, "my.home"); start(f);
        installed(f, "my.home", -1); f->wait_action = 10; event(f, 'A', "{}");
        CHECK("W03 Home Chooser back: watcher keeps running", hc_watch_status(f->w) == -1 && !f->removes);
        CHECK("W03 Home Chooser back: launcher missing, stock, one alert",
              eq(f->w->missing, "my.home") && f->alerts == 1 && !f->toasts && home_is(f, major < 10 ? STOCK : NULL));
        teardown(f);
        const char *records[] = {"{\"kidsApp\":\"k.app\"}","{\"home\":null,\"kidsApp\":\"k.app\"}","{\"home\":\"com.webos.app.home\",\"kidsApp\":\"k.app\"}"};
        for (unsigned i=0;i<G_N_ELEMENTS(records);i++) {
            Fixture *f=setup(major,"my.home"); record(f,records[i]); installed(f,HC_APP_ID,-1); start(f);
            CHECK("W13 idempotent restore",f->sets==(major<10 ? (i<2) : (i==2)));
            CHECK("W13 other entry retained",contains(f,"setSystemSettings")==FALSE || contains(f,"\"kidsApp\":\"k.app\"")); teardown(f);
        }
    }
}
static void test_notifications(void) {
    for(int mode=0;mode<7;mode++) {
        Fixture *f=setup(8,"my.home");
        if(mode==0) f->title=NULL;
        if(mode==1) { f->title=NULL; file(path(f,f->c.app_dirs,"my.home/appinfo.json"),"{\"title\":\"Disk Home\"}"); }
        if(mode==2) f->title="Bad \"one\"";
        if(mode==3) f->alert_fail=8;
        if(mode==4) f->alert_fail=-1;
        if(mode==5) { f->c.notify=FALSE; f->w->c.notify=FALSE; }
        if(mode==6) f->alert_fail=2;
        start(f); installed(f,"my.home",-1); event(f,'A',"{}");
        CHECK("W17 notice count",f->alerts==(mode==3?8:mode==6?3:mode==5?0:1));
        CHECK("W18 fallback only refusal",f->toasts==(mode==3));
        if(mode==0||mode==2) CHECK("W17 title fallback id",contains(f,"my.home was removed"));
        if(mode==1) CHECK("W17 title fallback disk",contains(f,"Disk Home was removed"));
        if(mode==6) CHECK("W18 retry spacing",g_array_index(f->waits,unsigned,f->waits->len-1)==3000);
        teardown(f);
    }
    Fixture *f=setup(8,"my.home"); start(f); installed(f,"my.home",-1); event(f,'A',"{}");
    choice(f,"other.home"); f->title="Other Home"; settings(f,"{}"); installed(f,"other.home",-1); event(f,'A',"{}");
    CHECK("W17 title cache follows new choice",f->alerts==2 && contains(f,"Other Home was removed")); teardown(f);
    f=setup(8,"my.home"); f->title=" \tPretty\nHome\r "; start(f);
    CHECK("W17 title controls trimmed",eq(f->w->title,"PrettyHome")); int n=f->title_gets;
    settings(f,"{\"home\":\"my.home\"}"); CHECK("W17 title cached",f->title_gets==n); teardown(f);
}
static void test_preload_close(void) {
    for(int major=5;major<=12;major++) {
        Fixture *f=setup(major,"my.home"); f->foreground="my.home"; start(f);
        CHECK("W14 preload version",f->preload_off==(major>=6));
        CHECK("W16 close version",f->closes==(major>=10));
        event(f,'F',"{\"appId\":\"my.home\"}"); event(f,'L',"{\"event\":\"launch\",\"appId\":\"my.home\"}");
        CHECK("W16 close once",f->closes==(major>=10));
        event(f,'F',"{\"appId\":\"com.webos.app.home\"}"); CHECK("W16 relaunch never closed",f->closes==(major>=10));
        hc_watch_stop(f->w,0,"stopped by signal"); hc_watch_free(f->w); f->w=NULL;
        CHECK("W19 clean exit preload",f->preload_on==(major>=6));
        /* teardown needs a watch, with no preload work remaining. */
        HCWatchOps ops={.call=fake_call,.context=f}; f->w=hc_watch_new(&f->c,&ops); teardown(f);
    }
    for(int major=10;major<=12;major++) {
        Fixture *f=setup(major,"my.home"); f->window="overlay"; f->foreground=STOCK; start(f);
        CHECK("W16 overlay no fresh launch",!f->closes);
        event(f,'L',"{\"event\":\"launch\",\"appId\":\"my.home\"}"); CHECK("W16 unsafe stock card",!f->closes);
        f->foreground=HC_APP_ID; event(f,'F',"{\"appId\":\"org.webosbrew.homechooser\"}"); CHECK("W16 unsafe HC card",!f->closes);
        event(f,'L',"{\"event\":\"background\",\"appId\":\"my.home\"}"); f->foreground="org.webosbrew.hbchannel";
        event(f,'F',"{\"appId\":\"org.webosbrew.hbchannel\"}"); CHECK("W16 background clears fresh",!f->closes);
        event(f,'L',"{\"event\":\"relaunch\",\"appId\":\"my.home\"}"); CHECK("W16 overlay safe card",f->closes==1);
        event(f,'L',"{\"event\":\"launch\",\"appId\":\"com.webos.app.home\"}"); CHECK("W16 relaunch logged",f->w->relaunch_logged); teardown(f);
    }
    Fixture *f=setup(11,"my.home"); f->preload_fail=2; f->foreground="my.home"; start(f);
    CHECK("W15 boot retries",f->preload_off==3 && f->closes==1); teardown(f);
    f=setup(11,"my.home"); f->preload_fail=20; start(f); CHECK("W15 retry cap",f->preload_off==12 && !f->w->preload_ready && !f->closes); teardown(f);
    f=setup(11,"my.home"); f->window="overlay"; f->foreground="other.home"; hc_watch_start(f->w);
    event(f,'L',"{\"event\":\"launch\",\"appId\":\"my.home\"}"); CHECK("W15 early launch remembered",f->closes==1); teardown(f);
    f=setup(11,"my.home"); hc_watch_start(f->w); preload_sync(f->w,1,'A'); pump();
    CHECK("W15 cancel off retry/settle",!f->preload_off && !f->w->preload_ready); teardown(f);
    f=setup(11,"my.home"); f->w->c.preload=FALSE; start(f); CHECK("W14 toggle",!f->preload_off && !f->closes); teardown(f);
    f=setup(11,STOCK); start(f); CHECK("W14 stock on",f->preload_on==1 && !f->preload_off); choice(f,"my.home"); event(f,'A',"{}");
    CHECK("W14 change off immediate ready",f->preload_off==1 && f->w->preload_ready); teardown(f);
    /* I08: webOS 10 closes once per boot and once per choice change, never in a loop. */
    f=setup(10,"my.home"); f->foreground="my.home"; start(f); CHECK("I08 10 closes after the launcher is in front",f->closes==1);
    for(int i=0;i<3;i++) { event(f,'L',"{\"event\":\"launch\",\"appId\":\"com.webos.app.home\"}"); event(f,'F',"{\"appId\":\"my.home\"}"); event(f,'T',NULL); }
    CHECK("I08 no close loop",f->closes==1 && f->w->relaunch_logged);
    choice(f,"other.home"); f->foreground="other.home"; settings(f,"{\"home\":\"my.home\"}"); CHECK("I08 choice change closes once more",f->closes==2);
    event(f,'F',"{\"appId\":\"other.home\"}"); CHECK("I08 still once",f->closes==2); teardown(f);
    f=setup(10,"my.home"); f->foreground="org.webosbrew.hbchannel"; start(f); CHECK("I08 not before the launcher is in front",!f->closes);
    f->foreground="my.home"; event(f,'F',"{\"appId\":\"my.home\"}"); CHECK("I08 then once",f->closes==1); teardown(f);
}
/* G11-G15: the 6-9 home gate follows the baked Home key redirect (docs/home-gate.md). */
static void test_gate(void) {
    for (int major = 5; major <= 12; major++) {
        Fixture *f = setup(major, "my.home"); start(f);
        gboolean ranged = major >= 6 && major <= 9;
        CHECK("G11 mounted at start on 6-9 only", ranged ? f->gate_last == 1 && f->gate_on >= 1 : !f->gate_on && !f->gate_off);
        installed(f, "my.home", -1); event(f, 'A', "{}");
        CHECK("G12 missing launcher unmounts", ranged ? f->gate_last == 0 : !f->gate_on && !f->gate_off);
        installed(f, "my.home", 1); event(f, 'A', "{}");
        CHECK("G12 launcher back mounts", ranged ? f->gate_last == 1 : !f->gate_on);
        choice(f, "other.home"); settings(f, "{\"home\":\"my.home\"}");
        CHECK("G13 choice change keeps it mounted", ranged ? f->gate_last == 1 : !f->gate_on);
        choice(f, STOCK); settings(f, "{}");
        CHECK("G13 LG Home chosen unmounts", ranged ? f->gate_last == 0 : !f->gate_off);
        teardown(f);
    }
    Fixture *f = setup(8, STOCK); start(f); CHECK("G13 LG Home at start", !f->gate_on && f->gate_last == 0); teardown(f);
    f = setup(7, "my.home"); unlink(path(f, f->c.state_dir, "homekey")); start(f);
    CHECK("G13 redirect disabled", !f->gate_on && f->gate_last == 0); teardown(f);
    f = setup(7, "my.home"); f->key_fail = 1; start(f);
    CHECK("G13 no baked filter, no gate", !f->gate_on); event(f, 'A', "{}"); CHECK("G13 filter retried, gate follows", f->gate_last == 1); teardown(f);
    for (int failing = 0; failing <= 1; failing++) {
        f = setup(8, "my.home"); start(f); f->gate_fail = failing;
        installed(f, HC_APP_ID, -1); watch_remove_tree(f->c.app_dir); event(f, 'A', "{}");
        CHECK("G14 uninstall unmounts before the state is removed", f->gate_off_with_choice >= 1);
        CHECK("G14 unmount failure keeps the restore pending", failing ? hc_watch_status(f->w) == 1 && g_file_test(f->w->choice_file, G_FILE_TEST_EXISTS) : hc_watch_status(f->w) == 0);
        teardown(f);
    }
    f = setup(8, "my.home"); start(f); int off = f->gate_off; hc_watch_stop(f->w, 0, "stopped by signal"); hc_watch_free(f->w); f->w = NULL;
    CHECK("G15 clean stop unmounts", f->gate_off == off + 1 && f->gate_last == 0);
    HCWatchOps ops = {.call = fake_call, .context = f}; f->w = hc_watch_new(&f->c, &ops); teardown(f);
    f = setup(8, "my.home"); start(f); off = f->gate_off; event(f, 's', NULL);
    CHECK("G15 settings loss fails", hc_watch_status(f->w) == 1);
    hc_watch_free(f->w); f->w = NULL;
    CHECK("G15 failed watcher keeps the gate for its restart", f->gate_off == off);
    f->w = hc_watch_new(&f->c, &ops); teardown(f);
    f = setup(9, NULL); start(f); hc_watch_free(f->w); f->w = NULL;
    CHECK("G15 no choice at start clears leftovers", f->gate_last == 0 && !f->gate_on);
    f->w = hc_watch_new(&f->c, &ops); teardown(f);
}
/* Pid 100 is the real binary, 200 the gate (its exe is the staged copy). */
static void proc_setup(Fixture *f) {
    char *copy = g_build_filename(f->c.gate_dir, HC_GATE_NAME, NULL), *real = g_build_filename(f->c.proc, "100", "exe", NULL), *gate = g_build_filename(f->c.proc, "200", "exe", NULL);
    file(copy, "gate"); file(real, "stock"); file(gate, "x"); unlink(gate);
    char *abs = realpath(copy, NULL); CHECK("G16 gate exe link", !symlink(abs, gate)); free(abs); g_free(copy); g_free(real); g_free(gate);
}
/* G16-G19: a stock Home that SAM started before the mount, or while SAM could not answer, is closed. */
static void test_gate_close(void) {
    Fixture *f = setup(7, "my.home"); proc_setup(f); f->home_pid = "100"; f->running_dead = 1; start(f);
    CHECK("G16 no reply at mount, a retry closes it", f->running_calls == 2 && f->closes == 1 && !f->w->gate_pending && !f->w->gate_source);
    event(f, 'A', "{}"); event(f, 'A', "{}");
    CHECK("G17 confirmed close stops the retries and later syncs", f->running_calls == 2 && f->closes == 1);
    teardown(f);
    f = setup(7, "my.home"); proc_setup(f); f->home_pid = "100"; f->close_dead = 1; start(f);
    CHECK("G17 a close without reply is retried", f->closes == 2 && !f->w->gate_pending); teardown(f);
    f = setup(7, "my.home"); proc_setup(f); f->home_pid = "200"; start(f);
    CHECK("G18 the gate's own process is not closed", !f->closes && f->w->gate_pending);
    CHECK("G18 retries are bounded", f->running_calls == 1 + (int)f->c.preload_tries && !f->w->gate_source);
    f->home_pid = NULL; event(f, 'A', "{}");
    CHECK("G18 a later sync confirms once the gate is gone", !f->closes && !f->w->gate_pending && f->running_calls == 2 + (int)f->c.preload_tries);
    teardown(f);
    f = setup(7, "my.home"); proc_setup(f); f->home_pid = "200"; hc_watch_start(f->w);
    CHECK("G19 retry pending", f->w->gate_pending && f->w->gate_source); int calls = f->running_calls;
    installed(f, "my.home", -1); event(f, 'A', "{}");
    CHECK("G19 unmount cancels the retry", !f->w->gate_pending && !f->w->gate_source && f->running_calls == calls);
    installed(f, "my.home", 1); event(f, 'A', "{}"); CHECK("G19 re-mount starts it afresh", f->w->gate_pending || f->running_calls > calls);
    hc_watch_stop(f->w, 0, "stopped by signal"); hc_watch_free(f->w); f->w = NULL; calls = f->running_calls; pump();
    CHECK("G19 clean stop leaves no retry", f->running_calls == calls);
    HCWatchOps ops = {.call = fake_call, .context = f}; f->w = hc_watch_new(&f->c, &ops); teardown(f);
}
/* G20: a close that really closed a running stock Home, and an empty foreground afterwards (6.5.2 boot). */
static void test_gate_relaunch(void) {
    Fixture *f = setup(7, "my.home"); proc_setup(f); f->home_pid = "100"; f->foreground = ""; start(f);
    CHECK("G20 closed Home, empty foreground: stock Home launched once", f->closes == 1 && f->launches == 1 && !f->w->gate_settle_source);
    event(f, 'A', "{}"); event(f, 'A', "{}");
    CHECK("G20 no second launch for the same close", f->launches == 1); teardown(f);
    f = setup(7, "my.home"); proc_setup(f); f->home_pid = "100"; f->foreground = "my.home"; start(f);
    CHECK("G20 something in front: no launch", f->closes == 1 && !f->launches); teardown(f);
    f = setup(7, "my.home"); proc_setup(f); f->home_pid = "100"; start(f);
    CHECK("G20 foreground unknown: no launch", f->closes == 1 && !f->launches); teardown(f);
    f = setup(7, "my.home"); proc_setup(f); f->foreground = ""; start(f);
    CHECK("G20 Home was not running: no launch", !f->closes && !f->launches); teardown(f);
    f = setup(7, "my.home"); proc_setup(f); f->home_pid = "100"; f->foreground = ""; f->w->c.gate_settle_ms = 100000; hc_watch_start(f->w);
    CHECK("G20 settle pending", f->w->gate_settle_source != 0 && !f->launches);
    installed(f, "my.home", -1); event(f, 'A', "{}");
    CHECK("G20 unmount cancels the launch", !f->w->gate_settle_source && !f->launches);
    teardown(f);
    f = setup(7, "my.home"); proc_setup(f); f->home_pid = "100"; f->foreground = ""; f->w->c.gate_settle_ms = 100000; hc_watch_start(f->w);
    hc_watch_stop(f->w, 0, "stopped by signal"); hc_watch_free(f->w); f->w = NULL; pump();
    CHECK("G20 clean stop leaves no launch", !f->launches);
    HCWatchOps ops = {.call = fake_call, .context = f}; f->w = hc_watch_new(&f->c, &ops); teardown(f);
}
/* The gate's `decided` file, mtime `offset_s` from now: a gate run after (+) or before (-) the close. */
static void gate_ran(Fixture *f, int offset_s) {
    char *p = g_build_filename(f->c.gate_dir, "decided", NULL); file(p, "1");
    time_t t = time(NULL) + offset_s; struct timeval tv[2] = {{t, 0}, {t, 0}};
    CHECK("G21 set mtime", !utimes(p, tv)); g_free(p);
}
/* G21: SAM's idle relaunch already ran the gate after the close; an empty foreground then proves nothing. */
static void test_gate_ran(void) {
    Fixture *f = setup(7, "my.home"); proc_setup(f); f->home_pid = "100"; f->foreground = ""; gate_ran(f, 3600); start(f);
    CHECK("G21 gate ran after the close: no launch", f->closes == 1 && !f->launches && !f->w->gate_settle_source); teardown(f);
    f = setup(7, "my.home"); proc_setup(f); f->home_pid = "100"; f->foreground = ""; gate_ran(f, -3600); start(f);
    CHECK("G21 gate ran before the close only: launch", f->closes == 1 && f->launches == 1); teardown(f);
    f = setup(7, "my.home"); proc_setup(f); f->home_pid = "100"; f->foreground = ""; start(f);
    CHECK("G21 gate did not run: launch", f->closes == 1 && f->launches == 1); teardown(f);
}
static gboolean logged(Fixture *f, const char *text) {
    char *log = watch_read(f->c.log); gboolean found = log && strstr(log, text); g_free(log); return found;
}
static gboolean input_marked(Fixture *f) {
    char *p = g_build_filename(f->c.runtime, "last-input", NULL); gboolean m = g_file_test(p, G_FILE_TEST_EXISTS); g_free(p); return m;
}
/* A launcher of `window` chosen, `front` in front (NULL: no answer), last input HDMI 2, no settle. */
static Fixture *input_setup(int major, const char *id, const char *window, const char *front) {
    Fixture *f = setup(major, id); f->window = window; f->foreground = front; f->last_input = "com.webos.app.hdmi2";
    f->w->c.input_settle_ms = 0; return f;
}
/* A new watcher in the same boot (unit restart): same fixture, same runtime dir. */
static void input_restart(Fixture *f) {
    HCWatchOps ops = f->w->ops; hc_watch_free(f->w); f->w = hc_watch_new(&f->c, &ops); f->w->c.input_settle_ms = 0; start(f); pump();
}
/* L01-L07: webOS 10+, overlay launcher: the last input once per boot (docs/settings-app.md). */
static void test_last_input(void) {
    for (int major = 10; major <= 12; major++) for (int alone = 0; alone <= 1; alone++) {
        Fixture *f = input_setup(major, "my.home", "overlay", alone ? "my.home" : ""); start(f);
        CHECK("L01 overlay, nothing full-screen in front: the last input once", f->launches == 1 && eq(f->launched->str, "com.webos.app.hdmi2 "));
        CHECK("L01 logged", logged(f, alone ? "last input: launched com.webos.app.hdmi2 over the overlay launcher alone: {\"returnValue\":true}"
            : "last input: launched com.webos.app.hdmi2 over an empty foreground: {\"returnValue\":true}"));
        CHECK("L01 marker", input_marked(f) && !f->w->input_source);
        event(f, 'F', "{\"appId\":\"com.webos.app.hdmi2\"}"); event(f, 'T', NULL); event(f, 'A', "{}");
        choice(f, "other.home"); settings(f, "{\"home\":\"my.home\"}"); f->foreground = ""; event(f, 'A', "{}");
        CHECK("L05 no repeat in the watcher, choice change included", f->launches == 1);
        input_restart(f); CHECK("L05 no repeat after a restart in the same boot", f->launches == 1 && f->last_input_calls == 1);
        teardown(f);
    }
    for (int major = 5; major <= 12; major++) for (int mode = 0; mode < 3; mode++) {
        if (major >= 10 && !mode) continue;
        Fixture *f = input_setup(major, mode == 2 ? STOCK : "my.home", mode == 1 ? NULL : "overlay", ""); start(f); pump();
        CHECK("L02 full-screen, LG Home, before 10: nothing", !f->launches && !f->last_input_calls);
        CHECK("L02 10+ takes the decision once, before 10 nothing", input_marked(f) == (major >= 10));
        teardown(f);
    }
    const char *fronts[] = {"com.webos.app.hdmi1", HC_APP_ID, STOCK, "netflix"};
    for (unsigned i = 0; i < G_N_ELEMENTS(fronts); i++) {
        Fixture *f = input_setup(11, "my.home", "overlay", fronts[i]); start(f);
        char *line = g_strdup_printf("last input: %s in front; nothing launched this boot", fronts[i]);
        CHECK("L03 full-screen app in front: nothing", !f->launches && !f->last_input_calls && input_marked(f) && logged(f, line));
        g_free(line); teardown(f);
    }
    Fixture *f = input_setup(11, "my.home", "overlay", ""); f->w->c.input_settle_ms = 100000; start(f);
    event(f, 'F', "{\"appId\":\"my.home\"}"); CHECK("L03 the overlay itself in front: still pending", f->w->input_source && !input_marked(f));
    event(f, 'F', "{\"appId\":\"netflix\"}");
    CHECK("L03 an app came up during the settle: nothing", !f->w->input_source && input_marked(f) && !f->launches && logged(f, "last input: netflix came to the front; nothing launched this boot"));
    teardown(f);
    const char *inputs[] = {NULL, "!refuse", "!none", "", STOCK, FULLHOME, "bad id"};
    const char *whys[] = {"eim did not answer", "eim refused", "no lastSourceAppId", "no lastSourceAppId", "lastSourceAppId is stock Home", "lastSourceAppId is stock Home", "invalid lastSourceAppId"};
    for (unsigned i = 0; i < G_N_ELEMENTS(inputs); i++) {
        f = input_setup(10, "my.home", "overlay", ""); f->last_input = inputs[i]; start(f);
        char *line = g_strdup_printf("last input: %s, using com.webos.app.livetv", whys[i]);
        CHECK("L04 fallback to Live TV, logged", f->launches == 1 && eq(f->launched->str, "com.webos.app.livetv ") && logged(f, line));
        g_free(line); teardown(f);
    }
    f = input_setup(11, "my.home", "overlay", ""); file(path(f, f->c.runtime, "last-input"), "com.webos.app.livetv"); start(f); pump();
    CHECK("L05 marker from this boot: nothing", !f->launches && !f->last_input_calls && !f->w->input_source); teardown(f);
    for (int mode = 1; mode <= 2; mode++) {
        f = input_setup(11, "my.home", "overlay", ""); f->launch_fail = mode; start(f);
        CHECK("L06 failed launch logged", f->launches == 1 && input_marked(f) && logged(f, mode == 1 ?
            "last input: launch of com.webos.app.hdmi2 failed: {\"returnValue\":false}; not retried this boot" :
            "last input: launch of com.webos.app.hdmi2 failed: no reply; not retried this boot"));
        event(f, 'T', NULL); event(f, 'A', "{}"); pump(); input_restart(f);
        CHECK("L06 never retried", f->launches == 1 && hc_watch_status(f->w) == -1); teardown(f);
    }
    f = input_setup(11, "my.home", "overlay", NULL); start(f); for (int i = 0; i < 4; i++) pump();
    CHECK("L06 SAM silent: bounded, nothing launched", !f->launches && input_marked(f) && f->w->input_attempts == f->c.preload_tries &&
        logged(f, "last input: getForegroundAppInfo did not answer in 12 tries; nothing launched this boot"));
    teardown(f);
    f = input_setup(11, "my.home", "overlay", ""); f->w->c.input_settle_ms = 100000; hc_watch_start(f->w);
    CHECK("L07 pending", f->w->input_source != 0);
    hc_watch_stop(f->w, 0, "stopped by signal"); hc_watch_free(f->w); f->w = NULL; pump();
    CHECK("L07 clean stop leaves no launch", !f->launches && !input_marked(f));
    HCWatchOps ops = {.call = fake_call, .context = f}; f->w = hc_watch_new(&f->c, &ops); teardown(f);
}
static void test_end_log(void) {
    Fixture *f=setup(8,"my.home"); start(f); event(f,'a',NULL); event(f,'f',NULL); event(f,'l',NULL);
    CHECK("W07 auxiliary streams degrade",hc_watch_status(f->w)==-1); event(f,'s',NULL); CHECK("W07 settings loss restart",hc_watch_status(f->w)==1);
    int before=f->preload_on; hc_watch_free(f->w); CHECK("W19 failure no preload undo",f->preload_on==before);
    HCWatchOps ops={.call=fake_call,.context=f}; f->w=hc_watch_new(&f->c,&ops); teardown(f);
    f=setup(8,"my.home"); GString *s=g_string_new(NULL); for(int i=0;i<400;i++)g_string_append_printf(s,"old line %d padding padding padding\n",i);
    file(f->c.log,s->str); g_string_free(s,TRUE); f->w->c.log_max=2000; watch_log(f->w,"trim now");
    char *log=watch_read(f->c.log); CHECK("W20 last 200 lines",strstr(log,"old line 399") && !strstr(log,"old line 0 ")); g_free(log); teardown(f);
}
int main(int argc,char **argv) {
    if(argc!=2)return 2;
    base=argv[1]; setbuf(stdout,NULL);
    test_choices(); test_record(); test_backoff(); test_profiles(); test_idle_app();test_remove_return(); test_baked(); test_uninstall(); test_leftover(); test_restore_races(); test_notifications(); test_preload_close(); test_gate(); test_gate_close(); test_gate_relaunch(); test_gate_ran(); test_last_input(); test_end_log();
    printf("%u passed, %u failed (native watcher)\n",checks-failures,failures); return failures?1:0;
}
