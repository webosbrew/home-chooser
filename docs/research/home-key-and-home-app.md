# The Home key and the home app, webOS 5 to 26

Read-only study, 2026-10-19, checked claim by claim against the same roots on the same day.
Firmware roots inspected:

| Name | Firmware | `os-release` |
| --- | --- | --- |
| W20L | `04.63.25.01-HE_DTV_W20L_AFAAJAAA` | webOS TV 5.6.1 |
| W21P | `03.70.25.01-HE_DTV_W21P_AFADATAA` | webOS TV 6.6.0 |
| W22P | `04.60.90.01-HE_DTV_W22P_AFADATAA` | webOS TV 7.6.0 (webOS 22) |
| W22H | `13.30.61.01` | 8.3.0 (webOS 23, `number1-nameri`) |
| W22H | `23.21.20.01` | 9.2.1 (webOS 24, `ombre-okapi`) |
| W22O | `33.31.61.01` | 10.3.1 (webOS 25, `ponytail-papikonda`) |
| W22H | `43.22.40.01-HE_DTV_W22H_AFABJAPU` | 11.2.0 (webOS 26, `queue-qilian`) |

The static reading used no VM. The tests under [Tests](#tests) were then run on emulator VMs the
same day; their results are marked `[V, VM]`. No real TV was used. Paths are relative to each
firmware's `rootfs/`. `[V]` marks a string, file or code path that was read directly
(disassembly included); `[I]` marks interpretation; `[U]` marks a question that needs a
runtime test, listed under [Tests](#tests).

`usr/lib/qt5` is a symlink to `.` on every release, so `usr/lib/qt5/qml` and `usr/lib/qml` are
one directory. `QML2_IMPORT_PATH=/usr/lib/qt5/qml` is set in
`etc/systemd/system.conf.d/30-webos-global.conf`. [V]

## Which app is the home app

| Release | Home app(s) | Selection |
| --- | --- | --- |
| 5.6.1 | `com.webos.app.home` (native, 3.0 MB QML) and `com.webos.app.fullhome` (75 MB) | `general/defaultApps`, factory `{"home": "com.webos.app.fullhome"}` [V] |
| 6.6.0 | `com.webos.app.home` (native, 65 MB) | hard-coded id; `defaultApps` not described [V] |
| 7.6.0 | `com.webos.app.home` (native, 78 MB) | hard-coded id; `defaultApps` not described [V] |
| 8.3.0, 9.2.1 | `com.webos.app.home` (native, 53 MB) | hard-coded id; `defaultApps` not described [V] |
| 10.3.1 | `com.webos.app.home` (Flutter 3.22.2, 48 MB) | `launchDefaultApp`: settings record, then system profile [V] |
| 11.2.0 | `com.webos.app.home` (Flutter 3.27.3, 98 MB) | as 10.3.1 [V] |

The Flutter versions are `flutter_framework_version` in
`usr/palm/applications/com.webos.app.home/data/flutter_assets/version.json`; the appinfo
`version` is `2.0.0` on 11.2.0. [V]

On 5.6.1 the two apps are the "Basic Home" and the "New Home" (FullHome). The Settings app
(`usr/palm/applications/com.palm.app.settings/main.js`) writes the choice as an ordinary
settings record [V]:

```js
latestHome: { category: "general", settings: { defaultApps: { home: "com.webos.app.fullhome" } } }
basicHome:  { category: "general", settings: { defaultApps: { home: "com.webos.app.home" } } }
```

It then force-closes the outgoing home app (`closeByAppId` with `forceMode: true`). The UI path is
*General > Home Settings > Home Style*, and the entry is only shown when
`getSystemSettingDesc.defaultApps.ui.visible` is true. [V]

On 6.6.0 through 10.3.1 the settings description (`etc/palm/description.json` and its `.bson`
and category-key-map copies) and the factory defaults have no `defaultApps` item, and the
settings app does not write it. On 10.3.1 the settings app only reads the system profile
(`getProfiles {"names":["seniorMode","defaultApps","lifeOnScreen"]}`). 11.2.0 describes
`defaultApps` again, but its factory value is `{"kidsApp": "com.webos.app.familycare"}`, with no
`home` entry. [V]

### Appinfo

| Field | 5.6.1 home | 6.6.0 - 9.2.1 home | 10.3.1, 11.2.0 home |
| --- | --- | --- | --- |
| `type`, `main` | native, `/usr/bin/com.webos.app.home` | same | `flutter`, the package directory |
| `appCategory` | `["home"]` | absent | `["home"]` |
| `defaultWindowType` | `home` | `home` | absent |
| `supportQuickStart` | `false` | `false` | `true` |
| `handleScreenRemoteKey` | absent (fullhome: `true`) | `true` | `true` |
| `requiredPermissions` | absent | absent | 14 groups, including `settings` and `applications.internal` |

All releases also have `visible: false`, `lockable: false`, `noSplashOnLaunch: true` and
`transparent: true`; 10.3.1 adds `class.hidden: true`. In 5.6.1 the two home apps are the only
apps with `appCategory`. On 6.6.0 through 9.2.1 no app has `appCategory: ["home"]`, and
`com.webos.app.home` is the only app with `defaultWindowType: "home"`. `handleScreenRemoteKey`
is on a dozen or more other apps, so it is not a home marker. [V]

The home category is not unique from 10.3.1. `com.webos.app.seniorhome` (10.3.1 and 11.2.0; same
Flutter bundle, `defaultWindowType: "home"`) and `com.webos.app.kidshome` (11.2.0,
`["home","kidsApp"]`) also declare it. [V]

`keepAliveApps` in `etc/configd/layers/base/com.webos.applicationManager.json` contains
`com.webos.app.home` from 5.6.1 to 10.3.1 (5.6.1 also `fullhome`; 10.3.1 also `seniorhome` and
`tnativehome`, which has no package in that image). 11.2.0 moves the list into the system
profile: a top-level `keepAliveApps.appIds` in each device `profile.json`, with
`com.webos.app.home` in 123 of 133. [V]

### Role and binary

Through 9.2.1 the native binary has a privileged LS2 role:
`usr/share/ls2/roles/{pub,prv}/com.webos.app.home.json`, `type: privileged`, `exeName`
`/usr/bin/com.webos.app.home`, `allowedNames` `""`, `com.webos.app.home` and (from 6.6.0)
`com.webos.media.client.*`. The binary hard-codes
`/usr/palm/applications/com.webos.app.home/qml/main.qml`. [V]

From 10.3.1 there is no `usr/bin/com.webos.app.home` and no `ls2/roles` file. The launcher runs
in `usr/bin/flutter-client` under an ordinary app role,
`usr/share/luna-service2/roles.d/com.webos.app.home.app.json` (`type: regular`, `allowedNames`
`com.webos.app.home-*`), and its groups come from
`client-permissions.d/com.webos.app.home.app.json`. [V]

## The key path

The remote's Home button never arrives as `KEY_HOME`. The LG xkb keymap maps it to Super_L [V]:

```
# usr/share/X11/xkb/keycodes/lg
<IR_KEY_HOME>    = 781;  // Qt::Key_Super_L=0x01000053
<IR_KEY_MYAPPS>  = 995;  // Qt::Key_Menu=0x01000055
<IR_KEY_RECENT>  =1016;  // Qt::Key_webOS_Recent=0x01200005
<IR_KEY_BACK>    = 420;  // Qt::Key_webOS_Back=0x01200003

# usr/share/X11/xkb/symbols/lg_vndr/remotes
key <IR_KEY_HOME>   { [ 0x01000053 ] }; // Qt::Key_Super_L
```

The values are the same on all seven releases.

`etc/configd/layers/base/com.webos.surfacemanager.json` lists the key policy scripts under
`keyFilters`; `StarfishKeyFilter.qml` loads them through `com.webos.service.config/getConfigs`.
The Home case is the `handleSystemKeys` handler of `/usr/lib/qt5/qml/KeyFilters/systemUi.js`,
which calls `handleHomeKeys` and then `launchHomeApp`. Device layers of the same config
(`devicename`, `tooltype`, `tvoverlay`, and on 11.2.0 `commercial`) add filters but use the
same script and handler. [V]

The keys grouped with Home in `handleHomeKeys` changed in 7.6.0:

* 5.6.1 and 6.6.0: `Qt.Key_Super_L`, `Qt.Key_Menu` (My Apps) and `WebOS.Key_webOS_Recent`;
  `getActivateType()` maps them to `home`, `myapps` and `recent`.
* 7.6.0 to 10.3.1: `Qt.Key_Super_L`, `Qt.Key_Meta` and `Qt.Key_Menu`. Recent has its own case
  in `handleSystemKeys` (`runFlashBack()`), and `Qt.Key_Meta` maps to `home`. [V]

`launchHomeApp()` returns early (`HOME_IGNORED`) unless `access.ribbon` and
`overlayView.access.ribbon` are set and `settingsService.disableHome` is not. It also returns
early during first use (`bootManager.bootFirstUse`), except in power-only mode
(`systemPropertyService.powerOnlyMode`). [V]

### 5.6.1

* Press on Super_L starts a 1000 ms timer. A release while it runs cancels it and calls
  `launchHomeApp(key, true)`, unless the home view already holds an app (`homeView.appId`).
* Short press: `launch(settingsService.getLaunchDefaultApp.home, {"launchType": "homeKey"})`.
  `getLaunchDefaultApp` is a subscription to `general/defaultApps` that defaults to
  `{"home": "com.webos.app.home"}` (`StarfishViewStateController.qml`), so the
  `launchDefaultApp` branch after it is only a fallback. No `activateType` is sent.
* The fallback's JS call `applicationManager.launchDefaultApp("com.webos.app.home",
  {"category":"home"})` sends only `{"category":"home"}` on the bus; the id is kept by the
  `libwebosserviceplugin.so` wrapper.
* Long press: if the default is `com.webos.app.fullhome`, launch the most recent app; if it is
  `com.webos.app.home`, relaunch it with `{"activateType": "recent"}`; otherwise nothing.
* The key is handed to the focused home window only when its `_WEBOS_ACCESS_POLICY_KEYS_HOME`
  property says so (`access.keyFilter.home`).
* `WebOSCompositor/views/StarfishHomeView.qml` `openHome()` calls
  `applicationManager/launchDefaultApp {"category":"home"}`. [V]

### 6.6.0 to 9.2.1

* Short press launches `com.webos.app.home` with `{"activateType": getActivateType(key),
  "launchType": "homeKey"}`. On 7.6.0, `checkLaunch` is used instead of `launch` when
  `configdService.supportNMRM` is set. There is no default-app lookup.
* `systemUi.js` declares `HOME_APP_ID = "com.webos.app.home"` and compares the fullscreen app
  with it: a release does not relaunch home when home is already in front, and voice and
  key-policy cases branch on it.
* `StarfishHomeView.openHome()` calls `applicationManager/launch {"id":"com.webos.app.home"}`.
* Long press: 1500 ms `runFlashBack` (switch to the most recent app) on 6.6.0, 8.3.0 and 9.2.1.
  7.6.0 instead toggles screen magnification (`magController`, 2000 ms, or 2500 ms with zoom
  on); the magnification code is only in the 7.6.0 and 10.3.1 trees, so this is a branch
  difference, not a progression.
* 7.6.0 first closes a visible special UI (`closeSpecialUI()`). [V]

The literal id is not confined to the key filter. On 9.2.1 it is also compared or used in
`WebOSCompositor/controllers/StarfishViewStateController.qml`,
`controllers/StarfishFullscreenController.qml`, `controllers/fullscreen/StarfishRecents.qml`,
`services/StarfishBaseLunaServiceAPI.qml`, `services/base/StarfishFocusedWindowInfoMgr.qml`,
`services/base/StarfishOverlayPlayVideoStateMgr.qml`, `views/StarfishNudgeView.qml`,
`views/StarfishGestureUIFeedbackView.qml`, `views/StarfishHomeView.qml` and
`KeyFilters/appLaunch.js`; 6.6.0, 7.6.0 and 8.3.0 have most of the same sites. [V]

### 10.3.1 and 11.2.0

| Release | Launcher | Home key target | Long press |
| --- | --- | --- | --- |
| 10.3.1 (webOS 25) | Flutter, `appCategory: ["home"]` | `launchDefaultApp {"category": "home", "params": ...}` | 2000/2500 ms magnification |
| 11.2.0 (webOS 26) | Flutter, `appCategory: ["home"]` | same payload, key filter in C++ | `magController` present; timing not read |

10.3.1 is the first release where the QML no longer names the launcher. `launchHomeApp()` calls
`applicationManager.launchDefaultApp({"category": "home", "params": {"activateType":
getActivateType(key), "launchType": "homeKey"}})`. The wrapper,
`StarfishViewStateController.qml` `launchDefaultApp(params)`, is a fire-and-forget
`LS.adhoc.call`; nothing reads the reply. `StarfishHomeView.openHome()` calls the same method.
Every literal-id site listed for 9.2.1 is replaced by `compositor.isHomeApp(appId)` or
`compositor.getHomeAppId()`, exposed to the key filter as `global.isHomeApp` and
`global.getHomeAppId`. [V] They are `StarfishCompositor` methods in
`usr/lib/plugins/compositor/libstarfishcompositorplugin.so`, which forwards to a native
interface method (`getHomeAppIdFunc`) whose implementation was not found. On 11.2.0,
`usr/lib/weston/starfish-servicebridge.so` (`WStarfishServiceBridge::getHomeAppId`) holds
`cbSystemProfile`, `defaultApps`, `categories` and `com.webos.app.home`. [V]

On both releases the compositor's home id does not follow the settings record. With a launcher
chosen through `setDefaultApp` in front, a Home press relaunched it (`launchDefaultApp` from
`com.webos.surfacemanager` on 10.3.1, `com.webos.starfish.keyfilters` on 11.2.0), which the
key filter only does when `isHomeApp(fullscreenView.appId)` is false. On 11.2.0 a press with the
stock `com.webos.app.home` in front launched nothing while the record named the test launcher.
[V, VM] The id is the system profile's `home`: with `defaultApps.categories.home` moved to the
launcher by a profile override (T6), the compositor treated it as home on both releases, live
after `systemprofile/debug/rescan` and with no compositor restart. [V, VM]

The 10.3.1 Super_L handling also changed: the press starts the magnification timer; a release
before it fires re-injects a synthetic Super_L (`isInternalHomeKey`), and that second pass
reaches `launchHomeApp()` when `!global.isHomeApp(fullscreenView.appId) && !homeView.appId`. If
the home view is visible, the key goes to it when `surfaceAcceptsHomeKey(appId)` (the window's
`_WEBOS_ACCESS_POLICY_KEYS_HOME`) is `"true"`; otherwise the home view is closed. [V]

Evdev `KEY_LEFTMETA` (125) from a generic keyboard or from `com.webos.service.networkinput`'s
uinput device (`LGE Network Input`) arrives as `Qt.Key_Meta`, not as the remote's IR Super_L. On
10.3.1 `handleHomeKeys` re-sends it to the focused window as an internal Super_L (`Send home key
instead of windows key to focused view`), and the release reaches the same
`!global.isHomeApp(fullscreenView.appId)` launch branch as a remote press. [V, VM] The IR remote's
Super_L press, which arms the long-press timer, was not exercised on a VM.

11.2.0 has no key filter scripts and no compositor QML at all. The config adds
`keyFiltersCpp`, naming `/usr/lib/libsystemUI.so`, `libappLaunch.so` and `libpreProcess.so`,
and the Weston module `usr/lib/weston/starfish-keyfilters.so` loads them.
`libsystemUI.so.1.0.0` (`cpp/KeyFilters/systemUI.cpp`) exports `handleHomeKeys` and
`launchHomeApp`. `launchHomeApp` builds
`{"category":"home","params":{"launchType":"homeKey","activateType":...}}` and passes it to a
virtual method of the host's key filter interface; `libsystemUI` contains neither the string
`launchDefaultApp` nor the id `com.webos.app.home`. The gating strings (`HOME_IGNORED`,
`disableHome`, `bootFirstUse`, the two `access.ribbon` checks) are present. [V] That the host
method is `launchDefaultApp` is [I].

Boot follows the same change. `etc/palm/bootd.json` `firstAppInNormal` is `{LAST_INPUT_APP}`
from 5.6.1 to 9.2.1 and `com.webos.app.home` on 10.3.1 and 11.2.0. `firstAppInHome` and
`firstAppInNmrm` (`com.webos.app.home`) exist only from 6.6.0 to 9.2.1. Through 9.2.1,
`etc/systemd/system/snapshot-boot-components.service` and `etc/init/com.webos.app.home.conf`
preload `com.webos.app.home` (also on 5.6.1, not fullhome); from 10.3.1 the snapshot unit starts
only `com.webos.app.inputcommon` and the upstart job is gone. [V]

### What the home app itself does with the key

The QML launcher handles `activateType` in `AppInitializer/AppLifeCycleController.qml` →
`UserInterfaceLayer/UIController.qml`. `home` and `myapps` put a hidden app into `main` mode;
`preload` keeps it hidden. When the launcher is already visible it receives Super_L itself and
treats release as `goBack()` (closes a sub-view or edit mode), so a second Home press walks back
rather than relaunching. [V]

## How `sam` resolves `launchDefaultApp`

`com.webos.applicationManager/launchDefaultApp` exists in `usr/sbin/sam` on every release. Its
schema, `etc/palm/schemas/sam/applicationManager.launchDefaultApp.schema`, is identical on all
seven: `category` is required and other fields are allowed. The handler
(`LifeCycleLunaAdapter::LaunchDefaultApp`) resolves the category to an id, writes it into the
request as `id` and passes the request to the ordinary `launch`, so `params` reach the app as
with `launch`. From 6.6.0 the categories `LastInputApp` and `idleApp` are special-cased to
`LaunchLastApp`. [V]

`ApplicationManager::getDefaultAppByCategory` [V]:

1. Look the category up in the `general/defaultApps` settings record. `sam` subscribes to it
   on every release (`{"key":"defaultApps"}` on 5.6.1, `{"keys":["defaultApps",
   "lastAppHandlerPolicy"]}` from 6.6.0), whether or not the key is described.
2. On 10.3.1 and 11.2.0 only, if the record has no entry, look it up in the system profile's
   `defaultApps.categories`. The settings record wins when both are set, and a record entry
   that fails step 3 does not fall back to the profile [V, VM]; the debug log says
   `"from":"settingservice"` or `"from":"systemprofile"`.
3. Accept the id only if that app is installed and its appinfo `appCategory` contains the
   category [V, VM on 5.6.1 to 11.2.0]. No visibility, window-type or system-app check is made
   there.

On failure the reply is `no default app is set for the category` (errorCode 1) whether the
category is unset, the app is missing, or the app lacks the category. `sam` never falls back to
a built-in id; none of the seven `sam` binaries contains `com.webos.app.home`. [V]

From 9.2.1 `sam` also has a setter, `applicationManager/setDefaultApp {"category", "appId"}`
(both required), which calls `ApplicationManager::updateDefaultApps` and writes to the settings
service (`Fail to update default apps to settings service`). Its ACG groups are `applications`
and `applications.internal`. 11.2.0 adds `getDefaultAppByCategory`, `closeDefaultApp` and
`pauseDefaultApp`. [V] It sets the category's entry in `general/defaultApps` and keeps the
others (9.2.1, 10.3.1, 11.2.0). 6.6.0 answers `Unknown method "setDefaultApp"`. [V, VM]

### The system profile on webOS 25 and 26

`com.webos.service.systemprofile/getProfiles` publishes a `defaultApps` profile. There is no
`etc/webos-profile` and no profile client in `sam` before 10.3.1. Each device layer in
`etc/webos-profile/platform/layers/00-device/<device>/profile.json` carries a section such as
[V]:

```json
"defaultApps": {
    "_packageIncluded": true,
    "_configuration": "defaultApps.json",
    "support": true,
    "categories": {
        "home": "com.webos.app.home",
        "aod": "com.webos.app.lifeonscreen"
    }
}
```

The device layers are not uniform. Of 81 device layers on 10.3.1, 72 have exactly this; the rest
have no `aod`, `com.webos.app.atelier` as `aod`, `com.webos.app.tnativehome` or
`com.webos.app.seniorhome` as `home`. 11.2.0 (133 layers) adds `com.webos.app.hotelhome` and
`com.webos.app.appliancehome`, and two layers with `support: false`. The dynamic profiles
`platform/dynamic/senior_normal` (10.3.1, 11.2.0) and `platform/dynamic/kidsMode` (11.2.0,
`home: com.webos.app.kidshome`) also set it, so the profile's home app already changes at
runtime through `systemprofile/enableProfile`. [V]

`etc/webos-profile/platform/configuration/defaultApps.json` is an empty package manifest; the
category map in `profile.json` is the effective one. [V]

`usr/sbin/systemprofile` runs `usr/sbin/profile_generator`, which merges the layers listed in
`etc/webos-profile/common/layers/platformLayers.json` (`00-device`, `10-broadcastCountry`,
`20-serviceCountry`, `99-combination`, and a writable `test` layer at
`/var/webos-profile/testLayer`) and applies `/var/webos-profile/override/`
(`override_profile()`). The service API is `getProfiles`, `enableProfile`, `disableProfile`
and `debug.rescan`, in the public `systemProfile` group; there is no method to set a key. [V]

The override [V on the binaries, V, VM for the `defaultApps` case]:

* Every file under `/var/webos-profile/override/` is read, whatever its name; its JSON is a
  partial profile, for example
  `{"defaultApps":{"support":true,"categories":{"home":"<id>","aod":"com.webos.app.lifeonscreen"}}}`.
* 10.3.1 only overrides top-level keys the profile already has, and replaces each second-level
  value whole, so the file must carry the full `categories` object. 11.2.0 deep-merges, and
  applies `/var/webos-profile/remote/` after `override/`.
* `/var/webos-profile/testLayer/profile.json` is an ordinary layer applied last; it beats both
  directories.
* `luna://com.webos.service.systemprofile/debug/rescan '{}'` regenerates
  `/var/webos-profile/platform/profile.json`; at start `systemprofile` skips regeneration when
  that file exists and its selectors are unchanged. `sam` and the compositor
  (`libStarfishServiceIntegration` on 10.3.1, `starfish-servicebridge.so` on 11.2.0) subscribe
  to `getProfiles` and saw a rescan live.
* Removing the file and rescanning restored the stock profile. Whether an override survives a
  reboot without a rescan was not run.

## What makes a window "home" to the compositor

`WebOSCompositor/views/StarfishHomeView.qml` registers a view for surfaces whose
`item.type === '_WEBOS_WINDOW_TYPE_HOME'`. Only the 5.6.1 native home binaries set that type.
From 6.6.0 to 9.2.1 `usr/bin/com.webos.app.home` sets `_WEBOS_WINDOW_TYPE_CARD`: the stock home
is a fullscreen app, which is why the key filter compares `fullscreenView.appId` with
`HOME_APP_ID`. All native home binaries set `_WEBOS_ACCESS_POLICY_KEYS_HOME`, `_KEYS_BACK` and
`_KEYS_EXIT` to `"true"` from `main()`; 9.2.1 also sets `_WEBOS_WINDOW_CLASS = "1"`. [V]

The compositor inverts the policy value. `StarfishAccessControlPolicy.qml` `policy_keyfilter()`
returns `!(value === "true")`, and an unset property means filtered. A filtered key is consumed
(`KeyPolicy.Accepted`), so `"true"` is what lets the launcher receive its own Home key. [V] The
11.2.0 C++ policy was not read.

`defaultWindowType: "home"` is still in the `ApplicationDescription` schema enum on every
release, but WebAppMgr (`libWebAppMgrCore.so` `windowTypeFromString`) has no `home` mapping, so
a web app cannot become a home-type window through appinfo. The compositor's
`StarfishSystemUIManager.qml` maps `home` to `_WEBOS_SYSTEM_UI_TYPE_HOME` for in-compositor
system UIs. `usr/bin/flutter-client` contains `home` next to `_WEBOS_WINDOW_TYPE_HOME` and
`_WEBOS_WINDOW_TYPE_CARD`, after `defaultWindowType` in its appinfo strings; that it maps
`defaultWindowType: "home"` to the home type and everything else to card is [I]. The Flutter
`libapp.so` contains the three `_WEBOS_ACCESS_POLICY_KEYS_*` names; their values were not read.
[V]

### Floating launchers and focus (webOS 6)

A web launcher with `defaultWindowType: "floating"` and `appCategory: ["home"]`, launched by
the Home key through the backport on 6.6.0, shows over the app in front;
`getForegroundAppInfo` keeps reporting that app. How the launcher hides itself decides where
the keys go afterwards [V, VM]:

| Hide | Keys afterwards |
| --- | --- |
| `webOSSystem.hide()` | still the hidden launcher (the app behind gets `blur` and nothing more) |
| `window.close()` | the app behind, which gets `focus` |
| launch the app behind, then `webOSSystem.hide()` | the app behind |
| `applicationManager/suspense` | `Unknown method`; nothing changes |
| `applicationManager/closeByAppId` from a developer app | `Denied method call`; nothing changes |

With the Open House backport on 6.6.0, a card launcher chosen as home was launched by Home
from another app (`caller com.webos.surfacemanager`, `launchType: "homeKey"`), received the
remote's keys, and with `_WEBOS_ACCESS_POLICY_KEYS_HOME = "true"` received a second Home press
as a key instead of being relaunched: the backport's `isHomeApp` follows the choice. On the
1.5 GB VM each card launch made `sam` kill the app in front (`memoryReclaim`) and relaunch it
later (`autoReload`). [V, VM]

Keys sent while such a floating window is up can reach no window at all: surface-manager logged
`keyPressEvent ... Focused none-group nor grabbed item` for arrows and Back sent to a floating
launcher on 6.6.0, while card launchers got the same keys [V, VM]. The Beanjar maintainers'
reading of the logs is that LG's surface-manager delivers keys only to surface items in a focus
group or grabbed, and the floating app had `windowGroup: false` [I].

A native overlay works the same way on 6.6.0, as checked by the Dock project on the same VM: an
SDL app with appinfo `defaultWindowType: "overlay"` and `transparent: true`, and the SDL hints
`SDL_HINT_WEBOS_WINDOW_GROUP_LAYER=dock`, `WINDOW_GROUP_OWNER=<app id>`,
`WINDOW_LAYER_NAME=dock` and `WINDOW_GROUP_TYPE=_WEBOS_WINDOW_TYPE_OVERLAY`, showed the card
app beneath through its transparent areas, took 5-way keys, and `getForegroundAppInfo` listed
both the card and the overlay. Homebrew Channel's `elevateService` elevated its native
developer service on that VM. [V, VM; reported by the Dock session, not rerun here]

This is AltHome's issue #4 (`webOSSystem.hide()` on webOS 6 leaves the layer holding focus).
AltHome's later webOS 6 path, `suspense`, is not a method on 6.6.0.

## Offering an app as home

### 5.6.1: supported, through the default-app record

1. Declare `appCategory: ["home"]`; `launchDefaultApp` requires it.
2. Write `general/defaultApps = {"home": "<app id>"}` through
   `com.webos.settingsservice/setSystemSettings`, then close the previous home app
   (`com.webos.applicationManager/closeByAppId` with `forceMode`), which is what the built-in
   Home Style switch does. The key filter launches the record's id directly with
   `{"launchType": "homeKey"}`.
3. Handle `launchType: "homeKey"` and the long-press `activateType: "recent"`.

### 6.6.0 to 9.2.1: no supported switch

The key filter and about ten compositor files name `com.webos.app.home`, and the stock home
window is a card. A root install has two options:

1. **Replace the home package.** Keep the id `com.webos.app.home` and replace the QML under
   `usr/palm/applications/com.webos.app.home/qml` (the binary loads `qml/main.qml`) or the whole
   package. The privileged role, keep-alive entry, boot hooks and every id comparison then
   apply unchanged.
2. **Make the id configurable**, as 10.3.1 did: see the mod map below.

`sam` already reads `general/defaultApps` and resolves `launchDefaultApp` through it, but the
stock home has no `appCategory` on these releases, so `launchDefaultApp {"category":"home"}`
fails for it.

### 10.3.1 and 11.2.0: default-app lookup

The platform looks the home app up by category, so the Home key launches the chosen launcher
with no change:

1. Ship a launcher with `appCategory: ["home"]`. The stock app's `requiredPermissions` are the
   reference.
2. Set `general/defaultApps.home` through `applicationManager/setDefaultApp` or
   `setSystemSettings`; it takes precedence over the device profile. The profile itself is
   read-only on the rootfs; an override file in `/var/webos-profile/override/` changes it.
3. Handle `activateType` and `launchType` as before.

`isHomeApp` follows the system profile, not the record (see [10.3.1 and
11.2.0](#1031-and-1120)). With the record alone the compositor treats the chosen launcher as
an ordinary app: each Home press relaunches it, and the recents, nudge, voice and gesture sites
do not see it as home. A profile override that names the launcher fixes that without a patch.

## Mod map for a custom home

Goal: a small settings app owns the choice, a launcher declares itself a home app, and the
platform resolves the choice. On every release the choice can live in the same place, the
`general/defaultApps` settings record, because `sam` reads it on all seven and prefers it over
the system profile on 10.3.1 and 11.2.0.

### Launcher contract (all releases)

* appinfo: `appCategory: ["home"]` (required by `sam`'s lookup), `visible: false`,
  `lockable: false`, `noSplashOnLaunch: true`, `transparent: true`.
* Window: a fullscreen (card) window, as the stock home from 6.6.0 on, with
  `_WEBOS_ACCESS_POLICY_KEYS_HOME = "true"` so a Home press while it is in front reaches it.
  Do not rely on `_WEBOS_WINDOW_TYPE_HOME`: from 6.6.0 the compositor's home view is not the
  stock home's path. [I] On 5.6.1 a card launcher is relaunched by each Home press
  (`caller com.webos.surfacemanager`) instead of receiving it [V, VM]. On 10.3.1 and 11.2.0
  a web launcher that sets the property receives the key and is also relaunched with
  `launchType: "homeKey"` [V, VM], so a launcher should treat that relaunch as a Home press
  and not as a fresh start.
* Handle `launchType: "homeKey"` and `activateType` (`home`, `myapps`, `recent`).
* Add the id to `keepAliveApps` if it should stay resident.

### Settings app

* Candidate list: `com.webos.applicationManager/listApps`, filtered on `appCategory`
  containing `home`. More than one stock app has it from 10.3.1 (`seniorhome`, `kidshome`).
* Writing the choice:

  | Release | Write | Status |
  | --- | --- | --- |
  | 5.6.1 | `setSystemSettings {"category":"general","settings":{"defaultApps":{...}}}` | described key [V] |
  | 6.6.0 - 8.3.0 | same | key not described, stored anyway on 6.6.0 [V, VM]; see the first-write caveat |
  | 9.2.1 - 11.2.0 | `applicationManager/setDefaultApp {"category":"home","appId":"<id>"}` | adds or replaces only `home`, keeps other entries [V, VM] |

  `defaultApps` is one object for all categories (11.2.0 ships `kidsApp` in it), so a
  `setSystemSettings` write has to carry the existing entries.
* First-write caveat (webOS 6): when the write creates `defaultApps`, `sam` does not see it;
  `launchDefaultApp` still answers `no default app is set for the category` until `sam`
  restarts, after which later writes reach it live. A second identical write does not help, and
  neither does changing the value and changing it back; settings service logs
  `SYSTEM_SETTING_CHANGED` for each write. [V, VM: first write and restart on 6.6.0 and 6.5.2,
  repeated writes on 6.5.2] A writer that creates the
  key has to restart `sam` once (`systemctl restart sam`). That `sam`'s subscription is left dead
  when it starts on an absent key is [I]. No luna method deletes the key again:
  `resetSystemSettings` answers `returnValue: true` and leaves it in place.
* Going back to stock: on 6.6.0 to 9.2.1 the stock home lacks `appCategory`, so set no `home`
  entry rather than `com.webos.app.home`, and let the key filter fall back.
* After the change, close the previous home with `closeByAppId {"forceMode": true}`, as the
  5.6.1 Home Style pane does.

### Permissions

The ACG groups (`usr/share/luna-service2/api-permissions.d`, `groups.d`) [V]:

| Method | Groups | `public` flag of the group |
| --- | --- | --- |
| settingsservice `setSystemSettings`, `setSystemSettingDesc` | `settings`; also `public` in `compat.api.json` | `settings`: true |
| settingsservice `getSystemSettings` | `settings`, `settings.read`, `public` | true |
| applicationManager `setDefaultApp` (9.2.1+) | `applications`, `applications.internal` | `applications`: true; `.internal`: false |
| applicationManager `launchDefaultApp` | `applications`, `applications.launch`, `applications.internal` | true / true / false |
| applicationManager `closeByAppId`, `listApps` | `applications.internal`, `private` | false |

Developer-mode callers get `devmodeGroups: ["ares.webos.cli", "public"]`
(`usr/share/luna-service2/devmode_certificate.json`). `appinstalld` validates a developer
app's `requiredPermissions` (`Not allowed permission is used`). Settings service also checks
`etc/palm/settingsservice.allow`, a per-key whitelist for some callers, which has no `general`
entry. The stock settings app (`com.palm.app.settings`) declares no `requiredPermissions`; its
`client-permissions.d` file grants it `private` and `public`, and `private` covers
`setSystemSettings`, `closeByAppId` and `listApps`. `allowed_groups.json` lists no `settings`
or `applications*` group for `dev` or `oem` on any release.

On 11.2.0 three developer apps declaring `settings`, `settings` + `applications` and
`settings` + `applications.internal` installed without error, but each got
`{"returnValue":false,"errorText":"Access denied"}` from settings service for
`getSystemSettings {"category":"general","keys":["defaultApps"]}`: the per-key whitelist
applies to them. [V, VM] Whether the `applicationManager` methods are granted to them is
[U] (T5); a root caller passes both checks.

Homebrew Channel's root exec is not open to an ordinary app on 11.2.0. A developer app without
`requiredPermissions` that called `luna://org.webosbrew.hbchannel.service/exec` got
`{"returnValue":false,"errorCode":-1,"errorText":"Denied method call \"exec\" for category
\"/\""}`; the service logged `LS_REQUIRES_SECURITY` (`Service security groups don't allow method
call.`) and the command did not run. The same call as root replied `uid=0(root)`. On 10.3.1 the
same app's call ran and wrote the record. [V, VM] Homebrew Channel is to be updated to allow
the call again; until then the writes on webOS 26 and later are tested with root `luna-send`.

### Key filter backport, 6.6.0 - 9.2.1

Backport the 10.3.1 shape, which has two parts, not one:

1. **A home id the whole compositor shares.** Add `getHomeAppId()` and `isHomeApp(appId)` to
   the compositor and to the key filter's `global`. The id is `general/defaultApps.home`
   (subscribed the way 5.6.1's `getLaunchDefaultApp` is, in `StarfishViewStateController.qml`),
   falling back to `com.webos.app.home` when unset or not installed; unlike 10.3.1, whose
   compositor id does not follow the record. Replace every literal-id
   and `HOME_APP_ID` site listed under [6.6.0 to 9.2.1](#660-to-921) with them; a site that is
   missed treats the custom launcher as an ordinary app (recents, nudges, voice, gestures,
   relaunch on Home).
2. **The launch.** In `launchHomeApp()` and `StarfishHomeView.openHome()`, either
   * call `launchDefaultApp {"category":"home","params":{...}}` as 10.3.1 does. The 10.3.1
     wrapper ignores the reply, so the fallback to `com.webos.app.home` needs a call with a
     reply callback; or
   * launch `getHomeAppId()` directly, which carries the fallback in the id itself.

   Keep 7.6.0's `checkLaunch` branch for `supportNMRM`.

The 10.3.1 sources are `StarfishKeyFilter.qml` (`launchDefaultApp`, `isHomeApp`,
`getHomeAppId`), `StarfishViewStateController.qml` `launchDefaultApp(params)` and
`systemUi.js` `launchHomeApp()`. There is one QML tree to patch.

### 10.3.1 and 11.2.0

No compositor patch is needed. Set the launcher in two places, both as root:

1. The settings record, through `setDefaultApp`, which `launchDefaultApp` reads first.
2. A system profile override in `/var/webos-profile/override/` naming the same id as
   `defaultApps.categories.home` (on 10.3.1 with the full `categories` object), followed by
   `systemprofile/debug/rescan`. This is the id `isHomeApp` and `getHomeAppId` return.

With only the record set, Home from an ordinary app launched the launcher, but with the
launcher in front a press delivered the key (`_KEYS_HOME = "true"`; keydown and keyup 1013) and
also relaunched it twice with `launchType: "homeKey"`; with the stock home in front, 11.2.0
launched nothing. With the override, on both releases, a press with the launcher in front only
delivered the key, and a press with the stock home in front launched the launcher. With the
override alone (no `home` in the record), `launchDefaultApp` and the Home key also resolved to
the launcher. [V, VM] Going back to stock is removing the file, a rescan and removing the
record's `home`.

### Boot alignment (optional)

If the custom home should own boot and not only the Home key: `etc/palm/bootd.json`
`firstAppInHome`/`firstAppInNmrm` (6.6.0 - 9.2.1) or `firstAppInNormal` (10.3.1+), the snapshot
preload in `etc/systemd/system/snapshot-boot-components.service` and
`etc/init/com.webos.app.home.conf` (through 9.2.1), and `keepAliveApps`.

### Deployment

The stock rootfs is SquashFS. Decided: the patch is applied at boot to copies of the stock
files and the copies are bind-mounted over the originals; the packed image is not modified.

* Open House ships diffs, not patched files, so no LG file is redistributed. Each diff is
  stored with the sha256 of the stock file it applies to.
* A boot script in Homebrew Channel's `/var/lib/webosbrew/init.d` hashes each target file,
  copies the files whose hash it knows, applies the diffs to the copies and bind-mounts each
  copy over its original.
* A changed hash (a firmware update) means the file is not patched and stock behaviour stays.
  The id sites span several files, so a set that cannot be applied completely is not mounted
  at all.
* The compositor then has to read the patched files again (see below). Uninstalling is not
  mounting; nothing on the rootfs changes.

What this works with on 6.6.0 to 9.2.1 [V]:

* Tools: `usr/bin/patch`, `diff` and `sha256sum` are busybox applets (`bin/busybox.nosuid`);
  `bin/mount` is util-linux with `--bind`.
* No QML cache: `etc/systemd/system.conf.d/80-starfish-global.conf` sets
  `QML_DISABLE_DISK_CACHE=1`, and there are no `.qmlc`/`.jsc` files and no compiled-in QML
  resources for the compositor. `StarfishKeyFilterShell.js` loads each key filter script by
  its path (`Qt.include(Qt.resolvedUrl(file))`), from the `keyFilters` list in
  `etc/configd/layers/base/com.webos.surfacemanager.json`.
* The list differs by device layer: `devicename/m16ppez` uses `easytv.js` without
  `preProcess.js`, `tvoverlay` (tunerless) uses `preProcess_Monitor.js` and, on 9.2.1,
  `systemUi_monitor.js`; 6.6.0 has no `preProcess.js`. A patch set has to be keyed by the file
  (and its stock hash), not by the release.
* The compositor is `surface-manager-daemon.service` (`/usr/bin/surface-manager.sh`), started
  from `default-webos.target` (6.6.0 to 8.3.0) or `multi-user.target` (9.2.1). It reads
  `keyFilters` at start and again when that config value changes.
* Homebrew Channel's `startup.sh` runs `run-parts /var/lib/webosbrew/init.d` once per boot,
  unjailed, from the developer-mode start script at `minimal-boot-done`, and itself already
  bind-mounts `/etc/hosts` and others. That is after the compositor has started [I], so a
  mount made there takes effect after `surface-manager-daemon` restarts or `keyFilters` is
  re-read. The stock `run-exscript-cbd` core-boot-done hook is not enabled (and absent on
  9.2.1).

## Tests

Each test runs as root on a booted firmware unless it says otherwise; `<id>` is an installed
test launcher with `appCategory: ["home"]`. All replies are to be recorded per release.

* **T1, undescribed write (6.6.0 - 8.3.0).**
  `luna-send -n 1 -f luna://com.webos.settingsservice/setSystemSettings
  '{"category":"general","settings":{"defaultApps":{"home":"<id>"}}}'`, then
  `getSystemSettings '{"category":"general","keys":["defaultApps"]}'`, then the same `get`
  with `"subscribe":true` across a second write. `returnValue: true` and the value read back
  prove the key is stored without a description; an `errorKey` reply means
  `setSystemSettingDesc` is needed (register the 5.6.1 record from `etc/palm/description.json`,
  `dbtype: "G"` and `dimension: []` included, and repeat).
* **T1b, first write on 6.6.0.** On a VM where `general/defaultApps` has never been written,
  write `home` = `<id>`, run T3 (expect the error seen), write the same value again without
  restarting `sam`, and run T3 again. Success shows a second write is enough; failure means
  a writer must restart `sam` once after creating the key.
* **T2, setter (9.2.1 - 11.2.0).** `applicationManager/setDefaultApp
  '{"category":"home","appId":"<id>"}'`, then `getSystemSettings` as in T1. Shows what it
  writes and whether it keeps other categories.
* **T3, resolution.** After T1 or T2, `applicationManager/launchDefaultApp
  '{"category":"home","params":{"launchType":"homeKey"}}'`. `<id>` in front proves the
  lookup; repeat with an app lacking `appCategory: ["home"]` and expect
  `no default app is set for the category`. On 10.3.1 and 11.2.0 this also confirms that the
  settings record beats the profile.
* **T4, Home key and isHomeApp (10.3.1, 11.2.0).** After T2, press Home (`sendkey` or the
  test input path) from an ordinary app, then again with `<id>` in front. The first press
  should bring `<id>`; the second should reach `<id>` (if its `_KEYS_HOME` is `"true"`) and
  not relaunch it. A relaunch or a stock home shows that `isHomeApp` does not follow the
  default app.
* **T5, permissions.** Install developer apps with `requiredPermissions` of `["settings"]`,
  `["settings","applications"]` and `["settings","applications.internal"]`. Record install
  errors (`WRONG_PERMISSION`), then from each app call `setSystemSettings` (T1 payload),
  `setDefaultApp`, `launchDefaultApp`, `listApps` and `closeByAppId`, each independently. A
  `Denied method call` marks a group that is not granted; an `Access denied` from settings
  service marks the key whitelist.
* **T6, profile override (10.3.1, 11.2.0).** Place a `defaultApps.categories.home` override
  under `/var/webos-profile/override/` (and separately under `/var/webos-profile/testLayer/`),
  call `systemprofile/debug/rescan` or restart `systemprofile`, and read
  `getProfiles '{"names":["defaultApps"]}'` and the `[override profile]` log lines. Then
  repeat T4: if the compositor's `isHomeApp` follows the profile, a launcher set in both
  places is home to the compositor without a patch. (Run with the override directory; the
  test layer was not used.)
* **T7, card launcher on 5.6.1.** With `<id>` as a card window set as default, press Home
  with `<id>` in front and record whether it is relaunched or receives the key.

### Results, 2026-10-19

Run on Beanjar VMs on the Raspberry Pi 5, one at a time, as root through the VM's root shell,
with a web test launcher (`appCategory: ["home"]`) and a control app without the category. Home
was injected as QEMU `send-key meta_l` and, on 10.3.1 and 11.2.0, also as
`com.webos.service.networkinput/sendSpecialKey {"key":"HOME","delay":0,"rcu":"true"}`; both
arrive as evdev 125. The 11.2.0 VM runs `43.21.89.01` (W22O); T1b ran on a W21A VM with 6.5.2
(`03.53.25.01`); the other VMs run the firmwares in the table at the top. 7.6.0 and 8.3.0 were
not run.

| Test | 5.6.1 | 6.x | 9.2.1 | 10.3.1 | 11.2.0 |
| --- | --- | --- | --- | --- | --- |
| T1 write | stored (described) | stored, no description (6.6.0) | stored; live subscribers notified | - | - |
| T1b first write | - | not seen by `sam` after a second write or a value change; seen after a `sam` restart (6.5.2) | - | - | - |
| T2 `setDefaultApp` | - | unknown method (6.6.0) | `home` set, others kept | same | same |
| T3 chosen app | launched | launched after a `sam` restart (6.6.0, 6.5.2) | launched | launched | launched |
| T3 app without category | refused | refused (6.6.0) | refused | refused | refused |
| T3 no `home` entry | - | - | refused (no profile) | profile's `com.webos.app.home` | same |
| T4 Home from another app | - | - | - | chosen launcher launched | same |
| T4 Home with launcher in front | - | - | - | key delivered and launcher relaunched | same (`_KEYS_HOME` set); no relaunch logged with it unset |
| T4 Home with stock home in front | - | - | - | - | nothing launched |
| T6 override, rescan | - | - | - | `getProfiles` shows the launcher | same |
| T6 Home from another app or stock home | - | - | - | launcher launched | same |
| T6 Home with launcher in front | - | - | - | key delivered, no relaunch (also with record set) | same |
| T5 developer app | - | - | - | - | installs; `Access denied` from settings service; `applicationManager` replies not captured |
| hbchannel `exec` from an app | - | - | - | ran, record written | `Denied method call "exec"`; root call works |
| T7 card launcher | relaunched per press | - | - | - | - |

"Refused" is `{"errorCode":1,"errorText":"no default app is set for the category"}`.

An earlier 10.3.1 attempt, in which the injected key reached the compositor and nothing
launched, did not reproduce.

The 11.2.0 VM's record does not survive a reboot as written. After `setSystemSettings` restored
`{"MembershipApp":...,"kidsApp":...}` and read it back, the next boot answered with an older
`home` entry back (11 s after start), and 60 s later with `{"MembershipApp":...}` only. In
other boots the old `home` stayed. At boot `applicationManager` wrote `defaultApps` with only
`MembershipApp`, and `accountmanager` called `setDefaultApp` for `MembershipApp`. The old id
was still in the settings LevelDB (`/var/db/main/*.ldb`) after `/var/luna/preferences/general`
had dropped it. [V, VM] `getDefaultAppByCategory` is an unknown method on this `43.21.89.01`
VM.

Why entries are lost [V, 11.2.0 `sam` and `accountmanager`; same `sam` structure on 9.2.1 and
10.3.1]: `setDefaultApp`'s only writer, `ApplicationManager::updateDefaultApps`, copies `sam`'s
in-memory map, sets the one category and writes the whole object with `setSystemSettings`. The
map is filled only by `sam`'s `getSystemSettings` subscription (`OnDefaultAppsChanged`), and
nothing makes the write wait for the first reply. `sam` has no other `defaultApps` writer.
`accountmanager` calls `setDefaultApp` for `MembershipApp` 12 to 17 s into every boot of the
VM, although its `.accountmanager/data.dat` exists on the persistent `cmn_data` image (checked
in the Beanjar logs and image); the static reading, which tied the call to a missing
`data.dat`, missed the path that makes it. [V, VM] Whether that call wipes the other entries is
a race: in one boot it was queued until settings service connected; in another the full record
was there at 18.6 s and only `MembershipApp` at 19.4 s. [V, VM] The call is made at every start of
`accountmanager` on 9.2.1, 10.3.1 and 11.2.0: `LsHandler::start` calls
`PolicyManager::checkingAccountList`, which with an existing account database still ends in
`setDefaultApp {"category":"MembershipApp","appId":...}` (`com.webos.app.overlaymembership` when
`multipleLoginList` is non-empty, else `com.webos.app.membership`); 9.2.1 makes it synchronously.
5.6.1 to 8.3.0 have no `setDefaultApp` caller (and no `setDefaultApp` in `sam`). [V] So on 9.2.1
and later a chosen `home` in the record can be lost at any boot; on 10.3.1 and later the profile
override still names it.

The old `home` returning is not a lost write: the stored object keeps every sub-key ever
written. On 10.3.1, `setSystemSettings` of `defaultApps` with an extra sub-key and then without
it read back, and showed in `/var/luna/preferences/general`, as a replace; after `systemctl
restart settings-service.service` the extra sub-key was back, and so was a `home` written in an
earlier run. Writing the sub-key as `null` or `""` stores that value; `resetSystemSettings` for
`defaultApps` changes nothing. [V, VM] So no `setSystemSettings` write removes `home`, and what
settings service serves until its next start differs from what it stores. The service also has
`deleteSystemSettings` (in `api-permissions.d/com.webos.settingsservice.api.json`) [V]; for
`{"category":"general","keys":["defaultApps"]}` it is a no-op: it answers `returnValue: true`
and the key and its sub-keys stay, also after a restart. [V, VM] What does work is overwriting:
with `"home": null` in the record, `launchDefaultApp {"category":"home"}` launched the profile's
`com.webos.app.home` on 10.3.1, live and after a settings-service restart, the same as with no
`home` entry. [V, VM] db8 writes are synchronous (`"sync": 1` in `etc/palm/db8`, the settings
kind has `"sync": true`) and no boot restored from backup [V].

Open after this run: whether `"home": null` reads as unset on 11.2.0 as on 10.3.1; whether a `home` set through `setDefaultApp` and a profile override survive reboots on 11.2.0;
T5 on the `applicationManager` methods; 7.6.0 and 8.3.0 (`w22p-mullet`, `w22o-number1`); the IR
remote's Super_L long-press path, which no VM injection exercises.
