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
[Deployment](#deployment) and [Persistence](settings-app.md#persistence)). The app never mounts
the patch or restarts the compositor while the TV runs: it enables the backport and asks for a
reboot ([Install and remove from the app](#install-and-remove-from-the-app)).

## Coverage

A set is chosen by the sha256 of its target files, so each set covers exactly the builds listed
for it. "VM" means the set was installed and the Home key checked on a beanjar VM of that build;
"static" means it was checked only against the unpacked firmware (every hash matches, `patch -F0`
applies and reverses to the stock bytes, no stock-id literal is left except the `Settings.qml`
fallback, `qmllint` passes, and `backport/test-real-roundtrip.sh` passes) because no VM exists
for the build.

| Build | Model(s) | webOS | Set dir | Verified |
| --- | --- | --- | --- | --- |
| 03.53.25.01 | W21A | 6.5.2-43 | `backport/6.5.2` | VM: w21a-kisscurl, 2026-10-23 E2E with reboot, 66 PASS (the one FAIL was a runner over-assertion, since fixed) |
| 03.70.25.01, 03.60.55.01 | W21P, W21U | 6.6.0-6, 6.6.0-12 | `backport/6.6.0` | VM |
| 04.60.90.01, 04.60.85.01 | W22L, W22P, W22A, W22H | 7.6.0-19, 7.6.0-18 | `backport/7.6.0` | VM |
| 13.30.56.01, 13.30.61.01 | W22O, W22H | 8.3.0-2301, 8.3.0-2401 | `backport/8.3.0` | VM |
| 03.30.18.01 | S23Z (monitor) | 8.3.0-1063 | `backport/8.3.0-s23z` | static |
| 13.40.90.01, 13.41.12.01, 13.41.13.01, 13.40.76.01 | W23A, W23H, W23P, W23O | 8.4.0 | `backport/8.4.0` | static |
| 23.21.20.01 | W22H | 9.2.1-40 | `backport/9.2.1` | VM |
| 33.20.23.01 | W24P, W24G | 9.2.4-4603 | `backport/9.2.4` | VM: w24g-ombre, 2026-10-23 E2E with reboot, 65/65 |

A set that is only statically verified carries `"verified": "static"` in its `manifest.json`; the
worker ignores the field. The 9.2.4 `systemUi.js` diff is not the 9.2.1 one: 9.2.4 added a fifth
stock-id comparison in the Home key handler, under the new screen-magnification branch
(`if (fullscreenView.appId !== HOME_APP_ID) launchHomeApp(key)`), which the 9.2.1 diff (it deletes
`var HOME_APP_ID`) would leave as a ReferenceError; the set replaces it with
`!global.isHomeApp(fullscreenView.appId)`. The 8.4.0 `StarfishViewStateController.qml` diff also
converts two new `showSpinnerOnRelaunch ... "com.webos.app.home"` comparisons (spinner on relaunch
and foreground of the home app), and 8.4.0 gets the 9.2.1 `StarfishGestureUIFeedbackView.qml` diff.

No reviewed set, by evidence from the coverage check: 6.3.1-1065 (W21Z projector) and 7.4.0-190508
(W22O) have an older Home-key path. `Key_Super_L` is `if (!pressed && !homeView.appId)
launchHomeApp(key)`, with no `fullscreenView.appId === HOME_APP_ID` comparison in `systemUi.js`
(one stock-id site on 6.3.1, two on 7.4.0), and `StarfishBaseLunaServiceAPI.qml` and
`StarfishNudgeView.qml` have no stock-id site, so the 6.6.0 and 7.6.0 hunks for `systemUi.js`,
`StarfishBaseLunaServiceAPI.qml` and `StarfishNudgeView.qml` all reject (7.4.0 also rejects
`StarfishFullscreenController.qml`). Whether replacing the launch site alone would be enough
depends on the stock `homeView.appId`, a runtime question not established by offline generation. The generated fallback accepts these
stock layouts, but that does not establish Home-key behavior on a device.

## Generated fallback

Reviewed hash-keyed sets always come first. On webOS 6 to 9 with no matching set,
`homechooser-keyfilter-gen.js` can rewrite copies of the live stock files. It uses the same
`Settings.getHomeAppId`, `isHomeApp` and `launchHome` behavior as the reviewed sets; it replaces
stock-id comparisons, launches and values and exposes the helpers to the key filter. The worker
bakes the stored choice into the copies, bind-mounts them and restarts the compositor through the
same flow, at boot. Refresh, stop and boot reapplication use the generated manifest in
`/tmp/homechooser-backport/generated/manifest.json`. No stock file is written. If stop cannot unmount a target, it reports failure and keeps the
stage and manifest so status and a later stop can still find it.

This fallback is opt-in. When generation is possible, the app offers **Use a patch generated for
this firmware (not reviewed)**. The switch writes
`/var/lib/webosbrew/homechooser/keyfilter-generated`; the ordinary `keyfilter` enable flag is
still required for boot application. Switching on installs immediately if a choice is stored,
otherwise the next choice installs it. Switching off removes the opt-in flag, disables the
backport and unmounts it. Uninstall cleanup removes both flags. Generation passing is structural
evidence only; the footer continues to identify a generated patch as not reviewed.

The generator runs on the TV's Node (`node`, falling back to `/usr/bin/node`), using APIs and
syntax compatible with Node 8.12. It reads and writes latin1 so unchanged bytes survive exactly.
Before writing any output it requires:

* webOS major 6 through 9, all three required files (`systemUi.js`, `Settings.qml`,
  `StarfishKeyFilter.qml`), and no target already calling `isHomeApp` or `getHomeAppId`;
* exact counts for anchored rules and no residual stock-id literal or `HOME_APP_ID`, except the
  single `stockHomeAppId` fallback in `Settings.qml`;
* no stock id elsewhere in `WebOSCompositorBase`, imports of `WebOSCompositorBase` for QML using
  the new `Settings` helpers, changed bytes in every target, unchanged bracket balance and
  exactly one `@HOMECHOOSER_HOME@` placeholder in `Settings.qml`.

Discovery scans QML/JS in `WebOSCompositor`, `KeyFilters` and `WebOSCompositorBase/global` for
stock-id references, plus the required files; it checks the rest of `WebOSCompositorBase` for
stray references. Any failed guard aborts the whole run (exit 2), leaving stock behavior.
Usage or I/O errors (exit 1) also prevent mounts. Bracket balance is a guard, not a QML parser.

When unmounted with no reviewed match, `status` reports `generated=possible` or
`generated=refused` with `reason=...`, and `optin=yes|no`. It caches the check in the worker's
work directory by SHA256 of the generator, OS release and every QML/JS file in the discovery and
guard trees (paths included). New files and changed bytes invalidate the key; an unchanged key
reuses the result without starting Node. Apply always runs generation afresh. While a generated
patch is mounted, status reports `mounted=yes`, `release=generated`, `match=generated` and a
`hash=<sha256> <absolute target path>` line for each staged file, including the baked choice.

For an offline lab check and a candidate reviewed set, run:

```sh
node backport/homechooser-keyfilter-gen.js check --root /path/to/rootfs
node backport/homechooser-keyfilter-gen.js lab-set --root /path/to/rootfs --release 6.x.y --out /path/to/candidate-set
sh backport/test-generated.sh
```

`lab-set` writes `manifest.json` and unified diffs in the reviewed-set layout, using the same
rules as on-device generation. Review those diffs and test the Home key before adding the set.
Every rule change must keep generation byte-identical to every reviewed set with an available
firmware root; `test-generated.sh` compares exact target trees, checks determinism, refusal of
already-patched and out-of-range firmware, and the `lab-set` round trip.

For a known VM to exercise the unknown-build flow, create
`/var/lib/webosbrew/homechooser/debug-no-sets` (or set `HOMECHOOSER_NO_SETS=1` for a worker run).
This suppresses reviewed-set lookup without bypassing generation guards or opt-in. Remove the
marker after the check. It is a root debug aid, outside the service's file allow-list.

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
| no argument, or `apply` | Find the set, patch copies, bind-mount them, restart the compositor. This is the boot path (the init.d entry runs it); the app does not call it, so it is otherwise for manual and debug use. |
| `apply samfirst` | Restart `sam` (`systemctl restart sam`), then as `apply`, even when the backport is already mounted. The settings app asks for it on webOS 6 to 8 when its first write created `general/defaultApps` (`sam` misses that write until it restarts); doing it inside this one run keeps it from depending on a panel that the restart closes. |
| `stop` | Unmount every target of every set it finds, restart the compositor. Debug and manual use only; neither the app nor the watcher calls it. |
| `refresh [stock] [restart]` | If mounted, write the stored choice into the mounted copy of `Settings.qml` again (in place, so the bind mount keeps working). It never restarts the compositor unless `restart` is given (debug). With `stock`, write an empty id whatever is stored: the watcher does that while the chosen app is not installed. `norestart` is still accepted and does nothing. |
| `status` | Print `mounted=yes` and `release=<set>`, or `mounted=no` and `match=<set or none>`; `data=missing` first if the data directory is absent. Always ends with `reboot=needed` or `reboot=no` (below). |

`status` says `reboot=needed` when the flag file (`/var/lib/webosbrew/homechooser/keyfilter`,
`HOMECHOOSER_KEYFILTER_FLAG` overrides it) exists, nothing is mounted, and a reviewed set matches
or the generated patch is possible (`generated=possible`) and opted in; or when the flag file is
absent and something is still mounted. In the first case the next boot will mount the patch, in the
second the next boot will not. Otherwise it says `reboot=no`.

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
6. It restarts the compositor (only `apply` and `stop`, and `refresh restart`, ever do), `surface-manager-daemon.service`, the unit that runs
   `/usr/bin/surface-manager.sh` on every release (`surface-manager.service` is a oneshot wrapper
   that starts it), with the compositor's clients stopped first and started again after it; see
   [Restarting the compositor](#restarting-the-compositor). The compositor reads the QML and the
   key filter scripts again at start; there is no QML cache (`QML_DISABLE_DISK_CACHE=1`).

It logs to `/tmp/homechooser-backport.log`. `stop` is idempotent: with nothing mounted it does
nothing. The script, the manifest parser (the three keys are picked line by line, no `jq`) and the
`patch` flags use only busybox applets, which the images ship.

The configured id is read once, at compositor start, and the loaded QML keeps it until the
compositor starts again. A choice made later reaches the compositor through the settings record,
which `getHomeAppId()` reads first (`general/defaultApps`, verified in every set and in the
generator), so the baked id is only the fallback for the next start. Nothing restarts the compositor
to change it: when the watcher sees the stored choice change and the app is installed, it has
already brought the record to the new choice and runs `refresh`, and the mounted copy gets the new
id (same inode). `refresh stock` (the chosen app is not installed) and the app's `refresh` when the
stock home is restored do the same. Each logs `configured home updated; the running compositor keeps
its loaded id until the next start (the settings record is read first)`.

If the chosen app is uninstalled, the running compositor keeps its stale baked id until a reboot, but
it reads the record first. The watcher therefore writes `defaultApps.home` as the stock home id
(`com.webos.app.fullhome` if installed, else `com.webos.app.home`, the same resolution as webOS 5),
not null, so the Home key opens LG Home at once; the `launchHome` `returnValue === false` branch
(present in every set and in the generator) stays as a second line of defence. The watcher's alert
says only that the Home button opens LG Home again.

### Restarting the compositor

Only the boot `apply` restarts the compositor, plus `stop` and `refresh restart` for manual and
debug use; the app and the watcher do not. At boot the restart is needed: the init.d hook runs
after the compositor has started (see [Why the boot hook runs after the compositor](#why-the-boot-hook-runs-after-the-compositor)),
so the patched files are mounted over a compositor that has already read the stock ones. A
A bare
`systemctl restart surface-manager-daemon.service` crashes WebAppMgr: measured on a 9.2.1 VM with
no Home Chooser installed (`scratch/wam-crash/notes.md`), the running WebAppMgr exits on "Broken
pipe" when the compositor goes, `Restart=on-failure` starts a new one while the new compositor is
still starting (pid 5635 was up at 193.74 s, the compositor ready at 194.05 s), and that one often
dies with SIGSEGV in the stock `libcbe.so` (fault address `0xc`, a null `wl_output`). The first of
three restarts crashed `com.webos.app.inputcommon` instead.

The firmware's units, read from the rootfs of 6.5.2, 6.6.0, 7.6.0, 8.3.0, 8.4.0, 9.2.1 and 9.2.4
(the roots in `backport/test-real-roundtrip.sh`; the 8.3.0-s23z root was not on hand). The
effective content is the same on all of them: up to 8.4.0 the units are in `lib/systemd/system`
with drop-ins in `surface-manager*.service.d/override.conf`, and 9.x folds those drop-ins into
`etc/systemd/system`. `webapp-mgr.service` is in `etc/systemd/system` everywhere. systemd is 239
(6.x), 244 (7.x, 8.x) and 250 (9.x).

* `surface-manager.service`: `Type=oneshot`, `RemainAfterExit=yes`,
  `ExecStart=/bin/systemctl start surface-manager-daemon.service`, `ExecStop=` the matching stop.
* `surface-manager-daemon.service`: `Type=notify`, `ExecStart=/usr/bin/surface-manager.sh`,
  `Restart=on-failure`, `TimeoutStopSec=5`, no start timeout of its own (`DefaultTimeoutStartSec`
  is the stock 90 s). No unit depends on it except the wrapper.
* Ready: the compositor (`WebOSCoreCompositor::emitLsmReady`, `libWebOSCoreCompositor.so`) runs
  `/sbin/initctl emit --no-wait lsm-ready`. `/sbin/initctl` is LG's upstart shim; on the 9.2.1 VM
  its log, `/var/log/initctl.log`, shows each emit as `TOUCH : lsm-ready`,
  `Exec : /bin/touch /tmp/lsm-ready`, then `NOTIFY : surface-manager` with the compositor's pid,
  i.e. the daemon's `READY=1` (`sd_pid_notify`; the compositor binaries have no `sd_notify` of
  their own). The shim's table has the same entry on every release. So
  `systemctl restart surface-manager-daemon.service` returns once the new compositor has
  emitted lsm-ready (the VM log: restart at 192.36 s, emit at 194.05 s, the worker's "restarted"
  in the same second). `/tmp/lsm-ready` itself is never removed, so it is no signal of a restart.
* Bound to the wrapper, `Requires=` and `After=surface-manager.service`: `webapp-mgr.service`
  (also `ConditionPathExists=/tmp/lsm-ready`; pulled in by `webos-ibd.target`), `booster.service`,
  `maliit-server.service`, `preload-manager.service`, and on 9.x `chromecast-provisioning.service`.
  None of them is bound to the daemon, so a restart of the daemon alone touches none of them.
* `lsm-ready.path` (`PathExists=/tmp/lsm-ready`, `Unit=lsm-ready.target`, wanted by
  `default-webos.target` up to 8.4.0 and `multi-user.target` on 9.x) starts `lsm-ready.target`,
  which `Wants=lsm-ready.service`. `lsm-ready.service` (`ConditionPathExists=/tmp/boot-done`,
  `Requires=` and `BindsTo=surface-manager.service`, `Type=oneshot`, `RemainAfterExit=yes`) runs
  `systemctl stop` then `start` of `webapp-mgr.service`, `booster.service` and
  `maliit-server.service`, in that order (`lsm-ready-factory.service` is the same for a factory
  boot). That is the firmware's list of what to restart once the compositor is ready. The path
  unit fires only while its target is inactive, so a later lsm-ready does not run it again.
* `surface-manager.sh`: when `/tmp/lsm-respawned` exists (the starfish platform and compositor
  plugins refer to it) it SIGKILLs the processes matching `"@system_native_app"` before it starts
  the compositor, so native apps are not left behind on a respawn.

`systemctl restart surface-manager.service` would order everything (systemd try-restarts the
units that `Requires=` or `BindsTo=` it, stops before the wrapper stops and starts after it is
active again), but it also restarts `preload-manager.service`, which forgets the watcher's
non-permanent `setPreloadPolicy` for the stock home, and `chromecast-provisioning.service` on 9.x.
So the worker does that ordering itself, for the units the firmware names:

1. Read the dependants from `lsm-ready.service`: its `ExecStart=/bin/systemctl start <unit>`
   lines, `etc/systemd/system` before `lib/systemd/system` as systemd reads them, else
   `webapp-mgr.service booster.service maliit-server.service` (`HOMECHOOSER_DEPENDANTS`
   overrides the list for tests).
2. `systemctl stop` each of them that `systemctl is-active` reports `active`, `activating` or
   `reloading`, in that order. One that is not running is not started either.
3. `systemctl restart surface-manager-daemon.service` (`HOMECHOOSER_UNIT`), which returns with
   the compositor ready.
4. `systemctl start` the same units in the same order, also if a step before failed. A failure
   is logged and makes the exit status 1.

The sequence runs in a child process that the worker waits for, with stdin, stdout and stderr on
a file under `/tmp/homechooser-backport` (copied to the worker's output afterwards) and SIGHUP,
SIGINT and SIGPIPE ignored. Home Chooser's service reads the worker's output through pipes and
SIGKILLs it at its timeout (90 s, 150 s for `samfirst`; a compositor that never becomes ready
holds `systemctl restart` for the 90 s start timeout), and when the app runs it, the stop of
`webapp-mgr` closes the app. The worker is in the service's process tree, not WebAppMgr's, and
the child goes on after the worker or its reader is gone, so the stop and the start are never
separated. Stopping WebAppMgr closes every web app; with the bare restart they were lost to the
crash too.

### Why the boot hook runs after the compositor

Measured on the 9.2.1 VM (`scratch/wam-crash/saved/20261024T005314Z-stop/`: `dmesg.txt`,
`var-log/initctl.log`, `tmp-logs/homechooser-boot.log`):

* systemd starts `surface-manager-daemon.service` at about 6.1 s of monotonic time ("Starting webos
  - surface-manager-daemon.service" at 6.13 s, next to `mount-readwrite.service` at 6.11 s). `/var`
  is not there yet: `mount-readwrite` mounts the entries of its fstab at about 6.2 to 6.45 s (the
  bind entries at 6.43 s) and emits `mount-readwrite-done` at 7.08 s. The compositor emits
  `lsm-ready` and the unit counts as started at 10.0 s.
* Homebrew Channel's `startup.sh`, which runs the init.d entries, is started from `devmode.service`
  in `webos-mbd.target`, after `minimal-boot-done`. The hook therefore always runs after the
  compositor has started, on every boot (`homechooser-boot.log` shows it applying the set after
  that point).
* The compositor unit's only input that a third party can write is
  `EnvironmentFile=-/var/systemd/system/env/surface-manager.env`, and it is read before `/var`
  exists. `main.qml` imports its modules by absolute `file:///` URLs. So there is no hook that runs
  before the compositor, short of editing LG's files (the unit, `surface-manager.sh` or the QML
  directories), which the backport does not do.

That is why the app does not mount live: a mount over a running compositor does nothing until it
restarts, and a live restart closes every web app. The app writes the flag and the init.d entry,
then asks the user to reboot; the boot hook does the mount and the restart, before the user sees
anything, with the firmware-order sequence above.

Restoring LG Home, disabling the generated patch, and uninstalling Home Chooser follow the same
rule: the flag is removed, the baked id is emptied in the mounted copy, nothing is unmounted and
nothing restarts. The bind mounts stay until the next boot, when nothing re-applies them (the flag
is gone). Their sources are the staged copies under `/tmp/homechooser-backport/stage`, not files
in the app directory, so they survive an uninstall (the tests assert this). The record is not
left unset: on webOS 6 to 9 restore and uninstall write `defaultApps.home` as the stock home id
(not null, which would make the running compositor fall back to its old baked id), so the live
patch opens LG Home immediately. The explicit stock id stays in the record after an uninstall; stock
firmware ignores the record for the Home key. webOS 5 writes the stock id too; 10 and later write
null (docs/research/home-key-and-home-app.md).

A Quick Start+ power-off is a suspend, not a reboot (inferred, not measured), so it does not run
the boot hook; that is why the app offers a real reboot (`com.webos.service.tvpower/power/reboot`).

Tests (`sh` and `busybox sh`; the shell is the argument):

* `backport/test-homechooser-keyfilter.sh [shell]` runs the worker against a fake root with stubbed
  `mount`, `umount` and `systemctl`: a matching set, one hash mismatch, a missing target, a
  failing patch, a failing mount, an existing mount, the substitution of the stored choice
  (trailing newline, CRLF, surrounding spaces, empty and invalid ids), `refresh`, `stop`,
  `status`, no data directory and a bad argument, and the baked id for an installed, uninstalled
  and silent `sam` (stubbed `luna-send`), `refresh stock` and `refresh` with the app gone,
  `refresh` and `refresh stock` never restarting (no `systemctl` call), `refresh restart`,
  `norestart` as a no-op, `status` `reboot=needed` and `reboot=no` in every case, the mount
  sources being under the work directory and not the data directory (reviewed and generated), and
  the exact `systemctl` sequence of a compositor restart: the
  dependants from a fake `lsm-ready.service` (`etc` over `lib`, the default list), a client that
  is not running, failed stops, starts and restarts, and a worker SIGKILLed with its output pipe
  closed during the restart.
* `backport/test-real-roundtrip.sh [shell] [release=rootfs ...]` runs the worker against the
  real stock files of every set (the firmware roots are listed in the script; a set whose root is
  not on the machine is skipped with a notice; `OPENHOUSE_FIRMWARES` and `OPENHOUSE_HYPERVISOR`
  move the two directories they are under, and `OPENHOUSE_S23Z_ROOTFS` points at an unpacked
  S23Z image; other roots can be given as `release=rootfs` arguments), with only `mount`,
  `umount`, `systemctl` and `luna-send` stubbed: the set is found by hash, every target is
  patched and mounted from sources under the work directory, the baked id follows the installed
  state without any restart, the patched copies reverse-patch to the stock bytes, `stop` unmounts,
  the stock files are unchanged, and every compositor restart (`apply`, `stop`, `refresh restart`)
  is the stop, restart, start sequence with the clients read from that firmware's own
  `lsm-ready.service`.
* `backport/test-settings-diff.sh` rebuilds a stand-in for the stock `Settings.qml` from the diff
  (the stock file is not in the repository), applies the diff of each of the four releases with
  GNU and busybox `patch`, substitutes the placeholder, reverses the patch and runs `qmllint` on
  the added code.

## Install and remove from the app

The app shows the backport on webOS 6 to 9: whether it is enabled (the flag file), whether the
init.d link is there and points at this install, whether the targets are mounted now, whether a
reboot is pending (`reboot=` from `status`), and the set that is mounted or the one that matches
the live files. The release gate is the major version; the set is chosen by hash. The package
carries `backport/` (`app/scripts/copy-backport.js` runs between the build and `ares-package`).

Install, as root through Home Chooser's service ([service](service.md)), each step in the step log:

1. `status`. It stops here, changing nothing, if the data is missing or no set matches the live
   files and nothing is mounted, unless generation is possible and opted in.
2. Read `general/defaultApps`. If the key is absent, write the empty record with
   `setSystemSettings`. Subscriptions that start on an absent key miss its creation (see the study's
   first-write caveat), and the compositor's subscription is of the same kind as `sam`'s.
3. Create the flag file and make `/var/lib/webosbrew/init.d/homechooser` a symlink to the entry in
   the app (see [Persistence](settings-app.md#persistence)).
4. `status` again. Nothing is mounted and nothing is restarted: `reboot=needed` is the expected
   answer, and the app asks the user to reboot (Reboot or Later). The patch is mounted by the
   boot hook on the next boot.

Until that reboot the launcher choice still works, through the stock key filter and the settings
record where the stock code reads it; the Home key changes after the reboot.

Remove (restore LG Home, or disable the generated patch): delete the flag file (and the generated
opt-in where that is what was asked), run `refresh` (it bakes the empty id into the mounted copy
when no choice is stored), read `status`, and ask for a reboot (`reboot=needed` while something is
still mounted). Nothing is unmounted and the compositor is not restarted. The init.d link stays,
since the watcher uses it while a choice is stored. The app's restore writes `defaultApps.home` as the stock id before this (webOS 6 to 9), so the
running compositor, which reads the record first, opens LG Home at once; the reboot only removes the
patch.

Uninstalling Home Chooser is handled by the watcher in the same way: no `stop`, no restart; the
record gets the stock home id (webOS 6 to 9), so the toast says the Home button opens LG Home again;
the mounts go at the next reboot.
