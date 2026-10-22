# End-to-end test

`homechooser_e2e.py` runs one scenario on one Beanjar VM and records every assertion with its raw
evidence. Python 3 standard library only; the device is reached with the `ares-*` tools by device
name (never raw ssh).

```
python3 -I e2e/homechooser_e2e.py --list
python3 -I e2e/homechooser_e2e.py --vm w22h-ombre            # or --vm 9.x
```

Options: `--launcher <id>` (default `org.webosbrew.dock`) and `--launcher-ipk <path>`; `--build`
(`npm run package` first; otherwise the newest `build/ipk/*.ipk` is used); `--host-mode
auto|local|remote` and `--host-device` (see below); `--elevation luna|root-shell`; `--input
qmp|ares`; `--viewer-shots`; `--until N` (stop after step N, no cleanup, for debugging);
`--no-stop`. Artefacts go to `e2e/results/<vm>-<timestamp>/` (git-ignored): `run.log`,
`results.json`, numbered screenshots (taken inside the guest with `ares-do screenshot`).

Before booting, the runner stops if any VM is running. It adds the `hbchannel` layer and
`link=lan` while the VM is off (standing permission), boots it, creates or fixes the
`<vm>-root` ares device (root, port 6622, the VM's root key fetched from the host) and updates a
changed LAN address. It stops the VM at the end (not `w21p-kisscurl`).

## The matrix

| Release | VM | Elevation | How sure |
| --- | --- | --- | --- |
| 5.x | `w20l-jhericurl` | luna | docs: W20L `04.63.25.01` is 5.6.1 in the study |
| 6.x | `w21p-kisscurl` | luna | docs: W21P `03.70.25.01` is 6.6.0. Owned by the Dock project: ask first. Dock must be left installed and selected with Home Chooser installed; the runner does not restore that yet |
| 7.x | `w22p-mullet` | luna | docs: W22P `04.60.90.01` is 7.6.0 |
| 8.x | `w22o-number1` | luna | listing only: webOS 23, `13.30.56.01`; the study names it as the 8.3.0 VM but its os-release was not read |
| 9.x | `w22h-ombre` | luna | verified: 9.2.1 (os-release read by the runner) |
| 10.x | `w22o-ponytail-us` | luna | docs: W22O `33.31.61.01` is 10.3.1 |
| 11.x | `w22o-queue-us` | root-shell | docs: `43.21.89.01` is 11.2.0 (`w22o-queue`, `w22o-queue-jp` run the same firmware) |
| 12.x | `w24g-spacebuns` | root-shell | UNSURE: the only VM `beanjar list` calls "webOS 12" (`02.68.80.01`, W24G). No doc names it; os-release not read. If it turns out not to be 12.x there is no 12.x VM |

The runner checks the release it finds (`/usr/lib/os-release`) against the row and records the
result.

## Beanjar host abstraction

Everything on the Beanjar machine (`beanjar list/info/settings/start/stop`, the QMP `sendkey`
through `socat`, the viewer capture through `grim`, fetching a key) goes through the
`BeanjarHost` class. Mode is automatic: local when `beanjar` is on PATH (the Pi), otherwise remote
through `ares-shell -d raspberrypi -r` (`--host-device` renames the device, `--host-mode` forces
a mode). No remote quoting exists outside that class. The viewer capture is the whole Wayland
output (no hard-coded crop geometry); the screenshots the steps look at come from inside the
guest.

## Elevation strategies

* `luna` (webOS 5 to 10): the app elevates its own service through Homebrew Channel's
  `elevateService`; the runner only verifies the service runs as root (`ps`).
* `root-shell` (11.x and 12.x): Homebrew Channel refuses `elevateService` from ordinary apps there
  (README "Decisions", `Denied method call`). The runner runs Homebrew Channel's own helper as
  root through `<vm>-root`, `…/org.webosbrew.hbchannel.service/elevate-service
  org.webosbrew.homechooser.service` (what `services/service.ts` runs), then makes the service quit
  so the root instance starts, and verifies the same outcome. **This is a test-harness substitute
  for the missing luna path: the app's own elevation is not exercised on those releases.** The
  strategy used is written at the top of every run log. This path is untested so far.

## Steps and what each asserts

Expectations come from `docs/settings-app.md` (Persistence, When apps are removed), the service
and backport docs.

0. Baseline: release matches the row; no Home Chooser, Homebrew Channel present; state recorded.
1. Install Home Chooser and the launcher; elevate (strategy above).
2. Open Home Chooser, drive it with remote keys (QMP `sendkey`, or `ares-do key` with
   `--input ares`): the focus is read from the app's DOM through the web inspector (port 9998,
   forwarded with `ares-novacom -f`; rows carry `data-app-id`), moved to the launcher's row, OK,
   then the "Use <name> as Home?" dialog is checked and "Use as Home" is pressed. Asserted:
   choice file holds the id; `general/defaultApps` `home` is the id; `homechooser-home` active;
   `init.d/homechooser` links into the installed app; on 6 to 9 the backport targets are mounted
   and the flag file exists, elsewhere not; on 10 and later the profile override exists.
3. Home from an ordinary app (Homebrew Channel; docs T4): the launcher is running after having
   been closed first, or in front (a launcher bar like Dock is an overlay window, so the app
   beneath stays "foreground"). A press with the stock home in front is only observed.
4. `ares-install --remove` the launcher: record `home` is stock/unset (5.x: a stock id), the
   override is gone on 10+, on 6 to 9 the backport stays mounted with an emptied baked id, the
   choice file stays, the watcher stays active, Home opens the stock launcher. Reinstall: the
   record and the override (10+) return, the id is baked in again (6 to 9), Home opens the
   launcher.
5. `ares-install --remove` Home Chooser: record, override, choice file, `init.d` link, flag,
   mounts and the watcher unit are gone; Home opens stock.
6. Cleanup: remove the launcher and Home Chooser (not on `w21p-kisscurl`), stop the VM.

A check that fails where the docs name a limitation is recorded `XFAIL` with the citation
(`Recorder.check(..., expected=...)`); nothing uses it yet, since 11.x and 12.x run with
root-shell elevation.

The log also has a "home-app residency" section at five points (before selecting, after "Use as
Home", after the Home press, after the launcher uninstall, after the Home Chooser uninstall): is the
stock home and the launcher running (`applicationManager/running`), pids and RSS. Observation only.
