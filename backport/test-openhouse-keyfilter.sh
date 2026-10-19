#!/bin/sh
# Tests for openhouse-keyfilter against a fake root with stubbed mount, umount and systemctl.
# usage: test-openhouse-keyfilter.sh [shell]     (shell defaults to sh, e.g. "busybox sh")
SH=${1:-sh}
HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT=$HERE/openhouse-keyfilter
PASS=0
FAIL=0

check() { # check <description> <command...>
    d=$1; shift
    if "$@"; then PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); echo "FAIL: $d"; fi
}
has() { grep -qF -- "$1" "$2"; }
hasnt() { ! grep -qF -- "$1" "$2"; }

setup() {
    T=$(mktemp -d)
    mkdir -p "$T/root/usr/lib/qml" "$T/data/1.0" "$T/data/2.0" "$T/bin" "$T/orig"
    printf 'one\ntwo\nthree\nhome = "com.webos.app.home"\nfive\n' > "$T/root/usr/lib/qml/a.qml"
    printf 'alpha\nbeta\nHOME_APP_ID\ngamma\n' > "$T/root/usr/lib/qml/b.js"
    cp "$T/root/usr/lib/qml/a.qml" "$T/root/usr/lib/qml/b.js" "$T/orig/"
    printf 'one\ntwo\nthree\nhome = Settings.getHomeAppId()\nfive\n' > "$T/new-a"
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
[ "\$1" = "-l" ] && shift
grep -vF " \$1 " "$T/mounts" > "$T/mounts.new"; mv "$T/mounts.new" "$T/mounts"
STUB
    cat > "$T/bin/systemctl" <<STUB
#!/bin/sh
echo "systemctl \$*" >> "$T/cmds"
STUB
    chmod +x "$T/bin/"*
    export OPENHOUSE_DATA="$T/data" OPENHOUSE_ROOT="$T/root" OPENHOUSE_WORK="$T/work" \
           OPENHOUSE_LOG="$T/log" OPENHOUSE_MOUNTS="$T/mounts" OPENHOUSE_MOUNT="$T/bin/mount" \
           OPENHOUSE_UMOUNT="$T/bin/umount" OPENHOUSE_SYSTEMCTL="$T/bin/systemctl"
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
run status
check "status mounted" has 'mounted=yes' "$T/out"
check "status release" has 'release=1.0' "$T/out"

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
rm -rf "$T"

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
rm -rf "$T"

echo "== missing target mounts nothing"
setup
rm "$T/root/usr/lib/qml/a.qml"
run
check "exit 0" rc 0
check "no mount" test "$(ncmds mount)" = 0
rm -rf "$T"

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
rm -rf "$T"

echo "== mount failure undoes the first mount"
setup
echo b.js > "$T/mount-fail-on"
run
check "exit 1" rc 1
check "first mount undone" test "$(ncmds umount)" = 1
check "mounts empty" test ! -s "$T/mounts"
check "no restart" test "$(ncmds systemctl)" = 0
rm -rf "$T"

echo "== no data directory"
setup
rm -rf "$T/data"
run
check "exit 0" rc 0
check "no mount" test "$(ncmds mount)" = 0
rm -rf "$T"

echo "== usage"
setup
run bogus
check "exit 2" rc 2
rm -rf "$T"

echo "$PASS passed, $FAIL failed ($SH)"
[ "$FAIL" = 0 ]
