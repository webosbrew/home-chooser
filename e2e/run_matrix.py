#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run the Home Chooser E2E on several VMs: one worker per Beanjar machine, so the runs on
a machine are serialised (at most one VM running on it) and machines run in parallel.  Each run is a
subprocess of homechooser_e2e.py.  See e2e/README.md "Several machines".

    python3 -I e2e/run_matrix.py --dry-run                 # the plan for the whole matrix
    python3 -I e2e/run_matrix.py --build --tier release    # a release pass (e2e/README.md "Tiers")
    python3 -I e2e/run_matrix.py --build --vms 5.x,9.x,11.x --reboot-check

--vms (names or releases) defaults to the runner's MATRIX.  --tier sets defaults only: an explicit option
wins.  Options the scheduler does not know are passed to every run.
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hc_e2e.beanjar  # noqa: E402
import hc_e2e.launchers  # noqa: E402
import hc_e2e.matrix  # noqa: E402
import hc_e2e.plan  # noqa: E402
import hc_e2e.recorder  # noqa: E402

RUNNER = [sys.executable, '-I', os.path.join(HERE, 'homechooser_e2e.py')]
REFUSED_FLAGS = ('--vm', '--host', '--host-mode', '--host-device', '--outdir', '--no-stop', '--list')

# The tiers (e2e/README.md "Tiers").  Each sets the defaults of the options below; an option given on the
# command line wins.  vms: None = every MATRIX slot, 'default' = hosts.toml's default_slot.
# update_on: the slots that get --update-check ('all', or VM names / releases).
UPDATE_SLOT = 'w24g-ombre'  # release tier: one update check.  9.x carries the most update assertions (the
#                             live key filter, the flag and the baked id survive) and this slot is on the Pi,
#                             the fastest machine; the 10+ override and 11+ re-elevation run in the full tier.
FULL_LAUNCHERS = 'all-fixtures'  # web first (homechooser_e2e.FIXTURE_TYPES): the first launcher makes the API calls
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
        return (t, 'normal', True) if spec == hc_e2e.launchers.DEMO_SPEC else (t, w, False)
    info = hc_e2e.launchers.ipk_appinfo(ipk) if ipk and os.path.exists(ipk) else {}
    return 'app', 'overlay' if info.get('defaultWindowType') == 'overlay' else 'normal', False


def launcher_specs(args):
    return [s for s in ((hc_e2e.launchers.fixture_specs() if args.launchers == 'all-fixtures' else args.launchers.split(','))
                        if args.launchers else [args.launcher or 'fixture:web.normal']) if s]


def run_plan_for(run, args):
    """The runner's plan for one run (homechooser_e2e.run_plan), from the slot's release: for --dry-run."""
    specs = launcher_specs(args)
    shapes = [spec_shape(s, args.launcher_ipk if len(specs) == 1 else None) for s in specs]
    major = int(run['release'].split('.')[0])
    return hc_e2e.plan.run_plan(shapes, major, {
        'api': args.api_check, 'reboot': args.reboot_check, 'update': updates(run, args.update_on),
        'launcher_update': args.launcher_update_check, 'until': 5})


# ---------------------------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------------------------
def wanted_vms(names):
    """Release selects its first slot; an explicit VM restricts the slot to that VM."""
    if not names:
        return [(m[0], m[1]) for m in hc_e2e.matrix.MATRIX]
    out, seen = [], set()
    for name in names:
        row = next((m for m in hc_e2e.matrix.MATRIX if name == m[0] or name in hc_e2e.matrix.candidates(m[1])), None)
        if row is None:
            hc_e2e.matrix.entry(name)
        key = hc_e2e.matrix.candidates(row[1])
        if key not in seen:
            seen.add(key)
            out.append((row[0], row[1] if name == row[0] else name))
    return out


def select_hosts(cfg, names):
    selected = names.split(',') if names else list(cfg['hosts'])
    unknown = set(selected) - cfg['hosts'].keys()
    if unknown:
        raise SystemExit('unknown hosts: ' + ', '.join(sorted(unknown)))
    return dict(cfg, hosts={h: cfg['hosts'][h] for h in selected})


