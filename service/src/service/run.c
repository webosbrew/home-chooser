/* SPDX-License-Identifier: Apache-2.0 */
#include <stdio.h>
#include "fs.h"
#include "proc.h"
#include "service/files.h"
#include "json.h"
#include "log.h"
#include "policy.h"
#include "service/methods.h"
#include "service/reply.h"

#define LOG_LINES 5

static void sanitize(char *s, size_t n)
{
	for (size_t i = 0; i < n; i++) {
		unsigned char c = (unsigned char)s[i];
		if (c == '\n' || c == '\t' || (c >= 0x20 && c < 0x7f)) continue;
		s[i] = c == '\r' ? ' ' : '?';
	}
}

bool on_run(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	if (!gate(msg, "run", true)) return true;
	jvalue_ref req = parse_object(LSMessageGetPayload(msg));
	char *name = req ? get_string(req, "command") : NULL;
	const hc_command *cmd = hc_command_lookup(name);
	if (!cmd) {
		LOGE("RUN_DENIED", "%s", name ? name : "(none)");
		send_error(msg, "notAllowed", "%s is not a command this service runs", name ? name : "(none)");
	} else {
		GString *out = g_string_new(NULL), *err = g_string_new(NULL);
		int code = 0;
		if (cmd->native_tail) {
			char *buf = NULL;
			ssize_t n = slurp(HC_WATCH_LOG, FILE_MAX_BYTES, &buf, NULL);
			if (n > 0) {
				size_t from = hc_last_lines(buf, (size_t)n, LOG_LINES);
				g_string_append_len(out, buf + from, (gssize)((size_t)n - from));
			}
			g_free(buf);
		} else {
			LOGI("RUN", "%s", cmd->name);
			code = run_argv(cmd->argv, cmd->timeout_s, out, err, NULL);
		}
		sanitize(out->str, out->len);
		sanitize(err->str, err->len);
		if (code == -2) {
			send_error(msg, "timeout", "%s did not finish in %d seconds", cmd->name, cmd->timeout_s);
		} else if (code < 0) {
			send_error(msg, "spawnFailed", "Could not run %s", cmd->name);
		} else if (code != 0 && !cmd->tolerant) {
			jvalue_ref o = jobject_create();
			put_bool(o, "returnValue", false);
			put_str(o, "errorCode", "exitStatus");
			char text[128];
			snprintf(text, sizeof text, "%s exited with status %d", cmd->name, code);
			put_str(o, "errorText", text);
			put_int(o, "exitCode", code);
			put_str(o, "stdoutString", out->str);
			put_str(o, "stderrString", err->str);
			send_reply(msg, o);
		} else {
			jvalue_ref o = jobject_create();
			put_bool(o, "returnValue", true);
			put_int(o, "exitCode", code);
			put_str(o, "stdoutString", out->str);
			put_str(o, "stderrString", err->str);
			send_reply(msg, o);
		}
		g_string_free(out, TRUE);
		g_string_free(err, TRUE);
	}
	g_free(name);
	j_release(&req);
	return true;
}
