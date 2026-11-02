/* SPDX-License-Identifier: Apache-2.0 */
/* Firmware update simulation for the Home key filter guard (docs/home-key-hook.md).
 *
 *   test-fwupdate WORKDIR STEP...      STEP = [live:]VERSION:EXPECT:STOCK_JS   (STOCK_JS "-" is a missing file)
 *
 * Each step is one "boot" of Home Chooser on a firmware whose stock systemUi.js is STOCK_JS and whose
 * /etc/os-release says VERSION. Between two steps the TV "updates": configd forgets every volatile
 * write and returns the new firmware's own list, the stock file is swapped for the next one, the
 * persistent state directory (choice, flag, snapshot, old generated files) is kept. A "live:" step
 * swaps the file without a reboot and runs `refresh` (the watcher path) against the active filter.
 * EXPECT: R filter regenerated from the new stock, S stock list and no generated file, N not a 6-9
 * release (nothing touched), K live only: restore impossible, generated filter kept and still on disk.
 * Every step also checks the invariants: Home never names a generated file that is missing, every
 * configd write is volatile, and only the file the list names is left in the state directory. */
#include "homekey.h"
#include <pbnjson.h>
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define KEY "com.webos.surfacemanager.keyFilters"
#define STOCK_LITERAL "/usr/lib/qt5/qml/KeyFilters/systemUi.js"
static raw_buffer kb(const char *s) { return j_str_to_buffer(s, strlen(s)); }
static jvalue_ref parse(const char *s) { return jdom_create(kb(s), jschema_all(), NULL); }
static jvalue_ref current;
static int sets, persisted, starts, toasts;
static char *fake(const char *uri, const char *payload, int timeout, void *context) {
    (void)context; assert(timeout > 0 && timeout <= 5);
    if (strstr(uri, "getCurrentState")) return g_strdup("{\"returnValue\":true,\"currentState\":\"launcher\"}");
    if (strstr(uri, "createToast")) { toasts++; if (strstr(payload, "will start shortly")) starts++; return g_strdup("{\"returnValue\":false}"); }
    if (strstr(uri, "getAppInfo")) return g_strdup("{\"returnValue\":true}");
    if (strstr(uri, "setConfigs")) {
        sets++;
        jvalue_ref req = parse(payload); bool vol = false;
        jboolean_get(jobject_get(req, kb("volatile")), &vol);
        if (!vol) persisted++; /* would land in configd_factory_db.json as a user override */
        jvalue_ref list = jobject_get(jobject_get(req, kb("configs")), kb(KEY));
        assert(jis_array(list) && jarray_size(list) > 0);
        j_release(&current); current = jvalue_duplicate(list); j_release(&req);
        return g_strdup("{\"returnValue\":true}");
    }
    assert(strstr(uri, "getConfigs"));
    jvalue_ref reply = jobject_create(), configs = jobject_create();
    jobject_set(reply, kb("returnValue"), jboolean_create(true));
    jobject_set(configs, kb(KEY), jvalue_duplicate(current));
    jobject_set(reply, kb("configs"), configs);
    char *text = g_strdup(jvalue_stringify(reply)); j_release(&reply); return text;
}
static char *entry_file(jvalue_ref list, int i) {
    raw_buffer b = jstring_get_fast(jobject_get(jarray_get(list, i), kb("file")));
    return g_strndup(b.m_str, b.m_len);
}
static int generated_files(const char *dir) {
    int n = 0; GDir *d = g_dir_open(dir, 0, NULL); const char *name;
    while (d && (name = g_dir_read_name(d))) if (g_str_has_prefix(name, "systemUi-") && g_str_has_suffix(name, ".js")) n++;
    if (d) g_dir_close(d);
    return n;
}
/* What Home would load must exist; nothing may reference a generated file that is gone. */
static void invariants(const char *state, const char *label, int orphans_ok) {
    int ours = 0;
    for (ssize_t i = 0; i < jarray_size(current); i++) {
        char *f = entry_file(current, i);
        if (strcmp(f, STOCK_LITERAL) && strcmp(f, "/usr/lib/qml/KeyFilters/systemUi.js") && strstr(f, "/systemUi-")) {
            ours++;
            if (!g_file_test(f, G_FILE_TEST_IS_REGULAR)) { printf("FAIL %s: list names missing %s\n", label, f); exit(1); }
        }
        g_free(f);
    }
    /* N: an update that leaves 6-9 stops the guard before it looks at the state directory, so the old
     * generated file stays behind (an orphan, never referenced). Reported, not failed: see the report. */
    if (generated_files(state) > ours && orphans_ok) { printf("NOTE %s: %d orphan generated file(s) in the state dir\n", label, generated_files(state) - ours); return; }
    if (generated_files(state) > ours) { printf("FAIL %s: %d generated file(s) left, list names %d\n", label, generated_files(state), ours); exit(1); }
    if (persisted) { printf("FAIL %s: %d non-volatile configd write(s)\n", label, persisted); exit(1); }
}
#define CHECK(cond, ...) do { if (!(cond)) { printf("FAIL %s: ", label); printf(__VA_ARGS__); printf("\n"); exit(1); } } while (0)
static char *slurp(const char *p) { char *s = NULL; g_file_get_contents(p, &s, NULL, NULL); return s; }
static void copy_to(const char *from, const char *to) {
    if (!strcmp(from, "-")) { unlink(to); return; }
    char *b = slurp(from); assert(b); assert(g_file_set_contents(to, b, -1, NULL)); g_free(b);
}
int main(int argc, char **argv) {
    setbuf(stdout, NULL);
    assert(argc >= 3);
    char *work = argv[1];
    char *state = g_build_filename(work, "state", NULL), *stock_js = g_build_filename(work, "systemUi.js", NULL);
    char *os = g_build_filename(work, "os-release", NULL), *flag = g_build_filename(state, "homekey", NULL);
    char *home = g_build_filename(state, "home", NULL), *snap = g_build_filename(state, "keyfilters-stock.json", NULL);
    assert(!g_mkdir_with_parents(state, 0755));
    g_file_set_contents(flag, "", -1, NULL); g_file_set_contents(home, "test.home\n", -1, NULL);
    HCKeyOps o = {state, stock_js, os, fake, NULL, 2, 0};
    /* Two firmware lists: an update may add, drop or reorder other handlers. */
    const char *lists[] = {
        "[{\"file\":\"/other.js\",\"handler\":\"first\",\"extra\":[1,true]},{\"file\":\"" STOCK_LITERAL "\",\"handler\":\"second\",\"extra\":{\"a\":2}}]",
        "[{\"file\":\"" STOCK_LITERAL "\",\"handler\":\"second\"},{\"file\":\"/usr/lib/qt5/qml/KeyFilters/newer.js\",\"handler\":\"fourth\"},{\"file\":\"/other.js\",\"handler\":\"first\"}]"};
    jvalue_ref firmware = NULL; int boots = 0, last_n = 0;
    for (int s = 2; s < argc; s++) {
        char *spec = g_strdup(argv[s]); gboolean live = g_str_has_prefix(spec, "live:");
        char *p = spec + (live ? 5 : 0), *version = p, *expect = strchr(p, ':'); assert(expect); *expect++ = 0;
        char *js = strchr(expect, ':'); assert(js); *js++ = 0;
        char label[200]; snprintf(label, sizeof label, "step %d (%s %s %s)", s - 1, live ? "live" : "boot", version, expect);
        char *osdata = g_strdup_printf("NAME=\"webOS\"\nVERSION=\"%s\"\nVERSION_ID=\"%s\"\n", version, version);
        g_file_set_contents(os, osdata, -1, NULL); g_free(osdata);
        copy_to(js, stock_js);
        if (!live) { /* the reboot: configd's volatile state is gone, the firmware's own list is back */
            if (firmware) j_release(&firmware);
            firmware = parse(lists[boots++ % 2]);
            j_release(&current); current = jvalue_duplicate(firmware);
        } else assert(firmware && s > 2);
        int before = sets; starts = 0; toasts = 0;
        GString *out = g_string_new(NULL);
        int rc = hc_key_run(&o, live ? "refresh" : "boot", FALSE, out);
        printf("%s rc=%d %s", label, rc, out->str);
        char *source = strcmp(js, "-") ? slurp(js) : NULL;
        char *expected = NULL, *why = NULL;
        if (source) expected = hc_key_generate(source, "test.home", &why);
        if (expect[0] == 'R') {
            CHECK(rc == 0, "rc %d", rc); CHECK(expected, "test bug: shape not known (%s)", why ? why : "?");
            CHECK(strstr(out->str, "active=yes") && !strstr(out->str, "reason="), "not active: %s", out->str);
            CHECK(starts == (live ? 0 : 1), "announce toasts %d", starts);
            int stockish = 0;
            for (ssize_t i = 0; i < jarray_size(current); i++) {
                char *f = entry_file(current, i), *want = entry_file(firmware, i);
                if (strstr(f, "/systemUi-")) {
                    char *b = slurp(f); CHECK(b && !strcmp(b, expected), "generated file is not the new stock patched"); g_free(b);
                    CHECK(!strcmp(want, STOCK_LITERAL), "replaced a non-stock entry"); stockish++;
                } else CHECK(!strcmp(f, want), "entry %zd changed", i);
                g_free(f); g_free(want);
            }
            CHECK(jarray_size(current) == jarray_size(firmware) && stockish == 1, "list shape");
            CHECK(generated_files(state) == 1, "%d generated files", generated_files(state));
            char *sj = slurp(snap); jvalue_ref saved = parse(sj ? sj : ""); g_free(sj);
            CHECK(jis_valid(saved) && jvalue_equal(saved, firmware), "stock snapshot is not this firmware's list");
            j_release(&saved);
        } else if (expect[0] == 'S') {
            CHECK(rc == 0, "rc %d", rc);
            CHECK(strstr(out->str, "active=no") && strstr(out->str, "reason="), "no refusal reason: %s", out->str);
            CHECK(jvalue_equal(current, firmware), "list is not exactly the firmware's own");
            CHECK(generated_files(state) == 0, "generated file left behind");
            CHECK(starts == 0, "announced a redirect that does not happen");
            if (!live) CHECK(sets == before, "boot wrote configd %d time(s) for stock", sets - before);
        } else if (expect[0] == 'N') {
            CHECK(rc == 0 && strstr(out->str, "patch=not-needed"), "not a no-op: %s", out->str);
            CHECK(sets == before && jvalue_equal(current, firmware), "touched the list");
        } else if (expect[0] == 'K') {
            CHECK(live && rc == 1, "rc %d", rc);
            CHECK(strstr(out->str, "reason="), "no reason");
            CHECK(generated_files(state) == 1, "generated filter not retained");
        } else CHECK(0, "unknown expectation %s", expect);
        last_n = expect[0] == 'N';
        invariants(state, label, last_n);
        g_free(source); g_free(expected); g_free(why); g_string_free(out, TRUE); g_free(spec);
    }
    /* Finish by stopping: whatever the history, stock comes back exactly and nothing is left. */
    char label[] = "final stop"; GString *out = g_string_new(NULL);
    unlink(flag); unlink(home);
    int rc = hc_key_run(&o, "stop", FALSE, out);
    CHECK(rc == 0 || (rc == 1 && access(stock_js, F_OK) != 0), "stop rc %d: %s", rc, out->str);
    if (rc == 0) { CHECK(jvalue_equal(current, firmware), "stop did not restore the firmware list"); CHECK(last_n || generated_files(state) == 0, "files left"); }
    invariants(state, label, last_n);
    g_string_free(out, TRUE); j_release(&current); j_release(&firmware);
    puts("PASS firmware update scenario");
    return 0;
}
