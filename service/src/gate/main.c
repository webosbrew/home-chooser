/* SPDX-License-Identifier: Apache-2.0 */
/* SAM executes this as /usr/bin/com.webos.app.home (a bind mount) with its own argv. */
#include <signal.h>
#include "gate/gate.h"
#include "log.h"

PmLogContext hc_log;

int main(int argc, char **argv)
{
    signal(SIGPIPE, SIG_IGN);
    PmLogGetContext("HomeChooser", &hc_log);
    HCGateConfig config = hc_gate_config();
    return hc_gate_main(&config, argc, argv);
}
