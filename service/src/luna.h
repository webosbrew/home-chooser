/* SPDX-License-Identifier: Apache-2.0 */
#ifndef HC_LUNA_H
#define HC_LUNA_H
#include <glib.h>
#include <pbnjson.h>

#define CALL_TIMEOUT_S 20

/* Callback context for a bounded luna-send call. */
typedef struct { void *ctx; int status; } Call;
void call_start(const char *uri, const char *payload, void (*done)(Call *, jvalue_ref), void *ctx);
/* All privileged calls use HC_LUNA_IDENTITY, never the service's own hub identity. */
int luna_run(const char *uri, const char *payload, int replies, int timeout_s, int (*done)(const char *), GString *out);

/* Boot/watcher child transport on the default GLib context. No LS2 registration.
 * All calls/subscriptions exec luna-send with the fixed application identity. */
typedef struct HCLuna HCLuna;
typedef struct HCSubscription HCSubscription;
typedef void (*HCSubscriptionReply)(const char *text, gboolean ended, void *context);
HCLuna *hc_luna_open(GMainLoop *loop, const char *program);
void hc_luna_close(HCLuna *client);
char *hc_luna_call(const char *uri, const char *payload, int timeout_s, void *client);
HCSubscription *hc_luna_subscribe(HCLuna *client, const char *uri, const char *payload,
    HCSubscriptionReply reply, void *context);
/* One reply (-n 1) without blocking: reply(text) for the reply, then reply(NULL, TRUE) at the end.
 * The caller bounds it and frees it with hc_luna_cancel, never from inside reply. */
HCSubscription *hc_luna_once(HCLuna *client, const char *uri, const char *payload,
    HCSubscriptionReply reply, void *context);
void hc_luna_cancel(HCSubscription *subscription);
#endif
