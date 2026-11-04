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
| `watcher/{main,core,record,restore,preload,notify,gate,input}.c` | Boot lifecycle, serial event dispatch, reconcile, stock recovery, preload/close guards, notices, the 6–9 home gate mount and the 10+ last-input launch after boot |
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
| `listApps` | `{subscribe?}` | `{returnValue, apiVersion, subscribed?, apps:[…]}`; selected app fields only |
| `listRunning` | `{subscribe?}` | `{returnValue, apiVersion, subscribed?, running:[…]}`; selected process fields only |
| `getForegroundApp` | `{subscribe?}` | `{returnValue, apiVersion, subscribed?, appId, windowId?, processId?}`; one SAM lookup per reply |
| `getAppInfo` | `{id}` | `{returnValue, apiVersion, app:{…}}`; one `listApps` item |
| `getAppIcon` | `{id, size?}` | `{returnValue, apiVersion, mimeType, data, byteLength}`; base64, at most 256 KiB decoded |
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
`appInstallService/remove` for a store install, using the verified `folderPath`. A stale alert can
still remove its target after the Home selection changes. Targets cannot be Home Chooser,
Homebrew Channel, the requester, non-removable apps or apps outside developer/store app directories.
If alerts are refused, the service launches Home Chooser's confirmation screen. Only that screen
uses `uninstallConfirmed`: it needs a live random one-shot token, rechecks that the requester is
still Home, and first tries `removeLaunchPoint` before the appropriate `appInstallService` route.
The settings app allows 120 seconds for screen confirmation.

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
`rebootPrompt`.

On 11+ the service takes its provided groups when it registers: on 11.2.0 a changed file followed by
`ls-control scan-services` alone did not let a launcher in while the same service instance ran, and a
reboot did. So when apply changes the file on 11+, the running service must register again. Run by
the service (`accessApply`), the script prints `restart-service` and the settings app quits the
service and waits for a new root instance, as after elevation; run by the boot entry or the watcher,
it sends SIGTERM to `homechooser-service` (Home Chooser's own service only) and the hub starts it on
the next call. On first install the order is: elevation (quit, new root instance), `accessApply`
writes the file and scans, `restart-service`, quit, the next `status` starts an instance that
registers with the file in place. That a fresh registration is enough, without a reboot, is not yet
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
Host overrides: `HOMECHOOSER_LUNA_ROOTS`, `HOMECHOOSER_LS_CONTROL`, `HOMECHOOSER_OSRELEASE`, `HOMECHOOSER_NODE`.

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
directory exists on 6.6.0, 7.6.0 and 11.2.0 roots). Browser bookmark icons are not read until their
directory is verified on a device.

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
* **Icons**: `readIcon`, `getAppIcon` and `getIcons` (for app items) ask SAM for the app's `folderPath` and chosen icon field.
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
  output capped at 1 MiB per stream, a timeout each):

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
| `profileGet` | `/usr/bin/luna-send -a com.webos.applicationManager -n 1 luna://com.webos.service.systemprofile/getProfiles {"names":["defaultApps","idleApp"]}` (stdout is luna-send's reply; the exit status is 0 also for a false `returnValue`, so the caller reads it) | 20 s |
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
* `test-watcher` and `test-watcher-luna`: native watcher policy and child-stream runtime with an executable
  Luna double under qemu-arm and firmware libraries; includes runtime staging, removed-package restore,
  subscription EOF, garbage/pretty JSON, bounded hangs, parent death, cancellation, signal exit, preload retry/settle, close guards
  and the 10+ last-input launch after boot (once per boot, overlay only, nothing full-screen in front, Live TV fallback, no retry of a failed launch).
* `test-homekey`: native live-filter transactions with fake Luna.
* `test-launcher-contract` and `test-launcher-subscribe`: the launcher handlers against the API
  description on a fake hub, and the lists' subscriptions with real upstream children
  ([api/README.md](../api/README.md#how-it-is-checked)).
* `app/src/platform/tests`: the service client and the elevation flow against a mocked bridge.
* `service/scripts/tests/test-homechooser-access.sh`: manifest edits, per-release permissions, repair and cleanup
  against temporary roots and stubbed `ls-control`; watcher/boot tests cover the integration.
* The end-to-end runner ([`e2e/README.md`](../e2e/README.md)) checks hub sender ids, elevation,
  effective permissions and the launcher calls on a device (`--api-check`).
