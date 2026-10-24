# Writing a launcher for Home Chooser (API version 1)

Home Chooser (`org.webosbrew.homechooser`) lets the owner of a rooted LG webOS TV choose which app
the Home button opens. This guide is for the authors of such apps (launchers, docks, alternative
home screens). It covers what the owner and Home Chooser do for you, how to declare your app, and
the luna API that Home Chooser's root service offers to a launcher.

## What Home Chooser does for a launcher

* **It makes your app the Home app on webOS 5 to 12.** The owner picks your app in Home Chooser;
  from then on the Home button opens it. The choice survives reboots: a small watcher re-applies it
  at boot, and puts LG Home back if your app is uninstalled (and re-applies the choice if it comes
  back). On webOS 6 to 9 it installs a key filter patch for this, which needs one TV restart the
  first time. See [Per-release notes](#per-release-notes).
* **It tells the owner when your launcher is removed.** If the chosen launcher is uninstalled, the
  owner sees one alert saying that the Home button opens LG Home again, instead of a Home button
  that does nothing.
* **It gives your launcher an app list and a few other calls without root.** Your app has no root
  of its own. Home Chooser's service (`org.webosbrew.homechooser.service`) runs as root and offers
  `listApps`, `running`, `closeById` and `uninstall` to a launcher that declares one permission.
  Closing and uninstalling work only while your app is the selected Home, and an uninstall always
  asks the owner first.

What it needs: a TV with Homebrew Channel, and the owner must have opened Home Chooser once, so
that Homebrew Channel elevated its service. Until then only `getInfo` works, and it tells you so.

Status: the API is implemented and covered by host tests, and the selection flow runs end to end on
VMs of every release. The API calls themselves have not been verified on a device yet; see
[Stability](#stability). Please report what you see on yours.

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
  "requiredPermissions": ["homechooser.applications"]
}
```

* `"appCategory": ["home"]` makes the app eligible: Home Chooser lists every installed app that
  has it. This is the whole contract for being chosen as Home. It works for `type` `web`, `native`
  and `qml`, and needs no variant per webOS release.
* `"requiredPermissions": ["homechooser.applications"]` opts into the API below. Leave it out if
  you only want to be selectable. Merge both entries with your other categories and permissions.
* **A launcher that draws over the running app** (a dock, a bar) is an overlay launcher. Add
  `"defaultWindowType": "overlay"` and, for a see-through window, `"transparent": true`. See
  [The stock home](#the-stock-home) for what happens to LG's own home app.

The permission is read from the appinfo the app was installed with, so reinstall after any change
to `requiredPermissions`. Install Home Chooser, and open it once, before you install a launcher
that requests the permission: the group is made available by Home Chooser's access files, which it
applies after elevation. If the install is refused with `Not allowed permission is used`, open
Home Chooser and install again (not yet confirmed on a device).

### 2. How the owner selects your launcher

The owner opens Home Chooser (it is an ordinary app), moves to your launcher's row and presses OK.
Home Chooser asks `Use <your title> as Home?`; the owner confirms and the Home button opens your
app. On webOS 6 to 9 the first selection ends with a request to restart the TV, and the Home button
changes after the restart.

### 3. Asking to become Home

Your app may ask the owner to select it, for example from a first-run screen. Launch Home Chooser
with the parameter `request`, set to your own app id, through
`luna://com.webos.applicationManager/launch`:

```json
{"id": "org.webosbrew.homechooser", "params": {"request": "org.example.launcher"}}
```

Home Chooser opens, loads the eligible launchers, and shows the same `Use <title> as Home?` dialog.
The owner decides. This is a request only:

* there is no reply that tells you the result; call `getInfo` later and read `isHome`;
* if your app is already the selected Home, Home Chooser shows an "already your Home" toast;
* the request is dropped without any message when the id is the stock home, unknown, not in the
  Home category or malformed, and also when Home Chooser still shows a setup problem (for example
  the service is not elevated yet) or is busy;
* on webOS 6 to 9 firmware without a reviewed patch, the dialog says so and changes nothing unless
  the owner has turned on the generated patch (see [6.x to 9.x](#6x-to-9x));
* the parameters are read at start (`PalmSystem.launchParams`) and on `webOSRelaunch` (dispatched on `document`; `window` is listened to as well), so the
  request works whether or not Home Chooser is already open.

### 4. Check where you stand, then use the API

At start, call `getInfo`, then branch:

1. The call fails (platform error such as `ServiceUnknown`, or an unknown-method error): Home
   Chooser is missing or too old. Offer to install or open it.
2. `apiVersion` is not `1`: this guide does not describe that service.
3. `root` is false: the owner has not finished setup; ask them to open Home Chooser.
4. `permitted` is false: your installed appinfo lacks the permission; fix it and reinstall.
5. `isHome` is false and you want to be Home: send the request above.
6. Otherwise call `listApps` and `running`; use `closeById` and `uninstall` while `isHome` is true.

## Calling the service

Calls go to `luna://org.webosbrew.homechooser.service/<method>` with a JSON object. No method
subscribes, and every call is a single request with a single reply. The service takes the caller
from the application id that the system reports for the message; nothing in the payload can name
another caller, and a shell `luna-send` (no application id) is refused.

A web app can use the platform's bridge directly. This is the same call shape as Home Chooser's own
client (`app/src/platform/bridge.js`), with no dependencies:

```js
const SERVICE = 'luna://org.webosbrew.homechooser.service';
const TIMEOUT_MS = 30000;

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

// Ask the owner to make this app the Home app (fire and forget; read getInfo().isHome later).
function requestHome() {
  return lunaCall('luna://com.webos.applicationManager/launch', {
    id: 'org.webosbrew.homechooser',
    params: {request: 'org.example.launcher'}
  });
}
```

`webOS.service.request` from webOSTV.js does the same through the same bridge; its failure callback
receives the same `returnValue:false` object. A native or QML launcher uses its own luna client
(libluna-service2, or the QML `LunaService` element) and the same URI and payloads. No native or QML
example is given here because none has been checked on a device.

Successful replies have `returnValue: true`. Failures from Home Chooser look like this:

```json
{"returnValue": false, "errorCode": "notHome", "errorText": "Only the app selected as Home may call this method"}
```

Show your own plain failure message to the user and keep `errorText` in your logs. Allow 30 seconds
per call, as Home Chooser's own client does; the service makes bounded calls one after another, so
a slow platform call delays the others.

## Methods

All methods are on the service's root category, and all were introduced in API version 1.

| Method | Needs root | Needs the permission | Only the selected Home | Reply carries `apiVersion` |
| --- | --- | --- | --- | --- |
| `getInfo` | no | no (reports it) | no (reports it) | yes |
| `listApps` | yes | yes | no | yes |
| `running` | yes | yes | no | yes |
| `closeById` | yes | yes | yes | no |
| `uninstall` | yes | yes | yes | no |

Every method needs a caller with a valid application id (1 to 128 characters of `A-Za-z0-9._-`).
When several checks fail, the first of these is reported: `forbidden`, `notRoot`, `notHome`,
`badRequest`, `noPermission`. "The permission" is `homechooser.applications` in your installed
`requiredPermissions`. "The selected Home" means your app id is the one Home Chooser has stored as
the owner's choice; this is not the same as what the Home key opens at this moment (see `isHome`).

### `getInfo {}`

Feature detection and status. Works for any app, before elevation and without the permission.

```json
{"returnValue": true, "apiVersion": 1, "version": "0.1.0", "root": true, "group": "homechooser.applications", "permitted": true, "isHome": true}
```

* `apiVersion`: the version of this contract, independent of the package version. Check it before
  relying on this guide.
* `version`: the Home Chooser package version.
* `root`: false means the owner has not finished setup (open Home Chooser).
* `group`: the permission name to list in `requiredPermissions`.
* `permitted`: your installed appinfo lists the permission. It is also false when the service
  could not read your appinfo, which can happen while `root` is false; look at `root` first.
* `isHome`: your app id equals the choice Home Chooser has stored. While the chosen app is missing,
  the watcher puts LG Home back but keeps the stored choice, so this can be true while the Home key
  opens LG Home. On webOS 6 to 9 it is also true between the selection and the TV restart.

Errors: `forbidden` (no valid application id).

There is no list of supported methods yet. To detect the service, use the platform's own failure
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
find the other launchers. Within an `apiVersion` this field list only grows. `icon` and `largeIcon` are file names
relative to `folderPath`. There is no icon proxy for launchers: a web launcher may not be able to
read another app's files, and Home Chooser's own icon service is for its settings app only.

Errors: `forbidden`, `notRoot`, `noPermission`, `timeout`, `callFailed`, `badReply`.

### `running {}`

Lists running apps.

```json
{"returnValue": true, "apiVersion": 1, "running": [{"id": "org.example.app", "instanceId": "instance", "processid": 123, "webprocessid": 456}]}
```

Each item carries `id`, `instanceId`, `processid` and `webprocessid`, when present, with the
platform's own spelling and types. Errors: as `listApps`; `badReply` also means the platform did
not return a `running` array.

### `closeById {id, force?}`

Closes an app. Only the selected Home may call it. `id` is an application id; `force: true` (a JSON
boolean) asks the platform for a forced close. Reply `{"returnValue": true, "id": "<id>"}`.

There is no restriction on the target: the Home app may close any app, including Home Chooser, LG's
own apps and itself. Errors: `forbidden`, `notRoot`, `notHome`, `badRequest` (missing or malformed
`id`), `noPermission`, `callFailed`, `timeout`.

### `uninstall {id}`

Asks the owner to uninstall an app. Only the selected Home may call it.

Home Chooser refuses these targets, with `notAllowed` and an `errorText` that says which rule
applied: Home Chooser itself, Homebrew Channel, the calling launcher, an app that is not removable,
and an app outside the developer and store application folders
(`/media/developer/apps/usr/palm/applications/`, `/media/cryptofs/apps/usr/palm/applications/`). An
unknown id is `notFound`.

**The owner always confirms.** Home Chooser raises an alert, `Uninstall <title>?`, with Uninstall
and Cancel; if the alert is refused it opens its own confirmation screen with the same question.
Your launcher cannot confirm for the owner and cannot call the confirmation method.

```json
{"returnValue": true, "pending": true, "id": "org.example.app", "confirm": "alert"}
```

`confirm` is `alert` or `screen`. **A pending uninstall is not a completed removal.** There is no
second reply: the owner may cancel, ignore it (the question expires after 120 seconds) or confirm,
and nothing tells you which. Call `listApps` again to see whether the app is gone. If neither the
alert nor the screen could be shown, the call fails with `confirmFailed`.

Errors: `forbidden`, `notRoot`, `notHome`, `badRequest`, `noPermission`, `notFound`, `notAllowed`,
`timeout`, `confirmFailed`, `internal` (no confirmation token could be made). How the confirmation
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
| `noPermission` | The installed appinfo lacks `homechooser.applications` | Fix `requiredPermissions`, reinstall |
| `notHome` | Your app is not the selected Home | Send the `request` launch, wait for the owner |
| `badRequest` | `id` is missing or not an app id | A bug in the launcher |
| `notFound` | `uninstall`: no such app | Refresh the list |
| `notAllowed` | `uninstall`: the target is refused by rule (`errorText` says which) | Do not retry; hide the action for this app |
| `confirmFailed` | `uninstall`: the owner could not be asked | Try again later; tell the user |
| `timeout` | No usable reply from the application manager in time, or none at all | Retry once; log `errorText` |
| `callFailed` | The application manager refused the call | Log `errorText`; do not loop |
| `badReply` | The application manager's reply had an unexpected shape | Log it; report it |
| `internal` | The service could not make a confirmation token | Retry once |

Treat an unknown `errorCode` as a failure and log it.

## Per-release notes

Releases are named by the major version in `/etc/os-release` (`VERSION_ID`), not by LG's marketing
year. Your launcher declares the same appinfo on all of them. The API and the selection request
behave the same everywhere; what differs is how the Home button is made to follow the choice, what
the owner sees, and how the permission reaches you.

### 5.x

The stock key filter launches the `home` entry of the `general/defaultApps` settings record
directly, so Home Chooser only writes that record. No patch, no restart. The stock launcher is
`com.webos.app.fullhome` (an overlay window), and LG Home is not running, so Home Chooser does not
touch the stock home's memory here. The permission arrives through the hub group in the same way as
on 6.x to 10.x (below).

### 6.x to 9.x

The stock key filter launches LG Home by its fixed id, whatever the record says. Home Chooser ships
a key filter patch that makes the Home key follow the record, and mounts patched copies of the
system files at boot. For a launcher this means:

* **Restart on first setup.** The first selection stores the choice and enables the patch, then
  asks the owner to restart the TV with a system alert (Reboot or Later). The Home button opens your
  launcher after that restart. Until then the choice is stored (`isHome` is true) but the Home
  button still opens LG Home. Later changes between launchers need no restart. Nothing restarts the
  compositor while the TV runs, so setup never closes your window.
* **Reviewed builds.** The patch is keyed by the hashes of the stock files, for the builds listed
  in [Coverage](key-filter-backport.md#coverage) (6.5.2 to 9.2.4).
* **Generated patch on unreviewed firmware.** On an unlisted build, the owner can opt in to a patch
  generated from the live files (`Use a patch generated for this firmware (not reviewed)`), which
  is applied only if strict checks pass. Without the opt-in, selecting a launcher shows an
  explanation and changes nothing; if even generation is refused, the dialog says the Home button
  cannot be changed on this TV's software. A `request` from your app gets the same dialog.
  See [Generated fallback](key-filter-backport.md#generated-fallback).
* **When the owner restores LG Home or your launcher is removed**, the record is written with the
  stock home id (not cleared), so LG Home opens at once; the patch stays mounted until the next
  restart. The owner is told about a removed launcher as described above, and the choice is
  re-applied if you are reinstalled.

### 10.x and later

The compositor takes the home app from the system profile, so Home Chooser also writes a profile
override (`/var/webos-profile/override/org.webosbrew.homechooser.json`) with your id, in addition
to the settings record. There is no patch and no restart. When your launcher is removed or the
owner restores LG Home, the override is removed and the record's `home` is cleared, which leaves
the profile's own stock launcher.

### Permission on 5.x to 10.x and on 11.x and 12.x

On 5.x to 10.x a developer app's declared permission becomes one of its client permissions, and
Home Chooser's access file maps the group `homechooser.applications` to the five launcher methods.

On 11.x and 12.x a developer app keeps only the groups `ares.webos.cli` and `public` in its own
client-permissions file, whatever its `requiredPermissions` says, so the permission is not listed
there. Access comes through Home Chooser's API file instead, which on these releases also grants
the five methods to `ares.webos.cli`. The service still demands the declared permission in the
installed appinfo, so nothing changes for your appinfo: declare the permission, and `getInfo`
reports `permitted`. Also on 11.x and 12.x, the current Homebrew Channel refuses to elevate
Home Chooser's service from an ordinary app (`Denied method call`), so `root` stays false until the
owner elevates it by other means or a newer Homebrew Channel allows it. The layout of the access
files is in [the service reference](service.md#launcher-access-files).

### The stock home

LG's stock home (`com.webos.app.home`) normally stays resident (70 to 105 MB). While a third-party
home is selected, Home Chooser keeps it from being preloaded, once per boot. This is not permanent:
nothing is written, and a reboot starts with the stock policy until Home Chooser sets it again. 5.x
is left alone.

On 11.x and 12.x Home Chooser also force-closes it, and the close sticks. On 6.x to 10.x it does not,
for any launcher, overlay or not: the system relaunches the stock home at once as the last-input
app, so a close frees nothing. The stock home can therefore stay resident until the next reboot, and
`running` will list `com.webos.app.home` (a `closeById` of it by your launcher will not stick
either). After a reboot it is gone, because its preload is off from boot. Details are in
[The stock home is not preloaded](settings-app.md#the-stock-home-is-not-preloaded).

## Testing your launcher

**By hand, as root on the device.** `luna-send -a <app id>` makes the call as an installed app with
its effective client permissions, so install your launcher first; an uninstalled or made-up id
tests nothing.

```sh
luna-send -a org.example.launcher -n 1 luna://org.webosbrew.homechooser.service/getInfo '{}'
luna-send -a org.example.launcher -n 1 luna://org.webosbrew.homechooser.service/listApps '{}'
luna-send -a org.example.launcher -n 1 luna://org.webosbrew.homechooser.service/running '{}'
```

Use a disposable app on a test device for `uninstall`.

**With the fixtures.** [`e2e/fixtures`](../e2e/fixtures/README.md) holds six minimal launchers
(`native`, `qml` and `web`, each `normal` and `overlay`). The web ones can declare a permission when
built, through the environment variable `HC_FIXTURE_PERMISSION` (the native and qml fixtures cannot).
Building only the web fixtures needs python3 and `ares-package`, no webOS SDK:

```sh
HC_FIXTURE_PERMISSION=homechooser.applications sh e2e/fixtures/web/build.sh build/fixtures
```

Install `build/fixtures/org.webosbrew.hcfixture.web.normal_1.0.0_all.ipk` and use its id with `-a`.
Without the variable you get a launcher with no permission, which is the control for `noPermission`.

**The end-to-end runner.** `e2e/homechooser_e2e.py` runs the whole Home selection lifecycle on a
Beanjar VM through the `ares-*` tools: install, elevate, choose through the real UI, the Home key
launching your app, removal of your launcher and of Home Chooser. For your own launcher:

```sh
python3 -I e2e/homechooser_e2e.py --vm 9.x --launcher org.example.launcher --launcher-ipk path/to/launcher.ipk
```

`--vm` takes a VM name or a release (`5.x` to `12.x`); the matrix and the options are in
[`e2e/README.md`](../e2e/README.md). The runner checks selection, persistence and restore. It does
not call the methods above yet: an `--api-check` suite that exercises the API contract for a
launcher (the checks in this guide, with the fixtures as controls) is being added to the runner.
Treat its flags as unstable until it is documented in the E2E README.

Beanjar VMs are the maintainers' test devices (one per release, 5.6.1 to 12.0.0). Run one test at a
time on a device or VM, remove test apps when done, and check `df -h /media/developer` first: the
partition is small and a full one makes the next install fail for an unrelated-looking reason.

## Stability

Home Chooser is not 1.0 and API version 1 is not frozen. Write your launcher to ignore reply fields
and `errorCode` values it does not know, to check `apiVersion` at start, and to fail with a plain
message when a call fails. After the freeze, replies may gain fields and methods may be added
within version 1; removing a field or changing a meaning will raise `apiVersion`. The device status
of each call, release by release, is in [the service reference](service.md#launcher-api-device-status).

**Before 1.0**, these names may still change:

* the permission `homechooser.applications` (un-namespaced, and broader than its name suggests);
* `getInfo.group` (it is the permission) and `isHome` (it reflects the stored choice, not what the
  Home key opens);
* `closeById` (the platform's name is `closeByAppId`) and its lack of a target restriction;
* the error codes `notAllowed` (one code for several reasons) and `timeout` (also used when no
  reply came at all);
* the pass-through spelling of the `running` fields (`processid`, `webprocessid`);
* the `request` launch parameter, the `confirm` field of the `uninstall` reply, and the lack of a
  capability list in `getInfo`.
