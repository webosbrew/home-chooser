#!/bin/sh
# The generator (homechooser-keyfilter-gen.js) against the REAL stock files of every firmware root
# found on this machine, with node and patch only (no worker, no TV).
#
# For each root:
#   webOS 6 to 9 with a reviewed set (every stock hash in a backport/<set>/manifest.json matches):
#     the generated files are byte for byte the reviewed set applied with `patch -p0 -F0`, and
#     they are the same files the set lists;
#   webOS 6 to 9 without a set (6.3.1, 7.4.0): the run passes its post-conditions, and what it
#     wrote differs from stock in every file, with the stock hashes in its manifest right;
#   webOS 5 and 10 and later: it aborts (exit 2), also with the os-release check switched off,
#     and writes nothing.
# Then a lab-set run for one root: its diffs apply to the stock files and give the generated files.
#
# usage: test-generated.sh [release=rootfs ...]
#   With no pairs, the roots below are tried; one that is not on this machine is skipped with a
#   notice. Explicit pairs must exist.
HERE=$(cd "$(dirname "$0")" && pwd)
GEN=$HERE/homechooser-keyfilter-gen.js
NODE=${NODE:-node}
PASS=0
FAIL=0
SKIPPED=0
IDENTICAL=0
DEFAULTS=
check() { d=$1; shift; if "$@"; then PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); echo "FAIL: $d"; fi; }

