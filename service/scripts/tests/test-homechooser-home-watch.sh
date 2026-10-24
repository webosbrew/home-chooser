#!/bin/sh
# Tests for homechooser-home-watch with a stub luna-send that plays a scripted subscription stream
# and records the calls, a fake clock and a fake sleep.
# usage: test-homechooser-home-watch.sh [shell]     (shell defaults to sh, e.g. "busybox sh")
SH=${1:-sh}
HERE=$(cd "$(dirname "$0")/.." && pwd)
SCRIPT=$HERE/homechooser-home-watch
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
    printf 'my.home\norg.webosbrew.homechooser\ncom.webos.app.home\n' > "$T/installed"
    : > "$T/mark"
    : > "$T/kfcalls"
    mkfifo "$T/appsfeed"
    mkdir -p "$T/app" "$T/service/scripts" "$T/init.d"
    cp "$SCRIPT" "$T/service/scripts/homechooser-home-watch"
    cp -R "$HERE/lib" "$T/service/scripts/lib"
    printf '#!/bin/sh\necho "homekey $* from=$0" >> %s/kfcalls\necho "$(cat %s/clock) KF $*" >> %s/calls\n' "$T" "$T" "$T" > "$T/service/scripts/homechooser-homekey"
    printf '#!/bin/sh\necho "access $* from=$0" >> %s/access\n' "$T" > "$T/service/scripts/homechooser-access"
    printf '#!/bin/sh\n' > "$T/service/scripts/homechooser"
    touch "$T/flag"
    ln -s "$T/service/scripts/homechooser" "$T/init.d/homechooser"
    : > "$T/calls"
    : > "$T/stream"
    echo '{"returnValue":true}' > "$T/r-setdefault"
    echo '{"returnValue":true}' > "$T/r-setsys"
    echo '{"returnValue":true,"settings":{"defaultApps":{"kidsApp":"k.app"}}}' > "$T/record"
    echo '{"returnValue":false,"errorText":"There is no matched result from DB"}' > "$T/profiles"
    cat > "$T/bin/luna-send" <<'STUB'
#!/bin/sh
T=$T
# Reject any production call without the fixed identity; then parse its remaining argv.
[ "$1 $2" = '-a com.webos.applicationManager' ] || { echo 'bad identity' >> "$T/identity-errors"; exit 1; }
echo "$1 $2" >> "$T/identity-args"
shift 2
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
                [ "$id" = org.webosbrew.homechooser ] && rm -rf $T/app ;;
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
            title=; [ -f $T/notitle ] || title=',"title":"Title of '$id'"'
            echo '{"returnValue":true,"appInfo":{"id":"'$id'"'"$title"'}}'
        else
            echo '{"returnValue":false,"errorCode":-1,"errorText":"Invalid appId specified OR Unsupported Application Type"}'
        fi ;;
    */getSystemSettings) cat $T/record ;;
    */setDefaultApp)
        cat $T/r-setdefault
        if [ -f $T/live ]; then
            id=$(echo "$4" | sed 's/.*"appId":"\([^"]*\)".*/\1/')
            if grep -q '"home"' $T/record; then
                sed -e 's/"home":"[^"]*"/"home":"'"$id"'"/' -e 's/"home":null/"home":"'"$id"'"/' $T/record > $T/record.new
            else
                sed 's/"defaultApps":{/"defaultApps":{"home":"'"$id"'",/' $T/record > $T/record.new
            fi
            mv $T/record.new $T/record
        fi ;;
    */setSystemSettings)
        cat $T/r-setsys
        [ -f $T/live ] && echo "$4" | sed 's/^{"category":"general","settings":\(.*\)}$/{"returnValue":true,"settings":\1}/' > $T/record ;;
    */createAlert)
        echo "$1 $2" >> $T/notify-args
        if [ -f $T/alert-deny ]; then echo '{"returnValue":false,"errorText":"Denied method call"}'; else echo '{"returnValue":true}'; fi ;;
    */createToast) echo "$1 $2" >> $T/notify-args; echo '{"returnValue":true}' ;;
    */getProfiles) cat $T/profiles ;;
    */debug/rescan) echo '{"returnValue":true}' ;;
    */setPreloadPolicy|*/closeByAppId) echo '{"returnValue":true}' ;;
esac
STUB
    sed -i "s|^T=.*|T=$T|" "$T/bin/luna-send"
    printf '#!/bin/sh\ncat %s/clock\n' "$T" > "$T/bin/clock"
    # The fake sleep advances the clock. A line queued in napq is done inside the sleep, as if it
    # happened while the watcher was waiting: UNINSTALL <id>, INSTALL <id> (each followed by a
    # listApps message), QUIET-UNINSTALL <id> (no message), RMCHOICE, or CHOICE <id>.
    cat > "$T/bin/sleep" <<'SLEEPSTUB'
#!/bin/sh
T=$T
echo "SLEEP $1" >> $T/calls
c=$(cat $T/clock); echo $((c + $1)) > $T/clock
if [ -s $T/napq ]; then
    l=$(head -n 1 $T/napq); sed 1d $T/napq > $T/napq.new; mv $T/napq.new $T/napq
    echo "$(cat $T/clock) DURING-SLEEP $l" >> $T/calls
    case "$l" in
        "UNINSTALL "*)
            id=${l#UNINSTALL }
            grep -vxF "$id" $T/installed > $T/installed.new; mv $T/installed.new $T/installed
            [ "$id" = org.webosbrew.homechooser ] && rm -rf $T/app
            echo '{"returnValue":true,"subscribed":true,"apps":[]}' > $T/appsfeed ;;
        "QUIET-UNINSTALL "*)
            id=${l#QUIET-UNINSTALL }
            grep -vxF "$id" $T/installed > $T/installed.new; mv $T/installed.new $T/installed
            [ "$id" = org.webosbrew.homechooser ] && rm -rf $T/app ;;
        "INSTALL "*)
            echo "${l#INSTALL }" >> $T/installed
            [ "${l#INSTALL }" = org.webosbrew.homechooser ] && mkdir -p $T/app
            echo '{"returnValue":true,"subscribed":true,"apps":[]}' > $T/appsfeed ;;
        RMCHOICE) rm -f $T/home ;;
        "CHOICE "*) echo "${l#CHOICE }" > $T/home ;;
    esac
