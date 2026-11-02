# SPDX-License-Identifier: Apache-2.0
"""Firmware update simulations for the Home key guard: the real stock systemUi.js of one firmware is
swapped for the next one between two boots (test-fwupdate.c does the boots against fake configd), plus
synthetic updates built from the base file. Read-only use of the firmware corpus, copies only.

  fwupdate-guard.py PROGRAM WORKDIR BASE_SYSTEMUI.JS [QEMU...]

Sources come from service/scripts/tests/corpus.tsv (same environment variables) and three extra
files that tell what an unrecognised firmware looks like. A missing source skips the pairs that use it;
at least one real pair must run."""
import os, re, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
JS = 'usr/lib/qml/KeyFilters/systemUi.js'

def sources():
    env = dict(os.environ)
    home = Path.home()
    env.setdefault('OPENHOUSE_FIRMWARES', str(home / 'Projects/webos-firmwares'))
    env.setdefault('OPENHOUSE_HYPERVISOR', str(home / 'Projects/webos-hypervisor'))
    env.setdefault('OPENHOUSE_STOCK_COPIES', str(REPO / 'scratch/hk-universal/rw/stock'))
    env.setdefault('OPENHOUSE_WEBOS71', str(home / 'Temp/webos-7.1.0'))
    out = {}
    lines = (HERE.parent / 'scripts/tests/corpus.tsv').read_text().splitlines()
    # Firmware that is not a 6-9 one: what the guard sees when an update moves to 10, or an unknown build.
    lines.append('10.3.1-W22H\t10.3.1\tlive\t${OPENHOUSE_HYPERVISOR}/scratch/firmware/33.31.61.01-HE_DTV_W22H_AFABJAPU/rootfs')
    for line in lines:
        if not line or line.startswith('#'): continue
        name, version, _kind, root = line.split('\t')
        path = Path(re.sub(r'\$\{(\w+)\}', lambda m: env[m[1]], root)) / JS
        if not path.is_file():  # 6.3.1 is only kept as a stock copy
            path = Path(env['OPENHOUSE_STOCK_COPIES']) / name / JS
        if path.is_file(): out[name] = (version, path)
    return out

# (name, [(os version, expectation, source id)...]); R regenerated, S stock, N not 6-9 (see test-fwupdate.c)
REAL = [
    ('6.3.1 -> 6.5.2 (launchType added)',       [('6.3.1', 'R', '6.3.1-W21Z'), ('6.5.2', 'R', '6.5.2-W21A')]),
    ('6.5.2 -> 6.6.0',                          [('6.5.2', 'R', '6.5.2-W21A'), ('6.6.0', 'R', '6.6.0-6-W21P')]),
    ('6.6.0 build 6 -> 12 (same file)',         [('6.6.0', 'R', '6.6.0-6-W21P'), ('6.6.0', 'R', '6.6.0-12-W21U')]),
    ('6.6.0 -> 7.4.0 (NMRM branch)',            [('6.6.0', 'R', '6.6.0-12-W21U'), ('7.4.0', 'R', '7.4.0-W22O')]),
    ('7.1.0 -> 7.4.0',                          [('7.1.0', 'R', '7.1.0-tmp'), ('7.4.0', 'R', '7.4.0-W22O')]),
    ('7.4.0 -> 7.6.0 (HOME_APP_ID appears)',    [('7.4.0', 'R', '7.4.0-W22O'), ('7.6.0', 'R', '7.6.0-18-W22H')]),
    ('7.6.0 build 18 -> 19 (same file)',        [('7.6.0', 'R', '7.6.0-18-W22H'), ('7.6.0', 'R', '7.6.0-19-W22P')]),
    ('7.6.0 -> 8.3.0 (S23Z)',                   [('7.6.0', 'R', '7.6.0-19-W22P'), ('8.3.0', 'R', '8.3.0-1063-S23Z')]),
    ('8.3.0 build 1063 -> 2301',                [('8.3.0', 'R', '8.3.0-1063-S23Z'), ('8.3.0', 'R', '8.3.0-2301-W22O')]),
    ('8.3.0 -> 8.4.0',                          [('8.3.0', 'R', '8.3.0-2301-W22O'), ('8.4.0', 'R', '8.4.0-W23A')]),
    ('8.4.0 -> 9.2.1',                          [('8.4.0', 'R', '8.4.0-W23A'), ('9.2.1', 'R', '9.2.1-40-W22H')]),
    ('9.2.1 -> 9.2.4',                          [('9.2.1', 'R', '9.2.1-40-W22H'), ('9.2.4', 'R', '9.2.4-W24P')]),
    ('9.2.4 -> 8.4.0 (downgrade)',              [('9.2.4', 'R', '9.2.4-W24P'), ('8.4.0', 'R', '8.4.0-W23A')]),
    ('whole path 6.3.1 -> 9.2.4, one state dir', [('6.3.1', 'R', '6.3.1-W21Z'), ('6.5.2', 'R', '6.5.2-W21A'), ('6.6.0', 'R', '6.6.0-12-W21U'),
                                                  ('7.4.0', 'R', '7.4.0-W22O'), ('7.6.0', 'R', '7.6.0-19-W22P'), ('8.3.0', 'R', '8.3.0-1063-S23Z'),
                                                  ('8.4.0', 'R', '8.4.0-W23A'), ('9.2.1', 'R', '9.2.1-40-W22H'), ('9.2.4', 'R', '9.2.4-W24P')]),
    # Real files this guard has never seen under a 6-9 release: it must fall back to stock.
    ('9.2.4 -> 10.3.1 file (major 10: guard off)',   [('9.2.4', 'R', '9.2.4-W24P'), ('10.3.1', 'N', '10.3.1-W22H')]),
    ('7.6.0 -> 10.3.1 shape under a 7.x os-release', [('7.6.0', 'R', '7.6.0-19-W22P'), ('7.6.1', 'S', '10.3.1-W22H')]),
    ('7.6.0 -> 5.6.1 file (no launchHomeApp)',       [('7.6.0', 'R', '7.6.0-19-W22P'), ('7.6.1', 'S', '5.6.1-W20L')]),
    ('unknown build, then a known one again',        [('9.2.4', 'R', '9.2.4-W24P'), ('9.3.0', 'S', '10.3.1-W22H'), ('9.3.1', 'R', '9.2.4-W24P')]),
]

