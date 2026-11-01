/* SPDX-License-Identifier: Apache-2.0 */
#include "internal.h"

static void log_stock(HCWatch *w)
{
    if (w->major >= 6 && w->major <= 9 && !w->gate_active)
        watch_log(w, "webOS %d: stock home left running until the next reboot (sam relaunches it as the last-input app)", w->major);
}
static gboolean preload_set(HCWatch *w, gboolean on)
{
    jvalue_ref p = jobject_create(), app = jobject_create();
    put_str(app, "id", STOCK); put_bool(app, "isEnabled", on);
    jobject_set(p, key_buf("application"), app);
    jvalue_ref r = watch_call(w, PRELOAD "setPreloadPolicy", jvalue_stringify(p), 5);
    gboolean ok = reply_ok(r);
    watch_log(w, "setPreloadPolicy %s isEnabled=%s: %s", STOCK, on ? "true" : "false", r ? jvalue_stringify(r) : "no reply");
    j_release(&r); j_release(&p); return ok;
}
static gboolean settled(gpointer context)
{
    HCWatch *w = context;
    w->preload_source = 0;
    if (w->preload_state == 2 && w->status < 0) {
        w->depth++;
        w->preload_ready = TRUE;
        log_stock(w); close_foreground(w);
        w->depth--;
    }
    if (w->ops.wake) w->ops.wake(w->ops.context);
    return G_SOURCE_REMOVE;
}
static gboolean boot_attempt(gpointer context)
{
    HCWatch *w = context;
    w->preload_source = 0;
    if (w->preload_state != 2 || w->status >= 0) return G_SOURCE_REMOVE;
    w->depth++;
    unsigned generation = w->preload_generation;
    gboolean ok = preload_set(w, FALSE);
    if (generation == w->preload_generation && w->preload_state == 2 && w->status < 0) {
        if (ok) w->preload_source = g_timeout_add(w->c.preload_settle_ms, settled, w);
        else if (++w->preload_attempts < w->c.preload_tries)
            w->preload_source = g_timeout_add(w->c.preload_retry_ms, boot_attempt, w);
    }
    w->depth--;
    if (w->ops.wake) w->ops.wake(w->ops.context);
    return G_SOURCE_REMOVE;
}
void preload_sync(HCWatch *w, int state, char why)
{
    if (!w->c.preload || w->major < 6 || w->preload_state == state) return;
    if (!w->preload_state && state == 1 && !why) { w->preload_state = 1; return; }
    w->preload_state = state;
    w->preload_generation++;
    if (w->preload_source) { g_source_remove(w->preload_source); w->preload_source = 0; }
    if (state == 1) w->preload_ready = FALSE;
    if (state == 2 && why == 's') {
        w->preload_attempts = 0;
        w->preload_source = g_timeout_add(0, boot_attempt, w);
        return;
    }
    if (preload_set(w, state == 1) && state == 2) {
        log_stock(w); w->preload_ready = TRUE;
    }
}
void preload_follow(HCWatch *w, const char *id, char why)
{
    if (w->missing || stock_id(id)) preload_sync(w, 1, why);
    else { remember_title(w, id); preload_sync(w, 2, why); }
}

static gboolean close_choice(HCWatch *w)
{
    if (w->major < 10 || !w->c.preload) return FALSE;
    char *id = watch_choice(w);
    if (!id) return FALSE;
    if (!eq(w->close_choice, id)) {
        g_free(w->close_choice); w->close_choice = g_strdup(id);
        w->close_done = w->close_launched = w->relaunch_logged = FALSE;
        w->window = 0;
    }
    g_free(id);
    return !w->missing && w->preload_ready && !w->close_done && !stock_id(w->close_choice);
}
static char *foreground_now(HCWatch *w)
{
    jvalue_ref r = watch_call(w, SAM "getForegroundAppInfo", "{}", 5);
    char *id = reply_ok(r) ? get_string(r, "appId") : NULL;
    j_release(&r); return id;
}
static void maybe_close(HCWatch *w, const char *foreground, gboolean fresh)
{
    if (!close_choice(w)) return;
    if (!w->window) {
        jvalue_ref r = watch_id_call(w, SAM "getAppInfo", w->close_choice, 5);
        if (!reply_ok(r)) { j_release(&r); return; }
        char *type = get_string(member(r, "appInfo"), "defaultWindowType");
        w->window = eq(type, "overlay") ? 2 : 1;
        g_free(type); j_release(&r);
    }
    if (w->window == 2) {
        if (!fresh) return;
        if (!foreground || !*foreground || stock_id(foreground) || eq(foreground, HC_APP_ID)) {
            watch_log(w, "overlay %s has no safe full-screen card beneath it; stock home left running", w->close_choice);
            return;
        }
    } else if (!eq(foreground, w->close_choice)) return;
    w->close_done = TRUE; w->close_launched = FALSE;
    jvalue_ref p = jobject_create(); put_str(p, "id", STOCK); put_bool(p, "forceMode", true);
    jvalue_ref r = watch_call(w, SAM "closeByAppId", jvalue_stringify(p), 5);
    watch_log(w, "closeByAppId %s after %s in front: %s", STOCK, w->close_choice, r ? jvalue_stringify(r) : "no reply");
    j_release(&r); j_release(&p);
}
void close_foreground(HCWatch *w)
{
    if (!close_choice(w)) return;
    char *id = foreground_now(w);
    if (id) maybe_close(w, id, w->close_launched);
    g_free(id);
}
void handle_foreground(HCWatch *w, jvalue_ref r)
{
    char *id = get_string(r, "appId");
    if (!id || !*id) { g_free(id); return; }
    maybe_close(w, id, w->close_launched);
    if (w->close_done && eq(id, STOCK) && !w->relaunch_logged) {
        watch_log(w, "sam brought %s to the foreground after its close; not closing it again this boot", STOCK);
        w->relaunch_logged = TRUE;
    }
    g_free(id);
}
void handle_life(HCWatch *w, jvalue_ref r)
{
    char *id = get_string(r, "appId"), *event = get_string(r, "event");
    close_choice(w);
    if (eq(id, w->close_choice) && (eq(event, "close") || eq(event, "background"))) {
        w->close_launched = FALSE; goto done;
    }
    if (!eq(event, "launch") && !eq(event, "relaunch") && !eq(event, "foreground")) goto done;
    if (eq(id, STOCK) && w->close_done && !w->relaunch_logged) {
        watch_log(w, "sam relaunched %s after its close; not closing it again this boot", STOCK);
        w->relaunch_logged = TRUE;
    }
    if (w->close_choice && eq(id, w->close_choice)) {
        w->close_launched = TRUE;
        char *fg = foreground_now(w);
        if (fg) maybe_close(w, fg, TRUE);
        g_free(fg);
    }
done:
    g_free(id); g_free(event);
}
