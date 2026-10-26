#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
log_stock_left_running() {
    [ "$MAJOR" -ge 6 ] && [ "$MAJOR" -le 10 ] &&
        log "webOS $MAJOR: stock home left running until the next reboot (sam relaunches it as the last-input app)"
    return 0
}

# preload_set <true|false>: one setPreloadPolicy call; logs the reply, succeeds on returnValue true.
preload_set() {
    out=$(timed_n 5 "$LUNA" -a "$HC_LUNA_IDENTITY" -n 1 "$PRELOAD/setPreloadPolicy" "{\"application\":{\"id\":\"$STOCK_HOME\",\"isEnabled\":$1}}" 2>&1 | flatten)
    log "setPreloadPolicy $STOCK_HOME isEnabled=$1: ${out:-no reply}"
    ok_reply "$out"
}

# preload_boot_off: in the background, at a watcher start. The preload manager may not answer yet
# this early in the boot (measured on 9.2.1: the first call timed out), so the call is retried every
# PRELOAD_RETRY seconds, up to PRELOAD_TRIES times; then the boot is given PRELOAD_SETTLE seconds
# before a foreground-dependent stock-home close is allowed.
preload_boot_off() {
    trap 'kill "$CP" 2>/dev/null; exit 0' TERM INT HUP
    n=0
    while :; do
        [ "$(cat "$TMP/preload" 2>/dev/null)" = off ] || exit 0
        preload_set false && break
        n=$((n + 1))
        [ "$n" -ge "$PRELOAD_TRIES" ] && exit 0
        $PRELOAD_SLEEP "$PRELOAD_RETRY" &
        CP=$!
        wait "$CP"
    done
    $PRELOAD_SLEEP "$PRELOAD_SETTLE" &
    CP=$!
    wait "$CP"
    [ "$(cat "$TMP/preload" 2>/dev/null)" = off ] || exit 0
    log_stock_left_running
    : > "$TMP/preload-ready"
    [ -p "$TMP/events" ] && echo B > "$TMP/events"
}

# preload_sync <on|off> [start]: bring the stock home's preload policy to the wanted state, once
# per change of state (PRELOAD_STATE starts empty, so once per watcher start too). Never
# "permanent", so the preload manager keeps it in memory only. Whatever happens, the caller goes on.
preload_sync() {
    [ "$PRELOAD_ON" = 1 ] && [ "$MAJOR" -ge 6 ] || return 0
    [ "$PRELOAD_STATE" = "$1" ] && return 0
    if [ "$1" = on ]; then
        flag=true
        # an "on" before anything was changed has nothing to undo, except at a start or an exit
        # (the policy may have been left disabled by an earlier run of the watcher)
        [ -z "$PRELOAD_STATE" ] && [ -z "$2" ] && { PRELOAD_STATE=on; return 0; }
    else
        flag=false
    fi
    PRELOAD_STATE=$1
    echo "$1" > "$TMP/preload" 2>/dev/null
    [ "$1" = on ] && rm -f "$TMP/preload-ready"
    if [ "$1" = off ] && [ "$2" = start ]; then
        [ -n "$CLOSE_PID" ] && kill "$CLOSE_PID" 2>/dev/null
        preload_boot_off > /dev/null 2>&1 &
        CLOSE_PID=$!
        return 0
    fi
    preload_set "$flag" || return 0
    if [ "$1" = off ]; then
        log_stock_left_running
        : > "$TMP/preload-ready"
    fi
    return 0
}

# preload_follow <choice> [start]: the stock home is not preloaded while a third-party home is
# chosen and installed.
preload_follow() {
    if [ -n "$MISSING" ] || [ "$1" = "$STOCK_HOME" ] || [ "$1" = "$STOCK_FULLHOME" ]; then
        preload_sync on "$2"
    else
        remember_title "$1"
        preload_sync off "$2"
    fi
}

# --- Telling the user about a removal ---------------------------------------------------------

# remember_title <id>: the chosen app's title, from getAppInfo (failing that its appinfo.json),
# while it is installed; once it is gone it cannot be looked up. Cached per choice in TITLE_ID and
# TITLE_VAL: an installed app without a title is not asked again, a getAppInfo that does not answer is.
