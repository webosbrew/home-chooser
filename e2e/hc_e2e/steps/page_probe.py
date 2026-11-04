# SPDX-License-Identifier: Apache-2.0
"""--api-check: the launcher page holds listRunning, getForegroundApp and getAppLifeEvents subscriptions (the web
fixture's probe.js, the page's own bus permissions) while the runner launches and closes the disposable app; the
runner reads what the page recorded through the web inspector.  The verdict is hc_e2e.page_probe."""
import json

from .. import page_probe
from ..api_spec import DISPOSABLE_ID
from ..inspector import Inspector


class PageProbeSteps:
    def page_probe_step(self, label):
        """Only for the web fixtures of the --api-check build (they carry probe.js); another launcher's page
        records nothing, so the check is left out there."""
        r, d = self.rec, self.dev
        if not (self.L.is_fixture and self.L.id.startswith('org.webosbrew.hcfixture.web.')):
            r.note('%s: launcher page subscriptions not checked (only the web fixture carries the probe page)' % label, '')
            return
        own = self.insp is None
        insp = self.insp or Inspector(d)

        def read():
            return page_probe.parse_probe(insp.evaluate(page_probe.PROBE_JS, self.launcher))
        try:
            rc, out = d.launch(self.launcher)
            r.check('%s: the launcher page is opened for its subscriptions' % label, rc == 0, out.strip()[-300:])
            ready = self.wait(lambda: (lambda x: x if x and all(page_probe.acked(x, m) for m in page_probe.METHODS)
                                       else None)(read()), 40, 2)
            r.check('%s: the launcher page holds listRunning, getForegroundApp and getAppLifeEvents subscriptions, '
                    'each acknowledged (subscribed: true)' % label, bool(ready),
                    json.dumps(ready or read() or insp.last_error)[:900])
            if not ready:
                return
            d.launch(DISPOSABLE_ID)
            self.wait(lambda: DISPOSABLE_ID in self.running_ids(), 30, 2)
            # give the foreground time to follow before the close (a launch closed at once is not 'in front')
            self.wait(lambda: any(x.get('appId') == DISPOSABLE_ID for x in page_probe.replies_of(read(), 'getForegroundApp')), 15, 1)
            d.close(DISPOSABLE_ID)
            self.wait(lambda: DISPOSABLE_ID not in self.running_ids(), 20, 2)
            data = self.wait(lambda: (lambda x: x if x and not page_probe.probe_problems(x, DISPOSABLE_ID) else None)(read()), 25, 2)
            data = data or read()
            problems = page_probe.probe_problems(data, DISPOSABLE_ID)
            r.check('%s: the page received the launch and the close of %s on all three subscriptions' % (label, DISPOSABLE_ID),
                    not problems, '%s; %s\n%s' % ('; '.join(problems) or 'ok', page_probe.probe_summary(data, DISPOSABLE_ID),
                                                   json.dumps(data)[:1500]))
        finally:
            d.close(DISPOSABLE_ID)
            if own:
                insp.close()
