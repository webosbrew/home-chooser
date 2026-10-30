/* SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <stdlib.h>
#include "apps.h"
#include "fs.h"
#include "json.h"
#include "policy.h"

static const char *const RUNNING_FIELDS[] = {"id", "instanceId", "processId", "webProcessId", NULL};

IconResult load_app_icon(jvalue_ref info, const char *field, size_t max,
	char **bytes, size_t *length, const char **mime)
{
	jvalue_ref inner = info ? jobject_get(info, key_buf("appInfo")) : NULL;
	if (inner && !(jis_valid(inner) && jis_object(inner))) inner = NULL;
	jvalue_ref src = inner ? inner : info;
	char *folder = src ? get_string(src, "folderPath") : NULL;
	char *icon = src ? get_string(src, field) : NULL;
	char resolved[PATH_MAX];
	*mime = icon ? hc_icon_mime(icon) : NULL;
	IconResult result = ICON_NONE;
	if (folder && icon && *mime && hc_icon_resolve(folder, icon, resolved, sizeof resolved)) {
		bool too_big = false;
		ssize_t n = slurp(resolved, max, bytes, &too_big);
		if (n >= 0) { *length = (size_t)n; result = ICON_OK; }
		else if (too_big) result = ICON_TOO_BIG;
	}
	g_free(folder);
	g_free(icon);
	return result;
}

char *icon_base64(const char *bytes, size_t length)
{
	size_t outlen = 4 * ((length + 2) / 3) + 1;
	char *b64 = g_malloc(outlen);
	hc_base64((const unsigned char *)bytes, length, b64, outlen);
	return b64;
}

char *read_choice(void)
{
	char *buf = NULL;
	return slurp(HC_HOME_FILE, 4096, &buf, NULL) >= 0 ? buf : NULL;
}

jvalue_ref pick_app(jvalue_ref app, const char *id)
{
	jvalue_ref out = jobject_create();
	for (int f = 0; LIST_FIELDS[f]; f++) {
		jvalue_ref v = jobject_get(app, key_buf(LIST_FIELDS[f]));
		if (v && jis_valid(v) && !jis_null(v)) jobject_set(out, key_buf(LIST_FIELDS[f]), jvalue_duplicate(v));
	}
	jvalue_ref existing = jobject_get(out, key_buf("id"));
	if (!existing || !jis_valid(existing)) put_str(out, "id", id);
	return out;
}

void put_normalized_pid(jvalue_ref out, const char *dest, jvalue_ref item, const char *source)
{
	jvalue_ref v = jobject_get(item, key_buf(source));
	if (!v || !jis_valid(v) || jis_null(v)) return;
	if (jis_number(v)) {
		int64_t pid = 0;
		if (jnumber_get_i64(v, &pid) == CONV_OK && pid >= 0)
			jobject_set(out, key_buf(dest), jnumber_create_i64(pid));
	} else if (jis_string(v)) {
		char *value = get_string(item, source);
		char *end = NULL;
		errno = 0;
		long long pid = value ? strtoll(value, &end, 10) : 0;
		if (value && end && end != value && !*end && !errno && pid >= 0)
			jobject_set(out, key_buf(dest), jnumber_create_i64(pid));
		g_free(value);
	}
}

/* Keep only the listed fields of each object of an array. */
jvalue_ref pick_fields(jvalue_ref arr, const char *const *fields)
{
	jvalue_ref out = jarray_create(NULL);
	ssize_t len = arr && jis_valid(arr) && jis_array(arr) ? jarray_size(arr) : 0;
	for (ssize_t i = 0; i < len; i++) {
		jvalue_ref app = jarray_get(arr, i);
		if (!jis_object(app)) continue;
		jvalue_ref o = jobject_create();
		for (int f = 0; fields[f]; f++) {
			jvalue_ref v = jobject_get(app, key_buf(fields[f]));
			if (v && jis_valid(v) && !jis_null(v)) jobject_set(o, key_buf(fields[f]), jvalue_duplicate(v));
		}
		jarray_append(out, o);
	}
	return out;
}

/* SAM uses lowercase pid keys. Publish stable names and numeric types. */
jvalue_ref pick_running(jvalue_ref arr)
{
	jvalue_ref out = jarray_create(NULL);
	ssize_t len = jarray_size(arr);
	for (ssize_t i = 0; i < len; i++) {
		jvalue_ref item = jarray_get(arr, i);
		if (!jis_object(item)) continue;
		jvalue_ref o = jobject_create();
		for (int f = 0; RUNNING_FIELDS[f]; f++) {
			const char *source = f == 2 ? "processid" : f == 3 ? "webprocessid" : RUNNING_FIELDS[f];
			jvalue_ref v = jobject_get(item, key_buf(source));
			if (!v || !jis_valid(v) || jis_null(v)) continue;
			if (f < 2) {
				if (jis_string(v)) jobject_set(o, key_buf(RUNNING_FIELDS[f]), jvalue_duplicate(v));
			} else put_normalized_pid(o, RUNNING_FIELDS[f], item, source);
		}
		jarray_append(out, o);
	}
	return out;
}

jvalue_ref appinfo_object(jvalue_ref reply)
{
	jvalue_ref info = reply ? jobject_get(reply, key_buf("appInfo")) : NULL;
	return info && jis_valid(info) && jis_object(info) ? info : reply;
}
