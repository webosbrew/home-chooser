# SPDX-License-Identifier: Apache-2.0
"""Our system alerts on screen: the ls2/service proofs, the element match, focus, and the reboot alert monitor."""
import json
import os
import re
import signal
import subprocess
import threading

from . import timing
from .constants import APP_ID, TVPOWER_REBOOT_URI
from .releases import parse_reboot_ask


def alert_ls2_proof(info, message, target, safe, uninstall_id=None):
    if not info or info.get('message') != message or info.get('buttons') != [target, safe]:
        return False
    actions = info.get('actions', [])
    if (not isinstance(actions, list) or len(actions) != 2
            or not all(isinstance(b, dict) for b in actions)
            or info.get('alert') != 'accepted' or not info.get('alert_id')):
        return False
    yes, no = actions
    if uninstall_id is None:
        good = (yes.get('onclick') == TVPOWER_REBOOT_URI
                and yes.get('params') == {'reason': 'remoteKey'})
    else:
        good = (yes.get('onclick') in ('luna://com.webos.appInstallService/remove',
                                       'luna://com.webos.appInstallService/dev/remove')
                and yes.get('params') == {'id': uninstall_id, 'subscribe': True})
    return bool(good and yes.get('label') == target and no.get('label') == safe
                and not no.get('onclick') and not no.get('params') and no.get('focus') is True)


def alert_service_proof(raw, message, target, safe, uninstall_id=None, caller=None, observed=None):
    """Authorize from this request's service log OR matched LS2 call and accepted reply."""
    if alert_ls2_proof(observed, message, target, safe, uninstall_id):
        return True
    if uninstall_id is None:
        info = parse_reboot_ask(raw)
        return (info['message'] == message and info['buttons'] == [target, safe]
                and info['alert'] == 'accepted' and bool(info['alert_id'])
                and any('REBOOT_ASK' in line and 'notification/createAlert accepted (exit 0):' in line
                        and re.search(r'"returnValue"\s*:\s*true', line)
                        and info['alert_id'] in line for line in raw.splitlines()))
    prefix = '%s by %s: alert shown for luna://com.webos.appInstallService/' % (uninstall_id, caller)
    for line in raw.splitlines():
        if not re.search(r'UNINSTALL_ASK\s+\{\}\s+' + re.escape(prefix), line):
            continue
        reply = line.split(prefix, 1)[1]
        if not re.match(r'(dev/)?remove \(exit 0\): ', reply):
            continue
        try:
            data = json.loads(reply.split(': ', 1)[1])
        except (ValueError, IndexError):
            continue
        if data.get('returnValue') is True and data.get('alertId'):
            return True
    return False


def match_element(frame, text=None, role=None, region=None):
    """Select one exact screen element; never guess between OCR hits."""
    if text is None and role is None:
        raise ValueError('text or role is required')
    rows = frame.get('elements')
    if not isinstance(rows, list):
        raise ValueError('missing elements array')
    matches = []
    for row in rows:
        if text is not None and row.get('text') != text:
            continue
        if role is not None and row.get('kind') not in (role if isinstance(role, tuple) else (role,)):
            continue
        box = row.get('box')
        centre = row.get('centre')
        if not isinstance(box, list) or len(box) != 4:
            raise ValueError('missing element geometry')
        if centre is None:
            centre = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]
        if (len(centre) != 2 or any(type(v) not in (int, float) for v in centre)
                or not 0 <= centre[0] < frame['width'] or not 0 <= centre[1] < frame['height']):
            raise ValueError('invalid element centre')
        if region and not (region[0] <= centre[0] <= region[2] and region[1] <= centre[1] <= region[3]):
            continue
        matches.append(dict(row, centre=centre))
    if len(matches) != 1:
        raise ValueError('expected exactly one element text=%r role=%r; found %s' % (text, role, len(matches)))
    return matches[0]


