# SPDX-License-Identifier: Apache-2.0
"""One webOS device through the ares tools: the root session, luna-send (the pty rule), elevation."""
import atexit
import json
import os
import re
import shlex
import signal
import subprocess
import threading
import time

from . import timing
from .constants import APP_ID, HB_ELEVATE, STOCK, SVC_ID
from .evidence import parse_luna

# ---------------------------------------------------------------------------------------------
# Device access: every ares-* command line is built here (Device.argv)
# ---------------------------------------------------------------------------------------------
# A Beanjar VM is reached only through `beanjar ares-TOOL [--root] -d [HOST/]NAME ...`, which writes its
# own per-run device list (prisoner on the VM's 9922; root with --root over the root SSH a layer forwards:
# rootssh, or hbchannel with devmode) and, for a VM on another machine, a per-call ssh port forward.  The
# runner never reads or writes ~/.webos/ose/novacom-devices.json and has no `<vm>-root` entries.  An
# author's real TV (--device) keeps plain `ares-TOOL -d NAME` with the user's own device list.

# Marks the guest's own lack of `script` (some webOS 5 BusyBox builds): the call ran without a terminal.
NO_SCRIPT_NOTE = 'luna-send: no script on the guest, ran without a pty'


def luna_send_command(uri, payload='{}', app=None, pty=True):
    """The guest shell command of one root `luna-send [-a APP] -n 1 URI PAYLOAD`, stderr folded in.

    With pty (the default) it runs under a terminal of the guest's own, `script -q -c CMD /dev/null`:
    measured on W22O 10.3.1 over `ares-shell -r`, luna-send -n 1 exits 0 with its reply unwritten when
    its stdout is not a terminal (Beanjar host/manager/README.md "ares tools").  That is narrower than
    every pipe (Home Chooser's daemon reads replies from a pipe), so Device.luna_send also probes the
    plain form once.  A guest without `script` runs the plain form and says so (NO_SCRIPT_NOTE)."""
    inner = 'luna-send %s-n 1 %s %s' % (('-a %s ' % shlex.quote(app)) if app else '',
                                        shlex.quote(uri), shlex.quote(payload))
    if not pty:
        return inner + ' 2>&1'
    return ('if command -v script >/dev/null 2>&1; then script -q -c %s /dev/null 2>&1 </dev/null; '
            'else echo %s; %s 2>&1; fi' % (shlex.quote(inner), shlex.quote(NO_SCRIPT_NOTE), inner))


def clean_reply(text):
    """A luna-send reply as read through a guest pty: CRLF -> LF (a lone CR too)."""
    return (text or '').replace('\r\n', '\n').replace('\r', '\n')


def luna_reply_text(text):
    """The reply's JSON object as compact text when one is there (no pty or script lines around it), else
    the cleaned output; callers json.loads() it as they did the `ares-do luna` reply."""
    text = clean_reply(text).strip()
    try:
        if isinstance(json.loads(text), dict):
            return text
    except ValueError:
        pass
    v = parse_luna(text)
    return text if v is None else json.dumps(v, separators=(',', ':'))


class RootSession:
    """One root shell kept open, `beanjar ares-shell --root -d T -r sh`, whose stdin takes the commands.

    Each `beanjar ares-TOOL` call to a VM on another machine asks that machine for the VM (`beanjar info`)
    and opens an ssh forward of its own: measured 2026-11-01 from this PC to rockpi4a-1, about 2 s before
    the tool even connects, so 3 to 4 s a command where the old direct device took under 1.  At that pace
    a step's few dozen commands outlast webOS's 180 s screensaver timer and a 6 s toast.  So shell commands
    share one session (Beanjar documents `-r sh < script.sh`: stdin, stdout and the exit status pass
    through): a command runs as `sh -c CMD </dev/null 2>&1` (its own stdin is never the session's), then a
    marker line with its status.  A timeout, an exit of the session (a reboot) or a write error ends it and
    the next command opens a new one."""

    def __init__(self, argv):
        self.argv = argv
        self.proc = None
        self.buf = b''
        self.lock = threading.Lock()

    def _start(self):
        self.proc = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        self.buf = b''

    def close(self):
        proc, self.proc = self.proc, None
        if proc is None:
            return
        for stream in (proc.stdin, proc.stdout):
            try:
                stream.close()
            except OSError:
                pass
        if proc.poll() is None:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(proc.pid, sig)
                except OSError:
                    pass
                try:
                    proc.wait(timeout=5)
                    break
                except subprocess.TimeoutExpired:
                    continue

    def run(self, cmd, timeout=60):
        """(rc, stdout+stderr) of one command, as run() gives them for a single tool call."""
        import select
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self.close()
                self._start()
            mark = 'HC_END_%s' % os.urandom(6).hex()
            line = "sh -c %s </dev/null 2>&1; printf '\\n%s %%d\\n' \"$?\"\n" % (shlex.quote(cmd), mark)
            done = re.compile(b'\n' + mark.encode() + b' (\\d+)\n')
            try:
                self.proc.stdin.write(line.encode())
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass  # the session is gone: its output and status are read below
            end = time.time() + timing.seconds(timeout)
            fd = self.proc.stdout.fileno()
            while True:
                m = done.search(self.buf)
                if m:
                    out, self.buf = self.buf[:m.start()], self.buf[m.end():]
                    return int(m.group(1)), out.decode('utf-8', 'replace')
                left = end - time.time()
                if left <= 0:
                    out = self.buf
                    self.close()
                    return 124, out.decode('utf-8', 'replace') + '\n[timeout]'
                ready, _, _ = select.select([fd], [], [], min(left, 1.0))
                if not ready:
                    continue
                chunk = os.read(fd, 65536)
                if chunk:
                    self.buf += chunk
                    continue
                out, proc = self.buf, self.proc
                rc = proc.wait()
                self.close()
                return rc or 255, out.decode('utf-8', 'replace')


