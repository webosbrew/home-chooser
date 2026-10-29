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
printf '#!/bin/sh\necho "$*" >> "%s/scans"\n' "$T" > "$T/control"
chmod +x "$T/control"
for root in "$T/dev" "$T/system"; do
    mkdir -p "$root/manifests.d" "$root/api-permissions.d" "$root/roles.d" "$root/services.d"
    printf '%s\n' '{"exeName":"/homechooser-service","allowedNames":["org.webosbrew.homechooser.service","com.webos.homechooser.alert"],"permissions":[{"service":"org.webosbrew.homechooser.service","outbound":["*"]},{"service":"com.webos.homechooser.alert","inbound":["com.webos.surfacemanager"],"outbound":[]}]}' > "$root/roles.d/org.webosbrew.homechooser.service.service.json"
    printf '[D-BUS Service]\nName=org.webosbrew.homechooser.service\nExec=/homechooser-service\nType=dynamic\n' > "$root/services.d/org.webosbrew.homechooser.service.service"
    printf '# Home Chooser alert callback\n[D-BUS Service]\nName=com.webos.homechooser.alert\n' > "$root/services.d/com.webos.homechooser.alert.service"
    printf '%s\n' '{"all":["com.webos.homechooser.alert/uninstallConfirmed","com.webos.homechooser.alert/rebootConfirmed"]}' > "$root/api-permissions.d/org.webosbrew.homechooser.alert.api.json"
done
# Pretty JSON, a space in a string, absent arrays and existing entries must all survive.
printf '{ "id": "keep spaces", "rolesFiles": ["/other/role"], "apiPermissionFiles": ["%s"], "serviceFiles": ["%s"] }\n' "$T/dev/api-permissions.d/org.webosbrew.homechooser.alert.api.json" "$T/dev/services.d/com.webos.homechooser.alert.service" > "$T/dev/manifests.d/org.webosbrew.homechooser.json"
printf '{"groupsFiles":["/other/group"],"apiPermissionFiles":["/other/api","%s"],"serviceFiles":["%s"]}\n' "$T/system/api-permissions.d/org.webosbrew.homechooser.alert.api.json" "$T/system/services.d/com.webos.homechooser.alert.service" > "$T/system/manifests.d/org.webosbrew.homechooser.json"
# The installer's own api file for the service: never written, moved or removed by us.
INSTALLER_API='{"org.webosbrew.homechooser.service.group":["org.webosbrew.homechooser.service/*"]}'
for root in "$T/dev" "$T/system"; do
    printf '%s\n' "$INSTALLER_API" > "$root/api-permissions.d/org.webosbrew.homechooser.service.api.json"
done
run() { $SH "$HERE/homechooser-access" "$@"; }
verify() {
    python3 - "$T" "$1" "$INSTALLER_API" <<'PY2'
import json, pathlib, sys
root=pathlib.Path(sys.argv[1]); mode=sys.argv[2]; installer=sys.argv[3]
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
    alert_api=r/'api-permissions.d/org.webosbrew.homechooser.alert.api.json'
    alert_service=r/'services.d/com.webos.homechooser.alert.service'
    role=json.loads((r/'roles.d/org.webosbrew.homechooser.service.service.json').read_text())
    assert m.get('apiPermissionFiles',[]).count(str(path))==(0 if mode=='removed' else 1)
    assert m.get('apiPermissionFiles',[]).count(str(alert_api))==0
    assert m.get('serviceFiles',[]).count(str(alert_service))==0
    assert path.exists()==(mode!='removed')
    assert not alert_api.exists()
    assert not alert_service.exists()
    assert role['allowedNames'].count('com.webos.homechooser.alert')==0
    assert role['allowedNames'][0]=='org.webosbrew.homechooser.service'
    assert role['permissions'][0]=={'service':'org.webosbrew.homechooser.service','outbound':['*']}
    alias=[entry for entry in role['permissions'] if entry['service']=='com.webos.homechooser.alert']
    assert not alias
    if mode=='removed': continue
    api=json.loads(path.read_text())
    expected=['org.webosbrew.homechooser.service/'+s for s in ['listApps','listRunning','closeApp','uninstallApp','getInfo','getForegroundApp','getAppInfo','getAppIcon']]
    assert api['org.webosbrew.homechooser.applications']==expected
    assert ('ares.webos.cli' in api)==(mode=='11')
    if mode=='11': assert api['ares.webos.cli']==expected
assert not (root/'absent').exists()
PY2
}
echo 'VERSION_ID="10.0.0"' > "$T/os-release"
run apply
verify 10
[ "$(wc -l < "$T/scans")" -eq 1 ]
run apply
[ "$(wc -l < "$T/scans")" -eq 1 ]
run status > "$T/status"
grep -q 'manifest=yes api=yes' "$T/status"
[ "$(wc -l < "$T/scans")" -eq 1 ]
echo 'PRETTY_NAME="webOS 11.2.0"' > "$T/os-release"
run apply
verify 11
[ "$(wc -l < "$T/scans")" -eq 2 ]
run apply
[ "$(wc -l < "$T/scans")" -eq 2 ]
# Repair drift, without duplicating the entry in the manifest.
echo '{}' > "$T/dev/api-permissions.d/org.webosbrew.homechooser.launcher.api.json"
run apply
verify 11
[ "$(wc -l < "$T/scans")" -eq 3 ]
run remove
verify removed
[ "$(wc -l < "$T/scans")" -eq 4 ]
run remove
[ "$(wc -l < "$T/scans")" -eq 4 ]
# Removal still deletes our files if the package manifest was removed by the installer.
run apply
mv "$T/dev/manifests.d/org.webosbrew.homechooser.json" "$T/dev-manifest"
run remove
[ ! -f "$T/dev/api-permissions.d/org.webosbrew.homechooser.launcher.api.json" ]
[ -f "$T/dev/api-permissions.d/org.webosbrew.homechooser.service.api.json" ]
[ ! -f "$T/dev/services.d/com.webos.homechooser.alert.service" ]
mv "$T/dev-manifest" "$T/dev/manifests.d/org.webosbrew.homechooser.json"
run remove
verify removed
if run bogus > /dev/null 2>&1; then exit 1; fi
echo "access: all checks passed ($SH)"
