#!/usr/bin/env python3
"""Run the Home Chooser E2E on several VMs: one queue and one worker per Beanjar machine, so the runs on
a machine are serialised (at most one VM running on it) and machines run in parallel.  Each run is a
subprocess of homechooser_e2e.py.  See e2e/README.md "Several machines".

    python3 -I e2e/run_matrix.py --dry-run                 # the plan for the whole matrix
    python3 -I e2e/run_matrix.py --build --vms 5.x,9.x,11.x --reboot-check

--vms (names or releases) defaults to the runner's MATRIX.  Options the scheduler does not know are
passed to every run.
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
import homechooser_e2e as e2e  # noqa: E402

RUNNER = [sys.executable, '-I', os.path.join(HERE, 'homechooser_e2e.py')]
REFUSED_FLAGS = ('--vm', '--host', '--host-mode', '--host-device', '--outdir', '--no-stop', '--list')


# ---------------------------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------------------------
def wanted_vms(names):
    """[(release, vm)] in order, each VM once; a release means its first matrix row."""
    out, seen = [], set()
    for n in names or [m[1] for m in e2e.MATRIX]:
        row = e2e.entry(n)
        if row[1] not in seen:
            seen.add(row[1])
            out.append((row[0], row[1]))
    return out


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
    """({host: [run]}, [problem]).  A VM goes to the host that pins it (`vms` in hosts.toml), else to the
    one host discovery found it on.  Ambiguous or unplaced VMs are problems and are not run.  VMs the
    runner leaves running (KEEP_LAUNCHER_VMS) go last on their host, since they hold it afterwards."""
    errors = errors or {}
    pinned, problems = {}, []
    for host, h in cfg['hosts'].items():
        for vm in h.get('vms', []):
            if vm in pinned:
                problems.append('%s is pinned to both %s and %s' % (vm, pinned[vm], host))
            pinned[vm] = host
    queues = {h: [] for h in cfg['hosts']}
    for release, vm in wanted:
        if vm in pinned:
            host = pinned[vm]
        else:
            where = [h for h in cfg['hosts'] if vm in found.get(h, ())]
            if len(where) != 1:
                if where:
                    problems.append('%s is on several machines (%s): pin it with vms = [...] in hosts.toml'
                                    % (vm, ', '.join(where)))
                else:
                    problems.append('%s is on no machine%s' % (vm, ' that answered (no answer: %s)' % ', '.join(sorted(errors))
                                                               if errors else ''))
                continue
            host = where[0]
        queues[host].append({'host': host, 'release': release, 'vm': vm})
    for host in queues:
        queues[host].sort(key=lambda r: r['vm'] in e2e.KEEP_LAUNCHER_VMS)
    return {h: q for h, q in queues.items() if q}, problems


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
        for run in self.queues[host]:
            self.run_one(run, wait)
            if run['status'] == 'refused':
                wait = 0  # one wait per host, not one per queued run
            if run['status'] != 'not run':
                self.say(line(run, self.outroot))

    def start(self):
        self.threads = [threading.Thread(target=self.worker, args=(h,), daemon=True) for h in self.queues]
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
    return lambda name: [r['name'] for r in e2e.BeanjarHost.from_config(cfg, name).list_vms()]


def host_busy(cfg):
    return lambda name: e2e.BeanjarHost.from_config(cfg, name).running_vms()


def build_once(args, outroot, cfg):
    """Everything a run would otherwise build or pick for itself, done once before the fan-out; the
    ipks are copied into the matrix directory, so a rebuild in the tree during the runs changes nothing."""
    os.makedirs(os.path.join(outroot, 'ipk'))
    rec = e2e.Recorder(os.path.join(outroot, 'ipk'))
    try:
        hc = args.hc_ipk or e2e.current_home_chooser_ipk(rec, args.build)
        args.hc_ipk = shutil.copy(hc, os.path.join(outroot, 'ipk'))
        rec.say('Home Chooser ipk: %s (from %s)' % (args.hc_ipk, hc))
        if args.update_check and not args.old_ipk:
            args.update_from = args.update_from or e2e.default_update_ref()
            old, tmp = e2e.build_old_home_chooser(args.update_from, rec)
            os.makedirs(os.path.join(outroot, 'ipk', 'old'))
            args.old_ipk = shutil.copy(old, os.path.join(outroot, 'ipk', 'old'))
            shutil.rmtree(tmp, ignore_errors=True)
            rec.say('old Home Chooser ipk (%s): %s' % (args.update_from, args.old_ipk))
        args.dock_ipk = args.dock_ipk or cfg['paths'].get('dock_ipk') or e2e.DOCK_IPK
        specs = (e2e.fixture_specs() if args.launchers == 'all-fixtures' else args.launchers.split(',')) \
            if args.launchers else [args.launcher or 'dock']
        for sp in [s for s in specs if s]:  # builds missing fixture ipks now, not in N runs at once
            e2e.make_launcher(sp, args.launcher_ipk if len(specs) == 1 else None, rec.say, args.dock_ipk)
    finally:
        counts = rec.finish()
    if counts.get('FAIL'):
        raise SystemExit('the build failed: %s' % os.path.join(outroot, 'ipk', 'run.log'))


def runner_command(args, rest):
    def command(run):
        argv = RUNNER + ['--vm', run['vm'], '--host', run['host'], '--outdir', run['outdir']]
        for flag, value in (('--hosts-file', args.hosts_file), ('--hc-ipk', args.hc_ipk), ('--dock-ipk', args.dock_ipk),
                            ('--launcher', args.launcher), ('--launchers', args.launchers),
                            ('--launcher-ipk', args.launcher_ipk), ('--update-from', args.update_from),
                            ('--old-ipk', args.old_ipk)):
            if value:
                argv += [flag, value]
        return argv + (['--update-check'] if args.update_check else []) + rest
    return command


def interrupted(*_):
    raise KeyboardInterrupt


def main(argv=None, lister=None, busy=None):
    ap = argparse.ArgumentParser(description='Home Chooser E2E on several VMs, one at a time per Beanjar machine',
                                 epilog='Other options go to every run of homechooser_e2e.py.', allow_abbrev=False)
    ap.add_argument('--vms', default='', help="comma list of VM names or releases (default: the runner's matrix)")
    ap.add_argument('--dry-run', action='store_true', help='print the plan and the commands, run nothing')
    ap.add_argument('--hosts-file', default=None, help='a hosts file instead of e2e/hosts.toml + e2e/hosts.local.toml')
    ap.add_argument('--wait-idle', type=int, default=180,
                    help='seconds to wait for a machine to have no VM running before a run is refused (default 180)')
    ap.add_argument('--results', default=os.path.join(HERE, 'results'), help='where matrix-<timestamp>/ goes')
    ap.add_argument('--build', action='store_true', help='npm run package once, before any run')
    ap.add_argument('--hc-ipk', default=None)
    ap.add_argument('--dock-ipk', default=None)
    ap.add_argument('--launcher', default=None)
    ap.add_argument('--launchers', default=None)
    ap.add_argument('--launcher-ipk', default=None)
    ap.add_argument('--update-check', action='store_true')
    ap.add_argument('--update-from', default=None)
    ap.add_argument('--old-ipk', default=None)
    args, rest = ap.parse_known_args(argv)
    bad = [a for a in rest if a.split('=')[0] in REFUSED_FLAGS]
    if bad:
        ap.error('the scheduler sets these per run or cannot allow them (a VM left running holds its machine): %s'
                 % ' '.join(bad))
    cfg = e2e.load_hosts(args.hosts_file)
    found, errors = discover(cfg, lister or host_lister(cfg))
    queues, problems = plan(wanted_vms([v for v in args.vms.split(',') if v]), cfg, found, errors)
    for h, e in sorted(errors.items()):
        print('warning: host %s did not answer: %s' % (h, e))
    for p in problems:
        print('not run: ' + p)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    outroot = os.path.join(args.results, 'matrix-' + stamp)
    command = runner_command(args, rest)
    if args.dry_run:
        print('plan (one VM at a time per machine; machines in parallel); results would go to %s' % outroot)
        for host, q in queues.items():
            h = cfg['hosts'][host]
            print('%s (%s): %d run(s)' % (host, 'local' if h.get('local') else 'ssh ' + h['ssh'], len(q)))
            for i, run in enumerate(q, 1):
                run['outdir'] = os.path.join(outroot, run['vm'])
                print('  %d. %-5s %-18s %s' % (i, run['release'], run['vm'], ' '.join(command(run)[len(RUNNER):])))
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
                '%s/%s' % (r['host'], r['vm']) for q in queues.values() for r in q if r['vm'] in left))
    write_summary(outroot, queues, problems, {
        'hc_ipk': args.hc_ipk, 'old_ipk': args.old_ipk, 'runner flags': ' '.join(command({'vm': '<vm>', 'host': '<host>',
                                                                                    'outdir': '<dir>'})[len(RUNNER):])})
    print('summary: %s' % os.path.join(outroot, 'summary.md'))
    runs = [r for q in queues.values() for r in q]
    if m.stopping.is_set():
        return 130
    return 0 if runs and all(r.get('status') == 'PASS' for r in runs) and not problems else 1


if __name__ == '__main__':
    sys.exit(main())
