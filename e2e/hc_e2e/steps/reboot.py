# SPDX-License-Identifier: Apache-2.0
"""Phases reboot (3r) and cache; the restart ask of older builds; the after-boot checks."""
import json

from .. import timing
from ..alerts import alert_service_proof
from ..constants import (APP_ID, BOOT_INPUT_WAIT, BOOT_SCRIPT, REBOOT_ASKED_TEXT, REBOOT_BUTTONS, REBOOT_PROMPT,
    RESTART_NOW, SCRIPTS_DIR, STOCK)
from ..evidence import messages_command, service_log_command
from ..releases import (BOOTD_LOG, boot_gate_expectation, boot_input_verdict, bootd_first_app, parse_reboot_ask,
    uses_home_gate)


class RebootSteps:
    """Phases reboot (3r) and cache; the restart ask of older builds; the after-boot checks."""

    def reboot_ask_log(self):
        """The REBOOT_ASK lines of the service since the choice was confirmed (syslog and journal)."""
        rc, out = self.dev.sh(service_log_command(getattr(self, 'ask_mark', ''),
                                                   'REBOOT_ASK') + " | tail -n 8")
        return out

    def reboot_ask_evidence(self):
        """Prefer the service log; use the observed notification call when PmLog discards info lines."""
        raw = self.reboot_ask_log()
        info = parse_reboot_ask(raw)
        if not info['alert'] and getattr(self, 'ask_monitor', None):
            observed = self.ask_monitor.snapshot()
            if observed['alert']:
                return observed, 'LS2 notification call/reply: ' + json.dumps(observed)
            return info, raw or 'no REBOOT_ASK line or notification bus evidence; bus events: ' + self.ask_monitor.diagnostic()
        return info, raw or 'no REBOOT_ASK line'

    def close_reboot_alert(self, label, info):
        """Our restart alert: closed over luna with the alertId from its createAlert reply (the same as the
        removal alert; no key is pressed, so no foreign popup can take it).  True when closeAlert was accepted."""
        r = self.rec
        if info['alert'] != 'accepted' or not info['alert_id']:
            r.note('%s: no accepted createAlert with an alertId in the prompt evidence; nothing to close' % label,
                   json.dumps(info))
            return False
        out = self.priv_luna('luna://com.webos.notification/closeAlert', '{"alertId":"%s"}' % info['alert_id'])
        good = '"returnValue":true' in out.replace(' ', '')
        r.check('%s: closeAlert on our restart alert (-a com.webos.applicationManager) was accepted' % label, good,
                'alertId %s\nreply: %s' % (info['alert_id'], out))
        return good

    def reboot_after_choice(self, comp0):
        """webOS 6-9, the patch not mounted: the choice mounts nothing and restarts nothing; the app asks the
        service for a system alert (Reboot / Later) and says so in a toast.  The runner checks, from the
        service's REBOOT_ASK log lines or notification bus events, that a createAlert was sent with the buttons
        Reboot and Later (or, when the alert was refused, that the toast fallback was shown).

        --reboot-via later (default): our alert is closed with closeAlert and the VM is restarted with
        beanjar stop/start.  No key is pressed.
        --reboot-via alert: prove ownership and exact buttons with Beanjar elements, click Reboot,
        and verify the boot changed. Keys retain the two-frame focus proof and human fallback."""
        r, d = self.rec, self.dev
        toasts = []

        def asked():
            u = self.ui() or {}
            if u.get('toast') and u['toast'] not in toasts:
                toasts.append(u['toast'])  # gone after 6 s
            info, _ = self.reboot_ask_evidence()
            return info if info['alert'] else None
        info = self.wait(asked, 90, 2) or self.reboot_ask_evidence()[0]
        if info['alert'] == 'refused':
            self.wait(lambda: self.reboot_ask_evidence()[0]['toast'] == 'accepted', 40, 3)
            info = self.reboot_ask_evidence()[0]
        ui = self.ui()
        if not info['alert'] and self.a.update_check and d.mounts():
            r.note('no restart prompt and the patch is mounted: the OLD build of --update-check mounts live (before 144ef21)', '')
            return
        self.shot('2-reboot-alert')
        info, raw_log = self.reboot_ask_evidence()
        r.check('6-9, patch not mounted: the service sent a createAlert for the restart prompt with the buttons %s and %s'
                % REBOOT_BUTTONS, info['message'] is not None and info['buttons'] == list(REBOOT_BUTTONS) and bool(info['alert']),
                raw_log or 'no REBOOT_ASK line')
        r.check('the restart prompt says "%s"' % REBOOT_PROMPT['apply'], info['message'] == REBOOT_PROMPT['apply'], repr(info['message']))
        if info['alert'] == 'accepted':
            r.check('the user was told: the restart alert was accepted by the notification service', True, raw_log)
            r.note('look at screenshot 2-reboot-alert: an alert "Home Chooser" with "%s" and the buttons Reboot and Later '
                   '(focus on Later)' % REBOOT_PROMPT['apply'], '')
        else:
            r.check('the alert was refused, so the fallback toast was shown (createToast accepted)',
                    info['alert'] == 'refused' and info['toast'] == 'accepted', raw_log)
        if ui is None:
            r.note('Home Chooser page not readable after the choice (an overlay is closed after a stretch without keys)',
                   self.page_gone_evidence(getattr(self, 'launch_mark', '') or ''))
        else:
            r.check('the app shows no restart dialog of its own', not ui.get('dialog'), json.dumps(ui))
            r.check('the app lists a "%s" row while the restart is pending' % RESTART_NOW, ui.get('restart') is True, json.dumps(ui))
            r.check('the app told "%s" in a toast (toasts seen: %s)' % (REBOOT_ASKED_TEXT, toasts),
                    REBOOT_ASKED_TEXT in toasts or ui.get('toast') == REBOOT_ASKED_TEXT, repr(toasts))
        s = self.state()
        st, raw = self.kf_status()
        r.note('state with the restart prompt up', json.dumps(s))
        r.check('before the reboot: homekey status says active=no and reboot=needed',
                st.get('active') == 'no' and st.get('reboot') == 'needed', raw)
        r.check('before the reboot: nothing is mounted', not s['mounts'], 'mounts: %s' % s['mounts'])
        r.check('before the reboot: the flag file and the init.d entry are written',
                s['flag'] == 'yes' and s['initd'] == BOOT_SCRIPT, 'flag %s; init.d %s' % (s['flag'], s['initd']))
        r.check('before the reboot: the record home is the launcher', s['record_home'] == self.launcher, s['record_raw'])
        self.check_no_compositor_restart('choosing the launcher', comp0)
        boot0 = d.boot_id()
        self.close_insp()
        observed = self.ask_monitor.snapshot() if getattr(self, 'ask_monitor', None) else None
        if getattr(self, 'ask_monitor', None):
            self.ask_monitor.close()
            self.ask_monitor = None
        if self.a.reboot_via == 'alert':
            def service_proof():
                raw = self.reboot_ask_log()
                return raw + '\nLS2: ' + json.dumps(observed), alert_service_proof(raw, REBOOT_PROMPT['apply'], 'Reboot', 'Later', observed=observed)
            def request_again():
                nonlocal observed
                observed = None
                self.ask_mark = self.messages_mark()
                d.launch(APP_ID)
                timing.sleep(3)
                self.click_element(RESTART_NOW, label='retry Restart now')
                self.wait(lambda: self.reboot_ask_evidence()[0]['alert'] == 'accepted', 30, 1)
            outcome = None
            def verify_reboot():
                nonlocal outcome
                outcome = self.wait_reboot(boot0, timeout=40 if self.use_click() else 240)
                return outcome if outcome in ('rebooted', 'off') else False
            automatic = self.alert_action(REBOOT_PROMPT['apply'], 'Reboot', 'Later',
                                          '2-reboot-alert', service_proof, verify_reboot,
                                          lambda: not any(e.get('text') == REBOOT_PROMPT['apply']
                                              for e in self.elements('reboot-result')['elements']), request_again,
                                          before_action=lambda: self.prepare_tvpower_reboot('2 reboot alert'))
            if not automatic:
                r.manual('press Reboot on the restart alert', ('automatic press declined: ' + self.alert_decline_reason + '. Press Reboot by hand within ') +
                     '240 s; otherwise the alert is closed and the VM is restarted with beanjar stop/start.')
            how = outcome if automatic else self.wait_reboot(boot0)
            if how in ('rebooted', 'off'):
                r.check('Reboot restarted the TV%s (alert -> tvpower/power/reboot; a VM that powers off counts)'
                        % (' (automatic press)' if automatic else ''), True, how)
                if how == 'off':
                    r.note('the VM powered off instead of rebooting (QEMU -no-reboot?); starting it with beanjar', '')
                    self.host.start(self.vm)
                    up = self.await_boot('2 reboot (Reboot on the alert; VM started again)', boot0)
                else:
                    up = self.await_boot('2 reboot (Reboot on the alert)', boot0)
            else:
                r.manual('restart alert wait timed out',
                         ('automatic press declined: ' + self.alert_decline_reason + '; ' if not automatic else '')
                         + 'no new boot id within 240 s')
                if how == 'unreachable':
                    r.note('closeAlert skipped: the guest is unreachable after the restart attempt', info.get('alert_id'))
                else:
                    self.close_reboot_alert('2 reboot', info)
                self.prepare_tvpower_reboot('2 reboot fallback')
                up = self.beanjar_reboot('2 reboot (alert left alone, beanjar stop/start)')
        else:
            self.close_reboot_alert('2 reboot (Later)', info)
            timing.sleep(3)
            self.shot('2-reboot-alert-closed')
            self.prepare_tvpower_reboot('2 reboot Later')
            up = self.beanjar_reboot('2 reboot (alert closed, then beanjar stop/start)')
        if not up:
            raise RuntimeError('the VM did not come back after the reboot')
        self.after_boot_checks('after the reboot')

    def after_boot_checks(self, label):
        """6-9 boot applies the volatile JS filter and starts the watcher without a GUI restart."""
        r = self.rec
        active = self.wait(lambda: self.watcher_active() == 'active', 180, 5)
        r.check(label + ': the watcher is active (started from init.d)', bool(active), self.watcher_active())
        self.wait(lambda: self.hook_mounts_match(), 180, 5)
        st, raw = self.kf_status()
        r.check(label + ': the boot hook mounted the patch: status active=yes and reboot=no',
                st.get('active') == 'yes' and st.get('reboot') == 'no', raw)
        r.note(label + ': native boot apply log (no GUI restart permitted)',
               self.dev.sh("grep -E 'HomeChooser.*(START|HOMEKEY)' /var/log/messages | tail -n 25")[1])
        r.note(label + ': compositor QML and crash evidence from retained messages logs', self.dev.sh(
            messages_command() + " | grep -E 'surface-manager.*(WebOSCompositorWindow::setCompositorMain|"
            "Type .*unavailable|Syntax error|is not a type)|crashd.*CRASHED|WebAppMgr.*(SIGSEGV|segfault)'"
            " | tail -n 24")[1].strip() or 'no matching retained line')

    def step_reboot(self):
        """Optional (--reboot-check), launcher chosen: after a boot the watcher re-applied the policy
        (it is never persisted) and the stock home is not running.  On 6.x to 9.x
        snapshot-boot-components launches the stock home at boot, outside the preload manager; the
        watcher's delayed close (45 s) is what removes it.  5.x: no policy call is expected."""
        r, d, host = self.rec, self.dev, self.host
        r.begin('3r reboot with the launcher chosen (--reboot-check)')
        self.prepare_tvpower_reboot('3r reboot')
        up = self.beanjar_reboot('3r')
        if not up:
            return
        self.verify_reboot_apply()

    def boot_first_app(self):
        """(kind, app, detail) of what bootd launched first on this boot, read from /var/log/bootd.log
        (docs/home-gate.md "At boot"); the two auto-launch settings are read for the record only."""
        rc, text = self.dev.sh('cat %s 2>&1' % BOOTD_LOG)
        settings = self.dev.luna('luna://com.webos.settingsservice/getSystemSettings',
                                 '{"category":"general","keys":["homeAutoLaunch","lgChannelAutoLaunch"]}')
        found = bootd_first_app(text)
        self.rec.note('bootd first app of this boot: %s %s (settings are read for the log only)' % found[:2],
                      found[2] + '\n' + settings.strip()[:400])
        return found

    def check_gate_front(self, label):
        """6.x to 9.x after a boot: the launcher is in front only where bootd would have opened LG Home
        (homeAutoLaunch on, lgChannelAutoLaunch off): the gate (mounted, stock Home not running:
        check_home_gate) put the chosen full-screen launcher in front.  Where bootd booted another app
        (lgChannelAutoLaunch on: Live TV), that app stays and only the Home key opens the launcher.  For an
        overlay launcher the last input app is in front.  What bootd did is read from its log, not guessed."""
        r = self.rec
        overlay = self.L.overlay
        kind, app, detail = self.boot_first_app()
        want_launcher, why = boot_gate_expectation(kind, app)
        if want_launcher is None and not overlay:
            r.check('%s: bootd.log tells what the TV booted to' % label, False, detail)
        elif want_launcher is False and not overlay:
            r.note(why, detail)
            r.check('%s: bootd booted to %s, not LG Home' % (label, app), app != STOCK, detail)
            self.wait(lambda: STOCK not in self.running_ids(), 60, 5)
            self.home_press(self.launcher, 'after-reboot-home')
        else:
            want = lambda fg: bool(fg) and fg != STOCK and (overlay or fg == self.launcher)
            ok = self.wait(lambda: want(self.dev.foreground()[0]), 180, 5)
            fg = self.dev.foreground()
            r.check('%s: %s in front (the gate ran at boot)' % (
                    label, 'the last input app (overlay launcher)' if overlay else 'the chosen launcher ' + self.launcher),
                    bool(ok), fg[1])
        r.check(label + ': the gate is mounted and the real stock Home is not running',
                self.gate_mount_count() >= 1 and STOCK not in self.running_ids(), repr(self.running_ids()))

    def check_boot_input(self, label):
        """10+ after a boot with an overlay launcher: the watcher launched the last input once
        (service/src/watcher/input.c), so a full-screen input app is in front beneath the overlay."""
        r = self.rec
        first = self.boot_first_app()  # bootd.log: what the TV booted to decides what the watcher should do
        verdict = lambda: boot_input_verdict(self.log_lines(), self.dev.foreground()[0], first)
        self.wait(lambda: verdict()[0], BOOT_INPUT_WAIT, 5)
        ok, why = verdict()
        lines = [l for l in self.log_lines() if 'last input:' in l]
        r.check(label + ': the watcher launched the last input once and the input is in front, or left the app bootd booted in front (overlay launcher)', ok,
                why + '\n' + '\n'.join(lines))
        r.note(label + ': the overlay launcher over the input', '%s running=%s' % (
            self.launcher, self.launcher in self.running_ids()))

    def verify_reboot_apply(self):
        r, d = self.rec, self.dev
        if self.uses_home_key_hook():
            self.after_boot_checks('after the boot')  # the watcher, the boot hook's mounts, status
        else:
            active = self.wait(lambda: self.watcher_active() == 'active', 180, 5)
            r.check('after the boot the watcher is active (started from init.d)', bool(active), self.watcher_active())
        s = self.state()
        r.check('after the boot the choice and the record are still the launcher',
                s['choice'] == self.launcher and s['record_home'] == self.launcher, s['record_raw'])
        if self.major >= 6:
            # the call comes at once, the close after the 45 s settle
            self.wait(lambda: 'isEnabled=false' in self.preload_log(), 150, 5)
            log = self.preload_log()
            r.check('after the boot the watcher called setPreloadPolicy isEnabled=false (log is from this boot: /tmp)',
                    'isEnabled=false' in log, log)
        else:
            r.check('after the boot (5.x) the watcher made no preload call', 'setPreloadPolicy' not in self.preload_log(),
                    self.preload_log())
        self.log_mark = 0  # a new boot, a new /tmp log
        if self.uses_override():
            self.check_idle_chosen('after the reboot')
            if self.L.overlay:
                self.check_boot_input('after the reboot')
        self.check_preload_off('after the reboot', settle=30)
        if uses_home_gate(self.major):
            self.check_gate_front('after the reboot')
        if self.uses_home_key_hook():
            self.check_hook_status('after reboot')
        self.residency('after the reboot')
        self.shot('3r-after-reboot')
        if self.a.api_check and self.is_owner():  # the API caller is the run's first launcher
            self.api_after_reboot()

    def cache_prepare_measure(self):
        """Validate the native generator without a Node process or prepared boot cache."""
        rc, out = self.dev.sh('/bin/sh ' + SCRIPTS_DIR + '/homechooser-homekey prepare')
        self.rec.check('native generator accepts this firmware', rc == 0, out)

    def step_cache(self):
        """The former cache scenario now verifies JS-only activation after boot."""
        self.after_boot_checks('native live-filter boot')
        rc, out = self.dev.sh('test ! -d /var/lib/webosbrew/homechooser/hook && echo no-cache; '
                              'test ! -e /var/preferences/configd_factory_db.json && echo no-factory-db')
        self.rec.check('no boot cache and no persistent configd override',
                       rc == 0 and 'no-cache' in out and 'no-factory-db' in out, out)
        self.home_press(self.launcher, 'native-filter-home')
