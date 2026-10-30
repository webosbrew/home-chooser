/* SPDX-License-Identifier: Apache-2.0 */
#include <signal.h>
#include <unistd.h>
#include "log.h"
#include "roles.h"
#include "version.h"

/* On-demand LS2 service only. Root workers live in homechooser-daemon. */
PmLogContext hc_log;

int main(void)
{
	signal(SIGPIPE, SIG_IGN);
	PmLogGetContext("HomeChooser", &hc_log);
	LOGI("START", "version %s, uid %d", HC_VERSION, (int)geteuid());

	return hc_service_main();
}
