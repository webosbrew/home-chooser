#!/usr/bin/env python3
"""Offline tests of the runner's pure logic: what it expects per release after 144ef21/34a4ff9 (no
compositor restart on a running TV, the stock id in the record on 6-9), the texts it asserts, the
launcher-API reply checks of --api-check, and the restart/crash detection.  No device, no VM.

    python3 -I -m unittest discover -s e2e -p 'test_*.py'
"""
import contextlib
import gzip
import io
import json
import os
import re
import shlex
import shutil
import sys
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import homechooser_e2e as e2e  # noqa: E402
import run_matrix  # noqa: E402

REPO = os.path.dirname(HERE)
FULL, HOME = e2e.FULLHOME, e2e.STOCK


class LogMark(unittest.TestCase):
    def test_rotated_messages_preserve_alert_and_crash_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            # webOS 6's BusyBox 1.29.3 rejects sort -V (observed on w21a-kisscurl).
            # Exercise that constraint as well as numeric archive ordering.
            bindir = os.path.join(root, 'bin')
            os.mkdir(bindir)
            sort = os.path.join(bindir, 'sort')
            with open(sort, 'w') as script:
                script.write('#!/bin/sh\ncase "$*" in *V*) exit 2;; esac\nexec %s "$@"\n'
                             % shlex.quote(shutil.which('sort')))
            os.chmod(sort, 0o755)
            env = dict(os.environ, PATH=bindir + os.pathsep + os.environ['PATH'])
            path = os.path.join(root, 'messages')
            for suffix, content in [('.12.gz', 'a old\n'),
                                    ('.2.gz', 'b REBOOT_ASK createAlert\n'),
                                    ('.0.gz', 'c crashd CRASHED\n')]:
                with gzip.open(path + suffix, 'wt') as log:
                    log.write(content)
            with open(path, 'w') as log:
                log.write('d current\n')
            cmd = e2e.messages_command(path) + " | awk '$1 >= \"b\"'"
            result = subprocess.run(['sh', '-c', cmd], capture_output=True, text=True, check=True, env=env)
            self.assertEqual(result.stdout, 'b REBOOT_ASK createAlert\nc crashd CRASHED\nd current\n')

    def test_messages_without_archives(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, 'messages')
            with open(path, 'w') as log:
                log.write('only current\n')
            result = subprocess.run(['sh', '-c', e2e.messages_command(path)],
                                    capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout, 'only current\n')
            self.assertEqual(result.stderr, '')

    def test_only_lines_after_the_mark(self):
        log = ['a closeByAppId x', 'b', 'c']
        self.assertEqual(e2e.lines_after(log, 2), ['c'])
        self.assertEqual(e2e.lines_after(log, 3), [])
        self.assertEqual(e2e.lines_after(log, 0), log)

    def test_a_log_shorter_than_the_mark_is_a_new_boot(self):
        self.assertEqual(e2e.lines_after(['x', 'y'], 50), ['x', 'y'])


