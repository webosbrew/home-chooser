# SPDX-License-Identifier: Apache-2.0
"""Phases home-proof (step 3) and window-home."""
from ..constants import STOCK
from ..releases import closes_stock_home


class HomeProofSteps:
    """Phases home-proof (step 3) and window-home."""

    def step3(self):
        """Per runtime: Home from an ordinary app opens the launcher, with sam's launch proof."""
        self.rec.begin('3 Home opens the launcher')
        # Record the current foreground without sending a key. A Home-key proof follows only after
        # the usual foreign-popup check in home_press().
        fg0 = self.dev.foreground()
        self.rec.note('foreground before the Home-key proof: %s' % fg0[0], fg0[1][:300])
        if self.planned('window-home') and not self.is_owner() and STOCK not in self.running_ids():
            # A later launcher on the same boot: the close rule needs a running LG Home, as the boot of a
            # fresh run leaves it; start it, then the press comes from an ordinary app as always.
            rc, out = self.dev.launch(STOCK)
            running = self.wait(lambda: STOCK in self.running_ids(), 30, 2)
            self.rec.note('LG Home started for the close rule (it is not running on this boot any more): running=%s'
                          % bool(running), 'rc=%s %s' % (rc, out.strip()[-200:]))
        self.home_press(self.launcher, '3-home')
        self.residency('after the Home press')
        r, d = self.rec, self.dev
        rc, out = d.sh('ps -eo args | grep -c "[%s]"' % self.launcher[:1] + self.launcher[1:])
        r.note('processes mentioning the launcher id', out.strip())

    def window_home(self):
        """Per window type, 10+: the watcher closes LG Home once the chosen launcher is in front."""
        if not closes_stock_home(self.major):
            return
        def deferred_close():
            return 'closeByAppId %s after %s in front' % (STOCK, self.launcher) in self.dev.sh(
                'cat /tmp/homechooser-home.log 2>&1')[1]
        closed = self.wait(deferred_close, 60, 3)
        self.rec.check('watcher closed LG Home after the chosen launcher was in front', bool(closed),
                       self.dev.sh('tail -n 12 /tmp/homechooser-home.log')[1])
        self.wait(lambda: STOCK not in self.running_ids(), 30, 3)
        self.rec.check('LG Home has no live process after the launcher was in front',
                       STOCK not in self.running_ids(), repr(self.running_ids()))
