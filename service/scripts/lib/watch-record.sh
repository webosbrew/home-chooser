#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
merged_write() {
    reply=$(call "$SETTINGS/getSystemSettings" '{"category":"general","keys":["defaultApps"]}' | flatten)
    if ! rest=$(record_body "$reply"); then
        log "cannot read the defaultApps record, not writing: $reply"
        return 1
    fi
    rest=$(printf '%s' "$rest" |
        sed -e 's/"home"[[:space:]]*:[[:space:]]*"[^"]*"[[:space:]]*,//' \
            -e 's/,[[:space:]]*"home"[[:space:]]*:[[:space:]]*"[^"]*"//' \
            -e 's/^[[:space:]]*"home"[[:space:]]*:[[:space:]]*"[^"]*"[[:space:]]*$//' \
            -e 's/"home"[[:space:]]*:[[:space:]]*null[[:space:]]*,//' \
            -e 's/,[[:space:]]*"home"[[:space:]]*:[[:space:]]*null//' \
            -e 's/^[[:space:]]*"home"[[:space:]]*:[[:space:]]*null[[:space:]]*$//')
    rest=$(printf '%s' "$rest" | sed -e 's/^[[:space:]]*,//' -e 's/,[[:space:]]*$//')
    if [ -z "$(printf '%s' "$rest" | tr -d '[:space:]')" ]; then
        if [ -n "$1" ]; then record="{\"home\":\"$1\"}"; else record="{\"home\":null}"; fi
    elif [ -n "$1" ]; then
        record="{\"home\":\"$1\",$rest}"
    else
        record="{\"home\":null,$rest}"
    fi
    out=$(call "$SETTINGS/setSystemSettings" "{\"category\":\"general\",\"settings\":{\"defaultApps\":$record}}" | flatten)
    if ok_reply "$out"; then
        log "setSystemSettings wrote $record"
        return 0
    fi
    log "setSystemSettings failed: $out"
    return 1
}

apply_record() {
    if [ "$MAJOR" -ge 9 ]; then
        out=$(call "$SAM/setDefaultApp" "{\"category\":\"home\",\"appId\":\"$1\"}" | flatten)
        if ok_reply "$out"; then
            log "setDefaultApp home=$1 ok"
            return 0
        fi
        if printf '%s' "$out" | grep -qi 'unknown method'; then
            log "setDefaultApp is an unknown method here, using setSystemSettings"
            merged_write "$1"
            return $?
        fi
        log "setDefaultApp failed: $out"
        return 1
    fi
    merged_write "$1"
}

# The profile override for webOS 10 and later: awk picks the defaultApps profile out of the
# getProfiles reply, drops its _-prefixed members and sets categories.home.
AWK_OVERRIDE='
function skipstr(s, i,   c) {
    i++
    while (i <= length(s)) {
        c = substr(s, i, 1)
        if (c == "\\") i++
        else if (c == "\"") return i
        i++
    }
    return length(s)
}
function matchend(s, i,   d, c) {
    d = 0
    while (i <= length(s)) {
        c = substr(s, i, 1)
        if (c == "\"") i = skipstr(s, i)
        else if (c == "{" || c == "[") d++
        else if (c == "}" || c == "]") { d--; if (d == 0) return i }
        i++
    }
    return 0
}
function members(obj, K, V,   n, i, j, c, e, key) {
    n = 0
    i = 2
    while (i < length(obj)) {
        c = substr(obj, i, 1)
        if (c == " " || c == "\t" || c == ",") { i++; continue }
        if (c != "\"") break
        j = skipstr(obj, i)
        key = substr(obj, i + 1, j - i - 1)
        i = j + 1
        while (substr(obj, i, 1) == " " || substr(obj, i, 1) == "\t" || substr(obj, i, 1) == ":") i++
        c = substr(obj, i, 1)
        if (c == "{" || c == "[") e = matchend(obj, i)
        else if (c == "\"") e = skipstr(obj, i)
        else {
            e = i
            while (e < length(obj) && substr(obj, e + 1, 1) !~ /[,} \t]/) e++
        }
        n++
        K[n] = key
        V[n] = substr(obj, i, e - i + 1)
        i = e + 1
    }
    return n
}
{ s = s " " $0 }
END {
    p = 1
    found = 0
    while ((q = index(substr(s, p), "\"defaultApps\"")) > 0) {
        pos = p + q - 1
        k = pos + 13
        while (substr(s, k, 1) == " " || substr(s, k, 1) == "\t" || substr(s, k, 1) == ":") k++
        if (substr(s, k, 1) == "{") {
            e = matchend(s, k)
            obj = substr(s, k, e - k + 1)
            if (e > 0 && index(obj, "\"categories\"") > 0) { found = 1; break }
        }
        p = pos + 13
    }
    if (!found) exit 1
    n = members(obj, K, V)
    out = ""
    hascat = 0
    for (i = 1; i <= n; i++) {
        if (substr(K[i], 1, 1) == "_") continue
        v = V[i]
        if (K[i] == "categories" && substr(v, 1, 1) == "{") {
            hascat = 1
            m = members(v, CK, CV)
            v = "{\"home\":\"" home "\""
            for (j = 1; j <= m; j++) if (CK[j] != "home") v = v "," "\"" CK[j] "\":" CV[j]
            v = v "}"
        }
        out = out (out == "" ? "" : ",") "\"" K[i] "\":" v
    }
    if (!hascat) exit 1
    print "{\"defaultApps\":{" out "}}"
}'

