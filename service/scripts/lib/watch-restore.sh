#!/bin/sh
app_state() {
    out=$(timed "$LUNA" -a "$HC_LUNA_IDENTITY" -n 1 "$SAM/getAppInfo" "{\"id\":\"$1\"}" 2>&1 | flatten)
    if ok_reply "$out"; then
        echo yes
    elif printf '%s' "$out" | grep -q '"returnValue"[[:space:]]*:[[:space:]]*false'; then
        echo no
    else
        echo unknown
    fi
}

# on_disk <id>: the app's appinfo.json is in one of INSTALL_DIRS, i.e. it is installed even if sam
# does not know it yet.
on_disk() {
    for d in $INSTALL_DIRS; do
        [ -f "$d/$1/appinfo.json" ] && return 0
    done
    return 1
}

# hc_gone: Home Chooser is not installed, by getAppInfo or by its directory.
hc_gone() {
    [ -d "$APPDIR" ] || return 0
    [ "$(app_state "$HC_ID")" = no ]
}

# kf_refresh [stock]: on webOS 6 to 9, bring the id baked into the mounted Settings.qml in line
# (the worker never restarts the compositor here, and does nothing if not mounted).
kf_refresh() {
    [ "$MAJOR" -ge 6 ] && [ "$MAJOR" -le 9 ] || return 0
    [ -f "$HOMEKEY" ] || return 0
    $WSH "$HOMEKEY" refresh $1 >> "$LOG" 2>&1
}

# sync_baked <choice>: the stored choice changed while the app is installed: write it into the
# baked copy without restarting the compositor. The record is read first by the compositor's
# lookup and the watcher has already put the choice there, so the baked id is only the fallback
# for the next compositor start. A choice whose app is not installed (or whose state is unknown)
# is left to check_apps.
sync_baked() {
    [ "$1" = "$BAKED" ] && return 0
    [ "$(app_state "$1")" = yes ] || return 0
    log "choice changed to $1, updating the baked home id"
    kf_refresh
    BAKED=$1
}

# remove_home: write `home` as null in the record, keeping the other entries.
remove_home() {
    reply=$(call "$SETTINGS/getSystemSettings" '{"category":"general","keys":["defaultApps"]}' | flatten)
    if [ -z "$(home_of "$reply")" ]; then
        log "the record has no home, nothing to remove"
        return 0
    fi
    merged_write "" || return 1
    left=$(current_home)
    if [ -n "$left" ]; then
        log "home is still '$left' after writing it as null"
        return 1
    fi
    log "home cleared (null) in the record"
}

# drop_override: webOS 10 and later, remove our profile override file and rescan.
drop_override() {
    [ "$MAJOR" -ge 10 ] && [ -f "$OVERRIDE" ] || return 0
    rm -f "$OVERRIDE"
    log "removed $OVERRIDE"
    out=$(call "$PROFILE/debug/rescan" '{}' | flatten)
    log "debug/rescan: $out"
}

# revert_platform: put the platform back to stock. Idempotent: it only writes what is not stock.
revert_platform() {
    log "putting the platform back to stock ($1)"
    if [ "$MAJOR" -ge 5 ] && [ "$MAJOR" -le 9 ]; then
        # 5.x: the Home key hook launches the record's home directly, so name a stock launcher.
        # 6 to 9: the patched QML reads the record first and falls back to the id it loaded at
        # compositor start (the old launcher, until a reboot), so the stock id is written, not
        # null; stock firmware ignores the record for the Home key, so it can stay after an uninstall.
        stock=$STOCK_HOME
        [ "$(app_state "$STOCK_FULLHOME")" = yes ] && stock=$STOCK_FULLHOME
        found=$(current_home)
        if [ "$found" = "$stock" ]; then
            log "home is already $stock"
        else
            merged_write "$stock" || return 1
        fi
        return 0
    fi
    drop_override
    remove_home
}

# restore_all: Home Chooser is uninstalled. Everything it did to the platform is undone, from files
# that do not live in the app directory, and the watcher ends.
restore_all() {
    log "Home Chooser is uninstalled, restoring the stock Home"
    [ -f "$SRC/homechooser-access" ] || finish "launcher access worker missing, restore pending" 1
    $WSH "$SRC/homechooser-access" remove >> "$LOG" 2>&1 ||
        finish "could not remove launcher access, restore pending" 1
    revert_platform "Home Chooser uninstalled" || finish "could not restore stock Home, restore pending" 1
    rm -f "$CHOICE" "$KEYFLAG"
    rm -rf "${HOMECHOOSER_CACHE:-/var/lib/webosbrew/homechooser/hook}"
    rm -f "${HOMECHOOSER_STALE:-/var/lib/webosbrew/homechooser/hook-stale}"
    for l in "$INITD"; do
        if [ -L "$l" ]; then
            rm -f "$l"
            log "removed $l"
        fi
    done
    rm -rf "$BOOT_DIR"
    rmdir "$(dirname "$CHOICE")" 2>/dev/null
    # No unmount and no compositor restart: the mounts stay until the next boot.
    notify_toast "Home Chooser was removed. The Home button opens LG Home again."
    finish "stock Home restored, exiting" 0
}

