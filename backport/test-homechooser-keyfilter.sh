#!/bin/sh
# Tests for homechooser-keyfilter against a fake root with stubbed mount, umount and systemctl.
# usage: test-homechooser-keyfilter.sh [shell]     (shell defaults to sh, e.g. "busybox sh")
SH=${1:-sh}
HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT=${KF_SCRIPT:-$HERE/homechooser-keyfilter}
PASS=0
FAIL=0

check() { # check <description> <command...>
    d=$1; shift
    if "$@"; then PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); echo "FAIL: $d"; fi
}
has() { grep -qF -- "$1" "$2"; }
hasnt() { ! grep -qF -- "$1" "$2"; }

mktimeout() { # mktimeout <new|old> <path>: a timeout that accepts only one form, and records its use
    if [ "$1" = new ]; then
        printf '#!/bin/sh\necho "timeout $*" >> %s.log\n[ "$1" = -t ] && exit 125\nshift\nexec "$@"\n' "$2" > "$2"
    else
        printf '#!/bin/sh\necho "timeout $*" >> %s.log\n[ "$1" = -t ] || exit 125\nshift 2\nexec "$@"\n' "$2" > "$2"
    fi
    chmod +x "$2"
}

setup() {
    T=$(mktemp -d)
    mkdir -p "$T/root/usr/lib/qml" "$T/data/1.0" "$T/data/2.0" "$T/bin" "$T/orig"
    printf 'one\ntwo\nthree\nhome = "com.webos.app.home"\nfive\n' > "$T/root/usr/lib/qml/a.qml"
    printf 'alpha\nbeta\nHOME_APP_ID\ngamma\n' > "$T/root/usr/lib/qml/b.js"
    cp "$T/root/usr/lib/qml/a.qml" "$T/root/usr/lib/qml/b.js" "$T/orig/"
    printf 'one\ntwo\nthree\nhome = Settings.getHomeAppId()\nproperty string configuredHomeAppId: "@HOMECHOOSER_HOME@"\nfive\n' > "$T/new-a"
    printf 'alpha\nbeta\nglobal.isHomeApp(x)\ngamma\n' > "$T/new-b"
    for f in a:a.qml b:b.js; do
        k=${f%%:*}; n=${f##*:}
        diff -u --label "usr/lib/qml/$n" --label "usr/lib/qml/$n" "$T/orig/$n" "$T/new-$k" > "$T/data/1.0/$n.diff"
    done
    sa=$(sha256sum "$T/orig/a.qml" | cut -d' ' -f1)
    sb=$(sha256sum "$T/orig/b.js" | cut -d' ' -f1)
    cat > "$T/data/1.0/manifest.json" <<JSON
{
  "release": "1.0",
  "files": [
    {
      "path": "/usr/lib/qml/a.qml",
      "sha256": "$sa",
      "diff": "a.qml.diff"
    },
    {
      "path": "/usr/lib/qml/b.js",
      "sha256": "$sb",
      "diff": "b.js.diff"
    }
  ]
}
JSON
    # a second set for another firmware, whose hashes match nothing
    printf '{"release":"2.0","files":[{"path":"/usr/lib/qml/a.qml","sha256":"%064d","diff":"a.qml.diff"}]}\n' 0 > "$T/data/2.0/manifest.json"
    : > "$T/mounts"
    : > "$T/cmds"
    cat > "$T/bin/mount" <<STUB
#!/bin/sh
echo "mount \$*" >> "$T/cmds"
[ -f "$T/mount-fail-on" ] && case "\$3" in *\$(cat "$T/mount-fail-on")) exit 1;; esac
echo "\$2 \$3 none bind 0 0" >> "$T/mounts"
STUB
    cat > "$T/bin/umount" <<STUB
#!/bin/sh
echo "umount \$*" >> "$T/cmds"
[ -f "$T/umount-fail" ] && exit 1
[ "\$1" = "-l" ] && shift
grep -vF " \$1 " "$T/mounts" > "$T/mounts.new"; mv "$T/mounts.new" "$T/mounts"
STUB
    # is-active answers active unless $T/inactive-<unit> exists; $T/fail-<verb>-<unit> makes that call fail;
    # $T/slow-restart makes a restart take a second.
    cat > "$T/bin/systemctl" <<STUB
#!/bin/sh
echo "systemctl \$*" >> "$T/cmds"
if [ "\$1" = is-active ]; then
    if [ -f "$T/inactive-\$2" ]; then echo inactive; exit 3; fi
    echo active; exit 0
fi
[ "\$1" = restart ] && [ -f "$T/slow-restart" ] && sleep 1
[ -f "$T/fail-\$1-\$2" ] && exit 1
exit 0
STUB
    echo my.home > "$T/installed"
    cat > "$T/bin/luna-send" <<'STUB'
#!/bin/sh
T=@T@
if [ "$1" = -a ]; then echo "$2" >> $T/lunaids; shift 2; fi
echo "$3 $4" >> $T/lunacalls
[ -f $T/sam-silent ] && exit 0
id=$(echo "$4" | sed 's/.*"id":"\([^"]*\)".*/\1/')
if grep -qxF "$id" $T/installed; then
    echo '{"returnValue":true,"appInfo":{}}'
else
    echo '{"returnValue":false,"errorText":"Invalid appId specified OR Unsupported Application Type"}'
fi
STUB
    sed -i "s|@T@|$T|" "$T/bin/luna-send"
    cat > "$T/bin/sleep" <<STUB
#!/bin/sh
echo "sleep \$*" >> "$T/cmds"
# sam comes up after the second sleep, if asked to
[ -f "$T/sam-late" ] && [ "\$(grep -c '^sleep' "$T/cmds")" -ge 2 ] && rm -f "$T/sam-silent"
STUB
    cat > "$T/bin/node" <<'STUB'
#!/bin/sh
T=@T@
echo "$*" >> "$T/node-calls"
shift
mode=$1; shift
while [ "$#" -gt 0 ]; do
    case "$1" in
        --root) root=$2;;
        --out) out=$2;;
        --manifest) manifest=$2;;
    esac
    shift 2
