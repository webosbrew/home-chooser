/* SPDX-License-Identifier: Apache-2.0 */
#include <stdarg.h>
#include <stdio.h>
#include <unistd.h>
#include "json.h"
#include "log.h"
#include "policy.h"
#include "service/reply.h"

/* Send and release the reply object. */
void send_reply(LSMessage *msg, jvalue_ref obj)
{
	LSError e;
	LSErrorInit(&e);
	if (!LSMessageRespond(msg, jvalue_stringify(obj), &e)) {
		LOGE("REPLY_FAILED", "%s", e.message ? e.message : "?");
		LSErrorFree(&e);
	}
	j_release(&obj);
}

void send_error(LSMessage *msg, const char *code, const char *fmt, ...)
{
	char text[512];
	va_list ap;
	va_start(ap, fmt);
	vsnprintf(text, sizeof text, fmt, ap);
	va_end(ap);
	jvalue_ref o = jobject_create();
	put_bool(o, "returnValue", false);
	put_str(o, "errorCode", code);
	put_str(o, "errorText", text);
	send_reply(msg, o);
}

void send_ok(LSMessage *msg)
{
	jvalue_ref o = jobject_create();
	put_bool(o, "returnValue", true);
	send_reply(msg, o);
}

/* Sender check, then (for privileged methods) the root check. Replies and returns false on refusal. */
bool gate(LSMessage *msg, const char *method, bool need_root)
{
	const char *app = LSMessageGetApplicationID(msg);
	if (!hc_sender_ok(app)) {
		const char *svc = LSMessageGetSenderServiceName(msg);
		LOGE("REFUSED", "%s from application id '%s', service name '%s'", method, app ? app : "(none)", svc ? svc : "(none)");
		send_error(msg, "forbidden", "Only the Home Chooser app may call this service");
		return false;
	}
	if (need_root && geteuid() != 0) {
		LOGI("NOT_ROOT", "%s refused: uid %d", method, (int)geteuid());
		send_error(msg, "notRoot", "The service is not running as root; elevate it through Homebrew Channel");
		return false;
	}
	LOGI("CALL", "%s", method);
	return true;
}

void send_error_for(LSMessage *msg, const char *method, const char *code, const char *text)
{
	LOGI("LAUNCHER_ERROR", "%s: %s: %s", method, code, text);
	jvalue_ref o = jobject_create();
	put_bool(o, "returnValue", false);
	put_int(o, "apiVersion", HC_API_VERSION);
	put_str(o, "errorCode", code);
	put_str(o, "errorText", text);
	send_reply(msg, o);
}
