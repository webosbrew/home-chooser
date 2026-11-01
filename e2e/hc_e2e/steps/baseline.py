# SPDX-License-Identifier: Apache-2.0
"""Phases baseline (step 0), qml-gate and install (step 1)."""
import json

from ..api_spec import DISPOSABLE_ID
from ..constants import APP_ID, FULLHOME, STOCK
from ..launchers import DEMO_TARGET_ID, DEMO_TARGET_TITLE, current_home_chooser_ipk, qml_support
from ..releases import stock_home_id


class BaselineSteps:
    """Phases baseline (step 0), qml-gate and install (step 1)."""

    def step0(self):
        r, d = self.rec, self.dev
        r.begin('0 baseline')
        r.check('device release matches the matrix (%s)' % self.release,
                '%d.' % self.major == self.release[:-1], 'os-release: ' + self.osr)
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
        screensaver = getattr(self.a, 'scenario', 'lifecycle') in ('screensaver', 'all')
        self.saver_baseline = self.screensaver_baseline() if screensaver and self.planned('screensaver') else None

    def step_qml_gate(self):
        """A qml launcher on a release where fixtures/qml/support.json says qml does not run: XFAIL, skipped."""
        r = self.rec
        sup, ev = qml_support(self.major)
        r.note('qml support expectation for %d.x: %s' % (self.major, sup), ev)
        r.check('qml fixtures run on %d.x' % self.major, False, ev, expected=ev)

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
        if self.uses_override():
            self.stock_idle, raw = self.idle_app()
            r.note('getProfiles idleApp before Home Chooser', raw)
        rc, out = d.install(hc)
        r.check('install Home Chooser', rc == 0 and 'Installed package' in out, out.strip())
        rc, out = d.install(self.L.ipk)
        r.check('install launcher %s' % self.launcher, rc == 0 and 'Installed package' in out, out.strip())
        if any(p == 'cache' for _, p in getattr(self, 'plan', ())):
            self.cache_prepare_measure()
        r.note('launcher appinfo (from the ipk; overlay=%s)' % self.L.overlay, json.dumps(self.L.appinfo))
        self.qml_note()
        if self.a.api_check:
            rc, out = d.install(self.a.disposable_ipk)
            r.check('--api-check: install the disposable app %s' % DISPOSABLE_ID, rc == 0 and 'Installed package' in out,
                    out.strip())
        if getattr(self.L, 'is_demo', False):
            rc, out = d.install(self.a.demo_target_ipk)
            r.check('fixture:web.demo: install the app step 3d opens, %s (%r)' % (DEMO_TARGET_ID, DEMO_TARGET_TITLE),
                    rc == 0 and 'Installed package' in out, out.strip())
        r.check('both apps are listed', APP_ID in d.installed() and self.launcher in d.installed(),
                str(d.installed()))
        strat.prepare(d, r)
        self.strat = strat
