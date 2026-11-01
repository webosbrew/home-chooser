# SPDX-License-Identifier: Apache-2.0
"""pytest plugin of the device tests (e2e/README.md "Quick start"): the options, one test per planned phase,
the session fixture around one VM run, live progress, and the reports.  The logic is in
hc_e2e.phase_tests (standard library); this file maps it to pytest.  Loaded by e2e/conftest.py.
"""
import base64
import logging
import os
import time

import pytest

from hc_e2e import cli
from hc_e2e.constants import HERE
from hc_e2e.matrix import entry, load_hosts
from hc_e2e.phase_tests import PhaseTests, planned_tests, runner_argv, test_name

CONFIG = None  # the pytest config, for test_vm.py's module-level test definition
RUN_KEY = pytest.StashKey()  # the session's PhaseTests, for the terminal summary
LOG = logging.getLogger('hc_e2e')
PROGRESS = {'begin': '==', 'PASS': 'PASS', 'FAIL': 'FAIL', 'XFAIL': 'XFAIL', 'MANUAL': 'MANUAL',
            'BLOCKED': 'BLOCKED', 'SKIPPED': 'SKIPPED', 'RECOVERED': 'RECOVERED'}


def pytest_addoption(parser):
    g = parser.getgroup('hc-e2e', 'Home Chooser device tests (e2e/device; one VM per pytest run)')
    g.addoption('--vm', default=None, help='a VM name or a release (9.x): the run, as homechooser_e2e.py --vm')
    g.addoption('--host', default=None, help="the Beanjar machine, a name in e2e/hosts.toml (default: its default)")
    g.addoption('--tier', choices=['smoke', 'release', 'full'], default=None,
                help='the defaults of a pass for this VM (hc_e2e.tiers, as run_matrix.py --tier); smoke without '
                     '--vm uses the default slot; an option given here wins')
    g.addoption('--launchers', default=None, help="'all-fixtures' or a comma list of --launcher values, one boot")
    g.addoption('--hc-ipk', default=None, help='the Home Chooser ipk (default: the newest build/ipk/*.ipk)')
    g.addoption('--api-check', nargs='?', const='smoke', choices=['smoke', 'full'], default=None,
                help='the launcher API check, smoke (the bare flag) or full')
    g.addoption('--reboot-check', nargs='?', const='auto', choices=['auto', 'all'], default=None,
                help='reboot with a launcher chosen: auto (the bare flag) or all')
    g.addoption('--uninstall-via', choices=['later', 'alert'], default=None, help='with --api-check')
    g.addoption('--update-check', action='store_true', default=False, help='the Home Chooser update (3u)')
    g.addoption('--outdir', default=None,
                help='the result directory (default e2e/results/<vm>-<timestamp>; must not exist): run.log, '
                     'results.json, the screenshots, and by default junit.xml and report.html')
    g.addoption('--hc-arg', action='append', default=[],
                help='one more homechooser_e2e.py argument, as is (repeat it: --hc-arg=--input-mode --hc-arg=keys)')
    g.addoption('--dry', action='store_true', default=False,
                help='no device: the plan runs against a fake that records the calls (hc_e2e.dry)')
    g.addoption('--dry-fail', default='', help='--dry: comma list of phases that record a FAIL check')
    g.addoption('--dry-manual', default='', help='--dry: comma list of phases that record a MANUAL check')
    g.addoption('--dry-raise', default='', help="--dry: comma list of phases that raise ('vm-start': the VM start)")


def options(config):
    return {k: config.getoption(k) for k in ('vm', 'host', 'tier', 'launchers', 'hc_ipk', 'api_check',
                                             'reboot_check', 'uninstall_via', 'update_check', 'outdir', 'hc_arg')}


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """Resolve the run before the junit and html plugins read their paths: they default into its outdir."""
    global CONFIG
    CONFIG = config
    config.hc = None
    o = options(config)
    if not (o['vm'] or o['tier']):
        return  # the offline unit tests only
    argv, why = runner_argv(o, load_hosts(None))
    if why:
        raise pytest.UsageError(why)
    ap = cli.build_parser()
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        raise pytest.UsageError('homechooser_e2e.py refuses: %s' % ' '.join(argv))
    if args.device or args.list:
        raise pytest.UsageError('the device tests run a VM; --device and --list are homechooser_e2e.py only')
    vm = entry(args.vm)[1]
    args.outdir = args.outdir or os.path.join(HERE, 'results', '%s-%s' % (vm, time.strftime('%Y%m%d-%H%M%S')))
    if os.path.exists(args.outdir):
        raise pytest.UsageError('--outdir %s exists' % args.outdir)
    config.hc = {'argv': argv, 'args': args, 'ap': ap, 'vm': vm, 'tests': planned_tests(args),
                 'dry': config.getoption('dry')}
    if config.pluginmanager.hasplugin('logging') and config.getoption('log_cli_level', None) is None:  # live progress: the logging plugin configures after us
        config.option.log_cli_level = 'INFO'
    if config.option.xmlpath is None:
        config.option.xmlpath = os.path.join(args.outdir, 'junit.xml')
    if config.pluginmanager.hasplugin('html') and not getattr(config.option, 'htmlpath', None):
        config.option.htmlpath = os.path.join(args.outdir, 'report.html')
        config.option.self_contained_html = True
    os.makedirs(args.outdir)  # now, not by the session: the html report makes it at its start
    args.outdir_made = True


def define_tests(namespace):
    """Called by test_vm.py at import: test_<vm>, one test parametrized by the planned phases."""
    if CONFIG is None or not getattr(CONFIG, 'hc', None):
        return
    namespace[test_name(CONFIG.hc['vm'])] = phase_test


