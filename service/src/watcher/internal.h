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
int app_state(HCWatch *w, const char *id); /* 1 yes, 0 unknown, -1 no */
int key_refresh(HCWatch *w, gboolean stock);
void sync_baked(HCWatch *w, const char *id);
void check_apps(HCWatch *w, char why);
void check_hc(HCWatch *w);
void repair_leftover(HCWatch *w);
void preload_sync(HCWatch *w, int state, char why);
void preload_follow(HCWatch *w, const char *id, char why);
void close_foreground(HCWatch *w);
int gate_apply(HCWatch *w, const char *choice);
void gate_sync(HCWatch *w);
/* 10+, overlay launcher: open the last input once per boot when nothing full-screen is in front. */
void input_arm(HCWatch *w);
void input_seen(HCWatch *w, const char *foreground);
void handle_foreground(HCWatch *w, jvalue_ref reply);
void handle_life(HCWatch *w, jvalue_ref reply);
void remember_title(HCWatch *w, const char *id);
void notify_removed(HCWatch *w, const char *id);
void watch_notify(HCWatch *w, const char *message, gboolean alert);
void watch_remove_tree(const char *path);
int watch_access(HCWatch *w, const char *action);
#endif
