#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Offline tests of hc_e2e.page_probe: the verdict on what the launcher page recorded from its own
listRunning, getForegroundApp and getAppLifeEvents subscriptions."""
import copy
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hc_e2e import launchers, page_probe  # noqa: E402

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


def fake_ipk(path, files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as t:
        for n in files:
            ti = tarfile.TarInfo('./usr/palm/applications/x/' + n)
            ti.size = 1
            t.addfile(ti, io.BytesIO(b'x'))
    d = buf.getvalue()
    with open(path, 'wb') as f:
        f.write(b'!<arch>\n' + b'data.tar.gz/'.ljust(16) + b'0'.ljust(12) + b'0'.ljust(6) + b'0'.ljust(6)
                + b'100644'.ljust(8) + str(len(d)).encode().ljust(10) + b'`\n' + d + (b'\n' if len(d) & 1 else b''))


class ProbeIpkTest(unittest.TestCase):
    def test_stale_api_build_without_probe_is_not_current(self):
        with tempfile.TemporaryDirectory() as d:
            old, new = os.path.join(d, 'old.ipk'), os.path.join(d, 'new.ipk')
            fake_ipk(old, ['index.html', 'appinfo.json'])
            fake_ipk(new, ['index.html', 'appinfo.json', 'homechooser.js', 'probe.js'])
            self.assertFalse(launchers.has_probe_page(old))
            self.assertTrue(launchers.has_probe_page(new))
            self.assertFalse(launchers.has_probe_page(os.path.join(d, 'missing.ipk')))


class FixtureCacheStampTest(unittest.TestCase):
    """The fixture caches are rebuilt when the stamp of their inputs is missing or differs, and only then."""

    def api_dir(self, d):
        ids = [launchers.FIXTURE_ID % ('web', w) for w in launchers.FIXTURE_WINDOWS] + [launchers.DISPOSABLE_ID]
        for i in ids:
            open(os.path.join(d, i + '_1.0.0_all.ipk'), 'w').close()
        return ids

    def run_cache(self, d, stamp_text):
        info = {'requiredPermissions': [launchers.API_GROUP], 'largeIcon': 'oversize.png'}
        built = []

        def build(argv, **kw):
            built.append(argv)
            self.api_dir(d)
            return SimpleNamespace(returncode=0, stdout=b'')
        if stamp_text is not None:
            with open(os.path.join(d, launchers.STAMP_NAME), 'w') as f:
                f.write(stamp_text)
        with patch.object(launchers, 'API_FIXTURE_DIR', d), patch.object(launchers, 'ipk_appinfo', return_value=info), \
                patch.object(launchers, 'has_probe_page', return_value=True), \
                patch.object(launchers.subprocess, 'run', side_effect=build):
            have = launchers.api_fixture_ipks(lambda t: None)
        return have, built

    def test_matching_stamp_reuses_the_cache(self):
        with tempfile.TemporaryDirectory() as d:
            self.api_dir(d)
            have, built = self.run_cache(d, launchers.api_stamp())
            self.assertEqual(built, [])
            self.assertEqual(len(have), 3)

    def test_stale_or_missing_stamp_rebuilds_and_stamps(self):
        for stamp in ('0' * 64, None):
            with tempfile.TemporaryDirectory() as d:
                self.api_dir(d)
                have, built = self.run_cache(d, stamp)
                self.assertEqual(len(built), 2, stamp)  # web, disposable
                self.assertTrue(launchers.cache_current(d, launchers.api_stamp()))
                _, built = self.run_cache(d, launchers.api_stamp())
                self.assertEqual(built, [])

    def test_stamp_follows_the_inputs(self):
        with tempfile.TemporaryDirectory() as repo:
            os.makedirs(os.path.join(repo, 'src'))
            f = os.path.join(repo, 'src', 'a.js')
            open(f, 'w').write('one')
            open(os.path.join(repo, 'src', 'README.md'), 'w').write('docs')
            with patch.object(launchers, 'REPO', repo):
                a = launchers.inputs_stamp(('src',))
                open(os.path.join(repo, 'src', 'README.md'), 'w').write('other docs')
                self.assertEqual(launchers.inputs_stamp(('src',)), a)  # *.md is not packaged
                open(f, 'w').write('two')
                self.assertNotEqual(launchers.inputs_stamp(('src',)), a)
                self.assertNotEqual(launchers.inputs_stamp(('src',), 'x'), launchers.inputs_stamp(('src',)))

    def test_api_stamp_covers_what_the_builders_copy(self):
        for rel in launchers._API_INPUTS + launchers._PLAIN_INPUTS:
            self.assertTrue(os.path.exists(os.path.join(launchers.REPO, rel)), rel)

    def test_plain_cache_rebuilds_on_stale_stamp_only(self):
        with tempfile.TemporaryDirectory() as d:
            ident = launchers.FIXTURE_ID % ('web', 'normal')
            open(os.path.join(d, ident + '_1.0.0_all.ipk'), 'w').close()
            with patch.object(launchers, 'FIXTURE_DIR', d), patch.object(launchers, 'ipk_appinfo', return_value={}), \
                    patch.object(launchers.timing, 'run', return_value=(0, 'ok')) as run:
                launchers.make_launcher('fixture:web.normal', announce=lambda t: None)
                self.assertEqual(run.call_count, 1)  # no stamp: built, then stamped
                launchers.make_launcher('fixture:web.normal', announce=lambda t: None)
                self.assertEqual(run.call_count, 1)
                open(os.path.join(d, launchers.STAMP_NAME), 'w').write('stale\n')
                launchers.make_launcher('fixture:web.normal', announce=lambda t: None)
                self.assertEqual(run.call_count, 2)


if __name__ == '__main__':
    unittest.main()
