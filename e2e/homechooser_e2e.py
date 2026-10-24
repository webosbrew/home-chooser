#!/usr/bin/env python3
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

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
APP_ID = 'org.webosbrew.homechooser'
SVC_ID = APP_ID + '.service'
STOCK = 'com.webos.app.home'
CHOICE = '/var/lib/webosbrew/homechooser/home'
FLAG = '/var/lib/webosbrew/homechooser/keyfilter'
INITD = '/var/lib/webosbrew/init.d/homechooser'
OVERRIDE = '/var/webos-profile/override/org.webosbrew.homechooser.json'
APPDIR = '/media/developer/apps/usr/palm/applications/' + APP_ID
HB_ELEVATE = '/media/developer/apps/usr/palm/services/org.webosbrew.hbchannel.service/elevate-service'


def messages_command(path='/var/log/messages'):
    """Read retained rotations oldest first, then the active syslog (webOS uses .N.gz)."""
    quoted = shlex.quote(path)
    # BusyBox 1.29 on webOS 6 has no sort -V. Sort the numeric rotation suffix instead.
    return ("{ for log in $(ls %s.[0-9]*.gz 2>/dev/null | "
            "awk -F. '{print $(NF-1), $0}' | sort -rn | cut -d' ' -f2-); do "
            "zcat \"$log\" 2>/dev/null; done; cat %s; }" % (quoted, quoted))


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
    ('5.x', 'w20l-jhericurl', 'luna', 'verified', 'W20L 04.63.25.01 = 5.6.1; os-release read 2026-10-22; w20h-jhericurl is also webOS 5'),
    ('6.x', 'w21a-kisscurl', 'luna', 'verified', 'W21A 03.53.25.01 = 6.5.2; os-release read 2026-10-23 (w21p-kisscurl, 6.6.0, belongs to the Dock project and is not in the matrix)'),
    ('9.x', 'w24g-ombre', 'luna', 'verified', 'W24G 33.20.23.01 = 9.2.4; os-release read 2026-10-23'),
    ('9.x', 'w24p-ombre', 'luna', 'docs', 'W24P 33.20.23.01 = 9.2.4-4603 per backport/9.2.4; os-release to be read'),
    ('7.x', 'w22p-mullet', 'luna', 'verified', 'W22P 04.60.90.01 = 7.6.0; os-release read 2026-10-22'),
    ('8.x', 'w22o-number1', 'luna', 'verified', 'W22O 13.30.56.01 = 8.3.0; os-release read 2026-10-22 (beanjar lists it as "webOS 23", a platform label)'),
    ('9.x', 'w22h-ombre', 'luna', 'verified', 'W22H 23.21.20.01 = 9.2.1; os-release read'),
    ('10.x', 'w22o-ponytail-us', 'luna', 'verified', 'W22O 33.31.61.01 = 10.3.1; os-release read 2026-10-22'),
    ('11.x', 'w22o-queue-us', 'root-shell', 'verified', 'W22O 43.21.89.01 = 11.2.0; os-release read 2026-10-22; w22o-queue and w22o-queue-jp run the same firmware'),
    ('12.x', 'w24g-spacebuns', 'root-shell', 'verified', 'W24G 02.68.80.01 = 12.0.0; os-release read 2026-10-22'),
]
KEEP_LAUNCHER_VMS = {'w21p-kisscurl'}

# Stock-home preload policy (backport/homechooser-home-watch; docs/settings-app.md "The stock home is
# not preloaded").  On 6.x to 10.x sam relaunches a closed stock home at once as the "last input" app,
# whatever the launcher (measured on 9.2.1, 8.3.0 and 10.3.1, 75 to 89 MB), so since 8791f6e neither the
# watcher nor the app closes it there: the expected result is the preload policy off, the skip line in
# the watcher log, no closeByAppId for it, and the stock home possibly running until the next reboot.
# On 11.x and 12.x the watcher closes it and it must stay closed.  On 5.x there is no preload call.
SKIP_LINE = 'stock home left running'
PRELOAD_SETTLE = 60   # seconds to wait after a switch/boot before sampling (watcher closes at +45 s)
PRELOAD_SAMPLE = 60   # seconds the stock home is watched for
FULLHOME = 'com.webos.app.fullhome'


# ---------------------------------------------------------------------------------------------
# What the product says and writes (pure; e2e/test_e2e_logic.py checks them against the sources)
# ---------------------------------------------------------------------------------------------
DONE_TEXT = 'Done. Press Home to try it.'
# The app has no restart dialog of its own: the service raises a system alert, the app toasts this and,
# while a restart is pending, shows RESTART_NOTE and a "Restart now" row (app/src/views/useHomeChooser.js).
REBOOT_ASKED_TEXT = 'Done. Restart the TV to finish.'
RESTART_NOTE = {'apply': 'The Home button changes after a restart.',
                'remove': 'LG Home is on the Home button now. A restart removes the patch.'}
RESTART_NOW = 'Restart now'
REBOOT_BUTTONS = ('Reboot', 'Later')
# the service's system alert (or the toast when the alert is refused; service/src/policy.c hc_reboot_message)
REBOOT_PROMPT = {'apply': 'Restart the TV to finish setting up the Home button.',
                 'remove': 'Restart the TV to finish removing the Home button patch.'}
# the watcher (backport/homechooser-home-watch): the same on every release, no restart mentioned
REMOVAL_ALERT = '%s was removed, so the Home button opens LG Home again.'
UNINSTALL_TOAST = 'Home Chooser was removed. The Home button opens LG Home again.'
OLD_RESTART_TEXTS = ('Restart the TV to finish the change.', 'Restart the TV to put LG Home back on the Home button.',
                     'The screen will blink once.', 'Restart the TV later to finish.', 'Restart the TV?')
# (text, file relative to the repo that must contain it)
TEXT_SOURCES = [
    (DONE_TEXT, 'app/src/views/useHomeChooser.js'),
    (REBOOT_ASKED_TEXT, 'app/src/views/useHomeChooser.js'),
    (RESTART_NOTE['apply'], 'app/src/views/useHomeChooser.js'),
    (RESTART_NOTE['remove'], 'app/src/views/useHomeChooser.js'),
    (RESTART_NOW, 'app/src/views/MainPanel.js'),
    (REBOOT_PROMPT['apply'], 'service/src/policy.c'),
    (REBOOT_PROMPT['remove'], 'service/src/policy.c'),
    ('$name' + REMOVAL_ALERT[2:], 'backport/homechooser-home-watch'),
    (UNINSTALL_TOAST, 'backport/homechooser-home-watch'),
]


def uses_backport(major):
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
    if uses_backport(major):
        return home == stock_id if stock_id else home in (FULLHOME, STOCK)
    return home in (None, '', 'null')


def stock_baseline_ok(major, home):
    """A stock image before Home Chooser: on 6.x to 9.x the record may be unset (fresh image) or the
    stock id an earlier uninstall left (it stays: stock firmware ignores it)."""
    if uses_backport(major):
        return home in (None, '', 'null', FULLHOME, STOCK)
    return stock_record_ok(major, home)


def expects_reboot_dialog(major, mounted_before):
    """6.x to 9.x: choosing a launcher with the patch not mounted asks for a restart; with the patch
    already mounted (an earlier choice this boot) the switch is live and nothing is asked."""
    return uses_backport(major) and not mounted_before


def parse_reboot_ask(text):
    """The service's REBOOT_ASK PmLog lines (service/src/main.c on_reboot_prompt) -> what was asked and how
    the notification service answered:
    {'message', 'buttons' (list), 'alert' ('accepted'|'refused'|None), 'alert_id', 'toast' ('accepted'|'refused'|None)}.
    The request line carries no token; the replies are the createAlert / createToast lines with their JSON."""
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


def parse_status(text):
    """`homechooser-keyfilter status` output -> {key: value} of its key=value lines."""
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


# ---------------------------------------------------------------------------------------------
# The launcher API (docs/launcher-api.md) as the runner checks it
# ---------------------------------------------------------------------------------------------
API_GROUP = 'homechooser.applications'
API_VERSION = 1
DISPOSABLE_ID = 'org.webosbrew.hcfixture.disposable'
API_FIXTURE_DIR = os.path.join(REPO, 'build', 'fixtures-api')
API_INFO_FIELDS = ('apiVersion', 'version', 'root', 'group', 'permitted', 'isHome')
API_LIST_FIELDS = ('id', 'title', 'icon', 'largeIcon', 'folderPath', 'version', 'vendor', 'type', 'removable', 'visible', 'appCategory')
API_RUNNING_FIELDS = ('id', 'instanceId', 'processid', 'webprocessid')


