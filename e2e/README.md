# End-to-end test

`homechooser_e2e.py` runs one scenario on one Beanjar VM and records every assertion with its raw
evidence. Python 3 standard library only; the device is reached with the `ares-*` tools by device
name (never raw ssh).

```
python3 -I e2e/homechooser_e2e.py --list
python3 -I e2e/homechooser_e2e.py --vm w22h-ombre            # or --vm 9.x
```

Options: `--launcher dock|fixture:<native|qml|web>.<normal|overlay>|<app id>` (default `dock`; an app id needs
`--launcher-ipk <path>`), `--launchers all-fixtures|<comma list>` (the scenario once per launcher on the same VM, in
sequence, the previous launcher and Home Chooser removed and the restore waited for in between; the log steps and the
screenshots carry the launcher's name), `--update-check` and `--update-from <git ref>` (see "The update step"); `--build`
(`npm run package` first; otherwise the newest `build/ipk/*.ipk` is used); `--host-mode
auto|local|remote` and `--host-device` (see below); `--elevation luna|root-shell`; `--input
qmp|ares`; `--until N` (stop after step N, no cleanup, for debugging);
`--reboot-check` (reboot the VM after step 3 and check the preload policy at boot); `--no-stop`; `--wait-popup N` (wait up to N s for a system popup holding key focus to be dismissed by hand). Artefacts go to `e2e/results/<vm>-<timestamp>/` (git-ignored): `run.log`,
`results.json`, numbered screenshots (`beanjar screenshot`, the TV picture).

Before booting, the runner stops if any VM is running. It adds the `hbchannel` layer and
`link=lan` while the VM is off (standing permission), boots it, creates or fixes the
`<vm>-root` ares device (root, port 6622, the VM's root key fetched from the host) and updates a
changed LAN address. It stops the VM at the end (not `w21p-kisscurl`).

## The matrix

| Release | VM | Elevation | How sure |
| --- | --- | --- | --- |
| 5.x | `w20l-jhericurl` | luna | verified: 5.6.1 (os-release) |
| 6.x | `w21a-kisscurl` | luna | verified: 6.5.2 (os-release). `w21p-kisscurl` (6.6.0) belongs to the Dock project and is not in the matrix |
| 7.x | `w22p-mullet` | luna | verified: 7.6.0 |
| 8.x | `w22o-number1` | luna | verified: 8.3.0 (beanjar's "webOS 23" is a platform label) |
| 9.x | `w22h-ombre` | luna | verified: 9.2.1 |
| 9.x | `w24g-ombre` | luna | verified: 9.2.4 (`w24p-ombre` runs the same firmware) |
| 10.x | `w22o-ponytail-us` | luna | verified: 10.3.1 |
| 11.x | `w22o-queue-us` | root-shell | verified: 11.2.0 (`w22o-queue`, `w22o-queue-jp` run the same firmware) |
| 12.x | `w24g-spacebuns` | root-shell | verified: 12.0.0 (os-release) |

The runner checks the release it finds (`/usr/lib/os-release`) against the row and records the
result.

## Which runs a change needs

A full matrix run with `--reboot-check` takes about 10 to 14 minutes per VM, so a change gets the
runs its reach calls for, not the whole matrix every time:

| Change | Runs |
| --- | --- |
| Any change | The offline suites: `backport/test-*.sh`, `make -C service test`, `cd app && npm run lint && CI=true npm test` |
| Code gated by release (a `MAJOR` bound, the 5.x branch, the 11/12 profile path, one backport set) | The releases inside the gate, plus one just outside it |
| Shared code (the apply flow, the watcher's main loop, the uninstall cleanup) | One release per family: 5.x, 9.x (backport), 11.x (root-shell elevation, profile path) |
| Only the runner or the docs | One VM |
| A release or a tag | The whole matrix with `--reboot-check` |

`--reboot-check` is used where boot behaviour is involved (the watcher, the preload policy, the
backport mounts). There is no shortened mode; settle and sampling times stay as they are.

## Beanjar host abstraction

Everything on the Beanjar machine (`beanjar list/info/settings/start/stop`, the QMP `sendkey`
through `socat`, the TV screenshot through `beanjar screenshot`, the fallback viewer capture through `grim`, fetching a key) goes through the
`BeanjarHost` class. Mode is automatic: local when `beanjar` is on PATH (the Pi), otherwise remote
over `ssh raspberrypi` (`--host-device` names another ssh config alias, `--host-mode` forces a
mode). The Pi is a Linux host, so it is reached with ssh; the `ares-*` tools are for the televisions
and VMs only. No remote quoting exists outside that class. The screenshots the steps look at come from
`beanjar screenshot` (Beanjar 0.3.1+), saved on the host and fetched with `scp` in remote mode; the
`grim` fallback is the whole Wayland output.

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
   choice file stays, the watcher stays active, Home opens the stock launcher. The watcher log shows
   the `notification createAlert:` calls and replies (a refusal is retried up to 8 times). The check
   is that the user was told: either the alert was accepted, or it was refused and the fallback toast
   (log: `alert was refused`, then an accepted `notification createToast:`) was shown; the toast
   fallback is the decided behaviour, so it passes. An unanswered alert, or a refused one with no
   accepted toast, is a `FAIL`. With an accepted alert the runner takes a `beanjar screenshot` (`4-alert`; look for
   "<launcher title> was removed, so the Home button opens LG Home again." and one OK button),
   presses OK (it is Home Chooser's own alert; the rule against pressing OK is about LG's system
   popups), and takes `4-alert-dismissed`. Reinstall: the
   record and the override (10+) return, the id is baked in again (6 to 9), Home opens the
   launcher.
5. `ares-install --remove` Home Chooser: record, override, choice file, `init.d` link, flag,
   mounts and the watcher unit are gone; Home opens stock. The runner polls the log once a second
   for the `notification createToast:` line, takes `5-toast` at once (a toast is short-lived, so the
   reply in the log is the evidence) and checks that exactly one toast was sent and what the reply was.
6. Cleanup: remove the launcher and Home Chooser (not on `w21p-kisscurl`). When the cleanup itself
   removed Home Chooser (a run that stopped early), it waits up to 150 s for the watcher to finish the
   restore (about 15 s per removal it has to confirm) and checks the choice file, the `init.d` entry
   and the unit, and only then stops the VM: stopping it earlier kills the watcher and leaves the choice
   file behind with a dangling `init.d` link, which the next run's baseline then sees. Stop the VM.

Measured on 2026-10-23: 10.3.1 accepts the alert from a plain root `luna-send` without `-a`; 5.6.1
and 9.2.1 answer `Alert is blocked by [ system ]` on every try (why is not established; on the 9.2.1
VM the stock home's first-run guide overlay was showing), so the toast is shown there. Right after a
compositor restart both calls get `UI is not yet ready` and succeed on a retry a few seconds later.

Preload checks (docs/settings-app.md "The stock home is not preloaded"), on 6.x and later; on 5.x
the watcher makes no preload call, so the checks only assert the policy is not disabled:

* after step 2 (the switch): `preloadmanager/getPreloadPolicy` shows `isEnabled:false` for
  `com.webos.app.home`, then after a 60 s settle `com.webos.app.home` is not in `applicationManager/running`
  in any of the samples taken every 5 s over 60 s. With an overlay launcher (Dock) on 6.x to 10.x the
  watcher deliberately leaves the stock home running (sam would relaunch it at once as the last-input app,
  75 to 89 MB measured on 10.3.1, 9.2.1 and 8.3.0), so the expected result is explicit: the stock home is running, the
  watcher log has the `overlay launcher ... stock home left running` line and no `closeByAppId
  com.webos.app.home` line; on 11.x and 12.x, or with a non-overlay launcher, the stock home must not run
  and a running one is a `FAIL`;
* after the launcher removal (step 4) and after the Home Chooser uninstall (step 5): `isEnabled:true`;
  after the launcher is reinstalled, `isEnabled:false` again;
* with `--reboot-check` (after step 3, launcher chosen): the VM is stopped and started with Beanjar;
  after the boot the watcher is active, the record still names the launcher, the watcher's log
  (in `/tmp`, so from this boot) shows `setPreloadPolicy ... isEnabled=false`, the policy reads false, and
  after the 45 s boot close plus a 30 s settle the stock home is not running over 60 s (same
  overlay rule: skip line, no close; the stock home running only on 6.x to 9.x, where `snapshot-boot-components`
  launches it, while on 10.x nothing starts it at boot, so its state is only noted). On 5.x it asserts that there was no preload call.

None of this has run yet: `getPreloadPolicy`'s reply shape (an `applications` array whose entries
carry the app id and `isEnabled`) and root `luna-send` access to `preloadmanager` are inferred from
the firmware.

A check that fails where the docs name a limitation is recorded `XFAIL` with the citation
(`Recorder.check(..., expected=...)`); nothing uses it yet, since 11.x and 12.x run with
root-shell elevation.

The log also has a "home-app residency" section at five points (before selecting, after "Use as
Home", after the Home press, after the launcher uninstall, after the Home Chooser uninstall): is the
stock home and the launcher running (`applicationManager/running`), pids and RSS. Observation only.

## Launchers: Dock and the fixtures

`--launcher dock` is Dock from `/home/mariotaku/Projects/webosbrew/Dock.app/dist`. The six fixtures
(`e2e/fixtures/README.md`; built into `build/fixtures/` by `sh e2e/fixtures/build.sh`, which the runner calls itself if
an ipk is missing) are `fixture:{native,qml,web}.{normal,overlay}`. Home Chooser supports a launcher only through
`appCategory: ["home"]` in its appinfo, so the fixtures declare nothing else and draw nothing but a solid colour.

* **Overlay expectations come from the ipk.** `Launcher.overlay` is `defaultWindowType == "overlay"` in the
  `appinfo.json` inside the launcher's ipk (any id, any launcher). It drives the 6.x to 10.x preload expectations
  (steps 2, 3r: the stock home left running, the skip line `overlay launcher <id> on webOS N: stock home left running`
  must name that id, no `closeByAppId com.webos.app.home`); a normal-window launcher must see the stock home closed. The
  watcher decides the same way for any id (`overlay_check` in `backport/homechooser-home-watch`: `getAppInfo`, else the
  installed `appinfo.json`), so the two are independent readings of the same declaration.
* **Launch proof** (every Home press that should open the launcher, for Dock, any `--launcher-ipk` app and the
  fixtures alike: steps 3, 4 and 3u). Nothing visual is evidence; `beanjar screenshot` pictures are kept as artefacts
  only. The check passes when both hold:
  1. sam's own record of the launch. Before the press the runner starts `luna-send -i
     luna://com.webos.applicationManager/getAppLifeEvents {"subscribe":true}` on the device (output to
     `/tmp/hc-e2e-life.log`), and takes a UTC mark. Afterwards it accepts either a sam line in `/var/log/messages`
     stamped at or after the mark that names the id, one of `NL_APP_LAUNCH_BEGIN {"app_id":"<id>",...}`,
     `APP_LAUNCHED` or `RelaunchAsCommon` (the `_ERR`/`_WARNING` variants are shown, never counted), or a
     `getAppLifeEvents` event for the id of kind `launch`, `relaunch`, `foreground` or `splash`. Either source also
     catches a relaunch of an app that is already running (`RelaunchAsCommon` line, `relaunch` event); the report
     says whether one was seen. The log line is the primary signal (no subscription to hold, and the same mark-based
     read the 12.x homeKey check uses); the event stream is the second source and is recorded either way.
  2. the id is in `applicationManager/running` afterwards, with a live pid (dead-pid ghost entries dropped).

  The formats are firmware-derived, not yet device-confirmed for a third-party launcher: see
  `e2e/fixtures/README.md` "What the runner proves". A missing proof is a `FAIL` for every type; on a release where
  `fixtures/qml/support.json` says `false` the qml fixture is skipped with an `XFAIL`.
* With `--launchers all-fixtures` a failure in one launcher is recorded and the next one runs.

## The update step (`--update-check`)

The old build comes from `--update-from <git ref>` (default: the last commit before HEAD that changed `app/`,
`service/` or `backport/`, or HEAD itself if those have uncommitted changes), built by `git archive` into a `/tmp`
directory (the working tree and `.git` are not touched; `node_modules` is linked from the main tree) and removed at the
end. Both builds are `0.1.0` today, so the version does not tell them apart: the runner compares the watcher script's md5
in the two ipks and in the app directory on the device. The old build is installed in step 1 instead of the current one
and used for step 2 (choose the launcher) and step 3 (Home opens it). Then step 3u installs the current build with
`ares-install` and no uninstall.

What the design says (`backport/homechooser-home-watch` header and `check_hc`; `docs/settings-app.md` "When apps are
removed" and "What is left when the watcher is not running"): an update installs by removing first, so the watcher takes
Home Chooser as gone only if it is still gone `HOMECHOOSER_CONFIRM_DELAY` (15 s) later, else it logs `Home Chooser is
back, not removing anything`; no notification is sent for the temporary absence of an update; the watcher runs from a copy
in `/tmp/homechooser-runtime`, so replacing the app directory does not pull the script from under it, and the unit is not
restarted. So no restart is expected, and the old build's watcher code keeps running until its next start.
Asserted after a 45 s settle:

* the app directory holds the new build (watcher script md5);
* the choice file and the record `home` still name the launcher;
* exactly one watcher unit and one top-level watcher process, with the same `MainPID` and `NRestarts` as before (no duplicate, no restart);
* the watcher log lines written during the update contain no `watching, home=` (no restart), no restore, revert, `isEnabled=true`, alert or toast; the optional `Home Chooser is back` line is noted either way, as is which build the running watcher is;
* the `init.d` entry links into the installed app and the target exists;
* on 6 to 9 the backport mounts are the same set, the flag file exists and the launcher id is still baked in; elsewhere none; on 10 and later the override exists;
* the stock home preload policy is still disabled (6 and later; 5.x: not disabled);
* Home opens the launcher (with the fixture proof above);
* the app opens, lists the launcher, shows no `Couldn't` toast and has the launcher's row ticked.

Then steps 4 and 5 run on the updated install.

## System popups and screenshots

* `w22o-ponytail-us` (10.3.1) shows the OLED "screen restoration" popup (`com.webos.app.factorywin`, buttons "Start Now", focused by default, and "Cancel") after every boot. It takes key focus, so the Home Chooser list never moves; BACK does not close it, and "Start Now" restarts the TV for 5 minutes. Before driving the list the runner probes one key; if the focus does not move it records the evidence (`oledepl isPixelRefresherCondition`, a screenshot) and waits `--wait-popup` seconds for someone to press Cancel by hand, then fails. It never presses OK on a system popup.
* Screenshots: `beanjar screenshot <vm> <file> --json` is the primary capture (about 2 s, headless, whatever the window position; `taken_by` is logged). No viewer is opened for it, so the runner no longer opens one. Only if it fails: the guest capture (`ares-do screenshot`; `com.webos.service.capture` does not work on 5.x (timeout) or 10.x/11.x/12.x (`ERROR_INTERNAL`), and after the first failure it is not tried again), then `grim` of the whole Pi desktop (`*-viewer.png`), for which the viewer is opened on first use and closed before the stop. Each fallback is logged with the word FALLBACK.
* On 5.x Home starts `com.webos.app.fullhome` as an overlay over the app in front (foreground stays the app), so step 3/4/5's "Home opens stock" looks for it running.
