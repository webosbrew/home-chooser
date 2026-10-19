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
    printf 'my.home\norg.webosbrew.openhouse\ncom.webos.app.home\n' > "$T/installed"
    : > "$T/mark"
    : > "$T/kfcalls"
    mkfifo "$T/appsfeed"
    mkdir -p "$T/app/backport/1.0" "$T/init.d"
    cp "$SCRIPT" "$T/app/backport/openhouse-home-watch"
    printf '#!/bin/sh\necho "keyfilter $* data=$OPENHOUSE_DATA from=$0" >> %s/kfcalls\n' "$T" > "$T/app/backport/openhouse-keyfilter"
    echo '{"release":"1.0","files":[]}' > "$T/app/backport/1.0/manifest.json"
    echo ' diff' > "$T/app/backport/1.0/x.diff"
    printf '#!/bin/sh\n' > "$T/app/backport/openhouse"
    touch "$T/flag"
    ln -s "$T/app/backport/openhouse" "$T/init.d/openhouse"
    : > "$T/calls"
    : > "$T/stream"
    echo '{"returnValue":true}' > "$T/r-setdefault"
    echo '{"returnValue":true}' > "$T/r-setsys"
    echo '{"returnValue":true,"settings":{"defaultApps":{"kidsApp":"k.app"}}}' > "$T/record"
    echo '{"returnValue":false,"errorText":"There is no matched result from DB"}' > "$T/profiles"
    cat > "$T/bin/luna-send" <<'STUB'
#!/bin/sh
T=$T
if [ "$1" = -i ]; then
    echo "$(cat $T/clock) SUB $2 $3" >> $T/calls
    case "$2" in
        */listApps) exec 5<> $T/appsfeed; exec cat <&5 ;;
    esac
    while IFS= read -r l; do
        case "$l" in
            RMCHOICE) rm -f $T/home ;;
            "UNINSTALL "*)
                id=${l#UNINSTALL }
                grep -vxF "$id" $T/installed > $T/installed.new; mv $T/installed.new $T/installed
                [ "$id" = org.webosbrew.openhouse ] && rm -rf $T/app ;;
            "INSTALL "*) echo "${l#INSTALL }" >> $T/installed ;;
            "CHOICE "*) echo "${l#CHOICE }" > $T/home ;;
            "FLAP "*) set=${l#FLAP }; echo "${set#* }" > "$T/flap-${set% *}" ;;
            APPS)
                n=$(wc -l < $T/mark)
                echo '{"returnValue":true,"subscribed":true,"apps":[]}' > $T/appsfeed
                i=0
                while [ $(wc -l < $T/mark) -le $n ] && [ $i -lt 100 ]; do sleep 0.1; i=$((i + 1)); done ;;
            TIMER)
                i=0
                while ! grep -qx timer $T/mark && [ $i -lt 100 ]; do sleep 0.1; i=$((i + 1)); done ;;
            *) printf '%s\n' "$l" ;;
        esac
    done < $T/stream
    exit 0
fi
echo "$(cat $T/clock) CALL $3 $4" >> $T/calls
case "$3" in
    */getAppInfo)
        [ -f $T/sam-silent ] && exit 0
        id=$(echo "$4" | sed 's/.*"id":"\([^"]*\)".*/\1/')
        if [ -s "$T/flap-$id" ]; then
            n=$(cat "$T/flap-$id")
            if [ "$n" -gt 0 ]; then
                echo $((n - 1)) > "$T/flap-$id"
                echo '{"returnValue":false,"errorCode":-1,"errorText":"Invalid appId specified OR Unsupported Application Type"}'
                exit 0
            fi
        fi
        if grep -qxF "$id" $T/installed; then
            echo '{"returnValue":true,"appInfo":{"id":"'$id'"}}'
        else
            echo '{"returnValue":false,"errorCode":-1,"errorText":"Invalid appId specified OR Unsupported Application Type"}'
        fi ;;
    */getSystemSettings) cat $T/record ;;
    */setDefaultApp)
        cat $T/r-setdefault
        if [ -f $T/live ]; then
            id=$(echo "$4" | sed 's/.*"appId":"\([^"]*\)".*/\1/')
            if grep -q '"home"' $T/record; then
                sed 's/"home":"[^"]*"/"home":"'"$id"'"/' $T/record > $T/record.new
            else
                sed 's/"defaultApps":{/"defaultApps":{"home":"'"$id"'",/' $T/record > $T/record.new
            fi
            mv $T/record.new $T/record
        fi ;;
    */setSystemSettings)
        cat $T/r-setsys
        [ -f $T/live ] && echo "$4" | sed 's/^{"category":"general","settings":\(.*\)}$/{"returnValue":true,"settings":\1}/' > $T/record ;;
    */getProfiles) cat $T/profiles ;;
    */debug/rescan) echo '{"returnValue":true}' ;;
