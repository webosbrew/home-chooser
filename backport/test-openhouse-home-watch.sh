#!/bin/sh
# Tests for openhouse-home-watch with a stub luna-send that plays a scripted subscription stream
# and records the calls, a fake clock and a fake sleep.
# usage: test-openhouse-home-watch.sh [shell]     (shell defaults to sh, e.g. "busybox sh")
SH=${1:-sh}
HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT=$HERE/openhouse-home-watch
PASS=0
FAIL=0

check() { d=$1; shift; if "$@"; then PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); echo "FAIL: $d"; fi; }
has() { grep -qF -- "$1" "$2"; }
hasnt() { ! grep -qF -- "$1" "$2"; }
count() { grep -c -- "$1" "$2"; }
cleanup() { rm -rf "${T:?}"; }

# setup <major> <choice file content (printf format)>
setup() {
    T=$(mktemp -d)
    mkdir -p "$T/bin" "$T/run"
    printf "VERSION_ID=\"$1.0.0\"\n" > "$T/os-release"
    printf "$2" > "$T/home"
    echo 1000 > "$T/clock"
    : > "$T/calls"
    : > "$T/stream"
    echo '{"returnValue":true}' > "$T/r-setdefault"
    echo '{"returnValue":true}' > "$T/r-setsys"
    echo '{"returnValue":true,"settings":{"defaultApps":{"kidsApp":"k.app"}}}' > "$T/record"
    echo '{"returnValue":false,"errorText":"There is no matched result from DB"}' > "$T/profiles"
    cat > "$T/bin/luna-send" <<STUB
#!/bin/sh
T=$T
if [ "\$1" = -i ]; then
    echo "\$(cat \$T/clock) SUB \$2 \$3" >> \$T/calls
    while IFS= read -r l; do
        if [ "\$l" = RMCHOICE ]; then rm -f \$T/home; else printf '%s\n' "\$l"; fi
    done < \$T/stream
    exit 0
fi
echo "\$(cat \$T/clock) CALL \$3 \$4" >> \$T/calls
case "\$3" in
    */getSystemSettings) cat \$T/record ;;
    */setDefaultApp) cat \$T/r-setdefault ;;
    */setSystemSettings) cat \$T/r-setsys ;;
    */getProfiles) cat \$T/profiles ;;
    */debug/rescan) echo '{"returnValue":true}' ;;
esac
STUB
    printf '#!/bin/sh\ncat %s/clock\n' "$T" > "$T/bin/clock"
    printf '#!/bin/sh\necho "SLEEP $1" >> %s/calls\nc=$(cat %s/clock); echo $((c + $1)) > %s/clock\n' "$T" "$T" "$T" > "$T/bin/sleep"
    chmod +x "$T/bin/"*
    export OPENHOUSE_CHOICE="$T/home" OPENHOUSE_LUNA_SEND="$T/bin/luna-send" OPENHOUSE_WATCH_LOG="$T/log" \
           OPENHOUSE_OSRELEASE="$T/os-release" OPENHOUSE_OVERRIDE="$T/override/o.json" \
           OPENHOUSE_CLOCK="$T/bin/clock" OPENHOUSE_SLEEP="$T/bin/sleep" OPENHOUSE_TMP="$T/run"
}
run() { $SH "$SCRIPT" > "$T/out" 2>&1; echo $? > "$T/rc"; }
rc() { [ "$(cat "$T/rc")" = "$1" ]; }
writes() { grep -c ' CALL .*/set\(DefaultApp\|SystemSettings\)' "$T/calls"; }
# one reply per line
reply() { printf '{"returnValue":true,"subscribed":true,"settings":{"defaultApps":%s}}\n' "$1" >> "$T/stream"; }

echo "== record already holds the choice ($SH)"
setup 8 'my.home\n'
reply '{"home":"my.home","kidsApp":"k.app"}'
run
check "subscription ends: exit 1" rc 1
check "subscribed with the settings payload" has 'SUB luna://com.webos.settingsservice/getSystemSettings {"category":"general","keys":["defaultApps"],"subscribe":true}' "$T/calls"
check "no write" test "$(writes)" = 0
cleanup

