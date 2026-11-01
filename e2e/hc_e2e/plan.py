# SPDX-License-Identifier: Apache-2.0
"""The plan of one VM run: PHASES (the scope table), run_plan(), and PlanRun, which runs it phase by phase."""
from .launchers import qml_support

# ---------------------------------------------------------------------------------------------
# Scopes (e2e/README.md "Scopes"): what a check depends on decides how often one VM run makes it.
#   per-release      independent of the launcher: made once, with the first launcher of the run;
#   per-window-type  depends only on normal vs overlay: once with the first launcher of each window type;
#   per-runtime      SAM launches this app as Home and the Home key reaches it: once per launcher.
# PHASES is the one table of it: every check the runner records is recorded inside one of these
# phases and results.json carries the phase's scope.  `guard` starts a crash window (check_no_crash at
# its end, 6-9); a phase without it runs inside the window of the phase before it.
# ---------------------------------------------------------------------------------------------
PER_RELEASE, PER_WINDOW, PER_RUNTIME = 'per-release', 'per-window-type', 'per-runtime'
PHASES = (
    # phase, scope, step, guard, what it checks
    ('baseline', PER_RELEASE, 0, False, 'release matches the row; no Home Chooser; Homebrew Channel; stock home id'),
    ('install', PER_RELEASE, 1, True, 'install Home Chooser and the launcher (and the API apps); elevation; '
                                      'the native generator accepts the firmware (6-9, when the plan has the cache phase)'),
    ('choose', PER_RELEASE, 2, True, 'the Home Chooser UI: first open, stale marker, list, dialog, Done toast, 11+ page '
                                     'stays; choice, record, watcher, init.d, key filter and flag (6-9), override (10+); '
                                     'device texts; reopen with the row ticked'),
    ('api-before', PER_RELEASE, 2, False, '2a, made inside choose while Home Chooser is open: the launcher API before '
                                          'the choice (smoke or full) and the request dialog'),
    ('screensaver', PER_RELEASE, 2, False, '--scenario screensaver|all: Home and the screensaver with the launcher chosen'),
    ('switch', PER_RUNTIME, 2, True, '2s, a later launcher on the same boot: install it, choose it in the list over the '
                                     'current one; Done toast, choice, record, watcher, key filter (6-9), override (10+)'),
    ('window-chosen', PER_WINDOW, 2, False, 'what the empty screen shows after a choice: preload policy and the home gate '
                                            '(6-9), idleApp (10+: the launcher, or support false for an overlay)'),
    ('home-proof', PER_RUNTIME, 3, True, 'Home from an ordinary app opens the launcher: sam launch proof, running'),
    ('qml-gate', PER_RUNTIME, 3, False, 'fixtures/qml/support.json says qml does not run on this release: XFAIL'),
    ('window-home', PER_WINDOW, 3, False, '10+: the watcher closes LG Home once the launcher is in front'),
    ('demo', PER_RUNTIME, 3, True, '3d, fixture:web.demo: OK on the demo opens its focused app'),
    ('api-selected', PER_RELEASE, 3, True, '3a, the launcher API with the launcher chosen (smoke or full)'),
    ('reboot', PER_WINDOW, 3, True, '3r: after a boot the choice holds, the watcher re-applies the policy, idleApp, the '
                                    'gate (6-9), the last input (10+ overlay); the API grants survive (first launcher)'),
    ('cache', PER_RELEASE, 3, True, '6-9 after the boot: no boot cache, no configd override; Home opens the launcher'),
    ('update', PER_RELEASE, 3, True, '3u: the current build over the old one: no watcher restart, choice kept'),
    ('launcher-update', PER_RELEASE, 3, True, '3v: a new version of the chosen launcher stays Home'),
    ('restore-repair', PER_RELEASE, 3, True, '3f: uninstall with the watcher stopped, then repair (ends the run)'),
    ('removal', PER_RELEASE, 4, True, '4: launcher removed: stock restore, the alert, Home opens stock; reinstall'),
    ('uninstall', PER_RELEASE, 5, True, '5: Home Chooser removed: residue gone, the toast, Home opens stock'),
    ('cleanup', PER_RELEASE, 6, False, '6: test apps removed, the watcher finished, the final screen'),
)
PHASE = {row[0]: dict(zip(('phase', 'scope', 'step', 'guard', 'checks'), row)) for row in PHASES}


def launcher_shape(L):
    """(runtime, window, demo) of a launcher: fixtures by their name, an author launcher by its appinfo."""
    fixture = getattr(L, 'fixture', None)
    runtime = fixture[0] if fixture else 'app'
    return runtime, 'overlay' if getattr(L, 'overlay', False) else 'normal', bool(getattr(L, 'is_demo', False))


