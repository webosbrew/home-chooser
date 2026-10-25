# Minimal Home key hook, webOS 6–9

Home Chooser runs a structural rewriter against the stock compositor when preparing the persistent hook cache. The same guarded
rules apply to webOS 6 through 9. A core mismatch leaves the device stock and logs the failed anchor. webOS 5 and 10 or later are refused.

## The hook

| Group | File | Change |
| --- | --- | --- |
| Core | `WebOSCompositorBase/global/Settings.qml` | Import WebOSServices; add the shared home-id, redirect and launch-fallback helpers. |
| Core | `WebOSCompositor/controllers/StarfishKeyFilter.qml` | Expose `applicationManager.getHomeAppId`; redirect stock-home `launch` and `checkLaunch` calls. |
| Best effort | `KeyFilters/systemUi.js`, when it defines `HOME_APP_ID` | Refresh that variable at the start of `handleHomeKeys`. Existing comparisons stay intact. |
| Best effort | `StarfishNudgeView.qml` | Suppress nudges over the chosen home. |
| Best effort | `StarfishBaseLunaServiceAPI.qml` | Report the chosen home as the launcher in `getCurrentState`. |
| Best effort | `StarfishRecents.qml` | Spare both the chosen launcher and LG Home during cleanup, retaining the firmware's last-input exclusion and default. |
| Best effort | `StarfishFullscreenController.qml` | Use the chosen home for NMRM last-input. |

The two core files are all or nothing. The other five files are independent: a missing file or
unexpected anchor skips that complete file and reports the reason in `unpatched-sites` and `skipped`. Everything outside this table stays stock, including the fallback
Home view, splash/spinner logic, gestures, home-type window metadata and the multiview toolbar's
literal case comparison. Launches through the Home key hook's applicationManager still use the redirect.

The helper reads `general/defaultApps.home` live, then the stored id baked into Settings.qml, then
`com.webos.app.home`. A property binding starts the settings subscription with the compositor.
Changing the record updates Home behavior without restarting anything. A failed custom-home launch
falls back to the stock home with the original request fields. An invalid JSON launch request stays
on the stock path. With the stock id selected, the stock launch implementation runs.

Older builds with no `HOME_APP_ID` definition need only the two core QML files. Builds that
compare the foreground app to that variable also mount systemUi.js. This preserves the firmware's
own behavior when Home is already in front, when voice UI is running, and when overlays have focus.

## Guards and runtime

`service/scripts/homechooser-homekey-hook.js` is compatible with Node 8.12, the webOS 6 runtime: no
lookbehind, named capture groups, dotAll, replaceAll or recursive mkdir. Preparation uses `node`
from PATH, falling back to `/usr/bin/node`. Boot and status never start Node.

Every core anchor must occur exactly once: the singleton pragma, Settings root id, subscribe
signature and import, and the applicationManager group, launch and checkLaunch headers. Both
launch members must lie inside their group. A missing core file or existing core home helper
refuses the complete plan before any output is written.

The optional systemUi rule checks its definition, handler header and known stock-id uses. A
mismatch leaves the original script in place while the core still redirects stock-home launches.
On firmware with foreground-home comparisons, skipping this file has a measured degradation:
with the chosen launcher already in front, Home relaunches it instead of handing it the key;
with LG Home in front, the stock comparison can hand Home to LG Home rather than launching the
chosen app. Voice and overlay decisions also retain their stock-home comparisons. Firmware with
no such comparisons needs no script edit. The harness verifies this core-only behavior separately
from the complete hook.

Each output must change, retain the original bracket balance and have its Settings import.
Settings.qml must contain exactly one stored-id placeholder. The entire plan is checked before
writing output; the worker stages everything before mounting anything.

```
node service/scripts/homechooser-homekey-hook.js check --root /path/to/rootfs
node service/scripts/homechooser-homekey-hook.js generate --root /path/to/rootfs --out /tmp/hook --manifest /tmp/manifest.json --version 0.1.0
```

