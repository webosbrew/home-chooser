/* SPDX-License-Identifier: Apache-2.0 */
/* The last TV input, opened when an overlay launcher has nothing beneath it: by the home gate on
 * webOS 6-9 (docs/home-gate.md) and by the watcher once after boot on 10 and later
 * (docs/settings-app.md). Each caller makes the eim call through its own transport, as
 * HC_LUNA_IDENTITY, and passes the reply here. */
#ifndef HC_LAST_INPUT_H
#define HC_LAST_INPUT_H
#include <pbnjson.h>

#define HC_LAST_INPUT_URI "luna://com.webos.service.eim/getLastInput"
#define HC_LAST_INPUT_PAYLOAD "{}"
#define HC_LIVETV_ID "com.webos.app.livetv"

/* lastSourceAppId of a getLastInput reply (NULL: no answer). Live TV when there was no answer, the
 * reply is not ok, or the id is empty, not a valid app id, or a stock Home (com.webos.app.home,
 * com.webos.app.fullhome). why, when given, is set to a static reason for the fallback, else NULL.
 * Free with g_free. */
char *hc_last_input(jvalue_ref reply, const char **why);
#endif