override_ok() {
    [ -f "$OVERRIDE" ] || return 1
    esc=$(printf '%s' "$1" | sed 's/\./\\./g')
    tr '\n' ' ' < "$OVERRIDE" | grep -q "\"home\"[[:space:]]*:[[:space:]]*\"$esc\""
}

# check_override <choice>: webOS 10 and later, the override file must exist and name the choice.
check_override() {
    [ "$MAJOR" -ge 10 ] || return 0
    if override_ok "$1"; then
        log "profile override ok"
        return 0
    fi
    log "profile override missing or wrong, rebuilding it"
    out=$(call "$PROFILE/getProfiles" '{"names":["defaultApps"]}' | flatten)
    if ! json=$(printf '%s' "$out" | awk -v home="$1" "$AWK_OVERRIDE") || [ -z "$json" ]; then
        log "getProfiles reply has no defaultApps profile with categories, override not written: $out"
        return 1
    fi
    mkdir -p "$(dirname "$OVERRIDE")" 2>/dev/null
    if printf '%s\n' "$json" > "$OVERRIDE.$$.tmp" && mv "$OVERRIDE.$$.tmp" "$OVERRIDE"; then
        log "wrote $OVERRIDE: $json"
    else
        log "cannot write $OVERRIDE"
        return 1
    fi
    out=$(call "$PROFILE/debug/rescan" '{}' | flatten)
    log "debug/rescan: $out"
}

# The interval before the next write: MIN_INTERVAL doubled once per streak step, capped.
interval() {
    iv=$MIN_INTERVAL
    i=0
    while [ "$i" -lt "$STREAK" ] && [ "$iv" -lt "$BACKOFF_CAP" ]; do
        iv=$((iv * 2))
        i=$((i + 1))
    done
    [ "$iv" -gt "$BACKOFF_CAP" ] && iv=$BACKOFF_CAP
    echo "$iv"
}

# current_home: the record's home, read once more (not from the subscription).
current_home() {
    reply=$(call "$SETTINGS/getSystemSettings" '{"category":"general","keys":["defaultApps"]}' | flatten)
    home_of "$reply"
}

# reconcile <choice> <found home>: the record does not match; write it again unless a write
# was made less than one interval ago. Then the record is read again (the subscription may be
# showing a stale reply), and if it still does not match, the watcher waits out the interval
# and reads it a last time before writing.
reconcile() {
    want=$1
    found=$2
    t=$(now)
    required=$(interval)
    if [ "$LAST_WRITE" -gt 0 ] && [ $((t - LAST_WRITE)) -lt "$required" ]; then
        found=$(current_home)
        if [ "$found" = "$want" ]; then
            log "home is '$want' again, nothing to write"
            return 0
        fi
        wait_for=$((required - (t - LAST_WRITE)))
        log "home is '${found:-none}', not '$want'; last write was $((t - LAST_WRITE)) s ago, waiting $wait_for s"
        nap "$wait_for"
        want=$(read_choice) || finish "choice file is gone, exiting" 0
        found=$(current_home)
        if [ "$found" = "$want" ]; then
            log "home is '$want' again, nothing to write"
            return 0
        fi
        t=$(now)
    fi
    if [ "$LAST_WRITE" -gt 0 ] && [ $((t - LAST_WRITE)) -lt $((2 * required)) ]; then
        STREAK=$((STREAK + 1))
    else
        STREAK=0
    fi
    LAST_WRITE=$t
    log "home is '${found:-none}', writing '$want' (streak $STREAK, next interval $(interval) s)"
    apply_record "$want"
    check_override "$want"
}

# --- Apps that come and go ---------------------------------------------------------------

# detect_timeout: once, find how `timeout` is called: `timeout SECS CMD` (BusyBox 1.30 and later,
# GNU) or `timeout -t SECS CMD` (BusyBox before 1.30, e.g. 1.29.2 on webOS 5). Sets TMO_FORM to
# new, old or none.