class WorkPool(dict):
    """Preview queues and the shared slots claimed at runtime."""
    def __init__(self, slots):
        super().__init__()
        self.slots = slots

    def pick(self, host, pending):
        available = [(i, s) for i, s in enumerate(pending) if host in s['choices']]
        if not available:
            return None
        exclusive = {h: sum(list(s['choices']) == [h] for s in pending)
                     for s in pending for h in s['choices']}
        def opportunity(slot):
            others = set(slot['choices']) - {host}
            return exclusive.get(host, 0) - min(exclusive.get(h, 0) for h in others) if others else 0
        i, slot = min(available, key=lambda pair: (
            all(v in hc_e2e.matrix.KEEP_LAUNCHER_VMS for v in pair[1]['choices'][host]),
            opportunity(pair[1]), -len(pair[1]['choices']), pair[0]))
        pending.pop(i)
        return {'host': host, 'release': slot['release'], 'vm': slot['choices'][host][0]}


def discover(cfg, lister):
    """({host: set of VM names}, {host: error}) from lister(host name) -> [VM name]."""
    found, errors = {}, {}
    for name in cfg['hosts']:
        try:
            found[name] = set(lister(name))
        except Exception as e:  # an unreachable machine: its VMs cannot be placed, the rest still run
            errors[name] = str(e)
    return found, errors


def plan(wanted, cfg, found, errors=None):
    """Resolve candidates and simulate equal-cost runs weighted by host slowdown."""
    errors = errors or {}
    pinned, problems, slots = {}, [], []
    for host, h in cfg['hosts'].items():
        for vm in h.get('vms', []):
            if vm in pinned:
                problems.append('%s is pinned to both %s and %s' % (vm, pinned[vm], host))
            pinned[vm] = host
    for release, alternatives in wanted:
        choices = {}
        for vm in hc_e2e.matrix.candidates(alternatives):
            where = [h for h in cfg['hosts'] if vm in found.get(h, ())]
            if vm in pinned:
                where = [pinned[vm]]
            if len(where) > 1:
                problems.append('%s is on several machines (%s): pin it with vms = [...] in hosts.toml'
                                % (vm, ', '.join(where)))
                continue
            if where:
                choices.setdefault(where[0], []).append(vm)
        if choices:
            slots.append({'release': release, 'choices': choices})
        elif not any('is on several machines' in p and any(v in p for v in hc_e2e.matrix.candidates(alternatives)) for p in problems):
            problems.append('%s is on no machine%s' % (' | '.join(hc_e2e.matrix.candidates(alternatives)),
                ' that answered (no answer: %s)' % ', '.join(sorted(errors)) if errors else ''))
    pool = WorkPool(slots)
    pending, clocks = list(slots), {h: 0 for h in cfg['hosts']}
    while pending:
        eligible = [h for h in clocks if any(h in s['choices'] for s in pending)]
        host = min(eligible, key=lambda h: clocks[h])
        pool.setdefault(host, []).append(pool.pick(host, pending))
        clocks[host] += cfg['hosts'][host].get('slowdown', 1.0)
    return pool, problems


# ---------------------------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------------------------
def counts_of(outdir):
    try:
        with open(os.path.join(outdir, 'results.json')) as f:
            res = json.load(f)
    except (OSError, ValueError):
        return {}
    counts = {}
    for r in res:
        counts[r['status']] = counts.get(r['status'], 0) + 1
    return counts


