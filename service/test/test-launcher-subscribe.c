/* SPDX-License-Identifier: Apache-2.0 */
/* Subscriptions of the launcher methods (src/service/subscribe.c) with the real handlers, the real
 * luna-child.c upstream children and fake-luna-send.py as luna-send, on the fake hub (fake-ls2.c),
 * under qemu. Covers, for listApps in detail and for each other upstream (listRunning: SAM running,
 * getForegroundApp: SAM getForegroundAppInfo) again, and for getAppLifeEvents (a stream, forwarded event
 * by event, never debounced or kept): the first reply is a fresh read; the shared
 * upstream starts with the first subscriber and is not started again for the second; a change reaches
 * every subscriber as the whole state from a new read; a dead upstream restarts with backoff and its
 * first reply counts as a change; the subscriber cap; cancelling the last subscriber kills the upstream.
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

/* The platform's answer to a one-shot read of method (a SAM method name), as a JSON object's members. */
static void state_of(const char *method, const char *members)
{
	char *text = g_strdup_printf("{\"apps\":{\"org.example.launcher\":{\"id\":\"org.example.launcher\","
		"\"requiredPermissions\":[\"org.webosbrew.homechooser.applications\"]}},"
		"\"replies\":{\"%s\":{\"returnValue\":true,%s}}}", method, members);
	write_text("state.json", text);
	g_free(text);
}

static void state(const char *apps_json)
{
	char *members = g_strdup_printf("\"apps\":%s", apps_json);
	state_of("listApps", members);
	g_free(members);
}

static void pump_ms(unsigned ms)
{
	gint64 end = g_get_monotonic_time() + ms * 1000;
	do {
		while (g_main_context_iteration(NULL, FALSE)) {}
		g_usleep(2000);
	} while (g_get_monotonic_time() < end);
}

static int count_streams_of(const char *method)
{
	char *needle = g_strdup_printf("/%s\"", method);
	char *p = g_build_filename(root, "calls.jsonl", NULL), *text = NULL;
	g_file_get_contents(p, &text, NULL, NULL);
	int n = 0;
	char **lines = g_strsplit(text ? text : "", "\n", -1);
	for (int i = 0; lines[i]; i++) if (strstr(lines[i], "\"stream\": true") && strstr(lines[i], needle)) n++;
	g_strfreev(lines);
	g_free(text);
	g_free(p);
	g_free(needle);
	return n;
}

static int count_streams(void)
{
	return count_streams_of("listApps");
}

/* The upstream method of the scenario now running (the fifo and ready file of fake-luna-send.py). */
static const char *g_up = "listApps";

static bool upstream_ready(void)
{
	char *name = g_strconcat(g_up, ".ready", NULL);
	char *p = g_build_filename(root, name, NULL);
	g_free(name);
	bool yes = g_file_test(p, G_FILE_TEST_EXISTS);
	g_free(p);
	return yes;
}

