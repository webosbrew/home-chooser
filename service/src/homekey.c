/* SPDX-License-Identifier: Apache-2.0 */
#define _GNU_SOURCE
#include "homekey.h"
#include "policy.h"
#include <pbnjson.h>
#include <sys/file.h>
#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <stdlib.h>
#include <stdarg.h>
#include <sys/stat.h>

#define KEY "com.webos.surfacemanager.keyFilters"
#define GET "luna://com.webos.service.config/getConfigs"
#define SET "luna://com.webos.service.config/setConfigs"
#define RECONFIGURE "luna://com.webos.service.config/reconfigure"
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
/* Our generated files: systemUi-XXXXXX.js (6-9, a changed copy of the stock file) and homekey40-XXXXXX.js (4.0,
 * the filter entry inserted before handleSystemKeys). */
static gboolean owned_named(const HCKeyOps *o, const char *s, const char *name) {
    char *prefix = path(o, name);
    gboolean yes = s && g_str_has_prefix(s, prefix) && g_str_has_suffix(s, ".js") && !strchr(s + strlen(prefix), '/');
    g_free(prefix); return yes;
}
static gboolean owned_patch(const HCKeyOps *o, const char *s) { return owned_named(o, s, "systemUi-"); }
static gboolean owned_insert(const HCKeyOps *o, const char *s) { return owned_named(o, s, "homekey40-"); }
static gboolean owned(const HCKeyOps *o, const char *s) { return owned_patch(o, s) || owned_insert(o, s); }
#define INSERT_HANDLER "handleHomeChooserKeys"
#define STOCK_HANDLER "handleSystemKeys"
/* Index of the stock handleSystemKeys entry (found by name: the list differs between device layers), -1 when
 * there is none of the stock systemUi.js. */
