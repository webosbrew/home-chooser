/* SPDX-License-Identifier: Apache-2.0 */
/* Keep the chosen launcher out of the memory manager's launch-time closes (docs/service.md, "Keeping the launcher
 * alive"). memchute reads its exempt lists once, at start:
 *   4.0 to 9.x: /home/root/memorymanager-conf.json in place of /etc/palm/memorymanager-conf.json. Ours is a symlink there
 *               to a copy of the stock file with the launcher added to KeepOnLaunchEx (KeepOnLaunch on 4.0, which has no Ex).
 *   10+:        memchute ignores that link and takes the lists from configd: a volatile KeepOnLaunchEx, read back exactly.
 * memchute is restarted only when what it would read changed. A restart that is owed is a file (keep-restart), so a watcher
 * that ends before it succeeded leaves it for the next one. */
#include "internal.h"
#include "proc.h"
#include <errno.h>
#include <stdarg.h>
#include <sys/stat.h>

#define MEM_FILE "memorymanager-conf.json"
#define MEM_MARK "keep-launcher"   /* 10+: the ids we may have put on the configd list, one a line */
#define MEM_OWED "keep-restart"    /* a memchute restart is owed */
#define MEM_KEY "com.webos.memorymanager.KeepOnLaunchEx"
#define MEM_SHORT "KeepOnLaunchEx"
#define CONFIG "luna://com.webos.service.config/"
#define MEM_REAPPLY_MAX 5
#define MEM_RESTART_MAX 5          /* failed restarts per watcher run; backoff mem_backoff_s (60 s), then double, a timer of its own */

/* causes logged once per watcher run, in mem_logged */
enum { L_STOCK = 1, L_STOCK_JSON = 2, L_STOCK_LIST = 4, L_WRITE = 8, L_LINK = 16, L_GAVE_UP = 32 };
static void log_once(HCWatch *w, unsigned cause, const char *format, ...) G_GNUC_PRINTF(3, 4);
static void log_once(HCWatch *w, unsigned cause, const char *format, ...)
{
    if (w->mem_logged & cause) return;
    w->mem_logged |= cause;
    va_list ap; va_start(ap, format);
    char *message = g_strdup_vprintf(format, ap); va_end(ap);
    watch_log(w, "%s", message); g_free(message);
}

static char *state_file(HCWatch *w, const char *name) { return g_build_filename(w->c.state_dir, name, NULL); }
static char *ours_path(HCWatch *w) { return state_file(w, MEM_FILE); }

/* ---- the restart ---- */

