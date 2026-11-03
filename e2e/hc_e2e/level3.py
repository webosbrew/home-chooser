# SPDX-License-Identifier: Apache-2.0
"""Launcher API level 3 (docs/launcher-api.md): the pure parsing and verdicts of the --api-check steps for
getAppLifeEvents (a stream), the systemApp / mediumLargeIcon / extraLargeIcon fields and the new getIcons sizes.
The device side is hc_e2e/steps/level3.py."""
import base64
import json
import re

LEVEL3_SINCE = 3
ICON_ERRORS = ('notFound', 'noIcon', 'iconTooLarge', 'overBudget')
START_EVENTS = ('launch', 'relaunch')
END_EVENTS = ('close', 'stop')


def parse_stream(text):
    """The JSON objects of `luna-send -i` output, one per line, in order (lines that are not a JSON object,
    a pty's CR, the luna-send banner, are dropped)."""
    out = []
    for line in (text or '').replace('\r', '\n').splitlines():
        i = line.find('{')
        if i < 0:
            continue
        try:
            v = json.loads(line[i:])
        except ValueError:
            continue
        if isinstance(v, dict):
            out.append(v)
    return out


def split_stream(replies):
    """(acknowledgement or None, events): the first reply without an `event` is the acknowledgement."""
    ack = next((r for r in replies if 'event' not in r), None)
    return ack, [r for r in replies if 'event' in r]


def stream_problems(replies, appid, validate=None):
    """What is wrong with a getAppLifeEvents subscription that watched `appid` launch and close.  validate:
    optional callable(reply) -> [problems] (the spec's schema) applied to the acknowledgement and every event."""
    ack, events = split_stream(replies)
    p = []
    if ack is None:
        p.append('no acknowledgement (the first reply must be {returnValue, apiVersion, subscribed: true})')
    else:
        if ack.get('returnValue') is not True:
            p.append('the acknowledgement is a refusal: %s %s' % (ack.get('errorCode'), ack.get('errorText')))
        if ack.get('subscribed') is not True:
            p.append('the acknowledgement does not say subscribed: true')
        v = ack.get('apiVersion')
        if not isinstance(v, int) or isinstance(v, bool) or v < LEVEL3_SINCE:
            p.append('acknowledgement apiVersion %r, level %d needed' % (v, LEVEL3_SINCE))
        if replies and replies[0] is not ack:
            p.append('the acknowledgement is not the first reply')
    for r in replies:
        if 'event' in r:
            if not isinstance(r['event'], str) or not r['event']:
                p.append('an event that is not a string: %r' % (r['event'],))
            if r.get('subscribed') is not True:
                p.append('event %s without subscribed: true' % r.get('event'))
            if 'appId' in r and not isinstance(r['appId'], str):
                p.append('event %s with a non-string appId' % r.get('event'))
        if validate:
            p += ['%s: %s' % (r.get('event', 'acknowledgement'), x) for x in validate(r)]
    mine = [(i, e['event']) for i, e in enumerate(events) if e.get('appId') == appid]
    started = next((i for i, kind in mine if kind in START_EVENTS), None)
    if started is None:
        p.append('no launch event for %s (seen for it: %s)' % (appid, [k for _, k in mine] or 'none'))
    elif not any(i > started and kind in END_EVENTS for i, kind in mine):
        p.append('no close or stop event for %s after its launch (seen for it: %s)' % (appid, [k for _, k in mine]))
    return p


def stream_summary(replies, appid):
    ack, events = split_stream(replies)
    return 'ack %s; %d event(s), kinds %s; for %s: %s' % (
        'yes' if ack else 'no', len(events), sorted({e['event'] for e in events if isinstance(e.get('event'), str)}),
        appid, [e['event'] for e in events if e.get('appId') == appid])


