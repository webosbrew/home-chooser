/* SPDX-License-Identifier: Apache-2.0 */
/* Host-side test of src/policy.c: ctest --test-dir build/service-host -R test-policy */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/stat.h>

#include "policy.h"

static int failures, checks;
#define CHECK(cond) do { checks++; if (!(cond)) { failures++; printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } } while (0)

static void test_sender(void)
{
	CHECK(hc_sender_ok("org.webosbrew.homechooser"));
	CHECK(hc_sender_ok("org.webosbrew.homechooser 1234"));
	CHECK(!hc_sender_ok(NULL));
	CHECK(!hc_sender_ok(""));
	CHECK(!hc_sender_ok("org.webosbrew.homechooser.service"));
	CHECK(!hc_sender_ok("org.webosbrew.homechooser2"));
	CHECK(!hc_sender_ok("org.webosbrew.homechooser-evil"));
	CHECK(!hc_sender_ok("com.webos.app.home"));
	CHECK(!hc_sender_ok("org.webosbrew.hbchannel"));
}

static void test_files(void)
{
	CHECK(hc_file_allowed("/var/lib/webosbrew/homechooser/home"));
	CHECK(hc_file_allowed("/var/lib/webosbrew/homechooser/homekey"));
	CHECK(hc_file_allowed("/var/webos-profile/override/org.webosbrew.homechooser.json"));
	CHECK(!hc_file_allowed(NULL));
	CHECK(!hc_file_allowed("/etc/passwd"));
	CHECK(!hc_file_allowed("/var/lib/webosbrew/homechooser/home/"));
	CHECK(!hc_file_allowed("/var/lib/webosbrew/homechooser/home.tmp"));
	CHECK(!hc_file_allowed("/var/lib/webosbrew/homechooser/../init.d/x"));
	CHECK(!hc_file_allowed("/var/webos-profile/override/other.json"));
	CHECK(!hc_file_allowed("/var/lib/webosbrew/init.d/homechooser"));
	CHECK(!hc_file_allowed("/etc/os-release"));
	CHECK(hc_file_readable("/etc/os-release"));
	CHECK(hc_file_readable("/var/lib/webosbrew/homechooser/home"));
	CHECK(!hc_file_readable("/etc/shadow"));
	CHECK(!hc_file_readable("/etc/os-release2"));
	CHECK(!hc_file_readable(NULL));
	CHECK(hc_osrelease_target_ok("/usr/lib/os-release"));
	CHECK(hc_osrelease_target_ok("/etc/os-release"));
	CHECK(!hc_osrelease_target_ok("/etc/shadow"));
	CHECK(!hc_osrelease_target_ok("/usr/lib/os-release2"));
	CHECK(!hc_osrelease_target_ok("/tmp/os-release"));
	CHECK(!hc_osrelease_target_ok("../usr/lib/os-release"));
	CHECK(!hc_osrelease_target_ok(NULL));
}