static int exec_argv(HCWatch *w, const char *const *argv, GString *out, GString *err)
{
    if (w->ops.exec) return w->ops.exec(argv, w->ops.context);
    return run_argv(argv, 30, out, err, NULL);
}
/* The first executable of a space separated list of paths, or NULL. */
static char *first_executable(const char *paths)
{
    char **list = g_strsplit(paths ? paths : "", " ", -1);
    char *found = NULL;
    for (int i = 0; list[i] && !found; i++) if (*list[i] && g_file_test(list[i], G_FILE_TEST_IS_EXECUTABLE)) found = g_strdup(list[i]);
    g_strfreev(list); return found;
}
static int run_restart(HCWatch *w)
{
    if (w->ops.restart) return w->ops.restart(w->ops.context);
    GString *out = g_string_new(NULL), *err = g_string_new(NULL);
    int rc;
    char *systemctl = first_executable(w->c.systemctl), *initctl = systemctl ? NULL : first_executable(w->c.initctl);
    if (systemctl) {
        const char *const argv[] = {systemctl, "restart", "memchute", NULL};
        rc = exec_argv(w, argv, out, err);
    } else if (initctl) {
        /* upstart (4.x): `restart` fails when the job is not running, then `start` brings it up */
        const char *const restart[] = {initctl, "restart", "memchute", NULL}, *const start[] = {initctl, "start", "memchute", NULL};
        rc = exec_argv(w, restart, out, err);
        if (rc) { g_string_truncate(out, 0); g_string_truncate(err, 0); rc = exec_argv(w, start, out, err); }
    } else { g_string_append(err, "neither systemctl nor initctl found"); rc = -1; }
    if (rc) watch_log(w, "memchute restart failed (%d): %s%s", rc, out->str, err->str);
    g_free(systemctl); g_free(initctl);
    g_string_free(out, TRUE); g_string_free(err, TRUE); return rc;
}
static void restart_timer_cancel(HCWatch *w)
{
    if (w->mem_restart_source) { g_source_remove(w->mem_restart_source); w->mem_restart_source = 0; }
}
static void restart_if_owed(HCWatch *w);
static gboolean restart_fire(gpointer context)
{
    HCWatch *w = context;
    w->mem_restart_source = 0;
    if (w->status >= 0) return G_SOURCE_REMOVE;
    w->depth++;
    restart_if_owed(w);
    w->depth--;
    if (w->ops.wake) w->ops.wake(w->ops.context);
    return G_SOURCE_REMOVE;
}
static gboolean restart_due(HCWatch *w)
{
    return w->mem_rs_fail < MEM_RESTART_MAX && watch_now(w) >= w->mem_rs_next;
}
static gboolean is_owed(HCWatch *w)
{
    if (w->mem_owed) return TRUE;
    char *p = state_file(w, MEM_OWED);
    gboolean owed = g_file_test(p, G_FILE_TEST_EXISTS); g_free(p);
    if (owed) w->mem_owed = TRUE;
    return owed;
}
/* A restart is due: remembered on disk until one succeeded. */
static void owe_restart(HCWatch *w)
{
    w->mem_owed = TRUE;
    char *p = state_file(w, MEM_OWED);
    write_atomic(p, "1\n", 2); g_free(p);
}
/* Restart now when one is owed and the backoff allows: a failure is counted, backed off and, at the cap, left for the next run. */
static void restart_if_owed(HCWatch *w)
{
    if (!is_owed(w)) return;
    if (w->mem_rs_fail >= MEM_RESTART_MAX) {
        log_once(w, L_GAVE_UP, "memchute restart failed %d times, not trying again in this run (a restart stays owed for the next one)", MEM_RESTART_MAX);
        return;
    }
    if (!restart_due(w)) return;
    watch_log(w, "restarting memchute so the launcher keep list takes effect");
    if (!run_restart(w)) {
        w->mem_owed = FALSE; w->mem_rs_fail = 0; w->mem_rs_next = 0;
        char *p = state_file(w, MEM_OWED); unlink(p); g_free(p);
        restart_timer_cancel(w);
        return;
    }
    gint64 wait_s = (gint64)w->c.mem_backoff_s << w->mem_rs_fail;
    w->mem_rs_next = watch_now(w) + wait_s;
    w->mem_rs_fail++;
    /* its own timer: the next attempt does not wait for the 300 s check or an app event */
    restart_timer_cancel(w);
    if (w->mem_rs_fail < MEM_RESTART_MAX) {
        w->mem_restart_delay_ms = (unsigned)(wait_s * 1000);
        w->mem_restart_source = g_timeout_add(w->mem_restart_delay_ms, restart_fire, w);
    }
}
/* TRUE when a call into the apply path could still do something about an owed restart. */
static gboolean owed_actionable(HCWatch *w) { return w->mem_owed && restart_due(w); }

static gboolean has_item(jvalue_ref list, const char *id)
{
    for (ssize_t i = 0; jis_array(list) && i < jarray_size(list); i++) {
        jvalue_ref e = jarray_get(list, i);
        if (!jis_string(e)) continue;
        raw_buffer b = jstring_get_fast(e);
        if (strlen(id) == b.m_len && !strncmp(b.m_str, id, b.m_len)) return TRUE;
    }
    return FALSE;
}
static gboolean all_strings(jvalue_ref list)
{
    if (!jis_array(list)) return FALSE;
    for (ssize_t i = 0; i < jarray_size(list); i++) if (!jis_string(jarray_get(list, i))) return FALSE;
    return TRUE;
}

/* ---- 4.0 to 9.x: the symlink route ---- */

