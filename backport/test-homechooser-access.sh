#!/bin/sh
# Host-only manifest/permission tests. Optional shell argument, e.g. "busybox sh".
set -eu
SH=${1:-sh}
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT HUP INT TERM
export HOMECHOOSER_LUNA_ROOTS="$T/dev $T/system $T/absent"
export HOMECHOOSER_OSRELEASE="$T/os-release" HOMECHOOSER_LS_CONTROL="$T/control"
printf '#!/bin/sh\necho "$*" >> "%s/scans"\n' "$T" > "$T/control"
chmod +x "$T/control"
for root in "$T/dev" "$T/system"; do mkdir -p "$root/manifests.d"; done
# Pretty JSON, a space in a string, absent arrays and existing entries must all survive.
printf '%s\n' '{ "id": "keep spaces", "rolesFiles": ["/other/role"] }' > "$T/dev/manifests.d/org.webosbrew.homechooser.json"
printf '%s\n' '{"groupsFiles":["/other/group"],"apiPermissionFiles":["/other/api"]}' > "$T/system/manifests.d/org.webosbrew.homechooser.json"
run() { $SH "$HERE/homechooser-access" "$@"; }
verify() {
    python3 - "$T" "$1" <<'PY'
import json, pathlib, sys
root=pathlib.Path(sys.argv[1]); mode=sys.argv[2]
for name in ['dev','system']:
    r=root/name
    m=json.loads((r/'manifests.d/org.webosbrew.homechooser.json').read_text())
    if name=='dev': assert m['id']=='keep spaces' and m['rolesFiles']==['/other/role']
    else:
        assert '/other/group' in m['groupsFiles'] and '/other/api' in m['apiPermissionFiles']
    for key, file in [('groupsFiles','groups.d/homechooser.groups.json'),('apiPermissionFiles','api-permissions.d/org.webosbrew.homechooser.service.api.json')]:
        path=r/file
        assert m.get(key,[]).count(str(path))==(0 if mode=='removed' else 1)
        assert path.exists()==(mode!='removed')
    if mode=='removed': continue
    group=json.loads((r/'groups.d/homechooser.groups.json').read_text())
    assert group=={'homechooser.applications':{'description':'Home Chooser launcher applications','public':False}}
    api=json.loads((r/'api-permissions.d/org.webosbrew.homechooser.service.api.json').read_text())
    expected=['org.webosbrew.homechooser.service/'+s for s in ['listApps','running','closeById','uninstall','getInfo']]
    assert api['homechooser.applications']==expected
    assert ('ares.webos.cli' in api)==(mode=='11')
    if mode=='11': assert api['ares.webos.cli']==expected
assert not (root/'absent').exists()
PY
}
echo 'VERSION_ID="10.0.0"' > "$T/os-release"
run apply
verify 10
[ "$(wc -l < "$T/scans")" -eq 1 ]
run apply
[ "$(wc -l < "$T/scans")" -eq 1 ]
run status > "$T/status"
grep -q 'manifest=yes group=yes api=yes' "$T/status"
[ "$(wc -l < "$T/scans")" -eq 1 ]
echo 'PRETTY_NAME="webOS 11.2.0"' > "$T/os-release"
run apply
verify 11
[ "$(wc -l < "$T/scans")" -eq 2 ]
run apply
[ "$(wc -l < "$T/scans")" -eq 2 ]
# Repair drift, without duplicating the entry in the manifest.
echo '{}' > "$T/dev/groups.d/homechooser.groups.json"
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
rm "$T/dev/manifests.d/org.webosbrew.homechooser.json"
run remove
[ ! -f "$T/dev/groups.d/homechooser.groups.json" ]
[ ! -f "$T/dev/api-permissions.d/org.webosbrew.homechooser.service.api.json" ]
if run bogus > /dev/null 2>&1; then exit 1; fi
echo "access: all checks passed ($SH)"
