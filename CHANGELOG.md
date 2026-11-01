# Changelog

All notable changes to Home Chooser are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

These changes are planned for 0.1.0.

### Added

- Choose a Home app on rooted webOS TVs from webOS 5 through 12, with a Home key hook on 6 through 9 and a restart prompt when the hook needs loading.
- Persist the choice across boots; restore LG Home and notify the owner when a chosen launcher or Home Chooser is removed.
- Stop stock Home preloading while another Home is chosen, then restore the policy when LG Home returns.
- On webOS 6 through 9, keep stock LG Home from running while another Home is chosen: a home gate takes its place for every SAM launch and opens the chosen launcher, or the last input under an overlay launcher.
- Freeze launcher API v1 with permission `org.webosbrew.homechooser.applications`: `getInfo`, `listApps`, `listRunning`, `getAppInfo`, `getAppIcon`, `getForegroundApp`, `closeApp`, and `uninstallApp` with owner confirmation.
- Provide launcher fixtures, an end-to-end runner, and offline service and app tests.
- Tell users on webOS 6 through 9 that turning the TV off with the remote may not restart it (Quick Start+ resumes instead), so the pending-restart note points at Restart now.
