# The Home key on webOS 3 and 4

A firmware study of why Home Chooser supports webOS 4.0 and 4.5 and not webOS 3.x or older, from
static reading of the firmware roots below and runtime checks on emulator VMs. `[V]` marks a string,
file or code path read directly (disassembly included); `[V, VM]` a behaviour observed at runtime on
an emulator VM of that release; `[I]` an interpretation. Paths are relative to each firmware's
`rootfs/`. The newer releases are in [the Home key and the home app](home-key-and-home-app.md).

| Firmware | Release (`/etc/starfish-release`) |
| --- | --- |
| `05.70.50.01-HE_DTV_W16R_AFAAABAA` | 3.4.3 |
| `06.10.65.01-HE_DTV_W17H_AFADABAA` | 3.9.3 |
| `05.50.70.01-HE_DTV_W18R_AFAAABAA` | 4.4.3 (webOS 4.0) |
| `05.40.97.01-HE_DTV_W19P_AFADABAA` | 4.10.2 (webOS 4.5) |

## Conclusion

* webOS 4.5 (4.10.x) takes the `general/defaultApps` record, like 5.x.
* webOS 4.0 (4.4.x) has no Home app and no record. A key filter entry of Home Chooser's own, inserted
  before LG's `handleSystemKeys`, makes the Home key open the launcher.
* webOS 3.x and older are not supported: there is no setting, filter or luna method that changes
  what Home does without restarting the compositor or reading the input devices.

## webOS 4.5: the record

* `usr/lib/qt5/qml/KeyFilters/systemUi.js` has `launchHomeApp(key, isNotLongPressed)`: a short press
  launches `settingsService.getLaunchDefaultApp` through `applicationManager.launch` with
  `{"launchType": "homeKey"}`; with no record it calls
  `applicationManager.launchDefaultApp("com.webos.app.home", {"category": "home"})`. A long press
  opens the most recent app (record `fullhome`) or `com.webos.app.home` with
  `{"activateType": "recent"}` (record `home`). [V]
* The record is `general/defaultApps`, described in `etc/palm/description.json`; the factory value in
  `etc/palm/defaultSettings.json` is `{"home": "com.webos.app.fullhome"}`. LG's Settings app
  (`usr/palm/applications/com.palm.app.settings/main.js`, "Home Style") writes `com.webos.app.fullhome`
  ("New Home") or `com.webos.app.home` ("Basic Home"). [V]
* Both apps are installed (`usr/palm/applications/com.webos.app.home` and `.fullhome`). [V]
* On the emulated 4.5, with `home` = `com.webos.app.home` the Home key makes SAM launch it (a ribbon
  over the screen underneath), and with `com.webos.app.fullhome` it launches that (full screen). [V, VM]

## webOS 4.0: a filter entry of our own

* There is no home app: `usr/palm/applications` has neither `com.webos.app.home` nor
  `com.webos.app.fullhome`. LG's Home is the compositor's `superRibbon`. `systemUi.js` has no
  `launchHomeApp`, no `HOME_APP_ID`, and `handleSystemKeys` calls `global.prepareToGoHome()`,
  `global.gotoRecents` (long press, 1000 ms) and `global.goHome()` directly. [V]
* The compositor reads `com.webos.surfacemanager.keyFilters` from configd, an array of
  `{"handler", "file"}` entries. `libWebOSCoreCompositor.so` (`WebOSKeyFilter`) builds one JS engine
  per distinct file, evaluates the file, calls `init()` if present, and for each key event calls the
  handlers in list order: result 0 is not accepted, 1 is accepted, anything else goes to the next
  entry. [V]
* A change of `keyFilters` reloads the filters: after a volatile `setConfigs` the new file was loaded in
  about 0.4 s, with no restart. [V, VM]
* The stock list (12 entries, `etc/configd/layers/base`) is the same on the VM. An entry inserted
  before `handleSystemKeys` runs after the screen saver, accessibility and magnifier handlers. [V, VM]
* From the filter, `applicationManager.launch` opens the launcher, the long press can be re-armed on
  LG's own timer so that recents still open, and open overlays can be closed first, as the stock
  `goHome` does. A filter that does nothing leaves the stock behaviour. [V, VM]
* Back: when the foreground app does not take Back, the release reaches `onBackReleased`, which calls
  `goHome()`, so Back opens LG's ribbon over such an app. The filter must handle Back for the chosen
  launcher. [V, VM]
* Restoring the saved list with a volatile `setConfigs` reads back exactly equal to the original, and
  the stock Home works again. [V, VM]
* The generator that serves 6.x to 9.x fingerprints `launchHomeApp`; no shape matches on 4.0, so
  nothing could be retargeted there. [V]

## webOS 3.x: why it is out of scope

* Home is QML compiled into `usr/bin/surface-manager-starfish` (a stub with the QML in its resources):
  `keyFilter.homePressed` starts the long-press timer and `homeReleased` calls `ysm.goHome()`. There
  is no key filter list to edit. [V]
* `etc/configd/layers/base/com.webos.surfacemanager.json` has no `keyFilters`, and configd reports
  the key as a missing config. [V, VM]
* SAM has no notion of a default or home app: no `defaultApp`, `launchDefault` or `idleApp` strings. [V]
* The compositor has no luna method that changes what Home does. Its `lifecycle` service has only
  `closeLRUSurface`, `closeByAppId`, `getForegroundAppInfo`, `getRecentsAppList`, `showInputPicker`,
  `showQuickSettings`, `getCurrentState`, `showUserAgreement` and `deleteMyChannels`. [V]
* The compositor reads `LSM_MAIN_QML` once, at start. Pointing it at a different main file needs a
  change to the upstart job and a compositor restart, and `lsm-ready.conf` restarts WebAppMgr and
  MaliitServer when the compositor restarts, which closes every web app, Home Chooser's panel
  included. [V]
* The only other route is to read the input devices and take the Home key before the compositor does
  (`libim` reads `/dev/input/event*` and uses `/dev/uinput`). The project excludes it: no raw key
  grab, only compositor-side routes. [V]

## `/etc/starfish-release`

* Present on every image from 1.4.0 to 12.0.0 that was checked, 3.x and 4.x included: a regular file of
  one line, `Rockhopper release MAJOR.MINOR.PATCH-BUILD (name)`, for example
  `Rockhopper release 4.4.3-22 (goldilocks-gorongosa)` and
  `Rockhopper release 1.4.0-2341 (afro-ashley)`. [V]
* `/etc/os-release` is absent on 1.4.0, 3.x and 4.x (the roots above) and present from 5.6.1. [V]
* Home Chooser therefore reads the release from `/etc/starfish-release` only.

## Stock homes by release

| Release | `com.webos.app.home` | `com.webos.app.fullhome` |
| --- | --- | --- |
| 1.4.0, 3.4.3, 3.9.3, 4.4.3 | absent | absent |
| 4.10.2 | present (Basic Home, ribbon) | present (New Home) |
| 5.6.1 | present | present |
| 6.6.0, 11.2.0 | present | absent |

`com.webos.app.fullhome` exists only on 4.5 and 5.x. Releases 7 to 11 are in
[the Home key and the home app](home-key-and-home-app.md); none of them has it.
