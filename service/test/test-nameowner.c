/* SPDX-License-Identifier: Apache-2.0 */
/* Host-side test of src/nameowner.c (who may register a service name): ctest --test-dir build/service-host -R test-nameowner
 * The role texts are the ones appinstalld wrote on 8.3.0 (a developer app, its service, Homebrew Channel's elevation). */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "nameowner.h"

static int failures, checks;
#define CHECK(cond) do { checks++; if (!(cond)) { failures++; printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } } while (0)

#define APPS "/media/developer/apps/usr/palm/applications/"
#define SERVICES "/media/developer/apps/usr/palm/services/"

static const char *DOCK_PRV =
	"{\n    \"role\": {\n    \"exeName\":\"" APPS "org.webosbrew.dock/dock\",\n    \"type\": \"regular\",\n"
	"    \"allowedNames\": [\"com.webos.media.client.*\",\"org.webosbrew.dock\",\"\"]\n    },\n"
	"    \"permissions\": [{\"service\":\"org.webosbrew.dock\",\"outbound\":[\"*\"],\"inbound\":[]}]\n}\n";
static const char *DOCK_SERVICE =
	"{\"exeName\": \"" SERVICES "org.webosbrew.dock.service/dock-service\", \"type\": \"regular\","
	"\"allowedNames\": [\"org.webosbrew.dock.service\", \"com.webos.avconnector.*\", \"\"]}";
static const char *HC_ELEVATED =
	"{\"exeName\":\"" SERVICES "org.webosbrew.homechooser.service/homechooser-service\",\"type\":\"regular\","
	"\"allowedNames\":[\"org.webosbrew.homechooser.service\",\"\",\"*\",\"com.webos.service.capture.client*\"],"
	"\"permissions\":[{\"service\":\"*\",\"inbound\":[\"*\"],\"outbound\":[\"*\"]}]}";
/* appinstalld's JSService template for a service id that is a dotted extension of an app id */
static const char *IMPERSONATOR =
	"{\"exeName\":\"" SERVICES "org.webosbrew.dock/evil\",\"type\":\"regular\",\"allowedNames\":[\"org.webosbrew.dock\",\"\"]}";
static const char *WEBAPP = "{\"appId\":\"org.webosbrew.dock-a\",\"type\":\"regular\",\"allowedNames\":[\"org.webosbrew.dock-a-*\"]}";
static const char *WEBAPP_HYPHEN =
	"{\"appId\":\"org.webosbrew.dock\",\"type\":\"regular\",\"allowedNames\":[\"org.webosbrew.dock-*\"]}";

static void test_glob(void)
{
	CHECK(hc_name_glob("a.b", "a.b"));
	CHECK(!hc_name_glob("a.b", "a.bc"));
	CHECK(!hc_name_glob("a.b", "a"));
	CHECK(hc_name_glob("a.*", "a.b.c"));
	CHECK(!hc_name_glob("a.*", "a"));
	CHECK(hc_name_glob("a-*", "a-"));
	CHECK(hc_name_glob("*", "anything"));
	CHECK(hc_name_glob("a?c", "abc"));
	CHECK(!hc_name_glob("a?c", "ac"));
	CHECK(hc_name_glob("*.service", "x.y.service"));
	CHECK(!hc_name_glob("", "a"));
	CHECK(hc_name_glob("", ""));
	CHECK(hc_name_glob("a*b*c", "aXbYbZc"));
	CHECK(!hc_name_glob("a*b*c", "aXbYbZ"));
}