fi
SLEEPSTUB
    sed -i "s|^T=.*|T=$T|" "$T/bin/sleep"
    : > "$T/napq"
    chmod +x "$T/bin/"*
    export HOMECHOOSER_CHOICE="$T/home" HOMECHOOSER_LUNA_SEND="$T/bin/luna-send" HOMECHOOSER_WATCH_LOG="$T/log" \
           HOMECHOOSER_OSRELEASE="$T/os-release" HOMECHOOSER_OVERRIDE="$T/override/o.json" \
           HOMECHOOSER_CLOCK="$T/bin/clock" HOMECHOOSER_SLEEP="$T/bin/sleep" HOMECHOOSER_TMP="$T/run" \
           HOMECHOOSER_RUNTIME="$T/rt" HOMECHOOSER_SHELL="$SH" HOMECHOOSER_APPDIR="$T/app" \
           HOMECHOOSER_HOMEKEY_FLAG="$T/flag" HOMECHOOSER_INITD="$T/init.d/homechooser" \
           HOMECHOOSER_RECHECK=100000 HOMECHOOSER_CONFIRM_DELAY=0 HOMECHOOSER_CHECK_MARK="$T/mark" \
           HOMECHOOSER_INSTALL_DIRS="$T/apps"
}
run() { $SH "$T/service/scripts/homechooser-home-watch" > "$T/out" 2>&1; echo $? > "$T/rc"; }
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
nohome() { ! grep -q '"home":"[^"]' "$T/record" && grep -q '"home":null' "$T/record"; }
# webOS 5 to 9 put the stock home id in the record (6 to 9: so the running compositor opens LG Home at once); 10 and later write null
stockhome() { grep -q '"home":"com.webos.app.home"' "$T/record" && ! grep -q '"home":null' "$T/record"; }
kfn() { grep -c "$1" "$T/kfcalls"; }
# one reply per line
reply() { printf '{"returnValue":true,"subscribed":true,"settings":{"defaultApps":%s}}\n' "$1" >> "$T/stream"; }

echo "== record already holds the choice ($SH)"
setup 8 'my.home\n'
reply '{"home":"my.home","kidsApp":"k.app"}'
run
check "subscription ends: exit 1" rc 1
check "access applied at start" has "access apply from=$T/rt/homechooser-access" "$T/access"
check "access staged in runtime" test -f "$T/rt/homechooser-access"
check "fixed luna identity used" has "-a com.webos.applicationManager" "$T/identity-args"
check "no calls bypass identity" test ! -f "$T/identity-errors"
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

echo "== home null or empty reads as unset: the choice is written, replacing the null"
for v in null '""'; do
    setup 8 'my.home\n'
    reply "{\"home\":$v,\"kidsApp\":\"k.app\"}"
    run
    check "home $v: one write" test "$(writes)" = 1
    check "home $v: merged payload, no second home" has '{"defaultApps":{"home":"my.home","kidsApp":"k.app"}}' "$T/calls"
    cleanup
done

echo "== no stored choice: a null home is left alone"
setup 8 ''
rm -f "$T/home"
reply '{"home":null,"kidsApp":"k.app"}'
run
check "exit 0" rc 0
check "no write" test "$(writes)" = 0
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
check "blank file: exit 0" rc 0
check "blank file: only the record read for a leftover" test "$(grep -c CALL "$T/calls")" = 1 -a "$(count getSystemSettings "$T/calls")" = 1
check "blank file: nothing subscribed" hasnt SUB "$T/calls"
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
check "no choice file: only the record read for a leftover" test "$(grep -c CALL "$T/calls")" = 1 -a "$(count getSystemSettings "$T/calls")" = 1
check "no choice file: nothing subscribed, no write" test "$(writes)" = 0 -a "$(count SUB "$T/calls")" = 0
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
HOMECHOOSER_LOG_MAX=2000 run
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
    check "$major: home written as the stock id (not null), other entries kept" has 'CALL luna://com.webos.settingsservice/setSystemSettings {"category":"general","settings":{"defaultApps":{"home":"com.webos.app.home","kidsApp":"k.app"}}}' "$T/calls"
    check "$major: no home written while the app is missing, then once when it is back" test "$(writes)" = 2
    check "$major: written again with home" has '{"defaultApps":{"home":"my.home","kidsApp":"k.app"}}' "$T/calls"
    check "$major: Home key hook refreshed to stock" test "$(kfn 'refresh stock')" = 1
    check "$major: Home key hook refreshed to the choice after" test "$(kfn 'refresh from')" -ge 2
    check "$major: choice file kept" test -f "$T/home"
    check "$major: logged" has 'my.home is not installed, stock Home in use' "$T/log"
    check "$major: logged return" has 'my.home is installed again' "$T/log"
    check "$major: no setDefaultApp" hasnt setDefaultApp "$T/calls"
    cleanup
done

echo "== chosen app uninstalled and installed again, webOS 5"
setup 5 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
printf 'my.home\norg.webosbrew.homechooser\ncom.webos.app.home\ncom.webos.app.fullhome\n' > "$T/installed"
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
check "no Home key hook on 5" test ! -s "$T/kfcalls"
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
check "removal is a setSystemSettings write" has '{"defaultApps":{"home":"com.webos.app.home","kidsApp":"k.app"}}' "$T/calls"
check "return uses setDefaultApp" has 'setDefaultApp {"category":"home","appId":"my.home"}' "$T/calls"
check "one setDefaultApp" test "$(count setDefaultApp "$T/calls")" = 1
cleanup

echo "== chosen app uninstalled, webOS 6 to 9: fullhome is the stock id when installed"
for major in 6 9; do
    setup $major 'my.home\n'
    live '{"home":"my.home","kidsApp":"k.app"}'
    printf 'my.home\norg.webosbrew.homechooser\ncom.webos.app.home\ncom.webos.app.fullhome\n' > "$T/installed"
    reply '{"home":"my.home","kidsApp":"k.app"}'
    echo 'UNINSTALL my.home' >> "$T/stream"
    echo APPS >> "$T/stream"
    run
    check "$major: fullhome named while the app is missing" has '{"defaultApps":{"home":"com.webos.app.fullhome","kidsApp":"k.app"}}' "$T/calls"
    check "$major: never null" hasnt '"home":null' "$T/calls"
    cleanup