static void test_calls(void)
{
	CHECK(hc_call_lookup("luna://com.webos.applicationManager/listApps") == HC_CALL_PLAIN);
	CHECK(hc_call_lookup("luna://com.webos.applicationManager/getAppInfo") == HC_CALL_PLAIN);
	CHECK(hc_call_lookup("luna://com.webos.applicationManager/setDefaultApp") == HC_CALL_PLAIN);
	CHECK(hc_call_lookup("luna://com.webos.applicationManager/launchDefaultApp") == HC_CALL_PLAIN);
	CHECK(hc_call_lookup("luna://com.webos.applicationManager/closeByAppId") == HC_CALL_PLAIN);
	/* systemprofile goes through the profileGet / profileRescan commands, not call */
	CHECK(hc_call_lookup("luna://com.webos.service.systemprofile/getProfiles") == HC_CALL_DENIED);
	CHECK(hc_call_lookup("luna://com.webos.service.systemprofile/debug/rescan") == HC_CALL_DENIED);
	CHECK(hc_call_lookup("luna://com.webos.settingsservice/getSystemSettings") == HC_CALL_SETTINGS_GET);
	CHECK(hc_call_lookup("luna://com.webos.settingsservice/setSystemSettings") == HC_CALL_SETTINGS_SET);
	CHECK(hc_call_lookup(NULL) == HC_CALL_DENIED);
	CHECK(hc_call_lookup("luna://com.webos.applicationManager/listApps/") == HC_CALL_DENIED);
	CHECK(hc_call_lookup("luna://com.webos.applicationManager/launch") == HC_CALL_DENIED);
	CHECK(hc_call_lookup("luna://org.webosbrew.hbchannel.service/exec") == HC_CALL_DENIED);
	CHECK(hc_call_lookup("luna://com.webos.settingsservice/resetSystemSettings") == HC_CALL_DENIED);
	CHECK(hc_call_lookup("luna://com.webos.service.systemprofile/debug/rescan?x") == HC_CALL_DENIED);

	const char *good[] = {"defaultApps"};
	const char *two[] = {"defaultApps", "x"};
	const char *other[] = {"localeInfo"};
	CHECK(hc_settings_ok("general", good, 1));
	CHECK(!hc_settings_ok("general", two, 2));
	CHECK(!hc_settings_ok("general", other, 1));
	CHECK(!hc_settings_ok("general", good, 0));
	CHECK(!hc_settings_ok("network", good, 1));
	CHECK(!hc_settings_ok(NULL, good, 1));
}

static void test_commands(void)
{
	CHECK(!hc_command_lookup("restartSam"));
	const hc_command *c = hc_command_lookup("watch");
	CHECK(c && strcmp(c->argv[0], "/bin/sh") == 0 && strcmp(c->argv[1], HC_SERVICE_DIR "/scripts/homechooser") == 0 && strcmp(c->argv[2], "watch") == 0 && !c->argv[3]);
	const char *kf[] = {"homekeyStatus", "status", "homekeyApply", "apply", "homekeyStop", "stop", "homekeyRefresh", "refresh", "homekeyPrepare", "prepare", "homekeyClear", "clear"};
	for (int i = 0; i < 8; i += 2) {
		c = hc_command_lookup(kf[i]);
		CHECK(c && strcmp(c->argv[1], HC_SERVICE_DIR "/scripts/homechooser-homekey") == 0 && strcmp(c->argv[2], kf[i + 1]) == 0 && !c->argv[3]);
	}
	CHECK(!hc_command_lookup("homekeyApplySam"));
	CHECK(hc_command_lookup("homekeyStatus") && hc_command_lookup("homekeyRefresh"));
	c = hc_command_lookup("stopWatcher");
	CHECK(c && c->tolerant && strcmp(c->argv[2], "homechooser-home") == 0);
	c = hc_command_lookup("watcherActive");
	CHECK(c && c->tolerant && strcmp(c->argv[1], "is-active") == 0);
	c = hc_command_lookup("profileGet");
	CHECK(c && !c->tolerant && strcmp(c->argv[0], "/usr/bin/luna-send") == 0 && strcmp(c->argv[1], "-a") == 0 && strcmp(c->argv[2], HC_LUNA_IDENTITY) == 0 && strcmp(c->argv[3], "-n") == 0 && strcmp(c->argv[4], "1") == 0
		&& strcmp(c->argv[5], "luna://com.webos.service.systemprofile/getProfiles") == 0
		&& strcmp(c->argv[6], "{\"names\":[\"defaultApps\",\"idleApp\"]}") == 0 && !c->argv[7]);
	c = hc_command_lookup("profileRescan");
	CHECK(c && !c->tolerant && strcmp(c->argv[0], "/usr/bin/luna-send") == 0 && strcmp(c->argv[1], "-a") == 0 && strcmp(c->argv[2], HC_LUNA_IDENTITY) == 0 && strcmp(c->argv[3], "-n") == 0 && strcmp(c->argv[4], "1") == 0
		&& strcmp(c->argv[5], "luna://com.webos.service.systemprofile/debug/rescan") == 0
		&& strcmp(c->argv[6], "{}") == 0 && !c->argv[7]);
	CHECK(!hc_command_lookup("luna-send"));
	c = hc_command_lookup("watchLog");
	CHECK(c && c->native_tail);
	CHECK(!hc_command_lookup(NULL));
	CHECK(!hc_command_lookup("sh"));
	CHECK(!hc_command_lookup("systemctl restart sam"));
	CHECK(!hc_command_lookup("restartSam "));
	/* every argv[0] is absolute and nothing is left unterminated */
	static const char *const names[] = {"watch", "homekeyStatus", "homekeyApply", "homekeyStop", "homekeyRefresh", "homekeyPrepare", "homekeyClear", "stopWatcher", "watcherActive", "profileGet", "profileRescan", "accessApply", "accessRemove", "accessStatus"};
	for (size_t i = 0; i < sizeof names / sizeof *names; i++) {
		c = hc_command_lookup(names[i]);
		CHECK(c && c->argv[0][0] == '/' && c->timeout_s > 0);
	}
}

