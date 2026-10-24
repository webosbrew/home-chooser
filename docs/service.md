# The root service

`org.webosbrew.homechooser.service` is a small native (C) luna service in the Home Chooser package
(`service/`). Homebrew Channel elevates it once, so it runs as root without the developer-mode
jail, and the app does every privileged thing through it. It replaces the earlier design in which
the app ran `luna-send` and shell commands through Homebrew Channel's `exec`. See the decision in
the [README](../README.md#decisions) and [Root access](settings-app.md#root-access).

## Package

`ares-package dist build/service-pkg` packs the app and the service together
(`npm run package` in `app/` builds the service with `make -C service`, stages
`services.json` and the binary with `app/scripts/stage-service.js`, then packages). The ipk holds
`usr/palm/services/org.webosbrew.homechooser.service/{homechooser-service,services.json}` (binary mode
755) next to the app. `services.json` has `"engine": "native"` and `"executable":
"homechooser-service"`, so `ares-package` marks the ipk `arm`. The SDK and firmware paths of the
build, and the one source of the version, are in
[Build and test](settings-app.md#build-and-test).

The binary is C, linked against `libluna-service2.so.3`, `libpbnjson_c.so.2` (all JSON in the
service is pbnjson), `libPmLogLib.so.3`, `libglib-2.0.so.0` and `libc.so.6`. `make -C service
check-abi` compares its needs with the oldest supported rootfs (webOS 5.6.1): SONAMEs present,
only `GLIBC_2.4` and `GLIBC_2.9` symbol versions, every undefined symbol defined by those
libraries. No libstdc++.

## Elevation

The installer gives a developer-mode native service a launch line of the form
`Exec=/usr/bin/jailer ... <binary>`. Homebrew Channel's `elevateService {"id": <service id>}`
(`services/elevate-service.ts`) rewrites that to `Exec=<binary>`, adds `*` to the role file's
`allowedNames` and outbound permissions, creates the service's `.root.json` client-permission file
and the `.api.public.json` API-permission file (`public: ["<id>/*"]`, which makes its methods
callable by apps), adds both to the manifest, and runs `ls-control scan-services`. Because of the
public permission the service checks its callers itself (below).

The running instance was started from the old launch line and is still jailed and not root. The
app therefore calls `status`; when `root` is false it calls `elevateService`, then `quit`, then
`status` until the new instance (started on demand by the hub from the rewritten line) reports
`root: true`. The sequence follows the AltHome port's auto-elevator.

## Methods

All methods are on category `/`, take and return JSON, and answer `{"returnValue": false,
"errorCode": <string>, "errorText": <string>}` on failure.

| Method | Request | Reply |
| --- | --- | --- |
| `status` | `{}` | `{returnValue, root, uid, version, apiVersion}`; works while not root |
| `quit` | `{}` | `{returnValue}`, then exits after 300 ms; works while not root |
| `call` | `{uri, payload}` | `{returnValue: true, reply: <the called service's reply>}`; the call times out after 20 s (`timeout`) |
| `readFile` | `{path}` | `{returnValue, exists, text}` (at most 64 KiB) |
| `writeFile` | `{path, text}` | `{returnValue}`; creates the directory, writes `<path>.tmp` and renames it, mode 0644; at most 256 KiB |
| `removeFile` | `{path}` | `{returnValue}`; a missing file is fine |
| `readIcon` | `{appId}` | `{returnValue, mime, data}` with `data` base64; at most 256 KiB |
| `link` / `unlink` / `linkStatus` | `{}` | the boot entry, below |
| `run` | `{command}` | `{returnValue, exitCode, stdoutString, stderrString}` |
| `getInfo` | `{}` | `{returnValue, apiVersion, version, root, group, permitted, isHome}` |
| `listApps` | `{}` | `{returnValue, apiVersion, apps:[…]}`; selected app fields only |
| `running` | `{}` | `{returnValue, apiVersion, running:[…]}`; selected process fields only |
| `closeById` | `{id, force?}` | `{returnValue, id}`; selected Home only |
| `uninstall` | `{id}` | `{returnValue, pending:true, id, confirm}` (`alert` or `screen`); selected Home only |
| `uninstallConfirmed` | `{token}` | `{returnValue, id, route}`; internal confirmation only |
| `rebootPrompt` | `{reason, screen?}` | `{returnValue, pending:true, confirm}` (`alert`, `toast`, or `screen` with `token`); Home Chooser only |
| `rebootConfirmed` | `{token}` | `{returnValue}`; internal confirmation only |

Every method except `status`, `quit` and `getInfo` is privileged: when the service is not root it answers
`errorCode: "notRoot"` and does nothing.

## Who may call

Settings methods (`status`, `quit`, `call`, files/icons, boot links, `run` and `rebootPrompt`) check
`LSMessageGetApplicationID`: it must be `org.webosbrew.homechooser` (an instance suffix after a
space is accepted). Others receive `forbidden`. `make -C service dev` relaxes this settings gate;
it is never packaged.

Launcher methods use a separate gate: a valid hub-reported application id, root and the caller's
installed `requiredPermissions` containing `homechooser.applications`. `getAppInfo` supplies the
permissions, with an installed `appinfo.json` fallback. `closeById` and `uninstall` also require the
caller to equal the stored Home selection. `getInfo` accepts any application caller, without the
root/consent gate, and reports consent and selection. Hub permissions can still deny delivery.

`uninstallConfirmed` accepts Home Chooser or a sender without an application id (the notification
route), always with a live random one-shot token. Four slots expire after 120 seconds; confirmation
consumes the token and rechecks that the requester is still Home. Targets cannot be Home Chooser,
Homebrew Channel, the requester, non-removable apps or apps outside developer/store app directories.
The service raises an `Uninstall <title>?` alert, with Uninstall/Cancel; only Uninstall calls the
token method. If alerts are refused, it launches Home Chooser's own confirmation screen.
Removal first uses `removeLaunchPoint` (`<id>_default`), then the appropriate `appInstallService`
route unless LG explicitly says “is not removable”. Stream status 31 means success and 25 failure
in the implementation. The settings app allows 120 seconds for confirmation.

See [Launcher API version 1](launcher-api.md) for the contract, fields, errors, caller rules,
selection requests and absence detection. Plain shell callers have no application id and are
refused by launcher gates.

## Restarting the TV

No step restarts the compositor while the TV runs. When the patch needs a restart to take effect
(first apply) or to go away (restore), the app asks the user to reboot the TV, through two
methods. `keyfilterApplySam` (`apply samfirst`, which restarted `sam` first) is removed: the app
no longer restarts anything, so there is no such command. `keyfilterApply` and `keyfilterStop`
stay for manual and debugging use only.

`rebootPrompt {reason, screen}` (Home Chooser only, root). `reason` is `apply` or `remove`.

1. The pending check runs the worker's `status` (`keyfilterStatus`, 20 s) and requires a line
   exactly `reboot=needed` (`hc_reboot_needed`: CR and blanks at line ends are ignored, a longer
   line such as `xreboot=needed` does not count). Otherwise the error is `notPending`.
2. An unknown `reason` is `badRequest`. The text comes from `hc_reboot_message`: `apply` is
   `Restart the TV to finish setting up the Home button.`, `remove` is `Restart the TV to finish removing the Home button patch.`
3. A random 128-bit token is minted (32 hex digits from `/dev/urandom`, own table of four slots,
   one shot, 10 minute lifetime).
4. `screen: true` answers `{returnValue: true, pending: true, confirm: "screen", token}` and the
   app shows its own dialog. Otherwise the service raises a `createAlert` (as
   `com.webos.applicationManager`, source Home Chooser) with the message and the buttons
   `Reboot` (onclick `luna://org.webosbrew.homechooser.service/rebootConfirmed`, params `{token}`)
   and `Later` (focused): reply `{returnValue: true, pending: true, confirm: "alert"}`, with no
   token. If the alert is refused the token is killed and a `createToast` (`noaction`) shows the
   message: `confirm: "toast"`. If the toast is refused too: `confirmFailed`.

`rebootConfirmed {token}` accepts Home Chooser or a sender without an application id (the
notification service runs the alert's onclick); any other application id gets `forbidden`, the same
rule as `uninstallConfirmed`. Root is required. The token must be live and unexpired and is
consumed (`badToken` otherwise). The pending check runs again (`notPending` when the state has
changed, for example the user restored in the meantime). Then
`luna-send -a com.webos.applicationManager -n 1 luna://com.webos.service.tvpower/power/reboot
'{"reason":"remoteKey"}'`. A true `returnValue` answers `{returnValue: true}`; anything else is
`rebootFailed` and the reply text goes to the log.

Abuse: only the Home Chooser app can mint a token, and only while the worker reports
`reboot=needed`, so the service cannot be used to reboot a TV in a normal state. A caller without an
application id (the notification service path) needs the 128-bit token, which only appears in the
alert it raised for Home Chooser (or in the reply to the app). The reboot is checked again at
confirm time, so an old alert cannot reboot the TV after the state has settled.

There is no fallback to `systemctl reboot` or `reboot`. That skips tvpowerd's power-off sequence
and its busy check (for example during a software update). If tvpowerd refuses, the user restarts
the TV.

The reboot method, from files only (not run on a device): tvpowerd (`usr/sbin/tvpowerd`) registers
`com.webos.service.tvpower` `/power/reboot` on 5.6.x to 12.0.0 (and `/power2/reboot` from 7.x). The
handler requires `reason` to be in `powerOffReasons` (`etc/tvpowerd/tvpowerd.json` up to 9.x;
`etc/configd/layers/base/com.webos.service.tvpower.json` on 10.x and later). `remoteKey` is listed
on every release and is not a force reason; an invalid reason answers `returnValue: false`. The
handler then calls `com.palm.sleep/shutdown/machineReboot` after tvpowerd's prepare/notify round.
ACG: the method is in group `tv.management` (`tv.internal.api.json`; on 12.0
`com.webos.service.tvpower.api.json`) and private, plus `all` (`*/*`).
`client-permissions.d/com.webos.sam.perm.json` gives `com.webos.applicationManager` both `all` and
`tv.management` on every release checked, which is why the call runs under that identity.

## Privileged luna identity

Every privileged luna call in the native service uses
`/usr/bin/luna-send -a com.webos.applicationManager`, the constant `HC_LUNA_IDENTITY`. There are no
calls under the native service's own luna identity. This includes `listApps`, `running`, every
`getAppInfo` lookup (consent, icons, uninstall, forwarding), `closeByAppId`, settings/default-app
calls, notifications, removal and the fixed profile commands. Arguments go directly to `execve`,
without a shell, using allow-listed URIs and the settings payload restriction below. Watcher
calls and subscriptions carry the same fixed identity.

Native one-shot calls use `luna_run` with a 20 second bound. They run serially, so a slow query or
confirmation can delay other requests. The alert and screen-launch bounds are 8 and 15 seconds;
streamed removal has a 90 second bound. Output is capped at 64 KiB per stream. JSON replies retain
UTF-8. Children are reaped after exit or timeout. Fixed profile commands use the same argv identity.

This identity is device-unverified separately on webOS 5.x, 6.x, 7.x, 8.x, 9.x, 10.x, 11.x and
12.x. Prior service-identity denials do not establish this identity's groups on every release.

## Launcher access files

`backport/homechooser-access apply|remove|status` manages only Home Chooser's API file under
`/var/luna-service2-dev` and `/var/luna-service2`. Apply acts where
`manifests.d/org.webosbrew.homechooser.json` exists. It writes
`api-permissions.d/org.webosbrew.homechooser.launcher.api.json` (`homechooser.applications`: five
launcher methods) and adds its absolute path to `apiPermissionFiles`, creating the array if
missing. On major 11+ the same five methods are also under `ares.webos.cli`. Neither includes
`uninstallConfirmed`, `rebootPrompt` or `rebootConfirmed`. The group is declared only by that API file, the way the installer declares
`<service>.group` for every developer service.

No `groupsFiles` entry and no `groups.d` file. The first version declared the group in
`groups.d/homechooser.groups.json` through `groupsFiles`. On webOS 9.2.1 (w22h-ombre) ls-hubd then
segfaults (`g_hash_table_iter_next`, NULL table) when appinstalld unloads the package manifest at
"removing service files" (`luna://com.webos.service.bus/removeOneManifest`). appinstalld retries
that removal at every boot, so the bus never comes up again. It was reproduced on 2026-10-24 with a
synthetic manifest that had only a `groupsFiles` entry. A manifest that provides a group only
through `apiPermissionFiles` was removed the same way without a crash. `ls-control scan-services`
after dropping the entry did not crash either. The same crash at uninstall was seen on 11.2.0
(w22o-queue-us), which recovered after a reboot. The first version also wrote over the installer's
own `api-permissions.d/org.webosbrew.homechooser.service.api.json`; that file is the installer's
and is not touched now. Apply and remove both strip the old `groupsFiles` entry, delete the old
groups file and rescan while the app is still installed.
The elevated service's broader public registration still requires the service's own caller gates.

Apply is idempotent and repairs drift. Remove drops these manifest entries and files, including
files whose package manifest has disappeared. `ls-control scan-services` runs only after a change;
status prints file presence. The app best-effort applies after elevation, the boot entry applies
without needing a Home choice, and the watcher applies at start. The watcher stages the access
worker into its runtime directory so uninstall cleanup works after the app directory disappears.
Host overrides: `HOMECHOOSER_LUNA_ROOTS`, `HOMECHOOSER_LS_CONTROL`, `HOMECHOOSER_OSRELEASE`.

The group format, manifest keys and app-id naming, non-dev root persistence across reboot, and
effective CLI permissions on 11+ are device-unverified. So are permission metadata in `getAppInfo`,
`running` availability/shape, launch-point ids, alert button payloads and click sender identity,
and the terminal removal status. The [device status](#launcher-api-device-status) below records
this separately for each release.

## Launcher API: confirmation and removal

The launcher-facing contract (parameters, replies, errors) is in [Launcher API](launcher-api.md).
This is what the service does behind `uninstall` and `uninstallConfirmed`.

* **Token.** The service stores at most four random, one-shot tokens, valid for 120 seconds; a newer
  request can replace an older slot. Tokens stay inside Home Chooser's confirmation flow: the alert
  button parameters and the screen launch parameters
  (`{confirmUninstall:"<id>", token:"<token>"}`). The confirmation screen obtains the title from
  `getAppInfo` and asks the same question as the alert.
* **Asking.** The service fetches the target's title, then raises the alert (8 s bound); if
  `createAlert` is refused it launches Home Chooser's settings app with the screen parameters (15 s
  bound). Both failing is `confirmFailed`.
* **Confirming.** `uninstallConfirmed {token}` rechecks that the requester is still the selected
  Home, then calls `applicationManager/removeLaunchPoint` with `<id>_default`. A platform refusal
  saying the app “is not removable” stops the operation (`notRemovable`). Other failures fall back
  to `appInstallService/dev/remove` for a developer app or `appInstallService/remove` for a store
  app, with `{id,subscribe:true}`, reading progress until status 31 (success) or 25 (failure), with
  a 90 second bound. The launch-point route reports acceptance, not an independently observed end
  of uninstall.
* **Reply and errors.** `{returnValue:true,id,route:"launchPoint"}` or
  `route:"appInstallService"`. Errors: `forbidden`, `notRoot`, `badToken`, `notHome`,
  `notRemovable`, `removeFailed`. Any root caller without an application id that holds a live token
  can complete the removal; the token is the capability.
* **Caller identity and launcher gates.** The caller is `LSMessageGetApplicationID` (dropping an
  instance suffix after a space). The service independently reads the caller's installed
  `requiredPermissions`; `closeById` and `uninstall` also compare the id with the stored choice in
  `/var/lib/webosbrew/homechooser/home`. These are service checks even where the hub allows the call
  through a broader group.

## Launcher API device status

The launcher API has host policy, shell and UI tests; none of the following is claimed as verified
on a device. This applies separately to each supported release:

| webOS major | API/group files | privileged `-a` identity | developer CLI group |
| --- | --- | --- | --- |
| 5.x | device-unverified | device-unverified | not added |
| 6.x | device-unverified | device-unverified | not added |
| 7.x | device-unverified | device-unverified | not added |
| 8.x | device-unverified | device-unverified | not added |
| 9.x | device-unverified | device-unverified | not added |
| 10.x | device-unverified | device-unverified | not added |
| 11.x | device-unverified | device-unverified | device-unverified |
| 12.x | device-unverified | device-unverified | device-unverified |

Specifically device-unverified: `getAppInfo` returning `requiredPermissions` (installed appinfo
fallback exists); a group declared only by an API file being grantable to launchers, the manifest
key `apiPermissionFiles`, and the manifest being named after the app id; `/var/luna-service2`
surviving reboot; `<id>_default` being the correct launch-point id; alert button `onclick`/`params`
and the click sender's application identity; `running` being callable and returning a `running`
array; status 31 reliably being the final successful removal status; and `ares.webos.cli` alone
allowing delivery on 11+.

For a device validation session, the order is: install/elevate Home Chooser and inspect the access
files; install a consenting Home fixture and inspect its effective client permissions; call
`getInfo`, `listApps` and `running`; test the Home request and close checks before/after selection;
exercise cancel and confirm for removal of a disposable user app, including the screen fallback;
then check access restoration after reboot and cleanup after Home Chooser removal. As root,
simulate an installed caller with its real id (see
[Testing your launcher](launcher-api.md#testing-your-launcher) for the fixture build). Destructive
removal checks require the owner's device permission.

## Allow-lists

The policy is in `service/src/policy.c` and tested on the host (`make -C service test`).

* **`call` URIs**: `applicationManager` `listApps`, `getAppInfo`, `setDefaultApp`,
  `launchDefaultApp`, `closeByAppId`; `settingsservice` `getSystemSettings` and
  `setSystemSettings`, only for category `general` and the single key `defaultApps` (the `keys`
  array of a get; the keys of `settings` of a set). Anything else is `notAllowed`.
  System profile calls are fixed commands (`profileGet`, `profileRescan`), not generic forwarding.
* **Files** (`readFile`, `writeFile`, `removeFile`, exact paths):
  `/var/lib/webosbrew/homechooser/home`, `/var/lib/webosbrew/homechooser/keyfilter`,
  `/var/lib/webosbrew/homechooser/keyfilter-generated`,
  `/var/webos-profile/override/org.webosbrew.homechooser.json`. `readFile` also reads
  `/etc/os-release`, because a web app cannot read it and the app needs the release. The
  writable files are opened with `O_NOFOLLOW`. `/etc/os-release` is a symlink to
  `../usr/lib/os-release` (webOS 5.6 to 26 roots checked: W20L, W20H, W21P, W22L, W22P, W22H 43.x), so
  it is resolved with `realpath()` and read only when the result is `/etc/os-release` or
  `/usr/lib/os-release`; any other target is `notAllowed`.
* **Icons**: `readIcon` asks `getAppInfo` for the app's `folderPath` and `icon`, joins them (no
  `..`), requires an image extension (png, jpg, jpeg, gif, svg, webp), resolves symlinks and
  requires the result to lie inside the real `folderPath`, then returns the file if it is a regular
  file of at most 256 KiB.
* **Boot entry**: `link` creates the directory `/var/lib/webosbrew/init.d`, removes the earlier
  `homechooser-keyfilter` entry, sets mode 755 on the four scripts in the app's `backport/`
  directory, and makes `/var/lib/webosbrew/init.d/homechooser` a symlink to the app's
  `backport/homechooser` (replacing a link or a regular file). `unlink` removes both names.
  `linkStatus` returns `entry` (`none`, `file`, `link`, `dangling`) and `target`. The app directory
  is the fixed `/media/developer/apps/usr/palm/applications/org.webosbrew.homechooser`.
* **Commands** (`run`, by name, fixed argv, no shell, minimal environment, stdin from `/dev/null`,
  output capped at 64 KiB per stream, a timeout each):

| Name | argv | Timeout |
| --- | --- | --- |
| `restartSam` | `/bin/systemctl restart sam` | 60 s |
| `watch` | `/bin/sh <app>/backport/homechooser watch` | 20 s |
| `keyfilterStatus` | `/bin/sh <app>/backport/homechooser-keyfilter status` | 20 s |
| `keyfilterApply` | same with `apply` (restarts the compositor; debug only, the app does not call it) | 90 s |
| `keyfilterStop` | same with `stop` (unmounts and restarts the compositor; debug only) | 90 s |
| `keyfilterRefresh` | same with `refresh` (never restarts the compositor unless given `restart`) | 90 s |
| `stopWatcher` | `/bin/systemctl stop homechooser-home` (a non-zero exit is not an error) | 20 s |
| `watcherActive` | `/bin/systemctl is-active homechooser-home` (a non-zero exit is not an error) | 10 s |
| `profileGet` | `/usr/bin/luna-send -a com.webos.applicationManager -n 1 luna://com.webos.service.systemprofile/getProfiles {"names":["defaultApps"]}` (stdout is luna-send's reply; the exit status is 0 also for a false `returnValue`, so the caller reads it) | 20 s |
| `profileRescan` | `/usr/bin/luna-send -a com.webos.applicationManager -n 1 luna://com.webos.service.systemprofile/debug/rescan {}` (same) | 30 s |
| `accessApply` | `/bin/sh <app>/backport/homechooser-access apply` | 30 s |
| `accessRemove` | same with `remove` | 30 s |
| `accessStatus` | same with `status` (non-zero exit is not an error) | 10 s |
| `watchLog` | not run: the last 5 lines of `/tmp/homechooser-home.log` | - |

A non-zero exit of any other command is `errorCode: "exitStatus"` with the output attached; a
command that does not finish is killed (`timeout`). The service does not wait for a background
process that outlives the command (the `watch` script starts a transient unit).

## Logs

Every operation, and every refusal, goes to PmLog with the context `HomeChooser`
(read it from the journal or with `PmLogCtl`; not verified on a TV): message ids `START`, `CALL`, `CALL_FORWARD`,
`CALL_DENIED`, `CALL_TIMEOUT`, `RUN`, `RUN_DENIED`, `WROTE`, `REMOVED`, `LINKED`, `UNLINKED`,
`PATH_DENIED`, `REFUSED`, `NOT_ROOT`, `ICON_NONE`, `EXIT`. The app logs its side with `[HomeChooser]`
in the web console.

## Tests

* `make -C service test`: the pure policy (sender, file and call allow-lists, argv table, icon
  path rules, base64, log tail) compiled natively with address and undefined-behaviour sanitizers.
* `app/src/platform/tests`: the service client and the elevation flow against a mocked bridge.
* `backport/test-homechooser-access.sh`: manifest edits, per-release permissions, repair and cleanup
  against temporary roots and stubbed `ls-control`; watcher/boot tests cover the integration.
* Device-unverified: hub sender ids, elevation of this package, effective groups for the fixed
  identity, and each launcher API behavior listed in the validation table.
