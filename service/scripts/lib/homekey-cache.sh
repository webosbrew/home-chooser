#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
installed_version() {
    sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$APPINFO" 2>/dev/null | head -n 1
}

checksum() { md5sum "$1" 2>/dev/null | cut -d' ' -f1; }

# The tab-separated index is written with the JSON manifest by the rewriter. Only the
# generated copies are mutable after boot; checksums always cover the persistent copies.
cache_status() {
    dir=${1:-$CACHE}
    CACHE_REASON=
    [ -f "$dir/manifest.json" ] && [ -s "$dir/manifest.tsv" ] || { CACHE_REASON=missing; return 1; }
    version=$(installed_version)
    [ -n "$version" ] && [ "$(cat "$dir/version" 2>/dev/null)" = "$version" ] || {
        CACHE_REASON=version-changed; return 1;
    }
    count=0
    tab=$(printf '\t')
    while IFS="$tab" read -r path stock patched tier; do
        case "$path:$stock:$patched:$tier" in
            /usr/lib/qml/*:[0-9a-f]*:[0-9a-f]*:core|/usr/lib/qml/*:[0-9a-f]*:[0-9a-f]*:best-effort) ;;
            *) CACHE_REASON=manifest-invalid; return 1 ;;
        esac
        [ "${#stock}" -eq 32 ] && [ "${#patched}" -eq 32 ] || { CACHE_REASON=manifest-invalid; return 1; }
        [ "$(checksum "$dir/stage$path")" = "$patched" ] || { CACHE_REASON=cache-corrupt; return 1; }
        if ! any_mounted || [ "$dir" != "$CACHE" ]; then
            [ "$(checksum "$ROOT$path")" = "$stock" ] || { CACHE_REASON=stock-changed; return 1; }
        fi
        count=$((count + 1))
    done < "$dir/manifest.tsv"
    [ "$count" -ge 2 ] || { CACHE_REASON=manifest-invalid; return 1; }
    return 0
}

do_prepare() {
    if cache_status; then return 0; fi
    reason=$CACHE_REASON
    if any_mounted; then
        printf '%s\n' "$reason" > "$STALE"
        log "Home key hook cache stale ($reason); waiting for stock files after reboot"
        return 0
    fi
    parent=$(dirname "$CACHE")
    mkdir -p "$parent" || return 1
    temp=$(mktemp -d "$CACHE.tmp.XXXXXX") || return 1
    version=$(installed_version)
    if [ -z "$version" ]; then
        rm -rf "$temp"
        log "Home key hook cache missing installed version"
        return 1
    fi
    "$NODE" "$REWRITER" generate --root "${ROOT:-/}" --out "$temp/stage" \
        --manifest "$temp/manifest.json" --version "$version" > "$temp/status" 2> "$temp/error"
    r=$?
    if [ "$r" != 0 ]; then
        log "Home key hook rewriter failed ($r): $(cat "$temp/error")"
        rm -rf "$temp"
        return "$r"
    fi
    printf '%s\n' "$version" > "$temp/version"
    printf '%s-%s\n' "$(date +%s)" "$$" > "$temp/generation"
    if ! cache_status "$temp"; then
        log "Home key hook generated cache failed validation: $CACHE_REASON"
        rm -rf "$temp"
        return 1
    fi
    old="$CACHE.old.$$"
    [ ! -e "$CACHE" ] || mv "$CACHE" "$old" || { rm -rf "$temp"; return 1; }
    if ! mv "$temp" "$CACHE"; then
        [ ! -e "$old" ] || mv "$old" "$CACHE"
        rm -rf "$temp"
        return 1
    fi
    rm -rf "$old"
    [ "$reason" = missing ] || printf '%s\n' "$reason" > "$STALE"
    log "Home key hook cache generated for version $version ($reason)"
}

# compositor_dependants: the compositor's clients that the firmware restarts once the compositor is
# ready, in its order: the `systemctl start <unit>` lines of lsm-ready.service (/etc before /lib,
# as systemd reads them). All releases 6.5.2 to 9.2.4 list webapp-mgr, booster and maliit-server,
# which is also the fallback when the unit is not found (docs/home-key-hook.md, "Restarting
# the compositor").
