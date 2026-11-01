/* SPDX-License-Identifier: Apache-2.0 */
/* Subscriptions of the launcher lists (src/service/subscribe.c) with the real handlers, the real
 * luna-child.c upstream children and fake-luna-send.py as luna-send, on the fake hub (fake-ls2.c),
 * under qemu. Covers: the first reply is a fresh read; the shared upstream starts with the first
 * subscriber and is not started again for the second; a change reaches every subscriber as the whole
 * list from a new read; a dead upstream restarts with backoff and its first reply counts as a change;
 * the subscriber cap; cancelling the last subscriber kills the upstream.
 *
 * argv: WORKDIR FAKE_LUNA_SEND. Link seams: run_argv runs the fake instead of /usr/bin/luna-send,
 * geteuid is root, read_choice has no Home. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#include "fake-ls2.h"
#include "fs.h"
#include "json.h"
#include "log.h"
#include "service/catalog.h"
#include "service/methods.h"

PmLogContext hc_log;
static unsigned checks, failures;
#define CHECK(expr) do { checks++; if (!(expr)) { failures++; fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #expr); } } while (0)

static char *root, *program;
static int fifo = -1;

uid_t __wrap_geteuid(void);
uid_t __wrap_geteuid(void) { return 0; }
char *__wrap_read_choice(void);
char *__wrap_read_choice(void) { return NULL; }

int __real_run_argv(const char *const *argv, int timeout_s, GString *out, GString *err, int (*done)(const char *));
int __wrap_run_argv(const char *const *argv, int timeout_s, GString *out, GString *err, int (*done)(const char *));
int __wrap_run_argv(const char *const *argv, int timeout_s, GString *out, GString *err, int (*done)(const char *))
{
	const char *copy[16];
	int n = 0;
	for (; argv[n] && n < 15; n++) copy[n] = argv[n];
	copy[n] = NULL;
	copy[0] = program;
	return __real_run_argv(copy, timeout_s, out, err, done);
}

static void write_text(const char *name, const char *text)
{
	char *p = g_build_filename(root, name, NULL);
	CHECK(g_file_set_contents(p, text, -1, NULL));
	g_free(p);
}

static void state(const char *apps_json)
{
	char *text = g_strdup_printf("{\"apps\":{\"org.example.launcher\":{\"id\":\"org.example.launcher\","
		"\"requiredPermissions\":[\"org.webosbrew.homechooser.applications\"]}},"
		"\"replies\":{\"listApps\":{\"returnValue\":true,\"apps\":%s}}}", apps_json);
	write_text("state.json", text);
	g_free(text);
}

static void pump_ms(unsigned ms)
{
	gint64 end = g_get_monotonic_time() + ms * 1000;
	do {
		while (g_main_context_iteration(NULL, FALSE)) {}
		g_usleep(2000);
	} while (g_get_monotonic_time() < end);
}

static int count_streams(void)
{
	char *p = g_build_filename(root, "calls.jsonl", NULL), *text = NULL;
	g_file_get_contents(p, &text, NULL, NULL);
	int n = 0;
	char **lines = g_strsplit(text ? text : "", "\n", -1);
	for (int i = 0; lines[i]; i++) if (strstr(lines[i], "\"stream\": true") && strstr(lines[i], "/listApps")) n++;
	g_strfreev(lines);
	g_free(text);
	g_free(p);
	return n;
}

static bool upstream_ready(void)
{
	char *p = g_build_filename(root, "listApps.ready", NULL);
	bool yes = g_file_test(p, G_FILE_TEST_EXISTS);
	g_free(p);
	return yes;
}

static void clear_ready(void)
{
	char *p = g_build_filename(root, "listApps.ready", NULL);
	unlink(p);
	g_free(p);
}

static bool wait_for(bool (*cond)(void), unsigned ms)
{
	for (unsigned t = 0; t < ms; t += 20) {
		if (cond()) return true;
		pump_ms(20);
	}
	return cond();
}

static void upstream_line(const char *line)
{
	CHECK(write(fifo, line, strlen(line)) == (ssize_t)strlen(line));
}

/* The reply at index i of msg, parsed. */
static jvalue_ref reply_at(LSMessage *msg, guint i)
{
	GPtrArray *r = fake_message_replies(msg);
	return i < r->len ? parse_object(g_ptr_array_index(r, i)) : NULL;
}

static bool reply_has(LSMessage *msg, guint i, const char *needle)
{
	GPtrArray *r = fake_message_replies(msg);
	return i < r->len && strstr(g_ptr_array_index(r, i), needle) != NULL;
}

static LSMessage *subscribe(void)
{
	LSMessage *m = fake_message_new("org.example.launcher", NULL, "{\"subscribe\":true}");
	on_launcher_list_apps(NULL, m, NULL);
	return m;
}

