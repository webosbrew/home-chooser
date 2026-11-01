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

void override_read(HCWatch *w, char **home, jvalue_ref *idle)
{
    char *text = watch_read(w->c.override);
    jvalue_ref r = parse_object(text);
    if (home) *home = get_string(member(member(r, "defaultApps"), "categories"), "home");
    if (idle) *idle = jis_object(member(r, "idleApp")) ? jvalue_duplicate(member(r, "idleApp")) : NULL;
    j_release(&r); g_free(text);
}
/* idleApp.support, or `fallback` when it is absent or not a boolean. */
static bool idle_support(jvalue_ref idle, bool fallback)
{
    bool support = fallback;
    if (jis_object(idle) && jis_boolean(member(idle, "support")) && jboolean_get(member(idle, "support"), &support) != CONV_OK)
        support = fallback;
    return support;
}
char *idle_app_of(jvalue_ref idle)
{
    char *id = jis_object(idle) && idle_support(idle, true) ? get_string(idle, "appId") : NULL;
    if (id && !*id) g_clear_pointer(&id, g_free);
    return id;
}
int window_type(HCWatch *w, const char *id)
{
    if (!id || !*id) return 0;
    jvalue_ref r = watch_id_call(w, SAM "getAppInfo", id, 10);
    int type = 0;
    if (reply_ok(r)) {
        char *t = get_string(member(r, "appInfo"), "defaultWindowType");
        type = eq(t, "overlay") ? 2 : 1; g_free(t);
    }
    j_release(&r); return type;
}
/* The written idleApp fits `window`: 1 names the launcher, 2 has support false; 0 (getAppInfo does not
 * know the launcher) accepts no idleApp, support false, or one already naming this launcher. */
static gboolean idle_ok(jvalue_ref idle, const char *id, int window)
{
    char *app = jis_object(idle) ? get_string(idle, "appId") : NULL;
    /* explicitly true / explicitly false */
    gboolean on = idle_support(idle, false), off = !idle_support(idle, true), ok;
    if (window == 1) ok = jis_object(idle) && on && eq(app, id);
    else if (window == 2) ok = jis_object(idle) && off;
    else ok = !jis_object(idle) || off || eq(app, id);
    g_free(app); return ok;
}
/* A copy of a profile object without its `_` metadata keys and without the keys in `skip`. */
static jvalue_ref profile_copy(jvalue_ref profile, const char *const *skip)
{
    jvalue_ref copy = jobject_create();
    jobject_iter it;
    jobject_key_value pair;
    if (jis_object(profile) && jobject_iter_init(&it, profile)) while (jobject_iter_next(&it, &pair)) {
        raw_buffer name = jstring_get_fast(pair.key);
        gboolean keep = name.m_len && name.m_str[0] != '_';
        for (int i = 0; keep && skip && skip[i]; i++)
            if (name.m_len == strlen(skip[i]) && !memcmp(name.m_str, skip[i], name.m_len)) keep = FALSE;
        if (keep) jobject_set(copy, name, jvalue_duplicate(pair.value));
    }
    return copy;
}
static jvalue_ref profile_named(jvalue_ref reply, const char *name)
{
    jvalue_ref profile = member(member(reply, "profiles"), name);
    return jis_object(profile) ? profile : member(reply, name);
}
void check_override(HCWatch *w, const char *id)
{
    if (w->major < 10) return;
    char *existing = NULL;
    jvalue_ref idle = NULL;
    override_read(w, &existing, &idle);
    int window = window_type(w, id);
    if (!window) watch_log(w, "getAppInfo does not know '%s', no idleApp for it", id ? id : "");
    gboolean ok = eq(existing, id) && idle_ok(idle, id, window);
    g_free(existing); j_release(&idle);
    if (ok) { watch_log(w, "profile override ok"); return; }
    watch_log(w, "profile override missing or wrong, rebuilding it");
    jvalue_ref r = watch_call(w, PROFILE "getProfiles", "{\"names\":[\"defaultApps\",\"idleApp\"]}", 10);
    jvalue_ref profile = profile_named(r, "defaultApps");
    if (!jis_object(profile) || !jis_object(member(profile, "categories"))) {
        watch_log(w, "getProfiles reply has no defaultApps profile with categories, override not written");
        j_release(&r); return;
    }
    jvalue_ref copy = profile_copy(profile, NULL), out = jobject_create();
    put_str(member(copy, "categories"), "home", id);
    jobject_set(out, key_buf("defaultApps"), copy);
    if (window) {
        /* The profile's other idleApp keys stay; support and appId are this choice's (docs/settings-app.md). */
        const char *const skip[] = {"support", "appId", NULL};
        jvalue_ref idle_out = profile_copy(profile_named(r, "idleApp"), skip);
        put_bool(idle_out, "support", window == 1);
        if (window == 1) put_str(idle_out, "appId", id);
        jobject_set(out, key_buf("idleApp"), idle_out);
    }
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
