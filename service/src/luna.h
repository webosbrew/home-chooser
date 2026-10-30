/* SPDX-License-Identifier: Apache-2.0 */
#ifndef HC_LUNA_H
#define HC_LUNA_H
#include <glib.h>
#include <pbnjson.h>
#include <luna-service2/lunaservice.h>

#define CALL_TIMEOUT_S 20

/* Callback context for a bounded luna-send call. */
typedef struct { void *ctx; int status; } Call;
void call_start(const char *uri, const char *payload, void (*done)(Call *, jvalue_ref), void *ctx);
/* All privileged calls use HC_LUNA_IDENTITY, never the service's own hub identity. */
int luna_run(const char *uri, const char *payload, int replies, int timeout_s, int (*done)(const char *), GString *out);

/* Private boot/watcher client, attached to the default GLib context. No RPC name.
 * Calls and subscriptions use the same application ID as luna-send -a. */
typedef struct HCLuna HCLuna;
typedef struct HCSubscription HCSubscription;
typedef void (*HCSubscriptionReply)(const char *text, gboolean ended, void *context);
HCLuna *hc_luna_open(GMainLoop *loop);
void hc_luna_close(HCLuna *client);
char *hc_luna_call(const char *uri, const char *payload, int timeout_s, void *client);
HCSubscription *hc_luna_subscribe(HCLuna *client, const char *uri, const char *payload,
    HCSubscriptionReply reply, void *context);
void hc_luna_cancel(HCSubscription *subscription);
#endif
