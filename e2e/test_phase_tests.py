#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Offline tests of the pytest device tests (e2e/device): the test ids a tier and its launchers get, the
options a pytest run hands the runner, PlanRun and PhaseTests against the fake device (hc_e2e.dry): the
plan order, the earlier phases pulled in as setup, the incremental skips, the verdicts, and the cleanup and
VM stop at the end whatever happened.  With pytest and pytest-html installed (e2e/requirements.txt) the
dry device run itself is run, and its junit.xml and report.html are read back.

    python3 -I -m unittest discover -s e2e -p 'test_*.py'
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hc_e2e.cli  # noqa: E402
import hc_e2e.plan  # noqa: E402
import hc_e2e.tiers  # noqa: E402
import run_matrix  # noqa: E402
from hc_e2e.dry import DrySession  # noqa: E402
from hc_e2e.phase_tests import PhaseTests, planned_tests, runner_argv  # noqa: E402
from hc_e2e.phase_tests import test_name as vm_test_name  # noqa: E402  (not a test here)

HAVE_PYTEST = all(importlib.util.find_spec(m) for m in ('pytest', 'pytest_html'))
CFG = {'default': 'pi', 'default_slot': '9.x', 'hosts': {'pi': {'ssh': 'raspberrypi'}}}


def parsed(argv):
    return hc_e2e.cli.build_parser().parse_args(argv)


def ids(argv):
    return [tid for tid, _ in planned_tests(parsed(argv))]


class TestIds(unittest.TestCase):
    def test_smoke_one_launcher_in_plan_order(self):
        argv, why = runner_argv({'tier': 'smoke'}, CFG)
        self.assertIsNone(why)
        self.assertEqual(argv[:2], ['--vm', '9.x'])
        self.assertEqual(ids(argv), ['web.normal-baseline', 'web.normal-install', 'web.normal-choose',
                                     'web.normal-window-chosen', 'web.normal-home-proof',
                                     'web.normal-api-selected', 'web.normal-removal', 'web.normal-uninstall',
                                     'web.normal-cleanup'])

    def test_release_tier_on_12_both_windows_reboot_each(self):
        argv, why = runner_argv({'tier': 'release', 'vm': '12.x'}, CFG)
        self.assertIsNone(why)
        got = ids(argv)
        self.assertEqual(got[0], 'web.normal-baseline')
        self.assertEqual(got[-2:], ['web.normal-uninstall', 'web.normal-cleanup'])
        for tid in ('web.normal-reboot', 'web.overlay-switch', 'web.overlay-window-home', 'web.overlay-reboot'):
            self.assertIn(tid, got)
        self.assertNotIn('web.normal-update', got)  # the release tier updates on UPDATE_SLOT only
        self.assertEqual(len(got), len(set(got)))

    def test_release_tier_update_on_its_slot_and_cache_never_with_it(self):
        argv, _ = runner_argv({'tier': 'release', 'vm': hc_e2e.tiers.UPDATE_SLOT}, CFG)
        self.assertIn('--update-check', argv)
        got = ids(argv)
        self.assertIn('web.normal-update', got)
        self.assertNotIn('web.normal-cache', got)
        argv, _ = runner_argv({'tier': 'release', 'vm': 'w22h-ombre'}, CFG)
        self.assertIn('web.normal-cache', ids(argv))

    def test_full_tier_six_launchers_and_qml_xfail_on_12(self):
        argv, _ = runner_argv({'tier': 'full', 'vm': '12.x'}, CFG)
        got = ids(argv)
        self.assertEqual(sorted({t.rsplit('-', 1)[0].split('-')[0] for t in got}),
                         ['native.normal', 'native.overlay', 'qml.normal', 'qml.overlay', 'web.normal', 'web.overlay'])
        self.assertIn('qml.normal-qml-gate', got)
        self.assertIn('web.normal-launcher-update', got)

    def test_an_option_wins_over_the_tier(self):
        argv, _ = runner_argv({'tier': 'release', 'vm': '9.x', 'launchers': 'fixture:native.normal',
                               'reboot_check': 'all', 'api_check': None}, CFG)
        self.assertIn('--reboot-check=all', argv)
        self.assertEqual(argv[argv.index('--launchers') + 1], 'fixture:native.normal')
        self.assertTrue(all(t.startswith('native.normal-') for t in ids(argv)))

    def test_release_and_full_need_a_vm_smoke_takes_the_default_slot(self):
        for tier in ('release', 'full'):
            argv, why = runner_argv({'tier': tier}, CFG)
            self.assertIsNone(argv)
            self.assertIn('run_matrix.py --tier %s --pytest' % tier, why)
        self.assertIsNone(runner_argv({}, CFG)[0])
        argv, _ = runner_argv({'vm': 'w22h-ombre', 'hc_arg': ['--input-mode', 'keys']}, CFG)
        self.assertEqual(argv, ['--vm', 'w22h-ombre', '--input-mode', 'keys'])

    def test_test_names_and_selection_words(self):
        self.assertEqual(vm_test_name('w22o-ponytail-us'), 'test_w22o_ponytail_us')
        argv, _ = runner_argv({'tier': 'release', 'vm': '10.x'}, CFG)
        got = ids(argv)
        # -k matches substrings of the id: these words pick what they say
        self.assertEqual([t for t in got if 'reboot' in t], ['web.normal-reboot', 'web.overlay-reboot'])
        self.assertEqual([t for t in got if 'web.overlay' in t and 'home' in t],
                         ['web.overlay-home-proof', 'web.overlay-window-home'])
        self.assertTrue(all(t.endswith('api-selected') for t in got if 'api' in t))