class StockRecord(unittest.TestCase):
    def test_5x_is_unchanged_a_stock_launcher(self):
        for home in (FULL, HOME):
            self.assertTrue(e2e.stock_record_ok(5, home))
            self.assertTrue(e2e.stock_record_ok(5, home, FULL))  # the id is not pinned on 5.x
        for home in (None, '', 'null', 'org.webosbrew.dock'):
            self.assertFalse(e2e.stock_record_ok(5, home))

    def test_6_to_9_require_the_stock_id_not_null(self):
        for major in (6, 7, 8, 9):
            self.assertTrue(e2e.stock_record_ok(major, HOME, HOME))
            self.assertTrue(e2e.stock_record_ok(major, FULL, FULL))
            self.assertFalse(e2e.stock_record_ok(major, FULL, HOME), 'the resolved id, exactly')
            self.assertFalse(e2e.stock_record_ok(major, HOME, FULL))
            for home in (None, '', 'null', 'org.webosbrew.dock'):
                self.assertFalse(e2e.stock_record_ok(major, home, HOME), (major, home))
            # stock id not resolved: either stock launcher, never null
            self.assertTrue(e2e.stock_record_ok(major, HOME))
            self.assertFalse(e2e.stock_record_ok(major, None))

    def test_10_and_later_require_null_or_unset(self):
        for major in (10, 11, 12):
            for home in (None, '', 'null'):
                self.assertTrue(e2e.stock_record_ok(major, home), (major, home))
            for home in (HOME, FULL, 'org.webosbrew.dock'):
                self.assertFalse(e2e.stock_record_ok(major, home), (major, home))

    def test_stock_home_id_resolution(self):
        self.assertEqual(e2e.stock_home_id(9, True), FULL)
        self.assertEqual(e2e.stock_home_id(6, False), HOME)
        self.assertEqual(e2e.stock_home_id(5, True), FULL)
        self.assertIsNone(e2e.stock_home_id(10, True))
        self.assertIsNone(e2e.stock_home_id(12, False))

    def test_baseline_on_6_to_9_accepts_unset_or_a_left_stock_id(self):
        for home in (None, '', 'null', HOME, FULL):
            self.assertTrue(e2e.stock_baseline_ok(8, home))
        self.assertFalse(e2e.stock_baseline_ok(8, 'org.webosbrew.dock'))
        self.assertFalse(e2e.stock_baseline_ok(10, HOME))
        self.assertFalse(e2e.stock_baseline_ok(5, None))


class RebootDialog(unittest.TestCase):
    def test_only_6_to_9_with_the_patch_not_mounted(self):
        for major in (6, 7, 8, 9):
            self.assertTrue(e2e.expects_reboot_dialog(major, False))
            self.assertFalse(e2e.expects_reboot_dialog(major, True), 'mounted: a live switch')
        for major in (5, 10, 11, 12):
            self.assertFalse(e2e.expects_reboot_dialog(major, False))

    def test_parse_status(self):
        out = 'data=missing\nmounted=no\nmatch=9.2.1\nreboot=needed\n'
        st = e2e.parse_status(out)
        self.assertEqual((st['mounted'], st['match'], st['reboot']), ('no', '9.2.1', 'needed'))
        st = e2e.parse_status('mounted=yes\nrelease=9.2.1\nreboot=no')
        self.assertEqual((st['mounted'], st['reboot']), ('yes', 'no'))
        self.assertEqual(e2e.parse_status('some log line = with spaces\n'), {})


class Texts(unittest.TestCase):
    def read(self, rel):
        with open(os.path.join(REPO, rel), encoding='utf-8') as f:
            return f.read()

    def test_every_asserted_text_is_in_its_source(self):
        for text, rel in e2e.TEXT_SOURCES:
            self.assertIn(text, self.read(rel), '%r not in %s' % (text, rel))

    def test_the_new_texts(self):
        self.assertEqual(e2e.UNINSTALL_TOAST, 'Home Chooser was removed. The Home button opens LG Home again.')
        self.assertEqual(e2e.RESTART_NOTE['remove'], 'LG Home is on the Home button now. A restart removes the patch.')
        self.assertEqual(e2e.REBOOT_PROMPT, {'apply': 'Restart the TV to finish setting up the Home button.',
                                             'remove': 'Restart the TV to finish removing the Home button patch.'})
        self.assertNotIn('restart', (e2e.REMOVAL_ALERT % 'Dock').lower())
        self.assertNotIn('restart', e2e.UNINSTALL_TOAST.lower())

    def test_the_old_restart_wording_is_gone_from_the_product(self):
        for rel in ('backport/homechooser-home-watch', 'app/src/views/useHomeChooser.js', 'app/src/views/MainPanel.js',
                    'service/src/policy.c'):
            body = self.read(rel)
            for old in e2e.OLD_RESTART_TEXTS:
                self.assertNotIn(old, body, '%r still in %s' % (old, rel))

    def test_app_restart_note_matches(self):
        body = self.read('app/src/views/useHomeChooser.js')
        m = re.search(r"RESTART_NOTE = \{(.*?)\};", body, re.S)
        found = dict(re.findall(r"(\w+): '([^']*)'", m.group(1)))
        self.assertEqual(found, e2e.RESTART_NOTE)

    def test_the_app_has_no_restart_dialog_and_the_service_says_what_it_logs(self):
        self.assertNotIn('rebootAsk', self.read('app/src/views/useHomeChooser.js'))
        self.assertNotIn('Restart the TV?', self.read('app/src/views/MainPanel.js'))
        main = self.read('service/src/main.c')
        self.assertIn('createAlert request: message: %s; buttons: Reboot (rebootConfirmed), Later (focused)', main)