def mutations(base):
    """Synthetic updates of the base file: (name, bytes or None)."""
    a = base.index('\nfunction launchHomeApp(key) {')
    b = base.index('\n}\n', a) + 3
    fn = base[a:b]
    m = [
        ('launchHomeApp argument renamed', base.replace('function launchHomeApp(key)', 'function launchHomeApp(keyCode)')),
        ('launch call renamed', base[:a] + fn.replace('applicationManager.launch', 'applicationManager.launchApp') + base[b:]),
        ('statement added to launchHomeApp', base[:a] + fn.replace('\n}\n', '\n    pmLog.info("HOME_KEY", {}, "", true);\n}\n') + base[b:]),
        ('launchHomeApp defined twice', base + fn),
        ('launchHomeApp gone', base[:a] + base[b:]),
        ('truncated inside launchHomeApp', base[:a + 200]),
        ('empty file', ''),
        ('HOME_APP_ID defined differently', base.replace('var HOME_APP_ID = "com.webos.app.home";', 'var HOME_APP_ID = "com.webos.app.main";')),
        ('HOME_APP_ID defined twice', base.replace('var HOME_APP_ID = "com.webos.app.home";', 'var HOME_APP_ID = "com.webos.app.home";\nvar HOME_APP_ID = "x";')),
    ]
    return m

def main():
    program, workdir, base_path, *qemu = sys.argv[1:]
    base = Path(base_path).read_text()
    work = Path(workdir).resolve()
    shutil.rmtree(work, ignore_errors=True); work.mkdir(parents=True)
    files = work / 'files'; files.mkdir()
    src = sources()
    cases = []
    ran_real = skipped = 0
    for name, steps in REAL:
        if any(s[2] not in src for s in steps):
            print('SKIP', name, '(source absent: %s)' % ', '.join(sorted({s[2] for s in steps if s[2] not in src})))
            skipped += 1; continue
        cases.append((name, [(v, e, str(src[i][1])) for v, e, i in steps], True))
    bp = files / 'base.js'; bp.write_text(base)
    cases.append(('synthetic: identical file, new version string', [('7.6.0', 'R', str(bp)), ('7.6.1', 'R', str(bp))], False))
    cases.append(('synthetic: stock file removed by the update', [('7.6.0', 'R', str(bp)), ('7.6.1', 'S', '-')], False))
    benign = files / 'benign.js'; benign.write_text(base.replace('Copyright (c)', 'Copyright (c) 2099 ', 1) + '\nfunction addedByTheUpdate() { return 1; }\n')
    cases.append(('synthetic: change outside launchHomeApp (known shape)', [('7.6.0', 'R', str(bp)), ('7.6.1', 'R', str(benign))], False))
    cases.append(('synthetic: removed, then restored by the next update', [('7.6.0', 'R', str(bp)), ('7.6.1', 'S', '-'), ('7.6.2', 'R', str(bp))], False))
    for i, (name, text) in enumerate(mutations(base)):
        p = files / ('mut%d.js' % i); p.write_text(text)
        cases.append(('synthetic: ' + name, [('7.6.0', 'R', str(bp)), ('7.6.1', 'S', str(p))], False))
    cases.append(('synthetic: a new function nobody reviewed, then back', [('7.6.0', 'R', str(bp)), ('7.6.1', 'S', str(files / 'mut0.js')), ('7.6.2', 'R', str(bp))], False))
    # Without a reboot (a watcher refresh on a filter that is already active).
    mut = {n: files / ('mut%d.js' % i) for i, (n, _) in enumerate(mutations(base))}
    cases.append(('synthetic live: unknown shape under an active filter', [('7.6.0', 'R', str(bp)), ('live:7.6.0', 'S', str(mut['launchHomeApp gone']))], False))
    cases.append(('synthetic live: known file replaced under an active filter', [('7.6.0', 'R', str(bp)), ('live:7.6.1', 'R', str(benign))], False))
    cases.append(('synthetic live: stock file vanishes under an active filter', [('7.6.0', 'R', str(bp)), ('live:7.6.0', 'K', '-')], False))
    failed = 0
    for n, (name, steps, real) in enumerate(cases):
        d = work / ('case%d' % n)
        args = [*qemu, program, str(d)] + ['%s:%s:%s' % s for s in steps]
        r = subprocess.run(args, capture_output=True, text=True, timeout=300)
        if r.returncode != 0 or 'PASS' not in r.stdout:
            failed += 1
            print('FAIL', name); print(r.stdout[-1500:]); print(r.stderr[-1500:])
        else:
            print('PASS', name)
            for line in r.stdout.splitlines():
                if line.startswith('NOTE'): print('  ' + line)
            ran_real += real
    print('%d scenarios, %d failed, %d real-firmware scenarios ran, %d skipped for missing sources' % (len(cases), failed, ran_real, skipped))
    if failed: return 1
    if not ran_real: print('FAIL no real firmware pair could run'); return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