SESSIONS = {}


def close_sessions():
    for session in list(SESSIONS.values()):
        session.close()
atexit.register(close_sessions)


class Device:
    """One webOS device and how to reach it.  `beanjar` set: a Beanjar VM, `target` its [HOST/]NAME;
    else a real TV by ares device name (`root_name` the root-capable one, often the same)."""

    def __init__(self, target, root_name=None, beanjar=True):
        self.target, self.beanjar = target, beanjar
        self.root_name = root_name or target
        self.vm = target  # what messages name
        # where root commands go, for messages only
        self.root = ('%s (beanjar --root)' % target) if beanjar else self.root_name
        self.luna_pty = None  # None: not probed yet; True: through `script`; False: plain
        self.say = print

    @classmethod
    def for_vm(cls, host, vm):
        """A VM on a Beanjar host: HOST/NAME through this PC's multi-host CLI, a bare NAME when local."""
        return cls(host.remote_target(vm) if host else vm)

    @classmethod
    def tv(cls, name, root_name=None):
        return cls(name, root_name, beanjar=False)

    def argv(self, tool, args=(), root=False):
        """The local command line of `ares-<tool> ARGS` on this device, as root or as the developer-mode user."""
        if self.beanjar:
            return ['beanjar', 'ares-' + tool] + (['--root'] if root else []) + ['-d', self.target] + list(args)
        return ['ares-' + tool, '-d', self.root_name if root else self.target] + list(args)

    def session(self):
        """The shared root shell of a VM (RootSession); None for a TV, whose commands keep one call each."""
        if not self.beanjar:
            return None
        key = tuple(self.argv('shell', ['-r', 'sh'], root=True))
        if key not in SESSIONS:
            SESSIONS[key] = RootSession(list(key))
        return SESSIONS[key]

    def reset(self):
        """Drop the root shell (the VM is stopped or rebooting); the next command opens a new one."""
        if self.session():
            self.session().close()

    def exec(self, cmd, timeout=60):
        if self.beanjar:
            return self.session().run(cmd, timeout)
        return timing.run(self.argv('do', ['--timeout', str(timeout * 1000), 'exec', cmd], root=True), timeout + 10)

    def sh(self, cmd, timeout=60):
        """Root shell command; (rc, output)."""
        if self.beanjar:
            return self.session().run(cmd, timeout)
        return timing.run(self.argv('shell', ['-r', cmd], root=True), timeout)

    def luna_send(self, uri, payload='{}', app=None, timeout=60):
        """(rc, cleaned output) of one root `luna-send [-a app] -n 1`, through a guest pty when that is what
        gives replies on this device (probed once with a harmless read, getForegroundAppInfo).  A call that
        printed nothing is not repeated: replies with side effects (createAlert, reboot) must not run twice."""
        if self.luna_pty is None:
            self.probe_luna()
        rc, out = self.exec(luna_send_command(uri, payload, app, pty=self.luna_pty is not False), timeout)
        return rc, clean_reply(out)

    def probe_luna(self):
        """Pick the luna-send form that prints a reply here: the `script` pty first, else plain.  Left
        unprobed (None) when neither answers, e.g. the device is down, so the next call probes again."""
        uri = 'luna://com.webos.applicationManager/getForegroundAppInfo'
        rc, out = self.exec(luna_send_command(uri), 30)
        if NO_SCRIPT_NOTE in out:
            self.luna_pty = False
            self.say('luna-send on %s: %s' % (self.vm, NO_SCRIPT_NOTE))
            return
        if parse_luna(clean_reply(out)) is not None:
            self.luna_pty = True
            self.say('luna-send on %s: replies through a guest pty (script)' % self.vm)
            return
        rc2, out2 = self.exec(luna_send_command(uri, pty=False), 30)
        if parse_luna(out2) is not None:
            self.luna_pty = False
            self.say('luna-send on %s: no reply through script (%r), the plain form answers' % (self.vm, out.strip()[-120:]))
        else:
            self.say('luna-send on %s: no reply either way yet (script rc=%s %r; plain rc=%s %r)'
                     % (self.vm, rc, out.strip()[-120:], rc2, out2.strip()[-120:]))

    def luna(self, uri, payload='{}'):
        """A root luna call with no application id; the reply text (`{...}` when there is one)."""
        rc, out = self.luna_send(uri, payload)
        return luna_reply_text(out)

    def install(self, ipk):
        return timing.run(self.argv('install', [ipk]), 300)

    def remove(self, appid):
        return timing.run(self.argv('install', ['--remove', appid]), 300)

    def installed(self):
        rc, out = timing.run(self.argv('install', ['-l']), 60)
        if rc != 0 or out.startswith('Failed to connect'):
            raise RuntimeError('ares-install -l on %s failed (rc=%s): %s' % (self.vm, rc, out.strip()[-300:]))
        return out.split()

    def launch(self, appid, params=None):
        return timing.run(self.argv('launch', [appid] + (['-p', json.dumps(params)] if params else [])), 60)

    def close(self, appid):
        return timing.run(self.argv('launch', ['-c', appid]), 60)

    def key(self, *keys):
        return timing.run(self.argv('do', ['key', '--delay', '300'] + list(keys), root=True), 60)

    def screenshot(self, path):
        return timing.run(self.argv('do', ['screenshot', path], root=True), 60)

    def forward(self, remote_port, local_port):
        """A Popen of `ares-novacom -f` (developer-mode user, as before), its own process group."""
        return subprocess.Popen(self.argv('novacom', ['-f', '-p', '%d:%d' % (remote_port, local_port)]),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                start_new_session=True)

    def stream(self, cmd):
        """A Popen of a root shell command whose stdout is read as it comes (ares-shell -r streams it;
        ares-do exec buffers until the command exits)."""
        return subprocess.Popen(self.argv('shell', ['-r', cmd], root=True), stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True, bufsize=1,
                                start_new_session=True)

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
        with its own client permissions (docs/launcher-api.md).  app None: no application id at all."""
        rc, out = self.luna_send(uri, payload, app, timeout)
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

    def service_procs(self):
        """[(user, pid)] of the running homechooser-service processes, by ps."""
        rc, out = self.sh("ps -eo user,pid,args | grep '[h]omechooser-service'")
        procs = []
        for line in out.splitlines():
            f = line.split()
            if len(f) >= 3 and f[1].isdigit() and 'homechooser-service' in line:
                procs.append((f[0], f[1]))
        return procs


# ---------------------------------------------------------------------------------------------
# Elevation strategies
# ---------------------------------------------------------------------------------------------
class Elevation:
    name = '?'

    def prepare(self, dev, rec):
        """Before the app starts."""

    def verify(self, dev, rec, wait=90):
        """The service runs as root: by ps (the service answers only the app)."""
        end = time.time() + timing.seconds(wait)
        out = ''
        while time.time() < end:
            out = dev.service_root()
            if out.startswith('root') and 'homechooser-service' in out and 'jailer' not in out:
                break
            timing.sleep(3)
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

    QUIT_WAIT = 10

    def prepare(self, dev, rec):
        rc, out = dev.sh('%s %s; echo rc=$?' % (HB_ELEVATE, SVC_ID), 90)
        rec.note('elevate-service ' + SVC_ID, out.strip())
        # The service answers only the app (hc_sender_ok): quit and status go out as the app, or a
        # running instance answers `forbidden` and stays alive.
        before = dev.service_procs()
        rec.note('service before quit', repr(before) if before else '(not running)')
        if before:
            rc, raw, _ = dev.luna_as(APP_ID, 'luna://%s/quit' % SVC_ID)
            rec.note('quit as ' + APP_ID, raw or '(no reply)')
            end = time.time() + timing.seconds(self.QUIT_WAIT)
            left = dev.service_procs()
            while left and time.time() < end:
                timing.sleep(1)
                left = dev.service_procs()
            rec.note('service after quit', repr(left) if left else '(gone)')
            if left:
                rec.check('elevation (%s): old service instance exited after quit' % self.name, False,
                          'pid %s (uid %s) still alive %s s after quit; quit reply: %s'
                          % (left[0][1], left[0][0], self.QUIT_WAIT, raw or '(no reply)'))
                return
        # the (rewritten, root) instance is started on demand by any call
        rc, raw, _ = dev.luna_as(APP_ID, 'luna://%s/status' % SVC_ID)
        rec.note('status as ' + APP_ID, raw or '(no reply)')


ELEVATIONS = {'luna': LunaElevation, 'root-shell': RootShellElevation}