if [ $# -eq 0 ]; then
    DEFAULTS=1
    # where the roots live on this machine: OPENHOUSE_FIRMWARES and OPENHOUSE_HYPERVISOR override
    P=$HOME/Projects
    H=${OPENHOUSE_HYPERVISOR:-$P/webos-hypervisor}
    F=${OPENHOUSE_FIRMWARES:-$P/webos-firmwares}
    set -- "5.6.1=$F/04.63.25.01-HE_DTV_W20L_AFAAJAAA/rootfs" \
           "5.6.2=$F/04.74.00.01-HE_DTV_W20H_AFADABAA/rootfs" \
           "6.3.1-W21Z=$H/scratch/firmware/03.00.27.01-HE_PJT_W21Z_AAAAGLAA/rootfs" \
           "6.5.2-W21A=$H/build/w21a/03.53.25.01-HE_DTV_W21A_AFADATAA/rootfs" \
           "6.6.0-6-W21P=$F/03.70.25.01-HE_DTV_W21P_AFADATAA/rootfs" \
           "6.6.0-12-W21U=$H/scratch/firmware/03.50.20.01-HE_DTV_W21U_AFADATAA/rootfs" \
           "7.4.0-W22O=$H/scratch/firmware/o22/04.40.93.01-HE_DTV_W22O_AFABATPU/rootfs" \
           "7.6.0-18-W22H=$H/build/w22h-mullet/04.60.85.01-HE_DTV_W22H_AFADJAAA/rootfs" \
           "7.6.0-19-W22P=$F/04.60.90.01-HE_DTV_W22P_AFADATAA/rootfs" \
           "8.3.0-1063-S23Z=${OPENHOUSE_S23Z_ROOTFS:-$H/scratch/firmware/03.30.18.01-HE_MNT_S23Z_AAACGLAA/rootfs.pak.unsquashfs}" \
           "8.3.0-2301-W22O=$H/scratch/o22-number1-extract/13.30.56.01-HE_DTV_W22O_AFABATPU/rootfs" \
           "8.3.0-2401-W22H=$H/scratch/w22-upgrades/number1-extracted/rootfs" \
           "8.4.0-W23H=$H/scratch/firmware/k8hpp/13.41.12.01-HE_DTV_W23H_AFADATAA/rootfs" \
           "8.4.0-W23A=$H/scratch/firmware/lm21ann/13.40.90.01-HE_DTV_W23A_AFADATAA/rootfs.pak.unsquashfs" \
           "9.2.1-40-W22H=$H/scratch/w22-upgrades/ombre-extracted/rootfs" \
           "9.2.4-W24P=$H/scratch/firmware/k8lpn2-normalized/33.20.23.01-HE_DTV_W24P_AFADATAA/rootfs" \
           "10.3.1=$H/scratch/w22-later/ponytail-rootfs" \
           "11.2.0=$F/43.22.40.01-HE_DTV_W22H_AFABJAPU/rootfs"
fi

entries() { tr '{},' '\n\n\n' < "$1" | sed -En 's/^[[:space:]]*"(path|sha256|diff)"[[:space:]]*:[[:space:]]*"([^"]*)".*$/\2/p'; }

# set_for <root>: print the reviewed set whose files all match the root's, if any
set_for() {
    for m in "$HERE"/*/manifest.json; do
        [ -f "$m" ] || continue
        ok=1
        entries "$m" > "$T/ents"
        while read -r p && read -r h && read -r d; do
            [ "$(sha256sum "$1$p" 2> /dev/null | cut -d' ' -f1)" = "$h" ] || ok=0
        done < "$T/ents"
        if [ "$ok" = 1 ]; then basename "$(dirname "$m")"; return 0; fi
    done
    return 1
}

# same_tree <dir-a> <dir-b>: the two trees hold the same files with the same bytes
same_tree() { diff -r "$1" "$2" > /dev/null 2>&1; }

for pair in "$@"; do
    name=${pair%%=*}
    root=${pair#*=}
    echo "== $name"
    if [ ! -d "$root/usr/lib/qml" ]; then
        if [ -n "$DEFAULTS" ]; then echo "SKIP: $name: firmware root not on this machine ($root)"; SKIPPED=$((SKIPPED + 1)); continue; fi
        FAIL=$((FAIL + 1)); echo "FAIL: $name: no rootfs at $root"; continue
    fi
    T=$(mktemp -d)
    major=$(sed -n 's/^VERSION_ID="\{0,1\}\([0-9]*\)\..*/\1/p' "$root/etc/os-release" | head -n 1)
    rm -rf "$T/out"
    "$NODE" "$GEN" generate --root "$root" --out "$T/out" --manifest "$T/out.json" > "$T/stdout" 2> "$T/stderr"
    rc=$?
    if [ "${major:-0}" -ge 6 ] && [ "${major:-0}" -le 9 ]; then
        check "$name: generate exits 0" test "$rc" = 0
        set=$(set_for "$root")
        if [ -n "$set" ]; then
            # the reviewed set, applied the way the worker applies it
            entries "$HERE/$set/manifest.json" > "$T/ents"
            mkdir -p "$T/ref"
            while read -r p && read -r h && read -r d; do
                mkdir -p "$T/ref$(dirname "$p")"
                cp "$root$p" "$T/ref$p"
            done < "$T/ents"
            applied=1
            while read -r p && read -r h && read -r d; do
                (cd "$T/ref" && patch -p0 -F0 -s < "$HERE/$set/$d") || applied=0
            done < "$T/ents"
            check "$name: reviewed set $set applies" test "$applied" = 1
            check "$name: generated tree is byte-identical to reviewed set $set ($(grep -c . "$T/ents" | awk '{print $1 / 3}') files)" same_tree "$T/ref" "$T/out"
            if same_tree "$T/ref" "$T/out"; then IDENTICAL=$((IDENTICAL + 1)); fi
        else
            echo "no reviewed set for $name: checking the post-conditions only"
            check "$name: output holds Settings.qml with the placeholder once" test "$(grep -c 'configuredHomeAppId: "@HOMECHOOSER_HOME@"' "$T/out/usr/lib/qml/WebOSCompositorBase/global/Settings.qml")" = 1
            n=0; bad=0
            for f in $(cd "$T/out" && find usr -type f); do
                n=$((n + 1))
                cmp -s "$T/out/$f" "$root/$f" && bad=$((bad + 1))
                if grep -v stockHomeAppId "$T/out/$f" | grep -q -e '"com.webos.app.home"' -e 'HOME_APP_ID'; then bad=$((bad + 1)); fi
            done
            check "$name: $n generated files, each changed, none names the stock id" test "$bad" = 0
        fi
        # the manifest names the stock hash of every file it generated
        ok=1
        entries "$T/out.json" > "$T/gents"
        while read -r p && read -r h && read -r d; do
            [ "$(sha256sum "$root$p" | cut -d' ' -f1)" = "$h" ] || ok=0
            [ -f "$T/out$p" ] || ok=0
        done < "$T/gents"
        check "$name: manifest hashes are the stock files' and name every output file" test "$ok" = 1 -a "$(grep -c . "$T/gents" | awk '{print $1 / 3}')" = "$(cd "$T/out" && find usr -type f | grep -c .)" -a "$(grep -c . "$T/gents")" -gt 0
        # same input, same output
        "$NODE" "$GEN" generate --root "$root" --out "$T/out2" > /dev/null 2>&1
        check "$name: a second run gives the same bytes" same_tree "$T/out" "$T/out2"
        "$NODE" "$GEN" check --root "$root" > /dev/null 2>&1
        check "$name: check agrees" test "$?" = 0
    else
        check "$name: aborts (exit 2) on webOS ${major:-?}" test "$rc" = 2
        check "$name: and writes nothing" test ! -e "$T/out" -a ! -e "$T/out.json"
        check "$name: says why" grep -q '^ABORT: ' "$T/stderr"
        "$NODE" "$GEN" check --root "$root" --skip-version-gate > /dev/null 2> "$T/stderr2"
        check "$name: aborts without the os-release check as well (the files themselves refuse)" test "$?" = 2
    fi
    rm -rf "${T:?}"
done

# an already patched tree is refused
for pair in "$@"; do
    name=${pair%%=*}; root=${pair#*=}
    [ -d "$root/usr/lib/qml" ] || continue
    major=$(sed -n 's/^VERSION_ID="\{0,1\}\([0-9]*\)\..*/\1/p' "$root/etc/os-release" | head -n 1)
    [ "${major:-0}" -ge 6 ] && [ "${major:-0}" -le 9 ] || continue
    T=$(mktemp -d)
    "$NODE" "$GEN" generate --root "$root" --out "$T/out" > /dev/null 2>&1 || { rm -rf "$T"; continue; }
    mkdir -p "$T/etc"; cp "$root/etc/os-release" "$T/etc/"
    "$NODE" "$GEN" check --root "$T/out" > /dev/null 2> "$T/stderr"
    # the output tree has no etc/os-release; give it one so the version gate passes
    cp -r "$T/etc" "$T/out/etc"
    "$NODE" "$GEN" check --root "$T/out" > /dev/null 2> "$T/stderr"
    check "$name: a tree that is already patched is refused" test "$?" = 2
    check "$name: with the reason named" grep -q 'already routes the home id' "$T/stderr"
    # lab-set: diffs that apply to the stock files and give the generated files
    "$NODE" "$GEN" lab-set --root "$root" --release "lab-$name" --out "$T/set" > /dev/null 2>&1
    check "$name: lab-set exits 0" test "$?" = 0
    entries "$T/set/manifest.json" > "$T/ents"
    mkdir -p "$T/lab"
    ok=1
    while read -r p && read -r h && read -r d; do
        mkdir -p "$T/lab$(dirname "$p")"; cp "$root$p" "$T/lab$p"
        [ "$(sha256sum "$root$p" | cut -d' ' -f1)" = "$h" ] || ok=0
    done < "$T/ents"
    while read -r p && read -r h && read -r d; do
        (cd "$T/lab" && patch -p0 -F0 -s < "$T/set/$d") || ok=0
    done < "$T/ents"
    same_tree "$T/lab/usr" "$T/out/usr" || ok=0
    check "$name: lab-set diffs apply and give the generated files" test "$ok" = 1
    rm -rf "${T:?}"
    break
done

echo "$PASS passed, $FAIL failed, $SKIPPED skipped, $IDENTICAL builds byte-identical to their reviewed set"
[ "$FAIL" = 0 ]
