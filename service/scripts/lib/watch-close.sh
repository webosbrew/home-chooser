#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Stock Home may be closed on 11+ only when another full-screen card holds the foreground.
# Overlay launchers also need a fresh launch event: SAM reports the card beneath an overlay as
# foreground, and closing that card when it is LG Home closes the overlay too.

close_choice() {
    [ "$MAJOR" -ge 11 ] && [ "$PRELOAD_ON" = 1 ] || return 1
    choice=$(read_choice) || return 1
    if [ "$CLOSE_CHOICE" != "$choice" ]; then
        CLOSE_CHOICE=$choice
        CLOSE_DONE=
        CLOSE_WINDOW=
        CLOSE_LAUNCHED=
        STOCK_RELAUNCH_LOGGED=
    fi
    [ -z "$MISSING" ] && [ -f "$TMP/preload-ready" ] && [ "$CLOSE_DONE" != "$choice" ] || return 1
    case "$choice" in
        "$STOCK_HOME"|"$STOCK_FULLHOME") return 1 ;;
    esac
    return 0
}

close_window() {
    [ -n "$CLOSE_WINDOW" ] && return 0
    info=$(timed_n 5 "$LUNA" -a "$HC_LUNA_IDENTITY" -n 1 "$SAM/getAppInfo" "{\"id\":\"$CLOSE_CHOICE\"}" 2>&1 | flatten)
    if ! ok_reply "$info"; then
        log "cannot inspect window type for $CLOSE_CHOICE; leaving stock home running"
        return 1
    fi
    if printf '%s' "$info" | grep -q '"defaultWindowType"[[:space:]]*:[[:space:]]*"overlay"'; then
        CLOSE_WINDOW=overlay
    else
        CLOSE_WINDOW=card
    fi
}

event_app() {
    printf '%s' "$1" | sed -n 's/.*"appId"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'
}

foreground_now() {
    reply=$(timed_n 5 "$LUNA" -a "$HC_LUNA_IDENTITY" -n 1 "$SAM/getForegroundAppInfo" '{}' 2>&1 | flatten)
    ok_reply "$reply" || return 1
    event_app "$reply"
}

# maybe_close_stock <foreground id> <fresh chosen-launcher launch: yes|no>
maybe_close_stock() {
    close_choice || return 0
    close_window || return 0
    foreground=$1
    if [ "$CLOSE_WINDOW" = overlay ]; then
        [ "$2" = yes ] || return 0
        # The selected overlay is in front, but SAM names the card beneath it. A blank card or
        # LG Home beneath it would make SAM's idle fallback replace the overlay after the close.
        case "$foreground" in
            ''|"$STOCK_HOME"|"$STOCK_FULLHOME"|"$HC_ID")
                log "overlay $CLOSE_CHOICE has no safe full-screen card beneath it; stock home left running"
                return 0 ;;
        esac
    else
        [ "$foreground" = "$CLOSE_CHOICE" ] || return 0
    fi
    CLOSE_DONE=$CLOSE_CHOICE
    CLOSE_LAUNCHED=
    out=$(timed_n 5 "$LUNA" -a "$HC_LUNA_IDENTITY" -n 1 "$SAM/closeByAppId" "{\"id\":\"$STOCK_HOME\",\"forceMode\":true}" 2>&1 | flatten)
    log "closeByAppId $STOCK_HOME after $CLOSE_CHOICE in front: ${out:-no reply}"
}

close_if_foreground() {
    close_choice || return 0
    id=$(foreground_now) || return 0
    if [ "$CLOSE_LAUNCHED" = "$CLOSE_CHOICE" ]; then
        maybe_close_stock "$id" yes
    else
        maybe_close_stock "$id" no
    fi
}

handle_foreground() {
    id=$(event_app "$1")
    [ -n "$id" ] || return 0
    if [ "$CLOSE_LAUNCHED" = "$CLOSE_CHOICE" ]; then
        maybe_close_stock "$id" yes
    else
        maybe_close_stock "$id" no
    fi
    if [ -n "$CLOSE_DONE" ] && [ "$id" = "$STOCK_HOME" ] && [ -z "$STOCK_RELAUNCH_LOGGED" ]; then
        log "sam brought $STOCK_HOME to the foreground after its close; not closing it again this boot"
        STOCK_RELAUNCH_LOGGED=1
    fi
}

handle_life() {
    flat=$(printf '%s' "$1" | flatten)
    id=$(event_app "$flat")
    close_choice || :
    if [ "$id" = "$CLOSE_CHOICE" ]; then
        case "$flat" in
            *'"event":"close"'*|*'"event":"background"'*) CLOSE_LAUNCHED=; return 0 ;;
        esac
    fi
    case "$flat" in
        *'"event":"launch"'*|*'"event":"relaunch"'*|*'"event":"foreground"'*) ;;
        *) return 0 ;;
    esac
    if [ "$id" = "$STOCK_HOME" ] && [ -n "$CLOSE_DONE" ] && [ -z "$STOCK_RELAUNCH_LOGGED" ]; then
        log "sam relaunched $STOCK_HOME after its close; not closing it again this boot"
        STOCK_RELAUNCH_LOGGED=1
    fi
    [ "$id" = "$CLOSE_CHOICE" ] || return 0
    CLOSE_LAUNCHED=$id
    foreground=$(foreground_now) || return 0
    maybe_close_stock "$foreground" yes
}
