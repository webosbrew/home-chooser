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
# No systemd (webOS 4.x runs upstart): a detached loop and its daemon (which has its own process group, like the real
# one), a pid file for each, one instance. The fake daemon logs each start; TERM ends it unless HC_TEST_STUBBORN is set.
mkdir -p "$T/w"
printf '#!/bin/sh\necho "$$ $*" >> "$HC_TEST_RAN"\nif [ -n "${HC_TEST_STUBBORN:-}" ]; then trap "" TERM; fi\nwhile :; do sleep 1; done\n' > "$T/w/homechooser-daemon"
chmod +x "$T/w/homechooser-daemon"
alive() { kill -0 "$1" 2>/dev/null; }
waitgone() { n=0; while alive "$1" && [ $n -lt 80 ]; do sleep 0.1; n=$((n + 1)); done; ! alive "$1"; }
waitran() { n=0; while [ "$(wc -l < "$T/ran")" -lt "$1" ] && [ $n -lt 50 ]; do sleep 0.1; n=$((n + 1)); done; [ "$(wc -l < "$T/ran")" -ge "$1" ]; }
(
    export HOMECHOOSER_SYSTEMD_RUN="$T/missing-systemd-run" HOMECHOOSER_SYSTEMCTL="$T/missing-systemctl"
    export HOMECHOOSER_BINARY="$T/w/homechooser-daemon" HOMECHOOSER_RUNTIME="$T/rt" HC_TEST_RAN="$T/ran" HOMECHOOSER_CHOICE="$T/home"
    printf 'Rockhopper release 4.4.3-22 (goldilocks-gorongosa)\n' > "$T/starfish-release"
    : > "$T/ran"
    [ "$($SH "$HERE/homechooser" active)" = inactive ] || exit 1
    $SH "$HERE/homechooser" watch
    waitran 1
    grep -q -- '--boot' "$T/ran"
    [ "$($SH "$HERE/homechooser" active)" = active ]
    pid=$(cat "$T/rt/watcher.pid")
    kill -0 "$pid"
    dpid=$(cat "$T/rt/watcher-daemon.pid")
    [ "$dpid" = "$(cut -d' ' -f1 "$T/ran")" ] || { echo 'daemon pid file is not the daemon'; exit 1; }
    $SH "$HERE/homechooser" watch # already running: no second loop
    [ "$(cat "$T/rt/watcher.pid")" = "$pid" ]
    [ "$(wc -l < "$T/ran")" = 1 ]
    $SH "$HERE/homechooser" stop
    alive "$pid" && { echo 'watcher loop still alive after stop'; exit 1; }
    alive "$dpid" && { echo 'daemon still alive after stop'; exit 1; }
    [ "$($SH "$HERE/homechooser" active)" = inactive ]
    [ ! -e "$T/rt/watcher.pid" ] && [ ! -e "$T/rt/watcher-daemon.pid" ] || exit 1
    sleep 6 # the loop's 5 s restart delay: nothing may come back
    [ "$(wc -l < "$T/ran")" = 1 ] || { echo 'daemon restarted during or after stop'; exit 1; }
    $SH "$HERE/homechooser" stop # nothing to stop is fine

    # Active while only the daemon lives (the loop was killed, as 0.2.1's stop did).
    : > "$T/ran"
    $SH "$HERE/homechooser" watch
    waitran 1
    pid=$(cat "$T/rt/watcher.pid"); dpid=$(cat "$T/rt/watcher-daemon.pid")
    kill -s KILL "$pid"; waitgone "$pid"
    alive "$dpid" || exit 1
    [ "$($SH "$HERE/homechooser" active)" = active ] || { echo 'not active with only the daemon'; exit 1; }
    $SH "$HERE/homechooser" stop
    alive "$dpid" && { echo 'daemon alive after stop (loop already gone)'; exit 1; }
    [ "$($SH "$HERE/homechooser" active)" = inactive ]

    # A recycled pid is not signalled: pid files naming a live process that is not ours.
    sleep 60 & other=$!
    echo "$other" > "$T/rt/watcher.pid"; echo "$other" > "$T/rt/watcher-daemon.pid"
    [ "$($SH "$HERE/homechooser" active)" = inactive ]
    $SH "$HERE/homechooser" stop
    alive "$other" || { echo 'stop signalled an unrelated pid'; exit 1; }
    [ ! -e "$T/rt/watcher.pid" ] || exit 1
    kill "$other"

    # A daemon that ignores TERM is KILLed after the wait.
    : > "$T/ran"
    HC_TEST_STUBBORN=1 $SH "$HERE/homechooser" watch
    waitran 1
    pid=$(cat "$T/rt/watcher.pid"); dpid=$(cat "$T/rt/watcher-daemon.pid")
    $SH "$HERE/homechooser" stop
    alive "$dpid" && { echo 'stubborn daemon survived stop'; exit 1; }
    alive "$pid" && exit 1

    # An orphan from an older version (no pid files, no loop) is found by its command line and stopped by stop and by watch.
    : > "$T/ran"
    rm -rf "$T/rt"
    "$T/w/homechooser-daemon" --boot < /dev/null > /dev/null 2>&1 & orphan=$!
    waitran 1
    [ "$($SH "$HERE/homechooser" active)" = inactive ]
    $SH "$HERE/homechooser" stop
    waitgone "$orphan" || { echo 'orphan daemon survived stop'; exit 1; }
    "$T/w/homechooser-daemon" --boot < /dev/null > /dev/null 2>&1 & orphan=$!
    sleep 1; : > "$T/ran"
    $SH "$HERE/homechooser" watch
    waitgone "$orphan" || { echo 'orphan daemon survived watch'; exit 1; }
    waitran 1
    [ "$(wc -l < "$T/ran")" = 1 ] # exactly one new daemon, the loop's
    $SH "$HERE/homechooser" stop
    [ "$($SH "$HERE/homechooser" active)" = inactive ]
) || { echo "FAIL watcher without systemd"; exit 1; }
printf 'PASS boot entry: native unit, active-unit guard, 5-12 staging and stopped uninstall fallback, watcher loop and daemon without systemd (%s)\n' "$SH"