echo "== home missing, webOS 8: merged setSystemSettings"
setup 8 'my.home\n'
reply '{"kidsApp":"k.app"}'
run
check "one write" test "$(writes)" = 1
check "merged payload" has 'CALL luna://com.webos.settingsservice/setSystemSettings {"category":"general","settings":{"defaultApps":{"home":"my.home","kidsApp":"k.app"}}}' "$T/calls"
check "no setDefaultApp on 8" hasnt setDefaultApp "$T/calls"
check "logged" has "writing 'my.home'" "$T/log"
cleanup

echo "== record absent entirely"
setup 6 'my.home\n'
printf '{"returnValue":false,"errorText":"There is no matched result from DB"}\n' >> "$T/stream"
cp "$T/stream" "$T/record"
run
check "creates the record" has '"settings":{"defaultApps":{"home":"my.home"}}' "$T/calls"
cleanup

echo "== home different, webOS 9: setDefaultApp"
setup 9 'my.home\n'
reply '{"home":"com.webos.app.home","kidsApp":"k.app"}'
run
check "setDefaultApp payload" has 'CALL luna://com.webos.applicationManager/setDefaultApp {"category":"home","appId":"my.home"}' "$T/calls"
check "no setSystemSettings" hasnt setSystemSettings "$T/calls"
cleanup

echo "== setDefaultApp unknown method falls back to the merged write"
setup 11 'my.home\n'
reply '{"home":"other","kidsApp":"k.app"}'
echo '{"returnValue":false,"errorText":"Unknown method \"setDefaultApp\" for category \"/\""}' > "$T/r-setdefault"
echo '{"returnValue":true,"settings":{"defaultApps":{"home":"other","kidsApp":"k.app"}}}' > "$T/record"
run
check "tried setDefaultApp" has setDefaultApp "$T/calls"
check "merged write replaces home" has '{"defaultApps":{"home":"my.home","kidsApp":"k.app"}}' "$T/calls"
cleanup

echo "== pretty-printed reply and a record that is a JSON string"
setup 8 'my.home\n'
cat >> "$T/stream" <<'JSON'
{
    "returnValue": true,
    "settings": {
        "defaultApps": "{\"home\":\"other\",\"kidsApp\":\"k.app\"}"
    }
}
JSON
cp "$T/stream" "$T/record"
run
check "one write" test "$(writes)" = 1
check "merged payload" has '{"defaultApps":{"home":"my.home","kidsApp":"k.app"}}' "$T/calls"
cleanup

echo "== replies that are not the record are ignored"
setup 8 'my.home\n'
printf 'noise\n{"returnValue":true,"subscribed":true}\n' >> "$T/stream"
run
check "no write" test "$(writes)" = 0
cleanup

echo "== unreadable record is not overwritten"
setup 8 'my.home\n'
reply '{"home":"other","nested":{"a":1}}'
cp "$T/stream" "$T/record"
run
check "no setSystemSettings" hasnt setSystemSettings "$T/calls"
check "logged" has 'not writing' "$T/log"
cleanup

echo "== storm: one write per interval, doubling, capped"
setup 8 'my.home\n'
i=0; while [ $i -lt 10 ]; do reply '{"home":"other"}'; i=$((i + 1)); done
echo '{"returnValue":true,"settings":{"defaultApps":{"home":"other"}}}' > "$T/record"
run
check "ten writes for ten mismatches" test "$(writes)" = 10
grep ' CALL .*/setSystemSettings' "$T/calls" | cut -d' ' -f1 > "$T/times"
check "first at 1000" test "$(sed -n 1p "$T/times")" = 1000
check "second 10 s later" test "$(sed -n 2p "$T/times")" = 1010
check "third 20 s later" test "$(sed -n 3p "$T/times")" = 1030
check "fourth 40 s later" test "$(sed -n 4p "$T/times")" = 1070
check "fifth 80 s later" test "$(sed -n 5p "$T/times")" = 1150
check "sixth 160 s later" test "$(sed -n 6p "$T/times")" = 1310
check "seventh capped at 300 s" test "$(sed -n 7p "$T/times")" = 1610
check "eighth capped at 300 s" test "$(sed -n 8p "$T/times")" = 1910
check "ninth capped at 300 s" test "$(sed -n 9p "$T/times")" = 2210
cleanup

echo "== a stale reply during the interval does not write again"
setup 8 'my.home\n'
reply '{"home":"other"}'
reply '{"home":"other"}'
echo '{"returnValue":true,"settings":{"defaultApps":{"home":"my.home","kidsApp":"k.app"}}}' > "$T/record"
run
check "one write" test "$(writes)" = 1
check "no sleeping" hasnt SLEEP "$T/calls"
cleanup

