/* SPDX-License-Identifier: Apache-2.0 */
#ifndef HC_SERVICE_REPLY_H
#define HC_SERVICE_REPLY_H
#include <luna-service2/lunaservice.h>
#include <pbnjson.h>

/* send_reply releases the object. Refused gates reply and return false. */
void send_reply(LSMessage *msg, jvalue_ref obj);
void send_error(LSMessage *msg, const char *code, const char *fmt, ...);
void send_ok(LSMessage *msg);
bool gate(LSMessage *msg, const char *method, bool need_root);
void send_error_for(LSMessage *msg, const char *method, const char *code, const char *text);
#endif
