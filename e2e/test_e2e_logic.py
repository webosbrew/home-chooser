#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
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


class ClickInput(unittest.TestCase):
    def frame(self):
        return {'width': 1280, 'height': 720, 'elements': [
            {'text': 'Use as Home', 'kind': 'button', 'box': [10, 20, 110, 60], 'centre': [60, 40]}]}

    def test_exact_role_region_and_centre(self):
        f = self.frame()
        self.assertEqual(e2e.match_element(f, 'Use as Home', 'button', [0, 0, 120, 80])['centre'], [60, 40])
        self.assertEqual(e2e.match_element(f, role='button')['text'], 'Use as Home')

    def test_missing_ambiguous_and_nonexact(self):
        f = self.frame()
        for text in ('Use', 'use as home', 'Cancel'):
            with self.assertRaises(ValueError):
                e2e.match_element(f, text)
        f['elements'] *= 2
        with self.assertRaises(ValueError):
            e2e.match_element(f, 'Use as Home')
        with self.assertRaises(ValueError):
            e2e.match_element(self.frame(), role='text')
        with self.assertRaises(ValueError):
            e2e.match_element(self.frame(), role='button', region=[200, 200, 300, 300])

    def test_launcher_icon_and_label_require_role(self):
        f = self.frame()
        f['elements'].append(dict(f['elements'][0], kind='icon'))
        with self.assertRaises(ValueError):
            e2e.match_element(f, 'Use as Home')
        self.assertEqual(e2e.match_element(f, 'Use as Home', ('text', 'button'))['kind'], 'button')

    def test_click_command_local_and_host(self):
        from unittest.mock import Mock
        for mode, target in [('local', 'vm'), ('remote', 'pi/vm')]:
            h = e2e.BeanjarHost(mode=mode, name='pi')
            h.display_beanjar = Mock(return_value=(0, 'ok'))
            h.remote_click('vm', [60, 40], self.frame())
            h.display_beanjar.assert_called_once_with('remote', 'click', target, '60', '40',
                                                      '--frame', '1280x720', '--json')

    def test_unavailable_click_logs_and_caches_fallback(self):
        from unittest.mock import Mock
        from types import SimpleNamespace
        h = e2e.BeanjarHost()
        h.beanjar, h.input_rec = Mock(return_value=(2, 'unknown command click')), Mock()
        s = e2e.Scenario.__new__(e2e.Scenario)
        s.a, s.host = SimpleNamespace(input_mode='click'), h
        self.assertFalse(s.use_click())
        self.assertFalse(s.use_click())
        h.beanjar.assert_called_once()
        h.input_rec.note.assert_called_once()
        s.a.input_mode = 'keys'
        self.assertFalse(s.use_click())

    def test_coordinate_mapping_logged_once_across_scenarios(self):
        from unittest.mock import Mock
        host = e2e.BeanjarHost()
        host.display_beanjar = Mock(return_value=(0, json.dumps(self.frame())))
        rec = Mock()
        for _ in range(2):
            s = e2e.Scenario.__new__(e2e.Scenario)
            s.host, s.vm, s.rec = host, 'vm', rec
            s.elements('test')
        rec.note.assert_called_once()

    def test_click_effect_and_fresh_read(self):
        from unittest.mock import Mock
        s = e2e.Scenario.__new__(e2e.Scenario)
        s.host, s.rec, s.vm = Mock(), Mock(), 'vm'
        s.host.remote_click.return_value = (0, 'ok')
        s.elements = Mock(return_value=self.frame())
        s.wait = lambda fn, *a: fn()
        s.click_element('Use as Home', effect=lambda: True)
        self.assertEqual(s.elements.call_count, 2)
        with self.assertRaises(RuntimeError):
            s.click_element('Use as Home', effect=lambda: False)

    def retry(self, outcomes, closed=True):
        from unittest.mock import Mock
        s = e2e.Scenario.__new__(e2e.Scenario)
        s.rec, s.host = Mock(), Mock()
        s.press_alert, s.use_click = Mock(return_value=True), lambda: True
        verify, request = Mock(side_effect=outcomes), Mock()
        args = ('prompt', 'Reboot', 'Later', 'test', Mock(), verify, lambda: closed, request)
        return s, args, request

    def test_alert_click_uses_owned_button_without_focus_keys(self):
        from unittest.mock import Mock
        from types import SimpleNamespace
        with open(os.path.join(HERE, 'samples', 'reboot-alert.json')) as f:
            frame = json.load(f)
        s = e2e.Scenario.__new__(e2e.Scenario)
        host = SimpleNamespace(click_available=lambda: True, remote_click=Mock(return_value=(0, 'clicked')))
        s.a, s.host, s.vm, s.rec = SimpleNamespace(input_mode='click'), host, 'vm', Mock()
        s.elements, s.press = Mock(return_value=frame), Mock()
        self.assertTrue(s.press_alert(e2e.REBOOT_PROMPT['apply'], 'Reboot', 'Later', 'test',
                                     lambda: ('matching accepted service request', True)))
        host.remote_click.assert_called_once_with('vm', [916.0, 513.0], frame)
        s.press.assert_not_called()
        s.elements.assert_called_once()

    def test_alert_success_never_retries(self):
        s, args, request = self.retry(['new boot id'])
        self.assertEqual(s.alert_action(*args), 'new boot id')
        request.assert_not_called()

    def test_alert_dismissal_retries_once_with_note(self):
        s, args, request = self.retry([False, True])
        self.assertTrue(s.alert_action(*args))
        request.assert_called_once()
        s.rec.note.assert_called_once()
        self.assertEqual(s.press_alert.call_count, 2)

    def test_alert_second_failure_fails(self):
        s, args, request = self.retry([False, False])
        with self.assertRaisesRegex(RuntimeError, 'second alert click failed'):
            s.alert_action(*args)
        request.assert_called_once()
        self.assertEqual(s.press_alert.call_count, 2)

    def test_alert_still_open_does_not_retry(self):
        s, args, request = self.retry([False], closed=False)
        with self.assertRaises(RuntimeError):
            s.alert_action(*args)
        request.assert_not_called()


