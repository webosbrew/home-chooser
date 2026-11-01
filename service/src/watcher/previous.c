/* SPDX-License-Identifier: Apache-2.0 */
/* The previous launcher, closed later (docs/settings-app.md, "The previous launcher"). A change of Home
 * made in Home Chooser closes nothing while the panel is on screen: closing the launcher in front would
 * empty the foreground, SAM would bring up its idle app (6-9: the gate, which opens the last input for an
 * overlay launcher) and surface-manager would close the panel before its "Done" toast. Instead the app
 * writes the previous launcher (nothing for a change from LG Home) to the `previous` file, and here:
 *  - the hold: while the file is fresh, every close the watcher makes waits (the previous launcher, the
 *    6-9 gate's stock Home close, the 10+ LG Home close, the 10+ last-input launch). It ends when Home
 *    Chooser is off screen: its own close or background life event, a foreground event naming an app
 *    other than the one in front when the hold began, or, on the periodic check, Home Chooser no longer
 *    in SAM's `running` list;
 *  - then each previous launcher is closed once, at the first moment SAM names a full-screen app in front
 *    that is neither that launcher nor Home Chooser, so the close cannot empty the screen. The new choice
 *    and a stock home are never closed; a launcher the owner opens again, one that closes by itself and
 *    one that is uninstalled are dropped.
 * The file is the pending list. A watcher restart reads it again (and holds again until the next of those
 * events); a file written before this boot is dropped, since what it names is not running any more. */
#include "internal.h"
#include <stdlib.h>
#include <sys/stat.h>

static char *previous_path(HCWatch *w) { return g_build_filename(w->c.state_dir, "previous", NULL); }

