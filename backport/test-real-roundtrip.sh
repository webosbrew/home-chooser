#!/bin/sh
# Round trip of the key filter backport against the REAL stock files of each release, with the
# worker (homechooser-keyfilter) run for real and only mount, umount, systemctl and luna-send
# stubbed. For each release it checks that status finds the set by hash; that apply patches
# copies and mounts them over every target; that the id baked into Settings.qml follows the
# installed state of the stored choice (installed, not installed); that refresh and refresh stock
# rewrite it in place; that the patched copies reverse-patch back to the stock bytes; that stop
# unmounts everything; and that the stock files were never touched.
#
# usage: test-real-roundtrip.sh [shell] [release=rootfs ...]
#   shell defaults to sh (e.g. "busybox sh"). With no release=rootfs pairs, every set directory
#   is tried against its known firmware root; a set whose root is not on this machine is skipped
#   with a notice. Explicit pairs must exist (a missing root fails).
SH=${1:-sh}
[ $# -gt 0 ] && shift
HERE=$(cd "$(dirname "$0")" && pwd)
PASS=0
SKIPPED=0
DEFAULTS=
FAIL=0
check() { d=$1; shift; if "$@"; then PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); echo "FAIL: $d"; fi; }
has() { grep -qF -- "$1" "$2"; }

