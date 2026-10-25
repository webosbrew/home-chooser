#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
do_status() {
    [ -f "$REWRITER" ] || echo data=missing
    [ ! -f "$CACHE/generation" ] || echo "cache-generation=$(cat "$CACHE/generation")"
    reboot=no
    if any_mounted; then
        echo mounted=yes
        cat "$WORK/applied-status" 2>/dev/null
        [ -f "$HOMEKEY_FLAG" ] || reboot=needed
        if ! cache_status; then
            echo hook=stale
            echo "reason=$CACHE_REASON"
            [ -f "$HOMEKEY_FLAG" ] && reboot=needed
        fi
    else
        echo mounted=no
        if cache_status; then
            cat "$CACHE/status"
            [ -f "$HOMEKEY_FLAG" ] && reboot=needed
        else
            echo hook=stale
            [ -f "$STALE" ] || echo "reason=$CACHE_REASON"
            echo patch=refused
        fi
    fi
    if [ -f "$STALE" ]; then
        [ -f "$CACHE/manifest.json" ] && echo hook=stale
        echo "reason=$(cat "$STALE")"
        [ -f "$HOMEKEY_FLAG" ] && [ -f "$CACHE/manifest.json" ] && reboot=needed
    fi
    [ ! -f "$WORK/recovered" ] || cat "$WORK/recovered"
    echo "reboot=$reboot"
}

