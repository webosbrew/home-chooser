/* Host-side test of src/policy.c: make test */
#include <stdio.h>
#include <string.h>

#include "policy.h"

static int failures, checks;
#define CHECK(cond) do { checks++; if (!(cond)) { failures++; printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } } while (0)

static void test_sender(void)
{
	CHECK(oh_sender_ok("org.webosbrew.openhouse"));
	CHECK(oh_sender_ok("org.webosbrew.openhouse 1234"));
	CHECK(!oh_sender_ok(NULL));
	CHECK(!oh_sender_ok(""));
	CHECK(!oh_sender_ok("org.webosbrew.openhouse.service"));
	CHECK(!oh_sender_ok("org.webosbrew.openhouse2"));
	CHECK(!oh_sender_ok("org.webosbrew.openhouse-evil"));
	CHECK(!oh_sender_ok("com.webos.app.home"));
	CHECK(!oh_sender_ok("org.webosbrew.hbchannel"));
}

static void test_files(void)
{
	CHECK(oh_file_allowed("/var/lib/webosbrew/openhouse/home"));
	CHECK(oh_file_allowed("/var/lib/webosbrew/openhouse/keyfilter"));
	CHECK(oh_file_allowed("/var/webos-profile/override/org.webosbrew.openhouse.json"));
	CHECK(!oh_file_allowed(NULL));
	CHECK(!oh_file_allowed("/etc/passwd"));
	CHECK(!oh_file_allowed("/var/lib/webosbrew/openhouse/home/"));
	CHECK(!oh_file_allowed("/var/lib/webosbrew/openhouse/home.tmp"));
	CHECK(!oh_file_allowed("/var/lib/webosbrew/openhouse/../init.d/x"));
	CHECK(!oh_file_allowed("/var/webos-profile/override/other.json"));
	CHECK(!oh_file_allowed("/var/lib/webosbrew/init.d/openhouse"));
	CHECK(!oh_file_allowed("/etc/os-release"));
	CHECK(oh_file_readable("/etc/os-release"));
	CHECK(oh_file_readable("/var/lib/webosbrew/openhouse/home"));
	CHECK(!oh_file_readable("/etc/shadow"));
	CHECK(!oh_file_readable("/etc/os-release2"));
	CHECK(!oh_file_readable(NULL));
}

static void test_calls(void)
{
	CHECK(oh_call_lookup("luna://com.webos.applicationManager/listApps") == OH_CALL_PLAIN);
	CHECK(oh_call_lookup("luna://com.webos.applicationManager/getAppInfo") == OH_CALL_PLAIN);
	CHECK(oh_call_lookup("luna://com.webos.applicationManager/setDefaultApp") == OH_CALL_PLAIN);
	CHECK(oh_call_lookup("luna://com.webos.applicationManager/launchDefaultApp") == OH_CALL_PLAIN);
	CHECK(oh_call_lookup("luna://com.webos.applicationManager/closeByAppId") == OH_CALL_PLAIN);
	CHECK(oh_call_lookup("luna://com.webos.service.systemprofile/getProfiles") == OH_CALL_PLAIN);
	CHECK(oh_call_lookup("luna://com.webos.service.systemprofile/debug/rescan") == OH_CALL_PLAIN);
	CHECK(oh_call_lookup("luna://com.webos.settingsservice/getSystemSettings") == OH_CALL_SETTINGS_GET);
	CHECK(oh_call_lookup("luna://com.webos.settingsservice/setSystemSettings") == OH_CALL_SETTINGS_SET);
	CHECK(oh_call_lookup(NULL) == OH_CALL_DENIED);
	CHECK(oh_call_lookup("luna://com.webos.applicationManager/listApps/") == OH_CALL_DENIED);
	CHECK(oh_call_lookup("luna://com.webos.applicationManager/launch") == OH_CALL_DENIED);
	CHECK(oh_call_lookup("luna://org.webosbrew.hbchannel.service/exec") == OH_CALL_DENIED);
	CHECK(oh_call_lookup("luna://com.webos.settingsservice/resetSystemSettings") == OH_CALL_DENIED);
	CHECK(oh_call_lookup("luna://com.webos.service.systemprofile/debug/rescan?x") == OH_CALL_DENIED);

	const char *good[] = {"defaultApps"};
	const char *two[] = {"defaultApps", "x"};
	const char *other[] = {"localeInfo"};
	CHECK(oh_settings_ok("general", good, 1));
	CHECK(!oh_settings_ok("general", two, 2));
	CHECK(!oh_settings_ok("general", other, 1));
	CHECK(!oh_settings_ok("general", good, 0));
	CHECK(!oh_settings_ok("network", good, 1));
	CHECK(!oh_settings_ok(NULL, good, 1));
}