class LauncherPreflight(unittest.TestCase):
    def test_candidate_rule_and_ours(self):
        for category in (['home'], ['kidsApp', 'home'], 'home'):
            self.assertTrue(e2e.foreign_launcher({'id': 'example.launcher', 'appCategory': category}))
        for ident in (e2e.APP_ID, 'org.webosbrew.hbchannel', 'org.webosbrew.hcfixture.web.normal'):
            self.assertFalse(e2e.foreign_launcher({'id': ident, 'appCategory': ['home']}))
        for category in (None, [], ['browser'], 'homebrew'):
            self.assertFalse(e2e.foreign_launcher({'id': 'example.app', 'appCategory': category}))

    def test_known_takeover_without_home_category(self):
        self.assertEqual(e2e.launcher_removal_reason({'id': 'com.homebrew.homeback', 'appCategory': []}),
                         'known HOME takeover app id')
        self.assertIsNone(e2e.launcher_removal_reason({'id': 'example.app', 'title': 'HomeBack manual'}))

    def test_owned_startup_entries_only(self):
        root = '/media/developer/apps/usr/palm/services/com.homebrew.homeback.service'
        self.assertTrue(e2e.startup_entry_owned('com.homebrew.homeback', 'homeback', root + '/startup.sh', ''))
        self.assertTrue(e2e.startup_entry_owned('com.homebrew.homeback', 'com.homebrew.homeback', '', 'exec ' + root + '/startup.sh'))
        for name, target, content in [('shared', '', 'exec ' + root + '/startup.sh'),
                                      ('com.homebrew.homeback', '', '# ' + root + '/startup.sh'),
                                      ('homeback', root + '.other/startup.sh', '')]:
            self.assertFalse(e2e.startup_entry_owned('com.homebrew.homeback', name, target, content))

    def test_startup_cleanup_uses_installed_layout(self):
        from unittest.mock import Mock
        d, r = Mock(), Mock()
        path = '/var/lib/webosbrew/init.d/homeback'
        d.sh.side_effect = [(0, 'run-parts /var/lib/webosbrew/init.d 200>&-'), (0, path),
                            (0, '/media/developer/apps/usr/palm/services/com.homebrew.homeback.service/startup.sh'),
                            (1, 'missing target'), (0, '')]
        e2e.clean_startup_entries(d, r, 'com.homebrew.homeback')
        self.assertEqual(d.sh.call_args.args[0], 'rm -- ' + path)
        self.assertTrue(r.check.call_args.args[1])

    def test_removal_reboots_before_proceeding(self):
        from unittest.mock import Mock
        s = Mock()
        e2e.restart_after_launcher_cleanup(s, [])
        s.beanjar_reboot.assert_not_called()
        e2e.restart_after_launcher_cleanup(s, ['com.homebrew.homeback'])
        s.beanjar_reboot.assert_called_once()
        s.beanjar_reboot.return_value = False
        with self.assertRaises(RuntimeError):
            e2e.restart_after_launcher_cleanup(s, ['example.launcher'])

    def test_removal_and_retention_notes(self):
        from unittest.mock import Mock
        d, r = Mock(), Mock()
        d.installed.side_effect = [['example.launcher', 'example.browser'], ['example.browser']]
        d.sh.side_effect = [(0, json.dumps({'id': 'example.launcher', 'version': '1', 'appCategory': ['home']})),
                            (0, json.dumps({'id': 'example.browser', 'version': '2'}))]
        d.remove.return_value = (0, 'removed')
        from unittest.mock import patch
        with patch.object(e2e, 'clean_startup_entries') as cleanup:
            self.assertEqual(e2e.clean_launchers(d, r), ['example.launcher'])
        cleanup.assert_called_once_with(d, r, 'example.launcher')
        d.remove.assert_called_once_with('example.launcher')
        self.assertTrue(r.check.call_args.args[1])
        self.assertIn('example.browser', r.note.call_args.args[1])

    def test_removal_failure_fails(self):
        from unittest.mock import Mock
        d, r = Mock(), Mock()
        d.installed.return_value = ['example.launcher']
        d.sh.return_value = (0, json.dumps({'id': 'example.launcher', 'appCategory': ['home']}))
        d.remove.return_value = (1, 'refused')
        with self.assertRaises(RuntimeError):
            e2e.clean_launchers(d, r)
        self.assertFalse(r.check.call_args.args[1])