static void test_judge(void)
{
	const char *n = "org.webosbrew.dock";
	CHECK(hc_role_judge(DOCK_PRV, n) == HC_ROLE_OWN);
	CHECK(hc_role_judge(DOCK_PRV, "org.webosbrew.dock2") == HC_ROLE_UNRELATED);
	CHECK(hc_role_judge(DOCK_SERVICE, n) == HC_ROLE_UNRELATED);
	CHECK(hc_role_judge(DOCK_SERVICE, "org.webosbrew.dock.service") == HC_ROLE_FOREIGN); /* a service's exe is not in applications/ */
	CHECK(hc_role_judge(HC_ELEVATED, n) == HC_ROLE_UNRELATED); /* "*" alone is not a match */
	CHECK(hc_role_judge(IMPERSONATOR, n) == HC_ROLE_FOREIGN);
	CHECK(hc_role_judge(WEBAPP, n) == HC_ROLE_UNRELATED);
	CHECK(hc_role_judge(WEBAPP_HYPHEN, "org.webosbrew.dock-x") == HC_ROLE_FOREIGN);
	CHECK(hc_role_judge(WEBAPP_HYPHEN, n) == HC_ROLE_UNRELATED);
	/* the executable must be inside the folder, not merely start with the same text */
	CHECK(hc_role_judge("{\"exeName\":\"" APPS "org.webosbrew.dock2/x\",\"allowedNames\":[\"org.webosbrew.dock\"]}", n) == HC_ROLE_FOREIGN);
	CHECK(hc_role_judge("{\"exeName\":\"" APPS "org.webosbrew.dock/../evil/x\",\"allowedNames\":[\"org.webosbrew.dock\"]}", n) == HC_ROLE_FOREIGN);
	CHECK(hc_role_judge("{\"exeName\":\"" APPS "org.webosbrew.dock/\",\"allowedNames\":[\"org.webosbrew.dock\"]}", n) == HC_ROLE_FOREIGN);
	CHECK(hc_role_judge("{\"exeName\":\"/usr/bin/x\",\"allowedNames\":[\"org.webosbrew.*\"]}", n) == HC_ROLE_FOREIGN);
	CHECK(hc_role_judge("{\"exeName\":\"" APPS "org.webosbrew.dock/x\",\"appId\":\"y\",\"allowedNames\":[\"org.webosbrew.dock\"]}", n) == HC_ROLE_FOREIGN);
	/* escapes and odd layouts */
	CHECK(hc_role_judge("{\"exeName\":\"" APPS "org.webosbrew.dock\\/x\",\"allowedNames\":[\"org.webosbrew.\\u0064ock\"]}", n) == HC_ROLE_OWN);
	CHECK(hc_role_judge("{\"allowedNames\":[\"org.webosbrew.dock\"],\"permissions\":[{\"allowedNames\":1}],\"exeName\":\"" APPS "org.webosbrew.dock/x\"}", n) == HC_ROLE_OWN);
	/* not role files */
	CHECK(hc_role_judge("", n) == HC_ROLE_INVALID);
	CHECK(hc_role_judge("[]", n) == HC_ROLE_INVALID);
	CHECK(hc_role_judge("{", n) == HC_ROLE_INVALID);
	CHECK(hc_role_judge("{\"exeName\":\"/x\"}", n) == HC_ROLE_INVALID);
	CHECK(hc_role_judge("{\"allowedNames\":[\"a\"]}", n) == HC_ROLE_INVALID);
	CHECK(hc_role_judge("{\"exeName\":\"/x\",\"allowedNames\":[\"a\"]} x", n) == HC_ROLE_INVALID);
	CHECK(hc_role_judge("{\"exeName\":\"/x\",\"allowedNames\":[\"a\"],}", n) == HC_ROLE_INVALID);
	CHECK(hc_role_judge(NULL, n) == HC_ROLE_INVALID);
}

static char root[256];

static void put(const char *rel, const char *text)
{
	char path[512], dir[512];
	snprintf(path, sizeof path, "%s%s", root, rel);
	snprintf(dir, sizeof dir, "%s", path);
	for (char *p = dir + strlen(root) + 1; *p; p++)
		if (*p == '/') { *p = '\0'; mkdir(dir, 0755); *p = '/'; }
	FILE *f = fopen(path, "w");
	if (!f) { printf("cannot write %s\n", path); exit(2); }
	fputs(text, f);
	fclose(f);
}

static void del(const char *rel)
{
	char path[512];
	snprintf(path, sizeof path, "%s%s", root, rel);
	unlink(path);
}

