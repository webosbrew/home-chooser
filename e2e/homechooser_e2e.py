#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""End-to-end test of Home Chooser on a Beanjar VM.  See e2e/README.md.

Python 3 standard library only.  The device is reached with the ares-* tools only (by device
name); the VM host (Beanjar) through the BeanjarHost class below, locally or over
`ssh raspberrypi` (the Pi is a Linux host, not a TV: ares-shell is for televisions only).
"""
import argparse
import base64
import json
import os
import re
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import glob
import hashlib
import io
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
APP_ID = 'org.webosbrew.homechooser'
SVC_ID = APP_ID + '.service'
TVPOWER = 'luna://com.webos.service.tvpower/'
SCREENSAVERS = ('com.webos.app.screensaver', 'com.webos.app.videoads')
TVPOWER_REBOOT_URI = 'luna://com.webos.service.tvpower/power/reboot'
STOCK = 'com.webos.app.home'
CHOICE = '/var/lib/webosbrew/homechooser/home'
FLAG = '/var/lib/webosbrew/homechooser/homekey'
HOOK_CACHE = '/var/lib/webosbrew/homechooser/hook'
HOOK_STALE = '/var/lib/webosbrew/homechooser/hook-stale'
INITD = '/var/lib/webosbrew/init.d/homechooser'
BOOT_DIR = '/var/lib/webosbrew/homechooser/boot'
BOOT_SCRIPT = '/media/developer/apps/usr/palm/services/' + SVC_ID + '/scripts/homechooser'
OVERRIDE = '/var/webos-profile/override/org.webosbrew.homechooser.json'
APPDIR = '/media/developer/apps/usr/palm/applications/' + APP_ID
SCRIPTS_DIR = '/media/developer/apps/usr/palm/services/' + SVC_ID + '/scripts'
HB_ELEVATE = '/media/developer/apps/usr/palm/services/org.webosbrew.hbchannel.service/elevate-service'


def uninstall_remnant_paths():
    """Files owned by Home Chooser that must disappear after watcher restoration."""
    paths = [HOOK_CACHE, HOOK_STALE, BOOT_DIR]
    for root in ('/var/luna-service2-dev', '/var/luna-service2'):
        paths.append(root + '/api-permissions.d/org.webosbrew.homechooser.launcher.api.json')
    return paths


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


# ---------------------------------------------------------------------------------------------
# The matrix: one table.  "sure" says how far the VM/release pairing was checked.
#   verified  = booted and os-release read by a run of this runner
#   docs      = the firmware is named for that release in docs/research/home-key-and-home-app.md
#   listing   = only `beanjar list` (platform column, firmware string); os-release not read
# elevation: luna = Homebrew Channel's luna elevateService (the app does it); root-shell = the
# runner runs Homebrew Channel's elevate-service helper as root (webOS 11.x/12.x, where
# Homebrew Channel refuses `elevateService` from apps: README.md "Decisions").
# ---------------------------------------------------------------------------------------------
MATRIX = [
    # release, vm, elevation, sure, note
    ('5.x', ('w20l-jhericurl-rk1', 'w20l-jhericurl'), 'luna', 'verified', 'W20L 04.63.25.01 = 5.6.1; os-release read 2026-10-22; w20h-jhericurl is also webOS 5'),
    ('6.x', ('w21a-kisscurl-rk1', 'w21a-kisscurl'), 'luna', 'verified', 'W21A 03.53.25.01 = 6.5.2; os-release read 2026-10-23 (w21p-kisscurl, 6.6.0, belongs to the Dock project and is not in the matrix)'),
    ('9.x', 'w24g-ombre', 'luna', 'verified', 'W24G 33.20.23.01 = 9.2.4; os-release read 2026-10-23'),
    ('9.x', 'w24p-ombre', 'luna', 'docs', 'W24P 33.20.23.01 = 9.2.4-4603 per firmware metadata; os-release to be read'),
    ('7.x', ('w22p-mullet-rk1', 'w22p-mullet'), 'luna', 'verified', 'W22P 04.60.90.01 = 7.6.0; os-release read 2026-10-22'),
    ('8.x', ('w22o-number1-rk2', 'w22o-number1'), 'luna', 'verified', 'W22O 13.30.56.01 = 8.3.0; os-release read 2026-10-22 (beanjar lists it as "webOS 23", a platform label)'),
    ('9.x', 'w22h-ombre', 'luna', 'verified', 'W22H 23.21.20.01 = 9.2.1; os-release read'),
    ('10.x', ('w22o-ponytail-us-rk2', 'w22o-ponytail-us'), 'luna', 'verified', 'W22O 33.31.61.01 = 10.3.1; os-release read 2026-10-22'),
    ('11.x', ('w22o-queue-us-rk2', 'w22o-queue-us'), 'root-shell', 'verified', 'W22O 43.21.89.01 = 11.2.0; os-release read 2026-10-22; w22o-queue and w22o-queue-jp run the same firmware'),
    ('12.x', 'w24g-spacebuns', 'root-shell', 'verified', 'W24G 02.68.80.01 = 12.0.0; os-release read 2026-10-22'),
]
KEEP_LAUNCHER_VMS = {'w21p-kisscurl'}

# Stock-home preload policy (service/src/watcher/preload.c; docs/settings-app.md "The stock home is
# not preloaded").  On 10.x sam relaunches a closed stock home at once as the "last input" app,
# whatever the launcher (measured on 10.3.1, 75 to 89 MB), so since 8791f6e neither the watcher nor the
# app closes it there: the expected result is the preload policy off, the skip line in the watcher log,
# no closeByAppId for it, and the stock home possibly running until the next reboot.  On 6.x to 9.x the
# home gate (docs/home-gate.md) takes its place: the gate is mounted, the watcher logs "home gate
# mounted" and closes a stock home the boot started (a bounded retry), and none runs afterwards.
# On 11.x and 12.x the watcher waits until the launcher has a safe foreground before closing it.
SKIP_LINE = 'stock home left running'
GATE_LINE = 'home gate mounted'
GATE_TARGET = '/usr/bin/com.webos.app.home'
PRELOAD_SETTLE = 60   # seconds to wait after a switch/boot before sampling
PRELOAD_SAMPLE = 60   # seconds the stock home is watched for
FULLHOME = 'com.webos.app.fullhome'


# ---------------------------------------------------------------------------------------------
# What the product says and writes (pure; e2e/test_e2e_logic.py checks them against the sources)
# ---------------------------------------------------------------------------------------------
DONE_TEXT = 'Done. Press Home to try it.'
# The app has no restart dialog of its own: the service raises a system alert, the app toasts this and,
# while a restart is pending, shows RESTART_NOTE and a "Restart now" row (app/src/views/useHomeChooser.js).
REBOOT_ASKED_TEXT = 'Done. Restart the TV to finish.'
RESTART_NOTE = {'apply': 'The Home button changes after a restart. Use Restart now — turning the TV off with the remote may not restart it.',
                'remove': 'LG Home is on the Home button now. A restart removes the hook.'}
RESTART_NOW = 'Restart now'
REBOOT_BUTTONS = ('Reboot', 'Later')
# the service's system alert (or the toast when the alert is refused; service/src/policy.c hc_reboot_message)
REBOOT_PROMPT = {'apply': 'Restart the TV to finish setting up the Home button.',
                 'remove': 'Restart the TV to finish removing the Home button patch.'}
# the watcher (service/src/watcher/notify.c): the same on every release, no restart mentioned
REMOVAL_ALERT = '%s was removed, so the Home button opens LG Home again.'
UNINSTALL_TOAST = 'Home Chooser was removed. The Home button opens LG Home again.'
OLD_RESTART_TEXTS = ('Restart the TV to finish the change.', 'Restart the TV to put LG Home back on the Home button.',
                     'The screen will blink once.', 'Restart the TV later to finish.', 'Restart the TV?')
# (text, file relative to the repo that must contain it)
TEXT_SOURCES = [
    (DONE_TEXT, 'app/src/views/useHomeChooser.js'),
    (REBOOT_PROMPT['apply'], 'service/src/policy.c'),
    (REBOOT_PROMPT['remove'], 'service/src/policy.c'),
    (REMOVAL_ALERT, 'service/src/watcher/notify.c'),
    (UNINSTALL_TOAST, 'service/src/watcher/restore.c'),
]


def device_text_forms(text):
    """A minified JS bundle can spell Unicode as JSON escapes while source files use UTF-8."""
    escaped = json.dumps(text, ensure_ascii=True)[1:-1]
    return (text,) if escaped == text else (text, escaped)


def uses_home_key_hook(major):
    return 6 <= major <= 9


def uses_home_gate(major):
    """6.x to 9.x with a launcher chosen: the daemon binds homechooser-home-gate over stock Home's
    binary (docs/home-gate.md), so stock Home is not left running."""
    return 6 <= major <= 9


def stock_home_id(major, fullhome_installed):
    """The id restore/removal/uninstall write as `home` on 5.x to 9.x (docs/settings-app.md): fullhome
    if it is installed, else com.webos.app.home.  10+ write null: None."""
    if 5 <= major <= 9:
        return FULLHOME if fullhome_installed else STOCK
    return None


def stock_record_ok(major, home, stock_id=None):
    """The record's `home` after a restore, a launcher removal or a Home Chooser uninstall.
    5.x: a stock launcher (unchanged).  6.x to 9.x: the stock id, not null (34a4ff9); exactly
    `stock_id` when it is known.  10+: null or unset."""
    if major == 5:
        return home in (FULLHOME, STOCK)
    if uses_home_key_hook(major):
        return home == stock_id if stock_id else home in (FULLHOME, STOCK)
    return home in (None, '', 'null')


def stock_baseline_ok(major, home):
    """A stock image before Home Chooser: on 6.x to 9.x the record may be unset (fresh image) or the
    stock id an earlier uninstall left (it stays: stock firmware ignores it)."""
    if uses_home_key_hook(major):
        return home in (None, '', 'null', FULLHOME, STOCK)
    return stock_record_ok(major, home)


def expects_reboot_dialog(major, mounted_before):
    """Every supported release applies immediately; no reboot prompt is expected."""
    return False


def parse_reboot_ask(text):
    """The service's REBOOT_ASK PmLog lines (service/src/service/system.c on_reboot_prompt) -> what was asked and how
    the notification service answered:
    {'message', 'buttons' (list), 'alert' ('accepted'|'refused'|None), 'alert_id', 'toast' ('accepted'|'refused'|None)}.
    The request line carries no private parameters; replies are the createAlert / createToast lines with their JSON."""
    out = {'message': None, 'buttons': [], 'alert': None, 'alert_id': None, 'toast': None}
    for line in (text or '').splitlines():
        if 'REBOOT_ASK' not in line:
            continue
        m = re.search(r'createAlert request: message: (.*?); buttons: (.*)$', line)
        if m:
            out['message'] = m.group(1).strip()
            out['buttons'] = [re.sub(r'\s*\(.*$', '', b).strip() for b in m.group(2).split(',')]
            continue
        m = re.search(r'notification/(createAlert|createToast) (accepted|refused)', line)
        if m:
            kind = 'alert' if m.group(1) == 'createAlert' else 'toast'
            out[kind] = m.group(2)
            if kind == 'alert':
                a = re.findall(r'\\?"alertId\\?"\s*:\s*\\?"([^"\\]+)', line)
                if a:
                    out['alert_id'] = a[-1]
    return out


def home_input_command(mark):
    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z?', mark):
        return 'true'
    since = mark.rstrip('Z').replace('T', ' ') + ' UTC'
    return ("{ " + messages_command() + " | awk '$1 >= \"%s\"'; " % mark
            + "journalctl -b --since %s --no-pager -o short-iso 2>/dev/null; }" % shlex.quote(since)
            + " | grep -Ei 'surface|compositor|input|key|home' | tail -n 160")


def alert_ls2_proof(info, message, target, safe, uninstall_id=None):
    if not info or info.get('message') != message or info.get('buttons') != [target, safe]:
        return False
    actions = info.get('actions', [])
    if (not isinstance(actions, list) or len(actions) != 2
            or not all(isinstance(b, dict) for b in actions)
            or info.get('alert') != 'accepted' or not info.get('alert_id')):
        return False
    yes, no = actions
    if uninstall_id is None:
        good = (yes.get('onclick') == TVPOWER_REBOOT_URI
                and yes.get('params') == {'reason': 'remoteKey'})
    else:
        good = (yes.get('onclick') in ('luna://com.webos.appInstallService/remove',
                                       'luna://com.webos.appInstallService/dev/remove')
                and yes.get('params') == {'id': uninstall_id, 'subscribe': True})
    return bool(good and yes.get('label') == target and no.get('label') == safe
                and not no.get('onclick') and not no.get('params') and no.get('focus') is True)


def alert_service_proof(raw, message, target, safe, uninstall_id=None, caller=None, observed=None):
    """Authorize from this request's service log OR matched LS2 call and accepted reply."""
    if alert_ls2_proof(observed, message, target, safe, uninstall_id):
        return True
    if uninstall_id is None:
        info = parse_reboot_ask(raw)
        return (info['message'] == message and info['buttons'] == [target, safe]
                and info['alert'] == 'accepted' and bool(info['alert_id'])
                and any('REBOOT_ASK' in line and 'notification/createAlert accepted (exit 0):' in line
                        and re.search(r'"returnValue"\s*:\s*true', line)
                        and info['alert_id'] in line for line in raw.splitlines()))
    prefix = '%s by %s: alert shown for luna://com.webos.appInstallService/' % (uninstall_id, caller)
    for line in raw.splitlines():
        if not re.search(r'UNINSTALL_ASK\s+\{\}\s+' + re.escape(prefix), line):
            continue
        reply = line.split(prefix, 1)[1]
        if not re.match(r'(dev/)?remove \(exit 0\): ', reply):
            continue
        try:
            data = json.loads(reply.split(': ', 1)[1])
        except (ValueError, IndexError):
            continue
        if data.get('returnValue') is True and data.get('alertId'):
            return True
    return False


def match_element(frame, text=None, role=None, region=None):
    """Select one exact screen element; never guess between OCR hits."""
    if text is None and role is None:
        raise ValueError('text or role is required')
    rows = frame.get('elements')
    if not isinstance(rows, list):
        raise ValueError('missing elements array')
    matches = []
    for row in rows:
        if text is not None and row.get('text') != text:
            continue
        if role is not None and row.get('kind') not in (role if isinstance(role, tuple) else (role,)):
            continue
        box = row.get('box')
        centre = row.get('centre')
        if not isinstance(box, list) or len(box) != 4:
            raise ValueError('missing element geometry')
        if centre is None:
            centre = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]
        if (len(centre) != 2 or any(type(v) not in (int, float) for v in centre)
                or not 0 <= centre[0] < frame['width'] or not 0 <= centre[1] < frame['height']):
            raise ValueError('invalid element centre')
        if region and not (region[0] <= centre[0] <= region[2] and region[1] <= centre[1] <= region[3]):
            continue
        matches.append(dict(row, centre=centre))
    if len(matches) != 1:
        raise ValueError('expected exactly one element text=%r role=%r; found %s' % (text, role, len(matches)))
    return matches[0]


# Some HOME takeover apps hook micomservice/lginput2 instead of declaring home.
# HomeBack injects Frida into lginput2/micomservice; those hooks survive app removal until reboot.
KNOWN_HOME_TAKEOVERS = ('com.homebrew.homeback',)
# Live 10.3.1 appinfo: "Launcher", "Minimalist home screen", no appCategory.
KNOWN_UNCATEGORIZED_LAUNCHERS = ('works.partridge.webos-launcher',)


def launcher_removal_reason(app):
    """Candidate rule plus documented HOME takeover exceptions, only for developer apps."""
    ident = app['id']
    if ident in (APP_ID, 'org.webosbrew.hbchannel') or ident.startswith('org.webosbrew.hcfixture.'):
        return None
    category = app.get('appCategory') or []
    category = [category] if isinstance(category, str) else category
    if 'home' in category:
        return 'appCategory contains home'
    if ident in KNOWN_HOME_TAKEOVERS:
        return 'known HOME takeover app id'
    if ident in KNOWN_UNCATEGORIZED_LAUNCHERS:
        return 'known launcher without home category'
    return None


def foreign_launcher(app):
    return launcher_removal_reason(app) is not None


def startup_entry_owned(ident, name, target, content):
    """A package symlink, or an exact-id script that executes a package path."""
    roots = ['/media/developer/apps/usr/palm/' + kind + '/' + app
             for kind in ('applications', 'services') for app in (ident, ident + '.service')]
    if target:
        return any(target == root or target.startswith(root + '/') for root in roots)
    # Observed 10.3.1 HomeBack bootstrap calls its remote worker through LS2,
    # rather than executing a package path. Require its exact hook name and call.
    if ident == 'com.homebrew.homeback' and name == 'homeback':
        if re.search(r'(?m)^\s*output=\$\(luna-send\s+-n\s+1\s+-f\s+"luna://com\.homebrew\.homeback\.service/remote/start"\s', content):
            return True
    return (name in (ident, ident + '.service') and
            any(re.search(r'(?m)^\s*(?:exec\s+)?(?:sh\s+|bash\s+)?[\"\']?' + re.escape(root) + r'/', content)
                for root in roots))


def startup_hook_directories(dev):
    # Read the installed script; the package path is used on 10.3.1, where the
    # historical /var/lib copy is absent. Never guess a hook directory.
    for path in ('/var/lib/webosbrew/startup.sh',
                 '/media/developer/apps/usr/palm/services/org.webosbrew.hbchannel.service/startup.sh'):
        rc, startup = dev.sh('cat ' + path)
        if rc:
            continue
        dirs = re.findall(r'(?m)^\s*run-parts\s+(/var/lib/webosbrew/[A-Za-z0-9_./-]+)', startup)
        if not dirs:
            raise RuntimeError('no supported startup hook directory in ' + path)
        return path, list(dict.fromkeys(dirs))
    raise RuntimeError('cannot inspect installed Homebrew Channel startup')


def clean_startup_entries(dev, rec, ident):
    source, dirs = startup_hook_directories(dev)
    rec.note('startup hook directories read from installed script', source)
    for directory in dirs:
        rc, raw = dev.sh('find ' + shlex.quote(directory) + ' -mindepth 1 -maxdepth 1')
        if rc:
            raise RuntimeError('cannot inspect startup entries: ' + raw)
        for path in raw.splitlines():
            if os.path.dirname(path) != directory:
                raise RuntimeError('unexpected startup entry path: ' + path)
            _, target = dev.sh('readlink ' + shlex.quote(path))
            _, content = dev.sh('cat ' + shlex.quote(path))
            if startup_entry_owned(ident, os.path.basename(path), target.strip(), content):
                rc, out = dev.sh('rm -- ' + shlex.quote(path))
                rec.check('remove owned startup entry ' + path, rc == 0, out)
                rec.note('removed startup entry owned by ' + ident, path + ' -> ' + target.strip())
                if rc:
                    raise RuntimeError('startup entry removal failed: ' + path)
            else:
                rec.note('retained startup entry: ownership not proven for ' + ident, path)


def restart_after_launcher_cleanup(scenario, removed):
    if removed:
        scenario.rec.note('preflight restarting after launcher removal',
                          ', '.join(removed) + ': HOME hooks injected into running processes survive uninstall')
        if not scenario.beanjar_reboot('preflight launcher cleanup reboot'):
            raise RuntimeError('VM did not return after launcher cleanup')


def clean_launchers(dev, rec):
    installed = dev.installed()
    kept, removed = [], []
    for ident in installed:
        # Read the developer appinfo itself: SAM may omit appCategory on older releases.
        path = '/media/developer/apps/usr/palm/applications/' + ident + '/appinfo.json'
        rc, raw = dev.sh('cat ' + shlex.quote(path))
        if rc:
            raise RuntimeError('cannot read developer appinfo for %s: %s' % (ident, raw))
        app = json.loads(raw)
        if app.get('id') != ident:
            raise RuntimeError('appinfo id mismatch for ' + ident)
        if foreign_launcher(app):
            rc, out = dev.remove(ident)
            ok = rc == 0 and ident not in dev.installed()
            rec.note('preflight launcher removal %s version %s: %s' %
                     (ident, app.get('version'), launcher_removal_reason(app)), out)
            rec.check('preflight removed foreign launcher ' + ident, ok, out)
            if not ok:
                raise RuntimeError('foreign launcher removal failed: ' + ident)
            removed.append(ident)
            clean_startup_entries(dev, rec, ident)
        else:
            kept.append('%s version %s' % (ident, app.get('version')))
    rec.note('preflight retained developer apps (ours or non-launchers)', ', '.join(kept) or 'none')
    return removed


def alert_focus(frame, message, target, safe, require_focus=True):
    """Fail closed on incomplete OCR, extra controls, or ambiguous contrast/geometry."""
    if not isinstance(frame, dict) or not isinstance(frame.get('elements'), list):
        raise ValueError('missing elements array')
    rows = frame['elements']
    if any(not isinstance(r, dict) for r in rows):
        raise ValueError('malformed element')
    texts = [r.get('text') for r in rows if r.get('kind') == 'text']
    # Some firmware renders no title header. Ownership also requires
    # the matching service request/reply; the app title behind the alert is not proof.
    if texts.count(message) != 1:
        raise ValueError('expected message not uniquely visible')
    buttons = [r for r in rows if r.get('kind') == 'button' and r.get('text') in (target, safe)]
    if len(buttons) != 2 or sorted(r.get('text', '') for r in buttons) != sorted((target, safe)):
        raise ValueError('expected exactly the two alert buttons')
    for b in buttons:
        box = b.get('box')
        if (not isinstance(box, list) or len(box) != 4
                or any(type(v) not in (int, float) for v in box)
                or not box[0] < box[2] or not box[1] < box[3]
                or not isinstance(b.get('style'), dict)
                or type(b['style'].get('disabled')) is not bool):
            raise ValueError('missing button geometry or contrast flag')
    first, second = sorted(buttons, key=lambda b: b['box'][1])
    if (first['box'][3] < second['box'][1]
            and max(first['box'][0], second['box'][0]) < min(first['box'][2], second['box'][2])):
        horizontal = False
        focused = [b for b in buttons if b['style']['disabled'] is False]
        direction = 'UP' if first['text'] == target else 'DOWN'
    else:
        first, second = sorted(buttons, key=lambda b: b['box'][0])
        if (first['box'][2] >= second['box'][0]
                or max(first['box'][1], second['box'][1]) >= min(first['box'][3], second['box'][3])):
            raise ValueError('buttons are not a disjoint aligned pair')
        horizontal = True
        focused = [b for b in buttons if b['style'].get('highlighted') is True]
        direction = 'LEFT' if first['text'] == target else 'RIGHT'
    if not require_focus:
        focused = [buttons[0]]
    if require_focus and all(b['style']['disabled'] for b in buttons):
        raise ValueError('both buttons are dimmed')
    if require_focus and len(focused) != 1:
        # Provisional only: ENTER also requires a reciprocal appearance swap.
        contrasts = [b['style'].get('contrast', 0) for b in buttons]
        if max(contrasts) - min(contrasts) < 2:
            raise ValueError('focus is ambiguous')
        focused = [buttons[contrasts.index(max(contrasts))]]
    # The alert shares the screen with controls behind it. Its message and button band
    # must contain exactly our pair; controls elsewhere (the app beneath) are irrelevant.
    top = min(b['box'][1] for b in buttons)
    bottom = max(b['box'][3] for b in buttons)
    messages = [r for r in rows if r.get('kind') == 'text' and r.get('text') == message]
    msg = messages[0]
    if horizontal:
        # 5.x puts the prompt immediately above a highlighted horizontal pair.
        if (not 0 <= top - msg['box'][3] <= bottom - top
                or msg.get('group') is None
                or any(b.get('group') != msg['group'] for b in buttons)):
            raise ValueError('message is outside the alert group')
    elif not top <= sum(msg['box'][1::2]) / 2 <= bottom:
        if not (0 <= top - msg['box'][3] <= bottom - top
                and max(msg['box'][0], min(b['box'][0] for b in buttons))
                    < min(msg['box'][2], max(b['box'][2] for b in buttons))):
            raise ValueError('message is outside the alert dialog')
        top = msg['box'][1]
    # The dialog rectangle is the union of its message and its two buttons.  A control counts as being
    # in the alert only if it overlaps that rectangle; OCR "buttons" in the app list behind the dialog
    # (e.g. a Homebrew Channel row title) share the vertical band but lie beside it, so they are not
    # extra alert controls.  A third control inside the rectangle still fails, and the exact message and
    # the two exact buttons were already required above.
    left = min([b['box'][0] for b in buttons] + [msg['box'][0]])
    right = max([b['box'][2] for b in buttons] + [msg['box'][2]])
    band = [r for r in rows if r.get('kind') == 'button'
            and isinstance(r.get('box'), list) and len(r['box']) == 4
            and r['box'][1] < bottom and r['box'][3] > top
            and r['box'][0] < right and r['box'][2] > left]
    if len(band) != 2:
        raise ValueError('extra controls in the alert band')
    return focused[0]['text'], direction


def alert_style_swap(before, after, target, safe):
    """Match reciprocal appearances with a small tolerance for OCR colour jitter."""
    def pair(frame):
        return {r['text']: r for r in frame['elements']
                if r.get('kind') == 'button' and r.get('text') in (target, safe)}
    a, b = pair(before), pair(after)
    for field in ('highlighted', 'disabled'):
        x, y = a[safe]['style'].get(field), a[target]['style'].get(field)
        if type(x) is bool and type(y) is bool and x != y:
            if b[target]['style'].get(field) == x and b[safe]['style'].get(field) == y:
                return field
            raise ValueError('focus appearance did not swap: ' + field)
    def rgb(row, field):
        value = row['style'].get(field, '')
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
            raise ValueError('missing appearance colours')
        return tuple(int(value[i:i+2], 16) for i in (1, 3, 5))
    def distance(x, y):
        return max(abs(i-j) for i, j in zip(x, y))
    for field in ('background', 'foreground'):
        x, y = rgb(a[safe], field), rgb(a[target], field)
        if distance(x, y) >= 40:
            if distance(rgb(b[target], field), x) <= 24 and distance(rgb(b[safe], field), y) <= 24:
                return field + ' colour'
    raise ValueError('no reciprocal focus appearance swap')