static void test_icons(void)
{
	char out[128];
	CHECK(hc_icon_path("/usr/palm/applications/a", "icon.png", out, sizeof out) && strcmp(out, "/usr/palm/applications/a/icon.png") == 0);
	CHECK(hc_icon_path("/usr/palm/applications/a/", "img/i.png", out, sizeof out) && strcmp(out, "/usr/palm/applications/a/img/i.png") == 0);
	CHECK(hc_icon_path("/usr/palm/applications/a", "/usr/palm/applications/a/icon.png", out, sizeof out) && strcmp(out, "/usr/palm/applications/a/icon.png") == 0);
	CHECK(!hc_icon_path("usr/palm", "icon.png", out, sizeof out));
	CHECK(!hc_icon_path("/usr/palm/../../etc", "icon.png", out, sizeof out));
	CHECK(!hc_icon_path("/usr/palm/a", "../b/icon.png", out, sizeof out));
	CHECK(!hc_icon_path("/usr/palm/a", "/etc/../shadow.png", out, sizeof out));
	CHECK(!hc_icon_path("/usr/palm/a", "", out, sizeof out));
	CHECK(!hc_icon_path(NULL, "icon.png", out, sizeof out));
	CHECK(!hc_icon_path("/usr/palm/applications/a", "icon.png", out, 10));
	CHECK(hc_icon_path("/a/..b", "icon.png", out, sizeof out)); /* "..b" is a name, not a parent */

	CHECK(hc_path_within("/a/b/c.png", "/a/b"));
	CHECK(hc_path_within("/a/b/c.png", "/a/b/"));
	CHECK(hc_path_within("/a/b", "/a/b"));
	CHECK(!hc_path_within("/a/bc/c.png", "/a/b"));
	CHECK(!hc_path_within("/etc/passwd", "/a/b"));
	CHECK(hc_path_within("/a", "/"));

	CHECK(strcmp(hc_icon_mime("/x/icon.PNG"), "image/png") == 0);
	CHECK(strcmp(hc_icon_mime("/x/icon.jpeg"), "image/jpeg") == 0);
	CHECK(strcmp(hc_icon_mime("/x/icon.svg"), "image/svg+xml") == 0);
	CHECK(hc_icon_mime("/x/shadow") == NULL);
	CHECK(hc_icon_mime("/x.d/shadow") == NULL);
	CHECK(hc_icon_mime("/x/icon.png.sh") == NULL);
	CHECK(hc_icon_mime(NULL) == NULL);
}