/* 0 absent, 1 our link, 2 someone else's (a foreign link, a regular file, anything else) */
static int link_state(HCWatch *w, const char *ours)
{
    struct stat st;
    if (lstat(w->c.mem_link, &st)) return 0;
    if (S_ISLNK(st.st_mode)) {
        char *target = g_file_read_link(w->c.mem_link, NULL);
        gboolean mine = eq(target, ours); g_free(target);
        if (mine) return 1;
    }
    return 2;
}
/* The stock file with id in KeepOnLaunchEx (KeepOnLaunch when there is no Ex list); NULL with a line in the log (once) when it cannot be made. */
static char *build_copy(HCWatch *w, const char *id)
{
    char *text = watch_read(w->c.mem_stock);
    if (!text) { log_once(w, L_STOCK, "memory manager: no readable %s, launcher keep list not set up", w->c.mem_stock); return NULL; }
    jvalue_ref root = parse_object(text); g_free(text);
    if (!root) { log_once(w, L_STOCK_JSON, "memory manager: %s is not a JSON object, launcher keep list not set up", w->c.mem_stock); return NULL; }
    const char *key = jis_array(member(root, "KeepOnLaunchEx")) ? "KeepOnLaunchEx" : "KeepOnLaunch";
    jvalue_ref list = member(root, key);
    if (!all_strings(list)) {
        log_once(w, L_STOCK_LIST, "memory manager: %s has no usable KeepOnLaunchEx or KeepOnLaunch list, launcher keep list not set up", w->c.mem_stock);
        j_release(&root); return NULL;
    }
    if (!has_item(list, id)) jarray_append(list, jstring_create(id));
    char *out = g_strdup(jvalue_stringify(root));
    j_release(&root);
    return out;
}
static void file_apply(HCWatch *w, const char *id)
{
    char *ours = ours_path(w);
    int state = link_state(w, ours);
    if (state == 2) {
        if (!w->mem_foreign_logged) { w->mem_foreign_logged = TRUE; watch_log(w, "memory manager: %s exists and is not ours, left alone (the launcher keep list is not set)", w->c.mem_link); }
        goto done;
    }
    char *copy = build_copy(w, id);
    if (!copy) goto done;
    char *old = watch_read(ours);
    gboolean same = eq(old, copy);
    g_free(old);
    gboolean changed = FALSE;
    if (!same) {
        /* temp file in the same directory, renamed into place (write_atomic): memchute must never see a partial file */
        if (!write_atomic(ours, copy, strlen(copy))) { log_once(w, L_WRITE, "memory manager: cannot write %s", ours); g_free(copy); goto done; }
        changed = TRUE;
    }
    g_free(copy);
    if (state == 0) {
        if (symlink(ours, w->c.mem_link)) { log_once(w, L_LINK, "memory manager: cannot link %s: %s", w->c.mem_link, g_strerror(errno)); goto done; }
        changed = TRUE;
    }
    if (changed) { watch_log(w, "memory manager: %s keeps %s alive", w->c.mem_link, id); owe_restart(w); }
    g_clear_pointer(&w->mem_id, g_free); w->mem_id = g_strdup(id);
    restart_if_owed(w);
done:
    g_free(ours);
}
static void file_undo(HCWatch *w)
{
    char *ours = ours_path(w);
    gboolean changed = FALSE;
    if (link_state(w, ours) == 1 && !unlink(w->c.mem_link)) changed = TRUE;
    if (!unlink(ours)) changed = TRUE;
    if (changed) { watch_log(w, "memory manager: launcher keep list removed"); owe_restart(w); }
    restart_if_owed(w);
    g_free(ours);
}

/* ---- 10+: the volatile configd route ---- */

static gboolean config_get(HCWatch *w, jvalue_ref *list)
{
    jvalue_ref r = watch_call(w, CONFIG "getConfigs", "{\"configNames\":[\"" MEM_KEY "\"]}", 3);
    *list = NULL;
    if (!reply_ok(r)) { j_release(&r); return FALSE; }
    jvalue_ref configs = member(r, "configs"), value = member(configs, MEM_KEY);
    if (!jis_array(value)) value = member(configs, MEM_SHORT);
    gboolean ok = all_strings(value);
    if (ok) *list = jvalue_duplicate(value);
    j_release(&r); return ok;
}
/* A volatile write of value (NULL: JSON null, which puts back the layer's own value). */
static gboolean config_put(HCWatch *w, jvalue_ref value)
{
    jvalue_ref p = jobject_create(), configs = jobject_create();
    jobject_set(configs, key_buf(MEM_KEY), value ? jvalue_duplicate(value) : jnull());
    jobject_set(p, key_buf("configs"), configs);
    jobject_set(p, key_buf("volatile"), jboolean_create(true));
    jvalue_ref r = watch_call(w, CONFIG "setConfigs", jvalue_stringify(p), 5);
    gboolean ok = reply_ok(r);
    j_release(&r); j_release(&p);
    return ok;
}