def api_reply_problems(method, reply):
    """What a successful launcher-API reply lacks or carries beyond the contract; [] when it conforms."""
    if not isinstance(reply, dict):
        return ['no JSON reply']
    if reply.get('returnValue') is not True:
        return ['returnValue is not true: %s %s' % (reply.get('errorCode'), reply.get('errorText'))]
    p = []
    if method == 'getInfo':
        p += ['missing %s' % f for f in API_INFO_FIELDS if f not in reply]
        if reply.get('apiVersion') != API_VERSION:
            p.append('apiVersion %r, expected %d' % (reply.get('apiVersion'), API_VERSION))
        if 'group' in reply and reply['group'] != API_GROUP:
            p.append('group %r' % reply['group'])
        p += ['%s is not a boolean' % f for f in ('root', 'permitted', 'isHome') if f in reply and not isinstance(reply[f], bool)]
        return p
    key, allowed = {'listApps': ('apps', API_LIST_FIELDS), 'running': ('running', API_RUNNING_FIELDS)}[method]
    if reply.get('apiVersion') != API_VERSION:
        p.append('apiVersion %r, expected %d' % (reply.get('apiVersion'), API_VERSION))
    items = reply.get(key)
    if not isinstance(items, list):
        return p + ['%s is not an array' % key]
    for it in items:
        if not isinstance(it, dict) or not it.get('id'):
            p.append('an item without id: %r' % (it,))
            continue
        extra = sorted(set(it) - set(allowed))
        if extra:
            p.append('%s carries fields beyond the contract: %s' % (it['id'], ', '.join(extra)))
    return p


def api_refused(reply):
    """A refusal: a JSON reply with returnValue false (the service's errorCode, or the hub's denial)."""
    return isinstance(reply, dict) and reply.get('returnValue') is False


# ---------------------------------------------------------------------------------------------
# Launchers under test: Dock, one of the six fixtures in e2e/fixtures ({native,qml,web} x
# {normal,overlay}), or any app id with --launcher-ipk.
# ---------------------------------------------------------------------------------------------
DOCK_ID = 'org.webosbrew.dock'
DOCK_IPK = '/home/mariotaku/Projects/webosbrew/Dock.app/dist/org.webosbrew.dock_0.1.0_arm.ipk'  # default; hosts.toml [paths] dock_ipk, --dock-ipk
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
    """--api-check: the web fixtures built with requiredPermissions ["homechooser.applications"] and the
    disposable non-home app, in build/fixtures-api/ (offline, built once).  {app id: ipk}."""
    def found():
        out = {}
        for ident in [FIXTURE_ID % ('web', w) for w in FIXTURE_WINDOWS] + [DISPOSABLE_ID]:
            g = sorted(glob.glob(os.path.join(API_FIXTURE_DIR, ident + '_*.ipk')))
            if g:
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
    """The previous commit that changed app/, service/ or backport/: the last one before HEAD, unless
    those paths have uncommitted changes (then HEAD is the build the working tree departs from)."""
    rc, dirty = run(['git', '-C', REPO, 'status', '--porcelain', '--', 'app', 'service', 'backport'], 30)
    if dirty.strip():
        return 'HEAD'
    rc, out = run(['git', '-C', REPO, 'log', '-1', '--format=%H', 'HEAD~1', '--', 'app', 'service', 'backport'], 30)
    ref = out.strip()
    if rc != 0 or not ref:
        raise SystemExit('cannot find a previous commit touching app/, service/ or backport/; use --update-from')
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


def entry(release_or_vm):
    for m in MATRIX:
        if release_or_vm in (m[0], m[1]):
            return m
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
        if not h.get('local') and not h.get('ssh'):
            raise SystemExit('host %s in %s has neither ssh = "<alias>" nor local = true' % (name, ', '.join(files)))
    cfg['default'] = cfg['default'] or next(iter(cfg['hosts']), None)
    return cfg


# ---------------------------------------------------------------------------------------------
# Running things
# ---------------------------------------------------------------------------------------------
def run(argv, timeout=120, input=None):
    """(rc, stdout+stderr) of a local process."""
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
                           input=input)
        return p.returncode, p.stdout.decode('utf-8', 'replace')
    except subprocess.TimeoutExpired as e:
        return 124, (e.stdout or b'').decode('utf-8', 'replace') + '\n[timeout]'


class BeanjarHost:
    """Every operation on the machine that runs Beanjar goes through here.

    local mode: beanjar and friends run as local commands.  remote mode: each command line is
    run with `ssh <host>` (an ssh config alias; BatchMode, so it never prompts) and files come
    back with scp.  The rest of the runner never quotes for the remote.  The mode comes from the
    host's entry in hosts.toml, never from `beanjar` being on PATH: this PC has Beanjar's own
    multi-host CLI, which is not the machine the VMs and their QMP sockets are on.
    """

    def __init__(self, mode='remote', device='raspberrypi', name='pi'):
        self.mode, self.device, self.name = mode, device, name
        self._dirs = {}

    @classmethod
    def from_config(cls, cfg, name, mode=None, device=None):
        h = cfg['hosts'].get(name)
        if h is None:
            raise SystemExit('unknown Beanjar host %r; hosts: %s (e2e/hosts.toml)' % (name, ', '.join(cfg['hosts'])))
        return cls(mode or ('local' if h.get('local') else 'remote'), device or h.get('ssh') or 'localhost', name)

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
        return self.beanjar('start', vm, timeout=600)

    def stop(self, vm):
        return self.beanjar('stop', vm, timeout=300)

    # -- input and capture
    def sendkey(self, vm, key):
        sock = self.vm_dir(vm) + '/run/qmp.sock'
        cmd = ('(echo \'{"execute":"qmp_capabilities"}\'; echo \'{"execute":"human-monitor-command",'
               '"arguments":{"command-line":"sendkey %s"}}\'; sleep 0.5) | socat - UNIX-CONNECT:%s | tail -1'
               % (key, sock))
        return self.sh(cmd, 30)

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
        self.sh('rm -f ' + tmp)
        return ok, out.strip()

    def screenshot(self, local_path):
        """The Pi desktop (whole Wayland output; no crop geometry, so the viewer window may sit anywhere on it)."""
        tmp = '/tmp/e2e-shot-%d.png' % os.getpid()
        rc, out = self.sh('export XDG_RUNTIME_DIR=/run/user/$(id -u) WAYLAND_DISPLAY=wayland-0; '
                          'grim %s' % tmp, 30)
        ok = rc == 0 and self.fetch(tmp, local_path)
        self.sh('rm -f ' + tmp)
        return ok, out


