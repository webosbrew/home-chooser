#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# usage: HC_FIXTURE_VERSION=1.0.1 build.sh <outdir> (default 1.0.0)
# Packages both web fixture launchers and the demo with ares-package into
#   <outdir>/org.webosbrew.hcfixture.web.normal_1.0.0_all.ipk
#   <outdir>/org.webosbrew.hcfixture.web.overlay_1.0.0_all.ipk
#   <outdir>/org.webosbrew.hcfixture.web.demo_1.0.0_all.ipk   (examples/web-launcher, id and title replaced)
# Offline: python3 stdlib + ares-package only.
set -eu
[ $# -eq 1 ] || { echo "usage: $0 <outdir>" >&2; exit 2; }
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$1"
OUT=$(cd "$1" && pwd)
VERSION=${HC_FIXTURE_VERSION:-1.0.0}
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

python3 -I - "$TMP/icon.png" "$TMP/oversize.png" <<'PY'
import random, struct, sys, zlib
w = h = 80
rows = b''
for y in range(h):
    row = b'\0'
    for x in range(w):
        edge = x < 4 or y < 4 or x >= w - 4 or y >= h - 4
        row += bytes((200, 200, 200) if edge else (20, 120, 60))
    rows += row
def chunk(t, d):
    c = struct.pack('>I', len(d)) + t + d
    return c + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
open(sys.argv[1], 'wb').write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
    + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b''))
# A valid but deliberately oversized icon exercises getAppIcon's distinct limit error.
w = h = 320
rng = random.Random(7)
rows = b''.join(b'\0' + bytes(rng.getrandbits(8) for _ in range(w * 3)) for _ in range(h))
open(sys.argv[2], 'wb').write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
    + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b''))
PY

for v in normal overlay; do
	id=org.webosbrew.hcfixture.web.$v
	stage=$TMP/$id
	mkdir -p "$stage"
	cp "$TMP/icon.png" "$stage/icon.png"
	cp "$TMP/oversize.png" "$stage/oversize.png"
	# The permission-declaring (--api-check) build also holds the page probe (probe.js, see its header); the plain
	# fixtures stay inert.
	probe=
	if [ -n "${HC_FIXTURE_PERMISSION:-}" ]; then
		cp "$HERE/../../../api/client/homechooser.js" "$HERE/probe.js" "$stage/"
		probe='<script src="homechooser.js"></script><script src="probe.js"></script>'
	fi
	sed -e "s/@VARIANT@/$v/g" -e "s|@PROBE@|$probe|" "$HERE/index.html" > "$stage/index.html"
	python3 -I - "$stage/appinfo.json" "$id" "$v" <<'PY'
import json, os, sys
dst, appid, v = sys.argv[1:4]
d = {"id": appid, "version": os.environ.get("HC_FIXTURE_VERSION", "1.0.0"), "vendor": "webOS Homebrew", "type": "web",
     "main": "index.html", "title": "HC Fixture web " + v, "icon": "icon.png",
     "largeIcon": "oversize.png",
     "appCategory": ["home"], "visible": True}
if os.environ.get("HC_FIXTURE_PERMISSION"):
    d["requiredPermissions"] = [os.environ["HC_FIXTURE_PERMISSION"]]
if v == "overlay":
    d["transparent"] = True
    d["defaultWindowType"] = "overlay"
json.dump(d, open(dst, 'w'), indent=2)
PY
	ares-package --outdir "$OUT" "$stage" >/dev/null
	echo "built $OUT/${id}_${VERSION}_all.ipk"
done

# The demo: examples/web-launcher as it is, with the fixture's id, title and version. It always
# declares the launcher API permission (it needs listApps), whatever HC_FIXTURE_PERMISSION says.
id=org.webosbrew.hcfixture.web.demo
stage=$TMP/$id
mkdir -p "$stage"
for f in "$HERE/../../../examples/web-launcher/"*; do
	case $f in *.md) ;; *) cp "$f" "$stage/" ;; esac
done
python3 -I - "$stage/appinfo.json" "$id" <<'PY'
import json, os, sys
dst, appid = sys.argv[1:3]
d = json.load(open(dst))
d.update(id=appid, title="HC Fixture web demo", version=os.environ.get("HC_FIXTURE_VERSION", "1.0.0"))
json.dump(d, open(dst, 'w'), indent=2)
PY
ares-package --outdir "$OUT" "$stage" >/dev/null
echo "built $OUT/${id}_${VERSION}_all.ipk"
