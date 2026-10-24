#!/bin/sh
# Round trip of the Home key hook against the REAL stock files of each release, with the
# worker (homechooser-homekey) run for real and only mount, umount, systemctl and luna-send
# stubbed. For each release it checks that status accepts structural anchors; that apply patches
# copies and mounts them over every target; that the id baked into Settings.qml follows the
# installed state of the stored choice (installed, not installed); that refresh and refresh stock
# rewrite it in place without restarting the compositor (only `refresh restart` does); that the staged copies match the rewriter plan; that stop
# unmounts everything; and that the stock files were never touched.
#
# usage: test-real-roundtrip.sh [shell] [release=rootfs ...]
#   shell defaults to sh (e.g. "busybox sh"). With no release=rootfs pairs, every corpus root
#   is tried; a root that is not on this machine is skipped
#   with a notice. Explicit pairs must exist (a missing root fails).
SH=${1:-sh}
[ $# -gt 0 ] && shift
HERE=$(cd "$(dirname "$0")/.." && pwd)
PASS=0
SKIPPED=0
DEFAULTS=
FAIL=0
check() { d=$1; shift; if "$@"; then PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); echo "FAIL: $d"; fi; }
has() { grep -qF -- "$1" "$2"; }

if [ $# -eq 0 ]; then
    DEFAULTS=1
    pairs=$(python3 -I "$HERE/tests/test_minimal_hook.py" --list-corpus)
    oldifs=$IFS; IFS='
'; set -- $pairs; IFS=$oldifs
fi

# seq: the systemctl calls that change something, in order; RESTART: what every release's
# lsm-ready.service gives (docs/home-key-hook.md, "Restarting the compositor")
seq() { grep '^systemctl ' "$T/cmds" | grep -v '^systemctl is-active '; }
RESTART="systemctl stop webapp-mgr.service
systemctl stop booster.service
systemctl stop maliit-server.service
systemctl restart surface-manager-daemon.service
systemctl start webapp-mgr.service
systemctl start booster.service
systemctl start maliit-server.service"

entries() { tr '{},' '\n\n\n' < "$1" | sed -En 's/^[[:space:]]*"(path)"[[:space:]]*:[[:space:]]*"([^"]*)".*$/\2/p'; }

for pair in "$@"; do
    rel=${pair%%=*}
    root=${pair#*=}
    echo "== $rel ($SH)"
    if [ ! -d "$root/usr/lib/qml" ]; then
        if [ -n "$DEFAULTS" ]; then echo "SKIP: $rel: firmware root not on this machine ($root)"; SKIPPED=$((SKIPPED + 1)); continue; fi
        FAIL=$((FAIL + 1)); echo "FAIL: $rel: no rootfs at $root"; continue
    fi
    T=$(mktemp -d)
    mkdir -p "$T/bin" "$T/data"
    if [ ! -f "$root/etc/os-release" ] && [ ! -f "$root/usr/lib/os-release" ]; then
        # Saved per-file corpus copies: version metadata is from the study, never a device guess.
        cp -r "$root" "$T/stock"
        root=$T/stock
        mkdir -p "$root/etc"
        version=$(printf '%s' "$rel" | sed 's/[^0-9.].*//')
        printf 'VERSION_ID="%s"\n' "$version" > "$root/etc/os-release"
    fi

    : > "$T/mounts"; : > "$T/cmds"
    printf 'my.home\n' > "$T/home"; echo my.home > "$T/installed"
    node "$HERE/homechooser-homekey-hook.js" generate --root "$root" --out "$T/expected" --manifest "$T/expected.json" > /dev/null || { FAIL=$((FAIL + 1)); rm -rf "$T"; continue; }
    entries "$T/expected.json" > "$T/entries"
    : > "$T/before"
    while read -r p; do sha256sum "$root$p" >> "$T/before"; done < "$T/entries"
    cat > "$T/bin/mount" <<STUB
#!/bin/sh
echo "mount \$*" >> "$T/cmds"
echo "\$2 \$3 none bind 0 0" >> "$T/mounts"
STUB
    cat > "$T/bin/umount" <<STUB
#!/bin/sh
echo "umount \$*" >> "$T/cmds"
grep -vF " \$1 " "$T/mounts" > "$T/mounts.new"; mv "$T/mounts.new" "$T/mounts"
STUB
    printf '#!/bin/sh\necho "systemctl $*" >> %s/cmds\n[ "$1" = is-active ] && echo active\nexit 0\n' "$T" > "$T/bin/systemctl"
    cat > "$T/bin/luna-send" <<STUB
#!/bin/sh
[ "\$1" = -a ] && shift 2
[ "\$3" = luna://com.webos.surfacemanager/getCurrentState ] && { echo '{"returnValue":true,"currentState":"none"}'; exit; }
id=\$(echo "\$4" | sed 's/.*"id":"\([^"]*\)".*/\1/')
if grep -qxF "\$id" "$T/installed"; then echo '{"returnValue":true}'; else echo '{"returnValue":false}'; fi
STUB
    chmod +x "$T/bin/"*
    export HOMECHOOSER_ROOT="$root" HOMECHOOSER_WORK="$T/work" HOMECHOOSER_LOG="$T/log" \
           HOMECHOOSER_MOUNTS="$T/mounts" HOMECHOOSER_MOUNT="$T/bin/mount" HOMECHOOSER_UMOUNT="$T/bin/umount" \
           HOMECHOOSER_SYSTEMCTL="$T/bin/systemctl" HOMECHOOSER_CHOICE="$T/home" HOMECHOOSER_LUNA_SEND="$T/bin/luna-send" \
           HOMECHOOSER_SAM_WAIT=0 HOMECHOOSER_HOMEKEY_FLAG="$T/homekey-flag"
    export HOMECHOOSER_CACHE="$T/hook" HOMECHOOSER_STALE="$T/hook-stale" HOMECHOOSER_APPINFO="$T/appinfo.json"
    printf '{"version":"1.0"}\n' > "$T/appinfo.json"
    SETTINGS=/usr/lib/qml/WebOSCompositorBase/global/Settings.qml
    stage=$T/work/stage$SETTINGS
    export HOMECHOOSER_NODE="${NODE:-node}" HOMECHOOSER_REWRITER="$HERE/homechooser-homekey-hook.js"
    w() { $SH "$HERE/homechooser-homekey" "$@" > "$T/out" 2>&1; }

    w prepare
    check "$rel: cache prepared" test -f "$T/hook/manifest.tsv"
    w status
    check "$rel: status accepts structural anchors" has 'patch=possible' "$T/out"
    w apply
    check "$rel: apply exits 0 and mounts every target" test "$(grep -c '^mount' "$T/cmds")" = "$(grep -c . "$T/entries")"
    check "$rel: compositor restarted once" test "$(grep -c '^systemctl restart' "$T/cmds")" = 1
    # the clients come from this firmware's own lsm-ready.service: stopped before, started after
    check "$rel: the firmware's clients around the compositor restart" test "$(seq)" = "$RESTART"
    # the bind sources live under WORK (/tmp), not in the app directory, so they outlive an uninstall
    check "$rel: every mount source is under the work directory" test "$(grep -c "^mount --bind $T/work/stage/" "$T/cmds")" = "$(grep -c . "$T/entries")"
    check "$rel: no mount source in the data (app) directory" test "$(grep '^mount ' "$T/cmds" | grep -c "$T/data")" = 0
    check "$rel: choice baked in (installed)" has 'configuredHomeAppId: "my.home"' "$stage"
    # With the baked id reset, staged copies must equal the independently generated plan.
    cp -r "$T/work/stage" "$T/rev"
    sed -i 's|configuredHomeAppId: "[^"]*"|configuredHomeAppId: "@HOMECHOOSER_HOME@"|' "$T/rev$SETTINGS"
    check "$rel: every staged byte equals the rewriter plan" diff -r "$T/rev" "$T/expected"
    w apply
    check "$rel: second apply is a no-op" has 'already mounted' "$T/log"
    : > "$T/cmds"
    w refresh stock
    check "$rel: refresh stock empties the id" has 'configuredHomeAppId: ""' "$stage"
    check "$rel: and does not restart the compositor" test -z "$(seq)"
    w refresh
    check "$rel: refresh puts the installed choice back" has 'configuredHomeAppId: "my.home"' "$stage"
    check "$rel: refresh does not restart the compositor" test -z "$(seq)"
    : > "$T/installed"
    w refresh
    check "$rel: refresh with the app gone bakes stock" has 'configuredHomeAppId: ""' "$stage"
    check "$rel: still no restart" test -z "$(seq)"
    echo my.home > "$T/installed"
    : > "$T/cmds"
    w refresh restart
    check "$rel: refresh restart restarts the compositor with its clients" test "$(seq)" = "$RESTART"
    : > "$T/installed"
    w refresh
    : > "$T/cmds"
    w stop
    check "$rel: stop unmounts everything" test ! -s "$T/mounts"
    check "$rel: and restarts the compositor" test "$(seq)" = "$RESTART"
    : > "$T/cmds"
    w stop
    check "$rel: second stop does nothing" test ! -s "$T/cmds"
    # apply again with the app not installed at all
    w apply
    check "$rel: apply with the app not installed bakes stock" has 'configuredHomeAppId: ""' "$stage"
    w stop
    : > "$T/after"
    while read -r p; do sha256sum "$root$p" >> "$T/after"; done < "$T/entries"
    check "$rel: the stock files were never touched" cmp -s "$T/before" "$T/after"
    rm -rf "${T:?}"
done

echo "$PASS passed, $FAIL failed, $SKIPPED skipped ($SH)"
[ "$FAIL" = 0 ]