done

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
    check "$major: removal wrote home as null" has '{"defaultApps":{"home":null,"kidsApp":"k.app"}}' "$T/calls"
    check "$major: rescan after removing the override" test "$(count debug/rescan "$T/calls")" -ge 2
    check "$major: override rebuilt when it came back" has '"home":"my.home"' "$T/override/o.json"
    check "$major: setDefaultApp when it came back" has 'setDefaultApp {"category":"home","appId":"my.home"}' "$T/calls"
    check "$major: no Home key hook" test ! -s "$T/kfcalls"
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
check "checked twice (and asked once for its title)" test "$(count 'getAppInfo {"id":"my.home"}' "$T/calls")" = 4
check "no revert logged" hasnt 'not installed' "$T/log"
cleanup
setup 8 'my.home\n'
live '{"home":"my.home"}'
reply '{"home":"my.home"}'
echo 'FLAP org.webosbrew.homechooser 1' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "Home Chooser answered again: no restore" has 'Home Chooser is back' "$T/log"
check "choice file kept" test -f "$T/home"
check "link kept" test -L "$T/init.d/homechooser"
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
HOMECHOOSER_RECHECK=1 run
check "reverted by the timer" has '{"defaultApps":{"home":"com.webos.app.home","kidsApp":"k.app"}}' "$T/calls"
check "mark says timer" has timer "$T/mark"
cleanup

echo "== choice changed while the old one is missing"
setup 8 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
printf 'my.home\nother.home\norg.webosbrew.homechooser\n' > "$T/installed"
reply '{"home":"my.home"}'
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
echo 'CHOICE other.home' >> "$T/stream"
reply '{"kidsApp":"k.app"}'
run
check "the new choice is written at once" has '{"defaultApps":{"home":"other.home","kidsApp":"k.app"}}' "$T/calls"
check "two writes: the removal and the new choice" test "$(writes)" = 2
check "Home key hook refreshed for the new choice" test "$(kfn 'refresh from')" = 2
cleanup

echo "== choice changed while the app is installed: baked id refreshed without a restart"
for major in 6 8 9; do
    setup $major 'my.home\n'
    live '{"home":"my.home","kidsApp":"k.app"}'
    printf 'my.home\nother.home\norg.webosbrew.homechooser\n' > "$T/installed"
    reply '{"home":"my.home","kidsApp":"k.app"}'
    echo 'CHOICE other.home' >> "$T/stream"
    reply '{"home":"my.home","kidsApp":"k.app"}'
    reply '{"home":"other.home","kidsApp":"k.app"}'
    run
    check "$major: record carries the new choice" has '"home":"other.home"' "$T/record"
    check "$major: the start refresh and the one for the new choice" test "$(kfn 'refresh from')" = 2
    check "$major: no refresh asks for a restart" hasnt restart "$T/kfcalls"
    check "$major: no stop" hasnt 'homekey stop' "$T/kfcalls"
    check "$major: the refresh ran from the runtime copy" has "refresh from=$T/rt/homechooser-homekey" "$T/kfcalls"
    cleanup
done

echo "== unchanged choice: no refresh on later settings messages"
setup 8 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
reply '{"home":"my.home","kidsApp":"k.app"}'
reply '{"home":"my.home","kidsApp":"k.app","x":1}'
run
check "only the start refresh" test "$(kfn 'refresh from')" = 1
cleanup

echo "== choice changed to an app that is not installed: left to the removal path"
setup 8 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
reply '{"home":"my.home","kidsApp":"k.app"}'
echo 'CHOICE ghost.app' >> "$T/stream"
reply '{"home":"my.home","kidsApp":"k.app"}'
run
check "only the start refresh for an uninstalled app" test "$(kfn 'refresh from')" = 1
cleanup

echo "== choice changed on webOS 5 and 10: the Home key hook is not involved"
for major in 5 10; do
    setup $major 'my.home\n'
    live '{"home":"my.home","kidsApp":"k.app"}'
    printf 'my.home\nother.home\norg.webosbrew.homechooser\n' > "$T/installed"
    reply '{"home":"my.home","kidsApp":"k.app"}'
    echo 'CHOICE other.home' >> "$T/stream"
    reply '{"home":"my.home","kidsApp":"k.app"}'
    run
    check "$major: no Home key hook call" test "$(kfn refresh)" = 0
    cleanup
done

echo "== Home Chooser uninstalled"
for major in 6 9; do
    setup $major 'my.home\n'
    live '{"home":"my.home","kidsApp":"k.app"}'
    reply '{"home":"my.home","kidsApp":"k.app"}'
    echo 'UNINSTALL org.webosbrew.homechooser' >> "$T/stream"
    echo APPS >> "$T/stream"
    reply '{"home":"other"}'
    run
    check "$major: exit 0" rc 0
    check "$major: home is the stock id in the record, not null" stockhome
    check "$major: other entries kept" has 'kidsApp' "$T/record"
    check "$major: choice file removed" test ! -f "$T/home"
    check "$major: Home key hook flag removed" test ! -f "$T/flag"
    check "$major: init.d link removed" test ! -L "$T/init.d/homechooser"
    check "$major: app directory is gone" test ! -d "$T/app"
    check "$major: no Home key hook stop, no unmount, no restart" hasnt 'homekey stop' "$T/kfcalls"
    check "$major: the worker was not asked to restart anything" hasnt restart "$T/kfcalls"
    check "$major: no worker call after the revert" hasnt 'homekey apply' "$T/kfcalls"
    check "$major: the watcher itself ran from the runtime" test -f "$T/rt/homechooser-home-watch"
    check "$major: logged" has 'stock Home restored' "$T/log"
    check "$major: nothing written after the restore" test "$(writes)" = 1
    cleanup
done
setup 8 'my.home\n'
printf '#!/bin/sh\n' > "$T/init.d/other-entry"
ln -s "$T/service/scripts/homechooser" "$T/init.d/other-link"
live '{"home":"my.home"}'
reply '{"home":"my.home"}'
echo 'UNINSTALL org.webosbrew.homechooser' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "an unrelated init.d link is left alone" test -L "$T/init.d/other-link"
check "a regular file in init.d is left alone" test -f "$T/init.d/other-entry"
cleanup

