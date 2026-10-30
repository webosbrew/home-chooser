/* SPDX-License-Identifier: Apache-2.0 */
#include <glib.h>
#include <unistd.h>
#include "roles.h"
#include "version.h"
#include "json.h"
#include "log.h"
#include "policy.h"
#include "service/methods.h"
#include "service/reply.h"

static GMainLoop *g_loop;
static LSHandle *g_handle;

/* ---- status, quit ---- */

static bool on_status(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	if (!gate(msg, "status", false)) return true;
	jvalue_ref o = jobject_create();
	put_bool(o, "returnValue", true);
	put_bool(o, "root", geteuid() == 0);
	put_int(o, "uid", (int)geteuid());
	put_str(o, "version", HC_VERSION);
	put_int(o, "apiVersion", HC_API_VERSION);
	send_reply(msg, o);
	return true;
}

static gboolean do_quit(gpointer data)
{
	(void)data;
	g_main_loop_quit(g_loop);
	return FALSE;
}

static bool on_quit(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	if (!gate(msg, "quit", false)) return true;
	send_ok(msg);
	g_timeout_add(300, do_quit, NULL); /* let the reply out first */
	return true;
}

/* ---- registration ---- */

static LSMethod methods[] = {
	{"status", on_status, LUNA_METHOD_FLAGS_NONE},
	{"quit", on_quit, LUNA_METHOD_FLAGS_NONE},
	{"call", on_call, LUNA_METHOD_FLAGS_NONE},
	{"readFile", on_read_file, LUNA_METHOD_FLAGS_NONE},
	{"writeFile", on_write_file, LUNA_METHOD_FLAGS_NONE},
	{"removeFile", on_remove_file, LUNA_METHOD_FLAGS_NONE},
	{"readIcon", on_read_icon, LUNA_METHOD_FLAGS_NONE},
	{"link", on_link, LUNA_METHOD_FLAGS_NONE},
	{"unlink", on_unlink, LUNA_METHOD_FLAGS_NONE},
	{"linkStatus", on_link_status, LUNA_METHOD_FLAGS_NONE},
	{"run", on_run, LUNA_METHOD_FLAGS_NONE},
	/* the launcher API: the caller is checked in each handler, not by hc_sender_ok */
	{"getInfo", on_launcher_get_info, LUNA_METHOD_FLAGS_NONE},
	{"listApps", on_launcher_list_apps, LUNA_METHOD_FLAGS_NONE},
	{"listRunning", on_launcher_running, LUNA_METHOD_FLAGS_NONE},
	{"closeApp", on_launcher_close_by_id, LUNA_METHOD_FLAGS_NONE},
	{"uninstallApp", on_launcher_uninstall, LUNA_METHOD_FLAGS_NONE},
	{"getForegroundApp", on_launcher_get_foreground, LUNA_METHOD_FLAGS_NONE},
	{"getAppInfo", on_launcher_get_app_info, LUNA_METHOD_FLAGS_NONE},
	{"getAppIcon", on_launcher_get_app_icon, LUNA_METHOD_FLAGS_NONE},
	{"uninstallConfirmed", on_uninstall_confirmed, LUNA_METHOD_FLAGS_NONE},
	{"rebootPrompt", on_reboot_prompt, LUNA_METHOD_FLAGS_NONE},
	{NULL, NULL, LUNA_METHOD_FLAGS_NONE}
};

int hc_service_main(void)
{
	g_loop = g_main_loop_new(NULL, FALSE);
	LSError e;
	LSErrorInit(&e);
	if (!LSRegister(HC_SERVICE_ID, &g_handle, &e)
		|| !LSRegisterCategory(g_handle, "/", methods, NULL, NULL, &e)
		|| !LSGmainAttach(g_handle, g_loop, &e)) {
		LOGE("REGISTER_FAILED", "%s", e.message ? e.message : "?");
		LSErrorFree(&e);
		return 1;
	}
	g_main_loop_run(g_loop);
	LSUnregister(g_handle, &e);
	LSErrorFree(&e);
	g_main_loop_unref(g_loop);
	LOGI("EXIT", "bye");
	return 0;
}