class RebootAsk(unittest.TestCase):
    APPLY = e2e.REBOOT_PROMPT['apply']
    REQUEST = ('2026-10-24T05:48:12 user.info org.webosbrew.homechooser.service [] REBOOT_ASK {} '
               'createAlert request: message: %s; buttons: Reboot (rebootConfirmed), Later (focused)' % APPLY)

    def test_accepted_alert(self):
        text = self.REQUEST + '\n' + ('2026-10-24T05:48:12 user.info svc [] REBOOT_ASK {} luna://com.webos.notification/'
                                      'createAlert accepted (exit 0): {"returnValue":true,"alertId":"com.x-1234"}')
        got = e2e.parse_reboot_ask(text)
        self.assertEqual(got['message'], self.APPLY)
        self.assertEqual(got['buttons'], ['Reboot', 'Later'])
        self.assertEqual((got['alert'], got['alert_id'], got['toast']), ('accepted', 'com.x-1234', None))

    def test_escaped_json_in_the_log(self):
        text = self.REQUEST + '\n' + 'REBOOT_ASK {} luna://com.webos.notification/createAlert accepted (exit 0): {\\"returnValue\\":true,\\"alertId\\":\\"a-9\\"}'
        self.assertEqual(e2e.parse_reboot_ask(text)['alert_id'], 'a-9')

    def test_refused_alert_then_toast(self):
        text = '\n'.join([self.REQUEST,
                          'REBOOT_ASK {} luna://com.webos.notification/createAlert refused (exit 0): {"returnValue":false,"errorText":"Alert is blocked by [ system ]"}',
                          'REBOOT_ASK {} luna://com.webos.notification/createToast accepted (exit 0): {"returnValue":true,"toastId":"t1"}'])
        got = e2e.parse_reboot_ask(text)
        self.assertEqual((got['alert'], got['alert_id'], got['toast']), ('refused', None, 'accepted'))

    def test_nothing_logged_and_foreign_lines(self):
        for text in ('', None, 'UNINSTALL_ASK luna://com.webos.notification/createAlert accepted (exit 0): {"alertId":"u1"}'):
            got = e2e.parse_reboot_ask(text)
            self.assertEqual((got['message'], got['buttons'], got['alert'], got['alert_id'], got['toast']), (None, [], None, None, None))

    def test_the_reboot_via_choices(self):
        with open(os.path.join(REPO, 'e2e/homechooser_e2e.py'), encoding='utf-8') as f:
            src = f.read()
        self.assertIn("'--reboot-via', choices=['later', 'alert'], default='later'", src)
        self.assertNotIn("'dialog'", src.split("'--reboot-via'")[1].split('add_argument')[0])


