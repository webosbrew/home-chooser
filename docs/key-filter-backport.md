# The key filter backport, webOS 6.6.0 to 9.2.1

On 10.3.1 the compositor and the Home key filter take the home app from `compositor.getHomeAppId()`
and `compositor.isHomeApp()`. On 6.6.0 to 9.2.1 they name `com.webos.app.home` in the source. The
backport gives those releases the same two functions, fed by the `general/defaultApps` record that
the settings app writes, and replaces every stock-id site in the compositor QML and the key filter
scripts with them. The facts behind it are in the
[study](research/home-key-and-home-app.md#key-filter-backport-660---921); this page says what was
built. Paths are relative to each firmware's `rootfs/`.

The patch is shipped as diffs in [`backport/`](../backport), one directory per release, and applied
at boot by `backport/openhouse-keyfilter`, which the init.d entry `backport/openhouse` runs (see
[Deployment](#deployment) and [Persistence](settings-app.md#persistence)).

## The home id

One source serves the compositor QML and the key filter scripts: the `Settings` singleton,
`usr/lib/qml/WebOSCompositorBase/global/Settings.qml`. It is the same file on all four releases
(sha256 `4dafd944...b78`, so one diff), it is the singleton every compositor QML file already
imports through `WebOSCompositorBase 1.0`, and it already owns the settings subscription cache
(`subscribe()` calls `SystemSettings.subscribeOnDemand`). `WebOSCompositor/StarfishUtils.qml`, the
other singleton, does not exist on 6.6.0.

* `getHomeAppId()` subscribes, through the existing `subscribe()`, to
  `luna://com.webos.settingsservice/getSystemSettings {"category":"general","keys":["defaultApps"],
  "subscribe":true}`. `DefaultSubscriptions.qml` has the prototype for that service and method, so
  nothing else is needed. It returns `defaultApps.home` when that is a non-empty string, else
  `configuredHomeAppId` when that is not empty, else `com.webos.app.home` (the constant
  `stockHomeAppId`). `configuredHomeAppId` is the stored choice
  (`/var/lib/webosbrew/openhouse/home`) as the boot worker wrote it into the patched copy, so the
  Home key keeps the choice at a boot where the settings record has lost its `home` (see the
  study's 11.2.0 findings) until the watcher has put it back. A binding that calls it re-evaluates when
  the record changes, so a later choice reaches the compositor live.
* `isHomeApp(appId)` is `appId === getHomeAppId()`. As on 10.3.1 with a profile override, the stock
  home is not special once another app is chosen.
* `launchHome(request)` launches the home app with `applicationManager/launch`: `{"id":
  getHomeAppId()}` plus the fields of `request`. It uses a `Service` object of its own
  (`service: "com.webos.applicationManager"`, `method: "launch"`, the pattern of
  `Toss/Service/SettingsService.qml`), because the launch helpers the compositor already has
  (`LS.applicationManager.launch`, `LS.adhoc.call`) give no reply to the QML. When the id is not the
  stock one and the reply has `returnValue: false`, it launches the real stock id,
  `com.webos.app.home`, with the same `request`.
* `StarfishViewStateController.qml` reads `Settings.getHomeAppId()` into a property
  (`homeAppId`) so the subscription starts with the compositor and not on the first Home press.

The key filter reaches these through `StarfishKeyFilter.qml`, in the existing `properties` groups:
`global.getHomeAppId`, `global.isHomeApp` (the names 10.3.1 uses in `global`) and
`applicationManager.launchHome(params)`, which calls `Settings.launchHome` with
`{params, checkUpdateOnLaunch: false, autoInstallation: false, reason: "hotKey"}`, the fields
`applicationManager.launch` sent. `StarfishKeyFilterShell.js` needs no change: the loader copies the
members of each `properties` group it already declares.

## Sites

Class (a) is a comparison with the stock id, (b) a launch or close of it, (c) anything else that
names it. Each row is one site in the stock file; line numbers are the stock ones.

| File (under `usr/lib/qml/`) | 6.6.0 | 7.6.0 | 8.3.0 | 9.2.1 | Class | Done |
| --- | --- | --- | --- | --- | --- | --- |
| `KeyFilters/systemUi.js` `HOME_APP_ID` definition | 26 | 26 | 26 | 31 | c | removed |
| `KeyFilters/systemUi.js` `launchHomeApp()` | 98 | 125 | 107 | 169 | b | `applicationManager.launchHome({activateType, launchType})` |
| `KeyFilters/systemUi.js` `launchHomeApp()`, `supportNMRM` | - | 127 | - | - | b | `checkLaunch(global.getHomeAppId(), ...)`, see below |
| `KeyFilters/systemUi.js` `handleHomeKeys` compares | 204, 218, 231, 239 | 237, 261, 275, 288, 296 | 223, 237, 249, 257 | 285, 299, 311, 319 | a | `global.isHomeApp(fullscreenView.appId)` |
| `KeyFilters/appLaunch.js` multiview toolbar `case` | - | - | - | 2212-2213 | a, b | `if (global.isHomeApp(targetAppId))` and `launchHome({activateType: "showOption"})`, as 10.3.1 |
| `WebOSCompositor/views/StarfishHomeView.qml` `openHome()` | 47 | 47 | 47 | 47 | b | `Settings.launchHome({})` |
| `WebOSCompositor/views/StarfishHomeView.qml` `closeHome()` | 51 | 51 | 51 | 51 | b | `closeWindowByAppId(Settings.getHomeAppId(), ...)` |
| `controllers/StarfishViewStateController.qml` splash skip | 618 | 598 | 580 | 639 | a | `!Settings.isHomeApp(appId)` |
| `controllers/StarfishViewStateController.qml` `lastInputApp` | - | 56 | 55 | 55 | c | `supportNMRM ? Settings.getHomeAppId() : ...`, as 10.3.1 |
| `controllers/StarfishFullscreenController.qml` `__lastInputApp` | - | 25 | 25 | 25 | c | same |
| `controllers/fullscreen/StarfishRecents.qml` `__lastInputApp` | - | 46 | - | - | c | same |
| `controllers/fullscreen/StarfishRecents.qml` `makeUpRecents`, `__updateQCardList` | - | - | 263, 664 | 311, 724 | a | `!Settings.isHomeApp(item.appId)`, `!Settings.isHomeApp(info.appId)` |
| `services/StarfishBaseLunaServiceAPI.qml` `__currentState` | 75 | 162 | 123 | 125 | a | `Settings.isHomeApp(views.fullscreen.currentItem.appId)` |
| `services/base/StarfishFocusedWindowInfoMgr.qml` `__focusableViewList` | 49 | 50 | 50 | 50 | c | `"appId" : Settings.getHomeAppId()` |
| `services/base/StarfishOverlayPlayVideoStateMgr.qml` synthetic home window | 67 | 67 | 67 | 67 | c | `appId: Settings.getHomeAppId()`, as 10.3.1 |
| `views/StarfishNudgeView.qml` `isSystemAllowable` | 56 | 56 | 56 | 58 | a | `Settings.isHomeApp(...)` |
| `views/StarfishGestureUIFeedbackView.qml` swipe icon | - | - | - | 85, 101, 132, 161 | a | `Settings.isHomeApp(focusedFullscreenInfo.appId)` |

Besides the sites, `Settings.qml` gets the functions above, `StarfishKeyFilter.qml` the three
exposures and `StarfishViewStateController.qml` the `homeAppId` property.

| Release | Stock-id sites replaced | Files patched |
| --- | --- | --- |
| 6.6.0 | 13 | 9 |
| 7.6.0 | 18 | 11 |
| 8.3.0 | 17 | 11 |
| 9.2.1 | 22 | 13 |

Decisions for the class (c) sites and for the kept ones:

* `lastInputApp`, `__lastInputApp` (7.6.0 and later): with `supportNMRM` the last input app
  defaults to home. 10.3.1 uses `compositor.getHomeAppId()` there; so does the backport.
* `StarfishFocusedWindowInfoMgr.qml`, `StarfishOverlayPlayVideoStateMgr.qml`: they report the home
  window's app id. 10.3.1 uses the compositor's home id; so does the backport.
* `closeHome()`: 10.3.1 calls `compositor.closeHomeApp()`. The backport closes the window of
  `getHomeAppId()`.
* `openHome()`: 10.3.1 calls `launchDefaultApp {"category":"home"}`. The backport launches the home
  id directly, which carries its own fallback, and sends no params, as the stock call did.
* The `supportNMRM` branch of `launchHomeApp()` (7.6.0) keeps `checkLaunch` and passes
  `global.getHomeAppId()`. `checkLaunch` gives the key filter no reply, so that branch has no
  fallback to `com.webos.app.home`.
* The recents cleanup (8.3.0 and later) no longer spares the stock home once another app is
  chosen, the same as on 10.3.1 with a profile override.
* `ContextMenu/Controller/ButtonListController.qml` (73-79) and `ContextMenu/View/MainView.qml`
  (76), 7.6.0 only, launch and compare the stock id. The context menu is an app of its own, outside
  the compositor process and the key path; 10.3.1 left the same two files unconverted. Kept.
* `ScreenRemote/AppInitializer/SystemProperties.qml` (117/153/158 list item, 181/186 and 377/435/451
  comparisons), `SportsAlarm` and `StoreStreamer` (`ExceptionsPopup.qml` toast source id, TTS app
  id, `FontManager.qml` locale directory): other processes, and identifiers of the stock app's own
  resources. Kept.
* `com.webos.app.home.uiStyle` and `com.webos.app.home.supportNMRM` (`KeyFilters/appLaunch.js`,
  `BootLogo`, `MasterBrand`, `ScreenRemote`): configd key names, not app ids. Kept.
* `Settings.qml` `stockHomeAppId`: the fallback constant, the one literal left in the patched files.
* Not covered: the compositor's native code (`libstarfishcompositorplugin.so` and the QML plugins),
  `etc/palm/bootd.json` and the snapshot preload, which the study lists as optional boot alignment.

## Deployment

`backport/<release>/` holds one unified diff per target file and a `manifest.json`:

```json
{"release": "9.2.1", "files": [{"path": "/usr/lib/qml/...", "sha256": "<stock sha256>", "diff": "<file name>"}]}
```

The diffs are `diff -u` of the stock file against the edited one, with the path relative to `/` in
both header lines and no timestamps, so `patch -p0` run in a directory that holds copies of the
files applies them. The `sha256` is the stock file's. A set is applied all or nothing, and the set
is chosen by hash, not by version: 6.6.0 is nine files, 7.6.0 and 8.3.0 eleven, 9.2.1 thirteen.

`backport/openhouse-keyfilter` is a busybox `sh` script, the worker. It is not in init.d itself:
the entry `/var/lib/webosbrew/init.d/openhouse`, a symlink to `backport/openhouse` in the installed
app, runs it at boot on webOS 6 to 9 when the backport is enabled (the flag file
`/var/lib/webosbrew/openhouse/keyfilter` exists). The names have no dot because `run-parts` skips
names that have one.

| Command | Effect |
| --- | --- |
| no argument, or `apply` | Find the set, patch copies, bind-mount them, restart the compositor. |
| `stop` | Unmount every target of every set it finds, restart the compositor. |
| `refresh` | If mounted, write the stored choice into the mounted copy of `Settings.qml` again (in place, so the bind mount keeps working) and restart the compositor when the id changed. |
| `status` | Print `mounted=yes` and `release=<set>`, or `mounted=no` and `match=<set or none>`; `data=missing` first if the data directory is absent. |

`apply`:

1. It reads its data from `/media/developer/apps/usr/palm/applications/org.webosbrew.openhouse/backport`
   (`OPENHOUSE_DATA` overrides it). With no directory it logs and exits 0.
2. If any target of any set is already a mount point in `/proc/mounts`, it logs and exits 0.
3. It hashes the live files with `sha256sum` and takes the first set in which every file matches.
   No set, or a missing file, means it logs, leaves the stock files and exits 0.
4. It copies the targets to `/tmp/openhouse-backport/stage/<path>` and runs `patch -p0` on each
   diff there. The first copy or patch failure drops the whole set: nothing is mounted, exit 1.
   Then, in each staged file that has a `configuredHomeAppId: "..."` line (only `Settings.qml`
   does; the diff carries the placeholder `@OPENHOUSE_HOME@`), it substitutes the stored choice:
   `/var/lib/webosbrew/openhouse/home`, trimmed of spaces, tabs, CR and LF, and used only if it
   is made of letters, digits, `.`, `-` and `_`; an absent, empty or invalid file (logged) gives
   an empty string. The substitution is after `patch`, so the diff stays byte-exact.
5. It runs `mount --bind` for each copy over its original. If one mount fails it unmounts the ones
   made and exits 1.
6. It restarts the compositor with `systemctl restart surface-manager-daemon.service`, the unit
   that runs `/usr/bin/surface-manager.sh` on all four releases (`surface-manager.service` is a
   oneshot wrapper that starts it). The compositor reads the QML and the key filter scripts again at
   start; there is no QML cache (`QML_DISABLE_DISK_CACHE=1`).

It logs to `/tmp/openhouse-backport.log`. `stop` is idempotent: with nothing mounted it does
nothing. The script, the manifest parser (the three keys are picked line by line, no `jq`) and the
`patch` flags use only busybox applets, which the images ship.

The configured id is read once, at boot. A choice made later reaches the compositor through the
settings record; `refresh` is what the app runs when the stock home is restored, so the removed
choice does not stay in the mounted copy until the next boot.

Tests (`sh` and `busybox sh`; the shell is the argument):

* `backport/test-openhouse-keyfilter.sh [shell]` runs the worker against a fake root with stubbed
  `mount`, `umount` and `systemctl`: a matching set, one hash mismatch, a missing target, a
  failing patch, a failing mount, an existing mount, the substitution of the stored choice
  (trailing newline, CRLF, surrounding spaces, empty and invalid ids), `refresh`, `stop`,
  `status`, no data directory and a bad argument.
* `backport/test-settings-diff.sh` rebuilds a stand-in for the stock `Settings.qml` from the diff
  (the stock file is not in the repository), applies the diff of each of the four releases with
  GNU and busybox `patch`, substitutes the placeholder, reverses the patch and runs `qmllint` on
  the added code.

## Install and remove from the app

The app shows the backport on webOS 6 to 9: whether it is enabled (the flag file), whether the
init.d link is there and points at this install, whether the targets are mounted now, and the set
that is mounted or the one that matches the live files (from `status`). The release gate is the
major version; the set is chosen by hash. The package carries `backport/` (`app/scripts/copy-backport.js`
runs between the build and `ares-package`).

Install, as root through Homebrew Channel's exec, each step in the step log:

1. `status`. It stops here, changing nothing, if the data is missing or no set matches the live
   files and nothing is mounted.
2. Read `general/defaultApps`. If the key is absent, write the empty record with
   `setSystemSettings`. Subscriptions that start on an absent key miss its creation (see the study's
   first-write caveat), and the compositor's subscription is of the same kind as `sam`'s.
3. Create the flag file and make `/var/lib/webosbrew/init.d/openhouse` a symlink to the entry in
   the app (see [Persistence](settings-app.md#persistence)).
4. Run `openhouse-keyfilter apply` once. The compositor restarts, so running apps may close.
5. `systemctl restart sam` if step 2 created the key.
6. `status` again; the install fails if the targets are not mounted.

After that a later choice reaches `sam` and the compositor live.

Remove: delete the flag file, then run the worker's `stop`, then read `status`. The flag is deleted
even if `stop` failed, so the next boot does not mount the files again. The init.d link stays, since
the watcher uses it while a choice is stored. The `defaultApps` record is left as it is.