echo "== Home Chooser uninstalled, webOS 5"
setup 5 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
reply '{"home":"my.home","kidsApp":"k.app"}'
echo 'UNINSTALL org.webosbrew.homechooser' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "exit 0" rc 0
check "home names the stock launcher" has '"home":"com.webos.app.home"' "$T/record"
check "choice removed" test ! -f "$T/home"
check "link removed" test ! -L "$T/init.d/homechooser"
check "no Home key hook on 5" test ! -s "$T/kfcalls"
cleanup

echo "== Home Chooser uninstalled, webOS 10 and later"
setup 11 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
mkdir -p "$T/override"
echo '{"defaultApps":{"categories":{"home":"my.home"}}}' > "$T/override/o.json"
reply '{"home":"my.home","kidsApp":"k.app"}'
echo 'UNINSTALL org.webosbrew.homechooser' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "exit 0" rc 0
check "override removed" test ! -f "$T/override/o.json"
check "rescanned" has debug/rescan "$T/calls"
check "home removed" nohome
check "access removed from runtime after uninstall" has "access remove from=$T/rt/homechooser-access" "$T/access"
check "choice, flag and link removed" test ! -f "$T/home" -a ! -f "$T/flag" -a ! -L "$T/init.d/homechooser"
check "no Home key hook on 11" test ! -s "$T/kfcalls"
cleanup

echo "== both removed, either order"
setup 8 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
reply '{"home":"my.home","kidsApp":"k.app"}'
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
echo 'UNINSTALL org.webosbrew.homechooser' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "chosen first: exit 0" rc 0
check "chosen first: one write in all" test "$(writes)" = 1
check "chosen first: stock id in the record" stockhome
check "chosen first: second revert found nothing to do" has 'home is already com.webos.app.home' "$T/log"
check "chosen first: all cleaned" test ! -f "$T/home" -a ! -f "$T/flag" -a ! -L "$T/init.d/homechooser"
cleanup
setup 8 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
reply '{"home":"my.home","kidsApp":"k.app"}'
echo 'UNINSTALL org.webosbrew.homechooser' >> "$T/stream"
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "both at once: exit 0" rc 0
check "both at once: one write" test "$(writes)" = 1
check "both at once: stock state, nothing left for a later removal to break" stockhome
check "both at once: all cleaned" test ! -f "$T/home" -a ! -f "$T/flag" -a ! -L "$T/init.d/homechooser"
check "both at once: no stop" hasnt 'homekey stop' "$T/kfcalls"
cleanup
setup 8 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
reply '{"home":"my.home","kidsApp":"k.app"}'
echo 'UNINSTALL org.webosbrew.homechooser' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "Home Chooser first: exit 0, then my.home may go any time" rc 0
check "Home Chooser first: record is stock" stockhome
check "Home Chooser first: no choice for anything to enforce" test ! -f "$T/home"
cleanup

echo "== the restore runs from the runtime copy after the app is gone (unit restart)"
setup 8 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
reply '{"home":"my.home","kidsApp":"k.app"}'
run
check "first run staged the copies" test -f "$T/rt/homechooser-home-watch"
check "first run staged sourced helpers" test -f "$T/rt/lib/watch-restore.sh"
check "first run staged the rewriter dependency" test -f "$T/rt/lib/homekey-settings.js"
rm -rf "$T/service/scripts"
rm -rf "$T/app"
printf 'my.home\n' > "$T/home"
touch "$T/flag"
: > "$T/stream"
$SH "$T/rt/homechooser-home-watch" > "$T/out" 2>&1; echo $? > "$T/rc"
check "exit 0" rc 0
check "record is stock" stockhome
check "choice removed" test ! -f "$T/home"
check "link removed" test ! -L "$T/init.d/homechooser"
check "no stop from the runtime copy either" hasnt 'homekey stop' "$T/kfcalls"
cleanup

echo "== idempotent: a second restore changes nothing"
setup 8 'my.home\n'
live '{"kidsApp":"k.app"}'
rm -rf "$T/app"
$SH "$SCRIPT" > "$T/out" 2>&1; echo $? > "$T/rc"
check "runs from the repository copy, app dir missing: exit 0" rc 0
check "6 to 9: a record without home gets the stock id" test "$(writes)" = 1
check "6 to 9: record is stock, other entries kept" stockhome
cleanup

echo "== idempotent: 10 and later, a record without home changes nothing"
setup 11 'my.home\n'
live '{"kidsApp":"k.app"}'
rm -rf "$T/app"
$SH "$SCRIPT" > "$T/out" 2>&1; echo $? > "$T/rc"
check "no write (nothing to remove)" test "$(writes)" = 0
cleanup

echo "== idempotent: 6 to 9, home already the stock id changes nothing"
setup 8 'my.home\n'
live '{"home":"com.webos.app.home","kidsApp":"k.app"}'
rm -rf "$T/app"
$SH "$SCRIPT" > "$T/out" 2>&1; echo $? > "$T/rc"
check "no write (already stock)" test "$(writes)" = 0
cleanup

echo "== idempotent: 10 and later, home already null changes nothing; 6 to 9 replace the null with the stock id"
setup 11 'my.home\n'
live '{"home":null,"kidsApp":"k.app"}'
rm -rf "$T/app"
$SH "$SCRIPT" > "$T/out" 2>&1; echo $? > "$T/rc"
check "exit 0" rc 0
check "no write (nothing to clear)" test "$(writes)" = 0
cleanup
setup 8 'my.home\n'
live '{"home":null,"kidsApp":"k.app"}'
rm -rf "$T/app"
$SH "$SCRIPT" > "$T/out" 2>&1; echo $? > "$T/rc"
check "exit 0" rc 0
check "null replaced by the stock id" test "$(writes)" = 1
check "record is stock" stockhome
cleanup

echo "== timeout in both forms (BusyBox before 1.30 wants -t)"
for form in new old none; do
    setup 8 'my.home\n'
    live '{"home":"my.home","kidsApp":"k.app"}'
    reply '{"home":"my.home","kidsApp":"k.app"}'
    echo 'UNINSTALL my.home' >> "$T/stream"
    echo APPS >> "$T/stream"
    if [ $form = none ]; then export HOMECHOOSER_TIMEOUT=; else mktimeout $form "$T/bin/timeout"; export HOMECHOOSER_TIMEOUT="$T/bin/timeout"; fi
    run
    unset HOMECHOOSER_TIMEOUT
    check "$form: removal still detected and reverted" has '{"defaultApps":{"home":"com.webos.app.home","kidsApp":"k.app"}}' "$T/calls"
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
HOMECHOOSER_TIMEOUT="$T/bin/timeout" run
check "a timeout that works in neither form is not used" test "$(writes)" = 1
cleanup

