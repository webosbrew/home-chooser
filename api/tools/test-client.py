#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Runs the generated client and mock (api/client) in node and checks what they do.

  test-client.py NODE

client-test.js prints one JSON line per observation; this checks each one and validates every reply
the mock gives against the method's result schema in api/launcher-v1.openrpc.json. Standard library
only; node is the only other program.
"""
import json
import os
import subprocess
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
API = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)
from launcher_schema import SPEC, Validator, load_spec, reply_problems  # noqa: E402


def check(problems, ok, what):
    if not ok:
        problems.append(what)


def main(argv):
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    spec = load_spec()
    run = subprocess.run([argv[0], os.path.join(TOOLS, 'client-test.js'), SPEC, os.path.join(API, 'client')],
                         capture_output=True, text=True, timeout=120)
    if run.returncode:
        print(run.stdout + run.stderr)
        print('FAIL client-test.js exit %d' % run.returncode)
        return 1
    records = [json.loads(line) for line in run.stdout.splitlines() if line.startswith('{')]
    sv = Validator([])
    problems, replies = [], 0
    service = spec['servers'][0]['url']
    methods = {m['name']: m for m in spec['methods']}
    for r in records:
        t = r['test']
        if t == 'bridge-example':
            label = 'bridge %s/%s' % (r['method'], r['example'])
            want = r['expected']
            res = r['result']
            check(problems, r['calls'][0]['uri'] == '%s/%s' % (service, r['method']), label + ': uri ' + r['calls'][0]['uri'])
            check(problems, {'cancel': True} in r['calls'], label + ': the bridge was not cancelled')
            if want['returnValue'] is True:
                check(problems, res['ok'] and res['value'] == want, label + ': not resolved with the reply: %s' % res)
            else:
                e = res.get('error') or {}
                check(problems, not res['ok'] and e.get('source') == 'homechooser' and e.get('errorCode') == want['errorCode']
                      and e.get('code') == want['errorCode'] and e.get('errorText') == want['errorText']
                      and e.get('reply') == want and e.get('name') == 'HomeChooserError' and e.get('isError'),
                      label + ': not rejected as a Home Chooser error: %s' % res)
        elif t == 'bridge-failure':
            e = r['result'].get('error') or {}
            source = 'platform' if r['label'] == 'platform' else 'transport'
            check(problems, not r['result']['ok'] and e.get('source') == source,
                  'bridge %s: not rejected with source %s: %s' % (r['label'], source, r['result']))
            if r['label'] == 'platform':
                check(problems, e.get('errorCode') == -1 and 'Unknown method' in e.get('errorText', ''),
                      'bridge platform: the bus code and text are not passed through: %s' % e)
        elif t == 'supports':
            check(problems, r['before'] is False and r['level1'] is True and r['level2'] is True and r['level3'] is False
                  and r['apiVersion'] == spec['info']['x-apiVersion'], 'supports(level) after a level 2 reply: %s' % r)
        elif t == 'supports-3':
            check(problems, r['level3'] is True and r['level4'] is False, 'supports(3) after a level 3 reply: %s' % r)
        elif t == 'bridge-life-events':
            sent = [c for c in r['calls'] if 'uri' in c]
            events = methods['getAppLifeEvents']['x-events']
            check(problems, len(sent) == 1 and sent[0]['uri'] == service + '/getAppLifeEvents' and sent[0]['payload'] == {'subscribe': True},
                  'life events: call %s' % sent)
            check(problems, r['calls'].count({'cancel': True}) == 1 and not r['errors'], 'life events: cancel or errors: %s' % r)
            check(problems, len(r['replies']) == 1 + len(events) and r['replies'][1:] == events and r['replies'][0].get('subscribed') is True
                  and 'event' not in r['replies'][0], 'life events: the ack and %d events in order: %s' % (len(events), r['replies']))
            check(problems, r['hasOneShot'] == 'undefined', 'life events: the client has a single-reply function for a stream')
        elif t == 'mock-life-events':
            events = r['mockEvents']
            check(problems, not r['errors'] and len(r['replies']) == 1 + len(events) + 1, 'mock life events: %s' % r['replies'])
            for reply in r['replies']:
                found = reply_problems(spec, 'getAppLifeEvents', reply, sv)
                replies += 1
                check(problems, found == [] and reply.get('subscribed') is True, 'mock life event reply: %s %s' % (found, reply))
            check(problems, 'event' not in r['replies'][0] and [x.get('event') for x in r['replies'][1:]] ==
                  [e['event'] for e in events] + ['close'], 'mock life events: order: %s' % [x.get('event') for x in r['replies']])
            check(problems, len(r['over']) == 1 and r['over'][0]['errorCode'] == 'tooManySubscriptions', 'mock life events cap: %s' % r['over'])
            for key in ('plain', 'no'):
                v = r[key].get('value') or {}  # lunaCall resolves with the reply whatever its returnValue
                check(problems, r[key]['ok'] and v.get('returnValue') is False and v.get('errorCode') == 'badRequest',
                      'mock getAppLifeEvents without subscribe true: %s' % r[key])
            check(problems, r['sizes']['ok'] and [i.get('errorCode') for i in r['sizes']['value']['icons']] == ['noIcon', 'notFound'],
                  'mock getIcons larger sizes: %s' % r['sizes'])
            check(problems, not r['bad']['ok'] and r['bad']['error']['errorCode'] == 'badRequest', 'mock getIcons hugeIcon: %s' % r['bad'])
        elif t == 'request':
            want = spec['x-requestLaunch']
            sent = [c for c in r['calls'] if 'uri' in c]
            check(problems, r['own'] == 'org.example.launcher', 'ownAppId from PalmSystem.identifier: %r' % r['own'])
            check(problems, r['a']['ok'] and r['b']['ok'], 'requestHome did not resolve: %s' % r)
            check(problems, [c['uri'] for c in sent] == [want['uri']] * 2, 'requestHome uri: %s' % sent)
            check(problems, sent and sent[0]['payload'] == want['payload'], 'requestHome payload %s is not x-requestLaunch %s'
                  % (sent and sent[0]['payload'], want['payload']))
            check(problems, len(sent) > 1 and sent[1]['payload']['params'] == {'request': 'org.example.other'},
                  'requestHome(appId): %s' % sent)
        elif t == 'request-no-id':
            check(problems, r['own'] is None and r['typeError'], 'requestHome without an app id: %s' % r)
        elif t == 'bridge-subscribe':
            label = 'subscribe %s' % r['label']
            sent = [c for c in r['calls'] if 'uri' in c]
            check(problems, len(sent) == 1 and sent[0]['uri'] == service + '/listInputs' and sent[0]['payload'] == {'subscribe': True},
                  label + ': call %s' % sent)
            check(problems, r['calls'].count({'cancel': True}) == 1, label + ': not cancelled exactly once: %s' % r['calls'])
            if r['label'] == 'two-replies':
                check(problems, len(r['replies']) == 2 and not r['errors'], label + ': %s' % r)
            else:
                source = 'homechooser' if r['label'] == 'refused' else 'transport'
                check(problems, not r['replies'] and len(r['errors']) == 1 and r['errors'][0]['source'] == source,
                      label + ': not one %s error and no reply: %s' % (source, r))
        elif t == 'mock-subscribe':
            for reply in r['replies']:
                found = reply_problems(spec, 'listInputs', reply, sv)
                replies += 1
                check(problems, found == [] and reply.get('subscribed') is True, 'mock subscribe reply: %s %s' % (found, reply))
            check(problems, len(r['replies']) == 2 and not r['errors'], 'mock subscribe: first, notify, cancel: %s' % r)
            check(problems, len(r['replies']) == 2 and r['replies'][0]['inputs'][0]['connected'] != r['replies'][1]['inputs'][0]['connected'],
                  'mock subscribe: the change is not in the second reply')
            check(problems, len(r['over']) == 1 and r['over'][0]['errorCode'] == 'tooManySubscriptions',
                  'mock subscribe: the cap: %s' % r['over'])
        elif t == 'mock-subscribe-foreground':
            for reply in r['replies']:
                found = reply_problems(spec, 'getForegroundApp', reply, sv)
                replies += 1
                check(problems, found == [] and reply.get('subscribed') is True, 'mock foreground reply: %s %s' % (found, reply))
            check(problems, [x.get('appId') for x in r['replies']] == [
                next(x for x in spec['methods'] if x['name'] == 'getForegroundApp')['examples'][0]['result']['value']['appId'],
                'org.example.other'] and not r['errors'], 'mock foreground subscription: %s' % r)
        elif t == 'mock-app-count':
            for n, reply in r['replies'].items():
                found = reply_problems(spec, 'listApps', reply, sv)
                replies += 1
                ids = [a['id'] for a in reply.get('apps', [])]
                check(problems, found == [] and len(ids) == int(n) and len(set(ids)) == len(ids),
                      'mock ?hcmock-apps=%s: %d apps, %s' % (n, len(ids), found))
            check(problems, r['icon'].get('errorCode') == 'noIcon', 'mock getIcons of a made-up app: %s' % r['icon'])
        elif t == 'commonjs':
            check(problems, set(r['methods']) == {'function'} and r['stream'] == ['undefined'], 'CommonJS export: %s' % r)
        elif t == 'mock-inert':
            check(problems, not r['hasMock'], 'the mock replaced the transport although a bridge exists')
        elif t == 'mock':
            sc = r['scenario']
            for method, calls in r['replies'].items():
                for c in calls:
                    res = c['result']
                    reply = res['value'] if res['ok'] else res['error']['reply']
                    label = 'mock %s %s %s' % (sc, method, json.dumps(c['params']))
                    if sc == 'absent':
                        check(problems, not res['ok'] and res['error']['source'] == 'platform', label + ': not a platform error')
                        continue
                    found = reply_problems(spec, method, reply, sv)
                    replies += 1
                    check(problems, found == [], '%s: %s' % (label, found))
                    if not res['ok']:
                        check(problems, res['error']['source'] == 'homechooser', label + ': source ' + res['error']['source'])
                    takes_id = any(x['name'] == 'id' for x in methods[method]['params'])
                    if sc == 'selected-home' and takes_id and c['params'].get('id') == '../bad':
                        check(problems, not res['ok'] and res['error']['errorCode'] == 'badRequest', label + ': not badRequest')
                    if sc == 'before-elevation' and method != 'getInfo':
                        check(problems, not res['ok'] and res['error']['errorCode'] == 'notRoot', label + ': not notRoot')
                    if sc == 'not-home' and method in ('closeApp', 'uninstallApp'):
                        check(problems, not res['ok'] and res['error']['errorCode'] == 'notHome', label + ': not notHome')
                    if sc == 'not-permitted' and method == 'listApps':
                        check(problems, not res['ok'] and res['error']['errorCode'] == 'noPermission', label + ': not noPermission')
                    if sc == 'selected-home' and method == 'listApps' and 'subscribe' not in c['params']:
                        want = next(x for x in spec['methods'] if x['name'] == 'listApps')['examples'][0]['result']['value']
                        got = res['value'].get('apps', []) if res['ok'] else []
                        check(problems, res['ok'] and got[:len(want['apps'])] == want['apps'] and len(got) == 20
                              and sum(1 for a in got if a.get('visible') is False) == 1
                              and any('iconColor' in a for a in got) and any('bgColor' in a for a in got),
                              label + ': not the spec example\'s apps first, 20 in all, one invisible, some with colours')
                    if sc == 'selected-home' and methods[method].get('x-subscribes'):
                        check(problems, res['ok'] and res['value'].get('subscribed') is (True if c['params'].get('subscribe') is True else None),
                              label + ': subscribed does not follow the subscribe param')
            check(problems, r['launch']['ok'] and r['request']['ok'], 'mock %s: launch or request failed: %s' % (sc, r))
            check(problems, r['own'] == spec['x-appinfo']['minimalExample']['id'], 'mock %s: ownAppId %r' % (sc, r['own']))
            check(problems, len(r['launches']) == 2 and r['launches'][1] == dict(
                spec['x-requestLaunch']['payload'], params={'request': r['own']}), 'mock %s: launches %s' % (sc, r['launches']))
    check(problems, records and records[-1]['test'] == 'done', 'client-test.js did not finish')
    for p in problems:
        print('FAIL ' + p)
    print('client-js: %d observations, %d mock replies validated against the spec: %s' % (
        len(records), replies, 'ok' if not problems else '%d problem(s)' % len(problems)))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
