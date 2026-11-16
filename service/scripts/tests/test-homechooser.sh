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
printf 'Rockhopper release 7.6.0-19 (mullet-mullet)\n' > "$T/starfish-release"
export HOMECHOOSER_RELEASE_FILE="$T/starfish-release"
export HOMECHOOSER_BINARY="$T/service" HOMECHOOSER_SYSTEMCTL="$T/bin/systemctl" HOMECHOOSER_SYSTEMD_RUN="$T/bin/systemd-run" HC_TEST_COMMANDS="$T/commands"
$SH "$HERE/homechooser"
[ "$(grep -c -- --no-block "$T/commands")" = 1 ]
grep -qF -- "--unit=homechooser-home --property=Restart=on-failure --property=RestartSec=5 /bin/sh -c" "$T/commands"
grep -qF -- "homechooser-boot $T/service /tmp/homechooser-runtime/homechooser-daemon" "$T/commands"
! grep -E 'sleep|mount|restart sam|surface-manager' "$T/commands"
: > "$T/commands"
$SH "$HERE/homechooser" watch
[ "$(grep -c -- --no-block "$T/commands")" = 1 ]
printf '#!/bin/sh\nexit 0\n' > "$T/bin/systemctl"
: > "$T/commands"
$SH "$HERE/homechooser"
[ ! -s "$T/commands" ]
# Legacy boot staging and stopped/no-choice uninstall recovery use the same executable.
printf '#!/bin/sh\necho "$*" >> "$HC_TEST_COMMANDS"\n[ "$1" != is-active ]\n' > "$T/bin/systemctl"
mkdir -p "$T/service-scripts/lib"
cp "$HERE/homechooser" "$T/service-scripts/homechooser"
printf '#!/bin/sh\nexit 0\n' > "$T/service-scripts/homechooser-access"
printf 'HC_TEST=yes\n' > "$T/service-scripts/lib/common.sh"
export HOMECHOOSER_SERVICE_SCRIPTS="$T/service-scripts" HOMECHOOSER_BOOT_DIR="$T/boot" HOMECHOOSER_CHOICE="$T/home"
for major in 4 5 6 7 8 9 10 11 12; do
    printf 'Rockhopper release %s.0.0-1 (test)\n' "$major" > "$T/starfish-release"
    rm -rf "$T/boot"
    $SH "$HERE/homechooser" stage
    if [ "$major" -ge 6 ] && [ "$major" -le 9 ]; then
        [ ! -e "$T/boot" ]
    else
        cmp "$T/service" "$T/boot/homechooser-daemon"
        cmp "$HERE/homechooser" "$T/boot/homechooser"
        test -x "$T/boot/homechooser-daemon"
        test -f "$T/boot/lib/common.sh"
        : > "$T/commands"
        $SH "$HERE/homechooser"
        [ ! -s "$T/commands" ] # unchosen installed legacy boot only applies access
        : > "$T/commands"
        $SH "$HERE/homechooser" watch
        grep -qF -- --no-block "$T/commands"
        # Package removed: cached boot entry still starts with no stored choice.
        mv "$T/service" "$T/service.saved"
        : > "$T/commands"
        $SH "$T/boot/homechooser"
        grep -qF -- "homechooser-boot $T/boot/homechooser-daemon /tmp/homechooser-runtime/homechooser-daemon" "$T/commands"
        mv "$T/service.saved" "$T/service"
        $SH "$HERE/homechooser" unstage
        [ ! -e "$T/boot" ]
    fi
done
# The default executable path and the Home-key worker both name the daemon.
cp "$HERE/homechooser-homekey" "$T/service-scripts/homechooser-homekey"
printf '#!/bin/sh\necho "$*" >> "$HC_TEST_COMMANDS"\n' > "$T/homechooser-daemon"
chmod +x "$T/homechooser-daemon"
: > "$T/commands"
(unset HOMECHOOSER_BINARY; $SH "$T/service-scripts/homechooser-homekey" refresh stock)
grep -qF -- '--homekey refresh stock' "$T/commands"
: > "$T/commands"
(unset HOMECHOOSER_BINARY; $SH "$HERE/homechooser" watch)
grep -qF -- "homechooser-boot $T/service-scripts/../homechooser-daemon /tmp/homechooser-runtime/homechooser-daemon" "$T/commands"
# No systemd (webOS 4.x runs upstart): a detached loop in its own process group, a pid file, one instance.
printf '#!/bin/sh\necho "$$ $*" >> "$HC_TEST_RAN"\nexec sleep 30\n' > "$T/watcher-daemon"
chmod +x "$T/watcher-daemon"
(
    export HOMECHOOSER_SYSTEMD_RUN="$T/missing-systemd-run" HOMECHOOSER_SYSTEMCTL="$T/missing-systemctl"
    export HOMECHOOSER_BINARY="$T/watcher-daemon" HOMECHOOSER_RUNTIME="$T/rt" HC_TEST_RAN="$T/ran" HOMECHOOSER_CHOICE="$T/home"
    printf 'Rockhopper release 4.4.3-22 (goldilocks-gorongosa)\n' > "$T/starfish-release"
    : > "$T/ran"
    [ "$($SH "$HERE/homechooser" active)" = inactive ] || exit 1
    $SH "$HERE/homechooser" watch
    n=0; while [ ! -s "$T/ran" ] && [ $n -lt 50 ]; do sleep 0.1; n=$((n + 1)); done
    grep -q -- '--boot' "$T/ran"
    [ "$($SH "$HERE/homechooser" active)" = active ]
    pid=$(cat "$T/rt/watcher.pid")
    kill -0 "$pid"
    $SH "$HERE/homechooser" watch # already running: no second loop
    [ "$(cat "$T/rt/watcher.pid")" = "$pid" ]
    [ "$(wc -l < "$T/ran")" = 1 ]
    $SH "$HERE/homechooser" stop
    n=0; while kill -0 "$pid" 2>/dev/null && [ $n -lt 50 ]; do sleep 0.1; n=$((n + 1)); done
    if kill -0 "$pid" 2>/dev/null; then echo 'watcher loop still alive after stop'; exit 1; fi
    [ "$($SH "$HERE/homechooser" active)" = inactive ]
    [ ! -e "$T/rt/watcher.pid" ] || exit 1
    [ -z "$(ps -o pid= -g "$pid")" ] || { echo 'process group left behind'; exit 1; }
    $SH "$HERE/homechooser" stop # nothing to stop is fine
) || { echo "FAIL watcher without systemd"; exit 1; }
printf 'PASS boot entry: native unit, active-unit guard, 5-12 staging and stopped uninstall fallback, watcher loop without systemd (%s)\n' "$SH"
