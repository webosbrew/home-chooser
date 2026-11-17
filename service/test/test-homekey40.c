/* SPDX-License-Identifier: Apache-2.0 */
/* webOS 4.0 Home key filter: gating, the insert before handleSystemKeys, restore, refusals.
 * argv: state dir, then optional real systemUi.js files of a 4.0, a 4.5 and a 6-9 firmware (skipped when missing). */
#include "homekey.h"
#include <pbnjson.h>
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define KEY "com.webos.surfacemanager.keyFilters"
#define SYS "/usr/lib/qt5/qml/KeyFilters/systemUi.js"
#define APP "/usr/lib/qt5/qml/KeyFilters/appLaunch.js"
static raw_buffer kb(const char *s) { return j_str_to_buffer(s, strlen(s)); }
static jvalue_ref parse(const char *s) { return jdom_create(kb(s), jschema_all(), NULL); }
static jvalue_ref current;
static int sets, fails, corrupt_after_set, corrupt_reads, overlay_app, app_installed = 1;
static char *fake(const char *uri, const char *payload, int timeout, void *context) {
    (void)context; assert(timeout > 0 && timeout <= 5);
    if (strstr(uri, "getCurrentState")) return g_strdup("{\"returnValue\":true,\"currentState\":\"launcher\"}");
    if (strstr(uri, "createToast")) return g_strdup("{\"returnValue\":false}");
    if (strstr(uri, "getAppInfo"))
        return g_strdup(!app_installed ? "{\"returnValue\":false}" :
            overlay_app ? "{\"returnValue\":true,\"appInfo\":{\"defaultWindowType\":\"overlay\"}}" : "{\"returnValue\":true,\"appInfo\":{\"id\":\"x\"}}");
    if (strstr(uri, "setConfigs")) {
        sets++;
        if (corrupt_after_set) { corrupt_reads = 1; corrupt_after_set = 0; }
        jvalue_ref req = parse(payload); bool vol = false;
        jboolean_get(jobject_get(req, kb("volatile")), &vol); assert(vol);
        jvalue_ref list = jobject_get(jobject_get(req, kb("configs")), kb(KEY));
        assert(jis_array(list) && jarray_size(list) > 0);
        if (fails) { if (fails > 0) fails--; j_release(&req); return g_strdup("{\"returnValue\":false}"); }
        j_release(&current); current = jvalue_duplicate(list); j_release(&req);
        return g_strdup("{\"returnValue\":true}");
    }
    assert(strstr(uri, "getConfigs"));
    jvalue_ref reply = jobject_create(), configs = jobject_create();
    jobject_set(reply, kb("returnValue"), jboolean_create(true));
    if (sets && corrupt_reads) { corrupt_reads--; jobject_set(configs, kb(KEY), jarray_create(NULL)); }
    else jobject_set(configs, kb(KEY), jvalue_duplicate(current));
    jobject_set(reply, kb("configs"), configs);
    char *text = g_strdup(jvalue_stringify(reply)); j_release(&reply); return text;
}
static int run(HCKeyOps *o, const char *action, gboolean stock, GString *out) {
    int rc = hc_key_run(o, action, stock, out);
    printf("%s rc=%d %s", action, rc, out->str); return rc;
}
static int go(HCKeyOps *o, const char *action) {
    GString *out = g_string_new(NULL); int rc = run(o, action, FALSE, out); g_string_free(out, TRUE); return rc;
}
static void status(HCKeyOps *o, const char *expected) {
    GString *out = g_string_new(NULL);
    assert(hc_key_run(o, "status", FALSE, out) == 0);
    printf("status %s", out->str); assert(strstr(out->str, expected)); g_string_free(out, TRUE);
}
static char *field(jvalue_ref item, const char *name) {
    raw_buffer b = jstring_get_fast(jobject_get(item, kb(name))); return g_strndup(b.m_str, b.m_len);
}
static int ours(jvalue_ref list) {
    int n = 0;
    for (ssize_t i = 0; i < jarray_size(list); i++) {
        char *h = field(jarray_get(list, i), "handler"); if (!strcmp(h, "handleHomeChooserKeys")) n++; g_free(h);
    }
    return n;
}
static int files40(const char *dir) {
    GDir *d = g_dir_open(dir, 0, NULL); const char *name; int n = 0;
    while (d && (name = g_dir_read_name(d))) if (g_str_has_prefix(name, "homekey40-")) n++;
    if (d) g_dir_close(d);
    return n;
}
static const char *stock12 =
    "[{\"handler\":\"handleScreenSaver\",\"file\":\"" SYS "\"},{\"handler\":\"handleAccessibility\",\"file\":\"" APP "\"},"
    "{\"handler\":\"handleAvReset\",\"file\":\"" APP "\"},{\"handler\":\"handleMagnifier\",\"file\":\"" SYS "\"},"
    "{\"handler\":\"handleLowSpeedDataBroadcast\",\"file\":\"" APP "\"},{\"handler\":\"handleSystemKeys\",\"file\":\"" SYS "\"},"
    "{\"handler\":\"handleHotkeys\",\"file\":\"" APP "\"},{\"handler\":\"handleCPHotkeys\",\"file\":\"" APP "\"},"
    "{\"handler\":\"handleFactoryKeys\",\"file\":\"" APP "\"},{\"handler\":\"handleTvhotkeyAppLaunch\",\"file\":\"" APP "\"},"
    "{\"handler\":\"handleLocalKeys\",\"file\":\"" APP "\"},{\"handler\":\"handleMagicNumber\",\"file\":\"" SYS "\"}]";