static void test_icon_confinement(void)
{
	char root[] = "test/iconXXXXXX";
	CHECK(mkdtemp(root) != NULL);
	char app[256], outside[256], icon[512], secret[512], escape[512], local[512], link[512], resolved[1024];
	char abs_app[1024], abs_icon[1024], abs_secret[1024];
	snprintf(app, sizeof app, "%s/app", root);
	snprintf(outside, sizeof outside, "%s/outside", root);
	snprintf(icon, sizeof icon, "%s/icon.png", app);
	snprintf(secret, sizeof secret, "%s/secret.png", outside);
	snprintf(escape, sizeof escape, "%s/escape.png", app);
	snprintf(local, sizeof local, "%s/local.png", app);
	snprintf(link, sizeof link, "%s/link", app);
	CHECK(mkdir(app, 0700) == 0 && mkdir(outside, 0700) == 0);
	FILE *f = fopen(icon, "w");
	CHECK(f != NULL);
	if (f) { fputs("safe", f); fclose(f); }
	f = fopen(secret, "w");
	CHECK(f != NULL);
	if (f) { fputs("secret", f); fclose(f); }
	CHECK(symlink("../outside/secret.png", escape) == 0);
	CHECK(symlink("icon.png", local) == 0);
	CHECK(symlink("../outside", link) == 0);
	CHECK(realpath(app, abs_app) != NULL && realpath(icon, abs_icon) != NULL && realpath(secret, abs_secret) != NULL);
	CHECK(hc_icon_resolve(abs_app, "icon.png", resolved, sizeof resolved) && strcmp(resolved, abs_icon) == 0);
	CHECK(hc_icon_resolve(abs_app, "local.png", resolved, sizeof resolved) && strcmp(resolved, abs_icon) == 0);
	CHECK(!hc_icon_resolve(abs_app, "../outside/secret.png", resolved, sizeof resolved));
	CHECK(!hc_icon_resolve(abs_app, "escape.png", resolved, sizeof resolved));
	CHECK(!hc_icon_resolve(abs_app, "link/secret.png", resolved, sizeof resolved));
	CHECK(!hc_icon_resolve(abs_app, abs_secret, resolved, sizeof resolved));
	unlink(link); unlink(local); unlink(escape); unlink(icon); unlink(secret);
	rmdir(app); rmdir(outside); rmdir(root);
}

static void test_base64_and_lines(void)
{
	char out[32];
	CHECK(hc_base64((const unsigned char *)"", 0, out, sizeof out) && strcmp(out, "") == 0);
	CHECK(hc_base64((const unsigned char *)"f", 1, out, sizeof out) && strcmp(out, "Zg==") == 0);
	CHECK(hc_base64((const unsigned char *)"fo", 2, out, sizeof out) && strcmp(out, "Zm8=") == 0);
	CHECK(hc_base64((const unsigned char *)"foo", 3, out, sizeof out) && strcmp(out, "Zm9v") == 0);
	CHECK(hc_base64((const unsigned char *)"foobar", 6, out, sizeof out) && strcmp(out, "Zm9vYmFy") == 0);
	CHECK(!hc_base64((const unsigned char *)"foobar", 6, out, 8));

	const char *t = "a\nb\nc\nd\n";
	CHECK(hc_last_lines(t, strlen(t), 2) == 4);   /* "c\nd\n" */
	CHECK(hc_last_lines(t, strlen(t), 9) == 0);
	CHECK(hc_last_lines("a\nb", 3, 1) == 2);
	CHECK(hc_last_lines("", 0, 5) == 0);
}

static void test_caller(void)
{
	char id[64];
	CHECK(hc_caller_id("com.example.dock", id, sizeof id) && strcmp(id, "com.example.dock") == 0);
	CHECK(hc_caller_id("com.example.dock 1234", id, sizeof id) && strcmp(id, "com.example.dock") == 0);
	/* a service or luna-send caller has no application id */
	CHECK(!hc_caller_id(NULL, id, sizeof id));
	CHECK(!hc_caller_id("", id, sizeof id));
	CHECK(!hc_caller_id(" 1234", id, sizeof id));
	CHECK(!hc_caller_id("bad/id", id, sizeof id));
	CHECK(!hc_caller_id("a;b", id, sizeof id));
	CHECK(!hc_caller_id("com.example.dock", id, 5));
	CHECK(!hc_app_id_ok(NULL) && !hc_app_id_ok("") && hc_app_id_ok("a-b_c.d1"));

	const char *with[] = {"all", HC_GROUP};
	const char *without[] = {"all", "public", "homechooser.applications.x"};
	CHECK(hc_perms_grant(with, 2));
	CHECK(!hc_perms_grant(without, 3));
	CHECK(!hc_perms_grant(NULL, 0));

	CHECK(hc_is_selected_home("com.example.dock", "com.example.dock"));
	CHECK(hc_is_selected_home("com.example.dock", "  com.example.dock \r\n"));
	CHECK(!hc_is_selected_home("com.example.dock", "com.example.dock2"));
	CHECK(!hc_is_selected_home("com.example.doc", "com.example.dock"));
	CHECK(!hc_is_selected_home("com.example.dock", ""));
	CHECK(!hc_is_selected_home("com.example.dock", "com.webos.app.home"));
	CHECK(!hc_is_selected_home("", ""));
	CHECK(!hc_is_selected_home(NULL, "x"));
	CHECK(!hc_is_selected_home("x", NULL));
}

