#!/bin/sh
handle() {
    flat=$(printf '%s' "$1" | flatten)
    case "$flat" in
        *'"settings"'*|*'no matched result'*) ;;
        *) return 0 ;;
    esac
    want=$(read_choice) || finish "choice file is gone, exiting" 0
    if [ -n "$MISSING" ]; then
        if [ "$MISSING" = "$want" ]; then return 0; fi
        # the choice was changed to another app while the old one was missing
        MISSING=
        kf_refresh
        BAKED=$want
    fi
    preload_follow "$want"
    sync_baked "$want"
    found=$(home_of "$flat")
    [ "$found" = "$want" ] && return 0
    reconcile "$want" "$found"
}

# feed <tag> <ping>: read a luna-send subscription on stdin, one line per complete reply (its
# braces balance, so both one-line and pretty-printed output work): "<tag> <reply on one line>",
# or just "<tag>" if ping is 1. A last "E <tag>" says the stream ended.
feed() {
    awk -v tag="$1" -v ping="$2" '
        {
            n = gsub(/[{]/, "{"); c = gsub(/[}]/, "}")
            depth += n - c
            buf = buf $0 " "
            if (depth <= 0) {
                if (ping) print tag; else print tag " " buf
                fflush()
                buf = ""
                depth = 0
            }
        }
        END { print "E " tag; fflush() }'
}

# The periodic fallback check: a line "T" every RECHECK seconds.
ticker() {
    trap 'kill "$TP" 2>/dev/null; exit 0' TERM INT HUP
    while :; do
        $TIMER_SLEEP "$RECHECK" &
        TP=$!
        wait "$TP"
        echo T
    done
}