class RebootAlertMonitor:
    """Observe the service's notification call when a firmware drops its PmLog info lines.

    ls-monitor carries each button's action and parameters in the request. Keep raw events only in memory;
    the recorded evidence contains the message, button actions and notification reply.
    """
    def __init__(self, dev):
        self.dev = dev
        self.proc = None
        self.thread = None
        self.remote_pid = None
        self.ready = threading.Event()
        self.lock = threading.Lock()
        self.calls = {}
        self.seen = []
        self.candidates = []
        self.info = {'message': None, 'buttons': [], 'alert': None, 'alert_id': None, 'toast': None}

    def start(self):
        self.proc = subprocess.Popen(
            # ares-do exec buffers stdout until the command exits; ares-shell -r streams it.
            ['ares-shell', '-d', self.dev.root, '-r',
             'printf "HC_MONITOR_PID=%s\\n" "$$"; exec ls-monitor -j -f com.webos.notification'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
            start_new_session=True)
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()
        self.ready.wait(timeout=5)
        sleep(1)  # let the hub attach before the confirmation key is sent
        return self.ready.is_set() and self.proc.poll() is None

    def _read(self):
        for line in self.proc.stdout:
            if line.startswith('HC_MONITOR_PID='):
                try:
                    self.remote_pid = int(line.partition('=')[2].strip())
                    self.ready.set()
                except ValueError:
                    pass
                continue
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                continue
            with self.lock:
                self._observe(event)

    def _observe(self, event):
        if len(self.seen) < 12 and (event.get('method') in ('createAlert', 'createToast')
                                    or (event.get('type') == 'return'
                                        and event.get('sender') == 'com.webos.notification')):
            self.seen.append({k: event.get(k) for k in
                              ('type', 'transport', 'sender', 'destination', 'method', 'replyToken')})
        if (event.get('type') == 'call' and event.get('transport') == 'TX'
                and event.get('destination') == 'com.webos.notification'
                and event.get('method') in ('createAlert', 'createToast')):
            payload = event.get('payload') or {}
            if not isinstance(payload, dict):
                if len(self.candidates) < 4:
                    self.candidates.append({'method': event.get('method'), 'payload_type': type(payload).__name__})
                return
            buttons = payload.get('buttons')
            if payload.get('sourceId') == APP_ID and len(self.candidates) < 4:
                self.candidates.append({
                    'method': event.get('method'), 'sourceId': payload.get('sourceId'),
                    'title': payload.get('title'), 'message': payload.get('message'),
                    'buttons': [{k: b.get(k) for k in ('label', 'onclick', 'focus')}
                                for b in buttons if isinstance(b, dict)] if isinstance(buttons, list) else type(buttons).__name__,
                })
            if payload.get('sourceId') != APP_ID:
                return
            method = event['method']
            if method == 'createAlert':
                if payload.get('title') != 'Home Chooser':
                    return
                self.info = {'message': payload.get('message'), 'buttons': [], 'alert': None,
                             'alert_id': None, 'toast': None, 'actions': buttons}
                self.info['buttons'] = [b.get('label') for b in buttons if isinstance(b, dict)] \
                    if isinstance(buttons, list) else []
            elif self.info['message'] and payload.get('message') != self.info['message']:
                return
            sender = event.get('senderUniqueName') or event.get('sender')
            self.calls[(sender, event.get('token'))] = (method, self.info.copy())
        elif (event.get('type') == 'return' and event.get('transport') == 'TX'
              and event.get('sender') == 'com.webos.notification'):
            destination = event.get('destinationUniqueName') or event.get('destination')
            call = self.calls.pop((destination, event.get('replyToken')), None)
            if call:
                method, request_info = call
                payload = event.get('payload') or {}
                if not isinstance(payload, dict):
                    return
                if method == 'createAlert':
                    self.info = request_info
                accepted = payload.get('returnValue') is True
                key = 'alert' if method == 'createAlert' else 'toast'
                self.info[key] = 'accepted' if accepted else 'refused'
                if method == 'createAlert' and accepted:
                    self.info['alert_id'] = payload.get('alertId')

    def snapshot(self):
        with self.lock:
            return dict(self.info, buttons=list(self.info['buttons']))

    def diagnostic(self):
        with self.lock:
            return json.dumps({'candidates': self.candidates, 'events': self.seen})

    def close(self):
        if self.remote_pid:
            pid = self.remote_pid
            self.dev.sh('if [ "$(cat /proc/%d/comm 2>/dev/null)" = ls-monitor ]; then kill %d; fi'
                        % (pid, pid), timeout=10)
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.proc.wait(timeout=5)
        if self.thread:
            self.thread.join(timeout=2)
        if self.proc and self.proc.stdout:
            self.proc.stdout.close()


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


# A known LG crash, accepted as a limitation (docs/home-key-hook.md "Known limitations"): on webOS 9.2.4 the
# boot compositor restart intermittently (about 1 boot in 10 on the VM) sets off a legacy-broadcast-dvb crash
# at API_SI_GetLinkageUriInfo+0x197.  crashd names only the process (`legacy-broadcas.<pid>`); the location is
# in the faultmanager entry, whose gz name carries the same pid.  So a crashd line is known only when a fault
# entry for the same pid has the known location.  Anything else (other process, other location, WebAppMgr)
# stays a crash.
KNOWN_CRASH_DOC = 'docs/research/home-key-hook-early-mount-study.md'
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
    msgs = [t for t in texts if '[crash] Fault Occured' in t and 'audiooutputd' in t]
    return (msgs[0] if len(msgs) == 1 else None), joined


def split_known_crashes(lines, reply):
    """All crash lines fail acceptance, including previously documented firmware crashes."""
    return [], list(lines)


# ---------------------------------------------------------------------------------------------
# The launcher API (docs/launcher-api.md) as the runner checks it
# ---------------------------------------------------------------------------------------------
API_GROUP = 'org.webosbrew.homechooser.applications'
API_VERSION = 1
DISPOSABLE_ID = 'org.webosbrew.hcfixture.disposable'
API_FIXTURE_DIR = os.path.join(REPO, 'build', 'fixtures-api')
API_INFO_FIELDS = ('apiVersion', 'version', 'root', 'permission', 'permitted', 'isSelectedHome')
API_LIST_FIELDS = ('id', 'title', 'icon', 'largeIcon', 'folderPath', 'version', 'vendor', 'type', 'removable', 'visible', 'appCategory')
API_RUNNING_FIELDS = ('id', 'instanceId', 'processId', 'webProcessId')


def api_reply_problems(method, reply):
    """Missing documented fields or wrong types in a successful launcher reply."""
    if not isinstance(reply, dict):
        return ['no JSON reply']
    if reply.get('returnValue') is not True:
        return ['returnValue is not true: %s %s' % (reply.get('errorCode'), reply.get('errorText'))]
    p = []
    if method == 'getInfo':
        p += ['missing %s' % f for f in API_INFO_FIELDS if f not in reply]
        if reply.get('apiVersion') != API_VERSION:
            p.append('apiVersion %r, expected %d' % (reply.get('apiVersion'), API_VERSION))
        if 'permission' in reply and reply['permission'] != API_GROUP:
            p.append('permission %r' % reply['permission'])
        if 'version' in reply and not isinstance(reply['version'], str):
            p.append('version is not a string')
        p += ['%s is not a boolean' % f for f in ('root', 'permitted', 'isSelectedHome') if f in reply and not isinstance(reply[f], bool)]
        return p
    if method in ('closeApp', 'uninstallApp'):
        if reply.get('apiVersion') != API_VERSION:
            p.append('apiVersion %r, expected %d' % (reply.get('apiVersion'), API_VERSION))
        if not isinstance(reply.get('id'), str) or not reply['id']:
            p.append('missing or invalid id')
        if method == 'uninstallApp':
            if reply.get('pending') is not True:
                p.append('pending is not true')
            if reply.get('confirmation') not in ('alert', 'screen'):
                p.append('missing or invalid confirmation')
        return p
    if method == 'getForegroundApp':
        if reply.get('apiVersion') != API_VERSION:
            p.append('invalid apiVersion')
        if not isinstance(reply.get('appId'), str):
            p.append('appId is not a string')
        if 'windowId' in reply and not isinstance(reply['windowId'], str):
            p.append('windowId is not a string')
        if 'processId' in reply and (not isinstance(reply['processId'], int) or isinstance(reply['processId'], bool)):
            p.append('processId is not an integer')
        return p
    if method == 'getAppIcon':
        if reply.get('apiVersion') != API_VERSION:
            p.append('invalid apiVersion')
        if reply.get('mimeType') not in ('image/png', 'image/jpeg', 'image/gif', 'image/svg+xml', 'image/webp'):
            p.append('invalid mimeType')
        if not isinstance(reply.get('data'), str):
            p.append('data is not a string')
        if not isinstance(reply.get('byteLength'), int) or isinstance(reply.get('byteLength'), bool):
            p.append('byteLength is not an integer')
        else:
            try:
                import base64
                if len(base64.b64decode(reply.get('data', ''), validate=True)) != reply['byteLength']:
                    p.append('decoded data length differs from byteLength')
            except (ValueError, TypeError):
                p.append('data is not base64')
        return p
    if method == 'getAppInfo':
        if reply.get('apiVersion') != API_VERSION:
            p.append('invalid apiVersion')
        item = reply.get('app')
        if not isinstance(item, dict):
            return p + ['app is not an object']
        reply = {'returnValue': True, 'apiVersion': API_VERSION, 'apps': [item]}
        method = 'listApps'
    key, allowed = {'listApps': ('apps', API_LIST_FIELDS), 'listRunning': ('running', API_RUNNING_FIELDS)}[method]
    if reply.get('apiVersion') != API_VERSION:
        p.append('apiVersion %r, expected %d' % (reply.get('apiVersion'), API_VERSION))
    items = reply.get(key)
    if not isinstance(items, list):
        return p + ['%s is not an array' % key]
    for it in items:
        if not isinstance(it, dict) or not it.get('id'):
            p.append('an item without id: %r' % (it,))
            continue
        if not isinstance(it['id'], str):
            p.append('an item with non-string id: %r' % (it,))
        for field in allowed:
            if field not in it or field == 'id':
                continue
            value = it[field]
            if field in ('removable', 'visible') and not isinstance(value, bool):
                p.append('%s has nonboolean %s' % (it['id'], field))
            elif field == 'appCategory' and (not isinstance(value, list) or any(not isinstance(v, str) for v in value)):
                p.append('%s has invalid appCategory' % it['id'])
            elif field not in ('removable', 'visible', 'appCategory', 'processId', 'webProcessId') and not isinstance(value, str):
                p.append('%s has nonstring %s' % (it['id'], field))
        for field in allowed:
            if field in it and field in ('processId', 'webProcessId') and (not isinstance(it[field], int) or isinstance(it[field], bool)):
                p.append('%s has nonnumeric %s' % (it['id'], field))
    return p


def api_refused(reply):
    """A refusal: a JSON reply with returnValue false (the service's errorCode, or the hub's denial)."""
    return isinstance(reply, dict) and reply.get('returnValue') is False


# ---------------------------------------------------------------------------------------------
# Launchers under test: Dock, one of the six fixtures in e2e/fixtures ({native,qml,web} x
# {normal,overlay}), or any app id with --launcher-ipk.
# ---------------------------------------------------------------------------------------------
DOCK_ID = 'org.webosbrew.dock'
DOCK_IPK = None  # Dock is optional; supply --dock-ipk or hosts.toml [paths] dock_ipk
FIXTURE_TYPES = ('native', 'qml', 'web')
FIXTURE_WINDOWS = ('normal', 'overlay')
FIXTURE_DIR = os.path.join(REPO, 'build', 'fixtures')
FIXTURE_ID = 'org.webosbrew.hcfixture.%s.%s'


def lines_after(lines, mark):
    """The log lines written after `mark` lines; a log shorter than the mark was started afresh (a new
    boot), so all of it counts."""
    return lines[mark:] if mark <= len(lines) else list(lines)


def ipk_files(path):
    """{member name: bytes} of the data.tar.gz inside an ipk (an ar archive; stdlib only)."""
    raw = open(path, 'rb').read()
    if raw[:8] != b'!<arch>\n':
        raise ValueError('%s is not an ar archive' % path)
    pos, data = 8, None
    while pos + 60 <= len(raw):
        name = raw[pos:pos + 16].decode().strip().rstrip('/')
        size = int(raw[pos + 48:pos + 58].decode().strip())
        body = raw[pos + 60:pos + 60 + size]
        if name == 'data.tar.gz':
            data = body
        pos += 60 + size + (size & 1)
    if data is None:
        raise ValueError('no data.tar.gz in %s' % path)
    out = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as t:
        for m in t.getmembers():
            if m.isfile():
                out[m.name.lstrip('./')] = t.extractfile(m).read()
    return out


def ipk_appinfo(path):
    for name, body in ipk_files(path).items():
        if name.endswith('/appinfo.json') and name.startswith('usr/palm/applications/'):
            return json.loads(body.decode('utf-8'))
    raise ValueError('no appinfo.json in %s' % path)


class Launcher:
    """id, ipk, and what the appinfo inside the ipk declares."""

    def __init__(self, spec, ident, ipk, kind='app'):
        self.spec, self.id, self.ipk, self.kind = spec, ident, ipk, kind
        self.appinfo = ipk_appinfo(ipk) if ipk and os.path.exists(ipk) else {}
        self.overlay = self.appinfo.get('defaultWindowType') == 'overlay'
        self.title = self.appinfo.get('title', ident)
        self.tag = spec.replace(':', '-')
        parts = ident.split('.')
        self.fixture = (parts[-2], parts[-1]) if kind == 'fixture' else None  # (type, window)

    @property
    def is_fixture(self):
        return self.kind == 'fixture'


def fixture_specs():
    return ['fixture:%s.%s' % (t, w) for t in FIXTURE_TYPES for w in FIXTURE_WINDOWS]


def api_fixture_ipks(announce=print):
    """--api-check: the web fixtures built with requiredPermissions ["org.webosbrew.homechooser.applications"] and the
    disposable non-home app, in build/fixtures-api/ (offline, built once).  {app id: ipk}."""
    def found():
        out = {}
        for ident in [FIXTURE_ID % ('web', w) for w in FIXTURE_WINDOWS] + [DISPOSABLE_ID]:
            g = sorted(glob.glob(os.path.join(API_FIXTURE_DIR, ident + '_*.ipk')))
            info = ipk_appinfo(g[-1]) if g and ident != DISPOSABLE_ID else {}
            if g and (ident == DISPOSABLE_ID or (
                    API_GROUP in info.get('requiredPermissions', [])
                    and info.get('largeIcon') == 'oversize.png')):
                out[ident] = g[-1]
        return out
    have = found()
    if len(have) < len(FIXTURE_WINDOWS) + 1:
        announce('API fixtures missing: building the web fixtures with %s and the disposable app into %s (offline)'
                 % (API_GROUP, API_FIXTURE_DIR))
        os.makedirs(API_FIXTURE_DIR, exist_ok=True)
        env = dict(os.environ, HC_FIXTURE_PERMISSION=API_GROUP)
        for script in ('web', 'disposable'):
            try:
                p = subprocess.run(['sh', os.path.join(HERE, 'fixtures', script, 'build.sh'), API_FIXTURE_DIR],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600, env=env)
                announce(p.stdout.decode('utf-8', 'replace').strip()[-400:])
            except subprocess.TimeoutExpired:
                announce('%s/build.sh timed out' % script)
        have = found()
    if len(have) < len(FIXTURE_WINDOWS) + 1:
        raise SystemExit('could not build the API fixtures in %s' % API_FIXTURE_DIR)
    return have


def make_launcher(spec, ipk_override=None, announce=print, dock_ipk=DOCK_IPK, api=False):
    """api (--api-check): a web fixture comes from the build with the permission; native and qml
    fixtures cannot declare it."""
    if spec == 'dock':
        if not (ipk_override or dock_ipk):
            raise SystemExit('Dock has no default ipk; use --dock-ipk or --launcher-ipk, or choose fixture:web.normal')
        return Launcher(spec, DOCK_ID, ipk_override or dock_ipk, 'dock')
    if spec.startswith('fixture:'):
        t, _, w = spec[8:].partition('.')
        if t not in FIXTURE_TYPES or w not in FIXTURE_WINDOWS:
            raise SystemExit('unknown fixture %r; use fixture:<native|qml|web>.<normal|overlay>' % spec)
        ident = FIXTURE_ID % (t, w)
        if api:
            if t != 'web':
                raise SystemExit('--api-check needs a launcher that declares %s: fixture:web.<normal|overlay>, '
                                 'or your own launcher with --launcher-ipk' % API_GROUP)
            return Launcher(spec, ident, api_fixture_ipks(announce)[ident], 'fixture')
        found = sorted(glob.glob(os.path.join(FIXTURE_DIR, ident + '_*.ipk')))
        if not found:
            announce('fixture ipk for %s missing: running e2e/fixtures/build.sh (offline)' % ident)
            rc, out = run(['sh', os.path.join(HERE, 'fixtures', 'build.sh')], 600)
            announce(out.strip()[-400:])
            found = sorted(glob.glob(os.path.join(FIXTURE_DIR, ident + '_*.ipk')))
        if not found:
            raise SystemExit('could not build the fixture ipk for %s' % ident)
        return Launcher(spec, ident, found[-1], 'fixture')
    if not ipk_override:
        raise SystemExit('--launcher %s needs --launcher-ipk' % spec)
    return Launcher(spec, spec, ipk_override, 'app')


def qml_support(major):
    """(supported, evidence) from e2e/fixtures/qml/support.json for a release; True/False/'unknown'."""
    try:
        d = json.load(open(os.path.join(HERE, 'fixtures', 'qml', 'support.json')))
        e = d.get(str(major), {})
        return e.get('supported', 'unknown'), e.get('evidence', 'no entry')
    except (OSError, ValueError):
        return 'unknown', 'support.json unreadable'


def default_update_ref():
    """The previous commit that changed app/ or service/: the last one before HEAD, unless
    those paths have uncommitted changes (then HEAD is the build the working tree departs from)."""
    rc, dirty = run(['git', '-C', REPO, 'status', '--porcelain', '--', 'app', 'service'], 30)
    if dirty.strip():
        return 'HEAD'
    rc, out = run(['git', '-C', REPO, 'log', '-1', '--format=%H', 'HEAD~1', '--', 'app', 'service'], 30)
    ref = out.strip()
    if rc != 0 or not ref:
        raise SystemExit('cannot find a previous commit touching app/ or service/; use --update-from')
    return ref


def build_old_home_chooser(ref, rec):
    """Build Home Chooser from a git ref in a temp dir: `git archive` extracted under /tmp (the main
    working tree and .git are not touched), node_modules linked from the main tree.
    Returns (ipk path, temp dir)."""
    tmp = tempfile.mkdtemp(prefix='hc-e2e-old-')
    rc, out = run(['sh', '-c', 'git -C %s archive %s | tar -x -C %s' % (shlex.quote(REPO), shlex.quote(ref), shlex.quote(tmp))], 120)
    rec.check('git archive %s into %s' % (ref, tmp), rc == 0, out[-300:])
    if rc != 0:
        raise RuntimeError('git archive failed')
    os.makedirs(os.path.join(tmp, 'build', 'ipk'), exist_ok=True)
    os.symlink(os.path.join(REPO, 'app', 'node_modules'), os.path.join(tmp, 'app', 'node_modules'))
    rec.say('building the old Home Chooser (%s): npm run package in %s/app' % (ref, tmp))
    rc, out = run(['sh', '-c', 'cd %s/app && npm run package' % shlex.quote(tmp)], 1200)
    ipks = sorted(glob.glob(os.path.join(tmp, 'build', 'ipk', '*.ipk')))
    rec.check('old Home Chooser build (%s) produced an ipk' % ref, rc == 0 and bool(ipks), out[-300:])
    if rc != 0 or not ipks:
        raise RuntimeError('old build failed')
    return ipks[-1], tmp


def current_home_chooser_ipk(rec, build=False):
    ipks = sorted(glob.glob(os.path.join(REPO, 'build/ipk/*.ipk')))
    if build or not ipks:
        rec.say('building Home Chooser: npm run package')
        rc, out = run(['sh', '-c', 'cd %s/app && npm run package' % shlex.quote(REPO)], 900)
        rec.check('npm run package', rc == 0, out[-300:])
        ipks = sorted(glob.glob(os.path.join(REPO, 'build/ipk/*.ipk')))
    return ipks[-1]


def candidates(vm):
    """A slot accepts a VM name or an ordered tuple of alternative VM names."""
    return (vm,) if isinstance(vm, str) else tuple(vm)


def entry(release_or_vm):
    for m in MATRIX:
        if release_or_vm == m[0] or release_or_vm in candidates(m[1]):
            vm = release_or_vm if release_or_vm != m[0] else candidates(m[1])[-1]
            return (m[0], vm, *m[2:])
    raise SystemExit('unknown VM/release %r; see the matrix in e2e/README.md' % release_or_vm)


# ---------------------------------------------------------------------------------------------
# Beanjar machines: e2e/hosts.toml, then the git-ignored e2e/hosts.local.toml over it.  Without
# either file the one machine is today's: `pi`, reached with `ssh raspberrypi`.
# ---------------------------------------------------------------------------------------------
HOSTS_FILE = os.path.join(HERE, 'hosts.toml')
HOSTS_LOCAL_FILE = os.path.join(HERE, 'hosts.local.toml')
DEFAULT_HOSTS = {'default': 'pi', 'hosts': {'pi': {'ssh': 'raspberrypi'}}, 'paths': {}}


def load_hosts(path=None):
    """{'default': name, 'hosts': {name: {'ssh': alias} or {'local': True}, optional 'vms': [...]},
    'paths': {...}}.  path: one file instead of hosts.toml + hosts.local.toml."""
    import tomllib
    files = [path] if path else [f for f in (HOSTS_FILE, HOSTS_LOCAL_FILE) if os.path.exists(f)]
    if not files:
        return json.loads(json.dumps(DEFAULT_HOSTS))
    cfg = {'default': None, 'hosts': {}, 'paths': {}}
    for f in files:
        with open(f, 'rb') as fh:
            d = tomllib.load(fh)
        cfg['default'] = d.get('default', cfg['default'])
        for name, h in d.get('hosts', {}).items():
            cfg['hosts'].setdefault(name, {}).update(h)
        cfg['paths'].update(d.get('paths', {}))
    for name, h in cfg['hosts'].items():
        import math
        if not math.isfinite(float(h.get('slowdown', 1.0))) or float(h.get('slowdown', 1.0)) < 1:
            raise SystemExit('host %s slowdown must be finite and >= 1.0' % name)
        if not h.get('local') and not h.get('ssh'):
            raise SystemExit('host %s in %s has neither ssh = "<alias>" nor local = true' % (name, ', '.join(files)))
    cfg['default'] = cfg['default'] or next(iter(cfg['hosts']), None)
    return cfg


# ---------------------------------------------------------------------------------------------
# Running things
# ---------------------------------------------------------------------------------------------
SLOWDOWN = 1.0


def seconds(value):
    return value * SLOWDOWN


def sleep(value):
    time.sleep(seconds(value))


def run(argv, timeout=120, input=None, env=None):
    """(rc, stdout+stderr) of a local process."""
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=seconds(timeout),
                           input=input, env=env)
        return p.returncode, p.stdout.decode('utf-8', 'replace')
    except subprocess.TimeoutExpired as e:
        return 124, (e.stdout or b'').decode('utf-8', 'replace') + '\n[timeout]'


class ScreensaverOutcome(RuntimeError):
    def __init__(self, status, evidence):
        super().__init__(evidence)
        self.status = status


def power_field(reply, key):
    """Firmware replies may wrap the state in a state object."""
    if isinstance(reply, dict):
        if key in reply:
            return reply[key]
        for value in reply.values():
            found = power_field(value, key)
            if found is not None:
                return found
    return None


class KeySendError(RuntimeError):
    """Input transport failed; callers must not continue a UI step."""


# Executed by python3 on the Beanjar host; argv stays separate from Python source.
QMP_SENDKEY = r"""
import json
import os
import socket
import sys
import time
try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(5)
        sock.connect(os.path.expandvars(sys.argv[1]))
        with sock.makefile('rb') as replies:
            def receive():
                line = replies.readline()
                if not line:
                    raise RuntimeError('QMP closed the connection')
                return json.loads(line)
            if 'QMP' not in receive():
                raise RuntimeError('missing QMP greeting')
            def execute(command, arguments, request_id):
                sock.sendall((json.dumps(dict(execute=command, arguments=arguments,
                                              id=request_id)) + '\n').encode())
                deadline = time.monotonic() + 5
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('QMP reply timed out')
                    sock.settimeout(remaining)
                    reply = receive()
                    if 'event' in reply:
                        continue
                    if reply.get('id') != request_id:
                        raise RuntimeError('unexpected QMP reply: ' + repr(reply))
                    if 'error' in reply:
                        raise RuntimeError('QMP error: ' + json.dumps(reply['error']))
                    if 'return' not in reply:
                        raise RuntimeError('invalid QMP reply: ' + repr(reply))
                    return reply['return']
            execute('qmp_capabilities', {}, 'capabilities')
            result = execute('human-monitor-command',
                             {'command-line': 'sendkey ' + sys.argv[2]}, 'key')
            if result:
                raise RuntimeError('sendkey monitor error: ' + str(result))
            print('QMP sendkey acknowledged')
except Exception as exc:
    print('QMP sendkey failed: ' + str(exc), file=sys.stderr)
    sys.exit(1)
"""


class BeanjarHost:
    """VM host operations and local Beanjar display routing go through here.

    local mode: beanjar and friends run as local commands.  remote mode: each command line is
    run with `ssh <host>` (an ssh alias or user@host; BatchMode, so it never prompts) and files come
    back with scp.  The rest of the runner never quotes for the remote.  The mode comes from the
    host's entry in hosts.toml, never from `beanjar` being on PATH: this PC has Beanjar's own
    multi-host CLI, which is not the machine the VMs and their QMP sockets are on.
    Display operations use that local CLI with HOST/NAME instead of our ssh plumbing.
    """

    def __init__(self, mode='remote', device='raspberrypi', name='pi'):
        self.mode, self.device, self.name = mode, device, name
        self._dirs = {}
        self.slowdown = 1.0
        self.input_rec = None
        self._remote_keys = {}
        self._frame_mapping_logged = False

    @classmethod
    def from_config(cls, cfg, name, mode=None, device=None):
        h = cfg['hosts'].get(name)
        if h is None:
            raise SystemExit('unknown Beanjar host %r; hosts: %s (e2e/hosts.toml)' % (name, ', '.join(cfg['hosts'])))
        host = cls(mode or ('local' if h.get('local') else 'remote'), device or h.get('ssh') or 'localhost', name)
        host.slowdown = float(h.get('slowdown', 1.0))
        return host

    # -- plumbing
    def sh(self, cmd, timeout=120):
        if self.mode == 'local':
            return run(['sh', '-c', cmd], timeout)
        return run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', self.device, cmd], timeout)

    def _q(self, *words):
        return ' '.join(shlex.quote(str(w)) for w in words)

    def fetch(self, remote_path, local_path):
        """Copy a host file to the machine running the runner."""
        if self.mode == 'local':
            shutil.copyfile(remote_path, local_path)
            return True
        rc, _ = run(['scp', '-q', '-o', 'BatchMode=yes', '%s:%s' % (self.device, remote_path), local_path])
        return rc == 0

    def beanjar(self, *args, timeout=300):
        return self.sh('beanjar ' + self._q(*args), timeout)

    # -- Beanjar
    def list_vms(self):
        """[{name, state, ...}] of this machine's own VMs (`beanjar list --local --json`; a host whose
        beanjar has remote hosts configured would otherwise list theirs too).  Raises on failure, so an
        unreachable host never reads as an idle one."""
        rc, out = self.beanjar('list', '--local', '--json', timeout=60)
        if rc == 2:  # a beanjar without --local: no remote hosts there either
            rc, out = self.beanjar('list', '--json', timeout=60)
        try:
            rows = json.loads(out[re.search(r'^\[', out, re.M).start():]) if rc == 0 else None
        except (ValueError, AttributeError):
            rows = None
        if not isinstance(rows, list):
            raise RuntimeError('beanjar list on %s failed (rc=%s): %s' % (self.name, rc, out.strip()[-300:]))
        return [r for r in rows if r.get('host') in (None, 'local')]

    def running_vms(self):
        """Every VM that is not Off (Starting, Running, Stopping all hold the machine)."""
        return [r['name'] for r in self.list_vms() if r.get('state') not in ('Off', 'off')]

    def vm_dir(self, vm):
        if vm not in self._dirs:
            self._dirs[vm] = self.info(vm).get('dir') or '$HOME/.local/share/beanjar/vms/' + vm
        return self._dirs[vm]

    def info(self, vm):
        rc, out = self.beanjar('info', vm)
        d = {}
        for line in out.splitlines():
            if ': ' in line:
                k, v = line.split(': ', 1)
                d.setdefault(k.strip(), v.strip())
        return d

    def settings(self, vm):
        rc, out = self.beanjar('settings', vm, 'show')
        return dict(l.split(': ', 1) for l in out.splitlines() if ': ' in l)

    def set_settings(self, vm, **kv):
        return self.beanjar('settings', vm, 'set', *['%s=%s' % i for i in kv.items()])

    def start(self, vm):
        # Check the host immediately before every boot, including scenario reboots.
        running = self.running_vms()
        if running:
            raise RuntimeError('refusing boot while VMs are running: %s' % running)
        result = self.beanjar('start', vm, timeout=600)
        if result[0] == 0:
            self.vnc_start(vm)
        return result

    def stop(self, vm):
        self.vnc_stop(vm)
        return self.beanjar('stop', vm, timeout=300)

    def remote_target(self, vm):
        """The local multi-host CLI owns routing for display input."""
        return vm if self.mode == 'local' else self.name + '/' + vm

    def display_beanjar(self, *args, timeout=60):
        try:
            return run(['beanjar', *args], timeout)
        except OSError as e:
            return 127, str(e)

    def screensaver(self, verb, vm):
        """Use the local multi-host CLI; None means an older CLI lacks the helper."""
        if not hasattr(self, '_screensaver_probe'):
            self._screensaver_probe = self.display_beanjar('screensaver', '--help', timeout=15)
        rc, help_text = self._screensaver_probe
        if rc == 127 or (rc == 2 and re.search(
                r"(?:invalid choice|unknown command|unrecognized (?:command|arguments)).*screensaver",
                help_text, re.I)):
            return None
        if rc:
            return rc, help_text  # A broken probe is a helper failure, not an older CLI.
        return self.display_beanjar('screensaver', verb, self.remote_target(vm),
                                    '--timeout', '60', '--json', timeout=90)

    def click_available(self):
        if not hasattr(self, '_click_available'):
            rc, out = self.beanjar('remote', 'click', '--help', timeout=30)
            self._click_available = rc == 0 and '--frame' in out
            if not self._click_available and self.input_rec:
                self.input_rec.note('click unavailable on %s; falling back to keys' % self.name, out)
        return self._click_available

    def remote_click(self, vm, centre, frame):
        return self.display_beanjar('remote', 'click', self.remote_target(vm),
                                   str(centre[0]), str(centre[1]), '--frame',
                                   '%sx%s' % (frame['width'], frame['height']), '--json')

    def vnc_start(self, vm):
        if self.input_rec is None:
            return
        target = self.remote_target(vm)
        rc, out = self.display_beanjar('vnc', 'start', target, '--port', '0')
        # Beanjar rejects a duplicate start before opening another front end.
        already = 'beanjar-vnc is already serving %s (pid ' % vm in out
        self.input_rec.say('beanjar vnc start %s: %s' % (target, out.strip()))
        if rc and not already:
            error = 'beanjar vnc start %s failed (rc=%s): %s' % (target, rc, out.strip())
            self.input_rec.check('input front end', False, error)
            raise KeySendError(error)

    def vnc_stop(self, vm):
        if self.input_rec is None:
            return
        target = self.remote_target(vm)
        rc, out = self.display_beanjar('vnc', 'stop', target)
        self.input_rec.say('beanjar vnc stop %s: %s' % (target, out.strip()))
        if rc:
            self.input_rec.check('input front end cleanup', False,
                                 '%s (rc=%s): %s' % (target, rc, out.strip()))

    def remote_press(self, vm, key):
        target = self.remote_target(vm)
        if vm not in self._remote_keys:
            rc, out = self.display_beanjar('remote', 'layout', target, '--json')
            try:
                layout = json.loads(out) if rc == 0 else None
                keys = {k['name'].upper() for k in layout['keys'] if k['code'] is not None}
            except (ValueError, TypeError, KeyError, AttributeError):
                return 1, 'remote layout %s failed (rc=%s): %s' % (target, rc, out.strip())
            required = {'ENTER', 'BACK', 'HOME', 'UP', 'DOWN', 'LEFT', 'RIGHT'}
            missing = required - keys
            if missing:
                return 1, 'remote layout %s missing keys: %s' % (target, ', '.join(sorted(missing)))
            self._remote_keys[vm] = keys
        if key not in self._remote_keys[vm]:
            return 1, 'remote layout %s missing key: %s' % (target, key)
        return self.display_beanjar('remote', 'press', target, key,
                                    '--hold', str(round(seconds(100))))

    # -- input and capture
    def sendkey(self, vm, key):
        sock = self.vm_dir(vm) + '/run/qmp.sock'
        return self.sh(self._q('python3', '-c', QMP_SENDKEY, sock, key), 30)

    def viewer_open(self, vm):
        return self.beanjar('viewer', 'open', vm, timeout=60)

    def viewer_close(self, vm):
        return self.beanjar('viewer', 'close', vm, timeout=60)

    def tv_screenshot(self, vm, local_path):
        """`beanjar screenshot` (0.3.1+): the TV picture of the running VM, headless or through a viewer.
        Returns (ok, detail); detail is the command's output (--json: path, taken_by)."""
        tmp = '/tmp/e2e-tv-%s-%d.png' % (vm, os.getpid())
        rc, out = self.beanjar('screenshot', vm, tmp, '--json', timeout=60)
        ok = rc == 0 and self.fetch(tmp, local_path)
        self.sh('rm -f ' + shlex.quote(tmp))
        return ok, out.strip()

    def screenshot(self, local_path):
        """No portable desktop capture exists in the host Python standard library."""
        return False, 'desktop capture unavailable: use beanjar screenshot or guest capture'


# ---------------------------------------------------------------------------------------------
# Device access (ares-* only)
# ---------------------------------------------------------------------------------------------
class Device:
    def __init__(self, vm, root=None):
        self.vm, self.root = vm, root or vm + '-root'

    def exec(self, cmd, timeout=60):
        return run(['ares-do', '--timeout', str(timeout * 1000), 'exec', '-d', self.root, cmd], timeout + 10)

    def sh(self, cmd, timeout=60):
        """Root shell command; (rc, output).  ares-shell -r is not used for stdout fidelity."""
        return run(['ares-shell', '-d', self.root, '-r', cmd], timeout)

    def luna(self, uri, payload='{}'):
        rc, out = run(['ares-do', 'luna', '-d', self.root, '--allow-false', uri, payload], 60)
        return out.strip()

    def install(self, ipk):
        return run(['ares-install', '-d', self.vm, ipk], 300)

    def remove(self, appid):
        return run(['ares-install', '-d', self.vm, '--remove', appid], 300)

    def installed(self):
        rc, out = run(['ares-install', '-d', self.vm, '-l'], 60)
        if rc != 0 or out.startswith('Failed to connect'):
            raise RuntimeError('ares-install -l on %s failed (rc=%s): %s' % (self.vm, rc, out.strip()[-300:]))
        return out.split()

    def launch(self, appid, params=None):
        return run(['ares-launch', '-d', self.vm, appid] + (['-p', json.dumps(params)] if params else []), 60)

    def close(self, appid):
        return run(['ares-launch', '-d', self.vm, '-c', appid], 60)

    def key(self, *keys):
        return run(['ares-do', 'key', '-d', self.root, '--delay', '300'] + list(keys), 60)

    def screenshot(self, path):
        return run(['ares-do', 'screenshot', '-d', self.root, path], 60)

    def foreground(self):
        out = self.luna('luna://com.webos.applicationManager/getForegroundAppInfo')
        try:
            return json.loads(out).get('appId', ''), out
        except ValueError:
            return None, out

    def preload_policy(self, appid=STOCK):
        """(isEnabled or None, raw reply) of preloadmanager/getPreloadPolicy for one app, as root."""
        out = self.luna('luna://com.webos.service.preloadmanager/getPreloadPolicy')
        try:
            for a in json.loads(out).get('applications', []):
                if appid in [v for v in a.values() if isinstance(v, str)]:
                    return a.get('isEnabled'), out
        except (ValueError, AttributeError, TypeError):
            pass
        return None, out

    def record_home(self):
        out = self.luna('luna://com.webos.settingsservice/getSystemSettings',
                        '{"category":"general","keys":["defaultApps"]}')
        try:
            d = json.loads(out)['settings']['defaultApps']
            if isinstance(d, str):
                d = json.loads(d)
            return d.get('home'), out
        except (ValueError, KeyError, AttributeError, TypeError):
            return '<unreadable>', out

    def release(self):
        rc, out = self.sh('. /usr/lib/os-release; echo "$VERSION_ID|$VERSION|$PRETTY_NAME"')
        m = re.search(r'(\d+)\.(\d+)', out)
        return (int(m.group(1)) if m else 0), out.strip()

    def mounts(self):
        rc, out = self.sh("awk '$2 ~ /^\\/usr\\/lib\\/qml\\// {print $2}' /proc/mounts")
        return [l for l in out.split() if l.startswith('/usr/lib/qml/')]

    def luna_as(self, app, uri, payload='{}', timeout=60):
        """(rc, raw, parsed) of a root `luna-send -a <app>`: the hub takes the caller to be that installed app
        with its own client permissions (docs/launcher-api.md).  app None: no application id at all.  Through
        exec, since luna-send under ares-shell -r prints nothing."""
        ident = ('-a %s ' % shlex.quote(app)) if app else ''
        rc, out = self.exec('luna-send %s-n 1 %s %s 2>&1' % (ident, uri, shlex.quote(payload)), timeout)
        return rc, out.strip(), parse_luna(out)

    def boot_id(self):
        rc, out = self.sh('cat /proc/sys/kernel/random/boot_id', 20)
        m = re.search(r'[0-9a-f]{8}-[0-9a-f-]{27}', out)
        return m.group(0) if m else ''

    def unit(self, name):
        """{MainPID, NRestarts, ActiveState} of a systemd unit."""
        rc, out = self.sh('systemctl show %s -p MainPID -p NRestarts -p ActiveState 2>&1' % name)
        return dict(l.strip().split('=', 1) for l in out.splitlines() if '=' in l)

    def app_installed(self, appid):
        """By getAppInfo as root (ares-install -l lists only developer apps on some releases)."""
        r = parse_luna(self.luna('luna://com.webos.applicationManager/getAppInfo', json.dumps({'id': appid})))
        return bool(r) and r.get('returnValue') is True

    def service_root(self):
        rc, out = self.sh("ps -eo user,args | grep '[h]omechooser-service'")
        return out.strip()


# ---------------------------------------------------------------------------------------------
# Elevation strategies
# ---------------------------------------------------------------------------------------------
class Elevation:
    name = '?'

    def prepare(self, dev, rec):
        """Before the app starts."""

    def verify(self, dev, rec, wait=90):
        """The service runs as root: by ps (the service answers only the app)."""
        end = time.time() + seconds(wait)
        out = ''
        while time.time() < end:
            out = dev.service_root()
            if out.startswith('root') and 'homechooser-service' in out and 'jailer' not in out:
                break
            sleep(3)
        rec.check('elevation (%s): service runs as root' % self.name,
                  out.startswith('root') and 'jailer' not in out, 'ps: ' + (out or '(not running)'))


class LunaElevation(Elevation):
    """The app asks Homebrew Channel itself (luna://org.webosbrew.hbchannel.service/elevateService);
    the runner only checks the outcome."""
    name = 'luna'


class RootShellElevation(Elevation):
    """Test-harness substitute for the missing luna path on 11.x/12.x: run Homebrew Channel's own
    elevate-service helper as root (service.ts: elevate-service <service id>), then `quit` the
    service so the hub starts the rewritten, root instance (what the app does after elevating)."""
    name = 'root-shell'

    def prepare(self, dev, rec):
        rc, out = dev.sh('%s %s; echo rc=$?' % (HB_ELEVATE, SVC_ID), 90)
        rec.note('elevate-service ' + SVC_ID, out.strip())
        # the (jailed) instance is started on demand by any call; call it, then make it quit
        dev.luna('luna://%s/status' % SVC_ID)
        dev.luna('luna://%s/quit' % SVC_ID)
        sleep(2)
        dev.luna('luna://%s/status' % SVC_ID)


ELEVATIONS = {'luna': LunaElevation, 'root-shell': RootShellElevation}