def alert_focus(frame, message, target, safe, require_focus=True):
    """Fail closed on incomplete OCR, extra controls, or ambiguous contrast/geometry."""
    if not isinstance(frame, dict) or not isinstance(frame.get('elements'), list):
        raise ValueError('missing elements array')
    rows = frame['elements']
    if any(not isinstance(r, dict) for r in rows):
        raise ValueError('malformed element')
    texts = [r.get('text') for r in rows if r.get('kind') == 'text']
    # Some firmware renders no title header. Ownership also requires
    # the matching service request/reply; the app title behind the alert is not proof.
    if texts.count(message) != 1:
        raise ValueError('expected message not uniquely visible')
    buttons = [r for r in rows if r.get('kind') == 'button' and r.get('text') in (target, safe)]
    if len(buttons) != 2 or sorted(r.get('text', '') for r in buttons) != sorted((target, safe)):
        raise ValueError('expected exactly the two alert buttons')
    for b in buttons:
        box = b.get('box')
        if (not isinstance(box, list) or len(box) != 4
                or any(type(v) not in (int, float) for v in box)
                or not box[0] < box[2] or not box[1] < box[3]
                or not isinstance(b.get('style'), dict)
                or type(b['style'].get('disabled')) is not bool):
            raise ValueError('missing button geometry or contrast flag')
    first, second = sorted(buttons, key=lambda b: b['box'][1])
    if (first['box'][3] < second['box'][1]
            and max(first['box'][0], second['box'][0]) < min(first['box'][2], second['box'][2])):
        horizontal = False
        focused = [b for b in buttons if b['style']['disabled'] is False]
        direction = 'UP' if first['text'] == target else 'DOWN'
    else:
        first, second = sorted(buttons, key=lambda b: b['box'][0])
        if (first['box'][2] >= second['box'][0]
                or max(first['box'][1], second['box'][1]) >= min(first['box'][3], second['box'][3])):
            raise ValueError('buttons are not a disjoint aligned pair')
        horizontal = True
        focused = [b for b in buttons if b['style'].get('highlighted') is True]
        direction = 'LEFT' if first['text'] == target else 'RIGHT'
    if not require_focus:
        focused = [buttons[0]]
    if require_focus and all(b['style']['disabled'] for b in buttons):
        raise ValueError('both buttons are dimmed')
    if require_focus and len(focused) != 1:
        # Provisional only: ENTER also requires a reciprocal appearance swap.
        contrasts = [b['style'].get('contrast', 0) for b in buttons]
        if max(contrasts) - min(contrasts) < 2:
            raise ValueError('focus is ambiguous')
        focused = [buttons[contrasts.index(max(contrasts))]]
    # The alert shares the screen with controls behind it. Its message and button band
    # must contain exactly our pair; controls elsewhere (the app beneath) are irrelevant.
    top = min(b['box'][1] for b in buttons)
    bottom = max(b['box'][3] for b in buttons)
    messages = [r for r in rows if r.get('kind') == 'text' and r.get('text') == message]
    msg = messages[0]
    if horizontal:
        # 5.x puts the prompt immediately above a highlighted horizontal pair.
        if (not 0 <= top - msg['box'][3] <= bottom - top
                or msg.get('group') is None
                or any(b.get('group') != msg['group'] for b in buttons)):
            raise ValueError('message is outside the alert group')
    elif not top <= sum(msg['box'][1::2]) / 2 <= bottom:
        if not (0 <= top - msg['box'][3] <= bottom - top
                and max(msg['box'][0], min(b['box'][0] for b in buttons))
                    < min(msg['box'][2], max(b['box'][2] for b in buttons))):
            raise ValueError('message is outside the alert dialog')
        top = msg['box'][1]
    # The dialog rectangle is the union of its message and its two buttons.  A control counts as being
    # in the alert only if it overlaps that rectangle; OCR "buttons" in the app list behind the dialog
    # (e.g. a Homebrew Channel row title) share the vertical band but lie beside it, so they are not
    # extra alert controls.  A third control inside the rectangle still fails, and the exact message and
    # the two exact buttons were already required above.
    left = min([b['box'][0] for b in buttons] + [msg['box'][0]])
    right = max([b['box'][2] for b in buttons] + [msg['box'][2]])
    band = [r for r in rows if r.get('kind') == 'button'
            and isinstance(r.get('box'), list) and len(r['box']) == 4
            and r['box'][1] < bottom and r['box'][3] > top
            and r['box'][0] < right and r['box'][2] > left]
    if len(band) != 2:
        raise ValueError('extra controls in the alert band')
    return focused[0]['text'], direction


def alert_style_swap(before, after, target, safe):
    """Match reciprocal appearances with a small tolerance for OCR colour jitter."""
    def pair(frame):
        return {r['text']: r for r in frame['elements']
                if r.get('kind') == 'button' and r.get('text') in (target, safe)}
    a, b = pair(before), pair(after)
    for field in ('highlighted', 'disabled'):
        x, y = a[safe]['style'].get(field), a[target]['style'].get(field)
        if type(x) is bool and type(y) is bool and x != y:
            if b[target]['style'].get(field) == x and b[safe]['style'].get(field) == y:
                return field
            raise ValueError('focus appearance did not swap: ' + field)
    def rgb(row, field):
        value = row['style'].get(field, '')
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
            raise ValueError('missing appearance colours')
        return tuple(int(value[i:i+2], 16) for i in (1, 3, 5))
    def distance(x, y):
        return max(abs(i-j) for i, j in zip(x, y))
    for field in ('background', 'foreground'):
        x, y = rgb(a[safe], field), rgb(a[target], field)
        if distance(x, y) >= 40:
            if distance(rgb(b[target], field), x) <= 24 and distance(rgb(b[safe], field), y) <= 24:
                return field + ' colour'
    raise ValueError('no reciprocal focus appearance swap')