/* Boot time, seconds since the epoch, from <proc>/stat; 0 when unknown. */
static gint64 boot_time(HCWatch *w)
{
    char *p = g_build_filename(w->c.proc, "stat", NULL), *text = NULL;
    g_file_get_contents(p, &text, NULL, NULL);
    g_free(p);
    const char *line = text ? strstr(text, "btime ") : NULL;
    gint64 t = line ? g_ascii_strtoll(line + 6, NULL, 10) : 0;
    g_free(text); return t;
}
/* Any stock home id: never closed here (the app's STOCK_LABELS are all com.webos.app.*home). */
static gboolean stock_home(const char *id)
{
    return stock_id(id) || (g_str_has_prefix(id, "com.webos.app.") && g_str_has_suffix(id, "home"));
}
static gboolean valid_id(const char *id)
{
    return id && *id && strspn(id, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-") == strlen(id);
}
static void ids_add(HCWatch *w, const char *id)
{
    if (!w->prev_ids) w->prev_ids = g_ptr_array_new_with_free_func(g_free);
    for (unsigned i = 0; i < w->prev_ids->len; i++) if (eq(g_ptr_array_index(w->prev_ids, i), id)) return;
    g_ptr_array_add(w->prev_ids, g_strdup(id));
}
static unsigned ids_count(HCWatch *w) { return w->prev_ids ? w->prev_ids->len : 0; }
static char *ids_text(HCWatch *w, const char *separator)
{
    GString *s = g_string_new(NULL);
    for (unsigned i = 0; i < ids_count(w); i++)
        g_string_append_printf(s, "%s%s", i ? separator : "", (char *)g_ptr_array_index(w->prev_ids, i));
    return g_string_free(s, FALSE);
}
static gint64 file_mtime(const char *p)
{
    struct stat st;
    return stat(p, &st) ? -1 : (gint64)st.st_mtim.tv_sec * G_USEC_PER_SEC + st.st_mtim.tv_nsec / 1000;
}
/* The list back to the file, or no file when nothing is left and the hold is over. */
static void ids_save(HCWatch *w)
{
    char *p = previous_path(w);
    if (!ids_count(w) && !w->prev_hold) {
        unlink(p); w->prev_mtime = -1;
    } else {
        char *text = ids_text(w, "\n"), *line = ids_count(w) ? g_strconcat(text, "\n", NULL) : g_strdup("");
        if (!write_atomic(p, line, strlen(line))) watch_log(w, "previous launcher: cannot write %s", p);
        w->prev_mtime = file_mtime(p);
        g_free(text); g_free(line);
    }
    g_free(p);
}
static void hold_end(HCWatch *w)
{
    w->prev_hold = FALSE;
    g_clear_pointer(&w->prev_front, g_free);
}
static char *foreground_id(HCWatch *w, gboolean *answered)
{
    jvalue_ref r = watch_call(w, SAM "getForegroundAppInfo", "{}", 5);
    if (answered) *answered = reply_ok(r);
    char *id = reply_ok(r) ? get_string(r, "appId") : NULL;
    j_release(&r); return id;
}

/* Reads the file when it changed since the last look: a new change of Home (re)starts the hold. */
void previous_load(HCWatch *w)
{
    char *p = previous_path(w);
    gint64 mtime = file_mtime(p);
    if (mtime == w->prev_mtime) { g_free(p); return; }
    w->prev_mtime = mtime;
    if (mtime < 0) {
        /* Removed behind our back (Home Chooser uninstalled, or by hand): nothing to hold or close. */
        if (w->prev_hold || ids_count(w)) watch_log(w, "previous launcher: %s is gone; nothing to close", p);
        hold_end(w); if (w->prev_ids) g_ptr_array_set_size(w->prev_ids, 0);
        g_free(p); return;
    }
    char *text = NULL;
    g_file_get_contents(p, &text, NULL, NULL);
    gint64 boot = boot_time(w);
    if (boot > 0 && mtime / G_USEC_PER_SEC < boot) {
        watch_log(w, "previous launcher: %s was written before this boot; dropped", p);
        hold_end(w); if (w->prev_ids) g_ptr_array_set_size(w->prev_ids, 0);
        unlink(p); w->prev_mtime = -1;
        g_free(text); g_free(p); return;
    }
    char **lines = g_strsplit(text ? text : "", "\n", -1);
    for (int i = 0; lines[i]; i++) {
        char *id = g_strstrip(lines[i]);
        if (valid_id(id)) ids_add(w, id);
        else if (*id) watch_log(w, "previous launcher: ignoring invalid app id in %s", p);
    }
    g_strfreev(lines); g_free(text); g_free(p);
    w->prev_hold = TRUE;
    g_free(w->prev_front); w->prev_front = foreground_id(w, NULL);
    char *list = ids_text(w, ", ");
    watch_log(w, "previous launcher: Home changed in Home Chooser; %s%s; closes wait until Home Chooser is off screen (in front: %s)",
        *list ? "to close later: " : "none to close", list, w->prev_front && *w->prev_front ? w->prev_front : "nothing");
    g_free(list);
    ids_save(w);
}
gboolean previous_holding(HCWatch *w)
{
    previous_load(w);
    return w->prev_hold;
}
gboolean previous_pending(HCWatch *w)
{
    previous_load(w);
    if (!ids_count(w) && w->prev_hold) { hold_end(w); ids_save(w); }
    return ids_count(w) > 0;
}

static void release(HCWatch *w, const char *why)
{
    watch_log(w, "previous launcher: Home Chooser is off screen (%s); held closes may go ahead", why);
    hold_end(w);
    ids_save(w);
    /* What waited: the 10+ LG Home close and the 6-9 gate's stock Home close, each under its own rules. */
    close_foreground(w);
    gate_resume(w);
}
static void drop(HCWatch *w, unsigned i, const char *why)
{
    watch_log(w, "previous launcher: %s %s; not closed", (char *)g_ptr_array_index(w->prev_ids, i), why);
    g_ptr_array_remove_index(w->prev_ids, i);
    ids_save(w);
}
static void finish(HCWatch *w)
{
    if (!w->only_close || ids_count(w)) return;
    /* Only here to close: with nothing left, no hold is needed either. */
    if (w->prev_hold) { hold_end(w); ids_save(w); }
    /* No choice and no Home key hook: the boot entry has nothing left to do, and would dangle once Home
     * Chooser is uninstalled (no watcher is left to remove it then). */
    char *choice = watch_choice(w), *flag = g_build_filename(w->c.state_dir, "homekey", NULL);
    struct stat st;
    if (!choice && !g_file_test(flag, G_FILE_TEST_EXISTS) && !lstat(w->c.init_link, &st) && S_ISLNK(st.st_mode)) {
        unlink(w->c.init_link);
        char *boot = g_build_filename(w->c.state_dir, "boot", NULL);
        watch_remove_tree(boot); g_free(boot);
        rmdir(w->c.state_dir); /* only when nothing else is left in it */
        watch_log(w, "previous launcher: no choice and no Home key hook; boot entry removed");
    }
    g_free(choice); g_free(flag);
    hc_watch_stop(w, 0, "previous launcher: nothing left to close, exiting");
}
/* Close what can be closed now: a full-screen app other than the launcher and Home Chooser is in front. */
static void close_ready(HCWatch *w)
{
    if (!ids_count(w)) { finish(w); return; }
    char *choice = watch_choice(w);
    for (unsigned i = ids_count(w); i-- > 0;) {
        const char *id = g_ptr_array_index(w->prev_ids, i);
        if (eq(id, choice)) drop(w, i, "is the launcher chosen now");
        else if (stock_home(id)) drop(w, i, "is a stock home");
    }
    g_free(choice);
    if (!ids_count(w)) { finish(w); return; }
    gboolean answered = FALSE;
    char *front = foreground_id(w, &answered);
    int window = front && *front && !eq(front, HC_APP_ID) ? window_type(w, front) : 0;
    if (window != 1) {
        if (answered && !eq(front, w->prev_logged)) {
            watch_log(w, "previous launcher: %s in front; waiting for a full-screen app", front && *front ? front : "nothing");
            g_free(w->prev_logged); w->prev_logged = g_strdup(front);
        }
        g_free(front); return;
    }
    for (unsigned i = ids_count(w); i-- > 0;) {
        char *id = g_strdup(g_ptr_array_index(w->prev_ids, i));
        if (eq(id, front)) {
            if (!eq(front, w->prev_logged)) {
                watch_log(w, "previous launcher: %s is in front; closed once another full-screen app is", id);
                g_free(w->prev_logged); w->prev_logged = g_strdup(front);
            }
            g_free(id); continue;
        }
        int state = app_state(w, id);
        if (state == -1) { drop(w, i, "is not installed"); g_free(id); continue; }
        /* Off the list (and the file) before the call: at most one close per change of Home. */
        g_ptr_array_remove_index(w->prev_ids, i);
        ids_save(w);
        jvalue_ref p = jobject_create(); put_str(p, "id", id); put_bool(p, "forceMode", true);
        jvalue_ref r = watch_call(w, SAM "closeByAppId", jvalue_stringify(p), 5);
        watch_log(w, "previous launcher: closeByAppId %s with %s in front: %s", id, front, r ? jvalue_stringify(r) : "no reply");
        j_release(&r); j_release(&p); g_free(id);
    }
    g_free(front);
    finish(w);
}

/* A foreground event (`front`), the periodic check ('T') or any other look (NULL). */
void previous_check(HCWatch *w, const char *front, char why)
{
    previous_load(w);
    if (!w->prev_hold && !ids_count(w)) { finish(w); return; }
    /* The app list changed: an uninstalled launcher has nothing to close. */
    for (unsigned i = ids_count(w); why == 'A' && i-- > 0;)
        if (app_state(w, g_ptr_array_index(w->prev_ids, i)) == -1) drop(w, i, "is not installed");
    if (w->only_close && !ids_count(w)) { finish(w); return; }
    if (w->prev_hold) {
        if (front && *front && !eq(front, HC_APP_ID) && !eq(front, w->prev_front)) {
            char *text = g_strdup_printf("%s came to the front", front);
            release(w, text); g_free(text);
        } else if (why == 'T') {
            jvalue_ref r = watch_call(w, SAM "running", "{}", 5);
            jvalue_ref list = reply_ok(r) ? member(r, "running") : NULL;
            gboolean found = FALSE;
            for (ssize_t i = 0; list && jis_array(list) && i < jarray_size(list) && !found; i++) {
                char *id = get_string(jarray_get(list, i), "id"); found = eq(id, HC_APP_ID); g_free(id);
            }
            if (list && jis_array(list) && !found) release(w, "not running");
            j_release(&r);
        }
        if (w->prev_hold || w->status >= 0) return;
    }
    close_ready(w);
}
/* A life event: Home Chooser leaving the screen ends the hold; a previous launcher opened again or
 * closed by itself is dropped; any launch may have put a full-screen app in front. */
void previous_life(HCWatch *w, const char *id, const char *event)
{
    previous_load(w);
    if (!w->prev_hold && !ids_count(w)) return;
    gboolean gone = eq(event, "close") || eq(event, "stop") || eq(event, "background");
    if (w->prev_hold && eq(id, HC_APP_ID) && gone) {
        char *text = g_strdup_printf("its %s event", event);
        release(w, text); g_free(text);
        if (w->status >= 0) return;
    }
    for (unsigned i = ids_count(w); id && i-- > 0;) {
        if (!eq(g_ptr_array_index(w->prev_ids, i), id)) continue;
        if (eq(event, "launch") || eq(event, "relaunch")) drop(w, i, "was opened again");
        else if (eq(event, "close") || eq(event, "stop")) drop(w, i, "closed by itself");
    }
    if (eq(event, "launch") || eq(event, "relaunch") || eq(event, "foreground") || gone) previous_check(w, NULL, 'L');
    else finish(w);
}
void previous_free(HCWatch *w)
{
    if (w->prev_ids) g_ptr_array_free(w->prev_ids, TRUE);
    g_free(w->prev_front); g_free(w->prev_logged);
}
