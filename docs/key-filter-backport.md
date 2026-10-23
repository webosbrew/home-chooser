# The key filter backport, webOS 6.5.2 to 9.2.4

On 10.3.1 the compositor and the Home key filter take the home app from `compositor.getHomeAppId()`
and `compositor.isHomeApp()`. On 6.5.2 to 9.2.4 they name `com.webos.app.home` in the source. The
backport gives those releases the same two functions, fed by the `general/defaultApps` record that
the settings app writes, and replaces every stock-id site in the compositor QML and the key filter
scripts with them. The facts behind it are in the
[study](research/home-key-and-home-app.md#key-filter-backport-660---921); this page says what was
built. Paths are relative to each firmware's `rootfs/`.

The patch is shipped as diffs in [`backport/`](../backport), one directory per release, and applied
at boot by `backport/homechooser-keyfilter`, which the init.d entry `backport/homechooser` runs (see
[Deployment](#deployment) and [Persistence](settings-app.md#persistence)).

## Coverage

A set is chosen by the sha256 of its target files, so each set covers exactly the builds listed
for it. "VM" means the set was installed and the Home key checked on a beanjar VM of that build;
"static" means it was checked only against the unpacked firmware (every hash matches, `patch -F0`
applies and reverses to the stock bytes, no stock-id literal is left except the `Settings.qml`
fallback, `qmllint` passes, and `backport/test-real-roundtrip.sh` passes) because no VM exists
for the build.

| Build | Model(s) | webOS | Set dir | Verified |
| --- | --- | --- | --- | --- |
| 03.53.25.01 | W21A | 6.5.2-43 | `backport/6.5.2` | static (VM run recorded below when done) |
| 03.70.25.01, 03.60.55.01 | W21P, W21U | 6.6.0-6, 6.6.0-12 | `backport/6.6.0` | VM |
| 04.60.90.01, 04.60.85.01 | W22L, W22P, W22A, W22H | 7.6.0-19, 7.6.0-18 | `backport/7.6.0` | VM |
| 13.30.56.01, 13.30.61.01 | W22O, W22H | 8.3.0-2301, 8.3.0-2401 | `backport/8.3.0` | VM |
| 03.30.18.01 | S23Z (monitor) | 8.3.0-1063 | `backport/8.3.0-s23z` | static |
| 13.40.90.01, 13.41.12.01, 13.41.13.01, 13.40.76.01 | W23A, W23H, W23P, W23O | 8.4.0 | `backport/8.4.0` | static |
| 23.21.20.01 | W22H | 9.2.1-40 | `backport/9.2.1` | VM |
| 33.20.23.01 | W24P, W24G | 9.2.4-4603 | `backport/9.2.4` | static (VM run recorded below when done) |

A set that is only statically verified carries `"verified": "static"` in its `manifest.json`; the
worker ignores the field. The 9.2.4 `systemUi.js` diff is not the 9.2.1 one: 9.2.4 added a fifth
stock-id comparison in the Home key handler, under the new screen-magnification branch
(`if (fullscreenView.appId !== HOME_APP_ID) launchHomeApp(key)`), which the 9.2.1 diff (it deletes
`var HOME_APP_ID`) would leave as a ReferenceError; the set replaces it with
`!global.isHomeApp(fullscreenView.appId)`. The 8.4.0 `StarfishViewStateController.qml` diff also
converts two new `showSpinnerOnRelaunch ... "com.webos.app.home"` comparisons (spinner on relaunch
and foreground of the home app), and 8.4.0 gets the 9.2.1 `StarfishGestureUIFeedbackView.qml` diff.

Not supported, by evidence from the coverage check: 6.3.1-1065 (W21Z projector) and 7.4.0-190508
(W22O) have an older Home-key path. `Key_Super_L` is `if (!pressed && !homeView.appId)
launchHomeApp(key)`, with no `fullscreenView.appId === HOME_APP_ID` comparison in `systemUi.js`
(one stock-id site on 6.3.1, two on 7.4.0), and `StarfishBaseLunaServiceAPI.qml` and
`StarfishNudgeView.qml` have no stock-id site, so the 6.6.0 and 7.6.0 hunks for `systemUi.js`,
`StarfishBaseLunaServiceAPI.qml` and `StarfishNudgeView.qml` all reject (7.4.0 also rejects
`StarfishFullscreenController.qml`). Whether replacing the launch site alone would be enough
depends on the stock `homeView.appId`, a runtime question nobody has answered.

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
  (`/var/lib/webosbrew/homechooser/home`) as the boot worker wrote it into the patched copy, so the
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
is chosen by hash, not by version: 6.5.2 and 6.6.0 are nine files, 7.6.0, 8.3.0 and 8.3.0-s23z
eleven, 8.4.0 twelve, 9.2.1 and 9.2.4 thirteen. The directory name is only the label `status`
prints as `match=` and `release=`; an optional `"verified"` key is ignored.

`backport/homechooser-keyfilter` is a busybox `sh` script, the worker. It is not in init.d itself:
the entry `/var/lib/webosbrew/init.d/homechooser`, a symlink to `backport/homechooser` in the installed
app, runs it at boot on webOS 6 to 9 when the backport is enabled (the flag file
`/var/lib/webosbrew/homechooser/keyfilter` exists). The names have no dot because `run-parts` skips
names that have one.

| Command | Effect |
| --- | --- |
| no argument, or `apply` | Find the set, patch copies, bind-mount them, restart the compositor. |
| `apply samfirst` | Restart `sam` (`systemctl restart sam`), then as `apply`, even when the backport is already mounted. The settings app asks for it on webOS 6 to 8 when its first write created `general/defaultApps` (`sam` misses that write until it restarts); doing it inside this one run keeps it from depending on a panel that the restart closes. |
| `stop` | Unmount every target of every set it finds, restart the compositor. |
| `refresh [stock] [norestart]` | If mounted, write the stored choice into the mounted copy of `Settings.qml` again (in place, so the bind mount keeps working) and restart the compositor when the id changed. With `stock`, write an empty id whatever is stored: the watcher does that while the chosen app is not installed. With `norestart`, update the copy and leave the compositor running: the watcher does that when it sees the stored choice change (see below). |
| `status` | Print `mounted=yes` and `release=<set>`, or `mounted=no` and `match=<set or none>`; `data=missing` first if the data directory is absent. |

`apply`:

1. It reads its data from `/media/developer/apps/usr/palm/applications/org.webosbrew.homechooser/backport`
   (`HOMECHOOSER_DATA` overrides it). With no directory it logs and exits 0.
2. If any target of any set is already a mount point in `/proc/mounts`, it logs and exits 0.
3. It hashes the live files with `sha256sum` and takes the first set in which every file matches.
   No set, or a missing file, means it logs, leaves the stock files and exits 0.
4. It copies the targets to `/tmp/homechooser-backport/stage/<path>` and runs `patch -p0` on each
   diff there. The first copy or patch failure drops the whole set: nothing is mounted, exit 1.
   Then, in each staged file that has a `configuredHomeAppId: "..."` line (only `Settings.qml`
   does; the diff carries the placeholder `@HOMECHOOSER_HOME@`), it substitutes the stored choice:
   `/var/lib/webosbrew/homechooser/home`, trimmed of spaces, tabs, CR and LF, and used only if it
   is made of letters, digits, `.`, `-` and `_`; an absent, empty or invalid file (logged) gives
   an empty string. The substitution is after `patch`, so the diff stays byte-exact. A stored
   choice is baked in only if `applicationManager/getAppInfo` says the app is installed: an
   uninstalled app, or a `sam` that has not answered after 20 s (`HOMECHOOSER_SAM_WAIT`, asked every
   2 s), bakes an empty id, which the key filter reads as the stock home, and the watcher
   refreshes it once it can tell. `refresh` makes the same decision.
5. It runs `mount --bind` for each copy over its original. If one mount fails it unmounts the ones
   made and exits 1.
6. It restarts the compositor with `systemctl restart surface-manager-daemon.service`, the unit
   that runs `/usr/bin/surface-manager.sh` on all four releases (`surface-manager.service` is a
   oneshot wrapper that starts it). The compositor reads the QML and the key filter scripts again at
   start; there is no QML cache (`QML_DISABLE_DISK_CACHE=1`).

It logs to `/tmp/homechooser-backport.log`. `stop` is idempotent: with nothing mounted it does
nothing. The script, the manifest parser (the three keys are picked line by line, no `jq`) and the
`patch` flags use only busybox applets, which the images ship.

The configured id is read once, at boot, and the loaded QML keeps it until the compositor restarts.
A choice made later reaches the compositor through the settings record, which `getHomeAppId()`
reads first, so the baked id is only the fallback for the next start. When the watcher sees the
stored choice change and the app is installed, it has already brought the record to the new
choice and runs `refresh norestart`: the mounted copy gets the new id (same inode) and the
compositor is not restarted. (Before this, the copy kept the old id until the watcher restarted.)
`refresh` without `norestart`, and `refresh stock`, still restart on a change; `refresh` is what the app runs when the stock home is restored, so the removed
choice does not stay in the mounted copy until the next boot.

Tests (`sh` and `busybox sh`; the shell is the argument):

* `backport/test-homechooser-keyfilter.sh [shell]` runs the worker against a fake root with stubbed
  `mount`, `umount` and `systemctl`: a matching set, one hash mismatch, a missing target, a
  failing patch, a failing mount, an existing mount, the substitution of the stored choice
  (trailing newline, CRLF, surrounding spaces, empty and invalid ids), `refresh`, `stop`,
  `status`, no data directory and a bad argument, and the baked id for an installed, uninstalled
  and silent `sam` (stubbed `luna-send`), `refresh stock` and `refresh` with the app gone, and
  `refresh norestart`.
* `backport/test-real-roundtrip.sh [shell] [release=rootfs ...]` runs the worker against the
  real stock files of every set (the firmware roots are listed in the script; a set whose root is
  not on the machine is skipped with a notice, and `OPENHOUSE_S23Z_ROOTFS` points at an unpacked
  S23Z image; other roots can be given as `release=rootfs` arguments), with only `mount`,
  `umount`, `systemctl` and `luna-send` stubbed: the set is found by hash, every target is
  patched and mounted, the baked id follows the installed state, the patched copies reverse-patch
  to the stock bytes, `stop` unmounts, and the stock files are unchanged.
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

Install, as root through Home Chooser's service ([service](service.md)), each step in the step log:

1. `status`. It stops here, changing nothing, if the data is missing or no set matches the live
   files and nothing is mounted.
2. Read `general/defaultApps`. If the key is absent, write the empty record with
   `setSystemSettings`. Subscriptions that start on an absent key miss its creation (see the study's
   first-write caveat), and the compositor's subscription is of the same kind as `sam`'s.
3. Create the flag file and make `/var/lib/webosbrew/init.d/homechooser` a symlink to the entry in
   the app (see [Persistence](settings-app.md#persistence)).
4. Run `homechooser-keyfilter apply` once. The compositor restarts, so running apps may close.
5. `systemctl restart sam` if step 2 created the key.
6. `status` again; the install fails if the targets are not mounted.

After that a later choice reaches `sam` and the compositor live.

Remove: delete the flag file, then run the worker's `stop`, then read `status`. The flag is deleted
even if `stop` failed, so the next boot does not mount the files again. The init.d link stays, since
the watcher uses it while a choice is stored. The `defaultApps` record is left as it is.
