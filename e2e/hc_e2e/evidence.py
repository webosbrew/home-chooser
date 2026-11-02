# SPDX-License-Identifier: Apache-2.0
"""Reading the device logs: messages commands, luna and status parsing, crash and fault classification."""
import json
import re
import shlex


def messages_command(path='/var/log/messages'):
    """Read retained rotations oldest first, then the active syslog (webOS uses .N.gz)."""
    quoted = shlex.quote(path)
    # BusyBox 1.29 on webOS 6 has no sort -V. Sort the numeric rotation suffix instead.
    return ("{ for log in $(ls %s.[0-9]*.gz 2>/dev/null | "
            "awk -F. '{print $(NF-1), $0}' | sort -rn | cut -d' ' -f2-); do "
            "zcat \"$log\" 2>/dev/null; done; cat %s; }" % (quoted, quoted))


def service_log_command(mark, message_id):
    """Read our PmLog records from syslog and the current boot's journal.

    Journal output uses MESSAGE only: --since enforces the UTC request boundary
    before timestamps are removed. Never read an unbounded journal on a missing mark.
    """
    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z?', mark):
        return 'true'
    since = shlex.quote(mark.rstrip('Z').replace('T', ' ') + ' UTC')
    pattern = shlex.quote('HomeChooser ' + message_id + ' ')
    return ("{ " + messages_command() + " | awk '$1 >= \"%s\"'; " % mark
            + "journalctl -b --since %s --no-pager -o cat 2>/dev/null; }" % since
            + " | grep -F " + pattern)


def home_input_command(mark):
    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z?', mark):
        return 'true'
    since = mark.rstrip('Z').replace('T', ' ') + ' UTC'
    return ("{ " + messages_command() + " | awk '$1 >= \"%s\"'; " % mark
            + "journalctl -b --since %s --no-pager -o short-iso 2>/dev/null; }" % shlex.quote(since)
            + " | grep -Ei 'surface|compositor|input|key|home' | tail -n 160")


def parse_status(text):
    """`homechooser-homekey status` output -> {key: value} of its key=value lines."""
    out = {}
    for line in (text or '').splitlines():
        k, sep, v = line.strip().partition('=')
        if sep and k and ' ' not in k:
            out.setdefault(k, v)
    return out


def parse_luna(text):
    """The JSON object of a luna-send reply (the first {...} line or the whole text); None if none."""
    text = (text or '').strip()
    for cand in [text] + [l.strip() for l in text.splitlines()]:
        i = cand.find('{')
        if i < 0:
            continue
        try:
            v = json.loads(cand[i:])
            if isinstance(v, dict):
                return v
        except ValueError:
            pass
    return None


# a plain stop/start of webapp-mgr (the boot apply's firmware-order sequence) matches none of these
CRASH_RE = re.compile(r'crashd.*CRASHED|CRASHED.*WebAppMgr|WebAppMgr.*(SIGSEGV|core dump|dumped core|segfault)'
                      r"|webapp-mgr\.service: (Main process exited, code=dumped|Failed with result .core-dump)", re.I)


def crash_lines(text):
    """The lines of a /var/log/messages excerpt that report a crash (crashd, a WebAppMgr fault)."""
    return [l for l in (text or '').splitlines() if CRASH_RE.search(l)]


# LG fault popups (foreign_popup).  faultmanager's getFaultDetectedStatus lists every fault of the boot with
# its faultTime in seconds since boot (the [uptime] stamp of /var/log/messages: a WebAppMgr crash at
# [194.278] gave faultTime 194.292 on 9.2.1).  `general` / keyCheck2_<N>_sysfaultdetector is boot-time noise:
# one per boot, faultTime 27-34 s, constant within the boot, present on passing steps (7 distinct N in the
# 2026-10-24 12.0 run, also 9.2.1 w22h-ombre and 7.x w22p-mullet), logged only as repeated `faultmanager
# FAULT_RESPONSE` lines and never with a crashd CRASHED line.  A real crash adds a crashd CRASHED line and a
# new fault entry at the crash time.
FAULT_NOISE_RE = re.compile(r'^keyCheck2_\d+_sysfaultdetector$')
POPUP_LINE_RE = r'crashd.*CRASHED|faultmanager.*(createNotiPopup|sendCreateAlert)|\[crash\] Fault'


def classify_faults(reply, since_uptime):
    """(real, noise) lists of getFaultDetectedStatus detectedFaults entries.  Real: not keyCheck2 noise and
    raised after `since_uptime` (seconds since boot at the step's mark; None counts every entry as new).
    Noise: keyCheck2 entries, and entries from before the mark (shown before this step began)."""
    real, noise = [], []
    try:
        faults = (json.loads(reply).get('faultDetectedStatus') or {}).get('detectedFaults') or []
    except (ValueError, AttributeError):
        return real, noise
    for f in faults:
        if not isinstance(f, dict):
            continue
        try:
            t = float(f.get('faultTime'))
        except (TypeError, ValueError):
            t = None
        old = since_uptime is not None and t is not None and t <= since_uptime
        (noise if FAULT_NOISE_RE.match(str(f.get('faultContent') or '')) or old else real).append(f)
    return real, noise