done
[ -f "$T/generator-success" ] || { echo 'ABORT: fixture refused' >&2; exit 2; }
if [ "$mode" = generate ]; then
    mkdir -p "$out/usr/lib/qml"
    cp "$root/usr/lib/qml/"* "$out/usr/lib/qml/"
    (cd "$out" && patch -p0 < "$T/data/1.0/a.qml.diff" && patch -p0 < "$T/data/1.0/b.js.diff") || exit 1
    cp "$T/data/1.0/manifest.json" "$manifest"
fi
echo 'ok files=2'
STUB
    sed -i "s|@T@|$T|" "$T/bin/node"
    : > "$T/generator.js"
    export HOMECHOOSER_NODE="$T/bin/node" HOMECHOOSER_GEN="$T/generator.js" \
           HOMECHOOSER_GENERATED_FLAG="$T/generated-flag" HOMECHOOSER_KEYFILTER_FLAG="$T/keyfilter-flag" HOMECHOOSER_NO_SETS_FLAG="$T/debug-no-sets"
    unset HOMECHOOSER_NO_SETS
    chmod +x "$T/bin/"*
    export HOMECHOOSER_LUNA_SEND="$T/bin/luna-send" HOMECHOOSER_SAM_WAIT=0 HOMECHOOSER_SLEEP="$T/bin/sleep"
    export HOMECHOOSER_DATA="$T/data" HOMECHOOSER_ROOT="$T/root" HOMECHOOSER_WORK="$T/work" \
           HOMECHOOSER_LOG="$T/log" HOMECHOOSER_MOUNTS="$T/mounts" HOMECHOOSER_MOUNT="$T/bin/mount" \
           HOMECHOOSER_UMOUNT="$T/bin/umount" HOMECHOOSER_SYSTEMCTL="$T/bin/systemctl" HOMECHOOSER_CHOICE="$T/home"
}

run() { $SH "$SCRIPT" "$@" > "$T/out" 2>&1; echo $? > "$T/rc"; }
rc() { [ "$(cat "$T/rc")" = "$1" ]; }
ncmds() { grep -c "^$1" "$T/cmds"; }
# seq: the systemctl calls that change something, in order (is-active left out)
seq() { grep '^systemctl ' "$T/cmds" | grep -v '^systemctl is-active '; }
# the compositor restart with every dependant running: stop the clients, restart, start them again
RESTART="systemctl stop webapp-mgr.service
systemctl stop booster.service
systemctl stop maliit-server.service
systemctl restart surface-manager-daemon.service
systemctl start webapp-mgr.service
systemctl start booster.service
systemctl start maliit-server.service"
restarted() { test "$(seq)" = "$RESTART"; }