Exit status is 0 for success, 2 for a guarded refusal (`ABORT: <specific reason>`), and 1 for usage
or I/O failure. `--skip-version-gate` is for offline negative controls only; the worker never uses it.
The manifest records each target path, stock and patched MD5, tier, skipped sites, and the
Home Chooser version. The companion tab-separated index lets BusyBox shell validate it without
starting Node. These hashes are per-TV cache keys, not a list of approved firmware hashes.

## Deployment

The installed `service/scripts/homechooser` entry is linked from Homebrew Channel's
`/var/lib/webosbrew/init.d/homechooser`. The `homekey` enable flag controls boot application.
Packaging places the five runtime scripts beside the service binary; the app bundle carries none. Offline tests are kept in the source tree.
The entry scripts source shared helpers from `scripts/lib/`: common paths, logging, Luna calls,
timeouts and locking; watcher record, preload, notifications, restore and events; and hook cache,
restart and mount logic. The watcher copies `lib/` with itself into `/tmp/homechooser-runtime`,
so it can restore stock after the installed service directory disappears.

On first installation or after an update, `homekeyPrepare` generates copies under
`/var/lib/webosbrew/homechooser/hook/`. Preparation validates every generated copy, builds in a
temporary directory and renames it into place. At boot the worker checks the installed version,
the cached copies and the MD5 of each stock file. It copies a valid cache to
`/tmp/homechooser-homekey/stage`, writes the configured
id into the staged Settings.qml, bind-mounts the files, then restarts the compositor once. An id is
baked only if applicationManager confirms that app is installed; an absent or unanswered app uses
an empty fallback until the watcher refreshes it. The underlying rootfs is never written.

When the cache is absent, corrupt, or based on changed stock files, boot leaves Home stock and
logs the reason. It removes the stale cache. The watcher regenerates it after boot, then status
reports `hook=stale`, the reason and `reboot=needed`; the hook becomes active on the next restart.
The app opens the existing restart alert once per cache generation when the user opens Home Chooser,
and keeps the “Restart now” action visible. A version change also invalidates the cache. `status`
reads the manifest and mounts without invoking Node. Removing the hook or uninstalling Home
Chooser deletes the persistent cache.

The boot hook runs after the compositor has already loaded stock QML. The restart follows the
firmware's `lsm-ready.service` order: read its systemctl start lines (`/etc/systemd/system` before
`/lib/systemd/system`), stop the listed clients that are running, restart
`surface-manager-daemon.service`, then start those clients after the compositor is ready. The
fallback list is webapp-mgr, booster, maliit-server. The compositor's notify-ready event is the
lsm-ready emit, rather than the existence of the persistent `/tmp/lsm-ready` file.

A bare compositor restart can crash WebAppMgr when it reconnects before outputs are ready. The
worker keeps clients stopped until both the systemd restart and a functional QML check succeed.
It calls the stock `com.webos.surfacemanager/getCurrentState` method as root with
`-a com.webos.applicationManager`, polling against monotonic uptime for up to 24 seconds with each call bounded to two
seconds. A successful reply must contain both `returnValue:true` and a string `currentState`.
The method is implemented and registered by the stock StarfishBaseLunaServiceAPI QML object on
all 19 corpus builds; it does not depend on the optional rewrite being applied. On 9.2.4 it stops
answering when broken Settings.qml prevents main.qml from loading, even though systemd reports
an active compositor, and answers again after stock rollback.

At boot only, either restart or QML-readiness failure unmounts the hook and attempts one stock
restart, including the same readiness check. Remembered clients start only after QML responds.
There are no further retries. Status preserves `recovered=stock` with the original failure
`reason=` (or `recovered=failed` if stock recovery also fails); the enable flag remains. Both
results are logged. Failed unmounts retain staged sources and the manifest for a later stop.
The restart sequence runs in a waited child with detached output so an app closing cannot cut
its stop/start sequence in half. See [the dated verification record](research/minimal-hook-verification.md).

