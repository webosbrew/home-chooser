/* SPDX-License-Identifier: Apache-2.0 */
#ifndef HC_HOMEKEY_H
#define HC_HOMEKEY_H
#include <glib.h>
/* Returned strings are owned by the caller. No service registration in this module. */
typedef struct {
    const char *state_dir, *stock_js, *release_file;
    char *(*call)(const char *uri, const char *payload, int timeout, void *context);
    void *context;
    unsigned readiness_attempts;
    unsigned retry_ms;
    /* Optional: one line of the transaction's log (syslog in the service); NULL discards them. */
    void (*log)(const char *line, void *context);
} HCKeyOps;
/* How this firmware's Home key is redirected: not at all (5, 10+, 4.5: the settings record and profile), by
 * a changed copy of systemUi.js (6-9), or by an entry of ours inserted before handleSystemKeys (4.0). */
typedef enum { HC_KEY_NONE = 0, HC_KEY_PATCH, HC_KEY_INSERT } HCKeyMode;
HCKeyMode hc_key_mode(const char *release_text, const char *stock_source);
/* The 4.0 filter file for a launcher; overlay: Back on it opens the last input. NULL with *reason on a bad id. */
char *hc_key40_generate(const char *app_id, gboolean overlay, char **reason);
char *hc_key_generate(const char *source, const char *app_id, char **reason);
int hc_key_run(const HCKeyOps *ops, const char *action, gboolean force_stock, GString *output);
/* The same, and clear_override (apply only): first clear a persistent keyFilters override another app saved in
 * configd (a persistent null; never done unless asked), re-take the stock snapshot from what is left, then apply.
 * Reasons that are tokens: persistent-override (the volatile write was accepted but a persistent list masks it;
 * stock was restored), override-not-cleared (the clear was done and the write is still masked), override-clear-failed. */
/* The state directory's marker that a persistent override was met (written by apply/boot and by the watcher when
 * another app took the list; removed by any apply that ends with the filter active or stock restored). `status`
 * reports it as override=yes while the filter is not active, which is how the settings app learns of it. */
void hc_key_mark_override(const char *state_dir, gboolean on);
/* The one line shown (a toast) when the Home button was taken over. */
#define HC_OVERRIDE_TOAST "Another app has taken over the Home button. Open Home Chooser to change it."
int hc_key_run_ex(const HCKeyOps *ops, const char *action, gboolean force_stock, gboolean clear_override, GString *output);
#endif