def pytest_generate_tests(metafunc):
    if metafunc.function is phase_test:
        tests = metafunc.config.hc['tests']
        metafunc.parametrize('hc_phase', [item for _, item in tests], ids=[tid for tid, _ in tests])


def pytest_report_header(config):
    hc = getattr(config, 'hc', None)
    if not hc:
        return None
    return ['hc-e2e: %s%s; homechooser_e2e.py %s' % (hc['vm'], ' (dry: no device)' if hc['dry'] else '',
                                                    ' '.join(hc['argv'])),
            'hc-e2e: results in %s' % hc['args'].outdir,
            'hc-e2e: planned phases (%s.x, one boot): %s' % (entry(hc['args'].vm)[0].split('.')[0],
                                                           ', '.join(t for t, _ in hc['tests']))]


def make_session(config):
    """The session factory of PhaseTests: the real VmSession, or the fake one with --dry; the progress
    listener is set on its Recorder at once, so the VM start is followed too (at DEBUG, its lines)."""
    hc = config.hc

    def make():
        if hc['dry']:
            from hc_e2e.dry import DrySession
            split = (lambda v: [x for x in v.split(',') if x])
            session = DrySession(hc['args'], split(config.getoption('dry_fail')),
                                 split(config.getoption('dry_raise')), split(config.getoption('dry_manual')))
        else:
            session = cli.VmSession(hc['args'], hc['ap'])
        session.rec.listener = progress
        return session
    return make


def progress(kind, text):
    """Recorder.listener: a short live line per step and per result; the rest at DEBUG (--log-cli-level=DEBUG)."""
    if kind in PROGRESS:
        LOG.info('%s %s', PROGRESS[kind], str(text).split('\n')[0][:160])
    else:
        LOG.debug('%s', text)


@pytest.fixture(scope='session')
def hc_run(request):
    """One VM run for the whole pytest session: started before the first phase test, cleaned up and stopped
    after the last one, whatever failed in between (a setup failure fails every test, the cleanup still runs)."""
    config = request.config
    tests = PhaseTests(make_session(config), config.hc['tests'])
    config.stash[RUN_KEY] = tests
    try:
        LOG.info('hc-e2e: %s: build or pick the ipks, start the VM, wait for root SSH (minutes on a device)',
                 config.hc['vm'])
        tests.open()
        LOG.info('hc-e2e: %s', tests.setup_error or 'the VM is up; the phases follow')
        yield tests
    finally:
        LOG.info('hc-e2e: the cleanup unless a test made it, then the VM stop')
        fails = tests.close()
    if fails:
        pytest.fail('the cleanup at the end recorded FAIL:\n' + '\n'.join(
            '%s %s: %s' % (r['status'], r['name'], r['evidence'][:300]) for r in fails), pytrace=False)


def phase_test(hc_run, hc_phase, record_property, request):
    """One phase of the run's plan (and, first, the earlier phases it needs that have not run)."""
    outcome = hc_run.run_test(hc_phase)
    request.node.hc_outcome = outcome
    record_property('phase', hc_phase[1])
    for status, n in sorted(outcome['counts'].items()):
        record_property(status, n)
    if outcome['setup']:
        record_property('setup', ', '.join(outcome['setup']))
    for path in outcome['screens']:
        record_property('screenshot', os.path.basename(path))
    setup = ('\n(ran first as setup: %s)' % ', '.join(outcome['setup'])) if outcome['setup'] else ''
    if outcome['status'] == 'skipped':
        pytest.skip(outcome['message'])
    if outcome['status'] == 'xfailed':
        pytest.xfail(outcome['message'])
    if outcome['status'] == 'failed':
        if hc_run.interrupted:
            request.session.shouldstop = 'interrupted (Ctrl-C): the run was cut short'  # no later phase runs
        pytest.fail(outcome['message'] + setup, pytrace=False)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """pytest-html: each phase's checks as text, and the screenshots of a failing phase embedded."""
    out = yield
    report = out.get_result()
    outcome = getattr(item, 'hc_outcome', None)
    if call.when != 'call' or outcome is None or not item.config.pluginmanager.hasplugin('html'):
        return
    from pytest_html import extras
    extra = getattr(report, 'extras', [])
    lines = ['%-7s %s  [%s]' % (r['status'], r['name'], r['step']) for r in outcome['results']]
    if outcome['setup']:
        lines.insert(0, 'setup (earlier phases run first): ' + ', '.join(outcome['setup']))
    extra.append(extras.text('\n'.join(lines) or '(no checks)', name='checks'))
    if report.failed:
        for path in outcome['screens']:
            with open(path, 'rb') as f:
                extra.append(extras.png(base64.b64encode(f.read()).decode(), name=os.path.basename(path)))
    report.extras = extra


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    tests = config.stash.get(RUN_KEY, None) if getattr(config, 'hc', None) else None
    if tests is None or tests.session is None:
        return
    tr = terminalreporter
    tr.section('hc-e2e results')
    if tests.interrupted:
        tr.write_line('INTERRUPTED (Ctrl-C): the run was cut short; it is reported as failed, not passed', red=True)
    tr.write_line('checks: %s' % (tests.counts or {}))
    tr.write_line('results: %s' % config.hc['args'].outdir)
    for name in ('run.log', 'results.json', 'junit.xml', 'report.html'):
        path = os.path.join(config.hc['args'].outdir, name)
        if os.path.exists(path) or name in ('junit.xml', 'report.html'):
            tr.write_line('  %s' % path)
