/* SPDX-License-Identifier: Apache-2.0 */
#ifndef HC_WATCH_INTERNAL_H
#define HC_WATCH_INTERNAL_H
#include "watcher.h"
#include "json.h"
#include "fs.h"
#include "policy.h"
#include "homekey.h"
#include <string.h>
#include <unistd.h>

#define SETTINGS "luna://com.webos.settingsservice/"
#define SAM "luna://com.webos.applicationManager/"
#define PROFILE "luna://com.webos.service.systemprofile/"
#define PRELOAD "luna://com.webos.service.preloadmanager/"
#define NOTIFICATION "luna://com.webos.notification/"
#define RECORD_GET "{\"category\":\"general\",\"keys\":[\"defaultApps\"]}"
#define STOCK HC_STOCK_HOME_ID
#define FULLHOME "com.webos.app.fullhome"

struct HCWatch {
    HCWatchConfig c;
    HCWatchOps ops;
    int major, status, depth;
    HCKeyMode key_mode; /* how the Home key is redirected on this firmware (6-9 patched copy, 4.0 inserted entry, else none) */
    gboolean early_exit;
    char *choice_file, *missing, *baked, *title_id, *title;
    char *close_choice;
    gboolean close_done, close_launched, relaunch_logged;
    int window; /* 0 unknown, 1 card, 2 overlay */
    gint64 last_write;
    unsigned streak;
    int preload_state; /* 0 unknown, 1 on, 2 off */
    gboolean preload_ready;
    unsigned preload_source, preload_attempts, preload_generation;
    GMainLoop *waiting;
    gboolean gate_active, gate_released, gate_pending; /* pending: stock Home close unconfirmed */
    unsigned gate_source, gate_attempts, gate_settle_source;
    gint64 gate_closed_us; /* wall clock of the close that armed the settle check */
    unsigned input_source, input_attempts; /* 10+: the last-input launch after boot (input.c) */
    /* previous.c: launchers to close once the change of Home is safe; the hold while Home Chooser is on screen */
    GPtrArray *prev_ids;
    gboolean prev_hold, only_close; /* only_close: no choice stored, running only to close prev_ids */
    char *prev_front, *prev_logged;
    gint64 prev_mtime;
    gboolean gate_held_logged;
    char *gate_wait_logged; /* 6-9: the overlay launcher whose "close waits" line was logged (gate.c) */
    gboolean gate_waiting; /* the stock Home close waits for a full-screen app beneath an overlay launcher */
    /* restore.c key_follow: the stock list configd put back after we baked; a bounded, rate-limited re-apply */
    gint64 key_last;
    unsigned key_reapplies;
    gboolean key_logged;
    gboolean saw_mine; /* a keyFilters reply showed our list: the filter is ours to lose, baked or not */
    gboolean override_pending; /* the takeover notice was not delivered yet: retried on a short timer (key_notice_retry) */
    unsigned override_tries;
    gboolean notify_quick; /* notify.c: one short try, no retries (the timer-driven notice) */
    gboolean override_toasted; /* the 'another app took over the Home button' toast was shown for this takeover */
    /* memory.c: the launcher kept on memchute's keep list */
    char *mem_id;
    gboolean mem_owed, mem_foreign_logged, mem_read_logged, mem_mask_logged, mem_loop_logged; /* mem_owed: a memchute restart is due */
    unsigned mem_reapplies, mem_tries, mem_retry_source;
    gboolean mem_old_done; /* 10+: the 4.0-9.x link and copy were looked for */
};
static inline gboolean eq(const char *a, const char *b) { return g_strcmp0(a, b) == 0; }
static inline gboolean stock_id(const char *id) { return eq(id, STOCK) || eq(id, FULLHOME); }
static inline jvalue_ref member(jvalue_ref o, const char *key) { return jobject_get(o, key_buf(key)); }
void watch_log(HCWatch *w, const char *format, ...) G_GNUC_PRINTF(2, 3);
char *watch_read(const char *path);
char *watch_choice(HCWatch *w);
void watch_wait(HCWatch *w, unsigned ms);
gint64 watch_now(HCWatch *w);
jvalue_ref watch_call(HCWatch *w, const char *uri, const char *payload, int timeout);
jvalue_ref watch_id_call(HCWatch *w, const char *method, const char *id, int timeout);
char *record_home(jvalue_ref reply);
char *current_home(HCWatch *w);
gboolean merged_write(HCWatch *w, const char *id);
gboolean apply_record(HCWatch *w, const char *id);
/* The override file's defaultApps home and its idleApp object (a copy); either may come back NULL. */
void override_read(HCWatch *w, char **home, jvalue_ref *idle);
/* idleApp.appId when idleApp names an app (support not false), else NULL. */
char *idle_app_of(jvalue_ref idle);
/* getAppInfo's defaultWindowType: 2 overlay, 1 full-screen, 0 unknown id, empty id or no answer. */
int window_type(HCWatch *w, const char *id);
void check_override(HCWatch *w, const char *id);
void drop_override(HCWatch *w);
void reconcile(HCWatch *w, const char *id);
/* 4.0 has no settings record to keep: the Home key filter is the whole redirect. */
static inline gboolean filter_only(const HCWatch *w) { return w->key_mode == HC_KEY_INSERT; }
int app_state(HCWatch *w, const char *id); /* 1 yes, 0 unknown, -1 no */
int key_refresh(HCWatch *w, gboolean stock);
void sync_baked(HCWatch *w, const char *id);
/* 6-9: a keyFilters reply (subscription event or poll, `text`) showing the stock list while we think the filter is baked:
 * apply it again, once per change. NULL text polls configd. */