class Luna(unittest.TestCase):
    def test_parse_luna(self):
        self.assertEqual(e2e.parse_luna('{"returnValue":true}'), {'returnValue': True})
        self.assertEqual(e2e.parse_luna('warning: x\n{"returnValue":false,"errorCode":"notHome"}\n'),
                         {'returnValue': False, 'errorCode': 'notHome'})
        self.assertIsNone(e2e.parse_luna(''))
        self.assertIsNone(e2e.parse_luna('[timeout]'))

    def test_get_info_contract(self):
        ok = {'returnValue': True, 'apiVersion': 1, 'version': '0.1.0', 'root': True, 'group': e2e.API_GROUP,
              'permitted': True, 'isHome': False}
        self.assertEqual(e2e.api_reply_problems('getInfo', ok), [])
        bad = dict(ok, apiVersion=2)
        del bad['isHome']
        p = e2e.api_reply_problems('getInfo', bad)
        self.assertTrue(any('isHome' in x for x in p) and any('apiVersion' in x for x in p), p)
        self.assertTrue(e2e.api_reply_problems('getInfo', dict(ok, root='yes')))
        self.assertTrue(e2e.api_reply_problems('getInfo', dict(ok, group='other')))

    def test_list_and_running_contract(self):
        apps = {'returnValue': True, 'apiVersion': 1, 'apps': [
            {'id': 'a', 'title': 'A', 'icon': 'i.png', 'folderPath': '/x', 'removable': True, 'visible': True}]}
        self.assertEqual(e2e.api_reply_problems('listApps', apps), [])
        leak = {'returnValue': True, 'apiVersion': 1, 'apps': [{'id': 'a', 'requiredPermissions': []}]}
        self.assertTrue(e2e.api_reply_problems('listApps', leak), 'a field beyond the contract')
        self.assertTrue(e2e.api_reply_problems('listApps', {'returnValue': True, 'apiVersion': 1}))
        run = {'returnValue': True, 'apiVersion': 1, 'running': [{'id': 'a', 'instanceId': 'x', 'processid': 12,
                                                                  'webprocessid': 34}]}
        self.assertEqual(e2e.api_reply_problems('running', run), [])
        self.assertTrue(e2e.api_reply_problems('running', {'returnValue': True, 'apiVersion': 1, 'running': [{}]}))

    def test_refusals(self):
        self.assertTrue(e2e.api_refused({'returnValue': False, 'errorCode': 'noPermission'}))
        self.assertTrue(e2e.api_refused({'returnValue': False, 'errorCode': -1, 'errorText': 'Denied method call'}))
        self.assertFalse(e2e.api_refused({'returnValue': True}))
        self.assertFalse(e2e.api_refused(None), 'no reply is not a clean refusal')
        p = e2e.api_reply_problems('listApps', {'returnValue': False, 'errorCode': 'notRoot'})
        self.assertTrue(p and 'notRoot' in p[0])


class Crashes(unittest.TestCase):
    def test_crash_lines(self):
        log = '\n'.join([
            '2026-10-24T01:00:00 host crashd[812]: CRASHED: /usr/bin/WebAppMgr signal 11',
            '2026-10-24T01:00:01 host WebAppMgr[900]: [I] app launched',
            '2026-10-24T01:00:02 host systemd[1]: webapp-mgr.service: Main process exited, code=dumped, status=11/SEGV',
            '2026-10-24T01:00:03 host sam[1]: NL_APP_LAUNCH_BEGIN',
        ])
        got = e2e.crash_lines(log)
        self.assertEqual(len(got), 2, got)
        self.assertEqual(e2e.crash_lines('WebAppMgr: ok\n'), [])


class WaitForDevice(unittest.TestCase):
    """2026-10-24: three VMs whose previous boot was cut at ~9 s came up in HBC failsafe mode (telnetd,
    no Dropbear): developer mode answered, root SSH never did.  The wait names that state."""

    def wait(self, root_up, apps):
        class Dev:
            def __init__(self, vm):
                pass

            def sh(self, cmd, timeout=60):
                return (0, 'up') if root_up else (255, 'connection refused')

            def installed(self):
                return apps

        said = []

        class Rec:
            def say(self, t):
                said.append(t)
        orig, e2e.Device = e2e.Device, Dev
        try:
            return e2e.wait_for_device('vm', Rec(), timeout=0), said
        finally:
            e2e.Device = orig

    def test_states(self):
        hb = ['org.webosbrew.hbchannel']
        self.assertEqual(self.wait(True, hb)[0], e2e.READY)
        self.assertEqual(self.wait(True, [])[0], e2e.NO_HBCHANNEL)
        self.assertEqual(self.wait(False, hb)[0], e2e.NO_ROOT_SSH)
        self.assertEqual(self.wait(False, [])[0], e2e.DOWN)
        state, said = self.wait(False, hb)
        self.assertEqual(len(said), 1)
        self.assertIn('no-root-ssh', said[0])


