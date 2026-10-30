#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
now() {
    if [ -n "$CLOCK" ]; then $CLOCK; else date +%s; fi
}

# The major release, from os-release the way the app reads it.
# Copy what the removal path needs out of the service scripts directory: this script, the Home key hook worker
# and the access helper. Each file is
# replaced by rename, so a copy that is running is never rewritten in place.
stage_runtime() {
    mkdir -p "$RT" || return 1
    for f in homechooser-home-watch homechooser-homekey homechooser-access; do
        if [ ! -f "$1/$f" ]; then
            [ "$f" != homechooser-home-watch ] && continue
            return 1
        fi
        cp "$1/$f" "$RT/$f.new" && chmod 755 "$RT/$f.new" && mv "$RT/$f.new" "$RT/$f" || return 1
    done
    # Keep the existing executable available for a volatile restore after package removal.
    if [ -x "$1/../homechooser-service" ]; then
        cp "$1/../homechooser-service" "$RT/homechooser-service.new" &&
            chmod 755 "$RT/homechooser-service.new" &&
            mv "$RT/homechooser-service.new" "$RT/homechooser-service" || return 1
    fi
    if [ -d "$1/lib" ]; then
        mkdir -p "$RT/lib" || return 1
        for f in "$1"/lib/*.sh "$1"/lib/*.js; do
            [ -f "$f" ] || continue
            cp "$f" "$RT/lib/$(basename "$f").new" &&
                mv "$RT/lib/$(basename "$f").new" "$RT/lib/$(basename "$f")" || return 1
        done
    fi
}

# read_choice: print the stored home id. The file holds the id alone; surrounding spaces, tabs,
# CR and LF are trimmed. Fails when the file is absent, empty, or holds anything but letters,
# digits, '.', '-' and '_' (the invalid case is logged).
read_choice() {
    [ -f "$CHOICE" ] || return 1
    id=$(tr -d '\r' < "$CHOICE" | tr '\n\t' '  ' | sed -e 's/^ *//' -e 's/ *$//')
    [ -n "$id" ] || return 1
    if printf '%s' "$id" | grep -q '[^A-Za-z0-9._-]'; then
        log "ignoring invalid app id in $CHOICE: '$id'"
        return 1
    fi
    printf '%s' "$id"
}

# call <uri> <payload>: print luna-send's output.
flatten() {
    tr '\n' ' '
}

# home_of <flat reply>: the record's home, or nothing. The record may come as an object or as
# a JSON string (then its quotes are escaped).
home_of() {
    printf '%s' "$1" |
        sed -n 's/.*\\\{0,1\}"home\\\{0,1\}"[[:space:]]*:[[:space:]]*\\\{0,1\}"\([^"\\]*\)\\\{0,1\}".*/\1/p'
}

# record_body <flat reply>: the members of the defaultApps object, without braces. Prints
# nothing and fails when the record is there but not a flat object of members.
record_body() {
    case "$1" in
        *'"defaultApps"'*) ;;
        *) return 0 ;;
    esac
    body=$(printf '%s' "$1" |
        sed -n 's/.*"defaultApps"[[:space:]]*:[[:space:]]*{\([^{}]*\)}.*/\1/p')
    if [ -z "$body" ]; then
        body=$(printf '%s' "$1" |
            sed -n 's/.*"defaultApps"[[:space:]]*:[[:space:]]*"{\([^{}]*\)}".*/\1/p' | sed 's/\\"/"/g')
    fi
    if [ -z "$body" ]; then
        # An empty object is fine; anything else is not understood.
        printf '%s' "$1" | grep -q '"defaultApps"[[:space:]]*:[[:space:]]*"\{0,1\}{[[:space:]]*}' && return 0
        return 1
    fi
    printf '%s' "$body"
}

ok_reply() {
    printf '%s' "$1" | grep -q '"returnValue"[[:space:]]*:[[:space:]]*true'
}

# Pause without blocking signals.
nap() {
    $SLEEP "$1" &
    NAP_PID=$!
    wait "$NAP_PID" 2>/dev/null
    NAP_PID=
}

cleanup() {
    [ -n "$LUNA_PID" ] && kill $LUNA_PID 2>/dev/null
    [ -n "$FEED_PIDS" ] && kill $FEED_PIDS 2>/dev/null
    [ -n "$NAP_PID" ] && kill "$NAP_PID" 2>/dev/null
    [ -n "$CLOSE_PID" ] && kill "$CLOSE_PID" 2>/dev/null
    rm -rf "$TMP"
}

finish() {
    log "$1"
    [ "${2:-0}" -eq 0 ] && preload_sync on exit
    cleanup
    exit "${2:-0}"
}

# The merged setSystemSettings write: the existing entries are read back and kept. With an empty
# id `home` is written as null: sub-keys of the record can never be removed (a write without
# `home` only looks like a replace until the settings service restarts), and null reads as unset
# (docs/research/home-key-and-home-app.md, the 11.2.0 / 10.3.1 results).