# check_hc: Home Chooser is gone, and still gone after the confirmation delay: the full restore.
check_hc() {
    if hc_gone; then
        nap "$CONFIRM_DELAY"
        if hc_gone; then
            restore_all
        fi
        log "Home Chooser is back, not removing anything"
    fi
}

# check_apps <why>: look at Home Chooser and at the chosen app.
check_apps() {
    why=$1
    check_hc
    want=$(read_choice) || finish "choice file is gone, exiting" 0
    [ -n "$MISSING" ] && [ "$MISSING" != "$want" ] && MISSING=
    st=$(app_state "$want")
    if [ "$st" = no ] && [ "$why" = start ] && on_disk "$want"; then
        log "getAppInfo does not know $want yet but its files are installed, leaving things as they are"
        st=unknown
    fi
    case "$st" in
        no)
            if [ "$MISSING" != "$want" ]; then
                nap "$CONFIRM_DELAY"
                # Home Chooser may have been removed while this waited, and its own message
                # may be queued behind this check or never come: look again before acting.
                check_hc
                if [ "$(app_state "$want")" = no ]; then
                    log "$want is not installed, stock Home in use until it comes back"
                    MISSING=$want
                    revert_platform "$want not installed"
                    kf_refresh stock
                    BAKED=
                    [ "$why" = start ] || announce_removed "$want"
                fi
            fi
            ;;
        yes)
            if [ -n "$MISSING" ]; then
                log "$want is installed again, applying the choice"
                MISSING=
                LAST_WRITE=0
                STREAK=0
                if [ "$(current_home)" != "$want" ]; then apply_record "$want"; fi
                check_override "$want"
                kf_refresh
                BAKED=$want
            elif [ "$why" = start ]; then
                kf_refresh
                BAKED=$want
            else
                sync_baked "$want"
            fi
            ;;
        *)
            log "getAppInfo did not answer for $want, leaving things as they are"
            ;;
    esac
    preload_follow "$want" "$why"
    [ -n "$CHECK_MARK" ] && echo "$why" >> "$CHECK_MARK"
    return 0
}

# --- No stored choice: a leftover Home -----------------------------------------------------

# leftover_gone <id>: the id is not installed: getAppInfo says so twice, CONFIRM_DELAY apart, and its
# files are not on disk. Stock ids, an id getAppInfo does not answer for, and an installed one fail.
leftover_gone() {
    case "$1" in "$STOCK_HOME"|"$STOCK_FULLHOME") return 1 ;; esac
    st=$(app_state "$1")
    if [ "$st" = unknown ]; then
        log "getAppInfo did not answer for $1, leaving things as they are"
        return 1
    fi
    [ "$st" = no ] || return 1
    if on_disk "$1"; then
        log "getAppInfo does not know $1 yet but its files are installed, leaving things as they are"
        return 1
    fi
    nap "$CONFIRM_DELAY"
    [ "$(app_state "$1")" = no ] && ! on_disk "$1"
}

# repair_leftover: no usable stored choice. If the record's home (or, on webOS 10 and later, our
# override file's) names an app that is not installed, an earlier Home Chooser was removed or stopped
# while it could not restore: put the platform back to stock the way a removal of the chosen app
# does. Nothing is written for a choice stored while this confirms, and no notification is sent.
repair_leftover() {
    rec=$(current_home)
    ovr=
    if [ "$MAJOR" -ge 10 ] && [ -f "$OVERRIDE" ]; then
        ovr=$(home_of "$(flatten < "$OVERRIDE")")
    fi
    [ -n "$rec$ovr" ] || return 0
    gone_rec=
    gone_ovr=
    [ -n "$rec" ] && leftover_gone "$rec" && gone_rec=$rec
    if [ -n "$ovr" ]; then
        if [ "$ovr" = "$gone_rec" ] || { [ "$ovr" != "$rec" ] && leftover_gone "$ovr"; }; then gone_ovr=$ovr; fi
    fi
    if [ -z "$gone_rec$gone_ovr" ]; then
        log "no stored choice and the home in use is installed or stock, nothing to repair"
        return 0
    fi
    if read_choice > /dev/null; then
        log "a choice was stored meanwhile, not repairing"
        return 0
    fi
    log "no stored choice, but the home names ${gone_rec:-$gone_ovr}, which is not installed: a leftover"
    if [ "$MAJOR" -ge 10 ]; then
        # the record and the override are repaired each on its own: one may name an installed app
        [ -n "$gone_ovr" ] && drop_override
        if [ -n "$gone_rec" ]; then
            if [ "$(current_home)" = "$gone_rec" ]; then remove_home; else log "home changed meanwhile, record left alone"; fi
        fi
    elif [ "$(current_home)" = "$gone_rec" ]; then
        revert_platform "leftover home $gone_rec not installed"
    else
        log "home changed meanwhile, record left alone"
    fi
    kf_refresh stock
}

# handle <reply text>: one complete reply of the settings subscription.
