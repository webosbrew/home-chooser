# SPDX-License-Identifier: Apache-2.0
"""A fake device run for the pytest device tests' --dry mode and the offline tests: no Beanjar, no VM.

DrySession stands where cli.VmSession stands and DryScenario where Scenario stands.  The plan is the real
one (run_plan for the matrix row's release) and it runs through the real PlanRun; each phase only records
that it ran (calls, also written to dry-calls.json) and one PASS check (qml-gate: XFAIL, as the real one).
--dry-fail adds a FAIL check to the named phases, --dry-manual a MANUAL one, --dry-raise makes them raise
('vm-start': the VM start fails), so the ordering, the selection, the incremental skips, the reports and the
cleanup can be shown without a device.
"""
import json
import os
import re

from .matrix import entry
from .plan import PER_RELEASE, PHASE, describe_plan, launcher_shape, plan_options, run_plan
from .recorder import Recorder
from .tiers import launcher_specs, spec_shape

# the smallest valid PNG (1x1, grey): the dry FAIL screenshot
PNG = bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010800000000'
                    '3a7e9b550000000a49444154789c63680000008200816e3c4f0b0000000049454e44ae426082')


class DryLauncher:
    """What the plan and the steps read of a Launcher, from its --launcher value alone (nothing built)."""

    def __init__(self, spec, ipk=None):
        runtime, window, demo = spec_shape(spec, ipk)
        self.spec, self.ipk = spec, ipk
        self.kind = 'fixture' if spec.startswith('fixture:') else 'app'
        self.id = 'org.webosbrew.hcfixture.%s' % spec[8:] if self.kind == 'fixture' else spec
        self.fixture = (runtime, 'demo' if demo else window) if self.kind == 'fixture' else None
        self.overlay = window == 'overlay'
        self.is_fixture, self.is_demo = self.kind == 'fixture', demo
        self.title = self.id
        self.tag = spec.replace(':', '-')


class DryScenario:
    """Scenario's interface to PlanRun, against nothing.  calls: [(what, launcher spec, phase), ...]."""

    def __init__(self, args, rec, row, launchers, calls, fail=(), raise_=(), manual=()):
        self.a, self.rec, self.launchers, self.calls = args, rec, launchers, calls
        self.release, self.vm = row[0], row[1]
        self.fail, self.raise_, self.manual = set(fail), set(raise_), set(manual)
        self.L, self.index = launchers[0], 0
        self.major = int(self.release.split('.')[0])
        self.insp, self.viewer_opened, self.ask_monitor = None, False, None
        self.shot_n = 0

    def begin_run(self):
        self.calls.append(('begin_run', '', ''))
        self.plan = run_plan([launcher_shape(L) for L in self.launchers], self.major, plan_options(self.a))
        self.rec.say('dry run: vm=%s release=%s, no device' % (self.vm, self.release))
        self.rec.say('plan, one boot (e2e/README.md "Scopes"):\n    '
                     + '\n    '.join(describe_plan(self.plan, self.launchers)))
        runners = {p: (lambda p=p: self.phase(p)) for p in PHASE if p != 'cleanup'}
        runners['api-before'] = None  # made inside choose, as in Scenario.begin_run
        return runners

    def phase(self, name):
        r = self.rec
        self.calls.append(('phase', self.L.spec, name))
        r.begin('%s (dry)' % name)
        if name == 'qml-gate':  # as Scenario.step_qml_gate: the documented limitation, XFAIL
            r.check('dry: qml fixtures run on %d.x' % self.major, False, 'fixtures/qml/support.json', expected='dry')
        else:
            r.check('dry: %s ran' % name, True, 'fake device, launcher %s' % self.L.spec)
        if name in self.manual:
            r.manual('dry: a check left to a person in %s' % name, '--dry-manual')
        if name in self.fail:
            r.check('dry: injected failure in %s' % name, False, '--dry-fail')
        if name in self.raise_:
            raise RuntimeError('--dry-raise: %s raised' % name)

    def use_launcher(self, index):
        self.index, self.L = index, self.launchers[index]
        self.rec.tag = '[%s]' % self.L.spec if len(self.launchers) > 1 else ''
        self.calls.append(('use_launcher', self.L.spec, ''))

    def uses_home_key_hook(self):
        return 6 <= self.major <= 9

    def compositor_snapshot(self):
        self.calls.append(('compositor_snapshot', self.L.spec, ''))
        return {'MainPID': '1'}

    def check_no_crash(self, label, before):
        self.calls.append(('check_no_crash', self.L.spec, label))
        self.rec.check('dry: no crash during the step', True, label)

    def end_run(self):
        r = self.rec
        owner = self.plan[0][0]
        if (owner, 'cleanup') in self.plan:
            self.use_launcher(owner)
            r.tag, r.scope = '', PER_RELEASE
            self.calls.append(('phase', self.L.spec, 'cleanup'))
            r.begin('6 cleanup (dry)')
            r.check('dry: no test apps left installed', 'cleanup' not in self.fail, 'fake device')
            if 'cleanup' in self.raise_:
                raise RuntimeError('--dry-raise: cleanup raised')
        else:
            r.say('--until %d: apps left installed for debugging; no cleanup' % self.a.until)

    def fail_shot(self, name):
        """Recorder.on_fail, as Scenario.fail_shot: one picture per step with a FAIL."""
        done = self.__dict__.setdefault('fail_shots', set())
        if self.rec.step in done:
            return
        done.add(self.rec.step)
        self.shot_n += 1
        label = re.sub(r'[^A-Za-z0-9]+', '-', self.rec.step).strip('-')[:60] or 'run'
        path = os.path.join(self.rec.outdir, '%02d-fail-%s.png' % (self.shot_n, label))
        with open(path, 'wb') as f:
            f.write(PNG)
        self.rec.say('screenshot %s (dry)' % os.path.basename(path))


class DrySession:
    """cli.VmSession's interface without a VM: bring_up 'starts' it, tear_down 'stops' it."""

    def __init__(self, args, fail=(), raise_=(), manual=()):
        self.args = args
        self.row = entry(args.vm)
        self.vm = self.row[1]
        specs = launcher_specs(args)
        self.launchers = [DryLauncher(s, args.launcher_ipk if len(specs) == 1 else None) for s in specs]
        args.multi = len(specs) > 1
        self.fail, self.raise_, self.manual = set(fail), set(raise_), set(manual)
        self.outdir = args.outdir
        os.makedirs(self.outdir, exist_ok=getattr(args, 'outdir_made', False))
        self.rec = Recorder(self.outdir)
        self.rec.say('artefacts: ' + self.outdir)
        self.calls = []
        self.scenario = None

    def bring_up(self):
        self.calls.append(('vm start', self.vm, ''))
        if 'vm-start' in self.raise_:
            raise SystemExit('--dry-raise: the VM did not start')
        self.scenario = DryScenario(self.args, self.rec, self.row, self.launchers, self.calls,
                                    self.fail, self.raise_, self.manual)
        self.rec.on_fail = self.scenario.fail_shot

    def tear_down(self):
        self.rec.on_fail = None
        self.calls.append(('vm stop', self.vm, ''))
        with open(os.path.join(self.outdir, 'dry-calls.json'), 'w') as f:
            json.dump(self.calls, f, indent=1)