static void test_remove(void)
{
	const char *dev = "/media/developer/apps/usr/palm/applications/org.x.app";
	const char *store = "/media/cryptofs/apps/usr/palm/applications/org.y.app";
	CHECK(hc_remove_check("org.x.app", "org.launcher", 1, dev) == HC_REMOVE_OK);
	CHECK(hc_remove_check("org.y.app", "org.launcher", -1, store) == HC_REMOVE_OK);
	/* the refusal list */
	CHECK(hc_remove_check(HC_APP_ID, "org.launcher", 1, dev) == HC_REMOVE_SELF);
	CHECK(hc_remove_check(HC_HBC_APP_ID, "org.launcher", 1, dev) == HC_REMOVE_HBC);
	CHECK(hc_remove_check("org.launcher", "org.launcher", 1, dev) == HC_REMOVE_CALLER);
	CHECK(hc_remove_check("org.x.app", "org.launcher", 0, dev) == HC_REMOVE_SYSTEM);
	CHECK(hc_remove_check("com.webos.app.home", "org.launcher", -1, "/usr/palm/applications/com.webos.app.home") == HC_REMOVE_SYSTEM);
	CHECK(hc_remove_check("org.x.app", "org.launcher", 1, "/mnt/lg/res/apps/org.x.app") == HC_REMOVE_SYSTEM);
	CHECK(hc_remove_check("org.x.app", "org.launcher", 1, NULL) == HC_REMOVE_SYSTEM);
	CHECK(hc_remove_check("org.x.app", "org.launcher", 1, "/media/developer/apps/usr/palm/applications") == HC_REMOVE_SYSTEM);
	CHECK(hc_remove_check("org.x.app", "org.launcher", 1, "/media/developer/apps/usr/palm/applications/../../../etc") == HC_REMOVE_SYSTEM);
	CHECK(hc_remove_check("org.x.app", "org.launcher", 1, "/media/developer/apps/usr/palm/applicationsX/a") == HC_REMOVE_SYSTEM);
	CHECK(hc_remove_check("a b", "org.launcher", 1, dev) == HC_REMOVE_BADID);
	CHECK(hc_remove_check("a\"}", "org.launcher", 1, dev) == HC_REMOVE_BADID);
	CHECK(hc_remove_check(NULL, "org.launcher", 1, dev) == HC_REMOVE_BADID);
	CHECK(hc_folder_kind_of(dev) == HC_FOLDER_DEV && hc_folder_kind_of(store) == HC_FOLDER_STORE);

	hc_remove_call rc;
	CHECK(hc_remove_step(0, "org.x.app", dev, &rc) && strcmp(rc.uri, "luna://com.webos.applicationManager/removeLaunchPoint") == 0
		&& strcmp(rc.payload, "{\"launchPointId\":\"org.x.app_default\"}") == 0 && !rc.progress);
	CHECK(hc_remove_step(1, "org.x.app", dev, &rc) && strcmp(rc.uri, "luna://com.webos.appInstallService/dev/remove") == 0
		&& strcmp(rc.payload, "{\"id\":\"org.x.app\",\"subscribe\":true}") == 0 && rc.progress);
	CHECK(hc_remove_step(1, "org.y.app", store, &rc) && strcmp(rc.uri, "luna://com.webos.appInstallService/remove") == 0
		&& strcmp(rc.payload, "{\"id\":\"org.y.app\",\"subscribe\":true}") == 0 && rc.progress);
	CHECK(!hc_remove_step(1, "org.x.app", "/usr/palm/applications/x", &rc));
	CHECK(!hc_remove_step(2, "org.x.app", dev, &rc));
	CHECK(!hc_remove_step(0, "bad id", dev, &rc));

	CHECK(hc_progress_state("{\"status\": 31, \"statusValue\": 31}") == 1);
	CHECK(hc_progress_state("{\"returnValue\":true,\"status\":25,\"details\":{}}") == -1);
	CHECK(hc_progress_state("{\"returnValue\":true,\"status\":22}\n{\"status\" : 31}") == 1);
	CHECK(hc_progress_state("{\"returnValue\":true,\"status\":22}") == 0);
	CHECK(hc_progress_state("{\"returnValue\":false,\"errorText\":\"x\"}") == -1);
	CHECK(hc_progress_state("{\"status\":310}") == 0);
	CHECK(hc_progress_state("") == 0 && hc_progress_state(NULL) == 0);
	CHECK(hc_remove_reply_class("{\"returnValue\":true}") == 1);
	CHECK(hc_remove_reply_class("{\"returnValue\": false, \"errorText\": \"org.x is not removable\"}") == 0);
	CHECK(hc_remove_reply_class("{\"returnValue\":false,\"errorText\":\"nope\"}") == -1);
	CHECK(hc_remove_reply_class("") == -1 && hc_remove_reply_class(NULL) == -1);
}