static void clear_ready(void)
{
	char *name = g_strconcat(g_up, ".ready", NULL);
	char *p = g_build_filename(root, name, NULL);
	g_free(name);
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

static int open_fifo(const char *method)
{
	char *name = g_strconcat(method, ".fifo", NULL);
	char *path = g_build_filename(root, name, NULL);
	unlink(path);
	CHECK(mkfifo(path, 0600) == 0);
	int fd = open(path, O_RDWR | O_NONBLOCK | O_CLOEXEC);
	CHECK(fd >= 0);
	g_free(path);
	g_free(name);
	return fd;
}

/* One of the other upstreams, start to end: kind and its handler; up, the SAM method the upstream
 * and the reads call; members, the platform's reply before and after a change; needle, what the
 * launcher reply carries after it. */
typedef struct {
	HCListKind kind;
	bool (*handler)(LSHandle *, LSMessage *, void *);
	const char *up;
	const char *before, *after, *after_needle, *third, *third_needle;
} Scenario;

static LSMessage *subscribe_to(const Scenario *sc)
{
	LSMessage *m = fake_message_new("org.example.launcher", NULL, "{\"subscribe\":true}");
	sc->handler(NULL, m, NULL);
	return m;
}

static void run_scenario(const Scenario *sc)
{
	g_up = sc->up;
	clear_ready();
	int saved = fifo;
	fifo = open_fifo(sc->up);
	int streams = count_streams_of(sc->up);
	char *first_line = g_strdup_printf("{\"subscribed\":true,\"returnValue\":true,%s}\n", sc->before);
	char *change_line = g_strdup_printf("{\"subscribed\":true,\"returnValue\":true,%s}\n", sc->after);

	/* the first reply is a fresh read; one upstream for two subscribers */
	state_of(sc->up, sc->before);
	LSMessage *a = subscribe_to(sc);
	CHECK(fake_message_replies(a)->len == 1 && reply_has(a, 0, "\"subscribed\":true"));
	CHECK(subs_count(NULL, sc->kind) == 1 && subs_upstream_running(sc->kind));
	CHECK(wait_for(upstream_ready, 5000));
	LSMessage *b = subscribe_to(sc);
	CHECK(fake_message_replies(b)->len == 1 && reply_has(b, 0, "\"subscribed\":true"));
	CHECK(subs_count(NULL, sc->kind) == 2);
	CHECK(count_streams_of(sc->up) == streams + 1);

	/* the upstream's first reply is no change; the next is, for both, as a new read */
	upstream_line(first_line);
	pump_ms(600);
	CHECK(fake_message_replies(a)->len == 1);
	state_of(sc->up, sc->after);
	upstream_line(change_line);
	pump_ms(1500);
	CHECK(fake_message_replies(a)->len == 2 && reply_has(a, 1, sc->after_needle) && reply_has(a, 1, "\"subscribed\":true"));
	CHECK(fake_message_replies(b)->len == 2 && reply_has(b, 1, sc->after_needle));

	/* a burst is one read */
	upstream_line(change_line);
	upstream_line(change_line);
	upstream_line(change_line);
	pump_ms(1500);
	CHECK(fake_message_replies(a)->len == 3);

	/* a dead upstream comes back after the backoff; its first reply is a change */
	clear_ready();
	upstream_line("!crash\n");
	pump_ms(300);
	CHECK(!subs_upstream_running(sc->kind));
	CHECK(wait_for(upstream_ready, 5000));
	CHECK(count_streams_of(sc->up) == streams + 2);
	state_of(sc->up, sc->third);
	upstream_line(first_line);
	pump_ms(1500);
	CHECK(fake_message_replies(a)->len == 4 && reply_has(a, 3, sc->third_needle));
	CHECK(fake_message_replies(b)->len == 4 && reply_has(b, 3, sc->third_needle));

	/* the cap of this method, apart from the other methods' */
	GPtrArray *others = g_ptr_array_new_with_free_func((GDestroyNotify)fake_message_free);
	for (int i = 0; i < HC_SUBSCRIBERS_MAX - 2; i++) g_ptr_array_add(others, subscribe_to(sc));
	CHECK(subs_count(NULL, sc->kind) == HC_SUBSCRIBERS_MAX);
	LSMessage *over = subscribe_to(sc);
	CHECK(reply_has(over, 0, "\"tooManySubscriptions\""));
	CHECK(subs_count(NULL, sc->kind) == HC_SUBSCRIBERS_MAX);

	/* cancelling all kills and reaps the upstream */
	fake_subscription_cancel_all();
	pump_ms(300);
	CHECK(subs_count(NULL, sc->kind) == 0 && !subs_upstream_running(sc->kind));
	int status;
	errno = 0;
	CHECK(waitpid(-1, &status, WNOHANG) == -1 && errno == ECHILD);
	CHECK(fake_message_refs(a) == 1 && fake_message_refs(b) == 1);

	fake_message_free(a);
	fake_message_free(b);
	fake_message_free(over);
	g_ptr_array_free(others, TRUE);
	g_free(first_line);
	g_free(change_line);
	close(fifo);
	fifo = saved;
	g_up = "listApps";
}

/* A non-streaming call of a SAM method (a one-shot read): getAppLifeEvents must never have one. */
static int count_oneshots_of(const char *method)
{
	char *needle = g_strdup_printf("/%s\"", method);
	char *p = g_build_filename(root, "calls.jsonl", NULL), *text = NULL;
	g_file_get_contents(p, &text, NULL, NULL);
	int n = 0;
	char **lines = g_strsplit(text ? text : "", "\n", -1);
	for (int i = 0; lines[i]; i++) if (strstr(lines[i], "\"stream\": false") && strstr(lines[i], needle)) n++;
	g_strfreev(lines);
	g_free(text);
	g_free(p);
	g_free(needle);
	return n;
}

static LSMessage *subscribe_life(void)
{
	LSMessage *m = fake_message_new("org.example.launcher", NULL, "{\"subscribe\":true}");
	on_launcher_life_events(NULL, m, NULL);
	return m;
}

/* getAppLifeEvents (level 3): a stream. The first reply is the acknowledgement (no read of SAM); each
 * event of the one shared upstream reaches every subscriber at once, as it came and in order, with
 * only the whitelisted fields; nothing is debounced, coalesced or replayed, not even after the
 * upstream was restarted; the cap; the last cancel kills the upstream. */
static void run_life_events(void)
{
	g_up = "getAppLifeEvents";
	clear_ready();
	int saved = fifo;
	fifo = open_fifo("getAppLifeEvents");
	int streams = count_streams_of("getAppLifeEvents");
	state("[]");

	LSMessage *a = subscribe_life();
	GPtrArray *ra = fake_message_replies(a);
	CHECK(ra->len == 1 && reply_has(a, 0, "\"subscribed\":true") && reply_has(a, 0, "\"apiVersion\":3") && !reply_has(a, 0, "\"event\""));
	CHECK(subs_count(NULL, HC_LIST_LIFE_EVENTS) == 1 && subs_upstream_running(HC_LIST_LIFE_EVENTS));
	CHECK(wait_for(upstream_ready, 5000));
	LSMessage *b = subscribe_life();
	CHECK(fake_message_replies(b)->len == 1 && !reply_has(b, 0, "\"event\""));
	CHECK(subs_count(NULL, HC_LIST_LIFE_EVENTS) == 2);
	CHECK(count_streams_of("getAppLifeEvents") == streams + 1);
	CHECK(count_oneshots_of("getAppLifeEvents") == 0);

	/* the platform's own acknowledgement, a failure and an event of the wrong type are not events */
	upstream_line("{\"returnValue\":true,\"subscribed\":true}\n{\"returnValue\":false,\"errorText\":\"x\"}\n{\"returnValue\":true,\"event\":5}\n");
	pump_ms(500);
	CHECK(fake_message_replies(a)->len == 1 && fake_message_replies(b)->len == 1);

	/* three events, two of them identical, in one write: three replies each, at once, in order */
	upstream_line("{\"event\":\"launch\",\"appId\":\"org.example.app\",\"returnValue\":true,\"reason\":\"hotKey\",\"secret\":\"x\"}\n"
		"{\"event\":\"foreground\",\"appId\":\"org.example.app\",\"windowType\":\"_WEBOS_WINDOW_TYPE_CARD\",\"returnValue\":true,\"windowGroup\":false}\n"
		"{\"event\":\"foreground\",\"appId\":\"org.example.app\",\"windowType\":\"_WEBOS_WINDOW_TYPE_CARD\",\"returnValue\":true,\"windowGroup\":false}\n");
	pump_ms(300);
	CHECK(fake_message_replies(a)->len == 4 && fake_message_replies(b)->len == 4);
	CHECK(reply_has(a, 1, "\"event\":\"launch\"") && reply_has(a, 1, "\"reason\":\"hotKey\"") && reply_has(a, 1, "\"subscribed\":true")
		&& reply_has(a, 1, "\"apiVersion\":3") && !reply_has(a, 1, "secret"));
	CHECK(reply_has(a, 2, "\"event\":\"foreground\"") && reply_has(a, 2, "_WEBOS_WINDOW_TYPE_CARD") && reply_has(a, 2, "\"windowGroup\":false"));
	CHECK(reply_has(a, 3, "\"event\":\"foreground\"") && reply_has(b, 3, "\"event\":\"foreground\""));
	CHECK(reply_has(b, 1, "\"event\":\"launch\""));
	CHECK(count_oneshots_of("getAppLifeEvents") == 0);

	/* a splash event keeps its own fields */
	upstream_line("{\"event\":\"splash\",\"appId\":\"x.y\",\"splashBackground\":\"\",\"showSpinner\":true,\"title\":\"T\",\"showSplash\":true,\"returnValue\":true}\n");
	pump_ms(300);
	CHECK(reply_has(a, 4, "\"title\":\"T\"") && reply_has(a, 4, "\"showSplash\":true") && reply_has(a, 4, "\"splashBackground\":\"\""));

	/* a dead upstream comes back after the backoff; nothing is replayed or invented for the gap */
	clear_ready();
	upstream_line("!crash\n");
	pump_ms(300);
	CHECK(!subs_upstream_running(HC_LIST_LIFE_EVENTS));
	CHECK(wait_for(upstream_ready, 5000));
	CHECK(count_streams_of("getAppLifeEvents") == streams + 2);
	upstream_line("{\"returnValue\":true,\"subscribed\":true}\n");
	pump_ms(500);
	CHECK(fake_message_replies(a)->len == 5 && fake_message_replies(b)->len == 5);
	upstream_line("{\"event\":\"close\",\"appId\":\"org.example.app\",\"reason\":\"windowPolicy\",\"returnValue\":true}\n");
	pump_ms(300);
	CHECK(fake_message_replies(a)->len == 6 && reply_has(a, 5, "\"event\":\"close\"") && reply_has(b, 5, "\"reason\":\"windowPolicy\""));

	/* the cap of this method, apart from the other methods' */
	GPtrArray *others = g_ptr_array_new_with_free_func((GDestroyNotify)fake_message_free);
	for (int i = 0; i < HC_SUBSCRIBERS_MAX - 2; i++) g_ptr_array_add(others, subscribe_life());
	CHECK(subs_count(NULL, HC_LIST_LIFE_EVENTS) == HC_SUBSCRIBERS_MAX);
	LSMessage *over = subscribe_life();
	CHECK(reply_has(over, 0, "\"tooManySubscriptions\""));
	CHECK(subs_count(NULL, HC_LIST_LIFE_EVENTS) == HC_SUBSCRIBERS_MAX);
	/* a late subscriber got no earlier event */
	CHECK(fake_message_replies(g_ptr_array_index(others, 0))->len == 1);

	/* a call without subscribe is refused and subscribes nothing */
	LSMessage *plain = fake_message_new("org.example.launcher", NULL, "{}");
	on_launcher_life_events(NULL, plain, NULL);
	CHECK(reply_has(plain, 0, "\"badRequest\"") && subs_count(NULL, HC_LIST_LIFE_EVENTS) == HC_SUBSCRIBERS_MAX);

	fake_subscription_cancel_all();
	pump_ms(300);
	CHECK(subs_count(NULL, HC_LIST_LIFE_EVENTS) == 0 && !subs_upstream_running(HC_LIST_LIFE_EVENTS));
	int status;
	errno = 0;
	CHECK(waitpid(-1, &status, WNOHANG) == -1 && errno == ECHILD);
	CHECK(fake_message_refs(a) == 1 && fake_message_refs(b) == 1);
	fake_message_free(a);
	fake_message_free(b);
	fake_message_free(over);
	fake_message_free(plain);
	g_ptr_array_free(others, TRUE);
	close(fifo);
	fifo = saved;
	g_up = "listApps";
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
	fifo = open_fifo("listApps");
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

	/* 10. The other upstreams: SAM running and getForegroundAppInfo. */
	static const Scenario running = {
		HC_LIST_RUNNING, on_launcher_running, "running",
		"\"running\":[{\"id\":\"one\",\"processid\":\"11\"}]",
		"\"running\":[{\"id\":\"one\"},{\"id\":\"two\",\"processid\":\"12\"}]", "\"processId\":12",
		"\"running\":[{\"id\":\"three\"}]", "\"three\""
	};
	static const Scenario foreground = {
		HC_LIST_FOREGROUND, on_launcher_get_foreground, "getForegroundAppInfo",
		"\"appId\":\"org.example.launcher\"",
		"\"appId\":\"org.example.app\",\"windowId\":\"w1\",\"processId\":\"42\"",
		"\"processId\":42",
		"\"appId\":\"\"", "\"appId\":\"\""
	};
	run_scenario(&running);
	run_scenario(&foreground);
	run_life_events();
	CHECK(count_streams() == 3);

	subs_shutdown();
	fake_message_free(a);
	fake_message_free(b);
	fake_message_free(c);
	fake_message_free(over);
	g_ptr_array_free(others, TRUE);
	close(fifo);
	g_free(calls);
	g_free(program);
	printf("%u passed, %u failed (launcher subscriptions)\n", checks - failures, failures);
	return failures ? 1 : 0;
}
