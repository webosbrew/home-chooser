/* SPDX-License-Identifier: Apache-2.0 */
#define _GNU_SOURCE
#include "internal.h"
#include "luna.h"
#include "log.h"
#include <glib-unix.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <sys/stat.h>

typedef struct { char tag; char *text; } Event;
typedef struct Runner Runner;
typedef struct { Runner *runner; char tag; HCSubscription *subscription; } Feed;
struct Runner {
    HCWatch *watch;
    HCLuna *luna;
    GMainLoop *loop;
    GQueue events;
    guint drain;
};

/* Copy by rename, including the running executable inode after package unlink.
 * The mapped process needs no copy to finish; a later unit restart does. */
static gboolean stage_file(const char *source, const char *target, int mode)
{
    char *bytes = NULL;
    gsize size;
    if (!g_file_get_contents(source, &bytes, &size, NULL)) return FALSE;
    char *temp = g_strconcat(target, ".new", NULL);
    gboolean ok = write_atomic(temp, bytes, size) && chmod(temp, mode) == 0 && rename(temp, target) == 0;
    if (!ok) unlink(temp);
    g_free(temp); g_free(bytes); return ok;
}
static gboolean stage_runtime(const HCWatchConfig *c)
{
    char *binary = g_build_filename(c->runtime, "homechooser-daemon", NULL);
    gboolean ok = stage_file("/proc/self/exe", binary, 0755); g_free(binary);
    char *exe = g_file_read_link("/proc/self/exe", NULL);
    char *dir = exe ? g_path_get_dirname(exe) : NULL;
    char *boot_dir = g_build_filename(c->state_dir, "boot", NULL);
    const char *roots[] = {c->scripts_dir, c->runtime, dir, boot_dir, NULL};
    const char *files[] = {"homechooser-access", "lib/common.sh", NULL};
    for (int i = 0; files[i]; i++) {
        gboolean copied = FALSE;
        for (int j = 0; roots[j] && !copied; j++) {
            char *source = g_build_filename(roots[j], files[i], NULL);
            char *target = g_build_filename(c->runtime, files[i], NULL);
            copied = stage_file(source, target, i ? 0644 : 0755);
            g_free(source); g_free(target);
        }
        ok = ok && copied;
    }
    g_free(exe); g_free(dir); g_free(boot_dir); return ok;
}

static char *runner_call(const char *uri, const char *payload, int timeout, void *context)
{
    Runner *r = context; return hc_luna_call(uri, payload, timeout, r->luna);
}
static gboolean drain_events(gpointer context);
static void wake(void *context)
{
    Runner *r = context;
    if (hc_watch_status(r->watch) >= 0) { g_main_loop_quit(r->loop); return; }
    if (!r->drain && !g_queue_is_empty(&r->events) && hc_watch_idle(r->watch))
        r->drain = g_idle_add(drain_events, r);
}
static gboolean drain_events(gpointer context)
{
    Runner *r = context;
    r->drain = 0;
    if (!hc_watch_idle(r->watch)) return G_SOURCE_REMOVE;
    Event *event = g_queue_pop_head(&r->events);
    if (event) {
        hc_watch_event(r->watch, event->tag, event->text);
        g_free(event->text); g_free(event);
    }
    wake(r); return G_SOURCE_REMOVE;
}
static void enqueue(Runner *r, char tag, const char *text)
{
    if (hc_watch_status(r->watch) >= 0) return;
    if (tag == 'A') text = NULL; /* list contents are deliberately ignored */
    /* The shell had pipe backpressure. Bound the replacement queue as well;
     * restart/re-read current state rather than grow forever on a hub storm. */
    if (g_queue_get_length(&r->events) >= 128 || (text && strlen(text) > 65536)) {
        hc_watch_stop(r->watch, 1, "subscription event queue overflow"); wake(r); return;
    }
    Event *event = g_new0(Event, 1);
    event->tag = tag; event->text = g_strdup(text);
    g_queue_push_tail(&r->events, event); wake(r);
}
static void subscription(const char *text, gboolean ended, void *context)
{
    Feed *f = context;
    if (!ended) enqueue(f->runner, f->tag, text);
    else enqueue(f->runner, g_ascii_tolower(f->tag), NULL);
}
static gboolean ticker(gpointer context)
{
    enqueue(context, 'T', NULL); return G_SOURCE_CONTINUE;
}
static int signal_fd = -1;
static void signal_handler(int number)
{
    int saved = errno;
    unsigned char byte = number;
    if (signal_fd >= 0) {
        ssize_t ignored = write(signal_fd, &byte, 1); (void)ignored;
    }
    errno = saved;
}
static gboolean stop_signal(gint fd, GIOCondition condition, gpointer context)
{
    (void)condition;
    char bytes[32];
    while (read(fd, bytes, sizeof bytes) > 0) {}
    Runner *r = context;
    hc_watch_stop(r->watch, 0, "stopped by signal"); wake(r);
    return G_SOURCE_CONTINUE;
}

