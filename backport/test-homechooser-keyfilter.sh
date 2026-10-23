#!/bin/sh
# Tests for homechooser-keyfilter against a fake root with stubbed mount, umount and systemctl.
# usage: test-homechooser-keyfilter.sh [shell]     (shell defaults to sh, e.g. "busybox sh")
SH=${1:-sh}
HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT=$HERE/homechooser-keyfilter
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
    cat > "$T/bin/systemctl" <<STUB
#!/bin/sh
echo "systemctl \$*" >> "$T/cmds"
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
           HOMECHOOSER_GENERATED_FLAG="$T/generated-flag" HOMECHOOSER_NO_SETS_FLAG="$T/debug-no-sets"
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

echo "== matching set applied ($SH)"
setup
run
check "exit 0" rc 0
check "a.qml patched in the stage" has 'Settings.getHomeAppId()' "$T/work/stage/usr/lib/qml/a.qml"
check "b.js patched in the stage" has 'global.isHomeApp(x)' "$T/work/stage/usr/lib/qml/b.js"
check "stock a.qml untouched" cmp -s "$T/orig/a.qml" "$T/root/usr/lib/qml/a.qml"
check "two bind mounts" test "$(ncmds mount)" = 2
check "bind mount of the stage over the original" has "mount --bind $T/work/stage/usr/lib/qml/a.qml $T/root/usr/lib/qml/a.qml" "$T/cmds"
check "compositor restarted once" test "$(grep -c '^systemctl restart surface-manager-daemon.service$' "$T/cmds")" = 1
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
check "compositor restarted" has 'systemctl restart surface-manager-daemon.service' "$T/cmds"
rm -f "${T:?}/home"
: > "$T/cmds"
run refresh
check "choice removed: id cleared" has 'configuredHomeAppId: ""' "$T/work/stage/usr/lib/qml/a.qml"
check "restart again" has 'systemctl restart' "$T/cmds"

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
check "compositor restarted" has 'systemctl restart surface-manager-daemon.service' "$T/cmds"
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
check "compositor restarted" has 'systemctl restart surface-manager-daemon.service' "$T/cmds"
: > "$T/cmds"
run refresh stock
check "second stock refresh: nothing to do" test ! -s "$T/cmds"
run refresh
check "plain refresh brings the choice back" has 'configuredHomeAppId: "my.home"' "$T/work/stage/usr/lib/qml/a.qml"
: > "$T/installed"
: > "$T/cmds"
run refresh
check "plain refresh with the app gone bakes stock" has 'configuredHomeAppId: ""' "$T/work/stage/usr/lib/qml/a.qml"
check "restarted for it" has 'systemctl restart' "$T/cmds"
: > "$T/cmds"
run refresh stock
check "stock refresh when already stock: no restart" test ! -s "$T/cmds"
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
check "log says the compositor was left" has 'compositor not restarted' "$T/log"
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

echo "== usage"
setup
run bogus
check "exit 2" rc 2
rm -rf "${T:?}"

echo "$PASS passed, $FAIL failed ($SH)"
[ "$FAIL" = 0 ]