/* The marker: every id we may have put on the list, one a line, written before the write it is for, so whatever configd ended up
 * holding is ours to take away. NULL when there is none. */
static char **mark_read(HCWatch *w)
{
    char *p = state_file(w, MEM_MARK), *text = watch_read(p); g_free(p);
    if (!text) return NULL;
    char **ids = g_strsplit_set(text, "\r\n ", -1);
    g_free(text);
    GPtrArray *keep = g_ptr_array_new();
    for (int i = 0; ids[i]; i++) if (*ids[i] && hc_app_id_ok(ids[i])) g_ptr_array_add(keep, g_strdup(ids[i]));
    g_strfreev(ids);
    if (!keep->len) { g_ptr_array_free(keep, TRUE); return NULL; }
    g_ptr_array_add(keep, NULL);
    return (char **)g_ptr_array_free(keep, FALSE);
}
static gboolean mark_has(char **marks, const char *id)
{
    for (int i = 0; marks && marks[i]; i++) if (eq(marks[i], id)) return TRUE;
    return FALSE;
}
static void mark_write(HCWatch *w, char **ids, const char *extra)
{
    GString *text = g_string_new(NULL);
    for (int i = 0; ids && ids[i]; i++) g_string_append_printf(text, "%s\n", ids[i]);
    if (extra && !mark_has(ids, extra)) g_string_append_printf(text, "%s\n", extra);
    char *p = state_file(w, MEM_MARK);
    if (text->len) write_atomic(p, text->str, text->len); else unlink(p);
    g_free(p); g_string_free(text, TRUE);
}
static gboolean marks_in(jvalue_ref list, char **marks)
{
    for (int i = 0; marks && marks[i]; i++) if (has_item(list, marks[i])) return TRUE;
    return FALSE;
}
/* list without the marked ids; plus `add` when given. A new array. */
static jvalue_ref list_with(jvalue_ref list, char **marks, const char *add)
{
    jvalue_ref next = jarray_create(NULL);
    for (ssize_t i = 0; i < jarray_size(list); i++) {
        jvalue_ref e = jarray_get(list, i);
        raw_buffer b = jstring_get_fast(e);
        char *s = g_strndup(b.m_str, b.m_len);
        gboolean skip = mark_has(marks, s);
        g_free(s);
        if (!skip) jarray_append(next, jvalue_duplicate(e));
    }
    if (add) jarray_append(next, jstring_create(add));
    return next;
}
/* Take our ids off the list configd holds now (current, which has at least one). First a volatile null, which puts the layer's own
 * value back; when that is not exactly what is left of current, another component's volatile value was in it, so current without
 * our ids is written (volatile) and read back. TRUE when our ids are off the list. */