static void test_commands(void)
{
	const oh_command *c = oh_command_lookup("restartSam");
	CHECK(c && strcmp(c->argv[0], "/bin/systemctl") == 0 && strcmp(c->argv[1], "restart") == 0 && strcmp(c->argv[2], "sam") == 0 && !c->argv[3]);
	c = oh_command_lookup("watch");
	CHECK(c && strcmp(c->argv[0], "/bin/sh") == 0 && strcmp(c->argv[1], OH_APP_DIR "/backport/openhouse") == 0 && strcmp(c->argv[2], "watch") == 0 && !c->argv[3]);
	const char *kf[] = {"keyfilterStatus", "status", "keyfilterApply", "apply", "keyfilterStop", "stop", "keyfilterRefresh", "refresh"};
	for (int i = 0; i < 8; i += 2) {
		c = oh_command_lookup(kf[i]);
		CHECK(c && strcmp(c->argv[1], OH_APP_DIR "/backport/openhouse-keyfilter") == 0 && strcmp(c->argv[2], kf[i + 1]) == 0 && !c->argv[3]);
	}
	c = oh_command_lookup("stopWatcher");
	CHECK(c && c->tolerant && strcmp(c->argv[2], "openhouse-home") == 0);
	c = oh_command_lookup("watcherActive");
	CHECK(c && c->tolerant && strcmp(c->argv[1], "is-active") == 0);
	c = oh_command_lookup("watchLog");
	CHECK(c && c->native_tail);
	CHECK(!oh_command_lookup(NULL));
	CHECK(!oh_command_lookup("sh"));
	CHECK(!oh_command_lookup("systemctl restart sam"));
	CHECK(!oh_command_lookup("restartSam "));
	/* every argv[0] is absolute and nothing is left unterminated */
	static const char *const names[] = {"restartSam", "watch", "keyfilterStatus", "keyfilterApply", "keyfilterStop", "keyfilterRefresh", "stopWatcher", "watcherActive"};
	for (size_t i = 0; i < sizeof names / sizeof *names; i++) {
		c = oh_command_lookup(names[i]);
		CHECK(c && c->argv[0][0] == '/' && c->timeout_s > 0);
	}
}

static void test_icons(void)
{
	char out[128];
	CHECK(oh_icon_path("/usr/palm/applications/a", "icon.png", out, sizeof out) && strcmp(out, "/usr/palm/applications/a/icon.png") == 0);
	CHECK(oh_icon_path("/usr/palm/applications/a/", "img/i.png", out, sizeof out) && strcmp(out, "/usr/palm/applications/a/img/i.png") == 0);
	CHECK(oh_icon_path("/usr/palm/applications/a", "/usr/palm/applications/a/icon.png", out, sizeof out) && strcmp(out, "/usr/palm/applications/a/icon.png") == 0);
	CHECK(!oh_icon_path("usr/palm", "icon.png", out, sizeof out));
	CHECK(!oh_icon_path("/usr/palm/../../etc", "icon.png", out, sizeof out));
	CHECK(!oh_icon_path("/usr/palm/a", "../b/icon.png", out, sizeof out));
	CHECK(!oh_icon_path("/usr/palm/a", "/etc/../shadow.png", out, sizeof out));
	CHECK(!oh_icon_path("/usr/palm/a", "", out, sizeof out));
	CHECK(!oh_icon_path(NULL, "icon.png", out, sizeof out));
	CHECK(!oh_icon_path("/usr/palm/applications/a", "icon.png", out, 10));
	CHECK(oh_icon_path("/a/..b", "icon.png", out, sizeof out)); /* "..b" is a name, not a parent */

	CHECK(oh_path_within("/a/b/c.png", "/a/b"));
	CHECK(oh_path_within("/a/b/c.png", "/a/b/"));
	CHECK(oh_path_within("/a/b", "/a/b"));
	CHECK(!oh_path_within("/a/bc/c.png", "/a/b"));
	CHECK(!oh_path_within("/etc/passwd", "/a/b"));
	CHECK(oh_path_within("/a", "/"));

	CHECK(strcmp(oh_icon_mime("/x/icon.PNG"), "image/png") == 0);
	CHECK(strcmp(oh_icon_mime("/x/icon.jpeg"), "image/jpeg") == 0);
	CHECK(strcmp(oh_icon_mime("/x/icon.svg"), "image/svg+xml") == 0);
	CHECK(oh_icon_mime("/x/shadow") == NULL);
	CHECK(oh_icon_mime("/x.d/shadow") == NULL);
	CHECK(oh_icon_mime("/x/icon.png.sh") == NULL);
	CHECK(oh_icon_mime(NULL) == NULL);
}

static void test_base64_and_lines(void)
{
	char out[32];
	CHECK(oh_base64((const unsigned char *)"", 0, out, sizeof out) && strcmp(out, "") == 0);
	CHECK(oh_base64((const unsigned char *)"f", 1, out, sizeof out) && strcmp(out, "Zg==") == 0);
	CHECK(oh_base64((const unsigned char *)"fo", 2, out, sizeof out) && strcmp(out, "Zm8=") == 0);
	CHECK(oh_base64((const unsigned char *)"foo", 3, out, sizeof out) && strcmp(out, "Zm9v") == 0);
	CHECK(oh_base64((const unsigned char *)"foobar", 6, out, sizeof out) && strcmp(out, "Zm9vYmFy") == 0);
	CHECK(!oh_base64((const unsigned char *)"foobar", 6, out, 8));

	const char *t = "a\nb\nc\nd\n";
	CHECK(oh_last_lines(t, strlen(t), 2) == 4);   /* "c\nd\n" */
	CHECK(oh_last_lines(t, strlen(t), 9) == 0);
	CHECK(oh_last_lines("a\nb", 3, 1) == 2);
	CHECK(oh_last_lines("", 0, 5) == 0);
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
