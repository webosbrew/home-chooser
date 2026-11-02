# SPDX-License-Identifier: Apache-2.0
"""Phases choose (step 2), switch (2s) and window-chosen; the device texts."""
import json
import shlex

from .. import timing
from ..alerts import RebootAlertMonitor
from ..constants import (APPDIR, APP_ID, BOOT_DIR, BOOT_SCRIPT, CHOICE, DONE_TEXT, OLD_RESTART_TEXTS, OVERRIDE,
    REBOOT_ASKED_TEXT, REBOOT_PROMPT, REMOVAL_ALERT, RESTART_NOTE, RESTART_NOW, SCRIPTS_DIR, STOCK, SVC_ID, UNINSTALL_TOAST)
from ..evidence import PREVIOUS_ARMED, closes_under_panel, previous_closes, previous_dropped
from ..inspector import Inspector, MARKER_JS, PLANT_JS, TICK_JS
from ..launchers import DEMO_TARGET_ID, DEMO_TARGET_TITLE
from ..releases import device_text_forms, expects_reboot_dialog


class ChooseSteps:
    """Phases choose (step 2), switch (2s) and window-chosen; the device texts."""

    overlay_takes_pointer = False

    def step2(self):
        r, d = self.rec, self.dev
        r.begin('2 choose the launcher through the UI')
        self.mark_log()  # the watcher log accumulates over the fixtures of a boot: later checks read only what follows
        self.residency('before selecting')
        self.insp = Inspector(self.dev)
        d.launch(APP_ID)
        ui = self.wait(lambda: (lambda u: u if u and self.launcher in (u.get('rows') or []) else None)(self.ui()), 120)
        self.strat.verify(d, r)
        self.shot('app-open')
        timing.sleep(4)  # the result toast of a left marker is set just after the list appears
        first = self.ui() or {}
        r.check('first open after a fresh install: no error toast (a marker left by an earlier install is dropped)',
                not (first.get('toast') or '').startswith("Couldn't"), 'toast: %r' % first.get('toast'))
        if self.plan_options().get('api'):  # 2a, part of the choose phase
            self.api_before()
            r.begin('2 choose the launcher through the UI (continued)')
        # a marker as an earlier install's cut-short run leaves it (WAM keeps localStorage across an
        # uninstall; the choice was removed with it): the next open must drop it without a toast
        r.note('planted stale marker', self.insp.evaluate(PLANT_JS % self.launcher))
        d.close(APP_ID)
        self.wait_closed(APP_ID, 3)
        self.launch_mark = self.messages_mark()
        d.launch(APP_ID)
        toasts = []

        def listed_stale():
            u = self.ui()
            if u and u.get('toast'):
                toasts.append(u['toast'])
            return u if u and self.launcher in (u.get('rows') or []) else None
        self.wait(listed_stale, 120, every=1)
        timing.sleep(3)
        stale = self.ui() or {}
        if stale.get('toast'):
            toasts.append(stale['toast'])
        self.shot('stale-marker')
        r.check('a stale marker (nothing stored) is dropped: no toast at all', not toasts, 'toasts: %r' % toasts)
        r.check('the stale marker is cleared', self.insp.evaluate(MARKER_JS) in (None, '', 'null'),
                'marker: %r' % self.insp.evaluate(MARKER_JS))
        ui = stale if stale.get('rows') else ui
        r.check('the list shows the launcher row (data-app-id)', bool(ui), 'DOM: %s' % json.dumps(ui))
        if not ui:
            raise RuntimeError('UI never listed the launcher; cannot continue')
        self.choose_in_ui(ui, full=True)
        if not self.a.update_check:  # --update-check: step 1 installed the old build; checked in 3u
            self.check_device_texts()
        # open the panel again after the apply (a cut-short run left its marker): no error, the choice ticked
        info = self.reopen_panel(120)
        again, seen = info['again'], info['seen']
        r.note('toasts seen while re-opening', repr(seen + [again.get('toast')]))
        again['toast'] = again.get('toast') or next((t for t in seen if t.startswith("Couldn't")), None)
        self.shot('reopened')
        r.check('re-open after the apply: no error toast', not (again.get('toast') or '').startswith("Couldn't"),
                'toast: %r' % again.get('toast'))
        # The tick is read in the same evaluate as the DOM (FOCUS_JS 'ticked'): on 6.5.2 and 9.2.1 a normal
        # (full-screen) launcher takes the front and the Home Chooser page is gone from /json a moment after
        # the DOM read, so a separate TICK_JS evaluate got None ("no page").  Only when that read did not
        # carry the tick, a bounded retry of the separate evaluate.
        ticked = self.ticked_after_reopen(info)
        for _ in range(5):
            if ticked == self.launcher or not self.insp:
                break
            timing.sleep(2)
            ticked = self.insp.evaluate(TICK_JS)
        r.check('re-open after the apply: the chosen row is ticked', ticked == self.launcher,
                self.reopen_evidence(info, ticked, ticked == self.launcher))
        d.close(APP_ID)
        self.close_insp()

    def choose_in_ui(self, ui, full):
        """Home Chooser is open and lists the launcher (`ui`): select its row, confirm "Use as Home", and check
        what the choice writes.  full: the per-release extras of the first choice (11+ page check)."""
        r, d = self.rec, self.dev
        r.say('UI driven: initial focus on %s' % ui.get('row'))
        rows = ui['rows']
        if not self.ensure_page('2 before the list keys'):
            raise RuntimeError('Home Chooser is not on screen; not pressing keys')
        self.ensure_key_focus(rows)
        ui = self.ui() or ui
        clicking = self.use_click() and not self.overlay_takes_pointer
        if clicking:
            self.click_element(self.L.title, role=('text', 'button'), effect=lambda: bool((self.ui() or {}).get('dialog')),
                               label='launcher row', focused_row=self.launcher)
        if not clicking:
            # move focus to the launcher's row; a key only while our page is readable
            for _ in range(len(rows) + 2):
                ui = self.ui()
                if ui is None:
                    r.note('2: our page went away while moving the focus; no more keys',
                           self.page_gone_evidence(getattr(self, 'launch_mark', '') or ''))
                    ui = {}
                    break
                if ui.get('row') == self.launcher:
                    break
                cur, tgt = rows.index(ui['row']) if ui.get('row') in rows else 0, rows.index(self.launcher)
                self.press('DOWN' if tgt > cur else 'UP')
                r.say('focus move: was %s, now %s; foreground %s' % (ui.get('row'), (self.ui() or {}).get('row'),
                                                                     self.dev.foreground()[0]))
            ui = self.ui() or {}
            r.check('focus is on the launcher row', ui.get('row') == self.launcher, json.dumps(ui))
            self.shot('row-focused')
            if ui.get('row') != self.launcher or (self.ui() or {}).get('row') != self.launcher:
                raise RuntimeError('focus is not on the launcher row of our page; not pressing OK')
            self.press('OK')
        ui = self.wait(lambda: (lambda u: u if u and u.get('dialog') else None)(self.ui()), 15, 1) or {}
        r.check('the confirmation dialog opened ("Use <name> as Home?")',
                bool(ui.get('dialog')) and ui['dialog'].startswith('Use '), json.dumps(ui))
        r.say('UI driven: dialog "%s", focus "%s"' % (ui.get('dialog'), ui.get('text')))
        self.shot('dialog')
        if not clicking:
            if ui.get('dialog') and ui.get('text') != 'Use as Home':
                self.press('UP')
                ui = self.ui() or {}
            r.check('focus is on "Use as Home"', ui.get('text') == 'Use as Home', json.dumps(ui))
            if ui.get('text') != 'Use as Home':
                raise RuntimeError('focus is not on "Use as Home" in our dialog; not pressing OK')
        mounted_before = bool(d.mounts()) if self.uses_home_key_hook() else False
        comp0 = self.compositor_snapshot() if self.uses_home_key_hook() else None
        if self.uses_home_key_hook():
            r.note('patch mounted before choosing: %s (a restart prompt is %s)' % (
                mounted_before, 'expected' if expects_reboot_dialog(self.major, mounted_before) else 'not expected'), '')
        self.ask_mark = self.messages_mark()
        if expects_reboot_dialog(self.major, mounted_before):
            self.ask_monitor = RebootAlertMonitor(d)
            if not self.ask_monitor.start():
                r.note('notification bus monitor did not start; using PmLog evidence', '')
        toasts = self.confirm_home_choice(clicking)
        if self.major >= 11 and full:
            page = self.insp._page() if self.insp else None
            state = self.page_state() or {}
            r.check('Home Chooser page is still open right after confirming',
                    bool(page and APP_ID in page.get('url', '') and
                         state.get('visibility') == 'visible' and self.ui()),
                    'page=%r; state=%r' % (page.get('url') if page else None, state))
        if expects_reboot_dialog(self.major, mounted_before):
            self.reboot_after_choice(comp0)
            toast = None
        else:
            if not toasts:
                self.wait(lambda: (lambda u: (toasts.append(u['toast']) or True) if u.get('toast') else False)(self.ui() or {}), 30, 1)
            toast = next((t for t in toasts if t), None)
            self.shot('after-confirm')
            r.note('toasts after confirming', repr(toasts))
            r.check('the toast said "%s"' % DONE_TEXT, toast == DONE_TEXT, repr(toast) if toast == DONE_TEXT else
                    '%r; why our page went away since the click (none: it stayed): %s' % (
                        toast, self.page_gone_evidence(self.ask_mark)))
            # the watcher closes nothing while the panel is up; a previous launcher is closed later
            self.wait(lambda: any(PREVIOUS_ARMED in l for l in self.log_since_mark()), 60, 3)
            since = self.log_since_mark()
            r.check('the watcher held its closes while Home Chooser was on screen (previous-launcher hold)',
                    any(PREVIOUS_ARMED in l for l in since) and not closes_under_panel(since),
                    '\n'.join(l for l in since if 'previous launcher:' in l or 'closeByAppId' in l) or 'no previous-launcher line')
            if self.uses_home_key_hook():
                # the patch was mounted already (an earlier choice in this boot): a live switch
                self.check_no_compositor_restart('choosing with the patch mounted (a live switch)', comp0)
                st, raw = self.kf_status()
                r.check('homekey status: active=yes and reboot=no (nothing to ask)',
                        st.get('active') == 'yes' and st.get('reboot') == 'no', raw)
                self.wait(lambda: self.launcher in self.baked_id(), 60, 5)
                baked = self.baked_id()
                r.check('the watcher wrote the launcher id into the selected systemUi.js (refresh, no restart)',
                        self.launcher in baked, baked)
        self.wait(lambda: self.watcher_active() == 'active' and (not self.uses_home_key_hook() or self.hook_mounts_match()), 90)
        self.residency('right after "Use as Home" (settled)')
        s = self.state()
        r.note('state after choosing', json.dumps(s))
        r.check('choice file holds the launcher id', s['choice'] == self.launcher, repr(s['choice']))
        r.check('general/defaultApps record home is the launcher', s['record_home'] == self.launcher, s['record_raw'])
        r.check('watcher unit homechooser-home is active', s['watcher'] == 'active', s['watcher'])
        r.check('init.d entry points to the installed boot entry',
                s['initd'] == (BOOT_SCRIPT if self.uses_home_key_hook() else BOOT_DIR + '/homechooser'), s['initd'])
        if self.uses_home_key_hook():
            r.check('live key-filter is selected and QML mounts are absent (webOS 6-9%s)' % (', after the boot' if not mounted_before else ''),
                    self.hook_mounts_match(s['mounts']), 'mounts: %s' % s['mounts'])
            r.check('Home key hook flag file exists', s['flag'] == 'yes', s['flag'])
            self.check_hook_status('after selection' + (' and the boot' if not mounted_before else ''))
        else:
            r.check('no Home key hook on this release', not s['mounts'] and s['flag'] == 'no',
                    'mounts: %s flag: %s' % (s['mounts'], s['flag']))
        if self.uses_override():
            r.check('profile override exists (webOS 10+)', s['override'] == 'yes', s['override'])
            r.note('override', d.sh('cat %s' % OVERRIDE)[1])

    def window_chosen(self):
        """Per window type, after a choice: idleApp (10+) and what the empty screen shows: the stock home's
        preload policy, the home gate on 6-9."""
        if self.uses_override():
            self.check_idle_chosen('after choosing')
        self.check_preload_off('after the switch')

    def step_switch(self):
        """A later launcher of the run, on the same boot: install it, then choose it in Home Chooser's list
        over the launcher chosen now, as a user switches (e2e/README.md "Scopes")."""
        r, d = self.rec, self.dev
        r.begin('2s switch to the launcher through the UI (same boot)')
        self.popup_mark = self.popup_mark_now()
        self.qml_note()
        rc, out = d.install(self.L.ipk)
        r.check('install launcher %s' % self.launcher, rc == 0 and 'Installed package' in out, out.strip())
        r.note('launcher appinfo (from the ipk; overlay=%s)' % self.L.overlay, json.dumps(self.L.appinfo))
        if getattr(self.L, 'is_demo', False) and not d.app_installed(DEMO_TARGET_ID):
            rc, out = d.install(self.a.demo_target_ipk)
            r.check('fixture:web.demo: install the app step 3d opens, %s (%r)' % (DEMO_TARGET_ID, DEMO_TARGET_TITLE),
                    rc == 0 and 'Installed package' in out, out.strip())
        self.mark_log()
        self.residency('before switching')
        self.close_insp()
        self.insp = Inspector(self.dev)
        self.launch_mark = self.messages_mark()
        if APP_ID in self.running_ids():  # a stale panel of an earlier failed step
            r.say('2s: closing a stale Home Chooser before opening it')
            d.close(APP_ID)
            self.wait_closed(APP_ID, 10)
        front = self.open_panel('2s open')
        ui = self.wait(lambda: (lambda u: u if u and self.launcher in (u.get('rows') or []) else None)(self.ui()), 120)
        r.check('the list shows the launcher row (data-app-id)', bool(ui),
                'DOM: %s; Home Chooser in front: %s' % (json.dumps(ui), front))
        if not ui:
            raise RuntimeError('UI never listed the launcher; cannot continue')
        prev = d.sh('cat %s 2>&1' % CHOICE)[1].strip()
        # A native overlay launcher is a transparent full-screen window (Dock's pattern) that stays above
        # Home Chooser's panel: a click lands on it, and it takes the key focus too (the list then ignores
        # the keys).  The pointer is not used over it; a user reaches the panel with the remote's keys.
        self.overlay_takes_pointer = '.native.overlay' in prev
        if self.overlay_takes_pointer:
            r.note('the previous launcher %s is a native overlay window: choosing with the remote keys, '
                   'no click (it takes the pointer and the key focus)' % prev)
        try:
            self.choose_in_ui(ui, full=False)
        finally:
            self.overlay_takes_pointer = False
            d.close(APP_ID)
            self.wait_closed(APP_ID, 10)
            self.close_insp()
        self.check_previous_closed(prev)

    def check_previous_closed(self, prev):
        """After a switch: nothing closed while the panel was up (checked in choose_in_ui); once Home or a
        full-screen app is next in front, exactly one close of the previous launcher, or it closed by itself;
        never a close of the new launcher."""
        r = self.rec
        if not prev or prev == self.launcher or prev == STOCK:
            return
        self.home_press(self.launcher, '2s-home-after-switch')
        self.wait(lambda: previous_closes(self.log_since_mark(), prev) or previous_dropped(self.log_since_mark(), prev), 60, 3)
        since = self.log_since_mark()
        closes, dropped = previous_closes(since, prev), previous_dropped(since, prev)
        r.check('the previous launcher %s was closed once after the panel, or closed by itself' % prev,
                len(closes) + len(dropped) == 1 and len(closes) <= 1, '\n'.join(closes + dropped) or 'no previous-launcher line')
        r.check('the new launcher %s was never closed by the watcher' % self.launcher,
                not previous_closes(since, self.launcher), '\n'.join(previous_closes(since, self.launcher)) or 'none')

    def check_device_texts(self):
        """The installed build carries the texts of 144ef21/34a4ff9 and none of the old restart wording:
        the watcher (app copy and the running /tmp copy), the service binary, the app bundle."""
        r, d = self.rec, self.dev
        notify = [SCRIPTS_DIR + '/../homechooser-daemon', '/tmp/homechooser-runtime/homechooser-daemon']
        restore = notify
        svcdir = '/media/developer/apps/usr/palm/services/' + SVC_ID

        def has(text, paths):
            found = set()
            for form in device_text_forms(text):
                rc, out = d.sh('grep -rlF %s %s 2>/dev/null' % (shlex.quote(form), ' '.join(paths)))
                found.update(l for l in out.split() if l.startswith('/'))
            return sorted(found)
        for text, sources in ((REMOVAL_ALERT[2:], notify), (UNINSTALL_TOAST, restore)):
            got = has(text, sources)
            r.check('device texts: the watcher says "%s"' % text.strip(), len(got) == 2, 'found in %s' % got)
        old = [(t, has(t, [SCRIPTS_DIR + '/../homechooser-daemon', '/tmp/homechooser-runtime/homechooser-daemon', APPDIR])) for t in OLD_RESTART_TEXTS]
        r.check('device texts: no old restart wording in the watcher or the app', not any(g for _, g in old),
                '; '.join('%r in %s' % (t, g) for t, g in old if g) or 'none')
        for reason, text in sorted(REBOOT_PROMPT.items()):
            got = has(text, [svcdir])
            r.check('device texts: the service reboot prompt (%s) is "%s"' % (reason, text), bool(got), 'found in %s' % got)
        for text in list(RESTART_NOTE.values()) + [REBOOT_ASKED_TEXT, RESTART_NOW]:
            got = has(text, [APPDIR])
            r.check('app contains no reboot instruction: ' + text, not got, repr(got))
