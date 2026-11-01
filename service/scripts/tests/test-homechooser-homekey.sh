#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Native transactions and policy, with fake Luna and real firmware userspace libraries.
set -e
REPO=$(cd "$(dirname "$0")/../../.." && pwd)
KEYFILTER_ROOTFS="${HOMECHOOSER_TEST_ROOTFS:-${KEYFILTER_ROOTFS:-$HOME/Projects/webos-firmwares/04.60.90.01-HE_DTV_W22P_AFADATAA/rootfs}}"
cmake -S "$REPO/service/test" -B "$REPO/build/service-host" -DKEYFILTER_ROOTFS="$KEYFILTER_ROOTFS" >/dev/null
cmake --build "$REPO/build/service-host"
exec ctest --test-dir "$REPO/build/service-host" --output-on-failure -R '^test-homekey$'
