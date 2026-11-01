# SPDX-License-Identifier: Apache-2.0
"""Device state the steps read: watcher, hook status, compositor and crash windows, reboots, residency."""
import json
import shlex
import time

from .. import timing
from ..constants import CHOICE, FLAG, FULLHOME, GET_PROFILES, INITD, OVERRIDE, SCRIPTS_DIR, STOCK
from ..evidence import CRASH_RE, KNOWN_CRASH_DOC, crash_lines, messages_command, parse_status, split_known_crashes
from ..preflight import READY, WAIT_STATES, wait_for_device
from ..releases import idle_app_stock, idle_app_verdict, profile_idle_app, stock_record_ok, uses_home_key_hook


class DeviceState:
    """Device state the steps read: watcher, hook status, compositor and crash windows, reboots, residency."""

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
        """After a reboot (any route): LAN address, Homebrew Channel, a new boot id."""
        r, host = self.rec, self.host
        end = time.time() + timing.seconds(120)
        while time.time() < end and (host.info(self.vm).get('lan_address') in (None, '', '-')):
            timing.sleep(3)
        state = wait_for_device(self.dev, r)
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
        end = time.time() + timing.seconds(timeout)
        gone = False
        last_boot = boot0
        while time.time() < end:
            timing.sleep(10)
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
        where the worker went with the app directory).  10+: null/unset, no override, idleApp stock."""
        r = self.rec
        want = {5: 'a stock launcher (%s or %s)' % (FULLHOME, STOCK)}.get(self.major) or (
            'the stock id %s, not null' % self.stock_id if self.uses_home_key_hook() else 'null or unset')
        r.check(label + ': record home is %s' % want, self.stock_record_ok(s['record_home']), s['record_raw'])
        if self.uses_override():
            r.check(label + ': profile override absent', s['override'] == 'no', 'override exists: ' + s['override'])
            self.check_idle_stock(label)
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
        rc, raw, _ = self.dev.luna_as('com.webos.applicationManager', uri, payload)
        return raw

    # -- the system profile's idleApp (10.x and later; docs/settings-app.md "What is written")
    def idle_app(self):
        """(idleApp object or None, raw reply) of getProfiles, as sam reads it."""
        raw = self.priv_luna(GET_PROFILES, '{"names":["idleApp"]}')
        return profile_idle_app(raw), raw

    def check_idle_chosen(self, label):
        """With the launcher chosen: idleApp names it (full-screen) or has support false (overlay)."""
        want = idle_app_verdict(None, self.launcher, self.L.overlay)[1]
        self.wait(lambda: idle_app_verdict(self.idle_app()[0], self.launcher, self.L.overlay)[0], 30, 3)
        idle, raw = self.idle_app()
        ok = idle_app_verdict(idle, self.launcher, self.L.overlay)[0]
        self.rec.check('%s: getProfiles idleApp is %s' % (label, want), ok, raw)

    def check_idle_stock(self, label):
        """No override: idleApp is the stock one again (LG Home, support true), never a missing launcher."""
        self.wait(lambda: idle_app_stock(self.idle_app()[0]), 30, 3)
        idle, raw = self.idle_app()
        baseline = getattr(self, 'stock_idle', None)
        self.rec.check(label + ': getProfiles idleApp is back to stock (%s, support true)' % STOCK, idle_app_stock(idle),
                       raw + ('' if baseline is None else '; before Home Chooser: %s' % json.dumps(baseline)))
