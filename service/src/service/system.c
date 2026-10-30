/* SPDX-License-Identifier: Apache-2.0 */
#include "notify.h"
#include "proc.h"
#include "json.h"
#include "log.h"
#include "policy.h"
#include "service/methods.h"
#include "service/reply.h"

/* ---- rebootPrompt ----
 * The patch needs a restart of the TV and nothing else restarts anything while the TV runs. Only
 * Home Chooser can ask and only while the worker reports reboot=needed. The button calls tvpowerd
 * directly (no systemctl fallback: it would skip the power-off sequence and its busy check). */

#define REBOOT_URI "luna://com.webos.service.tvpower/power/reboot"

/* True when the worker's status says reboot=needed right now. */
static bool reboot_pending(void)
{
	const hc_command *cmd = hc_command_lookup("homekeyStatus");
	if (!cmd) return false;
	GString *out = g_string_new(NULL), *err = g_string_new(NULL);
	int code = run_argv(cmd->argv, cmd->timeout_s, out, err, NULL);
	bool needed = code == 0 && hc_reboot_needed(out->str);
	g_string_free(out, TRUE);
	g_string_free(err, TRUE);
	return needed;
}

/* rebootPrompt {reason: "apply"|"remove"}: always a system alert (a toast when it is refused). */
bool on_reboot_prompt(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	if (!gate(msg, "rebootPrompt", true)) return true;
	jvalue_ref req = parse_object(LSMessageGetPayload(msg));
	char *reason = req ? get_string(req, "reason") : NULL;
	j_release(&req);
	const char *message = hc_reboot_message(reason);
	g_free(reason);
	if (!reboot_pending()) {
		send_error_for(msg, "rebootPrompt", "notPending", "No restart is pending");
		return true;
	}
	if (!message) {
		send_error_for(msg, "rebootPrompt", "badRequest", "reason must be apply or remove");
		return true;
	}
	jvalue_ref a = jobject_create();
	put_str(a, "sourceId", HC_APP_ID);
	put_str(a, "title", "Home Chooser");
	put_str(a, "message", message);
	jvalue_ref buttons = jarray_create(NULL);
	jvalue_ref yes = jobject_create();
	put_str(yes, "label", "Reboot");
	put_str(yes, "onclick", REBOOT_URI);
	jvalue_ref yes_params = jobject_create();
	put_str(yes_params, "reason", "remoteKey");
	jobject_set(yes, key_buf("params"), yes_params);
	jarray_append(buttons, yes);
	jvalue_ref no = jobject_create();
	put_str(no, "label", "Later");
	put_bool(no, "focus", true);
	jarray_append(buttons, no);
	jobject_set(a, key_buf("buttons"), buttons);
	LOGI("REBOOT_ASK", "createAlert request: message: %s; buttons: Reboot (tvpower/power/reboot), Later (focused)", message);
	bool alert = notify_call("REBOOT_ASK", "luna://com.webos.notification/createAlert", a, 8);
	j_release(&a);
	const char *how = "alert";
	if (!alert) {
		jvalue_ref toast = jobject_create();
		put_str(toast, "sourceId", HC_APP_ID);
		put_bool(toast, "noaction", true);
		put_str(toast, "message", message);
		bool shown = notify_call("REBOOT_ASK", "luna://com.webos.notification/createToast", toast, 8);
		j_release(&toast);
		if (!shown) {
			send_error_for(msg, "rebootPrompt", "confirmFailed", "Could not ask the user: neither the alert nor the toast could be shown");
			return true;
		}
		how = "toast";
	}
	jvalue_ref o = jobject_create();
	put_bool(o, "returnValue", true);
	put_bool(o, "pending", true);
	put_str(o, "confirm", how);
	send_reply(msg, o);
	return true;
}
