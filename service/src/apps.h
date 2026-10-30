/* SPDX-License-Identifier: Apache-2.0 */
#ifndef HC_APPS_H
#define HC_APPS_H
#include <glib.h>
#include <pbnjson.h>

#define ICON_MAX_BYTES 262144
#define LIST_FIELDS hc_launcher_list_fields

typedef enum { ICON_OK, ICON_NONE, ICON_TOO_BIG } IconResult;
IconResult load_app_icon(jvalue_ref info, const char *field, size_t max,
    char **bytes, size_t *length, const char **mime);
char *icon_base64(const char *bytes, size_t length);
char *read_choice(void);
jvalue_ref pick_app(jvalue_ref app, const char *id);
jvalue_ref pick_fields(jvalue_ref arr, const char *const *fields);
jvalue_ref pick_running(jvalue_ref arr);
void put_normalized_pid(jvalue_ref out, const char *dest, jvalue_ref item, const char *source);
/* Borrowed appInfo object, or the reply itself when SAM returns a flat object. */
jvalue_ref appinfo_object(jvalue_ref reply);
#endif