echo "== matching set applied ($SH)"
setup
run
check "exit 0" rc 0
check "a.qml patched in the stage" has 'Settings.getHomeAppId()' "$T/work/stage/usr/lib/qml/a.qml"
check "b.js patched in the stage" has 'global.isHomeApp(x)' "$T/work/stage/usr/lib/qml/b.js"
check "stock a.qml untouched" cmp -s "$T/orig/a.qml" "$T/root/usr/lib/qml/a.qml"
check "two bind mounts" test "$(ncmds mount)" = 2
check "bind mount of the stage over the original" has "mount --bind $T/work/stage/usr/lib/qml/a.qml $T/root/usr/lib/qml/a.qml" "$T/cmds"
check "compositor restarted once, clients stopped before and started after" restarted
check "each client asked about first" test "$(ncmds 'systemctl is-active')" = 3
check "restart output reaches the caller" has 'surface-manager-daemon.service restarted' "$T/out"
check "no restart output file left" test -z "$(ls "$T/work" | grep '^restart\.')"
check "log names the set" has 'diff set 1.0 matches' "$T/log"
check "no choice: placeholder replaced by an empty id" has 'configuredHomeAppId: ""' "$T/work/stage/usr/lib/qml/a.qml"
check "placeholder gone" hasnt '@HOMECHOOSER_HOME@' "$T/work/stage/usr/lib/qml/a.qml"
check "stock a.qml has no placeholder" hasnt configuredHomeAppId "$T/root/usr/lib/qml/a.qml"
run status
check "status mounted" has 'mounted=yes' "$T/out"
check "status release" has 'release=1.0' "$T/out"

echo "== refresh follows the stored choice"
: > "$T/cmds"
run refresh
check "exit 0" rc 0
check "nothing changed, no restart" test ! -s "$T/cmds"
printf '  my.home\r\n' > "$T/home"
inode=$(ls -i "$T/work/stage/usr/lib/qml/a.qml" | cut -d' ' -f1)
run refresh
check "exit 0" rc 0
check "mounted copy updated" has 'configuredHomeAppId: "my.home"' "$T/work/stage/usr/lib/qml/a.qml"
check "same inode" test "$(ls -i "$T/work/stage/usr/lib/qml/a.qml" | cut -d' ' -f1)" = "$inode"
check "no restart, no systemctl call at all" test ! -s "$T/cmds"
check "log says the running compositor keeps its id" has 'configured home updated; the running compositor keeps its loaded id until the next start (the settings record is read first)' "$T/log"
rm -f "${T:?}/home"
: > "$T/cmds"
run refresh
check "choice removed: id cleared" has 'configuredHomeAppId: ""' "$T/work/stage/usr/lib/qml/a.qml"
check "still no restart" test ! -s "$T/cmds"
printf 'my.home\n' > "$T/home"
: > "$T/cmds"
run refresh restart
check "refresh restart: exit 0" rc 0
check "refresh restart: copy updated" has 'configuredHomeAppId: "my.home"' "$T/work/stage/usr/lib/qml/a.qml"
check "refresh restart: compositor restarted" restarted
: > "$T/cmds"
run refresh restart
check "refresh restart with nothing changed: no restart" test ! -s "$T/cmds"
rm -f "${T:?}/home"
run refresh

echo "== already mounted is a no-op"
: > "$T/cmds"
run
check "exit 0" rc 0
check "no commands" test ! -s "$T/cmds"
check "log says so" has 'already mounted' "$T/log"

echo "== stop"
run stop
check "exit 0" rc 0
check "two umounts" test "$(ncmds umount)" = 2
check "mounts empty" test ! -s "$T/mounts"
check "compositor restarted, clients around it" test "$(seq)" = "$RESTART"
check "stage removed" test ! -d "$T/work/stage"
: > "$T/cmds"
run stop
check "second stop does nothing" test ! -s "$T/cmds"
run status
check "status stock" has 'mounted=no' "$T/out"
check "status match" has 'match=1.0' "$T/out"
rm -rf "${T:?}"

echo "== one hash mismatch mounts nothing"
setup
echo changed >> "$T/root/usr/lib/qml/b.js"
run
check "exit 0" rc 0
check "no mount" test "$(ncmds mount)" = 0
check "no restart" test "$(ncmds systemctl)" = 0
check "log says no set" has 'no diff set matches' "$T/log"
run status
check "status none" has 'match=none' "$T/out"
rm -rf "${T:?}"

echo "== missing target mounts nothing"
setup
rm "$T/root/usr/lib/qml/a.qml"
run
check "exit 0" rc 0
check "no mount" test "$(ncmds mount)" = 0
rm -rf "${T:?}"

