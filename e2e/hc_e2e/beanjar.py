# SPDX-License-Identifier: Apache-2.0
"""The Beanjar host: VM lifecycle, keys, clicks, screenshots, locally or over ssh."""
import json
import os
import re
import shlex
import shutil

from . import timing
from .device import close_sessions


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
            return timing.run(['sh', '-c', cmd], timeout)
        return timing.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', self.device, cmd], timeout)

    def _q(self, *words):
        return ' '.join(shlex.quote(str(w)) for w in words)

    def fetch(self, remote_path, local_path):
        """Copy a host file to the machine running the runner."""
        if self.mode == 'local':
            shutil.copyfile(remote_path, local_path)
            return True
        rc, _ = timing.run(['scp', '-q', '-o', 'BatchMode=yes', '%s:%s' % (self.device, remote_path), local_path])
        return rc == 0

    def ref(self, vm):
        """A VM name as the beanjar that `sh` runs must take it.  That beanjar may have other hosts
        configured and then refuses a bare name that exists on more than one, so a VM of the machine
        `sh` runs on is always `local/NAME`, in local and remote mode alike."""
        return 'local/' + vm

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
        rc, out = self.beanjar('info', self.ref(vm))
        d = {}
        for line in out.splitlines():
            if ': ' in line:
                k, v = line.split(': ', 1)
                d.setdefault(k.strip(), v.strip())
        return d

    def awaits_lan(self, vm):
        """True while a LAN-linked VM has no LAN address yet; a NAT VM never gets one, so never waits."""
        d = self.info(vm)
        return d.get('link', 'lan') == 'lan' and d.get('lan_address') in (None, '', '-')

    def settings(self, vm):
        rc, out = self.beanjar('settings', self.ref(vm), 'show')
        return dict(l.split(': ', 1) for l in out.splitlines() if ': ' in l)

    def set_settings(self, vm, **kv):
        return self.beanjar('settings', self.ref(vm), 'set', *['%s=%s' % i for i in kv.items()])

    def start(self, vm):
        # Check the host immediately before every boot, including scenario reboots.
        running = self.running_vms()
        if running:
            raise RuntimeError('refusing boot while VMs are running: %s' % running)
        close_sessions()  # a root shell of the previous boot is gone
        result = self.beanjar('start', self.ref(vm), timeout=600)
        if result[0] == 0:
            self.vnc_start(vm)
        return result

    def stop(self, vm):
        self.vnc_stop(vm)
        close_sessions()  # before the VM goes, not a read that waits for a dead forward
        return self.beanjar('stop', self.ref(vm), timeout=300)

    def remote_target(self, vm):
        """The local multi-host CLI owns routing for display input: HOST/NAME, `local/NAME` for this PC."""
        return self.ref(vm) if self.mode == 'local' else self.name + '/' + vm

    def display_beanjar(self, *args, timeout=60):
        try:
            return timing.run(['beanjar', *args], timeout)
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
                                    '--hold', str(round(timing.seconds(100))))

    # -- input and capture
    def sendkey(self, vm, key):
        sock = self.vm_dir(vm) + '/run/qmp.sock'
        return self.sh(self._q('python3', '-c', QMP_SENDKEY, sock, key), 30)

    def viewer_open(self, vm):
        return self.beanjar('viewer', 'open', self.ref(vm), timeout=60)

    def viewer_close(self, vm):
        return self.beanjar('viewer', 'close', self.ref(vm), timeout=60)

    def tv_screenshot(self, vm, local_path):
        """`beanjar screenshot` (0.3.1+): the TV picture of the running VM, headless or through a viewer.
        Returns (ok, detail); detail is the command's output (--json: path, taken_by)."""
        tmp = '/tmp/e2e-tv-%s-%d.png' % (vm, os.getpid())
        rc, out = self.beanjar('screenshot', self.ref(vm), tmp, '--json', timeout=60)
        ok = rc == 0 and self.fetch(tmp, local_path)
        self.sh('rm -f ' + shlex.quote(tmp))
        return ok, out.strip()

    def screenshot(self, local_path):
        """No portable desktop capture exists in the host Python standard library."""
        return False, 'desktop capture unavailable: use beanjar screenshot or guest capture'
