/* SPDX-License-Identifier: Apache-2.0 */
#include <glib.h>
#include <string.h>
#include <unistd.h>
#include "roles.h"
#include "version.h"
#include "json.h"
#include "log.h"
#include "policy.h"
#include "service/catalog.h"
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

/* Every method that does real work also gives the watcher a chance to be revived, after its own reply. */
#define REVIVING(name) \
	static bool name##_revive(LSHandle *sh, LSMessage *msg, void *ctx) \
	{ \
		bool r = name(sh, msg, ctx); \
		revive_touch(); \
		return r; \
	}
REVIVING(on_call)
REVIVING(on_read_file)
REVIVING(on_write_file)
REVIVING(on_remove_file)
REVIVING(on_read_icon)
REVIVING(on_link)
static bool on_unlink_revive(LSHandle *sh, LSMessage *msg, void *ctx)
{
	bool r = on_unlink(sh, msg, ctx);
	revive_hold();
	return r;
}
REVIVING(on_link_status)

/* run {command: stopWatcher} and unlink take the watcher down on purpose: hold the revival instead of touching it. */
static bool on_run_revive(LSHandle *sh, LSMessage *msg, void *ctx)
{
	const char *payload = LSMessageGetPayload(msg);
	bool stopping = payload && strstr(payload, "stopWatcher");
	bool r = on_run(sh, msg, ctx);
	if (stopping) revive_hold();
	else revive_touch();
	return r;
}
REVIVING(on_launcher_get_info)
REVIVING(on_launcher_list_apps)
REVIVING(on_launcher_running)
REVIVING(on_launcher_close_by_id)
REVIVING(on_launcher_uninstall)
REVIVING(on_launcher_get_foreground)
REVIVING(on_launcher_get_app_info)
REVIVING(on_launcher_get_app_icon)
REVIVING(on_launcher_list_launch_points)
REVIVING(on_launcher_life_events)
REVIVING(on_launcher_list_inputs)
REVIVING(on_launcher_get_icons)
REVIVING(on_uninstall_confirmed)
REVIVING(on_reboot_prompt)


static LSMethod methods[] = {
	{"status", on_status, LUNA_METHOD_FLAGS_NONE},
	{"quit", on_quit, LUNA_METHOD_FLAGS_NONE},
	{"call", on_call_revive, LUNA_METHOD_FLAGS_NONE},
	{"readFile", on_read_file_revive, LUNA_METHOD_FLAGS_NONE},
	{"writeFile", on_write_file_revive, LUNA_METHOD_FLAGS_NONE},
	{"removeFile", on_remove_file_revive, LUNA_METHOD_FLAGS_NONE},
	{"readIcon", on_read_icon_revive, LUNA_METHOD_FLAGS_NONE},
	{"link", on_link_revive, LUNA_METHOD_FLAGS_NONE},
	{"unlink", on_unlink_revive, LUNA_METHOD_FLAGS_NONE},
	{"linkStatus", on_link_status_revive, LUNA_METHOD_FLAGS_NONE},
	{"run", on_run_revive, LUNA_METHOD_FLAGS_NONE},
	/* the launcher API: the caller is checked in each handler, not by hc_sender_ok */
	{"getInfo", on_launcher_get_info_revive, LUNA_METHOD_FLAGS_NONE},
	{"listApps", on_launcher_list_apps_revive, LUNA_METHOD_FLAGS_NONE},
	{"listRunning", on_launcher_running_revive, LUNA_METHOD_FLAGS_NONE},
	{"closeApp", on_launcher_close_by_id_revive, LUNA_METHOD_FLAGS_NONE},
	{"uninstallApp", on_launcher_uninstall_revive, LUNA_METHOD_FLAGS_NONE},
	{"getForegroundApp", on_launcher_get_foreground_revive, LUNA_METHOD_FLAGS_NONE},
	{"getAppInfo", on_launcher_get_app_info_revive, LUNA_METHOD_FLAGS_NONE},
	{"getAppIcon", on_launcher_get_app_icon_revive, LUNA_METHOD_FLAGS_NONE},
	{"listLaunchPoints", on_launcher_list_launch_points_revive, LUNA_METHOD_FLAGS_NONE},
	{"getAppLifeEvents", on_launcher_life_events_revive, LUNA_METHOD_FLAGS_NONE},
	{"listInputs", on_launcher_list_inputs_revive, LUNA_METHOD_FLAGS_NONE},
	{"getIcons", on_launcher_get_icons_revive, LUNA_METHOD_FLAGS_NONE},
	{"uninstallConfirmed", on_uninstall_confirmed_revive, LUNA_METHOD_FLAGS_NONE},
	{"rebootPrompt", on_reboot_prompt_revive, LUNA_METHOD_FLAGS_NONE},
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
	revive_init();
	subs_init(g_handle);
	g_main_loop_run(g_loop);
	subs_shutdown();
	LSUnregister(g_handle, &e);
	LSErrorFree(&e);
	g_main_loop_unref(g_loop);
	LOGI("EXIT", "bye");
	return 0;
}
