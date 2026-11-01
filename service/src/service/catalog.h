/* SPDX-License-Identifier: Apache-2.0 */
/* The launcher API's subscribable reads (listApps, listLaunchPoints, listInputs, listRunning,
 * getForegroundApp) and their subscriptions. */
#ifndef HC_SERVICE_CATALOG_H
#define HC_SERVICE_CATALOG_H
#include <luna-service2/lunaservice.h>
#include <pbnjson.h>

/* Each is one platform read whose call also takes subscribe: true (docs/launcher-api.md "Subscriptions"). */
typedef enum {
	HC_LIST_APPS, HC_LIST_LAUNCH_POINTS, HC_LIST_INPUTS, HC_LIST_RUNNING, HC_LIST_FOREGROUND, HC_LIST_COUNT
} HCListKind;

/* At most this many subscribers per method; one more is refused with tooManySubscriptions. */
#define HC_SUBSCRIBERS_MAX 16
/* getIcons: at most this many items, and this many icon bytes in one reply. */
#define HC_ICON_ITEMS_MAX 64
#define HC_ICON_BATCH_BYTES (1024 * 1024)

/* catalog.c: the method name, the platform call that reads the whole state, and the launcher reply
 * built from the platform's reply (NULL when that reply is unusable: no list, or a foreground appId
 * that is not a string). */
const char *list_method(HCListKind kind);
const char *list_uri(HCListKind kind);
char *list_payload(HCListKind kind, bool subscribe);
jvalue_ref list_reply(HCListKind kind, jvalue_ref platform_reply, bool subscribed);

/* subscribe.c: one shared upstream subscription per method, fanned out to the launcher subscribers. */
void subs_init(LSHandle *sh);
void subs_shutdown(void);
/* The luna-send the upstream children run (tests); NULL for /usr/bin/luna-send. */
void subs_set_program(const char *program);
bool subs_room(LSHandle *sh, HCListKind kind);
/* Adds msg, which has had its first (full) reply, and starts the upstream on the first subscriber. */
void subs_add(LSHandle *sh, HCListKind kind, LSMessage *msg);
/* Number of subscribers of a method, and whether its upstream child is running (tests). */
unsigned subs_count(LSHandle *sh, HCListKind kind);
bool subs_upstream_running(HCListKind kind);
#endif
