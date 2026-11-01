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
- Freeze launcher API v1 with permission `org.webosbrew.homechooser.applications`: `getInfo`, `listApps`, `listRunning`, `getAppInfo`, `getAppIcon`, `getForegroundApp`, `closeApp`, and `uninstallApp` with owner confirmation.
- Add to launcher API v1, without changing what existed: `iconColor` and `bgColor` in `listApps`; `listLaunchPoints` (SAM's launch points in LG's order, bookmarks included); `listInputs` (the TV's external inputs from the input manager, each with the `params` to launch it with and, on webOS 7 and later, `signal`); `getIcons` (up to 64 app, launch point and input icons in one call); and `subscribe: true` on the three lists, which sends the whole list again after every change. The client gains `subscribe()` with a cancel handle, and the mock simulated changes.
- On webOS 11 and later, provide the launcher methods in LG's `application.query` and `application.launcher` groups as well, which are the groups a developer app holds there, and restart Home Chooser's own service when that access changes. The service's check of `requiredPermissions` stays the gate on every release; the docs no longer say the bus enforces it.
- Provide launcher fixtures, an end-to-end runner, and offline service and app tests.