esac
STUB
    sed -i "s|^T=.*|T=$T|" "$T/bin/luna-send"
    printf '#!/bin/sh\ncat %s/clock\n' "$T" > "$T/bin/clock"
    printf '#!/bin/sh\necho "SLEEP $1" >> %s/calls\nc=$(cat %s/clock); echo $((c + $1)) > %s/clock\n' "$T" "$T" "$T" > "$T/bin/sleep"
    chmod +x "$T/bin/"*
    export OPENHOUSE_CHOICE="$T/home" OPENHOUSE_LUNA_SEND="$T/bin/luna-send" OPENHOUSE_WATCH_LOG="$T/log" \
           OPENHOUSE_OSRELEASE="$T/os-release" OPENHOUSE_OVERRIDE="$T/override/o.json" \
           OPENHOUSE_CLOCK="$T/bin/clock" OPENHOUSE_SLEEP="$T/bin/sleep" OPENHOUSE_TMP="$T/run" \
           OPENHOUSE_RUNTIME="$T/rt" OPENHOUSE_SHELL="$SH" OPENHOUSE_APPDIR="$T/app" \
           OPENHOUSE_KEYFILTER_FLAG="$T/flag" OPENHOUSE_INITD="$T/init.d/openhouse" \
           OPENHOUSE_RECHECK=100000 OPENHOUSE_CONFIRM_DELAY=0 OPENHOUSE_CHECK_MARK="$T/mark"
}
run() { $SH "$T/app/backport/openhouse-home-watch" > "$T/out" 2>&1; echo $? > "$T/rc"; }
rc() { [ "$(cat "$T/rc")" = "$1" ]; }
writes() { grep -c ' CALL .*/set\(DefaultApp\|SystemSettings\)' "$T/calls"; }
mktimeout() { # mktimeout <new|old> <path>: a timeout that accepts only one form, and records its use
    if [ "$1" = new ]; then
        printf '#!/bin/sh\necho "timeout $*" >> %s.log\n[ "$1" = -t ] && exit 125\nshift\nexec "$@"\n' "$2" > "$2"
    else
        printf '#!/bin/sh\necho "timeout $*" >> %s.log\n[ "$1" = -t ] || exit 125\nshift 2\nexec "$@"\n' "$2" > "$2"
    fi
    chmod +x "$2"
}
# live <record json>: the stub keeps the record in step with what is written to it
live() { touch "$T/live"; echo '{"returnValue":true,"settings":{"defaultApps":'"$1"'}}' > "$T/record"; }
nohome() { ! grep -q '"home"' "$T/record"; }
kfn() { grep -c "$1" "$T/kfcalls"; }
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

echo "== chosen app uninstalled and installed again, webOS 6 to 8"
for major in 6 8; do
    setup $major 'my.home\n'
    live '{"home":"my.home","kidsApp":"k.app"}'
    reply '{"home":"my.home","kidsApp":"k.app"}'
    echo 'UNINSTALL my.home' >> "$T/stream"
    echo APPS >> "$T/stream"
    reply '{"kidsApp":"k.app"}'
    echo 'INSTALL my.home' >> "$T/stream"
    echo APPS >> "$T/stream"
    run
    check "$major: exit 1 (subscription ended)" rc 1
    check "$major: home removed, other entries kept" has 'CALL luna://com.webos.settingsservice/setSystemSettings {"category":"general","settings":{"defaultApps":{"kidsApp":"k.app"}}}' "$T/calls"
    check "$major: no home written while the app is missing, then once when it is back" test "$(writes)" = 2
    check "$major: written again with home" has '{"defaultApps":{"home":"my.home","kidsApp":"k.app"}}' "$T/calls"
    check "$major: backport refreshed to stock" test "$(kfn 'refresh stock')" = 1
    check "$major: backport refreshed to the choice after" test "$(kfn 'refresh data')" -ge 2
    check "$major: choice file kept" test -f "$T/home"
    check "$major: logged" has 'my.home is not installed, stock Home in use' "$T/log"
    check "$major: logged return" has 'my.home is installed again' "$T/log"
    check "$major: no setDefaultApp" hasnt setDefaultApp "$T/calls"
    cleanup
done