# clean_end <label> <major>: the end state after both uninstalls
clean_end() {
    check "$1: exit 0 (the unit stays stopped)" rc 0
    check "$1: choice file removed" test ! -f "$T/home"
    check "$1: init.d link removed" test ! -L "$T/init.d/homechooser"
    check "$1: flag removed" test ! -f "$T/flag"
    if [ "$2" -ge 10 ]; then check "$1: record restored (null)" nohome; else check "$1: record restored (stock id)" stockhome; fi
    check "$1: stock restored line logged" has 'stock Home restored' "$T/log"
    [ "$2" -ge 10 ] && check "$1: override removed" test ! -f "$T/override/o.json"
    if [ "$2" -ge 6 ]; then
        last=$(grep setPreloadPolicy "$T/calls" | tail -n 1)
        check "$1: stock home preload policy enabled again" test "${last#*isEnabled\":}" != "$last" -a "$(echo "$last" | grep -c 'isEnabled":true')" = 1
    fi
}

# Both apps removed, the second one while the watcher is waiting out the confirmation delay of the
# first (15 s), in either order and with the second one's listApps message delivered or lost.
echo "== both removed: Home Chooser goes while the chosen app is being confirmed"
for major in 5 8 9 11; do
    for how in UNINSTALL QUIET-UNINSTALL; do
        setup $major 'my.home\n'
        live '{"home":"my.home","kidsApp":"k.app"}'
        mkdir -p "$T/override"
        echo '{"defaultApps":{"categories":{"home":"my.home"}}}' > "$T/override/o.json"
        reply '{"home":"my.home","kidsApp":"k.app"}'
        echo 'UNINSTALL my.home' >> "$T/stream"
        echo APPS >> "$T/stream"
        echo "$how org.webosbrew.homechooser" > "$T/napq"
        HOMECHOOSER_CONFIRM_DELAY=15 run
        if [ "$major" = 5 ]; then
            check "5/$how: record names the stock launcher" has '"home":"com.webos.app.home"' "$T/record"
            check "5/$how: exit 0" rc 0
            check "5/$how: choice, flag and link removed" test ! -f "$T/home" -a ! -f "$T/flag" -a ! -L "$T/init.d/homechooser"
        else
            clean_end "$major/$how" "$major"
        fi
        check "$major/$how: the chosen app's revert was not made first and then repeated" test "$(writes)" -le 1
        cleanup
    done
done

echo "== both removed: the chosen app goes while Home Chooser is being confirmed"
for major in 8 11; do
    for how in UNINSTALL QUIET-UNINSTALL; do
        setup $major 'my.home\n'
        live '{"home":"my.home","kidsApp":"k.app"}'
        mkdir -p "$T/override"
        echo '{"defaultApps":{"categories":{"home":"my.home"}}}' > "$T/override/o.json"
        reply '{"home":"my.home","kidsApp":"k.app"}'
        echo 'UNINSTALL org.webosbrew.homechooser' >> "$T/stream"
        echo APPS >> "$T/stream"
        echo "$how my.home" > "$T/napq"
        HOMECHOOSER_CONFIRM_DELAY=15 run
        clean_end "$major/$how" "$major"
        cleanup
    done
done

echo "== both removed, far apart: the chosen app, then Home Chooser at the periodic check"
for major in 8 11; do
    setup $major 'my.home\n'
    live '{"home":"my.home","kidsApp":"k.app"}'
    mkdir -p "$T/override"
    echo '{"defaultApps":{"categories":{"home":"my.home"}}}' > "$T/override/o.json"
    reply '{"home":"my.home","kidsApp":"k.app"}'
    echo 'UNINSTALL my.home' >> "$T/stream"
    echo APPS >> "$T/stream"
    echo 'UNINSTALL org.webosbrew.homechooser' >> "$T/stream"
    echo TIMER >> "$T/stream"
    HOMECHOOSER_CONFIRM_DELAY=15 HOMECHOOSER_RECHECK=1 run
    clean_end "$major/far apart" "$major"
    cleanup
done

echo "== Home Chooser removed while the chosen app is confirmed, then put back: nothing is removed"
setup 8 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
reply '{"home":"my.home","kidsApp":"k.app"}'
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
printf 'UNINSTALL org.webosbrew.homechooser\nINSTALL org.webosbrew.homechooser\n' > "$T/napq"
HOMECHOOSER_CONFIRM_DELAY=15 run
check "the choice file stays (an update)" test -f "$T/home"
check "the link stays" test -L "$T/init.d/homechooser"
cleanup

# --- Removal notifications: our own trigger logic (what the notification service does with the
# calls is not tested here)
alerts() { grep -c ' CALL luna://com.webos.notification/createAlert' "$T/calls"; }
toasts() { grep -c ' CALL luna://com.webos.notification/createToast' "$T/calls"; }
lineof() { grep -n -- "$1" "$T/calls" | head -n 1 | cut -d: -f1; }
ALERT_MSG='Title of my.home was removed, so the Home button opens LG Home again.'
ALERT_MSG_OLD=$ALERT_MSG

echo "== the chosen app removed: one alert, after the revert, with its title"
for major in 5 8 10 11; do
    setup $major 'my.home\n'
    msg=$ALERT_MSG_OLD
    live '{"home":"my.home","kidsApp":"k.app"}'
    reply '{"home":"my.home","kidsApp":"k.app"}'
    echo 'UNINSTALL my.home' >> "$T/stream"
    echo APPS >> "$T/stream"
    echo APPS >> "$T/stream"
    echo TIMER >> "$T/stream"
    HOMECHOOSER_RECHECK=1 run
    check "$major: one alert, not repeated by the later checks" test "$(alerts)" = 1
    check "$major: no toast" test "$(toasts)" = 0
    check "$major: payload" has "CALL luna://com.webos.notification/createAlert {\"sourceId\":\"org.webosbrew.homechooser\",\"title\":\"Home Chooser\",\"message\":\"$msg\",\"buttons\":[{\"label\":\"OK\",\"focus\":true}]}" "$T/calls"
    check "$major: called through the fixed identity" test "$(cat "$T/notify-args")" = "-n 1"
    check "$major: after the revert write" test "$(lineof createAlert)" -gt "$(lineof 'CALL luna://com.webos.settingsservice/setSystemSettings')"
    check "$major: reply logged" has 'notification createAlert: {"returnValue":true}' "$T/log"
    cleanup
