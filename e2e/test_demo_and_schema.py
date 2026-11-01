#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Offline tests of fixture:web.demo (the demo's grid order the runner reproduces, step 3d) and of the
--api-check validation of every reply against api/launcher-v1.openrpc.json.  No device, no VM.

    python3 -I -m unittest discover -s e2e -p 'test_*.py'
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import homechooser_e2e as e2e  # noqa: E402

REPO = os.path.dirname(HERE)
SPEC = os.path.join(REPO, 'api', 'launcher-v1.openrpc.json')
CASES = os.path.join(REPO, 'service', 'test', 'launcher-contract-cases.json')
LAUNCHER_JS = os.path.join(REPO, 'examples', 'web-launcher', 'launcher.js')

APPS = [
    {'id': 'org.example.zeta', 'title': 'zeta'},
    {'id': 'org.example.alpha', 'title': 'Alpha'},
    {'id': 'org.example.self', 'title': 'Aardvark launcher'},
    {'id': 'org.example.hidden', 'title': 'AAA hidden', 'visible': False},
    {'id': 'org.example.shown', 'title': 'Beta', 'visible': True},
    {'id': 'org.example.b2', 'title': 'beta'},
    {'id': 'org.example.notitle'},
    {'id': 'org.example.upper', 'title': 'Éclair'},
    {'id': 'org.example.under', 'title': 'a_b'},
    {'id': 'org.example.zed', 'title': 'aZb'},
    {'id': 'org.example.astral', 'title': '\U0001F600 smile'},
    {'id': 'org.example.cjk', 'title': 'Ａ fullwidth'},
    {'title': 'no id'},
]