# ---------------------------------------------------------------------------------------------
# Inspector (CDP over a websocket, forwarded with ares-novacom)
# ---------------------------------------------------------------------------------------------
class Inspector:
    def __init__(self, vm):
        # A free port is picked and released before ares-novacom binds it, so a parallel run can take
        # it in between; then our forward exits at once and another port is tried.
        for _ in range(3):
            s = socket.socket()
            s.bind(('127.0.0.1', 0))
            self.port = s.getsockname()[1]
            s.close()
            self.proc = subprocess.Popen(['ares-novacom', '-d', vm, '-f', '-p', '9998:%d' % self.port],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         start_new_session=True)
            sleep(2)
            if self.proc.poll() is None:
                break

    def close(self):
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except OSError:
            pass

    def _page(self):
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d/json' % self.port, timeout=5) as r:
                pages = json.loads(r.read().decode())
        except Exception:
            return None
        for p in pages:
            if APP_ID in p.get('url', ''):
                return p
        return None

    def log_pages(self, why):
        """Say what /json lists, so a page-lookup failure is told apart from empty DOM content."""
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d/json' % self.port, timeout=5) as r:
                pages = [(p.get('id'), p.get('title'), p.get('url')) for p in json.loads(r.read().decode())]
        except Exception as e:
            pages = 'unreadable: %r' % (e,)
        print('  inspector evaluate -> None (%s); /json pages: %s' % (why, pages), flush=True)

    def evaluate(self, expr):
        """Runtime.evaluate in the Home Chooser page; None if it is not there.  Why a call gave None is
        kept in self.last_error (no page, the forward gone, a socket error, an error reply, a JS exception)."""
        self.last_error = None
        page = self._page()
        if not page:
            self.last_error = 'no page for %s in /json (forward %s)' % (
                APP_ID, 'alive' if self.proc.poll() is None else 'exited rc=%s' % self.proc.returncode)
            self.log_pages('no page for %s' % APP_ID)
            return None
        s = None
        try:
            s = socket.create_connection(('127.0.0.1', self.port), timeout=8)
            key = base64.b64encode(os.urandom(16)).decode()
            s.sendall(('GET /devtools/page/%s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nUpgrade: websocket\r\n'
                       'Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n'
                       % (page['id'], self.port, key)).encode())
            buf = b''
            while b'\r\n\r\n' not in buf:
                buf += s.recv(4096)
            buf = buf.split(b'\r\n\r\n', 1)[1]
            msg = json.dumps({'id': 1, 'method': 'Runtime.evaluate',
                              'params': {'expression': expr, 'returnByValue': True}}).encode()
            mask = os.urandom(4)
            hdr = bytes([0x81])
            if len(msg) < 126:
                hdr += bytes([0x80 | len(msg)])
            elif len(msg) < 65536:
                hdr += bytes([0x80 | 126]) + struct.pack('>H', len(msg))
            else:
                hdr += bytes([0x80 | 127]) + struct.pack('>Q', len(msg))
            s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(msg)))
            while True:
                frame, buf = self._frame(s, buf)
                if frame is None:
                    self.last_error = 'websocket gave no frame'
                    return None
                try:
                    d = json.loads(frame.decode('utf-8', 'replace'))
                except ValueError:
                    continue
                if d.get('id') == 1:
                    res = d.get('result') or {}
                    v = (res.get('result') or {}).get('value')
                    if v is None:
                        self.last_error = 'reply without a value: %s' % json.dumps(d)[:400]
                    return v
        except (OSError, ValueError) as e:
            self.last_error = 'websocket %s: %r (page %s, forward %s)' % (
                'handshake/read', e, page.get('id'), 'alive' if self.proc.poll() is None else 'exited')
            return None
        finally:
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass

    @staticmethod
    def _frame(s, buf):
        def need(n):
            nonlocal buf
            while len(buf) < n:
                chunk = s.recv(65536)
                if not chunk:
                    raise OSError('closed')
                buf += chunk
        need(2)
        ln = buf[1] & 0x7f
        off = 2
        if ln == 126:
            need(4)
            ln, off = struct.unpack('>H', buf[2:4])[0], 4
        elif ln == 127:
            need(10)
            ln, off = struct.unpack('>Q', buf[2:10])[0], 10
        need(off + ln)
        data, buf = buf[off:off + ln], buf[off + ln:]
        return data, buf


FOCUS_JS = ("(function(){var e=document.activeElement;var r=e&&e.closest&&e.closest('[data-app-id]');"
            "return JSON.stringify({row:r?r.getAttribute('data-app-id'):null,"
            "text:e?(e.textContent||'').trim().slice(0,80):null,"
            "dialog:(function(){var d=document.querySelector('[role=dialog]');return d?d.getAttribute('aria-label'):null})(),"
            "dialogText:(function(){var d=document.querySelector('[role=dialog]');return d?(d.textContent||'').trim().slice(0,300):null})(),"
            "toast:(function(){var d=document.querySelector('[role=status]');return d?d.textContent:null})(),"
            "restart:!!document.querySelector('[data-spotlight-id=restart-now]'),"
            "rows:Array.prototype.map.call(document.querySelectorAll('[data-app-id]'),function(x){return x.getAttribute('data-app-id')})})})()")
# Key focus of the Home Chooser page: on 9.x its window is an overlay (appinfo defaultWindowType), so sam's
# foreground app stays the card beneath (com.webos.app.home) while our dialog has the keys; the page's own
# document.hasFocus() is what says the keys reach it.
PAGE_JS = ("JSON.stringify({focus:document.hasFocus(),visibility:document.visibilityState,href:location.href})")
PLANT_JS = ("(function(){localStorage.setItem('homechooser.pending',JSON.stringify("
            "{id:'%s',title:'stale',at:Date.now()-86400000}));return localStorage.getItem('homechooser.pending')})()")
MARKER_JS = "String(localStorage.getItem('homechooser.pending'))"

# The row carrying the check mark (aria-label "Current"), by data-app-id; '' when none.
TICK_JS = ("(function(){var c=document.querySelector('[data-app-id] [aria-label=Current]');"
           "var r=c&&c.closest('[data-app-id]');return r?r.getAttribute('data-app-id'):''})()")


