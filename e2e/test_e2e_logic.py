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
import signal
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hc_e2e.alerts  # noqa: E402
import hc_e2e.api_spec  # noqa: E402
import hc_e2e.beanjar  # noqa: E402
import hc_e2e.checks  # noqa: E402
import hc_e2e.cli  # noqa: E402
import hc_e2e.constants  # noqa: E402
import hc_e2e.device  # noqa: E402
import hc_e2e.evidence  # noqa: E402
import hc_e2e.inspector  # noqa: E402
import hc_e2e.launchers  # noqa: E402
import hc_e2e.level3  # noqa: E402
import hc_e2e.plan  # noqa: E402
import hc_e2e.preflight  # noqa: E402
import hc_e2e.recorder  # noqa: E402
import hc_e2e.releases  # noqa: E402
import hc_e2e.scenario  # noqa: E402
import hc_e2e.steps.api  # noqa: E402
import hc_e2e.steps.panel  # noqa: E402
import hc_e2e.steps.state  # noqa: E402
import hc_e2e.timing  # noqa: E402
import run_matrix  # noqa: E402

REPO = os.path.dirname(HERE)
FULL, HOME = hc_e2e.constants.FULLHOME, hc_e2e.constants.STOCK


class ClickInput(unittest.TestCase):
    def frame(self):
        return {'width': 1280, 'height': 720, 'elements': [
            {'text': 'Use as Home', 'kind': 'button', 'box': [10, 20, 110, 60], 'centre': [60, 40]}]}

    def test_exact_role_region_and_centre(self):
        f = self.frame()
        self.assertEqual(hc_e2e.alerts.match_element(f, 'Use as Home', 'button', [0, 0, 120, 80])['centre'], [60, 40])
        self.assertEqual(hc_e2e.alerts.match_element(f, role='button')['text'], 'Use as Home')

    def test_missing_ambiguous_and_nonexact(self):
        f = self.frame()
        for text in ('Use', 'use as home', 'Cancel'):
            with self.assertRaises(ValueError):
                hc_e2e.alerts.match_element(f, text)
        f['elements'] *= 2
        with self.assertRaises(ValueError):
            hc_e2e.alerts.match_element(f, 'Use as Home')
        with self.assertRaises(ValueError):
            hc_e2e.alerts.match_element(self.frame(), role='text')
        with self.assertRaises(ValueError):
            hc_e2e.alerts.match_element(self.frame(), role='button', region=[200, 200, 300, 300])

    def test_launcher_icon_and_label_require_role(self):
        f = self.frame()
        f['elements'].append(dict(f['elements'][0], kind='icon'))
        with self.assertRaises(ValueError):
            hc_e2e.alerts.match_element(f, 'Use as Home')
        self.assertEqual(hc_e2e.alerts.match_element(f, 'Use as Home', ('text', 'button'))['kind'], 'button')

    def test_every_vm_argument_is_host_qualified(self):
        """A name that exists on several beanjar hosts must never reach beanjar bare."""
        from unittest.mock import Mock, patch
        for mode in ('local', 'remote'):
            h = hc_e2e.beanjar.BeanjarHost(mode=mode, name='pi')
            h.sh = Mock(return_value=(0, 'dir: /d\n'))
            h.running_vms = Mock(return_value=[])
            h.fetch = Mock(return_value=True)
            h.display_beanjar = Mock(return_value=(0, 'ok'))
            with patch.object(hc_e2e.beanjar, 'close_sessions'):
                h.info('w22o-queue-us')
                h.settings('w22o-queue-us')
                h.set_settings('w22o-queue-us', link='lan')
                h.start('w22o-queue-us')
                h.stop('w22o-queue-us')
                h.viewer_open('w22o-queue-us')
                h.viewer_close('w22o-queue-us')
                h.tv_screenshot('w22o-queue-us', '/tmp/x.png')
            cmds = [c.args[0] for c in h.sh.call_args_list if c.args[0].startswith('beanjar ')]
            self.assertEqual(len(cmds), 8, cmds)
            for cmd in cmds:
                self.assertIn('local/w22o-queue-us', cmd.split(), cmd)
                self.assertNotIn('w22o-queue-us', [w for w in cmd.split()], cmd)
            self.assertIn('beanjar start local/w22o-queue-us', cmds)
            self.assertIn('beanjar stop local/w22o-queue-us', cmds)

    def test_click_command_local_and_host(self):
        from unittest.mock import Mock
        for mode, target in [('local', 'local/vm'), ('remote', 'pi/vm')]:
            h = hc_e2e.beanjar.BeanjarHost(mode=mode, name='pi')
            h.display_beanjar = Mock(return_value=(0, 'ok'))
            h.remote_click('vm', [60, 40], self.frame())
            h.display_beanjar.assert_called_once_with('remote', 'click', target, '60', '40',
                                                      '--frame', '1280x720', '--json')

    def test_unavailable_click_logs_and_caches_fallback(self):
        from unittest.mock import Mock
        from types import SimpleNamespace
        h = hc_e2e.beanjar.BeanjarHost()
        h.beanjar, h.input_rec = Mock(return_value=(2, 'unknown command click')), Mock()
        s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
        s.a, s.host = SimpleNamespace(input_mode='click'), h
        self.assertFalse(s.use_click())
        self.assertFalse(s.use_click())
        h.beanjar.assert_called_once()
        h.input_rec.note.assert_called_once()
        s.a.input_mode = 'keys'
        self.assertFalse(s.use_click())

    def test_coordinate_mapping_logged_once_across_scenarios(self):
        from unittest.mock import Mock
        host = hc_e2e.beanjar.BeanjarHost()
        host.display_beanjar = Mock(return_value=(0, json.dumps(self.frame())))
        rec = Mock()
        for _ in range(2):
            s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
            s.host, s.vm, s.rec = host, 'vm', rec
            s.elements('test')
        rec.note.assert_called_once()

    def test_click_effect_and_fresh_read(self):
        from unittest.mock import Mock
        s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
        s.host, s.rec, s.vm = Mock(), Mock(), 'vm'
        s.host.remote_click.return_value = (0, 'ok')
        s.elements = Mock(return_value=self.frame())
        s.wait = lambda fn, *a: fn()
        s.click_element('Use as Home', effect=lambda: True)
        self.assertEqual(s.elements.call_count, 2)
        with self.assertRaises(RuntimeError):
            s.click_element('Use as Home', effect=lambda: False)

    def test_confirmation_captures_transient_toast_before_ocr(self):
        from unittest.mock import Mock
        s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
        s.launcher = 'fixture'
        s.ui = Mock(side_effect=[{'toast': hc_e2e.constants.DONE_TEXT}] + [{'toast': None}] * 3)
        s.dev = Mock(sh=Mock(return_value=(0, s.launcher + '\n')))
        s.wait = lambda fn, *args: fn()

        def click(text, *, effect, label, read_after):
            self.assertEqual(text, 'Use as Home')
            self.assertFalse(read_after)
            self.assertTrue(effect())

        s.click_element = click
        self.assertEqual(s.confirm_home_choice(True), [hc_e2e.constants.DONE_TEXT])

    def test_key_confirmation_keeps_choice_and_toast_checks(self):
        from unittest.mock import Mock
        s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
        s.launcher = 'fixture'
        s.ui = Mock(return_value={'toast': hc_e2e.constants.DONE_TEXT})
        s.dev = Mock(sh=Mock(return_value=(0, s.launcher + '\n')))
        s.wait = lambda fn, *args: fn()
        s.press = Mock()
        self.assertEqual(s.confirm_home_choice(False), [hc_e2e.constants.DONE_TEXT])
        s.press.assert_called_once_with('OK')
        s.dev.sh.assert_called_once_with('cat %s 2>&1' % hc_e2e.constants.CHOICE)

    def retry(self, outcomes, closed=True):
        from unittest.mock import Mock
        s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
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
        s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
        host = SimpleNamespace(click_available=lambda: True, remote_click=Mock(return_value=(0, 'clicked')))
        s.a, s.host, s.vm, s.rec = SimpleNamespace(input_mode='click'), host, 'vm', Mock()
        s.elements, s.press = Mock(return_value=frame), Mock()
        self.assertTrue(s.press_alert(hc_e2e.constants.REBOOT_PROMPT['apply'], 'Reboot', 'Later', 'test',
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
            self.assertTrue(hc_e2e.preflight.foreign_launcher({'id': 'example.launcher', 'appCategory': category}))
        for ident in (hc_e2e.constants.APP_ID, 'org.webosbrew.hbchannel', 'org.webosbrew.hcfixture.web.normal'):
            self.assertFalse(hc_e2e.preflight.foreign_launcher({'id': ident, 'appCategory': ['home']}))
        for category in (None, [], ['browser'], 'homebrew'):
            self.assertFalse(hc_e2e.preflight.foreign_launcher({'id': 'example.app', 'appCategory': category}))

    def test_known_takeover_without_home_category(self):
        self.assertEqual(hc_e2e.preflight.launcher_removal_reason({'id': 'com.homebrew.homeback', 'appCategory': []}),
                         'known HOME takeover app id')
        self.assertIsNone(hc_e2e.preflight.launcher_removal_reason({'id': 'example.app', 'title': 'HomeBack manual'}))

    def test_observed_partridge_launcher_without_home_category(self):
        self.assertEqual(hc_e2e.preflight.launcher_removal_reason({'id': 'works.partridge.webos-launcher',
                                                     'version': '1.0.0', 'title': 'Launcher'}),
                         'known launcher without home category')
        self.assertIsNone(hc_e2e.preflight.launcher_removal_reason({'id': 'example.app', 'title': 'Launcher'}))

    def test_owned_startup_entries_only(self):
        root = '/media/developer/apps/usr/palm/services/com.homebrew.homeback.service'
        self.assertTrue(hc_e2e.preflight.startup_entry_owned('com.homebrew.homeback', 'homeback', root + '/startup.sh', ''))
        self.assertTrue(hc_e2e.preflight.startup_entry_owned('com.homebrew.homeback', 'com.homebrew.homeback', '', 'exec ' + root + '/startup.sh'))
        for name, target, content in [('shared', '', 'exec ' + root + '/startup.sh'),
                                      ('com.homebrew.homeback', '', '# ' + root + '/startup.sh'),
                                      ('homeback', root + '.other/startup.sh', '')]:
            self.assertFalse(hc_e2e.preflight.startup_entry_owned('com.homebrew.homeback', name, target, content))

    def test_observed_homeback_remote_bootstrap_ownership(self):
        # Actual command from the retained 10.3.1 /var/lib/webosbrew/init.d/homeback.
        command = '    output=$(luna-send -n 1 -f "luna://com.homebrew.homeback.service/remote/start" \'{}\' 2>&1)\n'
        self.assertTrue(hc_e2e.preflight.startup_entry_owned('com.homebrew.homeback', 'homeback', '', command))
        for ident, name, content in [('example.app', 'homeback', command),
                                      ('com.homebrew.homeback', 'shared', command),
                                      ('com.homebrew.homeback', 'homeback', '# ' + command),
                                      ('com.homebrew.homeback', 'homeback', command.replace('/remote/start', '/status'))]:
            self.assertFalse(hc_e2e.preflight.startup_entry_owned(ident, name, '', content))

    def test_startup_cleanup_uses_installed_layout(self):
        from unittest.mock import Mock
        d, r = Mock(), Mock()
        path = '/var/lib/webosbrew/init.d/homeback'
        d.sh.side_effect = [(0, 'run-parts /var/lib/webosbrew/init.d 200>&-'), (0, path),
                            (0, '/media/developer/apps/usr/palm/services/com.homebrew.homeback.service/startup.sh'),
                            (1, 'missing target'), (0, '')]
        hc_e2e.preflight.clean_startup_entries(d, r, 'com.homebrew.homeback')
        self.assertEqual(d.sh.call_args.args[0], 'rm -- ' + path)
        self.assertTrue(r.check.call_args.args[1])

    def test_startup_cleanup_package_script_when_varlib_copy_absent(self):
        from unittest.mock import Mock
        d, r = Mock(), Mock()
        path = '/var/lib/webosbrew/init.d/homeback'
        package = '/media/developer/apps/usr/palm/services/org.webosbrew.hbchannel.service/startup.sh'
        d.sh.side_effect = [(1, 'No such file or directory'),
                            (0, '    run-parts /var/lib/webosbrew/init.d 200>&-\n'),
                            (0, path),
                            (0, '/media/developer/apps/usr/palm/services/com.homebrew.homeback.service/startup.sh'),
                            (1, 'missing target'), (0, '')]
        hc_e2e.preflight.clean_startup_entries(d, r, 'com.homebrew.homeback')
        self.assertEqual(d.sh.call_args_list[1].args[0], 'cat ' + package)
        self.assertEqual(d.sh.call_args.args[0], 'rm -- ' + path)

    def test_startup_cleanup_without_installed_layout_does_not_remove(self):
        from unittest.mock import Mock
        for replies in ([(1, 'missing'), (1, 'missing')], [(0, '# no run-parts\n')]):
            with self.subTest(replies=replies):
                d, r = Mock(), Mock()
                d.sh.side_effect = replies
                with self.assertRaises(RuntimeError):
                    hc_e2e.preflight.clean_startup_entries(d, r, 'com.homebrew.homeback')
                self.assertTrue(all(c.args[0].startswith('cat ') for c in d.sh.call_args_list))

    def test_removal_reboots_before_proceeding(self):
        from unittest.mock import Mock
        s = Mock()
        hc_e2e.preflight.restart_after_launcher_cleanup(s, [])
        s.beanjar_reboot.assert_not_called()
        hc_e2e.preflight.restart_after_launcher_cleanup(s, ['com.homebrew.homeback'])
        s.beanjar_reboot.assert_called_once()
        s.beanjar_reboot.return_value = False
        with self.assertRaises(RuntimeError):
            hc_e2e.preflight.restart_after_launcher_cleanup(s, ['example.launcher'])

    def test_removal_and_retention_notes(self):
        from unittest.mock import Mock
        d, r = Mock(), Mock()
        d.installed.side_effect = [['example.launcher', 'example.browser'], ['example.browser']]
        d.sh.side_effect = [(0, json.dumps({'id': 'example.launcher', 'version': '1', 'appCategory': ['home']})),
                            (0, json.dumps({'id': 'example.browser', 'version': '2'}))]
        d.remove.return_value = (0, 'removed')
        from unittest.mock import patch
        with patch.object(hc_e2e.preflight, 'clean_startup_entries') as cleanup:
            self.assertEqual(hc_e2e.preflight.clean_launchers(d, r), ['example.launcher'])
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
            hc_e2e.preflight.clean_launchers(d, r)
        self.assertFalse(r.check.call_args.args[1])


class AlertFocus(unittest.TestCase):
    def frame(self, name='reboot-alert'):
        with open(os.path.join(HERE, 'samples', name + '.json')) as f:
            return json.load(f)

    def parse(self, frame):
        return hc_e2e.alerts.alert_focus(frame, hc_e2e.constants.REBOOT_PROMPT['apply'], 'Reboot', 'Later')

    def test_recorded_restart_default(self):
        self.assertEqual(self.parse(self.frame()), ('Later', 'UP'))

    def test_app_title_behind_alert_is_optional(self):
        frame = self.frame()
        frame['elements'] = [r for r in frame['elements'] if r.get('text') != 'Home Chooser']
        self.assertEqual(self.parse(frame), ('Later', 'UP'))

    def test_recorded_target_focus(self):
        self.assertEqual(self.parse(self.frame('reboot-target')), ('Reboot', 'UP'))

    def test_recorded_uninstall(self):
        self.assertEqual(hc_e2e.alerts.alert_focus(self.frame('uninstall-alert'),
                         'Uninstall HC Fixture disposable?', 'Uninstall', 'Cancel'), ('Cancel', 'UP'))

    def test_bad_ownership_or_focus_fails_closed(self):
        import copy
        base = self.frame()
        for text in (hc_e2e.constants.REBOOT_PROMPT['apply'], 'Reboot', 'Later'):
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
        scenario = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
        scenario.rec = SimpleNamespace(say=lambda *a: None, note=lambda *a: None)
        scenario.PROOF_WAIT = 0
        scenario.press = lambda *a: self.fail('unproven service must send no key')
        scenario.shot = lambda *a: self.fail('service proof must precede screen capture')
        self.assertFalse(scenario.press_alert(hc_e2e.constants.REBOOT_PROMPT['apply'], 'Reboot', 'Later',
                                             'test', lambda: ('refused', False)))

    def test_recorded_service_evidence(self):
        with open(os.path.join(HERE, 'samples', 'alert-service.log')) as f:
            raw = f.read()
        reboot = lambda text: hc_e2e.alerts.alert_service_proof(text, hc_e2e.constants.REBOOT_PROMPT['apply'], 'Reboot', 'Later')
        uninstall = lambda text: hc_e2e.alerts.alert_service_proof(text, 'Uninstall HC Fixture disposable?',
                            'Uninstall', 'Cancel', hc_e2e.api_spec.DISPOSABLE_ID, 'org.webosbrew.hcfixture.web.normal')
        self.assertTrue(reboot(raw))
        self.assertTrue(uninstall(raw))
        self.assertFalse(reboot(raw.replace('accepted', 'refused')))
        self.assertFalse(reboot(raw.replace('(exit 0)', '(exit 1)')))
        self.assertFalse(reboot(raw.replace('"returnValue":true', '"returnValue":false')))
        self.assertFalse(reboot(raw.replace(hc_e2e.constants.REBOOT_PROMPT['apply'], 'different prompt')))
        self.assertFalse(uninstall(raw.replace(hc_e2e.api_spec.DISPOSABLE_ID, 'other.app')))
        self.assertFalse(uninstall(raw.replace('org.webosbrew.hcfixture.web.normal', 'other.caller')))
        self.assertFalse(uninstall(raw.replace('alert shown', 'alert refused')))
        self.assertFalse(uninstall(raw.replace('"returnValue":true', '"returnValue":false')))

    def test_service_proof_is_awaited_not_read_once(self):
        """matrix-20261102-194054/w22o-number1: the alert was on screen, its log line was not there yet."""
        from unittest.mock import patch
        from types import SimpleNamespace
        scenario = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
        scenario.rec = SimpleNamespace(say=lambda *a: None, note=lambda *a: None)
        readings = iter([('', False), ('', False), ('UNINSTALL_ASK line', True)])
        with patch.object(hc_e2e.timing, 'sleep') as slept:
            self.assertEqual(scenario.await_service_proof(lambda: next(readings)), ('UNINSTALL_ASK line', True))
        self.assertEqual(slept.call_count, 2)
        with patch.object(hc_e2e.timing, 'SLOWDOWN', 0.0):  # never proven: the last reading, after the bound
            self.assertEqual(scenario.await_service_proof(lambda: ('no line', False)), ('no line', False))

    def test_extra_alert_control_fails_closed(self):
        frame = self.frame()
        frame['elements'].append(dict(kind='button', text='LG action', box=[850,500,900,530]))  # inside the dialog rectangle
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
            scenario = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
            sent = []
            scenario.press = lambda *k: sent.extend(k)
            scenario.shot = lambda label: __file__
            scenario.rec = SimpleNamespace(say=lambda *a: None, note=lambda *a: None)
            with patch.object(hc_e2e.timing, 'run', side_effect=[(0, json.dumps(f)) for f in frames]):
                self.assertEqual(scenario.press_alert(hc_e2e.constants.REBOOT_PROMPT['apply'],
                                 'Reboot', 'Later', 'test', lambda: ('accepted service log', True)), success)
            self.assertEqual(sent, keys)

    def test_recorded_horizontal_5x(self):
        frame = self.frame('uninstall-alert-5x')
        parse = lambda: hc_e2e.alerts.alert_focus(frame, 'Uninstall HC Fixture disposable?', 'Uninstall', 'Cancel')
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
        self.assertEqual(hc_e2e.alerts.alert_focus(self.frame('uninstall-alert-11x'),
                         'Uninstall HC Fixture disposable?', 'Uninstall', 'Cancel'), ('Cancel', 'UP'))

    def test_swap_across_releases(self):
        import copy
        for name, target, safe in [('uninstall-alert-11x', 'Uninstall', 'Cancel'),
                                   ('reboot-alert-8x', 'Reboot', 'Later'),
                                   ('uninstall-alert-5x', 'Uninstall', 'Cancel')]:
            before = self.frame(name)
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    hc_e2e.alerts.alert_style_swap(before, before, target, safe)
                after = copy.deepcopy(before)
                buttons = {r['text']: r for r in after['elements'] if r.get('text') in (target, safe)}
                buttons[target]['style'], buttons[safe]['style'] = buttons[safe]['style'], buttons[target]['style']
                self.assertTrue(hc_e2e.alerts.alert_style_swap(before, after, target, safe))
                # Only one side changing is not a swap.
                buttons[safe]['style'] = buttons[target]['style']
                with self.assertRaises(ValueError):
                    hc_e2e.alerts.alert_style_swap(before, after, target, safe)

    def test_centered_extra_control_rejected(self):
        frame = self.frame('uninstall-alert-11x')
        frame['elements'].append(dict(kind='button', text='Other', box=[510,320,600,340]))
        with self.assertRaises(ValueError):
            hc_e2e.alerts.alert_focus(frame, 'Uninstall HC Fixture disposable?', 'Uninstall', 'Cancel')

    def test_busy_list_beside_alert_is_not_extra_controls(self):
        """A row title behind the dialog that OCR calls a button, sharing the band vertically but lying
        beside the dialog (w22o-queue-us-rk2 'Aerial webOS screensaver'), is not an alert control."""
        frame = self.frame('uninstall-alert-11x')
        frame['elements'].append(dict(kind='button', text='Aerial webOS screensaver', box=[118, 311, 364, 335]))
        self.assertEqual(hc_e2e.alerts.alert_focus(frame, 'Uninstall HC Fixture disposable?', 'Uninstall', 'Cancel'),
                         ('Cancel', 'UP'))
        # the same kind of control inside the dialog rectangle still fails
        frame['elements'].append(dict(kind='button', text='Inside', box=[600, 320, 700, 340]))
        with self.assertRaises(ValueError):
            hc_e2e.alerts.alert_focus(frame, 'Uninstall HC Fixture disposable?', 'Uninstall', 'Cancel')

    def test_missing_real_buttons_still_fail_with_busy_list(self):
        frame = self.frame('uninstall-alert-11x')
        frame['elements'] = [r for r in frame['elements'] if r.get('text') != 'Cancel']
        frame['elements'].append(dict(kind='button', text='Aerial webOS screensaver', box=[118, 311, 364, 335]))
        with self.assertRaises(ValueError):
            hc_e2e.alerts.alert_focus(frame, 'Uninstall HC Fixture disposable?', 'Uninstall', 'Cancel')

    def test_click_not_observed_takes_diagnostics_first(self):
        from unittest.mock import Mock
        s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
        s.host, s.rec, s.vm, s.insp = Mock(), Mock(), 'vm', None
        s.host.remote_click.return_value = (0, 'ok')
        s.elements = Mock(return_value=ClickInput.frame(self))
        s.wait = lambda fn, *a: fn()
        s.shot, s.ui = Mock(), Mock(return_value={'row': 'x', 'dialog': None})
        with self.assertRaises(RuntimeError):
            s.click_element('Use as Home', effect=lambda: False, label='launcher row')
        s.shot.assert_called_once_with('launcher row-click-not-observed')
        s.ui.assert_called_once()

    def test_see_toast_recreates_dead_inspector_and_reads_screen(self):
        from unittest.mock import Mock, patch
        s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
        s.vm, s.rec, s.dev = 'vm', Mock(), Mock()
        s.host = Mock()
        s.host.remote_target.return_value = 'vm'
        s.insp = Mock(last_error='websocket handshake/read: ConnectionResetError(104)')
        s.ui = Mock(return_value=None)
        s.close_insp = Mock(side_effect=lambda: setattr(s, 'insp', None))
        s.host.display_beanjar.return_value = (0, json.dumps({'elements': [
            {'kind': 'text', 'text': 'HC Fixture is already your Home.', 'box': [1, 2, 3, 4]}]}))
        with patch.object(hc_e2e.steps.api, 'Inspector', return_value=Mock(last_error='x')) as new, patch.object(hc_e2e.timing, 'sleep'):
            text, source = s.see_toast('already your Home', 5)
        self.assertEqual(source, 'screen')
        self.assertIn('already your Home', text)
        s.close_insp.assert_called_once()
        new.assert_called_once()

    def test_see_toast_dom_source_and_failure(self):
        from unittest.mock import Mock, patch
        s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
        s.vm, s.rec, s.host = 'vm', Mock(), Mock()
        s.host.remote_target.return_value = 'vm'
        s.host.display_beanjar.return_value = (0, json.dumps({'elements': []}))
        s.insp = Mock(last_error=None)
        s.ui = Mock(return_value={'toast': 'X is already your Home.'})
        self.assertEqual(s.see_toast('already your Home', 5), ('X is already your Home.', 'DOM'))
        s.ui = Mock(return_value={'toast': None})
        with patch.object(hc_e2e.timing, 'sleep'):
            text, source = s.see_toast('already your Home', 0)
        self.assertIsNone(text)
        self.assertTrue(source.startswith('none'))

    def _reopen_scenario(self, uis, launch_rc=0):
        from unittest.mock import Mock
        s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
        s.vm, s.rec, s.launcher = 'vm', Mock(), 'org.x.launcher'
        s.dev = Mock()
        s.dev.close.return_value = (0, 'closed')
        s.dev.launch.return_value = (launch_rc, 'Launched' if not launch_rc else 'Failed to launch')
        s.dev.foreground.return_value = ('', '{}')
        s.running_ids = Mock(return_value=[])
        s.insp = Mock(last_error='websocket handshake/read: ConnectionResetError(104)')
        it = iter(uis)
        s.ui = Mock(side_effect=lambda: next(it, None))
        s.close_insp = Mock(side_effect=lambda: setattr(s, 'insp', None))
        return s

    def test_reopen_recreates_inspector_and_passes(self):
        from unittest.mock import Mock, patch
        dom = {'rows': ['org.x.launcher'], 'toast': None}
        s = self._reopen_scenario([None, None, dom, dom])
        with patch.object(hc_e2e.steps.panel, 'Inspector', side_effect=lambda vm: Mock(last_error=None)) as new, \
                patch.object(hc_e2e.timing, 'sleep'):
            info = s.reopen_panel(30)
        self.assertEqual((info['failed'], info['recreated']), (2, 1))
        self.assertEqual(new.call_count, 1)
        self.assertTrue(info['dom'])
        self.assertEqual(s.reopen_evidence(info, 'org.x.launcher', True), 'ticked rows: %r; DOM: %s' % (
            'org.x.launcher', json.dumps(dom)))

    def test_reopen_ticked_comes_from_the_dom_reads(self):
        from unittest.mock import Mock, patch
        # the tick rides along with the DOM read; a later read without it (page going away) keeps it
        dom = {'rows': ['org.x.launcher'], 'toast': None, 'ticked': 'org.x.launcher'}
        gone = {'rows': ['org.x.launcher'], 'toast': None}
        s = self._reopen_scenario([dom, gone])
        s.insp = Mock(last_error=None)
        with patch.object(hc_e2e.timing, 'sleep'):
            info = s.reopen_panel(30)
        self.assertEqual(hc_e2e.scenario.Scenario.ticked_after_reopen(info), 'org.x.launcher')
        self.assertIn("ticked:", hc_e2e.inspector.FOCUS_JS)
        self.assertEqual(hc_e2e.scenario.Scenario.ticked_after_reopen({'again': {}}), None)
        self.assertEqual(hc_e2e.scenario.Scenario.ticked_after_reopen({'again': {'ticked': ''}}), None)

    def test_gate_log_verdict(self):
        seen = {}
        self.assertFalse(hc_e2e.scenario.Scenario.gate_log_verdict(seen)[0])
        hc_e2e.scenario.Scenario.gate_log_sightings(['22:14:58 boot apply: active=no', '22:16:03 setPreloadPolicy x'], seen)
        self.assertFalse(hc_e2e.scenario.Scenario.gate_log_verdict(seen)[0])
        # the refresh mounted first: only the close / launch lines are in the watcher log
        hc_e2e.scenario.Scenario.gate_log_sightings(['22:16:03 home gate: closeByAppId com.webos.app.home: {}; stock Home close confirmed'], seen)
        ok, detail = hc_e2e.scenario.Scenario.gate_log_verdict(seen)
        self.assertTrue(ok)
        self.assertIn('closeByAppId', detail)
        # a mount line seen later wins, and the trimmed log keeps the earlier sighting
        hc_e2e.scenario.Scenario.gate_log_sightings(['22:24:18 home gate: home gate mounted over /usr/bin/x; '], seen)
        self.assertEqual(hc_e2e.scenario.Scenario.gate_log_verdict(seen), (True, '22:24:18 home gate: home gate mounted over /usr/bin/x; '))
        hc_e2e.scenario.Scenario.gate_log_sightings([], seen)
        self.assertTrue(hc_e2e.scenario.Scenario.gate_log_verdict(seen)[0])

    def test_reopen_miss_evidence_without_dom(self):
        from unittest.mock import Mock, patch
        s = self._reopen_scenario([], launch_rc=1)
        with patch.object(hc_e2e.steps.panel, 'Inspector', side_effect=lambda vm: Mock(
                last_error='no page for x; /json unreadable: ConnectionResetError')), \
                patch.object(hc_e2e.timing, 'sleep'), patch.object(hc_e2e.timing, 'seconds', return_value=0):
            info = s.reopen_panel(5)
        self.assertFalse(info['dom'])
        self.assertGreaterEqual(info['failed'], 1)
        text = s.reopen_evidence(info, None, False)
        self.assertIn('no DOM read', text)
        self.assertNotIn('DOM: {', text)
        for want in ('LAUNCH FAILED', 'ares-launch launch rc=1', 'ares-launch close rc=0', "SAM foreground: ''",
                     'in SAM running: False', 'Inspector last_error: websocket', 'reads failed: %d of %d' % (
                         info['failed'], info['reads'])):
            self.assertIn(want, text)

    def _toast_scenario(self, ui, elements):
        from unittest.mock import Mock
        s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
        s.vm, s.rec, s.host, s.dev = 'vm', Mock(), Mock(), Mock()
        s.host.remote_target.return_value = 'vm'
        s.host.display_beanjar.side_effect = elements
        s.insp = Mock(last_error='websocket handshake/read: ConnectionResetError(104)')
        s.ui = ui
        s.close_insp = Mock(side_effect=lambda: setattr(s, 'insp', None))
        return s

    def test_see_toast_recreates_inspector_every_connection_failure(self):
        from unittest.mock import Mock, patch
        errors = ['websocket handshake/read: ConnectionResetError(104)', 'no page for x in /json (forward alive)',
                  'websocket gave no frame']
        calls = []

        def ui():
            calls.append(1)
            if len(calls) > 4:
                return {'toast': 'X is already your Home.'}
            s.insp.last_error = errors[(len(calls) - 1) % 3]
            return None
        s = self._toast_scenario(ui, lambda *a, **k: (124, '[timeout]'))

        def fresh(vm):
            return Mock(last_error=None)
        with patch.object(hc_e2e.steps.api, 'Inspector', side_effect=fresh) as new, patch.object(hc_e2e.timing, 'sleep'):
            self.assertEqual(s.see_toast('already your Home', 30), ('X is already your Home.', 'DOM'))
        self.assertEqual(new.call_count, 4)  # not capped at two
        self.assertEqual(s.close_insp.call_count, 4)

    def test_see_toast_ocr_is_capped_interleaved_and_logs_rows(self):
        from unittest.mock import Mock, patch
        s = self._toast_scenario(Mock(return_value={'toast': None}), None)
        s.insp.last_error = None
        rows = json.dumps({'elements': [{'kind': 'text', 'text': 'Some other text'}, {'kind': 'button', 'text': 'B'}]})
        s.host.display_beanjar.side_effect = lambda *a, **k: (0, rows)
        clock = [1000.0]
        with patch.object(time, 'time', side_effect=lambda: clock[0]), \
                patch.object(hc_e2e.timing, 'sleep', side_effect=lambda v: clock.__setitem__(0, clock[0] + 1)):
            text, source = s.see_toast('already your Home', 10)
        self.assertIsNone(text)
        ocr_calls = s.host.display_beanjar.call_args_list
        self.assertTrue(0 < len(ocr_calls) < s.ui.call_count)  # OCR interleaved, DOM rounds dominate
        for c in ocr_calls:
            self.assertLessEqual(c.kwargs['timeout'], hc_e2e.scenario.Scenario.TOAST_OCR_TIMEOUT)
        self.assertIn('rows: Some other text', source)
        self.assertIn('timeline:', source)
        self.assertIn('OCR', source)
        self.assertIn('DOM', source)

    def test_see_toast_timeline_records_error_and_ocr_timeout(self):
        from unittest.mock import Mock, patch
        s = self._toast_scenario(Mock(return_value=None), lambda *a, **k: (124, '[timeout]'))
        clock = [0.0]
        with patch.object(time, 'time', side_effect=lambda: clock[0]), \
                patch.object(hc_e2e.steps.api, 'Inspector', side_effect=lambda vm: Mock(last_error='no page for x in /json')), \
                patch.object(hc_e2e.timing, 'sleep', side_effect=lambda v: clock.__setitem__(0, clock[0] + 2)):
            text, source = s.see_toast('already your Home', 6)
        self.assertIsNone(text)
        self.assertIn('inspector-recreate', source)
        self.assertIn('elements rc=124', source)
        self.assertIn('err=', source)

    def test_api_request_selected_replaces_inspector_not_app(self):
        from unittest.mock import Mock, patch
        s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
        s.rec, s.dev, s.vm, s.launcher = Mock(), Mock(), 'vm', 'l'
        s.state = Mock(return_value={'choice': 'l'})
        s.shot = Mock()
        order = []
        s.insp = Mock()
        s.close_insp = Mock(side_effect=lambda: (order.append('close'), setattr(s, 'insp', None)))
        s.see_toast = Mock(side_effect=lambda *a: (order.append('toast'), ('t', 'DOM'))[1])
        s.dev.launch.side_effect = lambda *a: (order.append('launch'), (0, 'ok'))[1]
        s.dev.close.side_effect = lambda *a: order.append('app-close')
        with patch.object(hc_e2e.steps.api, 'Inspector', side_effect=lambda vm: order.append('new') or Mock()):
            s.api_request(selected=True)
        self.assertEqual(order[:4], ['close', 'new', 'launch', 'toast'])

    def test_foreground_probe_rules(self):
        from unittest.mock import Mock
        overlay_event = '{"event":"foreground","windowType":"_WEBOS_WINDOW_TYPE_OVERLAY"}'
        for overlay, reply_id, ok in [(True, 'org.webosbrew.hbchannel', True),
                                      (True, 'org.webosbrew.hcfixture.web.overlay', False),
                                      (False, 'org.webosbrew.hcfixture.web.normal', True)]:
            s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
            s.rec, s.dev = Mock(), Mock()
            s.launcher = 'org.webosbrew.hcfixture.web.' + ('overlay' if overlay else 'normal')
            s.L = Mock(overlay=overlay)
            s.dev.launch.return_value = (0, 'ok')
            s.dev.foreground.return_value = ('org.webosbrew.hbchannel' if overlay else s.launcher, '{}')
            s.api = Mock(return_value=({'returnValue': True, 'apiVersion': hc_e2e.api_spec.API_VERSION, 'appId': reply_id}, '{}'))
            s.life_begin = Mock()
            s.life_events = Mock(return_value=([('foreground', overlay_event)], True))
            s.wait = lambda fn, *a: fn()
            s.probe_foreground(s.launcher)
            checks = {c.args[0]: c.args[1] for c in s.rec.check.call_args_list}
            contract = [v for k, v in checks.items() if k.endswith('contract and appId')][0]
            self.assertEqual(contract, ok, (overlay, reply_id))
            self.assertEqual(s.life_begin.called, overlay)

    def test_ls2_owned_request_and_rejections(self):
        import copy
        for target, safe, message, app, uri, params in [
                ('Reboot', 'Later', hc_e2e.constants.REBOOT_PROMPT['apply'], None,
                 hc_e2e.constants.TVPOWER_REBOOT_URI, {'reason': 'remoteKey'}),
                ('Uninstall', 'Cancel', 'Uninstall HC Fixture disposable?', hc_e2e.api_spec.DISPOSABLE_ID,
                 'luna://com.webos.appInstallService/dev/remove', {'id': hc_e2e.api_spec.DISPOSABLE_ID, 'subscribe': True})]:
            info = dict(message=message, buttons=[target, safe], alert='accepted', alert_id='owned', actions=[
                dict(label=target, onclick=uri, params=params), dict(label=safe, focus=True)])
            proof = lambda observed: hc_e2e.alerts.alert_service_proof('', message, target, safe, app, observed=observed)
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
        s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
        keys = []
        s.press = lambda key: keys.append(key)
        s.shot = lambda label: __file__
        s.rec = SimpleNamespace(say=lambda *a: None, note=lambda *a: None)
        with patch.object(hc_e2e.timing, 'run', return_value=(0, json.dumps(before))):
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
        s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
        keys, notes = [], []
        s.press = lambda key: keys.append(key)
        s.shot = lambda label: __file__
        s.rec = SimpleNamespace(say=lambda *a: None, note=lambda *a: notes.append(a))
        with patch.object(hc_e2e.timing, 'run', side_effect=[(0, json.dumps(f)) for f in (before, after)]):
            self.assertTrue(s.press_alert('Uninstall HC Fixture disposable?', 'Uninstall', 'Cancel',
                                         'test', lambda: ('owned', True)))
        self.assertEqual(keys, ['UP', 'OK'])
        self.assertTrue(any('background colour' in note for note in notes))

    def test_monitor_binds_actions_to_reply(self):
        monitor = hc_e2e.alerts.RebootAlertMonitor(None)
        def call(token, message):
            monitor._observe(dict(type='call', transport='TX', sender='client', token=token,
                                  destination='com.webos.notification', method='createAlert', payload=dict(
                                      sourceId=hc_e2e.constants.APP_ID, title='Home Chooser', message=message, buttons=[
                                          dict(label='Reboot', onclick=hc_e2e.constants.TVPOWER_REBOOT_URI, params={'reason': 'remoteKey'}),
                                          dict(label='Later', focus=True)])))
        call(1, hc_e2e.constants.REBOOT_PROMPT['apply'])
        call(2, 'foreign prompt')
        monitor._observe(dict(type='return', transport='TX', sender='com.webos.notification',
                              destination='client', replyToken=1, payload=dict(returnValue=True, alertId='first')))
        self.assertTrue(hc_e2e.alerts.alert_ls2_proof(monitor.snapshot(), hc_e2e.constants.REBOOT_PROMPT['apply'], 'Reboot', 'Later'))

    def test_input_diagnostics_include_other_processes(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            journal = os.path.join(root, 'journalctl')
            with open(journal, 'w') as f:
                f.write("#!/bin/sh\necho 'surface-manager input Home received'\n")
            os.chmod(journal, 0o755)
            with patch.object(hc_e2e.evidence, 'messages_command', return_value="printf '%s\\n' '2026-10-29T06:27:00 input stale' '2026-10-29T06:29:00 input current'"):
                command = hc_e2e.evidence.home_input_command('2026-10-29T06:28:00Z')
            result = subprocess.run(['sh', '-c', command], capture_output=True, text=True, check=True,
                                    env=dict(os.environ, PATH=root + os.pathsep + os.environ['PATH']))
            self.assertNotIn('stale', result.stdout)
            self.assertIn('input current', result.stdout)
            self.assertIn('surface-manager input Home received', result.stdout)
            self.assertIn('2026-10-29 06:28:00 UTC', command)
        self.assertEqual(hc_e2e.evidence.home_input_command(''), 'true')

    def test_failed_home_attempt_stays_failed_without_retry(self):
        from types import SimpleNamespace
        s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
        keys, commands, checks, notes = [], [], [], []
        s.launcher, s.major, s.stock_id = 'fixture.launcher', 11, hc_e2e.constants.STOCK
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
            self.assertFalse(s.home_press(hc_e2e.constants.STOCK, 'test', from_app=False))
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
            cmd = hc_e2e.evidence.messages_command(path) + " | awk '$1 >= \"b\"'"
            result = subprocess.run(['sh', '-c', cmd], capture_output=True, text=True, check=True, env=env)
            self.assertEqual(result.stdout, 'b REBOOT_ASK createAlert\nc crashd CRASHED\nd current\n')

    def test_messages_without_archives(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, 'messages')
            with open(path, 'w') as log:
                log.write('only current\n')
            result = subprocess.run(['sh', '-c', hc_e2e.evidence.messages_command(path)],
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
            with patch.object(hc_e2e.evidence, 'messages_command', return_value="printf '%s\\n' '2026-10-29T06:27:00 HomeChooser UNINSTALL_ASK {} stale'"):
                command = hc_e2e.evidence.service_log_command(mark, 'UNINSTALL_ASK')
            result = subprocess.run(['sh', '-c', command], capture_output=True, text=True,
                                    env=dict(os.environ, PATH=root + os.pathsep + os.environ['PATH']), check=True)
            self.assertEqual(result.stdout, 'HomeChooser UNINSTALL_ASK {} current\n')
        self.assertEqual(hc_e2e.evidence.service_log_command('', 'UNINSTALL_ASK'), 'true')

    def test_only_lines_after_the_mark(self):
        log = ['a closeByAppId x', 'b', 'c']
        self.assertEqual(hc_e2e.evidence.lines_after(log, 2), ['c'])
        self.assertEqual(hc_e2e.evidence.lines_after(log, 3), [])
        self.assertEqual(hc_e2e.evidence.lines_after(log, 0), log)

    def test_a_log_shorter_than_the_mark_is_a_new_boot(self):
        self.assertEqual(hc_e2e.evidence.lines_after(['x', 'y'], 50), ['x', 'y'])


class BootInput(unittest.TestCase):
    LAUNCHED = '2026-11-01 01:31:00 last input: launched com.webos.app.hdmi2 over an empty foreground: {"returnValue":true}'
    HOME = ('home', 'com.webos.app.home', 'bootd launched com.webos.app.home with boot:true')
    FAILED = '2026-11-01 01:31:00 last input: launch of com.webos.app.livetv failed: no reply; not retried this boot'

    def test_launches_are_read_from_the_log(self):
        lines = ['2026-11-01 01:30:43 setPreloadPolicy com.webos.app.home isEnabled=false: no reply',
                 '2026-11-01 01:31:00 last input: eim did not answer, using com.webos.app.livetv', self.LAUNCHED, self.FAILED]
        self.assertEqual(hc_e2e.releases.boot_input_launches(lines),
                         [('com.webos.app.hdmi2', True), ('com.webos.app.livetv', False)])
        self.assertEqual(hc_e2e.releases.boot_input_launches(['x last input: netflix in front; nothing launched this boot']), [])

    def test_one_accepted_launch_in_front(self):
        self.assertEqual(hc_e2e.releases.boot_input_verdict([self.LAUNCHED], 'com.webos.app.hdmi2', self.HOME),
                         (True, 'com.webos.app.hdmi2 launched once and in front'))

    def test_not_in_front_yet(self):
        for fg in ('', None, 'org.webosbrew.hcfixture.web.overlay', 'com.webos.app.livetv'):
            self.assertFalse(hc_e2e.releases.boot_input_verdict([self.LAUNCHED], fg, self.HOME)[0], fg)

    def test_none_two_or_failed(self):
        self.assertIn('0 last-input launches', hc_e2e.releases.boot_input_verdict([], 'com.webos.app.hdmi2', self.HOME)[1])
        self.assertIn('2 last-input launches', hc_e2e.releases.boot_input_verdict([self.LAUNCHED, self.LAUNCHED], 'com.webos.app.hdmi2', self.HOME)[1])
        self.assertEqual(hc_e2e.releases.boot_input_verdict([self.FAILED], 'com.webos.app.livetv', self.HOME),
                         (False, 'the launch of com.webos.app.livetv failed'))

    LIVETV = ('other', 'com.webos.app.livetv', 'bootd launched com.webos.app.livetv')
    FRONT = '2026-11-03 19:00:00 last input: com.webos.app.livetv in front; nothing launched this boot'

    def test_bootd_booted_another_app_nothing_launched(self):
        v = hc_e2e.releases.boot_input_verdict
        self.assertTrue(v([self.FRONT], 'com.webos.app.livetv', self.LIVETV)[0])
        self.assertFalse(v([self.FRONT], 'org.x.overlay', self.LIVETV)[0])
        self.assertIn('no "com.webos.app.livetv in front', v([], 'com.webos.app.livetv', self.LIVETV)[1])
        self.assertIn('no last input launch is wanted', v([self.LAUNCHED, self.FRONT], 'com.webos.app.livetv', self.LIVETV)[1])
        self.assertFalse(v(['x last input: other.app in front; nothing launched this boot'], 'com.webos.app.livetv', self.LIVETV)[0])

    def test_bootd_launching_the_chosen_launcher_is_the_home_path(self):
        # matrix-20261103-193122 (10.x): idleApp override, bootd's first app is the overlay launcher itself
        ov = 'org.webosbrew.hcfixture.web.overlay'
        bootd = ('other', ov, 'bootd launched ' + ov)
        v = hc_e2e.releases.boot_input_verdict
        self.assertEqual(v([self.LAUNCHED], 'com.webos.app.hdmi2', bootd, ov),
                         (True, 'com.webos.app.hdmi2 launched once and in front'))
        self.assertFalse(v([self.LAUNCHED], 'com.webos.app.hdmi2', bootd)[0])  # launcher not named: other app
        self.assertFalse(v([], 'com.webos.app.hdmi2', bootd, ov)[0])

    def test_home_boot_keeps_one_launch_and_unknown_fails(self):
        v = hc_e2e.releases.boot_input_verdict
        self.assertFalse(v([self.FRONT], 'com.webos.app.livetv', self.HOME)[0])
        for bootd in (None, ('unknown', '', 'no first-app launch call')):
            ok, why = v([self.LAUNCHED], 'com.webos.app.hdmi2', bootd)
            self.assertFalse(ok)
            self.assertIn('unknown', why)


class StockRecord(unittest.TestCase):
    def test_uninstall_checks_every_owned_cache_and_access_path(self):
        paths = hc_e2e.constants.uninstall_remnant_paths()
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
            self.assertTrue(hc_e2e.releases.stock_record_ok(5, home))
            self.assertTrue(hc_e2e.releases.stock_record_ok(5, home, FULL))  # the id is not pinned on 5.x
        for home in (None, '', 'null', 'org.example.launcher'):
            self.assertFalse(hc_e2e.releases.stock_record_ok(5, home))

    def test_6_to_9_require_the_stock_id_not_null(self):
        for major in (6, 7, 8, 9):
            self.assertTrue(hc_e2e.releases.stock_record_ok(major, HOME, HOME))
            self.assertTrue(hc_e2e.releases.stock_record_ok(major, FULL, FULL))
            self.assertFalse(hc_e2e.releases.stock_record_ok(major, FULL, HOME), 'the resolved id, exactly')
            self.assertFalse(hc_e2e.releases.stock_record_ok(major, HOME, FULL))
            for home in (None, '', 'null', 'org.example.launcher'):
                self.assertFalse(hc_e2e.releases.stock_record_ok(major, home, HOME), (major, home))
            # stock id not resolved: either stock launcher, never null
            self.assertTrue(hc_e2e.releases.stock_record_ok(major, HOME))
            self.assertFalse(hc_e2e.releases.stock_record_ok(major, None))

    def test_10_and_later_require_null_or_unset(self):
        for major in (10, 11, 12):
            for home in (None, '', 'null'):
                self.assertTrue(hc_e2e.releases.stock_record_ok(major, home), (major, home))
            for home in (HOME, FULL, 'org.example.launcher'):
                self.assertFalse(hc_e2e.releases.stock_record_ok(major, home), (major, home))

    def test_platform_stock_after_a_removal_race(self):
        stock_idle = {'support': True, 'appId': HOME}
        launcher_idle = {'support': True, 'appId': 'org.example.launcher'}
        self.assertTrue(hc_e2e.releases.platform_stock(11, None, None, 'no', stock_idle))
        self.assertFalse(hc_e2e.releases.platform_stock(11, 'org.example.launcher', None, 'no', stock_idle))
        self.assertFalse(hc_e2e.releases.platform_stock(11, None, None, 'yes', stock_idle), 'override still there')
        self.assertFalse(hc_e2e.releases.platform_stock(11, None, None, 'no', launcher_idle), 'idleApp names the launcher')
        self.assertFalse(hc_e2e.releases.platform_stock(10, None, None, 'no', None), 'idleApp unreadable')
        self.assertTrue(hc_e2e.releases.platform_stock(9, HOME, HOME, 'no', None), 'no override or idleApp before 10')
        self.assertFalse(hc_e2e.releases.platform_stock(9, 'org.example.launcher', HOME, 'no', None))
        self.assertTrue(hc_e2e.releases.platform_stock(5, FULL, None, 'no', None))

    def test_stock_home_id_resolution(self):
        self.assertEqual(hc_e2e.releases.stock_home_id(9, True), FULL)
        self.assertEqual(hc_e2e.releases.stock_home_id(6, False), HOME)
        self.assertEqual(hc_e2e.releases.stock_home_id(5, True), FULL)
        self.assertIsNone(hc_e2e.releases.stock_home_id(10, True))
        self.assertIsNone(hc_e2e.releases.stock_home_id(12, False))

    def test_baseline_on_6_to_9_accepts_unset_or_a_left_stock_id(self):
        for home in (None, '', 'null', HOME, FULL):
            self.assertTrue(hc_e2e.releases.stock_baseline_ok(8, home))
        self.assertFalse(hc_e2e.releases.stock_baseline_ok(8, 'org.example.launcher'))
        self.assertFalse(hc_e2e.releases.stock_baseline_ok(10, HOME))
        self.assertFalse(hc_e2e.releases.stock_baseline_ok(5, None))


class RebootDialog(unittest.TestCase):
    def test_no_release_needs_a_reboot_after_apply(self):
        for major in (6, 7, 8, 9):
            self.assertFalse(hc_e2e.releases.expects_reboot_dialog(major, False))
            self.assertFalse(hc_e2e.releases.expects_reboot_dialog(major, True), 'mounted: a live switch')
        for major in (5, 10, 11, 12):
            self.assertFalse(hc_e2e.releases.expects_reboot_dialog(major, False))

    def test_parse_status(self):
        out = 'data=missing\nactive=no\npatch=possible\nreboot=needed\n'
        st = hc_e2e.evidence.parse_status(out)
        self.assertEqual((st['active'], st['patch'], st['reboot']), ('no', 'possible', 'needed'))
        st = hc_e2e.evidence.parse_status('active=yes\npatch=possible\nreboot=no')
        self.assertEqual((st['active'], st['reboot']), ('yes', 'no'))
        self.assertEqual(hc_e2e.evidence.parse_status('some log line = with spaces\n'), {})


class Texts(unittest.TestCase):
    def read(self, rel):
        with open(os.path.join(REPO, rel), encoding='utf-8') as f:
            return f.read()

    def test_every_asserted_text_is_in_its_source(self):
        for text, rel in hc_e2e.constants.TEXT_SOURCES:
            self.assertIn(text, self.read(rel), '%r not in %s' % (text, rel))

    def test_device_text_forms_match_utf8_and_minified_unicode_escape(self):
        note = hc_e2e.constants.RESTART_NOTE['apply']
        literal, escaped = hc_e2e.releases.device_text_forms(note)
        self.assertIn(' — ', literal)
        self.assertIn(' \\u2014 ', escaped)
        self.assertEqual(escaped.replace('\\u2014', '—'), literal)
        self.assertEqual(hc_e2e.releases.device_text_forms(hc_e2e.constants.RESTART_NOTE['remove']),
                         (hc_e2e.constants.RESTART_NOTE['remove'],))

    def test_the_new_texts(self):
        self.assertEqual(hc_e2e.constants.UNINSTALL_TOAST, 'Home Chooser was removed. The Home button opens LG Home again.')
        self.assertEqual(hc_e2e.constants.RESTART_NOTE['remove'], 'LG Home is on the Home button now. A restart removes the hook.')
        self.assertEqual(hc_e2e.constants.REBOOT_PROMPT, {'apply': 'Restart the TV to finish setting up the Home button.',
                                             'remove': 'Restart the TV to finish removing the Home button patch.'})
        self.assertNotIn('restart', (hc_e2e.constants.REMOVAL_ALERT % 'Launcher').lower())
        self.assertNotIn('restart', hc_e2e.constants.UNINSTALL_TOAST.lower())

    def test_the_old_restart_wording_is_gone_from_the_product(self):
        for rel in ('service/src/watcher/notify.c', 'service/src/watcher/restore.c', 'app/src/views/useHomeChooser.js', 'app/src/views/MainPanel.js',
                    'service/src/policy.c'):
            body = self.read(rel)
            for old in hc_e2e.constants.OLD_RESTART_TEXTS:
                self.assertNotIn(old, body, '%r still in %s' % (old, rel))

    def test_app_has_no_restart_prompt(self):
        body = self.read('app/src/views/useHomeChooser.js')
        self.assertNotIn('askRestart', body)
        self.assertNotIn('RESTART_NOTE', body)

    def test_the_app_has_no_restart_dialog_and_the_service_says_what_it_logs(self):
        self.assertNotIn('rebootAsk', self.read('app/src/views/useHomeChooser.js'))
        self.assertNotIn('Restart the TV?', self.read('app/src/views/MainPanel.js'))
        system = self.read('service/src/service/system.c')
        self.assertIn('createAlert request: message: %s; buttons: Reboot (tvpower/power/reboot), Later (focused)', system)


class RebootAsk(unittest.TestCase):
    APPLY = hc_e2e.constants.REBOOT_PROMPT['apply']
    REQUEST = ('2026-10-24T05:48:12 user.info org.webosbrew.homechooser.service [] REBOOT_ASK {} '
               'createAlert request: message: %s; buttons: Reboot (tvpower/power/reboot), Later (focused)' % APPLY)

    def test_accepted_alert(self):
        text = self.REQUEST + '\n' + ('2026-10-24T05:48:12 user.info svc [] REBOOT_ASK {} luna://com.webos.notification/'
                                      'createAlert accepted (exit 0): {"returnValue":true,"alertId":"com.x-1234"}')
        got = hc_e2e.releases.parse_reboot_ask(text)
        self.assertEqual(got['message'], self.APPLY)
        self.assertEqual(got['buttons'], ['Reboot', 'Later'])
        self.assertEqual((got['alert'], got['alert_id'], got['toast']), ('accepted', 'com.x-1234', None))

    def test_escaped_json_in_the_log(self):
        text = self.REQUEST + '\n' + 'REBOOT_ASK {} luna://com.webos.notification/createAlert accepted (exit 0): {\\"returnValue\\":true,\\"alertId\\":\\"a-9\\"}'
        self.assertEqual(hc_e2e.releases.parse_reboot_ask(text)['alert_id'], 'a-9')

    def test_refused_alert_then_toast(self):
        text = '\n'.join([self.REQUEST,
                          'REBOOT_ASK {} luna://com.webos.notification/createAlert refused (exit 0): {"returnValue":false,"errorText":"Alert is blocked by [ system ]"}',
                          'REBOOT_ASK {} luna://com.webos.notification/createToast accepted (exit 0): {"returnValue":true,"toastId":"t1"}'])
        got = hc_e2e.releases.parse_reboot_ask(text)
        self.assertEqual((got['alert'], got['alert_id'], got['toast']), ('refused', None, 'accepted'))

    def test_nothing_logged_and_foreign_lines(self):
        for text in ('', None, 'UNINSTALL_ASK luna://com.webos.notification/createAlert accepted (exit 0): {"alertId":"u1"}'):
            got = hc_e2e.releases.parse_reboot_ask(text)
            self.assertEqual((got['message'], got['buttons'], got['alert'], got['alert_id'], got['toast']), (None, [], None, None, None))

    def test_bus_monitor_observes_alert_without_retaining_confirmation_token(self):
        monitor = hc_e2e.alerts.RebootAlertMonitor(hc_e2e.device.Device('unused'))
        monitor._observe({
            'type': 'call', 'transport': 'TX', 'sender': 'com.webos.lunasend-100',
            'senderUniqueName': 'client-100',
            'destination': 'com.webos.notification', 'method': 'createAlert', 'token': 7,
            'payload': {'sourceId': hc_e2e.constants.APP_ID, 'title': 'Home Chooser', 'message': self.APPLY,
                        'buttons': [{'label': 'Reboot', 'onclick': hc_e2e.constants.TVPOWER_REBOOT_URI,
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
        monitor = hc_e2e.alerts.RebootAlertMonitor(hc_e2e.device.Device('unused'))
        monitor._observe({'type': 'return', 'transport': 'TX', 'sender': 'com.webos.notification',
                          'destination': 'com.webos.lunasend-100', 'replyToken': 9,
                          'payload': {'returnValue': True, 'alertId': 'foreign'}})
        monitor._observe({'type': 'call', 'transport': 'TX', 'sender': 'com.webos.lunasend-100',
                          'destination': 'com.webos.notification', 'method': 'createAlert', 'token': 8,
                          'payload': {'sourceId': 'another.app'}})
        self.assertIsNone(monitor.snapshot()['alert'])

    def test_bus_monitor_records_refused_alert_and_accepted_toast(self):
        monitor = hc_e2e.alerts.RebootAlertMonitor(hc_e2e.device.Device('unused'))
        for token, method in ((1, 'createAlert'), (2, 'createToast')):
            payload = {'sourceId': hc_e2e.constants.APP_ID, 'message': self.APPLY}
            if method == 'createAlert':
                payload.update(title='Home Chooser', message=self.APPLY, buttons=[
                    {'label': 'Reboot', 'onclick': hc_e2e.constants.TVPOWER_REBOOT_URI,
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
        with open(os.path.join(REPO, 'e2e/hc_e2e/cli.py'), encoding='utf-8') as f:
            src = f.read()
        self.assertIn("'--reboot-via', choices=['later', 'alert'], default='later'", src)
        self.assertNotIn("'dialog'", src.split("'--reboot-via'")[1].split('add_argument')[0])


class Luna(unittest.TestCase):
    def test_parse_luna(self):
        self.assertEqual(hc_e2e.evidence.parse_luna('{"returnValue":true}'), {'returnValue': True})
        self.assertEqual(hc_e2e.evidence.parse_luna('warning: x\n{"returnValue":false,"errorCode":"notHome"}\n'),
                         {'returnValue': False, 'errorCode': 'notHome'})
        self.assertIsNone(hc_e2e.evidence.parse_luna(''))
        self.assertIsNone(hc_e2e.evidence.parse_luna('[timeout]'))

    def test_get_info_contract(self):
        ok = {'returnValue': True, 'apiVersion': 3, 'version': '0.1.0', 'root': True, 'permission': hc_e2e.api_spec.API_GROUP,
              'permitted': True, 'isSelectedHome': False}
        self.assertEqual(hc_e2e.api_spec.api_reply_problems('getInfo', ok), [])
        bad = dict(ok, apiVersion=2)
        del bad['isSelectedHome']
        p = hc_e2e.api_spec.api_reply_problems('getInfo', bad)
        self.assertTrue(any('isSelectedHome' in x for x in p) and any('apiVersion' in x for x in p), p)
        self.assertTrue(hc_e2e.api_spec.api_reply_problems('getInfo', dict(ok, root='yes')))
        self.assertTrue(hc_e2e.api_spec.api_reply_problems('getInfo', dict(ok, permission='other')))
        self.assertEqual(hc_e2e.api_spec.api_reply_problems('getInfo', dict(ok, futureField=1)), [])
        self.assertTrue(hc_e2e.api_spec.api_reply_problems('getInfo', dict(ok, version=3)))

    def test_list_and_running_contract(self):
        apps = {'returnValue': True, 'apiVersion': 3, 'apps': [
            {'id': 'a', 'title': 'A', 'icon': 'i.png', 'folderPath': '/x', 'removable': True, 'visible': True}]}
        self.assertEqual(hc_e2e.api_spec.api_reply_problems('listApps', apps), [])
        leak = {'returnValue': True, 'apiVersion': 3, 'apps': [{'id': 'a', 'requiredPermissions': []}]}
        self.assertEqual(hc_e2e.api_spec.api_reply_problems('listApps', leak), [])
        self.assertTrue(hc_e2e.api_spec.api_reply_problems('listApps', {'returnValue': True, 'apiVersion': 3, 'apps': [{'id': 'a', 'removable': 'yes'}]}))
        self.assertTrue(hc_e2e.api_spec.api_reply_problems('listApps', {'returnValue': True, 'apiVersion': 3}))
        run = {'returnValue': True, 'apiVersion': 3, 'running': [{'id': 'a', 'instanceId': 'x', 'processId': 12,
                                                                  'webProcessId': 34}]}
        self.assertEqual(hc_e2e.api_spec.api_reply_problems('listRunning', run), [])
        self.assertEqual(hc_e2e.api_spec.api_reply_problems('listRunning', dict(run, running=[dict(run['running'][0], futureField=5)])), [])
        self.assertTrue(hc_e2e.api_spec.api_reply_problems('listRunning', dict(run, running=[{'id': 'a', 'processId': '12'}])))
        self.assertTrue(hc_e2e.api_spec.api_reply_problems('listRunning', {'returnValue': True, 'apiVersion': 3, 'running': [{}]}))

    def test_close_and_uninstall_reply_contract(self):
        close = {'returnValue': True, 'apiVersion': 3, 'id': 'a'}
        self.assertEqual(hc_e2e.api_spec.api_reply_problems('closeApp', dict(close, later=1)), [])
        self.assertTrue(hc_e2e.api_spec.api_reply_problems('closeApp', {'returnValue': True, 'id': 'a'}))
        self.assertEqual(hc_e2e.api_spec.api_reply_problems('uninstallApp', dict(close, pending=True, confirmation='alert')), [])
        self.assertTrue(hc_e2e.api_spec.api_reply_problems('uninstallApp', dict(close, pending=True, confirmation='bad')))

    def test_new_launcher_replies(self):
        front = {'returnValue': True, 'apiVersion': 3, 'appId': '', 'processId': 123}
        self.assertEqual(hc_e2e.api_spec.api_reply_problems('getForegroundApp', dict(front, futureField=True)), [])
        self.assertTrue(hc_e2e.api_spec.api_reply_problems('getForegroundApp', dict(front, processId='123')))
        app = {'returnValue': True, 'apiVersion': 3,
               'app': {'id': 'a', 'appCategory': ['home'], 'removable': True}}
        self.assertEqual(hc_e2e.api_spec.api_reply_problems('getAppInfo', dict(app, futureField=True)), [])
        self.assertTrue(hc_e2e.api_spec.api_reply_problems('getAppInfo', dict(app, app={'id': 'a', 'appCategory': 'home'})))
        icon = {'returnValue': True, 'apiVersion': 3, 'mimeType': 'image/png',
                'data': 'YWJj', 'byteLength': 3}
        self.assertEqual(hc_e2e.api_spec.api_reply_problems('getAppIcon', dict(icon, futureField=True)), [])
        self.assertTrue(hc_e2e.api_spec.api_reply_problems('getAppIcon', dict(icon, byteLength=4)))
        self.assertTrue(hc_e2e.api_spec.api_reply_problems('getAppIcon', dict(icon, data='not-base64')))

    def test_too_large_icon_refusal_is_a_valid_answer_for_a_big_file(self):
        refused = {'returnValue': False, 'errorCode': 'iconTooLarge', 'apiVersion': 3}
        tl = hc_e2e.api_spec.too_large_refusal
        self.assertTrue(tl(refused, 1048577))
        self.assertFalse(tl(refused, 172), 'a small file must not be refused')
        self.assertFalse(tl(refused, None), 'size unknown')
        self.assertFalse(tl(dict(refused, errorCode='noIcon'), 1048577))
        self.assertFalse(tl({'returnValue': True, 'apiVersion': 3}, 1048577))

    def test_refusals(self):
        self.assertTrue(hc_e2e.api_spec.api_refused({'returnValue': False, 'errorCode': 'noPermission'}))
        self.assertTrue(hc_e2e.api_spec.api_refused({'returnValue': False, 'errorCode': -1, 'errorText': 'Denied method call'}))
        self.assertFalse(hc_e2e.api_spec.api_refused({'returnValue': True}))
        self.assertFalse(hc_e2e.api_spec.api_refused(None), 'no reply is not a clean refusal')
        p = hc_e2e.api_spec.api_reply_problems('listApps', {'returnValue': False, 'errorCode': 'notRoot'})
        self.assertTrue(p and 'notRoot' in p[0])


class Crashes(unittest.TestCase):
    def test_crash_lines(self):
        log = '\n'.join([
            '2026-10-24T01:00:00 host crashd[812]: CRASHED: /usr/bin/WebAppMgr signal 11',
            '2026-10-24T01:00:01 host WebAppMgr[900]: [I] app launched',
            '2026-10-24T01:00:02 host systemd[1]: webapp-mgr.service: Main process exited, code=dumped, status=11/SEGV',
            '2026-10-24T01:00:03 host sam[1]: NL_APP_LAUNCH_BEGIN',
        ])
        got = hc_e2e.evidence.crash_lines(log)
        self.assertEqual(len(got), 2, got)
        self.assertEqual(hc_e2e.evidence.crash_lines('WebAppMgr: ok\n'), [])


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

    def test_known_crash_still_fails(self):
        self.assertEqual(hc_e2e.evidence.crash_lines(self.CRASHD), [self.CRASHD])
        known, other = hc_e2e.evidence.split_known_crashes([self.CRASHD], self.reply(self.NOISE, self.FAULT))
        self.assertEqual((known, other), ([], [self.CRASHD]))
        self.assertEqual(list(hc_e2e.evidence.known_fault_pids(self.reply(self.NOISE, self.FAULT))), ['4892'])

    def test_known_faultmanager_alert_is_foreign(self):
        fault = dict(self.FAULT, faultContent=self.FAULT['faultContent'].replace('4892', '7453'))
        self.assertEqual(hc_e2e.evidence.split_known_crashes([self.ALERT], self.reply(fault)), ([], [self.ALERT]))
        self.assertEqual(hc_e2e.evidence.split_known_crashes([self.ALERT], self.reply(self.FAULT)), ([], [self.ALERT]))
        other = self.ALERT.replace('API_SI_GetLinkageUriInfo+0x197', 'API_SI_Other+0x10')
        self.assertEqual(hc_e2e.evidence.split_known_crashes([other], self.reply(fault)), ([], [other]))

    def test_other_location_fails(self):
        f = dict(self.FAULT, faultContent=self.FAULT['faultContent'].replace('API_SI_GetLinkageUriInfo+0x197', 'API_SI_Other+0x10'))
        self.assertEqual(hc_e2e.evidence.split_known_crashes([self.CRASHD], self.reply(f)), ([], [self.CRASHD]))

    def test_other_process_fails(self):
        web = '2026-10-23T04:26:50Z [194.2] user.info crashd [] crashd CRASHED {"process":"WebAppMgr.5823"}'
        f = dict(self.FAULT, faultContent=self.FAULT['faultContent'].replace('legacy-broadcas', 'WebAppMgr'))
        self.assertEqual(hc_e2e.evidence.split_known_crashes([web], self.reply(self.FAULT)), ([], [web]))
        self.assertEqual(hc_e2e.evidence.known_fault_pids(self.reply(f)), {})

    def test_other_pid_or_no_fault_entry_fails(self):
        other = self.CRASHD.replace('4892', '999')
        self.assertEqual(hc_e2e.evidence.split_known_crashes([other], self.reply(self.FAULT)), ([], [other]))
        self.assertEqual(hc_e2e.evidence.split_known_crashes([self.CRASHD], 'Error: timeout'), ([], [self.CRASHD]))

    def test_mixed_lines(self):
        web = 'x crashd CRASHED {"process":"WebAppMgr.5823"}'
        known, other = hc_e2e.evidence.split_known_crashes([self.CRASHD, web], self.reply(self.FAULT))
        self.assertEqual((known, other), ([], [self.CRASHD, web]))


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
    with unittest.mock.patch.object(time, 'sleep', lambda *_: None):
        yield



class Audiooutputd183(unittest.TestCase):
    """beanjar#183: the LG audiooutputd crash dialog is dismissed with NO; everything else still stops the run."""
    MSG = '[crash] Fault Occured. Reason: RDXDErr3_%s__Linux Crash: %s crashes at location'

    @staticmethod
    def box(x, y, w=200, h=40):
        return [x, y, x + w, y + h]

    def frame(self, msg, focus='YES', buttons=('YES', 'NO'), tag='[crash]'):
        els = [{'kind': 'text', 'text': msg, 'box': self.box(400, 355, 800, 40), 'group': 1}]
        for i, b in enumerate(buttons):
            x = 500 + i * 300
            els.append({'kind': 'button', 'text': b, 'box': self.box(x, 400), 'centre': [x + 100, 420], 'group': 1,
                        'style': {'disabled': b != focus, 'highlighted': b == focus, 'contrast': 9 if b == focus else 1}})
        return {'width': 1920, 'height': 1080, 'elements': els}

    def make(self, frames, click=False, probes=(False, True)):
        s = scenario(10)
        s.a = type('A', (), {'wait_popup': 0})()
        s.pressed, s.clicked, s.frames, s.probes = [], [], list(frames), list(probes)
        s.insp = object()
        s.ui = lambda: {'row': 'a', 'rows': ['a', 'b']}
        s.shot = lambda label: label + '.png'
        s.dismiss_known_popup = lambda *a, **k: None
        s.use_click = lambda: click
        s.elements = lambda label: s.frames.pop(0) if len(s.frames) > 1 else s.frames[0]
        s.probe_keys = lambda rows, ui: s.probes.pop(0)
        s.press = lambda *k: s.pressed.extend(k)
        s.dev.foreground = lambda: ('app', '')
        s.dev.luna = lambda uri: '{}'

        def click_element(text=None, role=None, frame=None, **kw):
            hc_e2e.alerts.match_element(frame, text, role)  # raises like the real helper when there is no unique NO
            s.clicked.append(text)
        s.click_element = click_element
        return s

    def run_ensure(self, s):
        with mock_sleep():
            return s.ensure_key_focus(['a', 'b'])

    def test_keys_mode_moves_focus_to_no_then_ok_and_retries(self):
        msg = self.MSG % ('audiooutputd', 'audiooutputd')
        s = self.make([self.frame(msg, 'YES'), self.frame(msg, 'NO'), {'width': 1920, 'height': 1080, 'elements': []}])
        self.assertTrue(self.run_ensure(s))
        self.assertEqual(s.pressed, ['RIGHT', 'OK'])
        self.assertNotIn('YES', s.clicked)
        self.assertEqual([o[1] for o in s.rec.outcomes], ['RECOVERED'])
        ev = json.loads(s.rec.outcomes[0][2])
        self.assertIn('beanjar#183', ev['reference'])
        self.assertIn('audiooutputd', ev['ocr_before'])
        self.assertTrue(ev['screenshot_before'] and ev['screenshot_after'])
        self.assertEqual(s.probes, [])  # probed once before, once after (the retry)

    def test_click_mode_clicks_no_only(self):
        msg = self.MSG % ('audiooutputd', 'audiooutputd')
        s = self.make([self.frame(msg), {'width': 1920, 'height': 1080, 'elements': []}], click=True)
        self.assertTrue(self.run_ensure(s))
        self.assertEqual(s.clicked, ['NO'])
        self.assertEqual(s.pressed, [])
        self.assertEqual([o[1] for o in s.rec.outcomes], ['RECOVERED'])

    def test_focus_already_on_no_presses_ok_only(self):
        msg = self.MSG % ('audiooutputd', 'audiooutputd')
        s = self.make([self.frame(msg, 'NO'), {'width': 1920, 'height': 1080, 'elements': []}])
        self.assertTrue(self.run_ensure(s))
        self.assertEqual(s.pressed, ['OK'])

    def test_dialog_still_there_stops(self):
        msg = self.MSG % ('audiooutputd', 'audiooutputd')
        s = self.make([self.frame(msg, 'NO')], click=True)
        with self.assertRaises(RuntimeError):
            self.run_ensure(s)
        self.assertEqual(s.rec.outcomes, [])

    def test_other_process_crash_dialog_stops(self):
        s = self.make([self.frame(self.MSG % ('WebAppMgr', 'WebAppMgr'), 'NO')], probes=(False,))
        with self.assertRaises(RuntimeError):
            self.run_ensure(s)
        self.assertEqual(s.pressed, [])
        self.assertEqual(s.clicked, [])
        self.assertEqual(s.rec.outcomes, [])

    def test_general_dialog_stops(self):
        msg = '[general] audiooutputd Fault Occured'
        s = self.make([self.frame(msg, 'NO')], probes=(False,), click=True)
        with self.assertRaises(RuntimeError):
            self.run_ensure(s)
        self.assertEqual((s.pressed, s.clicked, s.rec.outcomes), ([], [], []))

    def test_yes_never_pressed(self):
        msg = self.MSG % ('audiooutputd', 'audiooutputd')
        # focus never reaches NO after the move: no OK
        s = self.make([self.frame(msg, 'YES'), self.frame(msg, 'YES')])
        with self.assertRaises(RuntimeError):
            self.run_ensure(s)
        self.assertNotIn('OK', s.pressed)
        # no NO button at all
        s = self.make([self.frame(msg, 'YES', buttons=('YES', 'OK'))], click=True)
        with self.assertRaises((RuntimeError, ValueError)):
            self.run_ensure(s)
        self.assertEqual(s.clicked, [])
        self.assertNotIn('OK', s.pressed)

    def test_fault_check_notes_audiooutputd_but_fails_other(self):
        def reply(*contents):
            return json.dumps({'returnValue': True, 'faultDetectedStatus': {'detected': 1, 'detectedFaults': [
                {'faultType': 'crash', 'faultContent': c, 'faultTime': '55.1'} for c in contents]}})
        for contents, ok in ((('RDXDErr3_audiooutputd__Linux Crash: audiooutputd',), True),
                             (('RDXDErr3_audiooutputd__Linux Crash: x', 'WebAppMgr'), False)):
            s = scenario(10)
            s.popup_mark = ('2026-10-24T01:00:00', 10.0)
            s.priv_luna = lambda uri, payload='{}', r=reply(*contents): r
            s.shot = lambda label: label
            s.foreign_popup('x')
            self.assertEqual([c for c in s.rec.checks if 'no Home Chooser or WebAppMgr crash fault' in c[0]][0][1], ok)
            self.assertEqual([hc_e2e.evidence.crash_process(e) for _, e in s.platform_crashes], ['audiooutputd'])


class WaitForDevice(unittest.TestCase):
    """2026-10-24: three VMs whose previous boot was cut at ~9 s came up in HBC failsafe mode (telnetd,
    no Dropbear): developer mode answered, root SSH never did.  The wait names that state."""

    def wait(self, root_up, apps):
        class Dev:
            vm = 'vm'

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
        return hc_e2e.preflight.wait_for_device(Dev(), Rec(), timeout=0), said

    def test_ares_app_list_failure_does_not_mean_no_apps(self):
        import unittest.mock
        with unittest.mock.patch.object(hc_e2e.timing, 'run', return_value=(1, 'Failed to connect to vm: disconnected')):
            with self.assertRaisesRegex(RuntimeError, 'ares-install -l on vm failed'):
                hc_e2e.device.Device('vm').installed()
        state, _ = self.wait(False, RuntimeError('Failed to connect'))
        self.assertEqual(state, hc_e2e.preflight.DOWN)

    def test_states(self):
        hb = ['org.webosbrew.hbchannel']
        self.assertEqual(self.wait(True, hb)[0], hc_e2e.preflight.READY)
        self.assertEqual(self.wait(True, [])[0], hc_e2e.preflight.NO_HBCHANNEL)
        self.assertEqual(self.wait(False, hb)[0], hc_e2e.preflight.NO_ROOT_SSH)
        self.assertEqual(self.wait(False, [])[0], hc_e2e.preflight.DOWN)
        state, said = self.wait(False, hb)
        self.assertEqual(len(said), 1)
        self.assertIn('no-root-ssh', said[0])


class StockHomeByRelease(unittest.TestCase):
    """6-9 use the home gate; 10 and later close LG Home once (idleApp keeps it away); 5 is not touched."""

    def test_releases(self):
        self.assertEqual({m: hc_e2e.releases.closes_stock_home(m) for m in range(5, 13)},
                         {5: False, 6: False, 7: False, 8: False, 9: False, 10: True, 11: True, 12: True})
        self.assertEqual({m: hc_e2e.releases.uses_home_gate(m) for m in range(5, 13)},
                         {5: False, 6: True, 7: True, 8: True, 9: True, 10: False, 11: False, 12: False})
        self.assertFalse(hasattr(hc_e2e.scenario.Scenario, 'stock_left_running'))


class IdleApp(unittest.TestCase):
    """getProfiles idleApp on 10+ (docs/settings-app.md "What is written")."""
    LAUNCHER = 'org.webosbrew.hcfixture.web.normal'
    # 10.3.1 luna-send output shape, stock and with the full-screen override
    STOCK_REPLY = ('{"returnValue":true,"profiles":{"idleApp":{"_packageIncluded":true,"_configuration":"idleApp.json",'
                   '"support":true,"appId":"com.webos.app.home"}}}')

    def test_parse(self):
        self.assertEqual(hc_e2e.releases.profile_idle_app(self.STOCK_REPLY)['appId'], hc_e2e.constants.STOCK)
        self.assertEqual(hc_e2e.releases.profile_idle_app('{"idleApp":{"support":false}}'), {'support': False})
        self.assertIsNone(hc_e2e.releases.profile_idle_app('{"returnValue":false,"errorText":"Denied"}'))
        self.assertIsNone(hc_e2e.releases.profile_idle_app('garbage'))

    def test_full_screen(self):
        ok, want = hc_e2e.releases.idle_app_verdict({'support': True, 'appId': self.LAUNCHER}, self.LAUNCHER, False)
        self.assertTrue(ok)
        self.assertIn(self.LAUNCHER, want)
        self.assertFalse(hc_e2e.releases.idle_app_verdict(hc_e2e.releases.profile_idle_app(self.STOCK_REPLY), self.LAUNCHER, False)[0])
        self.assertFalse(hc_e2e.releases.idle_app_verdict({'support': False, 'appId': self.LAUNCHER}, self.LAUNCHER, False)[0])
        self.assertFalse(hc_e2e.releases.idle_app_verdict(None, self.LAUNCHER, False)[0])

    def test_overlay(self):
        # 10.3.1 merges key by key: the stock appId stays beside support false
        self.assertTrue(hc_e2e.releases.idle_app_verdict({'support': False, 'appId': hc_e2e.constants.STOCK}, self.LAUNCHER, True)[0])
        self.assertFalse(hc_e2e.releases.idle_app_verdict({'support': True, 'appId': self.LAUNCHER}, self.LAUNCHER, True)[0])

    def test_stock(self):
        self.assertTrue(hc_e2e.releases.idle_app_stock(hc_e2e.releases.profile_idle_app(self.STOCK_REPLY)))
        self.assertFalse(hc_e2e.releases.idle_app_stock({'support': True, 'appId': self.LAUNCHER}))
        self.assertFalse(hc_e2e.releases.idle_app_stock({'support': False, 'appId': hc_e2e.constants.STOCK}))
        self.assertFalse(hc_e2e.releases.idle_app_stock(None))


class FaultPopups(unittest.TestCase):
    """foreign_popup: keyCheck2 is boot noise (triage 2026-10-24); a fault raised after the mark counts."""
    KEYCHECK = ('{ "returnValue": true, "faultDetectedStatus": { "detected": 1, "detectedFaults": [ { "faultType": '
                '"general", "faultContent": "keyCheck2_4415_sysfaultdetector", "faultTime": "30.448679184" } ] } }')

    def test_keycheck_is_noise(self):
        real, noise = hc_e2e.evidence.classify_faults(self.KEYCHECK, 300.0)
        self.assertEqual(real, [])
        self.assertEqual(len(noise), 1)
        # noise even without a mark
        self.assertEqual(hc_e2e.evidence.classify_faults(self.KEYCHECK, None)[0], [])

    def test_new_crash_counts(self):
        # 9.2.1 2026-10-23T04:26:50: crashd CRASHED WebAppMgr at [194.278], a fault entry at 194.292
        reply = json.dumps({'returnValue': True, 'faultDetectedStatus': {'detected': 2, 'detectedFaults': [
            {'faultTime': '31.319272907', 'faultType': 'general', 'faultContent': 'keyCheck2_4629_sysfaultdetector'},
            {'faultTime': '194.292195087', 'faultType': 'crash', 'faultContent': 'WebAppMgr'}]}})
        real, noise = hc_e2e.evidence.classify_faults(reply, 150.0)
        self.assertEqual([f['faultContent'] for f in real], ['WebAppMgr'])
        # the same crash, raised before this step's mark: noted, not counted
        real, noise = hc_e2e.evidence.classify_faults(reply, 200.0)
        self.assertEqual(real, [])
        self.assertEqual(len(noise), 2)

    def test_bad_reply(self):
        self.assertEqual(hc_e2e.evidence.classify_faults('Error: timeout', 10.0), ([], []))

    def test_line_pattern(self):
        rx = re.compile(hc_e2e.evidence.POPUP_LINE_RE)
        self.assertTrue(rx.search('2026-10-23T04:26:50Z [194.2] user.info crashd [] crashd CRASHED {"process":"WebAppMgr.5823"}'))
        self.assertFalse(rx.search('2026-10-23T04:26:47Z [191.6] user.info faultmanager [] faultmanager FAULT_RESPONSE {} '
                                   '[general]/tmp/faultmanager/general/keyCheck2_4629_sysfaultdetector(none)'))


class StockCloseBeforeHomeProof(unittest.TestCase):
    """The Home proof starts LG Home for the close rule only while the watcher has not closed it yet."""

    def scenario(self, log):
        from unittest.mock import Mock
        s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
        s.rec, s.launcher = Mock(), 'org.x.web.overlay'
        s.dev = Mock()
        s.dev.foreground.return_value = ('a', '{}')
        s.dev.sh.side_effect = lambda cmd, *a, **k: (0, log if 'homechooser-home.log' in cmd else '1')
        s.dev.launch.return_value = (0, 'Launched')
        s.planned = lambda phase: True
        s.is_owner = lambda: False
        s.running_ids = lambda: ['org.webosbrew.hbchannel']
        s.wait = lambda fn, *a: fn()
        s.home_press, s.residency = Mock(), Mock()
        return s

    def test_no_restart_after_the_watcher_close(self):
        s = self.scenario('closeByAppId com.webos.app.home after org.x.web.overlay in front: {}')
        s.step3()
        s.dev.launch.assert_not_called()

    def test_restart_before_the_close(self):
        s = self.scenario('2026 sam relaunched something')
        s.step3()
        s.dev.launch.assert_called_once_with(hc_e2e.constants.STOCK)


class FakeRec:
    def __init__(self):
        self.checks, self.notes, self.outcomes, self.said = [], [], [], []

    def say(self, line):
        self.said.append(line)

    def outcome(self, name, status, evidence):
        self.outcomes.append((name, status, evidence))

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

    def luna_as(self, app, uri, payload='{}', timeout=60):
        return 0, self.fault_reply.strip(), hc_e2e.evidence.parse_luna(self.fault_reply)

    def sh(self, cmd, timeout=60):
        if cmd.startswith('date'):
            return 0, '2026-10-24T01:00:00\n'
        if 'grep -c' in cmd:
            return 0, self.log_restarts + '\n'
        if '/var/log/messages' in cmd:
            return 0, self.messages
        return 0, ''


def scenario(major=9):
    s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
    s.rec, s.dev, s.major = FakeRec(), FakeDev(), major
    return s


class ScreensaverFakeDev(FakeDev):
    """Offline tvpower model; fault injection happens at the selected scenario step."""
    launcher = 'org.webosbrew.hcfixture.web.normal'

    def __init__(self, rec, fault=None, major=10):
        super().__init__()
        self.rec, self.fault, self.major = rec, fault, major
        self.home_wakes, self.back_goes_home = False, False
        self.selected, self.substate, self.processing = False, 'active', ''
        self.fg, self.previous = HOME, HOME
        self.choice, self.default_apps, self.policy = '', {'home': None, 'other': 'retained'}, True
        self.logs, self.calls, self.buttons = [], [], []
        self.saver_app, self.log_ignore = hc_e2e.constants.SCREENSAVERS[0], True
        self.boot_applied = True
        self.subprocessing, self.helper_mode = '', 'missing'

    def broken(self, kind):
        return self.fault == (int(self.rec.step[1]), kind)

    def select(self):
        self.selected = True
        self.choice = self.launcher
        self.default_apps['home'] = self.launcher
        self.policy = False
        self.fg = self.launcher

    def installed(self):
        return ['org.webosbrew.hbchannel'] + ([hc_e2e.constants.APP_ID, self.launcher] if self.selected else [])

    def luna(self, uri, payload='{}'):
        self.calls.append(('foreground', uri, payload))
        if uri.endswith('/getForegroundAppInfo'):
            return json.dumps({'returnValue': True, 'appId': self.fg, 'processId': '', 'windowId': ''})
        return json.dumps({'returnValue': False, 'errorText': 'Unknown method'})

    def foreground(self):
        return hc_e2e.device.Device.foreground(self)

    def record_home(self):
        return self.default_apps['home'], json.dumps({'returnValue': True, 'settings': {'defaultApps': self.default_apps}})

    def preload_policy(self):
        return self.policy, json.dumps({'returnValue': True, 'applications': [{'id': HOME, 'isEnabled': self.policy}]})

    def unit(self, name):
        return dict(self.units.get(name, {'MainPID': '100' if self.selected else '0',
                                         'NRestarts': '0', 'ActiveState': 'active' if self.selected else 'inactive'}))

    def sh(self, cmd, timeout=60):
        self.calls.append(cmd)
        if cmd.startswith('cat ' + hc_e2e.constants.CHOICE):
            return (0, self.choice) if self.selected else (1, 'No such file')
        if cmd.startswith('cat /tmp/homechooser-home.log'):
            return 0, '\n'.join(self.logs)
        if 'tvpower' in cmd:
            return 0, 'tvpower Screen Saver Ready; Failed to load lgchannels SDK'
        return super().sh(cmd, timeout)

    def launch(self, appid):
        self.fg = appid
        return 0, ''

    def luna_as(self, app, uri, payload='{}', timeout=60):
        self.calls.append((app, uri, payload))
        assert app is None, 'tvpower must run as root without -a'
        reply = {'returnValue': True}
        if uri.endswith('/getPowerState'):
            if '/power2/' in uri:
                reply = ({'returnValue': False, 'errorText': 'Unknown method "getPowerState" for category "/power2"'}
                         if self.major == 6 else
                         {'returnValue': True, 'state': 'ACTIVE', 'sub state': self.substate,
                          'sub processing': self.subprocessing})
            else:
                reply['state'] = 'Screen Saver' if self.substate == 'screen saver' else 'Active'
                if self.processing:
                    reply['processing'] = self.processing
        elif uri.endswith('/turnOnScreenSaver'):
            if self.broken('skip'):
                reply = {'returnValue': False, 'errorText': 'screenSaverEnabled is off (first use not completed)'}
            elif self.broken('refuse'):
                reply = {'returnValue': False, 'errorText': 'Unknown service error'}
            elif self.broken('timeout'):
                pass
            else:
                self.previous = self.fg
                self.processing = 'Screen Saver Ready' if self.broken('ready') else ''
                self.substate = 'active' if self.broken('ready') else 'screen saver'
                # SAM's singular foreground keeps the card beneath the screensaver.
                if self.log_ignore:
                    self.logs.append('ignoring screensaver app ' + self.saver_app)
                if self.broken('choice'):
                    self.choice = HOME
                if self.broken('record'):
                    self.default_apps['other'] = 'changed'
                if self.broken('policy'):
                    self.policy = True
                if self.broken('close'):
                    self.logs.append('closeByAppId ' + HOME)
                if self.broken('policy_call'):
                    self.logs.append('setPreloadPolicy ' + HOME)
                if self.broken('restart'):
                    self.units['homechooser-home'] = {'MainPID': '101', 'NRestarts': '1', 'ActiveState': 'active'}
        elif uri == hc_e2e.constants.TVPOWER_REBOOT_URI:
            if self.broken('busy') or self.processing == 'Screen Saver Ready' or self.subprocessing == 'screen saver ready':
                reply = {'returnValue': False, 'errorText': 'power is busy'}
            elif self.broken('reboot_refuse'):
                reply = {'returnValue': False, 'errorText': 'permission denied'}
            else:
                self.boot = 'b2'
        else:
            raise AssertionError('Unexpected endpoint: ' + uri)
        return 0, json.dumps(reply), reply

    def screensaver(self, verb, vm):
        self.calls.append(('beanjar', verb, vm))
        if self.helper_mode == 'missing':
            return None
        if self.helper_mode == 'fail':
            return 1, json.dumps({'error': 'screensaver: timed out waiting for native completion'})
        if self.helper_mode == 'invalid_json':
            return 0, 'not JSON'
        if self.helper_mode != 'stays_ready':
            self.processing = self.subprocessing = ''
            if verb == 'start':
                self.previous, self.substate = self.fg, 'screen saver'
            else:
                self.substate, self.fg = 'active', self.previous
        # Mirrors Beanjar screensaver.control's result keys, not LS2 returnValue.
        return 0, json.dumps({'action': verb, 'changed': True, 'fallback': True,
                              'power': {'sub state': self.substate, 'sub processing': self.subprocessing},
                              'screensaver_windows': [self.saver_app] if verb == 'start' else []})

    def press(self, vm, key):
        assert key in ('HOME', 'BACK'), 'Never confirm an LG dialog'
        self.buttons.append(key)
        was_shown = self.substate == 'screen saver'
        if self.broken('key_error'):
            return 1, 'remote unavailable'
        if self.broken('dismiss_ready'):
            self.processing, self.substate = 'Screen Saver Ready', 'active'
        else:
            self.processing, self.substate = '', 'active'
        home = self.launcher if self.selected else HOME
        if key == 'BACK':
            self.fg = home if was_shown and self.back_goes_home else self.previous
        else:
            self.fg = self.previous if was_shown and self.home_wakes else home
        if key == 'HOME' and not was_shown and self.broken('no_home_dispatch'):
            self.fg = self.previous
        if self.broken('wrong_foreground'):
            self.fg = 'com.webos.app.wrong'
        return 0, ''


class HomeAndScreensaver(unittest.TestCase):
    def make(self, major=10, fault=None):
        from types import SimpleNamespace
        from unittest.mock import Mock
        s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
        s.rec = hc_e2e.recorder.Recorder.__new__(hc_e2e.recorder.Recorder)
        s.rec.results, s.rec.step, s.rec.tag = [], '', ''
        s.rec.say = Mock()
        s.rec.note = Mock()
        s.dev = ScreensaverFakeDev(s.rec, fault, major)
        s.major, s.vm, s.stock_id, s.launcher = major, 'offline', HOME, s.dev.launcher
        s.L = SimpleNamespace(is_fixture=True, overlay=False)
        s.a = SimpleNamespace(api_check=False)
        s.host = SimpleNamespace(remote_press=s.dev.press, vnc_start=Mock(), start=Mock(return_value=(0, '')),
                                 screensaver=s.dev.screensaver)
        s.shot = Mock()
        # Exercise bounded polling without advancing wall time or reaching any transport.
        s.wait = lambda cond, timeout, every=3: next((v for v in (cond() for _ in range(3)) if v), None)
        s.messages_mark = lambda: '2026-10-24T01:00:00'
        s.wait_reboot = lambda boot0, timeout=240: 'rebooted' if s.dev.boot != boot0 else ''
        s.await_boot = Mock(return_value=True)
        s.verify_reboot_apply = lambda: s.rec.check('boot applied', s.dev.boot_applied)
        return s

    def run_scenario(self, s, baseline='PASS'):
        from unittest.mock import patch
        with patch.object(hc_e2e.timing, 'sleep'), patch.object(hc_e2e.timing, 'run', side_effect=AssertionError('OFFLINE ONLY')):
            if baseline == 'PASS' and not getattr(s, 'screensaver_stock', None):
                self.assertEqual(s.screensaver_baseline(), 'PASS')
            s.dev.select()
            s.screensaver_selected(baseline)
        return {int(row['name'][1]): row['status'] for row in s.rec.results if row['name'].startswith('S') and not row['name'].startswith('S0')}

    def test_pass_all_steps_both_screensaver_apps_and_firmware_routes(self):
        from unittest.mock import patch
        for major in (6, 7, 10, 11, 12):
            for app in hc_e2e.constants.SCREENSAVERS:
                with self.subTest(major=major, app=app), patch.object(hc_e2e.timing, 'sleep'):
                    s = self.make(major)
                    s.dev.saver_app = app
                    self.assertEqual(s.screensaver_baseline(), 'PASS')
                    self.assertEqual(self.run_scenario(s), {1: 'PASS', 2: 'PASS', 3: 'PASS', 4: 'PASS'})
                    self.assertEqual(s.dev.boot, 'b2')
                    s.await_boot.assert_called_once()
                    starts = [call for call in s.dev.calls if isinstance(call, tuple) and call[1].endswith('/turnOnScreenSaver')]
                    self.assertTrue(starts)
                    self.assertTrue(all(call[0] is None and ('/power/' if major == 6 else '/power2/') in call[1] for call in starts))
                    self.assertTrue(all(key in ('HOME', 'BACK') for key in s.dev.buttons))
                    for number in range(5):
                        labels = [call.args[0] for call in s.shot.call_args_list]
                        self.assertIn('S%d-before' % number, labels)
                        self.assertIn('S%d-after' % number, labels)

    def test_baseline_outcomes_capture_diagnostics_and_no_settings_writes(self):
        for fault, expected in [('skip', 'SKIPPED'), ('ready', 'BLOCKED'), ('timeout', 'FAIL'),
                                ('wrong_foreground', 'PASS'), ('key_error', 'FAIL'), ('refuse', 'FAIL')]:
            with self.subTest(fault=fault):
                s = self.make(fault=(0, fault))
                from unittest.mock import patch
                with patch.object(hc_e2e.timing, 'sleep'):
                    self.assertEqual(s.screensaver_baseline(), expected)
                self.assertFalse(any('setSystemSettings' in str(call) for call in s.dev.calls))
                if fault in ('skip', 'ready', 'timeout', 'refuse'):
                    self.assertTrue(any('tvpower logs' in call.args[0] for call in s.rec.note.call_args_list))
                self.assertEqual(s.shot.call_args_list[-1].args[0], 'S0-after')

    def test_each_selected_step_pass_blocked_skipped_and_fail(self):
        for step in range(1, 5):
            for expected in ('PASS', 'BLOCKED', 'SKIPPED', 'FAIL'):
                with self.subTest(step=step, expected=expected):
                    if expected == 'PASS':
                        fault = None
                    elif expected == 'SKIPPED':
                        # S2 consumes S1's screensaver, so its skip follows a refused S1 start.
                        fault = (1 if step == 2 else step, 'skip')
                    elif expected == 'BLOCKED':
                        fault = (step, 'dismiss_ready' if step == 2 else ('busy' if step == 4 else 'ready'))
                    else:
                        fault = (step, 'wrong_foreground' if step == 2 else 'refuse')
                    s = self.make(fault=fault)
                    results = self.run_scenario(s)
                    self.assertEqual(results[step], expected)
                    self.assertIn('S%d-after' % step, [call.args[0] for call in s.shot.call_args_list])
                    if expected == 'BLOCKED':
                        self.assertTrue(any('tvpower logs' in call.args[0] for call in s.rec.note.call_args_list))

    def test_daemon_mutations_and_forbidden_calls_fail_s1(self):
        for fault in ('choice', 'record', 'policy', 'restart', 'close', 'policy_call'):
            with self.subTest(fault=fault):
                s = self.make(fault=(1, fault))
                self.assertEqual(self.run_scenario(s)[1], 'FAIL')
                self.assertFalse(any(isinstance(call, tuple) and call[1] == hc_e2e.constants.TVPOWER_REBOOT_URI for call in s.dev.calls))

    def test_screensaver_preload_ignores_other_apps_and_response_order(self):
        import copy
        s = self.make()
        s.dev.select()
        before = s.screensaver_snapshot('before')
        stock = {'id': HOME, 'isEnabled': False, 'preloadMode': 'criu',
                 'extPolicy': [{'preloadMode': 'full', 'requiredMemory': 50}]}
        other = {'id': 'com.webos.app.browser', 'isEnabled': True}
        before['preload'] = [False, json.dumps({'returnValue': True, 'applications': [other, stock]})]
        after = copy.deepcopy(before)
        # The observed device change was array order only. An unrelated app's
        # policy is also outside the stock-home assertion.
        for row in (other, dict(other, isEnabled=False)):
            after['preload'][1] = json.dumps({'applications': [stock, row], 'returnValue': True}, indent=2)
            s.screensaver_unchanged(before, after)

    def test_screensaver_preload_requires_readable_unchanged_stock_entry(self):
        import copy
        s = self.make()
        s.dev.select()
        before = s.screensaver_snapshot('before')
        stock = {'id': HOME, 'isEnabled': False, 'preloadMode': 'criu'}
        before['preload'] = [False, json.dumps({'returnValue': True, 'applications': [stock]})]
        bad_replies = [
            {'returnValue': True, 'applications': [dict(stock, preloadMode='full')]},
            {'returnValue': True, 'applications': [dict(stock, isEnabled=True)]},
            {'returnValue': True, 'applications': []},
            {'returnValue': True, 'applications': [stock, stock]},
            {'returnValue': False, 'applications': [stock]},
            {'returnValue': True, 'applications': None},
        ]
        for reply in bad_replies + ['not JSON']:
            with self.subTest(reply=reply):
                after = copy.deepcopy(before)
                after['preload'][1] = reply if isinstance(reply, str) else json.dumps(reply)
                with self.assertRaises(hc_e2e.beanjar.ScreensaverOutcome):
                    s.screensaver_unchanged(before, after)

    def test_silent_daemon_passes_on_every_release(self):
        # Ignoring the screensaver is judged by the absence of actions, not by a log line.
        for major in (10, 11, 12):
            s = self.make(major)
            s.dev.log_ignore = False
            self.assertEqual(self.run_scenario(s)[1], 'PASS')

    def test_6x_never_invokes_unsupported_beanjar_screensaver_helper(self):
        from unittest.mock import Mock
        s = self.make(6)
        s.host.screensaver = Mock(side_effect=AssertionError('unsupported on 6.x'))
        for verb in ('start', 'stop'):
            self.assertIsNone(s.beanjar_screensaver(verb))
        s.dev.processing = 'Screen Saver Ready'
        with self.assertRaises(hc_e2e.beanjar.ScreensaverOutcome) as cm:
            s.prepare_tvpower_reboot('offline Ready evidence')
        self.assertEqual(cm.exception.status, 'BLOCKED')
        s.host.screensaver.assert_not_called()
        self.assertFalse(any(row['status'] == 'RECOVERED' for row in s.rec.results))

    def test_6x_ready_blocks_s4_before_reboot_or_helper(self):
        s = self.make(6, fault=(4, 'dismiss_ready'))
        s.dev.helper_mode = 'success'  # Even a nominally available helper must not be called.
        self.assertEqual(self.run_scenario(s)[4], 'BLOCKED')
        self.assertFalse(any(isinstance(call, tuple) and
                             (call[0] == 'beanjar' or call[1] == hc_e2e.constants.TVPOWER_REBOOT_URI)
                             for call in s.dev.calls))
        self.assertFalse(any(row['status'] == 'RECOVERED' for row in s.rec.results))

    def test_stuck_dismissal_never_attempts_reboot(self):
        s = self.make(fault=(4, 'dismiss_ready'))
        self.assertEqual(self.run_scenario(s)[4], 'BLOCKED')
        self.assertFalse(any(isinstance(call, tuple) and call[1] == hc_e2e.constants.TVPOWER_REBOOT_URI for call in s.dev.calls))

    def test_reboot_refusal_and_boot_apply_failure_fail(self):
        s = self.make(fault=(4, 'reboot_refuse'))
        self.assertEqual(self.run_scenario(s)[4], 'FAIL')
        s = self.make()
        s.dev.boot_applied = False
        self.assertEqual(self.run_scenario(s)[4], 'FAIL')

    def test_dependency_skip_and_block_preserve_before_after_evidence(self):
        for baseline in ('SKIPPED', 'BLOCKED', 'FAIL'):
            s = self.make()
            results = self.run_scenario(s, baseline)
            self.assertEqual(set(results.values()), {'SKIPPED' if baseline == 'SKIPPED' else 'BLOCKED'})
            self.assertEqual(len(s.shot.call_args_list), 8)
            self.assertFalse(s.dev.buttons)

    def test_unsupported_5x_is_skipped_without_trigger(self):
        s = self.make(5)
        self.assertEqual(s.screensaver_baseline(), 'SKIPPED')
        self.assertFalse(any(isinstance(call, tuple) and call[1].endswith('/turnOnScreenSaver') for call in s.dev.calls))

    def test_after_snapshot_error_cannot_pass(self):
        from unittest.mock import Mock
        s = self.make()
        s.screensaver_snapshot = Mock(side_effect=[{}, RuntimeError('capture unavailable')])
        self.assertEqual(s.screensaver_step(0, lambda before: None), 'FAIL')

    def test_selector_rejects_unvalidated_input_before_any_external_command(self):
        from unittest.mock import patch
        for argv in (['--scenario', 'screensaver', '--vm', '10.x', '--input', 'ares'],
                     ['--scenario', 'all', '--vm', '10.x', '--input', 'qmp'],
                     ['--scenario', 'screensaver', '--vm', '10.x', '--until', '2'],
                     ['--scenario', 'screensaver', '--device', 'offline']):
            with self.subTest(argv=argv), patch.object(sys, 'argv', ['runner'] + argv), \
                    patch.object(hc_e2e.timing, 'run', side_effect=AssertionError('OFFLINE ONLY')), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    hc_e2e.cli.main()
                self.assertEqual(cm.exception.code, 2)


    def test_selector_runs_screensaver_alone_or_with_lifecycle_and_cleans_up(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        # W/H: the window-type phases after the choice and after the Home press (10.x, run_plan)
        for mode, expected in (('lifecycle', [1, 2, 'W', 3, 'H', 4, 5, 6]),
                               ('screensaver', ['S0', 1, 2, 'W', 'S1-S4', 6]),
                               ('all', ['S0', 1, 2, 'W', 'S1-S4', 3, 'H', 4, 5, 6])):
            with self.subTest(mode=mode):
                s, events = self.make(), []
                s.a = SimpleNamespace(elevation=None, input='beanjar', until=5, scenario=mode,
                                      api_check=False, reboot_check=False, update_check=False,
                                      launcher_update_check=False, restore_repair_check=False)
                s.release, s.strategy_name, s.sure, s.note = '10.x', 'luna', 'fake', 'offline state model'
                s.L.spec, s.L.fixture, s.L.ipk = 'fixture:web.normal', ('web', 'normal'), 'offline.ipk'
                s.L.id = s.launcher
                s.host.mode = 'offline'
                s.dev.release = lambda: (10, '10.3.1')
                s.dev.app_installed = lambda app: False
                s.state = lambda: {'choice': 'No such file'}
                s.check_no_crash = Mock()
                s.screensaver_baseline = lambda: events.append('S0') or 'PASS'
                s.screensaver_selected = lambda baseline: events.append('S1-S4')
                s.window_chosen = lambda: events.append('W')
                s.window_home = lambda: events.append('H')
                for number in range(1, 7):
                    setattr(s, 'step%d' % number, lambda *args, n=number: events.append(n))
                s.run()
                self.assertEqual(events, expected)

    def test_selector_rejects_overlay_and_nonfixture_launchers(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        for fixture, overlay in ((True, True), (False, False)):
            with self.subTest(fixture=fixture, overlay=overlay), \
                    patch.object(sys, 'argv', ['runner', '--vm', '10.x', '--scenario', 'screensaver']), \
                    patch.object(hc_e2e.cli, 'load_hosts', return_value={}), \
                    patch.object(hc_e2e.cli, 'make_launcher', return_value=SimpleNamespace(is_fixture=fixture, overlay=overlay)), \
                    patch.object(hc_e2e.timing, 'run', side_effect=AssertionError('OFFLINE ONLY')), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    hc_e2e.cli.main()
                self.assertEqual(cm.exception.code, 2)

    def test_refusal_classification_uses_precondition_reply_not_transport_error(self):
        from unittest.mock import Mock
        for text, expected in (('screen saver is disabled', 'SKIPPED'),
                               ('First-use has not completed', 'SKIPPED'),
                               ('Invalid sub state', 'SKIPPED'),
                               ('Unknown service error', 'FAIL')):
            s = self.make()
            real = s.dev.luna_as
            s.dev.luna_as = lambda app, uri, *a, **k: (
                (0, text, {'returnValue': False, 'errorText': text}) if uri.endswith('/turnOnScreenSaver')
                else real(app, uri, *a, **k))
            with self.assertRaises(hc_e2e.beanjar.ScreensaverOutcome) as cm:
                s.screensaver_start()
            self.assertEqual(cm.exception.status, expected)
        s = self.make()
        real = s.dev.luna_as
        s.dev.luna_as = lambda app, uri, *a, **k: (
            (124, 'first use timeout', None) if uri.endswith('/turnOnScreenSaver') else real(app, uri, *a, **k))
        with self.assertRaises(hc_e2e.beanjar.ScreensaverOutcome) as cm:
            s.screensaver_start()
        self.assertEqual(cm.exception.status, 'FAIL')

    def _start_with(self, s, drops=0, refuse_transition=0, stuck_busy=False, busy_polls=0):
        """Fake tvpowerd for screensaver_start: the first `drops` requests are accepted and then
        silently cancelled; `refuse_transition` helper calls are refused; stuck_busy never settles;
        the first `busy_polls` power polls report a boot 'screen on' sub processing."""
        s.rec.step = 'S1'
        d, state = s.dev, {'drops': drops, 'refuse': refuse_transition, 'polls': 0}
        real_luna, real_helper = d.luna_as, d.screensaver
        def luna(app, uri, *a, **k):
            if uri.endswith('/power2/getPowerState') or uri.endswith('/power/getPowerState'):
                state['polls'] += 1
                if stuck_busy or state['polls'] <= busy_polls:
                    d.subprocessing, d.processing = 'screen on', 'Screen On'
                else:
                    d.subprocessing = d.processing = ''
            if uri.endswith('/turnOnScreenSaver') and state['drops']:
                state['drops'] -= 1
                return 0, '{ "returnValue": true }', {'returnValue': True}
            return real_luna(app, uri, *a, **k)
        def helper(verb, vm):
            d.calls.append(('beanjar', verb, vm))
            if state['refuse']:
                state['refuse'] -= 1
                return 1, json.dumps({'error': 'screensaver: unrelated native power transition: {}'})
            if state['drops']:
                state['drops'] -= 1
                return 0, json.dumps({'action': 'start'})
            d.calls.pop()
            return real_helper(verb, vm)
        d.luna_as, d.screensaver, s.host.screensaver = luna, helper, helper
        d.helper_mode = 'success'
        return state

    def test_start_waits_for_settled_power_and_records_evidence(self):
        s = self.make()
        self._start_with(s, busy_polls=4)  # two power+power2 reads busy: sub processing "screen on"
        s.screensaver_start()
        notes = [c.args for c in s.rec.note.call_args_list]
        settle = [n for n in notes if n[0].endswith('power settle wait')]
        self.assertEqual(len(settle), 1)
        self.assertIn('settled after', settle[0][1])
        self.assertIn('3 poll(s)', settle[0][1])
        self.assertTrue(any(isinstance(c, tuple) and c[1].endswith('/turnOnScreenSaver') for c in s.dev.calls))

    def test_start_never_settling_is_blocked_and_sends_nothing(self):
        for mode in ('tvpower', 'beanjar'):
            s = self.make()
            s.a.saver_start = mode
            self._start_with(s, stuck_busy=True)
            with self.assertRaises(hc_e2e.beanjar.ScreensaverOutcome) as cm:
                s.screensaver_start()
            self.assertEqual(cm.exception.status, 'BLOCKED')
            self.assertIn('NEVER settled', str(cm.exception))
            self.assertIn('screen on', str(cm.exception))
            self.assertFalse(any(isinstance(c, tuple) and (c[1].endswith('/turnOnScreenSaver') or c[:2] == ('beanjar', 'start'))
                                 for c in s.dev.calls))

    def test_start_dropped_once_is_retried_after_settling(self):
        for mode in ('tvpower', 'beanjar', 'refused'):
            s = self.make()
            s.a.saver_start = 'tvpower' if mode == 'tvpower' else 'beanjar'
            self._start_with(s, drops=0 if mode == 'refused' else 1, refuse_transition=1 if mode == 'refused' else 0)
            s.screensaver_start()
            notes = [c.args for c in s.rec.note.call_args_list]
            self.assertEqual(len([n for n in notes if n[0].endswith('power settle wait')]), 2)
            self.assertTrue(any('succeeded on retry' in n[0] for n in notes))
            if mode != 'refused':
                self.assertTrue(any('dropped by firmware' in n[0] for n in notes))
            self.assertTrue(s.screensaver_shown(s.screensaver_power()))

    def test_start_dropped_twice_fails_with_both_attempts(self):
        for mode in ('tvpower', 'beanjar'):
            s = self.make()
            s.a.saver_start = mode
            self._start_with(s, drops=2)
            with self.assertRaises(hc_e2e.beanjar.ScreensaverOutcome) as cm:
                s.screensaver_start()
            self.assertEqual(cm.exception.status, 'FAIL')
            self.assertIn('attempt 1: dropped by firmware', str(cm.exception))
            self.assertIn('attempt 2: dropped by firmware', str(cm.exception))

    def test_reboot_timeout_captures_state_and_distinguishes_ready(self):
        for ready, expected in ((False, 'FAIL'), (True, 'BLOCKED')):
            s = self.make()
            def timeout(boot0, timeout=240):
                if ready:
                    s.dev.processing = 'Screen Saver Ready'
                return ''
            s.wait_reboot = timeout
            self.assertEqual(self.run_scenario(s)[4], expected)
            self.assertTrue(any('S4 reboot timeout: tvpower logs' == call.args[0]
                                for call in s.rec.note.call_args_list))


    def test_s4_stale_ready_recovers_then_reboots_with_explicit_result(self):
        s = self.make(fault=(4, 'dismiss_ready'))
        s.dev.helper_mode = 'success'
        self.assertEqual(self.run_scenario(s)[4], 'PASS')
        self.assertEqual(s.dev.boot, 'b2')
        recovered = [row for row in s.rec.results if row['status'] == 'RECOVERED']
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]['name'],
                         'recovered stale Screen Saver Ready via beanjar screensaver stop, beanjar#56')
        evidence = json.loads(recovered[0]['evidence'])
        self.assertEqual(evidence['helper']['action'], 'stop')
        self.assertEqual(evidence['before']['power'][1]['processing'], 'Screen Saver Ready')
        self.assertNotIn('processing', evidence['after']['power'][1])
        calls = s.dev.calls
        stop = calls.index(('beanjar', 'stop', s.vm))
        reboot = next(i for i, call in enumerate(calls) if isinstance(call, tuple) and call[1] == hc_e2e.constants.TVPOWER_REBOOT_URI)
        self.assertLess(stop, reboot)
        state_reads = [call[1] for call in calls[stop + 1:reboot] if isinstance(call, tuple)]
        self.assertIn(hc_e2e.constants.TVPOWER + 'power/getPowerState', state_reads)
        self.assertIn(hc_e2e.constants.TVPOWER + 'power2/getPowerState', state_reads)
        self.assertEqual(s.dev.buttons[-1], 'BACK')

    def test_s4_recovery_failure_or_persistent_ready_blocks_without_reboot(self):
        for mode in ('fail', 'stays_ready', 'invalid_json'):
            with self.subTest(mode=mode):
                s = self.make(fault=(4, 'dismiss_ready'))
                s.dev.helper_mode = mode
                self.assertEqual(self.run_scenario(s)[4], 'BLOCKED')
                self.assertFalse(any(row['status'] == 'RECOVERED' for row in s.rec.results))
                self.assertEqual(s.dev.boot, 'b1')
                self.assertTrue(any('power after beanjar screensaver stop' in call.args[0]
                                    for call in s.rec.note.call_args_list))
                self.assertTrue(any('recovery failed: tvpower logs' in call.args[0]
                                    for call in s.rec.note.call_args_list))

    def test_ready_detectors_include_both_native_fields_and_nested_replies(self):
        for route in ('power', 'power2'):
            s = self.make()
            s.dev.select()
            s.rec.step = 'S4'
            s.dev.previous = s.dev.fg
            s.dev.helper_mode = 'success'
            if route == 'power':
                s.dev.processing = 'Screen Saver Ready'
            else:
                s.dev.subprocessing = 'screen saver ready'
            power = s.screensaver_power()
            power[route] = (power[route][0], {'state': power[route][1]})
            self.assertTrue(s.screensaver_ready(power))
            self.assertTrue(s.prepare_tvpower_reboot('test'))
            self.assertFalse(s.screensaver_ready(s.screensaver_power()))
            self.assertEqual(s.rec.results[-1]['status'], 'RECOVERED')

    def test_helper_missing_keeps_busy_reboot_handling_and_logs_once(self):
        s = self.make()
        s.dev.select()
        s.rec.step = 'S4'
        s.dev.processing = 'Screen Saver Ready'
        self.assertFalse(s.prepare_tvpower_reboot('first'))
        self.assertFalse(s.prepare_tvpower_reboot('second'))
        missing = [call for call in s.rec.note.call_args_list if 'subcommand missing' in call.args[0]]
        self.assertEqual(len(missing), 1)
        # Old behaviour is retained: an attempted tvpower reboot still reports busy.
        rc, raw, reply = s.dev.luna_as(None, hc_e2e.constants.TVPOWER_REBOOT_URI)
        self.assertFalse(reply['returnValue'])
        self.assertIn('power is busy', raw)
        self.assertFalse(any(row['status'] == 'RECOVERED' for row in s.rec.results))

    def test_no_recovery_helper_when_state_is_not_ready(self):
        s = self.make()
        self.assertTrue(s.prepare_tvpower_reboot('ordinary reboot'))
        self.assertFalse(any(isinstance(call, tuple) and call[0] == 'beanjar' for call in s.dev.calls))

    def test_opt_in_beanjar_start_retains_button_dismissal_for_s0_to_s4(self):
        from unittest.mock import patch
        s = self.make()
        s.a.saver_start = 'beanjar'
        s.dev.helper_mode = 'success'
        with patch.object(hc_e2e.timing, 'sleep'):
            self.assertEqual(s.screensaver_baseline(), 'PASS')
            self.assertEqual(self.run_scenario(s), {1: 'PASS', 2: 'PASS', 3: 'PASS', 4: 'PASS'})
        self.assertEqual(len([call for call in s.dev.calls if isinstance(call, tuple) and call[:2] == ('beanjar', 'start')]), 6)
        self.assertFalse(any(isinstance(call, tuple) and call[1].endswith('/turnOnScreenSaver') for call in s.dev.calls))
        self.assertEqual(s.dev.buttons, ['BACK', 'HOME', 'HOME', 'HOME', 'BACK', 'HOME', 'BACK', 'HOME', 'BACK'])
        self.assertFalse(any(isinstance(call, tuple) and call[:2] == ('beanjar', 'stop') for call in s.dev.calls))

    def test_opt_in_beanjar_start_missing_or_failing_is_reported(self):
        for mode, expected in (('missing', 'SKIPPED'), ('fail', 'BLOCKED'), ('invalid_json', 'BLOCKED'),
                               ('stays_ready', 'FAIL')):  # accepted but never shown, twice: dropped by firmware
            s = self.make()
            s.a.saver_start, s.dev.helper_mode = 'beanjar', mode
            with self.assertRaises(hc_e2e.beanjar.ScreensaverOutcome) as cm:
                s.screensaver_start()
            self.assertEqual(cm.exception.status, expected)

    def test_main_reboot_step_recovers_before_restart_and_blocks_failures(self):
        from unittest.mock import Mock
        for mode in ('success', 'fail', 'missing'):
            s = self.make()
            s.dev.select()
            s.dev.previous, s.dev.processing, s.dev.helper_mode = s.dev.fg, 'Screen Saver Ready', mode
            def restart(label):
                if mode == 'success':
                    self.assertFalse(s.screensaver_ready(s.screensaver_power()))
                    self.assertEqual(s.rec.results[-1]['status'], 'RECOVERED')
                return True
            s.beanjar_reboot = Mock(side_effect=restart)
            if mode == 'fail':
                with self.assertRaises(hc_e2e.beanjar.ScreensaverOutcome) as cm:
                    s.step_reboot()
                self.assertEqual(cm.exception.status, 'BLOCKED')
                s.beanjar_reboot.assert_not_called()
            else:
                s.step_reboot()
                s.beanjar_reboot.assert_called_once_with('3r')

    def test_recovery_precedes_each_reboot_alert_attempt(self):
        from unittest.mock import Mock
        s, events = self.make(), []
        s.press_alert = Mock(side_effect=lambda *args: events.append('press') or True)
        s.use_click = lambda: True
        outcomes = iter((False, 'rebooted'))
        def verify():
            events.append('verify')
            return next(outcomes)
        result = s.alert_action('message', 'Reboot', 'Later', 'test', None, verify,
                                lambda: True, lambda: events.append('request'),
                                before_action=lambda: events.append('recovery'))
        self.assertEqual(result, 'rebooted')
        self.assertEqual(events, ['recovery', 'press', 'verify', 'request', 'recovery', 'press', 'verify'])

    def test_cli_saver_start_defaults_and_accepts_beanjar(self):
        from unittest.mock import patch
        for choice in ([], ['--saver-start', 'beanjar']):
            with patch.object(sys, 'argv', ['runner', '--list'] + choice), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(hc_e2e.cli.main(), 0)

    def test_removal_race_runs_after_step_3_then_cleans_up(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        s, events = self.make(), []
        s.a = SimpleNamespace(elevation=None, input='beanjar', until=5, scenario='lifecycle',
                              api_check=False, reboot_check=False, update_check=False,
                              launcher_update_check=False, restore_repair_check=False, removal_race=True)
        s.release, s.strategy_name, s.sure, s.note = '11.x', 'luna', 'fake', 'offline state model'
        s.L.spec, s.L.fixture, s.L.ipk = 'fixture:web.normal', ('web', 'normal'), 'offline.ipk'
        s.L.id = s.launcher
        s.host.mode = 'offline'
        s.dev.release = lambda: (11, '11.2.0')
        s.dev.app_installed = lambda app: False
        s.state = lambda: {'choice': 'No such file'}
        s.check_no_crash = Mock()
        s.window_chosen, s.window_home = Mock(), Mock()
        for number in range(1, 7):
            setattr(s, 'step%d' % number, lambda *args, n=number: events.append(n))
        s.step_removal_race = lambda: events.append('3r')
        s.run()
        self.assertEqual(events, [1, 2, 3, '3r', 6])

    def test_removal_race_is_rejected_where_it_cannot_run(self):
        from unittest.mock import patch
        for argv in (['--vm', '11.x', '--removal-race', '--restore-repair-check'],
                     ['--vm', '11.x', '--removal-race', '--until', '2'],
                     ['--vm', '11.x', '--removal-race', '--scenario', 'all']):
            with self.subTest(argv=argv), patch.object(hc_e2e.cli, 'entry', side_effect=AssertionError('OFFLINE ONLY')), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    hc_e2e.cli.main(argv)
                self.assertEqual(cm.exception.code, 2)

    def test_main_recovery_failure_is_recorded_blocked_and_cleanup_still_runs(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        s = self.make()
        s.a = SimpleNamespace(elevation=None, input='beanjar', until=5, scenario='lifecycle',
                              api_check=False, reboot_check=True, update_check=False,
                              launcher_update_check=False, restore_repair_check=False)
        s.release, s.strategy_name, s.sure, s.note = '10.x', 'luna', 'fake', 'offline'
        s.L.spec, s.L.fixture, s.L.ipk = 'fixture:web.normal', ('web', 'normal'), 'offline.ipk'
        s.L.id = s.launcher
        s.host.mode = 'offline'
        s.dev.release = lambda: (10, '10.3.1')
        s.dev.app_installed = lambda app: False
        s.state = lambda: {'choice': 'No such file'}
        s.check_no_crash = Mock()
        s.window_chosen, s.window_home = Mock(), Mock()
        for number in range(1, 7):
            setattr(s, 'step%d' % number, Mock())
        s.step1.side_effect = lambda strategy: s.dev.select()
        def stale():
            s.dev.processing, s.dev.helper_mode = 'Screen Saver Ready', 'fail'
        s.step3.side_effect = stale
        s.beanjar_reboot = Mock()
        s.run()
        s.beanjar_reboot.assert_not_called()
        s.step4.assert_not_called()
        s.step6.assert_called_once()
        self.assertEqual([row['status'] for row in s.rec.results if row['name'] == 'tvpower reboot readiness'], ['BLOCKED'])
        self.assertFalse(any(row['status'] == 'FAIL' for row in s.rec.results))


    def test_stock_observations_drive_both_home_wake_and_direct_home_cases(self):
        from unittest.mock import patch
        # Device evidence: stock 7.6.0/9.2.1 wakes to hbchannel. Direct HOME is modeled
        # too; neither behavior is selected by a firmware-version hand list.
        for major in (6, 7, 9, 11):
            for wake_only in (True, False):
                with self.subTest(major=major, wake_only=wake_only), patch.object(hc_e2e.timing, 'sleep'):
                    s = self.make(major)
                    s.dev.home_wakes = wake_only
                    self.assertEqual(s.screensaver_baseline(), 'PASS')
                    measured = json.loads(next(row['evidence'] for row in s.rec.results
                                               if row['name'] == 'S0 home and screensaver'))
                    self.assertEqual(measured['HOME']['wake'][0], 'org.webosbrew.hbchannel' if wake_only else HOME)
                    self.assertEqual(measured['HOME']['home'][0], HOME)
                    self.assertEqual(measured['BACK']['wake'][0], 'org.webosbrew.hbchannel')
                    self.assertEqual(measured['BACK']['home'][0], HOME)
                    self.assertEqual(self.run_scenario(s), {1: 'PASS', 2: 'PASS', 3: 'PASS', 4: 'PASS'})
                    labels = [call.args[0] for call in s.shot.call_args_list]
                    self.assertEqual('S2-home' in labels, wake_only)
                    self.assertEqual(s.dev.fg, s.launcher)

    def test_mismatched_wake_behavior_fails_against_measured_stock_baseline(self):
        from unittest.mock import patch
        for stock_wakes in (True, False):
            with self.subTest(stock_wakes=stock_wakes), patch.object(hc_e2e.timing, 'sleep'):
                s = self.make(9)
                s.dev.home_wakes = stock_wakes
                self.assertEqual(s.screensaver_baseline(), 'PASS')
                s.dev.home_wakes = not stock_wakes
                self.assertEqual(self.run_scenario(s)[2], 'FAIL')

    def test_back_destination_also_comes_from_stock_instead_of_assuming_previous_app(self):
        from unittest.mock import patch
        s = self.make(7)
        s.dev.back_goes_home = True
        with patch.object(hc_e2e.timing, 'sleep'):
            self.assertEqual(s.screensaver_baseline(), 'PASS')
            self.assertEqual(s.screensaver_stock['BACK']['wake'][0], HOME)
            self.assertEqual(self.run_scenario(s)[3], 'PASS')

    def test_second_home_must_dispatch_chosen_launcher_after_stock_wake_only(self):
        s = self.make(9, fault=(2, 'no_home_dispatch'))
        s.dev.home_wakes = True
        self.assertEqual(self.run_scenario(s)[2], 'FAIL')
        row = next(row for row in s.rec.results if row['name'] == 'S2 home and screensaver')
        self.assertIn('second HOME did not match S0', row['evidence'])

    def test_stock_second_home_without_home_dispatch_is_observed_without_inventing_a_target(self):
        from unittest.mock import patch
        s = self.make(9, fault=(0, 'no_home_dispatch'))
        s.dev.home_wakes = True
        with patch.object(hc_e2e.timing, 'sleep'):
            self.assertEqual(s.screensaver_baseline(), 'PASS')
        self.assertEqual(s.screensaver_stock['HOME']['home'][0], 'org.webosbrew.hbchannel')
        # A measured previous-app result maps to the previous app, not a fabricated stock HOME.
        self.assertEqual(s.screensaver_expected('HOME', 'home', 'org.example.previous'), 'org.example.previous')

    def test_6x_uses_legacy_power_and_singular_foreground_without_modern_routes(self):
        s = self.make(6)
        s.dev.home_wakes = True
        self.assertEqual(self.run_scenario(s), {1: 'PASS', 2: 'PASS', 3: 'PASS', 4: 'PASS'})
        calls = [call[1] for call in s.dev.calls if isinstance(call, tuple)]
        self.assertIn(hc_e2e.constants.TVPOWER + 'power/getPowerState', calls)
        self.assertIn(hc_e2e.constants.TVPOWER + 'power/turnOnScreenSaver', calls)
        self.assertIn('luna://com.webos.applicationManager/getForegroundAppInfo', calls)
        self.assertFalse(any('/power2/' in uri or uri.endswith('/getForegroundApps') for uri in calls))
        self.assertIsNone(s.screensaver_power()['power2'][1])

    def test_6x_requires_real_shown_state_and_never_accepts_ready_or_trigger_success(self):
        for fault, expected in (('ready', 'BLOCKED'), ('timeout', 'FAIL')):
            s = self.make(6, fault=(0, fault))
            self.assertEqual(s.screensaver_baseline(), expected)
            self.assertFalse(s.screensaver_shown(s.screensaver_power()))
        s = self.make(6)
        s.dev.substate = 'screen saver'
        self.assertTrue(s.screensaver_shown(s.screensaver_power()))
        s.dev.processing = 'Screen Saver Ready'
        self.assertFalse(s.screensaver_shown(s.screensaver_power()))



class BeanjarScreensaverHelper(unittest.TestCase):
    def host(self, mode='remote'):
        from unittest.mock import Mock
        host = hc_e2e.beanjar.BeanjarHost(mode=mode, name='pi')
        host.display_beanjar = Mock(return_value=(0, 'help'))
        return host

    def test_help_is_probed_once_and_uses_local_multi_host_cli_with_bounded_calls(self):
        for mode, target in (('remote', 'pi/vm'), ('local', 'local/vm')):
            with self.subTest(mode=mode):
                host = self.host(mode)
                self.assertEqual(host.screensaver('stop', 'vm'), (0, 'help'))
                host.screensaver('start', 'vm')
                self.assertEqual([call.args for call in host.display_beanjar.call_args_list],
                                 [('screensaver', '--help'),
                                  ('screensaver', 'stop', target, '--timeout', '60', '--json'),
                                  ('screensaver', 'start', target, '--timeout', '60', '--json')])
                self.assertEqual([call.kwargs['timeout'] for call in host.display_beanjar.call_args_list], [15, 90, 90])

    def test_old_cli_missing_command_is_cached_without_running_start_or_stop(self):
        for rc, raw in ((2, "beanjar: error: invalid choice: 'screensaver'"), (127, 'beanjar not found')):
            host = self.host()
            host.display_beanjar.return_value = rc, raw
            self.assertIsNone(host.screensaver('stop', 'vm'))
            self.assertIsNone(host.screensaver('start', 'vm'))
            host.display_beanjar.assert_called_once_with('screensaver', '--help', timeout=15)

    def test_other_probe_errors_are_failures_rather_than_missing_subcommand(self):
        host = self.host()
        host.display_beanjar.return_value = 124, 'help timed out'
        self.assertEqual(host.screensaver('stop', 'vm'), (124, 'help timed out'))
        host.display_beanjar.assert_called_once()


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

    def test_dvb_crash_is_a_platform_crash_for_the_reference_note(self):
        s = scenario()
        snap = s.compositor_snapshot()
        s.dev.messages = KnownDvbCrash.CRASHD + '\n'
        s.dev.fault_reply = KnownDvbCrash().reply(KnownDvbCrash.NOISE, KnownDvbCrash.FAULT)
        s.check_no_crash('x', snap)
        self.assertEqual([ok for _, ok in s.rec.checks], [True, True])
        self.assertEqual([hc_e2e.evidence.crash_process(e) for _, e in s.platform_crashes], ['legacy-broadcas.4892'])

    def test_a_wam_crash_next_to_a_platform_crash_still_fails(self):
        for extra, ok in (('', True), ('x crashd CRASHED {"process":"WebAppMgr.1"}\n', False),
                          ('x crashd CRASHED {"process":"homechooser-d.77"}\n', False)):
            s = scenario()
            snap = s.compositor_snapshot()
            s.dev.messages = KnownDvbCrash.CRASHD + '\n' + extra
            s.dev.fault_reply = ''
            s.check_no_crash('x', snap)
            self.assertEqual([ok_ for _, ok_ in s.rec.checks], [ok, True], extra)
            self.assertEqual(len(s.platform_crashes), 1)

    def test_only_6_to_9(self):
        for major in (5, 10, 12):
            s = scenario(major)
            s.check_no_compositor_restart('x', {'boot': 'b1'})
            s.check_no_crash('x', {'boot': 'b1'})
            self.assertEqual(s.rec.checks, [])


class ApiLaunchers(unittest.TestCase):
    def test_api_check_needs_a_web_fixture_or_an_author_launcher(self):
        with self.assertRaises(SystemExit):
            hc_e2e.launchers.make_launcher('fixture:native.normal', api=True)
        with self.assertRaises(SystemExit):
            hc_e2e.launchers.make_launcher('fixture:qml.overlay', api=True)
        orig = hc_e2e.launchers.api_fixture_ipks
        hc_e2e.launchers.api_fixture_ipks = lambda announce=print: {hc_e2e.launchers.FIXTURE_ID % ('web', 'normal'): '/nonexistent/web.ipk'}
        try:
            L = hc_e2e.launchers.make_launcher('fixture:web.normal', api=True)
        finally:
            hc_e2e.launchers.api_fixture_ipks = orig
        self.assertEqual((L.id, L.ipk, L.is_fixture), ('org.webosbrew.hcfixture.web.normal', '/nonexistent/web.ipk', True))
        author = hc_e2e.launchers.make_launcher('org.example.launcher', '/nonexistent/author.ipk', api=True)
        self.assertEqual((author.id, author.is_fixture), ('org.example.launcher', False))
        self.assertEqual((author.spec, author.kind, author.tag), ('org.example.launcher', 'app', 'org.example.launcher'))
        with self.assertRaises(SystemExit):  # an app id without --launcher-ipk, and no special ids
            hc_e2e.launchers.make_launcher('org.webosbrew.dock')

    def test_no_dock_special_case_is_left(self):
        self.assertFalse([m for m, mod in sys.modules.items() if m.startswith('hc_e2e')
                          and (hasattr(mod, 'DOCK_IPK') or hasattr(mod, 'DOCK_ID'))])
        for src in (hc_e2e.cli.main, run_matrix.build_once, run_matrix.runner_command):
            self.assertNotIn('dock', __import__('inspect').getsource(src).lower())

    def test_disposable_fixture_declares_no_home_and_no_permission(self):
        with open(os.path.join(HERE, 'fixtures', 'disposable', 'build.sh')) as f:
            body = f.read()
        self.assertIn(hc_e2e.api_spec.DISPOSABLE_ID, body)
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
        host = hc_e2e.beanjar.BeanjarHost()
        directory = "/tmp/vm ' $(false); space"
        key = "ret' ; $(false)"
        host._dirs['vm'] = directory
        host.sh = Mock(return_value=(0, 'ok'))
        self.assertEqual(host.sendkey('vm', key), (0, 'ok'))
        command, timeout = host.sh.call_args.args
        self.assertEqual(shlex.split(command),
                         ['python3', '-c', hc_e2e.beanjar.QMP_SENDKEY, directory + '/run/qmp.sock', key])
        self.assertEqual(timeout, 30)

    def test_failed_press_stops_sequence_for_both_inputs(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        for mode in ('qmp', 'ares'):
            with self.subTest(mode=mode):
                scenario = object.__new__(hc_e2e.scenario.Scenario)
                scenario.a = SimpleNamespace(input=mode)
                scenario.vm = 'vm'
                scenario.host = SimpleNamespace(name='rk1', sendkey=Mock(return_value=(1, 'socket refused')))
                scenario.dev = SimpleNamespace(root='vm-root', key=Mock(return_value=(1, 'socket refused')))
                scenario.rec = Mock()
                with self.assertRaisesRegex(hc_e2e.beanjar.KeySendError, 'key OK.*socket refused'):
                    scenario.press('OK', 'DOWN')
                sender = scenario.host.sendkey if mode == 'qmp' else scenario.dev.key
                self.assertEqual(sender.call_count, 1)
                self.assertFalse(scenario.rec.check.call_args.args[1])

    def beanjar_host(self):
        from unittest.mock import Mock
        host = hc_e2e.beanjar.BeanjarHost(mode='remote', name='rk1')
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
        s = object.__new__(hc_e2e.scenario.Scenario)
        s.a, s.host, s.vm, s.rec = SimpleNamespace(input='beanjar'), host, 'vm', Mock()
        with patch.object(hc_e2e.timing, 'SLOWDOWN', 1.5), patch.object(hc_e2e.timing, 'sleep'):
            s.press('OK', 'ENTER', 'BACK', 'HOME', 'UP', 'DOWN', 'LEFT', 'RIGHT', *'0123456789')
        calls = [c.args for c in host.display_beanjar.call_args_list]
        self.assertEqual(calls[0], ('remote', 'layout', 'rk1/vm', '--json'))
        self.assertEqual(sum(c[:2] == ('remote', 'layout') for c in calls), 1)
        self.assertEqual([c[3] for c in calls[1:]],
                         ['ENTER', 'ENTER', 'BACK', 'HOME', 'UP', 'DOWN', 'LEFT', 'RIGHT', *'0123456789'])
        self.assertTrue(all(c[-2:] == ('--hold', '150') for c in calls[1:]))
        host.mode = 'local'
        self.assertEqual(host.remote_target('vm'), 'local/vm')

    def test_beanjar_missing_and_dimmed_keys_fail_before_press(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        for rows, key in [([{'name': 'HOME', 'code': 1}], 'OK'),
                          ([{'name': k, 'code': None if k == 'ENTER' else 1}
                            for k in ['ENTER', 'BACK', 'HOME', 'UP', 'DOWN', 'LEFT', 'RIGHT']], 'OK')]:
            host = self.beanjar_host()
            host.display_beanjar.return_value = (0, json.dumps({'keys': rows}))
            host.display_beanjar.side_effect = None
            s = object.__new__(hc_e2e.scenario.Scenario)
            s.a, s.host, s.vm, s.rec = SimpleNamespace(input='beanjar'), host, 'vm', Mock()
            with self.assertRaisesRegex(hc_e2e.beanjar.KeySendError, 'rk1/vm.*missing keys.*ENTER'):
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
        s = object.__new__(hc_e2e.scenario.Scenario)
        s.a, s.host, s.vm, s.rec = SimpleNamespace(input='beanjar'), host, 'vm', Mock()
        with self.assertRaisesRegex(hc_e2e.beanjar.KeySendError, 'key OK.*ENTER.*rk1/vm.*display refused'):
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
            ('start', 'local/vm'), ('vnc', 'start'), ('vnc', 'stop'), ('stop', 'local/vm'),
            ('start', 'local/vm'), ('vnc', 'start'), ('vnc', 'stop')])
        host.display_beanjar.side_effect = None
        host.display_beanjar.return_value = (1, 'beanjar-vnc is already serving vm (pid 123)')
        host.vnc_start('vm')
        host.input_rec.check.assert_not_called()
        host.display_beanjar.return_value = (1, 'cannot bind')
        with self.assertRaisesRegex(hc_e2e.beanjar.KeySendError, 'cannot bind'):
            host.vnc_start('vm')
        host.stop('vm')  # even failed VNC cleanup must still stop the VM
        self.assertEqual(events[-1], ('stop', 'local/vm'))

    def test_await_boot_restarts_vnc_before_returning_to_keys(self):
        from unittest.mock import Mock, patch
        s = object.__new__(hc_e2e.scenario.Scenario)
        s.vm, s.host, s.rec = 'vm', self.beanjar_host(), Mock()
        s.host.info = Mock(return_value={'lan_address': 'example'})
        s.dev = Mock()
        s.dev.boot_id.return_value = 'new'
        with patch.object(hc_e2e.steps.state, 'wait_for_device', return_value=hc_e2e.preflight.READY):
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
                    for name in ['prepare_vm', 'clean_launchers', 'restart_after_launcher_cleanup']:
                        stack.enter_context(patch.object(hc_e2e.cli, name))
                    stack.enter_context(patch.object(hc_e2e.cli, 'wait_for_device', return_value=hc_e2e.preflight.READY))
                    stack.enter_context(patch.object(hc_e2e.cli, 'load_hosts', return_value={'default': 'rk1'}))
                    stack.enter_context(patch.object(hc_e2e.cli, 'make_launcher', return_value=SimpleNamespace(spec='test', id='test')))
                    # no build/ipk in a clean checkout: current_home_chooser_ipk would run npm
                    stack.enter_context(patch.object(hc_e2e.cli, 'current_home_chooser_ipk', return_value='/fake/hc.ipk'))
                    stack.enter_context(patch.object(hc_e2e.beanjar.BeanjarHost, 'from_config', return_value=host))
                    stack.enter_context(patch.object(hc_e2e.cli, 'Recorder', return_value=rec))
                    stack.enter_context(patch.object(hc_e2e.cli, 'Scenario', return_value=scenario))
                    stack.enter_context(patch.object(os, 'makedirs'))
                    stack.enter_context(patch.object(shutil, 'which', return_value='/fake/ares-do'))
                    stack.enter_context(patch.object(signal, 'signal'))
                    if isinstance(failure, KeyboardInterrupt):
                        with self.assertRaises(KeyboardInterrupt):
                            hc_e2e.cli.main()
                    else:
                        hc_e2e.cli.main()
                self.assertEqual([e[:2] for e in events], [('start', 'local/w24g-ombre'),
                                 ('vnc', 'start'), ('vnc', 'stop')] +
                                 ([] if no_stop else [('stop', 'local/w24g-ombre')]))

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
                    host = hc_e2e.beanjar.BeanjarHost(mode='local')
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
            host = hc_e2e.beanjar.BeanjarHost(mode='local')
            host._dirs['vm'] = directory
            rc, output = host.sendkey('vm', 'ret')
            self.assertNotEqual(rc, 0)
            self.assertIn('QMP sendkey failed', output)


class DeviceAccess(unittest.TestCase):
    """Every ares command line comes from Device.argv: a VM through `beanjar ares-TOOL [--root] -d [HOST/]NAME`,
    an author's TV through plain `ares-TOOL -d NAME`."""

    def calls(self, dev, fn, reply=(0, 'ok')):
        from unittest.mock import patch
        with patch.object(hc_e2e.timing, 'run', return_value=reply) as run:
            fn(dev)
        return [c.args[0] for c in run.call_args_list]

    def test_vm_argv(self):
        d = hc_e2e.device.Device('rk1/vm')
        self.assertEqual(d.argv('install', ['-l']), ['beanjar', 'ares-install', '-d', 'rk1/vm', '-l'])
        self.assertEqual(d.argv('shell', ['-r', 'id'], root=True),
                         ['beanjar', 'ares-shell', '--root', '-d', 'rk1/vm', '-r', 'id'])
        # shell commands share one root shell per VM (RootSession)
        self.assertEqual(d.session().argv, ['beanjar', 'ares-shell', '--root', '-d', 'rk1/vm', '-r', 'sh'])
        self.assertIs(hc_e2e.device.Device('rk1/vm').session(), d.session())
        self.assertIsNot(hc_e2e.device.Device('rk2/vm').session(), d.session())
        from unittest.mock import patch
        with patch.object(hc_e2e.device.RootSession, 'run', return_value=(0, 'up')) as session_run:
            self.assertEqual(d.sh('echo up'), (0, 'up'))
            d.exec('id -u', 20)
        self.assertEqual([c.args for c in session_run.call_args_list], [('echo up', 60), ('id -u', 20)])
        # developer-mode user (prisoner), as the plain device was before
        self.assertEqual(self.calls(d, lambda x: x.install('/x/hc.ipk')),
                         [['beanjar', 'ares-install', '-d', 'rk1/vm', '/x/hc.ipk']])
        self.assertEqual(self.calls(d, lambda x: x.remove('a.b')),
                         [['beanjar', 'ares-install', '-d', 'rk1/vm', '--remove', 'a.b']])
        self.assertEqual(self.calls(d, lambda x: x.launch('a.b', {'request': 'c'})),
                         [['beanjar', 'ares-launch', '-d', 'rk1/vm', 'a.b', '-p', '{"request": "c"}']])
        self.assertEqual(self.calls(d, lambda x: x.close('a.b')),
                         [['beanjar', 'ares-launch', '-d', 'rk1/vm', '-c', 'a.b']])
        self.assertEqual(self.calls(d, lambda x: x.key('HOME')),
                         [['beanjar', 'ares-do', '--root', '-d', 'rk1/vm', 'key', '--delay', '300', 'HOME']])
        self.assertEqual(self.calls(d, lambda x: x.screenshot('/tmp/a.png')),
                         [['beanjar', 'ares-do', '--root', '-d', 'rk1/vm', 'screenshot', '/tmp/a.png']])
        self.assertNotIn('-root', ' '.join(d.argv('shell', root=True)).replace('--root', ''))

    def test_vm_target_follows_the_host(self):
        local = hc_e2e.beanjar.BeanjarHost('local', 'localhost', 'here')
        remote = hc_e2e.beanjar.BeanjarHost('remote', 'mariotaku@rockpi4a-1.lan', 'rockpi4a-1')
        self.assertEqual(hc_e2e.device.Device.for_vm(local, 'vm').target, 'local/vm')
        self.assertEqual(hc_e2e.device.Device.for_vm(remote, 'vm').target, 'rockpi4a-1/vm')
        self.assertEqual(hc_e2e.device.Device.for_vm(remote, 'vm').vm, 'rockpi4a-1/vm')

    def test_tv_argv(self):
        d = hc_e2e.device.Device.tv('tv')
        self.assertIsNone(d.session())
        self.assertEqual(self.calls(d, lambda x: x.sh('echo up')), [['ares-shell', '-d', 'tv', '-r', 'echo up']])
        self.assertEqual(self.calls(d, lambda x: x.exec('id -u', 20)),
                         [['ares-do', '-d', 'tv', '--timeout', '20000', 'exec', 'id -u']])
        self.assertEqual(d.argv('install', ['-l']), ['ares-install', '-d', 'tv', '-l'])
        self.assertEqual(d.argv('do', ['exec', 'id'], root=True), ['ares-do', '-d', 'tv', 'exec', 'id'])
        split = hc_e2e.device.Device.tv('tv', 'tv-root')
        self.assertEqual(split.argv('shell', ['-r', 'id'], root=True), ['ares-shell', '-d', 'tv-root', '-r', 'id'])
        self.assertEqual(split.argv('novacom', ['-f']), ['ares-novacom', '-d', 'tv', '-f'])

    def test_forward_and_stream(self):
        from unittest.mock import patch
        with patch.object(subprocess, 'Popen') as popen:
            hc_e2e.device.Device('rk1/vm').forward(9998, 4321)
            hc_e2e.device.Device('rk1/vm').stream('exec ls-monitor')
        self.assertEqual(popen.call_args_list[0].args[0],
                         ['beanjar', 'ares-novacom', '-d', 'rk1/vm', '-f', '-p', '9998:4321'])
        self.assertEqual(popen.call_args_list[1].args[0],
                         ['beanjar', 'ares-shell', '--root', '-d', 'rk1/vm', '-r', 'exec ls-monitor'])
        for c in popen.call_args_list:
            self.assertTrue(c.kwargs['start_new_session'])
            self.assertEqual(c.kwargs['stdin'], subprocess.DEVNULL)

    def test_run_never_hands_its_stdin_to_a_tool(self):
        from unittest.mock import patch, Mock
        with patch.object(subprocess, 'run', return_value=Mock(returncode=0, stdout=b'')) as r:
            hc_e2e.timing.run(['true'])
            hc_e2e.timing.run(['cat'], input=b'x')
        self.assertEqual(r.call_args_list[0].kwargs['stdin'], subprocess.DEVNULL)
        self.assertIsNone(r.call_args_list[1].kwargs['stdin'])


class RootSessionShell(unittest.TestCase):
    """The shared root shell, driven here with a local `sh` in place of `beanjar ares-shell --root -r sh`."""

    def setUp(self):
        self.s = hc_e2e.device.RootSession(['sh'])
        self.addCleanup(self.s.close)

    def test_status_output_and_one_process(self):
        self.assertEqual(self.s.run('echo up'), (0, 'up\n'))
        pid = self.s.proc.pid
        self.assertEqual(self.s.run('printf no-newline; exit 3'), (3, 'no-newline'))
        self.assertEqual(self.s.run('echo err >&2; false'), (1, 'err\n'))
        self.assertEqual(self.s.run("printf '%s\\n' \"it's\" 'a \"b\"'"), (0, "it's\na \"b\"\n"))
        self.assertEqual(self.s.run('exit 0'), (0, ''))
        self.assertEqual(self.s.proc.pid, pid)

    def test_a_command_never_reads_the_session_stdin(self):
        self.assertEqual(self.s.run('cat; echo after'), (0, 'after\n'))
        self.assertEqual(self.s.run('echo next'), (0, 'next\n'))

    def test_timeout_ends_the_session_and_the_next_command_opens_one(self):
        from unittest.mock import patch
        with patch.object(hc_e2e.timing, 'SLOWDOWN', 1.0):
            rc, out = self.s.run('echo partial; sleep 5', timeout=1)
        self.assertEqual(rc, 124)
        self.assertIn('partial', out)
        self.assertIn('[timeout]', out)
        self.assertIsNone(self.s.proc)
        self.assertEqual(self.s.run('echo back'), (0, 'back\n'))

    def test_a_session_that_died_is_reported_then_replaced(self):
        self.s.run('true')
        self.s.proc.kill()
        self.s.proc.wait()
        self.assertEqual(self.s.run('echo again'), (0, 'again\n'))
        dead = hc_e2e.device.RootSession(['sh', '-c', 'echo "no such VM" >&2; exit 1'])
        self.addCleanup(dead.close)
        self.assertEqual(dead.run('echo up'), (1, 'no such VM\n'))


class LunaThroughPty(unittest.TestCase):
    """luna-send -n 1 printed nothing to a pipe over ares-shell -r on W22O 10.3.1; under `script` its reply
    ends in CRLF.  The command is run here with a fake luna-send (and a fake script that turns LF into
    CRLF, as a pty does), so the quoting is what a guest shell sees."""

    REPLY = '{"returnValue":true,"appId":"com.webos.app.home"}'

    def guest(self, cmd, with_script=True):
        d = tempfile.mkdtemp(prefix='hc-luna-test-')
        self.addCleanup(lambda: __import__('shutil').rmtree(d, ignore_errors=True))
        with open(os.path.join(d, 'luna-send'), 'w') as f:
            # keep the arguments it got, one per line, then print the reply
            f.write('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > "${0%/*}/args"\n'
                    'printf \'%s\\n\' \'' + self.REPLY + '\'\n')
        if with_script:
            with open(os.path.join(d, 'script'), 'w') as f:
                f.write('#!/bin/sh\n[ "$1" = -q ] && [ "$2" = -c ] && [ "$4" = /dev/null ] || exit 64\n'
                        'printf "Script started, output log file is \'/dev/null\'.\\r\\n"\n'
                        '/bin/sh -c "$3" | sed "s/$/\\r/"\n')
        for n in os.listdir(d):
            os.chmod(os.path.join(d, n), 0o755)
        env = {'PATH': d + ':/usr/bin:/bin'} if with_script else {'PATH': d}
        p = subprocess.run(['/bin/sh', '-c', cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        with open(os.path.join(d, 'args')) as f:
            self.args = f.read().splitlines()
        return p.stdout.decode()

    def test_script_form_quotes_and_parses_with_crlf_and_a_banner(self):
        payload = '{"id":"it\'s \\"x\\"","n":1}'
        out = self.guest(hc_e2e.device.luna_send_command('luna://a.b/c', payload, 'com.webos.applicationManager'))
        self.assertIn('\r\n', out)
        self.assertIn('Script started', out)
        self.assertEqual(self.args, ['-a', 'com.webos.applicationManager', '-n', '1', 'luna://a.b/c', payload])
        self.assertEqual(hc_e2e.evidence.parse_luna(hc_e2e.device.clean_reply(out)), json.loads(self.REPLY))
        self.assertEqual(json.loads(hc_e2e.device.luna_reply_text(out)), json.loads(self.REPLY))
        self.assertNotIn('\r', hc_e2e.device.clean_reply(out))

    def test_no_script_on_the_guest_runs_plainly_and_says_so(self):
        cmd = hc_e2e.device.luna_send_command('luna://a.b/c')
        # the fake PATH holds luna-send only: `command -v` is the shell's own, so no script is found
        out = self.guest(cmd, with_script=False)
        self.assertIn(hc_e2e.device.NO_SCRIPT_NOTE, out)
        self.assertEqual(hc_e2e.evidence.parse_luna(out), json.loads(self.REPLY))
        self.assertNotIn('script', hc_e2e.device.luna_send_command('luna://a.b/c', pty=False))

    def test_reply_text(self):
        self.assertEqual(hc_e2e.device.luna_reply_text(self.REPLY + '\r\n'), self.REPLY)
        self.assertEqual(json.loads(hc_e2e.device.luna_reply_text('^D\x08\x08' + self.REPLY + '\r\n')), json.loads(self.REPLY))
        self.assertEqual(hc_e2e.device.luna_reply_text('[timeout]\r\n'), '[timeout]')
        self.assertEqual(hc_e2e.device.luna_reply_text(''), '')

    def probe(self, replies):
        from unittest.mock import Mock
        d = hc_e2e.device.Device('rk1/vm')
        d.say = Mock()
        sent = []
        d.exec = lambda cmd, timeout=60: (sent.append(cmd), replies.pop(0))[1]
        return d, sent

    def test_probe_prefers_the_pty_and_never_repeats_a_call(self):
        d, sent = self.probe([(0, self.REPLY + '\r\n'), (0, '\r\n')])
        rc, raw, parsed = d.luna_as('com.webos.applicationManager', 'luna://com.webos.service.tvpower/power/reboot')
        self.assertTrue(d.luna_pty)
        self.assertEqual(len(sent), 2)  # probe + the call, the empty reply not retried
        self.assertTrue(all('script -q -c' in c for c in sent))
        self.assertIn('getForegroundAppInfo', sent[0])
        self.assertIsNone(parsed)

    def test_probe_falls_back_to_plain_when_script_prints_nothing(self):
        d, sent = self.probe([(0, ''), (0, self.REPLY + '\n'), (0, self.REPLY + '\n')])
        self.assertEqual(json.loads(d.luna('luna://a.b/c')), json.loads(self.REPLY))
        self.assertIs(d.luna_pty, False)
        self.assertIn('script -q -c', sent[0])
        self.assertNotIn('script', sent[1] + sent[2])

    def test_probe_without_script_on_the_guest(self):
        d, sent = self.probe([(0, hc_e2e.device.NO_SCRIPT_NOTE + '\n' + self.REPLY + '\n'), (0, self.REPLY + '\n')])
        d.luna('luna://a.b/c')
        self.assertIs(d.luna_pty, False)
        self.assertEqual(len(sent), 2)
        self.assertIn(hc_e2e.device.NO_SCRIPT_NOTE, d.say.call_args_list[0].args[0])

    def test_probe_is_retried_while_the_device_gives_no_reply(self):
        d, sent = self.probe([(255, ''), (255, ''), (255, ''),
                              (0, self.REPLY + '\r\n'), (0, self.REPLY + '\r\n')])
        d.luna('luna://a.b/c')
        self.assertIsNone(d.luna_pty)
        self.assertEqual(json.loads(d.luna('luna://a.b/c')), json.loads(self.REPLY))
        self.assertTrue(d.luna_pty)


# ---------------------------------------------------------------------------------------------
# Scopes, the run plan, screenshots and polling (e2e/README.md "Scopes", "Tiers")
# ---------------------------------------------------------------------------------------------
WEB_N, WEB_O = ('web', 'normal', False), ('web', 'overlay', False)
ALL6 = [WEB_N, WEB_O, ('native', 'normal', False), ('native', 'overlay', False),
        ('qml', 'normal', False), ('qml', 'overlay', False)]
RELEASE_OPTS = {'api': 'smoke', 'reboot': 'auto', 'until': 5}
FULL_OPTS = {'api': 'full', 'reboot': 'auto', 'update': True, 'launcher_update': True, 'until': 5}


class Scopes(unittest.TestCase):
    def test_the_table_has_one_row_per_phase_and_three_scopes(self):
        names = [row[0] for row in hc_e2e.plan.PHASES]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual({row[1] for row in hc_e2e.plan.PHASES}, {hc_e2e.plan.PER_RELEASE, hc_e2e.plan.PER_WINDOW, hc_e2e.plan.PER_RUNTIME})
        self.assertEqual(hc_e2e.plan.PHASE['home-proof']['scope'], hc_e2e.plan.PER_RUNTIME)
        self.assertEqual(hc_e2e.plan.PHASE['qml-gate']['scope'], hc_e2e.plan.PER_RUNTIME)
        for phase in ('window-chosen', 'window-home', 'reboot'):
            self.assertEqual(hc_e2e.plan.PHASE[phase]['scope'], hc_e2e.plan.PER_WINDOW, phase)
        for phase in ('baseline', 'install', 'choose', 'api-selected', 'update', 'removal',
                      'uninstall', 'cleanup'):
            self.assertEqual(hc_e2e.plan.PHASE[phase]['scope'], hc_e2e.plan.PER_RELEASE, phase)

    def test_every_phase_is_planned_somewhere_and_run_knows_it(self):
        seen = set()
        for major in (5, 9, 10, 12):
            for opts in (RELEASE_OPTS, FULL_OPTS, dict(FULL_OPTS, update=False), dict(RELEASE_OPTS, screensaver='all'),
                         dict(RELEASE_OPTS, restore_repair=True), dict(RELEASE_OPTS, removal_race=True)):
                seen |= {p for _, p in hc_e2e.plan.run_plan(ALL6 + [('web', 'normal', True)], major, opts)}
        self.assertEqual(seen, set(hc_e2e.plan.PHASE))
        inspect = __import__('inspect')
        runners = inspect.getsource(hc_e2e.scenario.Scenario.begin_run)  # the phase runners
        for phase in hc_e2e.plan.PHASE:
            self.assertIn("'%s'" % phase, inspect.getsource(hc_e2e.scenario.Scenario.end_run)
                          if phase == 'cleanup' else runners, phase)

    def test_release_tier_plan_per_release_once_overlay_only_its_scopes(self):
        for major in (5, 6, 9, 10, 11, 12):
            plan = hc_e2e.plan.run_plan([WEB_N, WEB_O], major, RELEASE_OPTS)
            with self.subTest(major=major):
                self.assertEqual(plan[0], (0, 'baseline'))
                self.assertEqual(plan[-2:], [(0, 'uninstall'), (0, 'cleanup')])
                overlay = [p for i, p in plan if i == 1]
                self.assertEqual(overlay[:3], ['switch', 'window-chosen', 'home-proof'])
                self.assertTrue(all(hc_e2e.plan.PHASE[p]['scope'] != hc_e2e.plan.PER_RELEASE for p in overlay), overlay)
                for phase in ('install', 'choose', 'api-selected', 'removal', 'uninstall'):
                    self.assertEqual([i for i, p in plan if p == phase], [0], phase)
                self.assertEqual([i for i, p in plan if p == 'window-chosen'], [0, 1])
                self.assertEqual([i for i, p in plan if p == 'window-home'], [0, 1] if major >= 10 else [])
                self.assertEqual([i for i, p in plan if p == 'home-proof'], [0, 1])
                self.assertNotIn('update', [p for _, p in plan])

    def test_full_tier_six_launchers_one_boot_scopes_once(self):
        for major in (5, 9, 10, 11, 12):
            plan = hc_e2e.plan.run_plan(ALL6, major, FULL_OPTS)
            with self.subTest(major=major):
                proofs = [i for i, p in plan if p == 'home-proof']
                gates = [i for i, p in plan if p == 'qml-gate']
                self.assertEqual(sorted(proofs + gates), list(range(6)))
                self.assertEqual(gates, [4, 5] if major == 12 else [])
                self.assertEqual([i for i, p in plan if p == 'window-chosen'], [0, 1])  # web.normal, web.overlay
                self.assertEqual([i for i, p in plan if p == 'switch'], [i for i in range(1, 6) if i not in gates])
                # a later launcher of the same window type: switch and launch proof only
                for i in (2, 3):
                    self.assertEqual([p for j, p in plan if j == i], ['switch', 'home-proof'])
                self.assertEqual([i for i, p in plan if p == 'update'], [0])
                self.assertEqual([i for i, p in plan if p == 'launcher-update'], [0])

    def test_a_later_launcher_of_a_new_window_type_owns_its_window_checks(self):
        plan = hc_e2e.plan.run_plan([('native', 'normal', False), WEB_O, ('native', 'overlay', False)], 10, RELEASE_OPTS)
        self.assertEqual([p for i, p in plan if i == 1], ['switch', 'window-chosen', 'home-proof', 'window-home', 'reboot'])
        self.assertEqual([p for i, p in plan if i == 2], ['switch', 'home-proof'])

    def test_reboot_plan(self):
        n, o = 'normal', 'overlay'
        self.assertEqual(hc_e2e.plan.reboot_windows(9, [n, o], None), [])
        self.assertEqual(hc_e2e.plan.reboot_windows(5, [n, o], 'auto'), [n])
        self.assertEqual(hc_e2e.plan.reboot_windows(9, [o, n], 'auto'), [n])  # the full-screen one, wherever it is
        self.assertEqual(hc_e2e.plan.reboot_windows(9, [o], 'auto'), [o])     # a lone overlay launcher still boots
        self.assertEqual(hc_e2e.plan.reboot_windows(10, [n, o], 'auto'), [n, o])
        self.assertEqual(hc_e2e.plan.reboot_windows(12, [o, n], 'auto'), [o, n])
        self.assertEqual(hc_e2e.plan.reboot_windows(9, [n, o, n], 'all'), [n, o])
        for major in range(5, 13):
            plan = hc_e2e.plan.run_plan(ALL6, major, FULL_OPTS)
            reboots = [i for i, p in plan if p == 'reboot']
            self.assertEqual(reboots, [0, 1] if major >= 10 else [0], major)
            # the boot cache check follows the first 6-9 reboot when no update replaced the build
            plan = hc_e2e.plan.run_plan([WEB_N, WEB_O], major, RELEASE_OPTS)
            self.assertEqual([i for i, p in plan if p == 'cache'], [0] if 6 <= major <= 9 else [], major)

    def test_until_restore_repair_screensaver_and_a_gated_run(self):
        plan = hc_e2e.plan.run_plan([WEB_N, WEB_O], 9, dict(RELEASE_OPTS, until=3))
        self.assertNotIn('removal', [p for _, p in plan])
        self.assertFalse([i for i, _ in plan if i == 1])
        self.assertNotIn('cleanup', [p for _, p in plan])
        plan = hc_e2e.plan.run_plan([WEB_N, WEB_O], 9, dict(RELEASE_OPTS, until=4))
        self.assertEqual([p for _, p in plan][-3:], ['switch', 'window-chosen', 'home-proof'])
        plan = hc_e2e.plan.run_plan([WEB_N, WEB_O], 9, dict(RELEASE_OPTS, restore_repair=True))
        self.assertEqual([p for _, p in plan][-2:], ['restore-repair', 'cleanup'])
        plan = hc_e2e.plan.run_plan([WEB_N], 10, {'screensaver': 'screensaver', 'until': 5})
        self.assertEqual([p for _, p in plan], ['baseline', 'install', 'choose', 'window-chosen', 'screensaver', 'cleanup'])
        qml = [('qml', 'normal', False), ('qml', 'overlay', False)]
        self.assertEqual(hc_e2e.plan.run_plan(qml, 12, RELEASE_OPTS), [(0, 'baseline'), (0, 'qml-gate'), (1, 'qml-gate'), (0, 'cleanup')])
        plan = hc_e2e.plan.run_plan([('qml', 'normal', False), WEB_N], 12, RELEASE_OPTS)
        self.assertEqual(plan[:3], [(1, 'baseline'), (0, 'qml-gate'), (1, 'install')])

    def test_crash_windows_group_unguarded_phases_with_the_step_before(self):
        plan = hc_e2e.plan.run_plan([WEB_N, WEB_O], 10, RELEASE_OPTS)
        groups = [[p for _, p in g] for g in hc_e2e.plan.plan_groups([x for x in plan if x[1] != 'cleanup'])]
        self.assertIn(['choose', 'window-chosen'], groups)
        self.assertIn(['home-proof', 'window-home'], groups)
        self.assertIn(['switch', 'window-chosen'], groups)
        self.assertTrue(all(hc_e2e.plan.PHASE[g[0]]['guard'] or g[0] == 'baseline' for g in groups), groups)

    def test_describe_plan_and_api_build(self):
        plan = hc_e2e.plan.run_plan([WEB_N, WEB_O], 9, RELEASE_OPTS)
        lines = hc_e2e.plan.describe_plan(plan, ['fixture:web.normal', 'fixture:web.overlay'])
        self.assertTrue(lines[1].startswith('fixture:web.overlay: switch'))
        self.assertTrue(hc_e2e.launchers.api_build('fixture:native.normal', 0, 'smoke'))
        self.assertFalse(hc_e2e.launchers.api_build('fixture:native.normal', 2, 'full'))
        self.assertTrue(hc_e2e.launchers.api_build('fixture:web.overlay', 1, 'full'))
        self.assertFalse(hc_e2e.launchers.api_build('fixture:web.normal', 0, None))
        self.assertEqual(hc_e2e.launchers.FIXTURE_TYPES[0], 'web')  # all-fixtures puts a web launcher first
        self.assertEqual(hc_e2e.launchers.fixture_specs()[:2], ['fixture:web.normal', 'fixture:web.overlay'])

    def test_results_carry_the_scope_and_a_fail_calls_the_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = hc_e2e.recorder.Recorder(tmp)
            failed = []
            rec.on_fail = failed.append
            rec.scope = hc_e2e.plan.PER_WINDOW
            rec.check('a', True)
            rec.check('b', False)
            rec.check('c', False, expected='documented limitation')
            rec.log.close()
        self.assertEqual([r['scope'] for r in rec.results], [hc_e2e.plan.PER_WINDOW] * 3)
        self.assertEqual(failed, ['b'])


class Screenshots(unittest.TestCase):
    def test_only_key_steps_are_taken(self):
        for label in ('3-home-verdict', '4-home-stock-verdict', '3d-demo-ok-verdict', '3-home-first-press',
                      'fail-2-choose', '3r-after-reboot', 'final', '4-alert', '4-alert-dismissed', '4-alert-refused',
                      '5-toast', '3a-uninstall-alert', '3a-uninstall-alert-2-dismissed', 'keys-blocked',
                      '3-home-foreign-popup', 'audiooutputd-crash-dialog-before', 'launcher row-click-not-observed',
                      '3a-uninstall-alert-focus-proof', 'S1-before', '2-reboot-alert'):
            self.assertTrue(hc_e2e.recorder.screenshot_reason(label), label)
        for label in ('app-open', 'stale-marker', 'row-focused', 'dialog', 'after-confirm', 'reopened',
                      '3a-request-selected', '2a-request-dialog', '3a-uninstall-screen', '3a-uninstalled', '3u-app'):
            self.assertIsNone(hc_e2e.recorder.screenshot_reason(label), label)

    def scenario(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
        s.rec, s.shot_n, s.vm, s.capture_broken = FakeRec(), 0, 'vm', False
        s.rec.outdir, s.rec.step = tempfile.gettempdir(), '2 choose the launcher through the UI'
        s.a, s.L = SimpleNamespace(multi=False), SimpleNamespace(tag='t')
        s.host = SimpleNamespace(tv_screenshot=Mock(return_value=(True, 'ok')))
        return s

    def test_a_skipped_label_takes_nothing_and_a_fail_one_picture_per_step(self):
        s = self.scenario()
        self.assertIsNone(s.shot('dialog'))
        s.host.tv_screenshot.assert_not_called()
        s.fail_shot('x')
        s.fail_shot('y')
        self.assertEqual(s.host.tv_screenshot.call_count, 1)
        self.assertIn('fail-2-choose-the-launcher-through-the-UI', s.host.tv_screenshot.call_args.args[1])
        s.rec.step = '3 Home opens the launcher'
        s.fail_shot('z')
        self.assertEqual(s.host.tv_screenshot.call_count, 2)


class PreloadPolling(unittest.TestCase):
    """The 6-9 settle and sample (a fixed 60 s, then 60 s of samples nobody read) became a poll of what
    check_home_gate asserts, within the same 120 s bound; 10+ ends its settle at the watcher's close line."""

    def test_settled_condition(self):
        self.assertTrue(hc_e2e.checks.preload_settled(9, 1, False, False))
        self.assertFalse(hc_e2e.checks.preload_settled(9, 0, False, False))
        self.assertFalse(hc_e2e.checks.preload_settled(6, 1, True, False))
        self.assertTrue(hc_e2e.checks.preload_settled(10, 0, True, True))
        self.assertFalse(hc_e2e.checks.preload_settled(11, 1, False, False))

    def scenario(self, major, polls_until_settled):
        from types import SimpleNamespace
        from unittest.mock import Mock
        s = scenario(major)
        s.L = SimpleNamespace(overlay=False)
        state = {'n': 0}
        s.dev.preload_policy = lambda appid=hc_e2e.constants.STOCK: (False, '{"isEnabled":false}')
        def running_ids():
            state['n'] += 1
            return [hc_e2e.constants.STOCK] if state['n'] <= polls_until_settled else []
        s.running_ids = running_ids
        s.gate_mount_count = lambda: 1
        s.preload_log = lambda: 'closeByAppId com.webos.app.home' if state['n'] > polls_until_settled else ''
        s.check_home_gate = Mock()
        s.residency = Mock()
        s.waits = []
        def wait(cond, timeout, every=3):
            s.waits.append(timeout)
            for _ in range(int(timeout / every) + 1):
                v = cond()
                if v:
                    return v
            return v
        s.wait = wait
        s.stock_running_samples = lambda: [(0, False), (5, False)]
        return s, state

    def test_6_to_9_polls_then_checks_the_gate_without_a_fixed_sleep(self):
        from unittest.mock import patch
        for major in (6, 9):
            s, state = self.scenario(major, polls_until_settled=2)
            with patch.object(hc_e2e.timing, 'sleep') as slept:
                s.check_preload_off('after the switch')
            self.assertEqual([c.args for c in slept.call_args_list], [], 'no fixed wait')
            self.assertEqual(s.waits[-1], hc_e2e.constants.PRELOAD_SETTLE + hc_e2e.constants.PRELOAD_SAMPLE)  # the same upper bound
            self.assertEqual(state['n'], 3)  # stopped as soon as it settled
            s.check_home_gate.assert_called_once_with('after the switch')

    def test_10_after_a_boot_ends_the_settle_at_the_close_line_and_still_samples(self):
        from unittest.mock import patch
        s, state = self.scenario(10, polls_until_settled=0)
        with patch.object(hc_e2e.timing, 'sleep') as slept:
            s.check_preload_off('after the reboot', settle=30)
        slept.assert_not_called()
        self.assertEqual(s.waits[:2], [60, 30])  # the policy wait, then the settle bound (unchanged)
        self.assertIn(('after the reboot: %s is not running over %d s' % (hc_e2e.constants.STOCK, hc_e2e.constants.PRELOAD_SAMPLE), True),
                      s.rec.checks)
        s.check_home_gate.assert_not_called()

    def test_wait_closed_polls_with_the_old_bound(self):
        s = scenario(9)
        seen = []
        s.wait = lambda cond, timeout, every=3: seen.append((timeout, every)) or cond()
        s.running_ids = lambda: []
        self.assertTrue(s.wait_closed('x', 3))
        self.assertEqual(seen, [(3, 1)])


class ApiModes(unittest.TestCase):
    def test_bare_flag_is_smoke_and_full_is_the_sweep(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        for value, mode in ((True, 'smoke'), ('smoke', 'smoke'), ('full', 'full')):
            s = scenario(9)
            s.a = SimpleNamespace(api_check=value)
            for name in ('api_before_selection', 'step_api', 'api_smoke_before', 'api_smoke_selected'):
                setattr(s, name, Mock())
            s.api_before()
            s.api_selected()
            self.assertEqual(s.api_mode(), mode)
            full = mode == 'full'
            self.assertEqual((s.api_before_selection.called, s.step_api.called), (full, full))
            self.assertEqual((s.api_smoke_before.called, s.api_smoke_selected.called), (not full, not full))

    def test_cli_modes(self):
        from unittest.mock import patch
        seen = {}
        def fake(args, ap):
            seen.update(api=args.api_check)
            return 0
        for argv, api in ((['--api-check'], 'smoke'), (['--api-check=full'], 'full'), (['--api-check', 'full'], 'full')):
            with patch.object(sys, 'argv', ['runner', '--device', 'tv'] + argv), patch.object(hc_e2e.cli, 'run_device_api', fake):
                hc_e2e.cli.main()
            self.assertEqual(seen['api'], api)


class DeviceKitPath(unittest.TestCase):
    """--device (the author's compatibility kit): plain ares by default, Beanjar only by the maintainer's env var."""

    def env(self, **kw):
        from unittest.mock import patch
        return patch.dict(os.environ, kw, clear=False)

    def test_plain_by_default(self):
        import hc_e2e.device_api as da
        with self.env(**{da.VIA_BEANJAR_ENV: ''}):
            d = da.device_for('my-tv')
        self.assertFalse(d.beanjar)
        self.assertEqual(d.argv('install', ['-l']), ['ares-install', '-d', 'my-tv', '-l'])

    def test_maintainer_env_goes_through_beanjar(self):
        import hc_e2e.device_api as da
        with self.env(**{da.VIA_BEANJAR_ENV: '1'}):
            d = da.device_for('pi/w24g-ombre')
        self.assertEqual(d.argv('launch', ['a.b']), ['beanjar', 'ares-launch', '-d', 'pi/w24g-ombre', 'a.b'])
        self.assertEqual(d.argv('do', ['key'], root=True), ['beanjar', 'ares-do', '--root', '-d', 'pi/w24g-ombre', 'key'])

    def test_missing_tools_are_named(self):
        import hc_e2e.device_api as da
        from unittest.mock import patch
        with self.env(**{da.VIA_BEANJAR_ENV: ''}), patch('shutil.which', lambda t: None if t == 'ares-novacom' else '/x/' + t):
            self.assertEqual(da.missing_tools(), ['ares-novacom'])
        with self.env(**{da.VIA_BEANJAR_ENV: '1'}), patch('shutil.which', lambda t: None):
            self.assertEqual(len(da.missing_tools()), 1)
            self.assertIn('beanjar', da.missing_tools()[0])

    def test_result_dir_is_one_name(self):
        import hc_e2e.device_api as da
        self.assertEqual(da.result_dir_name('pi/w24g-ombre'), 'pi-w24g-ombre')
        self.assertEqual(da.result_dir_name('my-tv'), 'my-tv')


class PanelOpen(unittest.TestCase):
    """Opening Home Chooser for the switch and 3u-app: in front, held, one retry, every rc logged."""
    APP = hc_e2e.constants.APP_ID

    def test_in_front(self):
        self.assertTrue(hc_e2e.checks.panel_in_front(self.APP, False))
        self.assertTrue(hc_e2e.checks.panel_in_front('', True))
        self.assertTrue(hc_e2e.checks.panel_in_front(hc_e2e.constants.STOCK, True))
        self.assertFalse(hc_e2e.checks.panel_in_front('', False))
        self.assertFalse(hc_e2e.checks.panel_in_front(None, False))
        self.assertFalse(hc_e2e.checks.panel_in_front('org.webosbrew.hcfixture.web.normal', False))
        # 9.x: SAM names the card beneath our overlay while our list is on screen
        self.assertTrue(hc_e2e.checks.panel_in_front('org.webosbrew.hcfixture.web.normal', True))

    def test_verdict_holds(self):
        ok, why = hc_e2e.checks.panel_front_verdict([(0, '', False), (1, '', True), (3, self.APP, True), (5, '', True)], 4)
        self.assertTrue(ok)
        self.assertIn('in front at 1 s and still at 5 s', why)

    def test_verdict_closed_under_us(self):
        # pi 9.2.4: our overlay in front at once, then the launcher's card mapped and closed it (windowPolicy)
        ok, why = hc_e2e.checks.panel_front_verdict([(0, '', True), (1, '', True), (2, 'org.webosbrew.hcfixture.web.normal', False)], 4)
        self.assertIs(ok, False)
        self.assertIn('gone at 2 s', why)

    def test_verdict_undecided(self):
        self.assertIsNone(hc_e2e.checks.panel_front_verdict([], 4)[0])
        ok, why = hc_e2e.checks.panel_front_verdict([(0, '', False), (2, 'x.card', False)], 4)
        self.assertIsNone(ok)
        self.assertIn('not in front after 2 s', why)
        ok, why = hc_e2e.checks.panel_front_verdict([(0, self.APP, True), (2, self.APP, True)], 4)
        self.assertIsNone(ok)
        self.assertIn('held 2 of 4 s', why)

    def run_open(self, fronts, attempts=2):
        calls, said = [], []
        fronts = list(fronts)

        def launch():
            calls.append('launch')
            return 0, 'Launched application %s\n' % self.APP

        def close():
            calls.append('close')

        def front():
            calls.append('front')
            return fronts.pop(0)
        ok, notes = hc_e2e.checks.open_in_front(launch, close, front, said.append, attempts)
        self.assertEqual(notes, said)
        return ok, calls, said

    def test_first_open(self):
        ok, calls, said = self.run_open([(True, 'held')])
        self.assertTrue(ok)
        self.assertEqual(calls, ['launch', 'front'])
        self.assertIn("ares-launch rc=0 'Launched application %s'" % self.APP, said[0])
        self.assertIn('in front (held)', said[1])

    def test_one_retry(self):
        ok, calls, said = self.run_open([(False, 'gone at 2 s'), (True, 'held')])
        self.assertTrue(ok)
        self.assertEqual(calls, ['launch', 'front', 'close', 'launch', 'front'])
        self.assertIn('NOT in front (gone at 2 s); closing it and retrying the open once', said[1])
        self.assertTrue(said[2].startswith('open 2 of 2: ares-launch rc=0'))

    def test_gives_up_after_the_retry(self):
        ok, calls, said = self.run_open([(False, 'a'), (False, 'b')])
        self.assertFalse(ok)
        self.assertEqual(calls, ['launch', 'front', 'close', 'launch', 'front'])
        self.assertTrue(said[-1].endswith('NOT in front (b)'))


class PreviousLauncherLog(unittest.TestCase):
    """The watcher's previous-launcher hold and deferred close, as the runner reads its log."""
    ARMED = '2026-11-01 13:42:40 previous launcher: Home changed in Home Chooser; to close later: old.home; ' \
            'closes wait until Home Chooser is off screen (in front: old.home)'
    OFF = '2026-11-01 13:42:50 previous launcher: Home Chooser is off screen (its close event); held closes may go ahead'
    CLOSE = '2026-11-01 13:43:10 previous launcher: closeByAppId old.home with com.webos.app.livetv in front: {"returnValue":true}'
    GATE = '2026-11-01 13:42:51 home gate: closeByAppId com.webos.app.home: {"returnValue":true}; stock Home close confirmed'
    SELF = '2026-11-01 13:43:11 previous launcher: old.home closed by itself; not closed'

    def test_closes_after_the_panel_pass(self):
        self.assertEqual(hc_e2e.evidence.closes_under_panel([self.ARMED, self.OFF, self.GATE, self.CLOSE]), [])

    def test_a_close_under_the_panel_fails(self):
        self.assertEqual(hc_e2e.evidence.closes_under_panel([self.ARMED, self.CLOSE, self.OFF]), [self.CLOSE])
        self.assertEqual(hc_e2e.evidence.closes_under_panel([self.ARMED, self.GATE]), [self.GATE])

    def test_no_change_in_the_panel_holds_nothing(self):
        self.assertEqual(hc_e2e.evidence.closes_under_panel([self.GATE]), [])

    def test_previous_closes_counts_one_id(self):
        lines = [self.ARMED, self.OFF, self.CLOSE, self.CLOSE.replace('old.home', 'old.home2')]
        self.assertEqual(hc_e2e.evidence.previous_closes(lines, 'old.home'), [self.CLOSE])

    def test_previous_dropped_finds_a_self_close(self):
        self.assertEqual(hc_e2e.evidence.previous_dropped([self.ARMED, self.SELF], 'old.home'), [self.SELF])
        self.assertEqual(hc_e2e.evidence.previous_dropped([self.ARMED, self.SELF], 'old.home2'), [])

class MatrixFixes2(unittest.TestCase):
    LINES = ['[crash] Fault Occured.', 'Reason : RDXDErr3_audiooutputd__Linux Crash: audiooutputd crashes at location',
             'If you click YES and you turn off the TV', 'YES', 'NO']

    def test_audiooutputd_dialog_per_line_ocr(self):
        frame = {'elements': [{'kind': 'text', 'text': t} for t in self.LINES]}
        msg, text = hc_e2e.evidence.audiooutputd_dialog(frame)
        self.assertEqual(msg, '[crash] Fault Occured.')
        self.assertIn('audiooutputd crashes', text)

    def test_audiooutputd_dialog_rejects_other_shapes(self):
        no_name = {'elements': [{'text': t} for t in self.LINES if 'audiooutputd' not in t]}
        self.assertEqual(hc_e2e.evidence.audiooutputd_dialog(no_name)[0], None)
        twice = {'elements': [{'text': t} for t in self.LINES + ['[crash] Fault Occured.']]}
        self.assertEqual(hc_e2e.evidence.audiooutputd_dialog(twice)[0], None)

    def click_scenario(self, ui, effect_after_ok):
        s = scenario(11)
        s.a = type('A', (), {})()
        s.pressed, s.clicked = [], []
        s.host = type('H', (), {'remote_click': lambda self_, vm, c, f: (0, '')})()
        s.vm = 'vm'
        s.elements = lambda label: {'width': 100, 'height': 100, 'elements': [{'kind': 'text', 'text': 'Row', 'centre': [1, 2], 'box': [0, 0, 5, 5]}]}
        s.ui = lambda: ui
        s.press = lambda *k: s.pressed.extend(k)
        s.wait = lambda cond, timeout, every=3: s.pressed and effect_after_ok
        s.click_diagnostics = lambda label: None
        return s

    def test_click_falls_back_to_ok_when_row_focused_without_dialog(self):
        s = self.click_scenario({'row': 'r1'}, True)
        s.wait = lambda cond, timeout, every=3: bool(s.pressed) if timeout == 15 else False
        s.click_element('Row', effect=lambda: False, focused_row='r1', read_after=False)
        self.assertEqual(s.pressed, ['OK'])

    def test_click_no_fallback_when_dialog_or_other_row(self):
        for ui in ({'row': 'r1', 'dialog': 'x'}, {'row': 'other'}):
            s = self.click_scenario(ui, False)
            s.wait = lambda cond, timeout, every=3: False
            with self.assertRaises(RuntimeError):
                s.click_element('Row', effect=lambda: False, focused_row='r1', read_after=False)
            self.assertEqual(s.pressed, [])

    def test_wake_screensaver(self):
        s = scenario(11)
        s.pressed, state = [], ['com.webos.app.screensaver']
        s.running_ids = lambda: list(state)
        s.press = lambda *k: (s.pressed.extend(k), state.clear())
        s.wait = lambda cond, timeout, every=3: cond()
        self.assertTrue(s.wake_screensaver('t'))
        self.assertEqual(s.pressed, ['BACK'])
        s.pressed = []
        self.assertFalse(s.wake_screensaver('t'))
        self.assertEqual(s.pressed, [])

    def test_step_switch_closes_home_chooser_when_the_switch_fails(self):
        from unittest.mock import MagicMock
        s = scenario(11)
        s.rec, s.dev = MagicMock(), MagicMock()
        s.dev.install.return_value = (0, 'Installed package')
        s.dev.sh.return_value = (0, 'prev')
        s.L = MagicMock(is_demo=False, appinfo={}, overlay=False)
        s.launcher = 'l'
        for name in ('popup_mark_now', 'qml_note', 'mark_log', 'residency', 'close_insp', 'messages_mark', 'open_panel',
                     'wait_closed', 'check_previous_closed'):
            setattr(s, name, MagicMock())
        s.running_ids = lambda: [hc_e2e.constants.APP_ID]
        s.wait = lambda *a, **k: {'rows': ['l']}
        s.choose_in_ui = MagicMock(side_effect=RuntimeError('click effect not observed'))
        with __import__("unittest.mock").mock.patch("hc_e2e.steps.choose.Inspector"):
            with self.assertRaises(RuntimeError):
                s.step_switch()
        closes = [c for c in s.dev.close.call_args_list if c.args == (hc_e2e.constants.APP_ID,)]
        self.assertEqual(len(closes), 2)  # the stale panel before open_panel, and the finally
        s.check_previous_closed.assert_not_called()

    def switch_over(self, major, prev):
        from unittest.mock import MagicMock
        s = scenario(major)
        s.rec, s.dev = MagicMock(), MagicMock()
        s.dev.install.return_value = (0, 'Installed package')
        s.dev.sh.return_value = (0, prev)
        s.L = MagicMock(is_demo=False, appinfo={}, overlay=False)
        s.launcher = 'l'
        order = []
        for name in ('popup_mark_now', 'qml_note', 'mark_log', 'residency', 'close_insp', 'messages_mark',
                     'check_previous_closed'):
            setattr(s, name, MagicMock())
        s.dev.close.side_effect = lambda a: order.append(('close', a))
        s.open_panel = lambda label: order.append(('open_panel', label)) or True
        s.wait_closed = lambda a, t: order.append(('gone', a)) or True
        s.running_ids = lambda: []
        s.wait = lambda *a, **k: {'rows': ['l']}
        s.choose_in_ui = MagicMock()
        with __import__("unittest.mock").mock.patch("hc_e2e.steps.choose.Inspector"):
            s.step_switch()
        return s, order

    def test_native_overlay_is_closed_before_the_panel_from_11(self):
        prev = 'org.webosbrew.hcfixture.native.overlay'
        for major in (11, 12):
            s, order = self.switch_over(major, prev)
            self.assertEqual(order[:3], [('close', prev), ('gone', prev), ('open_panel', '2s open')])
            s.check_previous_closed.assert_called_once_with(prev, True)
            self.assertFalse(s.overlay_takes_pointer)

    def test_native_overlay_stays_on_10_and_below_and_other_launchers_stay(self):
        for major, prev in ((10, 'org.webosbrew.hcfixture.native.overlay'), (6, 'org.webosbrew.hcfixture.native.overlay'),
                            (12, 'org.webosbrew.hcfixture.web.overlay'), (12, 'org.webosbrew.hcfixture.native.normal')):
            s, order = self.switch_over(major, prev)
            self.assertEqual(order[0], ('open_panel', '2s open'))
            self.assertNotIn(('close', prev), order)
            s.check_previous_closed.assert_called_once_with(prev, False)

    def test_previous_close_verdict(self):
        v = hc_e2e.evidence.previous_close_verdict
        close, drop = ['previous launcher: closeByAppId x '], ['previous launcher: x closed by itself']
        self.assertTrue(v(close, [], False)[0])
        self.assertTrue(v([], drop, False)[0])
        self.assertFalse(v([], [], False)[0])
        self.assertFalse(v(close, drop, False)[0])
        # closed by the harness: no watcher line is needed, a watcher close would be wrong
        self.assertTrue(v([], [], True)[0])
        self.assertTrue(v([], drop, True)[0])
        self.assertFalse(v(close, [], True)[0])
        # its one attempt on the gone launcher is refused and changes nothing (11.x run 2026-11-02)
        refused = ['previous launcher: closeByAppId x with y in front: {"errorCode":1,"returnValue":false,'
                   '"errorText":"native app is not running"}']
        self.assertTrue(v(refused, [], True)[0])
        self.assertFalse(v(refused * 2, [], True)[0])

    def test_native_overlay_must_hide(self):
        f = hc_e2e.evidence.native_overlay_must_hide
        self.assertTrue(f('a.native.overlay', 11))
        self.assertFalse(f('a.native.overlay', 10))
        self.assertFalse(f('a.web.overlay', 12))
        self.assertFalse(f('', 12))


class AwaitsLanTest(unittest.TestCase):
    def test_nat_vm_never_waits_for_a_lan_address(self):
        host = hc_e2e.beanjar.BeanjarHost.__new__(hc_e2e.beanjar.BeanjarHost)
        for info, want in (({'link': 'lan', 'lan_address': '-'}, True), ({'link': 'lan', 'lan_address': '192.168.1.5'}, False),
                           ({'link': 'nat'}, False), ({}, True)):
            host.info = lambda vm, info=info: info
            self.assertEqual(host.awaits_lan('x'), want, info)


class BootdFirstAppTests(unittest.TestCase):
    """What bootd launched at boot decides whether the launcher is expected in front (6.x to 9.x)."""
    START = 'PERFORMANCE(  6.711645736) : Boot) BOOTD_START\n'
    TRY = 'DEBUG( 10.588595715) : BootSequencer) Try to launch first app\n'
    HOME = ('DEBUG( 13.480130399) : General) AppInfo=appId(com.webos.app.home), Visible(true), LaunchSplash(false), MVPD(false), Launched(true)\n'
            'DEBUG( 13.480140602) : Client) Call luna://com.webos.applicationManager/launch\n'
            'DEBUG( 13.480187732) : Client) Request: {"launchHidden":false,"id":"com.webos.app.home","noSplash":true,"params":{"boot":true}}\n'
            'DEBUG( 13.508727102) : Client) Response:launch success({"returnValue":true})\n')
    LISTING = ('DEBUG( 11.160741344) : Client) Call luna://com.webos.applicationManager/listLaunchPoints - Request: {}\n')
    LIVETV = ('DEBUG( 10.9) : Settings) lgChannelAutoLaunch(on): do not support Home boot mode\n'
              'DEBUG( 11.0) : Settings) Action (Input App) and lgChannelAutoLaunch (on): set id livetv\n')
    TV_LAUNCH = ('DEBUG( 13.4) : Client) Call luna://com.webos.applicationManager/launch\n'
                 'DEBUG( 13.5) : Client) Request: {"launchHidden":false,"id":"com.webos.app.livetv","noSplash":true}\n')

    def test_home_launch_with_boot_true(self):
        kind, app, _ = hc_e2e.releases.bootd_first_app(self.START + self.TRY + self.LISTING + self.HOME)
        self.assertEqual((kind, app), ('home', 'com.webos.app.home'))
        self.assertEqual(hc_e2e.releases.boot_gate_expectation(kind, app), (True, ''))

    def test_livetv_launch_when_lgchannel_autolaunch_is_on(self):
        text = self.START + self.LIVETV + self.TRY + self.LISTING + self.TV_LAUNCH
        kind, app, detail = hc_e2e.releases.bootd_first_app(text)
        self.assertEqual((kind, app), ('other', 'com.webos.app.livetv'))
        self.assertIn('do not support Home boot mode', detail)
        want, why = hc_e2e.releases.boot_gate_expectation(kind, app)
        self.assertFalse(want)
        self.assertIn('bootd booted to com.webos.app.livetv (lgChannelAutoLaunch on): launcher not expected at boot', why)

    def test_set_id_line_without_a_launch_call(self):
        kind, app, _ = hc_e2e.releases.bootd_first_app(self.START + self.LIVETV + self.TRY)
        self.assertEqual((kind, app), ('other', 'livetv'))

    def test_only_the_last_boot_counts(self):
        text = self.START + self.TRY + self.HOME + self.START + self.LIVETV + self.TRY + self.TV_LAUNCH
        self.assertEqual(hc_e2e.releases.bootd_first_app(text)[:2], ('other', 'com.webos.app.livetv'))
        text = self.START + self.LIVETV + self.TRY + self.TV_LAUNCH + self.START + self.TRY + self.HOME
        self.assertEqual(hc_e2e.releases.bootd_first_app(text)[0], 'home')

    def test_home_without_boot_flag_is_not_the_boot_launch(self):
        text = self.START + self.TRY + self.HOME.replace(',"params":{"boot":true}', '')
        self.assertEqual(hc_e2e.releases.bootd_first_app(text)[0], 'other')

    def test_unreadable_evidence_is_unknown(self):
        for text in ('', 'no such file', self.START, self.START + self.TRY + self.LISTING):
            kind, app, detail = hc_e2e.releases.bootd_first_app(text)
            self.assertEqual(kind, 'unknown', text)
            self.assertIsNone(hc_e2e.releases.boot_gate_expectation(kind, app)[0])


if __name__ == '__main__':
    unittest.main()


class HarnessFixes1103(unittest.TestCase):
    """The three one-off failures of 2026-11-02/03 (scratch/api-level3-device*.md)."""

    def test_page_vanished_needs_the_inspector_to_have_lost_the_page(self):
        steps = hc_e2e.steps.api
        gone = ('none (DOM last_error=None; timeline: t+0.0s #1 DOM 0.4s toast=None; '
                't+4.5s #2 DOM 0.1s err=no page for org.webosbrew.homechooser in /json (forward alive))')
        self.assertTrue(steps.page_vanished(gone))
        self.assertFalse(steps.page_vanished('none (DOM last_error=None; timeline: t+0.0s #1 DOM 0.4s toast=None)'))
        self.assertFalse(steps.page_vanished('DOM'))
        self.assertFalse(steps.page_vanished(None))

    def launch_scenario(self, running, events=(('launch', '{"event":"launch"}'),)):
        from unittest.mock import Mock
        s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
        s.rec = Mock()
        s.sam_launch_lines = Mock(return_value=['SAM APP_LAUNCHED {"app_id":"x"}'])
        s.life_events = Mock(return_value=(list(events), True))
        s.life_end = Mock()
        s.running_ids = Mock(side_effect=running)
        return s

    def test_launch_proof_waits_for_the_process_to_be_listed(self):
        """w22o-queue-us-20261103-141531: launch lines at once, `running` read once, a slow board lists it later."""
        from unittest.mock import patch
        s = self.launch_scenario([[], [], ['x']])
        with patch.object(hc_e2e.timing, 'sleep'):
            s.launch_proof('4-home', 'x', '2026-11-03T00:00:00')
        self.assertTrue(s.rec.check.call_args[0][1])
        self.assertIn('running: True', s.rec.check.call_args[0][2])

    def test_launch_proof_fails_when_the_app_never_runs(self):
        from unittest.mock import patch
        s = self.launch_scenario(lambda: [])
        s.running_ids.side_effect = None
        s.running_ids.return_value = ['other']
        with patch.object(hc_e2e.timing, 'sleep'), patch.object(hc_e2e.timing, 'SLOWDOWN', 0.01):
            s.launch_proof('4-home', 'x', '2026-11-03T00:00:00')
        self.assertFalse(s.rec.check.call_args[0][1])
        self.assertIn('running: False', s.rec.check.call_args[0][2])


class Level3Logic(unittest.TestCase):
    APP = 'org.webosbrew.hcfixture.disposable'

    def ev(self, kind, appid=None, **extra):
        d = {'subscribed': True, 'returnValue': True, 'apiVersion': 3, 'event': kind}
        if appid:
            d['appId'] = appid
        d.update(extra)
        return d

    ACK = {'subscribed': True, 'returnValue': True, 'apiVersion': 3}

    def test_parse_stream_keeps_json_lines_only(self):
        text = 'luna-send banner\r\n{"subscribed":true,"returnValue":true,"apiVersion":3}\r\nnoise {not json}\n[1]\n' \
               '{"event":"launch","appId":"a"}\n'
        got = hc_e2e.level3.parse_stream(text)
        self.assertEqual([r.get('event') for r in got], [None, 'launch'])
        self.assertEqual(hc_e2e.level3.parse_stream(None), [])

    def test_a_good_stream(self):
        replies = [self.ACK, self.ev('splash', self.APP), self.ev('launch', self.APP, reason='undefined'),
                   self.ev('foreground', 'other'), self.ev('close', self.APP), self.ev('stop', self.APP)]
        self.assertEqual(hc_e2e.level3.stream_problems(replies, self.APP), [])
        self.assertIn('close', hc_e2e.level3.stream_summary(replies, self.APP))

    def test_stream_problems(self):
        L = hc_e2e.level3.stream_problems
        good = [self.ACK, self.ev('launch', self.APP), self.ev('close', self.APP)]
        self.assertTrue(any('no acknowledgement' in p for p in L([self.ev('launch', self.APP)], self.APP)))
        self.assertTrue(any('no launch event' in p for p in L([self.ACK, self.ev('close', self.APP)], self.APP)))
        self.assertTrue(any('no close or stop' in p for p in L([self.ACK, self.ev('launch', self.APP)], self.APP)))
        # a close that came before the launch does not count as the close of this launch
        self.assertTrue(any('no close or stop' in p for p in
                            L([self.ACK, self.ev('close', self.APP), self.ev('launch', self.APP)], self.APP)))
        self.assertTrue(any('level 3' in p for p in L([dict(self.ACK, apiVersion=2)] + good[1:], self.APP)))
        self.assertTrue(any('not say subscribed' in p for p in L([dict(self.ACK, subscribed=False)] + good[1:], self.APP)))
        refused = {'returnValue': False, 'apiVersion': 3, 'errorCode': 'noPermission', 'errorText': 'x'}
        self.assertTrue(any('refusal' in p for p in L([refused], self.APP)))
        self.assertTrue(any('not the first' in p for p in L([self.ev('launch', self.APP)] + good, self.APP)))
        self.assertTrue(any('non-string appId' in p for p in L(good + [self.ev('stop', 5)], self.APP)))
        self.assertEqual(L(good, self.APP, validate=lambda r: []), [])
        self.assertTrue(any('schema says no' in p for p in L(good, self.APP, validate=lambda r: ['schema says no'])))

    def test_stream_replies_validate_against_the_spec(self):
        """The recorded stream of scratch/api-level3-device.md: ack and events pass the spec's schema."""
        replies = [self.ACK, self.ev('splash', self.APP, splashBackground='', showSpinner=True, showSplash=True, title='T'),
                   self.ev('launch', self.APP, reason='undefined'),
                   self.ev('foreground', self.APP, windowType='_WEBOS_WINDOW_TYPE_CARD', windowGroup=False),
                   self.ev('close', self.APP, reason='undefined'), self.ev('stop', self.APP, reason='undefined'),
                   self.ev('background', self.APP)]
        validate = lambda r: hc_e2e.api_spec.spec_reply_problems('getAppLifeEvents', r) or []
        self.assertEqual(hc_e2e.level3.stream_problems(replies, self.APP, validate), [])
        self.assertEqual(hc_e2e.api_spec.spec_reply_problems('getAppLifeEvents', dict(self.ACK, apiVersion='3')) != [], True)

    def test_list_field_problems(self):
        L = hc_e2e.level3.list_field_problems
        apps = [{'id': 'a', 'systemApp': True, 'mediumLargeIcon': '', 'extraLargeIcon': 'x.png'},
                {'id': 'l', 'systemApp': False}, {'id': 'old'}]
        self.assertEqual(L(apps, 'l'), [])
        self.assertTrue(L([{'id': 'a', 'systemApp': 'true'}]))
        self.assertTrue(L([{'id': 'a', 'extraLargeIcon': None}]))
        self.assertTrue(L([{'id': 'a', 'mediumLargeIcon': 3}]))
        self.assertTrue(L([{'id': 'l', 'systemApp': True}], 'l'))
        self.assertEqual(hc_e2e.level3.field_census(apps), (2, 1, 1, 0, 1))
        # the service-level checker of listApps knows the fields too
        reply = {'returnValue': True, 'apiVersion': 3, 'apps': [{'id': 'a', 'systemApp': 1}]}
        self.assertTrue(hc_e2e.api_spec.api_reply_problems('listApps', reply))
        reply['apps'][0]['systemApp'] = True
        self.assertEqual(hc_e2e.api_spec.api_reply_problems('listApps', reply), [])

    PNG = 'iVBORw0KGgo='  # 8 bytes

    def test_icon_sample_and_verdict(self):
        L3 = hc_e2e.level3
        apps = [{'id': 'a', 'extraLargeIcon': 'x.png'}, {'id': 'b', 'extraLargeIcon': ''}, {'id': 'c'},
                {'id': 'd', 'extraLargeIcon': 'y.png'}]
        named, bare = L3.icon_sample(apps)
        self.assertEqual((named, bare), (['a', 'd'], 'b'))
        items = [{'id': i, 'size': 'extraLargeIcon'} for i in named + [bare]]
        good = {'returnValue': True, 'apiVersion': 3, 'icons': [
            {'mimeType': 'image/png', 'dataUrl': 'data:image/png;base64,' + self.PNG, 'byteLength': 8},
            {'errorCode': 'noIcon', 'errorText': 'x'}, {'errorCode': 'noIcon', 'errorText': 'x'}]}
        self.assertEqual(L3.icons_problems(items, good, set(named)), ([], 1))
        bad = json.loads(json.dumps(good))
        bad['icons'][0]['byteLength'] = 9
        self.assertTrue(L3.icons_problems(items, bad, set(named))[0])
        bad = json.loads(json.dumps(good))
        bad['icons'][2] = {'errorCode': 'notFound', 'errorText': 'x'}  # the bare app must be noIcon
        self.assertTrue(any('expected noIcon' in p for p in L3.icons_problems(items, bad, set(named))[0]))
        bad = json.loads(json.dumps(good))
        bad['icons'][2] = good['icons'][0]  # a picture for an app that has no such size: a fallback
        self.assertTrue(any('no such icon' in p for p in L3.icons_problems(items, bad, set(named))[0]))
        none = json.loads(json.dumps(good))
        none['icons'][0] = {'errorCode': 'noIcon', 'errorText': 'x'}
        self.assertTrue(any('none of 2' in p for p in L3.icons_problems(items, none, set(named))[0]))
        self.assertTrue(L3.icons_problems(items, {'icons': good['icons'][:2]}, set(named))[0])
        self.assertTrue(L3.icons_problems(items, {'returnValue': True}, set(named))[0])
        self.assertEqual(L3.icon_sample([{'id': 'a'}]), ([], 'a'))

    def test_icon_files_probe_and_unreadable_named_icon(self):
        L3 = hc_e2e.level3
        apps = [{'id': 'a', 'folderPath': '/usr/palm/applications/a/', 'extraLargeIcon': 'x.png'},
                {'id': 'b', 'folderPath': '/usr/palm/applications/b', 'extraLargeIcon': 'y.png'},
                {'id': 'c', 'extraLargeIcon': 'z.png'}, {'id': 'd', 'folderPath': '/f', 'extraLargeIcon': ''}]
        files = L3.icon_files(apps, 'extraLargeIcon')
        self.assertEqual(files, {'a': '/usr/palm/applications/a/x.png', 'b': '/usr/palm/applications/b/y.png'})
        self.assertIn('/usr/palm/applications/a/x.png',
                      L3.icon_probe_command(list(files.values())))
        out = 'Y /usr/palm/applications/a/x.png\nN /usr/palm/applications/b/y.png\n'
        self.assertEqual(L3.icon_probe_readable(files, out), {'a'})
        self.assertEqual(L3.icon_probe_readable(files, ''), set())
        # b names a file that is not there: noIcon is right, a picture or another error is not
        items = [{'id': 'a', 'size': 's'}, {'id': 'b', 'size': 's'}]
        png = {'mimeType': 'image/png', 'dataUrl': 'data:image/png;base64,' + self.PNG, 'byteLength': 8}
        no = {'errorCode': 'noIcon', 'errorText': 'x'}
        self.assertEqual(L3.icons_problems(items, {'icons': [png, no]}, {'a', 'b'}, {'a'}), ([], 1))
        self.assertTrue(L3.icons_problems(items, {'icons': [png, png]}, {'a', 'b'}, {'a'})[0])
        self.assertTrue(L3.icons_problems(items, {'icons': [png, {'errorCode': 'notFound', 'errorText': 'x'}]},
                                          {'a', 'b'}, {'a'})[0])
        # the one named icon is unreadable: noIcon is the whole expectation, not "none returned data"
        self.assertEqual(L3.icons_problems(items[1:], {'icons': [no]}, {'b'}, set()), ([], 0))

    def test_icon_size_step_expects_data_only_for_readable_files(self):
        from unittest.mock import Mock
        s = self.scenario()
        no = {'errorCode': 'noIcon', 'errorText': 'x'}
        png = {'mimeType': 'image/png', 'dataUrl': 'data:image/png;base64,' + self.PNG, 'byteLength': 8}
        apps = [{'id': 'rc', 'folderPath': '/p/rc', 'extraLargeIcon': 'iconLarge.png'}, {'id': 'bare', 'folderPath': '/p/bare'}]
        s.dev = Mock()
        s.dev.sh.return_value = (0, 'N /p/rc/iconLarge.png\n')
        rep = {'returnValue': True, 'apiVersion': 3, 'icons': [no, no]}
        s.api = Mock(return_value=(rep, json.dumps(rep)))
        s.api_level3_icon_size('w', apps, 'extraLargeIcon')
        self.assertTrue(s.rec.check.call_args[0][1])
        self.assertIn('named by 1, file present on 0', s.rec.check.call_args[0][2])
        s.rec.note.assert_called_once()
        self.assertEqual(s.api.call_args[0][1]['items'], [{'id': 'rc', 'size': 'extraLargeIcon'}, {'id': 'bare', 'size': 'extraLargeIcon'}])
        # the file is there: data is expected, and noIcon for it fails
        s = self.scenario()
        s.dev = Mock()
        s.dev.sh.return_value = (0, 'Y /p/rc/iconLarge.png\n')
        rep = {'returnValue': True, 'apiVersion': 3, 'icons': [png, no]}
        s.api = Mock(return_value=(rep, json.dumps(rep)))
        s.api_level3_icon_size('w', apps, 'extraLargeIcon')
        self.assertTrue(s.rec.check.call_args[0][1])
        self.assertIn('file present on 1', s.rec.check.call_args[0][2])
        rep = {'returnValue': True, 'apiVersion': 3, 'icons': [no, no]}
        s.api = Mock(return_value=(rep, json.dumps(rep)))
        s.api_level3_icon_size('w', apps, 'extraLargeIcon')
        self.assertFalse(s.rec.check.call_args[0][1])

    def scenario(self):
        from unittest.mock import Mock
        s = hc_e2e.scenario.Scenario.__new__(hc_e2e.scenario.Scenario)
        s.rec, s.launcher = Mock(), 'org.example.launcher'
        return s

    def test_smoke_refuses_the_plain_call_and_checks_fields(self):
        from unittest.mock import Mock
        s = self.scenario()
        refusal = {'returnValue': False, 'apiVersion': 3, 'errorCode': 'badRequest', 'errorText': 'stream'}
        s.api = Mock(return_value=(refusal, json.dumps(refusal)))
        apps = {'apps': [{'id': 'sys', 'systemApp': True}, {'id': 'org.example.launcher', 'systemApp': False}]}
        s.api_level3_smoke('before selection', apps)
        self.assertEqual([c[0][1] for c in s.rec.check.call_args_list], [True, True, True])
        s.api.assert_called_once_with('getAppLifeEvents', {})
        s = self.scenario()
        ok = {'returnValue': True, 'apiVersion': 3, 'subscribed': True}
        s.api = Mock(return_value=(ok, json.dumps(ok)))  # a service that streams a plain call: not badRequest
        s.api_level3_smoke('x', {'apps': [{'id': 'a', 'systemApp': 'yes'}]})
        self.assertEqual([c[0][1] for c in s.rec.check.call_args_list][:2], [False, False])

    def test_stream_check_reads_the_device_file(self):
        from unittest.mock import Mock, patch
        s = self.scenario()
        text = '\n'.join(json.dumps(x) for x in [self.ACK, self.ev('launch', self.APP), self.ev('close', self.APP)])
        s.dev = Mock()
        s.dev.sh.return_value = (0, text)
        s.wait = lambda cond, timeout, every=3: cond()
        self.assertTrue(s.stream_check('selected'))
        self.assertTrue(s.rec.check.call_args[0][1])
        s.dev.sh.return_value = (0, json.dumps(self.ACK))
        self.assertFalse(s.stream_check('selected'))
        s.stream_begin()
        self.assertIn('luna-send -a org.example.launcher -i luna://org.webosbrew.homechooser.service/getAppLifeEvents',
                      s.dev.sh.call_args[0][0].replace("'", '').replace('\\', ''))


class PlatformCrashAccounting(unittest.TestCase):
    """2026-11-03: platform crashes (audiooutputd, data-migrator, ...) are one reference note at the end of the
    run; only Home Chooser's own processes, fixtures and WebAppMgr fail it."""
    E = hc_e2e.evidence

    def test_crash_process_of_lines_and_faults(self):
        E = self.E
        line = '2026-11-03T09:31:51Z [52.1] user.info crashd [] crashd CRASHED {"process":"data-migrator.1667"}'
        self.assertEqual(E.crash_process(line), 'data-migrator.1667')
        self.assertEqual(E.crash_process({'faultType': 'crash', 'faultContent': 'RDXDErr3_data-migrator__Linux Crash: x'}),
                         'data-migrator')
        self.assertEqual(E.crash_process({'faultContent': 'RDXDErr3_legacy-broadcas__x'}), 'legacy-broadcas')
        self.assertEqual(E.crash_process('no process here'), '')
        self.assertIn('boot+52.1s', E.crash_time(line))
        self.assertEqual(E.crash_time({'faultTime': '55.1'}), 'boot+55.1s')

    def test_ours_versus_platform(self):
        E = self.E
        crashd = lambda p: 'x [1.0] crashd CRASHED {"process":"%s"}' % p
        ours = [crashd(p) for p in ('homechooser-d.9', 'homechooser-service.3', 'WebAppMgr.5823', 'org.webosbrew.hcfixture.web.normal.4',
                                    'org.webosbrew.homechooser.12')]
        platform = [crashd(p) for p in ('audiooutputd.1', 'data-migrator.1667', 'legacy-broadcas.4892', 'surface-manager.2')]
        self.assertEqual(E.split_platform_crashes(ours + platform), (ours, platform))
        self.assertTrue(E.is_ours_crash('a crash that names no process'))  # cannot be shown to be somebody else's
        self.assertTrue(E.is_ours_crash({'faultType': 'crash', 'faultContent': 'RDXDErr3_homechooser-daemon__x'}))
        self.assertFalse(E.is_ours_crash({'faultType': 'crash', 'faultContent': 'RDXDErr3_data-migrator__x'}))

    def test_reference_note_lists_each_process_and_time_once(self):
        E = self.E
        a = {'faultType': 'crash', 'faultContent': 'RDXDErr3_data-migrator__x', 'faultTime': '40.0'}
        head, text = E.crash_reference([('3-home', a), ('4-alert', a),
                                        ('4-alert', 'x [90.5] crashd CRASHED {"process":"audiooutputd.7"}')])
        self.assertIn('2', head)
        self.assertIn('not a failure', head)
        self.assertEqual(len(text.splitlines()), 2)
        self.assertIn('data-migrator  boot+40.0s  (seen at 3-home)', text)
        self.assertIn('audiooutputd.7', text)

    def test_popup_check_ignores_platform_crashes_and_fails_ours(self):
        def reply(*contents):
            return json.dumps({'returnValue': True, 'faultDetectedStatus': {'detected': 1, 'detectedFaults': [
                {'faultType': 'crash', 'faultContent': c, 'faultTime': '55.1'} for c in contents]}})
        for contents, ok in ((('RDXDErr3_data-migrator__Linux Crash: x',), True),
                             (('RDXDErr3_data-migrator__x', 'RDXDErr3_homechooser-daemon__y'), False)):
            s = scenario(10)
            s.popup_mark = ('2026-10-24T01:00:00', 10.0)
            s.priv_luna = lambda uri, payload='{}', r=reply(*contents): r
            s.shot = lambda label: label
            s.foreign_popup('x')
            self.assertEqual([c for c in s.rec.checks if 'crash fault this boot' in c[0]][0][1], ok)
            self.assertEqual([self.E.crash_process(e) for _, e in s.platform_crashes], ['data-migrator'])

    def test_end_of_run_note(self):
        s = scenario(10)
        s.report_platform_crashes()
        self.assertEqual(s.rec.notes, [])
        s.record_platform_crashes('x', ['a [1.0] crashd CRASHED {"process":"audiooutputd.1"}'])
        s.report_platform_crashes()
        self.assertIn('platform crashes this run', s.rec.notes[0][0] if isinstance(s.rec.notes[0], tuple) else s.rec.notes[0])


class DebugModeSetting(unittest.TestCase):
    def prepare(self, have, mode='release'):
        from unittest.mock import Mock
        host = Mock()
        host.running_vms.return_value = []
        host.settings.return_value = {'layers': 'normal,devmode,hbchannel', 'link': 'lan', 'debug_mode': have}
        host.set_settings.return_value = (0, 'ok')
        rec = Mock()
        hc_e2e.preflight.prepare_vm(host, 'vm', rec, mode)
        return host, rec

    def test_a_default_vm_is_set_to_release_and_it_is_logged(self):
        host, rec = self.prepare('(default)')
        host.set_settings.assert_called_once_with('vm', debug_mode='release')
        self.assertIn("{'debug_mode': 'release'}", rec.say.call_args_list[0][0][0])

    def test_release_is_left_alone_and_keep_touches_nothing(self):
        host, _ = self.prepare('release')
        host.set_settings.assert_not_called()
        host, _ = self.prepare('debug', 'keep')
        host.set_settings.assert_not_called()
        host, _ = self.prepare('release', 'debug')
        host.set_settings.assert_called_once_with('vm', debug_mode='debug')

    def test_a_failed_set_stops_the_run(self):
        from unittest.mock import Mock
        host = Mock()
        host.running_vms.return_value = []
        host.settings.return_value = {'layers': 'devmode,hbchannel', 'link': 'lan'}
        host.set_settings.return_value = (1, 'the VM is running')
        with self.assertRaises(SystemExit):
            hc_e2e.preflight.prepare_vm(host, 'vm', Mock())

    def test_the_option_defaults_to_release(self):
        src = open(os.path.join(HERE, 'hc_e2e', 'cli.py')).read()
        self.assertIn("'--debug-mode', choices=['release', 'debug', 'event', 'keep'], default='release'", src)
