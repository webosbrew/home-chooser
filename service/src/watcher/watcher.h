/* SPDX-License-Identifier: Apache-2.0 */
#ifndef HC_WATCHER_H
#define HC_WATCHER_H
#include <glib.h>

typedef struct HCWatch HCWatch;
typedef struct {
    const char *state_dir, *osrelease, *app_dir, *override, *init_link;
    const char *runtime, *scripts_dir, *access_script, *log, *stock_js;
    const char *app_dirs, *install_dirs, *luna_send;
    const char *gate_binary, *gate_dir, *proc; /* 6-9 home gate: installed binary, tmpfs runtime dir, /proc */
    unsigned confirm_ms, interval_s, backoff_s, recheck_s;
    unsigned preload_retry_ms, preload_settle_ms, preload_tries, gate_settle_ms;
    unsigned notify_retry_ms, notify_tries, log_max;
    gboolean preload, notify;
} HCWatchConfig;
typedef struct {
    char *(*call)(const char *, const char *, int, void *);
    /* Optional test seams. Production uses native Home-key, proc and GLib helpers. */
    int (*key)(gboolean stock, void *);
    int (*access)(const char *action, void *);
    int (*gate)(const char *choice, void *); /* NULL unmounts; 0 ok, 1 failed */
    gint64 (*now)(void *);
    void (*wait)(unsigned ms, void *);
    void (*wake)(void *);
    void *context;
} HCWatchOps;

HCWatchConfig hc_watch_config(void);
HCWatch *hc_watch_new(const HCWatchConfig *config, const HCWatchOps *ops);
void hc_watch_free(HCWatch *watch);
void hc_watch_start(HCWatch *watch);
/* S/A/T/F/L/B: settings/apps/ticker/foreground/life/preload-ready. Lowercase
 * subscription tag means transport ended. Settings loss is fatal, others degrade. */
void hc_watch_event(HCWatch *watch, char tag, const char *text);
void hc_watch_stop(HCWatch *watch, int status, const char *reason);
int hc_watch_status(HCWatch *watch); /* -1 while running */
gboolean hc_watch_idle(HCWatch *watch);
int hc_watcher_main(void);
/* Configurable paths for offline integration tests; production always uses defaults. */
int hc_watch_run(const HCWatchConfig *config, gboolean boot);
#endif
