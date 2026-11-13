# End-to-end test

`homechooser_e2e.py` records each assertion with its raw evidence. The runner (`homechooser_e2e.py`,
`run_matrix.py` and the `hc_e2e/` package, see "Code layout") is Python 3 standard library only; Beanjar VMs are
reached with `beanjar ares-TOOL`, televisions with `ares-*` by device name (never raw ssh). pytest is optional: it
runs the same scenario as one test per phase, with live progress and JUnit and HTML reports (see "The pytest device
tests").

## Quick start

```sh
# once, for pytest and the reports (the runner itself needs only python3)
python3 -m venv .venv && .venv/bin/pip install -r e2e/requirements.txt

# the offline unit tests, no device (about 6 s); the second line runs the same tests through pytest
python3 -I -m unittest discover -s e2e -p 'test_*.py'
.venv/bin/python -m pytest e2e

# a device run against the fake device: the plan, the test ids and both reports, no VM (under a second)
.venv/bin/python -m pytest e2e/device -v --vm 12.x --tier release --dry

# smoke on one VM: the default slot (9.x), fixture:web.normal, the API smoke check
# (about 6 min on the Pi, 10 min on a board)
.venv/bin/python -m pytest e2e/device -v --tier smoke

# one VM, some phases: the earlier phases they need run first, then the cleanup
# (a late phase such as reboot takes most of a full run's time)
.venv/bin/python -m pytest e2e/device -v --vm 10.x --tier release -k reboot
.venv/bin/python -m pytest e2e/device -v --vm 10.x --tier release -k "web.overlay and home"

# the release tier: every slot, one VM at a time per machine, machines in parallel (about 1 h with the three)
.venv/bin/python -I e2e/run_matrix.py --build --tier release --pytest

# the full tier: every slot, the six fixtures, the full API check (about 1.5 h with the three machines)
.venv/bin/python -I e2e/run_matrix.py --build --tier full --pytest
```

Without `--pytest`, `run_matrix.py` runs `homechooser_e2e.py` per slot as before, and the plain commands below
(`python3 -I e2e/homechooser_e2e.py --vm 9.x ...`) still work without the venv.

**Where the results land.** A pytest run writes `e2e/results/<vm>-<timestamp>/` (or `--outdir`): `run.log` and
`results.json` as the plain runner does, the screenshots, `junit.xml` and `report.html` (self-contained; each phase's
checks, and the screenshots of a failing phase embedded). The terminal summary ends with the counts and these paths.
A matrix run writes `e2e/results/matrix-<timestamp>/`: `summary.md`, `summary.json`, and per VM `<vm>/` (the same
files) and `<vm>.console.log` (that run's pytest output).

**Reading the progress.** With `-v` each phase test is named when it starts, `test_<vm>[<launcher>-<phase>]`, and
while it runs a live line per step (`== 2 choose`) and per check (`PASS ...`, `FAIL ...`, `XFAIL ...`, `MANUAL ...`)
follows; the VM start and the cleanup at the end have lines of their own. `--log-cli-level=DEBUG` shows every line
the runner writes, which is also in `run.log` (`tail -f e2e/results/<vm>-*/run.log`). The matrix prints one line per
finished run; `tail -f` a run's `<vm>.console.log` to follow it.

Put pytest's path before `--api-check` or `--reboot-check` without a value, or give the value (`--api-check=smoke`):
the bare flag takes the next word as its value.

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
the verified Home Chooser screen with ares keys, calls the API before and after selection as the
launcher would (`getInfo`, `listApps`, and `closeApp` once it is Home; `--api-check=full` calls every
v1 method and error case), then restores LG Home and removes what it installed. It refuses to replace an
already installed launcher or change an existing nonstock Home choice. It uses no Beanjar,
QMP, screenshots, or reboot. On webOS 6–9 the boot-time re-apply of the live key filter still needs a separate `--reboot-check` test.
The output is `e2e/results/<device>-<timestamp>/results.json` and `run.log`.

`ares-package`, `ares-install`, `ares-launch`, `ares-do`, `ares-shell` and `ares-novacom` must be
on `PATH`. The root ares name must support `ares-do exec` and the web inspector forward. See
[Testing your launcher](../docs/launcher-api.md#testing-your-launcher) for manual calls.

### Maintainer: the author kit on a Beanjar VM

Our test TVs are Beanjar VMs, which have no plain ares device entry. Setting
`HC_E2E_ARES_VIA_BEANJAR=1` makes `--device` take a Beanjar target (`[HOST/]NAME`) and send every call
through `beanjar ares-TOOL [--root] -d TARGET` (the `Device` class's VM form); nothing else changes, and an
author never sets it. The VM must already have the `hbchannel` layer, Home Chooser installed and LG Home
chosen. The result directory is `e2e/results/HOST-NAME-<timestamp>/`.

```sh
HC_E2E_ARES_VIA_BEANJAR=1 python3 -I e2e/homechooser_e2e.py --device pi/w24g-ombre --api-check \
  --launcher org.webosbrew.hcfixture.web.normal \
  --launcher-ipk build/fixtures-api/org.webosbrew.hcfixture.web.normal_1.0.0_all.ipk \
  --hc-ipk <home chooser ipk, only if it is not installed>
```

## Maintainer VM scenario

This mode runs the full Home-key lifecycle on one Beanjar VM. It needs the maintainer's Beanjar
host and VM setup in addition to ares:

* Every device command goes through Beanjar's ares wrapper (Beanjar's `host/manager/README.md`, "ares
  tools"), `beanjar ares-TOOL [--root] -d [HOST/]NAME ...`: `ares-install`, `ares-launch` and the inspector
  forward (`ares-novacom -f`) as the developer-mode user, the way the old plain device did; shell commands,
  luna calls, logs, mounts and key/screenshot fallbacks (`ares-shell -r`, `ares-do`) with `--root`. A VM on
  another machine is `HOST/NAME` (the `--host` name), which Beanjar reaches with a port forward per call.
  All of it is built in one place, the `Device` class (`hc_e2e/device.py`).
* Root shell commands share one `beanjar ares-shell --root -d [HOST/]NAME -r sh` per VM, fed on its stdin
  (each command `sh -c CMD </dev/null 2>&1`, then a marker line with its status). Each `beanjar ares-TOOL`
  call to a remote VM first asks its machine for the VM and opens an ssh forward, about 2 s from this PC to
  `rockpi4a-1`; one call per command made a step outlast webOS's 180 s screensaver timer. A timeout, a
  reboot or a Beanjar stop/start ends the shell and the next command opens a new one.
* So `beanjar` (this PC's multi-host CLI, with the hosts registered under the `hosts.toml` names) and the
  Rust ares-cli (`ares-do`, `ares-shell`, `ares-install`, `ares-launch`, `ares-novacom`, on `PATH` or in
  `~/.cargo/bin`) must be installed here.
* The VM needs the `devmode` layer (prisoner) and a layer that forwards root SSH: `hbchannel` with
  `devmode`, or `rootssh`. The runner adds `devmode` and `hbchannel` while the VM is off.
* There are no ares device entries for VMs: no `<vm>` or `<vm>-root` device, no fetched keys. Beanjar
  writes its own device list per run; the runner never reads or writes `~/.webos/ose/novacom-devices.json`.
* A luna call whose reply is read runs under a terminal of the guest's own, `script -q -c "luna-send -n 1
  ..." /dev/null`, and the reply's CRLF is folded. On W22O 10.3.1 `luna-send -n 1` over `ares-shell -r` printed
  nothing to a pipe (Beanjar's measurement), though Home Chooser's daemon does get replies from a pipe, so
  the form is probed once per device with a read-only `getForegroundAppInfo`: the `script` form, else the
  plain one. A guest without `script` (a webOS 5 BusyBox may lack it) runs the plain form, and the log says
  `luna-send: no script on the guest, ran without a pty`. A call that printed nothing is never repeated,
  since a reply may have side effects (an alert, a reboot).

```
python3 -I e2e/homechooser_e2e.py --list
python3 -I e2e/homechooser_e2e.py --vm w22h-ombre            # or --vm 9.x
```

Options: `--launcher fixture:<native|qml|web>.<normal|overlay>|fixture:web.demo|<app id>` (default `fixture:web.normal`;
`fixture:web.demo` is `examples/web-launcher` and adds step 3d, OK on the demo opens its focused app, the runner's own
`org.webosbrew.hcfixture.first`, installed with it; see
`fixtures/README.md`; an app id, for a real launcher, needs `--launcher-ipk <path>`), `--launchers all-fixtures|<comma list>` (all of
them on one boot: the first runs the whole scenario, each later one is installed and chosen in Home Chooser's list over
the one before and runs only its scopes, see "Scopes"; `all-fixtures` is web, native, qml, each normal then overlay;
the log steps and the screenshots carry the launcher's name), `--update-check` and `--update-from <git ref>` (see "The update step"); `--build`
(`npm run package` first; otherwise the newest `build/ipk/*.ipk` is used); `--hc-ipk <path>` (test this
Home Chooser ipk instead); `--old-ipk <path>` (`--update-check` with an older build already made);
`--host <name>`, `--hosts-file`, `--host-mode local|remote` and `--host-device` (see "Several machines");
`--launcher-update-check` (install version 1.0.1 of a selected web fixture, or pass
`--launcher-update-ipk <path>` for another launcher id); `--outdir <dir>` (instead of `e2e/results/<vm>-<timestamp>/`; must not exist);
`--elevation luna|root-shell`; `--input-mode click|keys` (default: `click`); `--input beanjar|qmp|ares` (key backend, default: `beanjar`); `--until N` (stop after step N, no cleanup, for debugging);
`--reboot-check[=auto|all]` (reboot the VM with a launcher chosen and check what is re-applied at boot; `auto`, the
bare flag: once with the full-screen launcher, plus once with the overlay launcher on 10+; `all`: once per window type
of the run; see "Scopes"); `--removal-race` (after step 3, step 3r, below, then the cleanup); `--reboot-via later|alert`
(accepted for compatibility but not used: no release asks for a restart any more, `expects_reboot_dialog()` is always false, so
`reboot_after_choice()` is never reached; see "webOS 6-9: nothing restarts");
`--api-check[=smoke|full]` (the launcher API, see "The launcher API check"; the bare flag is `smoke`); `--no-stop`; `--wait-popup N` (wait up to N s for a system popup holding key focus to be dismissed by hand). Artefacts go to `e2e/results/<vm>-<timestamp>/` (git-ignored): `run.log`,
`results.json`, numbered screenshots (`beanjar screenshot`, the TV picture). A check is `PASS`, `FAIL`, `XFAIL`
(below) or `MANUAL`: a check the runner leaves to a person because it would mean pressing a key at a system popup.

On webOS 6–9 the Home key is redirected by a volatile `setConfigs` of
`com.webos.surfacemanager.keyFilters` (a generated `systemUi-XXXXXX.js`; no QML mounts, see `docs/home-key-hook.md`).
After the selection (and after a `--reboot-check` boot) the runner runs `homechooser-homekey status` and asserts
`active=yes` (the live keyFilters list holds our generated file) and `patch=possible` (the generator accepts this
firmware's stock `systemUi.js`), and that no `/usr/lib/qml/` bind mount exists (`check_hook_status`, `hook_mounts_match`).
The same Home-key launch proofs apply on every build.

Log evidence includes the retained `/var/log/messages.N.gz` archives in chronological order;
boot and service activity can rotate the active file before an assertion reads it. After each
hook boot, the run also records the compositor's main-QML load lines and retained crash lines.

Before booting, the runner stops if any VM is running on its machine. It adds the `devmode` and `hbchannel`
layers and `link=lan` while the VM is off (standing permission), boots it and waits for its LAN address and
for root SSH through `beanjar ares-shell --root`. It stops the VM at the end (not `w21p-kisscurl`).

## Home and screensaver scenario

`--scenario screensaver` runs S0–S4 with the normal web fixture by default. It performs the
usual install, elevation, and launcher selection, then cleanup; it omits the other lifecycle
steps. `--scenario all` adds S0 before installation and S1–S4 after selection to the full
lifecycle. The default `--scenario lifecycle` preserves the existing run. This scenario requires
Beanjar remote input (`--input beanjar`), a normal fixture (web/native/supported qml), and a
complete run (`--until 5`). It is unavailable in the author-TV `--device` mode. These commands
are for a later, coordinated VM run; development and the tests below are entirely offline.

```sh
python3 -I e2e/homechooser_e2e.py --vm 10.x --scenario screensaver
python3 -I e2e/homechooser_e2e.py --vm 11.x --scenario all --launcher fixture:web.normal
python3 -I e2e/homechooser_e2e.py --vm 10.x --scenario screensaver --saver-start beanjar
```

| Step | Assertions |
| --- | --- |
| S0 | With Home Chooser and its choice file absent, start from Homebrew Channel separately for BACK and HOME. Confirm a shown screensaver, press the key and confirm dismissal, then record the foreground. Press HOME once more and record that foreground too. Foreground destinations are firmware observations, not S0 pass/fail expectations. |
| S1 | Put the selected fixture in front, start the screensaver, and observe for 5 seconds. Choice, the entire defaultApps reply, stock-home preload policy, daemon MainPID/NRestarts/active state, and boot id must stay unchanged. No daemon closeByAppId or setPreloadPolicy call is allowed. On 11+, where the daemon follows foreground and life events, its log lines naming a screensaver app are recorded; ignoring the screensaver is judged by the absence of actions, since the daemon logs actions and not skipped events. |
| S2 | Dismiss S1's saver using the measured BACK behavior, put hbchannel in front, and start again. HOME must dismiss to the destination observed in S0: the previous app for wake-only firmware, or the chosen launcher where stock dispatched Home immediately. On wake-only firmware, a second HOME must open the chosen launcher where S0's second HOME opened stock Home. Home Chooser state stays unchanged. |
| S3 | From hbchannel, start again and press BACK. Its destination must match S0's BACK observation, with a stock-Home dispatch mapped to the chosen launcher. The following HOME must likewise match S0: where it opened stock Home, it must now open the chosen fixture. Home Chooser state stays unchanged. |
| S4 | Start again, dismiss with BACK, recover a stale Ready state if necessary (recorded as RECOVERED), then request tvpower/power/reboot as root. Verify a new boot and reuse the existing boot-apply checks for the choice, record, watcher, hook, and preload policy. |

Each step records `power/getPowerState` and, on 7+, `power2/getPowerState`, foreground, daemon unit,
choice file (including read status), defaultApps, stock-home preload policy and boot id, with a
screenshot before and after. Additional snapshots record S1 while shown and button dismissal.
The preload comparison uses the complete parsed stock-Home policy entry; the full raw reply
is retained, but unrelated app entries and their array ordering are not HC state assertions.
The successful start proof on 7+ is `power2`'s `sub state: screen saver`, with no Ready
transition. On 6.x it is `power`'s actual `state: Screen Saver`; `returnValue:true` from a trigger
or `Active` with `processing: Screen Saver Ready` never proves a shown saver. Dismissal requires
the native active state and, after S0, the expected foreground.

S0 stores both keys' `previous`, `wake`, and subsequent `home` foreground replies in its
`results.json` evidence, plus logs and screenshots. After the follow-up HOME it observes a
foreground change for up to 15 seconds and records the resulting foreground even if unchanged.
A wake destination or a second HOME destination different from stock Home does not fail S0.
Failure to show/dismiss still fails (or remains BLOCKED for Ready); precondition refusals remain
SKIPPED. An unsuccessful S0 blocks later steps, preserving the existing dependency handling.

The measured baseline supplies S2/S3's expectations without a per-firmware key-behavior table.
An observed previous-app result maps to the previous app used by that step, an observed stock
Home result maps to the selected launcher, and other observed destinations are retained.
Unreadable baseline foreground evidence blocks the comparison. S2 deliberately uses hbchannel
under its saver so a wake to an already foreground launcher cannot stand in for Home dispatch.
The stock 7.6.0 and 9.2.1 device results showed that HOME and BACK both wake to hbchannel,
despite a real `_WEBOS_WINDOW_TYPE_SCREENSAVER` window being shown; this is valid baseline
behavior, separate from beanjar#56. A firmware that dispatches Home on the first press is
measured and checked accordingly.

On 6.5.2, `power2/getPowerState` and SAM `getForegroundApps` returned Unknown method. The 6.x
path uses `power/getPowerState` and the supported singular `getForegroundAppInfo`, and marks
power2 explicitly unqueried/unavailable in snapshots instead of querying it or treating a
refusal as an empty successful response. Its legacy shown-state requirement remains strict;
the retained 6.5.2 device run showed only Active/Ready and therefore supplied no shown-state
proof. No real 6.5.2 shown-state success is claimed.

The default `--saver-start tvpower` trigger is a root `luna-send -n 1` with no `-a`, calling
`luna://com.webos.service.tvpower/power2/turnOnScreenSaver` with `{}` on 7+. On 6 it uses
`power/turnOnScreenSaver` (accepted on 6.5.2, but that device run stalled in Ready; successful
shown-state completion remains unvalidated there).
Releases below 6 are SKIPPED. Dismissal uses `beanjar remote press <vm> HOME|BACK`, with the
existing VNC frontend started first. The harness keeps button dismissal under test, changes no
TV settings, and never confirms LG dialogs. Start/state waits are limited to 45 seconds,
foreground/dismissal waits to 30 seconds, and guest-reboot detection to 240 seconds, followed
by the existing bounded boot-readiness waits (all use the host timing slowdown).

For VMs whose firmware trigger stalls, opt into `--saver-start beanjar`. S0–S4 then use
`beanjar screensaver start <vm> --timeout 60 --json` through the local multi-host CLI
(`HOST/NAME` for a remote host). Beanjar waits for both native state and a real screensaver
window; the harness records its JSON and independently confirms native state. Every dismissal
is still a HOME/BACK remote-button check. This option explicitly selects the helper; the default
firmware trigger does not silently fall back. A missing helper makes this opt-in start SKIPPED;
a failing helper or unconfirmed native state is BLOCKED. Helper availability is probed with
local `beanjar screensaver --help` once per runner host; this does not contact a VM.
This helper is unavailable on 6.x: it requires power2 and plural foreground window types.
The harness never invokes it there. A 6.x Ready stall remains BLOCKED (#56), with legacy
power/foreground evidence and normal synced-stop cleanup; tvpowerd is never restarted.

Before S4's tvpower reboot and every Reboot alert action (including its retry and manual
fallback), the harness reads the supported native power states (power only on 6.x). The main
scenario's Later/fallback and `--reboot-check` host restarts also get this check; their existing
reboot route is preserved.
On 6.x, a captured Ready state blocks the reboot request because the recovery helper is
unsupported; cleanup uses the normal synced stop without trying the helper.
On 7+, if `power/getPowerState` has `processing: Screen Saver Ready` or `power2/getPowerState` has
`sub processing: screen saver ready`, it captures diagnostics and runs
`beanjar screensaver stop <vm> --timeout 60 --json`. It records the helper JSON, re-reads the
available power states, and only proceeds after successful recovery with readable states and
no Ready transition. A `RECOVERED` entry in `results.json` and the summary explicitly says
`recovered stale Screen Saver Ready via beanjar screensaver stop, beanjar#56`, with the helper
result and both before/after states. The later reboot/boot-apply assertions still run. S4 also
uses this recovery if BACK was delivered but its completion wait remained stuck in Ready;
it verifies the chosen launcher and unchanged Home Chooser state after recovery. Other
screensaver steps retain their existing button and firmware-path assertions.

This stop helper uses LG's built-in screensaver window, native wake, and normal close to
recover beanjar#56 without a GUI restart. The harness does not substitute a direct wake for
its remote-button assertions. A failed stop, malformed helper result, unreadable post-recovery
state, or persistent Ready remains BLOCKED, and no reboot request follows. If an older Beanjar
lacks the subcommand, recovery is skipped, a notice is logged once, and the previous reboot
refusal/BLOCKED behaviour is retained. A failed help probe for another reason remains a helper
failure. Helper calls have a 60-second native deadline and 90-second process timeout; help has
a 15-second process timeout, all subject to the host timing slowdown.

A refused trigger naming disabled screenSaverEnabled, incomplete first use, or an invalid
sub state is **SKIPPED**, retaining the reply. Unknown refusals, foreground parity mismatches
after S0, missing shown/dismissal proof, or Home Chooser mutations are **FAIL**. A `Screen Saver Ready` state that is not
recovered before reboot, or S4 reboot's `power is busy`, is **BLOCKED (beanjar#56)**: VM videoads
can fail with `Failed to load lgchannels SDK`, close, and leave tvpower stuck. Both power replies and retained
tvpower log lines are captured before recording the refusal/timeout outcome. The scenario never
relabels a Home Chooser assertion failure as that VM fault. Later steps inherit SKIPPED from a
skipped prerequisite and BLOCKED from an unsuccessful prerequisite, with before/after artefacts
still captured. These statuses appear in `results.json` and summary counts; FAIL retains the
runner's nonzero exit status. A run containing BLOCKED/SKIPPED needs a later completed run for
screensaver validation.

The fake device models trigger, shown/Ready state, HOME wake-only and direct-Home behavior,
learned BACK behavior, second-HOME dispatch, legacy 6.x replies, helper recovery/failure,
missing helpers, busy reboot, and boot apply. Run its PASS/BLOCKED/SKIPPED/FAIL/RECOVERED checks
and the rest of the offline harness suite with:

```sh
TMPDIR=/tmp/nw python3 -m unittest discover -s e2e -p 'test_*.py'
```

## The matrix

| Release | VM | Elevation | How sure |
| --- | --- | --- | --- |
| 5.x | `w20l-jhericurl` | luna | verified: 5.6.1 (os-release) |
| 6.x | `w21a-kisscurl` | luna | verified: 6.5.2 (os-release). `w21p-kisscurl` (6.6.0) belongs to another project and is not in the matrix |
| 7.x | `w22p-mullet` | luna | verified: 7.6.0 |
| 8.x | `w22o-number1` | luna | verified: 8.3.0 (beanjar's "webOS 23" is a platform label) |
| 9.x | `w22h-ombre` | luna | verified: 9.2.1 |
| 9.x | `w24g-ombre` | luna | verified: 9.2.4 (`w24p-ombre` runs the same firmware) |
| 10.x | `w22o-ponytail-us` | luna | verified: 10.3.1 |
| 11.x | `w22o-queue-us` | luna | verified: 11.2.0 (`w22o-queue`, `w22o-queue-jp` run the same firmware) |
| 12.x | `w24g-spacebuns` | luna | verified: 12.0.0 (os-release) |

The runner checks the release it finds (`/usr/lib/os-release`) against the row and records the
result.

## Which runs a change needs

A change gets the runs its reach calls for, not the whole matrix every time (the tiers are below):

| Change | Runs |
| --- | --- |
| Any change | The offline suites: `service/scripts/tests/test-*.sh`, `cmake -S service/test -B build/service-host && cmake --build build/service-host && ctest --test-dir build/service-host`, `cd app && npm run lint && CI=true npm test` |
| Code gated by release (a `MAJOR` bound, the 5.x branch, the 11/12 profile path, the hook version gate) | The releases inside the gate, plus one just outside it |
| Shared code (the apply flow, the watcher's main loop, the uninstall cleanup) | One release per family: 5.x, 9.x (Home key hook), 11.x (profile path, LG access groups) |
| Only the runner or the docs | `--tier smoke` |
| A release or a tag | `--tier release` |
| A new launcher runtime, a fixture change, an API method or errorCode on the device | `--tier full` |

`--reboot-check` is used where boot behaviour is involved (the watcher, the preload policy, the
live key filter re-applied at boot). Settles are polled within their old bounds (see "Waits"); sampling windows,
which are the verdict, stay as they are.

## Tiers

`run_matrix.py --tier` (and pytest's `--tier` for one VM) sets the defaults of a pass, from `TIERS` in
`hc_e2e/tiers.py`; an option given on the command line wins (`--vms`, `--launchers`,
`--api-check=full`, `--reboot-check=all`, `--update-on`, `--uninstall-via`). Without `--tier` the options mean what
they always did.

| Tier | VMs | Launchers (one boot per VM) | Checks | Expected wall time |
| --- | --- | --- | --- | --- |
| `smoke` | `default_slot` of `hosts.toml` (9.x) | `fixture:web.normal` | `--api-check` (smoke); no reboot, no update | about 6 min on the Pi, 10 min on a board |
| `release` | every slot | `fixture:web.normal`, `fixture:web.overlay` | `--api-check` (smoke), `--reboot-check`, `--uninstall-via alert`; `--update-check` on one slot, `w24g-ombre` | about 1 h with the three machines |
| `full` | every slot | `all-fixtures`: the six, web first | `--api-check=full`, `--reboot-check`, `--uninstall-via alert`, `--update-check` and `--launcher-update-check` everywhere | about 1.5 h with the three machines |

```
python3 -I e2e/run_matrix.py --dry-run --tier release      # the runs, their flags and each run's plan
python3 -I e2e/run_matrix.py --build --tier smoke
python3 -I e2e/run_matrix.py --build --tier release
python3 -I e2e/run_matrix.py --build --tier full
python3 -I e2e/run_matrix.py --build --tier release --update-on all   # a tier with one default changed
```

The release tier's update check runs on one slot: the 9.x Home key family carries most of the update assertions (the
live key filter, the flag and the baked id survive the update), and that slot is on the fastest machine. The 10+
override and the 11+ re-elevation after an update are in the full tier. The times are estimates from the per-step
times of earlier single-launcher runs, less the waits that are now polled.

## The pytest device tests

`e2e/device/` runs one VM run as one pytest session (`device/plugin.py`, loaded by `e2e/conftest.py`; the logic is
in `hc_e2e/phase_tests.py`, standard library). It needs `e2e/requirements.txt` (pytest and pytest-html, pinned).

* **Options.** `--vm`, `--host`, `--tier smoke|release|full`, `--launchers`, `--hc-ipk`, `--api-check[=smoke|full]`,
  `--reboot-check[=auto|all]`, `--uninstall-via`, `--update-check`, `--outdir` mean what they mean to
  `homechooser_e2e.py`; any other runner option goes through as `--hc-arg=<word>`, once per word
  (`--hc-arg=--input-mode --hc-arg=keys`, `--hc-arg=--build`). `--tier` fills what is not given from `TIERS`, as
  `run_matrix.py` does for one slot (the release tier's update check only on its slot); `--tier smoke` without `--vm`
  takes the default slot, and the release and full tiers over every slot are `run_matrix.py --pytest`. The options
  become a `homechooser_e2e.py` command line, which the runner's own parser checks before anything starts; the header
  of the run prints it with the planned phases.
* **One test per planned phase.** The tests come from `run_plan()` for the slot's release, one per `(launcher,
  phase)`, named `test_<vm>[<launcher>-<phase>]`: `test_w22o_ponytail_us[web.overlay-reboot]`; the launcher is the
  fixture's name or an author launcher's id. `-k` matches those words: `-k reboot`, `-k api` (`api-selected`; the API checks before the choice run in `choose`), `-k "web.overlay and home"` (its `home-proof` and `window-home`), `-k "not cleanup"`. Without `--vm`
  or `--tier` no device test exists, so `pytest e2e` runs the unit tests only. The run re-plans from the release it
  finds on the device; a test whose phase is not in that plan is skipped with the plan it found.
* **Order and dependencies.** The tests run in plan order on one boot, through `PlanRun` (`hc_e2e/plan.py`), the
  loop `homechooser_e2e.py` itself runs, with its rules: crash windows per group of phases, a later launcher's
  exception recorded and only that launcher stopped. A selected phase first runs every earlier phase of the plan that
  has not run, since each one leaves the device in the state the next expects; they are named in the test's
  `setup` property, in its failure message and in the HTML. Their FAIL checks fail the test that pulled them in.
* **Verdicts.** Every `r.check` is recorded as before (`run.log`, `results.json`). A phase test fails when a check
  recorded during it is `FAIL`, with each failing check, its step and evidence in the message; it is `xfail` when it
  recorded an `XFAIL` and no `PASS` (the qml gate); `MANUAL` never fails it. The counts per status, the phase, the setup phases
  and the screenshots taken are the test's JUnit properties. When a phase of the first launcher raises, the run ends
  as on the command line: that test fails with the traceback and every later phase but the cleanup is skipped with the reason; when a
  later launcher's phase raises, only that launcher's remaining phases are skipped.
* **Cleanup.** The session fixture starts the VM before the first test (`VmSession.bring_up`, as the command line
  does) and, after the last one, whatever failed, runs the cleanup (step 6) unless a selected `-cleanup` test made
  it, then stops VNC and the VM (`VmSession.tear_down`) and writes `results.json`. A FAIL of a cleanup made there is an
  error at teardown. If the VM does not come up, every test fails with the reason, and the stop still runs.
* **Reports.** `junit.xml` (xunit1, for the properties) and `report.html` go into the run's directory unless
  `--junitxml` or `--html` say otherwise.
* **`--dry`.** The same plan, PlanRun, fixture and reports against a fake device that records its calls
  (`hc_e2e/dry.py`, `dry-calls.json`); `--dry-fail`, `--dry-manual` and `--dry-raise` (comma lists of phases;
  `vm-start` for the VM start; `interrupt:<phase>` for a Ctrl-C in it) inject a FAIL, a MANUAL or an exception. A Ctrl-C in a phase fails that test (pytest exit status 2, `INTERRUPTED` in the summary), runs no later phase and still runs the cleanup. `e2e/test_phase_tests.py` runs it.

**The matrix with pytest.** `run_matrix.py --pytest` keeps its scheduler and runs each slot as
`python -m pytest e2e/device --vm <vm> --host <host> --outdir <matrix>/<vm> --hc-arg=...`, with the same flags the
plain run would get; run it with the venv's python. pytest-xdist with `--dist loadgroup` was not used: it fixes each
test's worker when the tests are collected, so it cannot express a slot that may run on any of several machines, the
wait for a machine with no VM running (ours or another session's), a refused run, or Ctrl-C stopping each running VM;
`run_matrix.py` already guarantees one VM per machine at a time, with one worker thread per machine claiming slots
under a lock. Each pytest session is one VM, so its fixture's teardown stops exactly that VM.

## Scopes

A check depends on the release, on the window type, or on the launcher itself, and one VM run makes it once at that
scope. `PHASES` in `hc_e2e/plan.py` is the one table of it; every result in `results.json` carries its phase's
`scope`, and the run log starts with the run's plan (`run_plan`).

* **per-release**, independent of the launcher: made once, with the first launcher of the run.
* **per-window-type**, normal or overlay: made with the first launcher of each window type.
* **per-runtime**, "SAM launches this app as Home and the Home key reaches it": made by every launcher. Each fixture's
  window is its own code path (SDL, `WebOSWindow`, a web page), so the key is the launcher, not the runtime alone.

All launchers of a run share one boot. The first runs the whole scenario; each later one is installed, chosen in Home
Chooser's list over the current one (a live switch, as a user makes it), checked at its scopes, and left installed.
Step 5 then removes Home Chooser with the last one chosen, and step 6 removes every launcher.

| Phase | Scope | What it checks | Who runs it |
| --- | --- | --- | --- |
| 0 baseline, 1 install | per-release | release, nothing installed, Homebrew Channel; install, elevation | first launcher |
| 2 choose | per-release | the Home Chooser UI: first open, stale marker, dialog, Done toast, 11+ page stays, choice/record/watcher/init.d, key filter and flag (6-9), override (10+), device texts, reopen and tick | first launcher |
| 2a, 3a API | per-release | `--api-check`, smoke or full; the request dialog and toast | first launcher |
| 2s switch | per-runtime | install, choose in the list over the current launcher; Done toast, choice/record/watcher, key filter (6-9), override (10+) | each later launcher |
| window after the choice | per-window-type | the preload policy and the home gate (6-9), idleApp (10+) | first launcher of each window type |
| 3 Home | per-runtime | Home from an ordinary app opens the launcher, sam's launch proof | every launcher |
| qml gate | per-runtime | qml on a release where `fixtures/qml/support.json` says false: `XFAIL` | each qml launcher |
| window after Home | per-window-type | 10+: the watcher closes LG Home once the launcher is in front | first launcher of each window type |
| 3d demo | per-runtime | OK on the demo opens its focused app | `fixture:web.demo` |
| 3r reboot | per-window-type | the boot checks; the API after a boot (first launcher) | see below |
| boot cache | per-release | 6-9 after the first boot: no boot cache, no configd override, Home opens the launcher | first launcher |
| 3u, 3v, 3f | per-release | the Home Chooser update, the launcher update, the restore repair | first launcher |
| 4 removal | per-release | stock restore on removal, the alert, reinstall | first launcher |
| 5 uninstall, 6 cleanup | per-release | residue, the toast, Home opens stock; test apps removed, the final screen | the run |

Reboots, at most one per window type and run: `--reboot-check` (`auto`) boots once with the full-screen launcher, plus
once with the overlay launcher on 10+, where the boot behaviour differs (the last input launch, the overlay's
`idleApp`). On 5 to 9 that is one boot; on 10 and later two. `--reboot-check=all` also boots with the overlay launcher on
5 to 9 (the 6-9 gate puts the last input app in front). A later launcher's boot comes in its own turn. On 10+, when the
first overlay launcher comes after a full-screen one on the same boot, LG Home no longer runs; the runner starts it
before that launcher's Home press, so the close rule is checked from the state a fresh boot leaves.
On webOS 6–9 it also runs `homechooser-homekey prepare` once on the TV (the native generator must accept this
firmware's `systemUi.js`; `cache_prepare_measure`), and after the boot asserts the keyFilters selection is active again
and that there is no boot cache and no configd factory-db override (`step_cache`). LG firmware files are never edited.

## Waits

Fixed waits were replaced by polls of what they waited for, with the same upper bound and the same verdict:

* 6-9, the preload checks after a choice or a boot: a fixed 60 s (30 s after a boot) and then 60 s of samples came
  before the home gate check, and the samples decided nothing; now the runner polls for the gate mounted and stock
  Home not running, within the same 120 s (90 s), and then makes the gate check as before.
* 10+, after a boot with a full-screen launcher: the 30 s settle ends early when the watcher logs its close of LG
  Home; the 60 s sample after it stays, since it is the verdict ("not running over 60 s").
* The Home press: the 6 s after the key and the 3 s after closing the app to be launched are polls (26 s for the
  result, 3 s for the close); the same for closing Home Chooser before it is opened again.
* Opening Home Chooser for a later launcher's switch and for the post-update check (`3u-app`), right after a Home
  press: the runner first polls up to 20 s for SAM to name a foreground app, then launches Home Chooser and polls
  up to 30 s for its page to be listed by the inspector and to stay listed for 4 s; on a miss it closes it and
  launches it once more. Home Chooser is an overlay, and surface-manager closes an overlay (`windowPolicy`) when a
  card maps beneath it: on the slower pi VMs the launcher's card from the Home press mapped about 8 s after the
  press, after Home Chooser had opened. Each `ares-launch` rc and the verdict are logged. The foreground alone
  is not the test: on 9.x SAM names the card beneath our overlay while the list is on screen.

Kept, because nothing observable ends them: the 45 s after the update install (the check is that no restore happens
within the watcher's confirmation window), the short settles before a negative check (no toast, the app still
installed, the choice unchanged), 0.8 s between keys, the 3 s after an app comes to the front before Home is pressed,
the demo's 8 s to render, and the waits for our own alert and toast to be shown on screen.

## Beanjar host abstraction

Everything on the Beanjar machine (`beanjar list/info/settings/start/stop`, the QMP `sendkey`
through Python 3, the TV screenshot through `beanjar screenshot`) goes through the
`BeanjarHost` class (`hc_e2e/beanjar.py`). The mode comes from the machine's entry in `e2e/hosts.toml`: remote over
`ssh <alias>` (the Pi: `ssh raspberrypi`), or local (`local = true`, beanjar on the PC running the
runner). It is no longer guessed from `beanjar` being on PATH: this PC has Beanjar's own multi-host CLI
on PATH, but the QMP sockets and the VM files are on the machine the VM runs on.
`--host-device` names another ssh config alias and `--host-mode` forces a mode for one run. The Pi is
a Linux host, so it is reached with ssh; the `ares-*` tools are for the televisions and VMs only.
VMs are listed with `beanjar list --local --json` (that machine's own VMs only); the QMP socket is under
the VM directory `beanjar info` reports. No remote quoting exists outside that class. The screenshots the steps look at come from
`beanjar screenshot` (Beanjar 0.3.1+), saved on the host and fetched with `scp` in remote mode; desktop capture has no standard-library fallback.

## Several machines

The default `--input-mode click` reads `beanjar elements HOST/NAME --json` on this PC,
selects exactly one element by exact text, optional kind and region, and clicks its centre with
`beanjar remote click HOST/NAME X Y --frame WxH`. Missing or ambiguous matches fail without input.
Launcher labels accept text/button kinds, excluding icons which can repeat the label. Each action
logs its input path and checks its effect using a fresh screen read or the existing state assertions.
The screen/source dimensions, scale and offsets are noted once per run. Hosts without
`remote click` use keys with an explicit NOTE. `--input-mode keys` selects the old navigation.
HOME proofs, Back cancellation and the probe proving remote keys reach Home Chooser always use keys.
The ares-only `--device` contract keeps keys because it has no Beanjar display host.

The key backend defaults to `beanjar remote press HOST/NAME KEY`, using the configured Beanjar host
name (local VMs use `NAME`). Register hosts here with the same names as `hosts.toml`.
`OK`/`ENTER` map to `ENTER`; HOME, BACK, arrows and digits keep their names. The runner reads
`remote layout --json` once and rejects missing or inactive keys. `--input qmp` uses QMP keyboard
input; `--input ares` uses `ares-do key`.

With clicks or the Beanjar key backend, the runner starts beanjar-vnc after each Beanjar start and
after a tvpower reboot returns. An already running beanjar-vnc is accepted. `--port 0` chooses a free
host port; the VM's LAN listener stays on 5900. VNC stops before each Beanjar stop and at exit,
including failures, Ctrl-C and `--no-stop`. QMP/ares with `--input-mode keys` do not start VNC.

Before installing Home Chooser, preflight reads `ares-install -l` and each developer app's appinfo.
It removes foreign apps declaring `appCategory` containing `home` (the candidate rule in
`app/src/homechooser/index.js`; the service API forwards the category), plus known HOME takeover ids
in `KNOWN_HOME_TAKEOVERS`. HomeBack (`com.homebrew.homeback`) hooks lginput2/micomservice through
Frida without declaring the home category. Home Chooser, `org.webosbrew.hcfixture.*` and Homebrew
Channel are exempt. Each removal records id, version and rule; non-launchers are retained and noted.
Removal failure fails setup. After removal the runner reads the installed Homebrew Channel
`startup.sh` for its `run-parts` directory and removes only package-owned symlinks or exact-id
scripts executing the package path; ambiguous entries are retained and noted. It then restarts the
VM through the normal stop/start/VNC path before any HOME tests, clearing injected in-memory hooks.

Host helper code must use `python3` with the standard library only (or baseline POSIX shell/coreutils), without extra tools such as socat, nc, jq or grim; Beanjar itself is required.

The Beanjar machines are listed in `e2e/hosts.toml`; `e2e/hosts.local.toml` (git-ignored) is read over
it, for this PC's own machines. Without either file the one machine is today's: `pi`,
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
names must be unique across the machines: matrix slots, result directories and temp files are named after
the VM only. A VM that exists on two machines (a copied image) is not run until `vms = [...]` pins it to
one of them.

To add a machine: install Beanjar on it, give it key login from this PC
(`ssh <alias-or-user@host> beanjar list --local --json` must work without a prompt), add it with `beanjar host add <name>
ssh://<user>@<alias>` so the multi-host CLI here knows it too, and add `[hosts.<name>] ssh =
"<alias>"` to `e2e/hosts.local.toml` (or `hosts.toml` when it is shared) under the same name. The VMs
run with `link=lan`, so their LAN addresses are reachable from this PC wherever they run.

`MATRIX` in `hc_e2e/matrix.py` contains release slots. The VM field accepts a name or an ordered
 tuple of alternatives (for slots whose VM has a different name on another machine). Each row runs
 exactly once, using a candidate reported by host discovery. The three 9.x rows remain separate
 slots; `--vms 9.x` selects the first, as before. The VMs of `pi` and `rockpi4a-1` have the same
 names, so `hosts.toml` pins each matrix VM to one of them (below); a single-run
 `homechooser_e2e.py --vm 6.x` runs on the default host (`pi`) unless `--host` says otherwise.

`run_matrix.py` has one worker per eligible machine and a shared pool of slots. A free worker claims
 a compatible slot under a lock. It favours shared work on machines with less exclusive work, leaving
 host-only slots for machines that need to run them; ties favour more alternative hosts, then matrix
 order. Candidates on the same host follow their order in the slot. Runs that keep a launcher go last.
 Actual assignments follow completion times. `--dry-run` simulates equal-cost runs weighted by the
 configured slowdown; its printed plan is an estimate, not a reservation.

The committed hosts are `pi` (`raspberrypi`), `rockpi4a-1` and `rockpi4a-2`
 (`mariotaku@rockpi4a-N.lan`, accepted directly by ssh and scp). `rockpi4a-2` has `opt_in = true`: it
 is the user's own machine, so a run with no `--hosts` never picks it; `--hosts rockpi4a-2` does.
 Each host can set `slowdown = 2.0`; the default is `1.0`, values must be finite and at least `1.0`.
 The Rock Pi boards use 2.0, the ratio measured against the Pi (a release-tier run of one VM: 10.x
 8m47s on `pi`, 17m56s on `rockpi4a-1`). Pi timing is unchanged.
 `pi` and `rockpi4a-1` carry the same VM names, so `hosts.toml` pins each matrix VM to one of them
 (`vms = [...]`) to balance the two: `rockpi4a-1` takes 5.x to 8.x, `pi` the three 9.x slots and
 10.x to 12.x, which finish within a few minutes of each other. `w21p-kisscurl` is never pinned or run.
 The unit test `CommittedPlan` in `test_run_matrix.py` holds the plan, the balance and both
 exclusions; `python3 -I e2e/run_matrix.py --tier release --dry-run` prints it.
In the per-run process the factor scales Python settling sleeps and polling intervals, boot/LAN and
 device readiness deadlines, inspector readiness, scenario/notification/alert waits, popup/manual
 waits, reboot and sam relaunch deadlines, preload settling and observation sleeps, and command
 timeouts through `run()` (including Beanjar/ares). Embedded guest shell sleeps, socket/HTTP probe
 timeouts, monitor thread joins, build subprocesses, and matrix `--wait-idle` are unchanged.
 Preload observation duration and polling interval both scale.

```
python3 -I e2e/run_matrix.py --dry-run                            # the plan for the whole matrix
python3 -I e2e/run_matrix.py --build --tier release               # the whole matrix, see "Tiers"
python3 -I e2e/run_matrix.py --build --reboot-check               # the whole matrix, one launcher
python3 -I e2e/run_matrix.py --vms 5.x,9.x,11.x --launchers all-fixtures
```

* `--hosts pi,rockpi4a-1` limits discovery and execution to those configured hosts.
* `--vms` takes explicit VM names (restricting a slot to that candidate) or releases (retaining its
  alternatives); default is all matrix slots, or the tier's. `--api-check`, `--reboot-check`, `--uninstall-via` and
  `--launcher-update-check` go to every run; `--update-check` to the slots of `--update-on` (default all). Options it
  does not know (`--wait-popup N`, `--elevation`, ...) go to every run as given; `--no-stop`, `--vm`, `--host` and `--outdir` are
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
  serialisation, the refusals and Ctrl-C (fake machines and a fake runner), offline.

What runs in parallel, and why it is safe:

* Device access: each `beanjar ares-TOOL` call has its own device list and, for a remote VM, its own port
  forward, so runs against several machines never share one; the user's ares device list is not touched.
* Temp files on a machine carry the VM name and the runner's pid (`/tmp/e2e-tv-<vm>-<pid>.png`); the
  inspector's local port is picked per run and another is tried if the forward cannot bind it.
* Each run has its own result directory.

Beanjar's own multi-host CLI (`beanjar host add/list`, `beanjar list` across hosts, `<host>/<vm>` for
start, stop, info and screenshot) reaches the machines the same way, ssh per call. The runner does not
depend on it: it runs `beanjar` on each machine over ssh, which works whether or not this PC has Beanjar.

## Elevation strategies

* `luna` (every release, the default): the app elevates its own service through Homebrew Channel's
  `elevateService`; the runner only verifies the service runs as root (`ps`). On 11.x and 12.x this
  needs Homebrew Channel 0.8.0 or later, which serves `elevateService` to ordinary apps there; older
  releases refuse it with `Denied method call`. The full tier passed with it on 11.2.0 and 12.0.0
  on 2026-11-13 (`w22o-queue-us`, `w24g-spacebuns` on rockpi4a-1).
* `root-shell` (`--elevation root-shell`, 11.x and 12.x with an older Homebrew Channel): the runner
  runs Homebrew Channel's own helper as root (`beanjar ares-shell --root`),
  `…/org.webosbrew.hbchannel.service/elevate-service org.webosbrew.homechooser.service` (what
  `services/service.ts` runs), then makes the service quit so the root instance starts, and verifies
  the same outcome. It is a test-harness substitute: the app's own elevation is not exercised. The
  strategy used is written at the top of every run log.
  The `--update-check` step reinstalls the service, which loses that root, so after the update the runner repeats
  the elevation (and verifies it) before it opens the app again.

## Steps and what each asserts

Expectations come from `docs/settings-app.md` (Persistence, When apps are removed), the service
and Home key hook docs.

0. Baseline: release matches the row; no Home Chooser, Homebrew Channel present; state recorded.
1. Install Home Chooser and the launcher; elevate (strategy above).
2. Open Home Chooser, prove remote keys reach it, then click the launcher label and Use as Home.
   With `--input-mode keys`, drive it with remote keys (`beanjar remote press`; QMP `sendkey` with
   `--input qmp`, or `ares-do key` with `--input ares`): the focus is read from the app's DOM through the web inspector (port 9998,
   forwarded with `ares-novacom -f`; rows carry `data-app-id`), moved to the launcher's row, OK,
   then the "Use <name> as Home?" dialog is checked and "Use as Home" is pressed. Asserted:
   choice file holds the id; `general/defaultApps` `home` is the id; `homechooser-home` active;
   `init.d/homechooser` links to the persistent boot restore copy; on 6 to 9 the live key filter is selected at once
   (`homechooser-homekey status` `active=yes`, no QML mounts) and the flag file exists, elsewhere not; on 10 and later the profile override exists and `getProfiles` shows `idleApp`
   naming the launcher (`support` true), or `support` false for an overlay launcher (bounded wait 30 s;
   the stock value is noted at step 1 before Home Chooser is installed).
   On 6 to 9 the first choice asks for no restart: see "webOS 6-9: nothing restarts". The
   toast is "Done. Press Home to try it." on every release. On 11.x and 12.x the runner
   also checks that the Home Chooser page is still visible immediately after confirmation. The installed build carries the
   current texts (`grep -F` on the device: the watcher in the service package and its running `/tmp` copy, the
   service binary, the app bundle) and none of the old restart wording.
   Also asserted: at the first open after a fresh install there is no error toast (WAM's
   `localStorage` outlives `ares-install --remove`, so a `homechooser.pending` marker left by an
   earlier run must not read as a failed apply; the runner also plants such a marker through the
   inspector, reopens, and asserts no toast and the marker cleared), and after the
   apply the panel is opened again with no error toast and the chosen row ticked.
3. Home from an ordinary app (Homebrew Channel; docs T4): the launcher is running after having
   been closed first, or in front (an overlay launcher bar is an overlay window, so the app
   beneath stays "foreground"). A press with the stock home in front is only observed. On 6 to 9
   this needs the boot of step 2, which has happened by then.
4. `ares-install --remove` the launcher: record `home` is the stock id on 5.x to 9.x
   (`com.webos.app.fullhome` if installed, else `com.webos.app.home`; on 6 to 9 exactly the id the
   baseline resolved, never null) and null or unset on 10+, the
   override is gone on 10+ and `getProfiles` `idleApp` is stock again (`com.webos.app.home`, `support`
   true; also after the step 5 uninstall), on 6 to 9 the stock keyFilters list is selected again (no `systemUi-` file in
   configd, no mounts) and the compositor was not restarted, the
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
   confirmed by the fault entry of the same pid; accepted limitation, see
   [Known LG crash on 9.2.4](../docs/home-key-hook.md#known-lg-crash-on-924)). That crash, like every other platform crash, goes to the end-of-run reference note and is no `FAIL` in the
   end-of-step crash check and the popup check; only a crash of a Home Chooser process or of WebAppMgr fails. If its popup holds key focus (before a Home proof, or when the probe key is
   blocked) the runner sends BACK at most once per fault (pid and faultTime; YES and NO do the same there), then
   checks that key focus is back: a probe key moves the list focus when our page is open, else the foreground app
   is unchanged. (The fault status keeps every fault of the boot, so it cannot show the popup is gone.) If focus
   is not back the step FAILs and no more keys are sent. Reinstall: the
   record and the override (10+) return, the id is baked into the selected `systemUi-XXXXXX.js` again (6 to 9), Home opens the
   launcher; on 6 to 9 this return is a live filter switch and must not restart the compositor.
5. `ares-install --remove` Home Chooser: override, choice file, `init.d` link, boot restore copy, flag and the
   watcher unit are gone; the record is stock as in step 4; on 6 to 9 the stock keyFilters list is selected again
   (`assert_stock`), the compositor was not restarted, and
   Home opens stock at once. The toast reads "Home Chooser was removed. The Home button opens LG Home
   again." on every release. The runner polls the log once a second
   for the `notification createToast:` line, takes `5-toast` at once (a toast is short-lived, so the
   reply in the log is the evidence) and checks that exactly one toast was sent and what the reply was.
With `--removal-race` step 3 is followed by 3r and the cleanup instead of steps 4 and 5:

* 3r. `ares-install --remove` the launcher and then Home Chooser at once, as an owner uninstalling both. The
  record's `home` (and on 10+ the override, and `getProfiles` `idleApp`) must be stock within 15 s plus 8 s (the
  8 s times the host slowdown) of the launcher's removal: one confirmation, not one per app. Then the step 5 checks (toast,
  watcher finished, `assert_stock`, preload policy enabled), Homebrew Channel opened and closed as the only app
  with screenshot `3r-last-app-closed` (LG Home or the last input in front, never the removed launcher), and Home
  opens stock.

6. Cleanup: remove the launcher and Home Chooser (not on `w21p-kisscurl`). When the cleanup itself
   removed Home Chooser (a run that stopped early), it waits up to 150 s for the watcher to finish the
   restore (about 15 s per removal it has to confirm) and checks the choice file, the `init.d` entry
   and the unit, and only then stops the VM: stopping it earlier kills the watcher and leaves the choice
   file behind until the next boot's restore copy runs. Stop the VM.

### webOS 6-9: nothing restarts

Every release applies a choice immediately (`expects_reboot_dialog()` returns false), and `homechooser-homekey status`
always reports `reboot=no`. The runner follows that:

* Step 2: no restart alert, no "Restart now" row, no REBOOT_ASK evidence is looked for. Asserted after the choice:
  `homechooser-homekey status` says `active=yes` and `patch=possible`, no `/usr/lib/qml/` mounts exist, the flag file and the
  `init.d` entry are written, the record names the launcher, and the compositor was not restarted.
* A later choice in the same boot is a live switch: `reboot=no`, the launcher id is baked into the selected generated
  `systemUi-XXXXXX.js` (`baked_id`), no compositor restart.
* No compositor restart: `surface-manager-daemon` keeps its `MainPID` and `NRestarts`, and no new
  `restarting surface-manager-daemon` line appears in `/tmp/homechooser-homekey.log`. Checked for
  the choice, the launcher removal and its return (a live switch), the Home Chooser
  uninstall and the API calls. It is skipped when a reboot came in between.
* No crash of ours: after every step `/var/log/messages` since the step began must have no `crashd ... CRASHED`
  line for a Home Chooser process (`homechooser-*`, the service, the app, an `org.webosbrew.hcfixture.*` fixture),
  no WebAppMgr crash line, and within one boot `webapp-mgr` keeps its `MainPID` and `NRestarts`. A crash of any
  other process (audiooutputd, legacy-broadcast-dvb, data-migrator, ...) is a platform crash: it fails nothing and
  is collected, by process and time, into one note at the end of the run, "platform crashes this run (reference,
  not a failure)" (`evidence.split_platform_crashes`, `crash_reference`). A crash that names no process counts as ours.
* VMs run with `debug_mode=release` (`--debug-mode`, default `release`): a retail set, where faultmanager shows no
  "[crash] Fault Occured" popup that would take key focus. The runner sets it while the VM is off, before the boot
  (`preflight.prepare_vm`), logs it, and leaves it set on the VM; `--debug-mode keep` leaves the VM's own setting,
  `debug` and `event` set that one and bring the popups back.
* LG's automatic update check is answered "no new version" (`--update-offer off`, default; `keep` leaves it).
  The update daemon POSTs `CheckSWAutoUpdate.laf` about 20 s after QEMU starts and LG's server offers a newer
  firmware (RESULT_CD 900), which raises "The latest version of the software is available ... update now?" and
  takes key focus. Every test VM is `mode=mitm`, and its mitmweb is up before QEMU, so right after each `beanjar
  start` (`BeanjarHost.start`, so reboots too) `hc_e2e/mitm.py` connects with `beanjar mitm connect HOST/NAME`,
  sets mitmproxy's `modify_body` option over the web API (Bearer password, `_mitmproxy_xsrf` cookie plus
  `X-XSRFToken`) and closes the connection. The rule replaces the response of `CheckSWAutoUpdate.laf` only; the
  offered version equals the installed one (from the firmware name), which the daemon's comparator treats as
  "Not Found Image Update". Nothing is written on the TV. The run log says `update offer: ...`, and `RACE LOST`
  if a check had been answered before the rule was in. Which reply, and why: the daemon disassembly notes in
  `hc_e2e/mitm.py`.
* LG's country answer is the TV's own (`--country-reply off`, default; `keep` leaves it). At boot LG's server
  answers `POST /init/<ver>/initservices` with `"country":{"code":"JP","ricCode":"AIC","threecode":"JPN"}`, the
  country of the public address of the machine the VM runs on, while the VMs are set to USA; sdx then raises
  "Changing the country will cause your TV to restart ... Continue?", which takes key focus. The same
  `modify_body` list gets two more rules that change `code` and `threecode` inside that reply's country object
  to the TV's own (`ricCode`, LG's regional cluster, and every other byte stay as sent). The request carries the
  country only in headers, which `modify_body` cannot read, so the TV's own country is read once per VM from
  its settings (read-only `getSystemSettings`: `smartServiceCountryCode2/3`) after a boot is up and kept in
  `e2e/results/country/HOST-VM.json`; the boots after that get the rule, and a VM never read yet gets none
  (the log says `country reply: ... not known yet`). The log line on the rules is `mitm rules: ... modify_body
  set; ...`. Facts: `scratch/country-popup/facts.md`.
* After a boot (`--reboot-check`, cleanup boot): the watcher is active, status is `active=yes` and `reboot=no`
  again, and the boot apply log lines and the compositor QML/crash lines of the retained messages are recorded
  (`after_boot_checks`).
* `--update-check` from a build older than 144ef21: that build mounted QML live and asked nothing; with no restart prompt
  and mounts present the runner notes it and goes on (`reboot_after_choice`).

The `--reboot-via` code (closing the restart alert with closeAlert, or clicking Reboot on a verified alert) remains in
`reboot_after_choice()` but is unreachable on the current design.

Measured on 2026-10-23: 10.3.1 accepted the alert from a plain root `luna-send` without `-a` (the watcher now sends with `-a com.webos.applicationManager`); 5.6.1
and 9.2.1 answer `Alert is blocked by [ system ]` on every try (why is not established; on the 9.2.1
VM the stock home's first-run guide overlay was showing), so the toast is shown there. Right after a
compositor restart both calls get `UI is not yet ready` and succeed on a retry a few seconds later.

Preload checks (docs/settings-app.md "The stock home is not preloaded"), on 6.x and later; on 5.x
the watcher makes no preload call, so the checks only assert the policy is not disabled:

* after step 2 (the switch): `preloadmanager/getPreloadPolicy` shows `isEnabled:false` for
  `com.webos.app.home`. On 10.x and later LG Home may still be running because the chosen launcher
  has not been brought in front yet. After step 3 opens it from an ordinary app, the runner
  asserts that the watcher logged one deferred close (bounded wait 60 s) and LG Home has no live
  process (30 s more). With `idleApp` naming the launcher, nothing brings it back. On 6.x to 9.x the home gate ([../docs/home-gate.md](../docs/home-gate.md))
  replaces that: the runner asserts the gate is mounted over `/usr/bin/com.webos.app.home` (`/proc/mounts`), stock
  Home is not in `running` (a bounded wait of 150 s, since the watcher retries the close of a boot-started Home every
  10 s) and the watcher log shows it manages the gate (the `home gate mounted` line, or another `home gate:` line when `homekey refresh` mounted first). After the `--reboot-check` boot the runner notes whether it runs and sam's
  launch lines for it (`snapshot-boot-components` may launch it at boot outside the preload manager: an open risk);
* after the launcher removal (step 4) and after the Home Chooser uninstall (step 5): `isEnabled:true`;
  after the launcher is reinstalled, `isEnabled:false` again;
* with `--reboot-check` (after step 3, launcher chosen): the VM is stopped and started with Beanjar;
  after the boot the watcher is active, the record still names the launcher, the watcher's log
  (in `/tmp`, so from this boot) shows `setPreloadPolicy ... isEnabled=false`, the policy reads false, and
  for a normal launcher, after the boot policy settles the stock home has no live process over
  60 s (on 10.x and later after a bounded wait of 150 s for the watcher's close: 10.x starts LG
  Home at boot), and on 10+ `idleApp` still names the launcher. For an overlay launcher the stock
  Home state is recorded: a close waits for a later launch over a nonstock full-screen card. On 10+
  with an overlay launcher the runner also asserts, within 240 s, that the watcher log of this boot has
  exactly one `last input: launched <id>` line (no `last input: launch of <id> failed`) and that `<id>`
  is SAM's foreground, and notes whether the overlay launcher still runs over it. On 6.x to 9.x (home gate) the runner also asserts, for every launcher, that the gate is mounted,
  the real stock Home is not running and a launcher is in front: the chosen launcher for a full-screen one, the last
  input app (anything but stock Home) for an overlay one. On 5.x there is no preload call.

A check that fails where the docs name a limitation is recorded `XFAIL` with the citation
(`Recorder.check(..., expected=...)`), as the qml gate does on releases without Qt (12.x).

The log also has a "home-app residency" section at five points (before selecting, after "Use as
Home", after the Home press, after the launcher uninstall, after the Home Chooser uninstall): is the
stock home and the launcher running (`applicationManager/running`), pids and RSS. Observation only.

## The launcher API check (`--api-check`)

Opt-in steps that test the contract of `docs/launcher-api.md` on the device. Every call is made as the
launcher with root `luna-send -a <launcher id> -n 1 luna://org.webosbrew.homechooser.service/<method>`
through `ares-do exec`, under the guest's `script` pty when that is the form that answers (see "Maintainer VM
scenario"). The hub then takes the caller to
be that installed app, with its own client permissions. The launcher must declare
`requiredPermissions: ["org.webosbrew.homechooser.applications"]` and `appCategory: ["home"]`. The default launcher is the
web fixture built with the permission (`HC_FIXTURE_PERMISSION=org.webosbrew.homechooser.applications sh
e2e/fixtures/web/build.sh build/fixtures-api`, which the runner does itself). The native and qml fixtures cannot
declare it and are refused as the first launcher of a run; later in a `--launchers` list they are the plain builds and
make no API calls. The run also installs a disposable non-home app,
`org.webosbrew.hcfixture.disposable` (`e2e/fixtures/disposable/build.sh`, no category and no permission).
`--launcher fixture:web.demo` (the example launcher, which always declares the permission) is accepted too.

Two modes. The host contract test (`service/test`, `test-launcher-contract`: the real handlers over every case of
`launcher-contract-cases.json`) owns the methods and their error codes, so on a device the API is a smoke check:

* `--api-check` (`smoke`): reachability, as a launcher calls it. 2a: the appinfo opt-in, `getInfo` (`root`,
  `permitted`, `isSelectedHome:false`) and `listApps` (names the launcher), then the `request` dialog. 3a: `getInfo`
  (`isSelectedHome:true`) and one Home-only method, `closeApp` of the running disposable app; with `--uninstall-via
  alert` also the `uninstallApp` confirmation on LG's alert (cancel, then confirm), then the "already your Home" toast.
  After a boot, after the update and after the uninstall as below. Level 3 (`hc_e2e/level3.py`,
  `hc_e2e/steps/level3.py`): in 2a `getAppLifeEvents` without `subscribe` is `badRequest` and the `systemApp`,
  `mediumLargeIcon` and `extraLargeIcon` fields of `listApps` are well formed; in 3a a subscription held as the launcher
  (`luna-send -a <id> -i`) across the launch and the `closeApp` of the disposable app must see the acknowledgement and
  its launch and close or stop events, every reply valid against the spec (never the browser: it raises an LG popup).
* `--api-check=full`: everything below, every method and error case, as the full tier runs it, and for level 3 also
  `getIcons` with `extraLargeIcon` and `mediumLargeIcon` (data for apps whose `listApps` entry names that icon, `noIcon`
  for one that does not, never another size's picture).

`luna-send -a <id>` makes the hub take the call as that app's id, but it does not carry the app's ACG: a web app's
calls go through WAM with the groups of its `requiredPermissions`, a root `luna-send` does not. A pass here therefore
does not prove that a launcher page can make the call. The proof from a real page is step 3d of `fixture:web.demo`:
the demo's grid comes from its own `listApps` call, so OK opening the runner's app, first in that grid, shows the page
reached the API.

Every reply that comes from Home Chooser (one with `apiVersion`) is also validated against its method's result schema
in `api/launcher-v1.openrpc.json`, with the spec lint's own checker (`api/tools/launcher_schema.py`), and recorded as a
check of its own; a reply of the luna bus itself (no `apiVersion`) is noted as outside the schemas.

The full sweep:

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
  With `--uninstall-via alert`, use the same ownership and button proof as Reboot with the exact question
  `Uninstall HC Fixture disposable?`, buttons Uninstall/Cancel (the key fallback additionally proves the safe Cancel focus).
  Require a matching `UNINSTALL_ASK` alert-shown line since this request's log mark, naming
  the disposable target and requesting launcher, successful remove route, and accepted alert id.
  After clicking Uninstall, verify the installed app set lost only the disposable fixture. An alert
  dismissed without removal is noted, requested again and clicked once more; a second failure fails.
  The key fallback retains the two-frame focus proof and MANUAL wait after a declined proof.
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

## Launchers: the fixtures and your own

The default launcher is `fixture:web.normal`. A real launcher is tested as an app id with `--launcher-ipk <path>`
(see "Checking your own launcher"); there is no built-in special case for any one project. The six fixtures
(`e2e/fixtures/README.md`; built into `build/fixtures/` by `sh e2e/fixtures/build.sh`, which the runner calls itself if
an ipk is missing) are `fixture:{native,qml,web}.{normal,overlay}`. Home Chooser supports a launcher only through
`appCategory: ["home"]` in its appinfo, so the fixtures declare nothing else and draw nothing but a solid colour.

* **Overlay or not no longer changes the preload expectations**: on 6.x to 9.x the home gate keeps the stock
  home from running, on 10.x and later it is closed for every launcher. Overlay decides the expected `idleApp`
  (`support` false instead of the launcher). `Launcher.overlay`
  (`defaultWindowType == "overlay"` in the ipk's `appinfo.json`) is still logged and still decides how "Home opens
  the launcher" is observed (an overlay leaves the app underneath in the foreground).
* **Launch proof** (every Home press that should open the launcher, for any `--launcher-ipk` app and the
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
* With `--launchers all-fixtures` a failure in a later launcher is recorded and the next one runs on the same boot; a
  failure of the first launcher ends the run (the later ones need its setup), and the cleanup still runs.

## The update step (`--update-check`)

The old build comes from `--update-from <git ref>` (default: the last published build, `origin/main`, when HEAD's
`app/` or `service/` differ from it; otherwise the last commit before HEAD that changed `app/`, `service/`, or HEAD
itself if those have uncommitted changes), built by `git archive` into a `/tmp`
directory (the working tree and `.git` are not touched; `node_modules` is linked from the main tree) and removed at the
end. Both builds are `0.1.0` today, so the version does not tell them apart: the runner compares the md5 of the native
`homechooser-daemon` executable (`usr/palm/services/<service id>/homechooser-daemon`) in the two ipks, in the service directory
on the device, and in the running process's mapped image (`/proc/<MainPID>/exe`). The old build is installed in step 1 instead of the current one
and used for step 2 (choose the launcher) and step 3 (Home opens it). Then step 3u installs the current build with
`ares-install` and no uninstall.

What the design says (the native watcher in `service/src/watcher/`, run by `homechooser-daemon`; `docs/home-key-hook.md`
"Boot and watcher lifetime"; `docs/settings-app.md` "When apps are removed" and "What is left when the watcher is not
running"): an update installs by removing first, so the watcher takes Home Chooser as gone only if it is still gone after its
confirmation delay (15 s, checked twice), else it logs `Home Chooser is back, not removing anything`; no notification is sent
for the temporary absence of an update; the daemon's running image stays mapped after the package directory is replaced and
a restart copy is kept in `/tmp/homechooser-runtime`, and the unit is not restarted. So no restart is expected, and the old
build's watcher code keeps running until its next start.
Asserted after a 45 s settle:

* the service directory holds the new `homechooser-daemon` (md5 = new build's), the running watcher's mapped image is still the old build's, and the app has the new version;
* the choice file and the record `home` still name the launcher;
* exactly one watcher unit and one top-level watcher process, with the same `MainPID` and `NRestarts` as before (no duplicate, no restart);
* the watcher log lines written during the update contain no `watching, home=` (no restart), no restore, revert, `isEnabled=true`, alert or toast; the optional `Home Chooser is back` line is noted either way, as is which build the running watcher is;
* the `init.d` entry links to the persistent boot restore copy and the target exists;
* on 6 to 9 the live key filter is still selected with no QML mounts, the flag file exists and the launcher id is still baked into the selected `systemUi-XXXXXX.js`; elsewhere no mounts or flag; on 10 and later the override exists;
* the stock home preload policy is still disabled (6 and later; 5.x: not disabled);
* Home opens the launcher (with the fixture proof above);
* the app opens, lists the launcher, shows no `Couldn't` toast and has the launcher's row ticked.

Then steps 4 and 5 run on the updated install.

## Code layout

`homechooser_e2e.py` is the command line only (`hc_e2e.cli.main`); `run_matrix.py` the scheduler. The `hc_e2e`
package is standard library only:

| Module | What it holds |
| --- | --- |
| `cli.py` | the options (`build_parser`), `VmSession` (the VM bring-up and teardown around a run), `main()` |
| `scenario.py` | `Scenario`: one VM run, its launchers on one boot, composed of the step mixins below; `begin_run`, `end_run`, `run` |
| `plan.py` | `PHASES` (the scope table), `run_plan()`, `plan_groups()`, `PlanRun` (the plan phase by phase) |
| `tiers.py` | `TIERS` and what a tier sets, for `run_matrix.py` and pytest |
| `phase_tests.py`, `dry.py` | the pytest device tests without pytest: options, test ids, `PhaseTests`; the fake device |
| `recorder.py` | `Recorder` (checks, `run.log`, `results.json`), `SCREENSHOTS` |
| `timing.py` | `SLOWDOWN`, `seconds()`, `sleep()`, `run()`: always used as `timing.X`, so one assignment or patch reaches every wait |
| `beanjar.py` | `BeanjarHost`: the VM's machine, locally or over ssh; keys, clicks, screenshots |
| `device.py` | `Device` (every `ares-*` command line), `RootSession`, the luna-send pty rule, the elevation strategies |
| `inspector.py` | the web inspector and the page probes |
| `preflight.py` | the VM settings, waiting for root SSH, removing foreign launchers |
| `matrix.py` | `MATRIX`, `entry()`, the hosts file |
| `launchers.py` | `Launcher`, the fixtures and their builds, the demo, the Home Chooser builds for the update |
| `constants.py`, `releases.py` | ids, paths and product texts; what each release is expected to do |
| `evidence.py`, `alerts.py`, `checks.py`, `api_spec.py` | log reading and crash classification; our alerts on screen; shared verdicts; the API replies and their schema |
| `device_api.py` | the author-TV API contract (`--device`) |
| `steps/` | the Scenario's steps, one mixin per family: `screen`, `state`, `popups`, `preload`, `home_press` (shared by the phases); `baseline`, `choose`, `panel`, `home_proof`, `demo`, `reboot`, `api`, `update`, `removal`, `screensaver` |

`e2e/device/` is the pytest plugin and test module; `e2e/test_*.py` are the offline unit tests (unittest, also
collected by pytest). The tests patch a name in the module that looks it up (`hc_e2e.steps.api.Inspector`,
`hc_e2e.cli.Scenario`), and the timing names in `hc_e2e.timing`.

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
* `audiooutputd` crash dialog (beanjar#183, closed as stock LG behaviour; Beanjar `docs/research/w22o-audiooutputd-gain-race.md`): on W22O firmware 33.31.61 and 33.31.68 (webOS 10.x) two audiooutputd gain-parse threads race, and on about 7-9% of boots audiooutputd crashes about 50-56 s after boot; LG shows "[crash] Fault Occured. Reason: RDXDErr3_audiooutputd__Linux Crash: ..." with YES and NO, and it takes key focus. When the probe key does not move the list, the runner reads the screen (`beanjar elements`); only if the dialog text contains both "[crash] Fault Occured" and "audiooutputd" (the text decides, not the VM name or release) does it dismiss it with NO (a click on the "NO" button, or in keys mode a focus move to NO verified on screen before OK; YES is never pressed), screenshots before and after (`audiooutputd-crash-dialog-before/after`), re-reads the screen to confirm the dialog is gone, records `RECOVERED` with the OCR text and the reference, and retries the key probe once. Any other dialog (`[general]` included) or a crash fault naming another process still stops the run as before. With `debug_mode=release` (the default) this popup is not shown at all, so the dismissal is needed only with `--debug-mode keep|debug|event`; it stays for that. The "no Home Chooser or WebAppMgr crash fault this boot" check does not fail on an audiooutputd crash (or any platform crash): it goes to the end-of-run reference note; a crashing Home Chooser process or WebAppMgr still fails.
* Screenshots: `beanjar screenshot <vm> <file> --json` is the primary capture (about 2 s, headless, whatever the window position; `taken_by` is logged). No viewer is opened for it, so the runner no longer opens one. Only if it fails: the guest capture (`ares-do screenshot`; `com.webos.service.capture` does not work on 5.x (timeout) or 10.x/11.x/12.x (`ERROR_INTERNAL`), and after the first failure it is not tried again), then a logged capture-unavailable result if both fail. Each fallback is logged with the word FALLBACK.
  Pictures are taken only at key steps, the `SCREENSHOTS` list in `hc_e2e/recorder.py`: after each Home press verdict
  (`<label>-verdict`, and `-first-press` when a press did nothing), once per step that has a `FAIL` (`fail-<step>`), after
  a reboot's checks (`3r-after-reboot`), the final screen (`final`), and every popup or fault as before (foreign popups,
  `keys-blocked`, the audiooutputd dialog, our alerts and toast, the keys-mode alert proof). Any other label is logged as
  skipped.
* On 5.x Home starts `com.webos.app.fullhome` as an overlay over the app in front (foreground stays the app), so step 3/4/5's "Home opens stock" looks for it running.
* Readiness: before step 1 (and after every reboot) the runner waits up to 5 minutes until root SSH (`beanjar ares-shell --root`, Homebrew Channel's Dropbear on guest port 22) answers and `ares-install -l` lists `org.webosbrew.hbchannel`; each change of state is logged (`ready`, `no-root-ssh`, `no-hbchannel`, `down`). `no-root-ssh` (developer mode answers, root SSH never does) is Homebrew Channel's failsafe mode: its `startup.sh` sets `/var/luna/preferences/webosbrew_failsafe` at the start of a boot and clears it about 12 s after its boot work, so a boot stopped within seconds of starting leaves the flag, and the next boot runs only telnetd (no Dropbear) and clears the flag after 15 s. Seen 2026-10-24 on `w20l-jhericurl`, `w22p-mullet` and `w22o-number1` after a 9 s boot each (saved logs `20261024T0031..0038Z-stop`: `boot-done` false, dmesg ends at 8.7-9.7 s; the next boot's `ps.txt` has telnetd and no `bin/dropbear -R`); `w24g-ombre`'s short boot ran 22 s and it was unaffected. On `no-root-ssh` at setup the runner restarts the VM once and waits again.
* Home Chooser is an overlay window (appinfo `defaultWindowType`), and surface-manager closes an overlay after a stretch without key input: measured on 6.5.2 (2026-10-24), about 90 s after the launch with no key and about 120 s after the last key; a close and relaunch alone does not reset it; sam logs `getAppLifeStatus ... "reason":"userTimeout"` and WAM `killApp reason : userTimeout`. On 9.2.1 the same close was seen about 90 s after the last key. The runner presses a key only while it can read our page; before the list keys of step 2 it opens Home Chooser again (and notes the close and its reason) if the page is gone, and anywhere else it stops rather than press a key at whatever is beneath.
