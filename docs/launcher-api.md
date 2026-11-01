# Writing a launcher for Home Chooser (API level 2)

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
4. **List what to show** with `listApps`, `listLaunchPoints` (LG's order, bookmarks) or `listInputs`,
   subscribe to keep it current, and read the icons in batches with `getIcons`. Cache both on your
   side: Home Chooser keeps nothing ([Subscriptions](#subscriptions), [Icons](#icons)).
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
  `listApps`, `listLaunchPoints`, `listInputs`, `listRunning`, `getForegroundApp`, `getAppInfo`,
  `getAppIcon`, `getIcons`, `closeApp` and `uninstallApp` to a launcher that declares one permission.
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
* `"requiredPermissions": ["org.webosbrew.homechooser.applications"]` opts into the API below: the
  service reads it from your installed appinfo and answers `noPermission` without it. If your appinfo
  also declares `requiredACG`, include `application.query` and `application.launcher` there
  ([Permission](#permission-on-5x-to-10x-and-on-11x-and-12x)). Leave it out if
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
2. `apiVersion` is below `1` or missing: this guide does not describe that service. Higher is fine: levels only add (see [Stability](#stability)). Check `apiVersion >= N` for the level a feature needs (`listLaunchPoints`, `listInputs`, `getIcons` and `subscribe` need 2), never `== 1`.
3. `root` is false: the owner has not finished setup; ask them to open Home Chooser.
4. `permitted` is false: check the installed appinfo for the permission and reinstall after a change;
   an appinfo lookup failure can also make this false.
5. `isSelectedHome` is false and you want to be Home: send the request above.
6. Otherwise call the launcher methods. `closeApp` and `uninstallApp` require `isSelectedHome` to be true.

## Calling the service

Calls go to `luna://org.webosbrew.homechooser.service/<method>` with a JSON object. Every call is a
single request with a single reply, except the methods that take `"subscribe": true`
([Subscriptions](#subscriptions)). The service takes the caller
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
  if (!(info.apiVersion >= 1)) return showUpdateHomeChooser(info);
  const hasLevel2 = info.apiVersion >= 2;   // listLaunchPoints, listInputs, getIcons, subscribe
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
{"returnValue": false, "apiVersion": 2, "errorCode": "notHome", "errorText": "Only the app selected as Home may call this method"}
```

Show your own plain failure message to the user and keep `errorText` in your logs. Allow 70 seconds
per call. An uninstall request can make two 20 second appinfo calls, an 8 second alert call and a
15 second screen launch, for about 63 seconds in the worst case.

## Subscriptions

`listApps`, `listLaunchPoints`, `listInputs`, `listRunning` and `getForegroundApp` take
`"subscribe": true` (a JSON boolean; any other value is an ordinary single reply). Then:

* the first reply is a fresh read of the whole state, with `"subscribed": true`;
* after every change the platform reports, the same reply comes again: the whole list (or, for
  `getForegroundApp`, the whole `{appId, windowId?, processId?}`), with `"subscribed": true`. Every
  reply is a full state, never a delta, so replace what you had. A reply may repeat the previous one;
* end it with the bus's own cancel (the bridge's `cancel()`, `LSCallCancel`, or by closing the app).
  Home Chooser stops reading the platform when a method has no subscriber left;
* a method takes at most 16 subscribers; one more is refused with `tooManySubscriptions`. Keep one
  subscription per method for the life of your app;
* a failure reply (`returnValue: false`) ends the subscription. If Home Chooser loses the platform's
  feed it restarts it by itself, with a growing delay, and sends the whole state again when it is back.

These are the methods whose platform call can itself be subscribed to: SAM's `listApps`,
`listLaunchPoints`, `running` and `getForegroundAppInfo` declare `subscribe` in their schemas on
every release whose firmware was read (5.6.1 to 11.2.0), and LG's own apps subscribe to them and to
the input manager's `getAllInputStatus`. `getAppInfo` does not take `subscribe`, because SAM's `getAppInfo` has no
subscription: to keep one app current, subscribe to `listApps` and pick it from the list.
`getAppIcon` and `getIcons` read files, which nothing announces a change of: fetch an icon again
when a subscribed list shows a new `version` (an app) or `icon` path (a launch point or an input).

The client has it as `HomeChooser.subscribe(method, params, onReply, onError)`, which returns
`{cancel}`:

```js
const sub = HomeChooser.subscribe('listInputs', {}, (reply) => render(reply.inputs),
  (error) => console.log('inputs ended', error.source, error.errorCode));
// later: sub.cancel();
```

**Cache on your side.** Home Chooser keeps no copy of any list or icon: every reply is read from the
platform when it is sent. Keep the latest list your subscription gave you and draw from it; keep icons
in memory keyed by what you asked for (`id` and `version` for an app; `launchPointId` and `icon` for
a launch point; `inputId` and `icon` for an input), and ask `getIcons` only for keys you have not
seen. When a subscription reply arrives, compare the keys: fetch icons only for new or changed
entries and drop the ones that are gone. Without a subscription, refresh on `webOSRelaunch` and when
the page becomes visible again.

## Methods

All methods are on the service's root category. `listLaunchPoints`, `listInputs`, `getIcons`, the
`subscribe` parameter and the `iconColor` and `bgColor` fields are level 2 (`apiVersion >= 2`;
[Stability](#stability)); the rest is level 1. Check `apiVersion` before using a level 2 addition: a
level 1 service answers an unknown method with the platform's error, and omits the new fields.

| Method | Needs root | Needs the permission | Only the selected Home | Reply carries `apiVersion` |
| --- | --- | --- | --- | --- |
| `getInfo` | no | no (reports it) | no (reports it) | yes |
| `listApps` | yes | yes | no | yes |
| `listRunning` | yes | yes | no | yes |
| `getForegroundApp` | yes | yes | no | yes |
| `getAppInfo` | yes | yes | no | yes |
| `getAppIcon` | yes | yes | no | yes |
| `listLaunchPoints` | yes | yes | no | yes |
| `listInputs` | yes | yes | no | yes |
| `getIcons` | yes | yes | no | yes |
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
{"returnValue": true, "apiVersion": 2, "version": "0.1.0", "root": true, "permission": "org.webosbrew.homechooser.applications", "permitted": true, "isSelectedHome": true}
```

* `apiVersion`: the feature level of this contract, independent of the package version; it only
  grows. Check `apiVersion >= N` before relying on a level N feature, never `== 1`.
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

### `listApps {subscribe?}`

Lists the installed apps. Level 1; `subscribe`, `iconColor` and `bgColor` are level 2.

```json
{"returnValue": true, "apiVersion": 2, "apps": [{"id": "org.example.app", "title": "Example", "icon": "icon.png", "folderPath": "/media/developer/apps/usr/palm/applications/org.example.app", "removable": true}, {"id": "org.example.launcher", "title": "Example Launcher", "icon": "icon.png", "appCategory": ["home"], "removable": true}]}
```

Each app carries `id`, `title`, `icon`, `largeIcon`, `folderPath`, `version`, `vendor`, `type`,
`removable`, `visible`, `appCategory`, `iconColor` and `bgColor`, when the platform has them; absent
fields are omitted. `appCategory` is the category array as sam reports it (for example `["home"]`),
so a launcher can find the other launchers. `iconColor` and `bgColor` are the appinfo's colours (for
example `"#6d757f"`), the only colour fields SAM's app description has; on webOS 5.6.1 about half of
the apps give `iconColor` and one in ten `bgColor`. Values are passed through as the platform gives
them. Within an `apiVersion` this field list only grows. `icon` and `largeIcon` are appinfo paths,
usually relative to `folderPath`. Use `getIcons` (or `getAppIcon`) to read them from a jailed
launcher. `listApps` lists every installed app, the invisible ones too (`visible: false`: input apps,
services with a window, LG's helpers); for what LG Home shows, and in its order, use
`listLaunchPoints`.

`{"subscribe": true}` keeps the call open: the first reply has `"subscribed": true`, and the whole
list comes again after every change of the installed apps ([Subscriptions](#subscriptions)).

Errors: `forbidden`, `notRoot`, `noPermission`, `lookupFailed`, `timeout`, `refused`, `badReply`,
`tooManySubscriptions`.

### `listRunning {subscribe?}`

Lists running apps. Level 1; `subscribe` is level 2.

```json
{"returnValue": true, "apiVersion": 2, "running": [{"id": "org.example.app", "instanceId": "instance", "processId": 123, "webProcessId": 456}]}
```

Each item carries `id`, `instanceId`, `processId` and `webProcessId`, when present. The service
renames the platform's lowercase PID keys and includes PID values only when they are non-negative
integers, given as numbers or numeric strings; they are normalized to JSON numbers. The list is in
the platform's order, which is not the order of use.

`{"subscribe": true}` keeps the call open: the whole list comes again after every change of the
running apps that SAM reports (an app started or closed) ([Subscriptions](#subscriptions)).

Errors: `forbidden`, `notRoot`, `noPermission`, `lookupFailed`, `timeout`, `refused`, `badReply`
(also when the platform did not return a `running` array), `tooManySubscriptions`.

### `getForegroundApp {subscribe?}`

The platform's current foreground app (SAM's `getForegroundAppInfo`). Level 1; `subscribe` is
level 2.

```json
{"returnValue": true, "apiVersion": 2, "appId": "org.example.app", "windowId": "window", "processId": 123}
```

`appId` is an empty string if the platform reports no foreground app. `windowId` is included when
the platform gives it. `processId` is included only when the platform gives a non-negative integer
PID, as a number or a numeric string; it is normalized to a JSON number.

`{"subscribe": true}` keeps the call open: the first reply is the foreground app now, and the whole
reply, `{appId, windowId?, processId?}` with `"subscribed": true`, comes again after every change of
the foreground app that SAM reports ([Subscriptions](#subscriptions)). That is what a launcher needs
to keep its own list of recently used apps ([Patterns](#patterns)).

**Overlays.** A launcher overlay may not become the platform's foreground app: while it is up, SAM
can go on reporting the app beneath it (on webOS 9 the stock home, on 11.x Live TV) or an empty
`appId`. A launcher that tracks the foreground app therefore ignores its own id and `""`.

Errors: `forbidden`, `notRoot`, `noPermission`, `lookupFailed`, `timeout`, `refused`, `badReply`,
`tooManySubscriptions`.

### `getAppInfo {id}`

Fetches one installed app without refreshing the full list. `app` has exactly the same documented
fields and types as one `listApps` item, including `appCategory` when the platform supplies it.

```json
{"returnValue": true, "apiVersion": 2, "app": {"id": "org.example.app", "title": "Example", "icon": "icon.png", "appCategory": ["home"]}}
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
{"returnValue": true, "apiVersion": 2, "mimeType": "image/png", "data": "iVBORw0KGgo=", "byteLength": 8}
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

### `listLaunchPoints {subscribe?}`

*Level 2.* Lists SAM's launch points: what LG Home shows as its app row, in SAM's order, browser bookmarks
pinned to Home included. This is the list LG Home reads (`applicationManager/listLaunchPoints`), not
the installed apps.

```json
{"returnValue": true, "apiVersion": 2, "launchPoints": [{"launchPointId": "netflix_default", "id": "netflix", "title": "Netflix", "lptype": "default", "icon": "/mnt/otncabi/usr/palm/applications/netflix/icon.png", "largeIcon": "/mnt/otncabi/usr/palm/applications/netflix/largeIcon.png", "iconColor": "#ffffff", "bgColor": "#ffffff", "bgImage": "/mnt/otncabi/usr/palm/applications/netflix/default_preview.png", "params": {}, "removable": true}]}
```

Each launch point carries `launchPointId`, `id` (the app it launches), `title`, `lptype`, `icon`,
`largeIcon`, `iconColor`, `bgColor`, `bgImage`, `params` and `removable`, when the platform has
them. SAM gives an empty string for an absent value; Home Chooser omits those, like absent fields.
`lptype` is `default` for an app's own launch point (`<app id>_default`) and `bookmark` for one an
app added, such as a browser bookmark; `params` is what the launch point passes to its app. Icon
paths are absolute, as SAM gives them; read them with `getIcons {launchPointId}`. Within an
`apiVersion` this field list only grows.

To launch a launch point, do what LG Home does:
`luna://com.webos.applicationManager/launch` with `{"id": <id>, "launchPointId": <launchPointId>,
"params": <params>}`. SAM's launch schema takes `launchPointId` and `params` on webOS 5 to 11; for an
app's own launch point `{"id": <id>}` alone is the same.

`{"subscribe": true}` keeps the call open and sends the whole list again after every change: an app
installed or removed, a bookmark added, the owner reordering LG Home on 5.x to 9.x
([Subscriptions](#subscriptions)). On webOS 10 and later LG Home keeps its own visible order in
another service as well (`com.webos.service.homelaunchpoints`); whether SAM's order follows the
owner's order there has not been verified.

Errors: `forbidden`, `notRoot`, `noPermission`, `lookupFailed`, `timeout`, `refused`, `badReply`,
`tooManySubscriptions`.

### `listInputs {subscribe?}`

*Level 2.* Lists the TV's external inputs, as the input manager (`com.webos.service.eim`,
`getAllInputStatus`) reports them, in its order. LG's own input picker and LG Home read the same
call.

```json
{"returnValue": true, "apiVersion": 2, "inputs": [{"id": "com.webos.app.hdmi1", "inputId": "HDMI_1", "label": "HDMI 1", "port": 1, "connected": true, "activate": true, "signal": true, "params": {}, "icon": "/usr/palm/plugins/inputapps/assets/small/HDMI_1.png"}]}
```

Each input carries `id`, the input's app (`com.webos.app.hdmi1`): launch it with
`applicationManager/launch {"id": <id>, "params": <params>}` to switch to that input. `inputId` is the input manager's
own id (`HDMI_1`), `label` its name, and `port`, `connected` (a device is plugged in) and `activate`
(the input in use) are passed through when the platform gives them. `icon` is the input manager's icon
path; read it with `getIcons {inputId}`.

`params` is always there: the launch parameters LG's input picker sends with that input. It is `{}` for
a plain input, and `{"id": "uniqueId", "value": "<uniqueId>"}` for a device the input manager gives a
`uniqueId` (the value is that number as a string). `signal` is the input manager's `hdmiSignalExist`
(a signal is present); it is there only where the input manager reports it (webOS 7 and later; 5.6.1
and 6.5.2 do not) and is left out otherwise, so a missing `signal` means unknown, not false. The
picker also lists the CEC devices behind an HDMI input (`subList`) as inputs of their own, each
launched with its own `uniqueId`; `listInputs` leaves them out and gives the HDMI input itself. Live TV is not an input here: it is the app
`com.webos.app.livetv`, which LG's input picker adds to its list itself.

`{"subscribe": true}` sends the whole list again after every change (a device plugged in, an input
switched).

Errors: `forbidden`, `notRoot`, `noPermission`, `lookupFailed`, `timeout`, `refused`, `badReply`,
`tooManySubscriptions`.

### `getIcons {items}`

*Level 2.* Reads up to 64 icons in one call. `items` is an array of 1 to 64 objects, each one of:

* `{"id": <app id>, "size"?: "icon" | "largeIcon"}`: the app's appinfo icon, exactly as `getAppIcon`;
* `{"launchPointId": <id>, "size"?: "icon" | "largeIcon"}`: a launch point's icon;
* `{"inputId": <id>}`: an input's icon.

```json
{"returnValue": true, "apiVersion": 2, "icons": [{"mimeType": "image/png", "dataUrl": "data:image/png;base64,iVBORw0KGgo=", "byteLength": 8}, {"errorCode": "notFound", "errorText": "No such app"}]}
```

`icons` has one entry per item, in request order: `{mimeType, dataUrl, byteLength}`, where
`dataUrl` goes straight into an `img` `src`, or `{errorCode, errorText}` for that item alone:
`notFound` (no such app, launch point or input), `noIcon` (no readable image of that size in the
place Home Chooser reads it from), `iconTooLarge` (the file is over 262144 bytes) or `overBudget`
(one reply carries at most 1048576 icon bytes and this icon did not fit: ask for it again in another
call).

Where Home Chooser reads: an app icon only inside that app's `folderPath`; a launch point icon only
inside its own app's folder under `/usr/palm/applications`, `/mnt/otncabi/usr/palm/applications`,
`/media/cryptofs/apps/usr/palm/applications` or `/media/developer/apps/usr/palm/applications`; an
input icon only under `/usr/palm/plugins/inputapps/assets`. Symlinks and `..` are resolved first, and
the file must have an image extension. Bookmark icons lie elsewhere, in a directory not yet verified,
so a bookmark's icon is `noIcon` for now: draw the bookmark's title and `iconColor`. Each call reads
the lists it needs once (the installed apps for `id` items, the launch points, the inputs) and every
file afresh. An invalid `items` fails the whole call.

Errors: `forbidden`, `notRoot`, `badRequest`, `noPermission`, `lookupFailed`, `timeout`, `refused`,
`badReply`.

### `closeApp {id, force?}`

Closes an app. Only the selected Home may call it. `id` is an application id; `force: true` (a JSON
boolean) asks the platform for a forced close. Reply
`{"returnValue": true, "apiVersion": 2, "id": "<id>"}`.

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
{"returnValue": true, "apiVersion": 2, "pending": true, "id": "org.example.app", "confirmation": "alert"}
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
| platform error (service or method unknown, `Denied method call`) | Home Chooser is not installed or too old; or, on 11.x and 12.x, its access file is not in place yet, or your `requiredACG` leaves out `application.query` or `application.launcher` (see [Permission on 5.x to 10.x and on 11.x and 12.x](#permission-on-5x-to-10x-and-on-11x-and-12x)) | Call `getInfo`; offer to install or open Home Chooser; check `requiredACG` |
| `forbidden` | The caller has no valid application id | Call from the app. A shell `luna-send` needs `-a <app id>` |
| `notRoot` | The service is not elevated | Ask the owner to open Home Chooser |
| `noPermission` | The installed appinfo lacks `org.webosbrew.homechooser.applications` | Fix `requiredPermissions`, reinstall |
| `lookupFailed` | The service could not read the caller's installed appinfo, from SAM or its file fallback (a timeout or an unreadable reply in that lookup is `timeout` or `badReply`) | Retry later; log `errorText` |
| `notHome` | Your app is not the selected Home | Send the `request` launch, wait for the owner |
| `badRequest` | `id` is missing or not an app id, `getAppIcon.size` is invalid, or `getIcons.items` is not 1 to 64 valid items | A bug in the launcher |
| `notFound` | A target lookup reports absence, or SAM's ambiguous refusal is resolved against the installed app list and the id is absent | Refresh the list |
| `noIcon` | `getAppIcon`: no readable icon from the requested appinfo field inside that app's folder (in `getIcons`, an item's code) | Show a placeholder |
| `iconTooLarge` | `getAppIcon`: the icon exceeds 262144 bytes (in `getIcons`, an item's code) | Show a placeholder |
| `tooManySubscriptions` | The method already has 16 subscribers | Cancel the subscriptions you no longer need; keep one per method |
| `protected` | `uninstallApp`: target is Home Chooser, Homebrew Channel or the caller | Do not offer uninstall for this app |
| `notRemovable` | `uninstallApp`: target is marked `removable:false` or outside the user app folders (system apps are) | Do not offer uninstall for this app |
| `confirmFailed` | `uninstallApp`: the owner could not be asked | Try again later; tell the user |
| `timeout` | A bounded application manager call reached its time limit | Retry once; log `errorText` |
| `refused` | The application manager explicitly refused an operation or target lookup for a reason other than absence | Log `errorText`; do not loop |
| `badReply` | The application manager did not provide usable JSON, or its successful list reply lacked the expected array | Log it; report it |
| `internal` | The service could not make a confirmation token | Retry once |

Treat an unknown `errorCode` as a failure and log it. A `getIcons` entry has its own `errorCode`
(`notFound`, `noIcon`, `iconTooLarge` or `overBudget`) for that item only; the call itself succeeded.

## Icons

Use `getIcons`. `listApps` gives each app's `icon` and `folderPath` as the app declared them, and
`listLaunchPoints` and `listInputs` give absolute icon paths, but loading such a path as a `file://`
URL needs your app to be able to read that file, and a [jailed](#glossary) launcher cannot read other
apps' folders or LG's directories. `getIcons` reads the files as root, only from the places listed
under [`getIcons`](#geticons-items), for every app type and release, and returns `data:` URLs:

```js
const key = (app) => app.id + '@' + app.version;
const missing = apps.filter((app) => !iconCache.has(key(app)));
for (let i = 0; i < missing.length; i += 64) {
  const batch = missing.slice(i, i + 64);
  const {icons} = await homeChooser('getIcons', {items: batch.map((app) => ({id: app.id}))});
  icons.forEach((icon, n) => {
    if (icon.errorCode !== 'overBudget') iconCache.set(key(batch[n]), icon.dataUrl || null);
  });
}
```

Ask for every icon a screen needs in one call (up to 64 items) rather than one call per icon. A call
costs a fixed part, the caller's permission check and one read of each list its items need (each
about 20 to 40 ms on a webOS 5 set), plus a file read per icon, so one call for 30 icons takes about
as long as two single `getAppIcon` calls. Ask again for an item that came back `overBudget` (the loop
above leaves it out of the cache, so the next pass asks again). `noIcon` and `iconTooLarge` are
normal answers; show a placeholder, or the `iconColor` on a tile.

Home Chooser does not cache: every call reads the files again. Cache in your launcher, as in
[Subscriptions](#subscriptions): by `id` and `version` for apps, and fetch only the entries a
subscription reply shows as new or changed. `getAppIcon {id, size?}` stays for a single icon and
answers as before.

## Platform calls you'll need

These are webOS platform behaviour, not part of Home Chooser's API: Home Chooser neither
provides nor freezes them. They are here because every launcher needs them.

* **Launch an app.** `luna://com.webos.applicationManager/launch` with `{"id": "<app id>"}`, and
  `"params": {...}` for the app's own launch parameters. The reply has `returnValue: true` when the
  platform accepted the launch. The [request launch](#3-asking-to-become-home) is this call with
  Home Chooser's id.
* **Launch a launch point** (an app tile or a bookmark from `listLaunchPoints`). The same call with
  `{"id": <id>, "launchPointId": <launchPointId>, "params": <params>}`, as LG Home sends it.
* **Switch to an input.** The same call that launch points use, with the input's `id` and `params`
  from `listInputs` (`{"id": "com.webos.app.hdmi1", "params": {}}`), which is what LG's input picker
  sends. It works from a third-party app id (verified on webOS 5.6.1, 6.5.2 and 7.6.0). Live TV is
  `{"id": "com.webos.app.livetv"}`.
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
touch the stock home's memory here. Calls reach the service in the same way as on 6.x to 10.x
(below).

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

Two things decide whether a launcher's call is answered: whether the luna bus delivers it to Home
Chooser's service at all, and the service's own check.

**The service's check is the gate on every release.** It reads `requiredPermissions` from the
launcher's installed appinfo: without `org.webosbrew.homechooser.applications` every method except
`getInfo` answers `noPermission` (`closeApp` and `uninstallApp` check `notHome` first). That is the
declaration Home Chooser reads to permit a launcher, and it is not something the bus enforces.

**Delivery by the bus** differs by release:

* **5.x to 10.x.** Homebrew Channel's elevation gives Home Chooser's service a `public` API file
  (`org.webosbrew.homechooser.service.api.public.json`, `{"public": ["org.webosbrew.homechooser.service/*"]}`),
  and every app holds the `public` group, so every app's calls reach the service. A developer app's
  client permissions are `["public"]` whatever its `requiredPermissions` or `requiredACG` say
  (verified on 9.2.1 and 10.3.1). Home Chooser's own group in its access file grants nothing more
  here; it exists so that installing a launcher that declares it is accepted (the installer refuses an
  undeclared group with `Not allowed permission is used`).
* **11.x and 12.x.** A developer app holds exactly the public groups of the system's
  `allowed_groups.json` (33 on 11.2.0, 36 on 12.0.0; `application.query` and `application.launcher`
  among them), or, when its appinfo declares `requiredACG`, only those of its `requiredACG` that are
  in that list. `requiredPermissions` is ignored there, and the `public` group is not given to
  developer apps. Home Chooser's access file therefore also provides the reading methods (`getInfo`,
  `listApps`, `listLaunchPoints`, `listInputs`, `listRunning`, `getForegroundApp`, `getAppInfo`,
  `getAppIcon`, `getIcons`) in `application.query`, and `closeApp` and `uninstallApp` in
  `application.launcher`. Verified on 11.2.0 and 12.0.0: with these, a launcher page's calls reach
  the service, and the service's `noPermission` and `notHome` answers stand. The service registers
  with its groups when it starts: when the access file changes, Home Chooser restarts its own service
  (the settings app quits it after setup; the boot entry and the watcher stop it), and the next call
  starts it again.

**A launcher that declares `requiredACG`** must list `application.query` and `application.launcher`
in it, or on 11.x and 12.x the bus refuses the methods of the group it left out (`Denied method
call`) before Home Chooser sees them. Without `requiredACG` nothing is needed.

Also on 11.x and 12.x, the current Homebrew Channel refuses to elevate Home Chooser's service from an
ordinary app (`Denied method call`), so `root` stays false until the owner elevates it by other means
or a newer Homebrew Channel allows it. The layout of the access files is in
[the service reference](service.md#luna-access-files).

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
the caller checks in the service's order; on a TV it does nothing. It lists 20 apps (one of them
`visible: false`), so a grid and its focus movement can be tried; `?hcmock-apps=<n>` lists `n`.
Subscriptions answer at once;
`HomeChooser.mock.notify('listInputs')` sends a change, and `?hcmock-changes=<ms>` flips an input's
`connected` and brings another app to the foreground every `<ms>` milliseconds. `?hcmock=before-elevation`,
`not-permitted`, `not-home` or `absent` in the page URL selects another start state. See
[`api/README.md`](../api/README.md#client-and-mock).

**By hand, as root on the device.** `luna-send -a <app id>` makes the call as an installed app with
its effective client permissions, so install your launcher first; an uninstalled or made-up id
tests nothing.

```sh
luna-send -a org.example.launcher -n 1 luna://org.webosbrew.homechooser.service/getInfo '{}'
luna-send -a org.example.launcher -n 1 luna://org.webosbrew.homechooser.service/listApps '{}'
luna-send -a org.example.launcher -n 1 luna://org.webosbrew.homechooser.service/listRunning '{}'
luna-send -a org.example.launcher -n 1 luna://org.webosbrew.homechooser.service/getIcons '{"items":[{"inputId":"HDMI_1"}]}'
luna-send -a org.example.launcher -i luna://org.webosbrew.homechooser.service/listInputs '{"subscribe":true}'
```

A `-i` subscription runs until you stop it (Ctrl-C).

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
app, checks the launcher API before and after choosing the fixture, then restores LG Home and removes
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
  an app is jailed depends on its type and the release; `getIcons` and `getAppIcon` work either way.

## Stability

`apiVersion` is the feature level of the API, and it only grows. A higher level keeps everything the earlier levels documented: no method or field is removed and no meaning changes. Launchers check `apiVersion >= N` for the level they need, never `== 1`, and ignore unknown fields and error codes. If a backward-incompatible change ever becomes unavoidable, it will be handled separately and announced.

What each level added:

* Level 1: the eight methods as frozen at tag `api-v1` (`getInfo`, `listApps`, `listRunning`, `getAppInfo`, `getAppIcon`, `getForegroundApp`, `closeApp`, `uninstallApp`), the permission, the `appCategory:["home"]` and `requiredPermissions` appinfo declarations, and the `request` launch parameter.
* Level 2: `listLaunchPoints`, `listInputs` (with each input's `params` and, where the input manager has it, `signal`), `getIcons`, the `subscribe` parameter of `listApps`, `listLaunchPoints`, `listInputs`, `listRunning` and `getForegroundApp`, and the `iconColor` and `bgColor` fields of `listApps` and `getAppInfo`.

A machine-readable description of every level is [`api/launcher-v1.openrpc.json`](../api/launcher-v1.openrpc.json); `x-since` marks what a level added. The file name keeps `v1` as the name of the API family.

Settings-only and confirmation methods, files, status output and log ids are internal and are not
frozen by this contract.
