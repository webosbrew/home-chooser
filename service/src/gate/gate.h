/* SPDX-License-Identifier: Apache-2.0 */
/* homechooser-home-gate: SAM runs it in place of com.webos.app.home on webOS 6-9 while a
 * launcher is chosen (docs/home-gate.md). No LS2 registration: bounded luna-send children only. */
#ifndef HC_GATE_H
#define HC_GATE_H
#include <glib.h>
#include <pbnjson.h>

typedef struct {
    const char *luna_send, *state_dir;
    const char *target; /* the bind this process runs from (HC_GATE_TARGET) */
    const char *dir;    /* the daemon's gate dir: the release marker goes there (HC_GATE_DIR) */
    const char *log;    /* the watcher's log file; PmLog as well */
    int query_timeout_s, launch_timeout_s;
    /* NULL: umount2(target, MNT_DETACH). 0 on success, else -1 with errno. */
    int (*unmount)(const char *target, void *context);
    void *context;
} HCGateConfig;

typedef enum { HC_GATE_RELEASE, HC_GATE_LAUNCH, HC_GATE_NOTHING } HCGateAction;
typedef struct {
    HCGateAction action;
    char *target; /* HC_GATE_LAUNCH: the app to open */
    char *why;
    char *choice; /* the valid choice read, if any: recorded when the gate releases itself */
} HCGateDecision;

HCGateConfig hc_gate_config(void);
/* One bounded luna-send -n 1 as identity; the parsed reply object or NULL. */
jvalue_ref hc_gate_call(const HCGateConfig *c, const char *identity, const char *uri, const char *payload, int timeout_s);
/* The decision table. envelope is SAM's argv[1]. Free with hc_gate_decision_clear. */
HCGateDecision hc_gate_decide(const HCGateConfig *c, const char *envelope);
void hc_gate_decision_clear(HCGateDecision *d);
/* Decide, act, log. Returns the process exit status: 0, or 1 when the gate could not remove itself. */
int hc_gate_main(const HCGateConfig *c, int argc, char **argv);
#endif
