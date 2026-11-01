# SPDX-License-Identifier: Apache-2.0
"""One pytest test per planned phase (e2e/device), without pytest: the options a pytest run turns into the
command line's, the test ids, and PhaseTests, which runs one VM session phase by phase and says what each
test's outcome is.  e2e/device/plugin.py only maps these outcomes to pytest.

A test is a phase of the run's plan, named test_<vm>[<launcher>-<phase>] (test_w22h_ombre[web.overlay-reboot]),
so `-k reboot`, `-k api` or `-k "web.overlay and home"` select phases.  The tests run in plan order on one
boot; a selected phase first runs every earlier phase of the plan that has not run (reported as its setup),
since each one leaves the device in the state the next expects.  The cleanup (step 6) and the VM stop run at
the end whatever happened; a phase test of its own, `-cleanup`, reports the cleanup's checks when selected.
"""
import argparse
import glob
import os
import re
import traceback

from .matrix import entry
from .plan import PlanRun, plan_options, run_plan
from .tiers import apply_tier, launcher_specs, spec_shape, updates

# the pytest options that map one to one onto the command line's (homechooser_e2e.py --help)
VALUE_OPTIONS = ('vm', 'host', 'launchers', 'hc_ipk', 'uninstall_via', 'outdir')


def runner_argv(opts, cfg):
    """The homechooser_e2e.py arguments of a pytest run.  opts: the pytest options (vm, host, tier, launchers,
    hc_ipk, api_check, reboot_check, uninstall_via, update_check, outdir, hc_arg); a tier fills what is unset,
    as run_matrix.py does for one slot (hc_e2e.tiers).  -> (argv, why not) ."""
    o = dict(opts)
    if o.get('tier'):
        t = argparse.Namespace(tier=o['tier'], vms=o.get('vm'), launcher=None, launchers=o.get('launchers'),
                               api_check=o.get('api_check'), reboot_check=o.get('reboot_check'),
                               uninstall_via=o.get('uninstall_via'), update_check=o.get('update_check'),
                               update_on=None, launcher_update_check=False)
        apply_tier(t, cfg)
        if not t.vms:
            return None, ('--tier %s runs every matrix slot: that is run_matrix.py --tier %s --pytest; '
                          'here give --vm' % (o['tier'], o['tier']))
        row = entry(t.vms)
        o.update(vm=t.vms, launchers=t.launchers, api_check=t.api_check, reboot_check=t.reboot_check,
                 uninstall_via=t.uninstall_via, update_check=updates({'vm': row[1], 'release': row[0]}, t.update_on),
                 launcher_update_check=t.launcher_update_check)
    if not o.get('vm'):
        return None, '--vm is required (a VM name or a release such as 9.x), or --tier smoke for the default slot'
    argv = []
    for key in VALUE_OPTIONS:
        if o.get(key):
            argv += ['--' + key.replace('_', '-'), o[key]]
    if o.get('api_check'):
        argv.append('--api-check=' + o['api_check'])
    if o.get('reboot_check'):
        argv.append('--reboot-check=' + o['reboot_check'])
    if o.get('update_check'):
        argv.append('--update-check')
    if o.get('launcher_update_check'):
        argv.append('--launcher-update-check')
    return argv + list(o.get('hc_arg') or []), None


def launcher_label(spec):
    """The launcher's part of a test id: a fixture by its name (web.overlay), an author launcher by its id."""
    return spec[len('fixture:'):] if spec.startswith('fixture:') else spec


def test_name(vm):
    return 'test_' + re.sub(r'\W', '_', vm)


def planned_tests(args):
    """[(test id, (launcher index, phase))] of a parsed command line, in plan order, from the matrix row's
    release (the run itself re-plans from the release it finds on the device)."""
    row = entry(args.vm)
    specs = launcher_specs(args)
    shapes = [spec_shape(s, args.launcher_ipk if len(specs) == 1 else None) for s in specs]
    plan = run_plan(shapes, int(row[0].split('.')[0]), plan_options(args))
    labels = [launcher_label(s) for s in specs]
    if len(set(labels)) < len(labels):
        labels = ['%d.%s' % (i, x) for i, x in enumerate(labels)]
    return [('%s-%s' % (labels[i], phase), (i, phase)) for i, phase in plan]


def counts_of(results):
    counts = {}
    for r in results:
        counts[r['status']] = counts.get(r['status'], 0) + 1
    return counts


def describe(result):
    return '%s %s [%s]: %s' % (result['status'], result['name'], result['step'],
                               (result['evidence'] or '').strip().replace('\n', ' ')[:300])


