/* SPDX-License-Identifier: Apache-2.0 */
#include <glib.h>
#include <string.h>
#include "json.h"

raw_buffer key_buf(const char *k) { return j_str_to_buffer(k, strlen(k)); }

jvalue_ref parse_object(const char *text)
{
	jvalue_ref v = jdom_create(j_str_to_buffer(text ? text : "", text ? strlen(text) : 0), jschema_all(), NULL);
	if (!jis_valid(v) || !jis_object(v)) {
		j_release(&v);
		return NULL;
	}
	return v;
}

/* Copy of a string member; NULL when absent or not a string. Free with g_free. */
char *get_string(jvalue_ref obj, const char *key)
{
	jvalue_ref v = jobject_get(obj, key_buf(key));
	if (!jis_valid(v) || !jis_string(v)) return NULL;
	raw_buffer b = jstring_get_fast(v);
	return g_strndup(b.m_str, b.m_len);
}

void put_str(jvalue_ref o, const char *k, const char *v) { jobject_set(o, key_buf(k), jstring_create(v ? v : "")); }
void put_bool(jvalue_ref o, const char *k, bool v) { jobject_set(o, key_buf(k), jboolean_create(v)); }
void put_int(jvalue_ref o, const char *k, int v) { jobject_set(o, key_buf(k), jnumber_create_i32(v)); }

bool reply_ok(jvalue_ref r)
{
	jvalue_ref rv = r ? jobject_get(r, key_buf("returnValue")) : NULL;
	bool v = false;
	return rv && jis_valid(rv) && jis_boolean(rv) && jboolean_get(rv, &v) == CONV_OK && v;
}
