/* SPDX-License-Identifier: Apache-2.0 */
#include "internal.h"
#include <sys/stat.h>

int app_state(HCWatch *w, const char *id)
{
    jvalue_ref r = watch_id_call(w, SAM "getAppInfo", id, 10);
    bool value;
    int state = jboolean_get(member(r, "returnValue"), &value) == CONV_OK ? (value ? 1 : -1) : 0;
    j_release(&r); return state;
}
static gboolean on_disk(HCWatch *w, const char *id)
{
    if (!id || strspn(id, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-") != strlen(id)) return FALSE;
    char **dirs = g_strsplit(w->c.install_dirs, " ", -1);
    gboolean found = FALSE;
    for (int i = 0; dirs[i] && !found; i++) {
        if (!*dirs[i]) continue;
        char *p = g_build_filename(dirs[i], id, "appinfo.json", NULL);
        found = g_file_test(p, G_FILE_TEST_IS_REGULAR); g_free(p);
    }
    g_strfreev(dirs); return found;
}
int key_refresh(HCWatch *w, gboolean stock)
{
    if (w->major < 6 || w->major > 9) return 0;
    if (w->ops.key) return w->ops.key(stock, w->ops.context);
    HCKeyOps ops = {w->c.state_dir, w->c.stock_js, w->c.osrelease, w->ops.call, w->ops.context, 30, 1000};
    GString *out = g_string_new(NULL);
    int rc = hc_key_run(&ops, "refresh", stock, out);
    watch_log(w, "Home key refresh%s: %s", stock ? " stock" : "", out->str);
    g_string_free(out, TRUE); return rc;
}
void sync_baked(HCWatch *w, const char *id)
{
    if (eq(w->baked, id) || app_state(w, id) != 1) return;
    if (!key_refresh(w, FALSE)) { g_free(w->baked); w->baked = g_strdup(id); }
}
static gboolean remove_home(HCWatch *w)
{
    char *id = current_home(w);
    if (!id) { watch_log(w, "the record has no home, nothing to remove"); return TRUE; }
    g_free(id);
    if (!merged_write(w, NULL)) return FALSE;
    id = current_home(w);
    gboolean ok = !id;
    if (id) watch_log(w, "home is still '%s' after writing it as null", id);
    else watch_log(w, "home cleared (null) in the record");
    g_free(id); return ok;
}
static gboolean revert_platform(HCWatch *w)
{
    watch_log(w, "putting the platform back to stock");
    if (w->major >= 5 && w->major <= 9) {
        const char *stock = app_state(w, FULLHOME) == 1 ? FULLHOME : STOCK;
        char *found = current_home(w);
        gboolean matches = eq(found, stock); g_free(found);
        if (matches) watch_log(w, "home is already %s", stock);
        return matches || merged_write(w, stock);
    }
    drop_override(w); return remove_home(w);
}
static gboolean hc_gone(HCWatch *w)
{
    return !g_file_test(w->c.app_dir, G_FILE_TEST_IS_DIR) || app_state(w, HC_APP_ID) == -1;
}
static void restore_all(HCWatch *w)
{
    watch_log(w, "Home Chooser is uninstalled, restoring the stock Home");
    if (watch_access(w, "remove")) {
        hc_watch_stop(w, 1, "could not remove launcher access, restore pending"); return;
    }
    if (!revert_platform(w)) { hc_watch_stop(w, 1, "could not restore stock Home, restore pending"); return; }
    if (key_refresh(w, TRUE)) { hc_watch_stop(w, 1, "could not restore stock keyFilters, restore pending"); return; }
    if (gate_apply(w, NULL)) { hc_watch_stop(w, 1, "could not remove the home gate, restore pending"); return; }
    if (w->status >= 0) return;
    const char *names[] = {"home", "homekey", "hook", "hook-stale", "boot", "keyfilters-stock.json", "keyfilter.lock", NULL};
    for (int i = 0; names[i]; i++) {
        char *p = g_build_filename(w->c.state_dir, names[i], NULL);
        watch_remove_tree(p); g_free(p);
    }
    struct stat st;
    if (!lstat(w->c.init_link, &st) && S_ISLNK(st.st_mode)) unlink(w->c.init_link);
    rmdir(w->c.state_dir);
    watch_notify(w, "Home Chooser was removed. The Home button opens LG Home again.", FALSE);
    hc_watch_stop(w, 0, "stock Home restored, exiting");
}
void check_hc(HCWatch *w)
{
    if (w->status >= 0 || !hc_gone(w)) return;
    watch_wait(w, w->c.confirm_ms);
    if (w->status >= 0) return;
    if (hc_gone(w)) restore_all(w);
    else watch_log(w, "Home Chooser is back, not removing anything");
}
void check_apps(HCWatch *w, char why)
{
    check_hc(w);
    if (w->status >= 0) return;
    char *want = watch_choice(w);
    if (!want) { hc_watch_stop(w, 0, "choice file is gone, exiting"); return; }
    if (w->missing && !eq(w->missing, want)) g_clear_pointer(&w->missing, g_free);
    int st = app_state(w, want);
    if (st == -1 && why == 's' && on_disk(w, want)) {
        watch_log(w, "getAppInfo does not know %s yet but its files are installed, leaving things as they are", want);
        st = 0;
    }
    if (st == -1 && !eq(w->missing, want)) {
        watch_wait(w, w->c.confirm_ms);
        check_hc(w);
        if (w->status >= 0) goto done;
        if (app_state(w, want) == -1) {
            watch_log(w, "%s is not installed, stock Home in use until it comes back", want);
            g_free(w->missing); w->missing = g_strdup(want);
            revert_platform(w); key_refresh(w, TRUE);
            g_clear_pointer(&w->baked, g_free);
            if (why != 's') notify_removed(w, want);
        }
    } else if (st == 1) {
        if (w->missing) {
            watch_log(w, "%s is installed again, applying the choice", want);
            g_clear_pointer(&w->missing, g_free);
            w->last_write = 0; w->streak = 0;
            char *found = current_home(w);
            if (!eq(found, want)) apply_record(w, want);
            g_free(found); check_override(w, want);
            if (!key_refresh(w, FALSE)) { g_free(w->baked); w->baked = g_strdup(want); }
        } else if (why == 's') {
            if (!key_refresh(w, FALSE)) { g_free(w->baked); w->baked = g_strdup(want); }
        } else sync_baked(w, want);
    } else if (!st) watch_log(w, "getAppInfo did not answer for %s, leaving things as they are", want);
    if (w->status >= 0) goto done;
    preload_follow(w, want, why);
    if (why != 'T') close_foreground(w);
    gate_sync(w);
done:
    g_free(want);
}

static gboolean leftover_gone(HCWatch *w, const char *id)
{
    if (!id || !*id || stock_id(id)) return FALSE;
    int st = app_state(w, id);
    if (!st) watch_log(w, "getAppInfo did not answer for %s, leaving things as they are", id);
    if (st != -1) return FALSE;
    if (on_disk(w, id)) {
        watch_log(w, "getAppInfo does not know %s yet but its files are installed, leaving things as they are", id);
        return FALSE;
    }
    watch_wait(w, w->c.confirm_ms);
    return w->status < 0 && app_state(w, id) == -1 && !on_disk(w, id);
}
void repair_leftover(HCWatch *w)
{
    char *record = current_home(w), *override = NULL, *idle = NULL;
    if (w->major >= 10) {
        jvalue_ref idle_app = NULL;
        override_read(w, &override, &idle_app);
        idle = idle_app_of(idle_app); j_release(&idle_app);
    }
    /* Each distinct id is confirmed once: the record's home, the override's home, its idleApp. */
    gboolean gone_record = leftover_gone(w, record);
    gboolean gone_home = override && (eq(record, override) ? gone_record : leftover_gone(w, override));
    gboolean gone_idle = idle && (eq(idle, record) ? gone_record : eq(idle, override) ? gone_home : leftover_gone(w, idle));
    gboolean gone_override = gone_home || gone_idle;
    if (w->status >= 0) goto done;
    if (!gone_record && !gone_override) { watch_log(w, "no stored choice and nothing to repair"); goto done; }
    char *choice = watch_choice(w);
    if (choice) { g_free(choice); watch_log(w, "a choice was stored meanwhile, not repairing"); goto done; }
    watch_log(w, "no stored choice, but the %s names %s, which is not installed: a leftover",
        gone_record ? "home" : gone_home ? "profile override's home" : "profile override's idleApp",
        gone_record ? record : gone_home ? override : idle);
    if (gone_override && w->major >= 10) drop_override(w);
    if (gone_record) {
        char *found = current_home(w);
        if (eq(found, record)) {
            if (w->major >= 10) remove_home(w);
            else revert_platform(w);
        } else watch_log(w, "home changed meanwhile, record left alone");
        g_free(found);
    }
    key_refresh(w, TRUE);
done:
    g_free(record); g_free(override); g_free(idle);
}
