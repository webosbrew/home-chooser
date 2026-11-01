# SPDX-License-Identifier: Apache-2.0
"""The Home press and its non-visual launch proof (sam lines, life events)."""
import re
import shlex

from ..constants import FULLHOME, STOCK
from ..evidence import home_input_command, messages_command


class HomePress:
    """The Home press and its non-visual launch proof (sam lines, life events)."""

    def home_press(self, expect, label, from_app=True):
        """Press Home from an ordinary app.  The stock home must be in the foreground afterwards;
        a launcher counts when it is running (it was closed first) or in the foreground: a
        overlay launcher bar is an overlay window, so the app underneath stays "foreground"."""
        r = self.rec
        proof = expect == self.launcher
        if proof:
            self.popup_mark = self.popup_mark_now()
            self.life_begin()
        if expect == STOCK and (self.major == 5 or self.stock_id == FULLHOME):
            # webOS 5: Home starts com.webos.app.fullhome as an overlay over the app in front
            # (foreground stays the app; seen in the w20l-jhericurl run), so it is observed like
            # a launcher: closed first, running afterwards.  6.x to 9.x with fullhome installed: the
            # record names it, so it is what opens (not seen on a device yet).
            expect = FULLHOME
        if expect != STOCK:
            self.dev.close(expect)
            pre = not self.wait_closed(expect, 3)
            r.say('%s: %s running before the press: %s%s' % (label, expect, pre,
                  ' (close did not stop it: this check does not discriminate)' if pre else ''))
        if from_app:
            self.front_app()
        if proof or self.uses_home_key_hook():
            popup = self.foreign_popup(label, dismiss=True)
            if popup:
                r.check('%s: no foreign LG fault popup before the Home-key proof' % label, False, popup)
                raise RuntimeError('foreign LG fault popup detected; not sending keys')
        mark = self.messages_mark()
        key_result = self.press('HOME')
        r.say('%s: Home key-send result: %r; log mark %s' % (label, key_result, mark))
        sam = []

        def seen():
            fg = self.dev.foreground()
            if fg[0] == expect or (expect != STOCK and expect in self.running_ids()):
                return fg
            if expect == STOCK:
                # getForegroundAppInfo keeps naming the app beneath on 12.x although the stock home
                # is on screen: sam's own record of the Home key is the signal there.
                sam[:] = self.sam_home_lines(mark)
                if sam:
                    return fg
            return None
        fg = self.wait(seen, 26, 2)  # polled from the press on (was a fixed 6 s, then 20 s of polls)
        if not fg:
            rc, evidence = self.dev.sh(home_input_command(mark))
            r.note(label + ': first Home attempt produced no action; no retry',
                   'key-send %r; messages/journal since %s (rc=%s):\n%s' % (key_result, mark, rc, evidence))
            self.shot(label + '-first-press')
        ok = bool(fg)
        fg = fg or self.dev.foreground()
        self.shot(label + '-verdict')
        if proof:
            self.launch_proof(label, expect, mark)
        via = 'foreground' if fg[0] == expect else ('sam log' if sam else 'none')
        r.check('%s: Home opens %s (foreground, sam homeKey launch of the stock home in the log, or for a '
                'launcher: running after being closed first)' % (label, expect), ok,
                'signal: %s; getForegroundAppInfo: %s; running: %s%s' % (
                    via, fg[1], self.running_ids(),
                    ('; sam lines:\n' + '\n'.join(sam[:6])) if sam and via == 'sam log' else ''))
        return ok

    # -- the non-visual launch proof (e2e/README.md "Launch proof"): no screenshot, no on-screen label
    LIFE_LOG = '/tmp/hc-e2e-life.log'
    LIFE_PID = '/tmp/hc-e2e-life.pid'
    LAUNCH_OK = re.compile(r'NL_APP_LAUNCH_BEGIN|APP_LAUNCHED|RelaunchAsCommon')
    LAUNCH_BAD = re.compile(r'APP_LAUNCH_ERR|APP_LAUNCH_WARNING|LAUNCH_FAILED')

    def life_begin(self):
        """Hold a subscription to applicationManager/getAppLifeEvents on the device across the Home press."""
        self.dev.sh('kill $(cat %s) 2>/dev/null; rm -f %s %s; '
                    'nohup sh -c \'luna-send -i luna://com.webos.applicationManager/getAppLifeEvents '
                    '"{\\"subscribe\\":true}" > %s 2>&1 < /dev/null & echo $! > %s\' > /dev/null 2>&1; sleep 1'
                    % (self.LIFE_PID, self.LIFE_LOG, self.LIFE_PID, self.LIFE_LOG, self.LIFE_PID))

    def life_events(self, appid):
        """(events naming appid, subscription held) from the subscription started by life_begin()."""
        rc, out = self.dev.sh('cat %s 2>/dev/null' % self.LIFE_LOG)
        ev = []
        for line in out.splitlines():
            if appid in line:
                m = re.search(r'"event"\s*:\s*"([^"]*)"', line)
                ev.append((m.group(1) if m else '?', line.strip()))
        return ev, bool(out.strip())

    def life_end(self):
        self.dev.sh('kill $(cat %s) 2>/dev/null; rm -f %s %s; true' % (self.LIFE_PID, self.LIFE_LOG, self.LIFE_PID))

    def sam_launch_lines(self, appid, mark):
        """sam lines in /var/log/messages stamped at or after `mark` that name appid and are launch-related:
        NL_APP_LAUNCH_BEGIN {"app_id":"<id>",...}, APP_LAUNCHED, RelaunchAsCommon (and the _ERR/_WARNING variants)."""
        rc, out = self.dev.sh((messages_command() + " | awk '$1 >= \"%s\"' | grep -F %s | grep -E ' sam ' | tail -n 40")
                              % (mark, shlex.quote(appid)))
        return [l for l in out.splitlines() if self.LAUNCH_OK.search(l) or self.LAUNCH_BAD.search(l)]

    def launch_proof(self, label, appid, mark):
        """After a Home press that should have started or relaunched `appid`: sam's own record of it
        (a launch line in the log, or a launch/relaunch/foreground event on getAppLifeEvents) and the app
        in applicationManager/running with a live pid.  Works for every app type; reads no screen."""
        r = self.rec
        got = {}

        def check():
            lines = self.sam_launch_lines(appid, mark)
            ok_lines = [l for l in lines if self.LAUNCH_OK.search(l)]
            ev, held = self.life_events(appid)
            got.update(lines=lines, ok_lines=ok_lines, ev=ev, held=held)
            return ok_lines or [e for e in ev if e[0] in ('launch', 'relaunch', 'foreground', 'splash')]
        self.wait(check, 20, 2)
        running = appid in self.running_ids()
        lines, ok_lines, ev, held = (got.get(k, []) for k in ('lines', 'ok_lines', 'ev', 'held'))
        self.life_end()
        proved = bool(ok_lines) or any(e[0] in ('launch', 'relaunch', 'foreground', 'splash') for e in ev)
        kinds = sorted({e[0] for e in ev})
        relaunch = any(e[0] == 'relaunch' for e in ev) or any('RelaunchAsCommon' in l for l in ok_lines)
        r.check('%s: sam records the launch of %s (log line since %s or a getAppLifeEvents event) and it is running afterwards'
                % (label, appid, mark), proved and running,
                'sam log: %d launch line(s); getAppLifeEvents: %s (subscription %s); relaunch seen: %s; running: %s\n%s'
                % (len(ok_lines), kinds or 'no events', 'received data' if held else 'silent', relaunch, running,
                   '\n'.join((lines + [e[1] for e in ev])[-8:]) or 'no sam line or event naming the id'))

    def messages_mark(self):
        """UTC time on the device as sam stamps its lines (2026-10-23T05:48:12), taken before a press.
        A time, not a line count, because /var/log/messages can rotate between the two reads."""
        rc, out = self.dev.sh('date -u +%Y-%m-%dT%H:%M:%S')
        return out.strip().splitlines()[-1] if out.strip() else ''

    def sam_home_lines(self, mark, pattern=None):
        """Lines in /var/log/messages stamped at or after `mark` that show the Home key reaching the
        stock home: surface-manager's launchDefaultApp {"category":"home",...homeKey}, or sam's
        relaunch of com.webos.app.home."""
        pattern = pattern or ('launchDefaultApp.*homeKey|homeKey.*launchDefaultApp|'
                              'RelaunchAsCommon.*com.webos.app.home')
        rc, out = self.dev.sh((messages_command() + " | awk '$1 >= \"%s\"' | grep -E '%s' | tail -n 8")
                              % (mark, pattern))
        return [l for l in out.splitlines() if l.strip()]
