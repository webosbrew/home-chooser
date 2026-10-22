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
`--no-stop`; `--wait-popup N` (wait up to N s for a system popup holding key focus to be dismissed by hand). Artefacts go to `e2e/results/<vm>-<timestamp>/` (git-ignored): `run.log`,
`results.json`, numbered screenshots (taken inside the guest with `ares-do screenshot`).

Before booting, the runner stops if any VM is running. It adds the `hbchannel` layer and
`link=lan` while the VM is off (standing permission), boots it, creates or fixes the
`<vm>-root` ares device (root, port 6622, the VM's root key fetched from the host) and updates a
changed LAN address. It stops the VM at the end (not `w21p-kisscurl`).

## The matrix

| Release | VM | Elevation | How sure |
| --- | --- | --- | --- |
| 5.x | `w20l-jhericurl` | luna | verified: 5.6.1 (os-release) |
| 6.x | `w21p-kisscurl` | luna | docs: W21P `03.70.25.01` is 6.6.0. Owned by the Dock project: ask first. Dock must be left installed and selected with Home Chooser installed; the runner does not restore that |
| 7.x | `w22p-mullet` | luna | verified: 7.6.0 |
| 8.x | `w22o-number1` | luna | verified: 8.3.0 (beanjar's "webOS 23" is a platform label) |
| 9.x | `w22h-ombre` | luna | verified: 9.2.1 |
| 10.x | `w22o-ponytail-us` | luna | verified: 10.3.1 |
| 11.x | `w22o-queue-us` | root-shell | verified: 11.2.0 (`w22o-queue`, `w22o-queue-jp` run the same firmware) |
| 12.x | `w24g-spacebuns` | root-shell | verified: 12.0.0 (os-release) |

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
   Also asserted: at the first open after a fresh install there is no error toast (WAM's
   `localStorage` outlives `ares-install --remove`, so a `homechooser.pending` marker left by an
   earlier run must not read as a failed apply; the runner also plants such a marker through the
   inspector, reopens, and asserts no toast and the marker cleared), and after the
   apply the panel is opened again with no error toast and the chosen row ticked.
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

## System popups and screenshots

* `w22o-ponytail-us` (10.3.1) shows the OLED "screen restoration" popup (`com.webos.app.factorywin`, buttons "Start Now", focused by default, and "Cancel") after every boot. It takes key focus, so the Home Chooser list never moves; BACK does not close it, and "Start Now" restarts the TV for 5 minutes. Before driving the list the runner probes one key; if the focus does not move it records the evidence (`oledepl isPixelRefresherCondition`, a screenshot) and waits `--wait-popup` seconds for someone to press Cancel by hand, then fails. It never presses OK on a system popup.
* `com.webos.service.capture` does not work on 5.x (timeout) or 10.x/11.x/12.x (`ERROR_INTERNAL`). The runner opens the Beanjar viewer (`beanjar viewer open`) after the start, closes it before the stop, and every `shot()` also saves a full-desktop `grim` of the Pi (`*-viewer.png`; the viewer window is wherever the compositor put it). After the first failed guest capture the guest capture is skipped.
* On 5.x Home starts `com.webos.app.fullhome` as an overlay over the app in front (foreground stays the app), so step 3/4/5's "Home opens stock" looks for it running.
