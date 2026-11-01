#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Offline child-process double, executed directly by the real GIO transport."""
import json
import os
import sys
import time

root = os.path.dirname(os.path.realpath(__file__))
args = sys.argv[1:]
# The home gate launches as com.webos.app.home; everything else is the privileged identity.
identities = ['com.webos.applicationManager']
if len(args) == 6 and args[-2].endswith('/launch'):
    identities.append('com.webos.app.home')
if len(args) not in (5, 6) or args[0] != '-a' or args[1] not in identities:
    sys.exit(91)
stream = args[2] == '-i'
if not stream and args[2:4] != ['-n', '1']:
    sys.exit(92)
uri, payload = args[-2], json.loads(args[-1])
method = uri.rsplit('/', 1)[-1]
entry = {'uri': uri, 'payload': payload, 'args': args, 'pid': os.getpid(),
         'parent': os.getppid(), 'group': os.getpgrp(), 'stream': stream}
fd = os.open(root + '/calls.jsonl', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
os.write(fd, (json.dumps(entry) + '\n').encode())
os.close(fd)
if stream:
    if payload.get('subscribe') is not True:
        sys.exit(93)
    with open(root + '/' + method + '.ready', 'w', encoding='ascii') as ready:
        ready.write(str(os.getpid()))
    with open(root + '/' + method + '.fifo', encoding='utf-8') as incoming:
        for line in incoming:
            if line == '!exit\n':
                break
            if line == '!crash\n':
                sys.exit(7)
            if line == '!close\n':
                os.close(1)
                time.sleep(60)
                break
            if line == '!storm\n':
                for _ in range(1400):
                    print('{"settings":{"defaultApps":{"home":"my.home"}}}', flush=True)
                continue
            sys.stdout.write(line)
            sys.stdout.flush()
    sys.exit(0)

if payload.get('test') == 'hang':
    time.sleep(60)
with open(root + '/state.json', encoding='utf-8') as source:
    state = json.load(source)
if method in state.get('hang', []):
    time.sleep(60)
if payload.get('test') == 'fail':
    print('{"returnValue":true}')
    sys.exit(9)
if payload.get('test') == 'garbage':
    print('garbage {not json}\nnoise\n{"returnValue": true}')
    sys.exit(0)
if payload.get('test') == 'no-newline':
    sys.stdout.write('{"returnValue":true}')
    sys.exit(0)
if payload.get('test') == 'false':
    print('{"returnValue":false,"errorText":"refused"}')
    sys.exit(0)
reply = {'returnValue': True}
if method in state.get('refuse', []):
    reply = {'returnValue': False, 'errorText': 'refused'}
elif method in state.get('replies', {}):
    reply = state['replies'][method]
elif method == 'getAppInfo' and 'apps' in state:
    reply['returnValue'] = payload['id'] in state['apps']
    reply['appInfo'] = state['apps'].get(payload['id'], {})
elif method == 'getAppInfo':
    reply['returnValue'] = not state.get('removed') or payload['id'] != 'org.webosbrew.homechooser'
    reply['appInfo'] = {'title': 'Native Test Home'}
elif method == 'getSystemSettings':
    reply['settings'] = {'defaultApps': state['record']}
elif method == 'setSystemSettings':
    state['record'] = payload['settings']['defaultApps']
elif method == 'getCurrentState':
    reply['currentState'] = 'normal'
elif method == 'getConfigs':
    reply['configs'] = {'com.webos.surfacemanager.keyFilters': state['filters']}
elif method == 'setConfigs':
    if payload.get('volatile') is not True:
        sys.exit(94)
    state['filters'] = payload['configs']['com.webos.surfacemanager.keyFilters']
elif method == 'getProfiles':
    reply['defaultApps'] = {'categories': {'home': 'com.webos.app.home'}}
elif method == 'getForegroundAppInfo':
    reply['appId'] = state.get('foreground', 'my.home')
elif method == 'getLastInput':
    if 'lastInput' in state:
        reply['lastSourceAppId'] = state['lastInput']
elif method == 'launch':
    state.setdefault('launched', []).append([args[1], payload['id']])
with open(root + '/state.new', 'w', encoding='utf-8') as target:
    json.dump(state, target)
os.replace(root + '/state.new', root + '/state.json')
print(json.dumps(reply))
