# SPDX-License-Identifier: Apache-2.0
"""The home and screensaver scenario, S0-S4 (--scenario screensaver|all)."""
import json
import re
import time

from .. import timing
from ..beanjar import ScreensaverOutcome, power_field
from ..constants import APP_ID, CHOICE, FULLHOME, SCREENSAVERS, STOCK, TVPOWER, TVPOWER_REBOOT_URI
from ..evidence import messages_command, parse_luna
from ..releases import closes_stock_home


class ScreensaverSteps:
    """The home and screensaver scenario, S0-S4 (--scenario screensaver|all)."""

    # -- home and screensaver (all calls below are bounded; no settings writes or ENTER)
    def screensaver_power(self):
        power = {'power': self.dev.luna_as(None, TVPOWER + 'power/getPowerState')[1:]}
        power['power2'] = (('Not queried on webOS 6 (6.5.2 device evidence: Unknown method)', None)
                           if self.major == 6 else
                           self.dev.luna_as(None, TVPOWER + 'power2/getPowerState')[1:])
        return power

    def screensaver_snapshot(self, label):
        d = self.dev
        choice_rc, choice = d.sh('cat %s 2>&1' % CHOICE)
        record_home, record_raw = d.record_home()
        snapshot = {'power': self.screensaver_power(), 'foreground': d.foreground(),
                    'unit': d.unit('homechooser-home'), 'choice': [choice_rc, choice.strip()],
                    'record_home': record_home, 'defaultApps': parse_luna(record_raw),
                    'preload': d.preload_policy(), 'boot': d.boot_id()}
        self.rec.note(label, json.dumps(snapshot))
        self.shot(label)
        return snapshot

    def screensaver_diagnostics(self, label):
        """Retain both state replies and tvpower lines before classifying a refusal."""
        self.rec.note(label + ': power and power2', json.dumps(self.screensaver_power()))
        mark = getattr(self, 'screensaver_log_mark', '')
        if re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z?', mark):
            command = messages_command() + " | awk '$1 >= \"%s\"'" % mark
        else:
            command = messages_command()
        self.rec.note(label + ': tvpower logs', self.dev.sh(
            command + " | grep -Ei 'tvpower|Screen Saver|lgchannels' | tail -n 160")[1])

    @staticmethod
    def screensaver_require(ok, evidence):
        if not ok:
            raise ScreensaverOutcome('FAIL', evidence)

    def screensaver_ready(self, power):
        return (any(power_field(row[1], 'processing') == 'Screen Saver Ready' for row in power.values())
                or power_field(power['power2'][1], 'sub processing') == 'screen saver ready')

    def beanjar_screensaver(self, verb):
        if self.major == 6:
            self.rec.note('beanjar screensaver unavailable on webOS 6; not attempted',
                          'Beanjar requires power2/getPowerState and getForegroundApps window types; '
                          'use legacy state evidence and normal synced-stop cleanup for beanjar#56.')
            return None
        result = self.host.screensaver(verb, self.vm)
        if result is None:
            if not getattr(self.host, '_screensaver_missing_logged', False):
                self.rec.note('beanjar screensaver subcommand missing; recovery unavailable; keeping existing behaviour',
                              str(getattr(self.host, '_screensaver_probe', 'older Beanjar')))
                self.host._screensaver_missing_logged = True
            return None
        rc, raw = result
        self.rec.note('beanjar screensaver %s JSON (rc=%s)' % (verb, rc), raw)
        return rc, raw, parse_luna(raw)

    def prepare_tvpower_reboot(self, label):
        """Recover only a stale Ready state; retain helper output and independent state proof."""
        before = self.screensaver_power()
        self.rec.note(label + ': power before reboot', json.dumps(before))
        if not self.screensaver_ready(before):
            return True
        self.screensaver_diagnostics(label + ': stale Screen Saver Ready before reboot')
        if self.major == 6:
            raise ScreensaverOutcome('BLOCKED', 'beanjar#56: Screen Saver Ready on webOS 6; '
                                     'Beanjar screensaver helper unsupported; use normal synced-stop cleanup')
        helper = self.beanjar_screensaver('stop')
        if helper is None:
            return False  # Old Beanjar: caller keeps its existing refusal/BLOCKED path.
        rc, raw, reply = helper
        after = self.screensaver_power()
        self.rec.note(label + ': power after beanjar screensaver stop', json.dumps(after))
        routes = ('power', 'power2') if self.major >= 7 else ('power',)
        readable = all(after[route][1] and after[route][1].get('returnValue') is True for route in routes)
        if rc or not reply or reply.get('action') != 'stop' or not readable or self.screensaver_ready(after):
            self.screensaver_diagnostics(label + ': screensaver recovery failed')
            raise ScreensaverOutcome('BLOCKED', 'beanjar#56: stale Screen Saver Ready recovery failed: ' + raw)
        self.rec.outcome('recovered stale Screen Saver Ready via beanjar screensaver stop, beanjar#56',
                         'RECOVERED', json.dumps({'helper': reply, 'before': before, 'after': after}))
        return True

    def screensaver_state(self, power, shown):
        route = 'power' if self.major == 6 else 'power2'
        reply = power[route][1]
        field = 'state' if self.major == 6 else 'sub state'
        expected = ('Screen Saver' if shown else 'Active') if self.major == 6 else ('screen saver' if shown else 'active')
        return bool(reply and reply.get('returnValue') is True and not self.screensaver_ready(power)
                    and power_field(reply, field) == expected)

    def screensaver_shown(self, power):
        return self.screensaver_state(power, shown=True)

    def screensaver_active(self, power):
        return self.screensaver_state(power, shown=False)

    def power_settled(self, power):
        """tvpowerd is idle: ACTIVE / active (6.x: Active) and no 'sub processing' (6.x: 'processing')."""
        route = 'power' if self.major == 6 else 'power2'
        reply = power[route][1]
        if not (reply and reply.get('returnValue') is True):
            return False
        if self.major == 6:
            return power_field(reply, 'state') == 'Active' and not power_field(reply, 'processing')
        return (power_field(reply, 'state') == 'ACTIVE' and power_field(reply, 'sub state') == 'active'
                and not power_field(reply, 'sub processing'))

    def screensaver_settle(self, label):
        """Bounded wait (60 s, scaled by SLOWDOWN) for a settled power state before a start request.

        tvpowerd accepts turnOnScreenSaver during its own boot Screen On step and then silently
        cancels it (beanjar#184, firmware behaviour). Returns (settled, evidence)."""
        polls, last = [], [None]
        def settled():
            last[0] = self.screensaver_power()
            polls.append(self.power_settled(last[0]))
            return polls[-1]
        t0 = time.time()
        ok = bool(self.wait(settled, 60, 2))
        evidence = 'power %s after %.1fs, %d poll(s); last seen: %s' % (
            'settled' if ok else 'NEVER settled within 60s (x%s slowdown)' % timing.SLOWDOWN,
            time.time() - t0, len(polls), json.dumps(last[0]))
        self.rec.note('screensaver start %s: power settle wait' % label, evidence)
        return ok, evidence

    def screensaver_request(self):
        """One start request. Returns None when the request was accepted, or a string describing
        a refusal during a power transition (the only refusal worth a retry)."""
        if getattr(self.a, 'saver_start', 'tvpower') == 'beanjar':
            helper = self.beanjar_screensaver('start')
            if helper is None:
                raise ScreensaverOutcome('SKIPPED', '--saver-start beanjar requires the beanjar screensaver subcommand')
            rc, raw, reply = helper
            if rc and re.search(r'power transition', raw, re.I):
                return 'beanjar screensaver start refused during a power transition: ' + raw
            if rc or not reply or reply.get('action') != 'start':
                self.screensaver_diagnostics('beanjar screensaver start failed')
                raise ScreensaverOutcome('BLOCKED', 'beanjar screensaver start failed: ' + raw)
            return None
        route = 'power' if self.major == 6 else 'power2'
        rc, raw, reply = self.dev.luna_as(None, TVPOWER + route + '/turnOnScreenSaver')
        self.rec.note('screensaver start reply (root, no -a)', raw)
        if rc or not reply or reply.get('returnValue') is not True:
            self.screensaver_diagnostics('screensaver refused')
            reason = str((reply or {}).get('errorText', raw)).lower()
            precondition = bool(re.search(
                r'screensaverenabled|screen\s*saver.*(?:disabled|not enabled|off)|'
                r'first[ -]?use|sub[ -]?state', reason))
            raise ScreensaverOutcome('SKIPPED' if precondition and reply and
                                     reply.get('returnValue') is False else 'FAIL', raw)
        return None

    def screensaver_start(self):
        if self.major < 6:
            raise ScreensaverOutcome('SKIPPED', 'No evidenced screensaver endpoint for webOS <6')
        beanjar = getattr(self.a, 'saver_start', 'tvpower') == 'beanjar'
        attempts = []
        for attempt in (1, 2):
            label = 'attempt %d' % attempt
            ok, settle = self.screensaver_settle(label)
            if not ok:
                self.screensaver_diagnostics('screensaver start %s: power never settled' % label)
                raise ScreensaverOutcome('BLOCKED', 'power state never settled before the start request; '
                                         'request not sent. ' + ' | '.join(attempts + [label + ': ' + settle]))
            refused = self.screensaver_request()
            if refused:
                attempts.append('%s: %s (%s)' % (label, refused, settle))
                self.rec.note('screensaver start %s refused during power transition' % label, refused)
                continue
            if self.wait(lambda: self.screensaver_shown(self.screensaver_power()), 45, 2):
                self.rec.note('screensaver confirmed after beanjar start' if beanjar
                              else 'screensaver confirmed: native shown state',
                              json.dumps(self.screensaver_power()))
                if attempts:
                    self.rec.note('screensaver start succeeded on retry', ' | '.join(attempts))
                return
            power = self.screensaver_power()
            self.screensaver_diagnostics('screensaver start %s: state not confirmed' % label)
            if self.screensaver_ready(power):
                raise ScreensaverOutcome('BLOCKED', 'beanjar#56: Screen Saver Ready')
            if not self.screensaver_active(power):
                raise ScreensaverOutcome('FAIL', 'Screensaver did not reach the native shown state; power: ' +
                                         json.dumps(power))
            attempts.append('%s: dropped by firmware (power back to plain Active, no screen saver; %s)' %
                            (label, settle))
            self.rec.note('screensaver start %s dropped by firmware' % label, json.dumps(power))
        raise ScreensaverOutcome('FAIL', 'Screensaver start dropped by firmware twice or refused during '
                                 'power transitions: ' + ' | '.join(attempts))

    def screensaver_button(self, key, expected=None):
        # This path deliberately uses Beanjar's IR remote, never turnOnScreen or a dialog action.
        rc, raw = self.host.remote_press(self.vm, key)
        self.screensaver_require(rc == 0, 'Remote %s failed: %s' % (key, raw))
        def dismissed():
            power = self.screensaver_power()
            return (self.screensaver_active(power)
                    and (expected is None or self.dev.foreground()[0] == expected))
        if not self.wait(dismissed, 30, 2):
            power = self.screensaver_power()
            self.screensaver_diagnostics('screensaver dismissal timeout')
            raise ScreensaverOutcome('BLOCKED' if self.screensaver_ready(power) else 'FAIL',
                                     'beanjar#56: Screen Saver Ready after button dismissal'
                                     if self.screensaver_ready(power) else
                                     '%s did not dismiss screensaver and return %s' % (key, expected))
        timing.sleep(1)
        foreground = self.dev.foreground()
        self.rec.note('%s dismissed screensaver; observed foreground %s' % (key, foreground[0]),
                      json.dumps({'power': self.screensaver_power(), 'foreground': foreground}))
        return foreground

    def screensaver_unchanged(self, before, after):
        for field in ('choice', 'defaultApps', 'unit', 'boot'):
            self.screensaver_require(before[field] == after[field], '%s changed: %r -> %r' %
                                     (field, before[field], after[field]))
        # getPreloadPolicy returns every app, and firmware can reorder that array while
        # showing a screensaver. Compare the complete stock-home entry, retaining raw
        # replies in snapshots, rather than asserting unrelated ordering is stable.
        def stock_policy(sample):
            reply = parse_luna(sample[1])
            rows = reply.get('applications') if isinstance(reply, dict) and reply.get('returnValue') is True else None
            if not isinstance(rows, list):
                return None
            matches = [row for row in rows if isinstance(row, dict) and row.get('id') == STOCK]
            return matches[0] if len(matches) == 1 else None
        old_policy, new_policy = stock_policy(before['preload']), stock_policy(after['preload'])
        self.screensaver_require(old_policy is not None and new_policy is not None
                                 and old_policy == new_policy
                                 and before['preload'][0] == after['preload'][0],
                                 'Stock-home preload policy changed or unreadable: %r -> %r' %
                                 (before['preload'], after['preload']))
        self.screensaver_require(after['choice'] == [0, self.launcher]
                                 and after['record_home'] == self.launcher
                                 and after['defaultApps'] is not None
                                 and after['preload'][0] is False
                                 and after['unit'].get('ActiveState') == 'active'
                                 and after['unit'].get('MainPID') not in (None, '', '0')
                                 and after['unit'].get('NRestarts') is not None,
                                 'Chosen launcher/daemon/preload state is not readable and applied')

    def screensaver_step(self, number, action, dependency=None):
        label = 'S%d home and screensaver' % number
        self.rec.begin(label)
        before = None
        status, evidence = 'PASS', 'Screensaver assertions passed'
        try:
            self.screensaver_log_mark = self.messages_mark()
            before = self.screensaver_snapshot('S%d-before' % number)
            if dependency and dependency != 'PASS':
                raise ScreensaverOutcome('SKIPPED' if dependency == 'SKIPPED' else 'BLOCKED',
                                         'Prerequisite screensaver step: ' + dependency)
            observed = action(before)
            if observed is not None:
                evidence = json.dumps(observed)
        except ScreensaverOutcome as exc:
            status, evidence = exc.status, str(exc)
        except Exception as exc:
            status, evidence = 'FAIL', repr(exc)
        finally:
            try:
                self.screensaver_snapshot('S%d-after' % number)
            except Exception as exc:
                status, evidence = 'FAIL', 'After snapshot failed: ' + repr(exc)
        self.rec.outcome(label, status, evidence)
        return status

    def screensaver_baseline(self):
        self.screensaver_stock = {}
        def action(before):
            self.screensaver_require(APP_ID not in self.dev.installed() and before['choice'][0] != 0,
                                     'S0 requires Home Chooser absent, including its choice file')
            for key in ('BACK', 'HOME'):
                self.dev.launch('org.webosbrew.hbchannel')
                self.screensaver_require(bool(self.wait(lambda: self.dev.foreground()[0] ==
                                                        'org.webosbrew.hbchannel', 30, 2)),
                                         'Baseline previous app did not reach foreground')
                previous = self.dev.foreground()
                self.screensaver_start()
                wake = self.screensaver_button(key)  # Firmware observation, no assumed destination.
                self.shot('S0-%s-wake' % key.lower())
                rc, raw = self.host.remote_press(self.vm, 'HOME')
                self.screensaver_require(rc == 0, 'S0 second HOME delivery failed: ' + raw)
                self.wait(lambda: (lambda fg: fg if fg[0] and fg[0] != wake[0] else None)(
                    self.dev.foreground()), 15, 1)
                timing.sleep(1)
                home = self.dev.foreground()
                self.shot('S0-%s-then-home' % key.lower())
                self.screensaver_stock[key] = {'previous': previous, 'wake': wake, 'home': home}
                self.rec.note('S0 stock %s then HOME baseline' % key, json.dumps(self.screensaver_stock[key]))
            return self.screensaver_stock
        self.host.vnc_start(self.vm)
        return self.screensaver_step(0, action)

    def screensaver_expected(self, key, phase, previous):
        """Map an observed stock-Home dispatch to our launcher; retain every other destination."""
        stock = getattr(self, 'screensaver_stock', {}).get(key)
        if not stock or not stock[phase][0] or not stock['previous'][0]:
            raise ScreensaverOutcome('BLOCKED', 'S0 has no readable %s %s foreground baseline' % (key, phase))
        observed = stock[phase][0]
        if observed == stock['previous'][0]:
            return previous
        if observed in (STOCK, FULLHOME, self.stock_id):
            return self.launcher
        return observed

    def screensaver_followup_home(self, key, previous, label):
        expected = self.screensaver_expected(key, 'home', previous)
        rc, raw = self.host.remote_press(self.vm, 'HOME')
        self.screensaver_require(rc == 0 and bool(self.wait(lambda: self.dev.foreground()[0] == expected, 30, 2)),
                                 '%s: second HOME did not match S0 (%s): %s' % (label, expected, raw))
        self.rec.note(label + ': second HOME matched S0', self.dev.foreground()[1])

    def screensaver_selected(self, baseline):
        def s1(before):
            self.screensaver_require(self.L.is_fixture and not self.L.overlay,
                                     'Screensaver foreground scenario requires a normal fixture launcher')
            self.dev.launch(self.launcher)
            self.screensaver_require(bool(self.wait(lambda: self.dev.foreground()[0] == self.launcher, 30, 2)),
                                     'Chosen fixture did not reach foreground')
            # Sample after launch has settled, so normal deferred stock-home close is outside S1.
            timing.sleep(5)
            stable = self.screensaver_snapshot('S1-launcher-settled')
            log0 = self.log_lines()
            self.screensaver_start()
            timing.sleep(5)
            during = self.screensaver_snapshot('S1-during')
            self.screensaver_unchanged(stable, during)
            lines = self.log_lines()
            delta = lines[len(log0):] if lines[:len(log0)] == log0 else lines
            self.rec.note('S1 daemon log during screensaver', '\n'.join(delta))
            self.screensaver_require(not any('closeByAppId' in line or 'setPreloadPolicy' in line
                                            for line in delta), 'Daemon reacted during screensaver: ' + '\n'.join(delta))
            if closes_stock_home(self.major):
                # 10+ follows foreground/life events; ignoring the screensaver is shown by the absence of any
                # action above (the daemon logs actions, not skipped events), so only record what it logged.
                self.rec.note('S1 daemon lines naming a screensaver app',
                              '\n'.join(line for line in delta if any(app in line for app in SCREENSAVERS)) or 'none')
        first = self.screensaver_step(1, s1, baseline)
        def s2(before):
            self.screensaver_require(self.screensaver_shown(before['power']), 'S2 requires the S1 screensaver')
            # Leave S1's saver using the measured BACK behaviour, then test HOME from the same
            # ordinary app as S0. A wake to an already foreground launcher cannot prove dispatch.
            self.screensaver_button('BACK', self.screensaver_expected('BACK', 'wake', self.launcher))
            previous = 'org.webosbrew.hbchannel'
            self.dev.launch(previous)
            self.screensaver_require(bool(self.wait(lambda: self.dev.foreground()[0] == previous, 30, 2)),
                                     'S2 previous app not foreground')
            self.screensaver_start()
            self.screensaver_button('HOME', self.screensaver_expected('HOME', 'wake', previous))
            self.screensaver_unchanged(before, self.screensaver_snapshot('S2-dismissed'))
            stock = self.screensaver_stock['HOME']
            if stock['wake'][0] not in (STOCK, FULLHOME, self.stock_id):
                self.screensaver_followup_home('HOME', previous, 'S2')
                self.screensaver_unchanged(before, self.screensaver_snapshot('S2-home'))
        second = self.screensaver_step(2, s2, first)
        def s3(before):
            previous = 'org.webosbrew.hbchannel'
            self.dev.launch(previous)
            self.screensaver_require(bool(self.wait(lambda: self.dev.foreground()[0] == previous, 30, 2)),
                                     'Previous app not foreground')
            self.screensaver_start()
            self.screensaver_button('BACK', self.screensaver_expected('BACK', 'wake', previous))
            self.screensaver_unchanged(before, self.screensaver_snapshot('S3-back'))
            self.screensaver_followup_home('BACK', previous, 'S3')
            self.screensaver_unchanged(before, self.screensaver_snapshot('S3-home'))
        third = self.screensaver_step(3, s3, second)
        def s4(before):
            self.screensaver_start()
            try:
                self.screensaver_button('BACK', self.launcher)
            except ScreensaverOutcome as exc:
                if exc.status != 'BLOCKED' or not self.screensaver_ready(self.screensaver_power()):
                    raise
                if not self.prepare_tvpower_reboot('S4 after BACK'):
                    raise
                power = self.screensaver_power()
                self.screensaver_require(self.screensaver_active(power)
                                         and self.dev.foreground()[0] == self.launcher,
                                         'S4 recovered Ready but previous foreground did not return')
            self.screensaver_unchanged(before, self.screensaver_snapshot('S4-dismissed'))
            boot0 = self.dev.boot_id()
            self.prepare_tvpower_reboot('S4 tvpower reboot')
            rc, raw, reply = self.dev.luna_as(None, TVPOWER_REBOOT_URI, '{"reason":"remoteKey"}')
            self.rec.note('S4 tvpower reboot reply', raw)
            if rc or not reply or reply.get('returnValue') is not True:
                self.screensaver_diagnostics('S4 reboot refused')
                busy = 'power is busy' in raw.lower() or self.screensaver_ready(self.screensaver_power())
                raise ScreensaverOutcome('BLOCKED' if busy else 'FAIL',
                                         ('beanjar#56: ' if busy else '') + raw)
            how = self.wait_reboot(boot0, timeout=240)
            if how == 'off':
                rc, raw = self.host.start(self.vm)
                self.screensaver_require(rc == 0, 'VM restart after guest power-off failed: ' + raw)
            if how not in ('off', 'rebooted'):
                self.screensaver_diagnostics('S4 reboot timeout')
                ready = self.screensaver_ready(self.screensaver_power())
                raise ScreensaverOutcome('BLOCKED' if ready else 'FAIL',
                                         'beanjar#56: Screen Saver Ready after dismissal' if ready else
                                         'TV did not restart after accepted tvpower reboot')
            self.screensaver_require(self.await_boot('S4 tvpower reboot', boot0),
                                     'TV did not return from tvpower reboot')
            start = len(self.rec.results)
            self.verify_reboot_apply()
            self.screensaver_require(not any(row['status'] == 'FAIL' for row in self.rec.results[start:]),
                                     'Boot apply assertions failed')
        self.screensaver_step(4, s4, third)
