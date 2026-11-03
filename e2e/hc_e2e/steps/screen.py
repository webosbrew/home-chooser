# SPDX-License-Identifier: Apache-2.0
"""Screen input and capture: screenshots, element clicks, keys, alert actions, key focus."""
import json
import os
import re
import time

from .. import timing
from ..alerts import alert_focus, alert_style_swap, match_element
from ..beanjar import KeySendError
from ..constants import SCREENSAVER_ID
from ..evidence import AUDIOOUTPUTD_REF
from ..inspector import FOCUS_JS
from ..recorder import screenshot_reason


class ScreenInput:
    """Screen input and capture: screenshots, element clicks, keys, alert actions, key focus."""

    # -- helpers
    def shot(self, label):
        """The TV picture from `beanjar screenshot` (about 2 s, headless, no viewer needed).  Only if
        that fails: the guest capture (ares-do screenshot; com.webos.service.capture fails on webOS 5
        and 10.x to 12.x, and after the first failure it is not tried again). Each
        fallback used is logged."""
        if not screenshot_reason(label):
            self.rec.say('screenshot %s skipped (not a key step: SCREENSHOTS)' % label)
            return None
        self.shot_n += 1
        name = '%02d-%s%s.png' % (self.shot_n, (self.L.tag + '-') if self.a.multi else '', label)
        path = os.path.join(self.rec.outdir, name)
        ok, detail = self.host.tv_screenshot(self.vm, path)
        self.rec.say('screenshot %s (beanjar screenshot): %s' % (name, detail[-160:] if detail else ('ok' if ok else 'failed')))
        if ok:
            return path
        self.rec.say('FALLBACK: beanjar screenshot failed for %s' % name)
        if not self.capture_broken:
            rc, out = self.dev.screenshot(path)
            self.rec.say('FALLBACK screenshot %s (guest capture): %s' % (name, 'ok' if rc == 0 else out.strip()))
            if rc != 0:
                self.capture_broken = True
            else:
                return path
        self.rec.say('FALLBACK: no host desktop capture; screenshots unavailable')
        return path

    def fail_shot(self, name):
        """Recorder.on_fail: one screenshot per step that has a FAIL (the first one's), never raising."""
        done = self.__dict__.setdefault('fail_shots', set())
        if self.rec.step in done:
            return
        done.add(self.rec.step)
        try:
            self.shot('fail-' + (re.sub(r'[^A-Za-z0-9]+', '-', self.rec.step).strip('-')[:60] or 'run'))
        except Exception as exc:
            self.rec.say('FAIL screenshot not taken: %r' % (exc,))

    def use_click(self):
        return (getattr(getattr(self, 'a', None), 'input_mode', 'keys') == 'click'
                and self.host is not None and self.host.click_available())

    def elements(self, label):
        rc, raw = self.host.display_beanjar('elements', self.host.remote_target(self.vm), '--json')
        self.rec.say('%s elements (rc=%s):\n%s' % (label, rc, raw))
        if rc:
            raise RuntimeError('elements failed: ' + raw)
        frame = json.loads(raw)
        if getattr(self.host, '_frame_mapping_logged', False) is not True:
            self.rec.note('screen coordinate mapping', json.dumps({k: frame.get(k) for k in
                          ('width', 'height', 'source_width', 'source_height', 'scale', 'offset_x', 'offset_y')}))
            self.host._frame_mapping_logged = True
        return frame

    def click_element(self, text=None, role=None, region=None, effect=None, label='UI', frame=None, read_after=True,
                      focused_row=None):
        frame = frame if frame is not None else self.elements(label)
        row = match_element(frame, text, role, region)
        self.rec.say('input click: %s %r at %s' % (label, text or role, row['centre']))
        rc, out = self.host.remote_click(self.vm, row['centre'], frame)
        if rc:
            raise KeySendError('click failed: ' + out)
        if effect is not None:
            seen = self.wait(effect, 5 if focused_row else 20, 1)
            if not seen and focused_row:
                # The pointer arrived and focused the row but the activation was lost (seen once on 11.x):
                # press OK once, only when the row is focused and no dialog is up.
                ui = self.ui() or {}
                if ui.get('row') == focused_row and not ui.get('dialog'):
                    self.rec.say('click fallback: %s is focused but nothing happened after 5 s; pressing OK once' % label)
                    self.press('OK')
                seen = self.wait(effect, 15, 1)
            if not seen:
                self.click_diagnostics(label)
                raise RuntimeError('click effect not observed: ' + label)
        return self.elements(label + '-after') if read_after else None

    def click_diagnostics(self, label):
        """Evidence only, before 'click effect not observed' is raised: the screen and one ui() read
        (focused element, dialog) after the click.  Never raises and never changes the click."""
        try:
            self.shot(label + '-click-not-observed')
        except Exception as exc:
            self.rec.say('click diagnostics: screenshot failed: %r' % (exc,))
        try:
            ui = self.ui()
            self.rec.say('click diagnostics (%s): ui() = %s' % (label, json.dumps(ui) if ui is not None else
                         'None (inspector read failed: %s)' % (getattr(self.insp, 'last_error', None)
                                                                if self.insp else 'no inspector')))
        except Exception as exc:
            self.rec.say('click diagnostics: ui() failed: %r' % (exc,))

    def alert_action(self, message, target, safe, label, proof, verify, closed, request, before_action=None):
        """Verify every alert click; retry only a proven dismissal without its action."""
        for attempt in range(2):
            if before_action:
                before_action()
            if not self.press_alert(message, target, safe, label, proof):
                return False
            result = verify()
            if result:
                return result
            if not self.use_click() or not closed():
                raise RuntimeError(label + ': alert action was not observed')
            evidence = '%s closed without %s; attempt %s' % (message, target, attempt + 1)
            self.rec.note(label + ': alert click did not act', evidence)
            if attempt:
                raise RuntimeError(label + ': second alert click failed: ' + evidence)
            request()
        return False

    # The service writes its UNINSTALL_ASK / REBOOT_ASK line before it replies, but pmlogd flushes it to
    # /var/log/messages late on a slow guest (matrix-20261102-194054/w22o-number1: the alert was on screen
    # at the first read, the line was not there yet).  An alert that is visibly up is not a reason to
    # give up on the proof: read it again for a while.
    PROOF_WAIT = 20

    def await_service_proof(self, service_proof, timeout=None, every=2):
        """service_proof() -> (raw, proven), repeated until proven or `timeout` seconds (scaled) pass;
        the last reading is returned."""
        end = time.time() + timing.seconds(self.PROOF_WAIT if timeout is None else timeout)
        while True:
            raw, proven = service_proof()
            if proven or time.time() >= end:
                return raw, proven
            timing.sleep(every)

    def press_alert(self, message, target, safe, label, service_proof):
        """Prove ownership and screen buttons; keys additionally require two focus proofs."""
        def read():
            raw_service, proven = self.await_service_proof(service_proof)
            self.rec.say('%s service ownership proof:\n%s' % (label, raw_service))
            if not proven:
                raise ValueError('no matching accepted service alert from this request')
            if self.use_click():
                frame = self.elements(label)
                path = 'live beanjar elements'
            else:
                # OCR runs locally for older hosts without pointer input.
                path = self.shot(label + '-focus-proof')
                if not os.path.isfile(path):
                    raise ValueError('screen proof screenshot missing')
                rc, raw = timing.run(['beanjar', 'elements', '--image', path, '--json'], timeout=60)
                self.rec.say('%s elements (rc=%s):\n%s' % (label, rc, raw))
                if rc:
                    raise ValueError('elements command failed')
                frame = json.loads(raw)
            focus, direction = alert_focus(frame, message, target, safe, require_focus=not self.use_click())
            return focus, direction, path, frame
        try:
            focus, direction, _, before = read()
            if self.use_click():
                self.click_element(target, 'button', label=label, frame=before, read_after=False)
                return True
            if focus != safe:
                raise ValueError('initial focus is %s, expected safe default %s' % (focus, safe))
            self.press(direction)
            focus, _, path, after = read()
            if focus != target:
                raise ValueError('focus did not move to %s (now %s)' % (target, focus))
            signal_used = alert_style_swap(before, after, target, safe)
            self.rec.note(label + ': focus appearance swapped', signal_used)
        except KeySendError:
            raise
        except (ValueError, TypeError, KeyError, IndexError, RuntimeError, OSError) as exc:
            if self.use_click():
                raise RuntimeError(label + ': click proof failed: ' + str(exc)) from exc
            self.alert_decline_reason = str(exc)
            self.rec.note(label + ': automatic press declined: ' + str(exc), 'MANUAL fallback')
            return False
        self.press('OK')
        self.rec.note(label + ': automatic ENTER sent on verified ' + target, path)
        return True

    def press(self, *keys):
        result = None
        for k in keys:
            self.rec.say('input keys: ' + str(k))
            key = str(k).upper()
            key = 'ENTER' if key == 'OK' else key
            if self.a.input == 'beanjar':
                sent = key
                location = self.host.remote_target(self.vm)
                result = self.host.remote_press(self.vm, sent)
            elif self.a.input == 'qmp':
                sent = {'DOWN': 'down', 'UP': 'up', 'LEFT': 'left', 'RIGHT': 'right',
                        'ENTER': 'ret', 'HOME': 'meta_l', 'BACK': 'esc',
                        **{str(n): str(n) for n in range(10)}}[key]
                location = self.host.name
                result = self.host.sendkey(self.vm, sent)
            else:
                sent = {'HOME': 'LEFTMETA', 'BACK': 'ESC'}.get(key, key)
                location = self.dev.root
                result = self.dev.key(sent)
            rc, out = result
            if rc:
                error = 'key %s (%s) on %s failed (rc=%s): %s' % (
                    k, sent, location, rc, out.strip())
                self.rec.check('input delivery', False, error)
                raise KeySendError(error)
            timing.sleep(0.8)
        return result

    def ui(self):
        v = self.insp.evaluate(FOCUS_JS) if self.insp else None
        try:
            return json.loads(v) if v else None
        except ValueError:
            return None

    def wait(self, cond, timeout, every=3):
        end = time.time() + timing.seconds(timeout)
        v = None
        while time.time() < end:
            v = cond()
            if v:
                return v
            timing.sleep(every)
        return v

    def probe_keys(self, rows, ui):
        """One probe: a key and its inverse; True when the first moved the list focus (keys reach our page)."""
        cur = rows.index(ui['row']) if ui.get('row') in rows else 0
        k1, k2 = ('DOWN', 'UP') if cur + 1 < len(rows) else ('UP', 'DOWN')
        self.press(k1)
        moved = (self.ui() or {}).get('row')
        self.press(k2)
        return moved != ui.get('row')

    def ensure_key_focus(self, rows):
        """Remote keys must reach Home Chooser.  A system popup (seen on w22o-ponytail-us 10.3.1 right
        after every boot: the OLED "screen restoration" popup of com.webos.app.factorywin, buttons
        Start Now (focused by default) and Cancel; BACK does not dismiss it) takes key focus and
        the list never moves.  The runner does NOT press OK on a system popup (Start Now restarts
        the TV for 5 minutes): it records the evidence (a probe key, the oledepl condition, a
        screenshot) and waits up to --wait-popup seconds for someone to dismiss it by hand, then
        fails the run."""
        r = self.rec
        self.wake_screensaver('keys')
        end = time.time() + timing.seconds(self.a.wait_popup)
        first = True
        while True:
            ui = self.ui()
            if ui is None:
                raise RuntimeError('our page is not readable; not pressing a probe key')
            if self.probe_keys(rows, ui):
                if not first:
                    r.say('key focus restored: the popup was dismissed')
                return True
            known = self.dismiss_known_popup('keys blocked', rows) if first else None
            if known is True:
                r.say('key focus restored: the known LG crash popup was dismissed')
                return True
            if known is False:
                raise RuntimeError('keys still blocked after dismissing the known LG crash popup; no more keys')
            if first and self.dismiss_audiooutputd_popup('keys blocked'):
                if self.probe_keys(rows, self.ui() or ui):  # retry the blocked step once
                    r.say('key focus restored: the audiooutputd crash dialog was dismissed with NO (%s)' % AUDIOOUTPUTD_REF)
                    return True
                r.check('remote keys reach Home Chooser after dismissing the audiooutputd crash dialog', False,
                        'focus did not move after a probe key; %s' % AUDIOOUTPUTD_REF)
                raise RuntimeError('keys still blocked after dismissing the audiooutputd crash dialog; no more keys')
            if first:
                fg = self.dev.foreground()
                saver = SCREENSAVER_ID in self.running_ids()
                epl = self.dev.luna('luna://com.webos.service.oledepl/isPixelRefresherCondition')
                self.shot('keys-blocked')
                r.say('keys do not reach Home Chooser (focus stayed on %s); foreground %s; oledepl '
                      'isPixelRefresherCondition %s; screensaver %s; no popup was identified (see the '
                      'keys-blocked screenshot); waiting up to %ds for the keys to work'
                      % (ui.get('row'), fg[1], epl[:160], 'running' if saver else 'not running', self.a.wait_popup))
                first = False
            if time.time() >= end:
                break
            timing.sleep(10)
        r.check('remote keys reach Home Chooser (no system popup holding key focus)', False,
                'focus did not move after a probe key; see the keys-blocked screenshot')
        raise RuntimeError('keys blocked: a probe key did not move the focus (see the keys-blocked evidence); not pressing OK')
