# SPDX-License-Identifier: Apache-2.0
"""The Recorder: every check with its evidence, run.log and results.json; which labels get a screenshot."""
import json
import os
import re
import time

# Screenshots (e2e/README.md "System popups and screenshots"): the runner takes one only where a label
# matches a row here; any other label is logged as skipped.  (label pattern, when)
SCREENSHOTS = (
    (r'-verdict$', 'after each Home press verdict (and the demo OK)'),
    (r'-first-press$', 'a Home press that produced no action'),
    (r'^fail-', 'on a FAIL, once per step'),
    (r'^3r-after-reboot', 'at a reboot, after the boot checks'),
    (r'^final$', 'the final state, after the cleanup'),
    (r'-foreign-popup$|^keys-blocked$|^audiooutputd-crash-dialog-|-click-not-observed$', 'a fault or a system popup'),
    (r'^4-alert|^5-toast$|^2-reboot-alert|^3a-uninstall-alert', 'our alert or toast'),
    (r'-focus-proof$', 'the alert proof reads this picture (keys mode)'),
    (r'^S\d', 'the home and screensaver scenario'),
)


def screenshot_reason(label):
    """The SCREENSHOTS row a label matches, or None: not a key step, no picture."""
    return next((why for pattern, why in SCREENSHOTS if re.search(pattern, label)), None)


# ---------------------------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------------------------
class Recorder:
    def __init__(self, outdir):
        self.outdir = outdir
        self.results = []
        self.step = ''
        self.tag = ''
        self.scope = ''  # the scope of the phase running (PHASES), written with every result
        self.on_fail = None  # called with the check's name after a FAIL (the scenario's screenshot)
        self.listener = None  # called with (kind, text) for each line, step and result: the pytest live progress
        self.log = open(os.path.join(outdir, 'run.log'), 'w')

    def say(self, text):
        text = re.sub(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', '[redacted]', str(text))
        line = '%s %s' % (time.strftime('%H:%M:%S'), text)
        print(line, flush=True)
        self.log.write(line + '\n')
        self.log.flush()
        self.tell('say', text)

    def tell(self, kind, text):
        """The listener, if one is set: kind is 'say', 'begin', or a result status.  It never stops the run."""
        listener = getattr(self, 'listener', None)
        if listener:
            try:
                listener(kind, text)
            except Exception:
                pass

    def begin(self, step):
        self.step = (self.tag + ' ' if self.tag else '') + step
        self.say('== ' + self.step)
        self.tell('begin', self.step)

    def note(self, what, evidence=''):
        self.say('note: %s\n    %s' % (what, str(evidence).replace('\n', '\n    ')))

    def check(self, name, ok, evidence='', expected=None):
        """expected: text citing the documented limitation; a failing check then records XFAIL."""
        evidence = re.sub(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', '[redacted]', str(evidence))
        status = 'PASS' if ok else ('XFAIL' if expected else 'FAIL')
        self.results.append({'step': self.step, 'name': name, 'status': status,
                             'evidence': str(evidence), 'expected': expected, 'scope': getattr(self, 'scope', '')})
        self.say('%s  %s\n    %s%s' % (status, name, str(evidence).replace('\n', '\n    '),
                                      '\n    expected: ' + expected if expected and not ok else ''))
        self.tell(status, name)
        if status == 'FAIL' and getattr(self, 'on_fail', None):
            self.on_fail(name)
        return ok

    def outcome(self, name, status, evidence):
        """An unavailable scenario is distinct from a product assertion failure."""
        if status not in ('PASS', 'FAIL', 'BLOCKED', 'SKIPPED', 'RECOVERED'):
            raise ValueError(status)
        evidence = re.sub(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', '[redacted]', str(evidence))
        self.results.append({'step': self.step, 'name': name, 'status': status,
                             'evidence': evidence, 'expected': None})
        self.say('%s  %s\n    %s' % (status, name, evidence))
        self.tell(status, name)

    def manual(self, name, why):
        """A check the runner cannot make without pressing a key at a popup that is not ours: MANUAL."""
        self.results.append({'step': self.step, 'name': name, 'status': 'MANUAL', 'evidence': str(why), 'expected': None})
        self.say('MANUAL  %s\n    %s' % (name, str(why).replace('\n', '\n    ')))
        self.tell('MANUAL', name)

    def finish(self):
        with open(os.path.join(self.outdir, 'results.json'), 'w') as f:
            json.dump(self.results, f, indent=2)
        counts = {}
        for r in self.results:
            counts[r['status']] = counts.get(r['status'], 0) + 1
        self.say('summary: %s' % counts)
        self.log.close()
        return counts