static void test_luna_run(void)
{
	const char *argv[8];
	/* every privileged call carries the one identity, never a bare luna-send */
	CHECK(hc_luna_argv("luna://com.webos.notification/createAlert", "{}", 1, argv));
	CHECK(strcmp(argv[0], "/usr/bin/luna-send") == 0 && strcmp(argv[1], "-a") == 0 && strcmp(argv[2], HC_LUNA_IDENTITY) == 0);
	CHECK(strcmp(argv[3], "-n") == 0 && strcmp(argv[4], "1") == 0 && strcmp(argv[5], "luna://com.webos.notification/createAlert") == 0
		&& strcmp(argv[6], "{}") == 0 && argv[7] == NULL);
	CHECK(strncmp(HC_LUNA_IDENTITY, "com.webos.", 10) == 0);
	CHECK(hc_luna_argv("luna://com.webos.appInstallService/remove", "{}", 5, argv) && strcmp(argv[4], "20") == 0);
	static const char *const forwarded[] = {
		"listApps", "running", "getAppInfo", "closeByAppId", "setDefaultApp", "launchDefaultApp"
	};
	for (size_t i = 0; i < sizeof forwarded / sizeof forwarded[0]; i++) {
		char uri[160];
		snprintf(uri, sizeof uri, "luna://com.webos.applicationManager/%s", forwarded[i]);
		CHECK(hc_luna_argv(uri, "{}", 1, argv));
		CHECK(strcmp(argv[1], "-a") == 0 && strcmp(argv[2], HC_LUNA_IDENTITY) == 0);
	}
	CHECK(hc_luna_uri_allowed("luna://com.webos.settingsservice/getSystemSettings"));
	CHECK(hc_luna_uri_allowed("luna://com.webos.settingsservice/setSystemSettings"));
	CHECK(hc_luna_uri_allowed("luna://com.webos.applicationManager/removeLaunchPoint"));
	CHECK(hc_luna_uri_allowed("luna://com.webos.applicationManager/launch"));
	CHECK(hc_luna_uri_allowed("luna://com.webos.service.eim/getLastInput"));
    CHECK(hc_luna_argv("luna://com.webos.service.config/getConfigs", "{}", 1, argv));
    CHECK(hc_luna_argv("luna://com.webos.service.config/setConfigs", "{}", 1, argv));
    CHECK(hc_luna_argv("luna://com.webos.surfacemanager/getCurrentState", "{}", 1, argv));
    CHECK(hc_call_lookup("luna://com.webos.service.config/setConfigs") == HC_CALL_DENIED);
	CHECK(hc_luna_uri_allowed("luna://com.webos.appInstallService/dev/remove"));
	CHECK(hc_luna_uri_allowed("luna://com.webos.service.systemprofile/debug/rescan"));
	CHECK(hc_luna_uri_allowed("luna://com.webos.service.systemprofile/getProfiles"));
	CHECK(hc_luna_uri_allowed("luna://com.webos.service.preloadmanager/setPreloadPolicy"));
	CHECK(hc_luna_uri_allowed("luna://com.webos.applicationManager/getAppLifeEvents"));
	CHECK(hc_call_lookup("luna://com.webos.service.preloadmanager/setPreloadPolicy") == HC_CALL_DENIED);
	CHECK(hc_call_lookup("luna://com.webos.service.systemprofile/getProfiles") == HC_CALL_DENIED);
	CHECK(!hc_luna_uri_allowed("luna://com.webos.applicationManager/uninstallApp"));
	CHECK(!hc_luna_uri_allowed("luna://com.webos.appInstallService/install"));
	CHECK(!hc_luna_uri_allowed(NULL));
	CHECK(!hc_luna_argv("luna://com.webos.appInstallService/install", "{}", 1, argv));
	CHECK(!hc_luna_argv("luna://com.webos.notification/createAlert", NULL, 1, argv));

	CHECK(hc_token_ok("0123456789abcdef0123456789abcdef"));
	CHECK(!hc_token_ok("0123456789ABCDEF0123456789abcdef"));
	CHECK(!hc_token_ok("0123456789abcdef0123456789abcde"));
	CHECK(!hc_token_ok("0123456789abcdef0123456789abcdefa"));
	CHECK(!hc_token_ok("0123456789abcdef0123456789abcde\""));
	CHECK(!hc_token_ok(NULL));
}

