#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# usage: build.sh <outdir>
# Packages two ordinary web apps with ares-package into
#   <outdir>/org.webosbrew.hcfixture.disposable_1.0.0_all.ipk   the disposable app of the launcher-API check
#       (homechooser_e2e.py --api-check): the caller without the permission, the app closeApp closes, and the
#       target of uninstall.
#   <outdir>/org.webosbrew.hcfixture.first_1.0.0_all.ipk        the app step 3d opens from fixture:web.demo: its
#       title "0 HC Fixture first" sorts before the titles of the TV's own apps in the demo's order, so the
#       demo's first tile is this harmless page and not an LG app behind a first-use or EULA overlay.
# Neither has a "home" category or requiredPermissions (HC_FIXTURE_PERMISSION is ignored).
# Offline: python3 stdlib + ares-package only.
set -eu
[ $# -eq 1 ] || { echo "usage: $0 <outdir>" >&2; exit 2; }
mkdir -p "$1"
OUT=$(cd "$1" && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
build() {
	id=$1
	stage=$TMP/$id
	mkdir -p "$stage"
	python3 -I - "$stage" "$id" "$2" "$3" <<'PY'
import json, struct, sys, zlib
stage, appid, title, rgb = sys.argv[1:5]
colour = bytes.fromhex(rgb)
w = h = 80
rows = b''.join(b'\0' + colour * w for _ in range(h))
def chunk(t, d):
    c = struct.pack('>I', len(d)) + t + d
    return c + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
open(stage + '/icon.png', 'wb').write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
    + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b''))
open(stage + '/index.html', 'w').write('<!doctype html><html><body style="margin:0;background:#%s"></body></html>\n' % rgb)
json.dump({"id": appid, "version": "1.0.0", "vendor": "webOS Homebrew", "type": "web", "main": "index.html",
           "title": title, "icon": "icon.png", "visible": True},
          open(stage + '/appinfo.json', 'w'), indent=2)
PY
	ares-package --outdir "$OUT" "$stage" >/dev/null
	echo "built $OUT/${id}_1.0.0_all.ipk"
}

build org.webosbrew.hcfixture.disposable "HC Fixture disposable" 783c14
build org.webosbrew.hcfixture.first "0 HC Fixture first" 14503c
