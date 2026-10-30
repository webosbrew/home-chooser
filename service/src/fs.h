/* SPDX-License-Identifier: Apache-2.0 */
#ifndef HC_FS_H
#define HC_FS_H
#include <stdbool.h>
#include <stddef.h>
#include <sys/types.h>

bool mkdir_p(const char *dir);
/* Reads up to max bytes into a g_malloc'ed, NUL-terminated buffer; -1 on error (errno set). */
ssize_t slurp(const char *path, size_t max, char **out, bool *too_big);
bool write_atomic(const char *path, const char *text, size_t len);
#endif
