#!/bin/sh
remember_title() {
    [ "$NOTIFY_ON" = 1 ] || return 0
    [ "$TITLE_ID" = "$1" ] && return 0
    out=$(timed "$LUNA" -a "$HC_LUNA_IDENTITY" -n 1 "$SAM/getAppInfo" "{\"id\":\"$1\"}" 2>&1 | flatten)
    ok_reply "$out" || return 0
    t=$(printf '%s' "$out" | grep -o '"title"[[:space:]]*:[[:space:]]*"[^"\\]*"' | head -n 1 | sed 's/^[^:]*:[[:space:]]*"\(.*\)"$/\1/')
    if [ -z "$t" ]; then
        for d in $APPS_DIRS; do
            [ -f "$d/$1/appinfo.json" ] || continue
            t=$(flatten < "$d/$1/appinfo.json" | grep -o '"title"[[:space:]]*:[[:space:]]*"[^"\\]*"' | head -n 1 | sed 's/^[^:]*:[[:space:]]*"\(.*\)"$/\1/')
            break
        done
    fi
    TITLE_ID=$1
    TITLE_VAL=$(printf '%s' "$t" | tr -d '\000-\037' | sed -e 's/^ *//' -e 's/ *$//')
}

# notify_call <method> <payload>: one call to the notification service with HC_LUNA_IDENTITY.
# A short timeout; the reply is logged and kept in NOTIFY_OUT; succeeds on returnValue true.
notify_call() {
    nc_try=0
    while :; do
        NOTIFY_OUT=$(timed_n 5 "$LUNA" -a "$HC_LUNA_IDENTITY" -n 1 "$NOTIF/$1" "$2" 2>&1 | flatten)
        log "notification $1: ${NOTIFY_OUT:-no reply}"
        ok_reply "$NOTIFY_OUT" && return 0
        # A false reply right after the compositor restart ("Alert is blocked by [ system  ui ]",
        # "UI is not yet ready", seen on 9.2.1) is transient: try again for up to NOTIFY_RETRIES
        # times, NOTIFY_RETRY_WAIT seconds apart. No reply at all is not retried.
        printf '%s' "$NOTIFY_OUT" | grep -q '"returnValue"[[:space:]]*:[[:space:]]*false' || return 1
        nc_try=$((nc_try + 1))
        [ "$nc_try" -lt "${NOTIFY_RETRIES:-8}" ] || return 1
        $SLEEP "${NOTIFY_RETRY_WAIT:-3}"
    done
}

# notify_toast <message>: an informational toast. Best effort.
notify_toast() {
    [ "$NOTIFY_ON" = 1 ] || return 0
    notify_call createToast "{\"sourceId\":\"$HC_ID\",\"noaction\":true,\"message\":\"$1\"}" || :
    return 0
}

# notify_alert <message>: an alert with one OK button; a reply that says false (refused) is shown
# as a toast with the same message, and logged. No reply at all is not retried. Best effort.
notify_alert() {
    [ "$NOTIFY_ON" = 1 ] || return 0
    if notify_call createAlert "{\"sourceId\":\"$HC_ID\",\"title\":\"Home Chooser\",\"message\":\"$1\",\"buttons\":[{\"label\":\"OK\",\"focus\":true}]}"; then
        return 0
    fi
    if printf '%s' "$NOTIFY_OUT" | grep -q '"returnValue"[[:space:]]*:[[:space:]]*false'; then
        log "the alert was refused, showing the message as a toast"
        notify_toast "$1"
    fi
    return 0
}

# announce_removed <id>: the chosen app was removed and the stock home is back.
announce_removed() {
    name=$1
    [ "$TITLE_ID" = "$1" ] && [ -n "$TITLE_VAL" ] && name=$TITLE_VAL
    notify_alert "$name was removed, so the Home button opens LG Home again."
}

# app_state <id>: yes (installed), no (getAppInfo answers returnValue false, which is what an
# uninstalled id gets) or unknown (no answer).
