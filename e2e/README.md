# End-to-end test

`homechooser_e2e.py` records each assertion with its raw evidence. Python 3 standard library
only; televisions and VMs are reached with `ares-*` by device name (never raw ssh).

## Launcher author: your own TV

Use a rooted webOS TV with Homebrew Channel and an ares root device configured. With Home
Chooser installed and LG Home currently selected, run the API compatibility kit:

```sh
python3 -I e2e/homechooser_e2e.py --device my-root-tv --api-check
python3 -I e2e/homechooser_e2e.py --device my-root-tv --api-check \
  --launcher org.example.launcher --launcher-ipk path/to/launcher.ipk
```

The first command builds and installs the permission-declaring web fixture and a disposable
control app. The second tests your launcher ipk, whose appinfo must declare both
`appCategory:["home"]` and
`requiredPermissions:["org.webosbrew.homechooser.applications"]`. Pass `--hc-ipk
path/to/homechooser.ipk` if Home Chooser is not installed yet. The kit selects the launcher on
the verified Home Chooser screen with ares keys, checks the v1 methods before and after
selection, then restores LG Home and removes what it installed. It refuses to replace an
already installed launcher or change an existing nonstock Home choice. It uses no Beanjar,
QMP, screenshots, or reboot. On webOS 6–9 the Home key hook still needs a separate reboot test.
The output is `e2e/results/<device>-<timestamp>/results.json` and `run.log`.

