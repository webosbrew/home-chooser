/* SPDX-License-Identifier: Apache-2.0 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <glib-unix.h>
#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>
#include "json.h"
#include "log.h"
#include "luna.h"
#include "policy.h"
#include "proc.h"

#define REPLY_LIMIT (1024 * 1024)

typedef struct {
    HCSubscription *owner;
    GIOChannel *channel;
    guint source;
    gboolean error;
} ChildIO;
struct HCLuna {
    char *program;
    GList *children;
    int signals[2];
    guint source;
    struct sigaction previous;
};
struct HCSubscription {
    HCLuna *client;
    GPid pid;
    int status;
    gboolean exited, eof, ended, one_shot;
    ChildIO out, err;
    HCSubscriptionReply reply;
    void *context;
    char *uri;
    GString *json;
    int depth;
    gboolean quoted, escaped, overflow;
};

/* GLib's child-watch helper starts a signal worker thread. Use a self-pipe and
 * waitpid for our own PIDs, leaving proc.c's synchronous children to their owner. */
static volatile sig_atomic_t child_signal_fd = -1;
static void child_signal(int number)
{
    (void)number;
    int saved = errno;
    char byte = 1;
    if (child_signal_fd >= 0) {
        ssize_t ignored = write(child_signal_fd, &byte, 1); (void)ignored;
    }
    errno = saved;
}
static void reap(HCSubscription *s, gboolean kill_child)
{
    if (s->exited) return;
    if (kill_child) kill(s->pid, SIGKILL);
    pid_t got;
    do { got = waitpid(s->pid, &s->status, kill_child ? 0 : WNOHANG); } while (got < 0 && errno == EINTR);
    if (got == s->pid || (got < 0 && errno == ECHILD)) {
        s->exited = TRUE;
        g_spawn_close_pid(s->pid);
    }
}
static void maybe_end(HCSubscription *s)
{
    if (s->ended) return;
    if (s->one_shot ? (s->eof && s->exited) : (s->eof || s->exited)) {
        /* An EOF child can still be alive (stdout closed before a hang). */
        if (!s->one_shot) reap(s, TRUE);
        s->ended = TRUE;
        s->reply(NULL, TRUE, s->context);
    }
}
static void reset_json(HCSubscription *s)
{
    g_string_truncate(s->json, 0);
    s->depth = 0; s->quoted = s->escaped = s->overflow = FALSE;
}
static void json_line(HCSubscription *s, const char *line)
{
    if (s->ended) return;
    /* Compact replies start at column zero; they can resync a truncated frame.
     * Indented object lines inside a pretty reply must stay in that frame. */
    jvalue_ref value = !s->depth || *line == '{' ? parse_object(line) : NULL;
    if (value) {
        reset_json(s);
        s->reply(line, FALSE, s->context);
        j_release(&value); return;
    }
    const char *first = line;
    while (g_ascii_isspace(*first)) first++;
    if (!s->depth && *first != '{') return;
    for (const char *p = first; *p; p++) {
        if (!s->overflow) {
            if (s->json->len == REPLY_LIMIT) { s->overflow = TRUE; g_string_truncate(s->json, 0); }
            else g_string_append_c(s->json, *p);
        }
        if (s->quoted) {
            if (s->escaped) s->escaped = FALSE;
            else if (*p == '\\') s->escaped = TRUE;
            else if (*p == '"') s->quoted = FALSE;
        } else if (*p == '"') s->quoted = TRUE;
        else if (*p == '{' || *p == '[') s->depth++;
        else if (*p == '}' || *p == ']') s->depth--;
        if (s->depth <= 0) {
            value = s->overflow ? NULL : parse_object(s->json->str);
            if (value) s->reply(s->json->str, FALSE, s->context);
            j_release(&value); reset_json(s);
            break;
        }
    }
}
static gboolean drain_io(ChildIO *io)
{
    HCSubscription *s = io->owner;
    for (;;) {
        char *line = NULL;
        GError *error = NULL;
        GIOStatus status = g_io_channel_read_line(io->channel, &line, NULL, NULL, &error);
        if (line) {
            if (io->error) LOGI("WATCH_LUNA_STDERR", "%s: %.4096s", s->uri, line);
            else json_line(s, line);
            g_free(line);
        }
        if (error) { LOGE("WATCH_LUNA_IO", "%s: %s", s->uri, error->message); g_error_free(error); }
        if (status == G_IO_STATUS_AGAIN) return TRUE;
        if (status == G_IO_STATUS_EOF || status == G_IO_STATUS_ERROR) {
            if (!io->error) s->eof = TRUE;
            return FALSE;
        }
    }
}
static gboolean child_io(GIOChannel *channel, GIOCondition condition, gpointer context)
{
    (void)channel; (void)condition;
    ChildIO *io = context;
    gboolean keep = drain_io(io);
    if (!keep) io->source = 0;
    maybe_end(io->owner);
    return keep;
}
static gboolean child_exited(gint fd, GIOCondition condition, gpointer context)
{
    (void)condition;
    HCLuna *c = context;
    char bytes[64]; while (read(fd, bytes, sizeof bytes) > 0) {}
    for (GList *l = c->children; l; l = l->next) {
        HCSubscription *s = l->data;
        reap(s, FALSE);
        if (!s->exited) continue;
        ChildIO *streams[] = {&s->out, &s->err};
        for (unsigned i = 0; i < G_N_ELEMENTS(streams); i++) {
            ChildIO *io = streams[i];
            if (io->source && !drain_io(io)) { g_source_remove(io->source); io->source = 0; }
        }
        maybe_end(s);
    }
    return G_SOURCE_CONTINUE;
}
HCLuna *hc_luna_open(GMainLoop *loop, const char *program)
{
    (void)loop;
    if (child_signal_fd >= 0) return NULL; /* one owning watcher per process */
    HCLuna *c = g_new0(HCLuna, 1);
    if (pipe2(c->signals, O_CLOEXEC | O_NONBLOCK)) { g_free(c); return NULL; }
    c->program = g_strdup(program ? program : "/usr/bin/luna-send");
    struct sigaction action = {0};
    action.sa_handler = child_signal; sigemptyset(&action.sa_mask); action.sa_flags = SA_NOCLDSTOP;
    if (sigaction(SIGCHLD, &action, &c->previous)) {
        close(c->signals[0]); close(c->signals[1]); g_free(c->program); g_free(c); return NULL;
    }
    child_signal_fd = c->signals[1];
    c->source = g_unix_fd_add(c->signals[0], G_IO_IN, child_exited, c);
    return c;
}
void hc_luna_cancel(HCSubscription *s)
{
    if (!s) return;
    s->client->children = g_list_remove(s->client->children, s);
    ChildIO *streams[] = {&s->out, &s->err};
    for (unsigned i = 0; i < G_N_ELEMENTS(streams); i++) {
        ChildIO *io = streams[i];
        if (io->source) g_source_remove(io->source);
        if (io->channel) g_io_channel_unref(io->channel);
    }
    reap(s, TRUE);
    g_string_free(s->json, TRUE); g_free(s->uri); g_free(s);
}
void hc_luna_close(HCLuna *c)
{
    if (!c) return;
    while (c->children) hc_luna_cancel(c->children->data);
    g_source_remove(c->source);
    sigaction(SIGCHLD, &c->previous, NULL);
    child_signal_fd = -1;
    close(c->signals[0]); close(c->signals[1]); g_free(c->program); g_free(c);
}
static gboolean setup_io(HCSubscription *s, ChildIO *io, int fd, gboolean error_stream)
{
    io->owner = s; io->error = error_stream;
    io->channel = g_io_channel_unix_new(fd);
    g_io_channel_set_close_on_unref(io->channel, TRUE);
    if (g_io_channel_set_encoding(io->channel, NULL, NULL) != G_IO_STATUS_NORMAL ||
        g_io_channel_set_flags(io->channel, G_IO_FLAG_NONBLOCK, NULL) != G_IO_STATUS_NORMAL) return FALSE;
    io->source = g_io_add_watch(io->channel, G_IO_IN | G_IO_HUP | G_IO_ERR | G_IO_NVAL, child_io, io);
    return io->source != 0;
}
static HCSubscription *start_child(HCLuna *c, const char *uri, const char *payload,
    HCSubscriptionReply reply, void *context, gboolean one_shot)
{
    if (!payload || !hc_luna_uri_allowed(uri)) return NULL;
    char *one[] = {c->program, "-a", HC_LUNA_IDENTITY, "-n", "1", (char *)uri, (char *)payload, NULL};
    char *stream[] = {c->program, "-a", HC_LUNA_IDENTITY, "-i", (char *)uri, (char *)payload, NULL};
    char *env[] = {"PATH=/usr/sbin:/usr/bin:/sbin:/bin", "LANG=C", "HOME=/root", NULL};
    HCSubscription *s = g_new0(HCSubscription, 1);
    s->client = c; s->reply = reply; s->context = context; s->one_shot = one_shot;
    s->json = g_string_new(NULL); s->uri = g_strdup(uri);
    int out, err;
    GError *error = NULL;
    pid_t parent = getpid();
    if (!g_spawn_async_with_pipes(NULL, one_shot ? one : stream, env, G_SPAWN_DO_NOT_REAP_CHILD,
        child_setup, &parent, &s->pid, NULL, &out, &err, &error)) {
        LOGE("WATCH_LUNA_SPAWN", "%s: %s", uri, error->message);
        g_error_free(error); g_string_free(s->json, TRUE); g_free(s->uri); g_free(s); return NULL;
    }
    c->children = g_list_prepend(c->children, s);
    gboolean out_ok = setup_io(s, &s->out, out, FALSE);
    gboolean err_ok = setup_io(s, &s->err, err, TRUE);
    if (!out_ok || !err_ok) { hc_luna_cancel(s); return NULL; }
    return s;
}
HCSubscription *hc_luna_subscribe(HCLuna *c, const char *uri, const char *payload,
    HCSubscriptionReply reply, void *context)
{
    return start_child(c, uri, payload, reply, context, FALSE);
}
HCSubscription *hc_luna_once(HCLuna *c, const char *uri, const char *payload,
    HCSubscriptionReply reply, void *context)
{
    return start_child(c, uri, payload, reply, context, TRUE);
}
typedef struct { GMainLoop *loop; char *text; gboolean timed_out; } OneReply;
static void one_reply(const char *text, gboolean ended, void *context)
{
    OneReply *call = context;
    if (ended) g_main_loop_quit(call->loop);
    else if (!call->text) call->text = g_strdup(text);
}
static gboolean call_timeout(gpointer context)
{
    OneReply *call = context;
    call->timed_out = TRUE; g_main_loop_quit(call->loop); return G_SOURCE_REMOVE;
}
char *hc_luna_call(const char *uri, const char *payload, int timeout_s, void *context)
{
    HCLuna *c = context;
    OneReply call = {.loop = g_main_loop_new(NULL, FALSE)};
    HCSubscription *s = start_child(c, uri, payload, one_reply, &call, TRUE);
    if (s) {
        GSource *timer = g_timeout_source_new(MAX(timeout_s, 0) * 1000);
        g_source_set_callback(timer, call_timeout, &call, NULL); g_source_attach(timer, NULL);
        g_main_loop_run(call.loop);
        g_source_destroy(timer); g_source_unref(timer);
        if (call.timed_out || !s->exited || !WIFEXITED(s->status) || WEXITSTATUS(s->status))
            g_clear_pointer(&call.text, g_free);
        hc_luna_cancel(s);
    }
    g_main_loop_unref(call.loop); return call.text;
}