# A known LG crash, accepted as a limitation (docs/home-key-hook.md "Known LG crash on 9.2.4"): on webOS 9.2.4 the
# boot compositor restart intermittently (about 1 boot in 10 on the VM) sets off a legacy-broadcast-dvb crash
# at API_SI_GetLinkageUriInfo+0x197.  crashd names only the process (`legacy-broadcas.<pid>`); the location is
# in the faultmanager entry, whose gz name carries the same pid.  So a crashd line is known only when a fault
# entry for the same pid has the known location.  Anything else (other process, other location, WebAppMgr)
# stays a crash.
KNOWN_CRASH_DOC = 'docs/home-key-hook.md#known-lg-crash-on-924'
KNOWN_FAULT_RE = re.compile(r'^RDXDErr3_legacy-broadcas__.*legacy-broadcast-dvb \(API_SI_GetLinkageUriInfo\+0x197\)'
                            r'.*legacy-broadcas\.(\d+)\.')
KNOWN_CRASHD_RE = re.compile(r'crashd.*CRASHED.*"process":\s*"legacy-broadcas\.(\d+)"')


def known_fault_pids(reply):
    """pid -> fault entry for every getFaultDetectedStatus entry that is the known legacy-broadcast-dvb crash."""
    found = {}
    try:
        faults = (json.loads(reply).get('faultDetectedStatus') or {}).get('detectedFaults') or []
    except (ValueError, AttributeError):
        return found
    for f in faults:
        m = isinstance(f, dict) and f.get('faultType') == 'crash' and KNOWN_FAULT_RE.match(str(f.get('faultContent') or ''))
        if m:
            found[m.group(1)] = f
    return found


# Known environment fault (beanjar#183, closed as stock LG behaviour; Beanjar docs/research/
# w22o-audiooutputd-gain-race.md): on W22O firmware 33.31.61 and 33.31.68 (webOS 10.x) two audiooutputd gain-parse
# threads race, and on about 7-9% of boots audiooutputd crashes 50-56 s after boot.  LG then shows a
# "[crash] Fault Occured. Reason: RDXDErr3_audiooutputd__Linux Crash: ..." dialog with YES and NO that takes key
# focus.  The guard is the dialog text naming audiooutputd, never the VM name, so it applies on any release.
# The dialog is dismissed with NO only (never YES); any other dialog or crash process still stops the run.
AUDIOOUTPUTD_REF = 'beanjar#183'
AUDIOOUTPUTD_FAULT_RE = re.compile(r'^(?:RDXDErr\d+_)?audiooutputd(?:__|\.|$)')
AUDIOOUTPUTD_CRASHD_RE = re.compile(r'crashd.*CRASHED.*"process":\s*"audiooutputd(?:\.\d+)?"')


def audiooutputd_fault(fault):
    return (isinstance(fault, dict) and fault.get('faultType') == 'crash'
            and bool(AUDIOOUTPUTD_FAULT_RE.match(str(fault.get('faultContent') or ''))))


def audiooutputd_dialog(frame):
    """(message element text, joined screen text) when the OCR frame shows the LG "[crash] Fault Occured"
    dialog for audiooutputd; (None, joined text) for anything else."""
    rows = frame.get('elements') if isinstance(frame, dict) else None
    rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    texts = [str(r.get('text') or '') for r in rows]
    joined = ' '.join(texts)
    if '[crash] Fault Occured' not in joined or 'audiooutputd' not in joined:
        return None, joined
    # OCR returns one element per line: the title line is the message, audiooutputd is on the Reason line.
    msgs = [t for t in texts if '[crash] Fault Occured' in t]
    return (msgs[0] if len(msgs) == 1 else None), joined


def split_known_crashes(lines, reply):
    """All crash lines fail acceptance, including previously documented firmware crashes."""
    return [], list(lines)


def lines_after(lines, mark):
    """The log lines written after `mark` lines; a log shorter than the mark was started afresh (a new
    boot), so all of it counts."""
    return lines[mark:] if mark <= len(lines) else list(lines)


PREVIOUS_ARMED = 'previous launcher: Home changed in Home Chooser'
PREVIOUS_RELEASED = 'previous launcher: Home Chooser is off screen'


def closes_under_panel(lines):
    """Watcher log lines that close something (a previous launcher, or stock Home through
    closeByAppId) between the hold being armed by a change made in Home Chooser and its end (Home
    Chooser off screen); docs/settings-app.md "The previous launcher".  Empty is the pass."""
    out, held = [], False
    for l in lines:
        if PREVIOUS_ARMED in l:
            held = True
        elif PREVIOUS_RELEASED in l:
            held = False
        elif held and 'closeByAppId' in l:
            out.append(l)
    return out


def previous_closes(lines, ident):
    """The watcher's deferred closes of the previous launcher `ident` (closed at most once per change)."""
    return [l for l in lines if 'previous launcher: closeByAppId %s ' % ident in l]


def previous_dropped(lines, ident):
    """The watcher's lines saying the previous launcher `ident` closed by itself (nothing left to close)."""
    return [l for l in lines if 'previous launcher: %s closed by itself' % ident in l]
