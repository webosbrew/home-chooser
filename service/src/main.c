/* SPDX-License-Identifier: Apache-2.0 */
#include <signal.h>
#include <string.h>
#include <unistd.h>
#include "log.h"
#include "roles.h"
#include "version.h"

/* One executable: the on-demand LS2 service and root one-off workers. */
PmLogContext hc_log;

int main(int argc, char **argv)
{
	signal(SIGPIPE, SIG_IGN);
	PmLogGetContext("HomeChooser", &hc_log);
	LOGI("START", "version %s, uid %d", HC_VERSION, (int)geteuid());

	if (argc > 1 && (!strcmp(argv[1], "--boot") || !strcmp(argv[1], "--homekey")))
		return hc_homekey_main(argc, argv);
	/* A native watcher role will join this dispatch when the shell watcher is ported. */
	return hc_service_main();
}
