# Launcher API, version 1

Home Chooser (`org.webosbrew.homechooser`) supplies the root service
`org.webosbrew.homechooser.service`. A launcher can use it to list, close and remove apps without
shipping its own elevated service. The owner must first open Home Chooser and let Homebrew Channel
elevate its service. This contract describes the implementation; the device validation status is
recorded below. `getInfo.apiVersion` is the contract version, independent of the package version.

## Appinfo contract

An installed launcher declares both its Home category and its permission request in `appinfo.json`:

```json
{
  "id": "org.example.launcher",
  "appCategory": ["home"],
  "requiredPermissions": ["homechooser.applications"]
}
```

Merge these entries with the app's other categories and permissions. An overlay launcher also sets
`"defaultWindowType": "overlay"`. The category makes the app eligible for Home Chooser's list; the
permission opts into the application API. Reinstall the app after changing its permissions, since
the hub uses installed client permissions.

Calls go to `luna://org.webosbrew.homechooser.service/<method>`, with JSON objects. No subscription
is offered by this API. Successful replies have `returnValue: true`; failures have
`{returnValue:false,errorCode:"<code>",errorText:"<detail>"}`. Launchers should present their own
plain failure message and retain the detail in logs. Do not interpret a pending uninstall as a
completed removal.

## Methods

### `getInfo {}`

```json
{"returnValue":true,"apiVersion":1,"version":"0.1.0","root":true,"group":"homechooser.applications","permitted":true,"isHome":true}
```

The service accepts any valid application caller here, even before elevation or without consent;
the hub's access control can still deny delivery. `permitted` reports the caller's declared
permission. `isHome` compares the caller with Home Chooser's stored selection. `root:false` means
the owner needs to open Home Chooser to complete setup. Permission discovery uses `getAppInfo` and
falls back to reading the installed app's `appinfo.json` when necessary; if discovery fails,
`permitted` is false. Errors: `forbidden` for a caller without an application id.

If the service returns `ServiceUnknown`, is unavailable, or has no `getInfo` method, treat Home
Chooser as absent or incompatible and offer to open/install it. Check `apiVersion` before relying
on this contract. A denial alone does not prove absence: it can mean missing installed permissions.

### `listApps {}`

Requires root and the declared permission. Replies:

```json
{"returnValue":true,"apiVersion":1,"apps":[{"id":"org.example.app","title":"Example","icon":"icon.png","folderPath":"/media/developer/apps/usr/palm/applications/org.example.app","removable":true}]}
```

Only `id`, `title`, `icon`, `largeIcon`, `folderPath`, `version`, `vendor`, `type`, `removable` and
`visible` are forwarded. Missing fields are omitted. There is no icon-file proxy for launchers;
`readIcon` belongs to Home Chooser's settings app. Errors: `forbidden`, `notRoot`, `noPermission`,
`timeout`, `callFailed`, `badReply`.

### `running {}`

Requires root and the declared permission. Replies:

```json
{"returnValue":true,"apiVersion":1,"running":[{"id":"org.example.app","instanceId":"instance","processid":123,"webprocessid":456}]}
```

Only those four item fields are forwarded, when present. Their values retain the platform's types.
The implementation expects the platform's array under `running`; a different shape is `badReply`.
Errors: the same as `listApps`. Availability and the platform field name are device-unverified.

### `closeById {id, force?}`

Requires root, the declared permission, and the caller being the currently selected Home.
`id` is an application id; optional boolean `force:true` sets the platform's `forceMode:true`.
It calls `applicationManager/closeByAppId` and replies
`{returnValue:true,id:"org.example.app"}` when accepted. Errors: `forbidden`, `notRoot`,
`noPermission`, `notHome`, `badRequest`, `timeout`, `callFailed`.

### `uninstall {id}`

Requires root, the declared permission, and the caller being the currently selected Home.
It refuses Home Chooser itself, Homebrew Channel, the requesting launcher, any app marked
`removable:false`, and anything outside the known developer/store application directories:
`/media/developer/apps/usr/palm/applications/` and
`/media/cryptofs/apps/usr/palm/applications/`. An unknown or invalid target is refused.

Home Chooser fetches the target title, then raises its own alert: `Uninstall <title>?`, with
**Uninstall** and **Cancel**. Only Uninstall calls the confirmation method. If `createAlert` is
refused, Home Chooser launches its settings app with `{confirmUninstall:"<id>",token:"<token>"}`;
that screen obtains the title from `getAppInfo` and asks the same question. A launcher must use
`uninstall`, never construct a confirmation screen or call `uninstallConfirmed` itself.

