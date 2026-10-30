#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# HBC entry must enqueue a single native boot worker and return without waiting.
set -eu
SH=${1:-sh}
HERE=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT HUP INT TERM
mkdir -p "$T/bin"
printf '#!/bin/sh\necho "$*" >> "$HC_TEST_COMMANDS"\nexit 0\n' > "$T/bin/systemd-run"
printf '#!/bin/sh\necho "$*" >> "$HC_TEST_COMMANDS"\n[ "$1" != is-active ]\n' > "$T/bin/systemctl"
printf '#!/bin/sh\nexit 99\n' > "$T/service"
chmod +x "$T/bin/"* "$T/service"
printf 'VERSION_ID="7.6.0"\n' > "$T/os-release"
export HOMECHOOSER_OSRELEASE="$T/os-release"
export HOMECHOOSER_BINARY="$T/service" HOMECHOOSER_SYSTEMCTL="$T/bin/systemctl" HOMECHOOSER_SYSTEMD_RUN="$T/bin/systemd-run" HC_TEST_COMMANDS="$T/commands"
$SH "$HERE/homechooser"
[ "$(grep -c -- --no-block "$T/commands")" = 1 ]
grep -qF -- "--unit=homechooser-home --property=Restart=on-failure --property=RestartSec=5 /bin/sh -c" "$T/commands"
grep -qF -- "homechooser-boot $T/service /tmp/homechooser-runtime/homechooser-service" "$T/commands"
! grep -E 'sleep|mount|restart sam|surface-manager' "$T/commands"
: > "$T/commands"
$SH "$HERE/homechooser" watch
[ "$(grep -c -- --no-block "$T/commands")" = 1 ]
printf '#!/bin/sh\nexit 0\n' > "$T/bin/systemctl"
: > "$T/commands"
$SH "$HERE/homechooser"
[ ! -s "$T/commands" ]
printf 'PASS boot entry: one nonblocking native unit, no boot waits, existing unit retained (%s)\n' "$SH"