echo "== chosen app uninstalled and installed again, webOS 5"
setup 5 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
printf 'my.home\norg.webosbrew.openhouse\ncom.webos.app.home\ncom.webos.app.fullhome\n' > "$T/installed"
reply '{"home":"my.home","kidsApp":"k.app"}'
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
reply '{"home":"com.webos.app.fullhome","kidsApp":"k.app"}'
echo 'INSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "stock fullhome named while missing" has '{"defaultApps":{"home":"com.webos.app.fullhome","kidsApp":"k.app"}}' "$T/calls"
check "re-applied after" has '{"defaultApps":{"home":"my.home","kidsApp":"k.app"}}' "$T/calls"
check "two writes" test "$(writes)" = 2
check "no key filter on 5" test ! -s "$T/kfcalls"
cleanup
setup 5 'my.home\n'
live '{"home":"my.home"}'
reply '{"home":"my.home"}'
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "no fullhome installed: com.webos.app.home" has '{"defaultApps":{"home":"com.webos.app.home"}}' "$T/calls"
cleanup

echo "== chosen app uninstalled and installed again, webOS 9"
setup 9 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
reply '{"home":"my.home","kidsApp":"k.app"}'
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
echo 'INSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "removal is a setSystemSettings write" has '{"defaultApps":{"kidsApp":"k.app"}}' "$T/calls"
check "return uses setDefaultApp" has 'setDefaultApp {"category":"home","appId":"my.home"}' "$T/calls"
check "one setDefaultApp" test "$(count setDefaultApp "$T/calls")" = 1
cleanup

echo "== chosen app uninstalled and installed again, webOS 10 and later"
for major in 10 11; do
    setup $major 'my.home\n'
    live '{"home":"my.home","kidsApp":"k.app"}'
    mkdir -p "$T/override"
    echo '{"defaultApps":{"categories":{"home":"my.home"}}}' > "$T/override/o.json"
    echo '{"returnValue":true,"profiles":{"defaultApps":{"categories":{"home":"com.webos.app.home","kids":"k.app"}}}}' > "$T/profiles"
    reply '{"home":"my.home","kidsApp":"k.app"}'
    echo 'UNINSTALL my.home' >> "$T/stream"
    echo APPS >> "$T/stream"
    reply '{"kidsApp":"k.app"}'
    echo 'INSTALL my.home' >> "$T/stream"
    echo APPS >> "$T/stream"
    run
    check "$major: removal wrote the record without home" has '{"defaultApps":{"kidsApp":"k.app"}}' "$T/calls"
    check "$major: rescan after removing the override" test "$(count debug/rescan "$T/calls")" -ge 2
    check "$major: override rebuilt when it came back" has '"home":"my.home"' "$T/override/o.json"
    check "$major: setDefaultApp when it came back" has 'setDefaultApp {"category":"home","appId":"my.home"}' "$T/calls"
    check "$major: no key filter" test ! -s "$T/kfcalls"
    # the order: override removed (rescan) before the record is written
    first=$(grep -n 'debug/rescan\|setSystemSettings' "$T/calls" | head -n 1)
    check "$major: rescan before the record write" test "${first#*CALL }" != "${first}" -a "$(echo "$first" | grep -c rescan)" = 1
    cleanup
done
setup 11 'my.home\n'
live '{"home":"my.home"}'
mkdir -p "$T/override"
echo '{"defaultApps":{"categories":{"home":"my.home"}}}' > "$T/override/o.json"
reply '{"home":"my.home"}'
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "override file removed" test ! -f "$T/override/o.json"
check "nothing re-asserted while missing" test "$(writes)" = 1
cleanup

echo "== chosen app already missing at start"
setup 10 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
mkdir -p "$T/override"
echo '{"defaultApps":{"categories":{"home":"my.home"}}}' > "$T/override/o.json"
grep -vx my.home "$T/installed" > "$T/i.new"; mv "$T/i.new" "$T/installed"
reply '{"home":"my.home","kidsApp":"k.app"}'
run
check "reverted at start" test ! -f "$T/override/o.json"
check "one write, the removal" test "$(writes)" = 1
check "no getProfiles (override not rebuilt)" hasnt getProfiles "$T/calls"
cleanup

echo "== a transient absence is not acted on"
setup 8 'my.home\n'
live '{"home":"my.home"}'
reply '{"home":"my.home"}'
echo 'FLAP my.home 1' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "no write" test "$(writes)" = 0
check "checked twice" test "$(count 'getAppInfo {"id":"my.home"}' "$T/calls")" = 3
check "no revert logged" hasnt 'not installed' "$T/log"
cleanup
setup 8 'my.home\n'
live '{"home":"my.home"}'
reply '{"home":"my.home"}'
echo 'FLAP org.webosbrew.openhouse 1' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "Open House answered again: no restore" has 'Open House is back' "$T/log"
check "choice file kept" test -f "$T/home"
check "link kept" test -L "$T/init.d/openhouse"
check "no write" test "$(writes)" = 0
cleanup

