# SPDX-License-Identifier: Apache-2.0
"""Opening Home Chooser and reading its page: in front, reopen, focus, the dialog."""
import json
import time

from .. import timing
from ..checks import open_in_front, panel_front_verdict
from ..constants import APP_ID, CHOICE, FOREGROUND_SETTLE, PANEL_FRONT_WAIT, PANEL_HOLD
from ..evidence import messages_command
from ..inspector import Inspector, PAGE_JS
from ..launchers import qml_support
from ..plan import launcher_shape


class PanelSteps:
    """Opening Home Chooser and reading its page: in front, reopen, focus, the dialog."""

    def settle_foreground(self, label, timeout=FOREGROUND_SETTLE):
        """Before Home Chooser opens over an app the Home key just launched: poll until SAM names a
        foreground app (at most `timeout` s).  An overlay opened before that card has mapped is closed by
        surface-manager (windowPolicy) when the card maps: on the pi VMs the launcher's card mapped about
        8 s after the Home press, while getForegroundAppInfo answered appId "".  The foreground is logged."""
        t0 = time.time()
        fg = self.wait(lambda: self.dev.foreground()[0], timeout, 1)
        self.rec.say('%s: foreground before opening Home Chooser: %r after %.0f s%s' % (
            label, fg, time.time() - t0, '' if fg else ' (none named; opening anyway)'))
        return fg

    def open_panel(self, label, front_wait=PANEL_FRONT_WAIT, hold=PANEL_HOLD):
        """Launch Home Chooser and poll until it is in front and stays there for `hold` s
        (panel_front_verdict) before any DOM read; on a miss close it and launch once more
        (open_in_front).  Every launch rc and the verdict are logged and, on a miss, the running list and
        sam's lines about closing the app (page_gone_evidence).  True when in front."""
        self.settle_foreground(label)
        if not self.insp:
            self.insp = Inspector(self.dev)
        mark = self.messages_mark()

        def front():
            samples, t0 = [], time.time()
            while True:
                fg = self.dev.foreground()[0]
                page = self.insp._page() is not None if self.insp else False
                samples.append((time.time() - t0, fg, page))
                ok, why = panel_front_verdict(samples, hold)
                if ok is not None or time.time() - t0 >= timing.seconds(front_wait):
                    break
                timing.sleep(1)
            if not ok:
                why += '; running: %s; %s' % (self.running_ids(), self.page_gone_evidence(mark).replace('\n', ' | '))
            return bool(ok), why

        def close():
            self.dev.close(APP_ID)
            self.wait_closed(APP_ID, 5)
        ok, _ = open_in_front(lambda: self.dev.launch(APP_ID), close, front,
                              lambda t: self.rec.say('%s: %s' % (label, t)))
        return ok

    def qml_note(self):
        if launcher_shape(self.L)[0] == 'qml':
            sup, ev = qml_support(self.major)
            self.rec.note('qml support expectation for %d.x: %s' % (self.major, sup), ev)

    def reopen_panel(self, timeout):
        """Close and relaunch Home Chooser, then read the panel until the launcher row is listed (within
        `timeout`).  Records the rc and output of both ares-launch calls, and recreates the Inspector after
        a connection-type read failure (as see_toast does).  Returns a dict: again (last DOM, {} when none
        was read), dom (True when any read gave a DOM), seen (toasts), launch (notes), reads, failed,
        recreated."""
        info = {'again': {}, 'dom': False, 'seen': [], 'launch': [], 'reads': 0, 'failed': 0, 'recreated': 0}
        if not self.insp:
            self.insp = Inspector(self.dev)
        for label, fn in (('close', self.dev.close), ('launch', self.dev.launch)):
            rc, out = fn(APP_ID)
            info['launch'].append('ares-launch %s rc=%s %r' % (label, rc, (out or '').strip()[-200:]))
            if label == 'close':
                self.wait_closed(APP_ID, 3)
        end = time.time() + timing.seconds(timeout)
        while True:
            if self.insp is None:
                self.insp = Inspector(self.dev)
            try:
                u = self.ui()
            except Exception as exc:
                u = None
                self.insp.last_error = 'ui() raised %r' % (exc,)
            info['reads'] += 1
            if u is None:
                info['failed'] += 1
                err = getattr(self.insp, 'last_error', None)
                if err and self.CONNECTION_ERROR.search(str(err)) and time.time() < end:
                    self.close_insp()
                    self.insp = Inspector(self.dev)
                    info['recreated'] += 1
            else:
                info['dom'] = True
                info['again'] = u
                if u.get('ticked'):
                    info['ticked'] = u['ticked']
                if u.get('toast'):
                    info['seen'].append(u['toast'])
                if self.launcher in (u.get('rows') or []):
                    break
            if time.time() >= end:
                break
            timing.sleep(1)
        timing.sleep(1)
        last = self.ui()
        if last is not None:
            info['dom'] = True
            info['again'] = last
            if last.get('ticked'):
                info['ticked'] = last['ticked']
        return info

    @staticmethod
    def ticked_after_reopen(info):
        """The ticked row id from the DOM reads of reopen_panel (the last read that carried one); None when
        no read did."""
        return (info.get('again') or {}).get('ticked') or info.get('ticked')

    def reopen_evidence(self, info, ticked, ok):
        """Evidence text of the re-open check.  On a miss it adds what tells the causes apart: the launch
        results, the SAM foreground app and running list, the Inspector error and the failed-read count;
        'no DOM read' instead of a DOM when no read ever gave one."""
        dom = json.dumps(info['again']) if info['dom'] else 'no DOM read'
        text = 'ticked rows: %r; DOM: %s' % (ticked, dom)
        launch_failed = any(' rc=0 ' not in n for n in info['launch'])
        if ok and not launch_failed:
            return text
        try:
            fg = self.dev.foreground()[0]
        except Exception as exc:
            fg = 'unreadable (%r)' % (exc,)
        try:
            running = APP_ID in self.running_ids()
        except Exception as exc:
            running = 'unreadable (%r)' % (exc,)
        return '%s; %s%s; SAM foreground: %r; %s in SAM running: %s; Inspector last_error: %s; reads failed: %d of %d' \
            '; Inspector recreated: %d' % (
                text, '; '.join(info['launch']), ' (LAUNCH FAILED)' if launch_failed else '', fg, APP_ID, running,
                getattr(self.insp, 'last_error', None) if self.insp else 'no inspector', info['failed'], info['reads'],
                info['recreated'])

    def focus_button(self, label, first=True):
        """Move focus inside our dialog to the button `label` (buttons sit in a row: the confirm one
        first, LEFT; the cancel one last, RIGHT).  True when focus is on it.  No key is pressed once the
        page cannot be read (the app may have been closed: the next key would go to whatever is beneath)."""
        for k in ['LEFT', 'UP'] if first else ['RIGHT', 'DOWN']:
            ui = self.ui()
            if ui is None:
                return False
            if ui.get('text') == label:
                return True
            self.press(k)
        return (self.ui() or {}).get('text') == label

    def page_state(self):
        """The Home Chooser page's own focus/visibility/url, or None when it cannot be read."""
        v = self.insp.evaluate(PAGE_JS) if self.insp else None
        try:
            return json.loads(v) if v else None
        except ValueError:
            return None

    def confirm_home_choice(self, clicking):
        # A successful toast lasts six seconds. Capture it while checking the click's
        # effect: a fresh OCR frame can take longer than the toast's entire lifetime.
        # The DOM is read before and after the one device command (the choice file, not a
        # whole state(): through a remote Beanjar host each command costs seconds).
        toasts = []

        def seen():
            u = self.ui() or {}
            if u.get('toast') and u['toast'] not in toasts:
                toasts.append(u['toast'])

        def chosen():
            seen()
            rc, out = self.dev.sh('cat %s 2>&1' % CHOICE)
            seen()
            return out.strip() == self.launcher

        if clicking:
            self.click_element('Use as Home', effect=chosen, label='Use as Home', read_after=False)
        else:
            self.press('OK')
        self.wait(chosen, 90, 2)
        return toasts

    def ensure_page(self, label):
        """Before keys go to the list: our page must be readable.  surface-manager closes an overlay window
        (Home Chooser's appinfo defaultWindowType) after a stretch without key input (6.5.2: about 90 s
        after the launch with no key, about 120 s after the last key; a relaunch alone does not reset it;
        logged as getAppLifeStatus closing reason userTimeout).  If the app was closed, why is noted and it
        is opened once more; no key is pressed at whatever is beneath.  True when the list is there."""
        if self.ui() is not None:
            return True
        self.rec.note('%s: our page is gone before the keys; opening Home Chooser again' % label,
                      self.page_gone_evidence(getattr(self, 'launch_mark', '') or ''))
        self.launch_mark = self.messages_mark()
        self.dev.launch(APP_ID)
        return bool(self.wait(lambda: (lambda u: u if u and self.launcher in (u.get('rows') or []) else None)(self.ui()),
                              60, 1))

    def page_gone_evidence(self, mark):
        """Why our page went away since `mark`: the inspector's reason, whether sam still runs the app,
        and the sam/WAM lines about closing it (windowPolicy when a card is put under our overlay, e.g.
        the stock home relaunched as the last-input app after a close; userTimeout after a stretch with
        no input) and about the stock home being killed and relaunched."""
        why = getattr(self.insp, 'last_error', None) if self.insp else 'no inspector'
        running = APP_ID in self.running_ids()
        rc, out = self.dev.sh(
            (messages_command() + " | awk '$1 >= \"%s\"' | grep -E '%s.*(killApp|windowPolicy|userTimeout|\"event\":\"close\")"
            "|com.webos.app.home.*(kill_process|APP_CLOSED)|NL_APP_LAUNCH_BEGIN.*com.webos.app.home' | tail -n 10") % (mark, APP_ID))
        return 'inspector: %s; %s running: %s\n%s' % (why, APP_ID, running, out.strip() or 'no close/kill line since %s' % mark)

    def ours_in_front(self, dialog):
        """The Home Chooser inspector page shows its own ConfirmDialog by aria-label.

        On webOS 9 the app's dialog is rendered over the stock home, so the foreground app id can
        remain com.webos.app.home even while the Home Chooser screen and its focused dialog are
        visibly on screen. The inspector selects the page by Home Chooser's app id; use that page's
        dialog DOM as the ownership check, then verify directional focus movement before OK.
        """
        page = self.insp._page() if self.insp else None
        ui = self.ui() or {}
        ps = self.page_state() or {}
        title = ui.get('dialog') or ''
        matches = title == dialog if not dialog.endswith('*') else title.startswith(dialog[:-1])
        ok = bool(page and APP_ID in page.get('url', '') and APP_ID in (ps.get('href') or '') and matches
                  and ps.get('focus') is True and ps.get('visibility') == 'visible')
        return ok, 'inspector page %r; dialog %r; focus %r; document.hasFocus %r, visibility %r; foreground %s (on 9.x our ' \
                   'overlay leaves the card beneath as the foreground app)' % (
            page.get('url') if page else None, title, ui.get('text'), ps.get('focus'), ps.get('visibility'),
            self.dev.foreground()[0])

    def close_insp(self):
        if self.insp:
            self.insp.close()
            self.insp = None