For the device recovery test only, `HOMECHOOSER_DEBUG_BREAK_SETTINGS=1` appends invalid QML to the
staged Settings.qml after the real rewrite and before mounting. The app never sets this environment
variable. Run it only as an explicit root diagnostic, then remove test apps, flags and temporary
files. `HOMECHOOSER_READY_WAIT` overrides the polling window for tests. Offline readiness cases
cover an immediate response, delayed registration, no response, a hung call, malformed success
and failed replies, successful rollback, and failed stock recovery without a restart loop.

There are no runtime restarts. Choosing another launcher updates the record and staged fallback;
restoring LG Home removes the enable flag and writes the stock id into the live record. Mounted
files remain until reboot. The app asks the owner to restart when enabling or removing the hook.

For explicit manual/debug use:

```
homechooser-homekey status
homechooser-homekey refresh
homechooser-homekey refresh stock
homechooser-homekey stop
```

`refresh` changes the staged Settings.qml in place, retaining its inode and bind mount. It never
restarts the compositor. `refresh stock` clears the baked id regardless of the stored choice.
The live record still takes priority. `stop` unmounts and restarts; `refresh restart` and
`apply samfirst` remain manual debugging commands. The app and watcher never call these restart
commands. Logs are in `/tmp/homechooser-homekey.log`.

Status reports `mounted=yes|no`, `patch=possible|refused`, `reason=` on refusal,
`unpatched-sites=N` for skipped best-effort files, repeated `skipped=<file>: <reason>` and
`file=<patched path>` lines, `hook=stale` when applicable, and `reboot=needed|no`.

## Offline verification

Run every `service/scripts/tests/test-*.sh` under both sh and busybox sh; pass a one-line executable wrapper
(`exec busybox sh "$@"`) as the shell argument to worker suites. Also run `make -C service test`,
`cd app && npm ci && npm run lint && CI=true npm test`, and
`python3 -I -m unittest discover -s e2e -p 'test_*.py'`.

`test-minimal-hook.sh` runs the study's pattern
matrix, syntax checks, deterministic output, restoration of the original bytes after removing our
edits, refusal/double-patch mutations, per-file best-effort drift, and the Home-key behavioral harness.
For each supported firmware it compares 144 scenarios: no choice versus stock, chosen launcher
versus stock with home roles exchanged, and the actual two-file core plan versus stock foreground
comparisons with redirected launches. The latter explicitly checks the launcher-front relaunch
degradation when systemUi is skipped.
The harness emulates QML service boundaries; VM checks remain necessary for QML/script visibility
and settings subscription timing. Synthetic anchor skeletons test failures even without a corpus.

`test-real-roundtrip.sh` exercises the real rewriter and worker with mount, systemctl and Luna
stubs. `test-homechooser-homekey.sh` tests refusal and I/O failure, partial mounts, boot recovery,
refresh inode preservation, install checks, restart order, locking and concurrent status calls.

Corpus locations are overridable:

| Variable | Default / meaning |
| --- | --- |
| `OPENHOUSE_FIRMWARES` | `~/Projects/webos-firmwares` |
| `OPENHOUSE_HYPERVISOR` | `~/Projects/webos-hypervisor` |
| `OPENHOUSE_STOCK_COPIES` | `scratch/hk-universal/rw/stock`, saved target files from deleted roots |
| `OPENHOUSE_WEBOS71` | `~/Temp/webos-7.1.0`, partial QML tree |
| `OPENHOUSE_CORPUS` | `service/scripts/tests/corpus.tsv`; id, version, kind, root in tab-separated columns |
| `NODE` | Node executable for offline checks (use a Node 8.12 executable for compatibility) |

Missing roots are skipped with a notice. No LG files are copied into the repository. Saved per-file
copies contain no os-release; tests copy them into a temporary directory and add version metadata
from the study's corpus. The original scratch study and its corpus are in `scratch/minimal-hook/`.

