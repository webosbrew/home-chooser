#!/bin/sh
# Tests for the init.d entry `openhouse` with stubbed systemd-run, systemctl and key filter worker.
# usage: test-openhouse.sh [shell]     (shell defaults to sh, e.g. "busybox sh")
SH=${1:-sh}
HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT=$HERE/openhouse
PASS=0
FAIL=0

check() { d=$1; shift; if "$@"; then PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); echo "FAIL: $d"; fi; }
has() { grep -qF -- "$1" "$2"; }
hasnt() { ! grep -qF -- "$1" "$2"; }
cleanup() { rm -rf "${T:?}"; }

# setup <major>: a fake app backport directory, with stub worker and watcher, and stub systemd tools
setup() {
    T=$(mktemp -d)
    mkdir -p "$T/app/backport" "$T/bin" "$T/state" "$T/init.d"
    cp "$SCRIPT" "$T/app/backport/openhouse"
    printf '#!/bin/sh\necho "keyfilter $* data=$OPENHOUSE_DATA" >> %s/cmds\n' "$T" > "$T/app/backport/openhouse-keyfilter"
    printf '#!/bin/sh\n' > "$T/app/backport/openhouse-home-watch"
    printf '#!/bin/sh\necho "systemd-run $*" >> %s/cmds\n' "$T" > "$T/bin/systemd-run"
    cat > "$T/bin/systemctl" <<STUB
#!/bin/sh
echo "systemctl \$*" >> $T/cmds
case "\$1" in is-active) [ -f $T/active ] ;; esac
STUB
    chmod +x "$T/app/backport/"* "$T/bin/"*
    printf 'VERSION_ID="%s.0.0"\n' "$1" > "$T/os-release"
    : > "$T/cmds"
    export OPENHOUSE_CHOICE="$T/state/home" OPENHOUSE_KEYFILTER_FLAG="$T/state/keyfilter" \
           OPENHOUSE_OSRELEASE="$T/os-release" OPENHOUSE_SYSTEMCTL="$T/bin/systemctl" \
           OPENHOUSE_SYSTEMD_RUN="$T/bin/systemd-run" OPENHOUSE_LOG="$T/log"
    unset OPENHOUSE_DIR
}
run() { OPENHOUSE_DIR="$T/app/backport" $SH "$T/app/backport/openhouse" "$@" > "$T/out" 2>&1; echo $? > "$T/rc"; }
rc() { [ "$(cat "$T/rc")" = "$1" ]; }
RUN_ARGS='systemd-run --unit=openhouse-home --property=Restart=on-failure --property=RestartSec=5 /bin/sh -c [ -f "$1" ] && exec /bin/sh "$1"; [ -f "$2" ] && exec /bin/sh "$2"; exit 0 openhouse-home-watch'

echo "== webOS 5 with a choice: watcher only ($SH)"
setup 5
printf 'my.home\n' > "$T/state/home"
touch "$T/state/keyfilter"
run
check "exit 0" rc 0
check "no key filter on 5" hasnt keyfilter "$T/cmds"
check "systemd-run with the watcher" has "$RUN_ARGS $T/app/backport/openhouse-home-watch" "$T/cmds"
check "checked the unit first" has 'systemctl is-active --quiet openhouse-home' "$T/cmds"
cleanup

echo "== no choice: nothing is started"
setup 5
run
check "exit 0" rc 0
check "no commands" test ! -s "$T/cmds"
cleanup

echo "== already running: not started twice"
setup 8
printf 'my.home\n' > "$T/state/home"
touch "$T/active"
run
check "no systemd-run" hasnt systemd-run "$T/cmds"
cleanup

echo "== webOS 6 to 9 with the backport enabled"
for major in 6 7 8 9; do
    setup $major
    touch "$T/state/keyfilter"
    run
    check "$major: key filter applied" has "keyfilter apply data=$T/app/backport" "$T/cmds"
    check "$major: no choice, no watcher" hasnt systemd-run "$T/cmds"
    cleanup
    setup $major
    printf 'my.home\n' > "$T/state/home"
    run
    check "$major: backport not enabled, no key filter" hasnt keyfilter "$T/cmds"
    check "$major: watcher started" has systemd-run "$T/cmds"
    cleanup