class Matrix:
    """The workers.  busy(host) -> [running VM names] (raises if the host cannot be asked);
    command(run) -> argv of the runner for one run."""

    def __init__(self, queues, outroot, command, busy, wait_idle=180, poll=5, say=print):
        self.queues, self.outroot, self.command, self.busy = queues, outroot, command, busy
        self.wait_idle, self.poll = wait_idle, poll
        self.stopping = threading.Event()
        self.procs = {}  # run vm -> Popen
        self.lock = threading.Lock()
        self._say = say
        self.pending = list(queues.slots) if isinstance(queues, WorkPool) else None
        self.hosts = list(dict.fromkeys(h for s in queues.slots for h in s['choices'])) if isinstance(queues, WorkPool) else list(queues)
        self.completed = {h: [] for h in self.hosts}

    def say(self, text):
        with self.lock:
            self._say('%s %s' % (time.strftime('%H:%M:%S'), text))

    def idle(self, host, wait):
        """'' when the host has no VM running, else why not (after waiting up to `wait` seconds:
        a VM just stopped by the previous run may still read Stopping)."""
        end = time.time() + wait
        while True:
            try:
                running = self.busy(host)
                why = 'VMs running on %s: %s' % (host, ', '.join(running)) if running else ''
            except Exception as e:
                why = 'cannot ask %s: %s' % (host, e)
            if not why or time.time() >= end or self.stopping.is_set():
                return why
            time.sleep(self.poll)

    def run_one(self, run, wait):
        run['outdir'] = os.path.join(self.outroot, run['vm'])
        why = self.idle(run['host'], wait)
        if self.stopping.is_set():
            run['status'] = 'not run'
            return
        if why:
            run.update(status='refused', detail=why)
            return
        argv = self.command(run)
        run['argv'] = argv
        t0 = time.time()
        with open(os.path.join(self.outroot, run['vm'] + '.console.log'), 'w') as log:
            # its own session: a Ctrl-C at the terminal reaches only the scheduler, which then stops each
            # run itself and waits for it to stop its VM
            p = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 start_new_session=True)
            with self.lock:
                self.procs[run['vm']] = p
            run['rc'] = p.wait()
            with self.lock:
                del self.procs[run['vm']]
        run['seconds'] = round(time.time() - t0)
        run['counts'] = counts_of(run['outdir'])
        if self.stopping.is_set() and run['rc'] != 0:
            run['status'] = 'interrupted'
        elif run['rc'] != 0 or run['counts'].get('FAIL') or not run['counts']:
            run['status'] = 'FAIL'
        else:
            run['status'] = 'PASS'

    def worker(self, host):
        wait = self.wait_idle
        queue = iter(self.queues.get(host, []))
        while True:
            if self.pending is None:
                run = next(queue, None)
            else:
                with self.lock:
                    run = None if self.stopping.is_set() else self.queues.pick(host, self.pending)
                    if run:
                        self.completed[host].append(run)
            if run is None:
                break
            self.run_one(run, wait)
            if run['status'] == 'refused':
                wait = 0
            if run['status'] != 'not run':
                self.say(line(run, self.outroot))

    def results(self):
        if self.pending is None:
            return self.queues
        result = {h: list(q) for h, q in self.completed.items()}
        pending = list(self.pending)
        for h in self.hosts:
            while True:
                run = self.queues.pick(h, pending)
                if run is None:
                    break
                run['status'] = 'not run'
                result[h].append(run)
        return result

    def start(self):
        self.threads = [threading.Thread(target=self.worker, args=(h,), daemon=True) for h in self.hosts]
        for t in self.threads:
            t.start()

    def alive(self):
        return any(t.is_alive() for t in self.threads)

    def join(self, timeout=None):
        end = None if timeout is None else time.time() + timeout
        for t in self.threads:
            t.join(None if end is None else max(0, end - time.time()))

    def stop(self, sig=signal.SIGINT):
        """No new runs; each running one gets `sig` (the runner unwinds and stops its VM).  SIGKILL
        goes to the whole process group and leaves the VM running."""
        self.stopping.set()
        with self.lock:
            procs = dict(self.procs)
        for vm, p in procs.items():
            try:
                if sig == signal.SIGKILL:
                    os.killpg(p.pid, sig)
                else:
                    p.send_signal(sig)
            except OSError:
                pass
        return sorted(procs)


def line(run, outroot):
    c = run.get('counts') or {}
    checks = ' '.join('%s %d' % (k, c[k]) for k in sorted(c))
    took = '%dm%02ds' % divmod(run['seconds'], 60) if 'seconds' in run else ''
    where = os.path.relpath(run['outdir'], os.getcwd()) if run.get('rc') is not None else run.get('detail', '')
    return '[%s] %-18s %-5s %-11s %-28s %7s  %s' % (run['host'], run['vm'], run['release'], run['status'],
                                                     checks, took, where)


def write_summary(outroot, queues, problems, info):
    runs = [r for q in queues.values() for r in q]
    with open(os.path.join(outroot, 'summary.json'), 'w') as f:
        json.dump(dict(info, problems=problems, runs=runs), f, indent=2)
    md = ['# Home Chooser E2E matrix %s' % os.path.basename(outroot), '']
    md += ['* %s: `%s`' % (k, v) for k, v in info.items()]
    md += ['', '| Host | Release | VM | Result | Checks | Time | Results |', '| --- | --- | --- | --- | --- | --- | --- |']
    for r in runs:
        c = r.get('counts') or {}
        link = '[%s](%s/)' % (r['vm'], r['vm']) if r.get('rc') is not None else r.get('detail', '')
        md.append('| %s | %s | `%s` | %s | %s | %s | %s |' % (
            r['host'], r['release'], r['vm'], r.get('status', 'not run'), ', '.join('%s %d' % (k, c[k]) for k in sorted(c)),
            '%dm%02ds' % divmod(r['seconds'], 60) if 'seconds' in r else '', link))
    if problems:
        md += ['', 'Not run:', ''] + ['* ' + p for p in problems]
    with open(os.path.join(outroot, 'summary.md'), 'w') as f:
        f.write('\n'.join(md) + '\n')


