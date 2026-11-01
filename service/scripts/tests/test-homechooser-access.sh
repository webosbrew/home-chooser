#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Host-only manifest/permission tests. Optional shell argument, e.g. "busybox sh".
set -eu
SH=${1:-sh}
HERE=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT HUP INT TERM
export HOMECHOOSER_LUNA_ROOTS="$T/dev $T/system $T/absent"
export HOMECHOOSER_OSRELEASE="$T/os-release" HOMECHOOSER_LS_CONTROL="$T/control"
# As if the service's accessApply ran it; the boot/watcher case is below.
export HOMECHOOSER_PARENT_EXE=/media/developer/apps/usr/palm/services/org.webosbrew.homechooser.service/homechooser-service
printf '#!/bin/sh\n[ "$1" = homechooser-service ] && cat "%s/pids" 2>/dev/null\n' "$T" > "$T/pidof"
chmod +x "$T/pidof"
export HOMECHOOSER_PIDOF="$T/pidof"
printf '#!/bin/sh\necho "$*" >> "%s/scans"\n' "$T" > "$T/control"
chmod +x "$T/control"
for root in "$T/dev" "$T/system"; do
    mkdir -p "$root/manifests.d" "$root/api-permissions.d" "$root/services.d"
    printf '[D-BUS Service]\nName=org.webosbrew.homechooser.service\nExec=/homechooser-service\nType=dynamic\n' > "$root/services.d/org.webosbrew.homechooser.service.service"
done
# Pretty JSON, a space in a string, absent arrays and existing entries must all survive.
printf '%s\n' '{ "id": "keep spaces", "rolesFiles": ["/other/role"] }' > "$T/dev/manifests.d/org.webosbrew.homechooser.json"
printf '%s\n' '{"groupsFiles":["/other/group"],"apiPermissionFiles":["/other/api"]}' > "$T/system/manifests.d/org.webosbrew.homechooser.json"
# The installer's own api file for the service: never written, moved or removed by us.
INSTALLER_API='{"org.webosbrew.homechooser.service.group":["org.webosbrew.homechooser.service/*"]}'
for root in "$T/dev" "$T/system"; do
    printf '%s\n' "$INSTALLER_API" > "$root/api-permissions.d/org.webosbrew.homechooser.service.api.json"
done
run() { $SH "$HERE/homechooser-access" "$@"; }
verify() {
    python3 - "$T" "$1" "$INSTALLER_API" "$HERE/../../api/launcher-v1.openrpc.json" <<'PY2'
import json, pathlib, sys
root=pathlib.Path(sys.argv[1]); mode=sys.argv[2]; installer=sys.argv[3]
spec=json.loads(pathlib.Path(sys.argv[4]).read_text())
# Every launcher method of the spec: what the permission grants, and getInfo.
spec_methods=spec['x-permission']['grants']+spec['x-permission']['notNeededFor']
for name in ['dev','system']:
    r=root/name
    m=json.loads((r/'manifests.d/org.webosbrew.homechooser.json').read_text())
    if name=='dev': assert m['id']=='keep spaces' and m['rolesFiles']==['/other/role']
    else:
        assert m['groupsFiles']==['/other/group'] and '/other/api' in m['apiPermissionFiles']
    # ls-hubd crashes unloading a developer manifest with groupsFiles: ours never adds one.
    if name=='dev': assert 'groupsFiles' not in m
    assert not (r/'groups.d/homechooser.groups.json').exists()
    assert (r/'api-permissions.d/org.webosbrew.homechooser.service.api.json').read_text().strip()==installer
    path=r/'api-permissions.d/org.webosbrew.homechooser.launcher.api.json'
    assert m.get('apiPermissionFiles',[]).count(str(path))==(0 if mode=='removed' else 1)
    assert path.exists()==(mode!='removed')
    if mode=='removed': continue
    api=json.loads(path.read_text())
    expected=api['org.webosbrew.homechooser.applications']
    assert sorted(expected)==sorted('org.webosbrew.homechooser.service/'+s for s in spec_methods), expected
    assert ('ares.webos.cli' in api)==(mode=='11')
    if mode=='11': assert api['ares.webos.cli']==expected
    # 11+: the reads in LG's application.query, closeApp and uninstallApp in application.launcher;
    # never default.permission. <=10: none of LG's groups.
    svc='org.webosbrew.homechooser.service/'
    writes={svc+'closeApp', svc+'uninstallApp'}
    lg={k for k in api if k not in ('org.webosbrew.homechooser.applications','ares.webos.cli')}
    if mode=='11':
        assert lg=={'application.query','application.launcher'}, lg
        assert set(api['application.launcher'])==writes
        assert set(api['application.query'])==set(expected)-writes
    else:
        assert not lg, lg
assert not (root/'absent').exists()
PY2
}
echo 'VERSION_ID="10.0.0"' > "$T/os-release"
run apply > "$T/out"
verify 10
# The service restart request is for 11+ only.
if grep -q restart-service "$T/out"; then exit 1; fi
[ "$(wc -l < "$T/scans")" -eq 1 ]
run apply
[ "$(wc -l < "$T/scans")" -eq 1 ]
run status > "$T/status"
grep -q 'manifest=yes api=yes' "$T/status"
[ "$(wc -l < "$T/scans")" -eq 1 ]
echo 'PRETTY_NAME="webOS 11.2.0"' > "$T/os-release"
run apply > "$T/out"
verify 11
grep -qx restart-service "$T/out"
[ "$(wc -l < "$T/scans")" -eq 2 ]
run apply > "$T/out"
if grep -q restart-service "$T/out"; then exit 1; fi
[ "$(wc -l < "$T/scans")" -eq 2 ]
# Run by the boot entry or the watcher: no message, the running service is stopped instead.
sleep 60 & victim=$!
echo "$victim" > "$T/pids"
echo '{}' > "$T/system/api-permissions.d/org.webosbrew.homechooser.launcher.api.json"
HOMECHOOSER_PARENT_EXE=/bin/sh run apply > "$T/out"
if grep -q restart-service "$T/out"; then exit 1; fi
if kill -0 "$victim" 2>/dev/null; then kill "$victim"; exit 1; fi
wait "$victim" 2>/dev/null || :
rm -f "$T/pids"
verify 11
[ "$(wc -l < "$T/scans")" -eq 3 ]
# Repair drift, without duplicating the entry in the manifest.
echo '{}' > "$T/dev/api-permissions.d/org.webosbrew.homechooser.launcher.api.json"
run apply > /dev/null
verify 11
[ "$(wc -l < "$T/scans")" -eq 4 ]
run remove
verify removed
[ "$(wc -l < "$T/scans")" -eq 5 ]
run remove
[ "$(wc -l < "$T/scans")" -eq 5 ]
# Removal still deletes our files if the package manifest was removed by the installer.
run apply
mv "$T/dev/manifests.d/org.webosbrew.homechooser.json" "$T/dev-manifest"
run remove
[ ! -f "$T/dev/api-permissions.d/org.webosbrew.homechooser.launcher.api.json" ]
[ -f "$T/dev/api-permissions.d/org.webosbrew.homechooser.service.api.json" ]
mv "$T/dev-manifest" "$T/dev/manifests.d/org.webosbrew.homechooser.json"
run remove
verify removed
if run bogus > /dev/null 2>&1; then exit 1; fi
echo "access: all checks passed ($SH)"