`ares-package`, `ares-install`, `ares-launch`, `ares-do`, `ares-shell` and `ares-novacom` must be
on `PATH`. The root ares name must support `ares-do exec` and the web inspector forward. See
[Testing your launcher](../docs/launcher-api.md#testing-your-launcher) for manual calls.

## Maintainer VM scenario

This mode runs the full Home-key lifecycle on one Beanjar VM. It needs the maintainer's Beanjar
host and VM setup in addition to ares:

```
python3 -I e2e/homechooser_e2e.py --list
python3 -I e2e/homechooser_e2e.py --vm w22h-ombre            # or --vm 9.x
```

Options: `--launcher dock|fixture:<native|qml|web>.<normal|overlay>|<app id>` (default `dock`, with `--api-check`
`fixture:web.normal`; an app id needs `--launcher-ipk <path>`), `--launchers all-fixtures|<comma list>` (the scenario once per launcher on the same VM, in
sequence, the previous launcher and Home Chooser removed and the restore waited for in between; the log steps and the
screenshots carry the launcher's name), `--update-check` and `--update-from <git ref>` (see "The update step"); `--build`
(`npm run package` first; otherwise the newest `build/ipk/*.ipk` is used); `--hc-ipk <path>` (test this
Home Chooser ipk instead); `--old-ipk <path>` (`--update-check` with an older build already made);
`--host <name>`, `--hosts-file`, `--host-mode local|remote` and `--host-device` (see "Several machines");
`--dock-ipk <path>` (required for Dock unless configured in `hosts.toml`);
`--launcher-update-check` (install version 1.0.1 of a selected web fixture, or pass
`--launcher-update-ipk <path>` for another launcher id); `--outdir <dir>` (instead of `e2e/results/<vm>-<timestamp>/`; must not exist);
`--elevation luna|root-shell`; `--input qmp|ares`; `--until N` (stop after step N, no cleanup, for debugging);
`--reboot-check` (reboot the VM after step 3 and check the preload policy at boot); `--reboot-via later|alert`
(webOS 6-9, how the restart the first choice asks for is done: `later`, the default, closes our alert and restarts the VM with
beanjar; `alert` leaves pressing Reboot to a person; see "webOS 6-9: no restart while the TV runs");
`--api-check` (the launcher-API contract, see "The launcher API check"); `--no-stop`; `--wait-popup N` (wait up to N s for a system popup holding key focus to be dismissed by hand). Artefacts go to `e2e/results/<vm>-<timestamp>/` (git-ignored): `run.log`,
`results.json`, numbered screenshots (`beanjar screenshot`, the TV picture). A check is `PASS`, `FAIL`, `XFAIL`
(below) or `MANUAL`: a check the runner leaves to a person because it would mean pressing a key at a system popup.

Every webOS 6–9 run exercises the minimal structural hook. After the selection boot and the
reboot check, the runner asserts `mounted=yes` and `patch=possible`; the same Home-key launch
proofs apply on every build. Offline checks also exercise the two-file core when optional
systemUi anchors fail; the documented foreground-comparison degradation is expected there.

Log evidence includes the retained `/var/log/messages.N.gz` archives in chronological order;
boot and service activity can rotate the active file before an assertion reads it. After each
hook boot, the run also records the compositor's main-QML load lines and retained crash lines.

Before booting, the runner stops if any VM is running on its machine. It adds the `hbchannel` layer and
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
| Any change | The offline suites: `service/scripts/tests/test-*.sh`, `make -C service test`, `cd app && npm run lint && CI=true npm test` |
| Code gated by release (a `MAJOR` bound, the 5.x branch, the 11/12 profile path, the hook version gate) | The releases inside the gate, plus one just outside it |
| Shared code (the apply flow, the watcher's main loop, the uninstall cleanup) | One release per family: 5.x, 9.x (Home key hook), 11.x (root-shell elevation, profile path) |
| Only the runner or the docs | One VM |
| A release or a tag | The whole matrix with `--reboot-check` |

`--reboot-check` is used where boot behaviour is involved (the watcher, the preload policy, the
Home key hook mounts). There is no shortened mode; settle and sampling times stay as they are.
On webOS 6–9 it also measures one cache preparation on the TV (elapsed time and peak Node RSS),
checks that a normal boot has no rewriter activity, then changes only the cache manifest's stock
MD5. It verifies a stock boot, watcher regeneration and restart prompt, and an active hook after
the next reboot. LG firmware files are never edited.

## Beanjar host abstraction

Everything on the Beanjar machine (`beanjar list/info/settings/start/stop`, the QMP `sendkey`
through `socat`, the TV screenshot through `beanjar screenshot`, the fallback viewer capture through `grim`, fetching a key) goes through the
`BeanjarHost` class. The mode comes from the machine's entry in `e2e/hosts.toml`: remote over
`ssh <alias>` (the Pi: `ssh raspberrypi`), or local (`local = true`, beanjar on the PC running the
runner). It is no longer guessed from `beanjar` being on PATH: this PC has Beanjar's own multi-host CLI
on PATH, but the QMP sockets, `grim` and the VM files are on the machine the VM runs on.
`--host-device` names another ssh config alias and `--host-mode` forces a mode for one run. The Pi is
a Linux host, so it is reached with ssh; the `ares-*` tools are for the televisions and VMs only.
VMs are listed with `beanjar list --local --json` (that machine's own VMs only); the QMP socket is under
the VM directory `beanjar info` reports. No remote quoting exists outside that class. The screenshots the steps look at come from
`beanjar screenshot` (Beanjar 0.3.1+), saved on the host and fetched with `scp` in remote mode; the
`grim` fallback is the whole Wayland output.

## Several machines

The Beanjar machines are listed in `e2e/hosts.toml`; `e2e/hosts.local.toml` (git-ignored) is read over
it, for this PC's own machines and `[paths]`. Without either file the one machine is today's: `pi`,
reached with `ssh raspberrypi`, so a single-machine run needs no configuration.

```toml
default = "pi"

[hosts.pi]
ssh = "raspberrypi"      # an ssh config alias, key login (BatchMode); `local = true` for this PC
# vms = ["w22o-ponytail-us"]
```

**One VM per machine.** A machine has one CPU set and one memory pool; two VMs on it contend and
every timing or RSS measured that way is contaminated. So at most one VM runs on a machine at a time,
and the runner refuses to boot one while any VM is running there, ours or another session's.
Different machines run at the same time.

Which VMs a machine has is asked from it (`beanjar list --local --json`), so a VM needs no entry. VM
names must be unique across the machines: the ares device entries (`<vm>`, `<vm>-root`) and the keys in
`~/.ssh` (`beanjar-<vm>-root`, `beanjar-<vm>_devmode`) are named after the VM only. A VM that exists on
two machines (a copied image) is not run until `vms = [...]` pins it to one of them.

To add a machine: install Beanjar on it, give it key login from this PC
(`ssh <alias-or-user@host> beanjar list --local --json` must work without a prompt), add it with `beanjar host add <name>
ssh://<user>@<alias>` so the multi-host CLI here knows it too, and add `[hosts.<name>] ssh =
"<alias>"` to `e2e/hosts.local.toml` (or `hosts.toml` when it is shared) under the same name. The VMs
run with `link=lan`, so their LAN addresses are reachable from this PC wherever they run.

`MATRIX` in `homechooser_e2e.py` contains release slots. The VM field accepts a name or an ordered
 tuple of alternatives, for example `('6.x', ('w21a-kisscurl-rk1', 'w21a-kisscurl'), 'luna',
 'verified', '...')`. Each row runs exactly once, using a candidate reported by host discovery.
 The three 9.x rows remain separate slots; `--vms 9.x` selects the first, as before. A single-run
 `homechooser_e2e.py --vm 6.x` retains the original Pi VM; explicit RK names work too.

`run_matrix.py` has one worker per eligible machine and a shared pool of slots. A free worker claims
 a compatible slot under a lock. It favours shared work on machines with less exclusive work, leaving
 host-only slots for machines that need to run them; ties favour more alternative hosts, then matrix
 order. Candidates on the same host follow their order in the slot. Runs that keep a launcher go last.
 Actual assignments follow completion times. `--dry-run` simulates equal-cost runs weighted by the
 configured slowdown; its printed plan is an estimate, not a reservation.

The committed hosts are `pi` (`raspberrypi`), `rockpi4a-1` and `rockpi4a-2`
 (`mariotaku@rockpi4a-N.lan`, accepted directly by ssh and scp). Each host can set `slowdown = 1.5`;
 the default is `1.0`, values must be finite and at least `1.0`. The boards use a provisional 1.5;
 this is a guess to measure, not a measured speed ratio. Pi timing is unchanged.
 In the per-run process the factor scales Python settling sleeps and polling intervals, boot/LAN and
 device readiness deadlines, inspector readiness, scenario/notification/alert waits, popup/manual
 waits, reboot and sam relaunch deadlines, preload settling and observation sleeps, and command
 timeouts through `run()` (including Beanjar/ares). Embedded guest shell sleeps, socket/HTTP probe
 timeouts, monitor thread joins, build subprocesses, and matrix `--wait-idle` are unchanged.
 Preload observation duration and polling interval both scale.

```
python3 -I e2e/run_matrix.py --dry-run                            # the plan for the whole matrix
python3 -I e2e/run_matrix.py --build --reboot-check               # the whole matrix
python3 -I e2e/run_matrix.py --vms 5.x,9.x,11.x --launchers all-fixtures
```

* `--hosts pi,rockpi4a-1` limits discovery and execution to those configured hosts.
* `--vms` takes explicit VM names (restricting a slot to that candidate) or releases (retaining its
  alternatives); default is all matrix slots. Options it does not know (`--reboot-check`,
  `--wait-popup N`, `--elevation`, ...) go to every run; `--no-stop`, `--vm`, `--host` and `--outdir` are
  refused, since the scheduler sets them or a VM left running would hold its machine.
* It builds once before the fan-out: Home Chooser (`--build`, else the newest `build/ipk/*.ipk`), the old
  build for `--update-check`, and any missing fixture ipk. The Home Chooser ipks are copied into the
  matrix directory and passed to each run (`--hc-ipk`, `--old-ipk`), so a rebuild in the tree during the
  runs changes nothing.
* Before each run it asks the machine for running VMs and waits up to `--wait-idle` seconds (default
  180; the previous run's VM may still read Stopping) for none; then it refuses the run (`refused` in the
  summary) and moves on. A machine that does not answer is refused the same way.
* One line per finished run; each run's console goes to `<vm>.console.log`. The result is
  `e2e/results/matrix-<timestamp>/`: `summary.md` and `summary.json` linking each run's directory
  `<vm>/` (`run.log`, `results.json`, screenshots), and `ipk/`.
* Ctrl-C starts no new run and interrupts the running ones; each runner unwinds through its cleanup and
  stops the VM it started. A second Ctrl-C kills them and names the VMs that may still be running
  (`beanjar stop <host>/<vm>`).
* `python3 -I -m unittest discover -s e2e -p 'test_*.py'` tests the planning, the per-machine
  serialisation, the refusals, Ctrl-C (fake machines and a fake runner) and the device-list lock, offline.

What runs in parallel, and why it is safe:

* `~/.webos/ose/novacom-devices.json`: the runner changes it under an exclusive `flock` on
  `novacom-devices.json.lock` and replaces it by rename, so parallel runs lose no entry and an ares command
  reading it never sees a half-written file. ares-cli-rs writes it in place without a lock, so
  `ares-setup-device` by hand during a matrix run is still unsafe.
* Keys fetched to `~/.ssh` are written under a temp name and renamed.
* Temp files on a machine carry the VM name and the runner's pid (`/tmp/e2e-tv-<vm>-<pid>.png`); the
  inspector's local port is picked per run and another is tried if the forward cannot bind it.
* Each run has its own result directory and its own ares device entries (per VM).

Beanjar's own multi-host CLI (`beanjar host add/list`, `beanjar list` across hosts, `<host>/<vm>` for
start, stop, info and screenshot) reaches the machines the same way, ssh per call. The runner does not
depend on it: it runs `beanjar` on each machine over ssh, which works whether or not this PC has Beanjar.

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
  The `--update-check` step reinstalls the service, which loses that root, so after the update the runner repeats
  the elevation (and verifies it) before it opens the app again.

## Steps and what each asserts

Expectations come from `docs/settings-app.md` (Persistence, When apps are removed), the service
and Home key hook docs.

0. Baseline: release matches the row; no Home Chooser, Homebrew Channel present; state recorded.
1. Install Home Chooser and the launcher; elevate (strategy above).
2. Open Home Chooser, drive it with remote keys (QMP `sendkey`, or `ares-do key` with
   `--input ares`): the focus is read from the app's DOM through the web inspector (port 9998,
   forwarded with `ares-novacom -f`; rows carry `data-app-id`), moved to the launcher's row, OK,
   then the "Use <name> as Home?" dialog is checked and "Use as Home" is pressed. Asserted:
   choice file holds the id; `general/defaultApps` `home` is the id; `homechooser-home` active;
   `init.d/homechooser` links to the persistent boot restore copy; on 6 to 9 the Home key hook targets are mounted
   (after the restart below) and the flag file exists, elsewhere not; on 10 and later the profile override exists.
   On 6 to 9 the first choice mounts nothing: see "webOS 6-9: no restart while the TV runs". The
   toast is "Done. Press Home to try it." where no restart is asked. On 11.x and 12.x the runner
   also checks that the Home Chooser page is still visible immediately after confirmation. The installed build carries the
   current texts (`grep -F` on the device: the watcher in the service package and its running `/tmp` copy, the
   service binary, the app bundle) and none of the old restart wording.
   Also asserted: at the first open after a fresh install there is no error toast (WAM's
   `localStorage` outlives `ares-install --remove`, so a `homechooser.pending` marker left by an
   earlier run must not read as a failed apply; the runner also plants such a marker through the
   inspector, reopens, and asserts no toast and the marker cleared), and after the
   apply the panel is opened again with no error toast and the chosen row ticked.
3. Home from an ordinary app (Homebrew Channel; docs T4): the launcher is running after having
   been closed first, or in front (a launcher bar like Dock is an overlay window, so the app
   beneath stays "foreground"). A press with the stock home in front is only observed. On 6 to 9
   this needs the boot of step 2, which has happened by then.
4. `ares-install --remove` the launcher: record `home` is the stock id on 5.x to 9.x
   (`com.webos.app.fullhome` if installed, else `com.webos.app.home`; on 6 to 9 exactly the id the
   baseline resolved, never null) and null or unset on 10+, the
   override is gone on 10+, on 6 to 9 the Home key hook stays mounted with an emptied baked id and the
   compositor was not restarted, the
   choice file stays, the watcher stays active, Home opens the stock launcher at once. The watcher log shows
   the `notification createAlert:` calls and replies (a refusal is retried up to 8 times). The check
   is that the user was told: either the alert was accepted, or it was refused and the fallback toast
   (log: `alert was refused`, then an accepted `notification createToast:`) was shown; the toast
   fallback is the decided behaviour, so it passes. An unanswered alert, or a refused one with no
   accepted toast, is a `FAIL`. With an accepted alert the runner takes a `beanjar screenshot` (`4-alert`; look for
   "<launcher title> was removed, so the Home button opens LG Home again." and one OK button),
   closes it with `notification/closeAlert` (alertId from the accepted reply, `luna-send -a
   com.webos.applicationManager`; no key is pressed, an LG popup on top would take it), and takes
   `4-alert-dismissed`. Before the alert screenshot and before each Home press that proves a launch,
   the runner notes `foreign popup present: ...` (the LG "[crash] Fault Occured" popup) and, before a Home
   proof, stops with a FAIL, when since the step's mark (messages time and uptime, taken when the step or the
   press begins) `/var/log/messages` has a `crashd CRASHED` or faultmanager `createNotiPopup`/`sendCreateAlert`
   line, or faultmanager's `getFaultDetectedStatus` lists a fault whose `faultTime` (seconds since boot) is after
   the mark. `general` / `keyCheck2_<N>_sysfaultdetector` (one per boot, 27-34 s after it, constant within the
   boot, seen on passing steps; only `faultmanager FAULT_RESPONSE` lines, no crashd line) and faults raised
   before the mark are noted, never counted. It never presses keys at a popup, with one exception: the known
   9.2.4 `legacy-broadcast-dvb` crash (`API_SI_GetLinkageUriInfo+0x197`, a crashd line for `legacy-broadcas.<pid>`
   confirmed by the fault entry of the same pid; accepted limitation, see docs/home-key-hook.md "Known limitations"
   and `docs/research/home-key-hook-early-mount-study.md`). That crash is recorded as a note, not a `FAIL`, in the
   end-of-step crash check and the popup check; any other crash, a WebAppMgr crash, or a legacy-broadcast crash at
   another location still fails. If its popup holds key focus (before a Home proof, or when the probe key is
   blocked) the runner sends BACK at most once per fault (pid and faultTime; YES and NO do the same there), then
   checks that key focus is back: a probe key moves the list focus when our page is open, else the foreground app
   is unchanged. (The fault status keeps every fault of the boot, so it cannot show the popup is gone.) If focus
   is not back the step FAILs and no more keys are sent. Reinstall: the
   record and the override (10+) return, the id is baked in again (6 to 9), Home opens the
   launcher; on 6 to 9 this return is a switch with the patch mounted and must not restart the compositor.
5. `ares-install --remove` Home Chooser: override, choice file, `init.d` link, boot restore copy, flag and the
   watcher unit are gone; the record is stock as in step 4; on 6 to 9 the patch stays mounted until
   the next boot (its sources are in `/tmp/homechooser-homekey/stage`, not in the app), the baked id
   is not checked (the installed worker may have been removed with the package), the compositor was not restarted, and
   Home opens stock at once. The toast reads "Home Chooser was removed. The Home button opens LG Home
   again." on every release. The runner polls the log once a second
   for the `notification createToast:` line, takes `5-toast` at once (a toast is short-lived, so the
   reply in the log is the evidence) and checks that exactly one toast was sent and what the reply was.
6. Cleanup: remove the launcher and Home Chooser (not on `w21p-kisscurl`). When the cleanup itself
   removed Home Chooser (a run that stopped early), it waits up to 150 s for the watcher to finish the
   restore (about 15 s per removal it has to confirm) and checks the choice file, the `init.d` entry
   and the unit, and only then stops the VM: stopping it earlier kills the watcher and leaves the choice
   file behind until the next boot's restore copy runs. Stop the VM.

### webOS 6-9: no restart while the TV runs

Since 144ef21 nothing restarts the compositor on a running TV (a bare restart crashed WebAppMgr;
`docs/home-key-hook.md` "Restarting the compositor"). The runner follows that:

* Step 2, patch not mounted: the choice mounts nothing. The app asks the service for a system alert
  (Reboot and Later; the app has no restart dialog of its own, because surface-manager closes an overlay
  after about 90 to 120 s without a key press, `userTimeout`, and the alert survives that) and toasts "Done.
  Restart the TV to finish."; while the restart is pending it also lists a "Restart now" row under the note
  "The Home button changes after a restart. Use Restart now — turning the TV off with the remote may not restart it.". The runner reads the service's `REBOOT_ASK` PmLog lines
  (since the confirmation was pressed), or the same notification call and reply from `ls-monitor` when a
  firmware drops the service's info logs. The monitor retains only token-free evidence. It checks a
  `createAlert` request with the apply message and the buttons Reboot and Later, and the notification
  service's reply. An accepted alert is the pass (screenshot
  `2-reboot-alert`: look for the message and the two buttons, focus on Later). A refused alert passes only
  with the fallback: an accepted `createToast` with the message. Through the inspector it also checks that
  the page shows no dialog, lists the "Restart now" row and showed the toast (a page that an overlay timeout
  closed is noted, not failed). Asserted before the restart: `homechooser-homekey status` says
  `mounted=no` and `reboot=needed`, nothing is mounted, the flag and the `init.d` entry are written, the
  record names the launcher, and the compositor was not restarted.
  * `--reboot-via later` (the default): our alert is closed with `notification/closeAlert` (the `alertId`
    from the accepted `createAlert` reply in the `REBOOT_ASK` line, `luna-send -a com.webos.applicationManager`,
    as the removal alert is closed) and the VM is restarted with `beanjar stop` and `start`. No key is pressed.
  * `--reboot-via alert`: capture the TV with Beanjar and run local `beanjar elements --image PNG --json`.
    Require the exact restart message, exactly Reboot/Later in the alert's
    message/button band, and Later as the sole undimmed button (`style.disabled == false`). Derive
    UP/DOWN/LEFT/RIGHT from their boxes, move once through the selected QMP/ares input path, then
    capture and prove ownership and Reboot focus again before ENTER. Both frames and their elements
    are saved in the run evidence. Each proof also requires this request's matching accepted
    `REBOOT_ASK` service log lines (message, buttons and alert id); bus-only evidence falls back
    to MANUAL. Titles are optional: webOS 9 renders no alert title, and an app title behind the
    alert does not prove ownership. The Pi needs no OCR installation. A new `boot_id` yields
    PASS (automatic press); an Off VM is started again and its new boot checked.
    Any failed proof stops key input and falls back to the existing 240 s MANUAL wait; if no reboot
    follows, close our alert with `closeAlert` and restart with Beanjar.
  After the boot: the watcher is active, the boot hook mounted the patch (`mounted=yes`, `reboot=no`, at
  least 9 mounts), and the Home key hook log of that boot (its firmware-order compositor restart is the only
  one allowed) is noted; the minimal-hook status checks come here.
