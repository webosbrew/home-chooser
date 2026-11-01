/* SPDX-License-Identifier: Apache-2.0 */
/* webOS 10 and later, overlay launcher: the last TV input, once per boot (docs/settings-app.md). The
 * override's idleApp support false makes SAM fall back to the last input when the foreground empties,
 * but SAM refuses that fallback until the first app launch of the boot, so after a reboot the overlay
 * draws over an empty foreground. One launch of the last input, chosen as the 6-9 home gate chooses
 * it (last-input.c), puts a full-screen input beneath the overlay and unlocks the fallback. */
#include "internal.h"
#include "last-input.h"

static char *input_marker(HCWatch *w)
{
    return g_build_filename(w->c.runtime, "last-input", NULL);
}
/* The decision for this boot is taken: the marker (tmpfs, gone after a reboot) holds the outcome. */
static void input_mark(HCWatch *w, const char *outcome)
{
    char *marker = input_marker(w);
    if (!mkdir_p(w->c.runtime) || !write_atomic(marker, outcome, strlen(outcome)))
        watch_log(w, "last input: cannot write %s", marker);
    g_free(marker);
    if (w->input_source) { g_source_remove(w->input_source); w->input_source = 0; }
}
static gboolean input_check(gpointer context);
/* SAM did not answer yet: again on the preload cadence, bounded. */
static void input_retry(HCWatch *w, const char *what)
{
    if (++w->input_attempts < w->c.preload_tries) {
        w->input_source = g_timeout_add(w->c.preload_retry_ms, input_check, w); return;
    }
    input_mark(w, "no answer");
    watch_log(w, "last input: %s did not answer in %u tries; nothing launched this boot", what, w->input_attempts);
}
/* Marked before the launch: a failure or a restart of the watcher never launches again this boot. */
static void input_launch(HCWatch *w, const char *beneath)
{
    jvalue_ref r = watch_call(w, HC_LAST_INPUT_URI, HC_LAST_INPUT_PAYLOAD, 5);
    const char *why = NULL;
    char *id = hc_last_input(r, &why);
    j_release(&r);
    if (why) watch_log(w, "last input: %s, using %s", why, id);
    input_mark(w, id);
    jvalue_ref l = watch_id_call(w, SAM "launch", id, 5);
    if (reply_ok(l)) watch_log(w, "last input: launched %s over %s: %s", id, beneath, jvalue_stringify(l));
    else watch_log(w, "last input: launch of %s failed: %s; not retried this boot", id, l ? jvalue_stringify(l) : "no reply");
    j_release(&l); g_free(id);
}
static void input_decide(HCWatch *w)
{
    char *id = watch_choice(w), *front = NULL;
    jvalue_ref r = NULL;
    int window = id && !stock_id(id) && !w->missing ? window_type(w, id) : -1;
    if (window == -1) {
        input_mark(w, "no launcher");
        watch_log(w, "last input: no third-party launcher in use; nothing launched this boot");
    } else if (!window) input_retry(w, "getAppInfo");
    else if (window == 1) {
        input_mark(w, "full-screen");
        watch_log(w, "last input: %s is full-screen; nothing launched this boot", id);
    } else if (!reply_ok(r = watch_call(w, SAM "getForegroundAppInfo", "{}", 5))) input_retry(w, "getForegroundAppInfo");
    else if ((front = get_string(r, "appId")) && *front && !eq(front, id)) {
        input_mark(w, "in front");
        watch_log(w, "last input: %s in front; nothing launched this boot", front);
    } else input_launch(w, front && *front ? "the overlay launcher alone" : "an empty foreground");
    j_release(&r); g_free(front); g_free(id);
}
static gboolean input_check(gpointer context)
{
    HCWatch *w = context;
    w->input_source = 0;
    if (w->status >= 0) return G_SOURCE_REMOVE;
    w->depth++;
    input_decide(w);
    w->depth--;
    if (w->ops.wake) w->ops.wake(w->ops.context);
    return G_SOURCE_REMOVE;
}

/* At watcher start: once per boot, after input_settle_ms. A watcher restart finds the marker. */
void input_arm(HCWatch *w)
{
    if (w->major < 10 || w->input_source) return;
    char *marker = input_marker(w);
    gboolean taken = g_file_test(marker, G_FILE_TEST_EXISTS);
    g_free(marker);
    if (taken) return;
    w->input_attempts = 0;
    w->input_source = g_timeout_add(w->c.input_settle_ms, input_check, w);
}
/* A foreground event while the check is pending: an app other than the overlay launcher came up (the
 * TV's or the owner's), so SAM has had its first launch; leave it in front. */
void input_seen(HCWatch *w, const char *foreground)
{
    if (!w->input_source || !foreground || !*foreground) return;
    char *id = watch_choice(w);
    if (!eq(foreground, id)) {
        input_mark(w, "in front");
        watch_log(w, "last input: %s came to the front; nothing launched this boot", foreground);
    }
    g_free(id);
}
