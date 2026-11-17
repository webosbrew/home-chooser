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
} HCKeyOps;
/* How this firmware's Home key is redirected: not at all (5, 10+, 4.5: the settings record and profile), by
 * a changed copy of systemUi.js (6-9), or by an entry of ours inserted before handleSystemKeys (4.0). */
typedef enum { HC_KEY_NONE = 0, HC_KEY_PATCH, HC_KEY_INSERT } HCKeyMode;
HCKeyMode hc_key_mode(const char *release_text, const char *stock_source);
/* The 4.0 filter file for a launcher; overlay: Back on it opens the last input. NULL with *reason on a bad id. */
char *hc_key40_generate(const char *app_id, gboolean overlay, char **reason);
char *hc_key_generate(const char *source, const char *app_id, char **reason);
int hc_key_run(const HCKeyOps *ops, const char *action, gboolean force_stock, GString *output);
#endif
