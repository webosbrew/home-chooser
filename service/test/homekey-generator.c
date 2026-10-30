/* SPDX-License-Identifier: Apache-2.0 */
#include "homekey.h"
#include <stdio.h>
int main(int argc, char **argv) {
    char *source = NULL, *reason = NULL;
    if (argc != 3 || !g_file_get_contents(argv[1], &source, NULL, NULL)) return 1;
    char *output = hc_key_generate(source, argv[2], &reason);
    if (output) fputs(output, stdout); else fprintf(stderr, "%s\n", reason);
    g_free(source); g_free(output); g_free(reason);
    return output ? 0 : 2;
}