echo "== patch failure mounts nothing"
setup
# the hashes match but the second diff does not apply
sed 's/^-HOME_APP_ID$/-OTHER_LINE/' "$T/data/1.0/b.js.diff" > "$T/x" && mv "$T/x" "$T/data/1.0/b.js.diff"
run
check "exit 1" rc 1
check "no mount" test "$(ncmds mount)" = 0
check "no restart" test "$(ncmds systemctl)" = 0
check "log names the diff" has 'b.js.diff does not apply' "$T/log"
check "stage removed" test ! -d "$T/work/stage"
rm -rf "${T:?}"

echo "== mount failure undoes the first mount"
setup
echo b.js > "$T/mount-fail-on"
run
check "exit 1" rc 1
check "first mount undone" test "$(ncmds umount)" = 1
check "mounts empty" test ! -s "$T/mounts"
check "no restart" test "$(ncmds systemctl)" = 0
rm -rf "${T:?}"

echo "== stored choice forms are trimmed and validated"
for form in 'my.home' 'my.home\n' 'my.home\r\n' '  my.home  \n' '\n\nmy.home\t\n'; do
    setup
    printf "$form" > "$T/home"
    run
    check "id from '$form'" has 'configuredHomeAppId: "my.home"' "$T/work/stage/usr/lib/qml/a.qml"
    rm -rf "${T:?}"
done
for bad in '' '  \r\n' 'a b' 'x"y' 'x|y' 'a&b' 'a\\b' 'a/b' 'a\nb'; do
    setup
    printf "$bad" > "$T/home"
    run
    check "invalid or empty '$bad': empty id" has 'configuredHomeAppId: ""' "$T/work/stage/usr/lib/qml/a.qml"
    check "invalid or empty '$bad': still mounts" has 'mounted /usr/lib/qml/a.qml' "$T/log"
    rm -rf "${T:?}"
done
setup
printf 'x"y\n' > "$T/home"
run
check "invalid id logged" has 'invalid app id' "$T/log"
rm -rf "${T:?}"

echo "== the stored choice is baked in only if the app is installed"
setup
printf 'my.home\n' > "$T/home"
run
check "installed: id baked in" has 'configuredHomeAppId: "my.home"' "$T/work/stage/usr/lib/qml/a.qml"
check "asked sam about the id" has 'getAppInfo {"id":"my.home"}' "$T/lunacalls"
check "asks sam as com.webos.applicationManager (luna-send -a)" has com.webos.applicationManager "$T/lunaids"
rm -rf "${T:?}"
setup
printf 'my.home\n' > "$T/home"
: > "$T/installed"
run
check "not installed: stock id baked in" has 'configuredHomeAppId: ""' "$T/work/stage/usr/lib/qml/a.qml"
check "still mounts" has 'mounted /usr/lib/qml/a.qml' "$T/log"
check "logged" has 'my.home is not installed' "$T/log"
rm -rf "${T:?}"
setup
printf 'my.home\n' > "$T/home"
touch "$T/sam-silent"
run
check "sam silent: stock id baked in" has 'configuredHomeAppId: ""' "$T/work/stage/usr/lib/qml/a.qml"
check "sam silent: still mounts and restarts" has 'systemctl restart surface-manager-daemon.service' "$T/cmds"
check "sam silent: logged" has 'sam did not answer' "$T/log"
rm -rf "${T:?}"
setup
printf 'my.home\n' > "$T/home"
touch "$T/sam-silent" "$T/sam-late"
HOMECHOOSER_SAM_WAIT=20 run
check "sam answers after a short wait: id baked in" has 'configuredHomeAppId: "my.home"' "$T/work/stage/usr/lib/qml/a.qml"
check "it slept, 2 s at a time" has 'sleep 2' "$T/cmds"
rm -rf "${T:?}"
setup
printf 'my.home\n' > "$T/home"
run
check "sam is asked when a choice is stored" test -s "$T/lunacalls"
rm -rf "${T:?}"
setup
run
check "no choice: sam is not asked" test ! -s "$T/lunacalls"
rm -rf "${T:?}"