done
echo "== the alert text is the same on 6 and 9 as everywhere: no restart sentence"
for major in 6 9; do
    setup $major 'my.home\n'
    live '{"home":"my.home","kidsApp":"k.app"}'
    reply '{"home":"my.home","kidsApp":"k.app"}'
    echo 'UNINSTALL my.home' >> "$T/stream"
    echo APPS >> "$T/stream"
    run
    check "$major: same text, no restart sentence" has "\"message\":\"$ALERT_MSG\"" "$T/calls"
    check "$major: no restart sentence" hasnt 'Restart the TV' "$T/calls"
    cleanup
done
echo "== the revert of a removed chosen app never restarts the compositor (6 to 9)"
for major in 6 9; do
    setup $major 'my.home\n'
    live '{"home":"my.home","kidsApp":"k.app"}'
    reply '{"home":"my.home","kidsApp":"k.app"}'
    echo 'UNINSTALL my.home' >> "$T/stream"
    echo APPS >> "$T/stream"
    run
    check "$major: refresh stock ran before the alert" test "$(lineof 'KF refresh stock')" -lt "$(lineof createAlert)"
    check "$major: refresh stock without restart" hasnt restart "$T/kfcalls"
    check "$major: one alert" test "$(alerts)" = 1
    cleanup
done

echo "== the alert names the id when the title is not known"
setup 8 'my.home\n'
live '{"home":"my.home"}'
touch "$T/notitle"
reply '{"home":"my.home"}'
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "id in the message" has '"message":"my.home was removed, so the Home button opens LG Home again."' "$T/calls"
cleanup
echo "== the title is read from appinfo.json when getAppInfo carries none"
setup 8 'my.home\n'
live '{"home":"my.home"}'
touch "$T/notitle"
mkdir -p "$T/apps/my.home"
echo '{"id":"my.home","title": "Pretty Home"}' > "$T/apps/my.home/appinfo.json"
reply '{"home":"my.home"}'
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
HOMECHOOSER_APPS_DIRS="$T/apps" run
check "title from the file" has '"message":"Pretty Home was removed, so' "$T/calls"
cleanup
echo "== a title with quotes or backslashes is not put into the payload"
setup 8 'my.home\n'
live '{"home":"my.home"}'
touch "$T/notitle"
mkdir -p "$T/apps/my.home"
echo '{"id":"my.home","title":"Bad \"one\""}' > "$T/apps/my.home/appinfo.json"
reply '{"home":"my.home"}'
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
HOMECHOOSER_APPS_DIRS="$T/apps" run
check "falls back to the id" has '"message":"my.home was removed, so' "$T/calls"
cleanup

echo "== no alert for a transient absence, for a watcher start with the app missing, or for a restore from the app"
setup 8 'my.home\n'
live '{"home":"my.home"}'
reply '{"home":"my.home"}'
echo 'FLAP my.home 1' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "transient absence: no alert" test "$(alerts)" = 0
check "transient absence: no toast" test "$(toasts)" = 0
cleanup
setup 10 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
grep -vx my.home "$T/installed" > "$T/i.new"; mv "$T/i.new" "$T/installed"
reply '{"home":"my.home","kidsApp":"k.app"}'
run
check "missing at start: reverted" has 'my.home is not installed' "$T/log"
check "missing at start: no alert (not a new removal)" test "$(alerts)" = 0
cleanup
setup 8 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
reply '{"home":"my.home","kidsApp":"k.app"}'
echo RMCHOICE >> "$T/stream"
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "restored from the app, then the app removed: exit 0" rc 0
check "restore from the app: no alert" test "$(alerts)" = 0
check "restore from the app: no toast" test "$(toasts)" = 0
cleanup

echo "== one alert per removal event: back and removed again is a second one"
setup 8 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
reply '{"home":"my.home","kidsApp":"k.app"}'
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
echo 'INSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "two removals, two alerts" test "$(alerts)" = 2
cleanup

echo "== a new choice replaces the missing one: no alert for it"
setup 8 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
printf 'my.home\nother.home\norg.webosbrew.homechooser\n' > "$T/installed"
reply '{"home":"my.home"}'
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
echo 'CHOICE other.home' >> "$T/stream"
reply '{"kidsApp":"k.app"}'
echo APPS >> "$T/stream"
echo 'UNINSTALL other.home' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "the title cached for the new choice is used" has '"message":"Title of other.home was removed' "$T/calls"
check "one alert per removal" test "$(alerts)" = 2
cleanup

echo "== a refused alert is shown as a toast with the same message (our fallback)"
setup 8 'my.home\n'
live '{"home":"my.home"}'
touch "$T/alert-deny"
reply '{"home":"my.home"}'
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
echo APPS >> "$T/stream"
run
check "a refused alert is tried 8 times (a transient refusal while the compositor restarts), then falls back" test "$(alerts)" = 8
check "one toast with the same message" has "CALL luna://com.webos.notification/createToast {\"sourceId\":\"org.webosbrew.homechooser\",\"noaction\":true,\"message\":\"$ALERT_MSG\"}" "$T/calls"
check "one toast" test "$(toasts)" = 1
check "the refusal is logged" has 'alert was refused' "$T/log"
check "the restore itself is unaffected" has 'my.home is not installed' "$T/log"
cleanup
echo "== a notification call that gives no answer is logged and not retried"
setup 8 'my.home\n'
live '{"home":"my.home"}'
cp "$T/bin/luna-send" "$T/bin/luna-send.real"
cat > "$T/bin/luna-send" <<'MUTE'
#!/bin/sh
case "$5" in */createAlert|*/createToast) echo "$5 $6" >> $T/muted; exit 0 ;; esac
exec $T/bin/luna-send.real "$@"
MUTE
sed -i "s|^exec \$T|exec $T|; 1a T=$T" "$T/bin/luna-send"
chmod +x "$T/bin/luna-send"
reply '{"home":"my.home"}'
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "tried once" test "$(grep -c createAlert "$T/muted")" = 1
check "no toast instead" test "$(grep -c createToast "$T/muted")" = 0
check "logged without a reply" has 'notification createAlert: no reply' "$T/log"
cleanup