def reboot_windows(major, windows, mode):
    """The window types a run reboots with (--reboot-check auto|all), in the order of `windows` (the run's
    launchers).  auto: one boot with the full-screen launcher (else the first one), plus one with the overlay
    launcher on 10+, whose boot behaviour differs (the last input launch); all: one per window type."""
    present = list(dict.fromkeys(windows))
    if not mode or not present:
        return []
    if mode == 'all':
        return present
    out = ['normal' if 'normal' in present else present[0]]
    if major >= 10 and 'overlay' in present and 'overlay' not in out:
        out.append('overlay')
    return [w for w in present if w in out]


def run_plan(shapes, major, opts):
    """The phases of one VM run on one boot, in order: [(launcher index, phase)].  shapes: launcher_shape()
    per launcher; opts: api, reboot ('', 'auto', 'all'), update, launcher_update, restore_repair,
    screensaver ('lifecycle', 'screensaver', 'all'), until.  The first runnable launcher makes the
    per-release phases and the whole lifecycle; each later one switches in, on the same boot, and makes
    only its scopes: its window type's checks when it is the first of its type, and its launch proof."""
    gated = [runtime == 'qml' and qml_support(major)[0] is False for runtime, _, _ in shapes]
    runnable = [i for i in range(len(shapes)) if not gated[i]]
    until = opts.get('until', 5)
    if not runnable:
        return [(0, 'baseline')] + [(i, 'qml-gate') for i in range(len(shapes))] + ([(0, 'cleanup')] if until >= 5 else [])
    owner = runnable[0]
    window_owner = {}
    for i in runnable:
        window_owner.setdefault(shapes[i][1], i)
    reboots = reboot_windows(major, [shapes[i][1] for i in runnable], opts.get('reboot'))
    hook = 6 <= major <= 9
    plan, extras, cache_done, ended = [(owner, 'baseline')], [], False, False

    def chosen(i, out):
        if window_owner.get(shapes[i][1]) == i:
            out.append((i, 'window-chosen'))

    def home(i, out):
        nonlocal cache_done
        owns = window_owner.get(shapes[i][1]) == i
        out.append((i, 'home-proof'))
        if owns and major >= 10:
            out.append((i, 'window-home'))
        if shapes[i][2]:
            out.append((i, 'demo'))
        if i == owner and opts.get('api'):
            out.append((i, 'api-selected'))
        if owns and shapes[i][1] in reboots:
            out.append((i, 'reboot'))
            if hook and not cache_done and not opts.get('update'):
                out.append((i, 'cache'))
                cache_done = True
    for i in range(len(shapes)):
        if gated[i]:
            (plan if i < owner else extras).append((i, 'qml-gate'))
            continue
        if i == owner:
            plan.append((i, 'install'))
            plan.append((i, 'choose'))
            if opts.get('api'):
                plan.append((i, 'api-before'))
            chosen(i, plan)
            if opts.get('screensaver') in ('screensaver', 'all'):
                plan.append((i, 'screensaver'))
                if opts.get('screensaver') == 'screensaver':
                    ended = True
                    break
            home(i, plan)
            for flag, phase in (('update', 'update'), ('launcher_update', 'launcher-update'),
                                ('restore_repair', 'restore-repair')):
                if opts.get(flag):
                    plan.append((i, phase))
            if opts.get('restore_repair'):
                ended = True  # it uninstalls Home Chooser: nothing else can follow on this boot
                break
            plan.append((i, 'removal'))
        else:
            extras.append((i, 'switch'))
            chosen(i, extras)
            home(i, extras)
    if not ended:
        plan += extras + [(owner, 'uninstall')]
    plan.append((owner, 'cleanup'))
    # --until N: the head's phases up to step N; later launchers come after step 4; cleanup only with 5
    extra = {(i, p) for i, p in extras}
    return [(i, p) for i, p in plan if (4 if (i, p) in extra else PHASE[p]['step']) <= until
            or (p == 'cleanup' and until >= 5)]


def plan_groups(plan):
    """The crash windows of a plan: a guarded phase starts one, the phases without a guard join it."""
    groups = []
    for item in plan:
        if not groups or PHASE[item[1]]['guard'] or groups[-1][0][0] != item[0]:
            groups.append([item])
        else:
            groups[-1].append(item)
    return groups


def describe_plan(plan, launchers):
    """One line per launcher: its phases, for the run log."""
    out = []
    for i, p in plan:
        name = launchers[i] if isinstance(launchers[i], str) else getattr(launchers[i], 'spec', str(i))
        if out and out[-1][0] == name:
            out[-1][1].append(p)
        else:
            out.append((name, [p]))
    return ['%s: %s' % (n, ', '.join(ps)) for n, ps in out]
