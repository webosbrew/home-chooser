/* SPDX-License-Identifier: Apache-2.0 */
/* Keep the chosen launcher out of the memory manager's launch-time closes (docs/service.md, "Keeping the launcher
 * alive"). memchute reads its exempt lists once, at start:
 *   4.0 to 9.x: /home/root/memorymanager-conf.json in place of /etc/palm/memorymanager-conf.json. Ours is a symlink there
 *               to a copy of the stock file with the launcher added to KeepOnLaunchEx (KeepOnLaunch on 4.0, which has no Ex).
 *   10+:        memchute ignores that link and takes the lists from configd: a volatile KeepOnLaunchEx, read back exactly.
 * memchute is restarted only when what it would read changed. */
#include "internal.h"
#include "proc.h"
#include <errno.h>
#include <sys/stat.h>

#define MEM_FILE "memorymanager-conf.json"
#define MEM_MARK "keep-launcher"
#define MEM_KEY "com.webos.memorymanager.KeepOnLaunchEx"
#define MEM_SHORT "KeepOnLaunchEx"
#define CONFIG "luna://com.webos.service.config/"
#define MEM_REAPPLY_MAX 5

static char *ours_path(HCWatch *w) { return g_build_filename(w->c.state_dir, MEM_FILE, NULL); }
static char *mark_path(HCWatch *w) { return g_build_filename(w->c.state_dir, MEM_MARK, NULL); }