class StockLeftRunning(unittest.TestCase):
    """8791f6e: on 6-10 the stock home is left running for every launcher; 11/12 close it; 5 is not touched."""

    def test_releases(self):
        import types
        got = {m: e2e.Scenario.stock_left_running(types.SimpleNamespace(major=m)) for m in range(5, 13)}
        self.assertEqual(got, {5: False, 6: True, 7: True, 8: True, 9: True, 10: True, 11: False, 12: False})


class FaultPopups(unittest.TestCase):
    """foreign_popup: keyCheck2 is boot noise (triage 2026-10-24); a fault raised after the mark counts."""
    KEYCHECK = ('{ "returnValue": true, "faultDetectedStatus": { "detected": 1, "detectedFaults": [ { "faultType": '
                '"general", "faultContent": "keyCheck2_4415_sysfaultdetector", "faultTime": "30.448679184" } ] } }')

    def test_keycheck_is_noise(self):
        real, noise = e2e.classify_faults(self.KEYCHECK, 300.0)
        self.assertEqual(real, [])
        self.assertEqual(len(noise), 1)
        # noise even without a mark
        self.assertEqual(e2e.classify_faults(self.KEYCHECK, None)[0], [])

    def test_new_crash_counts(self):
        # 9.2.1 2026-10-23T04:26:50: crashd CRASHED WebAppMgr at [194.278], a fault entry at 194.292
        reply = json.dumps({'returnValue': True, 'faultDetectedStatus': {'detected': 2, 'detectedFaults': [
            {'faultTime': '31.319272907', 'faultType': 'general', 'faultContent': 'keyCheck2_4629_sysfaultdetector'},
            {'faultTime': '194.292195087', 'faultType': 'crash', 'faultContent': 'WebAppMgr'}]}})
        real, noise = e2e.classify_faults(reply, 150.0)
        self.assertEqual([f['faultContent'] for f in real], ['WebAppMgr'])
        # the same crash, raised before this step's mark: noted, not counted
        real, noise = e2e.classify_faults(reply, 200.0)
        self.assertEqual(real, [])
        self.assertEqual(len(noise), 2)

    def test_bad_reply(self):
        self.assertEqual(e2e.classify_faults('Error: timeout', 10.0), ([], []))

    def test_line_pattern(self):
        rx = re.compile(e2e.POPUP_LINE_RE)
        self.assertTrue(rx.search('2026-10-23T04:26:50Z [194.2] user.info crashd [] crashd CRASHED {"process":"WebAppMgr.5823"}'))
        self.assertFalse(rx.search('2026-10-23T04:26:47Z [191.6] user.info faultmanager [] faultmanager FAULT_RESPONSE {} '
                                   '[general]/tmp/faultmanager/general/keyCheck2_4629_sysfaultdetector(none)'))


class FakeRec:
    def __init__(self):
        self.checks, self.notes = [], []

    def check(self, name, ok, evidence='', expected=None):
        self.checks.append((name, bool(ok)))
        return ok

    def note(self, what, evidence=''):
        self.notes.append(what)


class FakeDev:
    def __init__(self):
        self.boot, self.units, self.log_restarts, self.messages = 'b1', {}, '0', ''

    def unit(self, name):
        return dict(self.units.get(name, {'MainPID': '100', 'NRestarts': '0'}))

    def boot_id(self):
        return self.boot

    def sh(self, cmd, timeout=60):
        if cmd.startswith('date'):
            return 0, '2026-10-24T01:00:00\n'
        if 'grep -c' in cmd:
            return 0, self.log_restarts + '\n'
        if '/var/log/messages' in cmd:
            return 0, self.messages
        return 0, ''


def scenario(major=9):
    s = e2e.Scenario.__new__(e2e.Scenario)
    s.rec, s.dev, s.major = FakeRec(), FakeDev(), major
    return s


