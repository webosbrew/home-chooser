/* SPDX-License-Identifier: Apache-2.0 */
/* Driver of the launcher API contract test (launcher-spec.py contract). Runs the real handlers of
 * src/service/launcher.c, with the real reply.c, luna.c, apps.c, json.c, fs.c and policy.c, on the
 * cases in argv[1] (one JSON object per line) and prints, per case, one JSON line: the replies the
 * handler sent, the luna-send runs it asked for and the message's final reference count.
 *
 * The hub is fake-ls2.c. Four link-time seams (--wrap, see ../CMakeLists.txt) stand in for the
 * device; no production code changes for them:
 *   run_argv     the luna-send child of luna.c: records argv and answers from the case's script
 *   geteuid      the case's root state
 *   read_choice  the stored Home choice (HC_HOME_FILE on the device)
 *   open64       /dev/urandom fails when the case says entropy is false; other paths pass through */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>
#include <unistd.h>
#include "fake-ls2.h"
#include "json.h"
#include "log.h"
#include "service/methods.h"

PmLogContext hc_log;

static struct {
	bool root, entropy;
	char *choice;
	jvalue_ref script; /* the case's calls: [{stdout, exit}] */
	ssize_t next;
	jvalue_ref made;   /* [{argv, timeout}] */
} g;

uid_t __wrap_geteuid(void);
uid_t __wrap_geteuid(void) { return g.root ? 0 : 1000; }

char *__wrap_read_choice(void);
char *__wrap_read_choice(void) { return g.choice ? g_strdup(g.choice) : NULL; }

int __real_open64(const char *path, int flags, ...);
int __wrap_open64(const char *path, int flags, ...);
int __wrap_open64(const char *path, int flags, ...)
{
	mode_t mode = 0;
	if (flags & O_CREAT) { /* this glibc has no O_TMPFILE */
		va_list ap;
		va_start(ap, flags);
		mode = va_arg(ap, mode_t);
		va_end(ap);
	}
	if (!g.entropy && path && strcmp(path, "/dev/urandom") == 0) {
		errno = ENOENT;
		return -1;
	}
	return __real_open64(path, flags, mode);
}

int __wrap_run_argv(const char *const *argv, int timeout_s, GString *out, GString *err, int (*done)(const char *));
int __wrap_run_argv(const char *const *argv, int timeout_s, GString *out, GString *err, int (*done)(const char *))
{
	(void)err; (void)done;
	jvalue_ref rec = jobject_create();
	jvalue_ref args = jarray_create(NULL);
	for (int i = 0; argv[i]; i++) jarray_append(args, jstring_create(argv[i]));
	jobject_set(rec, key_buf("argv"), args);
	put_int(rec, "timeout", timeout_s);
	jarray_append(g.made, rec);
	if (g.next >= jarray_size(g.script)) return -1; /* unscripted: the runner reports the extra call */
	jvalue_ref step = jarray_get(g.script, g.next++);
	char *text = get_string(step, "stdout");
	if (text) g_string_append(out, text);
	g_free(text);
	int32_t code = 0;
	jvalue_ref exit_code = jobject_get(step, key_buf("exit"));
	if (jis_valid(exit_code) && jis_number(exit_code)) jnumber_get_i32(exit_code, &code);
	return code;
}

static bool get_bool(jvalue_ref o, const char *key, bool fallback)
{
	jvalue_ref v = jobject_get(o, key_buf(key));
	bool b = fallback;
	if (jis_valid(v) && jis_boolean(v)) jboolean_get(v, &b);
	return b;
}

static const struct {
	const char *name;
	bool (*handler)(LSHandle *, LSMessage *, void *);
} METHODS[] = {
	{"getInfo", on_launcher_get_info},
	{"listApps", on_launcher_list_apps},
	{"listRunning", on_launcher_running},
	{"getForegroundApp", on_launcher_get_foreground},
	{"getAppInfo", on_launcher_get_app_info},
	{"getAppIcon", on_launcher_get_app_icon},
	{"closeApp", on_launcher_close_by_id},
	{"uninstallApp", on_launcher_uninstall},
	{NULL, NULL}
};

static void run_case(jvalue_ref c)
{
	char *name = get_string(c, "name");
	char *method = get_string(c, "method");
	char *payload = get_string(c, "payload");
	char *app = get_string(c, "app");
	char *service = get_string(c, "service");
	g.root = get_bool(c, "root", true);
	g.entropy = get_bool(c, "entropy", true);
	g.choice = get_string(c, "choice");
	g.script = jvalue_copy(jobject_get(c, key_buf("calls")));
	if (!jis_array(g.script)) {
		j_release(&g.script);
		g.script = jarray_create(NULL);
	}
	g.next = 0;
	g.made = jarray_create(NULL);

	jvalue_ref out = jobject_create();
	put_str(out, "name", name);
	bool (*handler)(LSHandle *, LSMessage *, void *) = NULL;
	for (int i = 0; METHODS[i].name; i++)
		if (method && strcmp(method, METHODS[i].name) == 0) handler = METHODS[i].handler;
	if (!handler) {
		put_str(out, "error", "unknown method");
	} else {
		LSMessage *msg = fake_message_new(app, service, payload ? payload : "{}");
		handler(NULL, msg, NULL);
		jvalue_ref replies = jarray_create(NULL);
		GPtrArray *sent = fake_message_replies(msg);
		for (guint i = 0; i < sent->len; i++) jarray_append(replies, jstring_create(g_ptr_array_index(sent, i)));
		jobject_set(out, key_buf("replies"), replies);
		put_int(out, "refs", fake_message_refs(msg));
		fake_message_free(msg);
	}
	jobject_set(out, key_buf("calls"), g.made);
	g.made = NULL;
	printf("%s\n", jvalue_stringify(out));
	fflush(stdout);
	j_release(&out);
	j_release(&g.script);
	g_free(g.choice);
	g.choice = NULL;
	g_free(name);
	g_free(method);
	g_free(payload);
	g_free(app);
	g_free(service);
}

int main(int argc, char **argv)
{
	if (argc != 2) {
		fprintf(stderr, "usage: %s CASES.jsonl\n", argv[0]);
		return 2;
	}
	char *text = NULL;
	GError *error = NULL;
	if (!g_file_get_contents(argv[1], &text, NULL, &error)) {
		fprintf(stderr, "%s\n", error->message);
		g_error_free(error);
		return 2;
	}
	char **lines = g_strsplit(text, "\n", -1);
	int status = 0;
	for (int i = 0; lines[i]; i++) {
		if (!lines[i][0]) continue;
		jvalue_ref c = parse_object(lines[i]);
		if (!c) {
			fprintf(stderr, "line %d is not a JSON object\n", i + 1);
			status = 2;
			continue;
		}
		run_case(c);
		j_release(&c);
	}
	g_strfreev(lines);
	g_free(text);
	return status;
}