static const char *stub40 = "function handleSystemKeys(key, pressed, autoRepeat) {\n global.prepareToGoHome(); longPressTimer.set(key, 1000, global.gotoRecents);\n}\n";
static const char *stub45 = "function launchHomeApp(key) {}\nfunction handleSystemKeys(key, pressed, autoRepeat) { global.prepareToGoHome(); global.gotoRecents; launchHomeApp(key); }\n";

int main(int argc, char **argv) {
    setbuf(stdout, NULL);
    assert(argc >= 2);
    const char *dir = argv[1];
    assert(!g_mkdir_with_parents(dir, 0755));
    char *rel = g_build_filename(dir, "starfish-release", NULL), *js = g_build_filename(dir, "stock-systemUi.js", NULL);
    char *flag = g_build_filename(dir, "homekey", NULL), *home = g_build_filename(dir, "home", NULL);
    const char *r40 = "Rockhopper release 4.4.3-22 (goldilocks-gorongosa)\n", *r45 = "Rockhopper release 4.10.2-29 (goldilocks2-grampians)\n";
    HCKeyOps o = {dir, js, rel, fake, NULL, 3, 0};

    /* ---- gating: the mode follows the release major and the stock file, never a version number alone ---- */
    assert(hc_key_mode(r40, stub40) == HC_KEY_INSERT);
    assert(hc_key_mode(r45, stub45) == HC_KEY_NONE);       /* 4.5: launchHomeApp, the 5.x mechanism */
    assert(hc_key_mode(r45, stub40) == HC_KEY_INSERT);     /* the file decides, 4.0-shaped is 4.0 */
    assert(hc_key_mode(r40, stub45) == HC_KEY_NONE);       /* a 4.x release with launchHomeApp is never 4.0 */
    assert(hc_key_mode(r40, NULL) == HC_KEY_NONE);
    assert(hc_key_mode(r40, "var x = 1;\n") == HC_KEY_NONE);
    assert(hc_key_mode("Rockhopper release 3.9.3-1 (x)\n", stub40) == HC_KEY_NONE);
    assert(hc_key_mode("Rockhopper release 5.6.1-6 (x)\n", stub40) == HC_KEY_NONE);
    assert(hc_key_mode("Rockhopper release 6.6.0-6 (x)\n", stub40) == HC_KEY_PATCH);
    assert(hc_key_mode("Rockhopper release 9.2.4-1 (x)\n", NULL) == HC_KEY_PATCH);
    assert(hc_key_mode("Rockhopper release 11.2.0-51 (x)\n", stub40) == HC_KEY_NONE);
    assert(hc_key_mode(NULL, stub40) == HC_KEY_NONE);
    for (int i = 2; i < argc; i++) {
        char *real = NULL;
        if (!g_file_get_contents(argv[i], &real, NULL, NULL)) { printf("SKIP real systemUi.js %s\n", argv[i]); continue; }
        HCKeyMode want = i == 2 ? HC_KEY_INSERT : HC_KEY_NONE;
        const char *release = i == 2 ? r40 : i == 3 ? r45 : "Rockhopper release 4.10.2-1 (x)\n";
        printf("real %s: mode %d\n", argv[i], hc_key_mode(release, real));
        assert(hc_key_mode(release, real) == want); g_free(real);
    }

    /* ---- the transaction on a 4.0 ---- */
    g_file_set_contents(rel, r40, -1, NULL); g_file_set_contents(js, stub40, -1, NULL);
    g_file_set_contents(flag, "", -1, NULL); g_file_set_contents(home, "test.home\n", -1, NULL);
    current = parse(stock12);
    jvalue_ref original = jvalue_duplicate(current);
    GString *out = g_string_new(NULL);
    status(&o, "active=no\npatch=possible\n");
    assert(go(&o, "prepare") == 0);
    assert(go(&o, "apply") == 0); assert(sets == 1);
    assert(jarray_size(current) == 13 && ours(current) == 1);
    {   /* inserted at the stock handleSystemKeys' place, so the entries before it still run first */
        char *h = field(jarray_get(current, 5), "handler"), *n = field(jarray_get(current, 6), "handler"), *file = field(jarray_get(current, 5), "file");
        assert(!strcmp(h, "handleHomeChooserKeys") && !strcmp(n, "handleSystemKeys"));
        char *body = NULL; assert(g_file_get_contents(file, &body, NULL, NULL));
        assert(strstr(body, "var HC_TARGET = \"test.home\";") && strstr(body, "var HC_OVERLAY = false;"));
        assert(strstr(body, "function handleHomeChooserKeys(") && !strstr(body, "@ID@") && !strstr(body, "@OVERLAY@"));
        char *copy = g_build_filename(dir, "filter-fullscreen.js", NULL); g_file_set_contents(copy, body, -1, NULL); g_free(copy);
        g_free(h); g_free(n); g_free(file); g_free(body);
        jvalue_ref plain = jarray_create(NULL);
        for (ssize_t i = 0; i < jarray_size(current); i++) if (i != 5) jarray_append(plain, jvalue_duplicate(jarray_get(current, i)));
        assert(jvalue_equal(plain, original)); j_release(&plain);
    }
    status(&o, "active=yes\npatch=possible\n");
    assert(go(&o, "refresh") == 0); assert(sets == 1 && files40(dir) == 1);        /* identical bytes: no loader churn */
    assert(go(&o, "boot") == 0); assert(sets == 1);
    /* an overlay launcher: the Back rule changes, so the file does, and the old one is collected */
    overlay_app = 1; assert(go(&o, "refresh") == 0); assert(sets == 2 && files40(dir) == 1 && ours(current) == 1);
    {
        char *file = field(jarray_get(current, 5), "file"), *body = NULL; assert(g_file_get_contents(file, &body, NULL, NULL));
        assert(strstr(body, "var HC_OVERLAY = true;"));
        char *copy = g_build_filename(dir, "filter-overlay.js", NULL); g_file_set_contents(copy, body, -1, NULL); g_free(copy);
        g_free(file); g_free(body);
    }
    overlay_app = 0;
    g_file_set_contents(home, "other.home", -1, NULL); assert(go(&o, "refresh") == 0); assert(sets == 3 && files40(dir) == 1);
    /* an external edit of the live list is not overwritten */
    {
        jvalue_ref saved = jvalue_duplicate(current); int before = sets;
        jobject_set(jarray_get(current, 0), kb("external"), jboolean_create(true));
        assert(go(&o, "refresh") == 1); assert(sets == before);
        j_release(&current); current = saved;
    }
    /* a failed write restores the exact stock list; a corrupt read-back does too */
    fails = 1; g_file_set_contents(home, "third.home", -1, NULL); assert(go(&o, "refresh") == 1); assert(jvalue_equal(current, original));
    assert(go(&o, "apply") == 0);
    corrupt_after_set = 1; g_file_set_contents(home, "fourth.home", -1, NULL); assert(go(&o, "refresh") == 1); assert(jvalue_equal(current, original));
    assert(go(&o, "apply") == 0);
    /* a launcher that is gone: stock list back */
    app_installed = 0; assert(go(&o, "refresh") == 0); assert(jvalue_equal(current, original)); app_installed = 1;
    assert(go(&o, "apply") == 0); assert(ours(current) == 1);
    /* a restore that cannot be verified keeps our file; the verified one removes it */
    fails = -1; assert(go(&o, "stop") == 1); assert(files40(dir) >= 1 && ours(current) == 1);
    fails = 0; assert(go(&o, "stop") == 0); assert(jvalue_equal(current, original)); assert(files40(dir) == 0);
    status(&o, "active=no\n");
    unlink(flag); assert(go(&o, "apply") == 0); assert(jvalue_equal(current, original) && files40(dir) == 0);
    g_file_set_contents(flag, "", -1, NULL);

    /* ---- other layers ---- */
    {   /* a layer without handleMagicNumber (easytv.js style): the insert is still before handleSystemKeys */
        j_release(&current); current = parse(stock12);
        jarray_remove(current, 11);
        jvalue_ref layer = jvalue_duplicate(current); g_file_set_contents(home, "test.home", -1, NULL);
        assert(go(&o, "apply") == 0); assert(jarray_size(current) == 12 && ours(current) == 1);
        char *h = field(jarray_get(current, 5), "handler"); assert(!strcmp(h, "handleHomeChooserKeys")); g_free(h);
        assert(go(&o, "stop") == 0); assert(jvalue_equal(current, layer)); j_release(&layer);
    }
    {   /* handleSystemKeys listed first: the insert is index 0 */
        j_release(&current);
        current = parse("[{\"handler\":\"handleSystemKeys\",\"file\":\"" SYS "\"},{\"handler\":\"handleHotkeys\",\"file\":\"" APP "\"}]");
        jvalue_ref layer = jvalue_duplicate(current);
        assert(go(&o, "apply") == 0); assert(jarray_size(current) == 3);
        char *h = field(jarray_get(current, 0), "handler"); assert(!strcmp(h, "handleHomeChooserKeys")); g_free(h);
        assert(go(&o, "stop") == 0); assert(jvalue_equal(current, layer)); j_release(&layer);
    }
    {   /* no stock handleSystemKeys: nothing is written, stock retained, status says refused */
        j_release(&current);
        current = parse("[{\"handler\":\"handleScreenSaver\",\"file\":\"" SYS "\"},{\"handler\":\"handleSystemKeys\",\"file\":\"/other/systemUi.js\"}]");
        jvalue_ref layer = jvalue_duplicate(current); int before = sets;
        status(&o, "patch=refused\n");
        assert(go(&o, "apply") == 0); assert(sets == before && jvalue_equal(current, layer));
        status(&o, "active=no\n"); j_release(&layer);
    }
    {   /* the legacy {path: handler} form (the commercial layer) is refused without a write */
        j_release(&current); current = parse("[{\"" SYS "\":\"handleSystemKeys\"},{\"" APP "\":\"handleHotkeys\"}]");
        jvalue_ref layer = jvalue_duplicate(current); int before = sets;
        assert(go(&o, "apply") == 1); assert(sets == before && jvalue_equal(current, layer));
        assert(go(&o, "refresh") == 1); assert(sets == before);
        j_release(&layer);
    }
    j_release(&current); current = jarray_create(NULL); { int before = sets; assert(go(&o, "apply") == 1); assert(sets == before); }

    /* ---- 4.5 is not this path: nothing is read, written or generated ---- */
    j_release(&current); current = parse(stock12);
    g_file_set_contents(rel, r45, -1, NULL); g_file_set_contents(js, stub45, -1, NULL);
    { int before = sets; status(&o, "active=no\npatch=not-needed\n"); assert(go(&o, "apply") == 0); assert(go(&o, "boot") == 0); assert(sets == before && jvalue_equal(current, original)); }
    /* 4.0 to 4.5 update: the leftover generated file goes, never a stock one */
    {
        char *left = g_build_filename(dir, "homekey40-LEFT01.js", NULL); g_file_set_contents(left, "x", -1, NULL);
        assert(go(&o, "refresh") == 0); assert(!g_file_test(left, G_FILE_TEST_EXISTS)); g_free(left);
    }
    g_string_free(out, TRUE); j_release(&current); j_release(&original);
    g_free(rel); g_free(js); g_free(flag); g_free(home);
    puts("PASS webOS 4.0 filter: gating, insert before handleSystemKeys, overlay rule, restore, layer and legacy refusals, 4.5 left alone");
    return 0;
}
