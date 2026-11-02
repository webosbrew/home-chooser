# SPDX-License-Identifier: Apache-2.0
"""Launchers and ipks: the fixtures, building them, the demo, and the Home Chooser builds."""
import glob
import io
import json
import os
import re
import shlex
import subprocess
import tarfile
import tempfile

from . import timing
from .api_spec import API_FIXTURE_DIR, API_GROUP, DISPOSABLE_ID
from .constants import HERE, REPO

# ---------------------------------------------------------------------------------------------
# Launchers under test: one of the six fixtures in e2e/fixtures ({native,qml,web} x
# {normal,overlay}), or any app id with --launcher-ipk.
# ---------------------------------------------------------------------------------------------
# web first: `--launchers all-fixtures` runs them in this order, and the first launcher of a run makes the
# per-release checks, --api-check included, which needs a launcher that declares the permission
FIXTURE_TYPES = ('web', 'native', 'qml')
FIXTURE_WINDOWS = ('normal', 'overlay')
FIXTURE_DIR = os.path.join(REPO, 'build', 'fixtures')
FIXTURE_ID = 'org.webosbrew.hcfixture.%s.%s'
# fixture:web.demo is examples/web-launcher, packaged by fixtures/web/build.sh: unlike the six it uses the
# launcher API, so it always declares the permission, and the runner drives it (step 3d).
DEMO_SPEC = 'fixture:web.demo'
DEMO_ID = FIXTURE_ID % ('web', 'demo')
# Step 3d opens this app, built by fixtures/disposable/build.sh with the disposable app: a plain page whose
# title sorts before the TV's own titles in the demo's order, so OK on the demo's first tile opens it and not
# an LG app that LG's first-use or EULA overlay holds back.
DEMO_TARGET_ID = 'org.webosbrew.hcfixture.first'
DEMO_TARGET_TITLE = '0 HC Fixture first'


def demo_ascii_lower(text):
    return re.sub('[A-Z]', lambda m: m.group(0).lower(), text)


def demo_order(apps, self_id):
    """The demo's grid order (orderApps in examples/web-launcher/launcher.js): without itself, without
    visible:false and id-less entries, by title (the id when there is none) ignoring ASCII case, then by
    id; strings compare by UTF-16 code unit as in JavaScript."""
    def key(app):
        title = app.get('title') or app['id']
        return (demo_ascii_lower(title).encode('utf-16-be'), app['id'].encode('utf-16-be'))
    shown = [a for a in apps if isinstance(a, dict) and isinstance(a.get('id'), str) and a['id']
             and a['id'] != self_id and a.get('visible') is not False]
    return sorted(shown, key=key)


def demo_focus(reply, self_id):
    """The app the demo focuses after a fresh start: the first of its order, from a listApps reply."""
    if not isinstance(reply, dict) or reply.get('returnValue') is not True or not isinstance(reply.get('apps'), list):
        return None
    order = demo_order(reply['apps'], self_id)
    return order[0]['id'] if order else None


def ipk_files(path):
    """{member name: bytes} of the data.tar.gz inside an ipk (an ar archive; stdlib only)."""
    raw = open(path, 'rb').read()
    if raw[:8] != b'!<arch>\n':
        raise ValueError('%s is not an ar archive' % path)
    pos, data = 8, None
    while pos + 60 <= len(raw):
        name = raw[pos:pos + 16].decode().strip().rstrip('/')
        size = int(raw[pos + 48:pos + 58].decode().strip())
        body = raw[pos + 60:pos + 60 + size]
        if name == 'data.tar.gz':
            data = body
        pos += 60 + size + (size & 1)
    if data is None:
        raise ValueError('no data.tar.gz in %s' % path)
    out = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as t:
        for m in t.getmembers():
            if m.isfile():
                out[m.name.lstrip('./')] = t.extractfile(m).read()
    return out


def ipk_appinfo(path):
    for name, body in ipk_files(path).items():
        if name.endswith('/appinfo.json') and name.startswith('usr/palm/applications/'):
            return json.loads(body.decode('utf-8'))
    raise ValueError('no appinfo.json in %s' % path)


class Launcher:
    """id, ipk, and what the appinfo inside the ipk declares."""

    def __init__(self, spec, ident, ipk, kind='app'):
        self.spec, self.id, self.ipk, self.kind = spec, ident, ipk, kind
        self.appinfo = ipk_appinfo(ipk) if ipk and os.path.exists(ipk) else {}
        self.overlay = self.appinfo.get('defaultWindowType') == 'overlay'
        self.title = self.appinfo.get('title', ident)
        self.tag = spec.replace(':', '-')
        parts = ident.split('.')
        self.fixture = (parts[-2], parts[-1]) if kind == 'fixture' else None  # (type, window)

    @property
    def is_fixture(self):
        return self.kind == 'fixture'

    @property
    def is_demo(self):
        return self.spec == DEMO_SPEC


