#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Native transactions and policy, with fake Luna and real firmware userspace libraries.
HERE=$(cd "$(dirname "$0")/../.." && pwd)
exec make -C "$HERE" test-homekey ROOTFS="${HOMECHOOSER_TEST_ROOTFS:-$HOME/Projects/webos-firmwares/04.60.90.01-HE_DTV_W22P_AFADATAA/rootfs}"
