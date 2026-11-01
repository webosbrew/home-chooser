/* SPDX-License-Identifier: Apache-2.0 */
/* A fake of the few luna-service2 message functions the service's handlers use (fake-ls2.c), so the
 * real handlers run on the host without a hub: a message carries a chosen application id, sender
 * service name and payload, and keeps every reply and its reference count. */
#ifndef HC_TEST_FAKE_LS2_H
#define HC_TEST_FAKE_LS2_H
#include <glib.h>
#include <luna-service2/lunaservice.h>

/* app and service may be NULL (a sender without them). One reference, owned by the caller. */
LSMessage *fake_message_new(const char *app, const char *service, const char *payload);
/* The replies sent so far, in order (char *). */
GPtrArray *fake_message_replies(LSMessage *message);
int fake_message_refs(LSMessage *message);
void fake_message_free(LSMessage *message);
#endif
