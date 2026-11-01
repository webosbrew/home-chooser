# SPDX-License-Identifier: Apache-2.0
"""The stock home's preload policy and the home gate."""
import json
import time

from .. import timing
from ..checks import preload_settled
from ..constants import GATE_LINE, GATE_TARGET, PRELOAD_SAMPLE, PRELOAD_SETTLE, SKIP_LINE, STOCK
from ..evidence import lines_after, messages_command
from ..releases import closes_stock_home, uses_home_gate


class PreloadPolicy:
    """The stock home's preload policy and the home gate."""

    # -- the stock home's preload policy
    def preload_log(self):
        return self.dev.sh("grep -E 'setPreloadPolicy|closeByAppId|%s' /tmp/homechooser-home.log 2>&1 | tail -n 6" % SKIP_LINE)[1].strip()

    def gate_mount_count(self):
        rc, out = self.dev.sh("awk '$2 == \"%s\" {n++} END {print n + 0}' /proc/mounts" % GATE_TARGET)
        try:
            return int(out.split()[-1])
        except (ValueError, IndexError):
            return 0

    def check_home_gate(self, label):
        """6.x to 9.x, launcher chosen: the gate is bound over stock Home's binary, the watcher logged the
        mount, and stock Home is not running.  A Home the boot started before the mount is closed by the
        watcher's retry (every 10 s, up to 12 times), so the wait for it is bounded."""
        r = self.rec
        # The watcher trims its log to the last 200 lines once it passes 64 KB, so a line can leave the file
        # while the run goes on (the file is read whole; it is the file that lost the line). Look as early
        # as possible and remember the first sightings.
        # Who logs the mount is a race: the app starts the watcher and then runs `homekey refresh`, and
        # whichever of the two reaches the gate first mounts it.  When the refresh wins, it logs to syslog
        # only, and the watcher finds the gate already mounted (HC_GATE_UNCHANGED, no line) and writes just
        # the later close / launch lines.  So the evidence that the watcher manages the gate is the mount
        # line or any other 'home gate:' line; the mount itself is checked on /proc/mounts below.
        seen = {}

        def log_seen():
            self.gate_log_sightings(self.log_lines(), seen)
            return bool(seen.get('mounted') or seen.get('managed'))
        log_seen()
        count = self.gate_mount_count()
        r.check(label + ': the home gate is mounted over ' + GATE_TARGET, count >= 1,
                '%d mount(s) of %s in /proc/mounts' % (count, GATE_TARGET))
        gone = self.wait(lambda: STOCK not in self.running_ids(), 150, 5)
        r.check(label + ': stock Home (the real binary) is not running', bool(gone), repr(self.running_ids()))
        self.wait(log_seen, 60, 5)
        ok, detail = self.gate_log_verdict(seen)
        if not ok:
            lines = self.log_lines()
            detail += '; log now %d lines, first: %s' % (len(lines), lines[0] if lines else 'empty')
        r.check(label + ': watcher log shows the watcher manages the gate (the %r line or any "home gate:" line)'
                % GATE_LINE, ok, detail)
        r.note(label + ': home gate lines in the watcher log',
               '\n'.join(l for l in self.log_lines() if 'home gate' in l)[-1500:])

    @staticmethod
    def gate_log_sightings(lines, seen):
        """Record in `seen` the first mount line ('mounted') and the first other 'home gate:' line
        ('managed') of the watcher log lines; earlier sightings are kept."""
        for l in lines:
            if GATE_LINE in l:
                seen.setdefault('mounted', l)
            elif 'home gate:' in l:
                seen.setdefault('managed', l)

    @staticmethod
    def gate_log_verdict(seen):
        """(ok, detail) of the watcher-log check: the mount line, else any other 'home gate:' line."""
        if seen.get('mounted'):
            return True, seen['mounted']
        if seen.get('managed'):
            return True, 'no %r line (the gate was mounted before this watcher looked); managed: %s' % (
                GATE_LINE, seen['managed'])
        return False, 'no line containing %r or "home gate:"' % GATE_LINE

    def mark_log(self):
        """Remember how long the watcher log is now: /tmp/homechooser-home.log accumulates across the
        fixtures of one boot, so a check about one switch must only read the lines after this mark."""
        self.log_mark = len(self.log_lines())
        return self.log_mark

    def log_since_mark(self):
        return lines_after(self.log_lines(), getattr(self, 'log_mark', 0))

    def stock_running_samples(self, seconds=PRELOAD_SAMPLE, every=5):
        """Sample `running` for the stock home: list of (seconds, running)."""
        t0, out = time.time(), []
        while True:
            out.append((int(time.time() - t0), STOCK in self.running_ids()))
            if time.time() - t0 + every * timing.SLOWDOWN > seconds * timing.SLOWDOWN:
                break
            timing.sleep(every)
        return out

    def check_preload_off(self, label, settle=PRELOAD_SETTLE):
        """A third-party home is chosen: the watcher disabled the stock home's preload. On 10.x and later
        the close waits for a safe launcher foreground (once per boot and choice; a Home the boot started
        is closed then, so a bounded wait precedes the sampling). 6.x to 9.x: the home gate. 5.x: the
        watcher leaves the policy alone (settings-app.md), so it must not read false; the stock home is
        only observed (com.webos.app.home is not running on 5.x)."""
        r = self.rec
        enabled, raw = self.dev.preload_policy()
        if self.major <= 5:
            r.check(label + ': 5.x, the stock home policy is left alone (not disabled)', enabled is not False,
                    'isEnabled=%r; %s' % (enabled, raw))
            r.note(label + ': watcher log (5.x: no setPreloadPolicy expected)', self.preload_log())
            return
        ok = self.wait(lambda: self.dev.preload_policy()[0] is False, 60, 5)
        enabled, raw = self.dev.preload_policy()
        r.check(label + ': getPreloadPolicy shows isEnabled:false for ' + STOCK, enabled is False,
                'isEnabled=%r; %s' % (enabled, raw))
        r.note(label + ': watcher log', self.preload_log())
        if closes_stock_home(self.major) and (label == 'after the switch' or self.L.overlay):
            r.note(label + ': stock Home may still run until the launcher has a safe foreground',
                   'stock running=%s; foreground=%s' % (STOCK in self.running_ids(), self.dev.foreground()[0]))
            return
        if uses_home_gate(self.major):
            # The fixed settle and the 60 s sample that came before check_home_gate decided nothing (the
            # gate check makes the verdict): poll what it asserts, within the same 120 s bound, then check.
            r.say('%s: polling up to %d s for the gate and no stock home, then the gate checks'
                  % (label, settle + PRELOAD_SAMPLE))
            t0 = time.time()
            self.wait(lambda: preload_settled(self.major, self.gate_mount_count(), STOCK in self.running_ids(), False),
                      settle + PRELOAD_SAMPLE, 5)
            r.say('%s: settled after %d s' % (label, time.time() - t0))
            self.check_home_gate(label)
            return
        # 10+: a boot-started LG Home is closed by the watcher after its own settle; its close line ends
        # the settle early (same bound), then the stock home must stay away for the whole sample
        r.say('%s: settling up to %d s (until the watcher logs its close), then sampling %s for %d s'
              % (label, settle, STOCK, PRELOAD_SAMPLE))
        self.wait(lambda: preload_settled(self.major, 0, False, 'closeByAppId' in self.preload_log()), settle, 5)
        if closes_stock_home(self.major):
            # 10.x starts LG Home at boot; the watcher closes it once the launcher is in front
            self.wait(lambda: STOCK not in self.running_ids(), 150, 5)
        samples = self.stock_running_samples()
        ok = not any(run_ for _, run_ in samples)
        r.check('%s: %s is not running over %d s' % (label, STOCK, PRELOAD_SAMPLE), ok,
                'running at t=%s s' % [t for t, run_ in samples if run_] if not ok else 'never running in %d samples' % len(samples))
        if not ok:
            self.residency(label + ': stock home came back')
            r.note(label + ': whole watcher log', self.dev.sh('cat /tmp/homechooser-home.log 2>&1 | tail -n 40')[1])
            r.note(label + ': sam lines about the stock home', self.dev.sh(
                messages_command() + " | grep -E 'com.webos.app.home' | grep -E 'close|Close|launch|Launch|NL_APP' | tail -n 15")[1])

    def check_preload_on(self, label, wait_for=120):
        """The policy is back to enabled (launcher uninstalled, choice stock, Home Chooser uninstalled)."""
        r = self.rec
        if self.major <= 5:
            enabled, raw = self.dev.preload_policy()
            r.check(label + ': 5.x, the stock home policy was never touched', enabled is not False,
                    'isEnabled=%r; %s' % (enabled, raw))
            return
        self.wait(lambda: self.dev.preload_policy()[0] is True, wait_for, 5)
        enabled, raw = self.dev.preload_policy()
        r.check(label + ': getPreloadPolicy shows isEnabled:true for ' + STOCK, enabled is True,
                'isEnabled=%r; %s' % (enabled, raw))
        r.note(label + ': watcher log', self.preload_log())

    def front_app(self, appid='org.webosbrew.hbchannel'):
        """An ordinary app in front, as docs T4 presses Home from.  (With the stock home in front
        the key is passed to the home view and nothing is launched: see step 3.)"""
        self.dev.launch(appid)
        fg = self.wait(lambda: (lambda x: x if x[0] == appid else None)(self.dev.foreground()), 30, 2)
        self.rec.say('front app %s: %s' % (appid, fg))
        timing.sleep(3)

    def wait_closed(self, appid, timeout):
        """After a close: poll until appid has no live process, at most `timeout` s (the fixed wait it
        replaces).  True when it is gone."""
        return bool(self.wait(lambda: appid not in self.running_ids(), timeout, 1))

    def running_ids(self):
        """Ids in applicationManager/running whose process is alive.  On 12.x sam keeps a ghost entry
        (pid gone from /proc) for a force-closed flutter app, so an entry with a dead pid is dropped."""
        try:
            rows = json.loads(self.dev.luna('luna://com.webos.applicationManager/running'))['running']
        except (ValueError, KeyError, TypeError):
            return []
        pids = [x.get('processid') for x in rows if x.get('processid')]
        alive = set(pids)
        if pids:
            rc, out = self.dev.sh('for p in %s; do test -d /proc/$p && echo $p; done' % ' '.join(
                p for p in pids if str(p).isdigit()))
            alive = set(out.split())
        return [x['id'] for x in rows if not x.get('processid') or x['processid'] in alive]
