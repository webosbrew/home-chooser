/* SPDX-License-Identifier: Apache-2.0 */
#ifndef HC_HOMEKEY_H
#define HC_HOMEKEY_H
#include <glib.h>
/* Returned strings are owned by the caller. No service registration in this module. */
typedef struct {
    const char *state_dir, *stock_js, *osrelease;
    char *(*call)(const char *uri, const char *payload, int timeout, void *context);
    void *context;
    unsigned readiness_attempts;
    unsigned retry_ms;
} HCKeyOps;
char *hc_key_generate(const char *source, const char *app_id, char **reason);
int hc_key_run(const HCKeyOps *ops, const char *action, gboolean force_stock, GString *output);
#endif