class RebootAlertMonitor:
    """Observe the service's notification call when a firmware drops its PmLog info lines.

    ls-monitor carries each button's action and parameters in the request. Keep raw events only in memory;
    the recorded evidence contains the message, button actions and notification reply.
    """
    def __init__(self, dev):
        self.dev = dev
        self.proc = None
        self.thread = None
        self.remote_pid = None
        self.ready = threading.Event()
        self.lock = threading.Lock()
        self.calls = {}
        self.seen = []
        self.candidates = []
        self.info = {'message': None, 'buttons': [], 'alert': None, 'alert_id': None, 'toast': None}

    def start(self):
        self.proc = self.dev.stream('printf "HC_MONITOR_PID=%s\\n" "$$"; exec ls-monitor -j -f com.webos.notification')
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()
        self.ready.wait(timeout=5)
        timing.sleep(1)  # let the hub attach before the confirmation key is sent
        return self.ready.is_set() and self.proc.poll() is None

    def _read(self):
        for line in self.proc.stdout:
            if line.startswith('HC_MONITOR_PID='):
                try:
                    self.remote_pid = int(line.partition('=')[2].strip())
                    self.ready.set()
                except ValueError:
                    pass
                continue
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                continue
            with self.lock:
                self._observe(event)

    def _observe(self, event):
        if len(self.seen) < 12 and (event.get('method') in ('createAlert', 'createToast')
                                    or (event.get('type') == 'return'
                                        and event.get('sender') == 'com.webos.notification')):
            self.seen.append({k: event.get(k) for k in
                              ('type', 'transport', 'sender', 'destination', 'method', 'replyToken')})
        if (event.get('type') == 'call' and event.get('transport') == 'TX'
                and event.get('destination') == 'com.webos.notification'
                and event.get('method') in ('createAlert', 'createToast')):
            payload = event.get('payload') or {}
            if not isinstance(payload, dict):
                if len(self.candidates) < 4:
                    self.candidates.append({'method': event.get('method'), 'payload_type': type(payload).__name__})
                return
            buttons = payload.get('buttons')
            if payload.get('sourceId') == APP_ID and len(self.candidates) < 4:
                self.candidates.append({
                    'method': event.get('method'), 'sourceId': payload.get('sourceId'),
                    'title': payload.get('title'), 'message': payload.get('message'),
                    'buttons': [{k: b.get(k) for k in ('label', 'onclick', 'focus')}
                                for b in buttons if isinstance(b, dict)] if isinstance(buttons, list) else type(buttons).__name__,
                })
            if payload.get('sourceId') != APP_ID:
                return
            method = event['method']
            if method == 'createAlert':
                if payload.get('title') != 'Home Chooser':
                    return
                self.info = {'message': payload.get('message'), 'buttons': [], 'alert': None,
                             'alert_id': None, 'toast': None, 'actions': buttons}
                self.info['buttons'] = [b.get('label') for b in buttons if isinstance(b, dict)] \
                    if isinstance(buttons, list) else []
            elif self.info['message'] and payload.get('message') != self.info['message']:
                return
            sender = event.get('senderUniqueName') or event.get('sender')
            self.calls[(sender, event.get('token'))] = (method, self.info.copy())
        elif (event.get('type') == 'return' and event.get('transport') == 'TX'
              and event.get('sender') == 'com.webos.notification'):
            destination = event.get('destinationUniqueName') or event.get('destination')
            call = self.calls.pop((destination, event.get('replyToken')), None)
            if call:
                method, request_info = call
                payload = event.get('payload') or {}
                if not isinstance(payload, dict):
                    return
                if method == 'createAlert':
                    self.info = request_info
                accepted = payload.get('returnValue') is True
                key = 'alert' if method == 'createAlert' else 'toast'
                self.info[key] = 'accepted' if accepted else 'refused'
                if method == 'createAlert' and accepted:
                    self.info['alert_id'] = payload.get('alertId')

    def snapshot(self):
        with self.lock:
            return dict(self.info, buttons=list(self.info['buttons']))

    def diagnostic(self):
        with self.lock:
            return json.dumps({'candidates': self.candidates, 'events': self.seen})

    def close(self):
        if self.remote_pid:
            pid = self.remote_pid
            self.dev.sh('if [ "$(cat /proc/%d/comm 2>/dev/null)" = ls-monitor ]; then kill %d; fi'
                        % (pid, pid), timeout=10)
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.proc.wait(timeout=5)
        if self.thread:
            self.thread.join(timeout=2)
        if self.proc and self.proc.stdout:
            self.proc.stdout.close()