# ---------------------------------------------------------------------------------------------
def host_lister(cfg):
    return lambda name: [r['name'] for r in hc_e2e.beanjar.BeanjarHost.from_config(cfg, name).list_vms()]


def host_busy(cfg):
    return lambda name: hc_e2e.beanjar.BeanjarHost.from_config(cfg, name).running_vms()


def build_once(args, outroot, cfg):
    """Everything a run would otherwise build or pick for itself, done once before the fan-out; the
    ipks are copied into the matrix directory, so a rebuild in the tree during the runs changes nothing."""
    os.makedirs(os.path.join(outroot, 'ipk'))
    rec = hc_e2e.recorder.Recorder(os.path.join(outroot, 'ipk'))
    try:
        hc = args.hc_ipk or hc_e2e.launchers.current_home_chooser_ipk(rec, args.build)
        args.hc_ipk = shutil.copy(hc, os.path.join(outroot, 'ipk'))
        rec.say('Home Chooser ipk: %s (from %s)' % (args.hc_ipk, hc))
        if args.update_check and not args.old_ipk:  # a tier's update_on: only some runs use it
            args.update_from = args.update_from or hc_e2e.launchers.default_update_ref()
            old, tmp = hc_e2e.launchers.build_old_home_chooser(args.update_from, rec)
            os.makedirs(os.path.join(outroot, 'ipk', 'old'))
            args.old_ipk = shutil.copy(old, os.path.join(outroot, 'ipk', 'old'))
            shutil.rmtree(tmp, ignore_errors=True)
            rec.say('old Home Chooser ipk (%s): %s' % (args.update_from, args.old_ipk))
        specs = launcher_specs(args)
        for i, sp in enumerate(specs):  # builds missing fixture ipks now, not in N runs at once
            hc_e2e.launchers.make_launcher(sp, args.launcher_ipk if len(specs) == 1 else None, rec.say,
                              api=hc_e2e.launchers.api_build(sp, i, args.api_check))
        if args.api_check:  # the permission-built web fixtures and the disposable app
            hc_e2e.launchers.api_fixture_ipks(rec.say)
    finally:
        counts = rec.finish()
    if counts.get('FAIL'):
        raise SystemExit('the build failed: %s' % os.path.join(outroot, 'ipk', 'run.log'))


def runner_command(args, rest):
    def command(run):
        argv = RUNNER + ['--vm', run['vm'], '--host', run['host'], '--outdir', run['outdir']]
        for flag, value in (('--hosts-file', args.hosts_file), ('--hc-ipk', args.hc_ipk),
                            ('--launcher', args.launcher), ('--launchers', args.launchers),
                            ('--launcher-ipk', args.launcher_ipk), ('--update-from', args.update_from),
                            ('--old-ipk', args.old_ipk)):
            if value:
                argv += [flag, value]
        if updates(run, args.update_on):
            argv.append('--update-check')
        if args.api_check:
            argv.append('--api-check' if args.api_check in (True, 'smoke') else '--api-check=' + args.api_check)
        if args.reboot_check:
            argv.append('--reboot-check' if args.reboot_check == 'auto' else '--reboot-check=' + args.reboot_check)
        if args.uninstall_via:
            argv += ['--uninstall-via', args.uninstall_via]
        if args.launcher_update_check:
            argv.append('--launcher-update-check')
        return argv + rest
    return command


def interrupted(*_):
    raise KeyboardInterrupt