# ---------------------------------------------------------------------------------------------
# Device access (ares-* only)
# ---------------------------------------------------------------------------------------------
class Device:
    def __init__(self, vm):
        self.vm, self.root = vm, vm + '-root'

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
        end = time.time() + wait
        out = ''
        while time.time() < end:
            out = dev.service_root()
            if out.startswith('root') and 'homechooser-service' in out and 'jailer' not in out:
                break
            time.sleep(3)
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
        time.sleep(2)
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
            time.sleep(2)
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
        and 10.x to 12.x, and after the first failure it is not tried again), then the Pi desktop with
        the viewer window (grim, `*-viewer.png`; the viewer is opened for it on first use).  Each
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
        if not self.viewer_opened:
            rc, out = self.host.viewer_open(self.vm)
            self.viewer_opened = True
            self.rec.say('FALLBACK: beanjar viewer open: rc=%s %s' % (rc, out.strip()[-120:]))
            time.sleep(3)
        vname = name.replace('.png', '-viewer.png')
        ok, out = self.host.screenshot(os.path.join(self.rec.outdir, vname))
        self.rec.say('FALLBACK screenshot %s (grim, whole Pi desktop): %s' % (vname, 'ok' if ok else out.strip()))
        return path

    def press(self, *keys):
        for k in keys:
            if self.a.input == 'qmp':
                qk = {'DOWN': 'down', 'UP': 'up', 'LEFT': 'left', 'RIGHT': 'right', 'OK': 'ret', 'HOME': 'meta_l',
                      'BACK': 'esc'}[k]
                self.host.sendkey(self.vm, qk)
            else:
                ak = {'DOWN': 'DOWN', 'UP': 'UP', 'LEFT': 'LEFT', 'RIGHT': 'RIGHT', 'OK': 'ENTER', 'HOME': 'LEFTMETA',
                      'BACK': 'ESC'}[k]
                self.dev.key(ak)
            time.sleep(0.8)

    def ui(self):
        v = self.insp.evaluate(FOCUS_JS) if self.insp else None
        try:
            return json.loads(v) if v else None
        except ValueError:
            return None

    def wait(self, cond, timeout, every=3):
        end = time.time() + timeout
        v = None
        while time.time() < end:
            v = cond()
            if v:
                return v
            time.sleep(every)
        return v

    def ensure_key_focus(self, rows):
        """Remote keys must reach Home Chooser.  A system popup (seen on w22o-ponytail-us 10.3.1 right
        after every boot: the OLED "screen restoration" popup of com.webos.app.factorywin, buttons
        Start Now (focused by default) and Cancel; BACK does not dismiss it) takes key focus and
        the list never moves.  The runner does NOT press OK on a system popup (Start Now restarts
        the TV for 5 minutes): it records the evidence (a probe key, the oledepl condition, a
        screenshot) and waits up to --wait-popup seconds for someone to dismiss it by hand, then
        fails the run."""
        r = self.rec
        end = time.time() + self.a.wait_popup
        first = True
        while True:
            ui = self.ui()
            if ui is None:
                raise RuntimeError('our page is not readable; not pressing a probe key')
            cur = rows.index(ui['row']) if ui.get('row') in rows else 0
            k1, k2 = ('DOWN', 'UP') if cur + 1 < len(rows) else ('UP', 'DOWN')
            self.press(k1)
            moved = (self.ui() or {}).get('row')
            self.press(k2)
            if moved != ui.get('row'):
                if not first:
                    r.say('key focus restored: the popup was dismissed')
                return True
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
            time.sleep(10)
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

    def uses_backport(self):
        return uses_backport(self.major)

    def uses_override(self):
        return self.major >= 10

    def hook_mounts_match(self, mounts=None):
        rc, out = self.dev.exec(APPDIR + '/backport/homechooser-keyfilter status')
        planned = [line[5:] for line in out.splitlines() if line.startswith('file=')]
        mounted = self.dev.mounts() if mounts is None else mounts
        return rc == 0 and bool(planned) and set(planned) == set(mounted)

    def check_hook_status(self, label):
        rc, out = self.dev.exec(APPDIR + '/backport/homechooser-keyfilter status')
        self.rec.check('%s: minimal hook is mounted' % label,
                       rc == 0 and 'mounted=yes' in out and 'patch=possible' in out, out.strip())

    def kf_status(self):
        """(parsed, raw) of `homechooser-keyfilter status` (6.x to 9.x)."""
        rc, out = self.dev.exec(APPDIR + '/backport/homechooser-keyfilter status')
        return parse_status(out), out.strip()

    # -- nothing restarts while the TV runs (6.x to 9.x; 144ef21)
    BACKPORT_LOG = '/tmp/homechooser-backport.log'
    COMPOSITOR = 'surface-manager-daemon'

    def compositor_snapshot(self):
        d = self.dev
        u = d.unit(self.COMPOSITOR)
        n = d.sh("grep -c 'restarting %s' %s 2>/dev/null || true" % (self.COMPOSITOR, self.BACKPORT_LOG))[1].strip()
        return {'boot': d.boot_id(), 'pid': u.get('MainPID'), 'restarts': u.get('NRestarts'),
                'log_restarts': n.splitlines()[-1] if n else '0', 'wam': d.unit('webapp-mgr'), 'mark': self.messages_mark()}

    def check_no_compositor_restart(self, label, before):
        """The compositor was not restarted since `before` (same boot): same MainPID and NRestarts and no new
        `restarting surface-manager-daemon` line in the backport log (a /tmp file: it starts empty each boot)."""
        if not self.uses_backport() or not before:
            return
        now = self.compositor_snapshot()
        if now['boot'] != before['boot']:
            self.rec.note(label + ': a reboot came in between; the compositor check does not apply', '')
            return
        ok = (now['pid'] == before['pid'] and now['restarts'] == before['restarts']
              and now['log_restarts'] == before['log_restarts'])
        self.rec.check(label + ': no compositor restart (surface-manager-daemon MainPID/NRestarts unchanged, '
                       'no new "restarting %s" line in %s)' % (self.COMPOSITOR, self.BACKPORT_LOG), ok,
                       'before pid=%s NRestarts=%s log=%s; after pid=%s NRestarts=%s log=%s' % (
                           before['pid'], before['restarts'], before['log_restarts'],
                           now['pid'], now['restarts'], now['log_restarts']))

    def check_no_crash(self, label, before):
        """6.x to 9.x: no crashd CRASHED / WebAppMgr crash line since `before`, and within one boot WebAppMgr
        kept its MainPID and NRestarts.  The boot-time apply's restart is the only restart allowed."""
        if not self.uses_backport() or not before:
            return
        d = self.dev
        rc, out = d.sh((messages_command() + " | awk '$1 >= \"%s\"' | grep -iE %s | tail -n 20")
                       % (before['mark'], shlex.quote(CRASH_RE.pattern)))
        bad = crash_lines(out)
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
        end = time.time() + 120
        while time.time() < end and (host.info(self.vm).get('lan_address') in (None, '', '-')):
            time.sleep(3)
        ensure_devices(host, self.vm, r)
        state = wait_for_device(self.vm, r)
        up = state == READY
        r.check(label + ': the VM is back with Homebrew Channel', up, '%s (%s)' % (state, WAIT_STATES[state]))
        if up:
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
        Beanjar shows the VM off (QEMU may power off on a guest reboot), '' when nothing happened."""
        end = time.time() + timeout
        gone = False
        while time.time() < end:
            time.sleep(10)
            try:
                if self.vm not in self.host.running_vms():
                    return 'off'
            except RuntimeError:
                pass
            b = self.dev.boot_id()
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
        return ''

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
        rc, out = self.dev.sh("grep -h configuredHomeAppId /usr/lib/qml/WebOSCompositorBase/global/Settings.qml | head -2")
        return out.strip()

    def assert_stock(self, label, s, baked_emptied=True):
        """The platform is back to stock.  6.x to 9.x: the record names the stock id (not null) and the
        patch stays mounted until the next boot (nothing unmounts or restarts while the TV runs);
        baked_emptied: the watcher ran `refresh stock` (a launcher removal; not after an uninstall,
        where the worker went with the app directory).  10+: null/unset and no override."""
        r = self.rec
        want = {5: 'a stock launcher (%s or %s)' % (FULLHOME, STOCK)}.get(self.major) or (
            'the stock id %s, not null' % self.stock_id if self.uses_backport() else 'null or unset')
        r.check(label + ': record home is %s' % want, self.stock_record_ok(s['record_home']), s['record_raw'])
        if self.uses_override():
            r.check(label + ': profile override absent', s['override'] == 'no', 'override exists: ' + s['override'])
        if self.uses_backport():
            r.check(label + ': backport stays mounted until the next boot', len(s['mounts']) >= 1, 'mounts: %s' % s['mounts'])
            if baked_emptied:
                baked = self.baked_id()
                r.check(label + ': baked id in the mounted Settings.qml is emptied (refresh stock)',
                        '""' in baked and self.launcher not in baked, baked)

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

    def foreign_popup(self, label):
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
        ev = ['getFaultDetectedStatus new fault: %s' % json.dumps(f) for f in real]
        rc, out = self.dev.sh((messages_command() + " | awk '$1 >= \"%s\"' | grep -E '%s' | tail -n 4") % (t_mark, POPUP_LINE_RE))
        ev += [l for l in out.splitlines() if l.strip()]
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
        time.sleep(2)
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
        time.sleep(3)
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
        """6.x to 10.x, any launcher: the watcher leaves the stock home running (8791f6e)."""
        return 6 <= self.major <= 10

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
            if time.time() - t0 + every > seconds:
                break
            time.sleep(every)
        return out

    def check_preload_off(self, label, settle=PRELOAD_SETTLE):
        """A third-party home is chosen: the watcher disabled the stock home's preload; on 11.x and 12.x the
        stock home stays closed, on 6.x to 10.x it is left alone (check_stock_left_running).  5.x: the watcher leaves the policy alone (settings-app.md), so it must not
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
        r.say('%s: settling %d s, then sampling %s for %d s' % (label, settle, STOCK, PRELOAD_SAMPLE))
        time.sleep(settle)
        samples = self.stock_running_samples()
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
        time.sleep(3)

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
            time.sleep(3)
            pre = expect in self.running_ids()
            r.say('%s: %s running before the press: %s%s' % (label, expect, pre,
                  ' (close did not stop it: this check does not discriminate)' if pre else ''))
        if from_app:
            self.front_app()
        if proof:
            popup = self.foreign_popup(label)
            if popup:
                r.check('%s: no foreign LG fault popup before the Home-key proof' % label, False, popup)
                raise RuntimeError('foreign LG fault popup detected; not sending keys')
        mark = self.messages_mark()
        self.press('HOME')
        time.sleep(6)
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
        if not fg and expect == STOCK and not self.sam_home_lines(mark):
            r.note(label + ': no sam record of the Home key after the first press; evidence, then one more press',
                   'messages since %s mentioning key/home:\n%s' % (mark, '\n'.join(
                       self.sam_home_lines(mark, 'homeKey|launchDefaultApp|keyfilter|com.webos.app.home'))))
            self.shot(label + '-first-press')
            mark = self.messages_mark()
            self.press('HOME')
            time.sleep(6)
            fg = self.wait(seen, 20, 3)
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
        steps = [self.step1, self.step2, self.step3, self.step4, self.step5]

        def guarded(fn, *a):
            # 6.x to 9.x: no crash of WebAppMgr (or anything crashd reports) during the step
            snap = self.compositor_snapshot() if self.uses_backport() else None
            fn(*a)
            self.check_no_crash('end of step', snap)
        try:
            for i, fn in enumerate(steps, 1):
                if i > self.a.until:
                    break
                guarded(fn, strat) if i == 1 else guarded(fn)
                if i == 3 and self.a.api_check:
                    guarded(self.step_api)
                if i == 3 and self.a.reboot_check:
                    guarded(self.step_reboot)
                if i == 3 and self.a.update_check:
                    guarded(self.step_update)
        finally:
            if self.a.until >= 5:
                self.step6()
            else:
                r.say('--until %d: apps left installed for debugging; no cleanup' % self.a.until)

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
        time.sleep(4)  # the result toast of a left marker is set just after the list appears
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
        time.sleep(3)
        self.launch_mark = self.messages_mark()
        d.launch(APP_ID)
        toasts = []

        def listed_stale():
            u = self.ui()
            if u and u.get('toast'):
                toasts.append(u['toast'])
            return u if u and self.launcher in (u.get('rows') or []) else None
        self.wait(listed_stale, 120, every=1)
        time.sleep(3)
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
        if ui.get('dialog') and ui.get('text') != 'Use as Home':
            self.press('UP')
            ui = self.ui() or {}
        r.check('focus is on "Use as Home"', ui.get('text') == 'Use as Home', json.dumps(ui))
        if ui.get('text') != 'Use as Home':
            raise RuntimeError('focus is not on "Use as Home" in our dialog; not pressing OK')
        mounted_before = bool(d.mounts()) if self.uses_backport() else False
        comp0 = self.compositor_snapshot() if self.uses_backport() else None
        if self.uses_backport():
            r.note('patch mounted before choosing: %s (a restart prompt is %s)' % (
                mounted_before, 'expected' if expects_reboot_dialog(self.major, mounted_before) else 'not expected'), '')
        self.ask_mark = self.messages_mark()
        self.press('OK')
        # the choice is applied; nothing restarts the compositor any more, so the panel stays open
        toasts = []

        def chosen():
            u = self.ui() or {}
            if u.get('toast'):
                toasts.append(u['toast'])
            return self.state()['choice'] == self.launcher
        self.wait(chosen, 90, 2)
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
            if self.uses_backport():
                # the patch was mounted already (an earlier choice in this boot): a live switch
                self.check_no_compositor_restart('choosing with the patch mounted (a live switch)', comp0)
                st, raw = self.kf_status()
                r.check('keyfilter status: mounted=yes and reboot=no (nothing to ask)',
                        st.get('mounted') == 'yes' and st.get('reboot') == 'no', raw)
                self.wait(lambda: self.launcher in self.baked_id(), 60, 5)
                baked = self.baked_id()
                r.check('the watcher baked the launcher id into the mounted Settings.qml (refresh, no restart)',
                        self.launcher in baked, baked)
        self.wait(lambda: self.watcher_active() == 'active' and (not self.uses_backport() or self.hook_mounts_match()), 90)
        self.residency('right after "Use as Home" (settled)')
        s = self.state()
        r.note('state after choosing', json.dumps(s))
        r.check('choice file holds the launcher id', s['choice'] == self.launcher, repr(s['choice']))
        r.check('general/defaultApps record home is the launcher', s['record_home'] == self.launcher, s['record_raw'])
        r.check('watcher unit homechooser-home is active', s['watcher'] == 'active', s['watcher'])
        r.check('init.d entry is a symlink into the installed app',
                s['initd'] == APPDIR + '/backport/homechooser', s['initd'])
        if self.uses_backport():
            r.check('backport mounts are present (webOS 6-9%s)' % (', after the boot' if not mounted_before else ''),
                    self.hook_mounts_match(s['mounts']), 'mounts: %s' % s['mounts'])
            r.check('backport flag file exists', s['flag'] == 'yes', s['flag'])
            self.check_hook_status('after selection' + (' and the boot' if not mounted_before else ''))
        else:
            r.check('no backport on this release', not s['mounts'] and s['flag'] == 'no',
                    'mounts: %s flag: %s' % (s['mounts'], s['flag']))
        if self.uses_override():
            r.check('profile override exists (webOS 10+)', s['override'] == 'yes', s['override'])
            r.note('override', d.sh('cat %s' % OVERRIDE)[1])
        if not self.a.update_check:  # --update-check: step 1 installed the old build; checked in 3u
            self.check_device_texts()
        # open the panel again after the apply (a cut-short run left its marker): no error, the choice ticked
        if not self.insp:
            self.insp = Inspector(self.vm)
        d.close(APP_ID)
        time.sleep(3)
        d.launch(APP_ID)
        seen = []

        def listed():
            u = self.ui()
            if u and u.get('toast'):
                seen.append(u['toast'])
            return u if u and self.launcher in (u.get('rows') or []) else None
        again = self.wait(listed, 120, every=1)
        time.sleep(1)
        again = self.ui() or again or {}
        r.note('toasts seen while re-opening', repr(seen + [again.get('toast')]))
        again['toast'] = again.get('toast') or next((t for t in seen if t.startswith("Couldn't")), None)
        self.shot('reopened')
        r.check('re-open after the apply: no error toast', not (again.get('toast') or '').startswith("Couldn't"),
                'toast: %r' % again.get('toast'))
        ticked = self.insp.evaluate(TICK_JS) if self.insp else None
        r.check('re-open after the apply: the chosen row is ticked', ticked == self.launcher,
                'ticked rows: %r; DOM: %s' % (ticked, json.dumps(again)))
        d.close(APP_ID)
        if self.insp:
            self.insp.close()
            self.insp = None
        self.check_preload_off('after the switch')

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
        """The REBOOT_ASK lines of the service since the choice was confirmed (PmLog -> /var/log/messages)."""
        rc, out = self.dev.sh((messages_command() + " | awk '$1 >= \"%s\"' | grep REBOOT_ASK | tail -n 8")
                              % getattr(self, 'ask_mark', ''))
        return out

    def close_reboot_alert(self, label, info):
        """Our restart alert: closed over luna with the alertId from its createAlert reply (the same as the
        removal alert; no key is pressed, so no foreign popup can take it).  True when closeAlert was accepted."""
        r = self.rec
        if info['alert'] != 'accepted' or not info['alert_id']:
            r.note('%s: no accepted createAlert with an alertId in the REBOOT_ASK log; nothing to close' % label,
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
        service's REBOOT_ASK log lines, that a createAlert for the restart prompt was sent with the buttons
        Reboot and Later (or, when the alert was refused, that the toast fallback was shown).

        --reboot-via later (default): our alert is closed with closeAlert and the VM is restarted with
        beanjar stop/start.  No key is pressed.
        --reboot-via alert: pressing Reboot on the alert is MANUAL.  The runner cannot prove, without
        reading the picture, that the alert in front is ours and that focus is on Reboot: no luna method
        lists or reports the focused alert (notificationmgr has no list-alerts; the createAlert reply is only
        an alertId), the alert's own default focus is Later, and the inspector page of Home Chooser, which
        proved ownership of the old in-app dialog, is not in front of a system alert.  So it presses nothing;
        a person presses Reboot (the runner waits for the new boot id), else the alert is closed and the VM is
        restarted with beanjar, like the default."""
        r, d = self.rec, self.dev
        toasts = []

        def asked():
            u = self.ui() or {}
            if u.get('toast') and u['toast'] not in toasts:
                toasts.append(u['toast'])  # gone after 6 s
            info = parse_reboot_ask(self.reboot_ask_log())
            return info if info['alert'] else None
        info = self.wait(asked, 90, 2) or parse_reboot_ask(self.reboot_ask_log())
        if info['alert'] == 'refused':
            self.wait(lambda: parse_reboot_ask(self.reboot_ask_log())['toast'] == 'accepted', 40, 3)
            info = parse_reboot_ask(self.reboot_ask_log())
        ui = self.ui()
        if not info['alert'] and self.a.update_check and d.mounts():
            r.note('no restart prompt and the patch is mounted: the OLD build of --update-check mounts live (before 144ef21)', '')
            return
        self.shot('2-reboot-alert')
        raw_log = self.reboot_ask_log()
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
        r.check('before the reboot: keyfilter status says mounted=no and reboot=needed',
                st.get('mounted') == 'no' and st.get('reboot') == 'needed', raw)
        r.check('before the reboot: nothing is mounted', not s['mounts'], 'mounts: %s' % s['mounts'])
        r.check('before the reboot: the flag file and the init.d entry are written',
                s['flag'] == 'yes' and s['initd'] == APPDIR + '/backport/homechooser', 'flag %s; init.d %s' % (s['flag'], s['initd']))
        r.check('before the reboot: the record home is the launcher', s['record_home'] == self.launcher, s['record_raw'])
        self.check_no_compositor_restart('choosing the launcher', comp0)
        boot0 = d.boot_id()
        self.close_insp()
        if self.a.reboot_via == 'alert':
            r.manual('press Reboot on the restart alert', 'the runner cannot prove that the alert in front is ours with focus on '
                     'Reboot without reading the picture (see 2-reboot-alert), so it presses no key. Press Reboot by hand within '
                     '240 s; otherwise the alert is closed and the VM is restarted with beanjar stop/start.')
            how = self.wait_reboot(boot0)
            if how in ('rebooted', 'off'):
                r.check('Reboot restarted the TV (rebootConfirmed -> tvpower/power/reboot; a VM that powers off counts)', True, how)
                if how == 'off':
                    r.note('the VM powered off instead of rebooting (QEMU -no-reboot?); starting it with beanjar', '')
                    self.host.start(self.vm)
                    up = self.await_boot('2 reboot (Reboot on the alert; VM started again)', boot0)
                else:
                    up = self.await_boot('2 reboot (Reboot on the alert)', boot0)
            else:
                r.manual('Reboot on the alert restarted the TV', 'no new boot id within 240 s: nobody pressed Reboot')
                self.close_reboot_alert('2 reboot', info)
                up = self.beanjar_reboot('2 reboot (alert left alone, beanjar stop/start)')
        else:
            self.close_reboot_alert('2 reboot (Later)', info)
            time.sleep(3)
            self.shot('2-reboot-alert-closed')
            up = self.beanjar_reboot('2 reboot (alert closed, then beanjar stop/start)')
        if not up:
            raise RuntimeError('the VM did not come back after the reboot')
        self.after_boot_checks('after the reboot')

    def after_boot_checks(self, label):
        """6.x to 9.x after a boot with a launcher chosen: the init.d hook started the watcher and mounted
        the patch (the boot apply, with its one firmware-order compositor restart)."""
        r = self.rec
        active = self.wait(lambda: self.watcher_active() == 'active', 180, 5)
        r.check(label + ': the watcher is active (started from init.d)', bool(active), self.watcher_active())
        self.wait(lambda: self.hook_mounts_match(), 180, 5)
        st, raw = self.kf_status()
        r.check(label + ': the boot hook mounted the patch: status mounted=yes and reboot=no',
                st.get('mounted') == 'yes' and st.get('reboot') == 'no', raw)
        r.note(label + ': backport log of the boot apply (its compositor restart is the one allowed)',
               self.dev.sh('tail -n 20 %s 2>&1' % self.BACKPORT_LOG)[1])
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
        watch = [APPDIR + '/backport/homechooser-home-watch', '/tmp/homechooser-runtime/homechooser-home-watch']
        svcdir = '/media/developer/apps/usr/palm/services/' + SVC_ID

        def has(text, paths):
            rc, out = d.sh('grep -rlF %s %s 2>/dev/null' % (shlex.quote(text), ' '.join(paths)))
            return [l for l in out.split() if l.startswith('/')]
        for text in (REMOVAL_ALERT[2:], UNINSTALL_TOAST):
            got = has(text, watch)
            r.check('device texts: the watcher says "%s"' % text.strip(), len(got) == 2, 'found in %s' % got)
        old = [(t, has(t, watch + [APPDIR])) for t in OLD_RESTART_TEXTS]
        r.check('device texts: no old restart wording in the watcher or the app', not any(g for _, g in old),
                '; '.join('%r in %s' % (t, g) for t, g in old if g) or 'none')
        for reason, text in sorted(REBOOT_PROMPT.items()):
            got = has(text, [svcdir])
            r.check('device texts: the service reboot prompt (%s) is "%s"' % (reason, text), bool(got), 'found in %s' % got)
        for reason, text in sorted(RESTART_NOTE.items()):
            got = has(text, [APPDIR])
            r.check('device texts: the app restart note (%s) says "%s"' % (reason, text), bool(got), 'found in %s' % got)
        got = has(REBOOT_ASKED_TEXT, [APPDIR])
        r.check('device texts: the app toast says "%s"' % REBOOT_ASKED_TEXT, bool(got), 'found in %s' % got)

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
        up = self.beanjar_reboot('3r')
        if not up:
            return
        if self.uses_backport():
            self.after_boot_checks('after the boot')  # the watcher, the boot hook's mounts, status
        else:
            active = self.wait(lambda: self.watcher_active() == 'active', 180, 5)
            r.check('after the boot the watcher is active (started from init.d)', bool(active), self.watcher_active())
        s = self.state()
        r.check('after the boot the choice and the record are still the launcher',
                s['choice'] == self.launcher and s['record_home'] == self.launcher, s['record_raw'])
        if self.major >= 6:
            # the call comes at once, the close after the 45 s settle
            self.wait(lambda: ('closeByAppId' in self.preload_log()) or (self.stock_left_running() and SKIP_LINE in self.preload_log()), 150, 5)
            log = self.preload_log()
            r.check('after the boot the watcher called setPreloadPolicy isEnabled=false (log is from this boot: /tmp)',
                    'isEnabled=false' in log, log)
        else:
            r.check('after the boot (5.x) the watcher made no preload call', 'setPreloadPolicy' not in self.preload_log(),
                    self.preload_log())
        self.log_mark = 0  # a new boot, a new /tmp log
        self.check_preload_off('after the reboot', settle=30)
        if self.uses_backport():
            self.check_hook_status('after reboot')
        self.residency('after the reboot')
        self.shot('3r-after-reboot')
        if self.a.api_check:
            self.api_after_reboot()

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
        self.rec.check('%s: %s as %s succeeds with the documented fields only' % (label, method, self.launcher), not probs,
                       '%s\n%s' % ('; '.join(probs) or 'conforms', raw[:800]))
        return rep or {}

    def api_kit_note(self):
        if self.L.is_fixture:
            return 'the web fixture built with requiredPermissions [%s]' % API_GROUP
        return 'an author launcher (%s, ipk %s): fixture-only parts are skipped' % (self.launcher, self.L.ipk)

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
               d.exec('sh %s/backport/homechooser-access status 2>&1' % APPDIR)[1].strip())
        # the app's accessApply runs while the list loads: allow it a moment (listApps needs the grant)
        self.wait(lambda: (self.api('listApps')[0] or {}).get('returnValue') is True, 30, 3)
        info = self.api_conforms('getInfo', 'before selection')
        r.check('before selection: getInfo says root true, permitted true, isHome false',
                info.get('root') is True and info.get('permitted') is True and info.get('isHome') is False, json.dumps(info))
        apps = self.api_conforms('listApps', 'before selection')
        ids = [a.get('id') for a in apps.get('apps') or [] if isinstance(a, dict)]
        r.check('listApps names the launcher and the disposable app', self.launcher in ids and DISPOSABLE_ID in ids,
                '%d apps; launcher %s, disposable %s' % (len(ids), self.launcher in ids, DISPOSABLE_ID in ids))
        self.api_conforms('running', 'before selection')
        # an installed app without the permission
        for m, payload in (('listApps', {}), ('running', {}), ('closeById', {'id': APP_ID}), ('uninstall', {'id': self.launcher})):
            rep, raw = self.api(m, payload, app=DISPOSABLE_ID)
            r.check('%s as %s (no %s in its appinfo) is refused' % (m, DISPOSABLE_ID, API_GROUP), api_refused(rep),
                    raw[:400])
        rep, raw = self.api('getInfo', app=DISPOSABLE_ID)
        r.check('getInfo as the app without the permission: permitted false, or refused by the hub',
                api_refused(rep) or (rep or {}).get('permitted') is False, raw[:400])
        rep, raw = self.api('listApps', app=None)
        r.check('listApps without an application id (a plain root luna-send) is refused', api_refused(rep), raw[:400])
        for m in ('closeById', 'uninstall'):
            rep, raw = self.api(m, {'id': DISPOSABLE_ID})
            r.check('%s as %s while it is not the selected Home: refused with notHome' % (m, self.launcher),
                    api_refused(rep) and rep.get('errorCode') == 'notHome', raw[:400])
        rep, raw = self.api('uninstallConfirmed', {'token': '0' * 32})
        r.check('uninstallConfirmed as the launcher is refused (only Home Chooser or the notification service)',
                api_refused(rep), raw[:400])
        self.api_request(selected=False)

    def api_request(self, selected):
        """The `request` launch parameter: Home Chooser opens "Use <title> as Home?" for the requesting
        launcher (cancelled with Back, on our own dialog); once it is Home, the "already your Home" toast."""
        r, d = self.rec, self.dev
        choice0 = self.state()['choice']
        if not self.insp:
            self.insp = Inspector(self.vm)
        rc, out = d.launch(APP_ID, {'request': self.launcher})
        r.check('ares-launch %s -p {"request":"%s"}' % (APP_ID, self.launcher), rc == 0, out.strip())
        if selected:
            toast = self.wait(lambda: (lambda t: t if t and 'already your Home' in t else None)((self.ui() or {}).get('toast')), 30, 1)
            self.shot('3a-request-selected')
            r.check('request while it is Home: the toast "<title> is already your Home."', bool(toast), repr(toast))
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
        time.sleep(2)
        r.check('request cancelled: the choice is unchanged', self.state()['choice'] == choice0, repr(self.state()['choice']))

    def step_api(self):
        """The launcher is the selected Home (after step 3; on 6-9 after the boot that mounted the patch)."""
        r, d = self.rec, self.dev
        r.begin('3a launcher API with the launcher selected (--api-check)')
        comp0 = self.compositor_snapshot() if self.uses_backport() else None
        info = self.api_conforms('getInfo', 'selected')
        r.check('selected: getInfo says root true, permitted true, isHome true',
                info.get('root') is True and info.get('permitted') is True and info.get('isHome') is True, json.dumps(info))
        self.api_conforms('listApps', 'selected')
        self.api_conforms('running', 'selected')
        # closeById closes a running app
        d.launch(DISPOSABLE_ID)
        running = self.wait(lambda: DISPOSABLE_ID in self.running_ids(), 30, 2)
        r.check('the disposable app is running before closeById', bool(running), str(self.running_ids()))
        rep, raw = self.api('closeById', {'id': DISPOSABLE_ID})
        r.check('closeById {id:%s} as the selected Home is accepted' % DISPOSABLE_ID,
                (rep or {}).get('returnValue') is True and rep.get('id') == DISPOSABLE_ID, raw[:400])
        closed = self.wait(lambda: DISPOSABLE_ID not in self.running_ids(), 20, 2)
        r.check('closeById closed the app (not in applicationManager/running)', bool(closed), str(self.running_ids()))
        rep, raw = self.api('closeById', {})
        r.check('closeById without an id: badRequest', api_refused(rep) and rep.get('errorCode') == 'badRequest', raw[:400])
        # the uninstall refusal list
        for target, why in ((APP_ID, 'Home Chooser itself'), ('org.webosbrew.hbchannel', 'Homebrew Channel'),
                            (self.launcher, 'the requesting launcher')):
            rep, raw = self.api('uninstall', {'id': target})
            r.check('uninstall of %s is refused (notAllowed)' % why, api_refused(rep) and rep.get('errorCode') == 'notAllowed',
                    raw[:400])
        self.api_uninstall_cancel()
        self.api_uninstall_confirm()
        self.api_request(selected=True)
        self.check_no_compositor_restart('the launcher API calls', comp0)

    def uninstall_ask(self, label):
        """uninstall {id: disposable} as the Home: (confirm route 'alert'|'screen'|None, reply)."""
        r = self.rec
        self.uninstall_mark = self.messages_mark()
        rep, raw = self.api('uninstall', {'id': DISPOSABLE_ID})
        how = (rep or {}).get('confirm')
        r.check('%s: uninstall {id:%s} replies pending with a confirm route' % (label, DISPOSABLE_ID),
                (rep or {}).get('returnValue') is True and rep.get('pending') is True and how in ('alert', 'screen'), raw[:400])
        return how if (rep or {}).get('returnValue') is True else None

    def uninstall_alert_id(self):
        """The alertId of the service's createAlert, from its UNINSTALL_ASK log line (PmLog), if logged."""
        rc, out = self.dev.sh((messages_command() + " | awk '$1 >= \"%s\"' | grep UNINSTALL_ASK | tail -n 3")
                              % self.uninstall_mark)
        m = re.findall(r'\\?"alertId\\?"\s*:\s*\\?"([^"\\]+)', out)
        return (m[-1] if m else None), out.strip()

    def screen_dialog(self):
        if not self.insp:
            self.insp = Inspector(self.vm)
        return self.wait(lambda: (lambda u: u if (u.get('dialog') or '').startswith('Uninstall ') else None)(self.ui() or {}),
                         60, 1) or {}

    def dismiss_alert(self, label):
        """Our createAlert: closed with closeAlert when its id is in the log, else with Back (never OK)."""
        r = self.rec
        time.sleep(2)
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
        time.sleep(3)
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
        time.sleep(5)
        r.check('after the cancel the disposable app is still installed', d.app_installed(DISPOSABLE_ID), DISPOSABLE_ID)

    def api_uninstall_confirm(self):
        """The confirm path, only where the runner presses a key on its own screen: the screen route. On the
        alert route confirming means pressing Uninstall on a system popup: MANUAL."""
        r, d = self.rec, self.dev
        how = self.uninstall_ask('confirm')
        if how == 'alert':
            self.dismiss_alert('3a-uninstall-alert-2')
            r.manual('uninstall confirm path on the alert route (Uninstall on the system alert -> uninstallConfirmed '
                     '-> removal)', 'the runner never presses OK on a system popup and the token is only in the alert; '
                     'by hand: call uninstall as the launcher, press Uninstall, then check getAppInfo %s fails' % DISPOSABLE_ID)
            return
        if how != 'screen':
            return
        ui = self.screen_dialog()
        ok, why = self.ours_in_front('Uninstall *')
        r.check('confirm: the "Uninstall ...?" dialog is ours and in front', ok, why)
        if not ok:
            return
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
        r.check('after the reboot: getInfo says root true, permitted true, isHome true',
                info.get('root') is True and info.get('permitted') is True and info.get('isHome') is True, json.dumps(info))
        self.api_conforms('listApps', 'after the reboot')
        self.api_conforms('running', 'after the reboot')

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
        """Top-level watcher processes (a forked subshell shares the command line, so only a process
        whose parent is not itself a watcher counts) and the unit's own numbers."""
        d = self.dev
        top = d.sh("ps -eo pid,ppid,args | awk '/homechooser-home-watch/ && !/awk/ && !/grep/ {pid[$1]=1; pp[$1]=$2} "
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
        time.sleep(4)
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
        s['runtime_md5'] = self.md5('/tmp/homechooser-runtime/homechooser-home-watch')
        s['app_md5'] = self.md5(APPDIR + '/backport/homechooser-home-watch')
        s['app_version'] = d.sh("sed -n 's/.*\"version\": *\"\\([^\"]*\\)\".*/\\1/p' %s/appinfo.json | head -1" % APPDIR)[1].strip()
        s['baked'] = self.baked_id() if self.uses_backport() else ''
        return s

    def step_update(self):
        """The Home Chooser update: the current build installed over the old one with ares-install and no
        uninstall in between.  Design basis (backport/homechooser-home-watch, header and check_hc; docs/settings-app.md
        "When apps are removed"): an update installs by removing first, so the watcher takes Home Chooser as gone only
        if it is still gone HOMECHOOSER_CONFIRM_DELAY (15 s) later (check_hc: "Home Chooser is back, not removing
        anything"); no notification is sent "for the temporary absence of an update"; the watcher runs from a copy in
        /tmp/homechooser-runtime, so the app directory being replaced does not pull the script from under it, and the
        unit is NOT restarted by the update: one watcher, the same MainPID, still the old build's code until its next
        start (boot or restart).  So the log must show no restore and no \"watching, home=\" line from the update; a
        "Home Chooser is back" line appears only if the watcher happened to look during the gap."""
        r, d = self.rec, self.dev
        r.begin('3u update Home Chooser over the old build (--update-check)')
        new_files, old_files = ipk_files(self.hc_new), ipk_files(self.a.old_ipk)
        wk = 'usr/palm/applications/%s/backport/homechooser-home-watch' % APP_ID
        r.check('both ipks carry the watcher script', wk in new_files and wk in old_files, wk)
        new_md5 = hashlib.md5(new_files.get(wk, b'')).hexdigest()
        old_md5 = hashlib.md5(old_files.get(wk, b'')).hexdigest()
        r.note('old build %s (%s) vs new build %s: watcher script md5 old=%s new=%s' % (
            self.a.old_ref, os.path.basename(self.a.old_ipk), os.path.basename(self.hc_new), old_md5, new_md5),
            'identical scripts: the script-level checks do not discriminate' if old_md5 == new_md5 else 'scripts differ')
        before = self.update_snapshot()
        r.note('state before the update', json.dumps(before))
        r.check('before the update: exactly one watcher unit and one watcher process',
                before['watcher_procs']['top'] == 1 and before['watcher_procs']['units'] == '1', json.dumps(before['watcher_procs']))
        r.check('before the update: the running watcher is the old build (runtime copy md5 = old script)',
                before['runtime_md5'] == old_md5, 'runtime %s, old %s' % (before['runtime_md5'], old_md5))
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
        r.check('after the update: the app directory holds the new build (watcher script md5 = new, version %s)' % after['app_version'],
                after['app_md5'] == new_md5, 'device %s, new %s' % (after['app_md5'], new_md5))
        r.check('after the update: choice file unchanged', after['choice'] == before['choice'] == self.launcher, repr(after['choice']))
        r.check('after the update: record home is still the launcher', after['record_home'] == self.launcher, after['record_raw'])
        wp = after['watcher_procs']
        r.check('after the update: watcher unit active, exactly ONE watcher unit and ONE process (no duplicate)',
                after['watcher'] == 'active' and wp['top'] == 1 and wp['units'] == '1', json.dumps(wp))
        r.check('after the update: the watcher was not restarted (same MainPID, NRestarts unchanged: the design keeps it running from /tmp)',
                wp['main_pid'] == before['watcher_procs']['main_pid'] and wp['restarts'] == before['watcher_procs']['restarts'],
                'before %s, after %s' % (json.dumps(before['watcher_procs']), json.dumps(wp)))
        r.check('after the update: no \"watching, home=\" line (no watcher restart logged)',
                not any('watching, home=' in l for l in new_log), '\n'.join(l for l in new_log if 'watching' in l) or 'none')
        bad = [l for l in new_log if re.search(r'stock Home restored|is not installed, stock Home|isEnabled=true|createAlert|createToast|revert', l)]
        r.check('after the update: the watcher log shows no restore, no revert, no notification', not bad, '\n'.join(bad) or 'none')
        r.note('after the update: "Home Chooser is back" (only logged if the watcher looked during the gap)',
               '\n'.join(l for l in new_log if 'is back' in l) or 'not logged')
        r.note('after the update: which build the running watcher is (runtime copy md5; the design leaves it the old one until its next start)',
               'runtime %s; old %s; new %s' % (after['runtime_md5'], old_md5, new_md5))
        r.check('after the update: init.d entry is a symlink into the installed app and its target exists',
                after['initd'] == APPDIR + '/backport/homechooser' and d.sh('test -f %s && echo y' % INITD)[1].strip() == 'y', after['initd'])
        if self.uses_backport():
            r.check('after the update: backport mounts unchanged', sorted(after['mounts']) == sorted(before['mounts']) and bool(after['mounts']),
                    'before %s after %s' % (before['mounts'], after['mounts']))
            r.check('after the update: backport flag still exists', after['flag'] == 'yes', after['flag'])
            r.check('after the update: the launcher id is still baked into the mounted Settings.qml', self.launcher in after['baked'], after['baked'])
        else:
            r.check('after the update: no backport mounts or flag on this release', not after['mounts'] and after['flag'] == 'no',
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
        r.note('messages since the install mentioning Home Chooser (sam/appinstalld)', d.sh(
            (messages_command() + " | awk '$1 >= \"%s\"' | grep -F %s | tail -n 12") % (t_install, APP_ID))[1])

    def step4(self):
        r, d = self.rec, self.dev
        r.begin('4 sudden launcher uninstall')
        self.popup_mark = self.popup_mark_now()
        alerts_before = len(self.notif_lines('createAlert'))
        comp0 = self.compositor_snapshot() if self.uses_backport() else None
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
        comp0 = self.compositor_snapshot() if self.uses_backport() else None
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
        if self.uses_backport():
            r.check('on return the backport is still mounted', bool(s['mounts']), 'mounts: %s' % s['mounts'])
            self.wait(lambda: self.launcher in self.baked_id(), 60, 5)
            baked = self.baked_id()
            r.check('on return the id is baked into the mounted Settings.qml again',
                    self.launcher in baked, baked)
            # stock -> launcher with the patch mounted: the live switch
            self.check_no_compositor_restart('on return (a switch with the patch mounted)', comp0)
        self.wait(lambda: self.major <= 5 or self.dev.preload_policy()[0] is False, 60, 5)
        if self.major >= 6:
            enabled, raw = d.preload_policy()
            r.check('on return the stock home policy is disabled again (isEnabled:false)', enabled is False,
                    'isEnabled=%r; %s' % (enabled, raw))
        self.home_press(self.launcher, '4-home-launcher-back')

    def step5(self):
        r, d = self.rec, self.dev
        r.begin('5 uninstall Home Chooser')
        toasts_before = len(self.notif_lines('createToast'))
        comp0 = self.compositor_snapshot() if self.uses_backport() else None
        rc, out = d.remove(APP_ID)
        r.check('ares-install --remove Home Chooser', rc == 0, out.strip())
        self.check_toast(toasts_before)
        self.wait(lambda: self.watcher_active() != 'active' and self.stock_record_ok(d.record_home()[0]), 150, 5)
        time.sleep(5)
        s = self.state()
        r.note('state after uninstall', json.dumps(s))
        self.residency('after the Home Chooser uninstall')
        r.check('choice file removed', 'No such file' in s['choice'], repr(s['choice']))
        # 6-9: the record gets the stock id and the patch stays mounted (no unmount, no restart) until the
        # next boot; the baked id is left as it was (the worker went with the app directory)
        self.assert_stock('after uninstall', s, baked_emptied=False)
        self.check_no_compositor_restart('after the Home Chooser uninstall', comp0)
        self.check_preload_on('after the Home Chooser uninstall')
        r.check('init.d entry removed', 'No such file' in s['initd'] or s['initd'] == '', repr(s['initd']))
        r.check('backport flag removed', s['flag'] == 'no', s['flag'])
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
            for app in apps:
                if app in d.installed():
                    rc, out = d.remove(app)
                    r.say('removed %s: %s' % (app, out.strip()))
                    removed_hc = removed_hc or app == APP_ID
            r.check('no test apps left installed', not (set(apps) & set(d.installed())),
                    str(d.installed()))
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
    end = time.time() + timeout
    state = None
    while True:
        rc, out = dev.sh('echo up', 20)
        if out.strip().endswith('up'):
            now = READY if 'org.webosbrew.hbchannel' in dev.installed() else NO_HBCHANNEL
        else:
            # the devmode device (prisoner, 9922) is Developer Mode's own Dropbear, not HBC's
            now = NO_ROOT_SSH if 'org.webosbrew.hbchannel' in dev.installed() else DOWN
        if now != state:
            state = now
            rec.say('VM %s: %s (%s)' % (vm, state, WAIT_STATES[state]))
        if state == READY or time.time() >= end:
            return state
        time.sleep(5)


def failsafe_note(vm):
    return ("root SSH never answered while developer mode did: Homebrew Channel's startup.sh is most likely in "
            "failsafe mode (telnetd only, no Dropbear). It sets /var/luna/preferences/webosbrew_failsafe at the start "
            "of a boot and clears it about 12 s after its boot work, so a boot cut short before that (a VM stopped "
            "within seconds of starting) leaves the flag and the next boot runs failsafe; that boot clears the flag "
            "again after 15 s. Restarting %s once." % vm)


def interrupted(*_):
    raise KeyboardInterrupt


def main():
    ap = argparse.ArgumentParser(description='Home Chooser end-to-end test on one Beanjar VM')
    ap.add_argument('--vm', help='a VM name or a release (e.g. w22h-ombre or 9.x)')
    ap.add_argument('--launcher', default=None,
                    help="'dock' (default; with --api-check fixture:web.normal), 'fixture:<native|qml|web>.<normal|overlay>', "
                         "or an app id with --launcher-ipk (an author's own launcher)")
    ap.add_argument('--launchers', default=None,
                    help="'all-fixtures' or a comma list of --launcher values: the scenario runs once per launcher on the "
                         "same VM, in sequence, with cleanup between")
    ap.add_argument('--launcher-ipk', default=None, help='ipk of --launcher when it is an app id (or to replace Dock\'s)')
    ap.add_argument('--update-check', action='store_true',
                    help='install an older Home Chooser first, choose the launcher, then install the current build over it '
                         '(step 3u) and check that everything survives')
    ap.add_argument('--update-from', default=None,
                    help='git ref of the older Home Chooser (default: the previous commit that changed app/, service/ or backport/)')
    ap.add_argument('--host', default=None,
                    help="the Beanjar machine the VM is on, a name in e2e/hosts.toml (default: the file's default, 'pi')")
    ap.add_argument('--hosts-file', default=None, help='a hosts file instead of e2e/hosts.toml + e2e/hosts.local.toml')
    ap.add_argument('--host-mode', choices=['local', 'remote'], default=None,
                    help="override the host's mode: beanjar run here, or over ssh")
    ap.add_argument('--host-device', default=None, help="override the host's ssh target (an ssh config alias)")
    ap.add_argument('--dock-ipk', default=None, help='the Dock ipk (default: hosts.toml [paths] dock_ipk, else %s)' % DOCK_IPK)
    ap.add_argument('--hc-ipk', default=None, help='the Home Chooser ipk to test (default: the newest build/ipk/*.ipk)')
    ap.add_argument('--old-ipk', default=None, help='--update-check: the older Home Chooser ipk, already built (skips the build)')
    ap.add_argument('--outdir', default=None, help='the result directory (default e2e/results/<vm>-<timestamp>); must not exist')
    ap.add_argument('--elevation', choices=sorted(ELEVATIONS), default=None,
                    help="override the matrix's elevation strategy")
    ap.add_argument('--input', choices=['qmp', 'ares'], default='qmp',
                    help='remote-key path: QEMU sendkey on the host (default) or ares-do key')
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
                         "'alert': Reboot is pressed by a person (a MANUAL check; the runner presses no key at an alert "
                         "it cannot prove is ours with focus on Reboot) and the runner waits for the new boot")
    ap.add_argument('--api-check', action='store_true',
                    help='the launcher-API contract (docs/launcher-api.md): calls as the launcher before and after it is '
                         'chosen (2a, 3a), after --reboot-check and after the uninstall; installs a disposable app. '
                         'The launcher must declare homechooser.applications: the default is the web fixture built with it; '
                         '--launcher <id> --launcher-ipk <ipk> runs it against your own launcher')
    ap.add_argument('--no-stop', action='store_true', help='leave the VM running at the end')
    ap.add_argument('--list', action='store_true', help='print the matrix and exit')
    args = ap.parse_args()
    if args.list:
        for m in MATRIX:
            print('%-5s %-18s %-10s %-9s %s' % m)
        print('launchers: dock, %s' % ', '.join(fixture_specs()))
        return 0
    if not args.vm:
        ap.error('--vm is required')
    row = entry(args.vm)
    vm = row[1]
    if args.launchers:
        specs = fixture_specs() if args.launchers == 'all-fixtures' else [x for x in args.launchers.split(',') if x]
    else:
        specs = [args.launcher or ('fixture:web.normal' if args.api_check else 'dock')]
    args.multi = len(specs) > 1
    if args.multi and vm in KEEP_LAUNCHER_VMS:
        ap.error('%s keeps its launcher installed; run one launcher at a time there' % vm)
    cfg = load_hosts(args.hosts_file)
    dock_ipk = args.dock_ipk or cfg['paths'].get('dock_ipk') or DOCK_IPK
    launchers = [make_launcher(sp, args.launcher_ipk if len(specs) == 1 else None,
                               lambda t: print(t, flush=True), dock_ipk, api=args.api_check) for sp in specs]
    args.disposable_ipk = api_fixture_ipks(lambda t: print(t, flush=True))[DISPOSABLE_ID] if args.api_check else None
    host = BeanjarHost.from_config(cfg, args.host or cfg['default'], args.host_mode, args.host_device)
    args.new_ipk, args.old_ref = args.hc_ipk, None
    outdir = args.outdir or os.path.join(HERE, 'results', '%s-%s' % (vm, time.strftime('%Y%m%d-%H%M%S')))
    os.makedirs(outdir)
    rec = Recorder(outdir)
    rec.say('artefacts: ' + outdir)
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
        end = time.time() + 120
        while time.time() < end and (host.info(vm).get('lan_address') in (None, '', '-')):
            time.sleep(3)
        ensure_devices(host, vm, rec)
        state = wait_for_device(vm, rec)
        if state == NO_ROOT_SSH:
            rec.say(failsafe_note(vm))
            rc, out = host.stop(vm)
            rec.say('beanjar stop: %s' % out.strip()[-200:])
            rc, out = host.start(vm)
            rec.say('beanjar start: %s' % out.strip()[-200:])
            end = time.time() + 120
            while time.time() < end and (host.info(vm).get('lan_address') in (None, '', '-')):
                time.sleep(3)
            ensure_devices(host, vm, rec)
            state = wait_for_device(vm, rec)
        if state != READY:
            raise SystemExit('the VM did not come up with Homebrew Channel: %s (%s)' % (state, WAIT_STATES[state]))
        for launcher in launchers:
            rec.tag = '[%s]' % launcher.spec if args.multi else ''
            rec.say('==== launcher %s (%s)' % (launcher.spec, launcher.id))
            scenario = Scenario(args, host, rec, row, launcher)
            try:
                scenario.run()
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
        if started and not args.no_stop and vm not in KEEP_LAUNCHER_VMS:
            if scenario and scenario.viewer_opened:
                host.viewer_close(vm)
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
