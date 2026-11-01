# Writing a launcher for Home Chooser (API version 1)

Home Chooser (`org.webosbrew.homechooser`) lets the owner of a rooted LG webOS TV choose which app
the Home button opens. This guide is for the authors of such apps (launchers, docks, alternative
home screens). It covers what the owner and Home Chooser do for you, how to declare your app, and
the luna API that Home Chooser's root service offers to a launcher.

Start from [`examples/web-launcher`](../examples/web-launcher/README.md), a small complete web
launcher, and [`api/client`](../api/client), a one-file client generated from the
[API description](../api/launcher-v1.openrpc.json) with types and a mock for a desktop browser.
Terms such as SAM, the watcher or the home gate are explained in the [glossary](#glossary).

## Checklist

1. **Declare the app.** In `appinfo.json`, `"appCategory": ["home"]` makes it selectable and
   `"requiredPermissions": ["org.webosbrew.homechooser.applications"]` lets it call the API
   ([Declare the app](#1-declare-the-app)).
2. **Install Home Chooser first and open it once, then install your launcher.** Opening Home
   Chooser finishes its setup, which makes the permission available. A launcher installed before
   that is refused with `Not allowed permission is used`: open Home Chooser and install the
   launcher again. Reinstall after any change to `requiredPermissions`.
3. **Call `getInfo` at start and branch.** The call fails: Home Chooser is missing or too old.
   `root` false: ask the owner to open Home Chooser. `permitted` false: reinstall the launcher.
   `isSelectedHome` false: offer the [request launch](#3-asking-to-become-home)
   ([Check where you stand](#4-check-where-you-stand-then-use-the-api)).
4. **List the apps** with `listApps` and read their icons with `getAppIcon` ([Icons](#icons)).
5. **Launch apps, handle Back and `webOSRelaunch`** with the platform's own calls
   ([Platform calls you'll need](#platform-calls-youll-need)).
6. **Choose full-screen or overlay** ([below](#full-screen-or-overlay)).
7. **Try it** in a desktop browser with the mock, then on a TV with the compatibility kit
   ([Testing your launcher](#testing-your-launcher)).

### Full-screen or overlay

| Your launcher | Window | `appinfo.json` | When the last app closes |
| --- | --- | --- | --- |
| A home screen: an app grid, rows of content | full-screen | nothing extra | your launcher opens: it is where navigation starts and ends |
| A dock or bar drawn over the running app | overlay | `"defaultWindowType": "overlay"`, usually with `"transparent": true` | the last TV input (Live TV or HDMI) comes back; your launcher opens on Home only |

Home Chooser reads `defaultWindowType` from the installed appinfo to decide what an empty screen
shows on webOS 6 and later. On webOS 5 the screen goes back to Live TV in both cases. A
full-screen launcher is the root of navigation, so it must not close itself on Back.

## What Home Chooser does for a launcher

* **It makes your app the Home app on webOS 5 to 12.** The owner picks your app in Home Chooser;
  from then on the Home button opens it. The choice survives reboots: a small watcher re-applies it
  at boot, and puts LG Home back if your app is uninstalled (and re-applies the choice if it comes
  back). No restart is needed on any release. See [Per-release notes](#per-release-notes).
* **It tells the owner when your launcher is removed.** If the chosen launcher is uninstalled, the
  owner sees one alert saying that the Home button opens LG Home again, instead of a Home button
  that does nothing.
* **It gives your launcher an app list and a few other calls without root.** Your app has no root
  of its own. Home Chooser's service (`org.webosbrew.homechooser.service`) runs as root and offers
  `listApps`, `listRunning`, `getForegroundApp`, `getAppInfo`, `getAppIcon`, `closeApp` and `uninstallApp` to a launcher that declares one permission.
  Closing and uninstalling work only while your app is the selected Home, and an uninstall always
  asks the owner first.

What it needs: a TV with Homebrew Channel and a Home Chooser service running as root. On 5.x to
10.x, opening Home Chooser asks Homebrew Channel to elevate the service. On 11.x and 12.x the
current Homebrew Channel may refuse that request; see [Per-release notes](#per-release-notes).
Until elevation, only `getInfo` works, and it tells you so.

See [Stability](#stability) for the version policy.

## Quick start

### 1. Declare the app

A complete, minimal web launcher `appinfo.json`:

```json
{
  "id": "org.example.launcher",
  "version": "1.0.0",
  "vendor": "Example",
  "type": "web",
  "main": "index.html",
  "title": "Example Launcher",
  "icon": "icon.png",
  "appCategory": ["home"],
  "requiredPermissions": ["org.webosbrew.homechooser.applications"]
}
```

* `"appCategory": ["home"]` makes the app eligible: Home Chooser lists every installed app that
  has it. This is the whole contract for being chosen as Home. It works for `type` `web`, `native`
  and `qml`, and needs no variant per webOS release. webOS 12 ships no QML runtime, so a `qml` app
  does not run there.
* `"requiredPermissions": ["org.webosbrew.homechooser.applications"]` opts into the API below. Leave it out if
  you only want to be selectable. Merge both entries with your other categories and permissions.
* **A launcher that draws over the running app** (a dock, a bar) is an overlay launcher. Consider
  `"defaultWindowType": "overlay"` and, for a see-through window, `"transparent": true`. These
  are windowing advice, not part of Home Chooser's contract. Home Chooser reads
  `defaultWindowType` only to choose what an empty screen shows
  ([Full-screen or overlay](#full-screen-or-overlay)); it does not read `transparent`. See
  [The stock home](#the-stock-home) for what happens to LG's own home app.

The permission is read from the appinfo the app was installed with, so reinstall after any change
to `requiredPermissions`. Install Home Chooser, and open it once, before you install a launcher
that requests the permission: the group is made available by Home Chooser's access files, which it
applies after elevation. If the install is refused with `Not allowed permission is used`, open
Home Chooser and install again.

### 2. How the owner selects your launcher

The owner opens Home Chooser (it is an ordinary app), moves to your launcher's row and presses OK.
Home Chooser asks `Use <your title> as Home?`; the owner confirms and the Home button opens your
app at once.

### 3. Asking to become Home

Your app may ask the owner to select it, for example from a first-run screen. Launch Home Chooser
with the parameter `request`, set to your own app id, through
`luna://com.webos.applicationManager/launch`:

```json
{"id": "org.webosbrew.homechooser", "params": {"request": "org.example.launcher"}}
```

Home Chooser opens, loads the eligible launchers, and shows the same `Use <title> as Home?` dialog.
The owner decides. This is a request only:

* there is no reply that tells you the result; call `getInfo` later and read `isSelectedHome`;
* if your app is already the selected Home, Home Chooser shows an "already your Home" toast;
* the request is dropped without any message when the id is the stock home, unknown, not in the
  Home category or malformed, and also when Home Chooser still shows a setup problem (for example
  the service is not elevated yet) or is busy;
* on webOS 6 to 9, the dialog changes nothing if the Home key filter refuses the firmware
  (see [the live Home key filter](home-key-hook.md));
* the parameters are read at start (`PalmSystem.launchParams`) and on `webOSRelaunch` (dispatched on `document`; `window` is listened to as well), so the
  request works whether or not Home Chooser is already open.

When Home is pressed, launch parameters come from the platform and are passed through, not
guaranteed by this API. On webOS 6 to 9 the stock launch's parameters are forwarded with
`checkUpdateOnLaunch:false`, `autoInstallation:false` and `reason` (the stock reason, or `hotKey`).
A launch made by the [home gate](home-gate.md) (for example when the screen would otherwise be
empty) carries only the id. On webOS 10 and later the platform uses
`{launchType:"homeKey", activateType:...}`.

### 4. Check where you stand, then use the API

At start, call `getInfo`, then branch:

1. The call fails (platform error such as `ServiceUnknown`, or an unknown-method error): Home
   Chooser is missing or too old. Offer to install or open it.
2. `apiVersion` is not `1`: this guide does not describe that service.
3. `root` is false: the owner has not finished setup; ask them to open Home Chooser.
4. `permitted` is false: check the installed appinfo for the permission and reinstall after a change;
   an appinfo lookup failure can also make this false.
5. `isSelectedHome` is false and you want to be Home: send the request above.
6. Otherwise call the launcher methods. `closeApp` and `uninstallApp` require `isSelectedHome` to be true.

## Calling the service

Calls go to `luna://org.webosbrew.homechooser.service/<method>` with a JSON object. No method
subscribes, and every call is a single request with a single reply. The service takes the caller
from the application id that the system reports for the message; nothing in the payload can name
another caller, and a shell `luna-send` (no application id) is refused.

A web app can use the platform's bridge directly. This is the same call shape as Home Chooser's own
client (`app/src/platform/bridge.js`), with no dependencies:

```js
const SERVICE = 'luna://org.webosbrew.homechooser.service';
const TIMEOUT_MS = 70000;

// Resolves with the parsed reply whatever its returnValue; rejects when there is no bridge, the
// reply is not JSON, or nothing answers within TIMEOUT_MS.
function lunaCall(uri, payload) {
  return new Promise((resolve, reject) => {
    const Bridge = window.WebOSServiceBridge || window.PalmServiceBridge;
    if (!Bridge) { reject(new Error('no webOS service bridge')); return; }
    const bridge = new Bridge();
    const timer = setTimeout(() => { bridge.cancel(); reject(new Error('timeout')); }, TIMEOUT_MS);
    bridge.onservicecallback = (message) => {
      clearTimeout(timer);
      bridge.cancel();
      try { resolve(JSON.parse(message)); } catch (e) { reject(e); }
    };
    bridge.call(uri, JSON.stringify(payload || {}));
  });
}

// Resolves with the reply when returnValue is true; otherwise throws an Error whose `code` is the
// errorCode (a string from Home Chooser, or whatever the platform reports) and whose `reply` is
// the whole reply.
async function homeChooser(method, payload) {
  const reply = await lunaCall(`${SERVICE}/${method}`, payload);
  if (reply.returnValue !== true) {
    const error = new Error(reply.errorText || 'call failed');
    error.code = reply.errorCode;
    error.reply = reply;
    throw error;
  }
  return reply;
}

async function start() {
  let info;
  try {
    info = await homeChooser('getInfo');
  } catch (e) {
    return showInstallHomeChooser(e);        // absent, incompatible, or the hub denied delivery
  }
  if (info.apiVersion !== 1) return showUpdateHomeChooser(info);
  if (!info.root) return showOpenHomeChooser();
  if (!info.permitted) return showReinstallLauncher();
  const {apps} = await homeChooser('listApps');
  render(apps);
}

// Ask the owner to make this app the Home app (fire and forget; read getInfo().isSelectedHome later).
function requestHome() {
  return lunaCall('luna://com.webos.applicationManager/launch', {
    id: 'org.webosbrew.homechooser',
    params: {request: 'org.example.launcher'}
  });
}
```

[`api/client/homechooser.js`](../api/client/homechooser.js) is this code as one file to copy, one
function per method, generated from the API description. Its promises reject with an error whose
`source` tells a Home Chooser refusal (`errorCode` from the table below) from a platform reply and
from no reply at all; `requestHome()` sends the request launch. `homechooser-mock.js` beside it
answers the same calls with example data in a desktop browser.

`webOS.service.request` from webOSTV.js does the same through the same bridge; its failure callback
receives the same `returnValue:false` object. A native or QML launcher uses its own luna client
(libluna-service2, or the QML `LunaService` element) and the same URI and payloads. No native or QML
example is given here because the bridge example is sufficient to show the API shape.

Successful replies have `returnValue: true`. Every reply produced by a launcher method includes
`apiVersion`. Failures from Home Chooser look like this:

```json
{"returnValue": false, "apiVersion": 1, "errorCode": "notHome", "errorText": "Only the app selected as Home may call this method"}
```

Show your own plain failure message to the user and keep `errorText` in your logs. Allow 70 seconds
per call. An uninstall request can make two 20 second appinfo calls, an 8 second alert call and a
15 second screen launch, for about 63 seconds in the worst case.

## Methods

All methods are on the service's root category, and all were introduced in API version 1.

| Method | Needs root | Needs the permission | Only the selected Home | Reply carries `apiVersion` |
| --- | --- | --- | --- | --- |
| `getInfo` | no | no (reports it) | no (reports it) | yes |
| `listApps` | yes | yes | no | yes |
| `listRunning` | yes | yes | no | yes |
| `getForegroundApp` | yes | yes | no | yes |
| `getAppInfo` | yes | yes | no | yes |
| `getAppIcon` | yes | yes | no | yes |
| `closeApp` | yes | yes | yes | yes |
| `uninstallApp` | yes | yes | yes | yes |

Every method needs a caller with a valid application id: the id the platform reports for the message,
up to its first space, must be 1 to 128 characters of `A-Za-z0-9._-`.
When several checks fail, the first of these is reported: `forbidden`, `notRoot`, `notHome`,
`badRequest`, `noPermission`. "The permission" is `org.webosbrew.homechooser.applications` in your installed
`requiredPermissions`. "The selected Home" means your app id is the one Home Chooser has stored as
the owner's choice; this is not the same as what the Home key opens at this moment (see `isSelectedHome`).

### `getInfo {}`

Feature detection and status. Works for any app, before elevation and without the permission.

```json
{"returnValue": true, "apiVersion": 1, "version": "0.1.0", "root": true, "permission": "org.webosbrew.homechooser.applications", "permitted": true, "isSelectedHome": true}
```

* `apiVersion`: the version of this contract, independent of the package version. Check it before
  relying on this guide.
* `version`: the Home Chooser package version.
* `root`: false means the owner has not finished setup (open Home Chooser).
* `permission`: the permission name to list in `requiredPermissions`.
* `permitted`: your installed appinfo lists the permission. It is also false when the service
  could not read your appinfo, which can happen while `root` is false; look at `root` first.
* `isSelectedHome`: your app id equals the choice Home Chooser has stored. While the chosen app is missing,
  the watcher puts LG Home back but keeps the stored choice, so this can be true while the Home key
  opens LG Home.

Errors: `forbidden` (no valid application id).

There is no capability list. To detect the service, use the platform's own failure
(the service is unknown, or the method is unknown) and `apiVersion`. A denial by itself does not
prove Home Chooser is absent: it can also mean the permission is missing from your installed app.

### `listApps {}`

Lists the installed apps.

```json
{"returnValue": true, "apiVersion": 1, "apps": [{"id": "org.example.app", "title": "Example", "icon": "icon.png", "folderPath": "/media/developer/apps/usr/palm/applications/org.example.app", "removable": true}, {"id": "org.example.launcher", "title": "Example Launcher", "icon": "icon.png", "appCategory": ["home"], "removable": true}]}
```

Each app carries `id`, `title`, `icon`, `largeIcon`, `folderPath`, `version`, `vendor`, `type`,
`removable`, `visible` and `appCategory`, when the platform has them; absent fields are omitted.
`appCategory` is the category array as sam reports it (for example `["home"]`), so a launcher can
find the other launchers. Values are passed through as the platform gives them. Within an `apiVersion` this field list only grows. `icon` and `largeIcon` are appinfo
paths, usually relative to `folderPath`. Use `getAppIcon` to read them from a jailed launcher.

Errors: `forbidden`, `notRoot`, `noPermission`, `lookupFailed`, `timeout`, `refused`, `badReply`.

### `listRunning {}`

Lists running apps.

```json
{"returnValue": true, "apiVersion": 1, "running": [{"id": "org.example.app", "instanceId": "instance", "processId": 123, "webProcessId": 456}]}
```

Each item carries `id`, `instanceId`, `processId` and `webProcessId`, when present. The service
renames the platform's lowercase PID keys and includes PID values only when they are non-negative
integers, given as numbers or numeric strings; they are normalized to JSON numbers.
Errors: as `listApps`; `badReply` also means the platform did not return a `running` array.

### `getForegroundApp {}`

One lookup of the platform's current foreground app. This call does not subscribe to changes.

```json
{"returnValue": true, "apiVersion": 1, "appId": "org.example.app", "windowId": "window", "processId": 123}
```

`appId` is an empty string if the platform reports no foreground app. `windowId` is included when
the platform gives it. `processId` is included only when the platform gives a non-negative integer
PID, as a number or a numeric string; it is normalized to a JSON number. A launcher overlay may not become the platform's foreground app: the
app beneath it (on webOS 9 the stock home, on 11.x Live TV) can remain reported in front of a Home
Chooser overlay. Errors: `forbidden`,
`notRoot`, `noPermission`, `lookupFailed`, `timeout`, `refused`, `badReply`.

### `getAppInfo {id}`

Fetches one installed app without refreshing the full list. `app` has exactly the same documented
fields and types as one `listApps` item, including `appCategory` when the platform supplies it.

```json
{"returnValue": true, "apiVersion": 1, "app": {"id": "org.example.app", "title": "Example", "icon": "icon.png", "appCategory": ["home"]}}
```

Errors: `forbidden`, `notRoot`, `badRequest`, `noPermission`, `lookupFailed`, `notFound` for an
absent id, `refused` for another SAM refusal, `badReply`, `timeout`. When SAM gives its combined
missing-id/unsupported-type error, Home Chooser checks the installed app list before choosing
`notFound` or `refused`.

### `getAppIcon {id, size?}`

Reads the icon referenced in that app's appinfo inside that app's folder, for a jailed launcher.
`size` is `"icon"` (the default) or `"largeIcon"`. Paths that leave the app folder, including
symlink escapes, are rejected. There is no fallback to the other icon field.

```json
{"returnValue": true, "apiVersion": 1, "mimeType": "image/png", "data": "iVBORw0KGgo=", "byteLength": 8}
```

`data` is base64; `byteLength` counts the decoded bytes. An icon may be at most 262144 bytes, so
the base64 payload is at most 349528 characters. Luna replies carry the base64 as JSON; the cap
keeps that message near 350 KiB and matches the existing internal icon reader. This is Home
Chooser's own conservative limit, not a platform one. On 11.x, LG Home's appinfo advertises
`icon.png` but has no file at that path; `getAppIcon` reports `noIcon` for it. Show a placeholder
for this case.
Errors: as `getAppInfo`, plus `noIcon` when no readable
icon is referenced and `iconTooLarge` when the icon exceeds the byte limit. Any other `size`, including
`null`, is `badRequest`.

### `closeApp {id, force?}`

Closes an app. Only the selected Home may call it. `id` is an application id; `force: true` (a JSON
boolean) asks the platform for a forced close. Reply
`{"returnValue": true, "apiVersion": 1, "id": "<id>"}`.

There is no restriction on the target: the Home app may close any app, including Home Chooser,
Homebrew Channel, LG Home and itself. Errors: `forbidden`, `notRoot`, `notHome`, `badRequest`
(missing or malformed `id`), `noPermission`, `lookupFailed`, `refused`, `badReply`, `timeout`.

### `uninstallApp {id}`

Asks the owner to uninstall an app. Only the selected Home may call it.

Home Chooser refuses Home Chooser itself, Homebrew Channel and the calling launcher with
`protected`. It refuses an app marked `removable:false`, or one outside the
developer and store application folders with `notRemovable`
(`/media/developer/apps/usr/palm/applications/`, `/media/cryptofs/apps/usr/palm/applications/`). An
unknown id is `notFound`; Home Chooser resolves SAM's combined missing-id/unsupported-type error
against the installed app list. Other lookup refusals are `refused`. An optional `force` field is
ignored by `uninstallApp`.

**The owner always confirms.** Home Chooser raises an alert, `Uninstall <title>?`, with Uninstall
and Cancel; if the alert is refused it opens its own confirmation screen with the same question.
Your launcher cannot confirm for the owner and cannot call the confirmation method.

```json
{"returnValue": true, "apiVersion": 1, "pending": true, "id": "org.example.app", "confirmation": "alert"}
```

`confirmation` is `alert` or `screen`. **A pending uninstall is not a completed removal.** There is no
second reply: the owner may cancel, ignore it or confirm, and nothing tells you which. The LG alert
stays until the owner answers or dismisses it; Home Chooser's own confirmation screen expires after
120 seconds. Call `listApps` again to see whether the app is gone. If neither the
alert nor the screen could be shown, the call fails with `confirmFailed`.

Errors: `forbidden`, `notRoot`, `notHome`, `badRequest`, `noPermission`, `lookupFailed`, `notFound`,
`protected`, `notRemovable`, `refused`, `badReply`, `timeout`, `confirmFailed`, `internal`
(no confirmation token could be made). How the confirmation
and the removal work is in [the service reference](service.md#launcher-api-confirmation-and-removal).

## Error handling

Failures come from two places. The platform's luna bus answers when the service is unknown, a
method is unknown, or the bus denies delivery: the reply has `returnValue: false`, the platform's
own `errorText` (for example `Denied method call`) and often a numeric `errorCode`. Home Chooser
answers with a string `errorCode` from the table.

| `errorCode` | Cause | What to do |
| --- | --- | --- |
| platform error (service or method unknown, `Denied method call`) | Home Chooser is not installed or too old, or the bus denied delivery because the permission was not installed with your app | Call `getInfo`; offer to install or open Home Chooser; ask for a reinstall if `permitted` was false |
| `forbidden` | The caller has no valid application id | Call from the app. A shell `luna-send` needs `-a <app id>` |
| `notRoot` | The service is not elevated | Ask the owner to open Home Chooser |
| `noPermission` | The installed appinfo lacks `org.webosbrew.homechooser.applications` | Fix `requiredPermissions`, reinstall |
| `lookupFailed` | The service could not read the caller's installed appinfo, from SAM or its file fallback (a timeout or an unreadable reply in that lookup is `timeout` or `badReply`) | Retry later; log `errorText` |
| `notHome` | Your app is not the selected Home | Send the `request` launch, wait for the owner |
| `badRequest` | `id` is missing or not an app id, or `getAppIcon.size` is invalid | A bug in the launcher |
| `notFound` | A target lookup reports absence, or SAM's ambiguous refusal is resolved against the installed app list and the id is absent | Refresh the list |
| `noIcon` | `getAppIcon`: no readable icon from the requested appinfo field inside that app's folder | Show a placeholder |
| `iconTooLarge` | `getAppIcon`: the icon exceeds 262144 bytes | Show a placeholder |
| `protected` | `uninstallApp`: target is Home Chooser, Homebrew Channel or the caller | Do not offer uninstall for this app |
| `notRemovable` | `uninstallApp`: target is marked `removable:false` or outside the user app folders (system apps are) | Do not offer uninstall for this app |
| `confirmFailed` | `uninstallApp`: the owner could not be asked | Try again later; tell the user |
| `timeout` | A bounded application manager call reached its time limit | Retry once; log `errorText` |
| `refused` | The application manager explicitly refused an operation or target lookup for a reason other than absence | Log `errorText`; do not loop |
| `badReply` | The application manager did not provide usable JSON, or its successful list reply lacked the expected array | Log it; report it |
| `internal` | The service could not make a confirmation token | Retry once |

Treat an unknown `errorCode` as a failure and log it.

## Icons

Use `getAppIcon`. `listApps` gives each app's `icon` and `folderPath` as the app declared them,
but loading `folderPath` + `icon` as a `file://` URL needs your app to be able to read that file,
and a [jailed](#glossary) launcher cannot read other apps' folders. `getAppIcon` reads the file as root, inside
that app's folder only, for every app type and release, and returns base64 for a `data:` URL:

```js
const {mimeType, data} = await homeChooser('getAppIcon', {id});
img.src = `data:${mimeType};base64,${data}`;
```

Cache the result (by `id` and `version`): every call reads the file again. Ask for one icon at a
time rather than the whole list at once. `noIcon` and `iconTooLarge` are normal answers; show a
placeholder.

## Platform calls you'll need

These are webOS platform behaviour, not part of Home Chooser's API v1: Home Chooser neither
provides nor freezes them. They are here because every launcher needs them.

* **Launch an app.** `luna://com.webos.applicationManager/launch` with `{"id": "<app id>"}`, and
  `"params": {...}` for the app's own launch parameters. The reply has `returnValue: true` when the
  platform accepted the launch. The [request launch](#3-asking-to-become-home) is this call with
  Home Chooser's id.
* **Back.** Back arrives as a `keydown` with `keyCode` 461 when `appinfo.json` sets
  `"disableBackHistoryAPI": true`; otherwise the web runtime uses it for the page's history. A
  Home app is the root of navigation: on Back, move the focus to a start position or do nothing;
  do not close the app. The arrow keys are 37 to 40 and OK is 13.
* **`webOSRelaunch`.** When your launcher is already running and is launched again (the Home
  button, a request), the web runtime does not reload the page: it dispatches `webOSRelaunch` on
  `document`, and `PalmSystem.launchParams` holds the new parameters. Refresh the app list there,
  and on `visibilitychange` when the page becomes visible again.
* **Your own id.** `PalmSystem.identifier`, up to its first space, is the app id of a web app.

## Per-release notes

*Not needed to write a launcher.* This section explains how Home Chooser makes the Home button
follow the choice on each release, for when you want to know why something behaves as it does.

Releases are named by the major version in `/etc/os-release` (`VERSION_ID`), not by LG's marketing
year. Your launcher declares the same appinfo on all of them. The API and the selection request
behave the same everywhere; what differs is how the Home button is made to follow the choice, what
the owner sees, and how the permission reaches you.

### 5.x

The stock Home key handler launches the `home` entry of the `general/defaultApps` settings record
directly, so Home Chooser only writes that record. No patch, no restart. The stock launcher is
`com.webos.app.fullhome` (an overlay window), and LG Home is not running, so Home Chooser does not
touch the stock home's memory here. The permission arrives through the hub group in the same way as
on 6.x to 10.x (below).

### 6.x to 9.x

The stock Home key handler launches LG Home by its fixed id, whatever the record says. Home Chooser
generates a copy of this TV's Home key filter with your id as the launch target and selects it live
through the system configuration. For a launcher this means:

* **No restart.** The Home button opens your launcher as soon as the owner confirms, and at every
  boot after the system is ready. Nothing restarts the compositor, so setup never closes your
  window.
* **Guarded edit.** Only the launch target in the stock Home key function is changed. If this TV's
  firmware has a function shape Home Chooser does not recognise, it refuses, the Home button keeps
  opening LG Home, and the owner cannot select a custom Home. See
  [the live Home key filter](home-key-hook.md).
* **When the system would show LG Home** (for example when the last app closes), the
  [home gate](home-gate.md) runs instead: it opens your launcher if it is full-screen, or the last
  TV input if your launcher is an overlay.
* **When the owner restores LG Home or your launcher is removed**, the stock key filter is
  restored at once and LG Home opens on Home. The owner is told about a removed launcher as
  described above, and the choice is re-applied if you are reinstalled.

### 10.x and later

The compositor takes the home app from the system profile, so Home Chooser also writes a profile
override (`/var/webos-profile/override/org.webosbrew.homechooser.json`) with your id, in addition
to the settings record. There is no patch and no restart. When your launcher is removed or the
owner restores LG Home, the override is removed and the record's `home` is cleared, which leaves
the profile's own stock launcher.

### Permission on 5.x to 10.x and on 11.x and 12.x

On 5.x to 10.x a developer app's declared permission becomes one of its client permissions, and
Home Chooser's access file maps the group `org.webosbrew.homechooser.applications` to the eight launcher methods.

On 11.x and 12.x a developer app keeps only the groups `ares.webos.cli` and `public` in its own
client-permissions file, whatever its `requiredPermissions` says, so the permission is not listed
there. Access comes through Home Chooser's API file instead, which on these releases also grants
the eight methods to `ares.webos.cli`. The service still demands the declared permission in the
installed appinfo, so nothing changes for your appinfo: declare the permission, and `getInfo`
reports `permitted`. Also on 11.x and 12.x, the current Homebrew Channel refuses to elevate
Home Chooser's service from an ordinary app (`Denied method call`), so `root` stays false until the
owner elevates it by other means or a newer Homebrew Channel allows it. The layout of the access
files is in [the service reference](service.md#luna-access-files).

### The stock home

LG's stock home (`com.webos.app.home`) normally stays resident (70 to 105 MB). While a third-party
home is selected, Home Chooser keeps it from being preloaded, once per boot. This is not permanent:
nothing is written, and a reboot starts with the stock policy until Home Chooser sets it again. 5.x
is left alone.

On 6.x to 9.x the [home gate](home-gate.md) takes the stock home's place while your launcher is
chosen, and the running stock home is closed once. A launch of `com.webos.app.home` then runs the
gate, so `listRunning` can briefly list that id while the gate decides.

On 10.x Home Chooser does not close LG Home: the system relaunches the stock home at once as the
last-input app, so a close frees nothing. It can stay resident until the next reboot, and
`listRunning` will list `com.webos.app.home` (a `closeApp` of it by your launcher will not stick
either). After a reboot it is gone, because its preload is off from boot.

On 11.x and 12.x the watcher closes it once the chosen launcher is in front. For an overlay
launcher it waits for a launch with a nonstock full-screen app beneath the overlay; closing LG
Home beneath an overlay can dismiss the overlay and trigger an idle relaunch. Home Chooser itself
never closes a stock home while you select a launcher. Details are in
[The stock home is not preloaded](settings-app.md#the-stock-home-is-not-preloaded).

## Testing your launcher

**In a desktop browser.** Load
[`api/client/homechooser-mock.js`](../api/client/homechooser-mock.js) after `homechooser.js`. Where
there is no webOS service bridge it answers every method from the API description's examples, with
the caller checks in the service's order; on a TV it does nothing. `?hcmock=before-elevation`,
`not-permitted`, `not-home` or `absent` in the page URL selects another start state. See
[`api/README.md`](../api/README.md#client-and-mock).

**By hand, as root on the device.** `luna-send -a <app id>` makes the call as an installed app with
its effective client permissions, so install your launcher first; an uninstalled or made-up id
tests nothing.

```sh
luna-send -a org.example.launcher -n 1 luna://org.webosbrew.homechooser.service/getInfo '{}'
luna-send -a org.example.launcher -n 1 luna://org.webosbrew.homechooser.service/listApps '{}'
luna-send -a org.example.launcher -n 1 luna://org.webosbrew.homechooser.service/listRunning '{}'
```

Use a disposable app on a test device for `uninstallApp`.

**With the fixtures.** [`e2e/fixtures`](../e2e/fixtures/README.md) holds six minimal launchers
(`native`, `qml` and `web`, each `normal` and `overlay`). The web ones can declare a permission when
built, through the environment variable `HC_FIXTURE_PERMISSION` (the native and qml fixtures cannot).
Building only the web fixtures needs python3 and `ares-package`, no webOS SDK:

```sh
HC_FIXTURE_PERMISSION=org.webosbrew.homechooser.applications sh e2e/fixtures/web/build.sh build/fixtures
```

Install `build/fixtures/org.webosbrew.hcfixture.web.normal_1.0.0_all.ipk` and use its id with `-a`.
Without the variable you get a launcher with no permission, which is the control for `noPermission`.

**On your own TV.** Use a rooted webOS TV with Homebrew Channel installed and an ares device
configured for root access. Install Home Chooser first, or pass its ipk with `--hc-ipk`. The
ares-only compatibility kit installs a permission-declaring web fixture and a disposable control
app, checks the v1 API before and after choosing the fixture, then restores LG Home and removes
the test apps. It does not reboot the TV. It requires LG Home to be the
starting choice so cleanup can restore it.

```sh
python3 -I e2e/homechooser_e2e.py --device my-root-tv --api-check
python3 -I e2e/homechooser_e2e.py --device my-root-tv --api-check \
  --launcher org.example.launcher --launcher-ipk path/to/launcher.ipk
```

Your launcher ipk must declare `appCategory:["home"]` and
`requiredPermissions:["org.webosbrew.homechooser.applications"]`. If Home Chooser is absent, add
`--hc-ipk path/to/homechooser.ipk`. The web fixture is built with `ares-package`; your own ipk
is used as supplied. The kit will not replace an already installed launcher or disturb a
nonstock Home choice. Results are in `e2e/results/<device>-<timestamp>/`.

The project's own release test, which also covers the Home key, reboots and uninstalls, is
described in [`e2e/README.md`](../e2e/README.md). Check `df -h /media/developer` before installing:
a full partition makes an installation fail for an unrelated-looking reason.

## Glossary

* **SAM**: the System and Application Manager, `com.webos.applicationManager`, the webOS service
  that launches, lists and closes apps. Its log lines carry `sam`.
* **Compositor**: the webOS window manager (surface manager). It draws the windows and dispatches
  keys, the Home key among them.
* **Stock home**: LG's own home app, LG Home (`com.webos.app.home`; on webOS 5 the visible one is
  `com.webos.app.fullhome`).
* **Preload**: webOS starting an app in the background at boot so it opens fast. LG Home is
  preloaded; on webOS 6 and later Home Chooser turns that off while another launcher is chosen.
* **Watcher**: Home Chooser's background program. It re-applies the choice at boot, puts LG Home
  back when the chosen launcher is uninstalled, and applies the choice again when it returns.
* **Home gate**: on webOS 6 to 9, Home Chooser's stand-in for LG Home's program. When the system
  would show LG Home, it opens your launcher or the last TV input instead.
* **Elevation, root**: Homebrew Channel restarting Home Chooser's service as root. Until then
  `getInfo` reports `root: false`.
* **Jailed**: run by the platform in a sandbox (a jail) that cannot read other apps' files. Whether
  an app is jailed depends on its type and the release; `getAppIcon` works either way.

## Stability

**API version 1: frozen.** The eight launcher methods, permission,
`appCategory:["home"]` and `requiredPermissions` appinfo declarations, and the `request` launch
parameter are the v1 contract. Reply fields and methods may be added within v1. Removing a field
or method, or changing a documented meaning, raises `apiVersion`. Check `apiVersion` at start and
ignore unknown fields and error codes.

A machine-readable description of v1 is [`api/launcher-v1.openrpc.json`](../api/launcher-v1.openrpc.json).

Settings-only and confirmation methods, files, status output and log ids are internal and are not
frozen by this contract.