static gboolean config_strip(HCWatch *w, jvalue_ref current, char **marks)
{
    jvalue_ref rest = list_with(current, marks, NULL), layer = NULL;
    gboolean ok = FALSE;
    if (!config_put(w, NULL) || !config_get(w, &layer)) {
        watch_log(w, "memory manager: could not clear the volatile %s, it stays until reboot", MEM_SHORT);
    } else if (marks_in(layer, marks)) {
        /* the layer itself lists it: not ours */
        watch_log(w, "memory manager: %s still lists it after clearing the volatile value (not ours)", MEM_SHORT);
        ok = TRUE;
    } else if (jvalue_equal(layer, rest)) ok = TRUE;
    else {
        jvalue_ref back = NULL;
        ok = config_put(w, rest) && config_get(w, &back) && jvalue_equal(back, rest);
        j_release(&back);
        if (!ok) watch_log(w, "memory manager: could not write %s back without our entry", MEM_SHORT);
    }
    j_release(&layer); j_release(&rest);
    return ok;
}
/* TRUE when configd was not ready (no usable read, a refused write, no read-back yet): worth trying again soon. */
static gboolean config_apply(HCWatch *w, const char *id)
{
    gboolean again = FALSE;
    jvalue_ref current = NULL;
    char **marks = mark_read(w);
    if (!config_get(w, &current)) {
        again = TRUE;
        if (!w->mem_read_logged) { w->mem_read_logged = TRUE; watch_log(w, "memory manager: configd gave no usable %s, launcher keep list not set (retried soon)", MEM_KEY); }
        goto done;
    }
    gboolean first = !w->mem_seen;
    w->mem_seen = TRUE;
    gboolean has = has_item(current, id), ours = mark_has(marks, id);
    if (has && !ours) {
        /* on the list already (the firmware's, or a persistent value): nothing of ours to add. A launcher we added before leaves. */
        if (!w->mem_foreign_logged) { w->mem_foreign_logged = TRUE; watch_log(w, "memory manager: %s already lists %s, nothing to add", MEM_SHORT, id); }
        if (marks_in(current, marks)) {
            if (config_strip(w, current, marks)) { mark_write(w, NULL, NULL); owe_restart(w); watch_log(w, "memory manager: the previous launcher is off %s", MEM_SHORT); }
            else again = TRUE;
        } else if (marks) mark_write(w, NULL, NULL);
        restart_if_owed(w);
        goto done;
    }
    if (has) { /* ours, in place (or written and not read back yet); other ids of ours still on the list leave it */
        GPtrArray *left = g_ptr_array_new();
        for (int i = 0; marks[i]; i++) if (!eq(marks[i], id) && has_item(current, marks[i])) g_ptr_array_add(left, marks[i]);
        gboolean ok = TRUE;
        if (left->len) {
            g_ptr_array_add(left, NULL);
            ok = config_strip(w, current, (char **)left->pdata);
            if (ok) owe_restart(w);
            else again = TRUE;
        }
        g_ptr_array_free(left, TRUE);
        if (ok) { char *only[] = {(char *)id, NULL}; mark_write(w, only, NULL); }
        g_clear_pointer(&w->mem_id, g_free); w->mem_id = g_strdup(id);
        restart_if_owed(w); goto done;
    }
    if (ours) {
        if (first) {
            /* the volatile value does not survive a reboot (or a configd restart): this is the boot apply, not a drop */
            watch_log(w, "memory manager: %s has no volatile value for %s yet, writing it", MEM_SHORT, id);
        } else if (w->mem_reapplies >= MEM_REAPPLY_MAX) {
            /* we wrote it and it is gone: configd selected its layers again. Bounded, never a loop. */
            if (!w->mem_loop_logged) { w->mem_loop_logged = TRUE; watch_log(w, "memory manager: configd keeps dropping the %s value, not applying again for now", MEM_SHORT); }
            goto done;
        } else {
            w->mem_reapplies++;
            watch_log(w, "memory manager: configd dropped our %s value, writing it again (%u)", MEM_SHORT, w->mem_reapplies);
        }
    }
    /* the current list without the launchers we added before (a changed choice), plus this one */
    jvalue_ref next = list_with(current, marks, id);
    mark_write(w, marks, id); /* intent first: whatever configd holds after this write is ours to take away */
    gboolean wrote = config_put(w, next), exact = FALSE, read = FALSE;
    if (wrote) {
        jvalue_ref back = NULL;
        read = config_get(w, &back);
        exact = read && jvalue_equal(back, next);
        j_release(&back);
    }
    if (!wrote) {
        again = TRUE;
        mark_write(w, marks, NULL); /* nothing was written: the marker is what it was */
        if (!marks) mark_write(w, NULL, NULL);
    } else if (exact) {
        char *only[] = {(char *)id, NULL};
        mark_write(w, only, NULL);
        watch_log(w, "memory manager: volatile %s now lists %s", MEM_SHORT, id);
        owe_restart(w);
        g_clear_pointer(&w->mem_id, g_free); w->mem_id = g_strdup(id);
        restart_if_owed(w);
    } else if (!read) {
        /* written, no read-back: the marker keeps both, the restart waits for the next look */
        again = TRUE; owe_restart(w);
    } else if (!w->mem_mask_logged) {
        /* a persistent value masks the volatile one: no fight, no restart; the marker still names it so a later undo clears it */
        w->mem_mask_logged = TRUE; watch_log(w, "memory manager: a volatile %s is not what configd reads back (masked), left as it is", MEM_SHORT);
    }
    j_release(&next);
done:
    j_release(&current); g_strfreev(marks);
    return again;
}
static void config_undo(HCWatch *w)
{
    char **marks = mark_read(w);
    if (!marks) { restart_if_owed(w); return; } /* an owed restart from an earlier run is still due */
    jvalue_ref current = NULL;
    if (!config_get(w, &current)) { watch_log(w, "memory manager: configd gave no usable %s, the volatile value stays until reboot", MEM_KEY); g_strfreev(marks); return; }
    gboolean done = TRUE;
    if (marks_in(current, marks)) {
        done = config_strip(w, current, marks);
        if (done) { watch_log(w, "memory manager: volatile %s cleared", MEM_SHORT); owe_restart(w); }
    }
    if (done) mark_write(w, NULL, NULL);
    restart_if_owed(w);
    j_release(&current); g_strfreev(marks);
}