static int run_restart(HCWatch *w)
{
    if (w->ops.restart) return w->ops.restart(w->ops.context);
    const char *const systemctl[] = {"/bin/systemctl", "restart", "memchute", NULL};
    const char *const upstart[] = {"/sbin/initctl", "restart", "memchute", NULL};
    const char *const upstart_start[] = {"/sbin/initctl", "start", "memchute", NULL};
    GString *out = g_string_new(NULL), *err = g_string_new(NULL);
    int rc;
    if (g_file_test("/bin/systemctl", G_FILE_TEST_IS_EXECUTABLE) || g_file_test("/usr/bin/systemctl", G_FILE_TEST_IS_EXECUTABLE)) {
        const char *const alt[] = {"/usr/bin/systemctl", "restart", "memchute", NULL};
        rc = run_argv(g_file_test("/bin/systemctl", G_FILE_TEST_IS_EXECUTABLE) ? systemctl : alt, 30, out, err, NULL);
    } else {
        /* upstart (4.x): `restart` fails when the job is not running, then `start` brings it up */
        rc = run_argv(upstart, 30, out, err, NULL);
        if (rc) { g_string_truncate(out, 0); g_string_truncate(err, 0); rc = run_argv(upstart_start, 30, out, err, NULL); }
    }
    if (rc) watch_log(w, "memchute restart failed (%d): %s%s", rc, out->str, err->str);
    g_string_free(out, TRUE); g_string_free(err, TRUE); return rc;
}
/* Restart now when one is owed (a change made, or an earlier restart that failed). */
static void restart_if_owed(HCWatch *w)
{
    if (!w->mem_owed) return;
    watch_log(w, "restarting memchute so the launcher keep list takes effect");
    if (!run_restart(w)) w->mem_owed = FALSE;
}

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
/* The stock file with id in KeepOnLaunchEx (KeepOnLaunch when there is no Ex list); NULL with a log line when it cannot be made. */
static char *build_copy(HCWatch *w, const char *id)
{
    char *text = watch_read(w->c.mem_stock);
    if (!text) { watch_log(w, "memory manager: no readable %s, launcher keep list not set up", w->c.mem_stock); return NULL; }
    jvalue_ref root = parse_object(text); g_free(text);
    if (!root) { watch_log(w, "memory manager: %s is not a JSON object, launcher keep list not set up", w->c.mem_stock); return NULL; }
    const char *key = jis_array(member(root, "KeepOnLaunchEx")) ? "KeepOnLaunchEx" : "KeepOnLaunch";
    jvalue_ref list = member(root, key);
    if (!all_strings(list)) {
        watch_log(w, "memory manager: %s has no usable KeepOnLaunchEx or KeepOnLaunch list, launcher keep list not set up", w->c.mem_stock);
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
        if (!write_atomic(ours, copy, strlen(copy))) { watch_log(w, "memory manager: cannot write %s", ours); g_free(copy); goto done; }
        changed = TRUE;
    }
    g_free(copy);
    if (state == 0) {
        if (symlink(ours, w->c.mem_link)) { watch_log(w, "memory manager: cannot link %s: %s", w->c.mem_link, g_strerror(errno)); goto done; }
        changed = TRUE;
    }
    if (changed) { watch_log(w, "memory manager: %s keeps %s alive", w->c.mem_link, id); w->mem_owed = TRUE; }
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
    if (changed) { watch_log(w, "memory manager: launcher keep list removed"); w->mem_owed = TRUE; }
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
/* A volatile write of value (NULL: JSON null, which puts back the layer's own value); the list read back must equal want exactly,
 * unless want is NULL. */
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
static char *mark_read(HCWatch *w)
{
    char *p = mark_path(w), *text = watch_read(p); g_free(p);
    if (text) g_strstrip(text);
    if (text && !*text) g_clear_pointer(&text, g_free);
    return text;
}
static void mark_write(HCWatch *w, const char *id)
{
    char *p = mark_path(w);
    if (id) write_atomic(p, id, strlen(id)); else unlink(p);
    g_free(p);
}
static void config_apply(HCWatch *w, const char *id)
{
    jvalue_ref current = NULL;
    char *mark = mark_read(w);
    if (!config_get(w, &current)) {
        if (!w->mem_read_logged) { w->mem_read_logged = TRUE; watch_log(w, "memory manager: configd gave no usable %s, launcher keep list not set (retried later)", MEM_KEY); }
        goto done;
    }
    gboolean has = has_item(current, id), ours = eq(mark, id);
    if (has && !ours) {
        /* on the list already (the firmware's, or a persistent value): nothing of ours to add or to undo */
        if (!w->mem_foreign_logged) { w->mem_foreign_logged = TRUE; watch_log(w, "memory manager: %s already lists %s, nothing to add", MEM_SHORT, id); }
        goto done;
    }
    if (has) { /* ours, still in place */
        g_clear_pointer(&w->mem_id, g_free); w->mem_id = g_strdup(id);
        restart_if_owed(w); goto done;
    }
    if (ours) {
        /* we wrote it and it is gone: configd selected its layers again. Bounded, never a loop. */
        if (w->mem_reapplies >= MEM_REAPPLY_MAX) {
            if (!w->mem_loop_logged) { w->mem_loop_logged = TRUE; watch_log(w, "memory manager: configd keeps dropping the %s value, not applying again for now", MEM_SHORT); }
            goto done;
        }
        w->mem_reapplies++;
        watch_log(w, "memory manager: configd dropped our %s value, writing it again (%u)", MEM_SHORT, w->mem_reapplies);
    }
    /* the current list without the launcher we added before (a changed choice), plus this one */
    jvalue_ref next = jarray_create(NULL);
    for (ssize_t i = 0; i < jarray_size(current); i++) {
        jvalue_ref e = jarray_get(current, i);
        raw_buffer b = jstring_get_fast(e);
        if (mark && strlen(mark) == b.m_len && !strncmp(b.m_str, mark, b.m_len)) continue;
        jarray_append(next, jvalue_duplicate(e));
    }
    jarray_append(next, jstring_create(id));
    gboolean wrote = config_put(w, next), exact = FALSE;
    if (wrote) {
        jvalue_ref back = NULL;
        exact = config_get(w, &back) && jvalue_equal(back, next);
        j_release(&back);
    }
    if (!exact) {
        /* a persistent value masks the volatile one, or the write was refused: do not fight it */
        if (!w->mem_mask_logged) { w->mem_mask_logged = TRUE; watch_log(w, "memory manager: a volatile %s is not what configd reads back (%s), left as it is", MEM_SHORT, wrote ? "masked" : "refused"); }
    } else {
        mark_write(w, id);
        watch_log(w, "memory manager: volatile %s now lists %s", MEM_SHORT, id);
        w->mem_owed = TRUE;
        g_clear_pointer(&w->mem_id, g_free); w->mem_id = g_strdup(id);
        restart_if_owed(w);
    }
    j_release(&next);
done:
    j_release(&current); g_free(mark);
}
static void config_undo(HCWatch *w)
{
    char *mark = mark_read(w);
    if (!mark) return;
    jvalue_ref current = NULL;
    if (!config_get(w, &current)) { watch_log(w, "memory manager: configd gave no usable %s, the volatile value stays until reboot", MEM_KEY); g_free(mark); return; }
    if (has_item(current, mark)) {
        if (!config_put(w, NULL)) { watch_log(w, "memory manager: could not clear the volatile %s, it stays until reboot", MEM_SHORT); goto done; }
        jvalue_ref back = NULL;
        gboolean still = config_get(w, &back) && has_item(back, mark);
        j_release(&back);
        if (still) watch_log(w, "memory manager: %s still lists %s after clearing the volatile value (not ours)", MEM_SHORT, mark);
        else { watch_log(w, "memory manager: volatile %s cleared", MEM_SHORT); w->mem_owed = TRUE; }
    }
    mark_write(w, NULL);
    restart_if_owed(w);
done:
    j_release(&current); g_free(mark);
}

/* ---- entry points ---- */

static gboolean supported(const HCWatch *w) { return w->c.keep_launcher && w->major >= 4; }

void memory_undo(HCWatch *w)
{
    if (!supported(w)) return;
    if (w->major <= 9) file_undo(w); else config_undo(w);
    g_clear_pointer(&w->mem_id, g_free);
    w->mem_reapplies = 0;
}
void memory_follow(HCWatch *w, const char *id, gboolean force)
{
    if (!supported(w)) return;
    if (!id || !hc_app_id_ok(id) || stock_id(id) || eq(id, HC_APP_ID)) { memory_undo(w); return; }
    if (!force && eq(w->mem_id, id) && !w->mem_owed) return;
    if (w->major <= 9) file_apply(w, id); else config_apply(w, id);
}
void memory_free(HCWatch *w) { g_free(w->mem_id); w->mem_id = NULL; }
