#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# usage: build.sh <outdir>
# Builds both native fixture launchers and packages them with ares-package into
#   <outdir>/org.webosbrew.hcfixture.native.normal_1.0.0_arm.ipk
#   <outdir>/org.webosbrew.hcfixture.native.overlay_1.0.0_arm.ipk
# Same cross SDK as the service build (service/cmake/settings.cmake). Offline: contacts no device.
set -eu
[ $# -eq 1 ] || { echo "usage: $0 <outdir>" >&2; exit 2; }
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$1
SDK=${SDK:-/opt/arm-webos-linux-gnueabi_sdk-buildroot}
SYSROOT=${SYSROOT:-$SDK/arm-webos-linux-gnueabi/sysroot}
CROSS=${CROSS:-$SDK/bin/arm-webos-linux-gnueabi-}
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
        row += bytes((200, 200, 200) if edge else (20, 60, 140))
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
	id=org.webosbrew.hcfixture.native.$v
	stage=$TMP/$id
	mkdir -p "$stage"
	extra=
	libs="-lSDL2 -lhelpers"
	if [ "$v" = overlay ]; then
		libs="$libs -lwayland-client -lwayland-webos-client"
		title="HC Fixture native overlay"
	else
		title="HC Fixture native normal"
	fi
	"${CROSS}gcc" --sysroot="$SYSROOT" -std=gnu11 -O2 -g0 -Wall -Wextra -Werror \
		-DHC_VARIANT_$(echo "$v" | tr a-z A-Z) -DHC_APP_ID="\"$id\"" \
		-I"$SYSROOT/usr/include/SDL2" -I"$SYSROOT/usr/include" \
		-I"$SYSROOT/usr/include/glib-2.0" -I"$SYSROOT/usr/lib/glib-2.0/include" \
		-o "$stage/hcfixture" "$HERE/hcfixture.c" $libs -Wl,--as-needed,-O1
	"${CROSS}strip" "$stage/hcfixture"
	cp "$TMP/icon.png" "$stage/icon.png"
	if [ "$v" = overlay ]; then
		extra=',
  "transparent": true,
  "defaultWindowType": "overlay"'
	fi
	cat > "$stage/appinfo.json" <<JSON
{
  "id": "$id",
  "version": "$VERSION",
  "vendor": "webOS Homebrew",
  "title": "$title",
  "type": "native",
  "main": "hcfixture",
  "icon": "icon.png",
  "appCategory": ["home"],
  "visible": true,
  "noSplashOnLaunch": true$extra
}
JSON
	ares-package --outdir "$OUT" "$stage" >/dev/null
	echo "built $OUT/${id}_${VERSION}_arm.ipk"
}

build_variant normal
build_variant overlay
