/* SPDX-License-Identifier: Apache-2.0 */
#include "internal.h"
#include "proc.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>

HCWatchConfig hc_watch_config(void)
{
    HCWatchConfig c = {
        .luna_send = "/usr/bin/luna-send", .state_dir = HC_STATE_DIR, .osrelease = HC_OSRELEASE, .app_dir = HC_APP_DIR,
        .override = HC_OVERRIDE_FILE, .init_link = HC_INIT_LINK,
        .runtime = "/tmp/homechooser-runtime", .access_script = "/tmp/homechooser-runtime/homechooser-access",
        .scripts_dir = HC_SERVICE_DIR "/scripts",
        .gate_binary = HC_GATE_BINARY, .gate_dir = HC_GATE_DIR, .proc = "/proc",
        .log = HC_WATCH_LOG, .stock_js = "/usr/lib/qt5/qml/KeyFilters/systemUi.js",
        .app_dirs = "/media/developer/apps/usr/palm/applications /usr/palm/applications",
        .install_dirs = "/media/developer/apps/usr/palm/applications /usr/palm/applications /media/cryptofs/apps/usr/palm/applications",
        .confirm_ms = 15000, .interval_s = 10, .backoff_s = 300, .recheck_s = 300,
        .preload_retry_ms = 10000, .preload_settle_ms = 45000, .preload_tries = 12,
        .notify_retry_ms = 3000, .notify_tries = 8, .log_max = 65536,
        .preload = TRUE, .notify = TRUE
    };
    return c;
}

char *watch_read(const char *path)
{
    char *text = NULL;
    slurp(path, 256 * 1024, &text, NULL);
    return text;
}

static void rotate_log(const char *path)
{
    FILE *f = fopen(path, "r");
    if (!f) return;
    GQueue lines = G_QUEUE_INIT;
    char *line = NULL;
    size_t capacity = 0;
    ssize_t size;
    while ((size = getline(&line, &capacity, f)) >= 0) {
        g_queue_push_tail(&lines, g_strndup(line, size));
        if (g_queue_get_length(&lines) > 200) g_free(g_queue_pop_head(&lines));
    }
    free(line); fclose(f);
    GString *tail = g_string_new(NULL);
    while ((line = g_queue_pop_head(&lines))) { g_string_append(tail, line); g_free(line); }
    write_atomic(path, tail->str, tail->len);
    g_string_free(tail, TRUE);
}

void watch_log(HCWatch *w, const char *format, ...)
{
    va_list ap; va_start(ap, format);
    char *message = g_strdup_vprintf(format, ap); va_end(ap);
    GDateTime *now = g_date_time_new_now_local();
    char *stamp = g_date_time_format(now, "%Y-%m-%d %H:%M:%S");
    FILE *f = fopen(w->c.log, "a");
    long size = 0;
    if (f) { fprintf(f, "%s %s\n", stamp, message); size = ftell(f); fclose(f); }
    if (size > (long)w->c.log_max) rotate_log(w->c.log);
    g_free(stamp); g_date_time_unref(now); g_free(message);
}

static int major_version(const char *file)
{
    /* os-release is normally a symlink. Read the trusted platform path here. */
    char *text = NULL;
    g_file_get_contents(file, &text, NULL, NULL);
    if (!text) return 0;
    const char *keys[] = {"VERSION_ID", "VERSION", "PRETTY_NAME", NULL};
    int major = 0;
    for (int i = 0; keys[i] && !major; i++) {
        char *pattern = g_strdup_printf("(?m)^%s=[^0-9\\n]*([0-9]+)", keys[i]);
        GRegex *rx = g_regex_new(pattern, 0, 0, NULL);
        GMatchInfo *match = NULL;
        if (g_regex_match(rx, text, 0, &match)) {
            char *v = g_match_info_fetch(match, 1); major = atoi(v); g_free(v);
        }
        g_match_info_free(match); g_regex_unref(rx); g_free(pattern);
    }
    g_free(text); return major;
}

HCWatch *hc_watch_new(const HCWatchConfig *c, const HCWatchOps *ops)
{
    HCWatch *w = g_new0(HCWatch, 1);
    w->c = *c; w->ops = *ops; w->status = -1;
    w->major = major_version(c->osrelease);
    w->choice_file = g_build_filename(c->state_dir, "home", NULL);
    return w;
}

