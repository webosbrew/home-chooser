# SPDX-License-Identifier: Apache-2.0
"""Pure verdicts shared by the steps: preload settled, Home Chooser in front."""
from .constants import APP_ID
from .releases import uses_home_gate


def preload_settled(major, gate_mounts, stock_running, close_logged):
    """The condition polled instead of the fixed preload settle before the stock-home checks.  6-9: the
    home gate is mounted and stock Home does not run (what check_home_gate then asserts).  10+: the
    watcher logged its close of the stock home (a boot-started Home is closed after its own settle); until
    then the settle bound applies, as the fixed wait did."""
    if uses_home_gate(major):
        return gate_mounts >= 1 and not stock_running
    return bool(close_logged)


def panel_in_front(foreground, page_listed):
    """Home Chooser counts as in front once SAM's getForegroundAppInfo names it or once its page is in the
    inspector's /json.  The foreground alone cannot say no: on 9.x it names the card beneath our overlay
    (seen: org.webosbrew.hcfixture.web.normal for 30 s with our list on screen and readable), or "" right
    after a web launch.  What makes an open fail is our page going away (panel_front_verdict)."""
    return foreground == APP_ID or bool(page_listed)


def panel_front_verdict(samples, hold):
    """Whether Home Chooser reached the front and stayed there.  samples: [(seconds since the launch,
    foreground, page listed)] in time order.  (True, why) once it was in front (panel_in_front) and still
    is `hold` s later; (False, why) when a sample after the first in-front one is not in front (it was
    closed under us: surface-manager closes an overlay by windowPolicy when a card maps beneath it);
    (None, why) while undecided."""
    first = None
    for t, fg, page in samples:
        if panel_in_front(fg, page):
            if first is None:
                first = t
            if t - first >= hold:
                return True, 'in front at %.0f s and still at %.0f s (foreground %r, page listed %s)' % (first, t, fg, page)
        elif first is not None:
            return False, 'in front at %.0f s, gone at %.0f s (foreground %r, page listed %s)' % (first, t, fg, page)
    if not samples:
        return None, 'no sample yet'
    t, fg, page = samples[-1]
    if first is None:
        return None, 'not in front after %.0f s (foreground %r, page listed %s)' % (t, fg, page)
    return None, 'in front at %.0f s, held %.0f of %.0f s' % (first, t - first, hold)


def open_in_front(launch, close, front, say, attempts=2):
    """Open an app and confirm it reached the front.  launch() -> (rc, output); front() -> (ok, evidence),
    a poll that returns once the app is in front or its wait is over; close() runs before a retry.  The rc
    of every launch is said; a miss is said, the app closed and the open retried, `attempts` launches at
    most.  Returns (ok, the lines said)."""
    notes = []

    def note(text):
        notes.append(text)
        say(text)
    for n in range(1, attempts + 1):
        rc, out = launch()
        note('open %d of %d: ares-launch rc=%s %r' % (n, attempts, rc, (out or '').strip()[-200:]))
        ok, evidence = front()
        if ok:
            note('open %d of %d: in front (%s)' % (n, attempts, evidence))
            return True, notes
        retry = n < attempts
        note('open %d of %d: NOT in front (%s)%s' % (n, attempts, evidence,
                                                     '; closing it and retrying the open once' if retry else ''))
        if retry:
            close()
    return False, notes
