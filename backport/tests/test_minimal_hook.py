"""No LG sources are stored here. Corpus roots are read-only; all outputs are temporary."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
BACKPORT = HERE.parent
GEN = BACKPORT / 'homechooser-keyfilter-gen.js'
NODE = os.environ.get('NODE', 'node')
Q = 'usr/lib/qml/'
SET = Q + 'WebOSCompositorBase/global/Settings.qml'
KF = Q + 'WebOSCompositor/controllers/StarfishKeyFilter.qml'
SUI = Q + 'KeyFilters/systemUi.js'
NUDGE = Q + 'WebOSCompositor/views/StarfishNudgeView.qml'
RECENTS = Q + 'WebOSCompositor/controllers/fullscreen/StarfishRecents.qml'
FSC = Q + 'WebOSCompositor/controllers/StarfishFullscreenController.qml'
LUNA = Q + 'WebOSCompositor/services/StarfishBaseLunaServiceAPI.qml'
PASS = FAIL = SKIP = BUILDS = REVIEWED = SCENARIOS = 0


def run(*args):
    return subprocess.run([str(a) for a in args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding='latin1', timeout=90)


def check(label, ok, detail=''):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print('FAIL: ' + label + ': ' + detail, flush=True)


def put(root, file, text):
    p = root / file
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='latin1')


def corpus():
    env = dict(os.environ)
    projects = Path.home() / 'Projects'
    env.setdefault('OPENHOUSE_FIRMWARES', str(projects / 'webos-firmwares'))
    env.setdefault('OPENHOUSE_HYPERVISOR', str(projects / 'webos-hypervisor'))
    env.setdefault('OPENHOUSE_STOCK_COPIES', str(BACKPORT.parent / 'scratch/hk-universal/rw/stock'))
    env.setdefault('OPENHOUSE_WEBOS71', str(Path.home() / 'Temp/webos-7.1.0'))
    rows = []
    for line in Path(env.get('OPENHOUSE_CORPUS', HERE / 'corpus.tsv')).read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        name, version, kind, root = line.split('\t')
        root = re.sub(r'\$\{(\w+)\}', lambda m: env[m[1]], root)
        rows.append((name, version, kind, Path(root)))
    return rows


def synthetic(root):
    # Purpose-written anchor skeletons; these are not firmware copies.
    put(root, 'etc/os-release', 'VERSION_ID="9.2.4"\n')
    put(root, SET, 'pragma Singleton\nimport QtQuick 2.4\nimport WebOSCompositorBase 1.0\nQtObject {\n    id: root\n    function subscribe(type, method, key, returnRaw) {\n        return null;\n    }\n}\n')
    put(root, KF, 'import QtQuick 2.4\nimport WebOSCompositorBase 1.0\nQtObject {\n    readonly property QtObject applicationManager: QtObject {\n        readonly property var launch: function(appId, params, checkUpdateOnLaunch, autoInstallation, reason) {\n        }\n        readonly property var checkLaunch: function(appId, params, checkUpdateOnLaunch, autoInstallation) {\n        }\n    }\n}\n')
    put(root, SUI, 'var HOME_APP_ID = "com.webos.app.home";\nfunction handleHomeKeys(key, pressed, autoRepeat) {\n    if (fullscreenView.appId !== HOME_APP_ID) applicationManager.launch("com.webos.app.home", JSON.stringify({}));\n}\n')
    for file in [NUDGE, LUNA]:
        put(root, file, 'import QtQuick 2.4\nimport WebOSCompositorBase 1.0\nQtObject {\n    property bool home: views.fullscreen.currentItem.appId === "com.webos.app.home"\n}\n')
    put(root, RECENTS, 'import QtQuick 2.4\nimport WebOSCompositorBase 1.0\nQtObject {\n    property bool keep: item.appId !== "com.webos.app.home"\n    property bool candidate: item.appId !== __lastInputApp\n    property string lastInput: supportNMRM ? "com.webos.app.home" : ""\n}\n')
    put(root, FSC, 'import QtQuick 2.4\nimport WebOSCompositorBase 1.0\nQtObject {\n    property string lastInput: supportNMRM ? "com.webos.app.home" : ""\n}\n')


def reviewed(root, dest):
    for manifest in sorted(BACKPORT.glob('*/manifest.json')):
        entries = json.loads(manifest.read_text())['files']
        if not all((root / e['path'].lstrip('/')).is_file() and
                   hashlib.sha256((root / e['path'].lstrip('/')).read_bytes()).hexdigest() == e['sha256']
                   for e in entries):
            continue
        for e in entries:
            p = Path(e['path'].lstrip('/'))
            (dest / p).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / p, dest / p)
            r = subprocess.run(['patch', '-p0', '-F0', '-s'], cwd=dest,
                               input=(manifest.parent / e['diff']).read_bytes(), capture_output=True)
            if r.returncode:
                raise RuntimeError(r.stderr.decode())
        return manifest.parent.name
    return None


def restore(file, text):
    """Remove only our known insertions/substitutions; remaining bytes must be stock."""
    if file == SET:
        start = text.index('\n    // Home Chooser:')
        text = text[:start] + text[text.rindex('\n}') + 1:]
        return text.replace('import WebOSServices 1.0\n', '', 1)
    if file in [KF, SUI]:
        return ''.join(line for line in text.splitlines(True) if '// Home Chooser' not in line)
    text = re.sub(r'(\b([\w.]+)\.appId !== __lastInputApp) && \2\.appId !== "com.webos.app.home" && !Settings.isHomeApp\(\2\.appId\)', r'\1', text)
    text = re.sub(r' && !Settings.isHomeApp\([\w.]+\)', '', text)
    text = re.sub(r'Settings.isHomeApp\(([\w.]+)\)', r'\1 === "com.webos.app.home"', text)
    return text.replace('Settings.getHomeAppId()', '"com.webos.app.home"')


def build(name, version, root, work, harness=True):
    global BUILDS, REVIEWED, SCENARIOS
    out, out2 = work / 'out', work / 'out2'
    r = run(NODE, GEN, 'generate', '--root', root, '--out', out, '--manifest', work / 'manifest.json')
    major = int(version.split('.')[0])
    if not 6 <= major <= 9:
        check(name + ': refuses version', r.returncode == 2, r.stdout)
        check(name + ': refusal writes nothing', not out.exists())
        r = run(NODE, GEN, 'check', '--root', root, '--skip-version-gate')
        check(name + ': structural negative control', r.returncode == 2, r.stdout)
        return
    check(name + ': generate', r.returncode == 0, r.stdout)
    if r.returncode:
        return
    BUILDS += 1
    manifest = json.loads((work / 'manifest.json').read_text())
    files = [e['path'].lstrip('/') for e in manifest['files']]
    check(name + ': tier 1 and maximum seven files', SET in files and KF in files and 2 <= len(files) <= 7)
    run(NODE, GEN, 'generate', '--root', root, '--out', out2)
    check(name + ': deterministic', all((out / f).read_bytes() == (out2 / f).read_bytes() for f in files))
    check(name + ': check agrees', run(NODE, GEN, 'check', '--root', root).returncode == 0)
    for f in files:
        text = (out / f).read_text(encoding='latin1')
        stock = (root / f).read_text(encoding='latin1')
        check(name + ': changed ' + f, text != stock)
        check(name + ': removing edits restores stock ' + f, restore(f, text) == stock)
        tool = ['qmllint'] if f.endswith('.qml') else [NODE, '--check']
        if shutil.which(tool[0]):
            lint = run(*tool, out / f)
            check(name + ': syntax ' + f, lint.returncode == 0, lint.stdout)
    if RECENTS in files and harness:
        stock = (root / RECENTS).read_text(encoding='latin1')
        patched = (out / RECENTS).read_text(encoding='latin1')
        pattern = r'if \((!appExist && [^\n]*)\) \{'
        before, after = re.search(pattern, stock), re.search(pattern, patched)
        if before and after:
            # Execute the actual kill predicate; LG Home and the chosen home must survive
            # regardless of the NMRM/record last input. Other apps retain the stock predicate.
            script = '''var old = new Function('item','appExist','__lastInputApp','Settings','return '+process.argv[1]);
var hook = new Function('item','appExist','__lastInputApp','Settings','return '+process.argv[2]);
var stock='com.webos.app.home', chosen='org.example.launcher';
[stock,chosen].forEach(function(home) {
  [stock,'com.webos.app.livetv'].forEach(function(input) {
    [stock,chosen,input,'org.example.other','com.webos.app.homeconnect'].forEach(function(id) {
      [false,true].forEach(function(exists) {
        var item={appId:id}, settings={isHomeApp:function(app){return app===home;}};
        var expected=old(item,exists,input,settings) && id!==stock && id!==home;
        if (hook(item,exists,input,settings)!==expected) throw Error(id+' home='+home+' input='+input);
      });
    });
  });
});'''
            result = run(NODE, '-e', script, before[1], after[1])
            check(name + ': recents spares both homes and preserves other exclusions', result.returncode == 0, result.stdout)
            if '__lastInputApp' in before[1]:
                default = r'property string __lastInputApp:[^\n]*'
                check(name + ': recents last-input property stays stock',
                      re.search(default, stock)[0] == re.search(default, patched)[0])
        else:
            check(name + ': recents kill predicate found', False)
    # Whole output plus a version lets a second run exercise the double-patch guard.
    put(out, 'etc/os-release', 'VERSION_ID="' + version + '"\n')
    if not (out / SUI).exists():
        put(out, SUI, (root / SUI).read_text(encoding='latin1'))
    r = run(NODE, GEN, 'check', '--root', out)
    check(name + ': rejects second patch', r.returncode == 2 and 'already' in r.stdout, r.stdout)
    if not harness:
        return
    stock_api = (root / LUNA).read_text(encoding='latin1')
    check(name + ': stock QML readiness method exists', 'function getCurrentState(msg)' in stock_api and '"getCurrentState"' in stock_api)
    ref = work / 'ref'
    release = reviewed(root, ref)
    args = ['stock=' + str(root / SUI), 'min=' + str(out / SUI)]
    if release:
        args.append('ref=' + str(ref / SUI))
        REVIEWED += 1
    h = run(NODE, HERE / 'home-key-harness.js', *args)
    (work / 'harness.tsv').write_text(''.join(line + '\n' for line in h.stdout.splitlines() if '\t' in line))
    v = run(sys.executable, '-I', HERE / 'home-key-verdict.py', work / 'harness.tsv', name)
    check(name + ': behaviour' + (' == reviewed ' + release if release else ''), h.returncode == v.returncode == 0, v.stdout)
    SCENARIOS += 144
    print(v.stdout.strip(), flush=True)


def mutations(root, work):
    changes = [
        ('dup-launch', KF, lambda t: t + '\n' + re.search(r'^\s*readonly property var launch:.*$', t, re.M)[0] + '\n}\n', 'matched 2 times'),
        ('renamed-launch', KF, lambda t: t.replace('function(appId, params, checkUpdateOnLaunch, autoInstallation, reason)', 'function(id, params, checkUpdateOnLaunch, autoInstallation, reason)'), 'matched 0 times'),
        ('const-home', SUI, lambda t: t.replace('var HOME_APP_ID', 'const HOME_APP_ID'), 'stock id is used other than'),
        ('outside-use', SUI, lambda t: t + '\nvar other = HOME_APP_ID;\n', 'outside handleHomeKeys'),
        ('literal-compare', SUI, lambda t: t + '\nvar other = x === "com.webos.app.home";\n', 'stock id is used other than'),
        ('non-comparison', SUI, lambda t: t.replace('function handleHomeKeys(key, pressed, autoRepeat) {', 'function handleHomeKeys(key, pressed, autoRepeat) {\n    var x = [HOME_APP_ID];'), 'other than in a comparison'),
        ('settings-id', SET, lambda t: t.replace('id: root', 'id: settingsRoot'), 'id: root'),
        ('no-launch', SUI, lambda t: t.replace('launch("com.webos.app.home",', 'launch(HOME_APP_ID,'), 'no launch of the stock home'),
        ('duplicate-singleton', SET, lambda t: 'pragma Singleton\n' + t, 'singleton'),
    ]
    for name, file, change, reason in changes:
        dest = work / name
        shutil.copytree(root, dest)
        put(dest, file, change((dest / file).read_text(encoding='latin1')))
        r = run(NODE, GEN, 'generate', '--root', dest, '--out', dest / 'out')
        check(name + ': specific tier-1 refusal, no writes', r.returncode == 2 and reason in r.stdout and not (dest / 'out').exists(), r.stdout)
    dest = work / 'tier2'
    shutil.copytree(root, dest)
    put(dest, NUDGE, (dest / NUDGE).read_text().replace('views.fullscreen.currentItem.appId === "com.webos.app.home"', '"com.webos.app.home" === views.fullscreen.currentItem.appId'))
    r = run(NODE, GEN, 'generate', '--root', dest, '--out', dest / 'out')
    check('tier-2 drift skips only its file', r.returncode == 0 and 'unpatched-sites=1' in r.stdout and NUDGE in r.stdout and not (dest / 'out' / NUDGE).exists(), r.stdout)
    dest = work / 'recents-drift'
    shutil.copytree(root, dest)
    put(dest, RECENTS, (dest / RECENTS).read_text().replace('item.appId !== __lastInputApp', 'item.appId != __lastInputApp'))
    r = run(NODE, GEN, 'generate', '--root', dest, '--out', dest / 'out')
    check('recents last-input drift skips its whole file', r.returncode == 0 and 'unpatched-sites=1' in r.stdout and not (dest / 'out' / RECENTS).exists(), r.stdout)
    for major in [5, 10, 11]:
        put(root, 'etc/os-release', 'VERSION_ID="%d.0.0"\n' % major)
        r = run(NODE, GEN, 'generate', '--root', root, '--out', work / ('v%d' % major))
        check('version gate %d' % major, r.returncode == 2 and not (work / ('v%d' % major)).exists(), r.stdout)


def main():
    global SKIP
    with tempfile.TemporaryDirectory(prefix='minhook-') as tmp:
        w = Path(tmp)
        root = w / 'synthetic'
        synthetic(root)
        build('synthetic', '9.2.4', root, w / 'synth-run', harness=False)
        mutations(root, w / 'mutations')
        existing = []
        for name, version, kind, root in corpus():
            if not (root / SET).exists():
                SKIP += 1
                print('SKIP: %s: missing firmware root %s' % (name, root), flush=True)
                continue
            work = w / name
            work.mkdir()
            existing.append((name, version, kind, root))
            if kind == 'copy':
                # Saved target files have no os-release. The study's corpus supplies version metadata.
                copied = work / 'stock'
                shutil.copytree(root, copied)
                put(copied, 'etc/os-release', 'VERSION_ID="' + version + '"\n')
                root = copied
            build(name, version, root, work)
        # The study matrix is generated from the same resolved corpus paths.
        corpusfile = w / 'corpus.tsv'
        corpusfile.write_text(''.join('\t'.join([n, v, k, str(r)]) + '\n' for n, v, k, r in existing))
        matrix = run(sys.executable, '-I', HERE / 'minimal-hook-matrix.py', corpusfile)
        check('pattern matrix runs', matrix.returncode == 0, matrix.stdout)
        print(matrix.stdout)
    print('%d passed, %d failed, %d skipped; %d supported builds, %d reviewed comparisons, %d Home-key scenarios' % (PASS, FAIL, SKIP, BUILDS, REVIEWED, SCENARIOS))
    return bool(FAIL)


if __name__ == '__main__':
    if '--list-corpus' in sys.argv:
        for name, version, kind, root in corpus():
            if 6 <= int(version.split('.')[0]) <= 9:
                print(name + '=' + str(root))
    else:
        sys.exit(main())
