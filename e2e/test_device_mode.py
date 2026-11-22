#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Offline tests of --device mode, where the run has no Beanjar host (Scenario.host is None, issue #7): the steps
that need the host (screen reading, Beanjar remote keys) say so instead of raising AttributeError
("'NoneType' object has no attribute 'display_beanjar'"), and the rest of the contract goes on."""
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hc_e2e.scenario  # noqa: E402
import hc_e2e.steps.api  # noqa: E402
import hc_e2e.timing  # noqa: E402
from hc_e2e.steps.screen import NeedsBeanjarHost  # noqa: E402


def device_scenario(input_name='ares'):
    s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
    s.host, s.vm, s.rec, s.dev = None, 'device', Mock(), Mock()
    s.a = SimpleNamespace(input=input_name, input_mode='keys')
    s.insp = Mock(last_error=None)
    return s


class DeviceModeWithoutHost(unittest.TestCase):
    def test_elements_names_the_missing_host(self):
        # before the guard: AttributeError: 'NoneType' object has no attribute 'display_beanjar'
        s = device_scenario()
        with self.assertRaises(NeedsBeanjarHost) as cm:
            s.elements('uninstall-result')
        self.assertIn('--device mode', str(cm.exception))
        self.assertIsInstance(cm.exception, RuntimeError)  # the alert steps already turn this into MANUAL

    def test_beanjar_keys_need_the_host_but_ares_keys_do_not(self):
        s = device_scenario('beanjar')
        with self.assertRaises(NeedsBeanjarHost):
            s.press('OK')
        s = device_scenario('ares')
        s.dev.key.return_value = (0, '')
        with patch.object(hc_e2e.timing, 'sleep'):
            s.press('OK')
        s.dev.key.assert_called_once_with('ENTER')

    def test_toast_reads_the_dom_only_without_a_host(self):
        s = device_scenario()
        s.ui = Mock(return_value={'toast': None})
        with patch.object(hc_e2e.timing, 'sleep'):
            text, source = s.see_toast('already your Home', 3)
        self.assertIsNone(text)
        self.assertTrue(source.startswith('none'))
        self.assertNotIn('OCR', source)
        s.ui = Mock(return_value={'toast': 'X is already your Home.'})
        self.assertEqual(s.see_toast('already your Home', 3), ('X is already your Home.', 'DOM'))

    def test_a_declined_alert_proof_is_manual_not_a_crash(self):
        s = device_scenario()
        s.use_click = lambda: False
        s.shot = lambda label: None
        s.await_service_proof = lambda proof, **kw: ('raw', True)
        s.rec = Mock()
        ok = s.press_alert('Uninstall X?', 'Uninstall', 'Cancel', 'alert', lambda: ('raw', True))
        self.assertFalse(ok)
        self.assertIn('MANUAL fallback', json.dumps([c.args for c in s.rec.note.call_args_list]))


class FakeDevice:
    """applicationManager registers each app after `late` further getAppInfo calls (None: never)."""

    def __init__(self, late):
        self.late, self.calls = late, {}

    def app_installed(self, appid):
        self.calls[appid] = self.calls.get(appid, 0) + 1
        return self.late is not None and self.calls[appid] > self.late.get(appid, 0)


class WaitRegistered(unittest.TestCase):
    def scenario(self, late):
        from hc_e2e.device_api import DeviceApiScenario
        s = DeviceApiScenario.__new__(DeviceApiScenario)
        s.dev, s.rec = FakeDevice(late), Mock()
        return s

    def test_waits_until_the_launcher_is_known_then_passes(self):
        s = self.scenario({'org.x.launcher': 3})
        with patch.object(hc_e2e.timing, 'sleep'):
            self.assertTrue(s.wait_registered(['org.x.hc', 'org.x.launcher'], timeout=30))
        self.assertGreater(s.dev.calls['org.x.launcher'], 3)
        name, ok = s.rec.check.call_args.args[:2]
        self.assertTrue(ok)
        self.assertNotIn('not registered', name)

    def test_timeout_is_its_own_failure(self):
        s = self.scenario(None)
        with patch.object(hc_e2e.timing, 'sleep'):
            self.assertFalse(s.wait_registered(['org.x.launcher'], timeout=0))
        name, ok, evidence = s.rec.check.call_args.args[:3]
        self.assertFalse(ok)
        self.assertEqual(name, 'not registered after install: org.x.launcher')
        self.assertIn('does not know org.x.launcher', evidence)

    def test_the_wait_is_scaled_by_the_slowdown(self):
        s = self.scenario(None)
        seen = []
        s.wait = lambda cond, timeout, every=3: seen.append(timeout) or None
        self.assertFalse(s.wait_registered(['org.x.launcher']))
        self.assertEqual(seen, [s.REGISTER_WAIT])  # self.wait applies timing.seconds() to it


if __name__ == '__main__':
    unittest.main()
