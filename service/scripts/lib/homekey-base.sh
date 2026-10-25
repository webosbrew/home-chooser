#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# entries <manifest>: print staged target paths, one per line, in order.
entries() {
    tr '{},' '\n\n\n' < "$1" |
        sed -En 's/^[[:space:]]*"(path)"[[:space:]]*:[[:space:]]*"([^"]*)".*$/\2/p'
}

# configured_id: print the stored home id, trimmed; nothing if absent, empty or invalid.
configured_id() {
    [ -f "$CHOICE" ] || return 0
    id=$(tr -d '\r' < "$CHOICE" | tr '\n\t' '  ' | sed -e 's/^ *//' -e 's/ *$//')
    [ -n "$id" ] || return 0
    if printf '%s' "$id" | grep -q '[^A-Za-z0-9._-]'; then
        log "ignoring invalid app id in $CHOICE: '$id'" >&2
        return 0
    fi
    printf '%s' "$id"
}

# detect_timeout: once, find how `timeout` is called: `timeout SECS CMD` (BusyBox 1.30 and later,
# GNU) or `timeout -t SECS CMD` (BusyBox before 1.30, e.g. 1.29.2 on webOS 5). Sets TMO_FORM to
# new, old or none.
# A C++ compositor can report READY=1 even when main.qml failed. getCurrentState is
# registered by the stock StarfishBaseLunaServiceAPI QML object, independently of tier 2.
# Require both success and its string-valued currentState, not merely a live Luna service.
# Each request is bounded to two seconds; the entire polling window is READY_WAIT (24s).
qml_ready() {
    detect_timeout
    deadline=$(($(cut -d. -f1 /proc/uptime) + READY_WAIT))
    ready_attempt=0
    reply="$WORK/ready.$$.out"
    while :; do
        ready_attempt=$((ready_attempt + 1))
        case "$TMO_FORM" in
            new) "$TIMEOUT" 2 "$LUNA" -a "$HC_LUNA_IDENTITY" -n 1 luna://com.webos.surfacemanager/getCurrentState '{}' > "$reply" 2>&1 ;;
            old) "$TIMEOUT" -t 2 "$LUNA" -a "$HC_LUNA_IDENTITY" -n 1 luna://com.webos.surfacemanager/getCurrentState '{}' > "$reply" 2>&1 ;;
            *)
                # No timeout applet: bound the native luna-send child ourselves and reap it.
                "$LUNA" -a "$HC_LUNA_IDENTITY" -n 1 luna://com.webos.surfacemanager/getCurrentState '{}' > "$reply" 2>&1 &
                ready_pid=$!
                for ready_tick in 1 2; do
                    kill -0 "$ready_pid" 2>/dev/null || break
                    sleep 1
                done
                kill "$ready_pid" 2>/dev/null
                wait "$ready_pid" 2>/dev/null
                ;;
        esac
        if grep -q '"returnValue"[[:space:]]*:[[:space:]]*true' "$reply" &&
           grep -q '"currentState"[[:space:]]*:[[:space:]]*"[^"]*"' "$reply"; then
            log "QML ready via getCurrentState (attempt $ready_attempt)"
            rm -f "$reply"
            return 0
        fi
        if [ "$(cut -d. -f1 /proc/uptime)" -ge "$deadline" ]; then
            reason="QML getCurrentState unavailable after $READY_WAIT s: $(tr '\n\r' '  ' < "$reply" | cut -c1-240)"
            printf '%s\n' "$reason" > "$WORK/restart-reason"
            log "ERROR: $reason"
            rm -f "$reply"
            return 1
        fi
        "$SLEEP" 1
    done
}

