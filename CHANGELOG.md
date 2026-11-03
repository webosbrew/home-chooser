# Changelog

All notable changes to Home Chooser are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

These changes are planned for 0.1.0.

### Added

- Choose a Home app on rooted webOS TVs from webOS 5 through 12, on 6 through 9 through a Home key filter that takes effect live, with no restart.
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
