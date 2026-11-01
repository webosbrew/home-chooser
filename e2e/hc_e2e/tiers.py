# SPDX-License-Identifier: Apache-2.0
"""The test tiers (e2e/README.md "Tiers"): the defaults of a pass, for run_matrix.py and the pytest device
tests (e2e/device), and the plan a run gets from them without building anything."""
import os

from .launchers import DEMO_SPEC, fixture_specs, ipk_appinfo
from .plan import run_plan

# The tiers (e2e/README.md "Tiers").  Each sets the defaults of the options below; an option given on the
# command line wins.  vms: None = every MATRIX slot, 'default' = hosts.toml's default_slot.
# update_on: the slots that get --update-check ('all', or VM names / releases).
UPDATE_SLOT = 'w24g-ombre'  # release tier: one update check.  9.x carries the most update assertions (the
#                             live key filter, the flag and the baked id survive) and this slot is on the Pi,
#                             the fastest machine; the 10+ override and 11+ re-elevation run in the full tier.
FULL_LAUNCHERS = 'all-fixtures'  # web first (launchers.FIXTURE_TYPES): the first launcher makes the API calls
TIERS = {
    'smoke': {'vms': 'default', 'launchers': 'fixture:web.normal', 'api_check': 'smoke', 'reboot_check': None,
              'update_on': None, 'uninstall_via': None, 'launcher_update_check': False},
    'release': {'vms': None, 'launchers': 'fixture:web.normal,fixture:web.overlay', 'api_check': 'smoke',
                'reboot_check': 'auto', 'update_on': UPDATE_SLOT, 'uninstall_via': 'alert',
                'launcher_update_check': False},
    'full': {'vms': None, 'launchers': FULL_LAUNCHERS, 'api_check': 'full', 'reboot_check': 'auto',
             'update_on': 'all', 'uninstall_via': 'alert', 'launcher_update_check': True},
}
DEFAULT_SLOT = '9.x'  # without default_slot in hosts.toml


def apply_tier(args, cfg):
    """Fill every option the command line left unset from the tier's row; without --tier nothing changes."""
    if not args.tier:
        if args.update_check and args.update_on is None:
            args.update_on = 'all'
        return args
    row = TIERS[args.tier]
    if args.vms is None and row['vms'] == 'default':
        args.vms = cfg.get('default_slot') or DEFAULT_SLOT
    if args.launcher is None and args.launchers is None:
        args.launchers = row['launchers']
    for key in ('api_check', 'reboot_check', 'uninstall_via'):
        if getattr(args, key) is None:
            setattr(args, key, row[key])
    if args.update_on is None:
        args.update_on = 'all' if args.update_check else row['update_on']
    args.update_check = bool(args.update_on) and args.update_on != 'none'
    args.launcher_update_check = args.launcher_update_check or row['launcher_update_check']
    return args


def updates(run, update_on):
    """Whether this run gets --update-check: update_on is 'all', 'none'/None, or a comma list of VM names
    and releases."""
    if not update_on or update_on == 'none':
        return False
    names = [x for x in update_on.split(',') if x]
    return 'all' in names or run['vm'] in names or run['release'] in names


def spec_shape(spec, ipk=None):
    """launcher_shape of a --launcher value without building it: a fixture by its name, an author launcher
    by its ipk's appinfo (normal when it cannot be read)."""
    if spec.startswith('fixture:'):
        t, _, w = spec[8:].partition('.')
        return (t, 'normal', True) if spec == DEMO_SPEC else (t, w, False)
    info = ipk_appinfo(ipk) if ipk and os.path.exists(ipk) else {}
    return 'app', 'overlay' if info.get('defaultWindowType') == 'overlay' else 'normal', False


def launcher_specs(args):
    return [s for s in ((fixture_specs() if args.launchers == 'all-fixtures' else args.launchers.split(','))
                        if args.launchers else [args.launcher or 'fixture:web.normal']) if s]


def run_plan_for(run, args):
    """The runner's plan for one run (plan.run_plan), from the slot's release: for --dry-run."""
    specs = launcher_specs(args)
    shapes = [spec_shape(s, args.launcher_ipk if len(specs) == 1 else None) for s in specs]
    major = int(run['release'].split('.')[0])
    return run_plan(shapes, major, {
        'api': args.api_check, 'reboot': args.reboot_check, 'update': updates(run, args.update_on),
        'launcher_update': args.launcher_update_check, 'until': 5})
