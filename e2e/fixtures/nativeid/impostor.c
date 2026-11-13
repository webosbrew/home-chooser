/* SPDX-License-Identifier: Apache-2.0 */
/*
 * Home Chooser E2E probe: a luna service that registers the service name of another app, which the installer
 * lets any app do for names that start with its own id (a package with the app id org.webosbrew.hcfixture.native
 * may declare the service org.webosbrew.hcfixture.native.probe, and the hub then lets that executable register the
 * name). Its method `try` calls org.webosbrew.homechooser.service/getInfo from that name and returns the reply,
 * which must be a refusal: the name belongs to the app of that id, not to this executable.
 */
#include <glib.h>
#include <luna-service2/lunaservice.h>
#include <stdio.h>
#include <string.h>

#ifndef HC_IMPOSTOR_NAME
#error "define HC_IMPOSTOR_NAME"
#endif

static LSHandle *handle;

static gboolean stop(gpointer loop)
{
	g_main_loop_quit(loop);
	return G_SOURCE_REMOVE;
}

static bool relay(LSHandle *sh, LSMessage *reply, void *ctx)
{
	(void)sh;
	LSMessage *asked = ctx;
	LSError error;
	LSErrorInit(&error);
	LSMessageReply(handle, asked, LSMessageGetPayload(reply), &error);
	LSErrorFree(&error);
	LSMessageUnref(asked);
	return true;
}

static bool try_getinfo(LSHandle *sh, LSMessage *message, void *ctx)
{
	(void)ctx;
	LSError error;
	LSErrorInit(&error);
	LSMessageRef(message);
	if (!LSCall(sh, "luna://org.webosbrew.homechooser.service/getInfo", "{}", relay, message, NULL, &error)) {
		char text[512];
		snprintf(text, sizeof text, "{\"returnValue\":false,\"errorText\":\"%s\"}", error.message);
		LSErrorFree(&error);
		LSErrorInit(&error);
		LSMessageReply(sh, message, text, &error);
		LSMessageUnref(message);
	}
	LSErrorFree(&error);
	return true;
}

static LSMethod methods[] = {{"try", try_getinfo, 0}, {0}};

int main(void)
{
	LSError error;
	LSErrorInit(&error);
	GMainLoop *loop = g_main_loop_new(NULL, FALSE);
	if (!LSRegister(HC_IMPOSTOR_NAME, &handle, &error)) {
		fprintf(stderr, "register: %s\n", error.message);
		return 1;
	}
	LSRegisterCategory(handle, "/", methods, NULL, NULL, &error);
	LSGmainAttach(handle, loop, &error);
	g_timeout_add_seconds(120, stop, loop);
	g_main_loop_run(loop);
	return 0;
}
