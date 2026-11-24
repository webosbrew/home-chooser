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
/* Another app wrote keyFilters persistently (or replaced the list) while Home Chooser's filter is wanted: say so once,
 * and leave it (clearing needs the owner, in the settings app). */
static void override_seen(HCWatch *w)
{
    if (w->override_toasted) return;
    /* The boot apply may have told the owner already (the marker says so): once per takeover. */
    if (hc_key_override_state(w->c.state_dir) == 2) { w->override_toasted = TRUE; w->override_pending = FALSE; return; }
    hc_key_mark_override(w->c.state_dir, 1);
    if (!w->override_pending) watch_log(w, "Home key: another app has taken over keyFilters; not clearing it");
    /* Only a delivered notice counts: the notification service may not be up yet (it answered nothing for minutes
     * after a boot), so an undelivered one is tried again on the short 'N' timer. */
    if (watch_notify(w, HC_OVERRIDE_TOAST, FALSE)) {
        w->override_toasted = TRUE; w->override_pending = FALSE; hc_key_mark_override(w->c.state_dir, 2);
    } else if (!w->override_pending) {
        w->override_pending = TRUE; w->override_tries = 0;
        watch_log(w, "Home key: the notice was not delivered, trying again shortly");
    }
}
#define OVERRIDE_NOTICE_TRIES 30
void key_notice_retry(HCWatch *w)
{
    if (w->override_toasted || w->status >= 0) return;
    /* the boot apply could not tell the owner (marker 3): the watcher takes that over */
    if (!w->override_pending && hc_key_override_state(w->c.state_dir) == 3) { w->override_pending = TRUE; w->override_tries = 0; }
    if (!w->override_pending) return;
    /* the takeover is over (ours back, or stock restored: the marker is gone), or too many tries */
    if (!hc_key_override_state(w->c.state_dir) || ++w->override_tries > OVERRIDE_NOTICE_TRIES) { w->override_pending = FALSE; return; }
    w->notify_quick = TRUE; /* one short try: this runs on a 10 s timer and must not hold the watcher */
    override_seen(w);
    w->notify_quick = FALSE;
}
int key_refresh(HCWatch *w, gboolean stock)
{
    if (stock) { w->saw_mine = FALSE; w->override_pending = FALSE; } /* stock is what is wanted: nothing of ours to lose */
    if (w->key_mode == HC_KEY_NONE) return 0;
    if (w->ops.key) return w->ops.key(stock, w->ops.context);
    HCKeyOps ops = {w->c.state_dir, w->c.stock_js, w->c.release_file, w->ops.call, w->ops.context, 30, 1000, NULL};
    GString *out = g_string_new(NULL);
    int rc = hc_key_run(&ops, "refresh", stock, out);
    watch_log(w, "Home key refresh%s: %s", stock ? " stock" : "", out->str);
    /* Baked means the filter really redirects: rc 0 with active=no (unknown stock shape, unreadable
     * stock file) is the stock list, so nothing is baked and the home gate stays unmounted. */
    if (!stock && strstr(out->str, "reason=persistent-override")) override_seen(w);
    if (!rc && !stock && !strstr(out->str, "active=yes")) { rc = 1; g_clear_pointer(&w->baked, g_free); }
    g_string_free(out, TRUE); return rc;
}
void sync_baked(HCWatch *w, const char *id)
{
    if (eq(w->baked, id) || app_state(w, id) != 1) return;
    if (!key_refresh(w, FALSE)) { g_free(w->baked); w->baked = g_strdup(id); }
}
#define KEY_CONFIG "com.webos.surfacemanager.keyFilters"
#define KEY_GET "luna://com.webos.service.config/getConfigs"
#define KEY_REAPPLY_GAP_S 20
#define KEY_REAPPLY_MAX 5
#define KEY_QUIET_S 600
/* TRUE only for a list that plainly is the firmware's own: a stock systemUi entry and none of ours. Anything
 * else (no list, an error reply, our file present, no systemUi entry) is not a change to put right. */
