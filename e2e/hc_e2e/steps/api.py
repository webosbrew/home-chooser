# SPDX-License-Identifier: Apache-2.0
"""The launcher API as a launcher calls it: before the choice (2a, in the choose phase) and with it chosen (3a)."""
import glob
import json
import os
import re
import shlex
import time
import urllib.request

from .. import timing
from ..alerts import RebootAlertMonitor, alert_service_proof
from ..api_spec import (API_GROUP, API_VERSION, DISPOSABLE_ID, api_refused, api_reply_problems, launcher_methods,
    spec_reply_problems)
from ..constants import APP_ID, SCRIPTS_DIR, STOCK, SVC_ID
from ..evidence import messages_command, parse_luna, service_log_command
from ..inspector import Inspector
from ..launchers import FIXTURE_DIR, FIXTURE_ID


class ApiSteps:
    """The launcher API before the choice (2a, called by choose) and api-selected (3a)."""

    # -- the launcher API (--api-check; docs/launcher-api.md)
    def api(self, method, payload=None, app='launcher'):
        """(parsed reply, raw) of a launcher-API call as `app` (default: the launcher under test; None: no
        application id, as a plain shell luna-send)."""
        app = self.launcher if app == 'launcher' else app
        rc, raw, rep = self.dev.luna_as(app, 'luna://%s/%s' % (SVC_ID, method), json.dumps(payload or {}))
        if getattr(self.a, 'api_check', False):
            self.check_spec_schema(method, payload, app, rep, raw)
        return rep, raw

    def check_spec_schema(self, method, payload, app, rep, raw):
        """Every reply Home Chooser gives during --api-check, against the spec's result schema."""
        problems = spec_reply_problems(method, rep)
        if problems is None:
            self.rec.note('%s %s as %s: not a Home Chooser reply (no apiVersion), outside the spec\'s schemas'
                          % (method, json.dumps(payload or {}), app), raw[:400])
            return
        self.rec.check('%s %s as %s: the reply validates against the result schema of api/launcher-v1.openrpc.json'
                       % (method, json.dumps(payload or {}), app), not problems,
                       '%s\n%s' % ('; '.join(problems) or 'valid', raw[:400]))

    def api_conforms(self, method, label, payload=None):
        rep, raw = self.api(method, payload)
        probs = api_reply_problems(method, rep)
        self.rec.check('%s: %s as %s has the documented fields and types' % (label, method, self.launcher), not probs,
                       '%s\n%s' % ('; '.join(probs) or 'conforms', raw[:800]))
        return rep or {}

    def api_kit_note(self):
        if self.L.is_fixture:
            return 'the web fixture built with requiredPermissions [%s]' % API_GROUP
        return 'an author launcher (%s, ipk %s): fixture-only parts are skipped' % (self.launcher, self.L.ipk)

    def api_new_methods(self, label):
        """Exercise the v1 refresh, foreground and icon calls with real installed apps."""
        r = self.rec
        def icon_result(info, field, reply):
            icon, folder = info.get(field), info.get('folderPath')
            if not icon or not folder:
                return (api_refused(reply) and reply.get('errorCode') == 'noIcon'
                        and reply.get('apiVersion') == API_VERSION), 'appinfo has no %s' % field
            path = os.path.join(folder, icon)
            _, presence = self.dev.sh('if test -f %s; then echo present; else echo missing; fi' % shlex.quote(path))
            if presence.strip().endswith('missing'):
                return (api_refused(reply) and reply.get('errorCode') == 'noIcon'
                        and reply.get('apiVersion') == API_VERSION), 'appinfo path is missing: %s' % path
            if not presence.strip().endswith('present'):
                return False, 'could not inspect appinfo path: %s (%s)' % (path, presence[:200])
            return not api_reply_problems('getAppIcon', reply), 'appinfo path exists: %s' % path
        start = time.monotonic()
        foreground, raw = self.api('getForegroundApp')
        elapsed = (time.monotonic() - start) * 1000
        r.check('%s: getForegroundApp contract' % label,
                not api_reply_problems('getForegroundApp', foreground), raw[:400])
        platform_id, platform_raw = self.dev.foreground()
        r.check('%s: getForegroundApp matches platform foreground' % label,
                (foreground or {}).get('appId') == platform_id,
                'service=%r platform=%r; platform reply=%s' % ((foreground or {}).get('appId'), platform_id, platform_raw[:400]))
        r.note('%s: getForegroundApp timing and reply size' % label,
               '%.1f ms; %d bytes; %s' % (elapsed, len(raw.encode()), raw[:400]))
        for appid in (STOCK, 'org.webosbrew.hbchannel', self.launcher):
            if not self.dev.app_installed(appid):
                continue
            app_info = None
            for method, payload in (('getAppInfo', {'id': appid}), ('getAppIcon', {'id': appid})):
                start = time.monotonic()
                reply, raw = self.api(method, payload)
                elapsed = (time.monotonic() - start) * 1000
                problems = api_reply_problems(method, reply)
                ok, evidence = icon_result(app_info or {}, 'icon', reply) if method == 'getAppIcon' else (not problems, '')
                r.check('%s: %s %s contract' % (label, method, appid), ok,
                        '; '.join(problems) if problems and not ok else
                        '%s; %d reply bytes' % (evidence, len(raw.encode())))
                r.note('%s: %s %s timing and size' % (label, method, appid),
                       '%.1f ms; reply=%d bytes; icon=%s bytes; %s' %
                       (elapsed, len(raw.encode()), (reply or {}).get('byteLength', '-'),
                        raw[:400] if method == 'getAppInfo' else repr((reply or {}).get('mimeType'))))
                if method == 'getAppInfo':
                    app_info = (reply or {}).get('app') or {}
            if app_info is not None:
                reply, raw = self.api('getAppIcon', {'id': appid, 'size': 'largeIcon'})
                if appid == self.launcher and self.L.is_fixture and app_info.get('largeIcon') == 'oversize.png':
                    r.check('%s: getAppIcon rejects the oversized fixture largeIcon' % label,
                            api_refused(reply) and reply.get('errorCode') == 'iconTooLarge'
                            and reply.get('apiVersion') == API_VERSION, raw[:400])
                elif app_info.get('largeIcon'):
                    ok, evidence = icon_result(app_info, 'largeIcon', reply)
                    r.check('%s: getAppIcon largeIcon %s contract' % (label, appid),
                            ok, '%s; icon=%s bytes; reply=%d bytes' %
                            (evidence, (reply or {}).get('byteLength', '-'), len(raw.encode())))
                else:
                    r.check('%s: getAppIcon largeIcon %s has no fallback' % (label, appid),
                            api_refused(reply) and reply.get('errorCode') == 'noIcon'
                            and reply.get('apiVersion') == API_VERSION, raw[:400])
        if self.major == 9 and label == 'before selection' and self.L.is_fixture:
            # This one-shot method has no target parameter: put each sampled app in front first.
            try:
                for appid in (STOCK, 'org.webosbrew.hbchannel', self.launcher):
                    self.probe_foreground(appid)
            finally:
                self.life_end()

    def probe_foreground(self, appid):
        """Put appid in front and compare getForegroundApp with the platform.  A card app is named by SAM's
        getForegroundAppInfo.  An overlay launcher is not: SAM keeps naming the card beneath while
        getAppLifeEvents emits `foreground` with _WEBOS_WINDOW_TYPE_OVERLAY, so for it the overlay is proven
        by that life event and the service must agree with SAM's reply at that moment (the card beneath)."""
        r = self.rec
        overlay = appid == self.launcher and self.L.overlay
        if overlay:
            self.life_begin()
        rc, out = self.dev.launch(appid)
        r.check('getForegroundApp probe launches %s' % appid, rc == 0, out.strip()[-400:])
        if overlay:
            def came_up():
                ev, _ = self.life_events(appid)
                return [line for kind, line in ev if kind == 'foreground' and '_WEBOS_WINDOW_TYPE_OVERLAY' in line]
            seen = self.wait(came_up, 20, 2)
            r.check('getForegroundApp probe sees overlay %s in front (rule: life-event foreground with '
                    '_WEBOS_WINDOW_TYPE_OVERLAY; SAM getForegroundAppInfo keeps the card beneath)' % appid,
                    bool(seen), (seen[-1] if seen else 'no overlay foreground event: %r' % (self.life_events(appid)[0][-4:],)))
        else:
            platform = self.wait(lambda: self.dev.foreground()[0] == appid, 20, 2)
            r.check('getForegroundApp probe sees %s in front' % appid, bool(platform), self.dev.foreground()[1][:400])
        start = time.monotonic()
        reply, raw = self.api('getForegroundApp')
        elapsed = (time.monotonic() - start) * 1000
        platform_id, platform_raw = self.dev.foreground()
        if overlay:
            rule = 'overlay rule: service appId must equal SAM getForegroundAppInfo now (%r), the card beneath' % platform_id
            ok = (reply or {}).get('appId') == platform_id
        else:
            rule = 'card rule: service appId must equal %r' % appid
            ok = (reply or {}).get('appId') == appid
        r.check('getForegroundApp %s contract and appId' % appid,
                not api_reply_problems('getForegroundApp', reply) and ok,
                '%s; SAM: %s; service: %s' % (rule, platform_raw[:200], raw[:400]))
        r.note('getForegroundApp %s timing and reply size' % appid,
               '%.1f ms; %d bytes; %s' % (elapsed, len(raw.encode()), raw[:400]))

    # -- --api-check modes.  smoke (the bare flag): reachability as a launcher calls the API, getInfo, one
    # read method and one Home-only method, each reply checked against the spec's schema; the host contract
    # test (service/test) owns the methods and errorCodes.  full (--api-check=full): the whole sweep below.
    def api_mode(self):
        return 'full' if getattr(self.a, 'api_check', None) == 'full' else 'smoke'

    def api_before(self):
        return self.api_before_selection() if self.api_mode() == 'full' else self.api_smoke_before()

    def api_selected(self):
        return self.step_api() if self.api_mode() == 'full' else self.api_smoke_selected()

    def api_smoke_before(self):
        """2a smoke: the launcher declares the opt-in, gets the grant (11+: the namespaced files) and reaches
        getInfo and listApps; then the request dialog."""
        r, d = self.rec, self.dev
        r.begin('2a launcher API smoke before the launcher is chosen (--api-check)')
        r.note('API caller: ' + self.api_kit_note(), '')
        ai = self.L.appinfo
        perm, home = API_GROUP in (ai.get('requiredPermissions') or []), 'home' in (ai.get('appCategory') or [])
        r.check('the launcher appinfo declares requiredPermissions ["%s"] and appCategory "home"' % API_GROUP, perm and home,
                'requiredPermissions %r; appCategory %r' % (ai.get('requiredPermissions'), ai.get('appCategory')))
        r.note('launcher access files (homechooser-access status; the app applies them when it loads)',
               d.exec('sh %s/homechooser-access status 2>&1' % SCRIPTS_DIR)[1].strip())
        # the app's accessApply runs while the list loads: allow it a moment (listApps needs the grant)
        self.wait(lambda: (self.api('listApps')[0] or {}).get('returnValue') is True, 30, 3)
        info = self.api_conforms('getInfo', 'before selection')
        r.check('before selection: getInfo says root true, permitted true, isSelectedHome false',
                info.get('root') is True and info.get('permitted') is True and info.get('isSelectedHome') is False, json.dumps(info))
        apps = self.api_conforms('listApps', 'before selection')
        ids = [a.get('id') for a in apps.get('apps') or [] if isinstance(a, dict)]
        r.check('listApps names the launcher', self.launcher in ids, '%d apps; launcher %s' % (len(ids), self.launcher in ids))
        self.api_request(selected=False)

    def api_smoke_selected(self):
        """3a smoke: as the selected Home, getInfo and one Home-only method, closeApp of the running
        disposable app; with --uninstall-via alert also the uninstallApp confirmation on LG's alert; then the
        request toast."""
        r, d = self.rec, self.dev
        r.begin('3a launcher API smoke with the launcher selected (--api-check)')
        comp0 = self.compositor_snapshot() if self.uses_home_key_hook() else None
        info = self.api_conforms('getInfo', 'selected')
        r.check('selected: getInfo says root true, permitted true, isSelectedHome true',
                info.get('root') is True and info.get('permitted') is True and info.get('isSelectedHome') is True, json.dumps(info))
        d.launch(DISPOSABLE_ID)
        running = self.wait(lambda: DISPOSABLE_ID in self.running_ids(), 30, 2)
        r.check('the disposable app is running before closeApp', bool(running), str(self.running_ids()))
        rep, raw = self.api('closeApp', {'id': DISPOSABLE_ID})
        r.check('closeApp as the selected Home accepts the disposable app',
                (rep or {}).get('returnValue') is True and rep.get('id') == DISPOSABLE_ID
                and not api_reply_problems('closeApp', rep), raw[:400])
        gone = self.wait(lambda: DISPOSABLE_ID not in self.running_ids(), 20, 2)
        r.check('the disposable app is not running after closeApp', bool(gone), str(self.running_ids()))
        if getattr(self.a, 'uninstall_via', 'later') == 'alert':
            self.api_uninstall_cancel()
            self.api_uninstall_confirm()
        self.api_request(selected=True)
        self.check_no_compositor_restart('the launcher API calls', comp0)

    def api_before_selection(self):
        """Home Chooser open and elevated, the launcher installed but not chosen."""
        r, d = self.rec, self.dev
        r.begin('2a launcher API before the launcher is chosen (--api-check)')
        r.note('API caller: ' + self.api_kit_note(), '')
        ai = self.L.appinfo
        perm, home = API_GROUP in (ai.get('requiredPermissions') or []), 'home' in (ai.get('appCategory') or [])
        r.check('the launcher appinfo declares requiredPermissions ["%s"] and appCategory "home"' % API_GROUP, perm and home,
                'requiredPermissions %r; appCategory %r' % (ai.get('requiredPermissions'), ai.get('appCategory')))
        r.note('launcher access files (homechooser-access status; the app applies them when it loads)',
               d.exec('sh %s/homechooser-access status 2>&1' % SCRIPTS_DIR)[1].strip())
        if self.major >= 11:
            _, raw = d.sh('for root in /var/luna-service2-dev /var/luna-service2; do '
                          'file="$root/api-permissions.d/org.webosbrew.homechooser.launcher.api.json"; '
                          'test ! -f "$file" || cat "$file"; done')
            files = []
            for line in raw.splitlines():
                try:
                    files.append(json.loads(line))
                except ValueError:
                    pass
            methods = {SVC_ID + '/' + name for name in launcher_methods()}
            writes = {SVC_ID + '/closeApp', SVC_ID + '/uninstallApp'}
            r.check('11+: application.query provides the reading methods, application.launcher closeApp and uninstallApp',
                    bool(files) and all(set(f.get('application.query', [])) == methods - writes
                                        and set(f.get('application.launcher', [])) == writes
                                        and 'default.permission' not in f for f in files), raw[:1200])
            r.check('11+: namespaced permission and ares.webos.cli grant every launcher method',
                    bool(files) and all(set(f.get(API_GROUP, [])) == methods
                                        and set(f.get('ares.webos.cli', [])) == methods
                                        and 'homechooser.applications' not in f for f in files), raw[:1200])
        # the app's accessApply runs while the list loads: allow it a moment (listApps needs the grant)
        self.wait(lambda: (self.api('listApps')[0] or {}).get('returnValue') is True, 30, 3)
        info = self.api_conforms('getInfo', 'before selection')
        r.check('before selection: getInfo says root true, permitted true, isSelectedHome false',
                info.get('root') is True and info.get('permitted') is True and info.get('isSelectedHome') is False, json.dumps(info))
        apps = self.api_conforms('listApps', 'before selection')
        ids = [a.get('id') for a in apps.get('apps') or [] if isinstance(a, dict)]
        r.check('listApps names the launcher and the disposable app', self.launcher in ids and DISPOSABLE_ID in ids,
                '%d apps; launcher %s, disposable %s' % (len(ids), self.launcher in ids, DISPOSABLE_ID in ids))
        self.api_conforms('listRunning', 'before selection')
        self.api_new_methods('before selection')
        rep, raw = self.api('getAppIcon', {'id': DISPOSABLE_ID, 'size': 'largeIcon'})
        r.check('getAppIcon of an app without largeIcon has no fallback',
                api_refused(rep) and rep.get('errorCode') == 'noIcon'
                and rep.get('apiVersion') == API_VERSION, raw[:400])
        # an installed app without the permission
        for m, payload in (('listApps', {}), ('listRunning', {}), ('getForegroundApp', {}),
                           ('getAppInfo', {'id': self.launcher}), ('getAppIcon', {'id': self.launcher}),
                           ('closeApp', {'id': APP_ID}), ('uninstallApp', {'id': self.launcher})):
            rep, raw = self.api(m, payload, app=DISPOSABLE_ID)
            expected = 'notHome' if m in ('closeApp', 'uninstallApp') else 'noPermission'
            r.check('%s as %s fails with %s (check order)' % (m, DISPOSABLE_ID, expected),
                    api_refused(rep) and rep.get('errorCode') == expected, raw[:400])
        rep, raw = self.api('getInfo', app=DISPOSABLE_ID)
        r.check('getInfo as the app without the permission: permitted false, or refused by the hub',
                api_refused(rep) or (rep or {}).get('permitted') is False, raw[:400])
        rep, raw = self.api('listApps', app=None)
        r.check('listApps without an application id (a plain root luna-send) is refused', api_refused(rep), raw[:400])
        for m in ('closeApp', 'uninstallApp'):
            rep, raw = self.api(m, {'id': DISPOSABLE_ID})
            r.check('%s as %s while it is not the selected Home: refused with notHome' % (m, self.launcher),
                    api_refused(rep) and rep.get('errorCode') == 'notHome', raw[:400])
        rep, raw = self.api('uninstallConfirmed', {'token': '0' * 32})
        r.check('screen-only uninstallConfirmed as the launcher is refused',
                api_refused(rep), raw[:400])
        self.api_request(selected=False)

    # `beanjar elements` OCR takes tens of seconds on the rk boards (matrix-20261031-082841/w20l-jhericurl-rk1:
    # one call ran ~50 s), the app hides the toast after 6 s, so one OCR call must never eat the window:
    # it gets a short subprocess timeout and runs only every OCR_EVERY-th round; the DOM read is the main
    # source.  The values are unscaled seconds (run() applies SLOWDOWN).
    TOAST_OCR_TIMEOUT = 4
    TOAST_OCR_EVERY = 3
    CONNECTION_ERROR = re.compile(r'reset|no page|websocket|exited|connection|refused|forward|no frame|raised', re.I)

    def see_toast(self, needle, timeout):
        """Look for a toast (the app hides it after 6 s) in two independent places: the DOM through the
        Inspector every round, and the screen through `beanjar elements` OCR every TOAST_OCR_EVERY-th round
        with a TOAST_OCR_TIMEOUT cap.  The Inspector is closed and recreated after every connection-type
        failure (reset, no page, websocket), as often as the overall deadline allows.
        Returns (text, source) with source 'DOM' or 'screen', or (None, <why not>) carrying the whole
        timeline (each round's source, elapsed seconds and error) when it never showed."""
        start = time.time()
        end = start + timing.seconds(timeout)
        timeline, rnd = [], 0

        def note(source, t0, error=None, extra=''):
            entry = '#%d %s %.1fs%s%s' % (rnd, source, time.time() - t0, ' err=%s' % str(error)[:160] if error else '',
                                          ' ' + extra if extra else '')
            timeline.append('t+%.1fs %s' % (t0 - start, entry))
            self.rec.say('toast check: ' + timeline[-1])

        while True:
            rnd += 1
            if self.insp is None:
                self.insp = Inspector(self.dev)
            t0 = time.time()
            try:
                ui = self.ui()
            except Exception as exc:
                ui = None
                self.insp.last_error = 'ui() raised %r' % (exc,)
            text = (ui or {}).get('toast')
            if text and needle in text:
                return text, 'DOM'
            err = getattr(self.insp, 'last_error', None)
            note('DOM', t0, err if ui is None else None, '' if ui is None else 'toast=%r' % text)
            if ui is None and err and self.CONNECTION_ERROR.search(str(err)) and time.time() < end:
                t0 = time.time()
                self.close_insp()
                self.insp = Inspector(self.dev)
                note('inspector-recreate', t0, err)
            remaining = end - time.time()
            if remaining > 1 and (rnd - 1) % self.TOAST_OCR_EVERY == 0:
                t0 = time.time()
                cap = min(self.TOAST_OCR_TIMEOUT, remaining / timing.SLOWDOWN)
                error, rows = None, []
                try:
                    rc, raw = self.host.display_beanjar('elements', self.host.remote_target(self.vm), '--json',
                                                        timeout=cap)
                    if rc:
                        error = 'elements rc=%s%s' % (rc, ' (cut at %.1fs)' % timing.seconds(cap) if rc == 124 else '')
                    else:
                        for row in json.loads(raw).get('elements', []):
                            t = row.get('text') or ''
                            if row.get('kind') == 'text':
                                rows.append(t)
                            if row.get('kind') == 'text' and needle.lower() in t.lower():
                                note('OCR', t0, extra='match %r box %s' % (t, row.get('box')))
                                return t, 'screen'
                except Exception as exc:
                    error = 'elements failed: %r' % (exc,)
                note('OCR', t0, error, '' if error else 'no match; rows: %s' % ' | '.join(rows)[:300])
            if time.time() >= end:
                return None, 'none (DOM last_error=%s; timeline: %s)' % (getattr(self.insp, 'last_error', None),
                                                                        '; '.join(timeline))
            timing.sleep(0.5)

    def api_request(self, selected):
        """The `request` launch parameter: Home Chooser opens "Use <title> as Home?" for the requesting
        launcher (cancelled with Back, on our own dialog); once it is Home, the "already your Home" toast."""
        r, d = self.rec, self.dev
        choice0 = self.state()['choice']
        if selected:
            # Warm relaunch: Home Chooser still runs from an earlier step (the manual alert-uninstall path
            # closes neither it nor the Inspector), so the old forward may be stale.  Replace the Inspector,
            # never the app: the warm relaunch is a real user path.
            self.close_insp()
        if not self.insp:
            self.insp = Inspector(self.dev)
        rc, out = d.launch(APP_ID, {'request': self.launcher})
        r.check('ares-launch %s -p {"request":"%s"}' % (APP_ID, self.launcher), rc == 0, out.strip())
        if selected:
            toast, source = self.see_toast('already your Home', 30)
            self.shot('3a-request-selected')
            r.check('request while it is Home: the toast "<title> is already your Home."', bool(toast),
                    'source=%s: %r' % (source, toast))
            d.close(APP_ID)
            self.close_insp()
            return
        ui = self.wait(lambda: (lambda u: u if (u.get('dialog') or '').startswith('Use ') else None)(self.ui() or {}), 60, 1) or {}
        self.shot('2a-request-dialog')
        want = 'Use %s as Home?' % self.L.title
        r.check('request: Home Chooser asks "%s"' % want, ui.get('dialog') == want, json.dumps(ui))
        ok, why = self.ours_in_front('Use *')
        if not ok:
            r.check('request: the dialog is ours and in front (else no key is pressed)', False, why)
            return
        self.press('BACK')
        gone = self.wait(lambda: not (self.ui() or {}).get('dialog'), 10, 1)
        r.check('request: Back cancels the dialog', bool(gone), json.dumps(self.ui()))
        timing.sleep(2)
        r.check('request cancelled: the choice is unchanged', self.state()['choice'] == choice0, repr(self.state()['choice']))

    def api_close_probe(self, appid, label):
        """Observe SAM, the public list, process liveness and WAM pages around closeApp."""
        r, d = self.rec, self.dev
        def snapshot(prior=None):
            sam = parse_luna(d.luna('luna://com.webos.applicationManager/running')) or {}
            api, _ = self.api('listRunning')
            sam_row = next((x for x in sam.get('running', []) if x.get('id') == appid), None)
            api_row = next((x for x in (api or {}).get('running', []) if x.get('id') == appid), None)
            pids = {}
            for field in ('processid', 'webprocessid'):
                pid = str((sam_row or {}).get(field) or (prior or {}).get('pids', {}).get(field, {}).get('pid') or '')
                if pid.isdigit():
                    pids[field] = {'pid': pid, 'alive': 'alive=0' in d.sh('test -d /proc/%s; echo alive=$?' % pid)[1]}
            if not self.insp:
                self.insp = Inspector(self.dev)
            try:
                with urllib.request.urlopen('http://127.0.0.1:%d/json' % self.insp.port, timeout=5) as response:
                    pages = [x.get('url') for x in json.loads(response.read().decode()) if appid in x.get('url', '')]
            except Exception as error:
                pages = ['inspector error: %s' % error]
            wam = d.sh("ps -eo pid,ppid,args | grep '[W]ebAppMgr' | head -4")[1].strip()
            return {'sam': sam_row, 'listRunning': api_row, 'pids': pids, 'wamPages': pages, 'WebAppMgr': wam}
        before = snapshot()
        r.note('%s before closeApp' % label, json.dumps(before))
        rep, raw = self.api('closeApp', {'id': appid})
        r.check('%s closeApp accepts the target' % label,
                (rep or {}).get('returnValue') is True and rep.get('id') == appid
                and not api_reply_problems('closeApp', rep), raw[:400])
        self.wait(lambda: appid not in self.running_ids(), 20, 2)
        for process in before['pids'].values():
            pid = process['pid']
            self.wait(lambda: 'alive=1' in d.sh('test -d /proc/%s; echo alive=$?' % pid)[1], 15, 1)
        after = snapshot(before)
        r.note('%s after closeApp' % label, json.dumps(after))
        r.check('%s absent from SAM running and listRunning after closeApp' % label,
                before['sam'] is not None and before['listRunning'] is not None
                and after['sam'] is None and after['listRunning'] is None, json.dumps(after))
        return before, after

    def step_api(self):
        """The launcher is the selected Home (after step 3; on 6-9 after the boot that mounted the patch)."""
        r, d = self.rec, self.dev
        r.begin('3a launcher API with the launcher selected (--api-check)')
        comp0 = self.compositor_snapshot() if self.uses_home_key_hook() else None
        info = self.api_conforms('getInfo', 'selected')
        r.check('selected: getInfo says root true, permitted true, isSelectedHome true',
                info.get('root') is True and info.get('permitted') is True and info.get('isSelectedHome') is True, json.dumps(info))
        self.api_conforms('listApps', 'selected')
        self.api_conforms('listRunning', 'selected')
        self.api_new_methods('selected')
        # closeApp closes a running web app; record whether WAM retains its page or process.
        d.launch(DISPOSABLE_ID)
        running = self.wait(lambda: DISPOSABLE_ID in self.running_ids(), 30, 2)
        r.check('the disposable app is running before closeApp', bool(running), str(self.running_ids()))
        self.api_close_probe(DISPOSABLE_ID, 'web disposable')
        # The native fixture is optional; use it where a package was built and remove it afterward.
        native = sorted(glob.glob(os.path.join(FIXTURE_DIR, FIXTURE_ID % ('native', 'normal') + '_*.ipk')))
        if native and self.major == 9 and not getattr(self.a, 'device', None):
            native_id = FIXTURE_ID % ('native', 'normal')
            try:
                rc, out = d.install(native[-1])
                r.check('install native closeApp probe', rc == 0, out.strip()[-400:])
                if rc == 0:
                    rc, out = d.launch(native_id)
                    r.check('launch native closeApp probe', rc == 0, out.strip()[-400:])
                    self.wait(lambda: native_id in self.running_ids(), 30, 2)
                    self.api_close_probe(native_id, 'native fixture')
            finally:
                rc, out = d.remove(native_id)
                r.check('remove native closeApp probe', rc == 0, out.strip()[-400:])
        rep, raw = self.api('closeApp', {})
        r.check('closeApp without an id: badRequest', api_refused(rep) and rep.get('errorCode') == 'badRequest', raw[:400])
        # the uninstall refusal list
        for target, why in ((APP_ID, 'Home Chooser itself'), ('org.webosbrew.hbchannel', 'Homebrew Channel'),
                            (self.launcher, 'the requesting launcher')):
            rep, raw = self.api('uninstallApp', {'id': target})
            r.check('uninstall of %s is refused (protected)' % why, api_refused(rep) and rep.get('errorCode') == 'protected',
                    raw[:400])
        if d.app_installed(STOCK):
            rep, raw = self.api('uninstallApp', {'id': STOCK})
            r.check('uninstallApp of LG Home is refused (notRemovable)',
                    api_refused(rep) and rep.get('errorCode') == 'notRemovable', raw[:400])
        rep, raw = self.api('uninstallApp', {'id': 'org.webosbrew.hcfixture.notinstalled'})
        r.check('uninstallApp of an absent app is notFound',
                api_refused(rep) and rep.get('errorCode') == 'notFound', raw[:400])
        for method in ('getAppInfo', 'getAppIcon'):
            rep, raw = self.api(method, {'id': 'org.webosbrew.hcfixture.notinstalled'})
            r.check('%s of an absent app is notFound' % method,
                    api_refused(rep) and rep.get('errorCode') == 'notFound', raw[:400])
            rep, raw = self.api(method, {'id': '../bad'})
            r.check('%s rejects malformed id' % method,
                    api_refused(rep) and rep.get('errorCode') == 'badRequest', raw[:400])
        rep, raw = self.api('getAppIcon', {'id': self.launcher, 'size': 'invalid'})
        r.check('getAppIcon rejects an unknown size',
                api_refused(rep) and rep.get('errorCode') == 'badRequest', raw[:400])
        self.api_uninstall_cancel()
        self.api_uninstall_confirm()
        self.api_request(selected=True)
        self.check_no_compositor_restart('the launcher API calls', comp0)

    def uninstall_ask(self, label):
        """uninstall {id: disposable} as the Home: (confirm route 'alert'|'screen'|None, reply)."""
        r = self.rec
        if getattr(self, 'uninstall_monitor', None):
            self.uninstall_monitor.close()
        self.uninstall_monitor = RebootAlertMonitor(self.dev)
        if not self.uninstall_monitor.start():
            r.note('uninstall notification monitor unavailable; service log proof still required', '')
            self.uninstall_monitor.close()
            self.uninstall_monitor = None
        self.uninstall_mark = self.messages_mark()
        rep, raw = self.api('uninstallApp', {'id': DISPOSABLE_ID})
        how = (rep or {}).get('confirmation')
        r.check('%s: uninstall {id:%s} replies pending with a confirm route' % (label, DISPOSABLE_ID),
                (rep or {}).get('returnValue') is True and rep.get('pending') is True and how in ('alert', 'screen')
                and not api_reply_problems('uninstallApp', rep), raw[:400])
        return how if (rep or {}).get('returnValue') is True else None

    def uninstall_alert_id(self):
        """The alertId of the service's createAlert, from its UNINSTALL_ASK log line (PmLog), if logged."""
        rc, out = self.dev.sh(service_log_command(self.uninstall_mark, 'UNINSTALL_ASK')
                              + " | tail -n 3")
        m = re.findall(r'\\?"alertId\\?"\s*:\s*\\?"([^"\\]+)', out)
        observed = self.uninstall_monitor.snapshot() if getattr(self, 'uninstall_monitor', None) else None
        return (m[-1] if m else (observed or {}).get('alert_id')), out.strip()

    def screen_dialog(self):
        if not self.insp:
            self.insp = Inspector(self.dev)
        return self.wait(lambda: (lambda u: u if (u.get('dialog') or '').startswith('Uninstall ') else None)(self.ui() or {}),
                         60, 1) or {}

    def dismiss_alert(self, label):
        """Our createAlert: closed with closeAlert when its id is in the log, else with Back (never OK)."""
        r = self.rec
        timing.sleep(2)
        self.foreign_popup(label)
        self.shot(label)
        aid, ev = self.uninstall_alert_id()
        if aid:
            out = self.priv_luna('luna://com.webos.notification/closeAlert', '{"alertId":"%s"}' % aid)
            r.check('%s: closeAlert on our "Uninstall ...?" alert was accepted' % label,
                    '"returnValue":true' in out.replace(' ', ''), 'alertId %s\nreply: %s' % (aid, out))
        else:
            r.note('%s: no alertId in the UNINSTALL_ASK log line; dismissing with Back' % label, ev or 'no UNINSTALL_ASK line')
            self.press('BACK')
        timing.sleep(3)
        self.shot(label + '-dismissed')

    def api_uninstall_cancel(self):
        r, d = self.rec, self.dev
        how = self.uninstall_ask('cancel')
        if how == 'screen':
            ui = self.screen_dialog()
            self.shot('3a-uninstall-screen')
            r.check('screen route: Home Chooser asks "Uninstall %s?"' % 'HC Fixture disposable',
                    ui.get('dialog') == 'Uninstall HC Fixture disposable?', json.dumps(ui))
            ok, why = self.ours_in_front('Uninstall *')
            if ok:
                self.press('BACK')
                r.check('screen route: Back cancels the dialog', bool(self.wait(lambda: not (self.ui() or {}).get('dialog'), 10, 1)),
                        json.dumps(self.ui()))
            else:
                r.check('screen route: the dialog is ours and in front (else no key is pressed)', False, why)
            d.close(APP_ID)
            self.close_insp()
        elif how == 'alert':
            self.dismiss_alert('3a-uninstall-alert')
        timing.sleep(5)
        r.check('after the cancel the disposable app is still installed', d.app_installed(DISPOSABLE_ID), DISPOSABLE_ID)

    def api_uninstall_confirm(self):
        """Confirm our owned alert and verify removal; keys retain the human fallback."""
        r, d = self.rec, self.dev
        how = self.uninstall_ask('confirmation')
        if how == 'alert':
            if self.a.uninstall_via == 'alert':
                self.shot('3a-uninstall-alert-confirm')
                aid, ev = self.uninstall_alert_id()
                r.note('uninstall alert awaiting a visually verified Uninstall press',
                       'alertId %s; %s' % (aid, ev))
                installed_before = set(d.installed())
                def service_proof():
                    _, raw = self.uninstall_alert_id()
                    observed = self.uninstall_monitor.snapshot() if getattr(self, 'uninstall_monitor', None) else None
                    return raw + '\nLS2: ' + json.dumps(observed), alert_service_proof(raw, 'Uninstall HC Fixture disposable?', 'Uninstall', 'Cancel',
                                                    DISPOSABLE_ID, self.launcher, observed=observed)
                def request_again():
                    if self.uninstall_ask('retry confirmation') != 'alert':
                        raise RuntimeError('uninstall retry did not raise an alert')
                    timing.sleep(3)
                automatic = self.alert_action('Uninstall HC Fixture disposable?', 'Uninstall', 'Cancel',
                                             '3a-uninstall-alert', service_proof,
                                             lambda: self.wait(lambda: not d.app_installed(DISPOSABLE_ID),
                                                               30 if self.use_click() else 240, 2),
                                             lambda: not any(e.get('text') == 'Uninstall HC Fixture disposable?'
                                                 for e in self.elements('uninstall-result')['elements']), request_again)
                if not automatic:
                    r.manual('press Uninstall on our alert', 'automatic press declined: ' + self.alert_decline_reason + '; waiting up to 240 seconds')
                gone = self.wait(lambda: not d.app_installed(DISPOSABLE_ID), 240, 2)
                if gone:
                    installed_after = set(d.installed())
                    r.check('alert Uninstall press%s removed only %s'
                            % (' (automatic press)' if automatic else '', DISPOSABLE_ID),
                            DISPOSABLE_ID in installed_before
                            and {APP_ID, self.launcher} <= installed_after
                            and installed_after == installed_before - {DISPOSABLE_ID},
                            'before %s; after %s' % (sorted(installed_before), sorted(installed_after)))
                else:
                    self.dismiss_alert('3a-uninstall-alert-timeout')
                    r.manual('uninstall alert confirmation timed out',
                             ('automatic press declined: ' + self.alert_decline_reason + '; ' if not automatic else '')
                             + 'no disposable removal in 240 seconds')
                d.close(APP_ID)
                self.close_insp()
                return
            self.dismiss_alert('3a-uninstall-alert-2')
            r.manual('uninstall confirm path on the alert route (Uninstall on the system alert -> LG remove)',
                     'the runner never presses OK on a system popup; '
                     'by hand: call uninstall as the launcher, press Uninstall, then check getAppInfo %s fails' % DISPOSABLE_ID)
            return
        if how != 'screen':
            return
        ui = self.screen_dialog()
        ok, why = self.ours_in_front('Uninstall *')
        r.check('confirm: the "Uninstall ...?" dialog is ours and in front', ok, why)
        if not ok:
            return
        if self.use_click():
            self.click_element('Uninstall', effect=lambda: not d.app_installed(DISPOSABLE_ID), label='screen Uninstall')
        else:
            ok = self.focus_button('Uninstall')
            r.check('confirm: focus is on "Uninstall"', ok, json.dumps(self.ui()))
            if not ok:
                return
            self.press('OK')
        gone = self.wait(lambda: not d.app_installed(DISPOSABLE_ID), 120, 5)
        self.shot('3a-uninstalled')
        r.check('confirm on our screen: uninstallConfirmed removed %s' % DISPOSABLE_ID, bool(gone),
                'installed: %s; service log: %s' % (d.app_installed(DISPOSABLE_ID), d.sh(
                    (messages_command() + " | awk '$1 >= \"%s\"' | grep -E 'UNINSTALL' | tail -n 4") % self.uninstall_mark)[1].strip()))
        d.close(APP_ID)
        self.close_insp()

    def api_after_reboot(self):
        """--reboot-check: the access grants and the elevation survive a boot."""
        r = self.rec
        self.wait(lambda: (self.api('getInfo')[0] or {}).get('returnValue') is True, 60, 5)
        info = self.api_conforms('getInfo', 'after the reboot')
        r.check('after the reboot: getInfo says root true, permitted true, isSelectedHome true',
                info.get('root') is True and info.get('permitted') is True and info.get('isSelectedHome') is True, json.dumps(info))
        self.api_conforms('listApps', 'after the reboot')
        self.api_conforms('listRunning', 'after the reboot')

    def api_after_uninstall(self):
        """Home Chooser is gone: the launcher's calls fail cleanly (a JSON refusal, in time) and luna is up."""
        r, d = self.rec, self.dev
        for m in ('getInfo', 'listApps'):
            t0 = time.time()
            rep, raw = self.api(m)
            r.check('after the uninstall: %s as %s fails cleanly (returnValue false, %.0f s)' % (m, self.launcher, time.time() - t0),
                    api_refused(rep) and '[timeout]' not in raw, raw[:400])
        fg, out = d.foreground()
        r.check('after the uninstall: luna still answers (getForegroundAppInfo)', fg is not None and '"returnValue":true' in out.replace(' ', ''),
                out[:300])
