/* SPDX-License-Identifier: Apache-2.0 */
#include "log.h"
#include "luna.h"
#include "notify.h"
#include "policy.h"

static int never_stop(const char *text) { (void)text; return 0; }

/* A luna call that must come back with a true returnValue; reply text is logged. */
bool notify_call(const char *tag, const char *uri, jvalue_ref payload, int timeout_s)
{
	GString *out = g_string_new(NULL);
	int code = luna_run(uri, jvalue_stringify(payload), 1, timeout_s, never_stop, out);
	bool ok = code == 0 && hc_remove_reply_class(out->str) == 1;
	LOGI(tag, "%s %s (exit %d): %s", uri, ok ? "accepted" : "refused", code, out->str);
	g_string_free(out, TRUE);
	return ok;
}