echo "== getAppInfo does not answer"
setup 8 'my.home\n'
live '{"home":"my.home"}'
touch "$T/sam-silent"
reply '{"home":"my.home"}'
echo APPS >> "$T/stream"
run
check "nothing reverted" test "$(writes)" = 0
check "logged" has 'did not answer' "$T/log"
check "choice kept" test -f "$T/home"
cleanup

echo "== the periodic check notices a removal without a listApps message"
setup 8 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
reply '{"home":"my.home","kidsApp":"k.app"}'
echo 'UNINSTALL my.home' >> "$T/stream"
echo TIMER >> "$T/stream"
OPENHOUSE_RECHECK=1 run
check "reverted by the timer" has '{"defaultApps":{"kidsApp":"k.app"}}' "$T/calls"
check "mark says timer" has timer "$T/mark"
cleanup

echo "== choice changed while the old one is missing"
setup 8 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
printf 'my.home\nother.home\norg.webosbrew.openhouse\n' > "$T/installed"
reply '{"home":"my.home"}'
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
echo 'CHOICE other.home' >> "$T/stream"
reply '{"kidsApp":"k.app"}'
run
check "the new choice is written at once" has '{"defaultApps":{"home":"other.home","kidsApp":"k.app"}}' "$T/calls"
check "two writes: the removal and the new choice" test "$(writes)" = 2
check "backport refreshed for the new choice" test "$(kfn 'refresh data')" = 2
cleanup

echo "== Open House uninstalled"
for major in 6 9; do
    setup $major 'my.home\n'
    live '{"home":"my.home","kidsApp":"k.app"}'
    reply '{"home":"my.home","kidsApp":"k.app"}'
    echo 'UNINSTALL org.webosbrew.openhouse' >> "$T/stream"
    echo APPS >> "$T/stream"
    reply '{"home":"other"}'
    run
    check "$major: exit 0" rc 0
    check "$major: home removed from the record" nohome
    check "$major: other entries kept" has 'kidsApp' "$T/record"
    check "$major: choice file removed" test ! -f "$T/home"
    check "$major: backport flag removed" test ! -f "$T/flag"
    check "$major: init.d link removed" test ! -L "$T/init.d/openhouse"
    check "$major: app directory is gone" test ! -d "$T/app"
    check "$major: key filter stop ran" has "stop data=$T/rt" "$T/kfcalls"
    check "$major: from the runtime copy" has "from=$T/rt/openhouse-keyfilter" "$T/kfcalls"
    check "$major: manifest copied to the runtime" test -f "$T/rt/1.0/manifest.json"
    check "$major: the watcher itself ran from the runtime" test -f "$T/rt/openhouse-home-watch"
    check "$major: logged" has 'stock Home restored' "$T/log"
    check "$major: nothing written after the restore" test "$(writes)" = 1
    cleanup
done
setup 8 'my.home\n'
printf '#!/bin/sh\n' > "$T/init.d/openhouse-keyfilter-x"
ln -s "$T/app/backport/openhouse" "$T/init.d/openhouse-keyfilter"
live '{"home":"my.home"}'
reply '{"home":"my.home"}'
echo 'UNINSTALL org.webosbrew.openhouse' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "the older keyfilter link goes too" test ! -L "$T/init.d/openhouse-keyfilter"
check "a regular file in init.d is left alone" test -f "$T/init.d/openhouse-keyfilter-x"
cleanup

echo "== Open House uninstalled, webOS 5"
setup 5 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
reply '{"home":"my.home","kidsApp":"k.app"}'
echo 'UNINSTALL org.webosbrew.openhouse' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "exit 0" rc 0
check "home names the stock launcher" has '"home":"com.webos.app.home"' "$T/record"
check "choice removed" test ! -f "$T/home"
check "link removed" test ! -L "$T/init.d/openhouse"
check "no key filter on 5" test ! -s "$T/kfcalls"
cleanup

echo "== Open House uninstalled, webOS 10 and later"
setup 11 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
mkdir -p "$T/override"
echo '{"defaultApps":{"categories":{"home":"my.home"}}}' > "$T/override/o.json"
reply '{"home":"my.home","kidsApp":"k.app"}'
echo 'UNINSTALL org.webosbrew.openhouse' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "exit 0" rc 0
check "override removed" test ! -f "$T/override/o.json"
check "rescanned" has debug/rescan "$T/calls"
check "home removed" nohome
check "choice, flag and link removed" test ! -f "$T/home" -a ! -f "$T/flag" -a ! -L "$T/init.d/openhouse"
check "no key filter on 11" test ! -s "$T/kfcalls"
cleanup

