/* SPDX-License-Identifier: Apache-2.0 */
#include "json.h"
#include "log.h"
#include "luna.h"
#include "policy.h"
#include "proc.h"

void call_start(const char *uri, const char *payload, void (*done)(Call *, jvalue_ref), void *ctx)
{
	Call c = {.ctx = ctx};
	GString *out = g_string_new(NULL);
	int code = luna_run(uri, payload, 1, CALL_TIMEOUT_S, NULL, out);
	c.status = code;
	jvalue_ref parsed = code == 0 ? parse_object(out->str) : NULL;
	if (!parsed) LOGE("CALL_FAILED", "%s (exit %d)", uri, code);
	done(&c, parsed);
	j_release(&parsed);
	g_string_free(out, TRUE);
}

/* A blocking luna-send as HC_LUNA_IDENTITY. Returns the exit status (-2 timeout, -1 spawn failure,
 * 126 uri not on the list). stdout is the reply text. */
int luna_run(const char *uri, const char *payload, int replies, int timeout_s, int (*done)(const char *), GString *out)
{
	const char *argv[8];
	GString *err = g_string_new(NULL);
	if (!hc_luna_argv(uri, payload, replies, argv)) {
		g_string_free(err, TRUE);
		return 126;
	}
	LOGI("RUN", "luna-send -a %s %s", HC_LUNA_IDENTITY, uri);
	int code = run_argv(argv, timeout_s, out, err, done);
	/* JSON replies retain UTF-8 titles and paths. */
	if (err->len) LOGI("RUN_STDERR", "%s: %s", uri, err->str);
	g_string_free(err, TRUE);
	return code;
}
