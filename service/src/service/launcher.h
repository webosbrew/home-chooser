/* SPDX-License-Identifier: Apache-2.0 */
/* The launcher API's shared request state (launcher.c), used by the list, icon and subscription
 * modules (catalog.c, subscribe.c). */
#ifndef HC_SERVICE_LAUNCHER_H
#define HC_SERVICE_LAUNCHER_H
#include <luna-service2/lunaservice.h>
#include <pbnjson.h>
#include "luna.h"

#define SAM_URI "luna://com.webos.applicationManager"
#define EIM_URI "luna://com.webos.service.eim"

/* One launcher request in flight. */
typedef struct LOp {
	LSMessage *msg;
	LSHandle *sh;
	const char *method;
	char caller[130];
	char *target;      /* the id parameter */
	bool consent;
	bool force;
	bool icon_large;
	bool subscribe;    /* {"subscribe": true} on a list method */
	jvalue_ref items;  /* getIcons: the validated items */
	void (*next)(struct LOp *op);
	bool need_consent;
	bool native;       /* identified by its service name, not an application id; not yet checked against the appinfo */
	bool need_root, need_home; /* the checks a native caller gets after its appinfo */
	char *target_title;
	char *target_folder;
	char *lookup_text; /* Original SAM refusal while resolving an ambiguous target id. */
	int target_removable;
} LOp;

void op_free(LOp *op);
/* Gate (forbidden, notRoot, notHome) and a new request; NULL after a refusal reply. */
LOp *launcher_open(LSHandle *sh, LSMessage *msg, const char *method, bool need_root, bool need_home,
	bool need_consent, void (*next)(LOp *));
/* Reads the caller's appinfo for its consent (noPermission), then runs op->next. Takes over op. */
void launcher_consent(LOp *op);
/* Replies with the code for a failed or refused platform call (no reply: timeout/badReply;
 * a false reply: refused or lookupFailed as hc_sam_refusal_code says) and returns true; false when the
 * reply is usable. who names the platform service in errorText ("application manager"). */
bool platform_failure(LOp *op, Call *c, jvalue_ref reply, const char *who);
/* True for {"subscribe": true}; anything else is a single reply, as before subscriptions existed. */
bool wants_subscribe(LSMessage *msg);
#endif
