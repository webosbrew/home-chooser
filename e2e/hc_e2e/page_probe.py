# SPDX-License-Identifier: Apache-2.0
"""--api-check: the launcher page's own subscriptions (listRunning, getForegroundApp, getAppLifeEvents held through
the client by e2e/fixtures/web/probe.js, with the page's own bus permissions).  The pure verdict on what the page
recorded; the device side is hc_e2e/steps/page_probe.py."""
import json

METHODS = ('listRunning', 'getForegroundApp', 'getAppLifeEvents')
PROBE_JS = "JSON.stringify(window.__hcProbe || null)"


def parse_probe(value):
    """The page's record from Runtime.evaluate's value (a JSON string), or None."""
    try:
        v = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return None
    return v if isinstance(v, dict) and isinstance(v.get('subs'), dict) else None


def replies_of(data, method):
    sub = (data or {}).get('subs', {}).get(method) or {}
    return [r for r in sub.get('replies') or [] if isinstance(r, dict)]


def acked(data, method):
    """The first reply of a held subscription exists and says subscribed: true (apiVersion >= 3 for the stream)."""
    rs = replies_of(data, method)
    if not rs or rs[0].get('subscribed') is not True:
        return False
    return method != 'getAppLifeEvents' or ('event' not in rs[0] or rs[0]['event'] is None)


def probe_problems(data, appid):
    """What is wrong with the page's record after `appid` was launched and closed while it held the
    subscriptions.  Empty when the page saw: an acknowledgement on each; listRunning with appid, then without;
    getForegroundApp naming appid, then another app; life events launch and close for appid."""
    if data is None:
        return ['the page recorded nothing (window.__hcProbe missing)']
    p = []
    if not data.get('hasClient'):
        p.append('the page has no window.HomeChooser (the client did not load)')
    for m in METHODS:
        sub = (data.get('subs') or {}).get(m)
        if sub is None:
            p.append('%s: never subscribed' % m)
            continue
        if sub.get('error'):
            p.append('%s: the subscription ended with %s' % (m, json.dumps(sub['error'])))
        if not acked(data, m):
            p.append('%s: no first reply with subscribed: true' % m)
        for r in replies_of(data, m):
            if not isinstance(r.get('apiVersion'), int) or r['apiVersion'] < 1:
                p.append('%s: a reply without apiVersion' % m)
                break
        if m == 'getAppLifeEvents' and replies_of(data, m)[:1] and (replies_of(data, m)[0].get('apiVersion') or 0) < 3:
            p.append('getAppLifeEvents: acknowledgement apiVersion below 3')
    rs = replies_of(data, 'listRunning')
    seen = [i for i, r in enumerate(rs) if appid in (r.get('ids') or [])]
    if not seen:
        p.append('listRunning: no reply listed %s (ids per reply: %s)' % (appid, [len(r.get('ids') or []) for r in rs]))
    elif not any(appid not in (r.get('ids') or []) for r in rs[seen[0] + 1:]):
        p.append('listRunning: no reply after the launch dropped %s' % appid)
    rs = replies_of(data, 'getForegroundApp')
    seen = [i for i, r in enumerate(rs) if r.get('appId') == appid]
    if not seen:
        p.append('getForegroundApp: never named %s (seen: %s)' % (appid, [r.get('appId') for r in rs]))
    elif not any(r.get('appId') != appid for r in rs[seen[0] + 1:]):
        p.append('getForegroundApp: no change after %s was in front (seen: %s)' % (appid, [r.get('appId') for r in rs]))
    rs = replies_of(data, 'getAppLifeEvents')
    mine = [(i, r.get('event')) for i, r in enumerate(rs) if r.get('appId') == appid]
    start = next((i for i, k in mine if k in ('launch', 'relaunch')), None)
    if start is None:
        p.append('getAppLifeEvents: no launch event for %s (seen for it: %s)' % (appid, [k for _, k in mine] or 'none'))
    elif not any(i > start and k in ('close', 'stop') for i, k in mine):
        p.append('getAppLifeEvents: no close or stop event for %s after its launch (seen: %s)' % (appid, [k for _, k in mine]))
    return p


def probe_summary(data, appid):
    out = []
    for m in METHODS:
        rs = replies_of(data, m)
        if m == 'listRunning':
            what = ['%s%d' % ('+' if appid in (r.get('ids') or []) else '-', len(r.get('ids') or [])) for r in rs]
        elif m == 'getForegroundApp':
            what = [r.get('appId') for r in rs]
        else:
            what = ['%s:%s' % (r.get('event'), r.get('appId')) for r in rs if r.get('event') and r.get('appId') == appid]
        out.append('%s: %d replies %s' % (m, len(rs), what))
    return '; '.join(out)
