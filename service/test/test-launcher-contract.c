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
 *   open64       /dev/urandom fails when the case says entropy is false; other paths pass through
 *   hc_fs_prefix the fixed icon directories (launch points, inputs) lie under the case's scratch root
 * The upstream subscription children (luna-child.c) are stubs here: a subscribe case checks the first
 * reply and the catalog; test-launcher-subscribe runs the real children. Each case ends by cancelling
 * its subscriptions, so the message's reference count is back to one. */
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
#include "luna.h"
#include "service/catalog.h"
#include "service/methods.h"

PmLogContext hc_log;

static struct {
	bool root, entropy;
	char *choice, *fs_root;
	jvalue_ref script; /* the case's calls: [{stdout, exit}] */
	ssize_t next;
	jvalue_ref made;   /* [{argv, timeout}] */
} g;

uid_t __wrap_geteuid(void);
uid_t __wrap_geteuid(void) { return g.root ? 0 : 1000; }

char *__wrap_read_choice(void);
char *__wrap_read_choice(void) { return g.choice ? g_strdup(g.choice) : NULL; }

const char *__wrap_hc_fs_prefix(void);
const char *__wrap_hc_fs_prefix(void) { return g.fs_root ? g.fs_root : ""; }

/* The upstream children: not started here (see the top of this file). */
static int stub;
HCLuna *hc_luna_open(GMainLoop *loop, const char *program) { (void)loop; (void)program; return (HCLuna *)&stub; }
void hc_luna_close(HCLuna *client) { (void)client; }
HCSubscription *hc_luna_subscribe(HCLuna *client, const char *uri, const char *payload, HCSubscriptionReply reply, void *context)
{
	(void)client; (void)uri; (void)payload; (void)reply; (void)context;
	return (HCSubscription *)&stub;
}
HCSubscription *hc_luna_once(HCLuna *client, const char *uri, const char *payload, HCSubscriptionReply reply, void *context)
{
	(void)client; (void)uri; (void)payload; (void)reply; (void)context;
	return (HCSubscription *)&stub;
}
void hc_luna_cancel(HCSubscription *subscription) { (void)subscription; }

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
	{"listLaunchPoints", on_launcher_list_launch_points},
	{"listInputs", on_launcher_list_inputs},
	{"getIcons", on_launcher_get_icons},
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
	g.fs_root = get_string(c, "fsRoot");
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
		/* subscribers: the list already has that many (the cap), before the call */
		int32_t before = 0;
		jvalue_ref n = jobject_get(c, key_buf("subscribers"));
		if (jis_valid(n) && jis_number(n)) jnumber_get_i32(n, &before);
		GPtrArray *others = g_ptr_array_new_with_free_func((GDestroyNotify)fake_message_free);
		for (int k = 0; k < HC_LIST_COUNT; k++) {
			if (strcmp(method, list_method((HCListKind)k)) != 0) continue;
			for (int i = 0; i < before; i++) {
				LSMessage *other = fake_message_new("org.example.other", NULL, "{\"subscribe\":true}");
				subs_add(NULL, (HCListKind)k, other);
				g_ptr_array_add(others, other);
			}
		}
		LSMessage *msg = fake_message_new(app, service, payload ? payload : "{}");
		handler(NULL, msg, NULL);
		int added = 0;
		for (int k = 0; k < HC_LIST_COUNT; k++) added += (int)subs_count(NULL, (HCListKind)k);
		put_int(out, "subscribers", added - (int)others->len);
		fake_subscription_cancel_all();
		while (g_main_context_iteration(NULL, FALSE)) {}
		g_ptr_array_free(others, TRUE);
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
	g_free(g.fs_root);
	g.fs_root = NULL;
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
