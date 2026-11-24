# The root service

`org.webosbrew.homechooser.service` is a small native (C) luna service in the Home Chooser package
(`service/`). Homebrew Channel elevates it once, so it runs as root without the developer-mode
jail, and the app does every privileged thing through it. A developer app cannot make these calls
itself: the settings service's per-key whitelist refuses `general/defaultApps` to developer apps,
`closeByAppId` and `listApps` are in non-public groups, and on webOS 11 and later Homebrew Channel's
`exec` refuses ordinary apps ([Permissions](research/home-key-and-home-app.md#permissions)). A
service with a fixed set of narrow, allow-listed operations also keeps app input away from a shell.
See [Root access](settings-app.md#root-access) for the app side and
[architecture.md](architecture.md) for where the service sits.

## Package

`ares-package dist build/service-pkg` packs the app and the service together
(`npm run package` in `app/` builds the service with CMake into `build/service`, stages
`services.json`, the three binaries, three entry scripts and their `scripts/lib/` helpers with
`app/scripts/stage-service.js`, then packages). The ipk holds
`usr/palm/services/org.webosbrew.homechooser.service/{homechooser-service,homechooser-daemon,homechooser-home-gate,services.json,scripts/}` (binary mode
755) next to the app. `services.json` has `"engine": "native"` and `"executable":
"homechooser-service"`, so `ares-package` marks the ipk `arm`. The SDK and firmware paths of the
build, and the one source of the version, are in
[Build and test](build.md).

All three binaries are C, built by `service/CMakeLists.txt`. The service links `libluna-service2.so.3`,
`libpbnjson_c.so.2`, `libPmLogLib.so.3`, `libglib-2.0.so.0` and `libc.so.6`. The daemon and
`homechooser-home-gate` ([home-gate.md](home-gate.md)) link the same libraries **except
libluna-service2**. `cmake --build build/service --target check-abi` validates all three against the
oldest supported rootfs (5.6.1), rejects a libluna-service2 dependency in the daemon and the gate,
and rejects LS registration/call symbols in the daemon. No shared library is installed.

`services.json` declares only `homechooser-service`: only that executable receives
an LS2 service role. `homechooser-daemon` needs root for boot/state operations and
uses the existing privileged `/usr/bin/luna-send` role for every LS2 request.

Why the daemon does not use libluna-service2: the installer gives Home Chooser's executable a
`regular` role. With it, a call made on behalf of `com.webos.applicationManager`
(`LSCallFromApplication`) fails locally with "not privileged", and an executable with no role of its
own is refused registration ("Invalid permissions for (null)"). `/usr/bin/luna-send` has a
`privileged` role with `allowedNames:["*"]` and outbound `*`, so `luna-send -a
com.webos.applicationManager` children give the daemon the application identity the platform
methods need, without new permissions and without a role for the runtime copy that recovers an
uninstall from `/tmp`.

## Native source layout

`service/src/main.c` starts only the on-demand LS2 service, with the 24-method table.
`daemon-main.c` owns `--boot` and `--homekey <action>`.
The daemon applies the Home redirect and runs the C watcher on a single GLib
thread. All daemon LS2 traffic runs as `luna-send -a com.webos.applicationManager`
children. It never registers an LS2 client.

`json.c`, `luna.c`, `proc.c`, `fs.c`, `policy.c`, the Home-key transaction/generator,
`gate-mount.c` and `last-input.c` are compiled once into `libhomechooser.a` and linked into all three programs.
This archive is a build artifact, not part of the package.

| Source under `service/src/` | Responsibility |
| --- | --- |
| `log.h`, `version.h`, `roles.h` | Shared log context, build version and role entry points |
| `json.c` | pbnjson parsing, members and reply validation |
| `luna.c` | Bounded privileged one-shot calls through luna-send |
| `luna-child.c` | Child streams (daemon watcher, service subscriptions), GIOChannel JSON framing, deadlines, cancellation and reaping |
| `watcher/{main,core,record,restore,preload,notify,gate,input,previous,memory}.c` | Boot lifecycle, serial event dispatch, reconcile, stock recovery, preload/close guards, notices, the 6–9 home gate mount, the 10+ last-input launch after boot, the previous-launcher close and the memory manager keep list |
| `revive.c`, `service/revive-glue.c` | Watcher revival: the rate-limited check after a call (plain logic, and the GLib spawn it runs on) |
| `gate-mount.c` | Home gate bind mount: idempotent mount/unmount, propagated-copy cleanup, release marker, closing a running stock Home |
| `last-input.c` | The last TV input from an eim `getLastInput` reply, Live TV otherwise; used by the gate and the watcher |
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
| `service/launcher.c`, `service/launcher.h` | Launcher gates, consent, requests and private uninstall tokens |
| `service/catalog.c`, `service/catalog.h` | The launcher methods that subscribe (`listApps`, `listLaunchPoints`, `listInputs`, `listRunning`, `getForegroundApp`), the `getAppLifeEvents` stream and `getIcons` |
| `service/subscribe.c` | Their subscriptions: one shared upstream child per method, fan-out through the LS2 catalog |
| `service/system.c` | Retained reboot prompt method |

The extra shared `fs.c` keeps file access available to app metadata, boot links and service methods
without depending on the service role. Named commands have their own service module rather than
living in the generic process runner. Boot link RPCs retain their gates and replies in `bootlink.c`.
Service calls remain synchronous and bounded, except the launcher subscriptions (below). The daemon serializes watcher policy events and
uses GLib sources for confirmation/backoff, preload deadlines and child I/O. Subscription children
use `-i`; one-shot children use `-n 1` with a deadline while subscription I/O keeps running.
There is no shell or awk between the daemon and luna-send. Child EOF/exit ends a stream:
settings loss exits 1 for unit restart, apps loss keeps the 300 s ticker, foreground/life loss logs
and continues; the keyFilters feed (6-9 only, [home-key-hook.md](home-key-hook.md#configd-puts-the-stock-list-back)) loss logs and leaves it to the 300 s ticker. JSON false replies alone are not EOF. Children inherit the daemon process group,
are killed/reaped on cancellation and clean shutdown, and receive SIGKILL through
`PR_SET_PDEATHSIG` if their parent is killed. The systemd unit cgroup remains the outer cleanup
boundary. SIGCHLD uses a self-pipe, avoiding GLib child-watch worker threads.
Idle residency is the daemon plus two subscription children (settings and app list), four on 10+
with preload enabled (also foreground and life events).

The internal Home-key worker status uses `active=yes/no` for whether configd selects a generated
Home Chooser filter. The app and E2E parser use that field directly, without a legacy field alias.

## Elevation

The installer gives a developer-mode native service a launch line of the form
`Exec=/usr/bin/jailer ... <binary>`. Homebrew Channel's `elevateService {"id": <service id>}`
(`services/elevate-service.ts`) rewrites that to `Exec=<binary>`, adds `*` to the role file's
`allowedNames` and outbound permissions, creates the service's `.root.json` client-permission file
and the `.api.public.json` API-permission file (`public: ["<id>/*"]`, which makes its methods
callable by apps), adds both to the manifest, and runs `ls-control scan-services`. Because of the
public permission the service checks its callers itself (below).

The running instance was started from the old launch line and is still jailed and not root. The
app therefore calls `status`; when `root` is false it first calls `quit` and `status` (the launch line may
be rewritten by an earlier elevation that outlived its caller), and if the service is still not root it
calls `elevateService` (120 s budget: 40 to 60 s on a slow TV), then `quit`, then `status` until the new instance (started on demand by the hub from the rewritten line) reports
`root: true`. A timeout or error of `elevateService` does not end the sequence: `quit` and `status` run
anyway. The sequence follows the AltHome port's auto-elevator.

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
| `readIcon` | `{appId}` | `{returnValue, mime, data}` with `data` base64; at most 1 MiB |
| `link` / `unlink` / `linkStatus` | `{}` | the boot entry, below |
| `run` | `{command}` | `{returnValue, exitCode, stdoutString, stderrString}` |
| `getInfo` | `{}` | `{returnValue, apiVersion, version, root, permission, permitted, isSelectedHome}` |
| `listApps` | `{subscribe?}` | `{returnValue, apiVersion, subscribed?, apps:[…]}`; selected app fields only |
| `listRunning` | `{subscribe?}` | `{returnValue, apiVersion, subscribed?, running:[…]}`; selected process fields only |
| `getForegroundApp` | `{subscribe?}` | `{returnValue, apiVersion, subscribed?, appId, windowId?, processId?}`; one SAM lookup per reply |
| `getAppInfo` | `{id}` | `{returnValue, apiVersion, app:{…}}`; one `listApps` item |
| `getAppIcon` | `{id, size?}` | `{returnValue, apiVersion, mimeType, data, byteLength}`; base64, at most 1 MiB decoded |
| `listLaunchPoints` | `{subscribe?}` | `{returnValue, apiVersion, subscribed?, launchPoints:[…]}`; SAM's launch points, selected fields |
| `listInputs` | `{subscribe?}` | `{returnValue, apiVersion, subscribed?, inputs:[…]}`; eim `getAllInputStatus`, selected fields, plus `params` (the picker's launch params) and `signal` (`hdmiSignalExist`, where present) |
| `getIcons` | `{items}` | `{returnValue, apiVersion, icons:[…]}`; one entry per item, at most 64 items and 1 MiB of icons; `size` is `icon`, `largeIcon`, `mediumLargeIcon` or `extraLargeIcon` (the last two level 3), with no fallback between sizes |
| `getAppLifeEvents` | `{subscribe: true}` | Level 3, a stream: first `{returnValue, apiVersion, subscribed}` (no SAM read), then one reply per SAM event with the whitelisted fields (`hc_life_event_fields`); `subscribe` other than `true` is `badRequest` |
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
root/consent gate, and reports consent and selection. This check is the real gate: on 5.x to 10.x
every app reaches the service through Homebrew Channel's `public` API file, and on 11.x and 12.x
through LG's `application.query` and `application.launcher` groups in the access file below.

`uninstallApp` checks the target and caller before showing an `Uninstall <title>?` alert. The
Uninstall button calls LG's `appInstallService/dev/remove` for a developer install or
`appInstallService/remove` for a store install, using the verified `folderPath`; on webOS 4.0 and 4.5
it calls this service's `uninstallConfirmed` with a one-shot token instead (see below). A stale alert
can still remove its target after the Home selection changes, except on 4.x, where the token recheck applies. Targets cannot be Home Chooser,
Homebrew Channel, the requester, non-removable apps or apps outside developer/store app directories.
If alerts are refused, the service launches Home Chooser's confirmation screen. Only that screen
uses `uninstallConfirmed`: it needs a live random one-shot token, rechecks that the requester is
still Home, and first tries `removeLaunchPoint` before the appropriate `appInstallService` route.
The settings app allows 120 seconds for screen confirmation.

### Native launchers

A native executable's luna messages carry no application id: the hub stamps one only on calls from
the web, QML and node containers (`LSRegisterApplicationService` is refused outside
`/usr/share/luna-service2/containers.d`, and `LSCallFromApplication` needs a privileged role, which
Homebrew Channel's elevation does not give: its roles keep type `regular`). So `launcher_gate` falls
back, only when the application id is empty, to the sender service name, which the hub fixes from the
role (a native app's own handle is registered under its app id; ls-monitor shows
`org.webosbrew.dock` as the caller of `getInfo`). The name is admitted as the app of that id when SAM's
`getAppInfo` says `type` `native`, `appCategory` has `home` and `folderPath` is the developer apps
folder of that id, and `nameowner.c` finds that every role file that allows the name belongs to the app's
own executable (`/media/developer/apps/usr/palm/applications/<id>/...`), with at least one. It scans the
hub's own data: the role lists of every manifest in `ManifestsDirectories` and
`ManifestsVolatileDirectories` (read from `/etc/luna-service2/ls-hubd.conf`, plus the fixed
defaults), the legacy `ls2/roles/{pub,prv}` and `ls2-dev/roles/{pub,prv}` directories and the `roles.d`
directories, system and developer. Allowed names are glob patterns (`*`, `?`) as in the hub. A role
whose pattern is exactly `*` is not counted (firmware tools such as luna-send and node, and
executables Homebrew Channel elevated, among them this service). Unreadable or malformed data refuses. The
log says which path admitted a caller (`CALL ... (native service name)` or `(application id)`) and why a
native candidate was refused (`REFUSED ... not a native launcher's own name: <why>`). After admission
every launcher method treats the caller as the app id, including the selected-Home checks; the SAM
check runs before `notRoot` and `notHome`, so the order of refusals is unchanged.

Concluded facts (webOS 5.6.1, 6.5.2, 8.3.0, 9.2.4 and 11.2.0 unless noted; firmware trees for the static
parts of 5.6.1, 5.6.2, 6.6.0, 7.6.0 and 11.2.0):

* appinstalld accepts a service only if its id starts with `<package app id>.` (error `WRONG_SERVICEID`).
  A package `org.webosbrew.hcfixture.native` with the service `org.webosbrew.hcfixture.native.probe` installs
  and its executable registers that name, which is another app's id (the E2E kit does this on 5.6.1,
  8.3.0, 9.2.4 and 11.2.0). The hub itself only matches the name against the role's `allowedNames`.
* The hub reads manifests (`/var/luna-service2-dev/manifests.d/<package>.json`: `roleFiles`,
  `roleFilesPub`, `roleFilesPrv`). On 5.6.1 to 9.2.4 the app's role is
  `/var/palm/ls2-dev/roles/{pub,prv}/<id>.json` (`{"role": {exeName, type, allowedNames}, "permissions": [...]}`),
  a service's is `/var/luna-service2-dev/roles.d/<service id>.service.json` (flat). On 11.2.0 both are in
  `roles.d` (`<id>.app.json`, `<service id>.service.json`) and `ls2-dev` is absent. The app's role has type
  `regular` and `allowedNames` `[..., "<id>", ""]`. Firmware roles are in `/usr/share/luna-service2/roles.d`,
  and in the legacy `/usr/share/ls2/roles/{pub,prv}` (which also holds permission maps, and files with
  comments, trailing commas or no content). 10.x was not examined.
* `requiredPermissions` of a native app does not reach its bus role: its client-permissions file is
  `{"<id>": ["public", "<id>.service.group"]}` (5.6.1, 6.5.2, 8.3.0, 9.2.4); on 11.2.0 the file lists LG's
  groups. The calls reach the service through its public API file, as for web apps, so `permitted` stays
  the service's own reading of the installed appinfo. Before elevation the hub denies the call
  (reported by the Dock project, not re-run).
* No firmware's `libluna-service2.so` here exports `LSMessageGetSenderExePath` or `LSMessageGetSenderPid`
  (`nm -D` on 12 trees), so the sender's executable cannot be read; the role files are the only evidence.
* While a package with a conflicting role is installed, the real app is refused too (fail closed).
* Not seen: a native app calling `applicationManager/launch` from its own handle.

See [Launcher API level 2](launcher-api.md) for the contract, fields, errors, caller rules,
selection requests and absence detection. Plain shell callers have no application id and are
refused by launcher gates.

## Restarting the TV

Nothing needs a restart. On webOS 6–9, hook installation, runtime changes and disable use a live
configd **volatile** key-filter reload; no GUI process is restarted and no reboot prompt is raised.
The `rebootPrompt` RPC remains as a compatibility endpoint: status never reports a pending reboot,
so it refuses with `notPending`. See [the live-filter design](home-key-hook.md).

## Privileged luna identity

Privileged RPC workers and every daemon LS2 operation use
`/usr/bin/luna-send -a com.webos.applicationManager`, the constant `HC_LUNA_IDENTITY`.
The daemon never calls LSRegister or impersonates an application through its own connection.
These privileged operations include `listApps`, `listRunning`, every
`getAppInfo` lookup (consent, icons, uninstall, forwarding), `closeByAppId`, settings/default-app
calls, notifications, removal, the fixed profile commands and, on 10+, the watcher's `eim/getLastInput`
and last-input `launch` after boot. Arguments go directly to `execve`,
without a shell, using allow-listed URIs and the settings payload restriction below. Watcher
calls and subscriptions carry the same fixed identity.

Native one-shot calls use `luna_run` with a 20 second bound. They run serially, so a slow query or
confirmation can delay other requests. The alert and screen-launch bounds are 8 and 15 seconds;
streamed removal has a 90 second bound. Output is capped at 1 MiB per stream (a launch point list
of a set with many apps is larger than the former 64 KiB). JSON replies retain
UTF-8. Children are reaped after exit or timeout. Fixed profile commands use the same argv identity.
The service's own identity is not used for these calls: `systemprofile/getProfiles` (12.x) and
`systemprofile/debug/rescan` (11.x and 12.x) refuse it.

## Luna access files

`service/scripts/homechooser-access apply|remove|status` manages Home Chooser's access files under
`/var/luna-service2-dev` and `/var/luna-service2`. Apply acts where
`manifests.d/org.webosbrew.homechooser.json` exists. It writes
`api-permissions.d/org.webosbrew.homechooser.launcher.api.json` (`org.webosbrew.homechooser.applications`: the
launcher methods) and adds its absolute path to `apiPermissionFiles`, creating the array if
missing. On major 11+ the same methods are also under `ares.webos.cli`, and under two of LG's public
groups that 11.x and 12.x give every developer app: the reading methods under `application.query`,
`closeApp` and `uninstallApp` under `application.launcher` (never `default.permission`). The method
list is the one variable `LAUNCHER_METHODS` at the top of the script, each entry `method:group`
(`query` or `launcher`), which every grant is built from; `test-homechooser-access.sh` checks it
against the spec's `x-permission` (`grants` plus `getInfo`). None includes `uninstallConfirmed` or
`rebootPrompt`; on 4.x `uninstallConfirmed` alone is also granted to the `private` group, in its own manifest
([Launcher API: confirmation and removal](#launcher-api-confirmation-and-removal)).

On 11+ the service takes its provided groups when it registers: on 11.2.0 a changed file followed by
`ls-control scan-services` alone did not let a launcher in while the same service instance ran, and a
reboot did. So when apply changes the file on 11+, the running service must register again. Run by
the service (`accessApply`), the script prints `restart-service` and the settings app quits the
service and waits for a new root instance, as after elevation; run by the boot entry or the watcher,
it sends SIGTERM to `homechooser-service` (Home Chooser's own service only) and the hub starts it on
the next call. On first install the order is: elevation (quit, new root instance), `accessApply`
writes the file and scans, `restart-service`, quit, the next `status` starts an instance that
registers with the file in place. The settings app can be closed between `restart-service` and the
quit, when the file already matches. So the script notes the pid of the instance it told in
`/tmp/homechooser-runtime/access-restart-pending` and prints `restart-service` again while that
instance runs; once another instance runs, it removes the note (a reboot clears it too). That a fresh registration is enough, without a reboot, is not yet
verified on a device. The group is declared only by that API file, the way the installer declares
`<service>.group` for every developer service.

On webOS 9.2.1, Surface Manager turns `onclick` plus `params` into an alert action. Its role
allows calls to LG's `com.webos.*` services but denies `org.webosbrew.*` before our handler runs.
The buttons therefore call LG's tvpower and appInstallService directly. Neither Home Chooser's
role nor the vendor role grants an extra callback service.

No `groupsFiles` entry and no `groups.d` file. A package manifest that declares a group through
`groupsFiles` crashes ls-hubd on webOS 9.2.1 (`g_hash_table_iter_next`, NULL table) when
appinstalld unloads it at "removing service files"
(`luna://com.webos.service.bus/removeOneManifest`). appinstalld retries that removal at every boot,
so the bus never comes up again. The same crash at uninstall happens on 11.2.0, which recovers after
a reboot. A manifest that provides the group only through `apiPermissionFiles` is removed without a
crash, and so is `ls-control scan-services` after dropping such an entry. The installer's own
`api-permissions.d/org.webosbrew.homechooser.service.api.json` belongs to the installer and is not
touched. The access script manages only its current access files.

The elevated service's broader public registration still requires the service's own caller gates.

Apply is idempotent and repairs drift. Remove drops these manifest entries and files, including
files whose package manifest has disappeared. `ls-control scan-services` runs only after a change;
status prints file presence. The app best-effort applies after elevation, the boot entry applies
without needing a Home choice, and the watcher applies at start. The watcher stages the access
worker into its runtime directory so uninstall cleanup works after the app directory disappears.
Host overrides: `HOMECHOOSER_LUNA_ROOTS`, `HOMECHOOSER_LS_CONTROL`, `HOMECHOOSER_RELEASE_FILE`, `HOMECHOOSER_NODE`.

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
  no Home Chooser token, except on 4.x, where the button carries the token of `uninstallConfirmed`.
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
* **Caller identity and launcher gates.** The caller is `LSMessageGetApplicationID` (dropping an
  instance suffix after a space). The service independently reads the caller's installed
  `requiredPermissions`; `closeApp` and `uninstallApp` also compare the id with the stored choice in
  `/var/lib/webosbrew/homechooser/home`. These are service checks even where the hub allows the call
  through a broader group.

The firmware's `com.webos.appinstalld.service` registers `com.webos.appInstallService`. On 9.2.1
its API permissions list `dev/remove` under `ares.webos.cli` and `remove` under
`appinstalld.internal`, and Surface Manager's client permissions grant `all`, so an alert button
reaches both methods as `com.webos.applicationManager`. A developer app's `getAppInfo.folderPath` is
under `/media/developer/apps/usr/palm/applications/`, which is how the service picks the developer
endpoint.

On webOS 4.0 and 4.5 (W18R 05.50.70.01, W19P 05.40.97.01) the firmware has no
`client-permissions.d/com.webos.surfacemanager.perm.json`, which 5.x (W20H 04.74.00.01) and later ship with
`com.webos.surfacemanager*: [all]`. There Surface Manager, which makes the alert button's call (the log line is
`surface-manager LSM callForSource, AlertCallback com.webos.applicationManager luna://... /dev/ remove`), holds only
`private` (`client-permissions.d/compat.perm.json`: `"com.webos.surfacemanager": ["private"]`). appinstalld lists
`/dev/remove` only under `ares.webos.cli` and `remove` under `appinstalld.internal`
(`api-permissions.d/com.webos.appinstalld.api.json`), so the call is refused with `LS_REQUIRES_SECURITY`
(`Service security groups don't allow method call`, `Denied method call "remove" for category "/dev"`), the alert
closes and nothing is removed. So on a release below 5 (`/etc/starfish-release`) the service makes the
screen-fallback token before the alert and the Uninstall button calls
`luna://org.webosbrew.homechooser.service/uninstallConfirmed {token}`; the service then removes the app as root,
exactly as for the screen confirmation. The call carries the application id `com.webos.applicationManager` (the
identity that raised the alert), which `uninstallConfirmed` accepts besides Home Chooser's own and none; the token
is the capability. For the hub to admit it, `homechooser-access apply` writes, on 4.x only,
`api-permissions.d/org.webosbrew.homechooser.confirm.api.json` (`{"private": ["<service>/uninstallConfirmed"]}`: that one
method, no other, in the group Surface Manager holds) and a manifest of its own, `manifests.d/org.webosbrew.homechooser.confirm.json`
(the hub skips a manifest it has already loaded, so the installer's cannot take a new file; a manifest needs a version).
The service takes its groups when it registers, so the apply asks for a service restart (`restart-service`) when the file
changed; `remove` and any release from 5 on delete both files. `rebootPrompt`'s button calls `tvpower/power/reboot` directly; it is never shown
on 4.x (the Home key needs no reboot there), so that button is unchanged and unverified on 4.x.

## Launcher lists, icons and subscriptions

`listApps`, `listLaunchPoints`, `listInputs`, `listRunning` and `getForegroundApp` read
`applicationManager/listApps` (with the published `properties`), `applicationManager/listLaunchPoints`,
`com.webos.service.eim/getAllInputStatus`, `applicationManager/running` and
`applicationManager/getForegroundAppInfo` with one `luna_run` each, after the caller's consent lookup. `getIcons` makes the consent lookup and
then at most one read of each list its items need (`listApps` with `id`, `folderPath`, `icon`,
`largeIcon`; `listLaunchPoints`; `getAllInputStatus`), then reads the files. Nothing is cached: no
list, appinfo or icon outlives the request, and every reply is read from the platform when it is
sent. Launchers cache ([Subscriptions](launcher-api.md#subscriptions)).

**Subscriptions** (`subscribe.c`). A first reply is the same fresh read as a single call, with
`subscribed:true`; then the message joins the hub's subscription catalog (`LSSubscriptionAdd`, key
`launcher/<method>`). Per method, one upstream `luna-send -a com.webos.applicationManager -i <uri>
{...,"subscribe":true}` child (the daemon's `luna-child.c` transport: GIOChannel framing, self-pipe
SIGCHLD, reaping, `PR_SET_PDEATHSIG`) runs while the method has a subscriber: it starts with the first
and is killed when `LSSubscriptionGetHandleSubscribersCount` is zero after the hub's cancel callback
(`LSSubscriptionSetCancelFunction`; a cancel and a subscriber that went away both call it). The
upstream's replies are only a signal, whatever their shape (SAM sends single-item deltas for
launch points): its first reply after a start is the state just sent and is skipped; any later one
schedules, 200 ms later (a burst is one read), a non-blocking one-shot `luna-send -n 1` of the whole
state, bounded at 20 s, whose result goes to every subscriber with `LSSubscriptionReply`. A dead
upstream is restarted after 1 s, doubling to 60 s, and its first reply after a restart counts as a
change. A failed read is retried with the same backoff. A method takes 16 subscribers;
`tooManySubscriptions` refuses more. Idle cost: nothing without subscribers; one luna-send child per
subscribed method. `getAppInfo`, `getAppIcon` and `getIcons` take no `subscribe`: SAM's `getAppInfo`
has no subscription (its schema has only `id` and `properties`), and icon files have no change feed.

`getAppLifeEvents` (level 3) shares this machinery (one upstream child, the 16-subscriber cap, the
restart backoff) but is a stream, not a state: its first reply is the service's own acknowledgement
with no SAM read, and each upstream reply that carries a string `event` goes to every subscriber at
once through `LSSubscriptionReply`, cut to `hc_life_event_fields`. There is no debounce, no read and
no copy: SAM sends one event per message, so coalescing would lose events, and events missed while
the upstream is down are not replayed. The upstream's own acknowledgement (no `event`) is dropped.

Why children rather than the LS2 client API: the service's own identity cannot make the privileged
calls (`listApps` and `listLaunchPoints` are in non-public groups; [Privileged luna
identity](#privileged-luna-identity)), and every other outgoing call already goes through
`luna-send -a com.webos.applicationManager`. The daemon's tested stream transport is reused as is.

**Icon directories.** An app icon is resolved as for `getAppIcon`. A launch point icon is SAM's
absolute path; it must resolve (`realpath`, no `..`, image extension) inside
`<root>/<launch point id field>` for a root of `/usr/palm/applications`,
`/mnt/otncabi/usr/palm/applications`, `/media/cryptofs/apps/usr/palm/applications` or
`/media/developer/apps/usr/palm/applications` (5.6.1: SAM joins the app folder into launch point
icons, `/mnt/otncabi/usr/palm/applications/netflix/icon.png`). An input icon is eim's
`iconPrefix + icon` and must resolve under `/usr/palm/plugins/inputapps/assets` (5.6.1 device; the
directory exists on 6.6.0, 7.6.0 and 11.2.0 roots). A bookmark launch point (`lptype` `bookmark`, app
`com.webos.app.browser`) has its icon in the browser's favicon cache, and that directory is allowed for
it alone: `/mnt/lg/cache/webbrowser/chrome/Default/favicon/<number>.jpg` (11.x device: a JPEG, 0644
owned by the browser's user, in a directory the browser's user can write, which is why the real path is
checked). Its `favicon` field is a generic browser asset and is not forwarded.

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
  `/etc/starfish-release`, because a web app cannot read it and the app needs the release. The
  writable files are opened with `O_NOFOLLOW`. `/etc/starfish-release` is a regular file on every webOS
  firmware from 1.4 to 12 (one line, `Rockhopper release 4.10.2-29 (goldilocks2-grampians)`), so
  it is read as is; the release is the first `release MAJOR.MINOR.PATCH` in it, and a file without one is
  an unknown release. `/etc/os-release` (absent on 3.x and 4.x) is not read anywhere.
* **Icons**: `readIcon`, `getAppIcon` and `getIcons` (for app items) ask SAM for the app's `folderPath` and chosen icon field.
  They join the path (no `..`), require an image extension (png, jpg, jpeg, gif, svg, webp), resolve
  symlinks and require the result to lie inside the real `folderPath`, then return a regular
  file of at most 1 MiB.
* **Boot entry**: `link` creates `/var/lib/webosbrew/init.d`, sets mode 755 on the installed scripts,
  on 5/10–12 stages `homechooser-daemon` and boot/access helpers in `/var/lib/webosbrew/homechooser/boot`, then links
  `/var/lib/webosbrew/init.d/homechooser` to that boot copy on 5/10–12, or to the installed entry on 6–9 (replacing an earlier entry). The entry queues `homechooser-daemon --boot` in a nonblocking transient unit, before RPC name registration; it becomes the watcher after apply. `unlink`
  removes the link and copy.
  `linkStatus` returns `entry` (`none`, `file`, `link`, `dangling`) and `target`. The service directory
  is the fixed `/media/developer/apps/usr/palm/services/org.webosbrew.homechooser.service`.
* **Watcher supervision and revival**: on systemd the watcher is the transient unit `homechooser-home`
  (`Restart=on-failure`), which systemd keeps up. webOS 4.x runs upstart (1.10, jobs only from read-only `/etc/init`, no
  transient jobs), so there the watcher is a detached shell loop with a pid file that restarts the daemon on failure but
  stays dead if the loop itself is killed. Both are revived by the service: after every call that does real work (the
  app's `call`, `run`, file and link methods, and every launcher method; not `status` or `quit`), `hc_revive_touch` runs
  once the handler has replied. It does nothing unless the service is root, and at most once per 30 s per process. If a
  home choice is stored (`/var/lib/webosbrew/homechooser/home` is a regular file: the same condition the boot entry starts the
  watcher on, and the one a restore or uninstall removes) it spawns `homechooser active` without waiting; on a
  non-zero exit, if the choice is still stored, it spawns `homechooser watch`, the app's own start path (systemd-run or the loop),
  and logs `WATCHER_REVIVED` when that succeeds (`WATCHER_REVIVE_FAILED` with the exit status otherwise). Only one such command is
  in flight (killed after 25 s); a running watcher costs one quiet process per 30 s and no log line. A `run` of `stopWatcher`
  and `unlink` hold revival for 60 s and abandon a check in flight, so a restore that stops the watcher before it removes the choice is not undone.
* **Commands** (`run`, by name, fixed argv, no shell, minimal environment, stdin from `/dev/null`,
  output capped at 1 MiB per stream, a timeout each):

| Name | argv | Timeout |
| --- | --- | --- |
| `watch` | `/bin/sh <service>/scripts/homechooser watch` | 20 s |
| `homekeyStatus` | `/bin/sh <service>/scripts/homechooser-homekey status` | 20 s |
| `homekeyPrepare` | same with `prepare` (validate the native generator against this device’s stock JS) | 120 s |
| `homekeyClear` | same with `clear` (restore stock and delete unused JS versions) | 20 s |
| `homekeyApply` | same with `apply` (native, checksum-verified volatile reload) | 90 s |
| `homekeyApplyClear` | same with `apply clear-override`: first clears another app's persistent keyFilters list, only after the owner agreed in the app ([home-key-hook.md](home-key-hook.md#another-app-saved-keyfilters-persistently)) | 90 s |
| `homekeyStop` | same with `stop` (restore exact stock list volatile before deleting unused copies) | 90 s |
| `homekeyRefresh` | same with `refresh` (live launcher change, no process restart) | 90 s |
| `stopWatcher` | `/bin/sh <service>/scripts/homechooser stop`: `systemctl stop homechooser-home`, or on 4.x (no systemd) the watcher loop's process group from `/tmp/homechooser-runtime/watcher.pid` (a non-zero exit is not an error) | 20 s |
| `watcherActive` | same with `active`: prints `active` or `inactive` (`systemctl is-active homechooser-home`, or the pid file on 4.x; a non-zero exit is not an error) | 10 s |
| `profileGet` | `/usr/bin/luna-send -a com.webos.applicationManager -n 1 luna://com.webos.service.systemprofile/getProfiles {"names":["defaultApps","idleApp"]}` (stdout is luna-send's reply; the exit status is 0 also for a false `returnValue`, so the caller reads it) | 20 s |
| `profileRescan` | `/usr/bin/luna-send -a com.webos.applicationManager -n 1 luna://com.webos.service.systemprofile/debug/rescan {}` (same) | 30 s |
| `accessApply` | `/bin/sh <service>/scripts/homechooser-access apply` | 30 s |
| `accessRemove` | same with `remove` | 30 s |
| `accessStatus` | same with `status` (non-zero exit is not an error) | 10 s |
| `watchLog` | not run: the last 5 lines of `/tmp/homechooser-home.log` | - |

A non-zero exit of any other command is `errorCode: "exitStatus"` with the output attached; a
command that does not finish is killed (`timeout`). The service does not wait for a background
process that outlives the command (the `watch` script starts a transient unit).

## Keeping the launcher alive

On sets with little memory (seen on 1.5 GB sets running 5.x, 6.x and 7.x) the memory manager, `memchute`, closes a background app whenever another app opens, unless the app is on one of its keep lists. A launcher chosen by the owner is closed that way too, and Home starts it cold. While a launcher other than LG Home is chosen, the watcher (`watcher/memory.c`) puts the launcher's app id on `KeepOnLaunchEx` (`KeepOnLaunch` on 4.0, which has no `KeepOnLaunchEx`). Under real memory pressure the memory manager still closes the launcher with the other background apps; the keep list covers only the close at launch.

`memchute` reads its lists once, when it starts, so the change takes effect with a restart of `memchute`. The restart showed no visible side effect on a device; the preload manager restarts with it on systemd releases. How the lists are replaced depends on the release:

| webOS | How | Restart |
| --- | --- | --- |
| 4.0 to 9.x | `memchute` reads `/home/root/memorymanager-conf.json` in place of `/etc/palm/memorymanager-conf.json`, the whole file. Home Chooser keeps a copy of the TV's stock file with the launcher added to the list at `/var/lib/webosbrew/homechooser/memorymanager-conf.json` (written to a temporary file in that directory and renamed into place: a partial file silently changes `memchute`'s policy mode on 6.x) and links `/home/root/memorymanager-conf.json` to it. Never a regular file at `/home/root`. A path there that is not our link is someone else's override: left alone and logged | `systemctl restart memchute`; on 4.x (upstart) `initctl restart memchute`, or `initctl start memchute` when the job is not running |
| 10 and later | `memchute` ignores the link and takes its lists from configd at start. The watcher writes a volatile `com.webos.memorymanager.KeepOnLaunchEx` (the current value plus the launcher) through `setConfigs` with `volatile:true`, never without it, and reads it back: only an exactly equal read-back counts. A value that does not read back (a persistent value masks it, or the write is refused) is logged and left, not fought. Every id we may have put on the list is written to `keep-launcher` in the state directory before the write, one a line, so a restarted watcher recognises its own value and whatever configd ended up holding is ours to take away (a value that was written and never read back, or that a persistent value masks, included) | the same |

The copy is rebuilt from the TV's current stock file at every boot, so a firmware update's new stock file is not masked. `memchute` is restarted only when what it would read changed (the copy or the link, or the configd value), never in a loop, and each restart is logged in the watcher log. A restart that is owed is the file `keep-restart` in the state directory until one succeeds, so a watcher that ends first leaves it for the next one (an undo whose restart failed included). A failed restart is tried again after 60 s, 120 s, 240 s and so on, at most 5 times per watcher run, then left for the next run. An app is added only once SAM confirms it as installed, so an id is not added and taken away again at the cost of two restarts. If configd is not ready when the watcher starts (no usable read, a refused write, no read-back yet) the watcher tries again after 10 s, 20 s, 40 s and so on, at most 5 times, so the entry lands soon after boot; a read-back that is there and differs (a mask) is not retried. On 10 and later a link and copy left by an earlier 4.0 to 9.x release (our link into the state directory, our copy) are removed once, without a restart, because `memchute` ignores them there; a file at that path that is not our link stays. The volatile value does not survive a reboot: on 10 and later every boot finds the marker without a value and writes it again (one restart per boot, the boot apply; it does not count as a drop). configd may also select its layers again while the TV runs and drop the value; the 300 s check writes it again, at most 5 times per watcher run. A launcher that is not installed, a stock Home (`com.webos.app.home`, `com.webos.app.fullhome`) and an invalid id get no entry.

Everything is undone, with a `memchute` restart, when LG Home is chosen again (the watcher started for the previous-launcher close, or at the next boot, finds no choice), when the launcher is uninstalled, when Home Chooser is uninstalled (`restore_all`), and when the watcher ends cleanly with no launcher chosen: the link (only if it is ours) and the copy go on 4.0 to 9.x, on 10 and later a volatile `null` of the configd key, which puts the layer's own value back; when that is not exactly what is left of the list without our ids, another component's volatile value was in it, and the list without our ids is written back (volatile) and read back instead. A value on the list that is not ours is never touched. A link whose copy is gone (Home Chooser removed without the watcher) was checked on 6.x only, where `memchute` falls back to `/etc/palm`; 4.x and 5.x are not checked. A watcher that ends on a signal with a launcher still chosen keeps the entry. On 4.0 to 9.x the next boot compares and restarts nothing; on 10 and later the volatile value is gone after the reboot, so the next boot writes it again and restarts memchute once.

## Logs

Every operation, and every refusal, goes to PmLog with the context `HomeChooser`
(read it from the journal or with `PmLogCtl`). Current message ids: `START`, `CALL`,
`CALL_FORWARD`, `CALL_DENIED`, `CALL_FAILED`, `REPLY_FAILED`, `RUN`, `RUN_DENIED`,
`RUN_STDERR`, `WROTE`, `REMOVED`, `LINKED`, `UNLINKED`, `PATH_DENIED`, `REFUSED`, `SUBSCRIBE_UP`,
`SUBSCRIBE_DOWN`, `SUBSCRIBE_LOST`, `SUBSCRIBE_RETRY`, `SUBSCRIBE_READ`, `SUBSCRIBE_FAILED`,
`NOT_ROOT`, `ICON_NONE`, `LAUNCHER_ERROR`, `UNINSTALL_ASK`, `UNINSTALL`, `REBOOT_ASK`,
`REBOOT`, `HOMEKEY`, `REGISTER_FAILED`, `EXIT`. These ids are internal. The app logs its side with
`[HomeChooser]` in the web console.

## Tests

The native tests run with CTest: `cmake -S service/test -B build/service-host`,
`cmake --build build/service-host`, then `ctest --test-dir build/service-host`.

* `test-policy`: the pure policy (sender, file and call allow-lists, argv table, icon
  path rules, base64, log tail) compiled natively with address and undefined-behaviour sanitizers.
* `test-revive`: watcher revival with a fake clock, stored choice and commands (rate limit, root only, no stored choice, one start at a time, active and inactive paths, the hold).
* `test-watcher` and `test-watcher-luna`: native watcher policy and child-stream runtime with an executable
  Luna double under qemu-arm and firmware libraries; includes runtime staging, removed-package restore,
  subscription EOF, garbage/pretty JSON, bounded hangs, parent death, cancellation, signal exit, preload retry/settle, close guards
  and the 10+ last-input launch after boot (once per boot, overlay only, nothing full-screen in front, Live TV fallback, no retry of a failed launch);
  the launcher keep list (`memory.c`) with fakes for the files, configd and the `memchute` restart: the copy and link on 4.0 to 9.x, the volatile value on 10+, restart only on change, firmware update, changed choice, someone else's file, masked value, every undo.
* `test-homekey`: native live-filter transactions with fake Luna.
* `test-launcher-contract` and `test-launcher-subscribe`: the launcher handlers against the API
  description on a fake hub, and the lists' subscriptions with real upstream children
  ([api/README.md](../api/README.md#how-it-is-checked)).
* `app/src/platform/tests`: the service client and the elevation flow against a mocked bridge.
* `service/scripts/tests/test-homechooser-access.sh`: manifest edits, per-release permissions, repair and cleanup
  against temporary roots and stubbed `ls-control`; watcher/boot tests cover the integration.
* The end-to-end runner ([`e2e/README.md`](../e2e/README.md)) checks hub sender ids, elevation,
  effective permissions and the launcher calls on a device (`--api-check`).