echo "== both removed, either order"
setup 8 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
reply '{"home":"my.home","kidsApp":"k.app"}'
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
echo 'UNINSTALL org.webosbrew.openhouse' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "chosen first: exit 0" rc 0
check "chosen first: one write in all" test "$(writes)" = 1
check "chosen first: no home" nohome
check "chosen first: second revert found nothing to do" has 'nothing to remove' "$T/log"
check "chosen first: all cleaned" test ! -f "$T/home" -a ! -f "$T/flag" -a ! -L "$T/init.d/openhouse"
cleanup
setup 8 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
reply '{"home":"my.home","kidsApp":"k.app"}'
echo 'UNINSTALL org.webosbrew.openhouse' >> "$T/stream"
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "both at once: exit 0" rc 0
check "both at once: one write" test "$(writes)" = 1
check "both at once: stock state, nothing left for a later removal to break" nohome
check "both at once: all cleaned" test ! -f "$T/home" -a ! -f "$T/flag" -a ! -L "$T/init.d/openhouse"
check "both at once: stop ran" has stop "$T/kfcalls"
cleanup
setup 8 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
reply '{"home":"my.home","kidsApp":"k.app"}'
echo 'UNINSTALL org.webosbrew.openhouse' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "Open House first: exit 0, then my.home may go any time" rc 0
check "Open House first: record is stock" nohome
check "Open House first: no choice for anything to enforce" test ! -f "$T/home"
cleanup

echo "== the restore runs from the runtime copy after the app is gone (unit restart)"
setup 8 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
reply '{"home":"my.home","kidsApp":"k.app"}'
run
check "first run staged the copies" test -f "$T/rt/openhouse-home-watch"
rm -rf "$T/app"
printf 'my.home\n' > "$T/home"
touch "$T/flag"
: > "$T/stream"
$SH "$T/rt/openhouse-home-watch" > "$T/out" 2>&1; echo $? > "$T/rc"
check "exit 0" rc 0
check "record is stock" nohome
check "choice removed" test ! -f "$T/home"
check "link removed" test ! -L "$T/init.d/openhouse"
check "stop from the runtime copy" has "from=$T/rt/openhouse-keyfilter" "$T/kfcalls"
cleanup

echo "== idempotent: a second restore changes nothing"
setup 8 'my.home\n'
live '{"kidsApp":"k.app"}'
rm -rf "$T/app"
$SH "$SCRIPT" > "$T/out" 2>&1; echo $? > "$T/rc"
check "runs from the repository copy, app dir missing: exit 0" rc 0
check "no write (nothing to remove)" test "$(writes)" = 0
cleanup

echo "== timeout in both forms (BusyBox before 1.30 wants -t)"
for form in new old none; do
    setup 8 'my.home\n'
    live '{"home":"my.home","kidsApp":"k.app"}'
    reply '{"home":"my.home","kidsApp":"k.app"}'
    echo 'UNINSTALL my.home' >> "$T/stream"
    echo APPS >> "$T/stream"
    if [ $form = none ]; then export OPENHOUSE_TIMEOUT=; else mktimeout $form "$T/bin/timeout"; export OPENHOUSE_TIMEOUT="$T/bin/timeout"; fi
    run
    unset OPENHOUSE_TIMEOUT
    check "$form: removal still detected and reverted" has '{"defaultApps":{"kidsApp":"k.app"}}' "$T/calls"
    case $form in
        new) check "new: called as timeout 10" has 'timeout 10 ' "$T/bin/timeout.log"; check "new: no -t call used" hasnt 'timeout -t 10' "$T/bin/timeout.log" ;;
        old) check "old: called as timeout -t 10" has 'timeout -t 10 ' "$T/bin/timeout.log" ;;
        none) check "none: no timeout used" test ! -f "$T/bin/timeout.log" ;;
    esac
    cleanup
done
setup 8 'my.home\n'
live '{"home":"my.home"}'
printf '#!/bin/sh\nexit 125\n' > "$T/bin/timeout"; chmod +x "$T/bin/timeout"
reply '{"home":"my.home"}'
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
OPENHOUSE_TIMEOUT="$T/bin/timeout" run
check "a timeout that works in neither form is not used" test "$(writes)" = 1
cleanup

echo "$PASS passed, $FAIL failed ($SH)"
[ "$FAIL" = 0 ]