char *watch_choice(HCWatch *w)
{
    char *text = watch_read(w->choice_file);
    if (!text) return NULL;
    char *p = text, *out = text;
    for (; *p; p++) {
        if (*p == '\r') continue;
        *out++ = (*p == '\n' || *p == '\t') ? ' ' : *p;
    }
    *out = 0;
    /* Only spaces are trimmed by the original choice reader. */
    while (out > text && out[-1] == ' ') *--out = 0;
    p = text; while (*p == ' ') p++;
    if (!*p || strspn(p, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-") != strlen(p)) {
        if (*p) watch_log(w, "ignoring invalid app id in %s", w->choice_file);
        g_free(text); return NULL;
    }
    memmove(text, p, strlen(p) + 1); return text;
}

static gboolean wait_done(gpointer context)
{
    g_main_loop_quit(context); return G_SOURCE_REMOVE;
}
void watch_wait(HCWatch *w, unsigned ms)
{
    if (w->status >= 0) return;
    if (w->ops.wait) { w->ops.wait(ms, w->ops.context); return; }
    GMainLoop *loop = g_main_loop_new(NULL, FALSE);
    GSource *timer = g_timeout_source_new(ms);
    g_source_set_callback(timer, wait_done, loop, NULL); g_source_attach(timer, NULL);
    w->waiting = loop;
    g_main_loop_run(loop);
    w->waiting = NULL;
    g_source_destroy(timer); g_source_unref(timer); g_main_loop_unref(loop);
}
gint64 watch_now(HCWatch *w)
{
    return w->ops.now ? w->ops.now(w->ops.context) : g_get_real_time() / G_USEC_PER_SEC;
}
jvalue_ref watch_call(HCWatch *w, const char *uri, const char *payload, int timeout)
{
    char *text = w->ops.call(uri, payload, timeout, w->ops.context);
    jvalue_ref r = parse_object(text);
    g_free(text); return r;
}
jvalue_ref watch_id_call(HCWatch *w, const char *method, const char *id, int timeout)
{
    jvalue_ref p = jobject_create(); put_str(p, "id", id);
    jvalue_ref r = watch_call(w, method, jvalue_stringify(p), timeout);
    j_release(&p); return r;
}
int watch_access(HCWatch *w, const char *action)
{
    if (w->ops.access) return w->ops.access(action, w->ops.context);
    const char *const argv[] = {"/bin/sh", w->c.access_script, action, NULL};
    GString *out = g_string_new(NULL), *err = g_string_new(NULL);
    int rc = run_argv(argv, 30, out, err, NULL);
    if (out->len || err->len) watch_log(w, "launcher access %s: %s%s", action, out->str, err->str);
    g_string_free(out, TRUE); g_string_free(err, TRUE); return rc;
}
void watch_remove_tree(const char *path)
{
    struct stat st;
    if (lstat(path, &st)) return;
    if (S_ISDIR(st.st_mode)) {
        GDir *d = g_dir_open(path, 0, NULL);
        const char *name;
        while (d && (name = g_dir_read_name(d))) {
            char *child = g_build_filename(path, name, NULL);
            watch_remove_tree(child); g_free(child);
        }
        if (d) g_dir_close(d);
        rmdir(path);
    } else unlink(path);
}

void hc_watch_stop(HCWatch *w, int status, const char *reason)
{
    if (w->status >= 0) return;
    watch_log(w, "%s", reason);
    w->status = status;
    if (w->waiting) g_main_loop_quit(w->waiting);
}
int hc_watch_status(HCWatch *w) { return w->status; }
gboolean hc_watch_idle(HCWatch *w) { return w->depth == 0; }

void hc_watch_start(HCWatch *w)
{
    w->depth++;
    if (watch_access(w, "apply")) watch_log(w, "could not apply launcher access");
    if (!g_file_test(w->c.app_dir, G_FILE_TEST_IS_DIR)) check_hc(w);
    if (w->status >= 0) goto done;
    char *id = watch_choice(w);
    if (!id) { repair_leftover(w); id = watch_choice(w); }
    if (w->status >= 0) { g_free(id); goto done; }
    if (!id) { w->early_exit = TRUE; hc_watch_stop(w, 0, "no usable choice file, nothing to watch"); goto done; }
    watch_log(w, "watching, home=%s, webOS %d", id, w->major);
    g_free(id);
    check_apps(w, 's');
    if (w->status >= 0) goto done;
    id = watch_choice(w);
    if (!id) hc_watch_stop(w, 0, "choice file is gone, exiting");
    else if (!w->missing) check_override(w, id);
    g_free(id);
done:
    w->depth--;
}

void hc_watch_event(HCWatch *w, char tag, const char *text)
{
    if (w->status >= 0) return;
    w->depth++;
    if (tag == 's') hc_watch_stop(w, 1, "subscription ended");
    else if (tag == 'a') watch_log(w, "apps subscription ended, relying on the periodic check");
    else if (tag == 'f' || tag == 'l') watch_log(w, "stock-home close subscription ended; leaving stock home running");
    else if (tag == 'A' || tag == 'T') check_apps(w, tag);
    else if (tag == 'B') close_foreground(w);
    else {
        jvalue_ref r = parse_object(text);
        if (tag == 'F') handle_foreground(w, r);
        else if (tag == 'L') handle_life(w, r);
        else if (tag == 'S' && (jis_object(member(r, "settings")) || (text && strstr(text, "no matched result")))) {
            char *id = watch_choice(w);
            if (!id) hc_watch_stop(w, 0, "choice file is gone, exiting");
            else if (!eq(w->missing, id)) {
                if (w->missing) {
                    g_clear_pointer(&w->missing, g_free);
                    if (!key_refresh(w, FALSE)) { g_free(w->baked); w->baked = g_strdup(id); }
                }
                preload_follow(w, id, 0); close_foreground(w); sync_baked(w, id); gate_sync(w);
                char *found = record_home(r);
                if (!eq(found, id)) reconcile(w, id);
                g_free(found);
            }
            g_free(id);
        }
        j_release(&r);
    }
    w->depth--;
}

void hc_watch_free(HCWatch *w)
{
    /* Match clean finish: a failed watcher leaves preload state for its restart. */
    if (w->status == 0 && !w->early_exit) preload_sync(w, 1, 'e');
    /* Any clean end leaves stock Home in place; a failed watcher keeps it for its restart. */
    if (w->status == 0) gate_apply(w, NULL);
    if (w->preload_source) g_source_remove(w->preload_source);
    if (w->gate_source) g_source_remove(w->gate_source);
    g_free(w->choice_file); g_free(w->missing); g_free(w->baked);
    g_free(w->title_id); g_free(w->title); g_free(w->close_choice); g_free(w);
}