done
setup 8
touch "$T/state/keyfilter"
printf 'my.home\n' > "$T/state/home"
run
check "both: key filter before the watcher" test "$(grep -n 'keyfilter apply\|systemd-run' "$T/cmds" | cut -d: -f1 | tr '\n' ' ')" = "1 4 "
cleanup

echo "== webOS 10 and later: no key filter"
for major in 10 11 12; do
    setup $major
    touch "$T/state/keyfilter"
    printf 'my.home\n' > "$T/state/home"
    run
    check "$major: no key filter" hasnt keyfilter "$T/cmds"
    check "$major: watcher started" has systemd-run "$T/cmds"
    cleanup
done

echo "== watch: start the watcher now"
setup 11
printf 'my.home\n' > "$T/state/home"
touch "$T/state/keyfilter"
run watch
check "exit 0" rc 0
check "watcher started" has systemd-run "$T/cmds"
cleanup
setup 7
touch "$T/state/keyfilter"
run watch
check "watch never runs the key filter" hasnt keyfilter "$T/cmds"
check "no choice: nothing started" hasnt systemd-run "$T/cmds"
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
ln -s "$T/app/backport/openhouse" "$T/init.d/openhouse"
$SH -c "\"$T/init.d/openhouse\"" > "$T/out" 2>&1; echo $? > "$T/rc"
check "exit 0" rc 0
check "data found next to the real script" has "$RUN_ARGS $T/app/backport/openhouse-home-watch" "$T/cmds"
cleanup

echo "== app directory gone: quiet exit"
setup 8
printf 'my.home\n' > "$T/state/home"
touch "$T/state/keyfilter"
mkdir -p "$T/stray"
cp "$SCRIPT" "$T/stray/openhouse"
$SH "$T/stray/openhouse" > "$T/out" 2>&1; echo $? > "$T/rc"
check "exit 0" rc 0
check "no output" test ! -s "$T/out"
check "no commands" test ! -s "$T/cmds"
cleanup

echo "== the unit's wrapper: app copy, else the runtime copy, else nothing"
setup 8
printf 'my.home\n' > "$T/state/home"
printf '#!/bin/sh\necho "app watcher" >> %s/ran\n' "$T" > "$T/app/backport/openhouse-home-watch"
mkdir -p "$T/rt"
printf '#!/bin/sh\necho "runtime watcher" >> %s/ran\n' "$T" > "$T/rt/openhouse-home-watch"
# a systemd-run that runs what it is given, after the three --unit/--property options
printf '#!/bin/sh\nshift 3\nexec "$@"\n' > "$T/bin/systemd-run"
chmod +x "$T/bin/systemd-run"
OPENHOUSE_RUNTIME="$T/rt" run
check "app copy runs when the app is there" test "$(cat "$T/ran" 2>/dev/null)" = "app watcher"
rm -f "$T/ran"
# call the wrapper the way the unit does, with the app copy missing
WRAP=$(sed -n "s/.*\/bin\/sh -c '\(.*\)' \\\\$/\1/p" "$SCRIPT")
check "wrapper text found in the script" test -n "$WRAP"
/bin/sh -c "$WRAP" openhouse-home-watch "$T/app/backport/gone" "$T/rt/openhouse-home-watch"
check "runtime copy runs when the app copy is missing" test "$(cat "$T/ran" 2>/dev/null)" = "runtime watcher"
rm -f "$T/ran"
/bin/sh -c "$WRAP" openhouse-home-watch "$T/app/backport/gone" "$T/rt/gone"
check "neither: exit 0, nothing runs" test ! -f "$T/ran"
/bin/sh -c "$WRAP" openhouse-home-watch "$T/app/backport/gone" "$T/rt/gone"
check "neither: exit status 0" test $? = 0
cleanup

echo "== usage"
setup 8
run bogus
check "exit 2" rc 2
cleanup

echo "$PASS passed, $FAIL failed ($SH)"
[ "$FAIL" = 0 ]
