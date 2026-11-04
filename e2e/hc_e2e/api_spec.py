# SPDX-License-Identifier: Apache-2.0
"""The launcher API replies: field checks and the OpenRPC spec schema (api/tools/launcher_schema.py)."""
import os
import sys

from .constants import REPO

sys.path.insert(0, os.path.join(REPO, 'api', 'tools'))
import launcher_schema  # noqa: E402  (the launcher spec's own schema checker)


# ---------------------------------------------------------------------------------------------
# The launcher API (docs/launcher-api.md) as the runner checks it
# ---------------------------------------------------------------------------------------------
API_GROUP = 'org.webosbrew.homechooser.applications'
API_VERSION = 3  # the level this build of Home Chooser reports; the contract itself only requires >= 1
DISPOSABLE_ID = 'org.webosbrew.hcfixture.disposable'
API_FIXTURE_DIR = os.path.join(REPO, 'build', 'fixtures-api')
API_INFO_FIELDS = ('apiVersion', 'version', 'root', 'permission', 'permitted', 'isSelectedHome')
API_LIST_FIELDS = ('id', 'title', 'icon', 'largeIcon', 'folderPath', 'version', 'vendor', 'type', 'removable', 'visible', 'appCategory',
                   'iconColor', 'bgColor', 'systemApp', 'mediumLargeIcon', 'extraLargeIcon')
API_RUNNING_FIELDS = ('id', 'instanceId', 'processId', 'webProcessId')
_SPEC = None  # (spec, validator), loaded on first use by spec_reply_problems


def api_reply_problems(method, reply):
    """Missing documented fields or wrong types in a successful launcher reply."""
    if not isinstance(reply, dict):
        return ['no JSON reply']
    if reply.get('returnValue') is not True:
        return ['returnValue is not true: %s %s' % (reply.get('errorCode'), reply.get('errorText'))]
    p = []
    if method == 'getInfo':
        p += ['missing %s' % f for f in API_INFO_FIELDS if f not in reply]
        if reply.get('apiVersion') != API_VERSION:
            p.append('apiVersion %r, expected %d' % (reply.get('apiVersion'), API_VERSION))
        if 'permission' in reply and reply['permission'] != API_GROUP:
            p.append('permission %r' % reply['permission'])
        if 'version' in reply and not isinstance(reply['version'], str):
            p.append('version is not a string')
        p += ['%s is not a boolean' % f for f in ('root', 'permitted', 'isSelectedHome') if f in reply and not isinstance(reply[f], bool)]
        return p
    if method in ('closeApp', 'uninstallApp'):
        if reply.get('apiVersion') != API_VERSION:
            p.append('apiVersion %r, expected %d' % (reply.get('apiVersion'), API_VERSION))
        if not isinstance(reply.get('id'), str) or not reply['id']:
            p.append('missing or invalid id')
        if method == 'uninstallApp':
            if reply.get('pending') is not True:
                p.append('pending is not true')
            if reply.get('confirmation') not in ('alert', 'screen'):
                p.append('missing or invalid confirmation')
        return p
    if method == 'getForegroundApp':
        if reply.get('apiVersion') != API_VERSION:
            p.append('invalid apiVersion')
        if not isinstance(reply.get('appId'), str):
            p.append('appId is not a string')
        if 'windowId' in reply and not isinstance(reply['windowId'], str):
            p.append('windowId is not a string')
        if 'processId' in reply and (not isinstance(reply['processId'], int) or isinstance(reply['processId'], bool)):
            p.append('processId is not an integer')
        return p
    if method == 'getAppIcon':
        if reply.get('apiVersion') != API_VERSION:
            p.append('invalid apiVersion')
        if reply.get('mimeType') not in ('image/png', 'image/jpeg', 'image/gif', 'image/svg+xml', 'image/webp'):
            p.append('invalid mimeType')
        if not isinstance(reply.get('data'), str):
            p.append('data is not a string')
        if not isinstance(reply.get('byteLength'), int) or isinstance(reply.get('byteLength'), bool):
            p.append('byteLength is not an integer')
        else:
            try:
                import base64
                if len(base64.b64decode(reply.get('data', ''), validate=True)) != reply['byteLength']:
                    p.append('decoded data length differs from byteLength')
            except (ValueError, TypeError):
                p.append('data is not base64')
        return p
    if method == 'getAppInfo':
        if reply.get('apiVersion') != API_VERSION:
            p.append('invalid apiVersion')
        item = reply.get('app')
        if not isinstance(item, dict):
            return p + ['app is not an object']
        reply = {'returnValue': True, 'apiVersion': API_VERSION, 'apps': [item]}
        method = 'listApps'
    key, allowed = {'listApps': ('apps', API_LIST_FIELDS), 'listRunning': ('running', API_RUNNING_FIELDS)}[method]
    if reply.get('apiVersion') != API_VERSION:
        p.append('apiVersion %r, expected %d' % (reply.get('apiVersion'), API_VERSION))
    items = reply.get(key)
    if not isinstance(items, list):
        return p + ['%s is not an array' % key]
    for it in items:
        if not isinstance(it, dict) or not it.get('id'):
            p.append('an item without id: %r' % (it,))
            continue
        if not isinstance(it['id'], str):
            p.append('an item with non-string id: %r' % (it,))
        for field in allowed:
            if field not in it or field == 'id':
                continue
            value = it[field]
            if field in ('removable', 'visible', 'systemApp') and not isinstance(value, bool):
                p.append('%s has nonboolean %s' % (it['id'], field))
            elif field == 'appCategory' and (not isinstance(value, list) or any(not isinstance(v, str) for v in value)):
                p.append('%s has invalid appCategory' % it['id'])
            elif field not in ('removable', 'visible', 'systemApp', 'appCategory', 'processId', 'webProcessId') and not isinstance(value, str):
                p.append('%s has nonstring %s' % (it['id'], field))
        for field in allowed:
            if field in it and field in ('processId', 'webProcessId') and (not isinstance(it[field], int) or isinstance(it[field], bool)):
                p.append('%s has nonnumeric %s' % (it['id'], field))
    return p


