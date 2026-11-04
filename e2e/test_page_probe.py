#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Offline tests of hc_e2e.page_probe: the verdict on what the launcher page recorded from its own
listRunning, getForegroundApp and getAppLifeEvents subscriptions."""
import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hc_e2e import page_probe  # noqa: E402

APP = 'org.webosbrew.hcfixture.disposable'


def good():
    def r(**kw):
        return dict({'t': 1, 'apiVersion': 3, 'subscribed': True}, **kw)
    return {'hasClient': True, 'subs': {
        'listRunning': {'error': None, 'replies': [r(ids=['a']), r(ids=['a', APP]), r(ids=['a'])]},
        'getForegroundApp': {'error': None, 'replies': [r(appId='a'), r(appId=APP), r(appId='a')]},
        'getAppLifeEvents': {'error': None, 'replies': [
            r(), r(event='launch', appId=APP), r(event='foreground', appId=APP), r(event='close', appId=APP)]}}}


class PageProbeTest(unittest.TestCase):
    def test_good(self):
        self.assertEqual(page_probe.probe_problems(good(), APP), [])
        for m in page_probe.METHODS:
            self.assertTrue(page_probe.acked(good(), m))
        self.assertIn('listRunning: 3 replies', page_probe.probe_summary(good(), APP))

    def test_parse(self):
        d = good()
        self.assertEqual(page_probe.parse_probe(json.dumps(d)), d)
        self.assertIsNone(page_probe.parse_probe('null'))
        self.assertIsNone(page_probe.parse_probe('not json'))
        self.assertIsNone(page_probe.parse_probe(None))
        self.assertIn('recorded nothing', page_probe.probe_problems(None, APP)[0])

    def test_no_client_or_error(self):
        d = good()
        d['hasClient'] = False
        self.assertTrue(any('no window.HomeChooser' in p for p in page_probe.probe_problems(d, APP)))
        d = good()
        d['subs']['getForegroundApp']['error'] = {'source': 'platform', 'errorText': 'Denied method call'}
        self.assertTrue(any('ended with' in p for p in page_probe.probe_problems(d, APP)))
        d = good()
        del d['subs']['listRunning']
        self.assertTrue(any('never subscribed' in p for p in page_probe.probe_problems(d, APP)))

    def test_no_ack(self):
        d = good()
        d['subs']['listRunning']['replies'] = []
        self.assertFalse(page_probe.acked(d, 'listRunning'))
        self.assertTrue(any('listRunning: no first reply' in p for p in page_probe.probe_problems(d, APP)))
        d = good()
        d['subs']['getAppLifeEvents']['replies'][0] = {'apiVersion': 3, 'subscribed': True, 'event': 'launch', 'appId': APP}
        self.assertFalse(page_probe.acked(d, 'getAppLifeEvents'))
        d = good()
        d['subs']['getAppLifeEvents']['replies'][0]['apiVersion'] = 2
        self.assertTrue(any('below 3' in p for p in page_probe.probe_problems(d, APP)))

    def test_missing_launch_or_close(self):
        d = good()
        d['subs']['listRunning']['replies'] = d['subs']['listRunning']['replies'][:1]
        self.assertTrue(any('no reply listed' in p for p in page_probe.probe_problems(d, APP)))
        d = good()
        d['subs']['listRunning']['replies'] = d['subs']['listRunning']['replies'][:2]
        self.assertTrue(any('dropped' in p for p in page_probe.probe_problems(d, APP)))
        d = good()
        d['subs']['getForegroundApp']['replies'] = d['subs']['getForegroundApp']['replies'][:2]
        self.assertTrue(any('no change after' in p for p in page_probe.probe_problems(d, APP)))
        d = good()
        d['subs']['getForegroundApp']['replies'] = d['subs']['getForegroundApp']['replies'][:1]
        self.assertTrue(any('never named' in p for p in page_probe.probe_problems(d, APP)))
        d = good()
        d['subs']['getAppLifeEvents']['replies'] = d['subs']['getAppLifeEvents']['replies'][:3]
        self.assertTrue(any('no close or stop' in p for p in page_probe.probe_problems(d, APP)))
        d = good()
        d['subs']['getAppLifeEvents']['replies'] = d['subs']['getAppLifeEvents']['replies'][:1]
        self.assertTrue(any('no launch event' in p for p in page_probe.probe_problems(d, APP)))

    def test_other_app_is_not_mine(self):
        d = copy.deepcopy(good())
        for r in d['subs']['getAppLifeEvents']['replies']:
            if r.get('appId'):
                r['appId'] = 'other'
        self.assertTrue(any('no launch event' in p for p in page_probe.probe_problems(d, APP)))


if __name__ == '__main__':
    unittest.main()
