#!/bin/sh
# Round trip of the Settings.qml diff on all four releases. The stock file is not in the
# repository, so a stand-in is rebuilt from the diff (its context and removed lines at their
# line numbers, filler elsewhere). It checks that the diff applies with GNU patch and busybox
# patch, that the placeholder line is there and is replaced by the worker's substitution, that a
# patch -R gives the stand-in back, and that qmllint (if installed) accepts the result.
# usage: test-settings-diff.sh
HERE=$(cd "$(dirname "$0")" && pwd)
PASS=0
FAIL=0
check() { desc=$1; shift; if "$@"; then PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); echo "FAIL: $desc"; fi; }

T=$(mktemp -d)
REL=usr/lib/qml/WebOSCompositorBase/global/Settings.qml
DIFFNAME=WebOSCompositorBase_global_Settings.qml.diff

for r in 6.6.0 7.6.0 8.3.0 9.2.1; do
    d=$HERE/$r/$DIFFNAME
    w=$T/$r
    mkdir -p "$w/stock/$(dirname "$REL")"
    awk '
        /^@@ / { split($2, a, ","); n = substr(a[1], 2) + 0; next }
        /^(---|\+\+\+) / && !n { next }
        /^\+/ { next }
        /^[ -]/ { line[n++] = substr($0, 2); if (n > max) max = n }
        END { for (i = 1; i <= max + 2; i++) print (i in line) ? line[i] : "// filler " i }
    ' "$d" > "$w/stock/$REL"
    check "$r: the diff has the placeholder line" grep -qF 'readonly property string configuredHomeAppId: "@OPENHOUSE_HOME@"' "$d"
    check "$r: the diff is the same as 6.6.0's" cmp -s "$d" "$HERE/6.6.0/$DIFFNAME"
    # the stand-in is not valid QML (its context cuts functions short), so lint what the diff adds
    if command -v qmllint > /dev/null 2>&1; then
        { echo 'import QtQuick 2.4'; echo 'Item {'; grep '^+[^+]' "$d" | grep -v '^+import ' | cut -c2- |
            sed 's|configuredHomeAppId: "[^"]*"|configuredHomeAppId: "my.home"|'; echo '}'; } > "$w/added.qml"
        check "$r: qmllint accepts the added code" qmllint "$w/added.qml"
    fi
    for tool in "patch" "busybox patch"; do
        t=$w/$(echo "$tool" | tr -d ' ')
        mkdir -p "$t/$(dirname "$REL")"
        cp "$w/stock/$REL" "$t/$REL"
        check "$r: $tool applies" sh -c "cd '$t' && $tool -p0 < '$d' > /dev/null"
        check "$r: $tool leaves no reject" test ! -e "$t/$REL.rej"
        check "$r: $tool: placeholder present after patch" grep -qF 'configuredHomeAppId: "@OPENHOUSE_HOME@"' "$t/$REL"
        # the substitution the worker makes
        sed 's|configuredHomeAppId: "[^"]*"|configuredHomeAppId: "my.home-1_x"|' "$t/$REL" > "$t/new"
        check "$r: $tool: substituted" grep -qF 'configuredHomeAppId: "my.home-1_x"' "$t/new"
        check "$r: $tool: placeholder gone" sh -c "! grep -qF @OPENHOUSE_HOME@ '$t/new'"
        check "$r: $tool: only the placeholder line changed" test "$(diff "$t/$REL" "$t/new" | grep -c '^<')" = 1
        check "$r: $tool -R restores the stand-in" sh -c "cd '$t' && cp '$REL' x.qml && $tool -p0 -R < '$d' > /dev/null && cmp -s '$REL' '$w/stock/$REL'"
    done
done

rm -rf "${T:?}"
echo "$PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
