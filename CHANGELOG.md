# Changelog

All notable changes to Home Chooser are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.2.0] - 2026-11-17

### Added

- webOS 4.5 (W19P, starfish 4.10.x): the Home button opens your launcher through the `general/defaultApps` record, as on webOS 5. LG ships two stock homes there, as on 5.x (see Changed).
- webOS 4.0 (W18R, starfish 4.4.x): the Home button opens your launcher. 4.0 has no Home app and no default-app record, so Home Chooser inserts a key filter entry of its own before LG's `handleSystemKeys` (through configd, volatile, read back, restored exactly; no restart) that opens the launcher on Home, keeps the long press for recent apps and closes open overlays first. Back on your launcher, when it does not take Back itself, is swallowed (a full-screen launcher) or goes to the last input (an overlay launcher), instead of showing LG's ribbon over it. The only LG Home row on 4.0 is "LG Home (Ribbon)".
- webOS 4.x has no systemd: the watcher and the boot entry now run as a detached loop with a pid file there (the commands `stopWatcher` and `watcherActive` go through the entry script, which asks systemd where it exists).
- The service revives a dead watcher: when it runs as root and a home choice is stored, any call it serves (at most one check per 30 s) starts the watcher again if it is not running, on systemd and on the detached loop of webOS 4.x alike. Restoring LG Home and uninstalling do not trigger it.

### Changed