if [ $# -eq 0 ]; then
    DEFAULTS=1
    P=$HOME/Projects
    set -- "6.5.2=$P/webos-hypervisor/build/w21a/03.53.25.01-HE_DTV_W21A_AFADATAA/rootfs" \
           "6.6.0=$P/webos-firmwares/03.70.25.01-HE_DTV_W21P_AFADATAA/rootfs" \
           "7.6.0=$P/webos-firmwares/04.60.90.01-HE_DTV_W22P_AFADATAA/rootfs" \
           "8.3.0=$P/webos-hypervisor/scratch/w22-upgrades/number1-extracted/rootfs" \
           "8.3.0-s23z=${OPENHOUSE_S23Z_ROOTFS:-$P/webos-hypervisor/scratch/firmware/03.30.18.01-HE_MNT_S23Z_AAACGLAA/rootfs.pak.unsquashfs}" \
           "8.4.0=$P/webos-hypervisor/scratch/firmware/k8hpp/13.41.12.01-HE_DTV_W23H_AFADATAA/rootfs" \
           "9.2.1=$P/webos-hypervisor/scratch/w22-upgrades/ombre-extracted/rootfs" \
           "9.2.4=$P/webos-hypervisor/build/k8lpn2-ombre/rootfs"
fi

entries() { tr '{},' '\n\n\n' < "$1" | sed -En 's/^[[:space:]]*"(path|sha256|diff)"[[:space:]]*:[[:space:]]*"([^"]*)".*$/\2/p'; }

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
    cp -r "$HERE/$rel" "$T/data/"
    : > "$T/mounts"; : > "$T/cmds"
    printf 'my.home\n' > "$T/home"; echo my.home > "$T/installed"
    entries "$T/data/$rel/manifest.json" > "$T/entries"
    : > "$T/before"
    while read -r p && read -r h && read -r d; do sha256sum "$root$p" >> "$T/before"; done < "$T/entries"
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
    printf '#!/bin/sh\necho "systemctl $*" >> %s/cmds\n' "$T" > "$T/bin/systemctl"
    cat > "$T/bin/luna-send" <<STUB
#!/bin/sh
id=\$(echo "\$4" | sed 's/.*"id":"\([^"]*\)".*/\1/')
if grep -qxF "\$id" "$T/installed"; then echo '{"returnValue":true}'; else echo '{"returnValue":false}'; fi
STUB
    chmod +x "$T/bin/"*
    export HOMECHOOSER_DATA="$T/data" HOMECHOOSER_ROOT="$root" HOMECHOOSER_WORK="$T/work" HOMECHOOSER_LOG="$T/log" \
           HOMECHOOSER_MOUNTS="$T/mounts" HOMECHOOSER_MOUNT="$T/bin/mount" HOMECHOOSER_UMOUNT="$T/bin/umount" \
           HOMECHOOSER_SYSTEMCTL="$T/bin/systemctl" HOMECHOOSER_CHOICE="$T/home" HOMECHOOSER_LUNA_SEND="$T/bin/luna-send" \
           HOMECHOOSER_SAM_WAIT=0
    SETTINGS=/usr/lib/qml/WebOSCompositorBase/global/Settings.qml
    stage=$T/work/stage$SETTINGS
    w() { $SH "$HERE/homechooser-keyfilter" "$@" > "$T/out" 2>&1; }

    w status
    check "$rel: status finds the set by hash" has "match=$rel" "$T/out"
    w apply
    check "$rel: apply exits 0 and mounts every target" test "$(grep -c '^mount' "$T/cmds")" = "$(grep -c . "$T/entries" | awk '{print $1 / 3}')"
    check "$rel: compositor restarted once" test "$(grep -c '^systemctl restart' "$T/cmds")" = 1
    check "$rel: choice baked in (installed)" has 'configuredHomeAppId: "my.home"' "$stage"
    # reverse-patch the staged copies (placeholder back in) and compare with the real stock bytes
    mkdir -p "$T/rev"
    rm -rf "$T/rev"; cp -r "$T/work/stage" "$T/rev"
    sed -i 's|configuredHomeAppId: "[^"]*"|configuredHomeAppId: "@HOMECHOOSER_HOME@"|' "$T/rev$SETTINGS"
    ok=1
    while read -r p && read -r h && read -r d; do
        (cd "$T/rev" && patch -R -p0 < "$T/data/$rel/$d" > /dev/null) || ok=0
        cmp -s "$T/rev$p" "$root$p" || ok=0
    done < "$T/entries"
    check "$rel: reverse patch gives the stock bytes of every file" test "$ok" = 1
    w apply
    check "$rel: second apply is a no-op" has 'already mounted' "$T/log"
    : > "$T/cmds"
    w refresh stock
    check "$rel: refresh stock empties the id" has 'configuredHomeAppId: ""' "$stage"
    check "$rel: and restarts the compositor" has 'systemctl restart' "$T/cmds"
    w refresh
    check "$rel: refresh puts the installed choice back" has 'configuredHomeAppId: "my.home"' "$stage"
    : > "$T/installed"
    w refresh
    check "$rel: refresh with the app gone bakes stock" has 'configuredHomeAppId: ""' "$stage"
    : > "$T/cmds"
    w stop
    check "$rel: stop unmounts everything" test ! -s "$T/mounts"
    check "$rel: and restarts the compositor" has 'systemctl restart' "$T/cmds"
    : > "$T/cmds"
    w stop
    check "$rel: second stop does nothing" test ! -s "$T/cmds"
    # apply again with the app not installed at all
    w apply
    check "$rel: apply with the app not installed bakes stock" has 'configuredHomeAppId: ""' "$stage"
    w stop
    : > "$T/after"
    while read -r p && read -r h && read -r d; do sha256sum "$root$p" >> "$T/after"; done < "$T/entries"
    check "$rel: the stock files were never touched" cmp -s "$T/before" "$T/after"
    # Exercise the real generated fallback on the same stock root, suppressing reviewed lookup.
    # All side effects remain the stubs above; no stock file is ever written.
    export HOMECHOOSER_NO_SETS=1 HOMECHOOSER_GEN="$HERE/homechooser-keyfilter-gen.js" \
           HOMECHOOSER_GENERATED_FLAG="$T/generated-flag"
    echo my.home > "$T/installed"
    : > "$T/cmds"
    w status
    check "$rel: generated offered without opt-in" has 'generated=possible' "$T/out"
    check "$rel: opt-in initially off" has 'optin=no' "$T/out"
    w apply
    check "$rel: no opt-in mounts nothing" test ! -s "$T/mounts"
    touch "$T/generated-flag"
    w apply
    check "$rel: real generator apply succeeds" test "$?" = 0
    check "$rel: generated choice baked in" has 'configuredHomeAppId: "my.home"' "$stage"
    w status
    check "$rel: generated release" has 'release=generated' "$T/out"
    check "$rel: generated match" has 'match=generated' "$T/out"
    check "$rel: generated status hashes every target" test "$(grep -c '^hash=' "$T/out")" = "$(grep -c . "$T/entries" | awk '{print $1 / 3}')"
    w refresh stock
    check "$rel: generated refresh stock clears id" has 'configuredHomeAppId: ""' "$stage"
    w refresh
    check "$rel: generated refresh restores id" has 'configuredHomeAppId: "my.home"' "$stage"
    w stop
    check "$rel: generated stop unmounts everything" test ! -s "$T/mounts"
    check "$rel: generated manifest removed" test ! -e "$T/work/generated/manifest.json"
    : > "$T/after"
    while read -r p && read -r h && read -r d; do sha256sum "$root$p" >> "$T/after"; done < "$T/entries"
    check "$rel: generation left stock bytes unchanged" cmp -s "$T/before" "$T/after"
    unset HOMECHOOSER_NO_SETS HOMECHOOSER_GEN HOMECHOOSER_GENERATED_FLAG
    rm -rf "${T:?}"
done

echo "$PASS passed, $FAIL failed, $SKIPPED skipped ($SH)"
[ "$FAIL" = 0 ]