static int list_field_index(const char *name)
{
	for (int i = 0; hc_launcher_list_fields[i]; i++)
		if (strcmp(hc_launcher_list_fields[i], name) == 0) return i;
	return -1;
}

static void test_list_fields(void)
{
	/* the apiVersion 1 fields stay; appCategory is added so a launcher can find the other launchers */
	static const char *const v1[] = {"id", "title", "icon", "largeIcon", "folderPath", "version", "vendor", "type", "removable", "visible", "appCategory"};
	int n = 0;
	while (hc_launcher_list_fields[n]) n++;
	CHECK(n == (int)(sizeof v1 / sizeof v1[0]));
	for (size_t i = 0; i < sizeof v1 / sizeof v1[0]; i++) CHECK(list_field_index(v1[i]) >= 0);
	for (int i = 0; i < n; i++)
		for (int j = i + 1; j < n; j++) CHECK(strcmp(hc_launcher_list_fields[i], hc_launcher_list_fields[j]) != 0);
	/* nothing that reveals more than an app list needs */
	CHECK(list_field_index("requiredPermissions") < 0);
	CHECK(list_field_index("main") < 0);
	CHECK(list_field_index("trustLevel") < 0);
}

static void test_reboot(void)
{
	CHECK(hc_reboot_needed("active=no\nreboot=needed\n"));
	CHECK(hc_reboot_needed("reboot=needed"));
	CHECK(hc_reboot_needed("a=1\r\nreboot=needed\r\nb=2\r\n"));
	CHECK(hc_reboot_needed("a=1\n  reboot=needed \t\n"));
	CHECK(!hc_reboot_needed("reboot=no\n"));
	CHECK(!hc_reboot_needed("active=yes\n"));
	CHECK(!hc_reboot_needed(""));
	CHECK(!hc_reboot_needed("xreboot=needed\n"));
	CHECK(!hc_reboot_needed("reboot=needed2\n"));
	CHECK(!hc_reboot_needed("note: reboot=needed\n"));
	CHECK(!hc_reboot_needed(NULL));
	CHECK(hc_reboot_message("apply") && strcmp(hc_reboot_message("apply"), "Restart the TV to finish setting up the Home button.") == 0);
	CHECK(hc_reboot_message("remove") && strcmp(hc_reboot_message("remove"), "Restart the TV to finish removing the Home button patch.") == 0);
	CHECK(!hc_reboot_message("other"));
	CHECK(!hc_reboot_message("Apply"));
	CHECK(!hc_reboot_message(""));
	CHECK(!hc_reboot_message(NULL));
	const char *argv[8];
	CHECK(hc_luna_uri_allowed("luna://com.webos.service.tvpower/power/reboot"));
	CHECK(hc_luna_uri_allowed("luna://com.webos.notification/createToast"));
	CHECK(!hc_luna_uri_allowed("luna://com.webos.service.tvpower/power/shutdown"));
	CHECK(hc_luna_argv("luna://com.webos.service.tvpower/power/reboot", "{\"reason\":\"remoteKey\"}", 1, argv)
		&& strcmp(argv[0], "/usr/bin/luna-send") == 0 && strcmp(argv[1], "-a") == 0 && strcmp(argv[2], "com.webos.applicationManager") == 0
		&& strcmp(argv[3], "-n") == 0 && strcmp(argv[4], "1") == 0 && strcmp(argv[5], "luna://com.webos.service.tvpower/power/reboot") == 0
		&& strcmp(argv[6], "{\"reason\":\"remoteKey\"}") == 0 && !argv[7]);
}

