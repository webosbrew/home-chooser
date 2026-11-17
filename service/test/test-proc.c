/* SPDX-License-Identifier: Apache-2.0 */
/* run_argv: a failed exec is exit 127 with the reason on stderr (a bare 127 told nothing). */
#include <stdio.h>
#include <string.h>
#include "proc.h"

static int failures;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); failures++; } } while (0)

int main(void)
{
	GString *out = g_string_new(NULL), *err = g_string_new(NULL);
	const char *const missing[] = {"/nonexistent/luna-send", "-n", "1", NULL};
	CHECK(run_argv(missing, 5, out, err, NULL) == 127);
	CHECK(strstr(err->str, "exec /nonexistent/luna-send: No such file or directory"));
	g_string_truncate(out, 0); g_string_truncate(err, 0);
	const char *const denied[] = {"/etc/passwd", NULL};
	CHECK(run_argv(denied, 5, out, err, NULL) == 127);
	CHECK(strstr(err->str, "exec /etc/passwd: Permission denied"));
	g_string_truncate(out, 0); g_string_truncate(err, 0);
	const char *const ok[] = {"/bin/sh", "-c", "echo hi; echo oops >&2; exit 3", NULL};
	CHECK(run_argv(ok, 5, out, err, NULL) == 3);
	CHECK(!strcmp(out->str, "hi\n") && !strcmp(err->str, "oops\n"));
	g_string_free(out, TRUE); g_string_free(err, TRUE);
	if (!failures) puts("test-proc: all checks passed");
	return failures != 0;
}