static ssize_t insert_index(jvalue_ref list) {
    for (ssize_t i = 0; jis_array(list) && i < jarray_size(list); i++) {
        char *file = str(jarray_get(list, i), "file"), *handler = str(jarray_get(list, i), "handler");
        gboolean hit = stock_path(file) && handler && !strcmp(handler, STOCK_HANDLER);
        g_free(file); g_free(handler);
        if (hit) return i;
    }
    return -1;
}
/* A copy of a list without our 4.0 entries (the list as configd had it before the insert). */
static jvalue_ref without_inserted(const HCKeyOps *o, jvalue_ref list) {
    jvalue_ref out = jarray_create(NULL);
    for (ssize_t i = 0; i < jarray_size(list); i++) {
        char *file = str(jarray_get(list, i), "file");
        if (!owned_insert(o, file)) jarray_append(out, jvalue_duplicate(jarray_get(list, i)));
        g_free(file);
    }
    return out;
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
        if (stock_path(file) || (allow_ours && owned_patch(o, file))) systems++;
        else if (owned(o, file) && !(allow_ours && owned_insert(o, file))) ok = FALSE;
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
static void klog(const HCKeyOps *o, const char *fmt, ...) G_GNUC_PRINTF(2, 3);
static void klog(const HCKeyOps *o, const char *fmt, ...) {
    if (!o->log) return;
    va_list ap; va_start(ap, fmt); char *line = g_strdup_vprintf(fmt, ap); va_end(ap);
    o->log(line, o->context); g_free(line);
}
static ssize_t count(jvalue_ref list) { return jis_array(list) ? jarray_size(list) : -1; }
/* Index of the first entry where two lists differ (the shorter length when one is a prefix), -1 when equal. */
static ssize_t first_diff(jvalue_ref a, jvalue_ref b) {
    ssize_t na = count(a), nb = count(b), n = na < nb ? na : nb;
    for (ssize_t i = 0; i < n; i++) if (!jvalue_equal(jarray_get(a, i), jarray_get(b, i))) return i;
    return na == nb ? -1 : (n < 0 ? 0 : n);
}
void hc_key_mark_override(const char *state_dir, gboolean on) {
    char *p = g_build_filename(state_dir, "override", NULL);
    if (on) { if (g_file_test(state_dir, G_FILE_TEST_IS_DIR)) g_file_set_contents(p, "", -1, NULL); }
    else unlink(p);
    g_free(p);
}
static void toast(const HCKeyOps *o, const char *message) {
    jvalue_ref p = jobject_create();
    jobject_set(p, kb("sourceId"), jstring_create("org.webosbrew.homechooser"));
    jobject_set(p, kb("noaction"), jboolean_create(true));
    jobject_set(p, kb("message"), jstring_create(message));
    jvalue_ref r = call(o, "luna://com.webos.notification/createToast", jvalue_stringify(p), 2);
    j_release(&r); j_release(&p);
}
/* before: the list configd held before this write (NULL: do not classify). *persistent is set when the volatile
 * write was accepted and the read-back still is that earlier list: a persistent list masks volatile writes. */
static gboolean select_list(const HCKeyOps *o, jvalue_ref list, jvalue_ref before, gboolean *persistent) {
    if (persistent) *persistent = FALSE;
    if (!valid(o, list, TRUE)) { klog(o, "keyFilters write refused: the list is not valid"); return FALSE; }
    for (ssize_t i = 0; i < jarray_size(list); i++) {
        char *file = str(jarray_get(list, i), "file");
        gboolean available = !owned(o, file) || g_file_test(file, G_FILE_TEST_IS_REGULAR);
        if (stock_path(file)) available = g_file_test(o->stock_js, G_FILE_TEST_IS_REGULAR);
        if (!available) klog(o, "keyFilters write refused: %s is not available", file ? file : "(none)");
        g_free(file);
        if (!available) return FALSE;
    }
    jvalue_ref p = jobject_create(), configs = jobject_create();
    jobject_set(configs, kb(KEY), jvalue_duplicate(list));
    jobject_set(p, kb("configs"), configs);
    jobject_set(p, kb("volatile"), jboolean_create(true));
    char *text = o->call(SET, jvalue_stringify(p), 5, o->context);
    jvalue_ref r = parse(text);
    gboolean accepted = success(r), ok = accepted;
    j_release(&r); j_release(&p);
    jvalue_ref got = get_list(o);
    gboolean read = jis_valid(got), equal = read && jvalue_equal(got, list);
    ok = ok && equal;
    if (!ok) {
        klog(o, "setConfigs(volatile) reply=%s; wrote %zd entries, read back %zd; first difference at index %zd",
            text ? text : "(none)", count(list), read ? count(got) : (ssize_t)-1, read ? first_diff(list, got) : (ssize_t)-1);
        if (persistent && accepted && read && before && jis_array(before) && jvalue_equal(got, before)) {
            *persistent = TRUE;
            klog(o, "the read-back is the list from before the write: a persistent keyFilters override masks volatile writes");
        }
    }
    g_free(text); j_release(&got); return ok;
}
/* Clear the persistent override: a persistent null (no volatile flag) for the key, then read; when the list has
 * not changed, one reconfigure and read again, a few times. The first changed valid list is the result (else the
 * last read; JINVALID when configd never gave a valid one). A persistent list equal to the stock one cannot be
 * told from stock by reading, so an unchanged valid list is accepted after the retries; the volatile write that
 * follows is the proof. */
static jvalue_ref clear_persistent(const HCKeyOps *o, jvalue_ref before) {
    jvalue_ref p = jobject_create(), configs = jobject_create();
    jobject_set(configs, kb(KEY), jnull());
    jobject_set(p, kb("configs"), configs);
    char *text = o->call(SET, jvalue_stringify(p), 5, o->context);
    jvalue_ref r = parse(text);
    gboolean accepted = success(r);
    klog(o, "clear: setConfigs(persistent null) reply=%s", text ? text : "(none)");
    g_free(text); j_release(&r); j_release(&p);
    if (!accepted) return jinvalid();
    jvalue_ref got = jinvalid();
    for (unsigned n = 0; n < 3; n++) {
        j_release(&got); got = get_list(o);
        ssize_t at = jis_valid(got) ? first_diff(before, got) : -1;
        klog(o, "clear: read %u: %zd entries, %s", n + 1, jis_valid(got) ? count(got) : (ssize_t)-1,
            !jis_valid(got) ? "no valid list" : at >= 0 ? "changed" : "unchanged");
        if (jis_valid(got) && at >= 0) return got;
        if (n + 1 < 3) {
            jvalue_ref rr = call(o, RECONFIGURE, "{}", 5);
            klog(o, "clear: reconfigure returnValue=%s", success(rr) ? "true" : "false");
            j_release(&rr);
            if (o->retry_ms) g_usleep(o->retry_ms * 1000);
        }
    }
    return got;
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
int hc_key_run_ex(const HCKeyOps *o, const char *action, gboolean force_stock, gboolean clear_override, GString *out) {
    int rc = 1, lock = -1;
    char *source = NULL, *generated = NULL, *reason = NULL, *choice = NULL, *version = NULL, *lockpath = NULL;
    char *stock_file = path(o, "keyfilters-stock.json");
    char *flag = path(o, "homekey"), *home = path(o, "home"), *os = readfile(o->release_file);
    gboolean overlay = FALSE, cleared = FALSE;
    jvalue_ref current = jinvalid(), stock = jinvalid(), next = jinvalid();
    gboolean boot = !strcmp(action, "boot"), status = !strcmp(action, "status"), prepare = !strcmp(action, "prepare");
    /* The existing 5/10-12 policy is owned by the watcher, never this hook. */
    int major = hc_release_major(os);
    source = readfile(o->stock_js);
    HCKeyMode mode = hc_key_mode(os, source);
    g_free(os);
    if ((major >= 10 || (major == 4 && mode == HC_KEY_NONE)) && !status && !prepare) {
        /* An update from 6-9 (or from 4.0 to 4.5) leaves the generated filters and the snapshot behind; they have no use there. */
        GDir *d = g_dir_open(o->state_dir, 0, NULL); const char *name;
        while (d && (name = g_dir_read_name(d))) {
            if (!strcmp(name, "keyfilters-stock.json") || ((g_str_has_prefix(name, "systemUi-") || g_str_has_prefix(name, "homekey40-")) && g_str_has_suffix(name, ".js"))) {
                char *p = path(o, name); unlink(p); g_free(p);
            }
        }
        if (d) g_dir_close(d);
    }
    if (mode == HC_KEY_NONE) { g_string_append(out, "active=no\npatch=not-needed\nreboot=no\n"); rc = 0; goto done; }
    if (boot && !ready(o)) { reason = g_strdup("boot readiness timed out; stock Home retained"); goto done; }
    if (mode == HC_KEY_INSERT) generated = hc_key40_generate("org.webosbrew.homechooser.probe", FALSE, &reason);
    else generated = source ? hc_key_generate(source, "org.webosbrew.homechooser.probe", &reason) : NULL;
    if (!generated && !reason) reason = g_strdup("stock systemUi.js is unreadable");
    if (prepare) { rc = generated ? 0 : 2; goto done; }
    if (status) {
        current = get_list(o);
        gboolean possible = generated && (mode != HC_KEY_INSERT || insert_index(current) >= 0 || has_ours(o, current));
        gboolean live = has_ours(o, current);
        char *marker = path(o, "override"); gboolean seen = !live && g_file_test(marker, G_FILE_TEST_EXISTS); g_free(marker);
        g_string_append_printf(out, "active=%s\npatch=%s\nreboot=no\noverride=%s\n", live ? "yes" : "no", possible ? "possible" : "refused", seen ? "yes" : "no");
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
    if (clear_override && !strcmp(action, "apply") && !force_stock && !has_ours(o, current) && g_file_test(flag, G_FILE_TEST_EXISTS)) {
        /* Only for an apply that has a choice to make; a restore never writes persistently. */
        char *wanted = readfile(home); if (wanted) g_strstrip(wanted);
        gboolean go = wanted && *wanted; g_free(wanted);
        if (go) {
            klog(o, "clear: asked to clear a persistent keyFilters override");
            jvalue_ref after = clear_persistent(o, current);
            if (!jis_valid(after) || has_ours(o, after)) { j_release(&after); reason = g_strdup("override-clear-failed"); goto done; }
            j_release(&current); current = after; cleared = TRUE;
            klog(o, "clear: the stock snapshot is taken again from the %zd entries now in configd", count(current));
        }
    }
    if (!has_ours(o, current)) {
        stock = jvalue_duplicate(current);
        if (!g_file_set_contents(stock_file, jvalue_stringify(stock), -1, NULL)) goto done;
    } else {
        char *saved = readfile(stock_file); stock = parse(saved); g_free(saved);
        if (!valid(o, stock, FALSE)) { reason = g_strdup("stock list unavailable; retaining loaded files"); goto done; }
        /* Refuse to overwrite concurrent third-party edits while an override is active. */
        jvalue_ref normalized = mode == HC_KEY_INSERT ? without_inserted(o, current) : jvalue_duplicate(current);
        if (mode == HC_KEY_PATCH && jarray_size(stock) == jarray_size(normalized)) {
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
    /* 4.0: the entry goes before the stock handleSystemKeys; a list without one cannot be changed. */
    if (mode == HC_KEY_INSERT && insert_index(stock) < 0 && generated) {
        g_free(generated); generated = NULL;
        reason = g_strdup("keyFilters has no stock handleSystemKeys entry; stock Home retained");
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
            restore = !success(r);
            char *window = str(jobject_get(r, kb("appInfo")), "defaultWindowType");
            overlay = window && !strcmp(window, "overlay"); g_free(window);
            j_release(&p); j_release(&r);
        }
    }
    next = jvalue_duplicate(stock);
    if (!restore) {
        g_free(generated);
        generated = mode == HC_KEY_INSERT ? hc_key40_generate(choice, overlay, &reason) : hc_key_generate(source, choice, &reason);
        if (!generated) goto done;
        /* A new immutable pathname for each change avoids loader byte caches. */
        version = path(o, mode == HC_KEY_INSERT ? "homekey40-XXXXXX.js" : "systemUi-XXXXXX.js");
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
        if (mode == HC_KEY_INSERT) {
            jvalue_ref inserted = jobject_create();
            jobject_set(inserted, kb("file"), jstring_create(version));
            jobject_set(inserted, kb("handler"), jstring_create(INSERT_HANDLER));
            ssize_t at = insert_index(stock);
            jvalue_ref grown = jarray_create(NULL);
            for (ssize_t i = 0; i < jarray_size(stock); i++) {
                if (i == at) jarray_append(grown, jvalue_duplicate(inserted));
                jarray_append(grown, jvalue_duplicate(jarray_get(stock, i)));
            }
            j_release(&inserted); j_release(&next); next = grown;
            /* unchanged: the live entry already is this file's bytes, and nothing else differs */
            gboolean same = FALSE;
            for (ssize_t i = 0; unchanged && i < jarray_size(current); i++) {
                char *file = str(jarray_get(current, i), "file");
                if (owned_insert(o, file)) {
                    char *old = readfile(file); same = old && !strcmp(old, generated); g_free(old);
                    jobject_set(jarray_get(next, i), kb("file"), jstring_create(file));
                }
                g_free(file);
            }
            unchanged = unchanged && same && jvalue_equal(next, current);
            if (!unchanged) {
                /* the new version's path is what gets written */
                for (ssize_t i = 0; i < jarray_size(next); i++) {
                    char *file = str(jarray_get(next, i), "file");
                    if (owned_insert(o, file)) jobject_set(jarray_get(next, i), kb("file"), jstring_create(version));
                    g_free(file);
                }
            }
        }
        for (ssize_t i = 0; mode == HC_KEY_PATCH && i < jarray_size(next); i++) {
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
        if (unchanged) { unlink(version); hc_key_mark_override(o->state_dir, FALSE); g_string_append(out, "active=yes\nreboot=no\n"); rc = 0; goto done; }
    }
    gboolean persistent = FALSE;
    if (!jvalue_equal(next, current) && !select_list(o, next, current, &persistent)) {
        /* A failed/readback-ambiguous write may have applied. Restore stock through configd;
         * never delete any source file until an exact readback proves it is unused. */
        gboolean recovered = select_list(o, stock, NULL, NULL);
        if (recovered) collect(o, stock);
        if (persistent) hc_key_mark_override(o->state_dir, TRUE);
        reason = g_strdup(recovered ? (persistent ? (cleared ? "override-not-cleared" : "persistent-override") : "activation failed; restored stock Home") : "configd recovery unverified; retaining all generated files");
        goto done;
    }
    collect(o, next);
    hc_key_mark_override(o->state_dir, FALSE);
    /* Stock restored and read back (the write above read it back, or the list already was stock) and
     * no choice: the snapshot and the lock have no further use; the dir goes when nothing else is in it. */
    if (restore && (!choice || !*choice)) {
        unlink(stock_file); unlink(lockpath); rmdir(o->state_dir);
    }
    g_string_append_printf(out, "active=%s\nreboot=no\n", restore ? "no" : "yes");
    rc = 0;
    if (reason) toast(o, "Home Chooser cannot change Home on this firmware. LG Home remains available.");
done:
    if (reason) { g_string_append_printf(out, "reason=%s\n", reason); if (boot && rc) toast(o, !strcmp(reason, "persistent-override") ? HC_OVERRIDE_TOAST : "Home Chooser could not start your home. LG Home remains available."); }
    if (lock >= 0) close(lock);
    j_release(&current); j_release(&stock); j_release(&next);
    g_free(lockpath); g_free(stock_file); g_free(flag); g_free(home); g_free(source); g_free(generated); g_free(reason); g_free(choice); g_free(version);
    return rc;
}
int hc_key_run(const HCKeyOps *o, const char *action, gboolean force_stock, GString *out) {
    return hc_key_run_ex(o, action, force_stock, FALSE, out);
}