class RestartDetection(unittest.TestCase):
    def test_no_restart_passes(self):
        s = scenario()
        snap = s.compositor_snapshot()
        s.check_no_compositor_restart('x', snap)
        s.check_no_crash('x', snap)
        self.assertEqual([ok for _, ok in s.rec.checks], [True, True, True])

    def test_a_restart_by_pid_count_or_log_fails(self):
        for change in ({'units': {'surface-manager-daemon': {'MainPID': '200', 'NRestarts': '0'}}},
                       {'units': {'surface-manager-daemon': {'MainPID': '100', 'NRestarts': '1'}}},
                       {'log_restarts': '1'}):
            s = scenario()
            snap = s.compositor_snapshot()
            for k, v in change.items():
                setattr(s.dev, k, v)
            s.check_no_compositor_restart('x', snap)
            self.assertEqual([ok for _, ok in s.rec.checks], [False], change)

    def test_a_reboot_in_between_is_not_a_restart(self):
        s = scenario()
        snap = s.compositor_snapshot()
        s.dev.boot = 'b2'
        s.dev.log_restarts = '1'
        s.check_no_compositor_restart('x', snap)
        self.assertEqual(s.rec.checks, [])
        self.assertTrue(s.rec.notes)

    def test_a_wam_crash_or_respawn_fails(self):
        s = scenario()
        snap = s.compositor_snapshot()
        s.dev.messages = '2026-10-24T01:00:05 tv crashd[1]: CRASHED: WebAppMgr\n'
        s.dev.units = {'webapp-mgr': {'MainPID': '300', 'NRestarts': '1'}}
        s.check_no_crash('x', snap)
        self.assertEqual([ok for _, ok in s.rec.checks], [False, False])

    def test_only_6_to_9(self):
        for major in (5, 10, 12):
            s = scenario(major)
            s.check_no_compositor_restart('x', {'boot': 'b1'})
            s.check_no_crash('x', {'boot': 'b1'})
            self.assertEqual(s.rec.checks, [])


class ApiLaunchers(unittest.TestCase):
    def test_api_check_needs_a_web_fixture_or_an_author_launcher(self):
        with self.assertRaises(SystemExit):
            e2e.make_launcher('fixture:native.normal', api=True)
        with self.assertRaises(SystemExit):
            e2e.make_launcher('fixture:qml.overlay', api=True)
        orig = e2e.api_fixture_ipks
        e2e.api_fixture_ipks = lambda announce=print: {e2e.FIXTURE_ID % ('web', 'normal'): '/nonexistent/web.ipk'}
        try:
            L = e2e.make_launcher('fixture:web.normal', api=True)
        finally:
            e2e.api_fixture_ipks = orig
        self.assertEqual((L.id, L.ipk, L.is_fixture), ('org.webosbrew.hcfixture.web.normal', '/nonexistent/web.ipk', True))
        author = e2e.make_launcher('org.example.launcher', '/nonexistent/author.ipk', api=True)
        self.assertEqual((author.id, author.is_fixture), ('org.example.launcher', False))

    def test_disposable_fixture_declares_no_home_and_no_permission(self):
        with open(os.path.join(HERE, 'fixtures', 'disposable', 'build.sh')) as f:
            body = f.read()
        self.assertIn(e2e.DISPOSABLE_ID, body)
        self.assertNotIn('"appCategory"', body)
        self.assertNotIn('"requiredPermissions"', body)
        self.assertNotIn('HC_FIXTURE_PERMISSION"]', body)

    def test_matrix_dry_run_passes_api_check(self):
        tmp = tempfile.mkdtemp()
        hosts = os.path.join(tmp, 'hosts.toml')
        with open(hosts, 'w') as f:
            f.write('default = "pi"\n[hosts.pi]\nssh = "raspberrypi"\n')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = run_matrix.main(['--dry-run', '--hosts-file', hosts, '--vms', '9.x', '--api-check', '--reboot-via', 'later',
                                  '--results', tmp], lister=lambda h: ['w24g-ombre'], busy=lambda h: False)
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn('--api-check', out.getvalue())
        self.assertIn('--reboot-via later', out.getvalue())


if __name__ == '__main__':
    unittest.main()