/* ---- retry soon after boot, 10+ ---- */

static void retry_cancel(HCWatch *w)
{
    if (w->mem_retry_source) { g_source_remove(w->mem_retry_source); w->mem_retry_source = 0; }
}
static gboolean retry_fire(gpointer context)
{
    HCWatch *w = context;
    w->mem_retry_source = 0;
    if (w->status >= 0) return G_SOURCE_REMOVE;
    w->depth++;
    char *id = watch_choice(w);
    if (id) memory_follow(w, id, TRUE);
    g_free(id);
    w->depth--;
    if (w->ops.wake) w->ops.wake(w->ops.context);
    return G_SOURCE_REMOVE;
}
/* 10 s, 20 s, 40 s ... (mem_retry_ms doubling), at most mem_tries times per watcher run; the 300 s check goes on after that. */
static void retry_schedule(HCWatch *w)
{
    if (w->mem_retry_source || w->mem_tries >= w->c.mem_tries) return;
    unsigned delay = w->c.mem_retry_ms << w->mem_tries;
    w->mem_tries++;
    w->mem_retry_source = g_timeout_add(delay, retry_fire, w);
}

/* 10+ ignores the 4.0-9.x link: one left by a set that was upgraded is removed, ours only (a link into our state directory, our copy). */
static void old_route_cleanup(HCWatch *w)
{
    if (w->major < 10 || w->mem_old_done) return;
    w->mem_old_done = TRUE;
    char *ours = ours_path(w);
    gboolean removed = FALSE;
    if (link_state(w, ours) == 1 && !unlink(w->c.mem_link)) removed = TRUE;
    if (!unlink(ours)) removed = TRUE;
    if (removed) watch_log(w, "memory manager: webOS %d ignores %s; the link and copy left from an earlier release are removed", w->major, w->c.mem_link);
    g_free(ours);
}

/* ---- entry points ---- */

static gboolean supported(const HCWatch *w) { return w->c.keep_launcher && w->major >= 4; }

void memory_undo(HCWatch *w)
{
    if (!supported(w)) return;
    retry_cancel(w); old_route_cleanup(w);
    if (w->major <= 9) file_undo(w); else config_undo(w);
    g_clear_pointer(&w->mem_id, g_free); g_clear_pointer(&w->mem_confirmed, g_free);
    w->mem_reapplies = 0;
}
void memory_follow(HCWatch *w, const char *id, gboolean force)
{
    if (!supported(w)) return;
    if (!id || !hc_app_id_ok(id) || stock_id(id) || eq(id, HC_APP_ID)) { memory_undo(w); return; }
    if (!force && eq(w->mem_id, id) && !owed_actionable(w)) return;
    /* only an app SAM confirms as installed is added: an id that is added and then taken away costs two restarts */
    if (!eq(w->mem_confirmed, id)) {
        if (app_state(w, id) != 1) return;
        g_free(w->mem_confirmed); w->mem_confirmed = g_strdup(id);
    }
    if (w->major <= 9) { file_apply(w, id); return; }
    old_route_cleanup(w);
    if (config_apply(w, id)) retry_schedule(w);
    else if (eq(w->mem_id, id)) w->mem_tries = 0;
}
void memory_free(HCWatch *w) { retry_cancel(w); restart_timer_cancel(w); g_free(w->mem_id); g_free(w->mem_confirmed); w->mem_id = w->mem_confirmed = NULL; }