echo "== refresh stock, and refresh when the app is gone"
setup
printf 'my.home\n' > "$T/home"
run
: > "$T/cmds"
run refresh stock
check "stock refresh clears the id although the choice file is there" has 'configuredHomeAppId: ""' "$T/work/stage/usr/lib/qml/a.qml"
check "no restart" test ! -s "$T/cmds"
: > "$T/cmds"
run refresh stock
check "second stock refresh: nothing to do" test ! -s "$T/cmds"
run refresh
check "plain refresh brings the choice back" has 'configuredHomeAppId: "my.home"' "$T/work/stage/usr/lib/qml/a.qml"
: > "$T/installed"
: > "$T/cmds"
run refresh
check "plain refresh with the app gone bakes stock" has 'configuredHomeAppId: ""' "$T/work/stage/usr/lib/qml/a.qml"
check "no restart for it" test ! -s "$T/cmds"
: > "$T/cmds"
run refresh stock
check "stock refresh when already stock: no restart" test ! -s "$T/cmds"
printf 'my.home\n' > "$T/home"
echo my.home > "$T/installed"
run refresh
: > "$T/cmds"
run refresh stock restart
check "stock restart: id cleared" has 'configuredHomeAppId: ""' "$T/work/stage/usr/lib/qml/a.qml"
check "stock restart: compositor restarted" restarted
rm -rf "${T:?}"

echo "== refresh norestart updates the copy and leaves the compositor"
setup
printf 'my.home\n' > "$T/home"
run
: > "$T/cmds"
printf 'other.home\n' > "$T/home"
echo 'other.home' >> "$T/installed"
inode=$(ls -i "$T/work/stage/usr/lib/qml/a.qml" | cut -d' ' -f1)
run refresh norestart
check "exit 0" rc 0
check "copy carries the new id" has 'configuredHomeAppId: "other.home"' "$T/work/stage/usr/lib/qml/a.qml"
check "same inode" test "$(ls -i "$T/work/stage/usr/lib/qml/a.qml" | cut -d' ' -f1)" = "$inode"
check "no restart" test ! -s "$T/cmds"
check "log says the running compositor keeps its id" has 'the running compositor keeps its loaded id' "$T/log"
: > "$T/cmds"
run refresh norestart
check "unchanged: nothing to do" test ! -s "$T/cmds"
run refresh stock norestart
check "stock with norestart clears the id" has 'configuredHomeAppId: ""' "$T/work/stage/usr/lib/qml/a.qml"
check "stock with norestart: no restart" test ! -s "$T/cmds"
rm -rf "${T:?}"

echo "== timeout in both forms (BusyBox before 1.30 wants -t)"
for form in new old; do
    setup
    printf 'my.home\n' > "$T/home"
    mktimeout $form "$T/bin/timeout"
    HOMECHOOSER_TIMEOUT="$T/bin/timeout" run
    check "$form: id baked in through timeout" has 'configuredHomeAppId: "my.home"' "$T/work/stage/usr/lib/qml/a.qml"
    if [ $form = new ]; then check "new: timeout 10" has 'timeout 10 ' "$T/bin/timeout.log"; else check "old: timeout -t 10" has 'timeout -t 10 ' "$T/bin/timeout.log"; fi
    rm -rf "${T:?}"
done
setup
printf 'my.home\n' > "$T/home"
HOMECHOOSER_TIMEOUT= run
check "no timeout: still works" has 'configuredHomeAppId: "my.home"' "$T/work/stage/usr/lib/qml/a.qml"
rm -rf "${T:?}"

echo "== no data directory"
setup
rm -rf "$T/data"
run
check "exit 0" rc 0
check "no mount" test "$(ncmds mount)" = 0
rm -rf "${T:?}"

echo "== apply samfirst restarts sam before the compositor"
setup
run apply samfirst
check "exit 0" rc 0
check "sam restarted" has 'systemctl restart sam' "$T/cmds"
check "compositor restarted" has 'systemctl restart surface-manager-daemon.service' "$T/cmds"
check "sam first, compositor after" test "$(grep -n '^systemctl restart sam$' "$T/cmds" | cut -d: -f1)" -lt "$(grep -n '^systemctl restart surface-manager-daemon.service$' "$T/cmds" | cut -d: -f1)"
check "sam, then the compositor restart with its clients" test "$(seq)" = "systemctl restart sam
$RESTART"
check "mounted" test "$(ncmds mount)" = 2
rm -rf "${T:?}"

echo "== apply samfirst restarts sam even when nothing is left to mount"
setup
run apply samfirst
: > "$T/cmds"
run apply samfirst
check "exit 0" rc 0
check "sam restarted, nothing else" test "$(cat "$T/cmds")" = "systemctl restart sam"
rm -rf "${T:?}"

echo "== plain apply never restarts sam"
setup
run apply
check "no sam restart" hasnt 'systemctl restart sam' "$T/cmds"
rm -rf "${T:?}"

