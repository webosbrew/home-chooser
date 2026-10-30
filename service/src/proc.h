/* SPDX-License-Identifier: Apache-2.0 */
#ifndef HC_PROC_H
#define HC_PROC_H
#include <glib.h>

/* Bounded argv execution; -1 spawn failure, -2 timeout. See proc.c for stream completion. */
int run_argv(const char *const *argv, int timeout_s, GString *out, GString *err, int (*done)(const char *));
#endif