def main(argv=None, lister=None, busy=None):
    ap = argparse.ArgumentParser(description='Home Chooser E2E on several VMs, one at a time per Beanjar machine',
                                 epilog='Other options go to every run of homechooser_e2e.py.', allow_abbrev=False)
    ap.add_argument('--tier', choices=sorted(TIERS), default=None,
                    help='smoke (one VM, web.normal, no reboot), release (every slot, web normal+overlay on one boot, '
                         'reboot, one update check) or full (every slot, all six fixtures, full API): the defaults of '
                         'the options below; an option given here wins (e2e/README.md "Tiers")')
    ap.add_argument('--vms', default=None, help="comma list of VM names or releases (default: the runner's matrix)")
    ap.add_argument('--hosts', default='', help='comma list of host names (default: all configured hosts)')
    ap.add_argument('--dry-run', action='store_true', help='print the plan and the commands, run nothing')
    ap.add_argument('--hosts-file', default=None, help='a hosts file instead of e2e/hosts.toml + e2e/hosts.local.toml')
    ap.add_argument('--wait-idle', type=int, default=180,
                    help='seconds to wait for a machine to have no VM running before a run is refused (default 180)')
    ap.add_argument('--results', default=os.path.join(HERE, 'results'), help='where matrix-<timestamp>/ goes')
    ap.add_argument('--build', action='store_true', help='npm run package once, before any run')
    ap.add_argument('--hc-ipk', default=None)
    ap.add_argument('--launcher', default=None)
    ap.add_argument('--launchers', default=None)
    ap.add_argument('--launcher-ipk', default=None)
    ap.add_argument('--update-check', action='store_true')
    ap.add_argument('--update-from', default=None)
    ap.add_argument('--old-ipk', default=None)
    ap.add_argument('--update-on', default=None,
                    help="with --update-check: the slots that run it, VM names or releases, 'all' (the default "
                         "without --tier) or 'none'")
    ap.add_argument('--api-check', nargs='?', const='smoke', choices=['smoke', 'full'], default=None,
                    help='passed to every run; its fixtures are built once first')
    ap.add_argument('--reboot-check', nargs='?', const='auto', choices=['auto', 'all'], default=None,
                    help='passed to every run')
    ap.add_argument('--uninstall-via', choices=['later', 'alert'], default=None, help='passed to every run')
    ap.add_argument('--launcher-update-check', action='store_true', help='passed to every run')
    args, rest = ap.parse_known_args(argv)
    bad = [a for a in rest if a.split('=')[0] in REFUSED_FLAGS]
    if bad:
        ap.error('the scheduler sets these per run or cannot allow them (a VM left running holds its machine): %s'
                 % ' '.join(bad))
    loaded = hc_e2e.matrix.load_hosts(args.hosts_file)
    apply_tier(args, loaded)
    cfg = select_hosts(loaded, args.hosts)
    found, errors = discover(cfg, lister or host_lister(cfg))
    queues, problems = plan(wanted_vms([v for v in (args.vms or '').split(',') if v]), cfg, found, errors)
    for h, e in sorted(errors.items()):
        print('warning: host %s did not answer: %s' % (h, e))
    for p in problems:
        print('not run: ' + p)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    outroot = os.path.join(args.results, 'matrix-' + stamp)
    command = runner_command(args, rest)
    if args.dry_run:
        print('plan estimate (actual workers claim slots as they finish; one VM at a time per machine; machines in parallel); results would go to %s' % outroot)
        if args.tier:
            print('tier %s' % args.tier)
        for host, q in queues.items():
            h = cfg['hosts'][host]
            print('%s (%s): %d run(s)' % (host, 'local' if h.get('local') else 'ssh ' + h['ssh'], len(q)))
            for i, run in enumerate(q, 1):
                run['outdir'] = os.path.join(outroot, run['vm'])
                print('  %d. %-5s %-18s %s' % (i, run['release'], run['vm'], ' '.join(command(run)[len(RUNNER):])))
                for line in hc_e2e.plan.describe_plan(run_plan_for(run, args), launcher_specs(args)):
                    print('       ' + line)
        return 0
    if not queues:
        print('nothing to run')
        return 1
    os.makedirs(outroot)
    build_once(args, outroot, cfg)
    m = Matrix(queues, outroot, command, busy or host_busy(cfg), args.wait_idle)
    print('matrix %s: %s' % (outroot, ', '.join('%s %d run(s)' % (h, len(q)) for h, q in queues.items())), flush=True)
    signal.signal(signal.SIGTERM, interrupted)
    m.start()
    try:
        while m.alive():
            m.join(0.5)
    except KeyboardInterrupt:
        print('stopping: no new runs; each running one is stopped and stops its VM (Ctrl-C again to kill)', flush=True)
        m.stop(signal.SIGINT)
        try:
            while m.alive():
                m.join(0.5)
        except KeyboardInterrupt:
            left = m.stop(signal.SIGKILL)
            m.join(10)
            print('killed; these VMs may still be running: %s' % ', '.join(
                '%s/%s' % (r['host'], r['vm']) for q in m.results().values() for r in q if r['vm'] in left))
    queues = m.results()
    write_summary(outroot, queues, problems, {
        'tier': args.tier or '-', 'update on': args.update_on or '-', 'hc_ipk': args.hc_ipk, 'old_ipk': args.old_ipk, 'runner flags': ' '.join(command({'vm': '<vm>', 'release': '<release>', 'host': '<host>',
                                                                                    'outdir': '<dir>'})[len(RUNNER):])})
    print('summary: %s' % os.path.join(outroot, 'summary.md'))
    runs = [r for q in queues.values() for r in q]
    if m.stopping.is_set():
        return 130
    return 0 if runs and all(r.get('status') == 'PASS' for r in runs) and not problems else 1


if __name__ == '__main__':
    sys.exit(main())