# ---------------------------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------------------------
class Recorder:
    def __init__(self, outdir):
        self.outdir = outdir
        self.results = []
        self.step = ''
        self.tag = ''
        self.log = open(os.path.join(outdir, 'run.log'), 'w')

    def say(self, text):
        text = re.sub(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', '[redacted]', str(text))
        line = '%s %s' % (time.strftime('%H:%M:%S'), text)
        print(line, flush=True)
        self.log.write(line + '\n')
        self.log.flush()

    def begin(self, step):
        self.step = (self.tag + ' ' if self.tag else '') + step
        self.say('== ' + self.step)

    def note(self, what, evidence=''):
        self.say('note: %s\n    %s' % (what, str(evidence).replace('\n', '\n    ')))

    def check(self, name, ok, evidence='', expected=None):
        """expected: text citing the documented limitation; a failing check then records XFAIL."""
        evidence = re.sub(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', '[redacted]', str(evidence))
        status = 'PASS' if ok else ('XFAIL' if expected else 'FAIL')
        self.results.append({'step': self.step, 'name': name, 'status': status,
                             'evidence': str(evidence), 'expected': expected})
        self.say('%s  %s\n    %s%s' % (status, name, str(evidence).replace('\n', '\n    '),
                                      '\n    expected: ' + expected if expected and not ok else ''))
        return ok

    def outcome(self, name, status, evidence):
        """An unavailable scenario is distinct from a product assertion failure."""
        if status not in ('PASS', 'FAIL', 'BLOCKED', 'SKIPPED', 'RECOVERED'):
            raise ValueError(status)
        evidence = re.sub(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', '[redacted]', str(evidence))
        self.results.append({'step': self.step, 'name': name, 'status': status,
                             'evidence': evidence, 'expected': None})
        self.say('%s  %s\n    %s' % (status, name, evidence))

    def manual(self, name, why):
        """A check the runner cannot make without pressing a key at a popup that is not ours: MANUAL."""
        self.results.append({'step': self.step, 'name': name, 'status': 'MANUAL', 'evidence': str(why), 'expected': None})
        self.say('MANUAL  %s\n    %s' % (name, str(why).replace('\n', '\n    ')))

    def finish(self):
        with open(os.path.join(self.outdir, 'results.json'), 'w') as f:
            json.dump(self.results, f, indent=2)
        counts = {}
        for r in self.results:
            counts[r['status']] = counts.get(r['status'], 0) + 1
        self.say('summary: %s' % counts)
        self.log.close()
        return counts


# ---------------------------------------------------------------------------------------------
# The scenario
# ---------------------------------------------------------------------------------------------
class Scenario:
    def __init__(self, args, host, rec, row, launcher):
        self.a, self.host, self.rec = args, host, rec
        self.L = launcher
        self.release, self.vm, self.strategy_name, self.sure, self.note = row
        self.dev = Device(self.vm)
        self.launcher = launcher.id
        self.shot_n = 0
        self.insp = None
        self.capture_broken = False
        self.viewer_opened = False
        self.major = 0  # webOS TV release, 5..12
        self.stock_id = None  # what restore/removal/uninstall write as home (stock_home_id), from the baseline

    # -- helpers
    def shot(self, label):
        """The TV picture from `beanjar screenshot` (about 2 s, headless, no viewer needed).  Only if
        that fails: the guest capture (ares-do screenshot; com.webos.service.capture fails on webOS 5
        and 10.x to 12.x, and after the first failure it is not tried again). Each
        fallback used is logged."""
        self.shot_n += 1
        name = '%02d-%s%s.png' % (self.shot_n, (self.L.tag + '-') if self.a.multi else '', label)
        path = os.path.join(self.rec.outdir, name)
        ok, detail = self.host.tv_screenshot(self.vm, path)
        self.rec.say('screenshot %s (beanjar screenshot): %s' % (name, detail[-160:] if detail else ('ok' if ok else 'failed')))
        if ok:
            return path
        self.rec.say('FALLBACK: beanjar screenshot failed for %s' % name)
        if not self.capture_broken:
            rc, out = self.dev.screenshot(path)
            self.rec.say('FALLBACK screenshot %s (guest capture): %s' % (name, 'ok' if rc == 0 else out.strip()))
            if rc != 0:
                self.capture_broken = True
            else:
                return path
        self.rec.say('FALLBACK: no host desktop capture; screenshots unavailable')
        return path

    def use_click(self):
        return (getattr(getattr(self, 'a', None), 'input_mode', 'keys') == 'click'
                and self.host is not None and self.host.click_available())

    def elements(self, label):
        rc, raw = self.host.display_beanjar('elements', self.host.remote_target(self.vm), '--json')
        self.rec.say('%s elements (rc=%s):\n%s' % (label, rc, raw))
        if rc:
            raise RuntimeError('elements failed: ' + raw)
        frame = json.loads(raw)
        if getattr(self.host, '_frame_mapping_logged', False) is not True:
            self.rec.note('screen coordinate mapping', json.dumps({k: frame.get(k) for k in
                          ('width', 'height', 'source_width', 'source_height', 'scale', 'offset_x', 'offset_y')}))
            self.host._frame_mapping_logged = True
        return frame

    def click_element(self, text=None, role=None, region=None, effect=None, label='UI', frame=None, read_after=True):
        frame = frame if frame is not None else self.elements(label)
        row = match_element(frame, text, role, region)
        self.rec.say('input click: %s %r at %s' % (label, text or role, row['centre']))
        rc, out = self.host.remote_click(self.vm, row['centre'], frame)
        if rc:
            raise KeySendError('click failed: ' + out)
        if effect is not None:
            if not self.wait(effect, 20, 1):
                self.click_diagnostics(label)
                raise RuntimeError('click effect not observed: ' + label)
        return self.elements(label + '-after') if read_after else None

    def click_diagnostics(self, label):
        """Evidence only, before 'click effect not observed' is raised: the screen and one ui() read
        (focused element, dialog) after the click.  Never raises and never changes the click."""
        try:
            self.shot(label + '-click-not-observed')
        except Exception as exc:
            self.rec.say('click diagnostics: screenshot failed: %r' % (exc,))
        try:
            ui = self.ui()
            self.rec.say('click diagnostics (%s): ui() = %s' % (label, json.dumps(ui) if ui is not None else
                         'None (inspector read failed: %s)' % (getattr(self.insp, 'last_error', None)
                                                                if self.insp else 'no inspector')))
        except Exception as exc:
            self.rec.say('click diagnostics: ui() failed: %r' % (exc,))

    def alert_action(self, message, target, safe, label, proof, verify, closed, request, before_action=None):
        """Verify every alert click; retry only a proven dismissal without its action."""
        for attempt in range(2):
            if before_action:
                before_action()
            if not self.press_alert(message, target, safe, label, proof):
                return False
            result = verify()
            if result:
                return result
            if not self.use_click() or not closed():
                raise RuntimeError(label + ': alert action was not observed')
            evidence = '%s closed without %s; attempt %s' % (message, target, attempt + 1)
            self.rec.note(label + ': alert click did not act', evidence)
            if attempt:
                raise RuntimeError(label + ': second alert click failed: ' + evidence)
            request()
        return False

    def press_alert(self, message, target, safe, label, service_proof):
        """Prove ownership and screen buttons; keys additionally require two focus proofs."""
        def read():
            raw_service, proven = service_proof()
            self.rec.say('%s service ownership proof:\n%s' % (label, raw_service))
            if not proven:
                raise ValueError('no matching accepted service alert from this request')
            if self.use_click():
                frame = self.elements(label)
                path = 'live beanjar elements'
            else:
                # OCR runs locally for older hosts without pointer input.
                path = self.shot(label + '-focus-proof')
                if not os.path.isfile(path):
                    raise ValueError('screen proof screenshot missing')
                rc, raw = run(['beanjar', 'elements', '--image', path, '--json'], timeout=60)
                self.rec.say('%s elements (rc=%s):\n%s' % (label, rc, raw))
                if rc:
                    raise ValueError('elements command failed')
                frame = json.loads(raw)
            focus, direction = alert_focus(frame, message, target, safe, require_focus=not self.use_click())
            return focus, direction, path, frame
        try:
            focus, direction, _, before = read()
            if self.use_click():
                self.click_element(target, 'button', label=label, frame=before, read_after=False)
                return True
            if focus != safe:
                raise ValueError('initial focus is %s, expected safe default %s' % (focus, safe))
            self.press(direction)
            focus, _, path, after = read()
            if focus != target:
                raise ValueError('focus did not move to %s (now %s)' % (target, focus))
            signal_used = alert_style_swap(before, after, target, safe)
            self.rec.note(label + ': focus appearance swapped', signal_used)
        except KeySendError:
            raise
        except (ValueError, TypeError, KeyError, IndexError, RuntimeError, OSError) as exc:
            if self.use_click():
                raise RuntimeError(label + ': click proof failed: ' + str(exc)) from exc
            self.alert_decline_reason = str(exc)
            self.rec.note(label + ': automatic press declined: ' + str(exc), 'MANUAL fallback')
            return False
        self.press('OK')
        self.rec.note(label + ': automatic ENTER sent on verified ' + target, path)
        return True

    def press(self, *keys):
        result = None
        for k in keys:
            self.rec.say('input keys: ' + str(k))
            key = str(k).upper()
            key = 'ENTER' if key == 'OK' else key
            if self.a.input == 'beanjar':
                sent = key
                location = self.host.remote_target(self.vm)
                result = self.host.remote_press(self.vm, sent)
            elif self.a.input == 'qmp':
                sent = {'DOWN': 'down', 'UP': 'up', 'LEFT': 'left', 'RIGHT': 'right',
                        'ENTER': 'ret', 'HOME': 'meta_l', 'BACK': 'esc',
                        **{str(n): str(n) for n in range(10)}}[key]
                location = self.host.name
                result = self.host.sendkey(self.vm, sent)
            else:
                sent = {'HOME': 'LEFTMETA', 'BACK': 'ESC'}.get(key, key)
                location = self.dev.root
                result = self.dev.key(sent)
            rc, out = result
            if rc:
                error = 'key %s (%s) on %s failed (rc=%s): %s' % (
                    k, sent, location, rc, out.strip())
                self.rec.check('input delivery', False, error)
                raise KeySendError(error)
            sleep(0.8)
        return result

    def ui(self):
        v = self.insp.evaluate(FOCUS_JS) if self.insp else None
        try:
            return json.loads(v) if v else None
        except ValueError:
            return None

    def wait(self, cond, timeout, every=3):
        end = time.time() + seconds(timeout)
        v = None
        while time.time() < end:
            v = cond()
            if v:
                return v
            sleep(every)
        return v

    def probe_keys(self, rows, ui):
        """One probe: a key and its inverse; True when the first moved the list focus (keys reach our page)."""
        cur = rows.index(ui['row']) if ui.get('row') in rows else 0
        k1, k2 = ('DOWN', 'UP') if cur + 1 < len(rows) else ('UP', 'DOWN')
        self.press(k1)
        moved = (self.ui() or {}).get('row')
        self.press(k2)
        return moved != ui.get('row')

    def ensure_key_focus(self, rows):
        """Remote keys must reach Home Chooser.  A system popup (seen on w22o-ponytail-us 10.3.1 right
        after every boot: the OLED "screen restoration" popup of com.webos.app.factorywin, buttons
        Start Now (focused by default) and Cancel; BACK does not dismiss it) takes key focus and
        the list never moves.  The runner does NOT press OK on a system popup (Start Now restarts
        the TV for 5 minutes): it records the evidence (a probe key, the oledepl condition, a
        screenshot) and waits up to --wait-popup seconds for someone to dismiss it by hand, then
        fails the run."""
        r = self.rec
        end = time.time() + seconds(self.a.wait_popup)
        first = True
        while True:
            ui = self.ui()
            if ui is None:
                raise RuntimeError('our page is not readable; not pressing a probe key')
            if self.probe_keys(rows, ui):
                if not first:
                    r.say('key focus restored: the popup was dismissed')
                return True
            known = self.dismiss_known_popup('keys blocked', rows) if first else None
            if known is True:
                r.say('key focus restored: the known LG crash popup was dismissed')
                return True
            if known is False:
                raise RuntimeError('keys still blocked after dismissing the known LG crash popup; no more keys')
            if first and self.dismiss_audiooutputd_popup('keys blocked'):
                if self.probe_keys(rows, self.ui() or ui):  # retry the blocked step once
                    r.say('key focus restored: the audiooutputd crash dialog was dismissed with NO (%s)' % AUDIOOUTPUTD_REF)
                    return True
                r.check('remote keys reach Home Chooser after dismissing the audiooutputd crash dialog', False,
                        'focus did not move after a probe key; %s' % AUDIOOUTPUTD_REF)
                raise RuntimeError('keys still blocked after dismissing the audiooutputd crash dialog; no more keys')
            if first:
                fg = self.dev.foreground()
                epl = self.dev.luna('luna://com.webos.service.oledepl/isPixelRefresherCondition')
                self.shot('keys-blocked')
                r.say('keys do not reach Home Chooser (focus stayed on %s); foreground %s; oledepl '
                      'isPixelRefresherCondition %s; a system popup probably holds key focus (see the '
                      'keys-blocked screenshot); dismiss it by hand with Cancel, waiting up to %ds'
                      % (ui.get('row'), fg[1], epl[:160], self.a.wait_popup))
                first = False
            if time.time() >= end:
                break
            sleep(10)
        r.check('remote keys reach Home Chooser (no system popup holding key focus)', False,
                'focus did not move after a probe key; see the keys-blocked screenshot')
        raise RuntimeError('keys blocked: a system popup probably holds key focus; not pressing OK on it')

    def watcher_active(self):
        rc, out = self.dev.sh('systemctl is-active homechooser-home')
        return out.strip()

    def state(self):
        """A snapshot of everything Home Chooser writes."""
        d = self.dev
        s = {}
        rc, s['choice'] = d.sh('cat %s 2>&1' % CHOICE)
        s['choice'] = s['choice'].strip()
        s['record_home'], s['record_raw'] = d.record_home()
        s['watcher'] = self.watcher_active()
        rc, s['initd'] = d.sh('readlink %s 2>&1 || ls -la %s 2>&1' % (INITD, INITD))
        s['initd'] = s['initd'].strip()
        rc, s['flag'] = d.sh('test -e %s && echo yes || echo no' % FLAG)
        s['flag'] = s['flag'].strip()
        rc, s['override'] = d.sh('test -e %s && echo yes || echo no' % OVERRIDE)
        s['override'] = s['override'].strip()
        s['mounts'] = d.mounts()
        return s

    def uses_home_key_hook(self):
        return uses_home_key_hook(self.major)

    def uses_override(self):
        return self.major >= 10

    def hook_mounts_match(self, mounts=None):
        st, _ = self.kf_status()
        return st.get('active') == 'yes' and not self.dev.mounts()

    def check_hook_status(self, label):
        rc, out = self.dev.exec(SCRIPTS_DIR + '/homechooser-homekey status')
        self.rec.check('%s: live JS hook is selected' % label,
                       rc == 0 and 'active=yes' in out and 'patch=possible' in out, out.strip())

    def kf_status(self):
        """(parsed, raw) of `homechooser-homekey status` (6.x to 9.x)."""
        rc, out = self.dev.exec(SCRIPTS_DIR + '/homechooser-homekey status')
        return parse_status(out), out.strip()

    # -- nothing restarts while the TV runs (6.x to 9.x; 144ef21)
    HOMEKEY_LOG = '/tmp/homechooser-homekey.log'
    COMPOSITOR = 'surface-manager-daemon'

    def compositor_snapshot(self):
        d = self.dev
        u = d.unit(self.COMPOSITOR)
        n = d.sh("grep -c 'restarting %s' %s 2>/dev/null || true" % (self.COMPOSITOR, self.HOMEKEY_LOG))[1].strip()
        return {'boot': d.boot_id(), 'pid': u.get('MainPID'), 'restarts': u.get('NRestarts'),
                'log_restarts': n.splitlines()[-1] if n else '0', 'wam': d.unit('webapp-mgr'), 'mark': self.messages_mark()}

    def check_no_compositor_restart(self, label, before):
        """The compositor was not restarted since `before` (same boot): same MainPID and NRestarts and no new
        `restarting surface-manager-daemon` line in the Home key hook log (a /tmp file: it starts empty each boot)."""
        if not self.uses_home_key_hook() or not before:
            return
        now = self.compositor_snapshot()
        if now['boot'] != before['boot']:
            self.rec.note(label + ': a reboot came in between; the compositor check does not apply', '')
            return
        ok = (now['pid'] == before['pid'] and now['restarts'] == before['restarts']
              and now['log_restarts'] == before['log_restarts'])
        self.rec.check(label + ': no compositor restart (surface-manager-daemon MainPID/NRestarts unchanged, '
                       'no new "restarting %s" line in %s)' % (self.COMPOSITOR, self.HOMEKEY_LOG), ok,
                       'before pid=%s NRestarts=%s log=%s; after pid=%s NRestarts=%s log=%s' % (
                           before['pid'], before['restarts'], before['log_restarts'],
                           now['pid'], now['restarts'], now['log_restarts']))

    def check_no_crash(self, label, before):
        """6.x to 9.x: no crashd CRASHED / WebAppMgr crash line since `before`, and within one boot WebAppMgr
        kept its MainPID and NRestarts.  Boot apply must keep existing GUI processes alive."""
        if not self.uses_home_key_hook() or not before:
            return
        d = self.dev
        rc, out = d.sh((messages_command() + " | awk '$1 >= \"%s\"' | grep -iE %s | tail -n 20")
                       % (before['mark'], shlex.quote(CRASH_RE.pattern)))
        bad = crash_lines(out)
        if bad:
            known, bad = split_known_crashes(bad, self.priv_luna('luna://com.webos.service.faultmanager/getFaultDetectedStatus'))
            if known:
                self.rec.note('%s: known LG crash (legacy-broadcast-dvb, API_SI_GetLinkageUriInfo+0x197), accepted limitation, '
                              'not a FAIL; see %s' % (label, KNOWN_CRASH_DOC), '\n'.join(known[-3:]))
        self.rec.check(label + ': no crashd CRASHED or WebAppMgr crash line in /var/log/messages since %s' % before['mark'],
                       not bad, '\n'.join(bad[-6:]) or 'none')
        if d.boot_id() == before['boot']:
            wam = d.unit('webapp-mgr')
            self.rec.check(label + ': WebAppMgr not restarted (webapp-mgr MainPID/NRestarts unchanged)',
                           wam.get('MainPID') == before['wam'].get('MainPID')
                           and wam.get('NRestarts') == before['wam'].get('NRestarts'),
                           'before %s; after %s' % (before['wam'], wam))

    # -- reboots
    def await_boot(self, label, boot0):
        """After a reboot (any route): LAN address, device entries, Homebrew Channel, a new boot id."""
        r, host = self.rec, self.host
        end = time.time() + seconds(120)
        while time.time() < end and (host.info(self.vm).get('lan_address') in (None, '', '-')):
            sleep(3)
        ensure_devices(host, self.vm, r)
        state = wait_for_device(self.vm, r)
        up = state == READY
        r.check(label + ': the VM is back with Homebrew Channel', up, '%s (%s)' % (state, WAIT_STATES[state]))
        if up:
            host.vnc_start(self.vm)
            b = self.dev.boot_id()
            r.check(label + ': it is a new boot (boot_id changed)', bool(b) and b != boot0, '%s -> %s' % (boot0, b))
        return up

    def beanjar_reboot(self, label):
        boot0 = self.dev.boot_id()
        rc, out = self.host.stop(self.vm)
        self.rec.say('beanjar stop: %s' % out.strip()[-200:])
        rc, out = self.host.start(self.vm)
        self.rec.say('beanjar start: %s' % out.strip()[-200:])
        return self.await_boot(label, boot0)

    def wait_reboot(self, boot0, timeout=240):
        """Wait for the TV to restart by itself (tvpower): 'rebooted' when a new boot id is read, 'off' when
        Beanjar shows the VM off (QEMU may power off on a guest reboot),
        'unreachable' when the guest stops responding, or '' when nothing happened."""
        end = time.time() + seconds(timeout)
        gone = False
        last_boot = boot0
        while time.time() < end:
            sleep(10)
            try:
                if self.vm not in self.host.running_vms():
                    return 'off'
            except RuntimeError:
                pass
            b = self.dev.boot_id()
            last_boot = b
            if not b:
                gone = True
                continue
            if b != boot0:
                return 'rebooted'
            if gone:  # answering again with the old id: the address may have moved; fix the entries
                try:
                    ensure_devices(self.host, self.vm, self.rec)
                except SystemExit:
                    pass
        return 'unreachable' if not last_boot else ''

    def stock_record_ok(self, home):
        return stock_record_ok(self.major, home, self.stock_id)

    def residency(self, point):
        """Observation only: are the previous (stock) and the new home app running, pids and RSS."""
        d = self.dev
        out = d.luna('luna://com.webos.applicationManager/running')
        lines = []
        try:
            running = {a['id']: a for a in json.loads(out)['running']}
        except (ValueError, KeyError, TypeError):
            running = None
        if running is None:
            lines.append('running: unreadable: ' + out[:200])
        else:
            homes = [('previous home', STOCK)] + ([('5.x fullhome', 'com.webos.app.fullhome')] if self.major == 5 else [])
            for label, aid in homes + [('chosen launcher', self.launcher)]:
                a = running.get(aid)
                if not a:
                    lines.append('%-15s %-28s not running' % (label, aid))
                    continue
                pid = a.get('processid') or ''
                rss = ''
                if pid.isdigit():
                    rss = d.sh("grep VmRSS /proc/%s/status 2>&1" % pid)[1].strip()
                lines.append('%-15s %-28s running pid=%s webprocessid=%s %s'
                             % (label, aid, pid, a.get('webprocessid'), rss))
            lines.append('all running: ' + ', '.join('%s(%s)' % (k, v.get('processid')) for k, v in running.items()))
        self.rec.say('home-app residency [%s]\n    %s' % (point, '\n    '.join(lines)))

    def baked_id(self):
        raw = self.priv_luna('luna://com.webos.service.config/getConfigs',
                             '{"configNames":["com.webos.surfacemanager.keyFilters"]}')
        try:
            filters = json.loads(raw)['configs']['com.webos.surfacemanager.keyFilters']
        except (ValueError, KeyError, TypeError):
            return ''
        paths = {f.get('file', '') for f in filters if isinstance(f, dict)}
        generated = sorted(p for p in paths if p.startswith('/var/lib/webosbrew/homechooser/systemUi-'))
        return '\n'.join(self.dev.sh('grep -E '+shlex.quote('HOME_APP_ID|applicationManager\\.(launch|checkLaunch)')+' '+shlex.quote(p))[1] for p in generated)

    def assert_stock(self, label, s, baked_emptied=True):
        """The platform is back to stock.  6.x to 9.x: the record names the stock id (not null) and the
        patch stays mounted until the next boot (nothing unmounts or restarts while the TV runs);
        baked_emptied: the watcher ran `refresh stock` (a launcher removal; not after an uninstall,
        where the worker went with the app directory).  10+: null/unset and no override."""
        r = self.rec
        want = {5: 'a stock launcher (%s or %s)' % (FULLHOME, STOCK)}.get(self.major) or (
            'the stock id %s, not null' % self.stock_id if self.uses_home_key_hook() else 'null or unset')
        r.check(label + ': record home is %s' % want, self.stock_record_ok(s['record_home']), s['record_raw'])
        if self.uses_override():
            r.check(label + ': profile override absent', s['override'] == 'no', 'override exists: ' + s['override'])
        if self.uses_home_key_hook():
            raw = self.priv_luna('luna://com.webos.service.config/getConfigs',
                                 '{"configNames":["com.webos.surfacemanager.keyFilters"]}')
            r.check(label + ': stock keyFilters selected with no QML mounts',
                    not s['mounts'] and 'systemUi-' not in raw and '/KeyFilters/systemUi.js' in raw, raw)

    # -- the removal notifications (docs/settings-app.md "When apps are removed")
    def notif_lines(self, method):
        """The watcher log's `notification <method>:` lines (call replies), oldest first."""
        rc, out = self.dev.sh("grep 'notification %s:' /tmp/homechooser-home.log 2>&1" % method)
        return [l for l in out.splitlines() if 'notification %s:' % method in l]

    def priv_luna(self, uri, payload='{}'):
        """A privileged luna call as root, always with the identity the watcher uses (-a com.webos.applicationManager)."""
        # exec, not sh: luna-send under ares-shell -r exits with no output
        rc, out = self.dev.exec("luna-send -a com.webos.applicationManager -n 1 %s '%s' 2>&1" % (uri, payload))
        return out.strip()

    def popup_mark_now(self):
        """The mark foreign_popup reads from: (messages time, seconds since boot), taken when a step begins."""
        rc, out = self.dev.sh('cut -d" " -f1 /proc/uptime')
        try:
            up = float(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            up = None
        return self.messages_mark(), up

    def dismiss_known_popup(self, label, rows=None):
        """The "[crash] Fault Occured" popup of the known legacy-broadcast-dvb crash (YES and NO do the same
        there) may be dismissed with BACK, at most once per fault (keyed by pid and faultTime).  Only a fault
        entry that is that known crash qualifies.  getFaultDetectedStatus lists every fault of the boot, so it
        cannot show the popup is gone; the outcome checked is that key focus is back: with our page open
        (`rows`, or a readable page) a probe key moves the list focus; without one, the foreground app is
        still the one from before BACK.  Returns None when no unhandled known fault is pending, else
        whether focus is back.  The caller sends no further key when it is False."""
        done = self.__dict__.setdefault('dismissed_known', set())
        uri = 'luna://com.webos.service.faultmanager/getFaultDetectedStatus'
        pending = {(p, str(f.get('faultTime'))) for p, f in known_fault_pids(self.priv_luna(uri)).items()}
        pending -= done
        if not pending:
            return None
        self.rec.note('%s: known LG crash popup (see %s); dismissing it with BACK, once' % (label, KNOWN_CRASH_DOC),
                      'pid/faultTime %s' % ', '.join('%s/%s' % p for p in sorted(pending)))
        done |= pending
        fg0 = self.dev.foreground()[0]
        self.press('BACK')
        sleep(2)
        ui = self.ui() if self.insp else None
        if ui and ui.get('row') and (rows or ui.get('rows')):
            ok = self.probe_keys(rows or ui['rows'], ui)
            how = 'a probe key moved the list focus' if ok else 'a probe key did not move the list focus'
        else:
            fg = self.dev.foreground()[0]
            ok = bool(fg0) and fg == fg0
            how = 'foreground %s before BACK, %s after' % (fg0, fg)
        self.rec.check('%s: key focus is back after dismissing the known LG crash popup' % label, ok, how)
        return ok

    def dismiss_audiooutputd_popup(self, label):
        """Keys are blocked: read the screen (beanjar elements OCR).  Only the LG "[crash] Fault Occured" dialog
        that names audiooutputd (known on W22O 33.31.61/68, beanjar#183) is dismissed, by NO: a click on the
        element "NO", or in keys mode a move of the focus to NO verified on screen before OK.  YES is never
        pressed.  Returns True when the dialog was dismissed and the screen no longer shows it, None when the
        screen shows anything else (nothing is pressed; the caller stops as before), and raises when the dialog
        could not be dismissed safely."""
        r = self.rec
        try:
            frame = self.elements(label + ' (system popup?)')
        except Exception as exc:
            r.say('%s: screen not readable, no popup handling: %r' % (label, exc))
            return None
        message, text = audiooutputd_dialog(frame)
        if message is None:
            if '[crash] Fault Occured' in text and 'audiooutputd' in text:
                raise RuntimeError('audiooutputd crash dialog text is not a single element; not pressing keys')
            return None
        before = self.shot('audiooutputd-crash-dialog-before')
        r.note('%s: LG audiooutputd crash dialog (%s, stock LG behaviour); dismissing it with NO' % (label, AUDIOOUTPUTD_REF),
               'OCR text: %s' % text)
        if self.use_click():
            self.click_element('NO', 'button', label=label + ' NO', frame=frame, read_after=False)
        else:
            try:
                focus, direction = alert_focus(frame, message, 'NO', 'YES', require_focus=True)
            except ValueError as exc:
                raise RuntimeError('audiooutputd crash dialog: focus not provable (%s); not pressing keys' % exc) from exc
            if focus != 'NO':
                self.press(direction)
                frame = self.elements(label + ' focus on NO')
                if audiooutputd_dialog(frame)[0] is None:
                    raise RuntimeError('audiooutputd crash dialog not readable after moving focus; not pressing OK')
                try:
                    focus, _ = alert_focus(frame, message, 'NO', 'YES', require_focus=True)
                except ValueError as exc:
                    raise RuntimeError('audiooutputd crash dialog: focus not provable (%s); not pressing OK' % exc) from exc
                if focus != 'NO':
                    raise RuntimeError('focus did not move to NO (now %s); not pressing OK' % focus)
            self.press('OK')
        sleep(2)
        after = self.shot('audiooutputd-crash-dialog-after')
        gone = self.elements(label + ' after NO')
        still, after_text = audiooutputd_dialog(gone)
        if still is not None or '[crash] Fault Occured' in after_text:
            r.check('audiooutputd crash dialog is gone after NO', False, 'OCR text after: %s' % after_text)
            raise RuntimeError('audiooutputd crash dialog still on screen after NO; no more keys')
        r.outcome('audiooutputd crash dialog dismissed with NO, %s' % AUDIOOUTPUTD_REF, 'RECOVERED',
                  json.dumps({'ocr_before': text, 'ocr_after': after_text, 'screenshot_before': before,
                              'screenshot_after': after, 'reference': AUDIOOUTPUTD_REF}))
        return True

    def foreign_popup(self, label, dismiss=False):
        """Note (never press keys at) an LG system popup that may hold key focus.  No screen reading:
        faultmanager (9.2.1 rootfs: /usr/sbin/faultmanager) raises the "[crash] Fault Occured" popup through
        notificationmgr and answers luna://com.webos.service.faultmanager/getFaultDetectedStatus with
        detectedFaults; its crash lines (crashd CRASHED, faultmanager createNotiPopup) are in /var/log/messages.
        notificationmgr has no list-alerts method.  Only what appeared since the step's mark (popup_mark,
        set when the step began) counts: a crashd/faultmanager popup line since then, or a fault entry raised
        since then that is not keyCheck2 boot noise (classify_faults).  Noise is noted.  Returns the evidence
        of a real popup, or ''."""
        mark = getattr(self, 'popup_mark', None)
        if not mark:
            mark = self.popup_mark = self.popup_mark_now()
        t_mark, up_mark = mark
        reply = self.priv_luna('luna://com.webos.service.faultmanager/getFaultDetectedStatus')
        real, noise = classify_faults(reply, up_mark)
        try:
            all_faults = json.loads(reply).get('faultDetectedStatus', {}).get('detectedFaults', [])
        except (ValueError, TypeError, AttributeError):
            all_faults = real
        crashes = [f for f in all_faults if f.get('faultType') == 'crash']
        expected = [f for f in crashes if audiooutputd_fault(f)]
        crashes = [f for f in crashes if not audiooutputd_fault(f)]
        if expected:
            self.rec.note('%s: audiooutputd crash is an expected environment fault (%s, W22O 33.31.61/68), not counted'
                          % (label, AUDIOOUTPUTD_REF), ' | '.join(json.dumps(f) for f in expected))
        self.rec.check(label + ': no LG [crash] fault dialog or crash fault this boot', not crashes,
                       ' | '.join(json.dumps(f) for f in crashes) or 'none')
        real = [f for f in real if not audiooutputd_fault(f)]
        ev = ['getFaultDetectedStatus new fault: %s' % json.dumps(f) for f in real]
        rc, out = self.dev.sh((messages_command() + " | awk '$1 >= \"%s\"' | grep -E '%s' | tail -n 4") % (t_mark, POPUP_LINE_RE))
        lines = [l for l in out.splitlines() if l.strip()]
        known_lines = split_known_crashes(lines, reply)[0]
        ev += [l for l in lines if l not in known_lines and not AUDIOOUTPUTD_CRASHD_RE.search(l)]
        if noise:
            self.rec.note('%s: fault entries not counted (keyCheck2 boot noise or raised before the step, uptime at mark %s)'
                          % (label, up_mark), ' | '.join(json.dumps(f) for f in noise))
        if ev:
            self.rec.note('%s: foreign popup present: %s' % (label, ' | '.join(ev)),
                          'an LG system popup may hold key focus; no key is pressed at it')
            self.shot(label + '-foreign-popup')
        return ' | '.join(ev)

    def notif_wait(self, method, before, timeout, every=3):
        """Wait until the log has more `notification <method>:` lines than `before`."""
        return self.wait(lambda: len(self.notif_lines(method)) > before, timeout, every)

    def check_alert(self, before):
        """After the chosen launcher was removed: the watcher logged one createAlert call and its reply.
        A reply that is not returnValue true is a FAIL with the fallback recorded, never a silent pass.
        With an accepted alert: screenshot, close it with notification/closeAlert (no key press),
        screenshot again."""
        r, d = self.rec, self.dev
        got = self.notif_wait('createAlert', before, 90)
        # a refusal is retried by the watcher (up to 8 tries, 3 s apart): wait for an accepted reply
        self.wait(lambda: any('"returnValue":true' in l.replace(' ', '') for l in self.notif_lines('createAlert')[before:]), 45, 3)
        lines = self.notif_lines('createAlert')
        new = lines[before:]
        ok_ix = [i for i, l in enumerate(new) if '"returnValue":true' in l.replace(' ', '')]
        r.check('watcher sent a createAlert for the removal (refusals retried, at most one accepted, last)',
                bool(new) and len(new) <= 8 and (not ok_ix or ok_ix == [len(new) - 1]), '\n'.join(new) or 'no createAlert line in the log')
        if not new:
            return
        reply = new[-1]
        accepted = '"returnValue":true' in reply.replace(' ', '')
        if not accepted:
            # A refused alert is shown as a toast instead (decided: the toast is enough). 5.6.1 and 9.2.1
            # answered "Alert is blocked by [ system ]" on every try; 10.3.1 accepted the alert.
            refused = 'blocked' in reply or '"returnValue":false' in reply.replace(' ', '')
            # the fallback toast is retried too: wait for the latest createToast reply to be accepted
            self.wait(lambda: 'returnValue":true' in d.sh(
                "grep 'notification createToast:' /tmp/homechooser-home.log | tail -n 1")[1].replace(' ', ''), 30, 3)
            rc, fb = d.sh("grep -E 'alert was refused|notification createToast:' /tmp/homechooser-home.log | tail -n 3")
            toast_ok = refused and any('alert was refused' in l for l in fb.splitlines()) and \
                '"returnValue":true' in fb.strip().splitlines()[-1].replace(' ', '')
            self.shot('4-alert-refused')
            r.check('the user was told: the alert was refused, so the fallback toast was shown', toast_ok,
                    'alert reply: %s\nfallback:\n%s' % (reply, fb.strip()))
            return
        r.check('the user was told: the alert was accepted (watcher sends with -a com.webos.applicationManager)', True, 'reply: %s' % reply)
        sleep(2)
        self.foreign_popup('4-alert')
        self.shot('4-alert')
        r.note('look at screenshot 4-alert: an alert "Home Chooser" with the message "%s" and one OK button '
               '(no restart is mentioned on any release)' % (REMOVAL_ALERT % self.L.title), '')
        fg0 = d.foreground()
        m = re.search(r'"alertId"\s*:\s*"([^"]+)"', reply)
        if m:
            # closed over luna, no key press: an LG popup may be on top and would take the key
            out = self.priv_luna('luna://com.webos.notification/closeAlert', '{"alertId":"%s"}' % m.group(1))
            r.check('closeAlert on our alert (-a com.webos.applicationManager) was accepted',
                    '"returnValue":true' in out.replace(' ', ''), 'alertId %s\nreply: %s' % (m.group(1), out))
        else:
            r.note('accepted createAlert reply carries no alertId: alert not closed', reply)
        sleep(3)
        self.shot('4-alert-dismissed')
        r.note('alert closed by closeAlert; foreground before/after (the alert itself is not an app)',
               '%s -> %s' % (fg0[0], d.foreground()[0]))

    def check_toast(self, before):
        """After Home Chooser was removed: the watcher logged one createToast call and its reply; a
        screenshot is taken as soon as the call shows in the log (a toast is short-lived)."""
        r = self.rec
        self.notif_wait('createToast', before, 150, 1)
        self.shot('5-toast')
        self.wait(lambda: any('"returnValue":true' in l.replace(' ', '') for l in self.notif_lines('createToast')[before:]), 30, 3)
        lines = self.notif_lines('createToast')
        new = lines[before:]
        ok_ix = [i for i, l in enumerate(new) if '"returnValue":true' in l.replace(' ', '')]
        r.check('watcher sent a createToast for the uninstall (refusals retried, at most one accepted, last)',
                bool(new) and len(new) <= 8 and (not ok_ix or ok_ix == [len(new) - 1]), '\n'.join(new) or 'no createToast line in the log')
        if new:
            r.check('the notification service accepted the toast', '"returnValue":true' in new[-1].replace(' ', ''), new[-1])
        r.note('look at screenshot 5-toast: a toast "%s" (it may already be gone: the log reply is the evidence)'
               % UNINSTALL_TOAST, '')

    # -- the stock home's preload policy
    def preload_log(self):
        return self.dev.sh("grep -E 'setPreloadPolicy|closeByAppId|%s' /tmp/homechooser-home.log 2>&1 | tail -n 6" % SKIP_LINE)[1].strip()

    def stock_left_running(self):
        """10.x, any launcher: the watcher leaves the stock home running (8791f6e).  6.x to 9.x use the
        home gate instead (check_home_gate)."""
        return self.major == 10

    def gate_mount_count(self):
        rc, out = self.dev.sh("awk '$2 == \"%s\" {n++} END {print n + 0}' /proc/mounts" % GATE_TARGET)
        try:
            return int(out.split()[-1])
        except (ValueError, IndexError):
            return 0

    def check_home_gate(self, label):
        """6.x to 9.x, launcher chosen: the gate is bound over stock Home's binary, the watcher logged the
        mount, and stock Home is not running.  A Home the boot started before the mount is closed by the
        watcher's retry (every 10 s, up to 12 times), so the wait for it is bounded."""
        r = self.rec
        # The watcher trims its log to the last 200 lines once it passes 64 KB, so the mount line can
        # leave the file while the run goes on (the file is read whole; it is the file that lost the
        # line). Look as early as possible and remember the first sighting.
        seen = []

        def mount_line_seen():
            lines = [l for l in self.log_lines() if GATE_LINE in l]
            if lines:
                seen[:] = lines[-1:]
            return bool(seen)
        mount_line_seen()
        count = self.gate_mount_count()
        r.check(label + ': the home gate is mounted over ' + GATE_TARGET, count >= 1,
                '%d mount(s) of %s in /proc/mounts' % (count, GATE_TARGET))
        gone = self.wait(lambda: STOCK not in self.running_ids(), 150, 5)
        r.check(label + ': stock Home (the real binary) is not running', bool(gone), repr(self.running_ids()))
        self.wait(mount_line_seen, 60, 5)
        mounted = list(seen)
        detail = mounted[-1] if mounted else 'no line containing %r' % GATE_LINE
        if not mounted:
            lines = self.log_lines()
            detail += '; log now %d lines, first: %s' % (len(lines), lines[0] if lines else 'empty')
        r.check(label + ': watcher log has the %r line' % GATE_LINE, bool(mounted), detail)
        r.note(label + ': home gate lines in the watcher log',
               '\n'.join(l for l in self.log_lines() if 'home gate' in l)[-1500:])

    def mark_log(self):
        """Remember how long the watcher log is now: /tmp/homechooser-home.log accumulates across the
        fixtures of one boot, so a check about one switch must only read the lines after this mark."""
        self.log_mark = len(self.log_lines())
        return self.log_mark

    def log_since_mark(self):
        return lines_after(self.log_lines(), getattr(self, 'log_mark', 0))

    def check_stock_left_running(self, label, samples):
        """6.x to 10.x, any launcher: the watcher logged the skip and never called closeByAppId for the
        stock home (only the log lines since the mark, i.e. since this switch or boot).  Whether the stock
        home runs is recorded, not asserted."""
        r = self.rec
        self.wait(lambda: any(SKIP_LINE in l for l in self.log_since_mark()), 120, 5)
        log = self.log_since_mark()
        skips = [l for l in log if SKIP_LINE in l]
        closes = [l for l in log if 'closeByAppId ' + STOCK in l]
        r.note(label + ': watcher skip lines', '\n'.join(skips))
        r.check(label + ': watcher log has the skip line (webOS %d: stock home left running)' % self.major,
                bool(skips), skips[-1] if skips else 'no line containing %r' % SKIP_LINE)
        r.check(label + ': watcher log has no closeByAppId ' + STOCK, not closes, '\n'.join(closes) or 'none')
        running = bool(samples) and samples[-1][1]
        detail = 'running at t=%s s of %d samples' % ([t for t, run_ in samples if run_], len(samples))
        # "Left alone" means the watcher did not close it (the two checks above). Whether the stock home
        # is running then is up to the platform: with the preload policy off nothing may start it again
        # (6.5.2 after the switch, 10.x after a reboot), which only saves memory. Recorded, not asserted.
        r.note(label + ': %s running=%s (launcher %s, defaultWindowType=%s; left alone either way)'
               % (STOCK, running, self.launcher, self.L.appinfo.get('defaultWindowType')), detail)
        if label.startswith('after the reboot'):
            # Open risk (orchestrator, 2026-10-24): snapshot-boot-components may launch the stock home at
            # boot outside the preload manager, so with no close it could stay resident after the reboot
            # that applies the patch.  Who launched it this boot is recorded.
            r.note('%s: stock home %s after the reboot (open risk: a boot-time launch outside the preload '
                   'manager would keep it resident; recorded, not asserted)'
                   % (label, 'RUNNING' if running else 'not running'),
                   self.dev.sh(messages_command() + " | grep -E 'NL_APP_LAUNCH_BEGIN|APP_LAUNCHED|snapshot' | "
                               "grep 'com.webos.app.home' | head -n 8")[1].strip() or 'no launch line for the stock home')

    def stock_running_samples(self, seconds=PRELOAD_SAMPLE, every=5):
        """Sample `running` for the stock home: list of (seconds, running)."""
        t0, out = time.time(), []
        while True:
            out.append((int(time.time() - t0), STOCK in self.running_ids()))
            if time.time() - t0 + every * SLOWDOWN > seconds * SLOWDOWN:
                break
            sleep(every)
        return out

    def check_preload_off(self, label, settle=PRELOAD_SETTLE):
        """A third-party home is chosen: the watcher disabled the stock home's preload. On 11.x and 12.x
        a close waits for a safe launcher foreground. On 6.x to 10.x it is left alone (check_stock_left_running). 5.x: the watcher leaves the policy alone (settings-app.md), so it must not
        read false; the stock home is only observed (com.webos.app.home is not running on 5.x)."""
        r = self.rec
        enabled, raw = self.dev.preload_policy()
        if self.major <= 5:
            r.check(label + ': 5.x, the stock home policy is left alone (not disabled)', enabled is not False,
                    'isEnabled=%r; %s' % (enabled, raw))
            r.note(label + ': watcher log (5.x: no setPreloadPolicy expected)', self.preload_log())
            return
        ok = self.wait(lambda: self.dev.preload_policy()[0] is False, 60, 5)
        enabled, raw = self.dev.preload_policy()
        r.check(label + ': getPreloadPolicy shows isEnabled:false for ' + STOCK, enabled is False,
                'isEnabled=%r; %s' % (enabled, raw))
        r.note(label + ': watcher log', self.preload_log())
        if self.major >= 11 and (label == 'after the switch' or self.L.overlay):
            r.note(label + ': stock Home may still run until the launcher has a safe foreground',
                   'stock running=%s; foreground=%s' % (STOCK in self.running_ids(), self.dev.foreground()[0]))
            return
        r.say('%s: settling %d s, then sampling %s for %d s' % (label, settle, STOCK, PRELOAD_SAMPLE))
        sleep(settle)
        samples = self.stock_running_samples()
        if uses_home_gate(self.major):
            self.check_home_gate(label)
            return
        if self.stock_left_running():
            self.check_stock_left_running(label, samples)
            return
        ok = not any(run_ for _, run_ in samples)
        r.check('%s: %s is not running over %d s' % (label, STOCK, PRELOAD_SAMPLE), ok,
                'running at t=%s s' % [t for t, run_ in samples if run_] if not ok else 'never running in %d samples' % len(samples))
        if not ok:
            self.residency(label + ': stock home came back')
            r.note(label + ': whole watcher log', self.dev.sh('cat /tmp/homechooser-home.log 2>&1 | tail -n 40')[1])
            r.note(label + ': sam lines about the stock home', self.dev.sh(
                messages_command() + " | grep -E 'com.webos.app.home' | grep -E 'close|Close|launch|Launch|NL_APP' | tail -n 15")[1])

    def check_preload_on(self, label, wait_for=120):
        """The policy is back to enabled (launcher uninstalled, choice stock, Home Chooser uninstalled)."""
        r = self.rec
        if self.major <= 5:
            enabled, raw = self.dev.preload_policy()
            r.check(label + ': 5.x, the stock home policy was never touched', enabled is not False,
                    'isEnabled=%r; %s' % (enabled, raw))
            return
        self.wait(lambda: self.dev.preload_policy()[0] is True, wait_for, 5)
        enabled, raw = self.dev.preload_policy()
        r.check(label + ': getPreloadPolicy shows isEnabled:true for ' + STOCK, enabled is True,
                'isEnabled=%r; %s' % (enabled, raw))
        r.note(label + ': watcher log', self.preload_log())

    def front_app(self, appid='org.webosbrew.hbchannel'):
        """An ordinary app in front, as docs T4 presses Home from.  (With the stock home in front
        the key is passed to the home view and nothing is launched: see step 3.)"""
        self.dev.launch(appid)
        fg = self.wait(lambda: (lambda x: x if x[0] == appid else None)(self.dev.foreground()), 30, 2)
        self.rec.say('front app %s: %s' % (appid, fg))
        sleep(3)

    def running_ids(self):
        """Ids in applicationManager/running whose process is alive.  On 12.x sam keeps a ghost entry
        (pid gone from /proc) for a force-closed flutter app, so an entry with a dead pid is dropped."""
        try:
            rows = json.loads(self.dev.luna('luna://com.webos.applicationManager/running'))['running']
        except (ValueError, KeyError, TypeError):
            return []
        pids = [x.get('processid') for x in rows if x.get('processid')]
        alive = set(pids)
        if pids:
            rc, out = self.dev.sh('for p in %s; do test -d /proc/$p && echo $p; done' % ' '.join(
                p for p in pids if str(p).isdigit()))
            alive = set(out.split())
        return [x['id'] for x in rows if not x.get('processid') or x['processid'] in alive]

    def home_press(self, expect, label, from_app=True):
        """Press Home from an ordinary app.  The stock home must be in the foreground afterwards;
        a launcher counts when it is running (it was closed first) or in the foreground: a
        launcher bar such as Dock is an overlay window, so the app underneath stays "foreground"."""
        r = self.rec
        proof = expect == self.launcher
        if proof:
            self.popup_mark = self.popup_mark_now()
            self.life_begin()
        if expect == STOCK and (self.major == 5 or self.stock_id == FULLHOME):
            # webOS 5: Home starts com.webos.app.fullhome as an overlay over the app in front
            # (foreground stays the app; seen in the w20l-jhericurl run), so it is observed like
            # a launcher: closed first, running afterwards.  6.x to 9.x with fullhome installed: the
            # record names it, so it is what opens (not seen on a device yet).
            expect = FULLHOME
        if expect != STOCK:
            self.dev.close(expect)
            sleep(3)
            pre = expect in self.running_ids()
            r.say('%s: %s running before the press: %s%s' % (label, expect, pre,
                  ' (close did not stop it: this check does not discriminate)' if pre else ''))
        if from_app:
            self.front_app()
        if proof or self.uses_home_key_hook():
            popup = self.foreign_popup(label, dismiss=True)
            if popup:
                r.check('%s: no foreign LG fault popup before the Home-key proof' % label, False, popup)
                raise RuntimeError('foreign LG fault popup detected; not sending keys')
        mark = self.messages_mark()
        key_result = self.press('HOME')
        r.say('%s: Home key-send result: %r; log mark %s' % (label, key_result, mark))
        sleep(6)
        sam = []

        def seen():
            fg = self.dev.foreground()
            if fg[0] == expect or (expect != STOCK and expect in self.running_ids()):
                return fg
            if expect == STOCK:
                # getForegroundAppInfo keeps naming the app beneath on 12.x although the stock home
                # is on screen: sam's own record of the Home key is the signal there.
                sam[:] = self.sam_home_lines(mark)
                if sam:
                    return fg
            return None
        fg = self.wait(seen, 20, 3)
        if not fg:
            rc, evidence = self.dev.sh(home_input_command(mark))
            r.note(label + ': first Home attempt produced no action; no retry',
                   'key-send %r; messages/journal since %s (rc=%s):\n%s' % (key_result, mark, rc, evidence))
            self.shot(label + '-first-press')
        ok = bool(fg)
        fg = fg or self.dev.foreground()
        shot_path = self.shot(label)
        if proof:
            self.launch_proof(label, expect, mark)
        via = 'foreground' if fg[0] == expect else ('sam log' if sam else 'none')
        r.check('%s: Home opens %s (foreground, sam homeKey launch of the stock home in the log, or for a '
                'launcher: running after being closed first)' % (label, expect), ok,
                'signal: %s; getForegroundAppInfo: %s; running: %s%s' % (
                    via, fg[1], self.running_ids(),
                    ('; sam lines:\n' + '\n'.join(sam[:6])) if sam and via == 'sam log' else ''))
        return ok

    # -- the non-visual launch proof (e2e/README.md "Launch proof"): no screenshot, no on-screen label
    LIFE_LOG = '/tmp/hc-e2e-life.log'
    LIFE_PID = '/tmp/hc-e2e-life.pid'
    LAUNCH_OK = re.compile(r'NL_APP_LAUNCH_BEGIN|APP_LAUNCHED|RelaunchAsCommon')
    LAUNCH_BAD = re.compile(r'APP_LAUNCH_ERR|APP_LAUNCH_WARNING|LAUNCH_FAILED')

    def life_begin(self):
        """Hold a subscription to applicationManager/getAppLifeEvents on the device across the Home press."""
        self.dev.sh('kill $(cat %s) 2>/dev/null; rm -f %s %s; '
                    'nohup sh -c \'luna-send -i luna://com.webos.applicationManager/getAppLifeEvents '
                    '"{\\"subscribe\\":true}" > %s 2>&1 < /dev/null & echo $! > %s\' > /dev/null 2>&1; sleep 1'
                    % (self.LIFE_PID, self.LIFE_LOG, self.LIFE_PID, self.LIFE_LOG, self.LIFE_PID))

    def life_events(self, appid):
        """(events naming appid, subscription held) from the subscription started by life_begin()."""
        rc, out = self.dev.sh('cat %s 2>/dev/null' % self.LIFE_LOG)
        ev = []
        for line in out.splitlines():
            if appid in line:
                m = re.search(r'"event"\s*:\s*"([^"]*)"', line)
                ev.append((m.group(1) if m else '?', line.strip()))
        return ev, bool(out.strip())

    def life_end(self):
        self.dev.sh('kill $(cat %s) 2>/dev/null; rm -f %s %s; true' % (self.LIFE_PID, self.LIFE_LOG, self.LIFE_PID))

    def sam_launch_lines(self, appid, mark):
        """sam lines in /var/log/messages stamped at or after `mark` that name appid and are launch-related:
        NL_APP_LAUNCH_BEGIN {"app_id":"<id>",...}, APP_LAUNCHED, RelaunchAsCommon (and the _ERR/_WARNING variants)."""
        rc, out = self.dev.sh((messages_command() + " | awk '$1 >= \"%s\"' | grep -F %s | grep -E ' sam ' | tail -n 40")
                              % (mark, shlex.quote(appid)))
        return [l for l in out.splitlines() if self.LAUNCH_OK.search(l) or self.LAUNCH_BAD.search(l)]

    def launch_proof(self, label, appid, mark):
        """After a Home press that should have started or relaunched `appid`: sam's own record of it
        (a launch line in the log, or a launch/relaunch/foreground event on getAppLifeEvents) and the app
        in applicationManager/running with a live pid.  Works for every app type; reads no screen."""
        r = self.rec
        got = {}

        def check():
            lines = self.sam_launch_lines(appid, mark)
            ok_lines = [l for l in lines if self.LAUNCH_OK.search(l)]
            ev, held = self.life_events(appid)
            got.update(lines=lines, ok_lines=ok_lines, ev=ev, held=held)
            return ok_lines or [e for e in ev if e[0] in ('launch', 'relaunch', 'foreground', 'splash')]
        self.wait(check, 20, 2)
        running = appid in self.running_ids()
        lines, ok_lines, ev, held = (got.get(k, []) for k in ('lines', 'ok_lines', 'ev', 'held'))
        self.life_end()
        proved = bool(ok_lines) or any(e[0] in ('launch', 'relaunch', 'foreground', 'splash') for e in ev)
        kinds = sorted({e[0] for e in ev})
        relaunch = any(e[0] == 'relaunch' for e in ev) or any('RelaunchAsCommon' in l for l in ok_lines)
        r.check('%s: sam records the launch of %s (log line since %s or a getAppLifeEvents event) and it is running afterwards'
                % (label, appid, mark), proved and running,
                'sam log: %d launch line(s); getAppLifeEvents: %s (subscription %s); relaunch seen: %s; running: %s\n%s'
                % (len(ok_lines), kinds or 'no events', 'received data' if held else 'silent', relaunch, running,
                   '\n'.join((lines + [e[1] for e in ev])[-8:]) or 'no sam line or event naming the id'))

    def messages_mark(self):
        """UTC time on the device as sam stamps its lines (2026-10-23T05:48:12), taken before a press.
        A time, not a line count, because /var/log/messages can rotate between the two reads."""
        rc, out = self.dev.sh('date -u +%Y-%m-%dT%H:%M:%S')
        return out.strip().splitlines()[-1] if out.strip() else ''

    def sam_home_lines(self, mark, pattern=None):
        """Lines in /var/log/messages stamped at or after `mark` that show the Home key reaching the
        stock home: surface-manager's launchDefaultApp {"category":"home",...homeKey}, or sam's
        relaunch of com.webos.app.home."""
        pattern = pattern or ('launchDefaultApp.*homeKey|homeKey.*launchDefaultApp|'
                              'RelaunchAsCommon.*com.webos.app.home')
        rc, out = self.dev.sh((messages_command() + " | awk '$1 >= \"%s\"' | grep -E '%s' | tail -n 8")
                              % (mark, pattern))
        return [l for l in out.splitlines() if l.strip()]

    # -- steps
    def run(self):
        r, d = self.rec, self.dev
        strat = ELEVATIONS[self.a.elevation or self.strategy_name]()
        r.say('run: vm=%s release=%s elevation=%s (matrix default %s) input=%s host=%s launcher=%s (%s, overlay=%s, ipk %s)'
              % (self.vm, self.release, strat.name, self.strategy_name, self.a.input, self.host.mode,
                 self.launcher, self.L.spec, self.L.overlay, self.L.ipk))
        r.say('matrix confidence: %s -- %s' % (self.sure, self.note))
        self.major, osr = d.release()
        r.begin('0 baseline')
        r.check('device release matches the matrix (%s)' % self.release,
                '%d.' % self.major == self.release[:-1], 'os-release: ' + osr)
        fullhome = d.app_installed(FULLHOME)
        self.stock_id = stock_home_id(self.major, fullhome)
        r.note('stock home id written by restore/removal/uninstall: %s (%s installed: %s)'
               % (self.stock_id or 'null (10+)', FULLHOME, fullhome), '')
        base = self.state()
        r.note('baseline state (a stale record home is image state, not ours)', json.dumps(base))
        r.check('baseline: nothing of Home Chooser is installed',
                APP_ID not in d.installed() and 'No such' in base['choice'],
                'installed: %s; choice: %s' % (d.installed(), base['choice']))
        r.check('baseline: Homebrew Channel is installed', 'org.webosbrew.hbchannel' in d.installed(),
                str(d.installed()))
        df = d.sh('df -h /media/developer | tail -1')[1].strip()
        r.note('df /media/developer', df)
        r.note('baseline preload policy of %s (isEnabled, raw reply)' % STOCK, repr(d.preload_policy()))

        if self.L.is_fixture and self.L.fixture[0] == 'qml':
            sup, ev = qml_support(self.major)
            r.note('qml support expectation for %d.x: %s' % (self.major, sup), ev)
            if sup is False:
                r.check('qml fixtures run on %d.x' % self.major, False, ev, expected=ev)
                return
        screensaver = getattr(self.a, 'scenario', 'lifecycle') in ('screensaver', 'all')
        baseline = self.screensaver_baseline() if screensaver else None
        steps = [self.step1, self.step2, self.step3, self.step4, self.step5]

        def guarded(fn, *a):
            # 6.x to 9.x: no crash of WebAppMgr (or anything crashd reports) during the step
            snap = self.compositor_snapshot() if self.uses_home_key_hook() else None
            fn(*a)
            self.check_no_crash('end of step', snap)
        try:
            for i, fn in enumerate(steps, 1):
                if i > self.a.until:
                    break
                guarded(fn, strat) if i == 1 else guarded(fn)
                if i == 2 and screensaver:
                    self.screensaver_selected(baseline)
                    if self.a.scenario == 'screensaver':
                        break
                if i == 3 and self.a.api_check:
                    guarded(self.step_api)
                if i == 3 and self.a.reboot_check:
                    guarded(self.step_reboot)
                    if self.uses_home_key_hook() and not self.a.update_check:
                        guarded(self.step_cache)
                if i == 3 and self.a.update_check:
                    guarded(self.step_update)
                if i == 3 and self.a.launcher_update_check:
                    guarded(self.step_launcher_update)
                if i == 3 and self.a.restore_repair_check:
                    guarded(self.step_restore_repair)
                    break
        except ScreensaverOutcome as exc:
            r.outcome('tvpower reboot readiness', exc.status, str(exc))
        finally:
            if getattr(self, 'ask_monitor', None):
                self.ask_monitor.close()
                self.ask_monitor = None
            if self.a.until >= 5:
                self.step6()
            else:
                r.say('--until %d: apps left installed for debugging; no cleanup' % self.a.until)

    # -- home and screensaver (all calls below are bounded; no settings writes or ENTER)
    def screensaver_power(self):
        power = {'power': self.dev.luna_as(None, TVPOWER + 'power/getPowerState')[1:]}
        power['power2'] = (('Not queried on webOS 6 (6.5.2 device evidence: Unknown method)', None)
                           if self.major == 6 else
                           self.dev.luna_as(None, TVPOWER + 'power2/getPowerState')[1:])
        return power

    def screensaver_snapshot(self, label):
        d = self.dev
        choice_rc, choice = d.sh('cat %s 2>&1' % CHOICE)
        record_home, record_raw = d.record_home()
        snapshot = {'power': self.screensaver_power(), 'foreground': d.foreground(),
                    'unit': d.unit('homechooser-home'), 'choice': [choice_rc, choice.strip()],
                    'record_home': record_home, 'defaultApps': parse_luna(record_raw),
                    'preload': d.preload_policy(), 'boot': d.boot_id()}
        self.rec.note(label, json.dumps(snapshot))
        self.shot(label)
        return snapshot

    def screensaver_diagnostics(self, label):
        """Retain both state replies and tvpower lines before classifying a refusal."""
        self.rec.note(label + ': power and power2', json.dumps(self.screensaver_power()))
        mark = getattr(self, 'screensaver_log_mark', '')
        if re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z?', mark):
            command = messages_command() + " | awk '$1 >= \"%s\"'" % mark
        else:
            command = messages_command()
        self.rec.note(label + ': tvpower logs', self.dev.sh(
            command + " | grep -Ei 'tvpower|Screen Saver|lgchannels' | tail -n 160")[1])

    @staticmethod
    def screensaver_require(ok, evidence):
        if not ok:
            raise ScreensaverOutcome('FAIL', evidence)

    def screensaver_ready(self, power):
        return (any(power_field(row[1], 'processing') == 'Screen Saver Ready' for row in power.values())
                or power_field(power['power2'][1], 'sub processing') == 'screen saver ready')

    def beanjar_screensaver(self, verb):
        if self.major == 6:
            self.rec.note('beanjar screensaver unavailable on webOS 6; not attempted',
                          'Beanjar requires power2/getPowerState and getForegroundApps window types; '
                          'use legacy state evidence and normal synced-stop cleanup for beanjar#56.')
            return None
        result = self.host.screensaver(verb, self.vm)
        if result is None:
            if not getattr(self.host, '_screensaver_missing_logged', False):
                self.rec.note('beanjar screensaver subcommand missing; recovery unavailable; keeping existing behaviour',
                              str(getattr(self.host, '_screensaver_probe', 'older Beanjar')))
                self.host._screensaver_missing_logged = True
            return None
        rc, raw = result
        self.rec.note('beanjar screensaver %s JSON (rc=%s)' % (verb, rc), raw)
        return rc, raw, parse_luna(raw)

    def prepare_tvpower_reboot(self, label):
        """Recover only a stale Ready state; retain helper output and independent state proof."""
        before = self.screensaver_power()
        self.rec.note(label + ': power before reboot', json.dumps(before))
        if not self.screensaver_ready(before):
            return True
        self.screensaver_diagnostics(label + ': stale Screen Saver Ready before reboot')
        if self.major == 6:
            raise ScreensaverOutcome('BLOCKED', 'beanjar#56: Screen Saver Ready on webOS 6; '
                                     'Beanjar screensaver helper unsupported; use normal synced-stop cleanup')
        helper = self.beanjar_screensaver('stop')
        if helper is None:
            return False  # Old Beanjar: caller keeps its existing refusal/BLOCKED path.
        rc, raw, reply = helper
        after = self.screensaver_power()
        self.rec.note(label + ': power after beanjar screensaver stop', json.dumps(after))
        routes = ('power', 'power2') if self.major >= 7 else ('power',)
        readable = all(after[route][1] and after[route][1].get('returnValue') is True for route in routes)
        if rc or not reply or reply.get('action') != 'stop' or not readable or self.screensaver_ready(after):
            self.screensaver_diagnostics(label + ': screensaver recovery failed')
            raise ScreensaverOutcome('BLOCKED', 'beanjar#56: stale Screen Saver Ready recovery failed: ' + raw)
        self.rec.outcome('recovered stale Screen Saver Ready via beanjar screensaver stop, beanjar#56',
                         'RECOVERED', json.dumps({'helper': reply, 'before': before, 'after': after}))
        return True

    def screensaver_state(self, power, shown):
        route = 'power' if self.major == 6 else 'power2'
        reply = power[route][1]
        field = 'state' if self.major == 6 else 'sub state'
        expected = ('Screen Saver' if shown else 'Active') if self.major == 6 else ('screen saver' if shown else 'active')
        return bool(reply and reply.get('returnValue') is True and not self.screensaver_ready(power)
                    and power_field(reply, field) == expected)

    def screensaver_shown(self, power):
        return self.screensaver_state(power, shown=True)

    def screensaver_active(self, power):
        return self.screensaver_state(power, shown=False)

    def power_settled(self, power):
        """tvpowerd is idle: ACTIVE / active (6.x: Active) and no 'sub processing' (6.x: 'processing')."""
        route = 'power' if self.major == 6 else 'power2'
        reply = power[route][1]
        if not (reply and reply.get('returnValue') is True):
            return False
        if self.major == 6:
            return power_field(reply, 'state') == 'Active' and not power_field(reply, 'processing')
        return (power_field(reply, 'state') == 'ACTIVE' and power_field(reply, 'sub state') == 'active'
                and not power_field(reply, 'sub processing'))

    def screensaver_settle(self, label):
        """Bounded wait (60 s, scaled by SLOWDOWN) for a settled power state before a start request.

        tvpowerd accepts turnOnScreenSaver during its own boot Screen On step and then silently
        cancels it (beanjar#184, firmware behaviour). Returns (settled, evidence)."""
        polls, last = [], [None]
        def settled():
            last[0] = self.screensaver_power()
            polls.append(self.power_settled(last[0]))
            return polls[-1]
        t0 = time.time()
        ok = bool(self.wait(settled, 60, 2))
        evidence = 'power %s after %.1fs, %d poll(s); last seen: %s' % (
            'settled' if ok else 'NEVER settled within 60s (x%s slowdown)' % SLOWDOWN,
            time.time() - t0, len(polls), json.dumps(last[0]))
        self.rec.note('screensaver start %s: power settle wait' % label, evidence)
        return ok, evidence

    def screensaver_request(self):
        """One start request. Returns None when the request was accepted, or a string describing
        a refusal during a power transition (the only refusal worth a retry)."""
        if getattr(self.a, 'saver_start', 'tvpower') == 'beanjar':
            helper = self.beanjar_screensaver('start')
            if helper is None:
                raise ScreensaverOutcome('SKIPPED', '--saver-start beanjar requires the beanjar screensaver subcommand')
            rc, raw, reply = helper
            if rc and re.search(r'power transition', raw, re.I):
                return 'beanjar screensaver start refused during a power transition: ' + raw
            if rc or not reply or reply.get('action') != 'start':
                self.screensaver_diagnostics('beanjar screensaver start failed')
                raise ScreensaverOutcome('BLOCKED', 'beanjar screensaver start failed: ' + raw)
            return None
        route = 'power' if self.major == 6 else 'power2'
        rc, raw, reply = self.dev.luna_as(None, TVPOWER + route + '/turnOnScreenSaver')
        self.rec.note('screensaver start reply (root, no -a)', raw)
        if rc or not reply or reply.get('returnValue') is not True:
            self.screensaver_diagnostics('screensaver refused')
            reason = str((reply or {}).get('errorText', raw)).lower()
            precondition = bool(re.search(
                r'screensaverenabled|screen\s*saver.*(?:disabled|not enabled|off)|'
                r'first[ -]?use|sub[ -]?state', reason))
            raise ScreensaverOutcome('SKIPPED' if precondition and reply and
                                     reply.get('returnValue') is False else 'FAIL', raw)
        return None

    def screensaver_start(self):
        if self.major < 6:
            raise ScreensaverOutcome('SKIPPED', 'No evidenced screensaver endpoint for webOS <6')
        beanjar = getattr(self.a, 'saver_start', 'tvpower') == 'beanjar'
        attempts = []
        for attempt in (1, 2):
            label = 'attempt %d' % attempt
            ok, settle = self.screensaver_settle(label)
            if not ok:
                self.screensaver_diagnostics('screensaver start %s: power never settled' % label)
                raise ScreensaverOutcome('BLOCKED', 'power state never settled before the start request; '
                                         'request not sent. ' + ' | '.join(attempts + [label + ': ' + settle]))
            refused = self.screensaver_request()
            if refused:
                attempts.append('%s: %s (%s)' % (label, refused, settle))
                self.rec.note('screensaver start %s refused during power transition' % label, refused)
                continue
            if self.wait(lambda: self.screensaver_shown(self.screensaver_power()), 45, 2):
                self.rec.note('screensaver confirmed after beanjar start' if beanjar
                              else 'screensaver confirmed: native shown state',
                              json.dumps(self.screensaver_power()))
                if attempts:
                    self.rec.note('screensaver start succeeded on retry', ' | '.join(attempts))
                return
            power = self.screensaver_power()
            self.screensaver_diagnostics('screensaver start %s: state not confirmed' % label)
            if self.screensaver_ready(power):
                raise ScreensaverOutcome('BLOCKED', 'beanjar#56: Screen Saver Ready')
            if not self.screensaver_active(power):
                raise ScreensaverOutcome('FAIL', 'Screensaver did not reach the native shown state; power: ' +
                                         json.dumps(power))
            attempts.append('%s: dropped by firmware (power back to plain Active, no screen saver; %s)' %
                            (label, settle))
            self.rec.note('screensaver start %s dropped by firmware' % label, json.dumps(power))
        raise ScreensaverOutcome('FAIL', 'Screensaver start dropped by firmware twice or refused during '
                                 'power transitions: ' + ' | '.join(attempts))

    def screensaver_button(self, key, expected=None):
        # This path deliberately uses Beanjar's IR remote, never turnOnScreen or a dialog action.
        rc, raw = self.host.remote_press(self.vm, key)
        self.screensaver_require(rc == 0, 'Remote %s failed: %s' % (key, raw))
        def dismissed():
            power = self.screensaver_power()
            return (self.screensaver_active(power)
                    and (expected is None or self.dev.foreground()[0] == expected))
        if not self.wait(dismissed, 30, 2):
            power = self.screensaver_power()
            self.screensaver_diagnostics('screensaver dismissal timeout')
            raise ScreensaverOutcome('BLOCKED' if self.screensaver_ready(power) else 'FAIL',
                                     'beanjar#56: Screen Saver Ready after button dismissal'
                                     if self.screensaver_ready(power) else
                                     '%s did not dismiss screensaver and return %s' % (key, expected))
        sleep(1)
        foreground = self.dev.foreground()
        self.rec.note('%s dismissed screensaver; observed foreground %s' % (key, foreground[0]),
                      json.dumps({'power': self.screensaver_power(), 'foreground': foreground}))
        return foreground

    def screensaver_unchanged(self, before, after):
        for field in ('choice', 'defaultApps', 'unit', 'boot'):
            self.screensaver_require(before[field] == after[field], '%s changed: %r -> %r' %
                                     (field, before[field], after[field]))
        # getPreloadPolicy returns every app, and firmware can reorder that array while
        # showing a screensaver. Compare the complete stock-home entry, retaining raw
        # replies in snapshots, rather than asserting unrelated ordering is stable.
        def stock_policy(sample):
            reply = parse_luna(sample[1])
            rows = reply.get('applications') if isinstance(reply, dict) and reply.get('returnValue') is True else None
            if not isinstance(rows, list):
                return None
            matches = [row for row in rows if isinstance(row, dict) and row.get('id') == STOCK]
            return matches[0] if len(matches) == 1 else None
        old_policy, new_policy = stock_policy(before['preload']), stock_policy(after['preload'])
        self.screensaver_require(old_policy is not None and new_policy is not None
                                 and old_policy == new_policy
                                 and before['preload'][0] == after['preload'][0],
                                 'Stock-home preload policy changed or unreadable: %r -> %r' %
                                 (before['preload'], after['preload']))
        self.screensaver_require(after['choice'] == [0, self.launcher]
                                 and after['record_home'] == self.launcher
                                 and after['defaultApps'] is not None
                                 and after['preload'][0] is False
                                 and after['unit'].get('ActiveState') == 'active'
                                 and after['unit'].get('MainPID') not in (None, '', '0')
                                 and after['unit'].get('NRestarts') is not None,
                                 'Chosen launcher/daemon/preload state is not readable and applied')

    def screensaver_step(self, number, action, dependency=None):
        label = 'S%d home and screensaver' % number
        self.rec.begin(label)
        before = None
        status, evidence = 'PASS', 'Screensaver assertions passed'
        try:
            self.screensaver_log_mark = self.messages_mark()
            before = self.screensaver_snapshot('S%d-before' % number)
            if dependency and dependency != 'PASS':
                raise ScreensaverOutcome('SKIPPED' if dependency == 'SKIPPED' else 'BLOCKED',
                                         'Prerequisite screensaver step: ' + dependency)
            observed = action(before)
            if observed is not None:
                evidence = json.dumps(observed)
        except ScreensaverOutcome as exc:
            status, evidence = exc.status, str(exc)
        except Exception as exc:
            status, evidence = 'FAIL', repr(exc)
        finally:
            try:
                self.screensaver_snapshot('S%d-after' % number)
            except Exception as exc:
                status, evidence = 'FAIL', 'After snapshot failed: ' + repr(exc)
        self.rec.outcome(label, status, evidence)
        return status

    def screensaver_baseline(self):
        self.screensaver_stock = {}
        def action(before):
            self.screensaver_require(APP_ID not in self.dev.installed() and before['choice'][0] != 0,
                                     'S0 requires Home Chooser absent, including its choice file')
            for key in ('BACK', 'HOME'):
                self.dev.launch('org.webosbrew.hbchannel')
                self.screensaver_require(bool(self.wait(lambda: self.dev.foreground()[0] ==
                                                        'org.webosbrew.hbchannel', 30, 2)),
                                         'Baseline previous app did not reach foreground')
                previous = self.dev.foreground()
                self.screensaver_start()
                wake = self.screensaver_button(key)  # Firmware observation, no assumed destination.
                self.shot('S0-%s-wake' % key.lower())
                rc, raw = self.host.remote_press(self.vm, 'HOME')
                self.screensaver_require(rc == 0, 'S0 second HOME delivery failed: ' + raw)
                self.wait(lambda: (lambda fg: fg if fg[0] and fg[0] != wake[0] else None)(
                    self.dev.foreground()), 15, 1)
                sleep(1)
                home = self.dev.foreground()
                self.shot('S0-%s-then-home' % key.lower())
                self.screensaver_stock[key] = {'previous': previous, 'wake': wake, 'home': home}
                self.rec.note('S0 stock %s then HOME baseline' % key, json.dumps(self.screensaver_stock[key]))
            return self.screensaver_stock
        self.host.vnc_start(self.vm)
        return self.screensaver_step(0, action)

    def screensaver_expected(self, key, phase, previous):
        """Map an observed stock-Home dispatch to our launcher; retain every other destination."""
        stock = getattr(self, 'screensaver_stock', {}).get(key)
        if not stock or not stock[phase][0] or not stock['previous'][0]:
            raise ScreensaverOutcome('BLOCKED', 'S0 has no readable %s %s foreground baseline' % (key, phase))
        observed = stock[phase][0]
        if observed == stock['previous'][0]:
            return previous
        if observed in (STOCK, FULLHOME, self.stock_id):
            return self.launcher
        return observed

    def screensaver_followup_home(self, key, previous, label):
        expected = self.screensaver_expected(key, 'home', previous)
        rc, raw = self.host.remote_press(self.vm, 'HOME')
        self.screensaver_require(rc == 0 and bool(self.wait(lambda: self.dev.foreground()[0] == expected, 30, 2)),
                                 '%s: second HOME did not match S0 (%s): %s' % (label, expected, raw))
        self.rec.note(label + ': second HOME matched S0', self.dev.foreground()[1])

    def screensaver_selected(self, baseline):
        def s1(before):
            self.screensaver_require(self.L.is_fixture and not self.L.overlay,
                                     'Screensaver foreground scenario requires a normal fixture launcher')
            self.dev.launch(self.launcher)
            self.screensaver_require(bool(self.wait(lambda: self.dev.foreground()[0] == self.launcher, 30, 2)),
                                     'Chosen fixture did not reach foreground')
            # Sample after launch has settled, so normal deferred stock-home close is outside S1.
            sleep(5)
            stable = self.screensaver_snapshot('S1-launcher-settled')
            log0 = self.log_lines()
            self.screensaver_start()
            sleep(5)
            during = self.screensaver_snapshot('S1-during')
            self.screensaver_unchanged(stable, during)
            lines = self.log_lines()
            delta = lines[len(log0):] if lines[:len(log0)] == log0 else lines
            self.rec.note('S1 daemon log during screensaver', '\n'.join(delta))
            self.screensaver_require(not any('closeByAppId' in line or 'setPreloadPolicy' in line
                                            for line in delta), 'Daemon reacted during screensaver: ' + '\n'.join(delta))
            if self.major >= 11:
                # 11+ follows foreground/life events; ignoring the screensaver is shown by the absence of any
                # action above (the daemon logs actions, not skipped events), so only record what it logged.
                self.rec.note('S1 daemon lines naming a screensaver app',
                              '\n'.join(line for line in delta if any(app in line for app in SCREENSAVERS)) or 'none')
        first = self.screensaver_step(1, s1, baseline)
        def s2(before):
            self.screensaver_require(self.screensaver_shown(before['power']), 'S2 requires the S1 screensaver')
            # Leave S1's saver using the measured BACK behaviour, then test HOME from the same
            # ordinary app as S0. A wake to an already foreground launcher cannot prove dispatch.
            self.screensaver_button('BACK', self.screensaver_expected('BACK', 'wake', self.launcher))
            previous = 'org.webosbrew.hbchannel'
            self.dev.launch(previous)
            self.screensaver_require(bool(self.wait(lambda: self.dev.foreground()[0] == previous, 30, 2)),
                                     'S2 previous app not foreground')
            self.screensaver_start()
            self.screensaver_button('HOME', self.screensaver_expected('HOME', 'wake', previous))
            self.screensaver_unchanged(before, self.screensaver_snapshot('S2-dismissed'))
            stock = self.screensaver_stock['HOME']
            if stock['wake'][0] not in (STOCK, FULLHOME, self.stock_id):
                self.screensaver_followup_home('HOME', previous, 'S2')
                self.screensaver_unchanged(before, self.screensaver_snapshot('S2-home'))
        second = self.screensaver_step(2, s2, first)
        def s3(before):
            previous = 'org.webosbrew.hbchannel'
            self.dev.launch(previous)
            self.screensaver_require(bool(self.wait(lambda: self.dev.foreground()[0] == previous, 30, 2)),
                                     'Previous app not foreground')
            self.screensaver_start()
            self.screensaver_button('BACK', self.screensaver_expected('BACK', 'wake', previous))
            self.screensaver_unchanged(before, self.screensaver_snapshot('S3-back'))
            self.screensaver_followup_home('BACK', previous, 'S3')
            self.screensaver_unchanged(before, self.screensaver_snapshot('S3-home'))
        third = self.screensaver_step(3, s3, second)
        def s4(before):
            self.screensaver_start()
            try:
                self.screensaver_button('BACK', self.launcher)
            except ScreensaverOutcome as exc:
                if exc.status != 'BLOCKED' or not self.screensaver_ready(self.screensaver_power()):
                    raise
                if not self.prepare_tvpower_reboot('S4 after BACK'):
                    raise
                power = self.screensaver_power()
                self.screensaver_require(self.screensaver_active(power)
                                         and self.dev.foreground()[0] == self.launcher,
                                         'S4 recovered Ready but previous foreground did not return')
            self.screensaver_unchanged(before, self.screensaver_snapshot('S4-dismissed'))
            boot0 = self.dev.boot_id()
            self.prepare_tvpower_reboot('S4 tvpower reboot')
            rc, raw, reply = self.dev.luna_as(None, TVPOWER_REBOOT_URI, '{"reason":"remoteKey"}')
            self.rec.note('S4 tvpower reboot reply', raw)
            if rc or not reply or reply.get('returnValue') is not True:
                self.screensaver_diagnostics('S4 reboot refused')
                busy = 'power is busy' in raw.lower() or self.screensaver_ready(self.screensaver_power())
                raise ScreensaverOutcome('BLOCKED' if busy else 'FAIL',
                                         ('beanjar#56: ' if busy else '') + raw)
            how = self.wait_reboot(boot0, timeout=240)
            if how == 'off':
                rc, raw = self.host.start(self.vm)
                self.screensaver_require(rc == 0, 'VM restart after guest power-off failed: ' + raw)
            if how not in ('off', 'rebooted'):
                self.screensaver_diagnostics('S4 reboot timeout')
                ready = self.screensaver_ready(self.screensaver_power())
                raise ScreensaverOutcome('BLOCKED' if ready else 'FAIL',
                                         'beanjar#56: Screen Saver Ready after dismissal' if ready else
                                         'TV did not restart after accepted tvpower reboot')
            self.screensaver_require(self.await_boot('S4 tvpower reboot', boot0),
                                     'TV did not return from tvpower reboot')
            start = len(self.rec.results)
            self.verify_reboot_apply()
            self.screensaver_require(not any(row['status'] == 'FAIL' for row in self.rec.results[start:]),
                                     'Boot apply assertions failed')
        self.screensaver_step(4, s4, third)

    def step1(self, strat):
        r, d = self.rec, self.dev
        r.begin('1 install')
        self.hc_new = self.a.new_ipk or current_home_chooser_ipk(r, self.a.build)
        hc = self.a.old_ipk if self.a.update_check else self.hc_new
        if self.a.update_check:
            r.say('--update-check: the OLD Home Chooser (%s, %s) is installed first; the current build %s goes over it in step 3u'
                  % (self.a.old_ref, hc, self.hc_new))
        df = d.sh('df -h /media/developer | tail -1')[1].strip()
        r.note('df /media/developer before installing', df)
        rc, out = d.install(hc)
        r.check('install Home Chooser', rc == 0 and 'Installed package' in out, out.strip())
        rc, out = d.install(self.L.ipk)
        r.check('install launcher %s' % self.launcher, rc == 0 and 'Installed package' in out, out.strip())
        if self.uses_home_key_hook() and self.a.reboot_check and not self.a.update_check:
            self.cache_prepare_measure()
        r.note('launcher appinfo (from the ipk; overlay=%s)' % self.L.overlay, json.dumps(self.L.appinfo))
        if self.a.api_check:
            rc, out = d.install(self.a.disposable_ipk)
            r.check('--api-check: install the disposable app %s' % DISPOSABLE_ID, rc == 0 and 'Installed package' in out,
                    out.strip())
        r.check('both apps are listed', APP_ID in d.installed() and self.launcher in d.installed(),
                str(d.installed()))
        strat.prepare(d, r)
        self.strat = strat

    def step2(self):
        r, d = self.rec, self.dev
        r.begin('2 choose the launcher through the UI')
        self.mark_log()  # the watcher log accumulates over the fixtures of a boot: later checks read only what follows
        self.residency('before selecting')
        self.insp = Inspector(self.vm)
        d.launch(APP_ID)
        ui = self.wait(lambda: (lambda u: u if u and self.launcher in (u.get('rows') or []) else None)(self.ui()), 120)
        self.strat.verify(d, r)
        self.shot('app-open')
        sleep(4)  # the result toast of a left marker is set just after the list appears
        first = self.ui() or {}
        r.check('first open after a fresh install: no error toast (a marker left by an earlier install is dropped)',
                not (first.get('toast') or '').startswith("Couldn't"), 'toast: %r' % first.get('toast'))
        if self.a.api_check:
            self.api_before_selection()
            r.begin('2 choose the launcher through the UI (continued)')
        # a marker as an earlier install's cut-short run leaves it (WAM keeps localStorage across an
        # uninstall; the choice was removed with it): the next open must drop it without a toast
        r.note('planted stale marker', self.insp.evaluate(PLANT_JS % self.launcher))
        d.close(APP_ID)
        sleep(3)
        self.launch_mark = self.messages_mark()
        d.launch(APP_ID)
        toasts = []

        def listed_stale():
            u = self.ui()
            if u and u.get('toast'):
                toasts.append(u['toast'])
            return u if u and self.launcher in (u.get('rows') or []) else None
        self.wait(listed_stale, 120, every=1)
        sleep(3)
        stale = self.ui() or {}
        if stale.get('toast'):
            toasts.append(stale['toast'])
        self.shot('stale-marker')
        r.check('a stale marker (nothing stored) is dropped: no toast at all', not toasts, 'toasts: %r' % toasts)
        r.check('the stale marker is cleared', self.insp.evaluate(MARKER_JS) in (None, '', 'null'),
                'marker: %r' % self.insp.evaluate(MARKER_JS))
        ui = stale if stale.get('rows') else ui
        r.check('the list shows the launcher row (data-app-id)', bool(ui), 'DOM: %s' % json.dumps(ui))
        if not ui:
            raise RuntimeError('UI never listed the launcher; cannot continue')
        r.say('UI driven: initial focus on %s' % ui.get('row'))
        rows = ui['rows']
        if not self.ensure_page('2 before the list keys'):
            raise RuntimeError('Home Chooser is not on screen; not pressing keys')
        self.ensure_key_focus(rows)
        ui = self.ui() or ui
        clicking = self.use_click()
        if clicking:
            self.click_element(self.L.title, role=('text', 'button'), effect=lambda: bool((self.ui() or {}).get('dialog')),
                               label='launcher row')
        else:
            # move focus to the launcher's row; a key only while our page is readable
            for _ in range(len(rows) + 2):
                ui = self.ui()
                if ui is None:
                    r.note('2: our page went away while moving the focus; no more keys',
                           self.page_gone_evidence(getattr(self, 'launch_mark', '') or ''))
                    ui = {}
                    break
                if ui.get('row') == self.launcher:
                    break
                cur, tgt = rows.index(ui['row']) if ui.get('row') in rows else 0, rows.index(self.launcher)
                self.press('DOWN' if tgt > cur else 'UP')
                r.say('focus move: was %s, now %s; foreground %s' % (ui.get('row'), (self.ui() or {}).get('row'),
                                                                     self.dev.foreground()[0]))
            ui = self.ui() or {}
            r.check('focus is on the launcher row', ui.get('row') == self.launcher, json.dumps(ui))
            self.shot('row-focused')
            if ui.get('row') != self.launcher or (self.ui() or {}).get('row') != self.launcher:
                raise RuntimeError('focus is not on the launcher row of our page; not pressing OK')
            self.press('OK')
        ui = self.wait(lambda: (lambda u: u if u and u.get('dialog') else None)(self.ui()), 15, 1) or {}
        r.check('the confirmation dialog opened ("Use <name> as Home?")',
                bool(ui.get('dialog')) and ui['dialog'].startswith('Use '), json.dumps(ui))
        r.say('UI driven: dialog "%s", focus "%s"' % (ui.get('dialog'), ui.get('text')))
        self.shot('dialog')
        if not clicking:
            if ui.get('dialog') and ui.get('text') != 'Use as Home':
                self.press('UP')
                ui = self.ui() or {}
            r.check('focus is on "Use as Home"', ui.get('text') == 'Use as Home', json.dumps(ui))
            if ui.get('text') != 'Use as Home':
                raise RuntimeError('focus is not on "Use as Home" in our dialog; not pressing OK')
        mounted_before = bool(d.mounts()) if self.uses_home_key_hook() else False
        comp0 = self.compositor_snapshot() if self.uses_home_key_hook() else None
        if self.uses_home_key_hook():
            r.note('patch mounted before choosing: %s (a restart prompt is %s)' % (
                mounted_before, 'expected' if expects_reboot_dialog(self.major, mounted_before) else 'not expected'), '')
        self.ask_mark = self.messages_mark()
        if expects_reboot_dialog(self.major, mounted_before):
            self.ask_monitor = RebootAlertMonitor(d)
            if not self.ask_monitor.start():
                r.note('notification bus monitor did not start; using PmLog evidence', '')
        toasts = self.confirm_home_choice(clicking)
        if self.major >= 11:
            page = self.insp._page() if self.insp else None
            state = self.page_state() or {}
            r.check('Home Chooser page is still open right after confirming',
                    bool(page and APP_ID in page.get('url', '') and
                         state.get('visibility') == 'visible' and self.ui()),
                    'page=%r; state=%r' % (page.get('url') if page else None, state))
        if expects_reboot_dialog(self.major, mounted_before):
            self.reboot_after_choice(comp0)
            toast = None
        else:
            if not toasts:
                self.wait(lambda: (lambda u: (toasts.append(u['toast']) or True) if u.get('toast') else False)(self.ui() or {}), 30, 1)
            toast = next((t for t in toasts if t), None)
            self.shot('after-confirm')
            r.note('toasts after confirming', repr(toasts))
            r.check('the toast said "%s"' % DONE_TEXT, toast == DONE_TEXT, repr(toast))
            if self.uses_home_key_hook():
                # the patch was mounted already (an earlier choice in this boot): a live switch
                self.check_no_compositor_restart('choosing with the patch mounted (a live switch)', comp0)
                st, raw = self.kf_status()
                r.check('homekey status: active=yes and reboot=no (nothing to ask)',
                        st.get('active') == 'yes' and st.get('reboot') == 'no', raw)
                self.wait(lambda: self.launcher in self.baked_id(), 60, 5)
                baked = self.baked_id()
                r.check('the watcher wrote the launcher id into the selected systemUi.js (refresh, no restart)',
                        self.launcher in baked, baked)
        self.wait(lambda: self.watcher_active() == 'active' and (not self.uses_home_key_hook() or self.hook_mounts_match()), 90)
        self.residency('right after "Use as Home" (settled)')
        s = self.state()
        r.note('state after choosing', json.dumps(s))
        r.check('choice file holds the launcher id', s['choice'] == self.launcher, repr(s['choice']))
        r.check('general/defaultApps record home is the launcher', s['record_home'] == self.launcher, s['record_raw'])
        r.check('watcher unit homechooser-home is active', s['watcher'] == 'active', s['watcher'])
        r.check('init.d entry points to the installed boot entry',
                s['initd'] == (BOOT_SCRIPT if self.uses_home_key_hook() else BOOT_DIR + '/homechooser'), s['initd'])
        if self.uses_home_key_hook():
            r.check('live key-filter is selected and QML mounts are absent (webOS 6-9%s)' % (', after the boot' if not mounted_before else ''),
                    self.hook_mounts_match(s['mounts']), 'mounts: %s' % s['mounts'])
            r.check('Home key hook flag file exists', s['flag'] == 'yes', s['flag'])
            self.check_hook_status('after selection' + (' and the boot' if not mounted_before else ''))
        else:
            r.check('no Home key hook on this release', not s['mounts'] and s['flag'] == 'no',
                    'mounts: %s flag: %s' % (s['mounts'], s['flag']))
        if self.uses_override():
            r.check('profile override exists (webOS 10+)', s['override'] == 'yes', s['override'])
            r.note('override', d.sh('cat %s' % OVERRIDE)[1])
        if not self.a.update_check:  # --update-check: step 1 installed the old build; checked in 3u
            self.check_device_texts()
        # open the panel again after the apply (a cut-short run left its marker): no error, the choice ticked
        info = self.reopen_panel(120)
        again, seen = info['again'], info['seen']
        r.note('toasts seen while re-opening', repr(seen + [again.get('toast')]))
        again['toast'] = again.get('toast') or next((t for t in seen if t.startswith("Couldn't")), None)
        self.shot('reopened')
        r.check('re-open after the apply: no error toast', not (again.get('toast') or '').startswith("Couldn't"),
                'toast: %r' % again.get('toast'))
        # One evaluate can fail while the page settles (None: no page, socket error); the DOM read above
        # may still have worked. A bounded retry, only while nothing is ticked yet.
        ticked = self.insp.evaluate(TICK_JS) if self.insp else None
        for _ in range(5):
            if ticked == self.launcher or not self.insp:
                break
            sleep(2)
            ticked = self.insp.evaluate(TICK_JS)
        r.check('re-open after the apply: the chosen row is ticked', ticked == self.launcher,
                self.reopen_evidence(info, ticked, ticked == self.launcher))
        d.close(APP_ID)
        if self.insp:
            self.insp.close()
            self.insp = None
        self.check_preload_off('after the switch')

    def reopen_panel(self, timeout):
        """Close and relaunch Home Chooser, then read the panel until the launcher row is listed (within
        `timeout`).  Records the rc and output of both ares-launch calls, and recreates the Inspector after
        a connection-type read failure (as see_toast does).  Returns a dict: again (last DOM, {} when none
        was read), dom (True when any read gave a DOM), seen (toasts), launch (notes), reads, failed,
        recreated."""
        info = {'again': {}, 'dom': False, 'seen': [], 'launch': [], 'reads': 0, 'failed': 0, 'recreated': 0}
        if not self.insp:
            self.insp = Inspector(self.vm)
        for label, fn in (('close', self.dev.close), ('launch', self.dev.launch)):
            rc, out = fn(APP_ID)
            info['launch'].append('ares-launch %s rc=%s %r' % (label, rc, (out or '').strip()[-200:]))
            if label == 'close':
                sleep(3)
        end = time.time() + seconds(timeout)
        while True:
            if self.insp is None:
                self.insp = Inspector(self.vm)
            try:
                u = self.ui()
            except Exception as exc:
                u = None
                self.insp.last_error = 'ui() raised %r' % (exc,)
            info['reads'] += 1
            if u is None:
                info['failed'] += 1
                err = getattr(self.insp, 'last_error', None)
                if err and self.CONNECTION_ERROR.search(str(err)) and time.time() < end:
                    self.close_insp()
                    self.insp = Inspector(self.vm)
                    info['recreated'] += 1
            else:
                info['dom'] = True
                info['again'] = u
                if u.get('toast'):
                    info['seen'].append(u['toast'])
                if self.launcher in (u.get('rows') or []):
                    break
            if time.time() >= end:
                break
            sleep(1)
        sleep(1)
        last = self.ui()
        if last is not None:
            info['dom'] = True
            info['again'] = last
        return info

    def reopen_evidence(self, info, ticked, ok):
        """Evidence text of the re-open check.  On a miss it adds what tells the causes apart: the launch
        results, the SAM foreground app and running list, the Inspector error and the failed-read count;
        'no DOM read' instead of a DOM when no read ever gave one."""
        dom = json.dumps(info['again']) if info['dom'] else 'no DOM read'
        text = 'ticked rows: %r; DOM: %s' % (ticked, dom)
        launch_failed = any(' rc=0 ' not in n for n in info['launch'])
        if ok and not launch_failed:
            return text
        try:
            fg = self.dev.foreground()[0]
        except Exception as exc:
            fg = 'unreadable (%r)' % (exc,)
        try:
            running = APP_ID in self.running_ids()
        except Exception as exc:
            running = 'unreadable (%r)' % (exc,)
        return '%s; %s%s; SAM foreground: %r; %s in SAM running: %s; Inspector last_error: %s; reads failed: %d of %d' \
            '; Inspector recreated: %d' % (
                text, '; '.join(info['launch']), ' (LAUNCH FAILED)' if launch_failed else '', fg, APP_ID, running,
                getattr(self.insp, 'last_error', None) if self.insp else 'no inspector', info['failed'], info['reads'],
                info['recreated'])

    def focus_button(self, label, first=True):
        """Move focus inside our dialog to the button `label` (buttons sit in a row: the confirm one
        first, LEFT; the cancel one last, RIGHT).  True when focus is on it.  No key is pressed once the
        page cannot be read (the app may have been closed: the next key would go to whatever is beneath)."""
        for k in ['LEFT', 'UP'] if first else ['RIGHT', 'DOWN']:
            ui = self.ui()
            if ui is None:
                return False
            if ui.get('text') == label:
                return True
            self.press(k)
        return (self.ui() or {}).get('text') == label

    def page_state(self):
        """The Home Chooser page's own focus/visibility/url, or None when it cannot be read."""
        v = self.insp.evaluate(PAGE_JS) if self.insp else None
        try:
            return json.loads(v) if v else None
        except ValueError:
            return None

    def confirm_home_choice(self, clicking):
        # A successful toast lasts six seconds. Capture it while checking the click's
        # effect: a fresh OCR frame can take longer than the toast's entire lifetime.
        toasts = []

        def chosen():
            u = self.ui() or {}
            if u.get('toast'):
                toasts.append(u['toast'])
            return self.state()['choice'] == self.launcher

        if clicking:
            self.click_element('Use as Home', effect=chosen, label='Use as Home', read_after=False)
        else:
            self.press('OK')
        self.wait(chosen, 90, 2)
        return toasts

    def ensure_page(self, label):
        """Before keys go to the list: our page must be readable.  surface-manager closes an overlay window
        (Home Chooser's appinfo defaultWindowType) after a stretch without key input (6.5.2: about 90 s
        after the launch with no key, about 120 s after the last key; a relaunch alone does not reset it;
        logged as getAppLifeStatus closing reason userTimeout).  If the app was closed, why is noted and it
        is opened once more; no key is pressed at whatever is beneath.  True when the list is there."""
        if self.ui() is not None:
            return True
        self.rec.note('%s: our page is gone before the keys; opening Home Chooser again' % label,
                      self.page_gone_evidence(getattr(self, 'launch_mark', '') or ''))
        self.launch_mark = self.messages_mark()
        self.dev.launch(APP_ID)
        return bool(self.wait(lambda: (lambda u: u if u and self.launcher in (u.get('rows') or []) else None)(self.ui()),
                              60, 1))

    def page_gone_evidence(self, mark):
        """Why our page went away since `mark`: the inspector's reason, whether sam still runs the app,
        and the sam/WAM lines about closing it (windowPolicy when a card is put under our overlay, e.g.
        the stock home relaunched as the last-input app after a close; userTimeout after a stretch with
        no input) and about the stock home being killed and relaunched."""
        why = getattr(self.insp, 'last_error', None) if self.insp else 'no inspector'
        running = APP_ID in self.running_ids()
        rc, out = self.dev.sh(
            (messages_command() + " | awk '$1 >= \"%s\"' | grep -E '%s.*(killApp|windowPolicy|userTimeout|\"event\":\"close\")"
            "|com.webos.app.home.*(kill_process|APP_CLOSED)|NL_APP_LAUNCH_BEGIN.*com.webos.app.home' | tail -n 10") % (mark, APP_ID))
        return 'inspector: %s; %s running: %s\n%s' % (why, APP_ID, running, out.strip() or 'no close/kill line since %s' % mark)

    def ours_in_front(self, dialog):
        """The Home Chooser inspector page shows its own ConfirmDialog by aria-label.

        On webOS 9 the app's dialog is rendered over the stock home, so the foreground app id can
        remain com.webos.app.home even while the Home Chooser screen and its focused dialog are
        visibly on screen. The inspector selects the page by Home Chooser's app id; use that page's
        dialog DOM as the ownership check, then verify directional focus movement before OK.
        """
        page = self.insp._page() if self.insp else None
        ui = self.ui() or {}
        ps = self.page_state() or {}
        title = ui.get('dialog') or ''
        matches = title == dialog if not dialog.endswith('*') else title.startswith(dialog[:-1])
        ok = bool(page and APP_ID in page.get('url', '') and APP_ID in (ps.get('href') or '') and matches
                  and ps.get('focus') is True and ps.get('visibility') == 'visible')
        return ok, 'inspector page %r; dialog %r; focus %r; document.hasFocus %r, visibility %r; foreground %s (on 9.x our ' \
                   'overlay leaves the card beneath as the foreground app)' % (
            page.get('url') if page else None, title, ui.get('text'), ps.get('focus'), ps.get('visibility'),
            self.dev.foreground()[0])

    def reboot_ask_log(self):
        """The REBOOT_ASK lines of the service since the choice was confirmed (syslog and journal)."""
        rc, out = self.dev.sh(service_log_command(getattr(self, 'ask_mark', ''),
                                                   'REBOOT_ASK') + " | tail -n 8")
        return out

    def reboot_ask_evidence(self):
        """Prefer the service log; use the observed notification call when PmLog discards info lines."""
        raw = self.reboot_ask_log()
        info = parse_reboot_ask(raw)
        if not info['alert'] and getattr(self, 'ask_monitor', None):
            observed = self.ask_monitor.snapshot()
            if observed['alert']:
                return observed, 'LS2 notification call/reply: ' + json.dumps(observed)
            return info, raw or 'no REBOOT_ASK line or notification bus evidence; bus events: ' + self.ask_monitor.diagnostic()
        return info, raw or 'no REBOOT_ASK line'

    def close_reboot_alert(self, label, info):
        """Our restart alert: closed over luna with the alertId from its createAlert reply (the same as the
        removal alert; no key is pressed, so no foreign popup can take it).  True when closeAlert was accepted."""
        r = self.rec
        if info['alert'] != 'accepted' or not info['alert_id']:
            r.note('%s: no accepted createAlert with an alertId in the prompt evidence; nothing to close' % label,
                   json.dumps(info))
            return False
        out = self.priv_luna('luna://com.webos.notification/closeAlert', '{"alertId":"%s"}' % info['alert_id'])
        good = '"returnValue":true' in out.replace(' ', '')
        r.check('%s: closeAlert on our restart alert (-a com.webos.applicationManager) was accepted' % label, good,
                'alertId %s\nreply: %s' % (info['alert_id'], out))
        return good

    def reboot_after_choice(self, comp0):
        """webOS 6-9, the patch not mounted: the choice mounts nothing and restarts nothing; the app asks the
        service for a system alert (Reboot / Later) and says so in a toast.  The runner checks, from the
        service's REBOOT_ASK log lines or notification bus events, that a createAlert was sent with the buttons
        Reboot and Later (or, when the alert was refused, that the toast fallback was shown).

        --reboot-via later (default): our alert is closed with closeAlert and the VM is restarted with
        beanjar stop/start.  No key is pressed.
        --reboot-via alert: prove ownership and exact buttons with Beanjar elements, click Reboot,
        and verify the boot changed. Keys retain the two-frame focus proof and human fallback."""
        r, d = self.rec, self.dev
        toasts = []

        def asked():
            u = self.ui() or {}
            if u.get('toast') and u['toast'] not in toasts:
                toasts.append(u['toast'])  # gone after 6 s
            info, _ = self.reboot_ask_evidence()
            return info if info['alert'] else None
        info = self.wait(asked, 90, 2) or self.reboot_ask_evidence()[0]
        if info['alert'] == 'refused':
            self.wait(lambda: self.reboot_ask_evidence()[0]['toast'] == 'accepted', 40, 3)
            info = self.reboot_ask_evidence()[0]
        ui = self.ui()
        if not info['alert'] and self.a.update_check and d.mounts():
            r.note('no restart prompt and the patch is mounted: the OLD build of --update-check mounts live (before 144ef21)', '')
            return
        self.shot('2-reboot-alert')
        info, raw_log = self.reboot_ask_evidence()
        r.check('6-9, patch not mounted: the service sent a createAlert for the restart prompt with the buttons %s and %s'
                % REBOOT_BUTTONS, info['message'] is not None and info['buttons'] == list(REBOOT_BUTTONS) and bool(info['alert']),
                raw_log or 'no REBOOT_ASK line')
        r.check('the restart prompt says "%s"' % REBOOT_PROMPT['apply'], info['message'] == REBOOT_PROMPT['apply'], repr(info['message']))
        if info['alert'] == 'accepted':
            r.check('the user was told: the restart alert was accepted by the notification service', True, raw_log)
            r.note('look at screenshot 2-reboot-alert: an alert "Home Chooser" with "%s" and the buttons Reboot and Later '
                   '(focus on Later)' % REBOOT_PROMPT['apply'], '')
        else:
            r.check('the alert was refused, so the fallback toast was shown (createToast accepted)',
                    info['alert'] == 'refused' and info['toast'] == 'accepted', raw_log)
        if ui is None:
            r.note('Home Chooser page not readable after the choice (an overlay is closed after a stretch without keys)',
                   self.page_gone_evidence(getattr(self, 'launch_mark', '') or ''))
        else:
            r.check('the app shows no restart dialog of its own', not ui.get('dialog'), json.dumps(ui))
            r.check('the app lists a "%s" row while the restart is pending' % RESTART_NOW, ui.get('restart') is True, json.dumps(ui))
            r.check('the app told "%s" in a toast (toasts seen: %s)' % (REBOOT_ASKED_TEXT, toasts),
                    REBOOT_ASKED_TEXT in toasts or ui.get('toast') == REBOOT_ASKED_TEXT, repr(toasts))
        s = self.state()
        st, raw = self.kf_status()
        r.note('state with the restart prompt up', json.dumps(s))
        r.check('before the reboot: homekey status says active=no and reboot=needed',
                st.get('active') == 'no' and st.get('reboot') == 'needed', raw)
        r.check('before the reboot: nothing is mounted', not s['mounts'], 'mounts: %s' % s['mounts'])
        r.check('before the reboot: the flag file and the init.d entry are written',
                s['flag'] == 'yes' and s['initd'] == BOOT_SCRIPT, 'flag %s; init.d %s' % (s['flag'], s['initd']))
        r.check('before the reboot: the record home is the launcher', s['record_home'] == self.launcher, s['record_raw'])
        self.check_no_compositor_restart('choosing the launcher', comp0)
        boot0 = d.boot_id()
        self.close_insp()
        observed = self.ask_monitor.snapshot() if getattr(self, 'ask_monitor', None) else None
        if getattr(self, 'ask_monitor', None):
            self.ask_monitor.close()
            self.ask_monitor = None
        if self.a.reboot_via == 'alert':
            def service_proof():
                raw = self.reboot_ask_log()
                return raw + '\nLS2: ' + json.dumps(observed), alert_service_proof(raw, REBOOT_PROMPT['apply'], 'Reboot', 'Later', observed=observed)
            def request_again():
                nonlocal observed
                observed = None
                self.ask_mark = self.messages_mark()
                d.launch(APP_ID)
                sleep(3)
                self.click_element(RESTART_NOW, label='retry Restart now')
                self.wait(lambda: self.reboot_ask_evidence()[0]['alert'] == 'accepted', 30, 1)
            outcome = None
            def verify_reboot():
                nonlocal outcome
                outcome = self.wait_reboot(boot0, timeout=40 if self.use_click() else 240)
                return outcome if outcome in ('rebooted', 'off') else False
            automatic = self.alert_action(REBOOT_PROMPT['apply'], 'Reboot', 'Later',
                                          '2-reboot-alert', service_proof, verify_reboot,
                                          lambda: not any(e.get('text') == REBOOT_PROMPT['apply']
                                              for e in self.elements('reboot-result')['elements']), request_again,
                                          before_action=lambda: self.prepare_tvpower_reboot('2 reboot alert'))
            if not automatic:
                r.manual('press Reboot on the restart alert', ('automatic press declined: ' + self.alert_decline_reason + '. Press Reboot by hand within ') +
                     '240 s; otherwise the alert is closed and the VM is restarted with beanjar stop/start.')
            how = outcome if automatic else self.wait_reboot(boot0)
            if how in ('rebooted', 'off'):
                r.check('Reboot restarted the TV%s (alert -> tvpower/power/reboot; a VM that powers off counts)'
                        % (' (automatic press)' if automatic else ''), True, how)
                if how == 'off':
                    r.note('the VM powered off instead of rebooting (QEMU -no-reboot?); starting it with beanjar', '')
                    self.host.start(self.vm)
                    up = self.await_boot('2 reboot (Reboot on the alert; VM started again)', boot0)
                else:
                    up = self.await_boot('2 reboot (Reboot on the alert)', boot0)
            else:
                r.manual('restart alert wait timed out',
                         ('automatic press declined: ' + self.alert_decline_reason + '; ' if not automatic else '')
                         + 'no new boot id within 240 s')
                if how == 'unreachable':
                    r.note('closeAlert skipped: the guest is unreachable after the restart attempt', info.get('alert_id'))
                else:
                    self.close_reboot_alert('2 reboot', info)
                self.prepare_tvpower_reboot('2 reboot fallback')
                up = self.beanjar_reboot('2 reboot (alert left alone, beanjar stop/start)')
        else:
            self.close_reboot_alert('2 reboot (Later)', info)
            sleep(3)
            self.shot('2-reboot-alert-closed')
            self.prepare_tvpower_reboot('2 reboot Later')
            up = self.beanjar_reboot('2 reboot (alert closed, then beanjar stop/start)')
        if not up:
            raise RuntimeError('the VM did not come back after the reboot')
        self.after_boot_checks('after the reboot')

    def after_boot_checks(self, label):
        """6-9 boot applies the volatile JS filter and starts the watcher without a GUI restart."""
        r = self.rec
        active = self.wait(lambda: self.watcher_active() == 'active', 180, 5)
        r.check(label + ': the watcher is active (started from init.d)', bool(active), self.watcher_active())
        self.wait(lambda: self.hook_mounts_match(), 180, 5)
        st, raw = self.kf_status()
        r.check(label + ': the boot hook mounted the patch: status active=yes and reboot=no',
                st.get('active') == 'yes' and st.get('reboot') == 'no', raw)
        r.note(label + ': native boot apply log (no GUI restart permitted)',
               self.dev.sh("grep -E 'HomeChooser.*(START|HOMEKEY)' /var/log/messages | tail -n 25")[1])
        r.note(label + ': compositor QML and crash evidence from retained messages logs', self.dev.sh(
            messages_command() + " | grep -E 'surface-manager.*(WebOSCompositorWindow::setCompositorMain|"
            "Type .*unavailable|Syntax error|is not a type)|crashd.*CRASHED|WebAppMgr.*(SIGSEGV|segfault)'"
            " | tail -n 24")[1].strip() or 'no matching retained line')

    def close_insp(self):
        if self.insp:
            self.insp.close()
            self.insp = None

    def check_device_texts(self):
        """The installed build carries the texts of 144ef21/34a4ff9 and none of the old restart wording:
        the watcher (app copy and the running /tmp copy), the service binary, the app bundle."""
        r, d = self.rec, self.dev
        notify = [SCRIPTS_DIR + '/../homechooser-daemon', '/tmp/homechooser-runtime/homechooser-daemon']
        restore = notify
        svcdir = '/media/developer/apps/usr/palm/services/' + SVC_ID

        def has(text, paths):
            found = set()
            for form in device_text_forms(text):
                rc, out = d.sh('grep -rlF %s %s 2>/dev/null' % (shlex.quote(form), ' '.join(paths)))
                found.update(l for l in out.split() if l.startswith('/'))
            return sorted(found)
        for text, sources in ((REMOVAL_ALERT[2:], notify), (UNINSTALL_TOAST, restore)):
            got = has(text, sources)
            r.check('device texts: the watcher says "%s"' % text.strip(), len(got) == 2, 'found in %s' % got)
        old = [(t, has(t, [SCRIPTS_DIR + '/../homechooser-daemon', '/tmp/homechooser-runtime/homechooser-daemon', APPDIR])) for t in OLD_RESTART_TEXTS]
        r.check('device texts: no old restart wording in the watcher or the app', not any(g for _, g in old),
                '; '.join('%r in %s' % (t, g) for t, g in old if g) or 'none')
        for reason, text in sorted(REBOOT_PROMPT.items()):
            got = has(text, [svcdir])
            r.check('device texts: the service reboot prompt (%s) is "%s"' % (reason, text), bool(got), 'found in %s' % got)
        for text in list(RESTART_NOTE.values()) + [REBOOT_ASKED_TEXT, RESTART_NOW]:
            got = has(text, [APPDIR])
            r.check('app contains no reboot instruction: ' + text, not got, repr(got))

    def step3(self):
        self.rec.begin('3 Home opens the launcher')
        # Record the current foreground without sending a key. A Home-key proof follows only after
        # the usual foreign-popup check in home_press().
        fg0 = self.dev.foreground()
        self.shot('3-home-from-stock-home')
        self.rec.note('foreground before the Home-key proof: %s' % fg0[0],
                      'see screenshot 3-home-from-stock-home')
        self.home_press(self.launcher, '3-home')
        self.residency('after the Home press')
        if self.major >= 11:
            def deferred_close():
                return 'closeByAppId %s after %s in front' % (STOCK, self.launcher) in self.dev.sh(
                    'cat /tmp/homechooser-home.log 2>&1')[1]
            closed = self.wait(deferred_close, 60, 3)
            self.rec.check('watcher closed LG Home after the chosen launcher was in front', bool(closed),
                           self.dev.sh('tail -n 12 /tmp/homechooser-home.log')[1])
            self.wait(lambda: STOCK not in self.running_ids(), 30, 3)
            self.rec.check('LG Home has no live process after the launcher was in front',
                           STOCK not in self.running_ids(), repr(self.running_ids()))
        r, d = self.rec, self.dev
        rc, out = d.sh('ps -eo args | grep -c "[%s]"' % self.launcher[:1] + self.launcher[1:])
        r.note('processes mentioning the launcher id', out.strip())

    def step_reboot(self):
        """Optional (--reboot-check), launcher chosen: after a boot the watcher re-applied the policy
        (it is never persisted) and the stock home is not running.  On 6.x to 9.x
        snapshot-boot-components launches the stock home at boot, outside the preload manager; the
        watcher's delayed close (45 s) is what removes it.  5.x: no policy call is expected."""
        r, d, host = self.rec, self.dev, self.host
        r.begin('3r reboot with the launcher chosen (--reboot-check)')
        self.prepare_tvpower_reboot('3r reboot')
        up = self.beanjar_reboot('3r')
        if not up:
            return
        self.verify_reboot_apply()

    def check_gate_front(self, label):
        """6.x to 9.x after a boot: the gate (mounted, stock Home not running: check_home_gate) put the
        chosen full-screen launcher in front; for an overlay launcher the last input app is in front."""
        r = self.rec
        overlay = self.L.overlay
        want = lambda fg: bool(fg) and fg != STOCK and (overlay or fg == self.launcher)
        ok = self.wait(lambda: want(self.dev.foreground()[0]), 180, 5)
        fg = self.dev.foreground()
        r.check('%s: %s in front (the gate ran at boot)' % (
                label, 'the last input app (overlay launcher)' if overlay else 'the chosen launcher ' + self.launcher),
                bool(ok), fg[1])
        r.check(label + ': the gate is mounted and the real stock Home is not running',
                self.gate_mount_count() >= 1 and STOCK not in self.running_ids(), repr(self.running_ids()))

    def verify_reboot_apply(self):
        r, d = self.rec, self.dev
        if self.uses_home_key_hook():
            self.after_boot_checks('after the boot')  # the watcher, the boot hook's mounts, status
        else:
            active = self.wait(lambda: self.watcher_active() == 'active', 180, 5)
            r.check('after the boot the watcher is active (started from init.d)', bool(active), self.watcher_active())
        s = self.state()
        r.check('after the boot the choice and the record are still the launcher',
                s['choice'] == self.launcher and s['record_home'] == self.launcher, s['record_raw'])
        if self.major >= 6:
            # the call comes at once, the close after the 45 s settle
            self.wait(lambda: 'isEnabled=false' in self.preload_log(), 150, 5)
            log = self.preload_log()
            r.check('after the boot the watcher called setPreloadPolicy isEnabled=false (log is from this boot: /tmp)',
                    'isEnabled=false' in log, log)
        else:
            r.check('after the boot (5.x) the watcher made no preload call', 'setPreloadPolicy' not in self.preload_log(),
                    self.preload_log())
        self.log_mark = 0  # a new boot, a new /tmp log
        self.check_preload_off('after the reboot', settle=30)
        if uses_home_gate(self.major):
            self.check_gate_front('after the reboot')
        if self.uses_home_key_hook():
            self.check_hook_status('after reboot')
        self.residency('after the reboot')
        self.shot('3r-after-reboot')
        if self.a.api_check:
            self.api_after_reboot()

    def cache_prepare_measure(self):
        """Validate the native generator without a Node process or prepared boot cache."""
        rc, out = self.dev.sh('/bin/sh ' + SCRIPTS_DIR + '/homechooser-homekey prepare')
        self.rec.check('native generator accepts this firmware', rc == 0, out)

    def step_cache(self):
        """The former cache scenario now verifies JS-only activation after boot."""
        self.after_boot_checks('native live-filter boot')
        rc, out = self.dev.sh('test ! -d /var/lib/webosbrew/homechooser/hook && echo no-cache; '
                              'test ! -e /var/preferences/configd_factory_db.json && echo no-factory-db')
        self.rec.check('no boot cache and no persistent configd override',
                       rc == 0 and 'no-cache' in out and 'no-factory-db' in out, out)
        self.home_press(self.launcher, 'native-filter-home')

    # -- the launcher API (--api-check; docs/launcher-api.md)
    def api(self, method, payload=None, app='launcher'):
        """(parsed reply, raw) of a launcher-API call as `app` (default: the launcher under test; None: no
        application id, as a plain shell luna-send)."""
        app = self.launcher if app == 'launcher' else app
        rc, raw, rep = self.dev.luna_as(app, 'luna://%s/%s' % (SVC_ID, method), json.dumps(payload or {}))
        return rep, raw

    def api_conforms(self, method, label, payload=None):
        rep, raw = self.api(method, payload)
        probs = api_reply_problems(method, rep)
        self.rec.check('%s: %s as %s has the documented fields and types' % (label, method, self.launcher), not probs,
                       '%s\n%s' % ('; '.join(probs) or 'conforms', raw[:800]))
        return rep or {}

    def api_kit_note(self):
        if self.L.is_fixture:
            return 'the web fixture built with requiredPermissions [%s]' % API_GROUP
        return 'an author launcher (%s, ipk %s): fixture-only parts are skipped' % (self.launcher, self.L.ipk)

    def api_new_methods(self, label):
        """Exercise the v1 refresh, foreground and icon calls with real installed apps."""
        r = self.rec
        def icon_result(info, field, reply):
            icon, folder = info.get(field), info.get('folderPath')
            if not icon or not folder:
                return (api_refused(reply) and reply.get('errorCode') == 'noIcon'
                        and reply.get('apiVersion') == API_VERSION), 'appinfo has no %s' % field
            path = os.path.join(folder, icon)
            _, presence = self.dev.sh('if test -f %s; then echo present; else echo missing; fi' % shlex.quote(path))
            if presence.strip().endswith('missing'):
                return (api_refused(reply) and reply.get('errorCode') == 'noIcon'
                        and reply.get('apiVersion') == API_VERSION), 'appinfo path is missing: %s' % path
            if not presence.strip().endswith('present'):
                return False, 'could not inspect appinfo path: %s (%s)' % (path, presence[:200])
            return not api_reply_problems('getAppIcon', reply), 'appinfo path exists: %s' % path
        start = time.monotonic()
        foreground, raw = self.api('getForegroundApp')
        elapsed = (time.monotonic() - start) * 1000
        r.check('%s: getForegroundApp contract' % label,
                not api_reply_problems('getForegroundApp', foreground), raw[:400])
        platform_id, platform_raw = self.dev.foreground()
        r.check('%s: getForegroundApp matches platform foreground' % label,
                (foreground or {}).get('appId') == platform_id,
                'service=%r platform=%r; platform reply=%s' % ((foreground or {}).get('appId'), platform_id, platform_raw[:400]))
        r.note('%s: getForegroundApp timing and reply size' % label,
               '%.1f ms; %d bytes; %s' % (elapsed, len(raw.encode()), raw[:400]))
        for appid in (STOCK, 'org.webosbrew.hbchannel', self.launcher):
            if not self.dev.app_installed(appid):
                continue
            app_info = None
            for method, payload in (('getAppInfo', {'id': appid}), ('getAppIcon', {'id': appid})):
                start = time.monotonic()
                reply, raw = self.api(method, payload)
                elapsed = (time.monotonic() - start) * 1000
                problems = api_reply_problems(method, reply)
                ok, evidence = icon_result(app_info or {}, 'icon', reply) if method == 'getAppIcon' else (not problems, '')
                r.check('%s: %s %s contract' % (label, method, appid), ok,
                        '; '.join(problems) if problems and not ok else
                        '%s; %d reply bytes' % (evidence, len(raw.encode())))
                r.note('%s: %s %s timing and size' % (label, method, appid),
                       '%.1f ms; reply=%d bytes; icon=%s bytes; %s' %
                       (elapsed, len(raw.encode()), (reply or {}).get('byteLength', '-'),
                        raw[:400] if method == 'getAppInfo' else repr((reply or {}).get('mimeType'))))
                if method == 'getAppInfo':
                    app_info = (reply or {}).get('app') or {}
            if app_info is not None:
                reply, raw = self.api('getAppIcon', {'id': appid, 'size': 'largeIcon'})
                if appid == self.launcher and self.L.is_fixture and app_info.get('largeIcon') == 'oversize.png':
                    r.check('%s: getAppIcon rejects the oversized fixture largeIcon' % label,
                            api_refused(reply) and reply.get('errorCode') == 'iconTooLarge'
                            and reply.get('apiVersion') == API_VERSION, raw[:400])
                elif app_info.get('largeIcon'):
                    ok, evidence = icon_result(app_info, 'largeIcon', reply)
                    r.check('%s: getAppIcon largeIcon %s contract' % (label, appid),
                            ok, '%s; icon=%s bytes; reply=%d bytes' %
                            (evidence, (reply or {}).get('byteLength', '-'), len(raw.encode())))
                else:
                    r.check('%s: getAppIcon largeIcon %s has no fallback' % (label, appid),
                            api_refused(reply) and reply.get('errorCode') == 'noIcon'
                            and reply.get('apiVersion') == API_VERSION, raw[:400])
        if self.major == 9 and label == 'before selection' and self.L.is_fixture:
            # This one-shot method has no target parameter: put each sampled app in front first.
            try:
                for appid in (STOCK, 'org.webosbrew.hbchannel', self.launcher):
                    self.probe_foreground(appid)
            finally:
                self.life_end()

    def probe_foreground(self, appid):
        """Put appid in front and compare getForegroundApp with the platform.  A card app is named by SAM's
        getForegroundAppInfo.  An overlay launcher is not: SAM keeps naming the card beneath while
        getAppLifeEvents emits `foreground` with _WEBOS_WINDOW_TYPE_OVERLAY, so for it the overlay is proven
        by that life event and the service must agree with SAM's reply at that moment (the card beneath)."""
        r = self.rec
        overlay = appid == self.launcher and self.L.overlay
        if overlay:
            self.life_begin()
        rc, out = self.dev.launch(appid)
        r.check('getForegroundApp probe launches %s' % appid, rc == 0, out.strip()[-400:])
        if overlay:
            def came_up():
                ev, _ = self.life_events(appid)
                return [line for kind, line in ev if kind == 'foreground' and '_WEBOS_WINDOW_TYPE_OVERLAY' in line]
            seen = self.wait(came_up, 20, 2)
            r.check('getForegroundApp probe sees overlay %s in front (rule: life-event foreground with '
                    '_WEBOS_WINDOW_TYPE_OVERLAY; SAM getForegroundAppInfo keeps the card beneath)' % appid,
                    bool(seen), (seen[-1] if seen else 'no overlay foreground event: %r' % (self.life_events(appid)[0][-4:],)))
        else:
            platform = self.wait(lambda: self.dev.foreground()[0] == appid, 20, 2)
            r.check('getForegroundApp probe sees %s in front' % appid, bool(platform), self.dev.foreground()[1][:400])
        start = time.monotonic()
        reply, raw = self.api('getForegroundApp')
        elapsed = (time.monotonic() - start) * 1000
        platform_id, platform_raw = self.dev.foreground()
        if overlay:
            rule = 'overlay rule: service appId must equal SAM getForegroundAppInfo now (%r), the card beneath' % platform_id
            ok = (reply or {}).get('appId') == platform_id
        else:
            rule = 'card rule: service appId must equal %r' % appid
            ok = (reply or {}).get('appId') == appid
        r.check('getForegroundApp %s contract and appId' % appid,
                not api_reply_problems('getForegroundApp', reply) and ok,
                '%s; SAM: %s; service: %s' % (rule, platform_raw[:200], raw[:400]))
        r.note('getForegroundApp %s timing and reply size' % appid,
               '%.1f ms; %d bytes; %s' % (elapsed, len(raw.encode()), raw[:400]))

    def api_before_selection(self):
        """Home Chooser open and elevated, the launcher installed but not chosen."""
        r, d = self.rec, self.dev
        r.begin('2a launcher API before the launcher is chosen (--api-check)')
        r.note('API caller: ' + self.api_kit_note(), '')
        ai = self.L.appinfo
        perm, home = API_GROUP in (ai.get('requiredPermissions') or []), 'home' in (ai.get('appCategory') or [])
        r.check('the launcher appinfo declares requiredPermissions ["%s"] and appCategory "home"' % API_GROUP, perm and home,
                'requiredPermissions %r; appCategory %r' % (ai.get('requiredPermissions'), ai.get('appCategory')))
        r.note('launcher access files (homechooser-access status; the app applies them when it loads)',
               d.exec('sh %s/homechooser-access status 2>&1' % SCRIPTS_DIR)[1].strip())
        if self.major >= 11:
            _, raw = d.sh('for root in /var/luna-service2-dev /var/luna-service2; do '
                          'file="$root/api-permissions.d/org.webosbrew.homechooser.launcher.api.json"; '
                          'test ! -f "$file" || cat "$file"; done')
            files = []
            for line in raw.splitlines():
                try:
                    files.append(json.loads(line))
                except ValueError:
                    pass
            methods = {SVC_ID + '/' + name for name in
                       ('getInfo', 'listApps', 'listRunning', 'getForegroundApp', 'getAppInfo',
                        'getAppIcon', 'closeApp', 'uninstallApp')}
            r.check('11+: namespaced permission and ares.webos.cli grant all eight launcher methods',
                    bool(files) and all(set(f.get(API_GROUP, [])) == methods
                                        and set(f.get('ares.webos.cli', [])) == methods
                                        and 'homechooser.applications' not in f for f in files), raw[:1200])
        # the app's accessApply runs while the list loads: allow it a moment (listApps needs the grant)
        self.wait(lambda: (self.api('listApps')[0] or {}).get('returnValue') is True, 30, 3)
        info = self.api_conforms('getInfo', 'before selection')
        r.check('before selection: getInfo says root true, permitted true, isSelectedHome false',
                info.get('root') is True and info.get('permitted') is True and info.get('isSelectedHome') is False, json.dumps(info))
        apps = self.api_conforms('listApps', 'before selection')
        ids = [a.get('id') for a in apps.get('apps') or [] if isinstance(a, dict)]
        r.check('listApps names the launcher and the disposable app', self.launcher in ids and DISPOSABLE_ID in ids,
                '%d apps; launcher %s, disposable %s' % (len(ids), self.launcher in ids, DISPOSABLE_ID in ids))
        self.api_conforms('listRunning', 'before selection')
        self.api_new_methods('before selection')
        rep, raw = self.api('getAppIcon', {'id': DISPOSABLE_ID, 'size': 'largeIcon'})
        r.check('getAppIcon of an app without largeIcon has no fallback',
                api_refused(rep) and rep.get('errorCode') == 'noIcon'
                and rep.get('apiVersion') == API_VERSION, raw[:400])
        # an installed app without the permission
        for m, payload in (('listApps', {}), ('listRunning', {}), ('getForegroundApp', {}),
                           ('getAppInfo', {'id': self.launcher}), ('getAppIcon', {'id': self.launcher}),
                           ('closeApp', {'id': APP_ID}), ('uninstallApp', {'id': self.launcher})):
            rep, raw = self.api(m, payload, app=DISPOSABLE_ID)
            expected = 'notHome' if m in ('closeApp', 'uninstallApp') else 'noPermission'
            r.check('%s as %s fails with %s (check order)' % (m, DISPOSABLE_ID, expected),
                    api_refused(rep) and rep.get('errorCode') == expected, raw[:400])
        rep, raw = self.api('getInfo', app=DISPOSABLE_ID)
        r.check('getInfo as the app without the permission: permitted false, or refused by the hub',
                api_refused(rep) or (rep or {}).get('permitted') is False, raw[:400])
        rep, raw = self.api('listApps', app=None)
        r.check('listApps without an application id (a plain root luna-send) is refused', api_refused(rep), raw[:400])
        for m in ('closeApp', 'uninstallApp'):
            rep, raw = self.api(m, {'id': DISPOSABLE_ID})
            r.check('%s as %s while it is not the selected Home: refused with notHome' % (m, self.launcher),
                    api_refused(rep) and rep.get('errorCode') == 'notHome', raw[:400])
        rep, raw = self.api('uninstallConfirmed', {'token': '0' * 32})
        r.check('screen-only uninstallConfirmed as the launcher is refused',
                api_refused(rep), raw[:400])
        self.api_request(selected=False)

    # `beanjar elements` OCR takes tens of seconds on the rk boards (matrix-20261031-082841/w20l-jhericurl-rk1:
    # one call ran ~50 s), the app hides the toast after 6 s, so one OCR call must never eat the window:
    # it gets a short subprocess timeout and runs only every OCR_EVERY-th round; the DOM read is the main
    # source.  The values are unscaled seconds (run() applies SLOWDOWN).
    TOAST_OCR_TIMEOUT = 4
    TOAST_OCR_EVERY = 3
    CONNECTION_ERROR = re.compile(r'reset|no page|websocket|exited|connection|refused|forward|no frame|raised', re.I)

    def see_toast(self, needle, timeout):
        """Look for a toast (the app hides it after 6 s) in two independent places: the DOM through the
        Inspector every round, and the screen through `beanjar elements` OCR every TOAST_OCR_EVERY-th round
        with a TOAST_OCR_TIMEOUT cap.  The Inspector is closed and recreated after every connection-type
        failure (reset, no page, websocket), as often as the overall deadline allows.
        Returns (text, source) with source 'DOM' or 'screen', or (None, <why not>) carrying the whole
        timeline (each round's source, elapsed seconds and error) when it never showed."""
        start = time.time()
        end = start + seconds(timeout)
        timeline, rnd = [], 0

        def note(source, t0, error=None, extra=''):
            entry = '#%d %s %.1fs%s%s' % (rnd, source, time.time() - t0, ' err=%s' % str(error)[:160] if error else '',
                                          ' ' + extra if extra else '')
            timeline.append('t+%.1fs %s' % (t0 - start, entry))
            self.rec.say('toast check: ' + timeline[-1])

        while True:
            rnd += 1
            if self.insp is None:
                self.insp = Inspector(self.vm)
            t0 = time.time()
            try:
                ui = self.ui()
            except Exception as exc:
                ui = None
                self.insp.last_error = 'ui() raised %r' % (exc,)
            text = (ui or {}).get('toast')
            if text and needle in text:
                return text, 'DOM'
            err = getattr(self.insp, 'last_error', None)
            note('DOM', t0, err if ui is None else None, '' if ui is None else 'toast=%r' % text)
            if ui is None and err and self.CONNECTION_ERROR.search(str(err)) and time.time() < end:
                t0 = time.time()
                self.close_insp()
                self.insp = Inspector(self.vm)
                note('inspector-recreate', t0, err)
            remaining = end - time.time()
            if remaining > 1 and (rnd - 1) % self.TOAST_OCR_EVERY == 0:
                t0 = time.time()
                cap = min(self.TOAST_OCR_TIMEOUT, remaining / SLOWDOWN)
                error, rows = None, []
                try:
                    rc, raw = self.host.display_beanjar('elements', self.host.remote_target(self.vm), '--json',
                                                        timeout=cap)
                    if rc:
                        error = 'elements rc=%s%s' % (rc, ' (cut at %.1fs)' % seconds(cap) if rc == 124 else '')
                    else:
                        for row in json.loads(raw).get('elements', []):
                            t = row.get('text') or ''
                            if row.get('kind') == 'text':
                                rows.append(t)
                            if row.get('kind') == 'text' and needle.lower() in t.lower():
                                note('OCR', t0, extra='match %r box %s' % (t, row.get('box')))
                                return t, 'screen'
                except Exception as exc:
                    error = 'elements failed: %r' % (exc,)
                note('OCR', t0, error, '' if error else 'no match; rows: %s' % ' | '.join(rows)[:300])
            if time.time() >= end:
                return None, 'none (DOM last_error=%s; timeline: %s)' % (getattr(self.insp, 'last_error', None),
                                                                        '; '.join(timeline))
            sleep(0.5)

    def api_request(self, selected):
        """The `request` launch parameter: Home Chooser opens "Use <title> as Home?" for the requesting
        launcher (cancelled with Back, on our own dialog); once it is Home, the "already your Home" toast."""
        r, d = self.rec, self.dev
        choice0 = self.state()['choice']
        if selected:
            # Warm relaunch: Home Chooser still runs from an earlier step (the manual alert-uninstall path
            # closes neither it nor the Inspector), so the old forward may be stale.  Replace the Inspector,
            # never the app: the warm relaunch is a real user path.
            self.close_insp()
        if not self.insp:
            self.insp = Inspector(self.vm)
        rc, out = d.launch(APP_ID, {'request': self.launcher})
        r.check('ares-launch %s -p {"request":"%s"}' % (APP_ID, self.launcher), rc == 0, out.strip())
        if selected:
            toast, source = self.see_toast('already your Home', 30)
            self.shot('3a-request-selected')
            r.check('request while it is Home: the toast "<title> is already your Home."', bool(toast),
                    'source=%s: %r' % (source, toast))
            d.close(APP_ID)
            self.close_insp()
            return
        ui = self.wait(lambda: (lambda u: u if (u.get('dialog') or '').startswith('Use ') else None)(self.ui() or {}), 60, 1) or {}
        self.shot('2a-request-dialog')
        want = 'Use %s as Home?' % self.L.title
        r.check('request: Home Chooser asks "%s"' % want, ui.get('dialog') == want, json.dumps(ui))
        ok, why = self.ours_in_front('Use *')
        if not ok:
            r.check('request: the dialog is ours and in front (else no key is pressed)', False, why)
            return
        self.press('BACK')
        gone = self.wait(lambda: not (self.ui() or {}).get('dialog'), 10, 1)
        r.check('request: Back cancels the dialog', bool(gone), json.dumps(self.ui()))
        sleep(2)
        r.check('request cancelled: the choice is unchanged', self.state()['choice'] == choice0, repr(self.state()['choice']))

    def api_close_probe(self, appid, label):
        """Observe SAM, the public list, process liveness and WAM pages around closeApp."""
        r, d = self.rec, self.dev
        def snapshot(prior=None):
            sam = parse_luna(d.luna('luna://com.webos.applicationManager/running')) or {}
            api, _ = self.api('listRunning')
            sam_row = next((x for x in sam.get('running', []) if x.get('id') == appid), None)
            api_row = next((x for x in (api or {}).get('running', []) if x.get('id') == appid), None)
            pids = {}
            for field in ('processid', 'webprocessid'):
                pid = str((sam_row or {}).get(field) or (prior or {}).get('pids', {}).get(field, {}).get('pid') or '')
                if pid.isdigit():
                    pids[field] = {'pid': pid, 'alive': 'alive=0' in d.sh('test -d /proc/%s; echo alive=$?' % pid)[1]}
            if not self.insp:
                self.insp = Inspector(self.vm)
            try:
                with urllib.request.urlopen('http://127.0.0.1:%d/json' % self.insp.port, timeout=5) as response:
                    pages = [x.get('url') for x in json.loads(response.read().decode()) if appid in x.get('url', '')]
            except Exception as error:
                pages = ['inspector error: %s' % error]
            wam = d.sh("ps -eo pid,ppid,args | grep '[W]ebAppMgr' | head -4")[1].strip()
            return {'sam': sam_row, 'listRunning': api_row, 'pids': pids, 'wamPages': pages, 'WebAppMgr': wam}
        before = snapshot()
        r.note('%s before closeApp' % label, json.dumps(before))
        rep, raw = self.api('closeApp', {'id': appid})
        r.check('%s closeApp accepts the target' % label,
                (rep or {}).get('returnValue') is True and rep.get('id') == appid
                and not api_reply_problems('closeApp', rep), raw[:400])
        self.wait(lambda: appid not in self.running_ids(), 20, 2)
        for process in before['pids'].values():
            pid = process['pid']
            self.wait(lambda: 'alive=1' in d.sh('test -d /proc/%s; echo alive=$?' % pid)[1], 15, 1)
        after = snapshot(before)
        r.note('%s after closeApp' % label, json.dumps(after))
        r.check('%s absent from SAM running and listRunning after closeApp' % label,
                before['sam'] is not None and before['listRunning'] is not None
                and after['sam'] is None and after['listRunning'] is None, json.dumps(after))
        return before, after

    def step_api(self):
        """The launcher is the selected Home (after step 3; on 6-9 after the boot that mounted the patch)."""
        r, d = self.rec, self.dev
        r.begin('3a launcher API with the launcher selected (--api-check)')
        comp0 = self.compositor_snapshot() if self.uses_home_key_hook() else None
        info = self.api_conforms('getInfo', 'selected')
        r.check('selected: getInfo says root true, permitted true, isSelectedHome true',
                info.get('root') is True and info.get('permitted') is True and info.get('isSelectedHome') is True, json.dumps(info))
        self.api_conforms('listApps', 'selected')
        self.api_conforms('listRunning', 'selected')
        self.api_new_methods('selected')
        # closeApp closes a running web app; record whether WAM retains its page or process.
        d.launch(DISPOSABLE_ID)
        running = self.wait(lambda: DISPOSABLE_ID in self.running_ids(), 30, 2)
        r.check('the disposable app is running before closeApp', bool(running), str(self.running_ids()))
        self.api_close_probe(DISPOSABLE_ID, 'web disposable')
        # The native fixture is optional; use it where a package was built and remove it afterward.
        native = sorted(glob.glob(os.path.join(FIXTURE_DIR, FIXTURE_ID % ('native', 'normal') + '_*.ipk')))
        if native and self.major == 9 and not getattr(self.a, 'device', None):
            native_id = FIXTURE_ID % ('native', 'normal')
            try:
                rc, out = d.install(native[-1])
                r.check('install native closeApp probe', rc == 0, out.strip()[-400:])
                if rc == 0:
                    rc, out = d.launch(native_id)
                    r.check('launch native closeApp probe', rc == 0, out.strip()[-400:])
                    self.wait(lambda: native_id in self.running_ids(), 30, 2)
                    self.api_close_probe(native_id, 'native fixture')
            finally:
                rc, out = d.remove(native_id)
                r.check('remove native closeApp probe', rc == 0, out.strip()[-400:])
        rep, raw = self.api('closeApp', {})
        r.check('closeApp without an id: badRequest', api_refused(rep) and rep.get('errorCode') == 'badRequest', raw[:400])
        # the uninstall refusal list
        for target, why in ((APP_ID, 'Home Chooser itself'), ('org.webosbrew.hbchannel', 'Homebrew Channel'),
                            (self.launcher, 'the requesting launcher')):
            rep, raw = self.api('uninstallApp', {'id': target})
            r.check('uninstall of %s is refused (protected)' % why, api_refused(rep) and rep.get('errorCode') == 'protected',
                    raw[:400])
        if d.app_installed(STOCK):
            rep, raw = self.api('uninstallApp', {'id': STOCK})
            r.check('uninstallApp of LG Home is refused (notRemovable)',
                    api_refused(rep) and rep.get('errorCode') == 'notRemovable', raw[:400])
        rep, raw = self.api('uninstallApp', {'id': 'org.webosbrew.hcfixture.notinstalled'})
        r.check('uninstallApp of an absent app is notFound',
                api_refused(rep) and rep.get('errorCode') == 'notFound', raw[:400])
        for method in ('getAppInfo', 'getAppIcon'):
            rep, raw = self.api(method, {'id': 'org.webosbrew.hcfixture.notinstalled'})
            r.check('%s of an absent app is notFound' % method,
                    api_refused(rep) and rep.get('errorCode') == 'notFound', raw[:400])
            rep, raw = self.api(method, {'id': '../bad'})
            r.check('%s rejects malformed id' % method,
                    api_refused(rep) and rep.get('errorCode') == 'badRequest', raw[:400])
        rep, raw = self.api('getAppIcon', {'id': self.launcher, 'size': 'invalid'})
        r.check('getAppIcon rejects an unknown size',
                api_refused(rep) and rep.get('errorCode') == 'badRequest', raw[:400])
        self.api_uninstall_cancel()
        self.api_uninstall_confirm()
        self.api_request(selected=True)
        self.check_no_compositor_restart('the launcher API calls', comp0)

    def uninstall_ask(self, label):
        """uninstall {id: disposable} as the Home: (confirm route 'alert'|'screen'|None, reply)."""
        r = self.rec
        if getattr(self, 'uninstall_monitor', None):
            self.uninstall_monitor.close()
        self.uninstall_monitor = RebootAlertMonitor(self.dev)
        if not self.uninstall_monitor.start():
            r.note('uninstall notification monitor unavailable; service log proof still required', '')
            self.uninstall_monitor.close()
            self.uninstall_monitor = None
        self.uninstall_mark = self.messages_mark()
        rep, raw = self.api('uninstallApp', {'id': DISPOSABLE_ID})
        how = (rep or {}).get('confirmation')
        r.check('%s: uninstall {id:%s} replies pending with a confirm route' % (label, DISPOSABLE_ID),
                (rep or {}).get('returnValue') is True and rep.get('pending') is True and how in ('alert', 'screen')
                and not api_reply_problems('uninstallApp', rep), raw[:400])
        return how if (rep or {}).get('returnValue') is True else None

    def uninstall_alert_id(self):
        """The alertId of the service's createAlert, from its UNINSTALL_ASK log line (PmLog), if logged."""
        rc, out = self.dev.sh(service_log_command(self.uninstall_mark, 'UNINSTALL_ASK')
                              + " | tail -n 3")
        m = re.findall(r'\\?"alertId\\?"\s*:\s*\\?"([^"\\]+)', out)
        observed = self.uninstall_monitor.snapshot() if getattr(self, 'uninstall_monitor', None) else None
        return (m[-1] if m else (observed or {}).get('alert_id')), out.strip()

    def screen_dialog(self):
        if not self.insp:
            self.insp = Inspector(self.vm)
        return self.wait(lambda: (lambda u: u if (u.get('dialog') or '').startswith('Uninstall ') else None)(self.ui() or {}),
                         60, 1) or {}

    def dismiss_alert(self, label):
        """Our createAlert: closed with closeAlert when its id is in the log, else with Back (never OK)."""
        r = self.rec
        sleep(2)
        self.foreign_popup(label)
        self.shot(label)
        aid, ev = self.uninstall_alert_id()
        if aid:
            out = self.priv_luna('luna://com.webos.notification/closeAlert', '{"alertId":"%s"}' % aid)
            r.check('%s: closeAlert on our "Uninstall ...?" alert was accepted' % label,
                    '"returnValue":true' in out.replace(' ', ''), 'alertId %s\nreply: %s' % (aid, out))
        else:
            r.note('%s: no alertId in the UNINSTALL_ASK log line; dismissing with Back' % label, ev or 'no UNINSTALL_ASK line')
            self.press('BACK')
        sleep(3)
        self.shot(label + '-dismissed')

    def api_uninstall_cancel(self):
        r, d = self.rec, self.dev
        how = self.uninstall_ask('cancel')
        if how == 'screen':
            ui = self.screen_dialog()
            self.shot('3a-uninstall-screen')
            r.check('screen route: Home Chooser asks "Uninstall %s?"' % 'HC Fixture disposable',
                    ui.get('dialog') == 'Uninstall HC Fixture disposable?', json.dumps(ui))
            ok, why = self.ours_in_front('Uninstall *')
            if ok:
                self.press('BACK')
                r.check('screen route: Back cancels the dialog', bool(self.wait(lambda: not (self.ui() or {}).get('dialog'), 10, 1)),
                        json.dumps(self.ui()))
            else:
                r.check('screen route: the dialog is ours and in front (else no key is pressed)', False, why)
            d.close(APP_ID)
            self.close_insp()
        elif how == 'alert':
            self.dismiss_alert('3a-uninstall-alert')
        sleep(5)
        r.check('after the cancel the disposable app is still installed', d.app_installed(DISPOSABLE_ID), DISPOSABLE_ID)

    def api_uninstall_confirm(self):
        """Confirm our owned alert and verify removal; keys retain the human fallback."""
        r, d = self.rec, self.dev
        how = self.uninstall_ask('confirmation')
        if how == 'alert':
            if self.a.uninstall_via == 'alert':
                self.shot('3a-uninstall-alert-confirm')
                aid, ev = self.uninstall_alert_id()
                r.note('uninstall alert awaiting a visually verified Uninstall press',
                       'alertId %s; %s' % (aid, ev))
                installed_before = set(d.installed())
                def service_proof():
                    _, raw = self.uninstall_alert_id()
                    observed = self.uninstall_monitor.snapshot() if getattr(self, 'uninstall_monitor', None) else None
                    return raw + '\nLS2: ' + json.dumps(observed), alert_service_proof(raw, 'Uninstall HC Fixture disposable?', 'Uninstall', 'Cancel',
                                                    DISPOSABLE_ID, self.launcher, observed=observed)
                def request_again():
                    if self.uninstall_ask('retry confirmation') != 'alert':
                        raise RuntimeError('uninstall retry did not raise an alert')
                    sleep(3)
                automatic = self.alert_action('Uninstall HC Fixture disposable?', 'Uninstall', 'Cancel',
                                             '3a-uninstall-alert', service_proof,
                                             lambda: self.wait(lambda: not d.app_installed(DISPOSABLE_ID),
                                                               30 if self.use_click() else 240, 2),
                                             lambda: not any(e.get('text') == 'Uninstall HC Fixture disposable?'
                                                 for e in self.elements('uninstall-result')['elements']), request_again)
                if not automatic:
                    r.manual('press Uninstall on our alert', 'automatic press declined: ' + self.alert_decline_reason + '; waiting up to 240 seconds')
                gone = self.wait(lambda: not d.app_installed(DISPOSABLE_ID), 240, 2)
                if gone:
                    installed_after = set(d.installed())
                    r.check('alert Uninstall press%s removed only %s'
                            % (' (automatic press)' if automatic else '', DISPOSABLE_ID),
                            DISPOSABLE_ID in installed_before
                            and {APP_ID, self.launcher} <= installed_after
                            and installed_after == installed_before - {DISPOSABLE_ID},
                            'before %s; after %s' % (sorted(installed_before), sorted(installed_after)))
                else:
                    self.dismiss_alert('3a-uninstall-alert-timeout')
                    r.manual('uninstall alert confirmation timed out',
                             ('automatic press declined: ' + self.alert_decline_reason + '; ' if not automatic else '')
                             + 'no disposable removal in 240 seconds')
                d.close(APP_ID)
                self.close_insp()
                return
            self.dismiss_alert('3a-uninstall-alert-2')
            r.manual('uninstall confirm path on the alert route (Uninstall on the system alert -> LG remove)',
                     'the runner never presses OK on a system popup; '
                     'by hand: call uninstall as the launcher, press Uninstall, then check getAppInfo %s fails' % DISPOSABLE_ID)
            return
        if how != 'screen':
            return
        ui = self.screen_dialog()
        ok, why = self.ours_in_front('Uninstall *')
        r.check('confirm: the "Uninstall ...?" dialog is ours and in front', ok, why)
        if not ok:
            return
        if self.use_click():
            self.click_element('Uninstall', effect=lambda: not d.app_installed(DISPOSABLE_ID), label='screen Uninstall')
        else:
            ok = self.focus_button('Uninstall')
            r.check('confirm: focus is on "Uninstall"', ok, json.dumps(self.ui()))
            if not ok:
                return
            self.press('OK')
        gone = self.wait(lambda: not d.app_installed(DISPOSABLE_ID), 120, 5)
        self.shot('3a-uninstalled')
        r.check('confirm on our screen: uninstallConfirmed removed %s' % DISPOSABLE_ID, bool(gone),
                'installed: %s; service log: %s' % (d.app_installed(DISPOSABLE_ID), d.sh(
                    (messages_command() + " | awk '$1 >= \"%s\"' | grep -E 'UNINSTALL' | tail -n 4") % self.uninstall_mark)[1].strip()))
        d.close(APP_ID)
        self.close_insp()

    def api_after_reboot(self):
        """--reboot-check: the access grants and the elevation survive a boot."""
        r = self.rec
        self.wait(lambda: (self.api('getInfo')[0] or {}).get('returnValue') is True, 60, 5)
        info = self.api_conforms('getInfo', 'after the reboot')
        r.check('after the reboot: getInfo says root true, permitted true, isSelectedHome true',
                info.get('root') is True and info.get('permitted') is True and info.get('isSelectedHome') is True, json.dumps(info))
        self.api_conforms('listApps', 'after the reboot')
        self.api_conforms('listRunning', 'after the reboot')

    def api_after_uninstall(self):
        """Home Chooser is gone: the launcher's calls fail cleanly (a JSON refusal, in time) and luna is up."""
        r, d = self.rec, self.dev
        for m in ('getInfo', 'listApps'):
            t0 = time.time()
            rep, raw = self.api(m)
            r.check('after the uninstall: %s as %s fails cleanly (returnValue false, %.0f s)' % (m, self.launcher, time.time() - t0),
                    api_refused(rep) and '[timeout]' not in raw, raw[:400])
        fg, out = d.foreground()
        r.check('after the uninstall: luna still answers (getForegroundAppInfo)', fg is not None and '"returnValue":true' in out.replace(' ', ''),
                out[:300])

    # -- the app update (--update-check)
    def watcher_procs(self):
        """Native --boot process count and the transient unit's own numbers."""
        d = self.dev
        top = d.sh("ps -eo pid,ppid,args | awk '/homechooser-daemon.*--boot/ && !/awk/ && !/grep/ {pid[$1]=1; pp[$1]=$2} "
                   "END {n=0; for (p in pid) if (!(pp[p] in pid)) {n++; printf \"%s \", p}; print \"top=\" n}'")[1].strip()
        m = re.search(r'top=(\d+)', top)
        n = int(m.group(1)) if m else -1
        show = d.sh('systemctl show homechooser-home -p MainPID -p NRestarts -p ActiveState 2>&1')[1]
        kv = dict(l.split('=', 1) for l in show.splitlines() if '=' in l)
        units = d.sh("systemctl list-units --all --no-legend 'homechooser-home*' 2>&1 | grep -c homechooser-home")[1].strip()
        return {'top': n, 'pids': top, 'main_pid': kv.get('MainPID'), 'restarts': kv.get('NRestarts'),
                'active': kv.get('ActiveState'), 'units': units}

    def md5(self, path):
        out = self.dev.sh('md5sum %s 2>&1' % path)[1].split()
        return out[0] if out and len(out[0]) == 32 else 'missing'

    def log_lines(self):
        rc, out = self.dev.sh('cat /tmp/homechooser-home.log 2>&1')
        return out.splitlines()

    def open_app_check(self, label):
        """Open Home Chooser, no error toast, the chosen row ticked; close it."""
        r, d = self.rec, self.dev
        self.insp = Inspector(self.vm)
        d.launch(APP_ID)
        seen = []

        def listed():
            u = self.ui()
            if u and u.get('toast'):
                seen.append(u['toast'])
            return u if u and self.launcher in (u.get('rows') or []) else None
        ui = self.wait(listed, 120, every=1)
        sleep(4)
        ui = self.ui() or ui or {}
        if ui.get('toast'):
            seen.append(ui['toast'])
        self.shot(label)
        r.check('%s: the app opens and lists the launcher' % label, bool(ui) and self.launcher in (ui.get('rows') or []), json.dumps(ui))
        r.check('%s: no error toast after opening' % label, not any(t.startswith("Couldn't") for t in seen), 'toasts: %r' % seen)
        ticked = self.insp.evaluate(TICK_JS)
        r.check('%s: the chosen row is still ticked' % label, ticked == self.launcher, 'ticked: %r' % ticked)
        d.close(APP_ID)
        self.insp.close()
        self.insp = None

    def update_snapshot(self):
        d = self.dev
        s = self.state()
        s['watcher_procs'] = self.watcher_procs()
        s['preload'] = d.preload_policy()[0] if self.major >= 6 else None
        s['log_lines'] = len(self.log_lines())
        s['runtime_md5'] = self.md5('/tmp/homechooser-runtime/homechooser-daemon')
        s['daemon_md5'] = self.md5(SCRIPTS_DIR + '/../homechooser-daemon')
        pid = s['watcher_procs']['main_pid']
        s['running_md5'] = self.md5('/proc/%s/exe' % pid) if pid and pid != '0' else 'missing'
        s['app_version'] = d.sh("sed -n 's/.*\"version\": *\"\\([^\"]*\\)\".*/\\1/p' %s/appinfo.json | head -1" % APPDIR)[1].strip()
        s['baked'] = self.baked_id() if self.uses_home_key_hook() else ''
        return s

    def step_update(self):
        """The Home Chooser update: the current build installed over the old one with ares-install and no
        uninstall in between. The native watcher confirms package absence twice, 15 s apart, so
        a temporary update gap does not restore stock. Its mapped image survives package replacement,
        and /tmp/homechooser-runtime retains a restart copy. The unit is not restarted by the update:
        one watcher, the same MainPID, still the old build's code until its next
        start (boot or restart).  So the log must show no restore and no \"watching, home=\" line from the update; a
        "Home Chooser is back" line appears only if the watcher happened to look during the gap."""
        r, d = self.rec, self.dev
        r.begin('3u update Home Chooser over the old build (--update-check)')
        new_files, old_files = ipk_files(self.hc_new), ipk_files(self.a.old_ipk)
        wk = 'usr/palm/services/%s/homechooser-daemon' % SVC_ID
        r.check('both ipks carry the native watcher executable', wk in new_files and wk in old_files, wk)
        new_md5 = hashlib.md5(new_files.get(wk, b'')).hexdigest()
        old_md5 = hashlib.md5(old_files.get(wk, b'')).hexdigest()
        r.note('old build %s (%s) vs new build %s: watcher executable md5 old=%s new=%s' % (
            self.a.old_ref, os.path.basename(self.a.old_ipk), os.path.basename(self.hc_new), old_md5, new_md5),
            'identical executables: hashes do not discriminate' if old_md5 == new_md5 else 'executables differ')
        before = self.update_snapshot()
        r.note('state before the update', json.dumps(before))
        r.check('before the update: exactly one watcher unit and one watcher process',
                before['watcher_procs']['top'] == 1 and before['watcher_procs']['units'] == '1', json.dumps(before['watcher_procs']))
        r.check('before the update: the running watcher is the old build (mapped executable hash)',
                before['running_md5'] == old_md5, 'running %s, old %s' % (before['running_md5'], old_md5))
        t_install = self.messages_mark()
        rc, out = d.install(self.hc_new)
        r.check('ares-install of the current build over the old one (no uninstall)', rc == 0 and 'Installed package' in out, out.strip())
        # CONFIRM_DELAY 15 s plus the compositor/sam settling; the watcher may also sit in nap
        self.wait(lambda: False, 45, 45)
        self.wait(lambda: self.watcher_active() == 'active', 60, 5)
        after = self.update_snapshot()
        r.note('state after the update', json.dumps(after))
        new_log = self.log_lines()[before['log_lines']:]
        r.note('watcher log lines written during the update', '\n'.join(new_log) or '(none)')
        r.check('after the update: the service directory holds the new watcher (md5 = new, app version %s)' % after['app_version'],
                after['daemon_md5'] == new_md5, 'device %s, new %s' % (after['daemon_md5'], new_md5))
        r.check('after the update: choice file unchanged', after['choice'] == before['choice'] == self.launcher, repr(after['choice']))
        r.check('after the update: record home is still the launcher', after['record_home'] == self.launcher, after['record_raw'])
        wp = after['watcher_procs']
        r.check('after the update: watcher unit active, exactly ONE watcher unit and ONE process (no duplicate)',
                after['watcher'] == 'active' and wp['top'] == 1 and wp['units'] == '1', json.dumps(wp))
        r.check('after the update: the watcher was not restarted (same MainPID, NRestarts unchanged: its mapped image survives replacement)',
                wp['main_pid'] == before['watcher_procs']['main_pid'] and wp['restarts'] == before['watcher_procs']['restarts'],
                'before %s, after %s' % (json.dumps(before['watcher_procs']), json.dumps(wp)))
        r.check('after the update: no \"watching, home=\" line (no watcher restart logged)',
                not any('watching, home=' in l for l in new_log), '\n'.join(l for l in new_log if 'watching' in l) or 'none')
        bad = [l for l in new_log if re.search(r'stock Home restored|is not installed, stock Home|isEnabled=true|createAlert|createToast|revert', l)]
        r.check('after the update: the watcher log shows no restore, no revert, no notification', not bad, '\n'.join(bad) or 'none')
        r.note('after the update: "Home Chooser is back" (only logged if the watcher looked during the gap)',
               '\n'.join(l for l in new_log if 'is back' in l) or 'not logged')
        r.check('after the update: the mapped watcher executable remains the old build',
                after['running_md5'] == old_md5, 'running %s; old %s; new %s' % (after['running_md5'], old_md5, new_md5))
        r.check('after the update: init.d points to the installed boot entry and its target exists',
                after['initd'] == (BOOT_SCRIPT if self.uses_home_key_hook() else BOOT_DIR + '/homechooser') and d.sh('test -f %s && echo y' % INITD)[1].strip() == 'y', after['initd'])
        if self.uses_home_key_hook():
            r.check('after the update: live key-filter remains selected without QML mounts', not after['mounts'] and not before['mounts'] and self.hook_mounts_match(),
                    'before %s after %s' % (before['mounts'], after['mounts']))
            r.check('after the update: Home key hook flag still exists', after['flag'] == 'yes', after['flag'])
            r.check('after the update: the launcher id is still baked into the selected systemUi.js', self.launcher in after['baked'], after['baked'])
        else:
            r.check('after the update: no Home key hook mounts or flag on this release', not after['mounts'] and after['flag'] == 'no',
                    'mounts %s flag %s' % (after['mounts'], after['flag']))
        if self.uses_override():
            r.check('after the update: profile override still exists', after['override'] == 'yes', after['override'])
        if self.major >= 6:
            self.wait(lambda: d.preload_policy()[0] is False, 30, 5)
            en = d.preload_policy()
            r.check('after the update: stock home preload policy still disabled (isEnabled:false)', en[0] is False, 'isEnabled=%r; %s' % en)
        else:
            en = d.preload_policy()
            r.check('after the update: 5.x, the stock home policy is still not disabled', en[0] is not False, 'isEnabled=%r' % (en[0],))
        self.check_device_texts()
        self.home_press(self.launcher, '3u-home')
        if self.strat.name == 'root-shell':
            # the update reinstalled the service: root is gone and only the luna path (the app) gets it back,
            # which root-shell stands in for; without it the app shows "can't set up root access yet"
            self.strat.prepare(d, r)
            self.strat.verify(d, r)
        self.open_app_check('3u-app')
        if self.a.api_check:
            info = self.api_conforms('getInfo', 'after the Home Chooser update')
            r.check('after the update: launcher permission and selected-Home grant survive',
                    info.get('root') is True and info.get('permitted') is True
                    and info.get('isSelectedHome') is True, json.dumps(info))
            self.api_conforms('listApps', 'after the Home Chooser update')
        r.note('messages since the install mentioning Home Chooser (sam/appinstalld)', d.sh(
            (messages_command() + " | awk '$1 >= \"%s\"' | grep -F %s | tail -n 12") % (t_install, APP_ID))[1])

    def step4(self):
        r, d = self.rec, self.dev
        r.begin('4 sudden launcher uninstall')
        self.popup_mark = self.popup_mark_now()
        alerts_before = len(self.notif_lines('createAlert'))
        comp0 = self.compositor_snapshot() if self.uses_home_key_hook() else None
        rc, out = d.remove(self.launcher)
        r.check('ares-install --remove launcher', rc == 0, out.strip())
        s = {}
        self.wait(lambda: self.stock_record_ok(d.record_home()[0]), 120, 5)
        self.check_alert(alerts_before)
        s = self.state()
        r.note('state after the launcher was removed', json.dumps(s))
        self.residency('after the launcher uninstall')
        self.assert_stock('after removal', s)
        self.check_no_compositor_restart('after the launcher removal', comp0)
        self.check_preload_on('after the launcher removal')
        r.check('choice file is kept (the user\'s choice)', s['choice'] == self.launcher, repr(s['choice']))
        r.check('watcher still active', s['watcher'] == 'active', s['watcher'])
        rc, out = d.sh('tail -n 8 /tmp/homechooser-home.log')
        r.note('watcher log', out)
        self.home_press(STOCK, '4-home-stock')
        comp0 = self.compositor_snapshot() if self.uses_home_key_hook() else None
        # reinstall
        rc, out = d.install(self.L.ipk)
        r.check('reinstall launcher', rc == 0 and 'Installed package' in out, out.strip())
        self.wait(lambda: d.record_home()[0] == self.launcher, 120, 5)
        s = self.state()
        r.note('state after the launcher returned', json.dumps(s))
        r.check('on return the watcher applies the choice again (record home)', s['record_home'] == self.launcher,
                s['record_raw'])
        if self.uses_override():
            r.check('on return the override is rebuilt', s['override'] == 'yes', s['override'])
        if self.uses_home_key_hook():
            r.check('on return the live key-filter is selected', self.hook_mounts_match(), 'mounts: %s' % s['mounts'])
            self.wait(lambda: self.launcher in self.baked_id(), 60, 5)
            baked = self.baked_id()
            r.check('on return the id is baked into the selected systemUi.js again',
                    self.launcher in baked, baked)
            # stock -> launcher with the patch mounted: the live switch
            self.check_no_compositor_restart('on return (a switch with the patch mounted)', comp0)
        self.wait(lambda: self.major <= 5 or self.dev.preload_policy()[0] is False, 60, 5)
        if self.major >= 6:
            enabled, raw = d.preload_policy()
            r.check('on return the stock home policy is disabled again (isEnabled:false)', enabled is False,
                    'isEnabled=%r; %s' % (enabled, raw))
        self.home_press(self.launcher, '4-home-launcher-back')

    def step_launcher_update(self):
        """Install a higher version of the selected id without removing it first."""
        r, d = self.rec, self.dev
        r.begin('3v update the chosen launcher to a new version')
        old_version = self.L.appinfo.get('version')
        temp = None
        ipk = self.a.launcher_update_ipk
        if not ipk and self.L.is_fixture and self.L.fixture[0] == 'web':
            temp = tempfile.mkdtemp(prefix='launcher-update-', dir=os.path.join(REPO, 'build'))
            new_version = '1.0.1'
            env = dict(os.environ, HC_FIXTURE_VERSION=new_version)
            rc, out = run(['sh', os.path.join(HERE, 'fixtures', 'web', 'build.sh'), temp], 600, env=env)
            if not r.check('build updated web fixture', rc == 0, out.strip()[-400:]):
                shutil.rmtree(temp)
                return
            ipk = os.path.join(temp, self.launcher + '_' + new_version + '_all.ipk')
        try:
            info = ipk_appinfo(ipk) if ipk and os.path.isfile(ipk) else {}
            r.check('update ipk has the same app id and a new version',
                    info.get('id') == self.launcher and info.get('version') != old_version,
                    'old=%s; new=%s; id=%s' % (old_version, info.get('version'), info.get('id')))
            if info.get('id') != self.launcher or info.get('version') == old_version:
                return
            comp0 = self.compositor_snapshot() if self.uses_home_key_hook() else None
            rc, out = d.install(ipk)
            r.check('install updated launcher over the selected id', rc == 0 and 'Installed package' in out,
                    out.strip()[-400:])
            self.wait(lambda: d.record_home()[0] == self.launcher, 60, 3)
            s = self.state()
            r.check('updated launcher remains selected Home', s['choice'] == self.launcher
                    and s['record_home'] == self.launcher, json.dumps(s))
            actual = parse_luna(d.luna('luna://com.webos.applicationManager/getAppInfo',
                                       json.dumps({'id': self.launcher}))) or {}
            r.check('SAM reports the new launcher version', (actual.get('appInfo') or {}).get('version') == info['version'],
                    json.dumps(actual)[:400])
            self.check_no_compositor_restart('chosen launcher version update', comp0)
            self.home_press(self.launcher, '3v-home-after-launcher-update')
        finally:
            if temp:
                shutil.rmtree(temp)

    def step_restore_repair(self):
        """Reinstall after the watcher was stopped and could not undo the Home setting."""
        r, d = self.rec, self.dev
        r.begin('3f uninstall with watcher stopped, then repair a leftover Home')
        rc, out = d.sh('systemctl stop homechooser-home')
        r.check('stop the watcher before uninstall', rc == 0 and self.watcher_active() != 'active', out.strip())
        rc, out = d.remove(APP_ID)
        r.check('uninstall Home Chooser without a running watcher', rc == 0, out.strip()[-400:])
        left = self.state()
        r.check('the stopped watcher left the chosen app in the Home record',
                left['record_home'] == self.launcher, json.dumps(left))
        # Model a partial restore that removed its own choice file before the Luna write failed.
        # The documented leftover repair runs only when no usable choice is stored.
        rc, out = d.sh('rm -f %s' % shlex.quote(CHOICE))
        r.check('model interrupted restore: choice file removed, Home record retained',
                rc == 0 and 'No such file' in self.state()['choice']
                and d.record_home()[0] == self.launcher, out.strip())
        rc, out = d.remove(self.launcher)
        r.check('remove the launcher named by the leftover Home record', rc == 0, out.strip()[-400:])
        rc, out = d.install(self.hc_new)
        r.check('reinstall current Home Chooser', rc == 0 and 'Installed package' in out, out.strip()[-400:])
        if self.strat.name == 'root-shell':
            self.strat.prepare(d, r)
        rc, out = d.launch(APP_ID)
        r.check('open reinstalled Home Chooser to start leftover repair', rc == 0, out.strip()[-400:])
        repaired = self.wait(lambda: self.stock_record_ok(d.record_home()[0]), 90, 3)
        s = self.state()
        r.check('leftover Home record repaired to stock', bool(repaired), json.dumps(s))
        if self.uses_override():
            r.check('leftover profile override removed', s['override'] == 'no', json.dumps(s))
        r.note('watcher repair log', d.sh('tail -n 30 /tmp/homechooser-home.log')[1])
        self.home_press(STOCK, '3f-home-after-repair')
        rc, out = d.remove(APP_ID)
        r.check('uninstall reinstalled Home Chooser with no choice and no watcher',
                rc == 0 and self.watcher_active() != 'active', out.strip()[-400:])
        before_boot = self.state()
        r.note('state immediately after the stopped-watcher uninstall', json.dumps(before_boot))
        if not self.beanjar_reboot('3f cleanup boot'):
            return
        self.wait(lambda: self.stock_record_ok(d.record_home()[0])
                  and 'No such file' in self.state()['initd'], 90, 3)
        after_boot = self.state()
        r.note('state after the cleanup boot', json.dumps(after_boot))
        r.check('after the cleanup boot: Home record is stock',
                self.stock_record_ok(after_boot['record_home']), after_boot['record_raw'])
        r.check('after the cleanup boot: init.d entry and choice removed',
                'No such file' in after_boot['initd'] and 'No such file' in after_boot['choice'],
                json.dumps(after_boot))
        command = 'for path in %s; do test ! -e "$path" || printf "%%s\\n" "$path"; done' % (
            ' '.join(shlex.quote(path) for path in uninstall_remnant_paths() + [FLAG]))
        rc, remaining = d.sh(command)
        r.check('after the cleanup boot: flag, cache, stale marker, boot copy and Luna access files removed',
                rc == 0 and not remaining.strip(), remaining.strip() or 'none remain')
        if self.uses_home_key_hook():
            r.check('after the cleanup boot: Home key hook mounts gone', not after_boot['mounts'],
                    str(after_boot['mounts']))
        if self.uses_override():
            r.check('after the cleanup boot: profile override removed',
                    after_boot['override'] == 'no', after_boot['override'])
        self.check_preload_on('after the cleanup boot')

    def step5(self):
        r, d = self.rec, self.dev
        r.begin('5 uninstall Home Chooser')
        toasts_before = len(self.notif_lines('createToast'))
        comp0 = self.compositor_snapshot() if self.uses_home_key_hook() else None
        rc, out = d.remove(APP_ID)
        r.check('ares-install --remove Home Chooser', rc == 0, out.strip())
        self.check_toast(toasts_before)
        self.wait(lambda: self.watcher_active() != 'active' and self.stock_record_ok(d.record_home()[0]), 150, 5)
        sleep(5)
        s = self.state()
        r.note('state after uninstall', json.dumps(s))
        self.residency('after the Home Chooser uninstall')
        r.check('choice file removed', 'No such file' in s['choice'], repr(s['choice']))
        # 6-9: the record gets the stock id and the patch stays mounted (no unmount, no restart) until the
        # next boot; the baked id is left as it was (the worker went with the app directory)
        self.assert_stock('after uninstall', s, baked_emptied=False)
        self.check_no_compositor_restart('after the Home Chooser uninstall', comp0)
        self.check_preload_on('after the Home Chooser uninstall')
        command = 'for path in %s; do test ! -e "$path" || printf "%%s\\n" "$path"; done' % (
            ' '.join(shlex.quote(path) for path in uninstall_remnant_paths()))
        rc, remaining = d.sh(command)
        r.check('hook cache, stale marker and Home Chooser Luna access files removed',
                rc == 0 and not remaining.strip(), remaining.strip() or 'none remain')
        if self.uses_override():
            r.check('profile override removed after Home Chooser uninstall', s['override'] == 'no', s['override'])
        r.check('init.d entry removed', 'No such file' in s['initd'] or s['initd'] == '', repr(s['initd']))
        r.check('Home key hook flag removed', s['flag'] == 'no', s['flag'])
        r.check('watcher unit stopped', s['watcher'] != 'active', s['watcher'])
        rc, out = d.sh('tail -n 12 /tmp/homechooser-home.log')
        r.note('watcher log', out)
        self.home_press(STOCK, '5-home-stock')
        if self.a.api_check:
            self.api_after_uninstall()

    def step6(self):
        r, d = self.rec, self.dev
        r.begin('6 cleanup')
        if self.insp:
            self.insp.close()
        if self.vm in KEEP_LAUNCHER_VMS:
            r.note('this VM keeps Dock installed and selected: restore by hand (Home Chooser must be '
                   'installed and Dock chosen through its UI); the runner does not do that yet', '')
        else:
            removed_hc = False
            apps = [self.launcher, APP_ID] + ([DISPOSABLE_ID] if self.a.api_check else [])
            try:
                installed = d.installed()
            except RuntimeError as e:
                r.check('cleanup can list installed apps', False, str(e))
                return
            for app in apps:
                if app in installed:
                    rc, out = d.remove(app)
                    r.say('removed %s: %s' % (app, out.strip()))
                    removed_hc = removed_hc or app == APP_ID
            try:
                installed = d.installed()
            except RuntimeError as e:
                r.check('no test apps left installed', False, str(e))
                return
            r.check('no test apps left installed', not (set(apps) & set(installed)), str(installed))
            if removed_hc:
                # The watcher needs about 15 s per removal it has to confirm, one after the other, and a VM
                # stopped before it finishes leaves the choice file behind (a dangling init.d link
                # runs nothing at the next boot).  Wait for it before the VM is stopped.
                done = self.wait(lambda: self.watcher_active() != 'active'
                                 and 'No such file' in d.sh('cat %s 2>&1' % CHOICE)[1], 150, 5)
                s = self.state()
                r.check('after the cleanup uninstalls the watcher finished the restore: choice file gone, '
                        'init.d entry gone, watcher stopped',
                        bool(done) and ('No such file' in s['initd'] or s['initd'] == ''), json.dumps(s))
                r.note('watcher log', d.sh('tail -n 12 /tmp/homechooser-home.log')[1])
        r.note('df /media/developer', d.sh('df -h /media/developer | tail -1')[1])


class DeviceApiScenario(Scenario):
    """API contract on an existing rooted ares device; no Beanjar or reboot operations."""

    def __init__(self, args, rec, launcher):
        super().__init__(args, None, rec, ('device', args.device, 'luna', 'ares', 'author TV'), launcher)
        self.dev = Device(args.device, args.device)
        self.major, os_release = self.dev.release()
        self.rec.note('device release', os_release)
        self.stock_id = stock_home_id(self.major, self.dev.app_installed(FULLHOME))

    def shot(self, label):
        self.rec.note('screenshot %s skipped in --device mode' % label)
        return None

    def choose_on_device(self, target):
        """Use the inspected Home Chooser page; keys are sent only while it has focus."""
        r, d = self.rec, self.dev
        self.close_insp()
        self.insp = Inspector(self.vm)
        rc, out = d.launch(APP_ID)
        r.check('open Home Chooser to choose %s' % target, rc == 0, out.strip()[-300:])
        ui = self.wait(lambda: (lambda u: u if u and target in (u.get('rows') or []) else None)(self.ui()), 90, 1)
        if not ui or not self.ensure_page('device selection'):
            raise RuntimeError('Home Chooser does not show %s' % target)
        rows = ui['rows']
        self.ensure_key_focus(rows)
        for _ in range(len(rows) + 2):
            ui = self.ui() or {}
            if ui.get('row') == target:
                break
            if ui.get('row') not in rows:
                raise RuntimeError('focus left Home Chooser; no more keys sent')
            self.press('DOWN' if rows.index(target) > rows.index(ui['row']) else 'UP')
        ui = self.ui() or {}
        if ui.get('row') != target or not self.ensure_page('device selection confirm'):
            raise RuntimeError('target row is not focused; no OK sent')
        self.press('OK')
        want = 'Use LG Home?' if target == STOCK else 'Use %s as Home?' % self.L.title
        ui = self.wait(lambda: (lambda u: u if u and u.get('dialog') == want else None)(self.ui()), 20, 1) or {}
        ok, why = self.ours_in_front(want)
        r.check('selection dialog is ours and focused', ok, why)
        if not ok:
            raise RuntimeError('selection dialog is not verified; no OK sent')
        label = 'Use LG Home' if target == STOCK else 'Use as Home'
        if ui.get('text') != label:
            self.press('UP')
            ui = self.ui() or {}
        if ui.get('text') != label:
            raise RuntimeError('confirmation button %s is not focused; no OK sent' % label)
        self.ask_mark = self.messages_mark()
        self.ask_monitor = RebootAlertMonitor(d) if self.uses_home_key_hook() else None
        if self.ask_monitor and not self.ask_monitor.start():
            self.ask_monitor.close()
            self.ask_monitor = None
        self.press('OK')
        selected = self.wait(lambda: self.state()['choice'] == target if target != STOCK
                             else self.stock_record_ok(d.record_home()[0]), 90, 3)
        r.check('choice applied without reboot', bool(selected), json.dumps(self.state()))
        if self.uses_home_key_hook():
            info = self.wait(lambda: (lambda x: x if x['alert'] else None)(self.reboot_ask_evidence()[0]), 30, 2)
            if info and info['alert'] == 'accepted':
                self.close_reboot_alert('device mode: no reboot', info)
            elif info:
                r.note('restart alert refused; no popup to close', json.dumps(info))
        if self.ask_monitor:
            self.ask_monitor.close()
            self.ask_monitor = None
        self.close_insp()
        return bool(selected)

    def run_contract(self):
        r, d = self.rec, self.dev
        r.begin('device preflight')
        rc, uid = d.exec('id -u')
        if not r.check('ares device has a root shell', rc == 0 and uid.strip().endswith('0'), uid.strip()):
            return
        if not r.check('Homebrew Channel installed', d.app_installed('org.webosbrew.hbchannel'), ''):
            return
        if not r.check('launcher ipk exists', os.path.isfile(self.L.ipk), self.L.ipk):
            return
        prior = self.state()
        r.note('original Home setting', json.dumps(prior))
        original_choice_stock = (not prior['choice'] or 'No such file' in prior['choice']
                                 or prior['choice'] in (STOCK, FULLHOME))
        if not r.check('original Home is stock, so it can be restored after the contract run',
                       original_choice_stock and stock_baseline_ok(self.major, prior['record_home']),
                       'choice=%r record=%r' % (prior['choice'], prior['record_home'])):
            return
        if not r.check('launcher and disposable fixture are not already installed',
                       not d.app_installed(self.launcher) and not d.app_installed(DISPOSABLE_ID),
                       '%s, %s' % (self.launcher, DISPOSABLE_ID)):
            return
        installed_hc = False
        installed_launcher = False
        installed_disposable = False
        try:
            if not d.app_installed(APP_ID):
                if not self.a.hc_ipk:
                    raise RuntimeError('Home Chooser is absent: pass --hc-ipk to install it')
                rc, out = d.install(self.a.hc_ipk)
                installed_hc = rc == 0
                if not r.check('install Home Chooser', installed_hc, out.strip()[-400:]):
                    return
            rc, out = d.install(self.L.ipk)
            installed_launcher = rc == 0
            if not r.check('install launcher', installed_launcher, out.strip()[-400:]):
                return
            rc, out = d.install(self.a.disposable_ipk)
            installed_disposable = rc == 0
            if not r.check('install disposable API control', installed_disposable, out.strip()[-400:]):
                return
            strategy = ELEVATIONS[self.a.elevation or ('root-shell' if self.major >= 11 else 'luna')]()
            strategy.prepare(d, r)
            self.insp = Inspector(self.vm)
            d.launch(APP_ID)
            if not self.wait(lambda: (self.ui() or {}).get('rows'), 90, 1):
                raise RuntimeError('Home Chooser UI did not load')
            strategy.verify(d, r)
            self.api_before_selection()
            r.begin('device selection')
            if not self.choose_on_device(self.launcher):
                return
            self.step_api()
        finally:
            r.begin('device cleanup')
            self.close_insp()
            if installed_launcher and d.app_installed(self.launcher):
                if self.state()['choice'] == self.launcher:
                    try:
                        self.choose_on_device(STOCK)
                    except Exception as error:
                        r.check('restore LG Home through its verified UI', False, repr(error))
                rc, out = d.remove(self.launcher)
                r.check('remove test launcher', rc == 0, out.strip()[-300:])
            if installed_disposable and d.app_installed(DISPOSABLE_ID):
                rc, out = d.remove(DISPOSABLE_ID)
                r.check('remove disposable fixture', rc == 0, out.strip()[-300:])
            if installed_hc and d.app_installed(APP_ID):
                rc, out = d.remove(APP_ID)
                r.check('remove Home Chooser installed for this run', rc == 0, out.strip()[-300:])
                self.wait(lambda: self.watcher_active() != 'active', 90, 3)
                self.api_after_uninstall()
            r.check('original stock Home setting restored', self.stock_record_ok(d.record_home()[0]),
                    d.record_home()[1][:300])
            r.check('test apps gone', not d.app_installed(self.launcher) and not d.app_installed(DISPOSABLE_ID), '')


# ---------------------------------------------------------------------------------------------
# VM preparation: layers, LAN, devices
# ---------------------------------------------------------------------------------------------
def ensure_devices(host, vm, rec):
    """A device entry `<vm>-root` (root, 6622) with the VM's root key, and `<vm>` (devmode)."""
    info = host.info(vm)
    addr = info.get('lan_address') or ''
    if not addr or addr == '-':
        raise SystemExit('VM %s has no LAN address (is it running with link=lan?)' % vm)
    # Beanjar 0.3.1+git7 sends the VM's name as its DHCP hostname, so the LAN's DNS knows it while it
    # runs (`beanjar info` lan_hostname). Prefer the name, so the entries survive a new lease, but only
    # when it resolves to this lease; otherwise keep the address.
    name = info.get('lan_hostname') or ''
    if name and name != '-':
        import socket
        for cand in (name + '.lan', name):
            try:
                if socket.gethostbyname(cand) == addr:
                    rec.say('VM %s resolves as %s (%s)' % (vm, cand, addr))
                    addr = cand
                    break
            except OSError:
                pass
    keyname = 'beanjar-%s-root' % vm
    if fetch_key(host, '%s/identity/id_ed25519' % info['dir'], keyname):
        rec.say('fetched the root key of %s to ~/.ssh/%s' % (vm, keyname))
    dkeyname = 'beanjar-%s_devmode' % vm
    with devices_file() as devs:
        # the devmode device (prisoner, 9922) uses the VM's devmode key, fetched only when it is added
        if not any(x.get('name') == vm for x in devs) and fetch_key(host, '%s/identity/devmode_rsa' % info['dir'], dkeyname):
            rec.say('fetched the devmode key of %s to ~/.ssh/%s' % (vm, dkeyname))
        update_devices(devs, vm, addr, keyname, dkeyname, rec.say)


def fetch_key(host, src, keyname):
    """~/.ssh/<keyname> from the host unless it is there; True if fetched.  Fetched under a temp name and
    renamed, so an interrupted copy never leaves a truncated key behind."""
    keypath = os.path.expanduser('~/.ssh/' + keyname)
    if os.path.exists(keypath):
        return False
    tmp = '%s.%d.tmp' % (keypath, os.getpid())
    try:
        if not host.fetch(src, tmp):
            raise SystemExit('could not fetch %s from %s' % (src, host.name))
        os.chmod(tmp, 0o600)
        os.replace(tmp, keypath)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return True


DEVICES_FILE = os.path.expanduser('~/.webos/ose/novacom-devices.json')


class devices_file:
    """`with devices_file() as devs:` read-modify-write of the ares device list, which runs in parallel
    share.  An exclusive flock on <file>.lock serialises our runs; the new list is written to a temp file
    in the same directory and renamed over the old one, so an ares command reading it at that moment
    sees the old list or the new one, never a truncated file.  ares-cli-rs itself writes the file in
    place without a lock (common/device/src/io.rs write_in), so `ares-setup-device` by hand during a run
    is still unsafe.  The file is rewritten only if the list changed."""

    def __init__(self, path=None):
        self.path = path or DEVICES_FILE

    def __enter__(self):
        import fcntl
        self.lock = open(self.path + '.lock', 'a')
        fcntl.flock(self.lock, fcntl.LOCK_EX)
        try:
            with open(self.path) as f:
                self.devs = json.load(f)
        except BaseException:
            self.lock.close()
            raise
        self.before = json.dumps(self.devs)
        return self.devs

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None and json.dumps(self.devs) != self.before:
                tmp = '%s.%d.tmp' % (self.path, os.getpid())
                with open(tmp, 'w') as f:
                    json.dump(self.devs, f, indent=2)
                os.replace(tmp, self.path)
        finally:
            self.lock.close()  # releases the flock
        return False


def update_devices(devs, vm, addr, keyname, dkeyname, say=print):
    """Add or fix `<vm>-root` (root, 6622) and `<vm>` (devmode, prisoner, 9922) in an ares device list."""
    byname = {x['name']: x for x in devs}
    want = {vm + '-root': (6622, 'root', {'openSsh': keyname})}
    for name, (port, user, key) in want.items():
        e = byname.get(name)
        if e is None:
            devs.append({'profile': 'ose', 'name': name, 'host': addr, 'port': port, 'username': user,
                         'privateKey': key, 'files': 'sftp'})
            say('added ares device %s -> %s:%d' % (name, addr, port))
        elif e.get('host') != addr:
            say('updated ares device %s host %s -> %s' % (name, e.get('host'), addr))
            e['host'] = addr
    dv = byname.get(vm)
    if dv is None:
        devs.append({'profile': 'ose', 'name': vm, 'host': addr, 'port': 9922, 'username': 'prisoner',
                     'privateKey': {'openSsh': dkeyname}, 'files': 'sftp'})
        say('added ares device %s -> %s:9922' % (vm, addr))
    elif dv.get('host') != addr:
        say('updated ares device %s host %s -> %s' % (vm, dv.get('host'), addr))
        dv['host'] = addr


def prepare_vm(host, vm, rec):
    """Refuse a busy machine, set the VM's layers and link.  The caller starts it."""
    try:
        running = host.running_vms()
    except RuntimeError as e:
        raise SystemExit(str(e))
    if running:
        raise SystemExit('VMs already running on %s, not touching anything: %s' % (host.name, running))
    s = host.settings(vm)
    layers = (s.get('layers') or '').split(',')
    want = {}
    if 'hbchannel' not in layers:
        want['layers'] = ','.join(layers + ['hbchannel'])
    if s.get('link') != 'lan':
        want['link'] = 'lan'
    if want:
        rec.say('VM %s is off; settings %s' % (vm, want))
        rc, out = host.set_settings(vm, **want)
        rec.say(out.strip()[-300:])


# wait_for_device states
READY, NO_ROOT_SSH, NO_HBCHANNEL, DOWN = 'ready', 'no-root-ssh', 'no-hbchannel', 'down'
WAIT_STATES = {
    READY: 'root SSH answers and Homebrew Channel is installed',
    NO_ROOT_SSH: "developer mode answers and lists Homebrew Channel, but root SSH (HBC's Dropbear, guest 22 = "
                 "host 6622) does not",
    NO_HBCHANNEL: 'root SSH answers, but Homebrew Channel is not installed',
    DOWN: 'neither root SSH nor developer mode answers',
}


def wait_for_device(vm, rec, timeout=300):
    """One of the states above: READY as soon as it is reached, else the last state seen at the timeout.
    Every change of state is said, so a timeout shows which probe never passed."""
    dev = Device(vm)
    end = time.time() + seconds(timeout)
    state = None
    while True:
        rc, out = dev.sh('echo up', 20)
        try:
            installed = dev.installed()
        except RuntimeError:
            installed = []
        if out.strip().endswith('up'):
            now = READY if 'org.webosbrew.hbchannel' in installed else NO_HBCHANNEL
        else:
            # the devmode device (prisoner, 9922) is Developer Mode's own Dropbear, not HBC's
            now = NO_ROOT_SSH if 'org.webosbrew.hbchannel' in installed else DOWN
        if now != state:
            state = now
            rec.say('VM %s: %s (%s)' % (vm, state, WAIT_STATES[state]))
        if state == READY or time.time() >= end:
            return state
        sleep(5)


def failsafe_note(vm):
    return ("root SSH never answered while developer mode did: Homebrew Channel's startup.sh is most likely in "
            "failsafe mode (telnetd only, no Dropbear). It sets /var/luna/preferences/webosbrew_failsafe at the start "
            "of a boot and clears it about 12 s after its boot work, so a boot cut short before that (a VM stopped "
            "within seconds of starting) leaves the flag and the next boot runs failsafe; that boot clears the flag "
            "again after 15 s. Restarting %s once." % vm)


def interrupted(*_):
    raise KeyboardInterrupt


def run_device_api(args, ap):
    """Run the launcher contract using only ares tools on an author's TV."""
    if args.vm or not args.api_check or args.launchers or args.update_check or args.reboot_check or args.build:
        ap.error('--device requires --api-check and cannot be combined with VM, matrix, update, reboot or build options')
    args.input = 'ares'
    spec = args.launcher or 'fixture:web.normal'
    if spec == 'dock' and not (args.launcher_ipk or args.dock_ipk):
        ap.error('Dock is optional: pass --dock-ipk or --launcher-ipk, or use the web fixture')
    launcher = make_launcher(spec, args.launcher_ipk, dock_ipk=args.dock_ipk, api=True)
    args.disposable_ipk = api_fixture_ipks()[DISPOSABLE_ID]
    outdir = args.outdir or os.path.join(HERE, 'results', '%s-%s' %
                                        (args.device, time.strftime('%Y%m%d-%H%M%S')))
    os.makedirs(outdir)
    rec = Recorder(outdir)
    rec.say('ares-only device API contract: %s; artefacts: %s' % (args.device, outdir))
    scenario = None
    try:
        if not shutil.which('ares-do'):
            raise RuntimeError('ares-do not on PATH')
        scenario = DeviceApiScenario(args, rec, launcher)
        scenario.run_contract()
    except Exception as error:
        import traceback
        rec.say('device runner error:\n' + traceback.format_exc())
        rec.check('device contract ran to completion', False, repr(error))
    finally:
        if scenario:
            scenario.close_insp()
            if getattr(scenario, 'uninstall_monitor', None):
                scenario.uninstall_monitor.close()
            if getattr(scenario, 'ask_monitor', None):
                scenario.ask_monitor.close()
        counts = rec.finish()
    return 1 if counts.get('FAIL') else 0


def main():
    ap = argparse.ArgumentParser(description='Home Chooser E2E on a Beanjar VM or API contract on an ares TV')
    ap.add_argument('--scenario', choices=['lifecycle', 'screensaver', 'all'], default='lifecycle',
                    help='Home-key lifecycle (default), home and screensaver alone, or both')
    ap.add_argument('--saver-start', choices=['tvpower', 'beanjar'], default='tvpower',
                    help='screensaver trigger: root tvpower firmware call (default), or bounded Beanjar helper')
    ap.add_argument('--vm', help='a VM name or a release (e.g. w22h-ombre or 9.x)')
    ap.add_argument('--device', help='an existing root-capable ares device name; run --api-check without Beanjar or reboot')
    ap.add_argument('--launcher', default=None,
                    help="'dock' (default; with --api-check fixture:web.normal), 'fixture:<native|qml|web>.<normal|overlay>', "
                         "or an app id with --launcher-ipk (an author's own launcher)")
    ap.add_argument('--launchers', default=None,
                    help="'all-fixtures' or a comma list of --launcher values: the scenario runs once per launcher on the "
                         "same VM, in sequence, with cleanup between")
    ap.add_argument('--launcher-ipk', default=None, help='ipk of --launcher when it is an app id (or to replace Dock\'s)')
    ap.add_argument('--launcher-update-check', action='store_true',
                    help='after selection install a newer version of the same launcher id and verify Home stays selected')
    ap.add_argument('--launcher-update-ipk', default=None,
                    help='updated launcher ipk for --launcher-update-check (web fixture builds version 1.0.1 automatically)')
    ap.add_argument('--restore-repair-check', action='store_true',
                    help='after selection stop the watcher, uninstall, model a partial restore, and verify reinstall repairs Home')
    ap.add_argument('--update-check', action='store_true',
                    help='install an older Home Chooser first, choose the launcher, then install the current build over it '
                         '(step 3u) and check that everything survives')
    ap.add_argument('--update-from', default=None,
                    help='git ref of the older Home Chooser (default: the previous commit that changed app/ or service/)')
    ap.add_argument('--host', default=None,
                    help="the Beanjar machine the VM is on, a name in e2e/hosts.toml (default: the file's default, 'pi')")
    ap.add_argument('--hosts-file', default=None, help='a hosts file instead of e2e/hosts.toml + e2e/hosts.local.toml')
    ap.add_argument('--host-mode', choices=['local', 'remote'], default=None,
                    help="override the host's mode: beanjar run here, or over ssh")
    ap.add_argument('--host-device', default=None, help="override the host's ssh target (an ssh config alias)")
    ap.add_argument('--dock-ipk', default=None, help='optional Dock ipk (otherwise hosts.toml [paths] dock_ipk in VM mode)')
    ap.add_argument('--hc-ipk', default=None, help='the Home Chooser ipk to test (default: the newest build/ipk/*.ipk)')
    ap.add_argument('--old-ipk', default=None, help='--update-check: the older Home Chooser ipk, already built (skips the build)')
    ap.add_argument('--outdir', default=None, help='the result directory (default e2e/results/<vm>-<timestamp>); must not exist')
    ap.add_argument('--elevation', choices=sorted(ELEVATIONS), default=None,
                    help="override the matrix's elevation strategy")
    ap.add_argument('--input', choices=['beanjar', 'qmp', 'ares'], default='beanjar',
                    help='remote-key path: Beanjar IR remote (default), QEMU keyboard, or ares-do key')
    ap.add_argument('--input-mode', choices=['click', 'keys'], default='click',
                    help='prefer screen elements and pointer clicks (default), or key navigation')
    ap.add_argument('--build', action='store_true', help='run npm run package first')
    ap.add_argument('--until', type=int, default=5, help='debugging: stop after this step and skip the cleanup')
    ap.add_argument('--wait-popup', type=int, default=0,
                    help='seconds to wait for a system popup holding key focus to be dismissed by hand')
    ap.add_argument('--reboot-check', action='store_true',
                    help='after step 3 reboot the VM (beanjar stop/start) and check that the watcher re-applied '
                         'the stock-home preload policy and the stock home is not running')
    ap.add_argument('--reboot-via', choices=['later', 'alert'], default='later',
                    help="webOS 6-9, the restart the first choice asks for (the service's system alert): close our alert "
                         "with closeAlert and restart the VM with beanjar stop/start (default; no key is pressed), or "
                         "'alert': automatically press Reboot after verifying the alert and focus from the screen; "
                         "fall back to a 240 s MANUAL wait if proof fails")
    ap.add_argument('--api-check', action='store_true',
                    help='the launcher-API contract (docs/launcher-api.md): calls as the launcher before and after it is '
                         'chosen (2a, 3a), after --reboot-check and after the uninstall; installs a disposable app. '
                         'The launcher must declare org.webosbrew.homechooser.applications: the default is the web fixture built with it; '
                         '--launcher <id> --launcher-ipk <ipk> runs it against your own launcher')
    ap.add_argument('--uninstall-via', choices=['later', 'alert'], default='later',
                    help='with --api-check, automatically press Uninstall after proving our alert and focus; '
                         'fall back to a 240 s MANUAL wait if proof fails; default closes the alert')
    ap.add_argument('--no-stop', action='store_true', help='leave the VM running at the end')
    ap.add_argument('--list', action='store_true', help='print the matrix and exit')
    args = ap.parse_args()
    if args.list:
        for m in MATRIX:
            print('%-5s %-18s %-10s %-9s %s' % (m[0], ' | '.join(candidates(m[1])), *m[2:]))
        print('launchers: dock, %s' % ', '.join(fixture_specs()))
        return 0
    if args.device:
        if args.scenario != 'lifecycle' or args.saver_start != 'tvpower':
            ap.error('--scenario screensaver/all and --saver-start beanjar require VM mode')
        return run_device_api(args, ap)
    if args.scenario != 'lifecycle' and (args.input != 'beanjar' or args.until != 5):
        ap.error('--scenario screensaver/all requires --input beanjar and the complete setup/cleanup')
    if not args.vm:
        ap.error('--vm or --device is required')
    row = entry(args.vm)
    vm = row[1]
    if args.launchers:
        specs = fixture_specs() if args.launchers == 'all-fixtures' else [x for x in args.launchers.split(',') if x]
    else:
        specs = [args.launcher or ('fixture:web.normal' if args.api_check or args.scenario != 'lifecycle' else 'dock')]
    args.multi = len(specs) > 1
    if args.launcher_update_check and not args.launcher_update_ipk and any(not s.startswith('fixture:web.') for s in specs):
        ap.error('--launcher-update-check needs --launcher-update-ipk unless using a web fixture')
    if args.multi and vm in KEEP_LAUNCHER_VMS:
        ap.error('%s keeps its launcher installed; run one launcher at a time there' % vm)
    cfg = load_hosts(args.hosts_file)
    dock_ipk = args.dock_ipk or cfg['paths'].get('dock_ipk') or DOCK_IPK
    launchers = [make_launcher(sp, args.launcher_ipk if len(specs) == 1 else None,
                               lambda t: print(t, flush=True), dock_ipk, api=args.api_check) for sp in specs]
    if args.scenario != 'lifecycle' and any(not launcher.is_fixture or launcher.overlay for launcher in launchers):
        ap.error('--scenario screensaver/all requires a normal fixture launcher')
    args.disposable_ipk = api_fixture_ipks(lambda t: print(t, flush=True))[DISPOSABLE_ID] if args.api_check else None
    host = BeanjarHost.from_config(cfg, args.host or cfg['default'], args.host_mode, args.host_device)
    global SLOWDOWN
    SLOWDOWN = host.slowdown
    args.new_ipk, args.old_ref = args.hc_ipk, None
    outdir = args.outdir or os.path.join(HERE, 'results', '%s-%s' % (vm, time.strftime('%Y%m%d-%H%M%S')))
    os.makedirs(outdir)
    rec = Recorder(outdir)
    host.input_rec = rec if args.input == 'beanjar' or args.input_mode == 'click' else None
    rec.say('artefacts: ' + outdir)
    rec.say('host timing slowdown: %g' % host.slowdown)
    rec.say('Beanjar host: %s (%s%s)' % (host.name, host.mode, ', ssh ' + host.device if host.mode == 'remote' else ''))
    # the scheduler stops a run with SIGTERM or SIGINT; both unwind through the finally below, which stops the VM
    signal.signal(signal.SIGTERM, interrupted)
    started = False
    scenario = None
    old_tmp = None
    try:
        if not shutil.which('ares-do'):
            raise SystemExit('ares-do not on PATH')
        if args.update_check:
            args.old_ref = args.update_from or default_update_ref()
            rc, full = run(['git', '-C', REPO, 'rev-parse', '--short', args.old_ref], 30)
            rec.say('--update-check: old build from git ref %s (%s)' % (args.old_ref, full.strip()))
            args.new_ipk = args.new_ipk or current_home_chooser_ipk(rec, args.build)
            if not args.old_ipk:
                args.old_ipk, old_tmp = build_old_home_chooser(args.old_ref, rec)
            for label, f in (('old', args.old_ipk), ('current', args.new_ipk)):
                rec.say('%s Home Chooser ipk %s version %s' % (label, f, ipk_appinfo(f).get('version')))
        prepare_vm(host, vm, rec)
        started = True  # before the start, so a start interrupted half-way is stopped too
        rc, out = host.start(vm)
        rec.say('beanjar start: %s' % out.strip()[-200:])
        rec.say('waiting for the VM to answer on its LAN address (device entries are created or fixed first)')
        end = time.time() + seconds(120)
        while time.time() < end and (host.info(vm).get('lan_address') in (None, '', '-')):
            sleep(3)
        ensure_devices(host, vm, rec)
        state = wait_for_device(vm, rec)
        if state == NO_ROOT_SSH:
            rec.say(failsafe_note(vm))
            rc, out = host.stop(vm)
            rec.say('beanjar stop: %s' % out.strip()[-200:])
            rc, out = host.start(vm)
            rec.say('beanjar start: %s' % out.strip()[-200:])
            end = time.time() + seconds(120)
            while time.time() < end and (host.info(vm).get('lan_address') in (None, '', '-')):
                sleep(3)
            ensure_devices(host, vm, rec)
            state = wait_for_device(vm, rec)
        if state != READY:
            raise SystemExit('the VM did not come up with Homebrew Channel: %s (%s)' % (state, WAIT_STATES[state]))
        removed_launchers = clean_launchers(Device(vm), rec)
        for index, launcher in enumerate(launchers):
            rec.tag = '[%s]' % launcher.spec if args.multi else ''
            rec.say('==== launcher %s (%s)' % (launcher.spec, launcher.id))
            scenario = Scenario(args, host, rec, row, launcher)
            try:
                if index == 0:
                    restart_after_launcher_cleanup(scenario, removed_launchers)
                scenario.run()
                if index + 1 < len(launchers) and args.until >= 5:
                    # Give every fixture a stock boot. On 6-9, uninstall removes the cached hook
                    # but its bind mounts survive until boot. On 11, a later QML fixture can lose
                    # the Home Chooser page immediately after confirmation in the same boot.
                    if not scenario.beanjar_reboot('between fixtures: stock boot after cleanup'):
                        raise SystemExit('the VM did not return for the next fixture')
                    if scenario.uses_home_key_hook():
                        mounts = scenario.dev.mounts()
                        rec.check('between fixtures: Home key hook mounts are gone',
                                  not mounts, repr(mounts))
            except Exception as e:  # with several launchers: keep going, the failure is recorded
                if not args.multi:
                    raise
                import traceback
                rec.say('runner error in %s:\n%s' % (launcher.spec, traceback.format_exc()))
                rec.check('scenario ran to the end', False, repr(e))
            finally:
                if scenario.insp:
                    scenario.insp.close()
                    scenario.insp = None
    except SystemExit as e:
        rec.say('abort: %s' % e)
        rec.check('runner setup', False, str(e))
    except Exception as e:  # keep the artefacts
        import traceback
        rec.say('runner error:\n' + traceback.format_exc())
        rec.check('scenario ran to the end', False, repr(e))
    finally:
        if scenario and scenario.insp:
            scenario.insp.close()
        if started:
            host.vnc_stop(vm)
        if started and not args.no_stop and vm not in KEEP_LAUNCHER_VMS:
            if scenario and scenario.viewer_opened:
                host.viewer_close(vm)
            host.input_rec = None  # VNC cleanup above also covers --no-stop
            rc, out = host.stop(vm)
            rec.say('beanjar stop: %s' % out.strip()[-200:])
        elif started and vm in KEEP_LAUNCHER_VMS:
            rec.say('%s is left running for the manual restore of its end state' % vm)
        if old_tmp:
            shutil.rmtree(old_tmp, ignore_errors=True)
        counts = rec.finish()
    return 1 if counts.get('FAIL') else 0


if __name__ == '__main__':
    sys.exit(main())
