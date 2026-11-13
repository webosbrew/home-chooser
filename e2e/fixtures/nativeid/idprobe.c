/* SPDX-License-Identifier: Apache-2.0 */
/*
 * Home Chooser E2E probe: a headless native app that makes one launcher-API call the way a native launcher
 * does, from its own luna handle (libhelpers, the library SDL uses), not through `luna-send -a`.
 *
 * It calls org.webosbrew.homechooser.service/getInfo once and writes the reply, verbatim, to
 * /tmp/<app id>.getinfo (HC_PROBE_ID is the id); the runner reads that file. Nothing else: no window, no
 * registration with sam, no other call. A native app has no application id on the bus, so what the service
 * answers is what any native launcher gets. Built twice by build.sh: an eligible launcher and one without
 * the "home" category, which the service must refuse.
 */
#define _GNU_SOURCE
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <webos-helpers/libhelpers.h>

#ifndef HC_PROBE_ID
#error "define HC_PROBE_ID"
#endif

static volatile int done; /* set on libhelpers' thread */
static char reply[4096];

static bool on_reply(LSHandle *sh, LSMessage *message, void *ctx)
{
	(void)sh;
	(void)ctx;
	const char *text = HLunaServiceMessage(message);
	snprintf(reply, sizeof reply, "%s", text ? text : "(null)");
	done = 1;
	return true;
}

int main(void)
{
	static HContext call;
	call.callback = on_reply;
	call.userdata = NULL;
	call.multiple = 0;
	call.pub = 1;
	HLunaServiceCall("luna://org.webosbrew.homechooser.service/getInfo", "{}", &call);
	for (int i = 0; i < 300 && !done; i++)
		usleep(100000);
	if (!done)
		snprintf(reply, sizeof reply, "{\"returnValue\":false,\"errorText\":\"no reply in 30 s\"}");
	FILE *out = fopen("/tmp/" HC_PROBE_ID ".getinfo.part", "w");
	if (out) {
		fprintf(out, "%s\n", reply);
		fclose(out);
		rename("/tmp/" HC_PROBE_ID ".getinfo.part", "/tmp/" HC_PROBE_ID ".getinfo");
	}
	return 0;
}
