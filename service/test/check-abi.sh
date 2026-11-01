#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# usage: check-abi.sh BINARY ROOTFS
# Lists the SONAMEs the binary needs and the highest GLIBC / GLIBCXX / GLIB version it asks of
# each, then checks that the rootfs (the oldest supported release) has the libraries and offers
# those versions. Exits non-zero on a miss.
# The tools: READELF and NM, else CROSS (the build's tool prefix, service/cmake/settings.cmake) followed by the tool name.
set -eu
TASK_TMP=$(mktemp -d "${TMPDIR:-/tmp}/hc-abi.XXXXXX")
trap 'rm -rf "$TASK_TMP"' EXIT HUP INT TERM
BIN=$1
ROOT=$2
CROSS=${CROSS:-/opt/arm-webos-linux-gnueabi_sdk-buildroot/bin/arm-webos-linux-gnueabi-}
READELF=${READELF:-${CROSS}readelf}
fail=0

echo "== NEEDED"
needed=$($READELF -d "$BIN" | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p')
for so in $needed; do
	found=$(find "$ROOT/lib" "$ROOT/usr/lib" -name "$so" 2>/dev/null | head -1)
	if [ -n "$found" ]; then echo "$so  present"; else echo "$so  MISSING in $ROOT"; fail=1; fi
done

echo "== required symbol versions (binary)"
$READELF -V "$BIN" | sed -n 's/.*Name: \([A-Za-z_+.0-9]*\)  *Flags.*/\1/p' | sort -u | sed -n '/GLIBC\|GLIBCXX\|CXXABI\|GLIB_/p' | tee "$TASK_TMP/required"
echo "== offered symbol versions (rootfs libc, libstdc++, libglib)"
offered=$( { for l in "$ROOT"/lib/libc.so.6 "$ROOT"/lib/libc-*.so "$ROOT"/usr/lib/libglib-2.0.so.0 "$ROOT"/usr/lib/libstdc++.so.6; do
	[ -e "$l" ] && $READELF -V "$l" 2>/dev/null | sed -n 's/.*Name: \([A-Za-z_+.0-9]*\)  *Flags.*/\1/p'; done; } | sort -u)
while read -r v; do
	[ -n "$v" ] || continue
	if echo "$offered" | grep -qx "$v"; then echo "$v  ok"; else echo "$v  NOT OFFERED by the rootfs"; fail=1; fi
done < "$TASK_TMP/required"
rm -f "$TASK_TMP/required"

echo "== undefined symbols the rootfs libraries do not define (weak toolchain stubs are fine)"
NM=${NM:-${CROSS}nm}
$NM -D --undefined-only "$BIN" | awk '$1 != "w" {print $2}' | sed 's/@.*//' | sort -u > "$TASK_TMP/need"
for so in $needed; do
	f=$(find "$ROOT/lib" "$ROOT/usr/lib" -name "$so" 2>/dev/null | head -1)
	[ -n "$f" ] && $NM -D --defined-only "$f" | awk '{print $3}' | sed 's/@.*//'
done | sort -u > "$TASK_TMP/have"
missing=$(comm -23 "$TASK_TMP/need" "$TASK_TMP/have")
rm -f "$TASK_TMP/need" "$TASK_TMP/have"
if [ -n "$missing" ]; then echo "$missing"; fail=1; else echo "none"; fi
exit $fail
