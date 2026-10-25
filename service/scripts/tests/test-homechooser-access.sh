#!/bin/sh
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
for root in "$T/dev" "$T/system"; do mkdir -p "$root/manifests.d" "$root/api-permissions.d"; done
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
    assert m.get('apiPermissionFiles',[]).count(str(path))==(0 if mode=='removed' else 1)
    assert path.exists()==(mode!='removed')
    if mode=='removed': continue
    api=json.loads(path.read_text())
    expected=['org.webosbrew.homechooser.service/'+s for s in ['listApps','listRunning','closeApp','uninstallApp','getInfo']]
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
grep -q 'manifest=yes api=yes legacy-group=no' "$T/status"
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
mv "$T/dev-manifest" "$T/dev/manifests.d/org.webosbrew.homechooser.json"
# State left by the first version: a groupsFiles entry and groups.d file, which crash ls-hubd at
# uninstall. Both apply and remove strip them (and rescan) while the app is still installed.
for action in apply remove; do
    mkdir -p "$T/dev/groups.d"
    echo '{"homechooser.applications":{"description":"x","public":false}}' > "$T/dev/groups.d/homechooser.groups.json"
    printf '{"id":"keep spaces","rolesFiles":["/other/role"],"groupsFiles":["%s"]}\n' \
        "$T/dev/groups.d/homechooser.groups.json" > "$T/dev/manifests.d/org.webosbrew.homechooser.json"
    before=$(wc -l < "$T/scans")
    run $action
    [ "$(wc -l < "$T/scans")" -eq $((before + 1)) ]
    [ ! -f "$T/dev/groups.d/homechooser.groups.json" ]
    if grep -q groupsFiles "$T/dev/manifests.d/org.webosbrew.homechooser.json"; then exit 1; fi
    grep -q '"rolesFiles":\["/other/role"\]' "$T/dev/manifests.d/org.webosbrew.homechooser.json"
done
verify removed
if run bogus > /dev/null 2>&1; then exit 1; fi
echo "access: all checks passed ($SH)"