echo "== choice file forms"
for form in 'my.home' 'my.home\n' 'my.home\r\n' '  my.home  \n' '\t my.home\r\n\n' '\n\nmy.home'; do
    setup 8 "$form"
    reply '{"home":"other"}'
    run
    check "id trimmed from '$form'" has '"home":"my.home","kidsApp"' "$T/calls"
    cleanup
done
setup 8 ''
reply '{"home":"other"}'
run
check "empty file: exit 0" rc 0
check "empty file: nothing subscribed" hasnt SUB "$T/calls"
cleanup
setup 8 '  \r\n'
reply '{"home":"other"}'
run
check "blank file: exit 0, no calls" test ! -s "$T/calls"
cleanup
for bad in 'a b' 'x"y' 'x;y' 'a/b' 'a$b' '$(id)' 'a\nb'; do
    setup 8 "$bad"
    reply '{"home":"other"}'
    run
    check "invalid id '$bad': exit 0" rc 0
    check "invalid id '$bad': nothing subscribed" hasnt SUB "$T/calls"
    check "invalid id '$bad': logged" has 'invalid' "$T/log"
    cleanup
done
setup 8 'my.home\n'
rm -f "${T:?}/home"
run
check "no choice file: exit 0" rc 0
check "no choice file: no calls" test ! -s "$T/calls"
cleanup

echo "== choice file removed while watching"
setup 8 'my.home\n'
reply '{"home":"my.home"}'
echo RMCHOICE >> "$T/stream"
reply '{"home":"other"}'
run
check "exit 0" rc 0
check "no write" test "$(writes)" = 0
check "logged" has 'choice file is gone' "$T/log"
cleanup

echo "== profile override, webOS 10 and later"
setup 10 'my.home\n'
reply '{"home":"my.home"}'
cat > "$T/profiles" <<'JSON'
{"returnValue":true,"profiles":{"defaultApps":{"_source":"x","categories":{"home":"com.webos.app.home","kids":"k.app"},"other":{"a":[1,2],"b":"c"},"_hidden":1}}}
JSON
run
check "profile read" has 'CALL luna://com.webos.service.systemprofile/getProfiles {"names":["defaultApps"]}' "$T/calls"
check "override written" has '{"defaultApps":{"categories":{"home":"my.home","kids":"k.app"},"other":{"a":[1,2],"b":"c"}}}' "$T/override/o.json"
check "rescan" has '/debug/rescan' "$T/calls"
check "no record write when it matches" test "$(writes)" = 0
cleanup
setup 11 'my.home\n'
reply '{"home":"my.home"}'
mkdir -p "$T/override"
echo '{"defaultApps":{"categories":{"home":"my.home"}}}' > "$T/override/o.json"
run
check "good override: no getProfiles" hasnt getProfiles "$T/calls"
check "good override: no rescan" hasnt rescan "$T/calls"
cleanup
setup 11 'my.home\n'
reply '{"home":"other"}'
mkdir -p "$T/override"
echo '{"defaultApps":{"categories":{"home":"stale.app"}}}' > "$T/override/o.json"
echo '{"returnValue":true,"defaultApps":{"categories":{"home":"stale.app"}}}' > "$T/profiles"
run
check "wrong override rewritten" has '"home":"my.home"' "$T/override/o.json"
check "stale override rebuilt once, then fine after the write" test "$(count getProfiles "$T/calls")" = 1
cleanup
setup 10 'my.home\n'
reply '{"home":"my.home"}'
echo '{"returnValue":true,"profiles":{"defaultApps":{"x":1}}}' > "$T/profiles"
run
check "profile without categories: no override" test ! -f "$T/override/o.json"
check "logged" has 'override not written' "$T/log"
cleanup
setup 8 'my.home\n'
reply '{"home":"my.home"}'
run
check "webOS 8: no profile call" hasnt getProfiles "$T/calls"
cleanup

echo "== log is capped"
setup 8 'my.home\n'
reply '{"home":"my.home"}'
i=0; while [ $i -lt 400 ]; do echo "old line $i padding padding padding" >> "$T/log"; i=$((i + 1)); done
OPENHOUSE_LOG_MAX=2000 run
check "log trimmed" test "$(wc -l < "$T/log")" -lt 300
cleanup

echo "$PASS passed, $FAIL failed ($SH)"
[ "$FAIL" = 0 ]