static void test_scan(void)
{
	char why[256];
	const char *n = "org.webosbrew.dock";
	/* an empty tree: nothing allows the name */
	CHECK(!hc_name_owned(root, n, why, sizeof why) && strstr(why, "no role"));
	/* the legacy dev roles plus a manifest and an elevated service, as on 8.3.0 */
	put("/var/palm/ls2-dev/roles/prv/org.webosbrew.dock.json", DOCK_PRV);
	put("/var/palm/ls2-dev/roles/pub/org.webosbrew.dock.json", DOCK_PRV);
	put("/var/luna-service2-dev/roles.d/org.webosbrew.dock.service.service.json", DOCK_SERVICE);
	put("/var/luna-service2-dev/roles.d/org.webosbrew.homechooser.service.service.json", HC_ELEVATED);
	put("/var/luna-service2-dev/manifests.d/org.webosbrew.dock.json",
		"{\"id\":\"org.webosbrew.dock\",\"roleFilesPub\":[\"/var/palm/ls2-dev/roles/pub/org.webosbrew.dock.json\"],"
		"\"roleFilesPrv\":[\"/var/palm/ls2-dev/roles/prv/org.webosbrew.dock.json\"],"
		"\"roleFiles\":[\"/var/luna-service2-dev/roles.d/org.webosbrew.dock.service.service.json\"]}");
	CHECK(hc_name_owned(root, n, why, sizeof why));
	CHECK(!hc_name_owned(root, "org.webosbrew.other", why, sizeof why) && strstr(why, "no role"));
	CHECK(!hc_name_owned(root, "a/b", why, sizeof why));
	CHECK(!hc_name_owned(root, "", why, sizeof why));
	/* a foreign role in any directory refuses */
	put("/var/luna-service2-dev/roles.d/org.webosbrew.dock.evil.json", IMPERSONATOR);
	CHECK(!hc_name_owned(root, n, why, sizeof why) && strstr(why, "also allows"));
	del("/var/luna-service2-dev/roles.d/org.webosbrew.dock.evil.json");
	CHECK(hc_name_owned(root, n, why, sizeof why));
	put("/var/palm/ls2-dev/roles/pub/zz.json", IMPERSONATOR);
	CHECK(!hc_name_owned(root, n, why, sizeof why));
	del("/var/palm/ls2-dev/roles/pub/zz.json");
	put("/usr/share/luna-service2/roles.d/zz.role.json", "{\"exeName\":\"/usr/bin/x\",\"allowedNames\":[\"org.webosbrew.dock\"]}");
	CHECK(!hc_name_owned(root, n, why, sizeof why));
	del("/usr/share/luna-service2/roles.d/zz.role.json");
	/* a role only a manifest points at, in a directory nothing else scans, counts too */
	put("/somewhere/else/role.json", IMPERSONATOR);
	put("/var/luna-service2/manifests.d/zz.json", "{\"id\":\"zz\",\"roleFiles\":[\"/somewhere/else/role.json\"]}");
	CHECK(!hc_name_owned(root, n, why, sizeof why));
	del("/var/luna-service2/manifests.d/zz.json");
	del("/somewhere/else/role.json");
	/* ... and so does a manifests directory only the hub's configuration names */
	put("/etc/luna-service2/ls-hubd.conf", "[Security]\nManifestsDirectories=/usr/share/luna-service2/manifests.d;/opt/extra/manifests.d\n");
	put("/opt/extra/manifests.d/zz.json", "{\"roleFiles\":[\"/somewhere/else/role.json\"]}");
	put("/somewhere/else/role.json", IMPERSONATOR);
	CHECK(!hc_name_owned(root, n, why, sizeof why));
	del("/opt/extra/manifests.d/zz.json");
	del("/somewhere/else/role.json");
	CHECK(hc_name_owned(root, n, why, sizeof why));
	/* unreadable data refuses */
	put("/var/luna-service2-dev/roles.d/broken.json", "{not json");
	CHECK(!hc_name_owned(root, n, why, sizeof why) && strstr(why, "not a role"));
	del("/var/luna-service2-dev/roles.d/broken.json");
	put("/var/luna-service2-dev/manifests.d/broken.json", "[1]");
	CHECK(!hc_name_owned(root, n, why, sizeof why) && strstr(why, "not a manifest"));
	del("/var/luna-service2-dev/manifests.d/broken.json");
	if (geteuid() != 0) {
		char path[512];
		snprintf(path, sizeof path, "%s/var/luna-service2-dev/roles.d/org.webosbrew.dock.service.service.json", root);
		chmod(path, 0);
		CHECK(!hc_name_owned(root, n, why, sizeof why) && strstr(why, "cannot read"));
		chmod(path, 0644);
	}
	/* without the app's own role the name is not owned, even with nothing foreign around */
	del("/var/palm/ls2-dev/roles/prv/org.webosbrew.dock.json");
	del("/var/palm/ls2-dev/roles/pub/org.webosbrew.dock.json");
	CHECK(!hc_name_owned(root, n, why, sizeof why) && strstr(why, "no role"));
}

int main(void)
{
	char tmpl[] = "/tmp/hc-roles-XXXXXX";
	if (!mkdtemp(tmpl)) return 2;
	snprintf(root, sizeof root, "%s", tmpl);
	test_glob();
	test_judge();
	test_scan();
	char cmd[300];
	snprintf(cmd, sizeof cmd, "rm -rf '%s'", root);
	if (system(cmd) != 0) return 2;
	printf("%d checks, %d failures\n", checks, failures);
	return failures ? 1 : 0;
}