static gboolean list_is_stock(HCWatch *w, jvalue_ref reply, gboolean *mine_out, gboolean *listed_out)
{
    jvalue_ref list = member(member(reply, "configs"), KEY_CONFIG);
    if (!jis_array(list)) return FALSE;
    if (listed_out) *listed_out = jarray_size(list) > 0;
    char *ours = g_build_filename(w->c.state_dir, "systemUi-", NULL), *ours40 = g_build_filename(w->c.state_dir, "homekey40-", NULL);
    gboolean stock = FALSE, mine = FALSE;
    for (ssize_t i = 0; i < jarray_size(list); i++) {
        char *file = get_string(jarray_get(list, i), "file");
        if (g_str_has_prefix(file ? file : "", ours) || g_str_has_prefix(file ? file : "", ours40)) mine = TRUE;
        if (eq(file, "/usr/lib/qt5/qml/KeyFilters/systemUi.js") || eq(file, "/usr/lib/qml/KeyFilters/systemUi.js")) stock = TRUE;
        g_free(file);
    }
    if (mine_out) *mine_out = mine;
    g_free(ours); g_free(ours40); return stock && !mine;
}
void key_follow(HCWatch *w, const char *text)
{
    if (w->key_mode == HC_KEY_NONE || w->status >= 0) return;
    jvalue_ref reply;
    if (text) reply = parse_object(text);
    else reply = watch_call(w, KEY_GET, "{\"configNames\":[\"" KEY_CONFIG "\"]}", 3);
    gboolean mine = FALSE, listed = FALSE, stock = list_is_stock(w, reply, &mine, &listed);
    j_release(&reply);
    if (mine) {
        w->saw_mine = TRUE; w->override_pending = FALSE;
        if (w->override_toasted || hc_key_override_state(w->c.state_dir)) { w->override_toasted = FALSE; hc_key_mark_override(w->c.state_dir, 0); }
        return;
    }
    if (listed && !stock) {
        /* A list with none of ours and no stock systemUi entry: another app wrote it. Needs the filter to have been
         * ours (baked by a refresh, or seen in a reply) and a choice still wanted; the filter may have been applied by
         * the app, not by this watcher, so `baked` alone is not the test. */
        char *wanted = watch_choice(w);
        if (!wanted || !*wanted || stock_id(wanted)) { w->saw_mine = FALSE; w->override_pending = FALSE; } /* no filter of ours is wanted */
        gboolean ours = (w->baked && eq(wanted, w->baked)) || (w->saw_mine && wanted && *wanted);
        g_free(wanted);
        if (ours) override_seen(w);
        return;
    }
    if (!stock || !w->baked) return; /* the stock re-apply is only for a filter this watcher baked */
    /* Only the choice the baked filter was made for: a changed or removed choice has its own path. */
    char *want = watch_choice(w);
    gboolean same = eq(want, w->baked); g_free(want);
    if (!same) return;
    gint64 now = watch_now(w);
    if (w->key_last && now - w->key_last > KEY_QUIET_S) { w->key_reapplies = 0; w->key_logged = FALSE; }
    /* configd may re-select its layers more than once, or fight us: bounded, never a loop. */
    if (w->key_reapplies >= KEY_REAPPLY_MAX || (w->key_last && now - w->key_last < KEY_REAPPLY_GAP_S)) {
        if (!w->key_logged) { w->key_logged = TRUE; watch_log(w, "Home key: configd keeps putting the stock keyFilters back, not applying again for now"); }
        return;
    }
    w->key_last = now; w->key_reapplies++; w->key_logged = FALSE;
    watch_log(w, "Home key: configd put the stock keyFilters back, applying the filter again (%u)", w->key_reapplies);
    key_refresh(w, FALSE);
    gate_sync(w);
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
/* The stock Home to put back on 4.5 and 5.x to 9.x (4.0 has no record, see revert_platform): the one the record already names, else the one the owner last used
 * (the `stock` file, written by the app when it switched away from it), each only when installed; else
 * fullhome when installed, else com.webos.app.home. */
static char *stock_to_restore(HCWatch *w, const char *found)
{
    char *saved = NULL;
    if (stock_id(found) && (eq(found, STOCK) || app_state(w, found) == 1)) return g_strdup(found);
    char *path = g_build_filename(w->c.state_dir, "stock", NULL), *text = watch_read(path);
    g_free(path);
    if (text) saved = g_strdup(g_strstrip(text));
    g_free(text);
    if (stock_id(saved) && (eq(saved, STOCK) || app_state(w, saved) == 1)) return saved;
    g_free(saved);
    return g_strdup(app_state(w, FULLHOME) == 1 ? FULLHOME : STOCK);
}
static gboolean revert_platform(HCWatch *w)
{
    if (filter_only(w)) { watch_log(w, "webOS 4.0: no settings record to put back, the Home key filter is restored on its own"); return TRUE; }
    watch_log(w, "putting the platform back to stock");
    if (w->major <= 9) { /* 4.5 (not filter-only) shares the 5.x record mechanism; null would leave Home dead */
        char *found = current_home(w), *stock = stock_to_restore(w, found);
        gboolean matches = eq(found, stock);
        if (matches) watch_log(w, "home is already %s", stock);
        else watch_log(w, "restoring %s", stock);
        gboolean ok = matches || merged_write(w, stock);
        g_free(found); g_free(stock);
        return ok;
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
    memory_undo(w); /* best effort: a memchute that keeps a launcher exempt is no reason to hold the uninstall */
    const char *names[] = {"home", "previous", "stock", "keep-launcher", "keep-launcher.tmp", "keep-restart", "keep-restart.tmp", "memorymanager-conf.json", "memorymanager-conf.json.tmp", "homekey", "hook", "hook-stale", "boot", "keyfilters-stock.json", "keyfilter.lock", "override", NULL};
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
        if (w->status >= 0) goto done;
        /* A confirmed removal is reverted before Home Chooser is looked at again: when Home Chooser
         * went in the meantime, its own confirmation wait must not keep the platform naming a
         * removed app (idleApp, the record's home: a black screen when the last app closes). */
        gboolean gone = app_state(w, want) == -1;
        if (gone) {
            watch_log(w, "%s is not installed, stock Home in use until it comes back", want);
            g_free(w->missing); w->missing = g_strdup(want);
            revert_platform(w); key_refresh(w, TRUE);
            g_clear_pointer(&w->baked, g_free);
            gate_sync(w); /* 6-9: stock Home is the real binary again, before the wait below */
        }
        check_hc(w);
        if (w->status >= 0) goto done;
        if (gone && why != 's') notify_removed(w, want);
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
    if (w->missing) memory_undo(w); else memory_follow(w, want, why == 's' || why == 'T');
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
