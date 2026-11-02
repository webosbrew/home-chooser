/* SPDX-License-Identifier: Apache-2.0 */
#define _GNU_SOURCE
#include "homekey.h"
#include <pbnjson.h>
#include <sys/file.h>
#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <stdlib.h>
#include <sys/stat.h>

#define KEY "com.webos.surfacemanager.keyFilters"
#define GET "luna://com.webos.service.config/getConfigs"
#define SET "luna://com.webos.service.config/setConfigs"
#define GET_PAYLOAD "{\"configNames\":[\"" KEY "\"]}"
static raw_buffer kb(const char *s) { return j_str_to_buffer(s, strlen(s)); }
static jvalue_ref parse(const char *s) {
    return jdom_create(kb(s ? s : ""), jschema_all(), NULL);
}
static char *str(jvalue_ref o, const char *key) {
    jvalue_ref v = jobject_get(o, kb(key));
    if (!jis_string(v)) return NULL;
    raw_buffer b = jstring_get_fast(v); return g_strndup(b.m_str, b.m_len);
}
static gboolean success(jvalue_ref o) {
    bool v = false; jboolean_get(jobject_get(o, kb("returnValue")), &v); return v;
}
static jvalue_ref call(const HCKeyOps *o, const char *uri, const char *payload, int timeout) {
    char *s = o->call(uri, payload, timeout, o->context);
    jvalue_ref reply = parse(s); g_free(s); return reply;
}
static char *path(const HCKeyOps *o, const char *name) { return g_build_filename(o->state_dir, name, NULL); }
static char *readfile(const char *p) {
    char *s = NULL; g_file_get_contents(p, &s, NULL, NULL); return s;
}
static gboolean stock_path(const char *s) {
    return s && (!strcmp(s, "/usr/lib/qt5/qml/KeyFilters/systemUi.js") || !strcmp(s, "/usr/lib/qml/KeyFilters/systemUi.js"));
}
static gboolean owned(const HCKeyOps *o, const char *s) {
    char *prefix = path(o, "systemUi-");
    gboolean yes = s && g_str_has_prefix(s, prefix) && g_str_has_suffix(s, ".js") && !strchr(s + strlen(prefix), '/');
    g_free(prefix); return yes;
}
/* Reject malformed/empty lists. Every entry and all metadata/order are retained. */
static gboolean valid(const HCKeyOps *o, jvalue_ref list, gboolean allow_ours) {
    if (!jis_array(list) || jarray_size(list) == 0) return FALSE;
    unsigned systems = 0;
    for (ssize_t i = 0; i < jarray_size(list); i++) {
        jvalue_ref item = jarray_get(list, i);
        if (!jis_object(item)) return FALSE;
        char *file = str(item, "file"), *handler = str(item, "handler");
        gboolean ok = file && *file && handler && *handler;
        if (stock_path(file) || (allow_ours && owned(o, file))) systems++;
        else if (owned(o, file)) ok = FALSE;
        g_free(file); g_free(handler);
        if (!ok) return FALSE;
    }
    return systems > 0;
}
static gboolean has_ours(const HCKeyOps *o, jvalue_ref list) {
    for (ssize_t i = 0; i < jarray_size(list); i++) {
        char *file = str(jarray_get(list, i), "file");
        gboolean yes = owned(o, file); g_free(file); if (yes) return TRUE;
    }
    return FALSE;
}
static jvalue_ref get_list(const HCKeyOps *o) {
    jvalue_ref r = call(o, GET, GET_PAYLOAD, 3);
    jvalue_ref list = jobject_get(jobject_get(r, kb("configs")), kb(KEY));
    jvalue_ref result = success(r) && valid(o, list, TRUE) ? jvalue_duplicate(list) : jinvalid();
    j_release(&r); return result;
}
static void toast(const HCKeyOps *o, const char *message) {
    jvalue_ref p = jobject_create();
    jobject_set(p, kb("sourceId"), jstring_create("org.webosbrew.homechooser"));
    jobject_set(p, kb("noaction"), jboolean_create(true));
    jobject_set(p, kb("message"), jstring_create(message));
    jvalue_ref r = call(o, "luna://com.webos.notification/createToast", jvalue_stringify(p), 2);
    j_release(&r); j_release(&p);
}
static gboolean select_list(const HCKeyOps *o, jvalue_ref list) {
    if (!valid(o, list, TRUE)) return FALSE;
    for (ssize_t i = 0; i < jarray_size(list); i++) {
        char *file = str(jarray_get(list, i), "file");
        gboolean available = !owned(o, file) || g_file_test(file, G_FILE_TEST_IS_REGULAR);
        if (stock_path(file)) available = g_file_test(o->stock_js, G_FILE_TEST_IS_REGULAR);
        g_free(file);
        if (!available) return FALSE;
    }
    jvalue_ref p = jobject_create(), configs = jobject_create();
    jobject_set(configs, kb(KEY), jvalue_duplicate(list));
    jobject_set(p, kb("configs"), configs);
    jobject_set(p, kb("volatile"), jboolean_create(true));
    jvalue_ref r = call(o, SET, jvalue_stringify(p), 5);
    gboolean ok = success(r);
    j_release(&r); j_release(&p);
    jvalue_ref got = get_list(o);
    ok = ok && jis_valid(got) && jvalue_equal(got, list);
    j_release(&got); return ok;
}
/* Delete only our unreferenced generated versions, after exact config readback. */
static void collect(const HCKeyOps *o, jvalue_ref current) {
    GDir *d = g_dir_open(o->state_dir, 0, NULL);
    if (!d) return;
    const char *name;
    while ((name = g_dir_read_name(d))) {
        char *p = path(o, name);
        if (owned(o, p)) {
            gboolean used = FALSE;
            for (ssize_t i = 0; i < jarray_size(current); i++) {
                char *file = str(jarray_get(current, i), "file");
                if (file && !strcmp(file, p)) used = TRUE;
                g_free(file);
            }
            if (!used) unlink(p);
        }
        g_free(p);
    }
    g_dir_close(d);
}
static gboolean ready(const HCKeyOps *o) {
    for (unsigned n = 0; n < o->readiness_attempts; n++) {
        jvalue_ref r = call(o, "luna://com.webos.surfacemanager/getCurrentState", "{}", 2);
        char *state = str(r, "currentState");
        gboolean ok = success(r) && state;
        g_free(state); j_release(&r);
        jvalue_ref list = get_list(o);
        ok = ok && jis_valid(list); j_release(&list);
        if (ok) return TRUE;
        if (n + 1 < o->readiness_attempts) g_usleep(o->retry_ms * 1000);
    }
    return FALSE;
}
int hc_key_run(const HCKeyOps *o, const char *action, gboolean force_stock, GString *out) {
    int rc = 1, lock = -1;
    char *source = NULL, *generated = NULL, *reason = NULL, *choice = NULL, *version = NULL, *lockpath = NULL;
    char *stock_file = path(o, "keyfilters-stock.json");
    char *flag = path(o, "homekey"), *home = path(o, "home"), *os = readfile(o->osrelease);
    jvalue_ref current = jinvalid(), stock = jinvalid(), next = jinvalid();
    gboolean boot = !strcmp(action, "boot"), status = !strcmp(action, "status"), prepare = !strcmp(action, "prepare");
    /* The existing 5/10-12 policy is owned by the watcher, never this hook. */
    GRegex *release = g_regex_new("(?m)^VERSION(?:_ID)?=[\"']?([0-9]+)", 0, 0, NULL);
    GMatchInfo *match = NULL;
    int major = 0;
    if (os && g_regex_match(release, os, 0, &match)) {
        char *v = g_match_info_fetch(match, 1); major = atoi(v); g_free(v);
    }
    g_match_info_free(match); g_regex_unref(release); g_free(os);
    if (major >= 10 && !status && !prepare) {
        /* An update from 6-9 leaves the generated filters and the snapshot behind; 10+ has no use for them. */
        GDir *d = g_dir_open(o->state_dir, 0, NULL); const char *name;
        while (d && (name = g_dir_read_name(d))) {
            if (!strcmp(name, "keyfilters-stock.json") || (g_str_has_prefix(name, "systemUi-") && g_str_has_suffix(name, ".js"))) {
                char *p = path(o, name); unlink(p); g_free(p);
            }
        }
        if (d) g_dir_close(d);
    }
    if (major < 6 || major > 9) { g_string_append(out, "active=no\npatch=not-needed\nreboot=no\n"); rc = 0; goto done; }
    if (boot && !ready(o)) { reason = g_strdup("boot readiness timed out; stock Home retained"); goto done; }
    source = readfile(o->stock_js);
    generated = source ? hc_key_generate(source, "org.webosbrew.homechooser.probe", &reason) : NULL;
    if (!generated && !reason) reason = g_strdup("stock systemUi.js is unreadable");
    if (prepare) { rc = generated ? 0 : 2; goto done; }
    if (status) {
        current = get_list(o);
        g_string_append_printf(out, "active=%s\npatch=%s\nreboot=no\n", has_ours(o, current) ? "yes" : "no", generated ? "possible" : "refused");
        rc = 0; goto done;
    }
    /* A restore with no state dir has nothing to restore (stock keyFilters are saved in it, and the
     * uninstall removed it): do not recreate the dir and lock for a late run. apply/boot still create it. */
    if (!g_file_test(o->state_dir, G_FILE_TEST_IS_DIR) &&
        (!strcmp(action, "stop") || !strcmp(action, "clear") || !strcmp(action, "refresh"))) {
        g_string_append(out, "active=no\nreboot=no\n"); rc = 0; goto done;
    }
    if (g_mkdir_with_parents(o->state_dir, 0755)) goto done;
    lockpath = path(o, "keyfilter.lock");
    unsigned tries = 0;
    for (;;) {
        lock = open(lockpath, O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0600);
        if (lock < 0) goto done;
        while (flock(lock, LOCK_EX | LOCK_NB)) {
            if (++tries >= 50) { reason = g_strdup("another key-filter transaction is running"); goto done; }
            g_usleep(100000);
        }
        /* A finished restore unlinks the lock while others may wait on it: hold only the file that is
         * still at the path, else open the new one. */
        struct stat held, at;
        if (!fstat(lock, &held) && !lstat(lockpath, &at) && held.st_ino == at.st_ino && held.st_dev == at.st_dev) break;
        close(lock); lock = -1;
        if (++tries >= 50) { reason = g_strdup("another key-filter transaction is running"); goto done; }
    }
    current = get_list(o);
    if (!jis_valid(current)) { reason = g_strdup("configd did not return a valid nonempty keyFilters list"); goto done; }
    if (!has_ours(o, current)) {
        stock = jvalue_duplicate(current);
        if (!g_file_set_contents(stock_file, jvalue_stringify(stock), -1, NULL)) goto done;
    } else {
        char *saved = readfile(stock_file); stock = parse(saved); g_free(saved);
        if (!valid(o, stock, FALSE)) { reason = g_strdup("stock list unavailable; retaining loaded files"); goto done; }
        /* Refuse to overwrite concurrent third-party edits while an override is active. */
        jvalue_ref normalized = jvalue_duplicate(current);
        if (jarray_size(stock) == jarray_size(normalized)) {
            for (ssize_t i = 0; i < jarray_size(normalized); i++) {
                jvalue_ref item = jarray_get(normalized, i);
                char *file = str(item, "file"), *original = str(jarray_get(stock, i), "file");
                if (owned(o, file) && stock_path(original)) jobject_set(item, kb("file"), jstring_create(original));
                g_free(file); g_free(original);
            }
        }
        gboolean same = jvalue_equal(normalized, stock); j_release(&normalized);
        if (!same) { reason = g_strdup("keyFilters changed externally; retaining loaded files"); goto done; }
    }
    choice = readfile(home);
    if (choice) g_strstrip(choice);
    gboolean enabled = g_file_test(flag, G_FILE_TEST_EXISTS);
    gboolean restore = force_stock || !enabled || !choice || !*choice || !generated || !strcmp(action, "stop") || !strcmp(action, "clear");
    if (!restore) {
        if (strspn(choice, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-") != strlen(choice)) restore = TRUE;
        else {
            jvalue_ref p = jobject_create(); jobject_set(p, kb("id"), jstring_create(choice));
            jvalue_ref r = call(o, "luna://com.webos.applicationManager/getAppInfo", jvalue_stringify(p), 5);
            restore = !success(r); j_release(&p); j_release(&r);
        }
    }
    next = jvalue_duplicate(stock);
    if (!restore) {
        g_free(generated); generated = hc_key_generate(source, choice, &reason);
        if (!generated) goto done;
        /* A new immutable pathname for each change avoids Qt.include byte caches. */
        version = path(o, "systemUi-XXXXXX.js");
        int fd = mkstemps(version, 3);
        if (fd < 0) goto done;
        close(fd);
        if (!g_file_set_contents(version, generated, -1, NULL) || chmod(version, 0644)) goto done;
        char *bytes = readfile(version);
        char *expected = g_compute_checksum_for_string(G_CHECKSUM_SHA256, generated, -1);
        char *actual = bytes ? g_compute_checksum_for_string(G_CHECKSUM_SHA256, bytes, -1) : NULL;
        gboolean equal = actual && !strcmp(expected, actual);
        g_free(bytes); g_free(expected); g_free(actual);
        if (!equal) { reason = g_strdup("generated file checksum mismatch"); goto done; }
        /* Only a boot that really redirects Home announces it; stock and error boots stay silent. */
        if (boot) toast(o, "Your new home will start shortly");
        /* An unchanged choice and stock bytes need no loader churn. */
        gboolean unchanged = has_ours(o, current);
        for (ssize_t i = 0; i < jarray_size(next); i++) {
            jvalue_ref item = jarray_get(next, i);
            char *file = str(item, "file");
            if (stock_path(file)) {
                char *oldpath = str(jarray_get(current, i), "file");
                char *old = oldpath && owned(o, oldpath) ? readfile(oldpath) : NULL;
                if (!old || strcmp(old, generated)) unchanged = FALSE;
                g_free(oldpath); g_free(old);
                jobject_set(item, kb("file"), jstring_create(version));
            }
            g_free(file);
        }
        if (unchanged) { unlink(version); rc = 0; goto done; }
    }
    if (!jvalue_equal(next, current) && !select_list(o, next)) {
        /* A failed/readback-ambiguous write may have applied. Restore stock through configd;
         * never delete any source file until an exact readback proves it is unused. */
        gboolean recovered = select_list(o, stock);
        if (recovered) collect(o, stock);
        reason = g_strdup(recovered ? "activation failed; restored stock Home" : "configd recovery unverified; retaining all generated files");
        goto done;
    }
    collect(o, next);
    /* Stock restored and read back (the write above read it back, or the list already was stock) and
     * no choice: the snapshot and the lock have no further use; the dir goes when nothing else is in it. */
    if (restore && (!choice || !*choice)) {
        unlink(stock_file); unlink(lockpath); rmdir(o->state_dir);
    }
    g_string_append_printf(out, "active=%s\nreboot=no\n", restore ? "no" : "yes");
    rc = 0;
    if (reason) toast(o, "Home Chooser cannot change Home on this firmware. LG Home remains available.");
done:
    if (reason) { g_string_append_printf(out, "reason=%s\n", reason); if (boot && rc) toast(o, "Home Chooser could not start your home. LG Home remains available."); }
    if (lock >= 0) close(lock);
    j_release(&current); j_release(&stock); j_release(&next);
    g_free(lockpath); g_free(stock_file); g_free(flag); g_free(home); g_free(source); g_free(generated); g_free(reason); g_free(choice); g_free(version);
    return rc;
}