class DemoOrder(unittest.TestCase):
    def test_order_hides_self_and_invisible_and_ignores_ascii_case(self):
        order = [a['id'] for a in e2e.demo_order(APPS, 'org.example.self')]
        self.assertNotIn('org.example.self', order)
        self.assertNotIn('org.example.hidden', order)
        self.assertEqual(order[0], 'org.example.under')
        self.assertLess(order.index('org.example.alpha'), order.index('org.example.zed'))  # 'aZb' < 'a_b' only case-sensitively
        self.assertLess(order.index('org.example.alpha'), order.index('org.example.b2'))
        # 'Beta' and 'beta' tie on the title, so the id decides.
        self.assertLess(order.index('org.example.b2'), order.index('org.example.shown'))
        # A missing title sorts by the id; JavaScript compares UTF-16 code units, so U+FF21 sorts after
        # the surrogate pair of U+1F600.
        self.assertLess(order.index('org.example.astral'), order.index('org.example.cjk'))
        self.assertEqual(len(order), 10)

    def test_focus_from_a_list_reply(self):
        reply = {'returnValue': True, 'apiVersion': 1, 'apps': APPS}
        self.assertEqual(e2e.demo_focus(reply, 'org.example.self'), 'org.example.under')
        self.assertIsNone(e2e.demo_focus({'returnValue': False, 'errorCode': 'notRoot'}, 'x'))
        self.assertIsNone(e2e.demo_focus({'returnValue': True, 'apps': [{'id': 'x'}]}, 'x'))
        self.assertIsNone(e2e.demo_focus(None, 'x'))

    def test_the_demo_itself_sorts_the_same(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node is not installed')
        script = ('const o = require(process.argv[1]).orderApps(JSON.parse(process.argv[2]), process.argv[3]);'
                  'process.stdout.write(JSON.stringify(o.map(a => a.id)));')
        out = subprocess.run([node, '-e', script, LAUNCHER_JS, json.dumps(APPS), 'org.example.self'],
                             capture_output=True, text=True, timeout=30, check=True).stdout
        self.assertEqual(json.loads(out), [a['id'] for a in e2e.demo_order(APPS, 'org.example.self')])


class DemoLauncher(unittest.TestCase):
    def test_demo_spec_uses_the_plain_build_also_for_api_check(self):
        tmp = tempfile.mkdtemp()
        try:
            ipk = os.path.join(tmp, e2e.DEMO_ID + '_1.0.0_all.ipk')
            open(ipk, 'w').close()
            with patch.object(e2e, 'FIXTURE_DIR', tmp), \
                    patch.object(e2e, 'ipk_appinfo', return_value={'id': e2e.DEMO_ID, 'title': 'HC Fixture web demo'}), \
                    patch.object(e2e, 'api_fixture_ipks', side_effect=AssertionError('not the API build')):
                for api in (False, True):
                    L = e2e.make_launcher('fixture:web.demo', api=api)
                    self.assertEqual((L.id, L.ipk, L.is_fixture, L.is_demo, L.fixture, L.overlay),
                                     ('org.webosbrew.hcfixture.web.demo', ipk, True, True, ('web', 'demo'), False))
        finally:
            shutil.rmtree(tmp)
        with self.assertRaises(SystemExit):
            e2e.make_launcher('fixture:native.demo')
        with self.assertRaises(SystemExit):
            e2e.make_launcher('fixture:web.bogus')
        self.assertNotIn('fixture:web.demo', e2e.fixture_specs())  # all-fixtures stays the six inert ones

    def scenario(self, apps, running):
        s = e2e.Scenario.__new__(e2e.Scenario)
        s.rec = SimpleNamespace(begin=Mock(), say=Mock(), note=Mock(), checks=[])
        s.rec.check = lambda name, ok, evidence='', expected=None: s.rec.checks.append((name, bool(ok))) or bool(ok)
        s.launcher = e2e.DEMO_ID
        s.L = SimpleNamespace(is_demo=True)
        s.a = SimpleNamespace(api_check=False)
        s.dev = SimpleNamespace(foreground=Mock(return_value=(e2e.DEMO_ID, '{}')), close=Mock())
        s.api = Mock(return_value=({'returnValue': True, 'apiVersion': 1, 'apps': apps}, '{}'))
        s.running_ids = Mock(side_effect=lambda: list(running))
        s.foreign_popup = Mock(return_value=None)
        s.life_begin, s.life_end, s.shot = Mock(), Mock(), Mock()
        s.messages_mark = Mock(return_value='2026-11-01T00:00:00')
        s.press = Mock(return_value=(0, ''))
        s.launch_proof = Mock()
        return s

    def test_step_demo_presses_ok_and_proves_the_first_app(self):
        apps = [{'id': 'org.example.b', 'title': 'B'}, {'id': 'org.example.a', 'title': 'a'},
                {'id': e2e.DEMO_ID, 'title': 'HC Fixture web demo'}]
        s = self.scenario(apps, ['org.example.a'])
        with patch.object(e2e, 'sleep'):
            s.step_demo()
        s.press.assert_called_once_with('OK')
        s.launch_proof.assert_called_once_with('3d-demo-ok', 'org.example.a', '2026-11-01T00:00:00')
        # closed before OK (it was running) and after the proof
        self.assertEqual([c.args[0] for c in s.dev.close.call_args_list], ['org.example.a', 'org.example.a'])
        self.assertTrue(all(ok for _, ok in s.rec.checks), s.rec.checks)

    def test_step_demo_stops_without_an_app(self):
        s = self.scenario([{'id': e2e.DEMO_ID}], [])
        with patch.object(e2e, 'sleep'):
            s.step_demo()
        s.press.assert_not_called()
        self.assertEqual(s.rec.checks[-1][1], False)


def spec_records():
    spec = json.load(open(SPEC))
    cases = json.load(open(CASES))
    out = [(m['name'], ex['name'], ex['result']['value']) for m in spec['methods'] for ex in m['examples']]
    out += [(c['method'], c['name'], c['result']) for c in cases['cases']]
    return out


class SpecSchemaValidation(unittest.TestCase):
    def test_every_example_and_contract_case_validates(self):
        records = spec_records()
        self.assertGreater(len(records), 40)
        for method, name, reply in records:
            with self.subTest(method=method, name=name):
                self.assertEqual(e2e.spec_reply_problems(method, reply), [])

    def test_bad_replies_are_reported(self):
        info = next(r for m, _, r in spec_records() if m == 'getInfo' and r['returnValue'])
        self.assertTrue(e2e.spec_reply_problems('getInfo', dict(info, root='yes')))
        self.assertTrue(e2e.spec_reply_problems('getInfo', {k: v for k, v in info.items() if k != 'permitted'}))
        self.assertTrue(e2e.spec_reply_problems('getInfo', dict(info, apiVersion=2)))
        self.assertEqual(e2e.spec_reply_problems('getInfo', dict(info, futureField=1)), [])  # objects are open
        refusal = {'returnValue': False, 'apiVersion': 1, 'errorCode': 'notHome', 'errorText': 'x'}
        self.assertEqual(e2e.spec_reply_problems('closeApp', refusal), [])
        self.assertTrue(e2e.spec_reply_problems('listApps', refusal))  # listApps never sends notHome
        icon = {'returnValue': True, 'apiVersion': 1, 'mimeType': 'image/bmp', 'data': 'AAAA', 'byteLength': 3}
        self.assertTrue(e2e.spec_reply_problems('getAppIcon', icon))
        apps = {'returnValue': True, 'apiVersion': 1, 'apps': [{'id': 'bad id!'}]}
        self.assertTrue(e2e.spec_reply_problems('listApps', apps))

    def test_platform_replies_are_outside_the_schemas(self):
        self.assertIsNone(e2e.spec_reply_problems('getInfo', {'returnValue': False, 'errorCode': -1,
                                                              'errorText': 'Denied method call'}))
        self.assertIsNone(e2e.spec_reply_problems('getInfo', None))

    def test_api_records_a_check_per_reply_during_api_check(self):
        s = e2e.Scenario.__new__(e2e.Scenario)
        s.rec = SimpleNamespace(note=Mock(), checks=[])
        s.rec.check = lambda name, ok, evidence='', expected=None: s.rec.checks.append((name, bool(ok), evidence))
        s.launcher = 'org.example.launcher'
        good = copy.deepcopy(next(r for m, _, r in spec_records() if m == 'listApps' and r['returnValue']))
        bad = dict(good, apps='none')
        denied = {'returnValue': False, 'errorCode': -1, 'errorText': 'Denied method call'}
        s.dev = SimpleNamespace(luna_as=Mock(side_effect=[(0, json.dumps(r), r) for r in (good, bad, denied, good)]))
        s.a = SimpleNamespace(api_check=True)
        for _ in range(3):
            s.api('listApps')
        self.assertEqual([ok for _, ok, _ in s.rec.checks], [True, False])
        self.assertIn('api/launcher-v1.openrpc.json', s.rec.checks[0][0])
        s.rec.note.assert_called_once()
        s.a.api_check = False
        s.api('listApps')
        self.assertEqual(len(s.rec.checks), 2)


if __name__ == '__main__':
    unittest.main()
