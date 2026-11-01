# SPDX-License-Identifier: Apache-2.0
"""Phases removal (4), uninstall (5) and cleanup (6)."""
import json
import shlex

from .. import timing
from ..api_spec import DISPOSABLE_ID
from ..constants import APP_ID, CHOICE, STOCK, uninstall_remnant_paths
from ..launchers import DEMO_TARGET_ID
from ..matrix import KEEP_LAUNCHER_VMS


class RemovalSteps:
    """Phases removal (4), uninstall (5) and cleanup (6)."""

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

    def step5(self):
        r, d = self.rec, self.dev
        r.begin('5 uninstall Home Chooser')
        toasts_before = len(self.notif_lines('createToast'))
        comp0 = self.compositor_snapshot() if self.uses_home_key_hook() else None
        rc, out = d.remove(APP_ID)
        r.check('ares-install --remove Home Chooser', rc == 0, out.strip())
        self.check_toast(toasts_before)
        self.wait(lambda: self.watcher_active() != 'active' and self.stock_record_ok(d.record_home()[0]), 150, 5)
        timing.sleep(5)
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
            r.note('this VM keeps its launcher installed and selected: restore by hand (Home Chooser must be '
                   'installed and the launcher chosen through its UI); the runner does not do that yet', '')
        else:
            removed_hc = False
            launchers = getattr(self, 'launchers', None) or [self.L]
            ids = list(dict.fromkeys([L.id for L in launchers] if getattr(self, 'launchers', None) else [self.launcher]))
            apps = ids + [APP_ID] + ([DISPOSABLE_ID] if self.a.api_check else []) \
                + ([DEMO_TARGET_ID] if any(getattr(L, 'is_demo', False) for L in launchers) else [])
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
        self.shot('final')
