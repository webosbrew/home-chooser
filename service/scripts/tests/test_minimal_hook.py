# SPDX-License-Identifier: Apache-2.0
"""Native JS-only generator against read-only firmware corpus; no vendor sources copied to repo."""
import os
from pathlib import Path
import re
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]

def corpus():
    env = dict(os.environ)
    env.setdefault('OPENHOUSE_FIRMWARES', str(Path.home() / 'Projects/webos-firmwares'))
    env.setdefault('OPENHOUSE_HYPERVISOR', str(Path.home() / 'Projects/webos-hypervisor'))
    env.setdefault('OPENHOUSE_STOCK_COPIES', str(REPO / 'scratch/hk-universal/rw/stock'))
    env.setdefault('OPENHOUSE_WEBOS71', str(Path.home() / 'Temp/webos-7.1.0'))
    for line in (HERE / 'corpus.tsv').read_text().splitlines():
        if not line or line.startswith('#'): continue
        name, version, kind, root = line.split('\t')
        yield name, version, Path(re.sub(r'\$\{(\w+)\}', lambda m: env[m[1]], root))

if __name__ == '__main__':
    count = skipped = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        binary = tmp / 'generator'
        flags = subprocess.check_output(['pkg-config', '--cflags', '--libs', 'glib-2.0'], text=True).split()
        subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-I'+str(REPO/'service/src'),
                        str(REPO/'service/test/homekey-generator.c'), str(REPO/'service/src/homekey-generator.c'),
                        '-o', str(binary), *flags], check=True)
        for name, version, root in corpus():
            source = root / 'usr/lib/qml/KeyFilters/systemUi.js'
            if not source.is_file():
                print('SKIP', name, 'source absent:', source); skipped += 1; continue
            if not 6 <= int(version.split('.')[0]) <= 9: continue
            stock = source.read_bytes()
            result = subprocess.run([binary, source, 'test.home'], capture_output=True)
            assert result.returncode == 0, (name, result.stderr)
            patched = result.stdout
            a = stock.index(b'\nfunction launchHomeApp(key) {'); b = stock.index(b'\n}\n', a) + 3
            # Exact byte oracle: only the constant and stock arguments in this function change.
            expected = stock[:a] + stock[a:b].replace(b'"com.webos.app.home"', b'"test.home"') + stock[b:]
            expected = expected.replace(b'var HOME_APP_ID = "com.webos.app.home";', b'var HOME_APP_ID = "test.home";')
            assert patched == expected, name
            js = tmp / 'systemUi.js'; js.write_bytes(patched)
            subprocess.run(['node', '--check', js], check=True, capture_output=True)
            # Execute both NMRM branches, retaining access/disable gates and launch parameters.
            body = patched[patched.index(b'\nfunction launchHomeApp(key) {'):].split(b'\n}\n', 1)[0]+b'\n}'
            harness = b'''const vm=require('vm'); const assert=require('assert');
const code=process.argv[1]; for(const nmrm of [false,true]) for(const disabled of [false,true]) {
 let calls=[]; const c={access:{ribbon:true},overlayView:{access:{ribbon:true}},settingsService:{disableHome:disabled},
 bootManager:{bootFirstUse:false},systemPropertyService:{powerOnlyMode:false},pmLog:{info(){}},
 configdService:{supportNMRM:nmrm},getActivateType:k=>k,
 applicationManager:{launch:(...a)=>calls.push(a),checkLaunch:(...a)=>calls.push(a)}};
 vm.createContext(c);vm.runInContext(code,c);c.launchHomeApp(773);
 assert.equal(calls.length,disabled?0:1); if(!disabled){assert.equal(calls[0][0],'test.home');assert.equal(JSON.parse(calls[0][1]).activateType,773);}
}'''
            subprocess.run(['node', '-e', harness.decode(), body.decode()], check=True)
            for mutation in [stock.replace(b'function launchHomeApp(key)', b'function launchHomeApp(other)'),
                             stock[:a]+stock[a:b].replace(b'applicationManager.launch',b'applicationManager.newLaunch')+stock[b:],
                             b'var HOME_APP_ID = \"other\";\n'+stock]:
                js.write_bytes(mutation)
                r = subprocess.run([binary, js, 'test.home'], capture_output=True)
                assert r.returncode == 2 and not r.stdout, name
            r = subprocess.run([binary, source, 'bad"id'], capture_output=True)
            assert r.returncode == 2 and not r.stdout
            count += 1
            print('PASS', name, 'exact edits, executable branches/gates, syntax, unknown-shape refusal')
    assert count, 'no firmware sources tested'
    print(f'{count} firmware sources passed; {skipped} missing sources')