- Home Chooser now supports webOS 4.0 to 12 (it was 5 to 12). webOS 3.x and older are not supported.
- The app is built for Chromium 53, the browser of webOS 4.x (`npm run check-es` checks the bundle against ES2016), so the panel runs on webOS 4.
- The release is read from `/etc/starfish-release` only; `/etc/os-release` is absent on webOS 3.x and 4.x and is not read.
- On webOS 4.5 and 5.x, where LG ships two stock homes, the list has "LG Home (Ribbon)" (`com.webos.app.home`, the Basic Home) and "LG Home (Fullscreen)" (`com.webos.app.fullhome`, the New Home); each is its own choice and the check mark follows the record's exact id. Before, one "LG Home (stock)" row stood for the ribbon only while restores wrote the full-screen home, so after a restore it was unchecked and a second row called "Home" was checked. On webOS 6 and later the one stock row is "LG Home (Fullscreen)". The dialog and toasts name the variant.
- Putting LG Home back (restore, a removed launcher, Home Chooser's uninstall) restores the stock home you used last on 4.5 and 5.x instead of always the full-screen one.

### Fixed

- On webOS 4.x the Uninstall button of the watcher's alert (a launcher or Home Chooser removed) failed: the 4.x firmware has no client permissions file for `com.webos.surfacemanager`, so the installer refused its remove call. The button now goes through Home Chooser's service (`uninstallConfirmed`, with a one-time token), which is allowed to the private group in its own API file on 4.x.
- Putting LG Home back on webOS 4.x no longer writes `defaultApps.home` as `null`: webOS 4.5 restores a stock home id, and webOS 4.0 never touches the record.
- A launcher installed (or removed) while Home Chooser's panel was open did not show in the list, even after Home Chooser was opened again. The list is now read again when the panel comes back to the front (opened again, or the page turns visible). An open confirmation, the focus and a choice in progress are left alone.
- On webOS 11 and later, if the panel was closed during first-time setup after the launcher access files were written but before the service restarted, the next open did not restart it, so launchers could stay refused. The next open now asks for the restart again.

## [0.1.2] - 2026-11-16

### Fixed

- On webOS 6 through 9 with an overlay launcher chosen while LG Home was still loaded, the launcher was closed within seconds of the first Home press: closing LG Home under the overlay emptied the foreground, and the home gate opened the last input over it. The close of LG Home now waits until a full-screen app other than LG Home is in front.

## [0.1.1] - 2026-11-13

### Added

- Native launchers can call the launcher API. A native app's calls carry no application id, so Home Chooser identifies a native launcher by the service name its own process registers, when that name is the id of an installed native Home app whose own executable is the only one allowed to register it. Other callers without an application id are still refused (`forbidden`).

## [0.1.0] - 2026-11-13

The first release.

### Added

- Choose a Home app on rooted webOS TVs from webOS 5 through 12, on 6 through 9 through a Home key filter that takes effect live, with no restart.
- Elevate the service through Homebrew Channel on every release; on webOS 11 and later this needs Homebrew Channel 0.8.0 or later.
- Persist the choice across boots; restore LG Home and notify the owner when a chosen launcher or Home Chooser is removed.
- Stop stock Home preloading while another Home is chosen, then restore the policy when LG Home returns.
- On webOS 6 through 9, keep stock LG Home from running while another Home is chosen: a home gate takes its place for every SAM launch and opens the chosen launcher, or the last input under an overlay launcher.
- On webOS 10 and later, open the chosen launcher when the last app closes (the last input under an overlay launcher) through the system profile's `idleApp`, and close LG Home once per boot and per choice after the launcher is in front, on webOS 10 as on 11 and later. The override is removed whenever the launcher goes missing, LG Home is chosen or Home Chooser is uninstalled.
- On webOS 10 and later, with an overlay launcher chosen, open the last TV input once after each boot when nothing full-screen is in front, so a boot ends on the last input instead of a blank screen (the launch closes the overlay; Home opens it again, as on 6–9) and SAM's return to the last input works from the start of the session.
- Document in the launcher guide that on webOS 11 and later only one overlay stays on screen (a native overlay launcher hides its window or exits when it launches an app) and that native launchers should set `noSplashOnLaunch`.
- Launcher API `apiVersion` is a feature level that only grows; a higher level keeps everything earlier levels documented, and launchers check `apiVersion >= N`, never `== 1`. Level 1 is the freeze below (tag `api-v1`); level 2, which every reply reports, is the addition in the next entry. The client has `supports(level)`.
- Freeze launcher API level 1 with permission `org.webosbrew.homechooser.applications`: `getInfo`, `listApps`, `listRunning`, `getAppInfo`, `getAppIcon`, `getForegroundApp`, `closeApp`, and `uninstallApp` with owner confirmation.
- Add launcher API level 2, without changing what existed: `iconColor` and `bgColor` in `listApps`; `listLaunchPoints` (SAM's launch points in LG's order, bookmarks included); `listInputs` (the TV's external inputs from the input manager, each with the `params` to launch it with and, on webOS 7 and later, `signal`); `getIcons` (up to 64 app, launch point and input icons in one call); and `subscribe: true` on the three lists, on `listRunning` and on `getForegroundApp` (the methods whose platform call can be subscribed to), which sends the whole reply again after every change. The client gains `subscribe()` with a cancel handle, and the mock simulated changes.
- Add launcher API level 3, without changing what existed: `getAppLifeEvents` (a stream of SAM's app life events, `launch`, `foreground`, `splash`, `stop` and `close`, sent one per reply with `subscribe: true` required and no debouncing, coalescing or caching); `systemApp` in `listApps` and `getAppInfo`; `mediumLargeIcon` and `extraLargeIcon` in `listApps` and `listLaunchPoints` and as `size` values of `getIcons`. The client reads the stream through `subscribe()`, and the mock emits sample events.
- On webOS 11 and later, provide the launcher methods in LG's `application.query` and `application.launcher` groups as well, which are the groups a developer app holds there, and restart Home Chooser's own service when that access changes. The service's check of `requiredPermissions` stays the gate on every release; the docs no longer say the bus enforces it.
- Provide launcher fixtures, an end-to-end runner, and offline service and app tests.
- Never close an app while Home Chooser's panel is on screen. A change of Home, LG Home included, leaves the launcher it replaces to the watcher, which closes it once the panel is gone and another full-screen app is in front, at most once per change; the stock Home closes on 6–9 and 10+ wait for the panel too. Before, closing the old launcher under the panel let SAM bring up its idle app (the last input under an overlay launcher) and the panel closed before its "Done" toast.
- On webOS 6 through 9, when a firmware update leaves the stock `systemUi.js` with a `launchHomeApp` Home Chooser does not recognise (or unreadable), Home opens LG Home: the home gate is no longer mounted while the Home key filter is stock, and returns with the redirect once the shape is known. After an update to webOS 10 or later, the generated key filters and the stock snapshot of the old install are removed.

[Unreleased]: https://github.com/webosbrew/home-chooser/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/webosbrew/home-chooser/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/webosbrew/home-chooser/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/webosbrew/home-chooser/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/webosbrew/home-chooser/releases/tag/v0.1.0
