#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Shared webOS service-script helpers. Callers set LOG, OSRELEASE, LUNA and TIMEOUT.
HC_SERVICE_SCRIPTS=/media/developer/apps/usr/palm/services/org.webosbrew.homechooser.service/scripts
HC_STATE_DIR=/var/lib/webosbrew/homechooser
HC_RUNTIME_DIR=/tmp/homechooser-runtime
HC_BOOT_DIR=$HC_STATE_DIR/boot
HC_LUNA_IDENTITY=com.webos.applicationManager
HC_STOCK_HOME=com.webos.app.home
HC_STOCK_FULLHOME=com.webos.app.fullhome

log() {
    msg="$(date '+%Y-%m-%d %H:%M:%S') $*"
    [ "${HC_LOG_ECHO:-0}" = 1 ] && echo "$msg"
    echo "$msg" >> "$LOG" 2>/dev/null
    if [ -n "${LOG_MAX:-}" ]; then
        size=$(wc -c < "$LOG" 2>/dev/null || echo 0)
        if [ "$((size + 0))" -gt "$LOG_MAX" ]; then
            tail -n 200 "$LOG" > "$LOG.$$.tmp" 2>/dev/null && mv "$LOG.$$.tmp" "$LOG"
        fi
    fi
    return 0
}

major_version() {
    for key in VERSION_ID VERSION PRETTY_NAME; do
        v=$(sed -n "s/^$key=//p" "$OSRELEASE" 2>/dev/null | sed -n 's/[^0-9]*\([0-9][0-9]*\).*/\1/p' | head -n 1)
        if [ -n "$v" ]; then echo "$v"; return 0; fi
    done
    echo 0
}

detect_timeout() {
    [ -n "$TMO_FORM" ] && return 0
    TMO_FORM=none
    [ -n "$TIMEOUT" ] && command -v "$TIMEOUT" > /dev/null 2>&1 || return 0
    if "$TIMEOUT" 1 true > /dev/null 2>&1; then
        TMO_FORM=new
    elif "$TIMEOUT" -t 1 true > /dev/null 2>&1; then
        TMO_FORM=old
    fi
}

timed_n() {
    secs=$1
    shift
    detect_timeout
    case "$TMO_FORM" in
        new) "$TIMEOUT" "$secs" "$@" ;;
        old) "$TIMEOUT" -t "$secs" "$@" ;;
        *) "$@" ;;
    esac
}

timed() { timed_n 10 "$@"; }

call() { timed_n 10 "$LUNA" -a "$HC_LUNA_IDENTITY" -n 1 "$1" "$2" 2>&1; }

take_lock() {
    waited=0
    while ! mkdir "$LOCKDIR" 2>/dev/null; do
        owner=$(cat "$LOCKDIR/pid" 2>/dev/null)
        if [ -n "$owner" ] && ! kill -0 "$owner" 2>/dev/null; then
            rm -rf "$LOCKDIR"
            continue
        fi
        if [ "$waited" -ge "$LOCK_WAIT" ]; then
            log "ERROR: another homekey run holds $LOCKDIR (pid ${owner:-unknown}), giving up"
            exit 1
        fi
        sleep 1
        waited=$((waited + 1))
    done
    HAVE_LOCK=1
    echo $$ > "$LOCKDIR/pid"
}
