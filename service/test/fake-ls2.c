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

static GHashTable *subscriptions; /* key -> GPtrArray of LSMessage * */
static LSFilterFunc cancel_function;
static void *cancel_context;

static GPtrArray *subscribers(const char *key, bool create)
{
	if (!subscriptions) subscriptions = g_hash_table_new_full(g_str_hash, g_str_equal, g_free, (GDestroyNotify)g_ptr_array_unref);
	GPtrArray *list = g_hash_table_lookup(subscriptions, key);
	if (!list && create) {
		list = g_ptr_array_new();
		g_hash_table_insert(subscriptions, g_strdup(key), list);
	}
	return list;
}

bool LSSubscriptionSetCancelFunction(LSHandle *sh, LSFilterFunc function, void *ctx, LSError *lserror)
{
	(void)sh; (void)lserror;
	cancel_function = function;
	cancel_context = ctx;
	return true;
}

bool LSSubscriptionAdd(LSHandle *sh, const char *key, LSMessage *message, LSError *lserror)
{
	(void)sh; (void)lserror;
	g_ptr_array_add(subscribers(key, true), message);
	LSMessageRef(message);
	return true;
}

bool LSSubscriptionReply(LSHandle *sh, const char *key, const char *payload, LSError *lserror)
{
	(void)sh;
	GPtrArray *list = subscribers(key, false);
	for (guint i = 0; list && i < list->len; i++) LSMessageRespond(g_ptr_array_index(list, i), payload, lserror);
	return true;
}

unsigned int LSSubscriptionGetHandleSubscribersCount(LSHandle *sh, const char *key)
{
	(void)sh;
	GPtrArray *list = subscribers(key, false);
	return list ? list->len : 0;
}

void fake_subscription_cancel(LSMessage *message)
{
	GHashTableIter it;
	gpointer key, value;
	bool found = false;
	if (!subscriptions) return;
	g_hash_table_iter_init(&it, subscriptions);
	while (g_hash_table_iter_next(&it, &key, &value))
		if (g_ptr_array_remove(value, message)) found = true;
	if (!found) return;
	LSMessageUnref(message);
	if (cancel_function) cancel_function(NULL, message, cancel_context);
}

void fake_subscription_cancel_all(void)
{
	GHashTableIter it;
	gpointer key, value;
	if (!subscriptions) return;
	for (;;) {
		LSMessage *message = NULL;
		g_hash_table_iter_init(&it, subscriptions);
		while (!message && g_hash_table_iter_next(&it, &key, &value))
			if (((GPtrArray *)value)->len) message = g_ptr_array_index((GPtrArray *)value, 0);
		if (!message) return;
		fake_subscription_cancel(message);
	}
}