echo "== notifications can be turned off"
setup 8 'my.home\n'
live '{"home":"my.home"}'
reply '{"home":"my.home"}'
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
HOMECHOOSER_NOTIFY=0 run
check "none sent" test "$(alerts)$(toasts)" = 00
cleanup

echo "== Home Chooser uninstalled: one toast, the Home key hook left mounted (6 to 9)"
for major in 5 6 9 10; do
    setup $major 'my.home\n'
    live '{"home":"my.home","kidsApp":"k.app"}'
    reply '{"home":"my.home","kidsApp":"k.app"}'
    echo 'UNINSTALL org.webosbrew.homechooser' >> "$T/stream"
    echo APPS >> "$T/stream"
    echo APPS >> "$T/stream"
    run
    check "$major: exit 0" rc 0
    check "$major: one toast" test "$(toasts)" = 1
    check "$major: no alert" test "$(alerts)" = 0
    toast='Home Chooser was removed. The Home button opens LG Home again.'
    check "$major: payload" has "CALL luna://com.webos.notification/createToast {\"sourceId\":\"org.webosbrew.homechooser\",\"noaction\":true,\"message\":\"$toast\"}" "$T/calls"
    check "$major: fixed identity, one reply" test "$(cat "$T/notify-args")" = "-n 1"
    check "$major: after the record revert" test "$(lineof createToast)" -gt "$(lineof 'CALL luna://com.webos.settingsservice/setSystemSettings')"
    check "$major: no worker stop, no restart" hasnt 'KF stop' "$T/calls"
    check "$major: the only worker calls are refreshes" test "$(grep -vc '^homekey refresh' "$T/kfcalls")" = 0
    check "$major: files gone before the toast is the last word" test ! -f "$T/home"
    cleanup
done
echo "== both apps removed: the alert for the chosen app, then the toast for Home Chooser"
setup 8 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
reply '{"home":"my.home","kidsApp":"k.app"}'
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
echo 'UNINSTALL org.webosbrew.homechooser' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "one alert" test "$(alerts)" = 1
check "one toast" test "$(toasts)" = 1
check "alert first" test "$(lineof createAlert)" -lt "$(lineof createToast)"
cleanup
echo "== Home Chooser removed during the chosen app's confirmation: the toast only, the alert is skipped"
setup 8 'my.home\n'
live '{"home":"my.home","kidsApp":"k.app"}'
reply '{"home":"my.home","kidsApp":"k.app"}'
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
echo 'UNINSTALL org.webosbrew.homechooser' > "$T/napq"
HOMECHOOSER_CONFIRM_DELAY=15 run
check "no alert" test "$(alerts)" = 0
check "one toast" test "$(toasts)" = 1
cleanup

# --- A leftover Home: no stored choice, the home names an app that is not installed ----------
nochoice() { setup "$1" ''; rm -f "$T/home"; }

echo "== leftover: no choice, the record names a missing app, webOS 5 to 9: the stock id"
for major in 5 6 8 9; do
    nochoice $major
    live '{"home":"gone.app","kidsApp":"k.app"}'
    HOMECHOOSER_CONFIRM_DELAY=15 run
    check "$major: exit 0" rc 0
    check "$major: stock id written, other entries kept" has '{"defaultApps":{"home":"com.webos.app.home","kidsApp":"k.app"}}' "$T/calls"
    check "$major: one write, by setSystemSettings" test "$(writes)" = 1 -a "$(count setDefaultApp "$T/calls")" = 0
    check "$major: confirmed after the delay" test "$(count 'getAppInfo {"id":"gone.app"}' "$T/calls")" = 2 -a "$(count 'SLEEP 15' "$T/calls")" = 1
    check "$major: nothing subscribed" hasnt SUB "$T/calls"
    check "$major: no alert or toast" test "$(alerts)" = 0 -a "$(toasts)" = 0
    check "$major: logged as a leftover" has 'a leftover' "$T/log"
    if [ "$major" -ge 6 ]; then
        check "$major: baked id emptied" test "$(kfn 'refresh stock')" = 1
    else
        check "$major: no Home key hook" test ! -s "$T/kfcalls"
    fi
    cleanup
done
nochoice 7
live '{"home":"gone.app"}'
printf 'org.webosbrew.homechooser\ncom.webos.app.home\ncom.webos.app.fullhome\n' > "$T/installed"
run
check "fullhome named when installed" has '{"defaultApps":{"home":"com.webos.app.fullhome"}}' "$T/calls"
cleanup

echo "== leftover: webOS 10 and later, record and override name a missing app"
for major in 10 11; do
    nochoice $major
    live '{"home":"gone.app","kidsApp":"k.app"}'
    mkdir -p "$T/override"
    echo '{"defaultApps":{"categories":{"home":"gone.app"}}}' > "$T/override/o.json"
    run
    check "$major: exit 0" rc 0
    check "$major: override removed" test ! -f "$T/override/o.json"
    check "$major: rescan" test "$(count debug/rescan "$T/calls")" = 1
    check "$major: home written as null" has '{"defaultApps":{"home":null,"kidsApp":"k.app"}}' "$T/calls"
    check "$major: asked once per id" test "$(count 'getAppInfo {"id":"gone.app"}' "$T/calls")" = 2
    check "$major: no Home key hook" test ! -s "$T/kfcalls"
    cleanup
done
nochoice 11
live '{"home":null,"kidsApp":"k.app"}'
mkdir -p "$T/override"
echo '{"defaultApps":{"categories":{"home":"gone.app"}}}' > "$T/override/o.json"
run
check "override only: removed" test ! -f "$T/override/o.json"
check "override only: no record write" test "$(writes)" = 0
cleanup
nochoice 11
live '{"home":"my.home"}'
mkdir -p "$T/override"
echo '{"defaultApps":{"categories":{"home":"gone.app"}}}' > "$T/override/o.json"
run
check "override stale, record installed: override removed" test ! -f "$T/override/o.json"
check "override stale, record installed: the record is left alone" test "$(writes)" = 0
cleanup
nochoice 11
live '{"home":"gone.app"}'
mkdir -p "$T/override"
echo '{"defaultApps":{"categories":{"home":"my.home"}}}' > "$T/override/o.json"
run
check "record stale, override installed: override kept" test -f "$T/override/o.json"
check "record stale, override installed: home null" has '{"defaultApps":{"home":null}}' "$T/calls"
cleanup
nochoice 8
live '{"home":"gone.app"}'
mkdir -p "$T/override"
echo '{"defaultApps":{"categories":{"home":"gone.app"}}}' > "$T/override/o.json"
run
check "an override file before 10 is not looked at" test -f "$T/override/o.json"
cleanup

