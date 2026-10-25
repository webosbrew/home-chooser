#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
undo_mounts() {
    [ -f "$WORK/mounted" ] || return 0
    failed=0
    while read -r t; do
        if is_mounted "$t"; then
            "$UMOUNT" "$t" 2>/dev/null || "$UMOUNT" -l "$t" 2>/dev/null || failed=1
        fi
    done < "$WORK/mounted"
    [ "$failed" = 0 ] || return 1
    rm -f "$WORK/mounted"
}

do_apply() {
    detect_timeout
    # `apply samfirst`: restart sam before anything else. The settings app asks for this when its
    # first write created general/defaultApps (webOS 6 to 8: sam misses that write until it
    # restarts). Restarting sam brings the stock home forward and closes the settings panel, so
    # the restart has to be part of this one root-side run: a panel that is gone cannot make the
    # next call. Whatever happens below, the restart has been done.
    if [ "$1" = samfirst ]; then
        log "restarting sam"
        "$SYSTEMCTL" restart sam || log "ERROR: could not restart sam"
    fi
    if any_mounted; then
        log "Home key hook already mounted, nothing to do"
        return 0
    fi
    if ! cache_status; then
        reason=$CACHE_REASON
        printf '%s\n' "$reason" > "$STALE"
        log "Home key hook cache stale ($reason); leaving stock for this boot"
        rm -rf "$CACHE"
        return 0
    fi
    rm -rf "$WORK/stage"
    rm -f "$WORK/manifest.json" "$WORK/applied-status" "$WORK/recovered" "$WORK/restart-reason"
    cp -R "$CACHE/stage" "$WORK/stage" || return 1
    cp "$CACHE/manifest.json" "$WORK/manifest.json" || return 1
    cp "$CACHE/status" "$WORK/applied-status" || return 1
    cat "$WORK/applied-status" >> "$LOG"
    entries "$WORK/manifest.json" > "$AENT"
    # Put the stored choice into the copies that carry the placeholder.
    cid=$(baked_id)
    while read -r p; do
        if has_configured "$WORK/stage$p"; then
            set_configured "$WORK/stage$p" "$cid" || {
                log "ERROR: cannot set the configured home in $p, hook not applied"
                rm -rf "$WORK/stage" "$AENT"
                return 1
            }
            log "configured home in $p: '${cid:-none}'"
        fi
    done < "$AENT"

    # Test-only fault injection, after the real rewrite, before any mount. Never enabled by the app.
    if [ "${HOMECHOOSER_DEBUG_BREAK_SETTINGS:-0}" = 1 ]; then
        broken="$WORK/stage/usr/lib/qml/WebOSCompositorBase/global/Settings.qml"
        [ -f "$broken" ] || { log "ERROR: debug Settings.qml target missing"; return 1; }
        log "DEBUG: injecting a Settings.qml syntax error into the staged copy"
        printf '\nHOMECHOOSER DEBUG BROKEN QML !!!\n' >> "$broken" || return 1
    fi

    # Bind-mount the copies; if one fails, undo the ones already mounted.
    : > "$WORK/mounted"
    while read -r p; do
        if "$MOUNT" --bind "$WORK/stage$p" "$ROOT$p"; then
            echo "$ROOT$p" >> "$WORK/mounted"
            log "mounted $p"
        else
            log "ERROR: cannot mount over $p, hook not applied"
            undo_mounts || { log "ERROR: partial mounts remain; keeping stage and manifest"; return 1; }
            rm -rf "$WORK/stage" "$AENT"
            return 1
        fi
    done < "$AENT"
    rm -f "$AENT"
    VERIFY_QML=1
    if restart_compositor; then rm -f "$STALE"; return 0; fi
    failure=$(cat "$WORK/restart-reason" 2>/dev/null)
    [ -n "$failure" ] || failure="compositor restart sequence failed"
    log "ERROR: boot compositor restart failed: $failure; unmounting the hook and restarting once to stock"
    undo_mounts || { log "ERROR: recovery could not unmount all targets; keeping stage"; return 1; }
    if restart_compositor; then
        log "stock compositor recovery succeeded"
        printf 'recovered=stock\nreason=%s\n' "$failure" > "$WORK/recovered"
    else
        log "ERROR: stock compositor recovery failed: $(cat "$WORK/restart-reason" 2>/dev/null)"
        printf 'recovered=failed\nreason=%s; stock recovery: %s\n' "$failure" "$(cat "$WORK/restart-reason" 2>/dev/null)" > "$WORK/recovered"
    fi
    rm -rf "$WORK/stage"
    rm -f "$WORK/manifest.json" "$WORK/applied-status"
    return 1
}

do_stop() {
    unmounted=0
    for m in "$WORK/manifest.json"; do
        [ -f "$m" ] || continue
        entries "$m" > "$ETMP"
        while read -r p; do
            tries=0
            while is_mounted "$ROOT$p" && [ "$tries" -lt 3 ]; do
                tries=$((tries + 1))
                if "$UMOUNT" "$ROOT$p" 2>/dev/null || "$UMOUNT" -l "$ROOT$p" 2>/dev/null; then
                    log "unmounted $p"
                    unmounted=1
                else
                    log "ERROR: cannot unmount $p"
                    break
                fi
            done
        done < "$ETMP"
    done
    # Keep the target list and mounted sources if an unmount failed, so status and a
    # later stop can still find them.
    if any_mounted; then
        log "ERROR: targets remain mounted; keeping stage and manifest for a later stop"
        return 1
    fi
    rm -rf "$WORK/stage" "$WORK/mounted" "$WORK/applied-status" "$AENT" "$ETMP" "$WORK/manifest.json"
    if [ "$unmounted" = 1 ]; then
        restart_compositor
    else
        log "nothing mounted, nothing to do"
    fi
}

do_refresh() {
    detect_timeout
    any_mounted || { log "nothing mounted, nothing to refresh"; return 0; }
    cid=; stock=0; restart=0
    for a in "$@"; do
        case "$a" in
            stock) stock=1 ;;
            restart) restart=1 ;;
            norestart) ;;
        esac
    done
    [ "$stock" = 1 ] || cid=$(baked_id)
    changed=0
    entries "$WORK/manifest.json" > "$ETMP" 2>/dev/null
    while read -r p; do
        f="$WORK/stage$p"
        has_configured "$f" || continue
        grep -qF "configuredHomeAppId: \"$cid\"" "$f" && continue
        set_configured "$f" "$cid" && { log "configured home in $p: '${cid:-none}'"; changed=1; }
    done < "$ETMP"
    rm -f "$ETMP"
    if [ "$changed" != 1 ]; then
        log "configured home unchanged"
    elif [ "$restart" = 1 ]; then
        restart_compositor
    else
        log "configured home updated; the running compositor keeps its loaded id until the next start (the settings record is read first)"
    fi
}