int hc_watch_run(const HCWatchConfig *config, gboolean boot)
{
    Runner r = {0};
    r.loop = g_main_loop_new(NULL, FALSE);
    r.luna = hc_luna_open(r.loop, config->luna_send);
    if (!r.luna) { g_main_loop_unref(r.loop); return 1; }
    HCWatchConfig c = *config;
    HCWatchOps ops = {.call = runner_call, .wake = wake, .context = &r};
    r.watch = hc_watch_new(&c, &ops);
    if (!stage_runtime(&c)) watch_log(r.watch, "cannot stage all runtime recovery files in %s", c.runtime);
    /* g_unix_signal_add creates a GLib worker thread. A self-pipe keeps this role
     * single-threaded, including while a bounded call pumps the main context. */
    int pipefd[2];
    if (pipe2(pipefd, O_CLOEXEC | O_NONBLOCK)) {
        hc_watch_stop(r.watch, 1, "cannot create signal pipe");
        hc_watch_free(r.watch); hc_luna_close(r.luna); g_main_loop_unref(r.loop); return 1;
    }
    signal_fd = pipefd[1];
    const int signals[] = {SIGTERM, SIGINT, SIGHUP};
    struct sigaction action = {0}, previous[G_N_ELEMENTS(signals)];
    action.sa_handler = signal_handler; sigemptyset(&action.sa_mask);
    guint signal_source = g_unix_fd_add(pipefd[0], G_IO_IN, stop_signal, &r);
    /* Boot apply uses the same bounded luna-send transport. The watcher
     * still runs on readiness failure and retries the live filter on app events. */
    HCKeyOps key = {c.state_dir, c.stock_js, c.osrelease, runner_call, &r, 30, 1000};
    GString *output = g_string_new(NULL);
    if (boot) {
        hc_key_run(&key, "boot", FALSE, output);
        watch_log(r.watch, "boot apply: %s", output->str);
    }
    g_string_free(output, TRUE);
    /* Preserve boot worker signal semantics during the transaction. Clean
     * watcher shutdown starts only after boot apply, as with the former exec. */
    for (unsigned i = 0; i < G_N_ELEMENTS(signals); i++) sigaction(signals[i], &action, &previous[i]);
    if (hc_watch_status(r.watch) < 0) hc_watch_start(r.watch);
    Feed feeds[] = {{&r, 'S', NULL}, {&r, 'A', NULL}, {&r, 'F', NULL}, {&r, 'L', NULL}, {&r, 'K', NULL}};
    const char *uris[] = {SETTINGS "getSystemSettings", SAM "listApps", SAM "getForegroundAppInfo", SAM "getAppLifeEvents",
        "luna://com.webos.service.config/getConfigs"};
    const char *payloads[] = {"{\"category\":\"general\",\"keys\":[\"defaultApps\"],\"subscribe\":true}",
        "{\"subscribe\":true}", "{\"subscribe\":true}", "{\"subscribe\":true}",
        "{\"configNames\":[\"com.webos.surfacemanager.keyFilters\"],\"subscribe\":true}"};
    guint tick = 0;
    if (hc_watch_status(r.watch) < 0) {
        /* Every release: the previous-launcher close follows the foreground and life events (previous.c). */
        for (int i = 0; i < 5; i++) {
            if (feeds[i].tag == 'K' && !hc_watch_keys(r.watch)) continue; /* 6-9 only */
            feeds[i].subscription = hc_luna_subscribe(r.luna, uris[i], payloads[i], subscription, &feeds[i]);
            if (!feeds[i].subscription) enqueue(&r, g_ascii_tolower(feeds[i].tag), NULL);
        }
        tick = g_timeout_add_seconds(c.recheck_s, ticker, &r);
        hc_watch_event(r.watch, 'B', NULL);
        wake(&r);
        if (hc_watch_status(r.watch) < 0) g_main_loop_run(r.loop);
    }
    /* An unexpected loop end must trigger unit restart. */
    if (hc_watch_status(r.watch) < 0) hc_watch_stop(r.watch, 1, "watcher event loop ended");
    for (unsigned i = 0; i < G_N_ELEMENTS(feeds); i++) hc_luna_cancel(feeds[i].subscription);
    if (tick) g_source_remove(tick);
    if (r.drain) g_source_remove(r.drain);
    g_source_remove(signal_source);
    for (unsigned i = 0; i < G_N_ELEMENTS(signals); i++) sigaction(signals[i], &previous[i], NULL);
    signal_fd = -1; close(pipefd[0]); close(pipefd[1]);
    Event *event;
    while ((event = g_queue_pop_head(&r.events))) { g_free(event->text); g_free(event); }
    int status = hc_watch_status(r.watch);
    hc_watch_free(r.watch); hc_luna_close(r.luna); g_main_loop_unref(r.loop);
    return status;
}

int hc_watcher_main(void)
{
    HCWatchConfig config = hc_watch_config();
    return hc_watch_run(&config, TRUE);
}
