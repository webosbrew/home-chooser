/* SPDX-License-Identifier: Apache-2.0 */
#include "homekey.h"
#include <string.h>

/* SHA256 of the complete launchHomeApp function, from corpus.tsv. No vendor JS is shipped.
 * Matching complete function bytes refuses new access/launch/NMRM shapes before any write. */
static const char *const shapes[] = {
#include "homekey-shapes.inc"
    NULL
};

char *hc_key_generate(const char *source, const char *id, char **reason)
{
    const char *start = strstr(source, "\nfunction launchHomeApp(key) {");
    const char *end = start ? strstr(start, "\n}\n") : NULL;
    if (!id || !*id || strspn(id, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-") != strlen(id)) {
        *reason = g_strdup("invalid launcher id"); return NULL;
    }
    if (!start || !end || strstr(start + 1, "\nfunction launchHomeApp(key) {")) {
        *reason = g_strdup("unknown launchHomeApp structure"); return NULL;
    }
    end += 3;
    char *hash = g_compute_checksum_for_data(G_CHECKSUM_SHA256, (const guchar *)start, end - start);
    gboolean known = FALSE;
    for (int i = 0; shapes[i]; ++i) if (!strcmp(hash, shapes[i])) known = TRUE;
    g_free(hash);
    if (!known) { *reason = g_strdup("unknown launchHomeApp shape"); return NULL; }
    GString *result = g_string_new_len(source, start - source);
    const char *cursor = start;
    const char *literal = "\"com.webos.app.home\"";
    while (cursor < end) {
        const char *hit = strstr(cursor, literal);
        if (!hit || hit >= end) break;
        g_string_append_len(result, cursor, hit - cursor);
        g_string_append_printf(result, "\"%s\"", id);
        cursor = hit + strlen(literal);
    }
    g_string_append_len(result, cursor, end - cursor);
    g_string_append(result, end);
    /* Old 7.1/7.4 have no constant. Other firmware uses it in Home comparisons. */
    const char *definition = "var HOME_APP_ID = \"com.webos.app.home\";";
    char *def = strstr(result->str, definition);
    if (def) {
        if (strstr(result->str, "var HOME_APP_ID") != def || strstr(def + 1, "var HOME_APP_ID")) {
            g_string_free(result, TRUE); *reason = g_strdup("ambiguous HOME_APP_ID"); return NULL;
        }
        gsize offset = def - result->str;
        g_string_erase(result, offset, strlen(definition));
        char *replacement = g_strdup_printf("var HOME_APP_ID = \"%s\";", id);
        g_string_insert(result, offset, replacement);
        g_free(replacement);
    } else if (strstr(source, "var HOME_APP_ID")) {
        g_string_free(result, TRUE); *reason = g_strdup("unknown HOME_APP_ID definition"); return NULL;
    }
    return g_string_free(result, FALSE);
}