## Known limitations

* **Intermittent LG crash after the boot compositor restart (webOS 9.2.4).** On the W24G VM about 1 boot in 10 shows an LG `legacy-broadcast-dvb` crash at `API_SI_GetLinkageUriInfo+0x197` (`crashd CRASHED {"process":"legacy-broadcas.<pid>"}`, faultmanager entry `RDXDErr3_legacy-broadcas…`), with LG's "[crash] Fault Occured" popup. It shows only with the single boot-time compositor restart (stock boots: 0 of 10), and there is no restart-free option: the hook cannot load before the compositor's first QML load. See the [early-mount study](research/home-key-hook-early-mount-study.md). It is accepted as a known limitation pending a user acceptance test on a real TV. The E2E runner records it as a note, not a failure.

## Verified on a device

The minimal hook's device results are recorded here after VM verification. Offline coverage alone
does not mark a build as device verified.

These are the latest timestamped result directories for each VM as of 2026-10-25.
Commits are inferred from each run's local start time against `git log`, not recorded by
the runner. The 5–8, 9.2.4 and 11.x runs predate the starting tip `f392cf4`; the newer
9.2.1, 10.3.1 and 12.0.0 runs are targeted checks on `prerelease-tasks`. The latest 9.2.1
and 10.3.1 runs started at `69795e6` with the boot restore fix later committed as `a3a2d2b`.
The 12.0.0 run started at `8eadf3c` with an uncommitted assertion later committed as `9d459fd`.
Incomplete and failed runs are identified explicitly; the full matrix has not been rerun.

| webOS | VM / build | Latest run (local date; inferred commit) | Result | Hook |
| --- | --- | --- | --- | --- |
| 5.6.1 | w20l-jhericurl / W20L | `e2e/results/w20l-jhericurl-20261024-151120` (2026-10-24; `88bde96`) | 1 FAIL, setup stopped; earlier `…015912` had 54 PASS | no hook needed |
| 6.5.2 | w21a-kisscurl / W21A | `e2e/results/w21a-kisscurl-20261024-214245` (2026-10-24; `4818acf`) | 106 PASS, reboot checked | verified |
| 7.6.0 | w22p-mullet / W22P | `e2e/results/w22p-mullet-20261024-220405` (2026-10-24; `5d89b26`) | 106 PASS, reboot checked | verified |
| 8.3.0 | w22o-number1 / W22O | `e2e/results/w22o-number1-20261025-000109` (2026-10-25; `4e0bad5`) | 106 PASS, reboot checked | verified |
| 9.2.1 | w22h-ombre / W22H | `e2e/results/w22h-ombre-20261025-231546` (2026-10-25; `69795e6`–`a3a2d2b`) | 76 PASS; stopped-watcher uninstall and next-boot cleanup | verified in earlier `…224555` run (214 PASS, 1 MANUAL) |
| 9.2.4 | w24g-ombre / W24G | `e2e/results/w24g-ombre-20261025-201151` (2026-10-25; `7bf58f6`) | 186 PASS, 1 MANUAL; API checked | verified in earlier [runtime study](research/minimal-hook-verification.md) |
| 10.3.1 | w22o-ponytail-us / W22O | `e2e/results/w22o-ponytail-us-20261025-232305` (2026-10-25; `69795e6`–`a3a2d2b`) | 55 PASS; stopped-watcher uninstall and next-boot cleanup | no hook needed |
| 11.2.0 | w22o-queue-us / W22O | `e2e/results/w22o-queue-us-20261025-200110` (2026-10-25; `304d9c4`) | 139 PASS, 1 MANUAL; API checked | no hook needed |
| 12.0.0 | w24g-spacebuns / W24G | `e2e/results/w24g-spacebuns-20261025-223559` (2026-10-25; `8eadf3c`–`9d459fd`) | 166 PASS, 1 MANUAL; update and API checked | no hook needed |
