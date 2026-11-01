# The root service

`org.webosbrew.homechooser.service` is a small native (C) luna service in the Home Chooser package
(`service/`). Homebrew Channel elevates it once, so it runs as root without the developer-mode
jail, and the app does every privileged thing through it. It replaces the earlier design in which
the app ran `luna-send` and shell commands through Homebrew Channel's `exec`. See the decision in
the [README](../README.md#decisions) and [Root access](settings-app.md#root-access).

## Package

`ares-package dist build/service-pkg` packs the app and the service together
(`npm run package` in `app/` builds the service with CMake into `build/service`, stages
`services.json`, the three binaries, three entry scripts and their `scripts/lib/` helpers with
`app/scripts/stage-service.js`, then packages). The ipk holds
`usr/palm/services/org.webosbrew.homechooser.service/{homechooser-service,homechooser-daemon,homechooser-home-gate,services.json,scripts/}` (binary mode
755) next to the app. `services.json` has `"engine": "native"` and `"executable":
"homechooser-service"`, so `ares-package` marks the ipk `arm`. The SDK and firmware paths of the
build, and the one source of the version, are in
[Build and test](settings-app.md#build-and-test).

Both binaries are C. The service links `libluna-service2.so.3`, `libpbnjson_c.so.2`,
`libPmLogLib.so.3`, `libglib-2.0.so.0` and `libc.so.6`. The daemon links the same
libraries **except libluna-service2**. `cmake --build build/service --target check-abi` validates both
against the oldest supported rootfs (5.6.1) and rejects any daemon LS2 dependency
or LS registration/call symbols. `homechooser-home-gate` ([home-gate.md](home-gate.md)) links the
daemon's libraries, also without libluna-service2, and is checked the same way. No shared library is installed.

`services.json` declares only `homechooser-service`: only that executable receives
an LS2 service role. `homechooser-daemon` needs root for boot/state operations and
uses the existing privileged `/usr/bin/luna-send` role for every LS2 request.

## Native source layout

`service/src/main.c` starts only the on-demand LS2 service, with the unchanged
21-method table. `daemon-main.c` owns `--boot` and `--homekey <action>`.
The daemon applies the Home redirect and runs the C watcher on a single GLib
thread. All daemon LS2 traffic runs as `luna-send -a com.webos.applicationManager`
children. It never registers an LS2 client.

`json.c`, `luna.c`, `proc.c`, `fs.c`, `policy.c` and the Home-key transaction/generator
are compiled once into `libhomechooser.a` and linked into both programs as needed.
This archive is a build artifact, not part of the package.

| Source under `service/src/` | Responsibility |
| --- | --- |
| `log.h`, `version.h`, `roles.h` | Shared log context, build version and role entry points |
| `json.c` | pbnjson parsing, members and reply validation |
| `luna.c` | Bounded privileged one-shot calls through luna-send |
| `luna-child.c` | Daemon child streams, GIOChannel JSON framing, deadlines, cancellation and reaping |
| `watcher/{main,core,record,restore,preload,notify,gate}.c` | Boot lifecycle, serial event dispatch, reconcile, stock recovery, preload/close guards, notices and the 6–9 home gate mount |
| `gate-mount.c` | Home gate bind mount: idempotent mount/unmount, propagated-copy cleanup, release marker, closing a running stock Home |
| `gate/{main,decide,run}.c` | `homechooser-home-gate`, run by SAM as `com.webos.app.home` on 6–9: decision table and self-release to stock Home |
| `proc.c` | Process spawn, output limits, timeout and child reaping |
| `fs.c` | Shared bounded reads, directory creation and atomic writes |
| `policy.c` | Pure allow-lists and decision helpers |
| `homekey-main.c`, `homekey.c`, `homekey-generator.c`, `homekey-shapes.inc` | Worker role, live key-filter transaction and guarded JS generation |
| `apps.c` | Stored Home choice, icon loading and app/process field projection |
| `bootlink.c` | Boot link methods and legacy restore staging |
| `notify.c` | Bounded notification acceptance check and logging |
| `service/main.c`, `service/methods.h` | LS2 registration, method table, main loop, status and quit |
| `service/reply.c` | Settings sender/root gate and LS2 replies/errors |
| `service/files.c`, `service/files.h` | File RPCs and shared file-size limit |
| `service/call.c` | Allow-listed forwarding and settings icon requests |
| `service/run.c` | Named command RPC and log tail |
| `service/launcher.c` | Launcher gates, consent, requests and private uninstall tokens |
| `service/system.c` | Retained reboot prompt method |

The extra shared `fs.c` keeps file access available to app metadata, boot links and service methods
without depending on the service role. Named commands have their own service module rather than
living in the generic process runner. Boot link RPCs retain their gates and replies in `bootlink.c`.
Service calls remain synchronous and bounded. The daemon serializes watcher policy events and
uses GLib sources for confirmation/backoff, preload deadlines and child I/O. Subscription children
use `-i`; one-shot children use `-n 1` with a deadline while subscription I/O keeps running.
There is no shell or awk between the daemon and luna-send. Child EOF/exit ends a stream:
settings loss exits 1 for unit restart, apps loss keeps the 300 s ticker, foreground/life loss logs
and continues. JSON false replies alone are not EOF. Children inherit the daemon process group,
are killed/reaped on cancellation and clean shutdown, and receive SIGKILL through
`PR_SET_PDEATHSIG` if their parent is killed. The systemd unit cgroup remains the outer cleanup
boundary. SIGCHLD uses a self-pipe, avoiding GLib child-watch worker threads.
See the [port inventory and offline validation](research/native-watcher-port.md).

The internal Home-key worker status uses `active=yes/no` for whether configd selects a generated
Home Chooser filter. The app and E2E parser use that field directly, without a legacy field alias.
The [module refactor report](research/service-module-refactor.md) records the old section map and
offline verification.

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
| `getInfo` | `{}` | `{returnValue, apiVersion, version, root, permission, permitted, isSelectedHome}` |
| `listApps` | `{}` | `{returnValue, apiVersion, apps:[…]}`; selected app fields only |
| `listRunning` | `{}` | `{returnValue, apiVersion, running:[…]}`; selected process fields only |
| `getForegroundApp` | `{}` | `{returnValue, apiVersion, appId, windowId?, processId?}`; one SAM lookup |
| `getAppInfo` | `{id}` | `{returnValue, apiVersion, app:{…}}`; one `listApps` item |
| `getAppIcon` | `{id, size?}` | `{returnValue, apiVersion, mimeType, data, byteLength}`; base64, at most 256 KiB decoded |
| `closeApp` | `{id, force?}` | `{returnValue, apiVersion, id}`; selected Home only |
| `uninstallApp` | `{id}` | `{returnValue, apiVersion, pending:true, id, confirmation}` (`alert` or `screen`); selected Home only |
| `uninstallConfirmed` | `{token}` | `{returnValue, id, route}`; screen fallback only |
| `rebootPrompt` | `{reason}` | `{returnValue, pending:true, confirm}` (`alert`, or `toast` when the alert is refused); Home Chooser only |

Every method except `status`, `quit` and `getInfo` is privileged: when the service is not root it answers
`errorCode: "notRoot"` and does nothing.

The [launcher guide](launcher-api.md#error-handling) defines every launcher error code. The
settings and confirmation methods are internal; their additional codes have these conditions:

| Code | Condition |
| --- | --- |
| `forbidden` | A settings caller is not Home Chooser, or a confirmation caller is another app |
| `notAllowed` | A forwarded URI, settings payload, file path or named command is outside the allow-list |
| `badRequest` | A required JSON object, field or reboot reason is invalid |
| `timeout` | A bounded settings call or named command reaches its time limit |
| `badReply` | A settings call returns no usable JSON, including a spawn failure |
| `readFailed`, `writeFailed` | An allowed file cannot be read or written |
| `fileRemoveFailed` | `removeFile` cannot unlink an allowed file |
| `tooBig` | File text or an icon exceeds its method's size limit |
| `noIcon` | The requested app has no usable icon |
| `linkFailed`, `unlinkFailed` | The boot entry cannot be linked or unlinked |
| `spawnFailed`, `exitStatus` | A named command cannot start, or exits unsuccessfully |
| `badToken` | A confirmation token is unknown, expired or already used |
| `notHome` | The uninstall requester is no longer the selected Home at confirmation time |
| `notRemovable` | `removeLaunchPoint` explicitly says the target is not removable |
| `removeFailed` | Both uninstall removal routes fail or time out |
| `notPending` | A reboot prompt has no pending restart |
| `confirmFailed` | Neither confirmation route can ask the owner |
| `internal` | A confirmation token cannot be generated |

## Who may call

Settings methods (`status`, `quit`, `call`, files/icons, boot links, `run` and `rebootPrompt`) check
`LSMessageGetApplicationID`: it must be `org.webosbrew.homechooser` (an instance suffix after a
space is accepted). Others receive `forbidden`. A dev build relaxes this settings gate
(`cmake -S service -B build/service-dev -DEXTRA_CFLAGS=-DHC_ALLOW_ANY_SENDER`); it is never packaged.

Launcher methods use a separate gate: a valid hub-reported application id, root and the caller's
installed `requiredPermissions` containing `org.webosbrew.homechooser.applications`. SAM's `getAppInfo` supplies the
permissions, with an installed `appinfo.json` fallback. `closeApp` and `uninstallApp` also require the
caller to equal the stored Home selection. `getInfo` accepts any application caller, without the
root/consent gate, and reports consent and selection. Hub permissions can still deny delivery.

`uninstallApp` checks the target and caller before showing an `Uninstall <title>?` alert. The
Uninstall button calls LG's `appInstallService/dev/remove` for a developer install or
`appInstallService/remove` for a store install, using the verified `folderPath`. A stale alert can
still remove its target after the Home selection changes. Targets cannot be Home Chooser,
Homebrew Channel, the requester, non-removable apps or apps outside developer/store app directories.
If alerts are refused, the service launches Home Chooser's confirmation screen. Only that screen
uses `uninstallConfirmed`: it needs a live random one-shot token, rechecks that the requester is
still Home, and first tries `removeLaunchPoint` before the appropriate `appInstallService` route.
The settings app allows 120 seconds for screen confirmation.

See [Launcher API version 1](launcher-api.md) for the contract, fields, errors, caller rules,
selection requests and absence detection. Plain shell callers have no application id and are
refused by launcher gates.

## Restarting the TV

On webOS 6–9, hook installation, runtime changes and disable use a live configd **volatile**
key-filter reload. No GUI process is restarted and no reboot prompt is raised. The retained
`rebootPrompt` RPC is a compatibility endpoint: current status never reports a pending reboot,
so it refuses with `notPending`. See [the live-filter design](home-key-hook.md).

## Privileged luna identity

Privileged RPC workers and every daemon LS2 operation use
`/usr/bin/luna-send -a com.webos.applicationManager`, the constant `HC_LUNA_IDENTITY`.
The daemon never calls LSRegister or impersonates an application through its own connection.
These privileged operations include `listApps`, `listRunning`, every
`getAppInfo` lookup (consent, icons, uninstall, forwarding), `closeByAppId`, settings/default-app
calls, notifications, removal and the fixed profile commands. Arguments go directly to `execve`,
without a shell, using allow-listed URIs and the settings payload restriction below. Watcher
calls and subscriptions carry the same fixed identity.

Native one-shot calls use `luna_run` with a 20 second bound. They run serially, so a slow query or
confirmation can delay other requests. The alert and screen-launch bounds are 8 and 15 seconds;
streamed removal has a 90 second bound. Output is capped at 64 KiB per stream. JSON replies retain
UTF-8. Children are reaped after exit or timeout. Fixed profile commands use the same argv identity.

This identity has been exercised on Beanjar VMs for some releases. Prior service-identity
denials do not establish its groups on every release.

## Luna access files

`service/scripts/homechooser-access apply|remove|status` manages Home Chooser's access files under
`/var/luna-service2-dev` and `/var/luna-service2`. Apply acts where
`manifests.d/org.webosbrew.homechooser.json` exists. It writes
`api-permissions.d/org.webosbrew.homechooser.launcher.api.json` (`org.webosbrew.homechooser.applications`: eight
launcher methods) and adds its absolute path to `apiPermissionFiles`, creating the array if
missing. On major 11+ the same eight methods are also under `ares.webos.cli`. Neither includes
`uninstallConfirmed` or `rebootPrompt`. The group is declared only by that API file, the way the installer declares
`<service>.group` for every developer service.

On webOS 9.2.1, Surface Manager turns `onclick` plus `params` into an alert action. Its role
allows calls to LG's `com.webos.*` services but denies `org.webosbrew.*` before our handler runs.
The buttons therefore call LG's tvpower and appInstallService directly. Neither Home Chooser's
role nor the vendor role grants an extra callback service.

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
and is not touched now. A targeted 2026-10-25 update from development build `9581378` on
w22h-ombre (`e2e/results/w22h-ombre-20261025-214258`) ended with 46 PASS and 18 FAIL:
`ares-install` timed out, the selected Home was lost, and LG processes crashed. That run does
not isolate the ls-hubd failure as the sole cause. The `groupsFiles` layout existed only in
unreleased builds `1871941` through `9581378`; updating from those builds is unsupported.
The access script manages only its current access files; it does not try to
clean up that unsupported development layout.

The elevated service's broader public registration still requires the service's own caller gates.

Apply is idempotent and repairs drift. Remove drops these manifest entries and files, including
files whose package manifest has disappeared. `ls-control scan-services` runs only after a change;
status prints file presence. The app best-effort applies after elevation, the boot entry applies
without needing a Home choice, and the watcher applies at start. The watcher stages the access
worker into its runtime directory so uninstall cleanup works after the app directory disappears.
Host overrides: `HOMECHOOSER_LUNA_ROOTS`, `HOMECHOOSER_LS_CONTROL`, `HOMECHOOSER_OSRELEASE`, `HOMECHOOSER_NODE`.

The group format, manifest keys, app-id naming, permission metadata in `getAppInfo` and
`listRunning` availability have been exercised on Beanjar VMs for some releases. The
[device status](#launcher-api-device-status) below records coverage by release.

The settings-only methods (`status`, `quit`, `call`, file methods, `readIcon`, `link`,
`unlink`, `linkStatus`, `run`), confirmation and reboot methods, files, `status` output and log ids
are internal and are not part of the frozen launcher API.

## Launcher API: confirmation and removal

The launcher-facing contract (parameters, replies, errors) is in [Launcher API](launcher-api.md).
This is what the service does behind `uninstallApp` and its screen fallback.

* **Asking.** The service fetches the target's title, `removable` flag and `folderPath`, then
  applies the protected-app checks. For a developer app the alert button calls
  `appInstallService/dev/remove`; for a store app it calls `appInstallService/remove`. Both use
  `{id,subscribe:true}`. If `createAlert` is refused, the service launches Home Chooser's settings
  app with screen parameters (15 s bound). Both failing is `confirmFailed`.
* **Screen token.** The fallback screen receives `{confirmUninstall:"<id>", token:"<token>"}`.
  The service stores at most four random, one-shot tokens, valid for 120 seconds. The screen
  obtains the title from `getAppInfo` and asks the same question as the alert. Alert buttons carry
  no Home Chooser token.
* **Screen confirming.** `uninstallConfirmed {token}` rechecks that the requester is still the
  selected Home, then calls `applicationManager/removeLaunchPoint` with `<id>_default`. A platform
  refusal saying the app “is not removable” stops the operation (`notRemovable`). Other failures
  fall back to the appropriate `appInstallService` remove route, reading progress until status 31
  (success) or 25 (failure), with a 90 second bound. The launch-point route reports acceptance,
  not an independently observed end of uninstall.
* **Reply and errors.** `{returnValue:true,id,route:"launchPoint"}` or
  `route:"appInstallService"`. Errors: `forbidden`, `notRoot`, `badToken`, `notHome`,
  `notRemovable`, `removeFailed`. Any root caller without an application id that holds a live token
  can complete the screen removal; the token is the capability. A stale system alert can still
  remove the target after the selected Home changes, because LG receives its button action directly.

On webOS 9.2.1, the firmware's `com.webos.appinstalld.service` registers
`com.webos.appInstallService`. Its API permissions list `dev/remove` under `ares.webos.cli` and
`remove` under `appinstalld.internal`; Surface Manager's client permissions grant `all`. Live
alerts reached both methods as `com.webos.applicationManager`: `dev/remove` removed a disposable
developer fixture, while `remove` returned `FAILED_REMOVE` for a nonexistent ID. The fixture's
`getAppInfo.folderPath` was `/media/developer/apps/usr/palm/applications/...`, so the service chose
the developer endpoint. No store app was removed in this probe.
* **Caller identity and launcher gates.** The caller is `LSMessageGetApplicationID` (dropping an
  instance suffix after a space). The service independently reads the caller's installed
  `requiredPermissions`; `closeApp` and `uninstallApp` also compare the id with the stored choice in
  `/var/lib/webosbrew/homechooser/home`. These are service checks even where the hub allows the call
  through a broader group.

## Launcher API device status

The launcher API has host policy, shell and UI tests, plus targeted Beanjar VM verification.
The table tracks release coverage; a VM result is not a physical TV result.

| webOS major | API/group files | privileged `-a` identity | developer CLI group |
| --- | --- | --- | --- |
| 5.x | v1 not yet verified | v1 not yet verified | not added |
| 6.x | v1 not yet verified | v1 not yet verified | not added |
| 7.x | v1 not yet verified | v1 not yet verified | not added |
| 8.x | v1 not yet verified | v1 not yet verified | not added |
| 9.x | verified on Beanjar VM 9.2.4 | verified on Beanjar VM 9.2.4 | not added |
| 10.x | v1 not yet verified | v1 not yet verified | not added |
| 11.x | v1 not yet verified | v1 not yet verified | v1 not yet verified |
| 12.x | v1 not yet verified | v1 not yet verified | v1 not yet verified |

Physical TV behaviour is still unverified for the permission grant and the confirmation
routes. A VM test of the alert route does not press its Uninstall button.

For a device validation session, the order is: install/elevate Home Chooser and inspect the access
files; install a consenting Home fixture and inspect its effective client permissions; call
`getInfo`, `listApps` and `listRunning`; test the Home request and close checks before/after selection;
exercise cancel and confirm for removal of a disposable user app, including the screen fallback;
then check access restoration after reboot and cleanup after Home Chooser removal. As root,
simulate an installed caller with its real id (see
[Testing your launcher](launcher-api.md#testing-your-launcher) for the fixture build). Destructive
removal checks require the owner's device permission.

## Allow-lists

The policy is in `service/src/policy.c` and tested on the host (`test-policy`, below).

* **`call` URIs**: `applicationManager` `listApps`, `getAppInfo`, `setDefaultApp`,
  `launchDefaultApp`, `closeByAppId`; `settingsservice` `getSystemSettings` and
  `setSystemSettings`, only for category `general` and the single key `defaultApps` (the `keys`
  array of a get; the keys of `settings` of a set). Anything else is `notAllowed`.
  System profile calls are fixed commands (`profileGet`, `profileRescan`), not generic forwarding.
* **Files** (`readFile`, `writeFile`, `removeFile`, exact paths):
  `/var/lib/webosbrew/homechooser/home`, `/var/lib/webosbrew/homechooser/homekey`,
  `/var/webos-profile/override/org.webosbrew.homechooser.json`. `readFile` also reads
  `/etc/os-release`, because a web app cannot read it and the app needs the release. The
  writable files are opened with `O_NOFOLLOW`. `/etc/os-release` is a symlink to
  `../usr/lib/os-release` (webOS 5.6 to 26 roots checked: W20L, W20H, W21P, W22L, W22P, W22H 43.x), so
  it is resolved with `realpath()` and read only when the result is `/etc/os-release` or
  `/usr/lib/os-release`; any other target is `notAllowed`.
* **Icons**: `readIcon` and `getAppIcon` ask SAM for the app's `folderPath` and chosen icon field.
  They join the path (no `..`), require an image extension (png, jpg, jpeg, gif, svg, webp), resolve
  symlinks and require the result to lie inside the real `folderPath`, then return a regular
  file of at most 256 KiB.
* **Boot entry**: `link` creates `/var/lib/webosbrew/init.d`, sets mode 755 on the installed scripts,
  on 5/10–12 stages `homechooser-daemon` and boot/access helpers in `/var/lib/webosbrew/homechooser/boot`, then links
  `/var/lib/webosbrew/init.d/homechooser` to that boot copy on 5/10–12, or to the installed entry on 6–9 (replacing an earlier entry). The entry queues `homechooser-daemon --boot` in a nonblocking transient unit, before RPC name registration; it becomes the watcher after apply. `unlink`
  removes the link and copy.
  `linkStatus` returns `entry` (`none`, `file`, `link`, `dangling`) and `target`. The service directory
  is the fixed `/media/developer/apps/usr/palm/services/org.webosbrew.homechooser.service`.
* **Commands** (`run`, by name, fixed argv, no shell, minimal environment, stdin from `/dev/null`,
  output capped at 64 KiB per stream, a timeout each):

| Name | argv | Timeout |
| --- | --- | --- |
| `watch` | `/bin/sh <service>/scripts/homechooser watch` | 20 s |
| `homekeyStatus` | `/bin/sh <service>/scripts/homechooser-homekey status` | 20 s |
| `homekeyPrepare` | same with `prepare` (validate the native generator against this device’s stock JS) | 120 s |
| `homekeyClear` | same with `clear` (restore stock and delete unused JS versions) | 20 s |
| `homekeyApply` | same with `apply` (native, checksum-verified volatile reload) | 90 s |
| `homekeyStop` | same with `stop` (restore exact stock list volatile before deleting unused copies) | 90 s |
| `homekeyRefresh` | same with `refresh` (live launcher change, no process restart) | 90 s |
| `stopWatcher` | `/bin/systemctl stop homechooser-home` (a non-zero exit is not an error) | 20 s |
| `watcherActive` | `/bin/systemctl is-active homechooser-home` (a non-zero exit is not an error) | 10 s |
| `profileGet` | `/usr/bin/luna-send -a com.webos.applicationManager -n 1 luna://com.webos.service.systemprofile/getProfiles {"names":["defaultApps"]}` (stdout is luna-send's reply; the exit status is 0 also for a false `returnValue`, so the caller reads it) | 20 s |
| `profileRescan` | `/usr/bin/luna-send -a com.webos.applicationManager -n 1 luna://com.webos.service.systemprofile/debug/rescan {}` (same) | 30 s |
| `accessApply` | `/bin/sh <service>/scripts/homechooser-access apply` | 30 s |
| `accessRemove` | same with `remove` | 30 s |
| `accessStatus` | same with `status` (non-zero exit is not an error) | 10 s |
| `watchLog` | not run: the last 5 lines of `/tmp/homechooser-home.log` | - |

A non-zero exit of any other command is `errorCode: "exitStatus"` with the output attached; a
command that does not finish is killed (`timeout`). The service does not wait for a background
process that outlives the command (the `watch` script starts a transient unit).

## Logs

Every operation, and every refusal, goes to PmLog with the context `HomeChooser`
(read it from the journal or with `PmLogCtl`). Current message ids: `START`, `CALL`,
`CALL_FORWARD`, `CALL_DENIED`, `CALL_FAILED`, `REPLY_FAILED`, `RUN`, `RUN_DENIED`,
`RUN_STDERR`, `WROTE`, `REMOVED`, `LINKED`, `UNLINKED`, `PATH_DENIED`, `REFUSED`,
`NOT_ROOT`, `ICON_NONE`, `LAUNCHER_ERROR`, `UNINSTALL_ASK`, `UNINSTALL`, `REBOOT_ASK`,
`REBOOT`, `HOMEKEY`, `REGISTER_FAILED`, `EXIT`. These ids are internal. The app logs its side with
`[HomeChooser]` in the web console.

## Tests

The native tests run with CTest: `cmake -S service/test -B build/service-host`,
`cmake --build build/service-host`, then `ctest --test-dir build/service-host`.

* `test-policy`: the pure policy (sender, file and call allow-lists, argv table, icon
  path rules, base64, log tail) compiled natively with address and undefined-behaviour sanitizers.
* `test-watcher` and `test-watcher-luna`: native watcher policy and child-stream runtime with an executable
  Luna double under qemu-arm and firmware libraries; includes runtime staging, removed-package restore,
  subscription EOF, garbage/pretty JSON, bounded hangs, parent death, cancellation, signal exit, preload retry/settle and close guards.
* `test-homekey`: native live-filter transactions with fake Luna.
* `app/src/platform/tests`: the service client and the elevation flow against a mocked bridge.
* `service/scripts/tests/test-homechooser-access.sh`: manifest edits, per-release permissions, repair and cleanup
  against temporary roots and stubbed `ls-control`; watcher/boot tests cover the integration.
* Beanjar VM runs verify the hub sender ids, elevation, effective permissions and launcher calls
  for the rows marked verified above. Physical TV behavior remains unverified.