echo "== the compositor's clients come from the firmware's lsm-ready.service"
setup
mkdir -p "$T/root/lib/systemd/system" "$T/root/etc/systemd/system"
printf '[Service]\nType=oneshot\nExecStart=/bin/systemctl stop x.service\nExecStart=/bin/systemctl start x.service\nExecStart=/bin/systemctl stop y@1.service\nExecStart=/bin/systemctl start y@1.service\nRemainAfterExit=yes\n' > "$T/root/lib/systemd/system/lsm-ready.service"
run
check "lib unit: its start lines, in order" test "$(seq)" = "systemctl stop x.service
systemctl stop y@1.service
systemctl restart surface-manager-daemon.service
systemctl start x.service
systemctl start y@1.service"
run stop
printf 'ExecStart=/bin/systemctl stop z.service\nExecStart=/bin/systemctl start z.service\n' > "$T/root/etc/systemd/system/lsm-ready.service"
: > "$T/cmds"
run
check "etc unit wins over lib, as in systemd" test "$(seq)" = "systemctl stop z.service
systemctl restart surface-manager-daemon.service
systemctl start z.service"
run stop
printf '[Service]\nExecStart=/bin/true\n' > "$T/root/etc/systemd/system/lsm-ready.service"
rm "$T/root/lib/systemd/system/lsm-ready.service"
: > "$T/cmds"
run
check "a unit without start lines: the default clients" restarted
run stop
: > "$T/cmds"
HOMECHOOSER_DEPENDANTS= run
check "empty override: the compositor alone" test "$(seq)" = "systemctl restart surface-manager-daemon.service"
rm -rf "${T:?}"

echo "== only running clients are stopped and started again"
setup
touch "$T/inactive-booster.service"
run
check "exit 0" rc 0
check "booster left alone" test "$(seq)" = "systemctl stop webapp-mgr.service
systemctl stop maliit-server.service
systemctl restart surface-manager-daemon.service
systemctl start webapp-mgr.service
systemctl start maliit-server.service"
rm -rf "${T:?}"

echo "== a failed compositor restart still starts the clients"
setup
touch "$T/fail-restart-surface-manager-daemon.service"
run
check "exit 1" rc 1
check "clients started anyway" restarted
check "logged" has 'ERROR: could not restart surface-manager-daemon.service' "$T/log"
rm -rf "${T:?}"
setup
touch "$T/fail-stop-webapp-mgr.service" "$T/fail-start-booster.service"
run
check "failed start: exit 1" rc 1
check "a failed stop or start goes on with the rest" restarted
check "stop failure logged" has 'ERROR: could not stop webapp-mgr.service' "$T/log"
check "start failure logged" has 'ERROR: could not start booster.service' "$T/log"
rm -rf "${T:?}"

echo "== a worker killed during the restart does not leave the clients stopped"
setup
run
: > "$T/cmds"
touch "$T/slow-restart"
echo my.home > "$T/home"
# as Home Chooser's service runs it: output into a pipe, the worker SIGKILLed at a timeout and the
# pipe closed, so anything still writing to it gets SIGPIPE
mkfifo "$T/pipe"
cat "$T/pipe" > /dev/null &
rpid=$!
$SH "$SCRIPT" refresh restart > "$T/pipe" 2>&1 &
wpid=$!
n=0
while ! grep -q '^systemctl restart' "$T/cmds" && [ "$n" -lt 50 ]; do sleep 0.1; n=$((n + 1)); done
kill -9 "$wpid" "$rpid" 2>/dev/null
wait "$wpid" "$rpid" 2>/dev/null
check "killed while restarting" hasnt 'systemctl start maliit-server.service' "$T/cmds"
n=0
while ! grep -q '^systemctl start maliit-server.service' "$T/cmds" && [ "$n" -lt 50 ]; do sleep 0.1; n=$((n + 1)); done
check "the restart still ran to the end" restarted
check "and logged it" has 'starting maliit-server.service' "$T/log"
rm -rf "${T:?}"

