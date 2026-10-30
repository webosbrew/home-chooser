/* SPDX-License-Identifier: Apache-2.0 */
#include <signal.h>
#include <string.h>
#include <unistd.h>
#include "log.h"
#include "roles.h"
#include "version.h"

PmLogContext hc_log;

int main(int argc, char **argv)
{
    signal(SIGPIPE, SIG_IGN);
    PmLogGetContext("HomeChooser", &hc_log);
    LOGI("START", "daemon version %s, uid %d", HC_VERSION, (int)geteuid());
    if (geteuid() != 0) return 1;
    if (argc < 2 || (strcmp(argv[1], "--boot") && strcmp(argv[1], "--homekey"))) return 2;
    if (getpgrp() != getpid() && setpgid(0, 0)) return 1;
    return hc_homekey_main(argc, argv);
}