void key_follow(HCWatch *w, const char *text);
/* The 'N' timer: a takeover notice that was not delivered is tried again, a limited number of times. */
void key_notice_retry(HCWatch *w);
void check_apps(HCWatch *w, char why);
void check_hc(HCWatch *w);
void repair_leftover(HCWatch *w);
void preload_sync(HCWatch *w, int state, char why);
void preload_follow(HCWatch *w, const char *id, char why);
void close_foreground(HCWatch *w);
int gate_apply(HCWatch *w, const char *choice);
/* After the previous-launcher hold: the stock Home close it kept waiting, if still wanted. */
void gate_resume(HCWatch *w);
void gate_sync(HCWatch *w);
/* 6-9, overlay launcher: the foreground (`front`, NULL asks SAM) may now allow the deferred stock Home close. */
void gate_front(HCWatch *w, const char *front);
/* preload.c: the rule shared by the 10+ and 6-9 closes for an overlay launcher, see its comment. */
gboolean card_beneath(const char *foreground);
/* 10+, overlay launcher: open the last input once per boot when nothing full-screen is in front. */
void input_arm(HCWatch *w);
void input_seen(HCWatch *w, const char *foreground);
void handle_foreground(HCWatch *w, jvalue_ref reply);
/* previous.c: the previous launcher, closed after Home Chooser left the screen (docs/settings-app.md). */
void previous_load(HCWatch *w);
gboolean previous_holding(HCWatch *w); /* TRUE: Home Chooser may still be on screen, close nothing */
gboolean previous_pending(HCWatch *w); /* TRUE: launchers left to close */
void previous_check(HCWatch *w, const char *front, char why);
void previous_life(HCWatch *w, const char *id, const char *event);
void previous_free(HCWatch *w);
void handle_life(HCWatch *w, jvalue_ref reply);
void remember_title(HCWatch *w, const char *id);
void notify_removed(HCWatch *w, const char *id);
gboolean watch_notify(HCWatch *w, const char *message, gboolean alert);
void watch_remove_tree(const char *path);
int watch_access(HCWatch *w, const char *action);
/* memory.c: put id on memchute's keep list (force: look again even when already done); NULL, a stock home or an invalid id undoes it. */
void memory_follow(HCWatch *w, const char *id, gboolean force);
/* Take our keep list away (link, copy or volatile configd value) and restart memchute when something was removed. */
void memory_undo(HCWatch *w);
void memory_free(HCWatch *w);
#endif