* Step 2, patch already mounted (a second launcher of `--launchers` in the same boot): no dialog,
  `reboot=no`, the watcher bakes the new id into the mounted copy, no compositor restart.
* No compositor restart: `surface-manager-daemon` keeps its `MainPID` and `NRestarts`, and no new
  `restarting surface-manager-daemon` line appears in `/tmp/homechooser-homekey.log`. Checked for
  the choice, the launcher removal and its return (a switch with the patch mounted), the Home Chooser
  uninstall and the API calls. It is skipped when a reboot came in between.
* No crash: after every step `/var/log/messages` since the step began must have no `crashd ... CRASHED`
  line and no WebAppMgr crash line, and within one boot `webapp-mgr` keeps its `MainPID` and `NRestarts`.
* `--update-check` from a build older than 144ef21: that build mounts live and asks nothing; with no
  restart prompt and the patch mounted the runner notes it and goes on.

Measured on 2026-10-23: 10.3.1 accepted the alert from a plain root `luna-send` without `-a` (the watcher now sends with `-a com.webos.applicationManager`); 5.6.1
and 9.2.1 answer `Alert is blocked by [ system ]` on every try (why is not established; on the 9.2.1
VM the stock home's first-run guide overlay was showing), so the toast is shown there. Right after a
compositor restart both calls get `UI is not yet ready` and succeed on a retry a few seconds later.

Preload checks (docs/settings-app.md "The stock home is not preloaded"), on 6.x and later; on 5.x
the watcher makes no preload call, so the checks only assert the policy is not disabled:

* after step 2 (the switch): `preloadmanager/getPreloadPolicy` shows `isEnabled:false` for
  `com.webos.app.home`. On 11.x and 12.x LG Home may still be running because the chosen launcher
  has not been brought in front yet. After step 3 opens it from an ordinary app, the runner
  asserts that the watcher logged one deferred close and LG Home has no live process. On 6.x to 10.x, for
  every launcher (8791f6e), the stock home is not closed (sam would relaunch it at once as the last-input app, 75 to
  89 MB measured on 10.3.1, 9.2.1 and 8.3.0): the policy must still read `isEnabled:false`, the watcher log must have
  the `webOS N: stock home left running` line and no `closeByAppId com.webos.app.home` line, and whether the stock
  home runs is recorded, not asserted. After the `--reboot-check` boot the runner notes whether it runs and sam's
  launch lines for it (`snapshot-boot-components` may launch it at boot outside the preload manager: an open risk);
* after the launcher removal (step 4) and after the Home Chooser uninstall (step 5): `isEnabled:true`;
  after the launcher is reinstalled, `isEnabled:false` again;
* with `--reboot-check` (after step 3, launcher chosen): the VM is stopped and started with Beanjar;
  after the boot the watcher is active, the record still names the launcher, the watcher's log
  (in `/tmp`, so from this boot) shows `setPreloadPolicy ... isEnabled=false`, the policy reads false, and
  for a normal launcher, after the boot policy settles the stock home has no live process over
  60 s. For an overlay launcher the stock Home state is recorded: a close waits for a later
  launch over a nonstock full-screen card. On 6.x to 10.x there is no close, and the running
  state is recorded. On 5.x there is no preload call.

A check that fails where the docs name a limitation is recorded `XFAIL` with the citation
(`Recorder.check(..., expected=...)`); nothing uses it yet, since 11.x and 12.x run with
root-shell elevation.

The log also has a "home-app residency" section at five points (before selecting, after "Use as
Home", after the Home press, after the launcher uninstall, after the Home Chooser uninstall): is the
stock home and the launcher running (`applicationManager/running`), pids and RSS. Observation only.

## The launcher API check (`--api-check`)

Opt-in steps that test the contract of `docs/launcher-api.md` on the device. Every call is made as the
launcher with root `luna-send -a <launcher id> -n 1 luna://org.webosbrew.homechooser.service/<method>`
through `ares-do exec` (`luna-send` under `ares-shell -r` prints nothing). The hub then takes the caller to
be that installed app, with its own client permissions. The launcher must declare
`requiredPermissions: ["org.webosbrew.homechooser.applications"]` and `appCategory: ["home"]`. The default launcher is the
web fixture built with the permission (`HC_FIXTURE_PERMISSION=org.webosbrew.homechooser.applications sh
e2e/fixtures/web/build.sh build/fixtures-api`, which the runner does itself). The native and qml fixtures cannot
declare it and are refused. The run also installs a disposable non-home app,
`org.webosbrew.hcfixture.disposable` (`e2e/fixtures/disposable/build.sh`, no category and no permission).

* 2a, Home Chooser open and elevated, the launcher not chosen yet: the launcher's appinfo declares the
  permission and the category; `homechooser-access status` is noted; `getInfo`, `listApps`, `listRunning`,
  `getForegroundApp`, `getAppInfo` and `getAppIcon`
  succeed with the documented fields and allow additional fields (`getInfo`: `root`, `permitted` true and `isSelectedHome`
  false; `listApps` names the launcher and the disposable app); the launcher gets `noIcon` when
  requesting the disposable app's absent `largeIcon`, while the web fixture's oversized `largeIcon`
  checks `iconTooLarge`. Stock LG Home and Homebrew Channel exercise appinfo's real icon paths;
  when an advertised file is absent (as on 11.2.0 LG Home), the runner verifies that path on the VM
  and accepts `noIcon`.
  The disposable app (no permission) is
  refused with `noPermission` for the five methods callable before selection, and with `notHome`
  for `closeApp` and `uninstallApp` because the Home check comes first; `getInfo` reports
  `permitted:false` (or a hub denial); a caller without an app id is refused; `closeApp` and `uninstallApp` as the launcher answer
  `notHome`; `uninstallConfirmed` as the launcher is refused. Then the `request` launch parameter is tested:
  `ares-launch org.webosbrew.homechooser -p '{"request":"<launcher>"}'` opens "Use <title> as Home?", the
  runner checks that this dialog is ours and in front, presses Back, and checks that the choice is unchanged.
* 3a, after step 3 (the launcher is Home; on 6-9 after the boot of step 2): `getInfo` says `isSelectedHome:true`;
  `closeApp` closes the running disposable app (accepted, then gone from `listRunning`); `closeApp` without an
  id is `badRequest`; `uninstallApp` of Home Chooser, Homebrew Channel and the launcher itself is `protected`.
  `uninstallApp` of the disposable app answers `pending` with `confirmation:"alert"` or `"screen"`, and the
  confirmation is dismissed, never confirmed: the alert by `closeAlert` (its `alertId` is read from the
  service's `UNINSTALL_ASK` log line) or by Back when the id is not logged; the screen (Home Chooser
  opened with `confirmUninstall`) by Back on our own "Uninstall HC Fixture disposable?" dialog. The app must
  still be installed afterwards. The confirm path follows: on the screen route the runner presses Uninstall on
  our dialog and the app must be gone within 120 s (`uninstallConfirmed`); on the alert route it is `MANUAL`
  (the button calls LG's remove endpoint directly). Then a `request` while the launcher is Home must give
  the toast "<title> is already your Home.". On 6-9 there must be no compositor restart.
  With `--uninstall-via alert`, use the same two-frame proof as Reboot with the exact question
  `Uninstall HC Fixture disposable?`, buttons Uninstall/Cancel, and Cancel initially undimmed.
  Require a matching `UNINSTALL_ASK` alert-shown line since this request's log mark, naming
  the disposable target and requesting launcher, successful remove route, and accepted alert id.
  After automatic ENTER, verify the installed app set lost only the disposable fixture, yielding
  PASS (automatic press). A failed proof falls back to the 240 s MANUAL wait; timeout closes our alert.
* With `--reboot-check`, after the boot: `getInfo` (root, permitted, isSelectedHome), `listApps` and `listRunning`
  still succeed: the grants and the elevation survive a boot.
* After step 5: `getInfo` and `listApps` as the launcher fail cleanly (a JSON `returnValue:false` in
  time), and luna still answers.
* Cleanup removes the disposable app with the launcher and Home Chooser.

### Checking your own launcher

A launcher author can point the same checks at their launcher instead of the fixture:

```
python3 -I e2e/homechooser_e2e.py --vm 9.x --api-check \
    --launcher org.example.launcher --launcher-ipk dist/org.example.launcher_1.0.0_arm.ipk
python3 -I e2e/run_matrix.py --api-check --launcher org.example.launcher --launcher-ipk <ipk>   # every release
```

This is the whole scenario, steps 0 to 6, with your launcher chosen, the Home key proven to launch it,
removed and reinstalled, plus the API steps. Your appinfo must have both entries above; the first check
of 2a fails with what it read if not. What depends on the fixture is not assumed for your launcher. It is
built from your ipk, not with the permission, and the overlay rule of the preload checks follows your
appinfo's `defaultWindowType`. Your launcher's own behaviour is not tested (whatever it calls at start, its
UI). The run removes your launcher at the end like any other.

## Launchers: Dock and the fixtures

`--launcher dock` is Dock from `/home/mariotaku/Projects/webosbrew/Dock.app/dist` unless `[paths] dock_ipk` in
`e2e/hosts.local.toml` or `--dock-ipk` names another ipk. The six fixtures
(`e2e/fixtures/README.md`; built into `build/fixtures/` by `sh e2e/fixtures/build.sh`, which the runner calls itself if
an ipk is missing) are `fixture:{native,qml,web}.{normal,overlay}`. Home Chooser supports a launcher only through
`appCategory: ["home"]` in its appinfo, so the fixtures declare nothing else and draw nothing but a solid colour.

* **Overlay or not no longer changes the preload expectations** (8791f6e): on 6.x to 10.x the stock home is left
  running for every launcher, on 11.x and 12.x it is closed for every launcher. `Launcher.overlay`
  (`defaultWindowType == "overlay"` in the ipk's `appinfo.json`) is still logged and still decides how "Home opens
  the launcher" is observed (an overlay leaves the app underneath in the foreground).
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

  The formats are firmware-derived and verified on Beanjar VMs for supported fixtures: see
  `e2e/fixtures/README.md` "What the runner proves". A missing proof is a `FAIL` for every type; on a release where
  `fixtures/qml/support.json` says `false` (12.x: no qml-runner or Qt in the firmware) the qml fixture is skipped with an `XFAIL`.
* With `--launchers all-fixtures` a failure in one launcher is recorded and the next one runs.

## The update step (`--update-check`)

The old build comes from `--update-from <git ref>` (default: the last commit before HEAD that changed `app/`,
`service/`, or HEAD itself if those have uncommitted changes), built by `git archive` into a `/tmp`
directory (the working tree and `.git` are not touched; `node_modules` is linked from the main tree) and removed at the
end. Both builds are `0.1.0` today, so the version does not tell them apart: the runner compares the watcher script's md5
in the two ipks and in the service directory on the device. The old build is installed in step 1 instead of the current one
and used for step 2 (choose the launcher) and step 3 (Home opens it). Then step 3u installs the current build with
`ares-install` and no uninstall.

What the design says (`service/scripts/homechooser-home-watch` header and `check_hc`; `docs/settings-app.md` "When apps are
removed" and "What is left when the watcher is not running"): an update installs by removing first, so the watcher takes
Home Chooser as gone only if it is still gone `HOMECHOOSER_CONFIRM_DELAY` (15 s) later, else it logs `Home Chooser is
back, not removing anything`; no notification is sent for the temporary absence of an update; the watcher runs from a copy
in `/tmp/homechooser-runtime`, so replacing the app directory does not pull the script from under it, and the unit is not
restarted. So no restart is expected, and the old build's watcher code keeps running until its next start.
Asserted after a 45 s settle:

* the service directory holds the new watcher script (md5), and the app has the new version;
* the choice file and the record `home` still name the launcher;
* exactly one watcher unit and one top-level watcher process, with the same `MainPID` and `NRestarts` as before (no duplicate, no restart);
* the watcher log lines written during the update contain no `watching, home=` (no restart), no restore, revert, `isEnabled=true`, alert or toast; the optional `Home Chooser is back` line is noted either way, as is which build the running watcher is;
* the `init.d` entry links to the persistent boot restore copy and the target exists;
* on 6 to 9 the Home key hook mounts are the same set, the flag file exists and the launcher id is still baked in; elsewhere none; on 10 and later the override exists;
* the stock home preload policy is still disabled (6 and later; 5.x: not disabled);
* Home opens the launcher (with the fixture proof above);
* the app opens, lists the launcher, shows no `Couldn't` toast and has the launcher's row ticked.

Then steps 4 and 5 run on the updated install.

## System popups and screenshots

* On `w24g-ombre` (9.2.4), `legacy-broadcast-dvb` has intermittently crashed at
  `API_SI_GetLinkageUriInfo+0x197`. A full `--reboot-check --api-check` run reproduced the same fault on
  `main@b472a38` at uptime 38.50 s and on `rename-home-key-hook` at uptime 189.58 s after a Home press.
  A separate branch boot crashed at 38.52 s. In the main and 38.52 s branch crash logs, the compositor
  restarted once at about 16 s and TV snapshot/tuner initialization ran again around 37–38 s. A clean
  hooked boot also had this second initialization. An inspected stock-only boot had only the initial one;
  no crash occurred in 10 stock-only attempts.
  Main and branch use executable-identical patched QML/JS on the 9.2.4 corpus. A valid branch cache
  starts no Node rewriter at boot, and disabling the watcher preload call did not remove the second tuner
  initialization. The evidence does not isolate a new branch process as the cause or prove which part of
  the existing restart path triggers the LG fault. The runner keeps this as a `FAIL` and never confirms
  the LG crash popup.
* `w22o-ponytail-us` (10.3.1) shows the OLED "screen restoration" popup (`com.webos.app.factorywin`, buttons "Start Now", focused by default, and "Cancel") after every boot. It takes key focus, so the Home Chooser list never moves; BACK does not close it, and "Start Now" restarts the TV for 5 minutes. Before driving the list the runner probes one key; if the focus does not move it records the evidence (`oledepl isPixelRefresherCondition`, a screenshot) and waits `--wait-popup` seconds for someone to press Cancel by hand, then fails. It never presses OK on a system popup.
* Screenshots: `beanjar screenshot <vm> <file> --json` is the primary capture (about 2 s, headless, whatever the window position; `taken_by` is logged). No viewer is opened for it, so the runner no longer opens one. Only if it fails: the guest capture (`ares-do screenshot`; `com.webos.service.capture` does not work on 5.x (timeout) or 10.x/11.x/12.x (`ERROR_INTERNAL`), and after the first failure it is not tried again), then `grim` of the whole Pi desktop (`*-viewer.png`), for which the viewer is opened on first use and closed before the stop. Each fallback is logged with the word FALLBACK.
* On 5.x Home starts `com.webos.app.fullhome` as an overlay over the app in front (foreground stays the app), so step 3/4/5's "Home opens stock" looks for it running.
* Readiness: before step 1 (and after every reboot) the runner waits up to 5 minutes until root SSH (`<vm>-root`, Homebrew Channel's Dropbear on guest port 22, host 6622) answers and `ares-install -l` lists `org.webosbrew.hbchannel`; each change of state is logged (`ready`, `no-root-ssh`, `no-hbchannel`, `down`). `no-root-ssh` (developer mode answers, root SSH never does) is Homebrew Channel's failsafe mode: its `startup.sh` sets `/var/luna/preferences/webosbrew_failsafe` at the start of a boot and clears it about 12 s after its boot work, so a boot stopped within seconds of starting leaves the flag, and the next boot runs only telnetd (no Dropbear) and clears the flag after 15 s. Seen 2026-10-24 on `w20l-jhericurl`, `w22p-mullet` and `w22o-number1` after a 9 s boot each (saved logs `20261024T0031..0038Z-stop`: `boot-done` false, dmesg ends at 8.7-9.7 s; the next boot's `ps.txt` has telnetd and no `bin/dropbear -R`); `w24g-ombre`'s short boot ran 22 s and it was unaffected. On `no-root-ssh` at setup the runner restarts the VM once and waits again.
* Home Chooser is an overlay window (appinfo `defaultWindowType`), and surface-manager closes an overlay after a stretch without key input: measured on 6.5.2 (2026-10-24), about 90 s after the launch with no key and about 120 s after the last key; a close and relaunch alone does not reset it; sam logs `getAppLifeStatus ... "reason":"userTimeout"` and WAM `killApp reason : userTimeout`. On 9.2.1 the same close was seen about 90 s after the last key. The runner presses a key only while it can read our page; before the list keys of step 2 it opens Home Chooser again (and notes the close and its reason) if the page is gone, and anywhere else it stops rather than press a key at whatever is beneath.