echo "== status: reboot=needed or reboot=no"
reboot_of() { run status; grep '^reboot=' "$T/out"; }
setup
check "no flag, unmounted: no" test "$(reboot_of)" = reboot=no
check "status prints the line once" test "$(grep -c '^reboot=' "$T/out")" = 1
touch "$T/keyfilter-flag"
check "flag, unmounted, set matches: needed" test "$(reboot_of)" = reboot=needed
check "existing lines unchanged" has 'match=1.0' "$T/out"
run apply
check "flag, mounted: no" test "$(reboot_of)" = reboot=no
rm "$T/keyfilter-flag"
check "no flag, mounted: needed" test "$(reboot_of)" = reboot=needed
check "mounted lines unchanged" has 'release=1.0' "$T/out"
run stop
check "no flag, stopped: no" test "$(reboot_of)" = reboot=no
rm -rf "${T:?}"
setup
touch "$T/keyfilter-flag"
echo changed >> "$T/root/usr/lib/qml/b.js"
check "flag, unmounted, no match, no generated: no" test "$(reboot_of)" = reboot=no
touch "$T/generator-success"
echo possible >> "$T/generator.js"
check "flag, no match, generated possible, no opt-in: no" test "$(reboot_of)" = reboot=no
touch "$T/generated-flag"
check "flag, no match, generated possible, opted in: needed" test "$(reboot_of)" = reboot=needed
check "generated lines unchanged" has 'generated=possible' "$T/out"
rm "$T/generator-success"
echo changed >> "$T/generator.js"
check "flag, no match, generated refused, opted in: no" test "$(reboot_of)" = reboot=no
rm -rf "${T:?}"
setup
touch "$T/keyfilter-flag" "$T/generated-flag" "$T/generator-success" "$T/debug-no-sets"
run apply
check "generated: flag, mounted: no" test "$(reboot_of)" = reboot=no
rm "$T/keyfilter-flag"
check "generated: no flag, mounted: needed" test "$(reboot_of)" = reboot=needed
rm -rf "${T:?}"

echo "== the mount sources are under the work directory, not the app directory"
setup
run
check "reviewed: every bind source is under WORK" test "$(grep -c "^mount --bind $T/work/stage/" "$T/cmds")" = 2
check "reviewed: no mount source under DATA" test "$(grep '^mount ' "$T/cmds" | grep -c "$T/data")" = 0
check "reviewed: every /proc/mounts source is under WORK" test "$(grep -c "^$T/work/stage/" "$T/mounts")" = 2
rm -rf "$T/data"
check "the app directory can go: mounted copies stay" test -f "$T/work/stage/usr/lib/qml/a.qml"
rm -rf "${T:?}"
setup
touch "$T/generated-flag" "$T/generator-success"
HOMECHOOSER_NO_SETS=1 run apply
check "generated: every bind source is under WORK" test "$(grep -c "^mount --bind $T/work/stage/" "$T/cmds")" = 2
check "generated: no mount source under DATA" test "$(grep '^mount ' "$T/cmds" | grep -c "$T/data")" = 0
rm -rf "${T:?}"

echo "== generated fallback requires opt-in and respects refusals"
setup
touch "$T/debug-no-sets" "$T/generator-success"
run apply
check "no opt-in: no mounts" test "$(ncmds mount)" = 0
check "no opt-in: generator not run by apply" test ! -e "$T/node-calls"
run status
check "status offers generation" has 'generated=possible' "$T/out"
check "status opt-in off" has 'optin=no' "$T/out"
run status
check "same stock hashes: only one node invocation" test "$(wc -l < "$T/node-calls")" = 1
echo changed >> "$T/generator.js"
run status
check "changed rules hash: check recomputed" test "$(wc -l < "$T/node-calls")" = 2
echo '// new file' > "$T/root/usr/lib/qml/new.qml"
# Discovery is scoped to the generator's trees, test those too.
mkdir -p "$T/root/usr/lib/qml/KeyFilters"
echo '// new file' > "$T/root/usr/lib/qml/KeyFilters/new.js"
run status
check "new discovery input: check recomputed" test "$(wc -l < "$T/node-calls")" = 3
echo changed >> "$T/root/usr/lib/qml/KeyFilters/new.js"
run status
check "changed stock hash: check recomputed" test "$(wc -l < "$T/node-calls")" = 4
mv "$T/root/usr/lib/qml/KeyFilters/new.js" "$T/root/usr/lib/qml/KeyFilters/renamed.js"
run status
check "renamed discovery input: check recomputed" test "$(wc -l < "$T/node-calls")" = 5
rm -rf "${T:?}"

setup
touch "$T/debug-no-sets" "$T/generated-flag"
run apply
check "refusal leaves stock (exit 0)" rc 0
check "refusal: no mounts" test "$(ncmds mount)" = 0
check "refusal: no restart" test "$(ncmds systemctl)" = 0
check "refusal: no stage or manifest" test ! -e "$T/work/stage" -a ! -e "$T/work/generated/manifest.json"
run status
check "status says refused" has 'generated=refused' "$T/out"
check "status gives reason" has 'reason=ABORT: fixture refused' "$T/out"
check "status opt-in on" has 'optin=yes' "$T/out"
run status
check "refusal cached too" test "$(wc -l < "$T/node-calls")" = 2
rm -rf "${T:?}"

