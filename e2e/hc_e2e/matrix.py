# SPDX-License-Identifier: Apache-2.0
"""The release matrix and the Beanjar hosts file."""
import json
import os

from .constants import HERE

# ---------------------------------------------------------------------------------------------
# The matrix: one table.  "sure" says how far the VM/release pairing was checked.
#   verified  = booted and os-release read by a run of this runner
#   docs      = the firmware is named for that release in docs/research/home-key-and-home-app.md
#   listing   = only `beanjar list` (platform column, firmware string); os-release not read
# elevation: luna = Homebrew Channel's luna elevateService (the app does it); root-shell = the
# runner runs Homebrew Channel's elevate-service helper as root (webOS 11.x/12.x, where
# Homebrew Channel refuses `elevateService` from apps: README.md "Decisions").
# ---------------------------------------------------------------------------------------------
MATRIX = [
    # release, vm, elevation, sure, note
    ('5.x', 'w20l-jhericurl', 'luna', 'verified', 'W20L 04.63.25.01 = 5.6.1; os-release read 2026-10-22; w20h-jhericurl is also webOS 5'),
    ('6.x', 'w21a-kisscurl', 'luna', 'verified', 'W21A 03.53.25.01 = 6.5.2; os-release read 2026-10-23 (w21p-kisscurl, 6.6.0, belongs to another project and is not in the matrix)'),
    ('9.x', 'w24g-ombre', 'luna', 'verified', 'W24G 33.20.23.01 = 9.2.4; os-release read 2026-10-23'),
    ('9.x', 'w24p-ombre', 'luna', 'docs', 'W24P 33.20.23.01 = 9.2.4-4603 per firmware metadata; os-release to be read'),
    ('7.x', 'w22p-mullet', 'luna', 'verified', 'W22P 04.60.90.01 = 7.6.0; os-release read 2026-10-22'),
    ('8.x', 'w22o-number1', 'luna', 'verified', 'W22O 13.30.56.01 = 8.3.0; os-release read 2026-10-22 (beanjar lists it as "webOS 23", a platform label)'),
    ('9.x', 'w22h-ombre', 'luna', 'verified', 'W22H 23.21.20.01 = 9.2.1; os-release read'),
    ('10.x', 'w22o-ponytail-us', 'luna', 'verified', 'W22O 33.31.61.01 = 10.3.1; os-release read 2026-10-22'),
    ('11.x', 'w22o-queue-us', 'root-shell', 'verified', 'W22O 43.21.89.01 = 11.2.0; os-release read 2026-10-22; w22o-queue and w22o-queue-jp run the same firmware'),
    ('12.x', 'w24g-spacebuns', 'root-shell', 'verified', 'W24G 02.68.80.01 = 12.0.0; os-release read 2026-10-22'),
]
KEEP_LAUNCHER_VMS = {'w21p-kisscurl'}


def candidates(vm):
    """A slot accepts a VM name or an ordered tuple of alternative VM names."""
    return (vm,) if isinstance(vm, str) else tuple(vm)


def entry(release_or_vm):
    for m in MATRIX:
        if release_or_vm == m[0] or release_or_vm in candidates(m[1]):
            vm = release_or_vm if release_or_vm != m[0] else candidates(m[1])[-1]
            return (m[0], vm, *m[2:])
    raise SystemExit('unknown VM/release %r; see the matrix in e2e/README.md' % release_or_vm)


# ---------------------------------------------------------------------------------------------
# Beanjar machines: e2e/hosts.toml, then the git-ignored e2e/hosts.local.toml over it.  Without
# either file the one machine is today's: `pi`, reached with `ssh raspberrypi`.
# ---------------------------------------------------------------------------------------------
HOSTS_FILE = os.path.join(HERE, 'hosts.toml')
HOSTS_LOCAL_FILE = os.path.join(HERE, 'hosts.local.toml')
DEFAULT_HOSTS = {'default': 'pi', 'hosts': {'pi': {'ssh': 'raspberrypi'}}}


def load_hosts(path=None):
    """{'default': name, 'hosts': {name: {'ssh': alias} or {'local': True}, optional 'vms': [...]}, optional
    'default_slot'}.  path: one file instead of hosts.toml + hosts.local.toml."""
    import tomllib
    files = [path] if path else [f for f in (HOSTS_FILE, HOSTS_LOCAL_FILE) if os.path.exists(f)]
    if not files:
        return json.loads(json.dumps(DEFAULT_HOSTS))
    cfg = {'default': None, 'hosts': {}}
    for f in files:
        with open(f, 'rb') as fh:
            d = tomllib.load(fh)
        cfg['default'] = d.get('default', cfg['default'])
        if 'default_slot' in d:  # the smoke tier's VM (run_matrix.py --tier smoke): a VM name or a release
            cfg['default_slot'] = d['default_slot']
        for name, h in d.get('hosts', {}).items():
            cfg['hosts'].setdefault(name, {}).update(h)
    for name, h in cfg['hosts'].items():
        import math
        if not math.isfinite(float(h.get('slowdown', 1.0))) or float(h.get('slowdown', 1.0)) < 1:
            raise SystemExit('host %s slowdown must be finite and >= 1.0' % name)
        if not h.get('local') and not h.get('ssh'):
            raise SystemExit('host %s in %s has neither ssh = "<alias>" nor local = true' % (name, ', '.join(files)))
    cfg['default'] = cfg['default'] or next(iter(cfg['hosts']), None)
    return cfg
