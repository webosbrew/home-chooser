/* SPDX-License-Identifier: Apache-2.0 */
#include "last-input.h"
#include "json.h"
#include "policy.h"
#include <glib.h>

char *hc_last_input(jvalue_ref reply, const char **why)
{
    const char *reason = NULL;
    char *id = NULL;
    if (!reply) reason = "eim did not answer";
    else if (!reply_ok(reply)) reason = "eim refused";
    else {
        id = get_string(reply, "lastSourceAppId");
        if (!id || !*id) reason = "no lastSourceAppId";
        else if (!hc_app_id_ok(id)) reason = "invalid lastSourceAppId";
        else if (!g_strcmp0(id, HC_STOCK_HOME_ID) || !g_strcmp0(id, "com.webos.app.fullhome")) reason = "lastSourceAppId is stock Home";
    }
    if (reason) { g_free(id); id = g_strdup(HC_LIVETV_ID); }
    if (why) *why = reason;
    return id;
}
