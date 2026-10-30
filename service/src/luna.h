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
#endif