# app_state <id>: print yes (installed), no (sam answers returnValue false) or unknown (no
# answer). getAppInfo of an uninstalled id answers returnValue:false.
app_state() {
    detect_timeout
    case "$TMO_FORM" in
        new) set -- "$TIMEOUT" 10 "$LUNA" -a "$HC_LUNA_IDENTITY" -n 1 luna://com.webos.applicationManager/getAppInfo "{\"id\":\"$1\"}" ;;
        old) set -- "$TIMEOUT" -t 10 "$LUNA" -a "$HC_LUNA_IDENTITY" -n 1 luna://com.webos.applicationManager/getAppInfo "{\"id\":\"$1\"}" ;;
        *) set -- "$LUNA" -a "$HC_LUNA_IDENTITY" -n 1 luna://com.webos.applicationManager/getAppInfo "{\"id\":\"$1\"}" ;;
    esac
    out=$("$@" 2>&1 | tr '\n' ' ')
    if printf '%s' "$out" | grep -q '"returnValue"[[:space:]]*:[[:space:]]*true'; then
        echo yes
    elif printf '%s' "$out" | grep -q '"returnValue"[[:space:]]*:[[:space:]]*false'; then
        echo no
    else
        echo unknown
    fi
}

# baked_id: the id to put into the patched Settings.qml: the stored choice if that app is
# installed, else nothing (the stock home). Waits up to SAM_WAIT seconds for sam to answer.
baked_id() {
    bid=$(configured_id)
    [ -n "$bid" ] || return 0
    waited=0
    while :; do
        st=$(app_state "$bid")
        case "$st" in
            yes) printf '%s' "$bid"; return 0 ;;
            no) log "$bid is not installed, using the stock home" >&2; return 0 ;;
        esac
        if [ "$waited" -ge "$SAM_WAIT" ]; then
            log "sam did not answer for $bid in $SAM_WAIT s, using the stock home until the watcher refreshes it" >&2
            return 0
        fi
        "$SLEEP" 2
        waited=$((waited + 2))
    done
}

# set_configured <file> <id>: put <id> into the configuredHomeAppId line, in place (same inode,
# so a bind mount of the file sees it).
set_configured() {
    sed "s|configuredHomeAppId: \"[^\"]*\"|configuredHomeAppId: \"$2\"|" "$1" > "$1.new" &&
        cat "$1.new" > "$1"
    r=$?
    rm -f "$1.new"
    return $r
}

has_configured() {
    grep -q 'configuredHomeAppId: "' "$1" 2>/dev/null
}

is_mounted() {
    grep -qF " $1 " "$MOUNTS" 2>/dev/null
}

mkdir -p "$WORK" 2>/dev/null

# Temp files are named per invocation: status runs from the app, the watcher and the service can
# overlap, and a shared name let one run delete or truncate the list another was reading.
# ETMP is the read-only scratch list (any_mounted, status, refresh, stop); AENT is the
# apply list. Both are removed on every way out.
ETMP="$WORK/entries.$$.tmp"
AENT="$WORK/entries.$$"
LOCKDIR="$WORK/lock.d"
LOCK_WAIT=${HOMECHOOSER_LOCK_WAIT:-120}
HAVE_LOCK=0

cleanup() {
    rm -f "$ETMP" "$AENT"
    [ "$HAVE_LOCK" = 1 ] && rm -rf "$LOCKDIR"
    HAVE_LOCK=0
}
trap cleanup EXIT
trap 'exit 143' HUP INT TERM

# take_lock: apply, stop and refresh change mounts and the staged copies, so only one runs at a
# time. status takes no lock; it only reads, and reads a consistent answer at every step (the
# stock files match until the first bind mount, and a mount is only reported once it exists).
# A lock whose owner is gone is taken over; after LOCK_WAIT seconds the run gives up.
# Our staged target list owns the mounts.
any_mounted() {
    [ -f "$WORK/manifest.json" ] || return 1
    entries "$WORK/manifest.json" > "$ETMP"
    while read -r p; do
        if is_mounted "$ROOT$p"; then rm -f "$ETMP"; return 0; fi
    done < "$ETMP"
    rm -f "$ETMP"
    return 1
}