The initial reply is
`{returnValue:true,pending:true,id:"org.example.app",confirm:"alert"}` or `confirm:"screen"`.
This means confirmation was requested. Cancellation, expiration and completion produce no second
reply to the launcher; refresh the app list to observe removal. Both confirmation routes failing
returns `confirmFailed`.

The service stores at most four random, one-shot tokens, valid for 120 seconds; a newer request
can replace an older slot. Tokens stay inside Home Chooser's confirmation flow. Confirmation
rechecks that the requester is still the selected Home. It first calls
`applicationManager/removeLaunchPoint` with `<id>_default`. A platform refusal saying the app
“is not removable” stops the operation. Other failures fall back to
`appInstallService/dev/remove` for a developer app or `appInstallService/remove` for a store app,
with `{id,subscribe:true}`. The fallback reads progress until status 31 (success) or 25 (failure),
with a 90 second bound. The launch-point route reports acceptance, not an independently observed
end of uninstall.

Errors: `forbidden`, `notRoot`, `noPermission`, `notHome`, `badRequest`, `notFound`, `notAllowed`,
`timeout`, `confirmFailed`, `internal` (no random token could be generated).

`uninstallConfirmed {token}` is an internal confirmation endpoint, excluded from the launcher
permission files. Only Home Chooser or a caller without an application id (the notification
service route) may present a live token. Its reply is `{returnValue:true,id,route:"launchPoint"}`
or `route:"appInstallService"`. Errors: `forbidden`, `notRoot`, `badToken`, `notHome`,
`notRemovable`, `removeFailed`. The settings app allows 120 seconds for this call.

## Request selection as Home

Launch Home Chooser with `{"request":"org.example.launcher"}` through
`luna://com.webos.applicationManager/launch`:

```json
{"id":"org.webosbrew.homechooser","params":{"request":"org.example.launcher"}}
```

After loading the eligible launchers, Home Chooser opens its existing `Use <title> as Home?`
dialog. The owner confirms or cancels. An already selected app produces the existing “already
your Home” toast. Stock Home, unknown ids and apps without the Home category are ignored. Initial
`PalmSystem.launchParams` and subsequent `webOSRelaunch` events are handled. This is a request to
the owner, with no completion callback to the requesting launcher.

## Access and identities

The service takes the caller from `LSMessageGetApplicationID` (dropping an instance suffix after
a space); request payloads cannot choose a caller. The service independently reads the caller's
installed `requiredPermissions`. `closeById` and `uninstall` additionally compare that id with the
stored selection in `/var/lib/webosbrew/homechooser/home`. These are service checks even when the
hub permits the call via a broader group. An ordinary shell `luna-send` has no app id and is refused.

`backport/homechooser-access` grants `homechooser.applications` (declared only by Home Chooser's
own API file, never through `groupsFiles`; see docs/service.md) only `listApps`, `running`,
`closeById`, `uninstall`, `getInfo`. It references the files from each
existing Home Chooser package manifest under `/var/luna-service2-dev` and `/var/luna-service2`.
On webOS 11.x and later, where developer apps retain only `ares.webos.cli`/`public`, the same five
methods are also granted under `ares.webos.cli`; the service still demands the app's declared
`homechooser.applications` permission. `uninstallConfirmed` is absent from both lists.

Every privileged call in the native service and watcher uses root
`luna-send -a com.webos.applicationManager`, the fixed `HC_LUNA_IDENTITY`. No forwarded call is
made using the native service's own luna identity. Native calls have bounded subprocess timeouts
and run serially; a slow operation can delay other requests. Profile commands use this identity
as well.

## Device validation status

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
simulate an installed caller with its real id, for example:

```sh
luna-send -a org.webosbrew.hcfixture.web.normal -n 1 luna://org.webosbrew.homechooser.service/getInfo '{}'
luna-send -a org.webosbrew.hcfixture.web.normal -n 1 luna://org.webosbrew.homechooser.service/listApps '{}'
luna-send -a org.webosbrew.hcfixture.web.normal -n 1 luna://org.webosbrew.homechooser.service/running '{}'
```

Install the fixture from [`e2e/fixtures`](../e2e/fixtures/README.md) with the required permission
first; `-a` uses that installed app's effective client permissions. A made-up or uninstalled app id
does not test launcher access. Destructive removal checks require the owner's device permission.
