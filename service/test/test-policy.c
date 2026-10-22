/* Host-side test of src/policy.c: make test */
#include <stdio.h>
#include <string.h>

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
	CHECK(hc_file_allowed("/var/lib/webosbrew/homechooser/keyfilter"));
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
	CHECK(hc_call_lookup("luna://com.webos.service.systemprofile/getProfiles") == HC_CALL_PLAIN);
	CHECK(hc_call_lookup("luna://com.webos.service.systemprofile/debug/rescan") == HC_CALL_PLAIN);
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
	const hc_command *c = hc_command_lookup("restartSam");
	CHECK(c && strcmp(c->argv[0], "/bin/systemctl") == 0 && strcmp(c->argv[1], "restart") == 0 && strcmp(c->argv[2], "sam") == 0 && !c->argv[3]);
	c = hc_command_lookup("watch");
	CHECK(c && strcmp(c->argv[0], "/bin/sh") == 0 && strcmp(c->argv[1], HC_APP_DIR "/backport/homechooser") == 0 && strcmp(c->argv[2], "watch") == 0 && !c->argv[3]);
	const char *kf[] = {"keyfilterStatus", "status", "keyfilterApply", "apply", "keyfilterStop", "stop", "keyfilterRefresh", "refresh"};
	for (int i = 0; i < 8; i += 2) {
		c = hc_command_lookup(kf[i]);
		CHECK(c && strcmp(c->argv[1], HC_APP_DIR "/backport/homechooser-keyfilter") == 0 && strcmp(c->argv[2], kf[i + 1]) == 0 && !c->argv[3]);
	}
	c = hc_command_lookup("stopWatcher");
	CHECK(c && c->tolerant && strcmp(c->argv[2], "homechooser-home") == 0);
	c = hc_command_lookup("watcherActive");
	CHECK(c && c->tolerant && strcmp(c->argv[1], "is-active") == 0);
	c = hc_command_lookup("watchLog");
	CHECK(c && c->native_tail);
	CHECK(!hc_command_lookup(NULL));
	CHECK(!hc_command_lookup("sh"));
	CHECK(!hc_command_lookup("systemctl restart sam"));
	CHECK(!hc_command_lookup("restartSam "));
	/* every argv[0] is absolute and nothing is left unterminated */
	static const char *const names[] = {"restartSam", "watch", "keyfilterStatus", "keyfilterApply", "keyfilterStop", "keyfilterRefresh", "stopWatcher", "watcherActive"};
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

int main(void)
{
	test_sender();
	test_files();
	test_calls();
	test_commands();
	test_icons();
	test_base64_and_lines();
	printf("%d checks, %d failed\n", checks, failures);
	return failures ? 1 : 0;
}
