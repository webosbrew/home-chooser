/* SPDX-License-Identifier: Apache-2.0 */
#include "internal.h"

static jvalue_ref default_apps(jvalue_ref reply)
{
    jvalue_ref value = member(member(reply, "settings"), "defaultApps");
    if (jis_string(value)) {
        raw_buffer b = jstring_get_fast(value);
        char *text = g_strndup(b.m_str, b.m_len);
        jvalue_ref obj = parse_object(text); g_free(text); return obj;
    }
    return jis_object(value) ? jvalue_duplicate(value) : NULL;
}
char *record_home(jvalue_ref reply)
{
    jvalue_ref obj = default_apps(reply);
    char *id = get_string(obj, "home"); j_release(&obj);
    if (id && !*id) g_clear_pointer(&id, g_free);
    return id;
}
char *current_home(HCWatch *w)
{
    jvalue_ref r = watch_call(w, SETTINGS "getSystemSettings", RECORD_GET, 10);
    char *home = record_home(r); j_release(&r); return home;
}

gboolean merged_write(HCWatch *w, const char *id)
{
    jvalue_ref r = watch_call(w, SETTINGS "getSystemSettings", RECORD_GET, 10);
    jvalue_ref old = default_apps(r), record = jobject_create();
    gboolean valid = jis_object(r);
    if (jis_valid(member(member(r, "settings"), "defaultApps")) && !jis_object(old)) valid = FALSE;
    jobject_set(record, key_buf("home"), id ? jstring_create(id) : jnull());
    if (jis_object(old)) {
        jobject_iter it;
        jobject_key_value pair;
        if (jobject_iter_init(&it, old)) while (jobject_iter_next(&it, &pair)) {
            /* Shell refused non-flat records rather than risking unrelated settings. */
            if (jis_object(pair.value) || jis_array(pair.value)) valid = FALSE;
            raw_buffer name = jstring_get_fast(pair.key);
            if (name.m_len != 4 || memcmp(name.m_str, "home", 4))
                jobject_set(record, name, jvalue_duplicate(pair.value));
        }
    }
    j_release(&r); j_release(&old);
    if (!valid) {
        watch_log(w, "cannot read the defaultApps record, not writing"); j_release(&record); return FALSE;
    }
    jvalue_ref p = jobject_create(), settings = jobject_create();
    put_str(p, "category", "general");
    jobject_set(settings, key_buf("defaultApps"), record);
    jobject_set(p, key_buf("settings"), settings);
    r = watch_call(w, SETTINGS "setSystemSettings", jvalue_stringify(p), 10);
    gboolean ok = reply_ok(r);
    watch_log(w, "setSystemSettings %s: %s", ok ? "wrote" : "failed", jvalue_stringify(p));
    j_release(&r); j_release(&p); return ok;
}
gboolean apply_record(HCWatch *w, const char *id)
{
    if (w->major >= 9) {
        jvalue_ref p = jobject_create(); put_str(p, "category", "home"); put_str(p, "appId", id);
        jvalue_ref r = watch_call(w, SAM "setDefaultApp", jvalue_stringify(p), 10);
        gboolean ok = reply_ok(r);
        char *error = get_string(r, "errorText"), *lower = error ? g_ascii_strdown(error, -1) : NULL;
        gboolean fallback = lower && strstr(lower, "unknown method");
        watch_log(w, "setDefaultApp home=%s %s", id, ok ? "ok" : "failed");
        g_free(error); g_free(lower); j_release(&r); j_release(&p);
        if (ok || !fallback) return ok;
        watch_log(w, "setDefaultApp is an unknown method here, using setSystemSettings");
    }
    return merged_write(w, id);
}

static char *override_home(HCWatch *w)
{
    char *text = watch_read(w->c.override);
    jvalue_ref r = parse_object(text);
    char *id = get_string(member(member(r, "defaultApps"), "categories"), "home");
    j_release(&r); g_free(text); return id;
}
void check_override(HCWatch *w, const char *id)
{
    if (w->major < 10) return;
    char *existing = override_home(w);
    gboolean ok = eq(existing, id); g_free(existing);
    if (ok) { watch_log(w, "profile override ok"); return; }
    watch_log(w, "profile override missing or wrong, rebuilding it");
    jvalue_ref r = watch_call(w, PROFILE "getProfiles", "{\"names\":[\"defaultApps\"]}", 10);
    jvalue_ref profile = member(member(r, "profiles"), "defaultApps");
    if (!jis_object(profile)) profile = member(r, "defaultApps");
    if (!jis_object(profile) || !jis_object(member(profile, "categories"))) {
        watch_log(w, "getProfiles reply has no defaultApps profile with categories, override not written");
        j_release(&r); return;
    }
    jvalue_ref copy = jobject_create(), out = jobject_create();
    jobject_iter it;
    jobject_key_value pair;
    if (jobject_iter_init(&it, profile)) while (jobject_iter_next(&it, &pair)) {
        raw_buffer name = jstring_get_fast(pair.key);
        if (name.m_len && name.m_str[0] != '_') jobject_set(copy, name, jvalue_duplicate(pair.value));
    }
    put_str(member(copy, "categories"), "home", id);
    jobject_set(out, key_buf("defaultApps"), copy);
    if (write_atomic(w->c.override, jvalue_stringify(out), strlen(jvalue_stringify(out)))) {
        watch_log(w, "wrote %s", w->c.override);
        jvalue_ref scan = watch_call(w, PROFILE "debug/rescan", "{}", 10); j_release(&scan);
    } else watch_log(w, "cannot write %s", w->c.override);
    j_release(&r); j_release(&out);
}
void drop_override(HCWatch *w)
{
    if (w->major < 10 || !g_file_test(w->c.override, G_FILE_TEST_IS_REGULAR)) return;
    unlink(w->c.override);
    watch_log(w, "removed %s", w->c.override);
    jvalue_ref r = watch_call(w, PROFILE "debug/rescan", "{}", 10); j_release(&r);
}

static unsigned interval(HCWatch *w)
{
    unsigned iv = w->c.interval_s;
    for (unsigned i = 0; i < w->streak && iv < w->c.backoff_s; i++) iv = MIN(iv * 2, w->c.backoff_s);
    return MIN(iv, w->c.backoff_s);
}
void reconcile(HCWatch *w, const char *id)
{
    char *want = g_strdup(id);
    gint64 t = watch_now(w);
    unsigned required = interval(w);
    if (w->last_write > 0 && t - w->last_write < required) {
        char *found = current_home(w);
        gboolean matches = eq(found, want); g_free(found);
        if (matches) goto done;
        unsigned delay = required - (t - w->last_write);
        watch_log(w, "home mismatch; waiting %u s", delay);
        watch_wait(w, delay * 1000);
        if (w->status >= 0) goto done;
        g_free(want); want = watch_choice(w);
        if (!want) { hc_watch_stop(w, 0, "choice file is gone, exiting"); goto done; }
        found = current_home(w); matches = eq(found, want); g_free(found);
        if (matches) goto done;
        t = watch_now(w);
    }
    if (w->status >= 0) goto done;
    w->streak = w->last_write > 0 && t - w->last_write < 2 * required ? w->streak + 1 : 0;
    w->last_write = t;
    watch_log(w, "home mismatch, writing '%s' (streak %u, next interval %u s)", want, w->streak, interval(w));
    apply_record(w, want); check_override(w, want);
done:
    g_free(want);
}
