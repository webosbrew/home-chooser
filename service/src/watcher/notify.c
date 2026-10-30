/* SPDX-License-Identifier: Apache-2.0 */
#include "internal.h"

/* Preserve the shell title policy: escaped quotes/backslashes use the ID instead. */
static char *usable_title(jvalue_ref obj)
{
    char *title = get_string(obj, "title");
    if (!title) return NULL;
    if (strchr(title, '"') || strchr(title, '\\')) { g_free(title); return NULL; }
    char *p = title;
    for (char *s = title; *s; s++) if ((unsigned char)*s >= 32) *p++ = *s;
    *p = 0; g_strstrip(title); return title;
}
void remember_title(HCWatch *w, const char *id)
{
    if (!w->c.notify || eq(w->title_id, id)) return;
    jvalue_ref r = watch_id_call(w, SAM "getAppInfo", id, 10);
    if (!reply_ok(r)) { j_release(&r); return; }
    char *title = usable_title(member(r, "appInfo"));
    j_release(&r);
    if (!title || !*title) {
        char **dirs = g_strsplit(w->c.app_dirs, " ", -1);
        for (int i = 0; dirs[i]; i++) {
            if (!*dirs[i]) continue;
            char *p = g_build_filename(dirs[i], id, "appinfo.json", NULL), *text = watch_read(p);
            g_free(p);
            if (!text) continue;
            r = parse_object(text); g_free(text);
            g_free(title); title = usable_title(r); j_release(&r); break;
        }
        g_strfreev(dirs);
    }
    g_free(w->title_id); w->title_id = g_strdup(id);
    g_free(w->title); w->title = title;
}

/* 1 accepted, -1 explicit refusal after retries, 0 no usable answer. */
static int notify_call(HCWatch *w, const char *uri, jvalue_ref payload)
{
    for (unsigned n = 0; ; n++) {
        jvalue_ref r = watch_call(w, uri, jvalue_stringify(payload), 5);
        gboolean ok = reply_ok(r);
        bool value;
        gboolean refused = jboolean_get(member(r, "returnValue"), &value) == CONV_OK && !value;
        watch_log(w, "notification %s: %s", strrchr(uri, '/') + 1, r ? jvalue_stringify(r) : "no reply");
        j_release(&r);
        if (ok) return 1;
        if (!refused) return 0;
        if (n + 1 >= w->c.notify_tries || w->status >= 0) return -1;
        watch_wait(w, w->c.notify_retry_ms);
        if (w->status >= 0) return 0;
    }
}
void watch_notify(HCWatch *w, const char *message, gboolean alert)
{
    if (!w->c.notify) return;
    jvalue_ref p = jobject_create();
    put_str(p, "sourceId", HC_APP_ID);
    if (alert) {
        put_str(p, "title", "Home Chooser"); put_str(p, "message", message);
        jvalue_ref button = jobject_create(), buttons = jarray_create(NULL);
        put_str(button, "label", "OK"); put_bool(button, "focus", true);
        jarray_append(buttons, button); jobject_set(p, key_buf("buttons"), buttons);
    } else {
        put_bool(p, "noaction", true); put_str(p, "message", message);
    }
    int result = notify_call(w, alert ? NOTIFICATION "createAlert" : NOTIFICATION "createToast", p);
    j_release(&p);
    if (alert && result == -1 && w->status < 0) {
        watch_log(w, "the alert was refused, showing the message as a toast");
        watch_notify(w, message, FALSE);
    }
}
void notify_removed(HCWatch *w, const char *id)
{
    const char *name = eq(w->title_id, id) && w->title && *w->title ? w->title : id;
    char *message = g_strdup_printf("%s was removed, so the Home button opens LG Home again.", name);
    watch_notify(w, message, TRUE); g_free(message);
}
