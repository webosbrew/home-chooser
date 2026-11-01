# SPDX-License-Identifier: Apache-2.0
"""Phases update (3u), launcher-update (3v) and restore-repair (3f)."""
import hashlib
import json
import os
import re
import shlex
import shutil
import tempfile

from .. import timing
from ..constants import (APPDIR, APP_ID, BOOT_DIR, BOOT_SCRIPT, CHOICE, FLAG, HERE, INITD, REPO, SCRIPTS_DIR, STOCK,
    SVC_ID, uninstall_remnant_paths)
from ..evidence import messages_command, parse_luna
from ..inspector import Inspector, TICK_JS
from ..launchers import ipk_appinfo, ipk_files


class UpdateSteps:
    """Phases update (3u), launcher-update (3v) and restore-repair (3f)."""

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
        self.insp = Inspector(self.dev)
        front = self.open_panel(label)
        seen = []

        def listed():
            u = self.ui()
            if u and u.get('toast'):
                seen.append(u['toast'])
            return u if u and self.launcher in (u.get('rows') or []) else None
        ui = self.wait(listed, 120, every=1)
        timing.sleep(4)
        ui = self.ui() or ui or {}
        if ui.get('toast'):
            seen.append(ui['toast'])
        self.shot(label)
        r.check('%s: the app opens and lists the launcher' % label, bool(ui) and self.launcher in (ui.get('rows') or []),
                '%s; Home Chooser in front: %s' % (json.dumps(ui), front))
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
            rc, out = timing.run(['sh', os.path.join(HERE, 'fixtures', 'web', 'build.sh'), temp], 600, env=env)
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
