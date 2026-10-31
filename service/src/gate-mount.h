/* SPDX-License-Identifier: Apache-2.0 */
/* Home gate bind mount, webOS 6-9 (docs/home-gate.md). The daemon binds a tmpfs copy of the gate
 * over the stock Home binary, so every SAM launch of com.webos.app.home runs the gate. No LS2
 * registration, no SAM restart. Stock Home only ever runs from its own path (its LS2 role is
 * matched by exe path), so the gate's fallback is to remove itself and relaunch Home. */
#ifndef HC_GATE_MOUNT_H
#define HC_GATE_MOUNT_H
#include <glib.h>

typedef struct {
    const char *gate_binary; /* the installed gate executable (HC_GATE_BINARY) */
    const char *dir;         /* private runtime dir on tmpfs (HC_GATE_DIR); holds the copy and the release marker */
    const char *target;      /* the stock Home binary SAM executes (HC_GATE_TARGET) */
    const char *mountinfo;   /* /proc/self/mountinfo */
    /* NULL: the real MS_BIND mount and a lazy (MNT_DETACH) umount2. 0 on success. */
    int (*bind)(const char *source, const char *target, void *context);
    int (*unbind)(const char *target, void *context);
    /* luna-send as HC_LUNA_IDENTITY; only after a new mount (running, closeByAppId). */
    char *(*call)(const char *uri, const char *payload, int timeout, void *context);
    void *context;
    const char *proc;        /* /proc, for the processid -> exe check in hc_gate_close_stock */
    gboolean skip_close;     /* the caller (the watcher) closes stock Home itself, with retries */
} HCGateOps;

typedef enum {
    HC_GATE_FAILED = -1,
    HC_GATE_UNCHANGED = 0,
    HC_GATE_MOUNTED,
    HC_GATE_UNMOUNTED,
    HC_GATE_RELEASED /* the gate removed itself for this choice; not mounted again until it changes */
} HCGateResult;

HCGateOps hc_gate_ops(char *(*call)(const char *, const char *, int, void *), void *context);
/* Idempotent, reads mountinfo first. choice non-NULL: mount the gate for that launcher (unless the
 * gate released itself for the same choice) and close a running stock Home. NULL, or no installed
 * gate binary: unmount it everywhere, propagated copies included. Messages go to log. */
HCGateResult hc_gate_sync(const HCGateOps *ops, const char *choice, GString *log);
/* Close a running stock Home that is the real binary, never the gate (told apart by /proc/<pid>/exe
 * against the staged copy). TRUE once the answer is confirmed: `running` without Home, or
 * closeByAppId returned true. FALSE while unconfirmed (no reply, a failure, the gate itself or an
 * instance whose pid cannot be checked, which is never closed); the caller retries. Outcome to log. */
gboolean hc_gate_close_stock(const HCGateOps *ops, GString *log);
/* Forget a release, so the next sync mounts again (an explicit apply from the app). */
void hc_gate_rearm(const HCGateOps *ops);
/* The release marker inside dir; written by the gate before it unmounts itself. */
char *hc_gate_marker(const char *dir);
/* The one-off --homekey path: flag present, a valid non-stock choice that getAppInfo knows.
 * Returns that choice (g_free) or NULL. */
char *hc_gate_wanted(const char *state_dir, char *(*call)(const char *, const char *, int, void *), void *context);
/* Release major from os-release text (VERSION_ID, else VERSION); 0 when unknown. */
int hc_gate_major(const char *osrelease);
#endif
