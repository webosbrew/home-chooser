# SPDX-License-Identifier: Apache-2.0
"""The author-TV API contract (--device): ares tools only, no Beanjar, no reboot."""
import json
import os
import shutil
import time

from .alerts import RebootAlertMonitor
from .api_spec import DISPOSABLE_ID
from .constants import APP_ID, FULLHOME, HERE, STOCK
from .device import Device, ELEVATIONS
from .inspector import Inspector
from .launchers import api_fixture_ipks, make_launcher
from .recorder import Recorder
from .releases import stock_baseline_ok, stock_home_id
from .scenario import Scenario


# Maintainer only: set to reach --device NAME through `beanjar ares-TOOL -d NAME` (NAME as Beanjar takes it,
# [HOST/]VM) instead of plain `ares-TOOL -d NAME`.  An author never sets it; unset, nothing changes.
VIA_BEANJAR_ENV = 'HC_E2E_ARES_VIA_BEANJAR'
ARES_TOOLS = ('ares-install', 'ares-launch', 'ares-do', 'ares-shell', 'ares-novacom')


def via_beanjar():
    return os.environ.get(VIA_BEANJAR_ENV, '') not in ('', '0')


def device_for(name):
    return Device(name, beanjar=True) if via_beanjar() else Device.tv(name)


def missing_tools():
    """What the run needs from PATH and is not there: the ares tools (plain), or beanjar and its wrapper."""
    if via_beanjar():
        return [] if shutil.which('beanjar') else ['beanjar (%s is set)' % VIA_BEANJAR_ENV]
    return [t for t in ARES_TOOLS if not shutil.which(t)]


def result_dir_name(device):
    """A device name as one directory name: a Beanjar target is HOST/NAME."""
    return device.replace('/', '-')


class DeviceApiScenario(Scenario):
    """API contract on an existing rooted ares device; no Beanjar or reboot operations."""

    def __init__(self, args, rec, launcher):
        super().__init__(args, None, rec, ('device', args.device, 'luna', 'ares', 'author TV'), launcher)
        self.dev = device_for(args.device)
        self.dev.say = rec.say
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
        self.insp = Inspector(self.dev)
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
            strategy = ELEVATIONS[self.a.elevation or 'luna']()
            strategy.prepare(d, r)
            self.insp = Inspector(self.dev)
            d.launch(APP_ID)
            if not self.wait(lambda: (self.ui() or {}).get('rows'), 90, 1):
                raise RuntimeError('Home Chooser UI did not load')
            strategy.verify(d, r)
            self.api_before()
            r.begin('device selection')
            if not self.choose_on_device(self.launcher):
                return
            self.api_selected()
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


def run_device_api(args, ap):
    """Run the launcher contract using only ares tools on an author's TV."""
    if args.vm or not args.api_check or args.launchers or args.update_check or args.reboot_check or args.build:
        ap.error('--device requires --api-check and cannot be combined with VM, matrix, update, reboot or build options')
    args.input = 'ares'
    lacking = missing_tools()
    if lacking:
        ap.error('--device needs these on PATH: %s' % ', '.join(lacking))
    spec = args.launcher or 'fixture:web.normal'
    launcher = make_launcher(spec, args.launcher_ipk, api=True)
    args.disposable_ipk = api_fixture_ipks()[DISPOSABLE_ID]
    outdir = args.outdir or os.path.join(HERE, 'results', '%s-%s' %
                                        (result_dir_name(args.device), time.strftime('%Y%m%d-%H%M%S')))
    os.makedirs(outdir)
    rec = Recorder(outdir)
    rec.say('ares-only device API contract: %s; artefacts: %s' % (args.device, outdir))
    scenario = None
    try:
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