def fixture_specs():
    return ['fixture:%s.%s' % (t, w) for t in FIXTURE_TYPES for w in FIXTURE_WINDOWS]


def api_fixture_ipks(announce=print):
    """--api-check: the web fixtures built with requiredPermissions ["org.webosbrew.homechooser.applications"] and the
    disposable non-home app, in build/fixtures-api/ (offline, built once).  {app id: ipk}."""
    def found():
        out = {}
        for ident in [FIXTURE_ID % ('web', w) for w in FIXTURE_WINDOWS] + [DISPOSABLE_ID]:
            g = sorted(glob.glob(os.path.join(API_FIXTURE_DIR, ident + '_*.ipk')))
            info = ipk_appinfo(g[-1]) if g and ident != DISPOSABLE_ID else {}
            if g and (ident == DISPOSABLE_ID or (
                    API_GROUP in info.get('requiredPermissions', [])
                    and info.get('largeIcon') == 'oversize.png')):
                out[ident] = g[-1]
        return out
    have = found()
    if len(have) < len(FIXTURE_WINDOWS) + 1:
        announce('API fixtures missing: building the web fixtures with %s and the disposable app into %s (offline)'
                 % (API_GROUP, API_FIXTURE_DIR))
        os.makedirs(API_FIXTURE_DIR, exist_ok=True)
        env = dict(os.environ, HC_FIXTURE_PERMISSION=API_GROUP)
        for script in ('web', 'disposable'):
            try:
                p = subprocess.run(['sh', os.path.join(HERE, 'fixtures', script, 'build.sh'), API_FIXTURE_DIR],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600, env=env)
                announce(p.stdout.decode('utf-8', 'replace').strip()[-400:])
            except subprocess.TimeoutExpired:
                announce('%s/build.sh timed out' % script)
        have = found()
    if len(have) < len(FIXTURE_WINDOWS) + 1:
        raise SystemExit('could not build the API fixtures in %s' % API_FIXTURE_DIR)
    return have


def demo_target_ipk(announce=print):
    """fixture:web.demo: the ipk of DEMO_TARGET_ID, built with the disposable app into build/fixtures-api/."""
    def found():
        g = sorted(glob.glob(os.path.join(API_FIXTURE_DIR, DEMO_TARGET_ID + '_*.ipk')))
        return g[-1] if g and ipk_appinfo(g[-1]).get('title') == DEMO_TARGET_TITLE else None
    ipk = found()
    if not ipk:
        announce('demo target missing: building %s with fixtures/disposable/build.sh into %s (offline)'
                 % (DEMO_TARGET_ID, API_FIXTURE_DIR))
        os.makedirs(API_FIXTURE_DIR, exist_ok=True)
        rc, out = timing.run(['sh', os.path.join(HERE, 'fixtures', 'disposable', 'build.sh'), API_FIXTURE_DIR], 600)
        announce(out.strip()[-400:])
        ipk = found()
    if not ipk:
        raise SystemExit('could not build %s in %s' % (DEMO_TARGET_ID, API_FIXTURE_DIR))
    return ipk


def make_launcher(spec, ipk_override=None, announce=print, api=False):
    """api (--api-check): a web fixture comes from the build with the permission; native and qml
    fixtures cannot declare it."""
    if spec.startswith('fixture:'):
        t, _, w = spec[8:].partition('.')
        if spec != DEMO_SPEC and (t not in FIXTURE_TYPES or w not in FIXTURE_WINDOWS):
            raise SystemExit('unknown fixture %r; use fixture:<native|qml|web>.<normal|overlay> or %s' % (spec, DEMO_SPEC))
        ident = FIXTURE_ID % (t, w)
        if api and spec != DEMO_SPEC:  # the demo always declares the permission: the plain build serves
            if t != 'web':
                raise SystemExit('--api-check needs a launcher that declares %s: fixture:web.<normal|overlay>, '
                                 'or your own launcher with --launcher-ipk' % API_GROUP)
            return Launcher(spec, ident, api_fixture_ipks(announce)[ident], 'fixture')
        found = sorted(glob.glob(os.path.join(FIXTURE_DIR, ident + '_*.ipk')))
        if not found:
            announce('fixture ipk for %s missing: running e2e/fixtures/build.sh (offline)' % ident)
            rc, out = timing.run(['sh', os.path.join(HERE, 'fixtures', 'build.sh')], 600)
            announce(out.strip()[-400:])
            found = sorted(glob.glob(os.path.join(FIXTURE_DIR, ident + '_*.ipk')))
        if not found:
            raise SystemExit('could not build the fixture ipk for %s' % ident)
        return Launcher(spec, ident, found[-1], 'fixture')
    if not ipk_override:
        raise SystemExit('--launcher %s needs --launcher-ipk' % spec)
    return Launcher(spec, spec, ipk_override, 'app')


