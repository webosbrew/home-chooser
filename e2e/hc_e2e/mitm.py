# SPDX-License-Identifier: Apache-2.0
"""LG's software-update offer and country answer, rewritten through the VM's own mitmweb.

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

The second rule is for the country popup.  At boot the TV's sdx service POSTs `/init/<ver>/initservices`; the
reply carries `"country":{"code":"JP","ricCode":"AIC","threecode":"JPN"}`, which LG's server derives from the
public address of the machine running the VM (a Raspberry Pi in Japan), whatever country the TV is set to.  sdx
sees a country other than the TV's own and raises "Changing the country will cause your TV to restart ...
Continue?", which takes key focus.  The rule rewrites `code` and `threecode` of that reply to the TV's own
country and leaves `ricCode` (LG's regional server cluster, not a country) and everything else as sent.
mitmproxy's modify_body cannot use request data, and the request carries the country only in headers
(X-Device-Country), so the TV's country is read once from the TV's own settings (read-only luna) and kept per
VM in e2e/results/country/; a VM never read yet gets no country rule, and that is logged.
Facts: scratch/country-popup/facts.md.
"""
import base64
import contextlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.request

CHECK = 'CheckSWAutoUpdate.laf'
INIT = 'initservices'
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


def country_specs(code2, code3):
    """The two modify_body rules that give the initservices reply the TV's own country: only `code` and
    `threecode` inside the reply's country object change (lookbehind/lookahead keep every other byte, ricCode
    included), only in the response of initservices.  None for a country that is not two and three capitals."""
    if not (re.fullmatch(r'[A-Z]{2}', code2 or '') and re.fullmatch(r'[A-Z]{3}', code3 or '')):
        return None
    flt = '~s & ~u %s' % re.escape(INIT)
    return ['|%s|%s|%s' % (flt, r'(?<="country":\{"code":")[A-Z]{2}(?=")', code2),
            '|%s|%s|%s' % (flt, r'(?<="threecode":")[A-Z]{3}(?="\})', code3)]


COUNTRY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'results', 'country')


def country_file(host, vm, base=None):
    return os.path.join(base or COUNTRY_DIR, '%s-%s.json' % (host, vm))


def load_country(host, vm, base=None):
    """(code2, code3) kept for this VM, or None."""
    try:
        with open(country_file(host, vm, base)) as f:
            d = json.load(f)
        return (d['code2'], d['code3']) if country_specs(d['code2'], d['code3']) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_country(host, vm, code2, code3, base=None):
    path = country_file(host, vm, base)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump({'code2': code2, 'code3': code3}, f)


def read_country(dev):
    """(code2, code3) of the running TV from its own settings (read-only getSystemSettings), or None."""
    out = dev.luna('luna://com.webos.settingsservice/getSystemSettings',
                   '{"category":"option","keys":["smartServiceCountryCode2","smartServiceCountryCode3"]}')
    try:
        s = json.loads(out)['settings']
        c2, c3 = s['smartServiceCountryCode2'], s['smartServiceCountryCode3']
    except (ValueError, KeyError, TypeError):
        return None
    return (c2, c3) if country_specs(c2, c3) else None


def learn_country(host, vm, dev, say):
    """After a boot is up: keep the TV's country for the next boots of this VM (a changed value is replaced)."""
    got = read_country(dev)
    if not got:
        say('country reply: the TV reported no usable country (smartServiceCountryCode2/3), nothing kept')
        return None
    if load_country(host.name, vm) != got:
        save_country(host.name, vm, *got)
        say('country reply: kept %s/%s for %s/%s (the next boots get it in the initservices reply)' % (*got, host.name, vm))
    return got


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


def turn_off_update_offer(host, vm, say, opener=Inspector, update=True, country=True):
    """After `beanjar start`: have the VM's mitmweb rewrite, in one modify_body list, the update check (the
    installed version) and the initservices country (the TV's own).  Returns True when the rules are set.
    Says (never raises) what happened, including a check that was answered before the rules were in."""
    specs, what = [], []
    if update:
        fw = host.info(vm).get('fw', '').rstrip('/').rsplit('/', 1)[-1]
        if installed_version(fw):
            specs.append(modify_body_spec(fw))
            what.append('the CheckSWAutoUpdate.laf reply offers the installed version %s.%s' % installed_version(fw))
        else:
            say('update offer: no firmware version in %r, the update check is left alone' % fw)
    if country:
        own = load_country(host.name, vm)
        if own:
            specs += country_specs(*own)
            what.append('the initservices reply carries country %s/%s' % own)
        else:
            say('country reply: the country of %s/%s is not known yet (read from the TV after this boot), '
                'the initservices reply is left as LG sent it' % (host.name, vm))
    if not specs:
        return False
    ref = host.remote_target(vm)
    try:
        insp = opener(ref)
    except (OSError, RuntimeError) as e:
        say('mitm rules: cannot reach the mitmweb of %s: %s' % (ref, e))
        return False
    try:
        insp.set_option('modify_body', specs)
        have = insp.option('modify_body')
        if have != specs:
            say('mitm rules: mitmweb of %s did not keep the rules (modify_body = %r)' % (ref, have))
            return False
        say('mitm rules: mitmweb of %s modify_body set; %s (%d rules)' % (ref, '; '.join(what), len(specs)))
        early = insp.checks() if update else []
        if early:
            say('update offer: RACE LOST - the update check was already answered by LG before the rule '
                '(flows at %s); the popup may still come on this boot'
                % ', '.join('%.1f status=%s' % (t, s) for t, s, _ in early))
        return True
    except (OSError, ValueError, KeyError) as e:
        say('mitm rules: setting the rules on %s failed: %s' % (ref, e))
        return False
    finally:
        insp.close()
