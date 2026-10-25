#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# usage: build.sh <outdir>
# Packages both QML fixture launchers with ares-package into
#   <outdir>/org.webosbrew.hcfixture.qml.normal_1.0.0_all.ipk
#   <outdir>/org.webosbrew.hcfixture.qml.overlay_1.0.0_all.ipk
# Offline: contacts no device. python3 stdlib + sed + ares-package only.
set -eu
[ $# -eq 1 ] || { echo "usage: $0 <outdir>" >&2; exit 2; }
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$1
VERSION=1.0.0

mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# Plain 80x80 PNG (solid colour with a lighter border), stdlib only.
python3 -I - "$TMP/icon.png" <<'PY'
import struct, sys, zlib
w = h = 80
rows = b''
for y in range(h):
    row = b'\0'
    for x in range(w):
        edge = x < 4 or y < 4 or x >= w - 4 or y >= h - 4
        row += bytes((200, 200, 200) if edge else (120, 40, 140))
    rows += row
def chunk(t, d):
    c = struct.pack('>I', len(d)) + t + d
    return c + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) \
    + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b'')
open(sys.argv[1], 'wb').write(png)
PY

build_variant() { # <normal|overlay>
	v=$1
	id=org.webosbrew.hcfixture.qml.$v
	stage=$TMP/$id
	mkdir -p "$stage"
	cp "$TMP/icon.png" "$stage/icon.png"
	if [ "$v" = overlay ]; then
		title="HC Fixture qml overlay"
		bg=transparent
		wtype='    windowType: "_WEBOS_WINDOW_TYPE_OVERLAY"'
		extra=',
  "transparent": true,
  "defaultWindowType": "overlay"'
		body='    // Transparent window with one small strip along the bottom.
    Rectangle {
        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
        height: 90
        color: "#e08020"
    }'
	else
		title="HC Fixture qml normal"
		bg="#103060"
		wtype=
		extra=
		body=
	fi
	python3 -I - "$HERE/main.qml.in" "$stage/main.qml" "$id" "$title" "$bg" "$wtype" "$body" <<'PY'
import sys
src, dst, appid, title, bg, wtype, body = sys.argv[1:8]
t = open(src).read()
for k, v in (('@APPID@', appid), ('@TITLE@', title),
             ('@BGCOLOR@', bg), ('@WINDOWTYPE@', wtype), ('@BODY@', body)):
    t = t.replace(k, v)
assert not [k for k in ('@APPID@', '@TITLE@', '@BGCOLOR@', '@WINDOWTYPE@', '@BODY@') if k in t], 'unfilled marker'
open(dst, 'w').write(t)
PY
	python3 -I - "$HERE/appinfo.json.in" "$stage/appinfo.json" "$id" "$title" "$extra" <<'PY'
import json, sys
src, dst, appid, title, extra = sys.argv[1:6]
t = open(src).read().replace('@APPID@', appid).replace('@TITLE@', title).replace('@EXTRA@', extra)
json.loads(t)  # fail the build on invalid JSON
open(dst, 'w').write(t)
PY
	ares-package --outdir "$OUT" "$stage" >/dev/null
	echo "built $OUT/${id}_${VERSION}_all.ipk"
}

build_variant normal
build_variant overlay
