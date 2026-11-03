# SPDX-License-Identifier: Apache-2.0
"""The Scenario: one VM run, its launchers on one boot, and the steps it is made of (hc_e2e.steps)."""
from .device import Device, ELEVATIONS
from .plan import PER_RELEASE, PlanRun, describe_plan, launcher_shape, plan_options, run_plan
from .steps.api import ApiSteps
from .steps.baseline import BaselineSteps
from .steps.choose import ChooseSteps
from .steps.demo import DemoSteps
from .steps.home_press import HomePress
from .steps.home_proof import HomeProofSteps
from .steps.level3 import Level3Steps
from .steps.panel import PanelSteps
from .steps.popups import PopupsAndNotifications
from .steps.preload import PreloadPolicy
from .steps.reboot import RebootSteps
from .steps.removal import RemovalSteps
from .steps.screen import ScreenInput
from .steps.screensaver import ScreensaverSteps
from .steps.state import DeviceState
from .steps.update import UpdateSteps

# ---------------------------------------------------------------------------------------------
# The scenario
# ---------------------------------------------------------------------------------------------
class Scenario(ScreenInput, DeviceState, PopupsAndNotifications, PreloadPolicy, HomePress, BaselineSteps,
               ScreensaverSteps, ChooseSteps, PanelSteps, RebootSteps, HomeProofSteps, DemoSteps, ApiSteps,
               Level3Steps, UpdateSteps, RemovalSteps):
    def __init__(self, args, host, rec, row, launcher, launchers=None):
        self.a, self.host, self.rec = args, host, rec
        self.L = launcher
        self.launchers = list(launchers or [launcher])  # every launcher of the run, on one boot (run_plan)
        self.release, self.vm, self.strategy_name, self.sure, self.note = row
        self.dev = Device.for_vm(host, self.vm)
        self.dev.say = rec.say
        self.launcher = launcher.id
        self.shot_n = 0
        self.insp = None
        self.capture_broken = False
        self.viewer_opened = False
        self.major = 0  # webOS TV release, 5..12
        self.stock_id = None  # what restore/removal/uninstall write as home (stock_home_id), from the baseline

    # -- steps
    def plan_options(self):
        return plan_options(self.a)

    def use_launcher(self, index):
        """Make launcher `index` of the run the one the steps act on (a switch, on the same boot)."""
        self.index = index
        self.L = self.launchers[index]
        self.launcher = self.L.id
        self.rec.tag = '[%s]' % self.L.spec if getattr(self.a, 'multi', False) else ''

    def planned(self, phase, index=None):
        """Whether the run plan has `phase` for launcher `index` (default: the current one)."""
        return (getattr(self, 'index', 0) if index is None else index, phase) in getattr(self, 'plan', ())

    def is_owner(self):
        """The current launcher makes the per-release checks (the first runnable one of the run)."""
        plan = getattr(self, 'plan', None)
        return not plan or getattr(self, 'index', 0) == plan[0][0]

    def begin_run(self):
        """What a run does before its first phase: the head of the run log, the release, the plan.  Returns
        the phase runners for PlanRun (None: the phase is made inside another one)."""
        r, d = self.rec, self.dev
        self.launchers = getattr(self, 'launchers', None) or [self.L]
        strat = ELEVATIONS[self.a.elevation or self.strategy_name]()
        r.say('run: vm=%s release=%s elevation=%s (matrix default %s) input=%s host=%s launchers=%s'
              % (self.vm, self.release, strat.name, self.strategy_name, self.a.input, self.host.mode,
                 '; '.join('%s (%s, overlay=%s, ipk %s)' % (L.id, L.spec, L.overlay, L.ipk) for L in self.launchers)))
        r.say('matrix confidence: %s -- %s' % (self.sure, self.note))
        self.major, osr = d.release()
        self.osr = osr
        self.plan = run_plan([launcher_shape(L) for L in self.launchers], self.major, self.plan_options())
        r.say('plan, one boot (e2e/README.md "Scopes"):\n    ' + '\n    '.join(describe_plan(self.plan, self.launchers)))
        return {
            'baseline': self.step0, 'install': lambda: self.step1(strat), 'choose': self.step2,
            'screensaver': lambda: self.screensaver_selected(self.saver_baseline),
            'switch': self.step_switch, 'window-chosen': self.window_chosen, 'home-proof': self.step3,
            'qml-gate': self.step_qml_gate, 'window-home': self.window_home, 'demo': self.step_demo,
            'api-selected': self.api_selected, 'reboot': self.step_reboot, 'cache': self.step_cache,
            'update': self.step_update, 'launcher-update': self.step_launcher_update,
            'restore-repair': self.step_restore_repair, 'removal-race': self.step_removal_race, 'removal': self.step4, 'uninstall': self.step5,
        }

    def end_run(self):
        """What a run does after its last phase, whatever happened: the alert monitor closed, then the
        cleanup (step 6) unless --until left the apps installed."""
        r = self.rec
        if getattr(self, 'ask_monitor', None):
            self.ask_monitor.close()
            self.ask_monitor = None
        self.report_platform_crashes()
        owner = self.plan[0][0]
        if (owner, 'cleanup') in self.plan:
            self.use_launcher(owner)
            r.tag, r.scope = '', PER_RELEASE
            self.step6()
        else:
            r.say('--until %d: apps left installed for debugging; no cleanup' % self.a.until)

    def run(self):
        """The whole plan in order, then the cleanup (PlanRun)."""
        run = PlanRun(self)
        run.start()
        try:
            run.run_all()
        finally:
            run.finish()