class AlertFocus(unittest.TestCase):
    def frame(self, name='reboot-alert'):
        with open(os.path.join(HERE, 'samples', name + '.json')) as f:
            return json.load(f)

    def parse(self, frame):
        return e2e.alert_focus(frame, e2e.REBOOT_PROMPT['apply'], 'Reboot', 'Later')

    def test_recorded_restart_default(self):
        self.assertEqual(self.parse(self.frame()), ('Later', 'UP'))

    def test_app_title_behind_alert_is_optional(self):
        frame = self.frame()
        frame['elements'] = [r for r in frame['elements'] if r.get('text') != 'Home Chooser']
        self.assertEqual(self.parse(frame), ('Later', 'UP'))

    def test_recorded_target_focus(self):
        self.assertEqual(self.parse(self.frame('reboot-target')), ('Reboot', 'UP'))

    def test_recorded_uninstall(self):
        self.assertEqual(e2e.alert_focus(self.frame('uninstall-alert'),
                         'Uninstall HC Fixture disposable?', 'Uninstall', 'Cancel'), ('Cancel', 'UP'))

    def test_bad_ownership_or_focus_fails_closed(self):
        import copy
        base = self.frame()
        for text in (e2e.REBOOT_PROMPT['apply'], 'Reboot', 'Later'):
            frame = copy.deepcopy(base)
            frame['elements'] = [r for r in frame['elements'] if r.get('text') != text]
            with self.subTest(missing=text), self.assertRaises(ValueError):
                self.parse(frame)
        for disabled in (True, False, None):
            frame = copy.deepcopy(base)
            for row in frame['elements']:
                if row['kind'] == 'button' and row['text'] in ('Reboot', 'Later'):
                    row['style'] = dict(disabled=disabled, contrast=3, highlighted=False)
            with self.subTest(disabled=disabled), self.assertRaises(ValueError):
                self.parse(frame)

    def test_missing_service_proof_sends_no_key(self):
        from types import SimpleNamespace
        scenario = e2e.Scenario.__new__(e2e.Scenario)
        scenario.rec = SimpleNamespace(say=lambda *a: None, note=lambda *a: None)
        scenario.press = lambda *a: self.fail('unproven service must send no key')
        scenario.shot = lambda *a: self.fail('service proof must precede screen capture')
        self.assertFalse(scenario.press_alert(e2e.REBOOT_PROMPT['apply'], 'Reboot', 'Later',
                                             'test', lambda: ('refused', False)))

    def test_recorded_service_evidence(self):
        with open(os.path.join(HERE, 'samples', 'alert-service.log')) as f:
            raw = f.read()
        reboot = lambda text: e2e.alert_service_proof(text, e2e.REBOOT_PROMPT['apply'], 'Reboot', 'Later')
        uninstall = lambda text: e2e.alert_service_proof(text, 'Uninstall HC Fixture disposable?',
                            'Uninstall', 'Cancel', e2e.DISPOSABLE_ID, 'org.webosbrew.hcfixture.web.normal')
        self.assertTrue(reboot(raw))
        self.assertTrue(uninstall(raw))
        self.assertFalse(reboot(raw.replace('accepted', 'refused')))
        self.assertFalse(reboot(raw.replace('(exit 0)', '(exit 1)')))
        self.assertFalse(reboot(raw.replace('"returnValue":true', '"returnValue":false')))
        self.assertFalse(reboot(raw.replace(e2e.REBOOT_PROMPT['apply'], 'different prompt')))
        self.assertFalse(uninstall(raw.replace(e2e.DISPOSABLE_ID, 'other.app')))
        self.assertFalse(uninstall(raw.replace('org.webosbrew.hcfixture.web.normal', 'other.caller')))
        self.assertFalse(uninstall(raw.replace('alert shown', 'alert refused')))
        self.assertFalse(uninstall(raw.replace('"returnValue":true', '"returnValue":false')))

    def test_extra_alert_control_fails_closed(self):
        frame = self.frame()
        frame['elements'].append(dict(kind='button', text='LG action', box=[100,500,200,530]))
        with self.assertRaises(ValueError):
            self.parse(frame)

    def test_press_never_enters_after_failed_proof(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        default, target = self.frame(), self.frame('reboot-target')
        for frames, keys, success in (([target], [], False),
                                      ([default, default], ['UP'], False),
                                      ([default, {}], ['UP'], False),
                                      ([default, target], ['UP', 'OK'], True)):
            scenario = e2e.Scenario.__new__(e2e.Scenario)
            sent = []
            scenario.press = lambda *k: sent.extend(k)
            scenario.shot = lambda label: __file__
            scenario.rec = SimpleNamespace(say=lambda *a: None, note=lambda *a: None)
            with patch.object(e2e, 'run', side_effect=[(0, json.dumps(f)) for f in frames]):
                self.assertEqual(scenario.press_alert(e2e.REBOOT_PROMPT['apply'],
                                 'Reboot', 'Later', 'test', lambda: ('accepted service log', True)), success)
            self.assertEqual(sent, keys)

    def test_recorded_horizontal_5x(self):
        frame = self.frame('uninstall-alert-5x')
        parse = lambda: e2e.alert_focus(frame, 'Uninstall HC Fixture disposable?', 'Uninstall', 'Cancel')
        self.assertEqual(parse(), ('Cancel', 'LEFT'))
        buttons = {r['text']: r for r in frame['elements'] if r['kind'] == 'button'}
        buttons['Uninstall']['box'], buttons['Cancel']['box'] = buttons['Cancel']['box'], buttons['Uninstall']['box']
        self.assertEqual(parse(), ('Cancel', 'RIGHT'))
        buttons['Uninstall']['style']['highlighted'] = True
        with self.assertRaises(ValueError):
            parse()
        buttons['Uninstall']['style']['highlighted'] = False
        buttons['Uninstall']['box'] = buttons['Cancel']['box']
        with self.assertRaises(ValueError):
            parse()


class AlertEvidenceAcrossReleases(unittest.TestCase):
    frame = AlertFocus.frame
    parse = AlertFocus.parse
    def test_saved_8x_and_11x_defaults(self):
        self.assertEqual(self.parse(self.frame('reboot-alert-8x')), ('Later', 'UP'))
        self.assertEqual(e2e.alert_focus(self.frame('uninstall-alert-11x'),
                         'Uninstall HC Fixture disposable?', 'Uninstall', 'Cancel'), ('Cancel', 'UP'))

    def test_swap_across_releases(self):
        import copy
        for name, target, safe in [('uninstall-alert-11x', 'Uninstall', 'Cancel'),
                                   ('reboot-alert-8x', 'Reboot', 'Later'),
                                   ('uninstall-alert-5x', 'Uninstall', 'Cancel')]:
            before = self.frame(name)
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    e2e.alert_style_swap(before, before, target, safe)
                after = copy.deepcopy(before)
                buttons = {r['text']: r for r in after['elements'] if r.get('text') in (target, safe)}
                buttons[target]['style'], buttons[safe]['style'] = buttons[safe]['style'], buttons[target]['style']
                self.assertTrue(e2e.alert_style_swap(before, after, target, safe))
                # Only one side changing is not a swap.
                buttons[safe]['style'] = buttons[target]['style']
                with self.assertRaises(ValueError):
                    e2e.alert_style_swap(before, after, target, safe)

    def test_centered_extra_control_rejected(self):
        frame = self.frame('uninstall-alert-11x')
        frame['elements'].append(dict(kind='button', text='Other', box=[510,320,600,340]))
        with self.assertRaises(ValueError):
            e2e.alert_focus(frame, 'Uninstall HC Fixture disposable?', 'Uninstall', 'Cancel')

    def test_ls2_owned_request_and_rejections(self):
        import copy
        for target, safe, message, app, uri, params in [
                ('Reboot', 'Later', e2e.REBOOT_PROMPT['apply'], None,
                 e2e.TVPOWER_REBOOT_URI, {'reason': 'remoteKey'}),
                ('Uninstall', 'Cancel', 'Uninstall HC Fixture disposable?', e2e.DISPOSABLE_ID,
                 'luna://com.webos.appInstallService/dev/remove', {'id': e2e.DISPOSABLE_ID, 'subscribe': True})]:
            info = dict(message=message, buttons=[target, safe], alert='accepted', alert_id='owned', actions=[
                dict(label=target, onclick=uri, params=params), dict(label=safe, focus=True)])
            proof = lambda observed: e2e.alert_service_proof('', message, target, safe, app, observed=observed)
            self.assertTrue(proof(info))
            for field, value in [('message', 'foreign'), ('buttons', [safe, target]),
                                 ('alert', None), ('alert', 'refused'), ('alert_id', None), ('actions', [])]:
                with self.subTest(target=target, field=field, value=value):
                    bad = copy.deepcopy(info)
                    bad[field] = value
                    self.assertFalse(proof(bad))
            for field, value in [('onclick', 'luna://foreign/remove'), ('params', {'id': 'foreign'})]:
                bad = copy.deepcopy(info)
                bad['actions'][0][field] = value
                self.assertFalse(proof(bad))

    def test_press_rejects_contrast_without_swap(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        before = self.frame('uninstall-alert-11x')
        s = e2e.Scenario.__new__(e2e.Scenario)
        keys = []
        s.press = lambda key: keys.append(key)
        s.shot = lambda label: __file__
        s.rec = SimpleNamespace(say=lambda *a: None, note=lambda *a: None)
        with patch.object(e2e, 'run', return_value=(0, json.dumps(before))):
            self.assertFalse(s.press_alert('Uninstall HC Fixture disposable?', 'Uninstall', 'Cancel',
                                          'test', lambda: ('owned', True)))
        self.assertEqual(keys, ['UP'])

    def test_centered_swap_permits_enter(self):
        import copy
        from unittest.mock import patch
        from types import SimpleNamespace
        before = self.frame('uninstall-alert-11x')
        after = copy.deepcopy(before)
        buttons = {r['text']: r for r in after['elements'] if r.get('kind') == 'button'}
        buttons['Uninstall']['style'], buttons['Cancel']['style'] = buttons['Cancel']['style'], buttons['Uninstall']['style']
        s = e2e.Scenario.__new__(e2e.Scenario)
        keys, notes = [], []
        s.press = lambda key: keys.append(key)
        s.shot = lambda label: __file__
        s.rec = SimpleNamespace(say=lambda *a: None, note=lambda *a: notes.append(a))
        with patch.object(e2e, 'run', side_effect=[(0, json.dumps(f)) for f in (before, after)]):
            self.assertTrue(s.press_alert('Uninstall HC Fixture disposable?', 'Uninstall', 'Cancel',
                                         'test', lambda: ('owned', True)))
        self.assertEqual(keys, ['UP', 'OK'])
        self.assertTrue(any('background colour' in note for note in notes))

    def test_monitor_binds_actions_to_reply(self):
        monitor = e2e.RebootAlertMonitor(None)
        def call(token, message):
            monitor._observe(dict(type='call', transport='TX', sender='client', token=token,
                                  destination='com.webos.notification', method='createAlert', payload=dict(
                                      sourceId=e2e.APP_ID, title='Home Chooser', message=message, buttons=[
                                          dict(label='Reboot', onclick=e2e.TVPOWER_REBOOT_URI, params={'reason': 'remoteKey'}),
                                          dict(label='Later', focus=True)])))
        call(1, e2e.REBOOT_PROMPT['apply'])
        call(2, 'foreign prompt')
        monitor._observe(dict(type='return', transport='TX', sender='com.webos.notification',
                              destination='client', replyToken=1, payload=dict(returnValue=True, alertId='first')))
        self.assertTrue(e2e.alert_ls2_proof(monitor.snapshot(), e2e.REBOOT_PROMPT['apply'], 'Reboot', 'Later'))

    def test_input_diagnostics_include_other_processes(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            journal = os.path.join(root, 'journalctl')
            with open(journal, 'w') as f:
                f.write("#!/bin/sh\necho 'surface-manager input Home received'\n")
            os.chmod(journal, 0o755)
            with patch.object(e2e, 'messages_command', return_value="printf '%s\\n' '2026-10-29T06:27:00 input stale' '2026-10-29T06:29:00 input current'"):
                command = e2e.home_input_command('2026-10-29T06:28:00Z')
            result = subprocess.run(['sh', '-c', command], capture_output=True, text=True, check=True,
                                    env=dict(os.environ, PATH=root + os.pathsep + os.environ['PATH']))
            self.assertNotIn('stale', result.stdout)
            self.assertIn('input current', result.stdout)
            self.assertIn('surface-manager input Home received', result.stdout)
            self.assertIn('2026-10-29 06:28:00 UTC', command)
        self.assertEqual(e2e.home_input_command(''), 'true')

    def test_failed_home_attempt_stays_failed_without_retry(self):
        from types import SimpleNamespace
        s = e2e.Scenario.__new__(e2e.Scenario)
        keys, commands, checks, notes = [], [], [], []
        s.launcher, s.major, s.stock_id = 'fixture.launcher', 11, e2e.STOCK
        s.rec = SimpleNamespace(say=lambda *a: None, note=lambda *a: notes.append(a),
                                check=lambda name, ok, evidence: checks.append(ok))
        s.dev = SimpleNamespace(foreground=lambda: ('other', 'other'),
                                sh=lambda cmd: (commands.append(cmd) or (0, 'input diagnostic')))
        s.messages_mark = lambda: '2026-10-29T06:28:00'
        s.press = lambda key: (keys.append(key) or (0, 'sent'))
        s.uses_home_key_hook = lambda: False
        s.wait = lambda *a: None
        s.running_ids = lambda: []
        s.shot = lambda *a: 'saved.png'
        with mock_sleep():
            self.assertFalse(s.home_press(e2e.STOCK, 'test', from_app=False))
        self.assertEqual(keys, ['HOME'])
        self.assertEqual(checks, [False])
        self.assertTrue(commands)
        self.assertIn('input diagnostic', notes[0][1])


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

    def test_service_log_journal_boundary_and_ownership(self):
        from unittest.mock import patch
        mark = '2026-10-29T06:28:00'
        with tempfile.TemporaryDirectory() as root:
            journal = os.path.join(root, 'journalctl')
            with open(journal, 'w') as f:
                f.write('#!/bin/sh\n[ "$*" = "-b --since 2026-10-29 06:28:00 UTC --no-pager -o cat" ] || exit 1\n'
                        "echo 'HomeChooser UNINSTALL_ASK {} current'\n"
                        "echo 'Other UNINSTALL_ASK {} foreign'\n")
            os.chmod(journal, 0o755)
            with patch.object(e2e, 'messages_command', return_value="printf '%s\\n' '2026-10-29T06:27:00 HomeChooser UNINSTALL_ASK {} stale'"):
                command = e2e.service_log_command(mark, 'UNINSTALL_ASK')
            result = subprocess.run(['sh', '-c', command], capture_output=True, text=True,
                                    env=dict(os.environ, PATH=root + os.pathsep + os.environ['PATH']), check=True)
            self.assertEqual(result.stdout, 'HomeChooser UNINSTALL_ASK {} current\n')
        self.assertEqual(e2e.service_log_command('', 'UNINSTALL_ASK'), 'true')

    def test_only_lines_after_the_mark(self):
        log = ['a closeByAppId x', 'b', 'c']
        self.assertEqual(e2e.lines_after(log, 2), ['c'])
        self.assertEqual(e2e.lines_after(log, 3), [])
        self.assertEqual(e2e.lines_after(log, 0), log)

    def test_a_log_shorter_than_the_mark_is_a_new_boot(self):
        self.assertEqual(e2e.lines_after(['x', 'y'], 50), ['x', 'y'])


class StockRecord(unittest.TestCase):
    def test_uninstall_checks_every_owned_cache_and_access_path(self):
        paths = e2e.uninstall_remnant_paths()
        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual(set(paths), {
            '/var/lib/webosbrew/homechooser/hook',
            '/var/lib/webosbrew/homechooser/hook-stale',
            '/var/lib/webosbrew/homechooser/boot',
            '/var/luna-service2-dev/api-permissions.d/org.webosbrew.homechooser.launcher.api.json',
            '/var/luna-service2/api-permissions.d/org.webosbrew.homechooser.launcher.api.json',
        })

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
        out = 'data=missing\nmounted=no\npatch=possible\nreboot=needed\n'
        st = e2e.parse_status(out)
        self.assertEqual((st['mounted'], st['patch'], st['reboot']), ('no', 'possible', 'needed'))
        st = e2e.parse_status('mounted=yes\npatch=possible\nreboot=no')
        self.assertEqual((st['mounted'], st['reboot']), ('yes', 'no'))
        self.assertEqual(e2e.parse_status('some log line = with spaces\n'), {})


class Texts(unittest.TestCase):
    def read(self, rel):
        with open(os.path.join(REPO, rel), encoding='utf-8') as f:
            return f.read()

    def test_every_asserted_text_is_in_its_source(self):
        for text, rel in e2e.TEXT_SOURCES:
            self.assertIn(text, self.read(rel), '%r not in %s' % (text, rel))

    def test_device_text_forms_match_utf8_and_minified_unicode_escape(self):
        note = e2e.RESTART_NOTE['apply']
        literal, escaped = e2e.device_text_forms(note)
        self.assertIn(' — ', literal)
        self.assertIn(' \\u2014 ', escaped)
        self.assertEqual(escaped.replace('\\u2014', '—'), literal)
        self.assertEqual(e2e.device_text_forms(e2e.RESTART_NOTE['remove']),
                         (e2e.RESTART_NOTE['remove'],))

    def test_the_new_texts(self):
        self.assertEqual(e2e.UNINSTALL_TOAST, 'Home Chooser was removed. The Home button opens LG Home again.')
        self.assertEqual(e2e.RESTART_NOTE['remove'], 'LG Home is on the Home button now. A restart removes the hook.')
        self.assertEqual(e2e.REBOOT_PROMPT, {'apply': 'Restart the TV to finish setting up the Home button.',
                                             'remove': 'Restart the TV to finish removing the Home button patch.'})
        self.assertNotIn('restart', (e2e.REMOVAL_ALERT % 'Dock').lower())
        self.assertNotIn('restart', e2e.UNINSTALL_TOAST.lower())

    def test_the_old_restart_wording_is_gone_from_the_product(self):
        for rel in ('service/scripts/homechooser-home-watch', 'app/src/views/useHomeChooser.js', 'app/src/views/MainPanel.js',
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
        self.assertIn('createAlert request: message: %s; buttons: Reboot (tvpower/power/reboot), Later (focused)', main)


class RebootAsk(unittest.TestCase):
    APPLY = e2e.REBOOT_PROMPT['apply']
    REQUEST = ('2026-10-24T05:48:12 user.info org.webosbrew.homechooser.service [] REBOOT_ASK {} '
               'createAlert request: message: %s; buttons: Reboot (tvpower/power/reboot), Later (focused)' % APPLY)

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

    def test_bus_monitor_observes_alert_without_retaining_confirmation_token(self):
        monitor = e2e.RebootAlertMonitor(e2e.Device('unused'))
        monitor._observe({
            'type': 'call', 'transport': 'TX', 'sender': 'com.webos.lunasend-100',
            'senderUniqueName': 'client-100',
            'destination': 'com.webos.notification', 'method': 'createAlert', 'token': 7,
            'payload': {'sourceId': e2e.APP_ID, 'title': 'Home Chooser', 'message': self.APPLY,
                        'buttons': [{'label': 'Reboot', 'onclick': e2e.TVPOWER_REBOOT_URI,
                                     'params': {'reason': 'remoteKey'}},
                                    {'label': 'Later', 'focus': True}]}})
        monitor._observe({'type': 'return', 'transport': 'TX', 'sender': 'com.webos.notification',
                          'destination': 'com.webos.lunasend-100', 'destinationUniqueName': 'client-100',
                          'replyToken': 7,
                          'payload': {'returnValue': True, 'alertId': 'alert-7'}})
        info = monitor.snapshot()
        self.assertEqual((info['message'], info['buttons'], info['alert'], info['alert_id']),
                         (self.APPLY, ['Reboot', 'Later'], 'accepted', 'alert-7'))
        self.assertNotIn('private-token', json.dumps(info))
        self.assertNotIn('private-token', monitor.diagnostic())

    def test_bus_monitor_rejects_foreign_or_unbound_alerts(self):
        monitor = e2e.RebootAlertMonitor(e2e.Device('unused'))
        monitor._observe({'type': 'return', 'transport': 'TX', 'sender': 'com.webos.notification',
                          'destination': 'com.webos.lunasend-100', 'replyToken': 9,
                          'payload': {'returnValue': True, 'alertId': 'foreign'}})
        monitor._observe({'type': 'call', 'transport': 'TX', 'sender': 'com.webos.lunasend-100',
                          'destination': 'com.webos.notification', 'method': 'createAlert', 'token': 8,
                          'payload': {'sourceId': 'another.app'}})
        self.assertIsNone(monitor.snapshot()['alert'])

    def test_bus_monitor_records_refused_alert_and_accepted_toast(self):
        monitor = e2e.RebootAlertMonitor(e2e.Device('unused'))
        for token, method in ((1, 'createAlert'), (2, 'createToast')):
            payload = {'sourceId': e2e.APP_ID, 'message': self.APPLY}
            if method == 'createAlert':
                payload.update(title='Home Chooser', message=self.APPLY, buttons=[
                    {'label': 'Reboot', 'onclick': e2e.TVPOWER_REBOOT_URI,
                     'params': {'reason': 'remoteKey'}}, {'label': 'Later', 'focus': True}])
            monitor._observe({'type': 'call', 'transport': 'TX', 'sender': 'com.webos.lunasend-100',
                              'destination': 'com.webos.notification', 'method': method,
                              'token': token, 'payload': payload})
            monitor._observe({'type': 'return', 'transport': 'TX', 'sender': 'com.webos.notification',
                              'destination': 'com.webos.lunasend-100', 'replyToken': token,
                              'payload': {'returnValue': method == 'createToast'}})
        self.assertEqual((monitor.snapshot()['alert'], monitor.snapshot()['toast']),
                         ('refused', 'accepted'))

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
        ok = {'returnValue': True, 'apiVersion': 1, 'version': '0.1.0', 'root': True, 'permission': e2e.API_GROUP,
              'permitted': True, 'isSelectedHome': False}
        self.assertEqual(e2e.api_reply_problems('getInfo', ok), [])
        bad = dict(ok, apiVersion=2)
        del bad['isSelectedHome']
        p = e2e.api_reply_problems('getInfo', bad)
        self.assertTrue(any('isSelectedHome' in x for x in p) and any('apiVersion' in x for x in p), p)
        self.assertTrue(e2e.api_reply_problems('getInfo', dict(ok, root='yes')))
        self.assertTrue(e2e.api_reply_problems('getInfo', dict(ok, permission='other')))
        self.assertEqual(e2e.api_reply_problems('getInfo', dict(ok, futureField=1)), [])
        self.assertTrue(e2e.api_reply_problems('getInfo', dict(ok, version=3)))

    def test_list_and_running_contract(self):
        apps = {'returnValue': True, 'apiVersion': 1, 'apps': [
            {'id': 'a', 'title': 'A', 'icon': 'i.png', 'folderPath': '/x', 'removable': True, 'visible': True}]}
        self.assertEqual(e2e.api_reply_problems('listApps', apps), [])
        leak = {'returnValue': True, 'apiVersion': 1, 'apps': [{'id': 'a', 'requiredPermissions': []}]}
        self.assertEqual(e2e.api_reply_problems('listApps', leak), [])
        self.assertTrue(e2e.api_reply_problems('listApps', {'returnValue': True, 'apiVersion': 1, 'apps': [{'id': 'a', 'removable': 'yes'}]}))
        self.assertTrue(e2e.api_reply_problems('listApps', {'returnValue': True, 'apiVersion': 1}))
        run = {'returnValue': True, 'apiVersion': 1, 'running': [{'id': 'a', 'instanceId': 'x', 'processId': 12,
                                                                  'webProcessId': 34}]}
        self.assertEqual(e2e.api_reply_problems('listRunning', run), [])
        self.assertEqual(e2e.api_reply_problems('listRunning', dict(run, running=[dict(run['running'][0], futureField=5)])), [])
        self.assertTrue(e2e.api_reply_problems('listRunning', dict(run, running=[{'id': 'a', 'processId': '12'}])))
        self.assertTrue(e2e.api_reply_problems('listRunning', {'returnValue': True, 'apiVersion': 1, 'running': [{}]}))

    def test_close_and_uninstall_reply_contract(self):
        close = {'returnValue': True, 'apiVersion': 1, 'id': 'a'}
        self.assertEqual(e2e.api_reply_problems('closeApp', dict(close, later=1)), [])
        self.assertTrue(e2e.api_reply_problems('closeApp', {'returnValue': True, 'id': 'a'}))
        self.assertEqual(e2e.api_reply_problems('uninstallApp', dict(close, pending=True, confirmation='alert')), [])
        self.assertTrue(e2e.api_reply_problems('uninstallApp', dict(close, pending=True, confirmation='bad')))

    def test_new_launcher_replies(self):
        front = {'returnValue': True, 'apiVersion': 1, 'appId': '', 'processId': 123}
        self.assertEqual(e2e.api_reply_problems('getForegroundApp', dict(front, futureField=True)), [])
        self.assertTrue(e2e.api_reply_problems('getForegroundApp', dict(front, processId='123')))
        app = {'returnValue': True, 'apiVersion': 1,
               'app': {'id': 'a', 'appCategory': ['home'], 'removable': True}}
        self.assertEqual(e2e.api_reply_problems('getAppInfo', dict(app, futureField=True)), [])
        self.assertTrue(e2e.api_reply_problems('getAppInfo', dict(app, app={'id': 'a', 'appCategory': 'home'})))
        icon = {'returnValue': True, 'apiVersion': 1, 'mimeType': 'image/png',
                'data': 'YWJj', 'byteLength': 3}
        self.assertEqual(e2e.api_reply_problems('getAppIcon', dict(icon, futureField=True)), [])
        self.assertTrue(e2e.api_reply_problems('getAppIcon', dict(icon, byteLength=4)))
        self.assertTrue(e2e.api_reply_problems('getAppIcon', dict(icon, data='not-base64')))

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


class KnownDvbCrash(unittest.TestCase):
    """Real 9.2.4 crash and popup shapes from the 2026-10-25 and 2026-10-26 runs."""
    CRASHD = '2026-10-24T19:14:39.699944Z [38.504729568] user.info crashd [] crashd CRASHED {"process":"legacy-broadcas.4892"} '
    FAULT = {"faultType": "crash", "faultContent": "RDXDErr3_legacy-broadcas__Linux Crash: legacy-broadcas crashes at location "
             "\u0002usr\u0002sbin\u0002legacy-broadcast-dvb (API_SI_GetLinkageUriInfo+0x197) [0xf9efdc]"
             "____legacy-broadcas.4892.YOpk2V.gz", "faultTime": "38.523512976"}
    NOISE = {"faultType": "general", "faultContent": "keyCheck2_4433_sysfaultdetector", "faultTime": "28.193528519"}
    ALERT = ('2026-10-26T02:17:10.914382Z [346.705637182] user.info faultmanager [] '
             'faultmanager FAULT_STATUS {} [sendCreateAlert:513] title : '
             'RDXDErr3_legacy-broadcas__Linux Crash: legacy-broadcas crashes at location '
             '^Busr^Bsbin^Blegacy-broadcast-dvb (API_SI_GetLinkageUriInfo+0x197) [0xcdefdc]'
             '____legacy-broadcas.7453.Lw0EVv.gz, message : [crash] Fault Occured.')

    def reply(self, *faults):
        return json.dumps({'returnValue': True, 'faultDetectedStatus': {'detected': len(faults), 'detectedFaults': list(faults)}})

    def test_known_crash_is_noted_not_failed(self):
        self.assertEqual(e2e.crash_lines(self.CRASHD), [self.CRASHD])
        known, other = e2e.split_known_crashes([self.CRASHD], self.reply(self.NOISE, self.FAULT))
        self.assertEqual((known, other), ([self.CRASHD], []))
        self.assertEqual(list(e2e.known_fault_pids(self.reply(self.NOISE, self.FAULT))), ['4892'])

    def test_known_faultmanager_alert_is_not_foreign(self):
        fault = dict(self.FAULT, faultContent=self.FAULT['faultContent'].replace('4892', '7453'))
        self.assertEqual(e2e.split_known_crashes([self.ALERT], self.reply(fault)), ([self.ALERT], []))
        self.assertEqual(e2e.split_known_crashes([self.ALERT], self.reply(self.FAULT)), ([], [self.ALERT]))
        other = self.ALERT.replace('API_SI_GetLinkageUriInfo+0x197', 'API_SI_Other+0x10')
        self.assertEqual(e2e.split_known_crashes([other], self.reply(fault)), ([], [other]))

    def test_other_location_fails(self):
        f = dict(self.FAULT, faultContent=self.FAULT['faultContent'].replace('API_SI_GetLinkageUriInfo+0x197', 'API_SI_Other+0x10'))
        self.assertEqual(e2e.split_known_crashes([self.CRASHD], self.reply(f)), ([], [self.CRASHD]))

    def test_other_process_fails(self):
        web = '2026-10-23T04:26:50Z [194.2] user.info crashd [] crashd CRASHED {"process":"WebAppMgr.5823"}'
        f = dict(self.FAULT, faultContent=self.FAULT['faultContent'].replace('legacy-broadcas', 'WebAppMgr'))
        self.assertEqual(e2e.split_known_crashes([web], self.reply(self.FAULT)), ([], [web]))
        self.assertEqual(e2e.known_fault_pids(self.reply(f)), {})

    def test_other_pid_or_no_fault_entry_fails(self):
        other = self.CRASHD.replace('4892', '999')
        self.assertEqual(e2e.split_known_crashes([other], self.reply(self.FAULT)), ([], [other]))
        self.assertEqual(e2e.split_known_crashes([self.CRASHD], 'Error: timeout'), ([], [self.CRASHD]))

    def test_mixed_lines(self):
        web = 'x crashd CRASHED {"process":"WebAppMgr.5823"}'
        known, other = e2e.split_known_crashes([self.CRASHD, web], self.reply(self.FAULT))
        self.assertEqual((known, other), ([self.CRASHD], [web]))


class DismissKnownPopup(unittest.TestCase):
    """dismiss_known_popup: BACK at most once per fault; the outcome is key focus, not the fault status."""
    def make(self, fault_reply, fgs, probe=None):
        s = scenario()
        s.dev.fault_reply = fault_reply
        s.dev.foreground = lambda: (fgs.pop(0) if len(fgs) > 1 else fgs[0], '')
        s.insp = None
        s.pressed = []
        s.press = lambda *k: s.pressed.extend(k)
        s.priv_luna = lambda uri, payload='{}': s.dev.fault_reply
        if probe is not None:
            s.insp = object()
            s.ui = lambda: {'row': 'a', 'rows': ['a', 'b']}
            s.probe_keys = lambda rows, ui: probe
        return s

    def test_no_known_fault_sends_nothing(self):
        s = self.make(KnownDvbCrash().reply(KnownDvbCrash.NOISE), ['app'])
        self.assertIsNone(s.dismiss_known_popup('x'))
        self.assertEqual(s.pressed, [])

    def test_back_once_per_fault_and_focus_checked_by_foreground(self):
        s = self.make(KnownDvbCrash().reply(KnownDvbCrash.FAULT), ['app'])
        with mock_sleep():
            self.assertTrue(s.dismiss_known_popup('x'))
            self.assertIsNone(s.dismiss_known_popup('x'))  # the fault stays listed; no second BACK
        self.assertEqual(s.pressed, ['BACK'])
        self.assertEqual([ok for _, ok in s.rec.checks], [True])

    def test_focus_not_back_fails(self):
        s = self.make(KnownDvbCrash().reply(KnownDvbCrash.FAULT), ['app', 'other'])
        with mock_sleep():
            self.assertFalse(s.dismiss_known_popup('x'))
        self.assertEqual([ok for _, ok in s.rec.checks], [False])
        s = self.make(KnownDvbCrash().reply(KnownDvbCrash.FAULT), ['app'], probe=False)
        with mock_sleep():
            self.assertFalse(s.dismiss_known_popup('x'))
        self.assertEqual([ok for _, ok in s.rec.checks], [False])

    def test_page_probe_passes(self):
        s = self.make(KnownDvbCrash().reply(KnownDvbCrash.FAULT), ['app'], probe=True)
        with mock_sleep():
            self.assertTrue(s.dismiss_known_popup('x'))

    def test_other_crash_is_never_dismissed(self):
        f = dict(KnownDvbCrash.FAULT, faultContent='RDXDErr3_legacy-broadcas__x (API_SI_Other+0x1)__legacy-broadcas.4892.a.gz')
        s = self.make(KnownDvbCrash().reply(f), ['app'])
        self.assertIsNone(s.dismiss_known_popup('x'))
        self.assertEqual(s.pressed, [])


@contextlib.contextmanager
def mock_sleep():
    import unittest.mock
    with unittest.mock.patch.object(e2e.time, 'sleep', lambda *_: None):
        yield


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
                if isinstance(apps, Exception):
                    raise apps
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

    def test_ares_app_list_failure_does_not_mean_no_apps(self):
        import unittest.mock
        with unittest.mock.patch.object(e2e, 'run', return_value=(1, 'Failed to connect to vm: disconnected')):
            with self.assertRaisesRegex(RuntimeError, 'ares-install -l on vm failed'):
                e2e.Device('vm').installed()
        state, _ = self.wait(False, RuntimeError('Failed to connect'))
        self.assertEqual(state, e2e.DOWN)

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

    fault_reply = ''

    def exec(self, cmd, timeout=60):
        return 0, self.fault_reply

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

    def test_known_dvb_crash_is_a_note_not_a_fail(self):
        s = scenario()
        snap = s.compositor_snapshot()
        s.dev.messages = KnownDvbCrash.CRASHD + '\n'
        s.dev.fault_reply = KnownDvbCrash().reply(KnownDvbCrash.NOISE, KnownDvbCrash.FAULT)
        s.check_no_crash('x', snap)
        self.assertEqual([ok for _, ok in s.rec.checks], [True, True])
        self.assertTrue(any('known LG crash' in n for n in s.rec.notes), s.rec.notes)

    def test_dvb_crash_at_another_location_or_with_wam_crash_fails(self):
        for fault_reply, extra in (('', ''), (KnownDvbCrash().reply(
                dict(KnownDvbCrash.FAULT, faultContent='RDXDErr3_legacy-broadcas__x (API_SI_Other+0x1)__legacy-broadcas.4892.a.gz')), ''),
                (KnownDvbCrash().reply(KnownDvbCrash.FAULT), 'x crashd CRASHED {"process":"WebAppMgr.1"}\n')):
            s = scenario()
            snap = s.compositor_snapshot()
            s.dev.messages = KnownDvbCrash.CRASHD + '\n' + extra
            s.dev.fault_reply = fault_reply
            s.check_no_crash('x', snap)
            self.assertEqual([ok for _, ok in s.rec.checks], [False, True], fault_reply)

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
        with tempfile.TemporaryDirectory() as tmp:
            hosts = os.path.join(tmp, 'hosts.toml')
            with open(hosts, 'w') as f:
                f.write('default = "pi"\n[hosts.pi]\nssh = "raspberrypi"\n')
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = run_matrix.main(['--dry-run', '--hosts-file', hosts, '--vms', '9.x', '--api-check',
                                      '--reboot-via', 'later', '--results', tmp],
                                     lister=lambda h: ['w24g-ombre'], busy=lambda h: False)
            self.assertEqual(rc, 0, out.getvalue())
            self.assertIn('--api-check', out.getvalue())
            self.assertIn('--reboot-via later', out.getvalue())


class HostInput(unittest.TestCase):
    def test_command_quotes_arguments(self):
        from unittest.mock import Mock
        host = e2e.BeanjarHost()
        directory = "/tmp/vm ' $(false); space"
        key = "ret' ; $(false)"
        host._dirs['vm'] = directory
        host.sh = Mock(return_value=(0, 'ok'))
        self.assertEqual(host.sendkey('vm', key), (0, 'ok'))
        command, timeout = host.sh.call_args.args
        self.assertEqual(shlex.split(command),
                         ['python3', '-c', e2e.QMP_SENDKEY, directory + '/run/qmp.sock', key])
        self.assertEqual(timeout, 30)

    def test_failed_press_stops_sequence_for_both_inputs(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        for mode in ('qmp', 'ares'):
            with self.subTest(mode=mode):
                scenario = object.__new__(e2e.Scenario)
                scenario.a = SimpleNamespace(input=mode)
                scenario.vm = 'vm'
                scenario.host = SimpleNamespace(name='rk1', sendkey=Mock(return_value=(1, 'socket refused')))
                scenario.dev = SimpleNamespace(root='vm-root', key=Mock(return_value=(1, 'socket refused')))
                scenario.rec = Mock()
                with self.assertRaisesRegex(e2e.KeySendError, 'key OK.*socket refused'):
                    scenario.press('OK', 'DOWN')
                sender = scenario.host.sendkey if mode == 'qmp' else scenario.dev.key
                self.assertEqual(sender.call_count, 1)
                self.assertFalse(scenario.rec.check.call_args.args[1])

    def beanjar_host(self):
        from unittest.mock import Mock
        host = e2e.BeanjarHost(mode='remote', name='rk1')
        host.input_rec = Mock()
        keys = ['ENTER', 'BACK', 'HOME', 'UP', 'DOWN', 'LEFT', 'RIGHT'] + list('0123456789')
        layout = json.dumps({'keys': [{'name': k, 'code': i} for i, k in enumerate(keys)]})
        host.display_beanjar = Mock(side_effect=lambda *a, **kw:
                                    (0, layout if a[:2] == ('remote', 'layout') else 'ok'))
        return host

    def test_beanjar_mapping_layout_once_and_scaled_hold(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        host = self.beanjar_host()
        s = object.__new__(e2e.Scenario)
        s.a, s.host, s.vm, s.rec = SimpleNamespace(input='beanjar'), host, 'vm', Mock()
        with patch.object(e2e, 'SLOWDOWN', 1.5), patch.object(e2e, 'sleep'):
            s.press('OK', 'ENTER', 'BACK', 'HOME', 'UP', 'DOWN', 'LEFT', 'RIGHT', *'0123456789')
        calls = [c.args for c in host.display_beanjar.call_args_list]
        self.assertEqual(calls[0], ('remote', 'layout', 'rk1/vm', '--json'))
        self.assertEqual(sum(c[:2] == ('remote', 'layout') for c in calls), 1)
        self.assertEqual([c[3] for c in calls[1:]],
                         ['ENTER', 'ENTER', 'BACK', 'HOME', 'UP', 'DOWN', 'LEFT', 'RIGHT', *'0123456789'])
        self.assertTrue(all(c[-2:] == ('--hold', '150') for c in calls[1:]))
        host.mode = 'local'
        self.assertEqual(host.remote_target('vm'), 'vm')

    def test_beanjar_missing_and_dimmed_keys_fail_before_press(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        for rows, key in [([{'name': 'HOME', 'code': 1}], 'OK'),
                          ([{'name': k, 'code': None if k == 'ENTER' else 1}
                            for k in ['ENTER', 'BACK', 'HOME', 'UP', 'DOWN', 'LEFT', 'RIGHT']], 'OK')]:
            host = self.beanjar_host()
            host.display_beanjar.return_value = (0, json.dumps({'keys': rows}))
            host.display_beanjar.side_effect = None
            s = object.__new__(e2e.Scenario)
            s.a, s.host, s.vm, s.rec = SimpleNamespace(input='beanjar'), host, 'vm', Mock()
            with self.assertRaisesRegex(e2e.KeySendError, 'rk1/vm.*missing keys.*ENTER'):
                s.press(key)
            self.assertEqual(host.display_beanjar.call_count, 1)
            self.assertFalse(s.rec.check.call_args.args[1])

    def test_beanjar_press_failure_stops_sequence(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        host = self.beanjar_host()
        host.remote_press('vm', 'HOME')  # cache the real-shaped layout
        host.display_beanjar.reset_mock()
        host.display_beanjar.side_effect = None
        host.display_beanjar.return_value = (1, 'display refused')
        s = object.__new__(e2e.Scenario)
        s.a, s.host, s.vm, s.rec = SimpleNamespace(input='beanjar'), host, 'vm', Mock()
        with self.assertRaisesRegex(e2e.KeySendError, 'key OK.*ENTER.*rk1/vm.*display refused'):
            s.press('OK', 'DOWN')
        self.assertEqual(host.display_beanjar.call_count, 1)
        self.assertFalse(s.rec.check.call_args.args[1])

    def test_vnc_boot_stop_order_and_duplicate_start(self):
        from unittest.mock import Mock
        host = self.beanjar_host()
        events = []
        host.running_vms = lambda: []
        host.beanjar = lambda *a, **kw: (events.append(a) or (0, 'ok'))
        host.display_beanjar = Mock(side_effect=lambda *a, **kw: (events.append(a) or (0, 'ok')))
        host.start('vm')
        host.stop('vm')
        host.start('vm')
        host.vnc_stop('vm')  # final cleanup, including when the VM is left running
        self.assertEqual([e[:2] for e in events], [
            ('start', 'vm'), ('vnc', 'start'), ('vnc', 'stop'), ('stop', 'vm'),
            ('start', 'vm'), ('vnc', 'start'), ('vnc', 'stop')])
        host.display_beanjar.side_effect = None
        host.display_beanjar.return_value = (1, 'beanjar-vnc is already serving vm (pid 123)')
        host.vnc_start('vm')
        host.input_rec.check.assert_not_called()
        host.display_beanjar.return_value = (1, 'cannot bind')
        with self.assertRaisesRegex(e2e.KeySendError, 'cannot bind'):
            host.vnc_start('vm')
        host.stop('vm')  # even failed VNC cleanup must still stop the VM
        self.assertEqual(events[-1], ('stop', 'vm'))

    def test_await_boot_restarts_vnc_before_returning_to_keys(self):
        from unittest.mock import Mock, patch
        s = object.__new__(e2e.Scenario)
        s.vm, s.host, s.rec = 'vm', self.beanjar_host(), Mock()
        s.host.info = Mock(return_value={'lan_address': 'example'})
        s.dev = Mock()
        s.dev.boot_id.return_value = 'new'
        with patch.object(e2e, 'ensure_devices'), patch.object(e2e, 'wait_for_device', return_value=e2e.READY):
            self.assertTrue(s.await_boot('tvpower reboot', 'old'))
        s.host.display_beanjar.assert_called_once_with('vnc', 'start', 'rk1/vm', '--port', '0')

    def test_main_cleans_vnc_on_failure_interrupt_and_no_stop(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        for failure, no_stop in [(RuntimeError('scenario failed'), False),
                                 (KeyboardInterrupt(), False), (None, True)]:
            with self.subTest(failure=type(failure).__name__, no_stop=no_stop):
                host = self.beanjar_host()
                events = []
                host.running_vms = lambda: []
                host.info = Mock(return_value={'lan_address': 'example'})
                host.beanjar = lambda *a, **kw: (events.append(a) or (0, 'ok'))
                host.display_beanjar = Mock(side_effect=lambda *a, **kw: (events.append(a) or (0, 'ok')))
                rec = Mock()
                rec.finish.return_value = {}
                scenario = Mock(insp=None, viewer_opened=False)
                scenario.run.side_effect = failure
                argv = ['runner', '--vm', '9.x', '--outdir', 'unused'] + (['--no-stop'] if no_stop else [])
                with contextlib.ExitStack() as stack:
                    stack.enter_context(patch.object(sys, 'argv', argv))
                    for name in ['prepare_vm', 'ensure_devices', 'clean_launchers', 'restart_after_launcher_cleanup']:
                        stack.enter_context(patch.object(e2e, name))
                    stack.enter_context(patch.object(e2e, 'wait_for_device', return_value=e2e.READY))
                    stack.enter_context(patch.object(e2e, 'load_hosts', return_value={'default': 'rk1', 'paths': {}}))
                    stack.enter_context(patch.object(e2e, 'make_launcher', return_value=SimpleNamespace(spec='test', id='test')))
                    stack.enter_context(patch.object(e2e.BeanjarHost, 'from_config', return_value=host))
                    stack.enter_context(patch.object(e2e, 'Recorder', return_value=rec))
                    stack.enter_context(patch.object(e2e, 'Scenario', return_value=scenario))
                    stack.enter_context(patch.object(e2e.os, 'makedirs'))
                    stack.enter_context(patch.object(e2e.shutil, 'which', return_value='/fake/ares-do'))
                    stack.enter_context(patch.object(e2e.signal, 'signal'))
                    if isinstance(failure, KeyboardInterrupt):
                        with self.assertRaises(KeyboardInterrupt):
                            e2e.main()
                    else:
                        e2e.main()
                self.assertEqual([e[:2] for e in events], [('start', 'w24g-ombre'),
                                 ('vnc', 'start'), ('vnc', 'stop')] +
                                 ([] if no_stop else [('stop', 'w24g-ombre')]))

    def test_qmp_protocol_and_errors(self):
        import socket
        import threading
        for failure in (None, 'capabilities', 'key', 'monitor', 'disconnect'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                path = os.path.join(directory, 'qmp.sock')
                errors = []
                requests = []
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                    server.bind(path)
                    server.listen(1)
                    server.settimeout(5)
                    def serve():
                        try:
                            with server.accept()[0] as client:
                                client.settimeout(5)
                                with client.makefile('rb') as stream:
                                    client.sendall(b'{"QMP": {}}\n')
                                    for ident in ('capabilities', 'key'):
                                        request = json.loads(stream.readline())
                                        requests.append(request)
                                        if failure == 'disconnect':
                                            return
                                        response = {'id': ident, 'return': { } if ident == 'capabilities' else ''}
                                        if failure == ident:
                                            response = {'id': ident, 'error': {'desc': 'denied'}}
                                        if failure == 'monitor' and ident == 'key':
                                            response['return'] = 'unknown key'
                                        client.sendall(b'{"event":"TEST"}\n' + (json.dumps(response) + '\n').encode())
                                        if failure == ident:
                                            return
                        except Exception as exc:
                            errors.append(exc)
                    thread = threading.Thread(target=serve)
                    thread.start()
                    host = e2e.BeanjarHost(mode='local')
                    host._dirs['vm'] = directory
                    os.mkdir(os.path.join(directory, 'run'))
                    os.rename(path, os.path.join(directory, 'run', 'qmp.sock'))
                    try:
                        rc, output = host.sendkey('vm', 'meta_l')
                    finally:
                        thread.join(6)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(errors, [])
                    self.assertEqual(rc == 0, failure is None, output)
                    self.assertEqual(requests[0]['execute'], 'qmp_capabilities')
                    if failure in (None, 'key', 'monitor'):
                        self.assertEqual(requests[1]['arguments'], {'command-line': 'sendkey meta_l'})

    def test_unreachable_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            host = e2e.BeanjarHost(mode='local')
            host._dirs['vm'] = directory
            rc, output = host.sendkey('vm', 'ret')
            self.assertNotEqual(rc, 0)
            self.assertIn('QMP sendkey failed', output)


if __name__ == '__main__':
    unittest.main()
