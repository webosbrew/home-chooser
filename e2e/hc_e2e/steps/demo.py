# SPDX-License-Identifier: Apache-2.0
"""Phase demo (3d): OK on the demo launcher opens its focused app."""
import json

from .. import timing
from ..constants import APP_ID
from ..launchers import DEMO_TARGET_ID, DEMO_TARGET_TITLE, demo_focus, demo_order


class DemoSteps:
    """Phase demo (3d): OK on the demo launcher opens its focused app."""

    DEMO_SETTLE = 8  # s after the Home press for the demo's getInfo, listApps and first render

    def step_demo(self):
        """fixture:web.demo, after step 3 opened it with Home: OK starts the focused app.  The focused app is
        the first in the demo's order (demo_order), computed here from listApps as the demo; the launch is
        proved from sam, as for the launcher itself (launch_proof)."""
        r, d = self.rec, self.dev
        r.begin('3d the demo launcher opens the focused app')
        reply, raw = self.api('listApps')
        target = demo_focus(reply, self.launcher)
        if not r.check('3d: listApps as the demo names a first app in its order', bool(target), raw[:400]):
            return
        order = [a['id'] for a in demo_order(reply['apps'], self.launcher)]
        r.note('3d: the demo\'s order (first 8 of %d); focused: %s' % (len(order), target), ', '.join(order[:8]))
        listed = [a for a in reply['apps'] if isinstance(a, dict) and a.get('id') == DEMO_TARGET_ID]
        if not r.check('3d: the demo focuses %s, the runner\'s app %r, first in its order'
                       % (DEMO_TARGET_ID, DEMO_TARGET_TITLE), target == DEMO_TARGET_ID,
                       'focused %s; %s in listApps: %s' % (target, DEMO_TARGET_ID, json.dumps(listed)[:300])):
            return
        fg = d.foreground()
        r.note('3d: foreground before OK: %s (the demo is full-screen and was opened by the Home press)' % fg[0], fg[1][:300])
        if target in self.running_ids():
            d.close(target)
            timing.sleep(3)
        pre = target in self.running_ids()
        r.say('3d: %s running before OK: %s%s' % (target, pre, ' (close did not stop it)' if pre else ''))
        # Opened by step 3's Home press: the demo restarted, so its focus is on the first app.
        timing.sleep(self.DEMO_SETTLE)
        popup = self.foreign_popup('3d-demo-ok', dismiss=True)
        if popup:
            r.check('3d: no foreign LG fault popup before OK', False, popup)
            return
        self.life_begin()
        mark = self.messages_mark()
        try:
            key_result = self.press('OK')
            r.say('3d: OK key-send result: %r; log mark %s' % (key_result, mark))
            timing.sleep(4)
            self.launch_proof('3d-demo-ok', target, mark)
            fg = d.foreground()
            r.note('3d: foreground after OK: %s' % fg[0], fg[1][:300])
            self.shot('3d-demo-ok-verdict')
        finally:
            self.life_end()
            if target not in (self.launcher, APP_ID):
                d.close(target)
