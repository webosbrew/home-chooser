/* SPDX-License-Identifier: Apache-2.0 */
#ifndef HC_JSON_H
#define HC_JSON_H
#include <stdbool.h>
#include <pbnjson.h>

raw_buffer key_buf(const char *k);
jvalue_ref parse_object(const char *text);
/* Copy of a string member; NULL when absent or not a string. Free with g_free. */
char *get_string(jvalue_ref obj, const char *key);
void put_str(jvalue_ref o, const char *k, const char *v);
void put_bool(jvalue_ref o, const char *k, bool v);
void put_int(jvalue_ref o, const char *k, int v);
bool reply_ok(jvalue_ref r);
#endif