static void test_api_v1(void)
{
	CHECK(strcmp(HC_GROUP, "org.webosbrew.homechooser.applications") == 0);
	CHECK(hc_sam_app_missing("Application not found"));
	CHECK(hc_sam_app_missing("app does not exist"));
	CHECK(hc_sam_app_missing("No such app"));
	CHECK(hc_sam_app_missing("APP IS NOT INSTALLED"));
	CHECK(!hc_sam_app_missing("Permission denied"));
	CHECK(!hc_sam_app_missing("Service does not exist: com.webos.applicationManager"));
	CHECK(!hc_sam_app_missing("Method not found"));
	CHECK(!hc_sam_app_missing("Permission not found"));
	CHECK(!hc_sam_app_missing("Permission does not exist"));
	CHECK(!hc_sam_app_missing("temporary failure"));
	CHECK(!hc_sam_app_missing(NULL));
	CHECK(hc_sam_app_ambiguous("Invalid appId specified OR Unsupported Application Type: org.example.absent"));
	CHECK(hc_sam_app_ambiguous("INVALID APPID SPECIFIED OR UNSUPPORTED APPLICATION TYPE"));
	CHECK(!hc_sam_app_ambiguous("Permission denied"));
	CHECK(!hc_sam_app_ambiguous("App not found"));
	CHECK(!hc_sam_app_ambiguous(NULL));
	CHECK(strcmp(hc_unusable_reply_code(-2), "timeout") == 0);
	CHECK(strcmp(hc_unusable_reply_code(-1), "badReply") == 0);
	CHECK(strcmp(hc_unusable_reply_code(0), "badReply") == 0);
	CHECK(strcmp(hc_sam_refusal_code(true, "App not found"), "notFound") == 0);
	CHECK(strcmp(hc_sam_refusal_code(true, "Permission denied"), "refused") == 0);
	CHECK(strcmp(hc_sam_refusal_code(false, "App not found"), "refused") == 0);
	CHECK(strcmp(hc_remove_verdict_code(HC_REMOVE_SELF), "protected") == 0);
	CHECK(strcmp(hc_remove_verdict_code(HC_REMOVE_HBC), "protected") == 0);
	CHECK(strcmp(hc_remove_verdict_code(HC_REMOVE_CALLER), "protected") == 0);
	CHECK(strcmp(hc_remove_verdict_code(HC_REMOVE_SYSTEM), "notRemovable") == 0);
	CHECK(strcmp(hc_remove_verdict_code(HC_REMOVE_BADID), "badRequest") == 0);
}

int main(void)
{
	test_sender();
	test_files();
	test_calls();
	test_commands();
	test_icons();
	test_icon_confinement();
	test_base64_and_lines();
	test_caller();
	test_remove();
	test_luna_run();
	test_reboot();
	test_list_fields();
	test_api_v1();
	printf("%d checks, %d failed\n", checks, failures);
	return failures ? 1 : 0;
}
