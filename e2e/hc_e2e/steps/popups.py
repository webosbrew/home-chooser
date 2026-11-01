# SPDX-License-Identifier: Apache-2.0
"""Foreign popups and our notifications: the popup mark, known crashes, alerts and toasts."""
import json
import re

from .. import timing
from ..alerts import alert_focus
from ..constants import REMOVAL_ALERT, UNINSTALL_TOAST
from ..evidence import (AUDIOOUTPUTD_CRASHD_RE, AUDIOOUTPUTD_REF, KNOWN_CRASH_DOC, POPUP_LINE_RE,
    audiooutputd_dialog, audiooutputd_fault, classify_faults, known_fault_pids, messages_command, split_known_crashes)


class PopupsAndNotifications:
    """Foreign popups and our notifications: the popup mark, known crashes, alerts and toasts."""

    def popup_mark_now(self):
        """The mark foreign_popup reads from: (messages time, seconds since boot), taken when a step begins."""
        rc, out = self.dev.sh('cut -d" " -f1 /proc/uptime')
        try:
            up = float(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            up = None
        return self.messages_mark(), up

    def dismiss_known_popup(self, label, rows=None):
        """The "[crash] Fault Occured" popup of the known legacy-broadcast-dvb crash (YES and NO do the same
        there) may be dismissed with BACK, at most once per fault (keyed by pid and faultTime).  Only a fault
        entry that is that known crash qualifies.  getFaultDetectedStatus lists every fault of the boot, so it
        cannot show the popup is gone; the outcome checked is that key focus is back: with our page open
        (`rows`, or a readable page) a probe key moves the list focus; without one, the foreground app is
        still the one from before BACK.  Returns None when no unhandled known fault is pending, else
        whether focus is back.  The caller sends no further key when it is False."""
        done = self.__dict__.setdefault('dismissed_known', set())
        uri = 'luna://com.webos.service.faultmanager/getFaultDetectedStatus'
        pending = {(p, str(f.get('faultTime'))) for p, f in known_fault_pids(self.priv_luna(uri)).items()}
        pending -= done
        if not pending:
            return None
        self.rec.note('%s: known LG crash popup (see %s); dismissing it with BACK, once' % (label, KNOWN_CRASH_DOC),
                      'pid/faultTime %s' % ', '.join('%s/%s' % p for p in sorted(pending)))
        done |= pending
        fg0 = self.dev.foreground()[0]
        self.press('BACK')
        timing.sleep(2)
        ui = self.ui() if self.insp else None
        if ui and ui.get('row') and (rows or ui.get('rows')):
            ok = self.probe_keys(rows or ui['rows'], ui)
            how = 'a probe key moved the list focus' if ok else 'a probe key did not move the list focus'
        else:
            fg = self.dev.foreground()[0]
            ok = bool(fg0) and fg == fg0
            how = 'foreground %s before BACK, %s after' % (fg0, fg)
        self.rec.check('%s: key focus is back after dismissing the known LG crash popup' % label, ok, how)
        return ok

    def dismiss_audiooutputd_popup(self, label):
        """Keys are blocked: read the screen (beanjar elements OCR).  Only the LG "[crash] Fault Occured" dialog
        that names audiooutputd (known on W22O 33.31.61/68, beanjar#183) is dismissed, by NO: a click on the
        element "NO", or in keys mode a move of the focus to NO verified on screen before OK.  YES is never
        pressed.  Returns True when the dialog was dismissed and the screen no longer shows it, None when the
        screen shows anything else (nothing is pressed; the caller stops as before), and raises when the dialog
        could not be dismissed safely."""
        r = self.rec
        try:
            frame = self.elements(label + ' (system popup?)')
        except Exception as exc:
            r.say('%s: screen not readable, no popup handling: %r' % (label, exc))
            return None
        message, text = audiooutputd_dialog(frame)
        if message is None:
            if '[crash] Fault Occured' in text and 'audiooutputd' in text:
                raise RuntimeError('audiooutputd crash dialog text is not a single element; not pressing keys')
            return None
        before = self.shot('audiooutputd-crash-dialog-before')
        r.note('%s: LG audiooutputd crash dialog (%s, stock LG behaviour); dismissing it with NO' % (label, AUDIOOUTPUTD_REF),
               'OCR text: %s' % text)
        if self.use_click():
            self.click_element('NO', 'button', label=label + ' NO', frame=frame, read_after=False)
        else:
            try:
                focus, direction = alert_focus(frame, message, 'NO', 'YES', require_focus=True)
            except ValueError as exc:
                raise RuntimeError('audiooutputd crash dialog: focus not provable (%s); not pressing keys' % exc) from exc
            if focus != 'NO':
                self.press(direction)
                frame = self.elements(label + ' focus on NO')
                if audiooutputd_dialog(frame)[0] is None:
                    raise RuntimeError('audiooutputd crash dialog not readable after moving focus; not pressing OK')
                try:
                    focus, _ = alert_focus(frame, message, 'NO', 'YES', require_focus=True)
                except ValueError as exc:
                    raise RuntimeError('audiooutputd crash dialog: focus not provable (%s); not pressing OK' % exc) from exc
                if focus != 'NO':
                    raise RuntimeError('focus did not move to NO (now %s); not pressing OK' % focus)
            self.press('OK')
        timing.sleep(2)
        after = self.shot('audiooutputd-crash-dialog-after')
        gone = self.elements(label + ' after NO')
        still, after_text = audiooutputd_dialog(gone)
        if still is not None or '[crash] Fault Occured' in after_text:
            r.check('audiooutputd crash dialog is gone after NO', False, 'OCR text after: %s' % after_text)
            raise RuntimeError('audiooutputd crash dialog still on screen after NO; no more keys')
        r.outcome('audiooutputd crash dialog dismissed with NO, %s' % AUDIOOUTPUTD_REF, 'RECOVERED',
                  json.dumps({'ocr_before': text, 'ocr_after': after_text, 'screenshot_before': before,
                              'screenshot_after': after, 'reference': AUDIOOUTPUTD_REF}))
        return True

    def foreign_popup(self, label, dismiss=False):
        """Note (never press keys at) an LG system popup that may hold key focus.  No screen reading:
        faultmanager (9.2.1 rootfs: /usr/sbin/faultmanager) raises the "[crash] Fault Occured" popup through
        notificationmgr and answers luna://com.webos.service.faultmanager/getFaultDetectedStatus with
        detectedFaults; its crash lines (crashd CRASHED, faultmanager createNotiPopup) are in /var/log/messages.
        notificationmgr has no list-alerts method.  Only what appeared since the step's mark (popup_mark,
        set when the step began) counts: a crashd/faultmanager popup line since then, or a fault entry raised
        since then that is not keyCheck2 boot noise (classify_faults).  Noise is noted.  Returns the evidence
        of a real popup, or ''."""
        mark = getattr(self, 'popup_mark', None)
        if not mark:
            mark = self.popup_mark = self.popup_mark_now()
        t_mark, up_mark = mark
        reply = self.priv_luna('luna://com.webos.service.faultmanager/getFaultDetectedStatus')
        real, noise = classify_faults(reply, up_mark)
        try:
            all_faults = json.loads(reply).get('faultDetectedStatus', {}).get('detectedFaults', [])
        except (ValueError, TypeError, AttributeError):
            all_faults = real
        crashes = [f for f in all_faults if f.get('faultType') == 'crash']
        expected = [f for f in crashes if audiooutputd_fault(f)]
        crashes = [f for f in crashes if not audiooutputd_fault(f)]
        if expected:
            self.rec.note('%s: audiooutputd crash is an expected environment fault (%s, W22O 33.31.61/68), not counted'
                          % (label, AUDIOOUTPUTD_REF), ' | '.join(json.dumps(f) for f in expected))
        self.rec.check(label + ': no LG [crash] fault dialog or crash fault this boot', not crashes,
                       ' | '.join(json.dumps(f) for f in crashes) or 'none')
        real = [f for f in real if not audiooutputd_fault(f)]
        ev = ['getFaultDetectedStatus new fault: %s' % json.dumps(f) for f in real]
        rc, out = self.dev.sh((messages_command() + " | awk '$1 >= \"%s\"' | grep -E '%s' | tail -n 4") % (t_mark, POPUP_LINE_RE))
        lines = [l for l in out.splitlines() if l.strip()]
        known_lines = split_known_crashes(lines, reply)[0]
        ev += [l for l in lines if l not in known_lines and not AUDIOOUTPUTD_CRASHD_RE.search(l)]
        if noise:
            self.rec.note('%s: fault entries not counted (keyCheck2 boot noise or raised before the step, uptime at mark %s)'
                          % (label, up_mark), ' | '.join(json.dumps(f) for f in noise))
        if ev:
            self.rec.note('%s: foreign popup present: %s' % (label, ' | '.join(ev)),
                          'an LG system popup may hold key focus; no key is pressed at it')
            self.shot(label + '-foreign-popup')
        return ' | '.join(ev)

    def notif_wait(self, method, before, timeout, every=3):
        """Wait until the log has more `notification <method>:` lines than `before`."""
        return self.wait(lambda: len(self.notif_lines(method)) > before, timeout, every)

    def check_alert(self, before):
        """After the chosen launcher was removed: the watcher logged one createAlert call and its reply.
        A reply that is not returnValue true is a FAIL with the fallback recorded, never a silent pass.
        With an accepted alert: screenshot, close it with notification/closeAlert (no key press),
        screenshot again."""
        r, d = self.rec, self.dev
        got = self.notif_wait('createAlert', before, 90)
        # a refusal is retried by the watcher (up to 8 tries, 3 s apart): wait for an accepted reply
        self.wait(lambda: any('"returnValue":true' in l.replace(' ', '') for l in self.notif_lines('createAlert')[before:]), 45, 3)
        lines = self.notif_lines('createAlert')
        new = lines[before:]
        ok_ix = [i for i, l in enumerate(new) if '"returnValue":true' in l.replace(' ', '')]
        r.check('watcher sent a createAlert for the removal (refusals retried, at most one accepted, last)',
                bool(new) and len(new) <= 8 and (not ok_ix or ok_ix == [len(new) - 1]), '\n'.join(new) or 'no createAlert line in the log')
        if not new:
            return
        reply = new[-1]
        accepted = '"returnValue":true' in reply.replace(' ', '')
        if not accepted:
            # A refused alert is shown as a toast instead (decided: the toast is enough). 5.6.1 and 9.2.1
            # answered "Alert is blocked by [ system ]" on every try; 10.3.1 accepted the alert.
            refused = 'blocked' in reply or '"returnValue":false' in reply.replace(' ', '')
            # the fallback toast is retried too: wait for the latest createToast reply to be accepted
            self.wait(lambda: 'returnValue":true' in d.sh(
                "grep 'notification createToast:' /tmp/homechooser-home.log | tail -n 1")[1].replace(' ', ''), 30, 3)
            rc, fb = d.sh("grep -E 'alert was refused|notification createToast:' /tmp/homechooser-home.log | tail -n 3")
            toast_ok = refused and any('alert was refused' in l for l in fb.splitlines()) and \
                '"returnValue":true' in fb.strip().splitlines()[-1].replace(' ', '')
            self.shot('4-alert-refused')
            r.check('the user was told: the alert was refused, so the fallback toast was shown', toast_ok,
                    'alert reply: %s\nfallback:\n%s' % (reply, fb.strip()))
            return
        r.check('the user was told: the alert was accepted (watcher sends with -a com.webos.applicationManager)', True, 'reply: %s' % reply)
        timing.sleep(2)
        self.foreign_popup('4-alert')
        self.shot('4-alert')
        r.note('look at screenshot 4-alert: an alert "Home Chooser" with the message "%s" and one OK button '
               '(no restart is mentioned on any release)' % (REMOVAL_ALERT % self.L.title), '')
        fg0 = d.foreground()
        m = re.search(r'"alertId"\s*:\s*"([^"]+)"', reply)
        if m:
            # closed over luna, no key press: an LG popup may be on top and would take the key
            out = self.priv_luna('luna://com.webos.notification/closeAlert', '{"alertId":"%s"}' % m.group(1))
            r.check('closeAlert on our alert (-a com.webos.applicationManager) was accepted',
                    '"returnValue":true' in out.replace(' ', ''), 'alertId %s\nreply: %s' % (m.group(1), out))
        else:
            r.note('accepted createAlert reply carries no alertId: alert not closed', reply)
        timing.sleep(3)
        self.shot('4-alert-dismissed')
        r.note('alert closed by closeAlert; foreground before/after (the alert itself is not an app)',
               '%s -> %s' % (fg0[0], d.foreground()[0]))

    def check_toast(self, before):
        """After Home Chooser was removed: the watcher logged one createToast call and its reply; a
        screenshot is taken as soon as the call shows in the log (a toast is short-lived)."""
        r = self.rec
        self.notif_wait('createToast', before, 150, 1)
        self.shot('5-toast')
        self.wait(lambda: any('"returnValue":true' in l.replace(' ', '') for l in self.notif_lines('createToast')[before:]), 30, 3)
        lines = self.notif_lines('createToast')
        new = lines[before:]
        ok_ix = [i for i, l in enumerate(new) if '"returnValue":true' in l.replace(' ', '')]
        r.check('watcher sent a createToast for the uninstall (refusals retried, at most one accepted, last)',
                bool(new) and len(new) <= 8 and (not ok_ix or ok_ix == [len(new) - 1]), '\n'.join(new) or 'no createToast line in the log')
        if new:
            r.check('the notification service accepted the toast', '"returnValue":true' in new[-1].replace(' ', ''), new[-1])
        r.note('look at screenshot 5-toast: a toast "%s" (it may already be gone: the log reply is the evidence)'
               % UNINSTALL_TOAST, '')
