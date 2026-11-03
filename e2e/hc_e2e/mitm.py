# SPDX-License-Identifier: Apache-2.0
"""LG's software-update offer, answered "no new version" through the VM's own mitmweb.

Every test VM is mode=mitm: Beanjar runs a mitmweb per VM, up before QEMU, and the update daemon's first
`POST CheckSWAutoUpdate.laf` (about 20 s after QEMU starts) goes through it.  LG's server answers that check
with RESULT_CD 900 and a newer firmware, and the TV then raises "The latest version of the software is
available ... update now?", which takes key focus from Home Chooser.  We set one mitmproxy option, modify_body,
over mitmweb's web API right after `beanjar start`, so the reply names the version that is installed.

Why that reply: /usr/sbin/update (33.31.61, W22O) compares the offered MAJOR/MINOR with the installed ones
(strncmp, 2 and 5 characters); an offer that is not higher logs "NOTE: Not Found Image Update" and creates no
alert.  Any RESULT_CD other than exactly 900 is an error branch of the same function, also without an alert,
but "no new version" is what LG's own flow ends in, so the reply keeps 900.  The reply's body is base64 text
(LG's protocol), so the rule replaces the whole body; the version comes from the firmware name Beanjar reports.
Nothing is written on the TV, and only CheckSWAutoUpdate.laf is touched.
"""
import base64
import contextlib
import json
import re
import subprocess
import urllib.error
import urllib.request

CHECK = 'CheckSWAutoUpdate.laf'
XSRF = '0123456789abcdef0123456789abcdef'  # mitmweb takes any token that is both cookie and header


def installed_version(fw):
    """(major, minor) of a firmware directory name: '33.31.61.01-HE_DTV_W22O_AFABATPU' -> ('33', '31.61'); None
    when the name does not start with four dotted numbers."""
    m = re.match(r'(\d+)\.(\d+)\.(\d+)\.\d+', fw or '')
    return (m.group(1), '%s.%s' % (m.group(2), m.group(3))) if m else None


def no_update_reply(fw):
    """The XML of an update reply that offers the installed version (what the daemon reads from the 900 reply
    of LG's server, scratch/update-popup/CheckSWAutoUpdate-reply-900.xml, with the version equal and no force)."""
    major, minor = installed_version(fw)
    name = '%s.epk' % fw
    return ('<RESPONSE><RESULT_CD>900</RESULT_CD><MSG>Success</MSG><REQ_ID>00000000000000000000</REQ_ID>'
            '<IMAGE_URL>http://snu.lge.comSWDownload.laf</IMAGE_URL><IMAGE_SIZE>1</IMAGE_SIZE>'
            '<IMAGE_NAME>%s</IMAGE_NAME><UPDATE_MAJOR_VER>%s</UPDATE_MAJOR_VER><UPDATE_MINOR_VER>%s</UPDATE_MINOR_VER>'
            '<FORCE_FLAG>N</FORCE_FLAG><KE></KE><ECO_INFO>01</ECO_INFO><CONTENTS></CONTENTS>'
            '<PLATFORM_VERSION></PLATFORM_VERSION></RESPONSE>' % (name, major, minor)).encode()


def modify_body_spec(fw):
    """The mitmproxy modify_body rule: `|filter|regex|replacement`, applied to the response of the check only
    (`~s`: not to the request), replacing the whole (base64) body."""
    body = base64.b64encode(no_update_reply(fw)).decode()
    return '|~s & ~u %s|(?s)\\A.*\\Z|%s' % (re.escape(CHECK), body)


def connect_command(ref):
    return ['beanjar', 'mitm', 'connect', ref]


class Inspector:
    """mitmweb's web API of one VM, over `beanjar mitm connect` (a forward that lives until stdin ends)."""

    def __init__(self, ref, run=subprocess.Popen):
        self.proc = run(connect_command(ref), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            info = json.loads(self.proc.stdout.readline())
            self.base, self.password = info['url'], info['password']
        except (ValueError, KeyError):
            self.close()
            raise RuntimeError('beanjar mitm connect %s gave no {url, password} line' % ref)

    def close(self):
        with contextlib.suppress(Exception):
            self.proc.stdin.close()
            self.proc.wait(timeout=20)

    def request(self, method, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(
            self.base.rstrip('/') + path, data=data, method=method,
            headers={'Authorization': 'Bearer ' + self.password, 'X-XSRFToken': XSRF,
                     'Cookie': '_mitmproxy_xsrf=' + XSRF, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read()

    def set_option(self, name, value):
        self.request('PUT', '/options', {name: value})

    def option(self, name):
        return json.loads(self.request('GET', '/options'))[name]['value']

    def checks(self):
        """[(timestamp, status, content length)] of the update checks seen so far."""
        flows = json.loads(self.request('GET', '/flows'))
        return [(f['request']['timestamp_start'], (f.get('response') or {}).get('status_code'),
                 (f.get('response') or {}).get('contentLength'))
                for f in flows if CHECK in (f.get('request') or {}).get('path', '')]


def turn_off_update_offer(host, vm, say, opener=Inspector):
    """After `beanjar start`: have the VM's mitmweb answer the update check with the installed version.
    Returns True when the rule is set.  Says (never raises) what happened, including a check that was answered
    before the rule was in."""
    fw = host.info(vm).get('fw', '').rstrip('/').rsplit('/', 1)[-1]
    if not installed_version(fw):
        say('update offer: no firmware version in %r, the update check is left alone' % fw)
        return False
    spec = modify_body_spec(fw)
    ref = host.remote_target(vm)
    try:
        insp = opener(ref)
    except (OSError, RuntimeError) as e:
        say('update offer: cannot reach the mitmweb of %s: %s' % (ref, e))
        return False
    try:
        insp.set_option('modify_body', [spec])
        have = insp.option('modify_body')
        if have != [spec]:
            say('update offer: mitmweb of %s did not keep the rule (modify_body = %r)' % (ref, have))
            return False
        say('update offer: mitmweb of %s modify_body set; the CheckSWAutoUpdate.laf reply now offers the '
            'installed version %s.%s (rule %s...)' % (ref, *installed_version(fw), spec[:40]))
        early = insp.checks()
        if early:
            say('update offer: RACE LOST - the update check was already answered by LG before the rule '
                '(flows at %s); the popup may still come on this boot'
                % ', '.join('%.1f status=%s' % (t, s) for t, s, _ in early))
        return True
    except (OSError, ValueError, KeyError) as e:
        say('update offer: setting the rule on %s failed: %s' % (ref, e))
        return False
    finally:
        insp.close()
