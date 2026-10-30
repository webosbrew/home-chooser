/* SPDX-License-Identifier: Apache-2.0 */
#ifndef HC_NOTIFY_H
#define HC_NOTIFY_H
#include <stdbool.h>
#include <pbnjson.h>

/* A bounded notification call; logs reply text and requires a true returnValue. */
bool notify_call(const char *tag, const char *uri, jvalue_ref payload, int timeout_s);
#endif
