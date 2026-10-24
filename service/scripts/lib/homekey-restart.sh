#!/bin/sh
compositor_dependants() {
    if [ -n "${HOMECHOOSER_DEPENDANTS+set}" ]; then
        echo "$HOMECHOOSER_DEPENDANTS"
        return 0
    fi
    for f in "$ROOT/etc/systemd/system/lsm-ready.service" "$ROOT/lib/systemd/system/lsm-ready.service"; do
        [ -f "$f" ] || continue
        list=$(sed -n 's|^ExecStart=[^ ]*systemctl start \([A-Za-z0-9@._-]*\.service\)[[:space:]]*$|\1|p' "$f" | tr '\n' ' ')
        if [ -n "$list" ]; then
            echo $list
            return 0
        fi
    done
    echo "$DEPENDANTS_DEFAULT"
}

# unit_running <unit>: active, or on its way there (the same set systemd try-restarts).
unit_running() {
    case "$("$SYSTEMCTL" is-active "$1" 2>/dev/null)" in
        active|activating|reloading) return 0 ;;
    esac
    return 1
}

# restart_sequence <dependants...>: stop the dependants that are running, restart the compositor,
# start them again. The daemon is Type=notify and its READY=1 is sent by the initctl shim when the
# compositor emits lsm-ready. This proves process readiness; the boot path also requires the
# QML-served method before clients connect. A client left running while
# the compositor goes away exits on a broken pipe, is respawned by Restart=on-failure while the new
# compositor is still starting, and WebAppMgr can crash there (9.2.1, scratch/wam-crash/notes.md).
restart_sequence() {
    was=$(cat "$WORK/restart-clients" 2>/dev/null)
    for u in "$@"; do
        case " $was " in *" $u "*) continue ;; esac
        unit_running "$u" && was="$was $u"
    done
    printf '%s\n' "$was" > "$WORK/restart-clients"
    for u in $was; do
        log "stopping $u"
        "$SYSTEMCTL" stop "$u" || log "ERROR: could not stop $u"
    done
    r=0
    log "restarting $UNIT"
    if "$SYSTEMCTL" restart "$UNIT"; then
        log "$UNIT restarted"
    else
        log "ERROR: could not restart $UNIT"
        echo "systemctl restart $UNIT failed" > "$WORK/restart-reason"
        # Keep clients stopped until a ready compositor exists; boot recovery retries stock.
        return 1
    fi
    if [ "$VERIFY_QML" = 1 ]; then
        qml_ready || return 1
    fi
    for u in $was; do
        log "starting $u"
        "$SYSTEMCTL" start "$u" || { log "ERROR: could not start $u"; r=1; }
    done
    rm -f "$WORK/restart-clients"
    return $r
}

# restart_compositor: restart_sequence in a child of its own, waited for. The child does not hold
# the caller's stdout or stderr (Home Chooser's service reads them through pipes and SIGKILLs this
# worker at its timeout), so the stop and the start cannot be separated by the death of this
# process or of the web app that asked for the run, which the stop of webapp-mgr closes. Its
# output is copied to ours afterwards.
restart_compositor() {
    deps=$(compositor_dependants)
    out="$WORK/restart.$$.out"
    (
        trap '' HUP INT PIPE
        restart_sequence $deps
    ) < /dev/null > "$out" 2>&1 &
    wait $!
    r=$?
    cat "$out" 2>/dev/null
    rm -f "$out"
    return $r
}

