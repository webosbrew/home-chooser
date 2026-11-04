#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Offline tests of app/scripts/release-manifest.py."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
SCRIPT = os.path.join(ROOT, 'app', 'scripts', 'release-manifest.py')
spec = importlib.util.spec_from_file_location('release_manifest', SCRIPT)
rm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rm)

KEYS = {'id', 'version', 'type', 'title', 'appDescription', 'iconUri', 'sourceUrl',
        'rootRequired', 'ipkUrl', 'ipkHash'}


class ReleaseManifest(unittest.TestCase):
    def test_script_writes_manifest_next_to_ipk(self):
        appinfo = rm.load(os.path.join(ROOT, 'app', 'appinfo.json'))
        with tempfile.TemporaryDirectory() as tmp:
            ipk = os.path.join(tmp, '%s_%s_arm.ipk' % (appinfo['id'], appinfo['version']))
            with open(ipk, 'wb') as f:
                f.write(b'not really an ipk')
            out = subprocess.run([sys.executable, '-I', SCRIPT, ipk], check=True,
                                 capture_output=True, text=True).stdout.strip()
            self.assertEqual(out, os.path.join(tmp, appinfo['id'] + '.manifest.json'))
            with open(out, encoding='utf-8') as f:
                m = json.load(f)
        self.assertEqual(set(m), KEYS)
        self.assertEqual(m['ipkUrl'], os.path.basename(ipk))
        self.assertNotIn('/', m['ipkUrl'])
        self.assertEqual(m['ipkHash'], {'sha256': hashlib.sha256(b'not really an ipk').hexdigest()})
        self.assertIs(m['rootRequired'], True)
        self.assertEqual(m['version'], rm.load(os.path.join(ROOT, 'app', 'package.json'))['version'])
        self.assertEqual(m['title'], appinfo['title'])
        self.assertTrue(m['appDescription'] and '\n' not in m['appDescription'])

    def test_version_mismatch_and_wrong_ipk_name_fail(self):
        appinfo = {'id': 'a.b', 'version': '1.0.0', 'type': 'web', 'title': 'T'}
        with tempfile.TemporaryDirectory() as tmp:
            ipk = os.path.join(tmp, 'a.b_1.0.0_arm.ipk')
            open(ipk, 'wb').close()
            with self.assertRaises(SystemExit):
                rm.build(ipk, appinfo, {'version': '1.0.1'}, 'd')
            other = os.path.join(tmp, 'a.b_2.0.0_arm.ipk')
            open(other, 'wb').close()
            with self.assertRaises(SystemExit):
                rm.build(other, appinfo, {'version': '1.0.0'}, 'd')


if __name__ == '__main__':
    unittest.main()
