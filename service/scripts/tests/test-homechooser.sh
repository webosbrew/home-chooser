#!/bin/sh
# Tests for the init.d entry `homechooser` with stubbed systemd-run, systemctl and Home key hook worker.
# usage: test-homechooser.sh [shell]     (shell defaults to sh, e.g. "busybox sh")
SH=${1:-sh}
HERE=$(cd "$(dirname "$0")/.." && pwd)
SCRIPT=$HERE/homechooser
PASS=0
FAIL=0

check() { d=$1; shift; if "$@"; then PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); echo "FAIL: $d"; fi; }
has() { grep -qF -- "$1" "$2"; }
hasnt() { ! grep -qF -- "$1" "$2"; }
cleanup() { rm -rf "${T:?}"; }

# setup <major>: a fake app Home key hook directory, with stub worker and watcher, and stub systemd tools
setup() {
    T=$(mktemp -d)
    mkdir -p "$T/service/scripts" "$T/bin" "$T/state" "$T/init.d"
    cp "$SCRIPT" "$T/service/scripts/homechooser"
    mkdir -p "$T/service/scripts/lib"
    cp "$HERE/lib/common.sh" "$T/service/scripts/lib/common.sh"
    printf '#!/bin/sh\necho "homekey $*" >> %s/cmds\n' "$T" > "$T/service/scripts/homechooser-homekey"
    printf '#!/bin/sh\n' > "$T/service/scripts/homechooser-home-watch"
    printf '#!/bin/sh\necho "access $*" >> %s/access\n' "$T" > "$T/service/scripts/homechooser-access"
    printf '#!/bin/sh\necho "systemd-run $*" >> %s/cmds\n' "$T" > "$T/bin/systemd-run"
    cat > "$T/bin/systemctl" <<STUB
#!/bin/sh
echo "systemctl \$*" >> $T/cmds
case "\$1" in is-active) [ -f $T/active ] ;; esac
STUB
    chmod +x "$T/service/scripts/"* "$T/bin/"*
    printf 'VERSION_ID="%s.0.0"\n' "$1" > "$T/os-release"
    : > "$T/cmds"
    export HOMECHOOSER_CHOICE="$T/state/home" HOMECHOOSER_HOMEKEY_FLAG="$T/state/homekey" \
           HOMECHOOSER_OSRELEASE="$T/os-release" HOMECHOOSER_SYSTEMCTL="$T/bin/systemctl" \
           HOMECHOOSER_SYSTEMD_RUN="$T/bin/systemd-run" HOMECHOOSER_LOG="$T/log"
    unset HOMECHOOSER_DIR
}
run() { HOMECHOOSER_DIR="$T/service/scripts" $SH "$T/service/scripts/homechooser" "$@" > "$T/out" 2>&1; echo $? > "$T/rc"; }
rc() { [ "$(cat "$T/rc")" = "$1" ]; }
RUN_ARGS='systemd-run --unit=homechooser-home --property=Restart=on-failure --property=RestartSec=5 /bin/sh -c [ -f "$1" ] && exec /bin/sh "$1"; [ -f "$2" ] && exec /bin/sh "$2"; exit 0 homechooser-home-watch'

echo "== webOS 5 with a choice: watcher only ($SH)"
setup 5
printf 'my.home\n' > "$T/state/home"
touch "$T/state/homekey"
run
check "exit 0" rc 0
check "boot applies access" has "access apply" "$T/access"
check "no Home key hook on 5" hasnt homekey "$T/cmds"
check "systemd-run with the watcher" has "$RUN_ARGS $T/service/scripts/homechooser-home-watch" "$T/cmds"
check "checked the unit first" has 'systemctl is-active --quiet homechooser-home' "$T/cmds"
cleanup

echo "== no choice: nothing is started"
setup 5
run
check "exit 0" rc 0
check "no commands" test ! -s "$T/cmds"
check "access applied without a home choice" has "access apply" "$T/access"
cleanup

echo "== already running: not started twice"
setup 8
printf 'my.home\n' > "$T/state/home"
touch "$T/active"
run
check "no systemd-run" hasnt systemd-run "$T/cmds"
cleanup

echo "== webOS 6 to 9 with the Home key hook enabled"
for major in 6 7 8 9; do
    setup $major
    touch "$T/state/homekey"
    run
    check "$major: Home key hook applied" has "homekey apply" "$T/cmds"
    check "$major: no choice, no watcher" hasnt systemd-run "$T/cmds"
    cleanup
    setup $major
    printf 'my.home\n' > "$T/state/home"
    run
    check "$major: Home key hook not enabled, no Home key hook" hasnt homekey "$T/cmds"
    check "$major: watcher started" has systemd-run "$T/cmds"
    cleanup
