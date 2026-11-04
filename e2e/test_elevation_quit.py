# SPDX-License-Identifier: Apache-2.0
"""RootShellElevation.prepare: quit and status go out as the app, a non-root instance must exit first."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hc_e2e import device, timing  # noqa: E402
from hc_e2e.constants import APP_ID, SVC_ID  # noqa: E402


class Rec:
    def __init__(self):
        self.notes, self.checks = [], []

    def note(self, what, evidence=''):
        self.notes.append((what, evidence))

    def check(self, name, ok, evidence='', expected=None):
        self.checks.append((name, ok, evidence))


class FakeDev:
    """procs: successive service_procs() answers (the last repeats); refuse: quit leaves it alive."""
    def __init__(self, procs):
        self.procs = list(procs)
        self.calls = []

    def sh(self, cmd, timeout=60):
        self.calls.append(('sh', cmd))
        return 0, 'rc=0'

    def service_procs(self):
        return self.procs.pop(0) if len(self.procs) > 1 else self.procs[0]

    def luna_as(self, app, uri, payload='{}', timeout=60):
        self.calls.append(('luna_as', app, uri))
        return 0, '{"returnValue":true}', {'returnValue': True}

    def luna(self, uri, payload='{}'):
        self.calls.append(('luna', uri))
        return '{}'


class Quit(unittest.TestCase):
    def setUp(self):
        self._sleep = timing.sleep
        timing.sleep = lambda v: None
        self._t = device.RootShellElevation.QUIT_WAIT
        device.RootShellElevation.QUIT_WAIT = 0.05

    def tearDown(self):
        timing.sleep = self._sleep
        device.RootShellElevation.QUIT_WAIT = self._t

    def run_prepare(self, procs):
        dev, rec = FakeDev(procs), Rec()
        device.RootShellElevation().prepare(dev, rec)
        return dev, rec

    def luna_calls(self, dev):
        return [c for c in dev.calls if c[0] in ('luna', 'luna_as')]

    def test_alive_nonroot_quit_as_app_then_fresh_instance(self):
        dev, rec = self.run_prepare([[('prisoner', '123')], [('prisoner', '123')], []])
        self.assertEqual(self.luna_calls(dev), [
            ('luna_as', APP_ID, 'luna://%s/quit' % SVC_ID),
            ('luna_as', APP_ID, 'luna://%s/status' % SVC_ID)])
        self.assertEqual(rec.checks, [])
        self.assertIn(('service after quit', '(gone)'), rec.notes)

    def test_quit_refused_fails_clearly(self):
        dev, rec = self.run_prepare([[('prisoner', '123')]])
        self.assertEqual(len(rec.checks), 1)
        name, ok, ev = rec.checks[0]
        self.assertFalse(ok)
        self.assertIn('pid 123', ev)
        self.assertIn('uid prisoner', ev)
        self.assertEqual([c[2] for c in self.luna_calls(dev)], ['luna://%s/quit' % SVC_ID])

    def test_no_instance_only_status(self):
        dev, rec = self.run_prepare([[]])
        self.assertEqual(self.luna_calls(dev), [('luna_as', APP_ID, 'luna://%s/status' % SVC_ID)])
        self.assertEqual(rec.checks, [])


if __name__ == '__main__':
    unittest.main()