class PhaseTests:
    """One session's phase tests.  make_session() -> a cli.VmSession or dry.DrySession (rec, bring_up,
    scenario, tear_down).  open() before the first test, run_test(item) per test, close() at the end, always.
    Outcomes: {'status': passed|failed|skipped|xfailed, 'message', 'counts', 'setup', 'screens'}."""

    def __init__(self, make_session, ids):
        self.make_session = make_session
        self.ids = dict((item, tid) for tid, item in ids)
        self.session = None
        self.run = None
        self.setup_error = None
        self.error = None  # (exception, traceback) of a phase that ended the run: recorded at close(), as main()
        self.closed = False
        self.interrupted = False  # Ctrl-C in a phase: no phase runs after it, and the run is not a pass
        self.counts = None

    # -- the session
    def open(self):
        try:
            self.session = self.make_session()
        except SystemExit as e:
            self.setup_error = 'the runner refused the options: %s' % e
            return
        except Exception as e:
            self.setup_error = 'the runner could not prepare the run: %r\n%s' % (e, traceback.format_exc())
            return
        rec = self.session.rec
        try:
            self.session.bring_up()
            self.run = PlanRun(self.session.scenario)
            self.run.start()
        except SystemExit as e:  # what main() records
            rec.say('abort: %s' % e)
            rec.on_fail = None
            rec.check('runner setup', False, str(e))
            self.setup_error = 'runner setup: %s' % e
        except Exception as e:
            rec.say('runner error:\n' + traceback.format_exc())
            rec.on_fail = None
            rec.check('scenario ran to the end', False, repr(e))
            self.setup_error = 'runner error before the first phase: %r' % (e,)

    def close(self):
        """The cleanup unless a test made it, the deferred runner error (as main() records it), the VM stop,
        results.json.  -> the FAIL results no test has reported: the cleanup's, made here (the teardown's
        verdict)."""
        if self.closed or self.session is None:
            return []
        self.closed = True
        rec = self.session.rec
        mark, fails = len(rec.results), []
        try:
            raised = False
            if self.run is not None and getattr(self.run, 'plan', None) and not self.run.finished:
                try:
                    self.run.finish()
                except Exception as e:
                    self.error, raised = (e, traceback.format_exc()), True
            fails = [r for r in rec.results[mark:] if r['status'] == 'FAIL']
            if self.error:
                rec.say('runner error:\n' + self.error[1])
                rec.on_fail = None
                rec.check('scenario ran to the end', False, repr(self.error[0]))
                if raised:
                    fails.append(rec.results[-1])
        finally:
            self.session.tear_down()
            self.counts = rec.finish()
        return fails

    # -- one test
    def screens(self):
        return set(glob.glob(os.path.join(self.session.rec.outdir, '*.png'))) if self.session else set()

    def run_test(self, item):
        if self.setup_error:
            return self.outcome('failed', self.setup_error)
        if self.interrupted:
            return self.outcome('failed', 'the run was interrupted earlier; this phase did not run')
        rec, run = self.session.rec, self.run
        mark, shots = len(rec.results), self.screens()
        if item[1] == 'cleanup':
            if run.finished:
                return self.outcome('skipped', 'the cleanup already ran')
            pending = [x for x in run.items if x not in run.done]
        elif item not in run.where:
            return self.outcome('skipped', 'not in the plan for the release on the device (%s.x): %s'
                                % (self.session.scenario.major, ', '.join(self.ids.get(x, x[1]) for x in run.items)))
        elif item in run.done:
            return self.outcome('skipped', 'already ran as the setup of an earlier test')
        else:
            pending = run.pending(item)
        own = None
        try:
            for x in pending:
                run.run_phase(x)
            if item[1] == 'cleanup':
                run.finish()
            else:
                own = run.run_phase(item)
        except KeyboardInterrupt as e:  # Ctrl-C: a failure (never a pass), the cleanup still comes at close()
            self.interrupted = True
            self.error = (e, traceback.format_exc())
            where = self.ids.get(run.done[-1], run.done[-1]) if run.done else item[1]
            return self.outcome('failed', 'interrupted (Ctrl-C) in %s' % (where,), rec.results[mark:],
                                [x for x in pending if x in run.done], shots)
        except Exception as e:  # a phase of the first launcher raised: the run ends, the cleanup comes at close()
            self.error = (e, traceback.format_exc())
            where = 'the cleanup' if item[1] == 'cleanup' else self.ids.get(run.done[-1], run.done[-1])
            return self.outcome('failed', '%s raised %r\n%s' % (where, e, self.error[1]), rec.results[mark:],
                                [x for x in pending if x in run.done], shots)
        results = rec.results[mark:]
        fails = [r for r in results if r['status'] == 'FAIL']
        if fails:
            return self.outcome('failed', '\n'.join(describe(r) for r in fails), results, pending, shots)
        if own and own['status'] in ('skipped', 'outcome'):
            return self.outcome('skipped', own['reason'], results, pending, shots)
        counts = counts_of(own['results'] if own else results)
        if counts.get('XFAIL') and not counts.get('PASS'):
            return self.outcome('xfailed', '; '.join(r['expected'] or r['name'] for r in results
                                                     if r['status'] == 'XFAIL'), results, pending, shots)
        return self.outcome('passed', '', results, pending, shots)

    def outcome(self, status, message, results=(), setup=(), shots=None):
        new = sorted(self.screens() - shots) if shots is not None else []
        return {'status': status, 'message': message, 'counts': counts_of(results),
                'setup': [self.ids.get(x, '%s-%s' % x) for x in setup], 'screens': new,
                'results': list(results)}
