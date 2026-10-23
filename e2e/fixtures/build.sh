#!/bin/sh
# usage: build.sh [outdir]   (default: <repo>/build/fixtures)
# Builds the six launcher fixtures: {native,qml,web} x {normal,overlay}. Offline.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${1:-$HERE/../../build/fixtures}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
rm -f "$OUT"/org.webosbrew.hcfixture.*.ipk
for t in native qml web; do
	sh "$HERE/$t/build.sh" "$OUT"
done
n=$(ls "$OUT"/org.webosbrew.hcfixture.*.ipk | wc -l)
[ "$n" -eq 6 ] || { echo "expected 6 ipks, found $n" >&2; exit 1; }
echo "6 fixtures in $OUT"
