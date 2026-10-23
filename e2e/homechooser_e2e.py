#!/usr/bin/env python3
"""End-to-end test of Home Chooser on a Beanjar VM.  See e2e/README.md.

Python 3 standard library only.  The device is reached with the ares-* tools only (by device
name); the VM host (Beanjar) through the BeanjarHost class below, locally or through
`ares-shell -d raspberrypi -r`.
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
import time
import urllib.request

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
    ('6.x', 'w21p-kisscurl', 'luna', 'docs', "W21P 03.70.25.01 = 6.6.0; owned by the Dock project: ask before use; end state keeps Dock selected"),
    ('7.x', 'w22p-mullet', 'luna', 'verified', 'W22P 04.60.90.01 = 7.6.0; os-release read 2026-10-22'),
    ('8.x', 'w22o-number1', 'luna', 'verified', 'W22O 13.30.56.01 = 8.3.0; os-release read 2026-10-22 (beanjar lists it as "webOS 23", a platform label)'),
    ('9.x', 'w22h-ombre', 'luna', 'verified', 'W22H 23.21.20.01 = 9.2.1; os-release read'),
    ('10.x', 'w22o-ponytail-us', 'luna', 'verified', 'W22O 33.31.61.01 = 10.3.1; os-release read 2026-10-22'),
    ('11.x', 'w22o-queue-us', 'root-shell', 'verified', 'W22O 43.21.89.01 = 11.2.0; os-release read 2026-10-22; w22o-queue and w22o-queue-jp run the same firmware'),
    ('12.x', 'w24g-spacebuns', 'root-shell', 'verified', 'W24G 02.68.80.01 = 12.0.0; os-release read 2026-10-22'),
]
KEEP_LAUNCHER_VMS = {'w21p-kisscurl'}

# Stock-home preload policy (backport/homechooser-home-watch; docs/settings-app.md "The stock home is
# not preloaded").  Launchers that are overlay windows take nothing from the stock home's foreground, so
# on 6.x to 10.x sam relaunches a closed stock home at once as the "last input" app (measured on 9.2.1,
# 8.3.0 and 10.3.1, 75 to 89 MB with Dock chosen).  The watcher therefore does not close it there: the expected result
# is the stock home running, the skip line in the watcher log and no closeByAppId for it.
OVERLAY_LAUNCHERS = {'org.webosbrew.dock'}
SKIP_LINE = 'stock home left running'
PRELOAD_SETTLE = 60   # seconds to wait after a switch/boot before sampling (watcher closes at +45 s)
PRELOAD_SAMPLE = 60   # seconds the stock home is watched for


def entry(release_or_vm):
    for m in MATRIX:
        if release_or_vm in (m[0], m[1]):
            return m
    raise SystemExit('unknown VM/release %r; see the matrix in e2e/README.md' % release_or_vm)


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
    run with `ares-shell -d <device> -r`.  The rest of the runner never quotes for the remote.
    """

    def __init__(self, mode='auto', device='raspberrypi'):
        if mode == 'auto':
            mode = 'local' if shutil.which('beanjar') else 'remote'
        self.mode, self.device = mode, device

    # -- plumbing
    def sh(self, cmd, timeout=120):
        if self.mode == 'local':
            return run(['sh', '-c', cmd], timeout)
        return run(['ares-shell', '-d', self.device, '-r', cmd], timeout)

    def _q(self, *words):
        return ' '.join(shlex.quote(str(w)) for w in words)

    def fetch(self, remote_path, local_path):
        """Copy a host file to the machine running the runner."""
        if self.mode == 'local':
            shutil.copyfile(remote_path, local_path)
            return True
        rc, _ = run(['ares-pull', '-d', self.device, remote_path, local_path])
        return rc == 0

    def beanjar(self, *args, timeout=300):
        return self.sh('beanjar ' + self._q(*args), timeout)

    # -- Beanjar
    def vm_table(self):
        rc, out = self.beanjar('list')
        rows = {}
        for line in out.splitlines()[1:]:
            f = line.split()
            if len(f) >= 3:
                rows[f[0]] = {'state': f[1], 'line': line}
        return rows

    def running_vms(self):
        return [n for n, r in self.vm_table().items() if r['state'] not in ('Off', 'off')]

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
        return self.beanjar('start', vm, timeout=600)

    def stop(self, vm):
        return self.beanjar('stop', vm, timeout=300)

    # -- input and capture
    def sendkey(self, vm, key):
        sock = '$HOME/.local/share/beanjar/vms/%s/run/qmp.sock' % vm
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
        tmp = '/tmp/e2e-tv-%d.png' % os.getpid()
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

    def launch(self, appid):
        return run(['ares-launch', '-d', self.vm, appid], 60)

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
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        self.port = s.getsockname()[1]
        s.close()
        self.proc = subprocess.Popen(['ares-novacom', '-d', vm, '-f', '-p', '9998:%d' % self.port],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True)
        time.sleep(2)

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

    def evaluate(self, expr):
        """Runtime.evaluate in the Home Chooser page; None if it is not there."""
        page = self._page()
        if not page:
            return None
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
                    return None
                try:
                    d = json.loads(frame.decode('utf-8', 'replace'))
                except ValueError:
                    continue
                if d.get('id') == 1:
                    return d.get('result', {}).get('result', {}).get('value')
        except (OSError, ValueError):
            return None
        finally:
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
            "toast:(function(){var d=document.querySelector('[role=status]');return d?d.textContent:null})(),"
            "rows:Array.prototype.map.call(document.querySelectorAll('[data-app-id]'),function(x){return x.getAttribute('data-app-id')})})})()")
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
        self.log = open(os.path.join(outdir, 'run.log'), 'w')

    def say(self, text):
        line = '%s %s' % (time.strftime('%H:%M:%S'), text)
        print(line, flush=True)
        self.log.write(line + '\n')
        self.log.flush()

    def begin(self, step):
        self.step = step
        self.say('== ' + step)

    def note(self, what, evidence=''):
        self.say('note: %s\n    %s' % (what, str(evidence).replace('\n', '\n    ')))

    def check(self, name, ok, evidence='', expected=None):
        """expected: text citing the documented limitation; a failing check then records XFAIL."""
        status = 'PASS' if ok else ('XFAIL' if expected else 'FAIL')
        self.results.append({'step': self.step, 'name': name, 'status': status,
                             'evidence': str(evidence), 'expected': expected})
        self.say('%s  %s\n    %s%s' % (status, name, str(evidence).replace('\n', '\n    '),
                                      '\n    expected: ' + expected if expected and not ok else ''))
        return ok

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
    def __init__(self, args, host, rec, row):
        self.a, self.host, self.rec = args, host, rec
        self.release, self.vm, self.strategy_name, self.sure, self.note = row
        self.dev = Device(self.vm)
        self.launcher = args.launcher
        self.shot_n = 0
        self.insp = None
        self.capture_broken = False
        self.viewer_opened = False
        self.major = 0  # webOS TV release, 5..12

    # -- helpers
    def shot(self, label):
        """The TV picture from `beanjar screenshot` (about 2 s, headless, no viewer needed).  Only if
        that fails: the guest capture (ares-do screenshot; com.webos.service.capture fails on webOS 5
        and 10.x to 12.x, and after the first failure it is not tried again), then the Pi desktop with
        the viewer window (grim, `*-viewer.png`; the viewer is opened for it on first use).  Each
        fallback used is logged."""
        self.shot_n += 1
        name = '%02d-%s.png' % (self.shot_n, label)
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
                qk = {'DOWN': 'down', 'UP': 'up', 'OK': 'ret', 'HOME': 'meta_l', 'BACK': 'esc'}[k]
                self.host.sendkey(self.vm, qk)
            else:
                ak = {'DOWN': 'DOWN', 'UP': 'UP', 'OK': 'ENTER', 'HOME': 'LEFTMETA', 'BACK': 'ESC'}[k]
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
            ui = self.ui() or {}
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
        return 6 <= self.major <= 9

    def uses_override(self):
        return self.major >= 10

    def stock_record_ok(self, home):
        if self.major == 5:
            return home in ('com.webos.app.fullhome', STOCK)
        return home in (None, '', 'null')

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

    def assert_stock(self, label, s, mounts_stay=False):
        r = self.rec
        r.check(label + ': record home is stock/unset', self.stock_record_ok(s['record_home']), s['record_raw'])
        if self.uses_override():
            r.check(label + ': profile override absent', s['override'] == 'no', 'override exists: ' + s['override'])
        if self.uses_backport() and mounts_stay:
            r.check(label + ': backport stays mounted (docs: "the backport stays mounted until reboot")',
                    bool(s['mounts']), 'mounts: %s' % s['mounts'])
            baked = self.baked_id()
            r.check(label + ': baked id in the mounted Settings.qml is emptied (refresh stock)',
                    '""' in baked and self.launcher not in baked, baked)
        elif self.uses_backport():
            r.check(label + ': no backport mounts', not s['mounts'], 'mounts: %s' % s['mounts'])

    # -- the removal notifications (docs/settings-app.md "When apps are removed")
    def notif_lines(self, method):
        """The watcher log's `notification <method>:` lines (call replies), oldest first."""
        rc, out = self.dev.sh("grep 'notification %s:' /tmp/homechooser-home.log 2>&1" % method)
        return [l for l in out.splitlines() if 'notification %s:' % method in l]

    def notif_wait(self, method, before, timeout, every=3):
        """Wait until the log has more `notification <method>:` lines than `before`."""
        return self.wait(lambda: len(self.notif_lines(method)) > before, timeout, every)

    def check_alert(self, before):
        """After the chosen launcher was removed: the watcher logged one createAlert call and its reply.
        A reply that is not returnValue true is a FAIL with the fallback recorded, never a silent pass.
        With an accepted alert: screenshot, press OK (it is our own alert, not a system popup),
        screenshot again.  Plain root luna-send without -a was assumed to pass; this is the evidence."""
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
        r.check('the user was told: the alert was accepted (plain root luna-send, no -a)', True, 'reply: %s' % reply)
        time.sleep(2)
        self.shot('4-alert')
        r.note('look at screenshot 4-alert: an alert "Home Chooser" with the message "<launcher title> was removed, '
               'so the Home button opens LG Home again." and one OK button', '')
        fg0 = d.foreground()
        self.press('OK')
        time.sleep(3)
        self.shot('4-alert-dismissed')
        r.note('OK pressed on the alert; foreground before/after (the alert itself is not an app)',
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
        r.note('look at screenshot 5-toast: a toast "Home Chooser was removed. The Home button opens LG Home again." '
               '(it may already be gone: the log reply is the evidence)', '')

    # -- the stock home's preload policy
    def preload_log(self):
        return self.dev.sh("grep -E 'setPreloadPolicy|closeByAppId|overlay launcher' /tmp/homechooser-home.log 2>&1 | tail -n 6")[1].strip()

    def overlay_skip(self):
        """6.x to 10.x with an overlay launcher: the watcher leaves the stock home running."""
        return self.launcher in OVERLAY_LAUNCHERS and 6 <= self.major <= 10

    def check_stock_left_running(self, label, samples):
        """Overlay launcher on 6.x to 10.x: the stock home is running, the watcher logged the skip and
        never called closeByAppId for it."""
        r = self.rec
        self.wait(lambda: SKIP_LINE in self.dev.sh("cat /tmp/homechooser-home.log 2>&1")[1], 120, 5)
        log = self.dev.sh("cat /tmp/homechooser-home.log 2>&1")[1]
        skips = [l for l in log.splitlines() if SKIP_LINE in l]
        closes = [l for l in log.splitlines() if 'closeByAppId ' + STOCK in l]
        r.note(label + ': watcher skip lines', '\n'.join(skips))
        r.check(label + ': watcher log has the overlay skip line (stock home left running)', bool(skips),
                skips[-1] if skips else 'no line containing %r' % SKIP_LINE)
        r.check(label + ': watcher log has no closeByAppId ' + STOCK, not closes, '\n'.join(closes) or 'none')
        running = bool(samples) and samples[-1][1]
        detail = 'running at t=%s s of %d samples' % ([t for t, run_ in samples if run_], len(samples))
        if 'reboot' in label and self.major >= 10:
            # 10.x does not launch the stock home at boot (5.x to 9.x do, snapshot-boot-components), and the
            # policy is off, so after a reboot nothing starts it: either state is fine, a close is the failure.
            r.note(label + ': %s running=%s (10.x: not started at boot with the policy off; no check)' % (STOCK, running), detail)
            return
        r.check(label + ': %s is running (expected: left alone with an overlay launcher)' % STOCK, running, detail)

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
        """A third-party home is chosen: the watcher disabled the stock home's preload and the stock
        home stays closed.  5.x: the watcher leaves the policy alone (settings-app.md), so it must not
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
        if self.overlay_skip():
            self.check_stock_left_running(label, samples)
            return
        ok = not any(run_ for _, run_ in samples)
        r.check('%s: %s is not running over %d s' % (label, STOCK, PRELOAD_SAMPLE), ok,
                'running at t=%s s' % [t for t, run_ in samples if run_] if not ok else 'never running in %d samples' % len(samples))
        if not ok:
            self.residency(label + ': stock home came back')
            r.note(label + ': whole watcher log', self.dev.sh('cat /tmp/homechooser-home.log 2>&1 | tail -n 40')[1])
            r.note(label + ': sam lines about the stock home', self.dev.sh(
                "grep -E 'com.webos.app.home' /var/log/messages | grep -E 'close|Close|launch|Launch|NL_APP' | tail -n 15")[1])

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
        if expect == STOCK and self.major == 5:
            # webOS 5: Home starts com.webos.app.fullhome as an overlay over the app in front
            # (foreground stays the app; seen in the w20l-jhericurl run), so it is observed like
            # a launcher: closed first, running afterwards.
            expect = 'com.webos.app.fullhome'
        if expect != STOCK:
            self.dev.close(expect)
            time.sleep(3)
            pre = expect in self.running_ids()
            r.say('%s: %s running before the press: %s%s' % (label, expect, pre,
                  ' (close did not stop it: this check does not discriminate)' if pre else ''))
        if from_app:
            self.front_app()
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
        self.shot(label)
        via = 'foreground' if fg[0] == expect else ('sam log' if sam else 'none')
        r.check('%s: Home opens %s (foreground, sam homeKey launch of the stock home in the log, or for a '
                'launcher: running after being closed first)' % (label, expect), ok,
                'signal: %s; getForegroundAppInfo: %s; running: %s%s' % (
                    via, fg[1], self.running_ids(),
                    ('; sam lines:\n' + '\n'.join(sam[:6])) if sam and via == 'sam log' else ''))
        return ok

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
        rc, out = self.dev.sh("cat /var/log/messages | awk '$1 >= \"%s\"' | grep -E '%s' | tail -n 8"
                              % (mark, pattern))
        return [l for l in out.splitlines() if l.strip()]

    # -- steps
    def run(self):
        r, d = self.rec, self.dev
        strat = ELEVATIONS[self.a.elevation or self.strategy_name]()
        r.say('run: vm=%s release=%s elevation=%s (matrix default %s) input=%s host=%s launcher=%s'
              % (self.vm, self.release, strat.name, self.strategy_name, self.a.input, self.host.mode,
                 self.launcher))
        r.say('matrix confidence: %s -- %s' % (self.sure, self.note))
        self.major, osr = d.release()
        r.begin('0 baseline')
        r.check('device release matches the matrix (%s)' % self.release,
                '%d.' % self.major == self.release[:-1], 'os-release: ' + osr)
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

        steps = [self.step1, self.step2, self.step3, self.step4, self.step5]
        try:
            for i, fn in enumerate(steps, 1):
                if i > self.a.until:
                    break
                fn(strat) if i == 1 else fn()
                if i == 3 and self.a.reboot_check:
                    self.step_reboot()
        finally:
            if self.a.until >= 5:
                self.step6()
            else:
                r.say('--until %d: apps left installed for debugging; no cleanup' % self.a.until)

    def step1(self, strat):
        r, d = self.rec, self.dev
        r.begin('1 install')
        ipks = sorted(f for f in os.listdir(os.path.join(REPO, 'build/ipk')) if f.endswith('.ipk'))
        hc = os.path.join(REPO, 'build/ipk', ipks[-1]) if ipks else None
        if self.a.build or not hc:
            r.say('building Home Chooser: npm run package')
            rc, out = run(['sh', '-c', 'cd %s/app && npm run package' % REPO], 900)
            r.check('npm run package', rc == 0, out[-300:])
            hc = os.path.join(REPO, 'build/ipk', sorted(os.listdir(os.path.join(REPO, 'build/ipk')))[-1])
        rc, out = d.install(hc)
        r.check('install Home Chooser', rc == 0 and 'Installed package' in out, out.strip())
        rc, out = d.install(self.a.launcher_ipk)
        r.check('install launcher %s' % self.launcher, rc == 0 and 'Installed package' in out, out.strip())
        r.check('both apps are listed', APP_ID in d.installed() and self.launcher in d.installed(),
                str(d.installed()))
        strat.prepare(d, r)
        self.strat = strat

    def step2(self):
        r, d = self.rec, self.dev
        r.begin('2 choose the launcher through the UI')
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
        # a marker as an earlier install's cut-short run leaves it (WAM keeps localStorage across an
        # uninstall; the choice was removed with it): the next open must drop it without a toast
        r.note('planted stale marker', self.insp.evaluate(PLANT_JS % self.launcher))
        d.close(APP_ID)
        time.sleep(3)
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
        self.ensure_key_focus(rows)
        ui = self.ui() or ui
        # move focus to the launcher's row
        for _ in range(len(rows) + 2):
            ui = self.ui() or {}
            if ui.get('row') == self.launcher:
                break
            cur, tgt = rows.index(ui['row']) if ui.get('row') in rows else 0, rows.index(self.launcher)
            self.press('DOWN' if tgt > cur else 'UP')
            r.say('focus move: was %s, now %s; foreground %s' % (ui.get('row'), (self.ui() or {}).get('row'),
                                                                 self.dev.foreground()[0]))
        ui = self.ui() or {}
        r.check('focus is on the launcher row', ui.get('row') == self.launcher, json.dumps(ui))
        self.shot('row-focused')
        self.press('OK')
        ui = self.wait(lambda: (lambda u: u if u and u.get('dialog') else None)(self.ui()), 15, 1) or {}
        r.check('the confirmation dialog opened ("Use <name> as Home?")',
                bool(ui.get('dialog')) and ui['dialog'].startswith('Use '), json.dumps(ui))
        r.say('UI driven: dialog "%s", focus "%s"' % (ui.get('dialog'), ui.get('text')))
        self.shot('dialog')
        if ui.get('text') != 'Use as Home':
            self.press('UP')
            ui = self.ui() or {}
        r.check('focus is on "Use as Home"', ui.get('text') == 'Use as Home', json.dumps(ui))
        self.press('OK')
        # the choice is applied; on 6-9 the compositor restart may close the panel
        self.wait(lambda: self.state()['choice'] == self.launcher, 90)
        time.sleep(10)
        toast = (self.ui() or {}).get('toast')
        self.shot('after-confirm')
        r.note('toast after confirming (None if the panel was restarted/closed)', toast)
        self.wait(lambda: self.watcher_active() == 'active' and (not self.uses_backport() or self.dev.mounts()), 90)
        self.residency('right after "Use as Home" (settled)')
        s = self.state()
        r.note('state after choosing', json.dumps(s))
        r.check('choice file holds the launcher id', s['choice'] == self.launcher, repr(s['choice']))
        r.check('general/defaultApps record home is the launcher', s['record_home'] == self.launcher, s['record_raw'])
        r.check('watcher unit homechooser-home is active', s['watcher'] == 'active', s['watcher'])
        r.check('init.d entry is a symlink into the installed app',
                s['initd'] == APPDIR + '/backport/homechooser', s['initd'])
        if self.uses_backport():
            r.check('backport mounts are present (webOS 6-9)', len(s['mounts']) >= 9, 'mounts: %s' % s['mounts'])
            r.check('backport flag file exists', s['flag'] == 'yes', s['flag'])
        else:
            r.check('no backport on this release', not s['mounts'] and s['flag'] == 'no',
                    'mounts: %s flag: %s' % (s['mounts'], s['flag']))
        if self.uses_override():
            r.check('profile override exists (webOS 10+)', s['override'] == 'yes', s['override'])
            r.note('override', d.sh('cat %s' % OVERRIDE)[1])
        r.check('the toast said Done (or the panel restarted)', toast in (None, 'Done. Press Home to try it.'),
                repr(toast))
        # open the panel again after the apply (a cut-short run left its marker): no error, the choice ticked
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

    def step3(self):
        self.rec.begin('3 Home opens the launcher')
        # Observation, not an assertion: Home with the stock home already in front.
        fg0 = self.dev.foreground()
        self.press('HOME')
        time.sleep(6)
        fg1 = self.dev.foreground()
        self.shot('3-home-from-stock-home')
        self.rec.note('Home pressed with the stock home in front (observation only): before %s, after %s'
                      % (fg0[0], fg1[0]), 'see screenshot 3-home-from-stock-home')
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
        rc, out = host.stop(self.vm)
        r.say('beanjar stop: %s' % out.strip()[-200:])
        rc, out = host.start(self.vm)
        r.say('beanjar start: %s' % out.strip()[-200:])
        end = time.time() + 120
        while time.time() < end and (host.info(self.vm).get('lan_address') in (None, '', '-')):
            time.sleep(3)
        ensure_devices(host, self.vm, r)
        up = wait_for_device(self.vm, r)
        r.check('the VM is back with Homebrew Channel', up, 'wait_for_device')
        if not up:
            return
        active = self.wait(lambda: self.watcher_active() == 'active', 180, 5)
        r.check('after the boot the watcher is active (started from init.d)', bool(active), self.watcher_active())
        s = self.state()
        r.check('after the boot the choice and the record are still the launcher',
                s['choice'] == self.launcher and s['record_home'] == self.launcher, s['record_raw'])
        if self.major >= 6:
            # the call comes at once, the close after the 45 s settle
            self.wait(lambda: ('closeByAppId' in self.preload_log()) or (self.overlay_skip() and SKIP_LINE in self.preload_log()), 150, 5)
            log = self.preload_log()
            r.check('after the boot the watcher called setPreloadPolicy isEnabled=false (log is from this boot: /tmp)',
                    'isEnabled=false' in log, log)
        else:
            r.check('after the boot (5.x) the watcher made no preload call', 'setPreloadPolicy' not in self.preload_log(),
                    self.preload_log())
        self.check_preload_off('after the reboot', settle=30)
        self.residency('after the reboot')
        self.shot('3r-after-reboot')

    def step4(self):
        r, d = self.rec, self.dev
        r.begin('4 sudden launcher uninstall')
        alerts_before = len(self.notif_lines('createAlert'))
        rc, out = d.remove(self.launcher)
        r.check('ares-install --remove launcher', rc == 0, out.strip())
        s = {}
        self.wait(lambda: self.stock_record_ok(d.record_home()[0]), 120, 5)
        self.check_alert(alerts_before)
        s = self.state()
        r.note('state after the launcher was removed', json.dumps(s))
        self.residency('after the launcher uninstall')
        self.assert_stock('after removal', s, mounts_stay=True)
        self.check_preload_on('after the launcher removal')
        r.check('choice file is kept (the user\'s choice)', s['choice'] == self.launcher, repr(s['choice']))
        r.check('watcher still active', s['watcher'] == 'active', s['watcher'])
        rc, out = d.sh('tail -n 8 /tmp/homechooser-home.log')
        r.note('watcher log', out)
        self.home_press(STOCK, '4-home-stock')
        # reinstall
        rc, out = d.install(self.a.launcher_ipk)
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
            baked = self.baked_id()
            r.check('on return the id is baked into the mounted Settings.qml again',
                    self.launcher in baked, baked)
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
        rc, out = d.remove(APP_ID)
        r.check('ares-install --remove Home Chooser', rc == 0, out.strip())
        self.check_toast(toasts_before)
        self.wait(lambda: self.watcher_active() != 'active' and self.stock_record_ok(d.record_home()[0]), 150, 5)
        time.sleep(5)
        s = self.state()
        r.note('state after uninstall', json.dumps(s))
        self.residency('after the Home Chooser uninstall')
        r.check('choice file removed', 'No such file' in s['choice'], repr(s['choice']))
        self.assert_stock('after uninstall', s)
        self.check_preload_on('after the Home Chooser uninstall')
        r.check('init.d entry removed', 'No such file' in s['initd'] or s['initd'] == '', repr(s['initd']))
        r.check('backport flag removed', s['flag'] == 'no', s['flag'])
        r.check('watcher unit stopped', s['watcher'] != 'active', s['watcher'])
        rc, out = d.sh('tail -n 12 /tmp/homechooser-home.log')
        r.note('watcher log', out)
        self.home_press(STOCK, '5-home-stock')

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
            for app in (self.launcher, APP_ID):
                if app in d.installed():
                    rc, out = d.remove(app)
                    r.say('removed %s: %s' % (app, out.strip()))
                    removed_hc = removed_hc or app == APP_ID
            r.check('no test apps left installed', not ({self.launcher, APP_ID} & set(d.installed())),
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
    p = os.path.expanduser('~/.webos/ose/novacom-devices.json')
    devs = json.load(open(p))
    byname = {x['name']: x for x in devs}
    changed = False
    keyname = 'beanjar-%s-root' % vm
    keypath = os.path.expanduser('~/.ssh/' + keyname)
    if not os.path.exists(keypath):
        src = '%s/identity/id_ed25519' % info['dir']
        if not host.fetch(src, keypath):
            raise SystemExit('could not fetch the root key of %s' % vm)
        os.chmod(keypath, 0o600)
        rec.say('fetched the root key of %s to ~/.ssh/%s' % (vm, keyname))
    want = {vm + '-root': (6622, 'root', {'openSsh': keyname})}
    for name, (port, user, key) in want.items():
        e = byname.get(name)
        if e is None:
            devs.append({'profile': 'ose', 'name': name, 'host': addr, 'port': port, 'username': user,
                         'privateKey': key, 'files': 'sftp'})
            changed = True
            rec.say('added ares device %s -> %s:%d' % (name, addr, port))
        elif e.get('host') != addr:
            rec.say('updated ares device %s host %s -> %s' % (name, e.get('host'), addr))
            e['host'] = addr
            changed = True
    dv = byname.get(vm)
    if dv is None:
        # the devmode device (prisoner, 9922) with the VM's devmode key, fetched from the host
        dkeyname = 'beanjar-%s_devmode' % vm
        dkeypath = os.path.expanduser('~/.ssh/' + dkeyname)
        if not os.path.exists(dkeypath):
            if not host.fetch('%s/identity/devmode_rsa' % info['dir'], dkeypath):
                raise SystemExit('could not fetch the devmode key of %s' % vm)
            os.chmod(dkeypath, 0o600)
            rec.say('fetched the devmode key of %s to ~/.ssh/%s' % (vm, dkeyname))
        devs.append({'profile': 'ose', 'name': vm, 'host': addr, 'port': 9922, 'username': 'prisoner',
                     'privateKey': {'openSsh': dkeyname}, 'files': 'sftp'})
        rec.say('added ares device %s -> %s:9922' % (vm, addr))
        changed = True
    elif dv.get('host') != addr:
        rec.say('updated ares device %s host %s -> %s' % (vm, dv.get('host'), addr))
        dv['host'] = addr
        changed = True
    if changed:
        json.dump(devs, open(p, 'w'), indent=2)


def prepare_vm(host, vm, rec):
    running = host.running_vms()
    if running:
        raise SystemExit('VMs already running, not touching anything: %s' % running)
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
    rc, out = host.start(vm)
    rec.say('beanjar start: %s' % out.strip()[-200:])


def wait_for_device(vm, rec, timeout=300):
    dev = Device(vm)
    end = time.time() + timeout
    while time.time() < end:
        rc, out = dev.sh('echo up', 20)
        if out.strip().endswith('up'):
            apps = dev.installed()
            if 'org.webosbrew.hbchannel' in apps:
                return True
        time.sleep(5)
    return False


def main():
    ap = argparse.ArgumentParser(description='Home Chooser end-to-end test on one Beanjar VM')
    ap.add_argument('--vm', help='a VM name or a release (e.g. w22h-ombre or 9.x)')
    ap.add_argument('--launcher', default='org.webosbrew.dock')
    ap.add_argument('--launcher-ipk', default='/home/mariotaku/Projects/webosbrew/Dock.app/dist/org.webosbrew.dock_0.1.0_arm.ipk')
    ap.add_argument('--host-mode', choices=['auto', 'local', 'remote'], default='auto',
                    help='where beanjar runs: local if on PATH, else through ares-shell (default auto)')
    ap.add_argument('--host-device', default='raspberrypi', help='ares device of the Beanjar host in remote mode')
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
    ap.add_argument('--no-stop', action='store_true', help='leave the VM running at the end')
    ap.add_argument('--list', action='store_true', help='print the matrix and exit')
    args = ap.parse_args()
    if args.list:
        for m in MATRIX:
            print('%-5s %-18s %-10s %-9s %s' % m)
        return 0
    if not args.vm:
        ap.error('--vm is required')
    row = entry(args.vm)
    vm = row[1]
    host = BeanjarHost(args.host_mode, args.host_device)
    outdir = os.path.join(HERE, 'results', '%s-%s' % (vm, time.strftime('%Y%m%d-%H%M%S')))
    os.makedirs(outdir)
    rec = Recorder(outdir)
    rec.say('artefacts: ' + outdir)
    started = False
    scenario = None
    try:
        if not shutil.which('ares-do'):
            raise SystemExit('ares-do not on PATH')
        prepare_vm(host, vm, rec)
        started = True
        rec.say('waiting for the VM to answer on its LAN address (device entries are created or fixed first)')
        end = time.time() + 120
        while time.time() < end and (host.info(vm).get('lan_address') in (None, '', '-')):
            time.sleep(3)
        ensure_devices(host, vm, rec)
        if not wait_for_device(vm, rec):
            raise SystemExit('the VM did not come up with Homebrew Channel')
        scenario = Scenario(args, host, rec, row)
        scenario.run()
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
        counts = rec.finish()
    return 1 if counts.get('FAIL') else 0


if __name__ == '__main__':
    sys.exit(main())
