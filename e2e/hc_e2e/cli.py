# SPDX-License-Identifier: Apache-2.0
"""The command line of homechooser_e2e.py: options, the VM bring-up, the run, and the teardown."""
import argparse
import os
import shutil
import signal
import time

from . import timing
from .api_spec import DISPOSABLE_ID
from .beanjar import BeanjarHost
from .constants import HERE, REPO
from .device import Device, ELEVATIONS
from .device_api import run_device_api
from .launchers import (DEMO_SPEC, api_build, api_fixture_ipks, build_old_home_chooser, current_home_chooser_ipk,
    default_update_ref, demo_target_ipk, fixture_specs, ipk_appinfo, make_launcher)
from .matrix import KEEP_LAUNCHER_VMS, MATRIX, candidates, entry, load_hosts
from .preflight import (NO_ROOT_SSH, READY, WAIT_STATES, clean_launchers, failsafe_note, prepare_vm,
    restart_after_launcher_cleanup, wait_for_device)
from .recorder import Recorder
from .scenario import Scenario


def interrupted(*_):
    raise KeyboardInterrupt


def main():
    ap = argparse.ArgumentParser(description='Home Chooser E2E on a Beanjar VM or API contract on an ares TV')
    ap.add_argument('--scenario', choices=['lifecycle', 'screensaver', 'all'], default='lifecycle',
                    help='Home-key lifecycle (default), home and screensaver alone, or both')
    ap.add_argument('--saver-start', choices=['tvpower', 'beanjar'], default='tvpower',
                    help='screensaver trigger: root tvpower firmware call (default), or bounded Beanjar helper')
    ap.add_argument('--vm', help='a VM name or a release (e.g. w22h-ombre or 9.x)')
    ap.add_argument('--device', help='an existing root-capable ares device name; run --api-check without Beanjar or reboot')
    ap.add_argument('--launcher', default=None,
                    help="'fixture:<native|qml|web>.<normal|overlay>' (default fixture:web.normal), "
                         "'fixture:web.demo' (examples/web-launcher; adds step 3d: OK opens the focused app), "
                         "or an app id with --launcher-ipk (an author's own launcher)")
    ap.add_argument('--launchers', default=None,
                    help="'all-fixtures' or a comma list of --launcher values, all on one boot: the first runs the whole "
                         "lifecycle, each later one is switched to through Home Chooser and runs only its scopes "
                         "(window type, launch proof; e2e/README.md \"Scopes\")")
    ap.add_argument('--launcher-ipk', default=None, help='ipk of --launcher when it is an app id')
    ap.add_argument('--launcher-update-check', action='store_true',
                    help='after selection install a newer version of the same launcher id and verify Home stays selected')
    ap.add_argument('--launcher-update-ipk', default=None,
                    help='updated launcher ipk for --launcher-update-check (web fixture builds version 1.0.1 automatically)')
    ap.add_argument('--restore-repair-check', action='store_true',
                    help='after selection stop the watcher, uninstall, model a partial restore, and verify reinstall repairs Home')
    ap.add_argument('--update-check', action='store_true',
                    help='install an older Home Chooser first, choose the launcher, then install the current build over it '
                         '(step 3u) and check that everything survives')
    ap.add_argument('--update-from', default=None,
                    help='git ref of the older Home Chooser (default: the previous commit that changed app/ or service/)')
    ap.add_argument('--host', default=None,
                    help="the Beanjar machine the VM is on, a name in e2e/hosts.toml (default: the file's default, 'pi')")
    ap.add_argument('--hosts-file', default=None, help='a hosts file instead of e2e/hosts.toml + e2e/hosts.local.toml')
    ap.add_argument('--host-mode', choices=['local', 'remote'], default=None,
                    help="override the host's mode: beanjar run here, or over ssh")
    ap.add_argument('--host-device', default=None, help="override the host's ssh target (an ssh config alias)")
    ap.add_argument('--hc-ipk', default=None, help='the Home Chooser ipk to test (default: the newest build/ipk/*.ipk)')
    ap.add_argument('--old-ipk', default=None, help='--update-check: the older Home Chooser ipk, already built (skips the build)')
    ap.add_argument('--outdir', default=None, help='the result directory (default e2e/results/<vm>-<timestamp>); must not exist')
    ap.add_argument('--elevation', choices=sorted(ELEVATIONS), default=None,
                    help="override the matrix's elevation strategy")
    ap.add_argument('--input', choices=['beanjar', 'qmp', 'ares'], default='beanjar',
                    help='remote-key path: Beanjar IR remote (default), QEMU keyboard, or ares-do key')
    ap.add_argument('--input-mode', choices=['click', 'keys'], default='click',
                    help='prefer screen elements and pointer clicks (default), or key navigation')
    ap.add_argument('--build', action='store_true', help='run npm run package first')
    ap.add_argument('--until', type=int, default=5, help='debugging: stop after this step and skip the cleanup')
    ap.add_argument('--wait-popup', type=int, default=0,
                    help='seconds to wait for a system popup holding key focus to be dismissed by hand')
    ap.add_argument('--reboot-check', nargs='?', const='auto', choices=['auto', 'all'], default=None,
                    help='reboot the VM (beanjar stop/start) with a launcher chosen and check what the watcher '
                         're-applies at boot. auto (the bare flag): once with the full-screen launcher, plus once '
                         'with the overlay launcher on 10+ (the last input launch); all: once per window type')
    ap.add_argument('--reboot-via', choices=['later', 'alert'], default='later',
                    help="webOS 6-9, the restart the first choice asks for (the service's system alert): close our alert "
                         "with closeAlert and restart the VM with beanjar stop/start (default; no key is pressed), or "
                         "'alert': automatically press Reboot after verifying the alert and focus from the screen; "
                         "fall back to a 240 s MANUAL wait if proof fails")
    ap.add_argument('--api-check', nargs='?', const='smoke', choices=['smoke', 'full'], default=None,
                    help='the launcher API (docs/launcher-api.md) as the launcher calls it, before and after it is '
                         'chosen (2a, 3a), after --reboot-check and after the uninstall; installs a disposable app. '
                         'smoke (the bare flag): getInfo, listApps and closeApp, reachability only (the host contract '
                         'test owns the methods); full: every method and errorCode, as before. '
                         'The launcher must declare org.webosbrew.homechooser.applications: the default is the web fixture built with it; '
                         '--launcher <id> --launcher-ipk <ipk> runs it against your own launcher')
    ap.add_argument('--uninstall-via', choices=['later', 'alert'], default='later',
                    help='with --api-check, automatically press Uninstall after proving our alert and focus; '
                         'fall back to a 240 s MANUAL wait if proof fails; default closes the alert')
    ap.add_argument('--no-stop', action='store_true', help='leave the VM running at the end')
    ap.add_argument('--list', action='store_true', help='print the matrix and exit')
    args = ap.parse_args()
    if args.list:
        for m in MATRIX:
            print('%-5s %-18s %-10s %-9s %s' % (m[0], ' | '.join(candidates(m[1])), *m[2:]))
        print('launchers: %s' % ', '.join(fixture_specs() + [DEMO_SPEC]))
        return 0
    if args.device:
        if args.scenario != 'lifecycle' or args.saver_start != 'tvpower':
            ap.error('--scenario screensaver/all and --saver-start beanjar require VM mode')
        return run_device_api(args, ap)
    if args.scenario != 'lifecycle' and (args.input != 'beanjar' or args.until != 5):
        ap.error('--scenario screensaver/all requires --input beanjar and the complete setup/cleanup')
    if not args.vm:
        ap.error('--vm or --device is required')
    row = entry(args.vm)
    vm = row[1]
    if args.launchers:
        specs = fixture_specs() if args.launchers == 'all-fixtures' else [x for x in args.launchers.split(',') if x]
    else:
        specs = [args.launcher or 'fixture:web.normal']
    args.multi = len(specs) > 1
    if args.launcher_update_check and not args.launcher_update_ipk and not specs[0].startswith('fixture:web.'):
        ap.error('--launcher-update-check needs --launcher-update-ipk unless the first launcher is a web fixture')
    if args.multi and vm in KEEP_LAUNCHER_VMS:
        ap.error('%s keeps its launcher installed; run one launcher at a time there' % vm)
    cfg = load_hosts(args.hosts_file)
    launchers = [make_launcher(sp, args.launcher_ipk if len(specs) == 1 else None,
                               lambda t: print(t, flush=True), api=api_build(sp, i, args.api_check))
                 for i, sp in enumerate(specs)]
    if args.scenario != 'lifecycle' and any(not launcher.is_fixture or launcher.overlay for launcher in launchers):
        ap.error('--scenario screensaver/all requires a normal fixture launcher')
    args.disposable_ipk = api_fixture_ipks(lambda t: print(t, flush=True))[DISPOSABLE_ID] if args.api_check else None
    args.demo_target_ipk = demo_target_ipk(lambda t: print(t, flush=True)) \
        if any(getattr(launcher, 'is_demo', False) for launcher in launchers) else None
    host = BeanjarHost.from_config(cfg, args.host or cfg['default'], args.host_mode, args.host_device)
    timing.SLOWDOWN = host.slowdown
    args.new_ipk, args.old_ref = args.hc_ipk, None
    outdir = args.outdir or os.path.join(HERE, 'results', '%s-%s' % (vm, time.strftime('%Y%m%d-%H%M%S')))
    os.makedirs(outdir)
    rec = Recorder(outdir)
    host.input_rec = rec if args.input == 'beanjar' or args.input_mode == 'click' else None
    rec.say('artefacts: ' + outdir)
    rec.say('host timing slowdown: %g' % host.slowdown)
    rec.say('Beanjar host: %s (%s%s)' % (host.name, host.mode, ', ssh ' + host.device if host.mode == 'remote' else ''))
    # the scheduler stops a run with SIGTERM or SIGINT; both unwind through the finally below, which stops the VM
    signal.signal(signal.SIGTERM, interrupted)
    started = False
    scenario = None
    old_tmp = None
    try:
        if not shutil.which('beanjar'):
            raise SystemExit('beanjar not on PATH (VMs are reached with beanjar ares-TOOL)')
        if not (shutil.which('ares-do') or os.path.exists(os.path.expanduser('~/.cargo/bin/ares-do'))):
            raise SystemExit('ares-do not on PATH or in ~/.cargo/bin')
        if args.update_check:
            args.old_ref = args.update_from or default_update_ref()
            rc, full = timing.run(['git', '-C', REPO, 'rev-parse', '--short', args.old_ref], 30)
            rec.say('--update-check: old build from git ref %s (%s)' % (args.old_ref, full.strip()))
            args.new_ipk = args.new_ipk or current_home_chooser_ipk(rec, args.build)
            if not args.old_ipk:
                args.old_ipk, old_tmp = build_old_home_chooser(args.old_ref, rec)
            for label, f in (('old', args.old_ipk), ('current', args.new_ipk)):
                rec.say('%s Home Chooser ipk %s version %s' % (label, f, ipk_appinfo(f).get('version')))
        prepare_vm(host, vm, rec)
        started = True  # before the start, so a start interrupted half-way is stopped too
        rc, out = host.start(vm)
        rec.say('beanjar start: %s' % out.strip()[-200:])
        rec.say('waiting for the VM to answer on its LAN address')
        dev = Device.for_vm(host, vm)
        dev.say = rec.say
        end = time.time() + timing.seconds(120)
        while time.time() < end and (host.info(vm).get('lan_address') in (None, '', '-')):
            timing.sleep(3)
        state = wait_for_device(dev, rec)
        if state == NO_ROOT_SSH:
            rec.say(failsafe_note(vm))
            rc, out = host.stop(vm)
            rec.say('beanjar stop: %s' % out.strip()[-200:])
            rc, out = host.start(vm)
            rec.say('beanjar start: %s' % out.strip()[-200:])
            end = time.time() + timing.seconds(120)
            while time.time() < end and (host.info(vm).get('lan_address') in (None, '', '-')):
                timing.sleep(3)
            state = wait_for_device(dev, rec)
        if state != READY:
            raise SystemExit('the VM did not come up with Homebrew Channel: %s (%s)' % (state, WAIT_STATES[state]))
        removed_launchers = clean_launchers(dev, rec)
        # every launcher on this one boot: the first makes the whole lifecycle, the later ones switch in
        # through Home Chooser and make only their scopes (run_plan; e2e/README.md "Scopes")
        scenario = Scenario(args, host, rec, row, launchers[0], launchers)
        rec.on_fail = scenario.fail_shot
        restart_after_launcher_cleanup(scenario, removed_launchers)
        scenario.run()
    except SystemExit as e:
        rec.say('abort: %s' % e)
        rec.on_fail = None
        rec.check('runner setup', False, str(e))
    except Exception as e:  # keep the artefacts
        import traceback
        rec.say('runner error:\n' + traceback.format_exc())
        rec.on_fail = None
        rec.check('scenario ran to the end', False, repr(e))
    finally:
        rec.on_fail = None
        if scenario and scenario.insp:
            scenario.insp.close()
        if started:
            host.vnc_stop(vm)
        if started and not args.no_stop and vm not in KEEP_LAUNCHER_VMS:
            if scenario and scenario.viewer_opened:
                host.viewer_close(vm)
            host.input_rec = None  # VNC cleanup above also covers --no-stop
            rc, out = host.stop(vm)
            rec.say('beanjar stop: %s' % out.strip()[-200:])
        elif started and vm in KEEP_LAUNCHER_VMS:
            rec.say('%s is left running for the manual restore of its end state' % vm)
        if old_tmp:
            shutil.rmtree(old_tmp, ignore_errors=True)
        counts = rec.finish()
    return 1 if counts.get('FAIL') else 0