class FakeRun(unittest.TestCase):
    """PhaseTests over the dry session: no pytest needed."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))  # the Recorder's console lines

    def session_tests(self, argv, fail=(), raise_=(), manual=()):
        args = parsed(argv + ['--outdir', os.path.join(self.tmp, 'run')])
        planned = planned_tests(args)
        holder = {}

        def make():
            holder['s'] = DrySession(args, fail, raise_, manual)
            return holder['s']
        t = PhaseTests(make, planned)
        t.open()
        return t, dict((tid, item) for tid, item in planned), holder

    def calls(self, holder, kind='phase'):
        return ['%s:%s' % (c[1].replace('fixture:', ''), c[2]) for c in holder['s'].calls if c[0] == kind]

    def test_a_late_phase_pulls_the_earlier_ones_in_as_setup(self):
        t, items, h = self.session_tests(['--vm', '9.x'])
        out = t.run_test(items['web.normal-removal'])
        self.assertEqual(out['status'], 'passed')
        self.assertEqual(out['setup'], ['web.normal-baseline', 'web.normal-install', 'web.normal-choose',
                                        'web.normal-window-chosen', 'web.normal-home-proof'])
        self.assertEqual(self.calls(h), ['web.normal:baseline', 'web.normal:install', 'web.normal:choose',
                                         'web.normal:window-chosen', 'web.normal:home-proof', 'web.normal:removal'])
        self.assertEqual(t.close(), [])
        self.assertEqual(self.calls(h)[-1], 'web.normal:cleanup')  # uninstall was not selected; cleanup still ran
        self.assertEqual([c[0] for c in h['s'].calls][-1], 'vm stop')

    def test_crash_windows_open_and_close_with_their_groups(self):
        t, items, h = self.session_tests(['--vm', '9.x'])
        for tid in ('web.normal-baseline', 'web.normal-install', 'web.normal-choose', 'web.normal-window-chosen'):
            t.run_test(items[tid])
        kinds = [(c[0], c[2]) for c in h['s'].calls if c[0] != 'use_launcher']
        # choose opens a window, window-chosen (no guard) runs in it and closes it
        self.assertEqual(kinds[kinds.index(('phase', 'choose')) - 1], ('compositor_snapshot', ''))
        self.assertEqual(kinds[kinds.index(('phase', 'window-chosen')) + 1], ('check_no_crash', 'end of step'))
        self.assertNotEqual(kinds[kinds.index(('phase', 'choose')) + 1][0], 'check_no_crash')
        t.close()

    def test_fail_manual_xfail_verdicts(self):
        t, items, h = self.session_tests(['--vm', '12.x', '--launchers', 'fixture:qml.normal,fixture:web.normal'],
                                 fail={'install'}, manual={'choose'})
        self.assertEqual(t.run_test(items['web.normal-baseline'])['status'], 'passed')
        self.assertEqual(t.run_test(items['qml.normal-qml-gate'])['status'], 'xfailed')
        failed = t.run_test(items['web.normal-install'])
        self.assertEqual(failed['status'], 'failed')
        self.assertIn('dry: injected failure in install', failed['message'])
        self.assertEqual(len(failed['screens']), 1)  # the FAIL screenshot of the step
        manual = t.run_test(items['web.normal-choose'])
        self.assertEqual((manual['status'], manual['counts'].get('MANUAL')), ('passed', 1))
        t.close()

    def test_a_later_launcher_that_raises_skips_only_its_own_phases(self):
        t, items, h = self.session_tests(['--vm', '10.x', '--launchers', 'fixture:web.normal,fixture:web.overlay'],
                                 raise_={'switch'})
        outs = {tid: t.run_test(item) for tid, item in items.items() if tid != 'web.normal-cleanup'}
        self.assertEqual(outs['web.overlay-switch']['status'], 'failed')
        self.assertIn('scenario ran to the end', outs['web.overlay-switch']['message'])
        for tid in ('web.overlay-window-chosen', 'web.overlay-home-proof', 'web.overlay-window-home'):
            self.assertEqual(outs[tid]['status'], 'skipped', tid)
            self.assertIn('switch of fixture:web.overlay raised', outs[tid]['message'])
        self.assertEqual(outs['web.normal-uninstall']['status'], 'passed')  # the first launcher goes on
        self.assertEqual(t.run_test(items['web.normal-cleanup'])['status'], 'passed')
        self.assertEqual(t.close(), [])

    def test_the_first_launcher_raising_ends_the_run_cleanup_still_runs(self):
        t, items, h = self.session_tests(['--vm', '9.x'], raise_={'choose'})
        outs = [(tid, t.run_test(item)) for tid, item in items.items() if tid != 'web.normal-cleanup']
        status = dict((tid, o['status']) for tid, o in outs)
        self.assertEqual(status['web.normal-choose'], 'failed')
        self.assertTrue(all(status[tid] == 'skipped' for tid in ('web.normal-home-proof', 'web.normal-uninstall')))
        self.assertIn('choose raised', dict(outs)['web.normal-removal']['message'])
        self.assertEqual(t.close(), [])  # the deferred 'scenario ran to the end' is the choose test's failure
        res = json.load(open(os.path.join(self.tmp, 'run', 'results.json')))
        self.assertEqual(res[-1]['name'], 'scenario ran to the end')  # after the cleanup's, as main() records it
        self.assertEqual(res[-2]['name'], 'dry: no test apps left installed')
        self.assertEqual(self.calls(h)[-1], 'web.normal:cleanup')
        self.assertEqual(h['s'].calls[-1][0], 'vm stop')

    def test_ctrl_c_in_a_phase_is_a_failure_not_a_pass_and_stops_the_run(self):
        t, items, h = self.session_tests(['--vm', '9.x'], raise_={'interrupt:choose'})
        self.assertEqual(t.run_test(items['web.normal-baseline'])['status'], 'passed')
        out = t.run_test(items['web.normal-choose'])
        self.assertEqual(out['status'], 'failed')
        self.assertIn('interrupted (Ctrl-C)', out['message'])
        self.assertTrue(t.interrupted)
        later = t.run_test(items['web.normal-home-proof'])
        self.assertEqual(later['status'], 'failed')
        t.close()
        res = json.load(open(os.path.join(self.tmp, 'run', 'results.json')))
        self.assertEqual(res[-1]['name'], 'scenario ran to the end')
        self.assertEqual(res[-1]['status'], 'FAIL')
        self.assertEqual(self.calls(h)[-1], 'web.normal:cleanup')

    def test_a_vm_that_does_not_start_fails_every_test_and_is_stopped(self):
        t, items, h = self.session_tests(['--vm', '9.x'], raise_={'vm-start'})
        for item in items.values():
            out = t.run_test(item)
            self.assertEqual(out['status'], 'failed')
            self.assertIn('runner setup', out['message'])
        t.close()
        self.assertEqual([c[0] for c in h['s'].calls], ['vm start', 'vm stop'])
        res = json.load(open(os.path.join(self.tmp, 'run', 'results.json')))
        self.assertEqual([(r['status'], r['name']) for r in res], [('FAIL', 'runner setup')])

    def test_a_cleanup_failure_at_the_end_is_the_teardown_verdict(self):
        t, items, h = self.session_tests(['--vm', '9.x'], fail={'cleanup'})
        t.run_test(items['web.normal-baseline'])
        fails = t.close()
        self.assertEqual([r['name'] for r in fails], ['dry: no test apps left installed'])
        self.assertEqual(t.close(), [])  # once

    def test_cli_run_is_the_same_plan_walk(self):
        """Scenario.run() is PlanRun.run_all() then finish(): the dry scenario gets the same calls."""
        args = parsed(['--vm', '10.x', '--launchers', 'fixture:web.normal,fixture:web.overlay',
                       '--outdir', os.path.join(self.tmp, 'cli')])
        s = DrySession(args)
        s.bring_up()
        run = hc_e2e.plan.PlanRun(s.scenario)
        run.start()
        run.run_all()
        run.finish()
        s.tear_down()
        s.rec.finish()
        walked = ['%s:%s' % (c[1].replace('fixture:', ''), c[2]) for c in s.calls if c[0] == 'phase']
        expected = ['%s:%s' % (['web.normal', 'web.overlay'][i], p) for i, p in s.scenario.plan]
        self.assertEqual(walked, expected)


class MatrixPytest(unittest.TestCase):
    def test_runner_command_pytest_passes_the_runner_flags(self):
        args = SimpleNamespace(pytest=True, hosts_file=None, hc_ipk='/m/ipk/hc.ipk', launcher=None,
                               launchers='fixture:web.normal,fixture:web.overlay', launcher_ipk=None,
                               update_from=None, old_ipk=None, update_on='w24g-ombre', api_check='smoke',
                               reboot_check='auto', uninstall_via='alert', launcher_update_check=False)
        run = {'vm': 'w24g-ombre', 'release': '9.x', 'host': 'pi', 'outdir': '/m/w24g-ombre'}
        argv = run_matrix.runner_command(args, ['--wait-popup', '30'])(run)
        self.assertEqual(argv[:len(run_matrix.PYTEST)], run_matrix.PYTEST)
        rest = argv[len(run_matrix.PYTEST):]
        self.assertEqual(rest[:6], ['--vm', 'w24g-ombre', '--host', 'pi', '--outdir', '/m/w24g-ombre'])
        self.assertIn('--hc-arg=--update-check', rest)
        self.assertEqual(rest[-2:], ['--hc-arg=--wait-popup', '--hc-arg=30'])
        cli = run_matrix.runner_command(SimpleNamespace(**dict(vars(args), pytest=False)), ['--wait-popup', '30'])(run)
        self.assertEqual([a[len('--hc-arg='):] if a.startswith('--hc-arg=') else a for a in rest],
                         cli[len(run_matrix.RUNNER):])


@unittest.skipUnless(HAVE_PYTEST, 'pytest and pytest-html: e2e/requirements.txt')
class DryDeviceRun(unittest.TestCase):
    """The pytest device run itself, --dry: test ids, outcomes, junit.xml and report.html."""

    def pytest(self, *extra):
        out = os.path.join(tempfile.mkdtemp(), 'run')
        self.addCleanup(shutil.rmtree, os.path.dirname(out), True)
        p = subprocess.run([sys.executable, '-I', '-m', 'pytest', os.path.join(HERE, 'device'), '-p', 'no:cacheprovider',
                            '-q', '--dry', '--outdir', out] + list(extra),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
        return p, out

    def junit(self, out):
        cases = {}
        for tc in ET.parse(os.path.join(out, 'junit.xml')).iter('testcase'):
            kind = next((c.tag for c in tc if c.tag in ('failure', 'skipped', 'error')), 'passed')
            cases[tc.get('name')] = (kind, {p.get('name'): p.get('value') for p in tc.iter('property')})
        return cases

    def test_release_tier_reports_and_incremental_skips(self):
        p, out = self.pytest('--vm', '12.x', '--tier', 'release', '--dry-fail=window-home', '--dry-raise=switch')
        self.assertEqual(p.returncode, 1, p.stdout)
        for name in ('junit.xml', 'report.html', 'run.log', 'results.json', 'dry-calls.json'):
            self.assertTrue(os.path.exists(os.path.join(out, name)), name)
        cases = self.junit(out)
        self.assertEqual(cases['test_w24g_spacebuns[web.normal-window-home]'][0], 'failure')
        self.assertEqual(cases['test_w24g_spacebuns[web.normal-window-home]'][1]['FAIL'], '1')
        self.assertEqual(cases['test_w24g_spacebuns[web.overlay-switch]'][0], 'failure')
        self.assertEqual(cases['test_w24g_spacebuns[web.overlay-reboot]'][0], 'skipped')
        self.assertEqual(cases['test_w24g_spacebuns[web.normal-uninstall]'][0], 'passed')
        self.assertEqual(cases['test_w24g_spacebuns[web.normal-cleanup]'][0], 'passed')
        html = open(os.path.join(out, 'report.html')).read()
        self.assertIn('test_w24g_spacebuns[web.overlay-switch]', html)
        self.assertGreaterEqual(html.count('data:image/png;base64'), 2)  # the failing phases' screenshots
        calls = json.load(open(os.path.join(out, 'dry-calls.json')))
        self.assertEqual((calls[0][0], calls[-1][0]), ('vm start', 'vm stop'))

    def test_ctrl_c_is_reported_as_failed_not_passed(self):
        p, out = self.pytest('--vm', '9.x', '--dry-raise=interrupt:choose')
        self.assertEqual(p.returncode, 2, p.stdout)  # pytest's "interrupted" status, never 0
        self.assertIn('1 failed', p.stdout)
        self.assertIn('INTERRUPTED', p.stdout)
        cases = self.junit(out)
        self.assertEqual(cases['test_w24g_ombre[web.normal-choose]'][0], 'failure')
        self.assertNotIn('test_w24g_ombre[web.normal-home-proof]', cases)  # the session stopped

    def test_selection_pulls_setup_and_still_cleans_up(self):
        p, out = self.pytest('--vm', '10.x', '--tier', 'release', '-k', 'web.overlay and reboot')
        self.assertEqual(p.returncode, 0, p.stdout)
        cases = self.junit(out)
        self.assertEqual(list(cases), ['test_w22o_ponytail_us[web.overlay-reboot]'])
        props = cases['test_w22o_ponytail_us[web.overlay-reboot]'][1]
        self.assertTrue(props['setup'].startswith('web.normal-baseline, web.normal-install'))
        self.assertIn('web.overlay-home-proof', props['setup'])
        phases = [c[2] for c in json.load(open(os.path.join(out, 'dry-calls.json'))) if c[0] == 'phase']
        self.assertEqual(phases[-2:], ['reboot', 'cleanup'])
        self.assertNotIn('uninstall', phases)

    def test_without_a_vm_no_device_test_exists(self):
        p = subprocess.run([sys.executable, '-I', '-m', 'pytest', os.path.join(HERE, 'device'), '-p', 'no:cacheprovider',
                            '-q'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
        self.assertEqual(p.returncode, 5, p.stdout)  # no tests collected


if __name__ == '__main__':
    unittest.main()
