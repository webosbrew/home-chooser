/* SPDX-License-Identifier: Apache-2.0 */
/* Who may register a luna service name: the hub's own role files, read to decide whether a native
 * launcher's sender service name belongs to the app of that id (docs/service.md, "Native launchers").
 * Plain libc, so the host tests build it with sanitizers. */
#ifndef HC_NAMEOWNER_H
#define HC_NAMEOWNER_H
#include <stdbool.h>
#include <stddef.h>

#define HC_DEV_APPS_DIR "/media/developer/apps/usr/palm/applications/"

typedef enum {
	HC_ROLE_UNRELATED, /* no allowed name of the role matches the name */
	HC_ROLE_OWN,       /* it matches, and the role's executable is inside the app's own folder */
	HC_ROLE_FOREIGN,   /* it matches, and the executable is elsewhere (or the role has an app id instead) */
	HC_ROLE_INVALID    /* the file is not a role file this code understands */
} HcRoleVerdict;

/* The hub's allowed-name patterns are globs: '*' any run of characters, '?' one character. */
bool hc_name_glob(const char *pattern, const char *name);

/* One role file's text. The pattern "*" alone (every name: system executables such as luna-send and
 * node, and executables Homebrew Channel has elevated to root) is not counted as a match. */
HcRoleVerdict hc_role_judge(const char *text, const char *name);

/* Every role file the hub reads, under root ("" on the device): the manifests' role lists, the
 * legacy ls2 role directories and the roles.d directories, system and developer. True when at least
 * one role allowing `name` has its executable in HC_DEV_APPS_DIR/<name>/ and no other role allows it, and
 * everything that exists could be read. Otherwise false, with the reason in why. */
bool hc_name_owned(const char *root, const char *name, char *why, size_t whylen);
#endif
