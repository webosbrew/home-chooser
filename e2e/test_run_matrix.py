#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Offline tests of the scheduler (fake hosts, a fake runner).

    python3 -I -m unittest discover -s e2e -p 'test_*.py' -v
"""
import contextlib
import io
import json
import os
import re
import signal
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import homechooser_e2e as e2e  # noqa: E402
import run_matrix  # noqa: E402

# Logs "start|end|stopped <host> <vm> <time>" to $FAKE_LOG, sleeps $FAKE_SLEEP, writes results.json
# (FAIL for a VM named bad-*); on SIGINT logs "stopped" and exits like an interrupted runner.
FAKE_RUNNER = textwrap.dedent('''
    import json, os, signal, sys, time
    a = sys.argv[1:]
    vm, host, outdir = a[a.index('--vm') + 1], a[a.index('--host') + 1], a[a.index('--outdir') + 1]
    def log(what):
        with open(os.environ['FAKE_LOG'], 'a') as f:
            f.write('%s %s %s %.4f %s\\n' % (what, host, vm, time.time(), ' '.join(a)))
    def stop(*_):
        log('stopped')
        sys.exit(130)
    signal.signal(signal.SIGINT, stop)
    os.makedirs(outdir)
    log('start')
    time.sleep(float(os.environ.get('FAKE_SLEEP', '0.3')))
    json.dump([{'status': 'FAIL' if vm.startswith('bad-') else 'PASS'}], open(os.path.join(outdir, 'results.json'), 'w'))
    log('end')
''')

def read(path):
    with open(path) as f:
        return f.read()


def write(path, text):
    with open(path, 'w') as f:
        f.write(text)


CFG = {'default': 'a', 'hosts': {'a': {'ssh': 'host-a'}, 'b': {'ssh': 'host-b'}, 'c': {'local': True}}}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='hc-matrix-test-')
        self.runner = os.path.join(self.tmp, 'fake_runner.py')
        with open(self.runner, 'w') as f:
            f.write(FAKE_RUNNER)
        self.log = os.path.join(self.tmp, 'calls.log')
        os.environ['FAKE_LOG'] = self.log
        os.environ['FAKE_SLEEP'] = '0.3'

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def calls(self):
        out = []
        if os.path.exists(self.log):
            for l in read(self.log).splitlines():
                what, host, vm, t = l.split()[:4]
                out.append((what, host, vm, float(t)))
        return out

    def command(self, run):
        return [sys.executable, '-I', self.runner, '--vm', run['vm'], '--host', run['host'], '--outdir', run['outdir']]

    def matrix(self, queues, busy=lambda h: [], **kw):
        outroot = os.path.join(self.tmp, 'matrix')
        os.makedirs(outroot, exist_ok=True)
        lines = []
        kw.setdefault('wait_idle', 0)
        return run_matrix.Matrix(queues, outroot, self.command, busy, poll=0.05, say=lines.append, **kw), lines


class Planning(unittest.TestCase):
    def test_alternatives_three_hosts_and_unplaced(self):
        q, problems = run_matrix.plan([('5.x', ('rk', 'pi')), ('6.x', 'only'), ('7.x', 'missing')],
                                      CFG, {'a': {'pi', 'only'}, 'b': {'rk'}, 'c': set()})
        self.assertEqual([(r['release'], r['vm']) for r in q['a']], [('6.x', 'only')])
        self.assertEqual(q['b'][0]['vm'], 'rk')
        self.assertNotIn('c', q)
        self.assertEqual(len(problems), 1)
        self.assertIn('missing', problems[0])

    def test_cli_hosts_limits_discovery_and_release_selection(self):
        with tempfile.TemporaryDirectory() as d:
            hosts = os.path.join(d, 'hosts.toml')
            write(hosts, '[hosts.a]\nssh = "a"\n[hosts.b]\nssh = "b"\n')
            asked = []
            def lister(host):
                asked.append(host)
                return ['w21a-kisscurl-rk1']
            with contextlib.redirect_stdout(io.StringIO()) as output:
                rc = run_matrix.main(['--dry-run', '--hosts-file', hosts, '--hosts', 'b', '--vms', '6.x'],
                                     lister=lister)
            self.assertEqual(rc, 0)
            self.assertEqual(asked, ['b'])
            self.assertIn('w21a-kisscurl-rk1', output.getvalue())
            self.assertIn('--host b', output.getvalue())

    def test_host_filter_and_release_alternatives(self):
        cfg = run_matrix.select_hosts(CFG, 'b')
        self.assertEqual(list(cfg['hosts']), ['b'])
        with self.assertRaises(SystemExit):
            run_matrix.select_hosts(CFG, 'missing')
        self.assertEqual(run_matrix.wanted_vms(['6.x']), [('6.x', e2e.MATRIX[1][1])])
        rk = e2e.candidates(e2e.MATRIX[1][1])[0]
        self.assertEqual(run_matrix.wanted_vms([rk]), [('6.x', rk)])
        q, problems = run_matrix.plan(run_matrix.wanted_vms(['6.x']), cfg, {'b': {rk}})
        self.assertEqual(problems, [])
        self.assertEqual(q['b'][0]['vm'], rk)

    def test_discovered_vms_go_to_their_host(self):
        found = {'a': {'vm1', 'vm2'}, 'b': {'vm3'}, 'c': set()}
        q, problems = run_matrix.plan([('5.x', 'vm1'), ('6.x', 'vm3'), ('7.x', 'vm2')], CFG, found)
        self.assertEqual(problems, [])
        self.assertEqual({h: [r['vm'] for r in runs] for h, runs in q.items()}, {'a': ['vm1', 'vm2'], 'b': ['vm3']})

    def test_ambiguous_and_missing_vms_are_problems_not_runs(self):
        found = {'a': {'twin'}, 'b': {'twin'}}
        q, problems = run_matrix.plan([('9.x', 'twin'), ('5.x', 'ghost')], CFG, found, {'c': 'ssh: timeout'})
        self.assertEqual(q, {})
        self.assertIn('twin is on several machines (a, b)', problems[0])
        self.assertIn('ghost is on no machine that answered (no answer: c)', problems[1])

    def test_a_pin_overrides_discovery(self):
        cfg = json.loads(json.dumps(CFG))
        cfg['hosts']['b']['vms'] = ['twin']
        q, problems = run_matrix.plan([('9.x', 'twin')], cfg, {'a': {'twin'}, 'b': {'twin'}})
        self.assertEqual((problems, list(q), q['b'][0]['vm']), ([], ['b'], 'twin'))

    def test_a_vm_left_running_goes_last_on_its_host(self):
        keep = next(iter(e2e.KEEP_LAUNCHER_VMS))
        q, _ = run_matrix.plan([('6.x', keep), ('5.x', 'vm1')], CFG, {'a': {keep, 'vm1'}})
        self.assertEqual([r['vm'] for r in q['a']], ['vm1', keep])

    def test_releases_map_to_matrix_vms_once(self):
        first9 = next(m[1] for m in e2e.MATRIX if m[0] == '9.x')
        self.assertEqual(run_matrix.wanted_vms(['9.x', first9, '5.x']),
                         [('9.x', first9), ('5.x', e2e.MATRIX[0][1])])
        self.assertEqual(len(run_matrix.wanted_vms([])), len({m[1] for m in e2e.MATRIX}))

    def test_a_failing_host_is_reported_and_the_rest_discovered(self):
        def lister(h):
            if h == 'b':
                raise RuntimeError('beanjar list on b failed')
            return ['vm-' + h]
        found, errors = run_matrix.discover(CFG, lister)
        self.assertEqual(found, {'a': {'vm-a'}, 'c': {'vm-c'}})
        self.assertEqual(list(errors), ['b'])


class Serialisation(Fixture):
    def test_shared_pool_claims_every_slot_once(self):
        wanted = [('9.x', ('a%d' % i, 'b%d' % i)) for i in range(6)]
        q, problems = run_matrix.plan(wanted, CFG,
            {'a': {'a%d' % i for i in range(6)}, 'b': {'b%d' % i for i in range(6)}})
        self.assertEqual(problems, [])
        m, _ = self.matrix(q)
        m.start()
        m.join(30)
        self.assertFalse(m.alive())
        starts = [c for c in self.calls() if c[0] == 'start']
        self.assertEqual(len(starts), 6)
        self.assertEqual(len({c[2][1:] for c in starts}), 6)
        self.assertEqual(len([r for runs in m.results().values() for r in runs]), 6)

    def test_serial_within_a_host_parallel_across_hosts(self):
        queues = {h: [{'host': h, 'release': '9.x', 'vm': '%s-vm%d' % (h, i)} for i in range(3)] for h in ('a', 'b', 'c')}
        m, lines = self.matrix(queues)
        t0 = time.time()
        m.start()
        m.join(30)
        took = time.time() - t0
        spans = {}
        for what, host, vm, t in self.calls():
            spans.setdefault(host, {}).setdefault(vm, {})[what] = t
        for host, vms in spans.items():
            iv = sorted((v['start'], v['end']) for v in vms.values())
            self.assertEqual(len(iv), 3, host)
            for (s1, e1), (s2, e2) in zip(iv, iv[1:]):
                self.assertLessEqual(e1, s2, 'two runs overlapped on host %s' % host)
        starts = sorted(min(v['start'] for v in vms.values()) for vms in spans.values())
        self.assertLess(starts[-1] - starts[0], 0.5 * 3, 'the hosts did not start together')
        self.assertLess(took, 3 * 3 * 0.3, 'not parallel across hosts: %.1f s' % took)
        self.assertEqual(len(lines), 9)
        self.assertTrue(all(r['status'] == 'PASS' for q in queues.values() for r in q))

    def test_a_busy_host_is_refused_and_never_started(self):
        queues = {'a': [{'host': 'a', 'release': '5.x', 'vm': 'vm1'}],
                  'b': [{'host': 'b', 'release': '6.x', 'vm': 'vm2'}, {'host': 'b', 'release': '7.x', 'vm': 'vm3'}]}
        asked = []

        def busy(h):
            asked.append(h)
            return ['someone-elses-vm'] if h == 'b' else []
        m, lines = self.matrix(queues, busy, wait_idle=0.2)
        m.start()
        m.join(30)
        self.assertEqual({c[2] for c in self.calls()}, {'vm1'})
        self.assertEqual([r['status'] for r in queues['b']], ['refused', 'refused'])
        self.assertIn('someone-elses-vm', queues['b'][0]['detail'])
        self.assertEqual(queues['a'][0]['status'], 'PASS')

    def test_an_unreachable_host_is_refused(self):
        def busy(h):
            raise RuntimeError('ssh: connect timed out')
        queues = {'a': [{'host': 'a', 'release': '5.x', 'vm': 'vm1'}]}
        m, _ = self.matrix(queues, busy)
        m.start()
        m.join(30)
        self.assertEqual(queues['a'][0]['status'], 'refused')
        self.assertIn('cannot ask a', queues['a'][0]['detail'])

    def test_a_host_that_becomes_idle_runs(self):
        state = {'n': 0}

        def busy(h):
            state['n'] += 1
            return ['still-stopping'] if state['n'] < 3 else []
        queues = {'a': [{'host': 'a', 'release': '5.x', 'vm': 'vm1'}]}
        m, _ = self.matrix(queues, busy, wait_idle=5)
        m.start()
        m.join(30)
        self.assertEqual(queues['a'][0]['status'], 'PASS')

    def test_fail_counts(self):
        queues = {'a': [{'host': 'a', 'release': '5.x', 'vm': 'bad-vm'}]}
        m, _ = self.matrix(queues)
        m.start()
        m.join(30)
        self.assertEqual((queues['a'][0]['status'], queues['a'][0]['counts']), ('FAIL', {'FAIL': 1}))

    def test_stop_interrupts_the_running_ones_and_starts_no_more(self):
        os.environ['FAKE_SLEEP'] = '30'
        queues = {h: [{'host': h, 'release': '9.x', 'vm': '%s-vm%d' % (h, i)} for i in range(2)] for h in ('a', 'b')}
        m, _ = self.matrix(queues)
        m.start()
        end = time.time() + 10
        while len([c for c in self.calls() if c[0] == 'start']) < 2 and time.time() < end:
            time.sleep(0.05)
        self.assertEqual(sorted(m.stop(signal.SIGINT)), ['a-vm0', 'b-vm0'])
        m.join(10)
        self.assertFalse(m.alive())
        self.assertEqual(sorted(c[2] for c in self.calls() if c[0] == 'stopped'), ['a-vm0', 'b-vm0'])
        self.assertEqual([[r['status'] for r in q] for q in queues.values()], [['interrupted', 'not run']] * 2)


class Main(Fixture):
    def hosts_file(self, body):
        p = os.path.join(self.tmp, 'hosts.toml')
        with open(p, 'w') as f:
            f.write(body)
        return p

    def test_dry_run_of_the_real_matrix_runs_nothing(self):
        hosts = self.hosts_file('default = "pi"\n[hosts.pi]\nssh = "raspberrypi"\n[hosts.pc2]\nssh = "pc2"\n')
        vms = [e2e.candidates(m[1])[-1] for m in e2e.MATRIX]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = run_matrix.main(['--dry-run', '--hosts-file', hosts, '--reboot-check', '--results', self.tmp],
                                 lister=lambda h: vms[::2] if h == 'pi' else vms[1::2],
                                 busy=lambda h: self.fail('dry run asked for busy'))
        text = out.getvalue()
        self.assertEqual(rc, 0, text)
        self.assertIn('pi (ssh raspberrypi): %d run(s)' % len(vms[::2]), text)
        self.assertIn('pc2 (ssh pc2): %d run(s)' % len(vms[1::2]), text)
        self.assertIn('--host pc2', text)
        self.assertIn('--reboot-check', text)
        self.assertEqual(self.calls(), [])
        self.assertEqual([d for d in os.listdir(self.tmp) if d.startswith('matrix-')], [])

    def test_ctrl_c_stops_every_run_and_writes_the_summary(self):
        import subprocess
        hosts = self.hosts_file('[hosts.a]\nssh = "a"\n[hosts.b]\nssh = "b"\n')
        hc = os.path.join(self.tmp, 'hc.ipk')
        write(hc, '')
        os.environ['FAKE_SLEEP'] = '60'
        code = textwrap.dedent('''
            import sys
            sys.path.insert(0, %r)
            import run_matrix
            run_matrix.RUNNER = [sys.executable, '-I', %r]
            sys.exit(run_matrix.main(['--vms', 'w20l-jhericurl,w22h-ombre,w24g-spacebuns', '--hosts-file', %r,
                                      '--hc-ipk', %r, '--results', %r],
                                     lister=lambda h: ['w20l-jhericurl', 'w22h-ombre'] if h == 'a' else ['w24g-spacebuns'],
                                     busy=lambda h: []))
        ''') % (HERE, self.runner, hosts, hc, self.tmp)
        p = subprocess.Popen([sys.executable, '-I', '-c', code], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        end = time.time() + 20
        while len([c for c in self.calls() if c[0] == 'start']) < 2 and time.time() < end:
            time.sleep(0.05)
        p.send_signal(signal.SIGINT)
        out = p.communicate(timeout=30)[0].decode()
        self.assertEqual(p.returncode, 130, out)
        self.assertEqual(sorted(c[2] for c in self.calls() if c[0] == 'stopped'), ['w20l-jhericurl', 'w24g-spacebuns'])
        root = [os.path.join(self.tmp, d) for d in os.listdir(self.tmp) if d.startswith('matrix-')][0]
        s = json.loads(read(os.path.join(root, 'summary.json')))
        self.assertEqual([r['status'] for r in s['runs']], ['interrupted', 'not run', 'interrupted'])

    def test_refuses_flags_that_would_hold_a_machine(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            run_matrix.main(['--dry-run', '--no-stop'], lister=lambda h: [])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            run_matrix.main(['--dry-run', '--host', 'pi'], lister=lambda h: [])

    def test_a_whole_run_writes_the_summary(self):
        hosts = self.hosts_file('[hosts.a]\nssh = "a"\n[hosts.b]\nssh = "b"\n')
        hc = os.path.join(self.tmp, 'hc.ipk')
        write(hc, '')
        saved = run_matrix.RUNNER
        run_matrix.RUNNER = [sys.executable, '-I', self.runner]
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc = run_matrix.main(['--vms', 'w20l-jhericurl,w22h-ombre,w24g-spacebuns', '--hosts-file', hosts,
                                      '--hc-ipk', hc,
                                      '--results', self.tmp, '--wait-popup', '60', '--reboot-check'],
                                     lister=lambda h: ['w20l-jhericurl', 'w22h-ombre'] if h == 'a' else ['w24g-spacebuns'],
                                     busy=lambda h: [])
        finally:
            run_matrix.RUNNER = saved
        self.assertEqual(rc, 0, out.getvalue())
        root = [os.path.join(self.tmp, d) for d in os.listdir(self.tmp) if d.startswith('matrix-')][0]
        s = json.loads(read(os.path.join(root, 'summary.json')))
        self.assertEqual([(r['host'], r['vm'], r['status']) for r in s['runs']],
                         [('a', 'w20l-jhericurl', 'PASS'), ('a', 'w22h-ombre', 'PASS'), ('b', 'w24g-spacebuns', 'PASS')])
        md = read(os.path.join(root, 'summary.md'))
        self.assertIn('[w24g-spacebuns](w24g-spacebuns/)', md)
        argv = s['runs'][0]['argv']
        self.assertEqual(argv[argv.index('--hc-ipk') + 1], os.path.join(root, 'ipk', 'hc.ipk'))  # a private copy
        self.assertEqual(argv[-3:], ['--wait-popup', '60', '--reboot-check'])
        self.assertTrue(os.path.exists(os.path.join(root, 'w22h-ombre.console.log')))


# ---------------------------------------------------------------------------------------------
# The ares device list is not the runner's
# ---------------------------------------------------------------------------------------------
class NoDeviceList(unittest.TestCase):
    def test_runner_never_names_the_user_device_list(self):
        """VMs are reached with `beanjar ares-TOOL`, which writes its own device list; the user's own
        ~/.webos/ose/novacom-devices.json is never read or written, and no `<vm>-root` entry exists."""
        for name in ('homechooser_e2e.py', 'run_matrix.py'):
            src = read(os.path.join(HERE, name))
            literals = re.findall(r'\'[^\'\n]*\'|"[^"\n]*"', src)
            self.assertEqual([x for x in literals if 'novacom-devices' in x or x in ("'-root'", '"-root"')], [], name)


class HostsConfig(unittest.TestCase):
    def test_user_at_host_and_slowdown(self):
        cfg = {'hosts': {'rk': {'ssh': 'mariotaku@rockpi4a-1.lan', 'slowdown': 1.5}}}
        host = e2e.BeanjarHost.from_config(cfg, 'rk')
        self.assertEqual(host.slowdown, 1.5)
        with patch.object(e2e, 'run', return_value=(0, '')) as command:
            host.beanjar('list', '--local', '--json')
            argv = command.call_args.args[0]
            self.assertEqual(argv[-2:], ['mariotaku@rockpi4a-1.lan', 'beanjar list --local --json'])
            self.assertTrue(host.fetch('/tmp/a.png', 'a.png'))
            self.assertEqual(command.call_args.args[0][-2:], ['mariotaku@rockpi4a-1.lan:/tmp/a.png', 'a.png'])
        with patch.object(e2e, 'SLOWDOWN', host.slowdown), patch.object(e2e.time, 'sleep') as sleeper:
            e2e.sleep(4)
            sleeper.assert_called_once_with(6)
            self.assertEqual(e2e.seconds(240), 360)
        with patch.object(e2e, 'SLOWDOWN', 1.5), patch.object(e2e.subprocess, 'run') as command:
            command.return_value.returncode = 0
            command.return_value.stdout = b''
            e2e.run(['fake'], timeout=120)
            self.assertEqual(command.call_args.kwargs['timeout'], 180)
        with patch.object(e2e, 'SLOWDOWN', 1):
            self.assertEqual(e2e.seconds(240), 240)

    def test_default_is_todays_pi(self):
        with tempfile.TemporaryDirectory() as d:
            saved = e2e.HOSTS_FILE, e2e.HOSTS_LOCAL_FILE
            e2e.HOSTS_FILE, e2e.HOSTS_LOCAL_FILE = os.path.join(d, 'none.toml'), os.path.join(d, 'none.local.toml')
            try:
                cfg = e2e.load_hosts()
            finally:
                e2e.HOSTS_FILE, e2e.HOSTS_LOCAL_FILE = saved
        h = e2e.BeanjarHost.from_config(cfg, cfg['default'])
        self.assertEqual((h.name, h.mode, h.device), ('pi', 'remote', 'raspberrypi'))

    def test_local_override_merges_over_the_committed_file(self):
        with tempfile.TemporaryDirectory() as d:
            base, local = os.path.join(d, 'hosts.toml'), os.path.join(d, 'hosts.local.toml')
            write(base, 'default = "pi"\n[hosts.pi]\nssh = "raspberrypi"\n')
            write(local, '[hosts.pi]\nvms = ["w22h-ombre"]\n[hosts.box]\nlocal = true\n')
            saved = e2e.HOSTS_FILE, e2e.HOSTS_LOCAL_FILE
            e2e.HOSTS_FILE, e2e.HOSTS_LOCAL_FILE = base, local
            try:
                cfg = e2e.load_hosts()
            finally:
                e2e.HOSTS_FILE, e2e.HOSTS_LOCAL_FILE = saved
        self.assertEqual(cfg['hosts']['pi'], {'ssh': 'raspberrypi', 'vms': ['w22h-ombre']})
        self.assertEqual(e2e.BeanjarHost.from_config(cfg, 'box').mode, 'local')

    def test_committed_hosts_file_parses(self):
        cfg = e2e.load_hosts(e2e.HOSTS_FILE)
        self.assertEqual((cfg['default'], cfg['hosts']['pi']['ssh']), ('pi', 'raspberrypi'))

    def test_list_vms_reads_this_machines_json_only(self):
        rows = [{'name': 'mine', 'state': 'Running', 'host': None}, {'name': 'off', 'state': 'Off', 'host': None},
                {'name': 'theirs', 'state': 'Running', 'host': 'pc2'}]
        h = e2e.BeanjarHost('local', 'x', 't')
        h.sh = lambda cmd, timeout=120: (0, 'beanjar: warning: [x]\n' + json.dumps(rows))
        self.assertEqual(h.running_vms(), ['mine'])
        h.sh = lambda cmd, timeout=120: (255, 'ssh: connect to host x: timed out')
        with self.assertRaises(RuntimeError):
            h.running_vms()


if __name__ == '__main__':
    unittest.main()