def list_field_problems(apps, launcher_id=None):
    """Level 3 fields of listApps / getAppInfo items: systemApp a boolean, mediumLargeIcon and extraLargeIcon
    strings (an empty string stays one: docs/launcher-api.md), wherever they are present.  Present means the
    platform gave it, so a release without them is fine; the launcher (a developer app) is not a system app."""
    p = []
    for a in apps:
        if not isinstance(a, dict):
            continue
        if 'systemApp' in a and not isinstance(a['systemApp'], bool):
            p.append('%s: systemApp is not a boolean (%r)' % (a.get('id'), a['systemApp']))
        for f in ('mediumLargeIcon', 'extraLargeIcon'):
            if f in a and not isinstance(a[f], str):
                p.append('%s: %s is not a string (%r)' % (a.get('id'), f, a[f]))
        if launcher_id and a.get('id') == launcher_id and a.get('systemApp') is True:
            p.append('%s: the installed launcher is reported as a system app' % launcher_id)
    return p


def field_census(apps):
    """(apps with systemApp, systemApp true, systemApp false, non-empty mediumLargeIcon, non-empty extraLargeIcon)"""
    apps = [a for a in apps if isinstance(a, dict)]
    return (sum('systemApp' in a for a in apps), sum(a.get('systemApp') is True for a in apps),
            sum(a.get('systemApp') is False for a in apps),
            sum(bool(a.get('mediumLargeIcon')) for a in apps), sum(bool(a.get('extraLargeIcon')) for a in apps))


def icon_sample(apps, size='extraLargeIcon', limit=6):
    """(ids whose listApps entry names a `size` icon, one id whose entry has none or an empty one or None):
    what getIcons is asked about."""
    named = [a['id'] for a in apps if isinstance(a, dict) and isinstance(a.get('id'), str) and a.get(size)][:limit]
    bare = next((a['id'] for a in apps if isinstance(a, dict) and isinstance(a.get('id'), str) and not a.get(size)), None)
    return named, bare


def icons_problems(items, reply, named):
    """getIcons {items} reply against what was asked: one entry per item in order; an entry is an icon (an image
    data URL whose byteLength is the decoded length) or one of the documented errors; an item whose listApps
    entry has no icon of that size (not in `named`) is noIcon, never another size's picture.  Returns
    (problems, number of icons read)."""
    icons = (reply or {}).get('icons')
    if not isinstance(icons, list):
        return ['icons is not an array'], 0
    if len(icons) != len(items):
        return ['%d entries for %d items' % (len(icons), len(items))], 0
    p, data = [], 0
    for item, entry in zip(items, icons):
        who = item.get('id') or item.get('launchPointId') or item.get('inputId')
        if not isinstance(entry, dict):
            p.append('%s: entry is not an object' % who)
        elif 'errorCode' in entry:
            if entry['errorCode'] not in ICON_ERRORS:
                p.append('%s: unknown errorCode %r' % (who, entry['errorCode']))
            if who not in named and entry['errorCode'] != 'noIcon':
                p.append('%s: has no icon of this size, expected noIcon, got %s' % (who, entry['errorCode']))
        else:
            url = entry.get('dataUrl')
            m = re.match(r'data:(image/[a-z0-9.+-]+);base64,(.*)$', url or '', re.S)
            if not m:
                p.append('%s: dataUrl is not an image data URL' % who)
                continue
            try:
                n = len(base64.b64decode(m.group(2), validate=True))
            except ValueError:
                p.append('%s: dataUrl is not base64' % who)
                continue
            if entry.get('byteLength') != n:
                p.append('%s: byteLength %r, decoded %d' % (who, entry.get('byteLength'), n))
            if entry.get('mimeType') != m.group(1):
                p.append('%s: mimeType %r, data URL says %s' % (who, entry.get('mimeType'), m.group(1)))
            if who not in named:
                p.append('%s: got a picture for an app whose entry has no such icon' % who)
            data += 1
    if named and not data and not p:
        # a named icon may still be missing on disk or outside the app's folder (noIcon): only all of them is odd
        p.append('none of %d apps with an icon of this size returned data' % len(named))
    return p, data
