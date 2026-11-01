/* SPDX-License-Identifier: Apache-2.0 */
/* Fake luna-service2 messages for the host contract test; see fake-ls2.h. Only the functions the
 * handlers call are here, so a handler that starts using another one fails to link. */
#include <string.h>
#include "fake-ls2.h"

struct LSMessage {
	char *app, *service, *payload;
	int refs;
	GPtrArray *replies;
};

LSMessage *fake_message_new(const char *app, const char *service, const char *payload)
{
	LSMessage *m = g_new0(LSMessage, 1);
	m->app = g_strdup(app);
	m->service = g_strdup(service);
	m->payload = g_strdup(payload);
	m->refs = 1;
	m->replies = g_ptr_array_new_with_free_func(g_free);
	return m;
}

GPtrArray *fake_message_replies(LSMessage *message) { return message->replies; }
int fake_message_refs(LSMessage *message) { return message->refs; }

void fake_message_free(LSMessage *message)
{
	g_free(message->app);
	g_free(message->service);
	g_free(message->payload);
	g_ptr_array_free(message->replies, TRUE);
	g_free(message);
}

bool LSErrorInit(LSError *error)
{
	memset(error, 0, sizeof *error);
	return true;
}

void LSErrorFree(LSError *error) { (void)error; }
void LSMessageRef(LSMessage *message) { message->refs++; }
void LSMessageUnref(LSMessage *message) { message->refs--; }
const char *LSMessageGetApplicationID(LSMessage *message) { return message->app; }
const char *LSMessageGetSenderServiceName(LSMessage *message) { return message->service; }
const char *LSMessageGetPayload(LSMessage *message) { return message->payload; }

bool LSMessageRespond(LSMessage *message, const char *reply_payload, LSError *lserror)
{
	(void)lserror;
	g_ptr_array_add(message->replies, g_strdup(reply_payload));
	return true;
}