done
setup 8
touch "$T/state/homekey"
printf 'my.home\n' > "$T/state/home"
run
check "both: Home key hook before the watcher" test "$(grep -n 'homekey apply\|systemd-run' "$T/cmds" | cut -d: -f1 | tr '\n' ' ')" = "1 4 "
cleanup

echo "== webOS 10 and later: no Home key hook"
for major in 10 11 12; do
    setup $major
    touch "$T/state/homekey"
    printf 'my.home\n' > "$T/state/home"
    run
    check "$major: no Home key hook" hasnt homekey "$T/cmds"
    check "$major: watcher started" has systemd-run "$T/cmds"
    cleanup
done

echo "== watch: start the watcher now"
setup 11
printf 'my.home\n' > "$T/state/home"
touch "$T/state/homekey"
run watch
check "exit 0" rc 0
check "watcher started" has systemd-run "$T/cmds"
cleanup
setup 7
touch "$T/state/homekey"
run watch
check "watch never runs the Home key hook" hasnt homekey "$T/cmds"
check "no choice: the watcher is started for its leftover check" has "$RUN_ARGS $T/service/scripts/homechooser-home-watch" "$T/cmds"
cleanup
setup 7
touch "$T/active"
run watch
check "no choice, already running: not started twice" hasnt systemd-run "$T/cmds"
cleanup

echo "== systemd-run fails"
setup 8
printf 'my.home\n' > "$T/state/home"
printf '#!/bin/sh\nexit 1\n' > "$T/bin/systemd-run"
run
check "exit 0 (boot goes on)" rc 0
check "logged" has 'could not start' "$T/log"
cleanup

echo "== run through the init.d symlink"
setup 8
printf 'my.home\n' > "$T/state/home"
ln -s "$T/service/scripts/homechooser" "$T/init.d/homechooser"
$SH -c "\"$T/init.d/homechooser\"" > "$T/out" 2>&1; echo $? > "$T/rc"
check "exit 0" rc 0
check "data found next to the real script" has "$RUN_ARGS $T/service/scripts/homechooser-home-watch" "$T/cmds"
cleanup

echo "== service script directory gone: quiet exit"
setup 8
printf 'my.home\n' > "$T/state/home"
touch "$T/state/homekey"
mkdir -p "$T/stray"
cp "$SCRIPT" "$T/stray/homechooser"
$SH "$T/stray/homechooser" > "$T/out" 2>&1; echo $? > "$T/rc"
check "exit 0" rc 0
check "no output" test ! -s "$T/out"
check "no commands" test ! -s "$T/cmds"
cleanup

echo "== the unit's wrapper: service copy, else the runtime copy, else nothing"
setup 8
printf 'my.home\n' > "$T/state/home"
printf '#!/bin/sh\necho "service watcher" >> %s/ran\n' "$T" > "$T/service/scripts/homechooser-home-watch"
mkdir -p "$T/rt"
printf '#!/bin/sh\necho "runtime watcher" >> %s/ran\n' "$T" > "$T/rt/homechooser-home-watch"
# a systemd-run that runs what it is given, after the three --unit/--property options
printf '#!/bin/sh\nshift 3\nexec "$@"\n' > "$T/bin/systemd-run"
chmod +x "$T/bin/systemd-run"
HOMECHOOSER_RUNTIME="$T/rt" run
check "service copy runs when the app is there" test "$(cat "$T/ran" 2>/dev/null)" = "service watcher"
rm -f "$T/ran"
# call the wrapper the way the unit does, with the service copy missing
WRAP=$(sed -n "s/.*\/bin\/sh -c '\(.*\)' \\\\$/\1/p" "$SCRIPT")
check "wrapper text found in the script" test -n "$WRAP"
/bin/sh -c "$WRAP" homechooser-home-watch "$T/service/scripts/gone" "$T/rt/homechooser-home-watch"
check "runtime copy runs when the service copy is missing" test "$(cat "$T/ran" 2>/dev/null)" = "runtime watcher"
rm -f "$T/ran"
/bin/sh -c "$WRAP" homechooser-home-watch "$T/service/scripts/gone" "$T/rt/gone"
check "neither: exit 0, nothing runs" test ! -f "$T/ran"
/bin/sh -c "$WRAP" homechooser-home-watch "$T/service/scripts/gone" "$T/rt/gone"
check "neither: exit status 0" test $? = 0
cleanup

echo "== usage"
setup 8
run bogus
check "exit 2" rc 2
cleanup

echo "$PASS passed, $FAIL failed ($SH)"
[ "$FAIL" = 0 ]