echo "== leftover: what is left alone"
nochoice 8
live '{"home":"my.home"}'
run
check "an installed app: no write" test "$(writes)" = 0
check "an installed app: logged" has 'nothing to repair' "$T/log"
cleanup
for id in com.webos.app.home com.webos.app.fullhome; do
    nochoice 8
    live "{\"home\":\"$id\"}"
    : > "$T/installed"
    run
    check "stock id $id: no write" test "$(writes)" = 0
    check "stock id $id: not asked about" hasnt "getAppInfo {\"id\":\"$id\"}" "$T/calls"
    cleanup
done
nochoice 8
live '{"home":"gone.app"}'
touch "$T/sam-silent"
run
check "getAppInfo silent: no write" test "$(writes)" = 0
check "getAppInfo silent: logged" has 'did not answer for gone.app' "$T/log"
cleanup
nochoice 10
live '{"home":"gone.app"}'
mkdir -p "$T/override"
echo '{"defaultApps":{"categories":{"home":"gone.app"}}}' > "$T/override/o.json"
touch "$T/sam-silent"
run
check "getAppInfo silent, 10: override kept" test -f "$T/override/o.json"
check "getAppInfo silent, 10: no write" test "$(writes)" = 0
cleanup
nochoice 8
live '{"home":"my.home"}'
echo 1 > "$T/flap-my.home"
HOMECHOOSER_CONFIRM_DELAY=15 run
check "a transient absence: no write" test "$(writes)" = 0
check "a transient absence: asked twice" test "$(count 'getAppInfo {"id":"my.home"}' "$T/calls")" = 2
cleanup
nochoice 8
live '{"home":"slow.app"}'
mkdir -p "$T/apps/slow.app"
echo '{"id":"slow.app"}' > "$T/apps/slow.app/appinfo.json"
run
check "files on disk, sam does not know it yet: no write" test "$(writes)" = 0
check "files on disk: logged" has 'files are installed' "$T/log"
check "files on disk: no wait" hasnt SLEEP "$T/calls"
cleanup
nochoice 8
live '{"home":null,"kidsApp":"k.app"}'
run
check "home null: nothing asked" hasnt getAppInfo "$T/calls"
cleanup

echo "== leftover: a choice stored during the confirmation takes over"
nochoice 8
live '{"home":"gone.app","kidsApp":"k.app"}'
echo 'CHOICE my.home' > "$T/napq"
reply '{"home":"gone.app","kidsApp":"k.app"}'
HOMECHOOSER_CONFIRM_DELAY=15 run
check "not repaired" hasnt '"home":"com.webos.app.home"' "$T/calls"
check "logged" has 'a choice was stored meanwhile' "$T/log"
check "runs as the watcher: subscribed" has 'SUB luna://com.webos.settingsservice/getSystemSettings' "$T/calls"
check "the choice is written" has '{"defaultApps":{"home":"my.home","kidsApp":"k.app"}}' "$T/calls"
check "the subscription ends: exit 1" rc 1
cleanup

echo "== leftover: the record changed during the confirmation is left alone"
nochoice 8
live '{"home":"gone.app"}'
# the stub's sleep cannot rewrite the record, so the change is made by a second sleep stub
cat > "$T/bin/sleep2" <<EOF
#!/bin/sh
"$T/bin/sleep" "\$@"
echo '{"returnValue":true,"settings":{"defaultApps":{"home":"other.app"}}}' > "$T/record"
EOF
chmod +x "$T/bin/sleep2"
echo other.app >> "$T/installed"
HOMECHOOSER_SLEEP="$T/bin/sleep2" HOMECHOOSER_CONFIRM_DELAY=15 run
check "no write" test "$(writes)" = 0
check "logged" has 'home changed meanwhile' "$T/log"
cleanup

echo "== a stored choice at start: files on disk keep it from being taken as gone"
setup 8 'my.home\n'
live '{"home":"my.home"}'
grep -vx my.home "$T/installed" > "$T/i.new"; mv "$T/i.new" "$T/installed"
mkdir -p "$T/apps/my.home"
echo '{"id":"my.home"}' > "$T/apps/my.home/appinfo.json"
reply '{"home":"my.home"}'
run
check "no revert at start" test "$(writes)" = 0
check "logged" has 'files are installed' "$T/log"
cleanup
setup 8 'my.home\n'
live '{"home":"my.home"}'
reply '{"home":"my.home"}'
mkdir -p "$T/apps/my.home"
echo '{"id":"my.home"}' > "$T/apps/my.home/appinfo.json"
echo 'UNINSTALL my.home' >> "$T/stream"
echo APPS >> "$T/stream"
run
check "after start the files do not hold back a removal" has '"home":"com.webos.app.home"' "$T/calls"
cleanup

echo "== the stock home is closed only on 11 and later: never on 5, never on 6 to 10, whatever the launcher"
for major in 5 6 8 9 10 11 12; do
    setup $major 'com.webos.app.home\n'
    live '{"home":"com.webos.app.home"}'
    reply '{"home":"com.webos.app.home"}'
    echo APPS >> "$T/stream"
    echo 'CHOICE my.home' >> "$T/stream"
    echo APPS >> "$T/stream"
    run
    if [ "$major" -le 5 ]; then
        check "$major: no preload call" hasnt setPreloadPolicy "$T/calls"
    else
        check "$major: policy turned off" has 'setPreloadPolicy {"application":{"id":"com.webos.app.home","isEnabled":false}}' "$T/calls"
    fi
    if [ "$major" -ge 11 ]; then
        check "$major: stock home closed" has 'closeByAppId {"id":"com.webos.app.home","forceMode":true}' "$T/calls"
    else
        check "$major: stock home not closed" hasnt closeByAppId "$T/calls"
    fi
    if [ "$major" -ge 6 ] && [ "$major" -le 10 ]; then
        check "$major: logged" has "webOS $major: stock home left running until the next reboot" "$T/log"
    fi
    cleanup
done

echo "$PASS passed, $FAIL failed ($SH)"
[ "$FAIL" = 0 ]