def qml_support(major):
    """(supported, evidence) from e2e/fixtures/qml/support.json for a release; True/False/'unknown'."""
    try:
        d = json.load(open(os.path.join(HERE, 'fixtures', 'qml', 'support.json')))
        e = d.get(str(major), {})
        return e.get('supported', 'unknown'), e.get('evidence', 'no entry')
    except (OSError, ValueError):
        return 'unknown', 'support.json unreadable'


def api_build(spec, index, api):
    """--api-check: the first launcher of a run (it makes the API calls) and every web fixture come from the
    build with the permission; a later native or qml launcher is the plain fixture."""
    return bool(api) and (index == 0 or spec.startswith('fixture:web.'))


def default_update_ref():
    """The build a user updates from: the last published one (origin/main) when HEAD's app/ or
    service/ differ from it, so an unpublished intermediate commit (which may carry a bug fixed
    since) is never the old build.  Otherwise the previous commit that changed app/ or service/:
    the last one before HEAD, unless those paths have uncommitted changes (then HEAD is the build
    the working tree departs from)."""
    rc, dirty = timing.run(['git', '-C', REPO, 'status', '--porcelain', '--', 'app', 'service'], 30)
    if dirty.strip():
        return 'HEAD'
    rc, published = timing.run(['git', '-C', REPO, 'rev-parse', '--verify', '--quiet', 'origin/main'], 30)
    if rc == 0 and published.strip():
        rc, _ = timing.run(['git', '-C', REPO, 'diff', '--quiet', published.strip(), 'HEAD', '--', 'app', 'service'], 30)
        if rc == 1:
            return published.strip()
    rc, out = timing.run(['git', '-C', REPO, 'log', '-1', '--format=%H', 'HEAD~1', '--', 'app', 'service'], 30)
    ref = out.strip()
    if rc != 0 or not ref:
        raise SystemExit('cannot find a previous commit touching app/ or service/; use --update-from')
    return ref


def build_old_home_chooser(ref, rec):
    """Build Home Chooser from a git ref in a temp dir: `git archive` extracted under /tmp (the main
    working tree and .git are not touched), node_modules linked from the main tree.
    Returns (ipk path, temp dir)."""
    tmp = tempfile.mkdtemp(prefix='hc-e2e-old-')
    rc, out = timing.run(['sh', '-c', 'git -C %s archive %s | tar -x -C %s' % (shlex.quote(REPO), shlex.quote(ref), shlex.quote(tmp))], 120)
    rec.check('git archive %s into %s' % (ref, tmp), rc == 0, out[-300:])
    if rc != 0:
        raise RuntimeError('git archive failed')
    os.makedirs(os.path.join(tmp, 'build', 'ipk'), exist_ok=True)
    os.symlink(os.path.join(REPO, 'app', 'node_modules'), os.path.join(tmp, 'app', 'node_modules'))
    rec.say('building the old Home Chooser (%s): npm run package in %s/app' % (ref, tmp))
    rc, out = timing.run(['sh', '-c', 'cd %s/app && npm run package' % shlex.quote(tmp)], 1200)
    ipks = sorted(glob.glob(os.path.join(tmp, 'build', 'ipk', '*.ipk')))
    rec.check('old Home Chooser build (%s) produced an ipk' % ref, rc == 0 and bool(ipks), out[-300:])
    if rc != 0 or not ipks:
        raise RuntimeError('old build failed')
    return ipks[-1], tmp


def current_home_chooser_ipk(rec, build=False):
    ipks = sorted(glob.glob(os.path.join(REPO, 'build/ipk/*.ipk')))
    if build or not ipks:
        rec.say('building Home Chooser: npm run package')
        rc, out = timing.run(['sh', '-c', 'cd %s/app && npm run package' % shlex.quote(REPO)], 900)
        rec.check('npm run package', rc == 0, out[-300:])
        ipks = sorted(glob.glob(os.path.join(REPO, 'build/ipk/*.ipk')))
    return ipks[-1]