def spec_reply_problems(method, reply):
    """--api-check: problems of a reply against the method's result schema in api/launcher-v1.openrpc.json,
    checked with the spec lint's own checker (api/tools/launcher_schema.py). [] when it conforms; None for
    a reply the spec does not describe (not JSON, or a reply of the luna bus itself, without apiVersion)."""
    global _SPEC
    if _SPEC is None:
        _SPEC = (launcher_schema.load_spec(), launcher_schema.Validator([]))
    try:
        return launcher_schema.reply_problems(_SPEC[0], method, reply, _SPEC[1])
    except launcher_schema.SchemaError as e:
        return ['the spec cannot be checked: %s' % e]


def launcher_methods():
    """Every launcher method of api/launcher-v1.openrpc.json: what the permission grants, and getInfo."""
    perm = launcher_schema.load_spec()['x-permission']
    return list(perm['grants']) + list(perm['notNeededFor'])


ICON_MAX_BYTES = 1048576  # service/src/apps.h


def too_large_refusal(reply, size):
    """True when `reply` is the documented iconTooLarge refusal and the icon file (`size` bytes, None when not
    known) really is over the limit: a launcher whose own icon is that big is refused, which is correct."""
    return (api_refused(reply) and reply.get('errorCode') == 'iconTooLarge' and reply.get('apiVersion') == API_VERSION
            and isinstance(size, int) and size > ICON_MAX_BYTES)


def api_refused(reply):
    """A refusal: a JSON reply with returnValue false (the service's errorCode, or the hub's denial)."""
    return isinstance(reply, dict) and reply.get('returnValue') is False
