/* SPDX-License-Identifier: Apache-2.0 */
#include "gate/gate.h"
#include "json.h"
#include "policy.h"
#include "proc.h"
#include <string.h>

#define SAM "luna://com.webos.applicationManager/"
#define LIVETV "com.webos.app.livetv"

static gboolean stock_id(const char *id)
{
    return !g_strcmp0(id, HC_STOCK_HOME_ID) || !g_strcmp0(id, "com.webos.app.fullhome");
}

jvalue_ref hc_gate_call(const HCGateConfig *c, const char *identity, const char *uri, const char *payload, int timeout_s)
{
    const char *argv[] = {c->luna_send, "-a", identity, "-n", "1", uri, payload, NULL};
    GString *out = g_string_new(NULL), *err = g_string_new(NULL);
    int rc = run_argv(argv, timeout_s, out, err, NULL);
    jvalue_ref r = rc == 0 ? parse_object(out->str) : NULL;
    g_string_free(out, TRUE); g_string_free(err, TRUE); return r;
}
static jvalue_ref id_call(const HCGateConfig *c, const char *identity, const char *uri, const char *id, int timeout_s)
{
    jvalue_ref p = jobject_create(); put_str(p, "id", id);
    jvalue_ref r = hc_gate_call(c, identity, uri, jvalue_stringify(p), timeout_s);
    j_release(&p); return r;
}

static HCGateDecision decided(HCGateAction action, const char *target, char *why)
{
    HCGateDecision d = {action, g_strdup(target), why, NULL};
    return d;
}
void hc_gate_decision_clear(HCGateDecision *d)
{
    g_clear_pointer(&d->target, g_free); g_clear_pointer(&d->why, g_free); g_clear_pointer(&d->choice, g_free);
}

static char *read_choice(const HCGateConfig *c)
{
    char *path = g_build_filename(c->state_dir, "home", NULL), *text = NULL;
    if (g_file_get_contents(path, &text, NULL, NULL)) g_strstrip(text);
    g_free(path);
    if (text && !hc_app_id_ok(text)) g_clear_pointer(&text, g_free);
    return text;
}
static gboolean preload(jvalue_ref env)
{
    jvalue_ref v = jobject_get(env, key_buf("preload"));
    bool on = false;
    if (jis_string(v)) return jstring_get_fast(v).m_len > 0;
    return jis_boolean(v) && jboolean_get(v, &on) == CONV_OK && on;
}
/* lastSourceAppId; Live TV when it is empty, stock Home, or eim does not answer. */
static char *last_input(const HCGateConfig *c)
{
    jvalue_ref r = hc_gate_call(c, HC_LUNA_IDENTITY, "luna://com.webos.service.eim/getLastInput", "{}", c->query_timeout_s);
    char *id = reply_ok(r) ? get_string(r, "lastSourceAppId") : NULL;
    j_release(&r);
    if (!hc_app_id_ok(id) || stock_id(id)) { g_free(id); id = g_strdup(LIVETV); }
    return id;
}

HCGateDecision hc_gate_decide(const HCGateConfig *c, const char *envelope)
{
    HCGateDecision d;
    jvalue_ref env = parse_object(envelope), info = NULL, fg = NULL;
    /* Read first: a release records the choice it happened under, whatever the cause. */
    char *reason = get_string(env, "reason"), *choice = read_choice(c), *flag = g_build_filename(c->state_dir, "homekey", NULL);
    if (!env) { d = decided(HC_GATE_RELEASE, NULL, g_strdup("no launch envelope")); goto done; }
    /* A background preload must not bring anything to the front (the watcher turns preload off). */
    if (preload(env)) { d = decided(HC_GATE_NOTHING, NULL, g_strdup("preload launch ignored")); goto done; }
    if (!choice) { d = decided(HC_GATE_RELEASE, NULL, g_strdup("no valid choice")); goto done; }
    if (stock_id(choice)) { d = decided(HC_GATE_RELEASE, NULL, g_strdup("LG Home chosen")); goto done; }
    if (!g_file_test(flag, G_FILE_TEST_EXISTS)) { d = decided(HC_GATE_RELEASE, NULL, g_strdup("Home redirect disabled")); goto done; }
    info = id_call(c, HC_LUNA_IDENTITY, SAM "getAppInfo", choice, c->query_timeout_s);
    if (!reply_ok(info)) {
        d = decided(HC_GATE_RELEASE, NULL, g_strdup_printf(info ? "%s is not installed" : "getAppInfo did not answer for %s", choice));
        goto done;
    }
    char *window = get_string(jobject_get(info, key_buf("appInfo")), "defaultWindowType");
    gboolean overlay = !g_strcmp0(window, "overlay");
    g_free(window);
    if (!g_strcmp0(reason, "hotKey")) { d = decided(HC_GATE_LAUNCH, choice, g_strdup("Home key")); goto done; }
    if (!overlay) { d = decided(HC_GATE_LAUNCH, choice, g_strdup("full-screen launcher")); goto done; }
    fg = hc_gate_call(c, HC_LUNA_IDENTITY, SAM "getForegroundAppInfo", "{}", c->query_timeout_s);
    if (!reply_ok(fg)) { d = decided(HC_GATE_RELEASE, NULL, g_strdup("no foreground answer")); goto done; }
    char *front = get_string(fg, "appId");
    if (front && *front) d = decided(HC_GATE_LAUNCH, choice, g_strdup_printf("overlay launcher over %s", front));
    else {
        char *input = last_input(c);
        d = decided(HC_GATE_LAUNCH, input, g_strdup("overlay launcher, nothing in front: last input"));
        g_free(input);
    }
    g_free(front);
done:;
    char *why = g_strdup_printf("%s (reason %s)", d.why, reason ? reason : "none");
    g_free(d.why); d.why = why; d.choice = g_strdup(choice);
    j_release(&env); j_release(&info); j_release(&fg);
    g_free(reason); g_free(choice); g_free(flag); return d;
}
