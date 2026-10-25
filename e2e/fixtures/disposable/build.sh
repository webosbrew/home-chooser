#!/bin/sh
# usage: build.sh <outdir>
# Packages the disposable app of the launcher-API check (homechooser_e2e.py --api-check) with ares-package into
#   <outdir>/org.webosbrew.hcfixture.disposable_1.0.0_all.ipk
# An ordinary web app: no "home" category and no requiredPermissions (HC_FIXTURE_PERMISSION is ignored), so it
# is the caller without the permission, the app closeApp closes, and the target of uninstall.
# Offline: python3 stdlib + ares-package only.
set -eu
[ $# -eq 1 ] || { echo "usage: $0 <outdir>" >&2; exit 2; }
mkdir -p "$1"
OUT=$(cd "$1" && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
id=org.webosbrew.hcfixture.disposable
stage=$TMP/$id
mkdir -p "$stage"

python3 -I - "$stage" "$id" <<'PY'
import json, struct, sys, zlib
stage, appid = sys.argv[1:3]
w = h = 80
rows = b''.join(b'\0' + bytes((120, 60, 20)) * w for _ in range(h))
def chunk(t, d):
    c = struct.pack('>I', len(d)) + t + d
    return c + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
open(stage + '/icon.png', 'wb').write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
    + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b''))
open(stage + '/index.html', 'w').write('<!doctype html><html><body style="margin:0;background:#783c14"></body></html>\n')
json.dump({"id": appid, "version": "1.0.0", "vendor": "webOS Homebrew", "type": "web", "main": "index.html",
           "title": "HC Fixture disposable", "icon": "icon.png", "visible": True},
          open(stage + '/appinfo.json', 'w'), indent=2)
PY
ares-package --outdir "$OUT" "$stage" >/dev/null
echo "built $OUT/${id}_1.0.0_all.ipk"