int main(int argc, char **argv)
{
	if (argc != 3) return 2;
	root = argv[1];
	setbuf(stdout, NULL);
	mkdir_p(root);
	char *source = NULL;
	CHECK(g_file_get_contents(argv[2], &source, NULL, NULL));
	program = g_build_filename(root, "luna-send", NULL);
	CHECK(g_file_set_contents(program, source, -1, NULL));
	chmod(program, 0755);
	g_free(source);
	char *calls = g_build_filename(root, "calls.jsonl", NULL);
	unlink(calls);
	clear_ready();
	char *fifo_path = g_build_filename(root, "listApps.fifo", NULL);
	unlink(fifo_path);
	CHECK(mkfifo(fifo_path, 0600) == 0);
	fifo = open(fifo_path, O_RDWR | O_NONBLOCK | O_CLOEXEC);
	CHECK(fifo >= 0);
	subs_set_program(program);
	subs_init(NULL);

	/* 1. First subscriber: a fresh read with subscribed true, then the upstream starts. */
	state("[{\"id\":\"one\",\"title\":\"One\"}]");
	LSMessage *a = subscribe();
	jvalue_ref first = reply_at(a, 0);
	CHECK(first && reply_ok(first));
	CHECK(reply_has(a, 0, "\"subscribed\":true") && reply_has(a, 0, "\"one\""));
	j_release(&first);
	CHECK(subs_count(NULL, HC_LIST_APPS) == 1);
	CHECK(subs_upstream_running(HC_LIST_APPS));
	CHECK(wait_for(upstream_ready, 5000));
	CHECK(count_streams() == 1);

	/* 2. The upstream's first reply is the state just sent: no broadcast. */
	upstream_line("{\"subscribed\":true,\"apps\":[],\"returnValue\":true}\n");
	pump_ms(600);
	CHECK(fake_message_replies(a)->len == 1);

	/* 3. A change: the whole list again, from a new read. */
	state("[{\"id\":\"one\",\"title\":\"One\"},{\"id\":\"two\",\"title\":\"Two\"}]");
	upstream_line("{\"returnValue\":true,\"apps\":[]}\n");
	pump_ms(1500);
	CHECK(fake_message_replies(a)->len == 2);
	CHECK(reply_has(a, 1, "\"two\"") && reply_has(a, 1, "\"subscribed\":true"));

	/* 4. A burst of changes is one read (debounced). */
	upstream_line("{\"returnValue\":true,\"apps\":[]}\n{\"returnValue\":true,\"apps\":[]}\n{\"returnValue\":true,\"apps\":[]}\n");
	pump_ms(1500);
	CHECK(fake_message_replies(a)->len == 3);

	/* 5. A second subscriber: its own fresh read, the same upstream. */
	LSMessage *b = subscribe();
	CHECK(fake_message_replies(b)->len == 1 && reply_has(b, 0, "\"two\""));
	CHECK(subs_count(NULL, HC_LIST_APPS) == 2);
	CHECK(count_streams() == 1);

	/* 6. The upstream dies: restarted after the 1 s backoff, and its first reply is a change. */
	clear_ready();
	upstream_line("!crash\n");
	pump_ms(300);
	CHECK(!subs_upstream_running(HC_LIST_APPS));
	CHECK(wait_for(upstream_ready, 5000));
	CHECK(subs_upstream_running(HC_LIST_APPS));
	CHECK(count_streams() == 2);
	state("[{\"id\":\"three\"}]");
	guint before_a = fake_message_replies(a)->len, before_b = fake_message_replies(b)->len;
	upstream_line("{\"subscribed\":true,\"apps\":[],\"returnValue\":true}\n");
	pump_ms(1500);
	CHECK(fake_message_replies(a)->len == before_a + 1 && reply_has(a, before_a, "\"three\""));
	CHECK(fake_message_replies(b)->len == before_b + 1 && reply_has(b, before_b, "\"three\""));

	/* 7. The cap: 16 subscribers, the 17th is refused and not added. */
	GPtrArray *others = g_ptr_array_new_with_free_func((GDestroyNotify)fake_message_free);
	for (int i = 0; i < HC_SUBSCRIBERS_MAX - 2; i++) {
		LSMessage *m = subscribe();
		g_ptr_array_add(others, m);
	}
	CHECK(subs_count(NULL, HC_LIST_APPS) == HC_SUBSCRIBERS_MAX);
	LSMessage *over = subscribe();
	CHECK(reply_has(over, 0, "\"tooManySubscriptions\""));
	CHECK(subs_count(NULL, HC_LIST_APPS) == HC_SUBSCRIBERS_MAX);
	CHECK(count_streams() == 2);

	/* 8. Cancelling one keeps the upstream; cancelling the last kills it and reaps the child. */
	fake_subscription_cancel(a);
	pump_ms(100);
	CHECK(subs_upstream_running(HC_LIST_APPS));
	fake_subscription_cancel_all();
	pump_ms(300);
	CHECK(subs_count(NULL, HC_LIST_APPS) == 0);
	CHECK(!subs_upstream_running(HC_LIST_APPS));
	int status;
	errno = 0;
	CHECK(waitpid(-1, &status, WNOHANG) == -1 && errno == ECHILD);
	CHECK(fake_message_refs(a) == 1 && fake_message_refs(b) == 1);

	/* 9. A new first subscriber starts a new upstream. */
	clear_ready();
	LSMessage *c = subscribe();
	CHECK(wait_for(upstream_ready, 5000));
	CHECK(count_streams() == 3);
	fake_subscription_cancel_all();
	pump_ms(300);
	CHECK(!subs_upstream_running(HC_LIST_APPS));

	subs_shutdown();
	fake_message_free(a);
	fake_message_free(b);
	fake_message_free(c);
	fake_message_free(over);
	g_ptr_array_free(others, TRUE);
	close(fifo);
	g_free(fifo_path);
	g_free(calls);
	g_free(program);
	printf("%u passed, %u failed (launcher subscriptions)\n", checks - failures, failures);
	return failures ? 1 : 0;
}
