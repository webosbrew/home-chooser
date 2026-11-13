#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# usage: build.sh <outdir>
# Builds the packages of the native-caller check of the launcher-API kit (hc_e2e/steps/api.py, api_native_identity) with
# the SDK that builds service/, packaged with ares-package into <outdir>:
#   org.webosbrew.hcfixture.native.probe_1.0.0_arm.ipk        a native launcher (home category, the permission group)
#       that calls getInfo once from its own process (idprobe.c)
#   org.webosbrew.hcfixture.native.probenohome_1.0.0_arm.ipk  the same without the home category: must be refused
#   org.webosbrew.hcfixture.native_1.0.0_arm.ipk              an app whose id is a prefix of the first one's, with a
#       service named like that app (impostor.c): a name the installer allows and the service must refuse
# Offline: contacts no device.
set -eu
[ $# -eq 1 ] || { echo "usage: $0 <outdir>" >&2; exit 2; }
HERE=$(cd "$(dirname "$0")" && pwd)
SDK=${SDK:-/opt/arm-webos-linux-gnueabi_sdk-buildroot}
SYSROOT=${SYSROOT:-$SDK/arm-webos-linux-gnueabi/sysroot}
CROSS=${CROSS:-$SDK/bin/arm-webos-linux-gnueabi-}
GROUP=org.webosbrew.homechooser.applications
mkdir -p "$1"
OUT=$(cd "$1" && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
CFLAGS="--sysroot=$SYSROOT -std=gnu11 -O2 -g0 -Wall -Wextra -Werror -I$SYSROOT/usr/include -I$SYSROOT/usr/include/glib-2.0 -I$SYSROOT/usr/lib/glib-2.0/include"

python3 -I - "$TMP/icon.png" <<'PY'
import struct, sys, zlib
w = h = 80
rows = b''.join(b'\0' + bytes((90, 90, 90)) * w for _ in range(h))
def chunk(t, d):
    c = struct.pack('>I', len(d)) + t + d
    return c + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
open(sys.argv[1], 'wb').write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
    + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b''))
PY

probe() { # <id> <title> <appCategory json>
	stage=$TMP/$1
	mkdir -p "$stage"
	"${CROSS}gcc" $CFLAGS -DHC_PROBE_ID="\"$1\"" -o "$stage/idprobe" "$HERE/idprobe.c" -lhelpers -lluna-service2 -lglib-2.0 -Wl,--as-needed
	"${CROSS}strip" "$stage/idprobe"
	cp "$TMP/icon.png" "$stage/icon.png"
	cat > "$stage/appinfo.json" <<JSON
{
  "id": "$1",
  "version": "1.0.0",
  "vendor": "webOS Homebrew",
  "title": "$2",
  "type": "native",
  "main": "idprobe",
  "icon": "icon.png",
  "appCategory": $3,
  "requiredPermissions": ["$GROUP"],
  "visible": true,
  "noSplashOnLaunch": true
}
JSON
	ares-package --outdir "$OUT" "$stage" >/dev/null
	echo "built $OUT/$1_1.0.0_arm.ipk"
}
probe org.webosbrew.hcfixture.native.probe "HC Fixture native probe" '["home"]'
probe org.webosbrew.hcfixture.native.probenohome "HC Fixture native probe no home" '[]'

# the impostor: app org.webosbrew.hcfixture.native (a web app with nothing in it) and the service named like the probe
name=org.webosbrew.hcfixture.native.probe
mkdir -p "$TMP/imp/app" "$TMP/imp/svc"
"${CROSS}gcc" $CFLAGS -DHC_IMPOSTOR_NAME="\"$name\"" -o "$TMP/imp/svc/impostor" "$HERE/impostor.c" -lluna-service2 -lglib-2.0 -Wl,--as-needed
"${CROSS}strip" "$TMP/imp/svc/impostor"
cat > "$TMP/imp/svc/services.json" <<JSON
{"id": "$name", "description": "E2E impostor", "engine": "native", "executable": "impostor", "services": [{"name": "$name"}]}
JSON
cp "$TMP/icon.png" "$TMP/imp/app/icon.png"
echo '<!doctype html><html><body></body></html>' > "$TMP/imp/app/index.html"
cat > "$TMP/imp/app/appinfo.json" <<JSON
{"id": "org.webosbrew.hcfixture.native", "version": "1.0.0", "vendor": "webOS Homebrew", "type": "web", "main": "index.html",
 "title": "HC Fixture native impostor", "icon": "icon.png", "visible": false}
JSON
ares-package --outdir "$OUT" "$TMP/imp/app" "$TMP/imp/svc" >/dev/null
echo "built $OUT/org.webosbrew.hcfixture.native_1.0.0_arm.ipk"