echo "== generated lifecycle, including refresh and stop without reviewed data"
setup
touch "$T/generated-flag" "$T/generator-success"
HOMECHOOSER_NO_SETS=1 run apply
check "generated apply succeeds" rc 0
check "generated mounts both files" test "$(ncmds mount)" = 2
run status
check "generated mounted" has 'mounted=yes' "$T/out"
check "generated release" has 'release=generated' "$T/out"
check "generated match" has 'match=generated' "$T/out"
check "generated hashes" has "hash=$(sha256sum "$T/work/stage/usr/lib/qml/b.js" | cut -d' ' -f1) /usr/lib/qml/b.js" "$T/out"
# Stop and refresh must use the generated manifest even when no reviewed sets exist.
rm -rf "$T/data"
echo my.home > "$T/home"
run refresh
check "generated refresh bakes choice" has 'configuredHomeAppId: "my.home"' "$T/work/stage/usr/lib/qml/a.qml"
run refresh stock
check "generated refresh stock clears choice" has 'configuredHomeAppId: ""' "$T/work/stage/usr/lib/qml/a.qml"
run apply
check "already mounted generated: no second generator invocation" test "$(wc -l < "$T/node-calls")" = 1
touch "$T/umount-fail"
run stop
check "failed generated stop reports failure" rc 1
check "failed stop keeps the generated target list" test -f "$T/work/generated/manifest.json"
check "failed stop keeps the mounted sources" test -d "$T/work/stage"
run status
check "failed stop still reports generated mount" has 'match=generated' "$T/out"
rm "$T/umount-fail"
run stop
check "generated stop succeeds" rc 0
check "generated mounts gone" test ! -s "$T/mounts"
check "generated manifest removed" test ! -e "$T/work/generated/manifest.json"
rm -rf "${T:?}"

echo "== reviewed sets take precedence even with opt-in"
setup
touch "$T/generated-flag" "$T/generator-success"
run apply
check "reviewed set used" has '1.0' "$T/work/release"
check "generator not run" test ! -e "$T/node-calls"
rm -rf "${T:?}"

echo "== concurrent runs"
setup
# Overlapping status runs (app install, watcher refresh, service) must each see the same answer:
# they used to share one scratch list and one run could delete it under another.
n=
for round in 1 2 3 4 5; do
    for i in 1 2 3 4 5 6 7 8; do
        $SH "$SCRIPT" status > "$T/par.$round.$i" 2>&1 &
    done
    wait
done
bad=0
for o in "$T"/par.*; do
    grep -qx 'match=1.0' "$o" && grep -qx 'mounted=no' "$o" || { bad=$((bad + 1)); n=$(tr '\n' ' ' < "$o"); }
done
check "40 parallel status runs all report match=1.0 ($bad wrong: $n)" test "$bad" = 0
check "no scratch lists left behind" test -z "$(ls "$T/work" | grep '^entries')"
rm -f "$T"/par.*

# apply, stop and refresh exclude each other
$SH "$SCRIPT" apply > "$T/par.a" 2>&1 &
$SH "$SCRIPT" apply > "$T/par.b" 2>&1 &
wait
check "two parallel applies mount once" test "$(ncmds mount)" = 2
check "no lock left after the runs" test ! -e "$T/work/lock.d"
# a lock whose owner is gone is taken over
mkdir "$T/work/lock.d"; echo 999999 > "$T/work/lock.d/pid"
run stop
check "stale lock taken over" rc 0
check "stale lock removed on exit" test ! -e "$T/work/lock.d"
# a live owner holds the others off, and they give up after the timeout
sleep 30 & holder=$!
mkdir "$T/work/lock.d"; echo $holder > "$T/work/lock.d/pid"
HOMECHOOSER_LOCK_WAIT=1 run apply
check "busy lock: gives up with 1" rc 1
check "busy lock: says so" has 'giving up' "$T/out"
check "busy lock: not removed by the loser" test -d "$T/work/lock.d"
HOMECHOOSER_LOCK_WAIT=1 run status
check "status ignores the lock" rc 0
kill $holder 2>/dev/null; wait $holder 2>/dev/null
rm -rf "${T:?}"

echo "== usage"
setup
run bogus
check "exit 2" rc 2
rm -rf "${T:?}"

echo "$PASS passed, $FAIL failed ($SH)"
[ "$FAIL" = 0 ]
