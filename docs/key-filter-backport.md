# Minimal Home-key hook, webOS 6–9

Home Chooser runs one structural rewriter against the stock compositor at boot. The same guarded
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
literal case comparison. Launches through the key filter's applicationManager still use the redirect.

The helper reads `general/defaultApps.home` live, then the stored id baked into Settings.qml, then
`com.webos.app.home`. A property binding starts the settings subscription with the compositor.
Changing the record updates Home behavior without restarting anything. A failed custom-home launch
falls back to the stock home with the original request fields. An invalid JSON launch request stays
on the stock path. With the stock id selected, the stock launch implementation runs.

Older builds with no `HOME_APP_ID` definition need only the two core QML files. Builds that
compare the foreground app to that variable also mount systemUi.js. This preserves the firmware's
own behavior when Home is already in front, when voice UI is running, and when overlays have focus.

## Guards and runtime

`backport/homechooser-keyfilter-rewrite.js` is compatible with Node 8.12, the webOS 6 runtime: no
lookbehind, named capture groups, dotAll, replaceAll or recursive mkdir. The worker uses `node`
from PATH, falling back to `/usr/bin/node`, as before.

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
node backport/homechooser-keyfilter-rewrite.js check --root /path/to/rootfs
node backport/homechooser-keyfilter-rewrite.js generate --root /path/to/rootfs --out /tmp/hook --manifest /tmp/hook.json
```

Exit status is 0 for success, 2 for a guarded refusal (`ABORT: <specific reason>`), and 1 for usage
or I/O failure. `--skip-version-gate` is for offline negative controls only; the worker never uses it.
The manifest records the staged target paths. Status checks the current stock files directly.

## Deployment

The installed `backport/homechooser` entry is linked from Homebrew Channel's
`/var/lib/webosbrew/init.d/homechooser`. The `keyfilter` enable flag controls boot application.
Packaging copies only the five runtime scripts. Offline tests are kept in the source tree.

At boot the worker generates files under `/tmp/homechooser-backport/stage`, writes the configured
id into the staged Settings.qml, bind-mounts the files, then restarts the compositor once. An id is
baked only if applicationManager confirms that app is installed; an absent or unanswered app uses
an empty fallback until the watcher refreshes it. The underlying rootfs is never written.

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
homechooser-keyfilter status
homechooser-keyfilter refresh
homechooser-keyfilter refresh stock
homechooser-keyfilter stop
```

`refresh` changes the staged Settings.qml in place, retaining its inode and bind mount. It never
restarts the compositor. `refresh stock` clears the baked id regardless of the stored choice.
The live record still takes priority. `stop` unmounts and restarts; `refresh restart` and
`apply samfirst` remain manual debugging commands. The app and watcher never call these restart
commands. Logs are in `/tmp/homechooser-backport.log`.

Status reports `mounted=yes|no`, `patch=possible|refused`, `reason=` on refusal,
`unpatched-sites=N` for skipped best-effort files, repeated `skipped=<file>: <reason>` and
`file=<patched path>` lines, and `reboot=needed|no`. The structural check is cached by the rules,
version metadata and QML/JS content; it is recomputed after input changes.

## Offline verification

Run every `backport/test-*.sh` under both sh and busybox sh; pass a one-line executable wrapper
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
stubs. `test-homechooser-keyfilter.sh` tests refusal and I/O failure, partial mounts, boot recovery,
refresh inode preservation, install checks, restart order, locking and concurrent status calls.

Corpus locations are overridable:

| Variable | Default / meaning |
| --- | --- |
| `OPENHOUSE_FIRMWARES` | `~/Projects/webos-firmwares` |
| `OPENHOUSE_HYPERVISOR` | `~/Projects/webos-hypervisor` |
| `OPENHOUSE_STOCK_COPIES` | `scratch/hk-universal/rw/stock`, saved target files from deleted roots |
| `OPENHOUSE_WEBOS71` | `~/Temp/webos-7.1.0`, partial QML tree |
| `OPENHOUSE_CORPUS` | `backport/tests/corpus.tsv`; id, version, kind, root in tab-separated columns |
| `NODE` | Node executable for offline checks (use a Node 8.12 executable for compatibility) |

Missing roots are skipped with a notice. No LG files are copied into the repository. Saved per-file
copies contain no os-release; tests copy them into a temporary directory and add version metadata
from the study's corpus. The original scratch study and its corpus are in `scratch/minimal-hook/`.

## Verified on a device

The minimal hook's device results are recorded here after VM verification. Offline coverage alone
does not mark a build as device verified.

| webOS | VM / build | Minimal hook verified | Evidence |
| --- | --- | --- | --- |
| 9.2.4 | w24g-ombre / 33.20.23.01 W24G | 154 full E2E checks; API, reboot and automatic rollback verified | [Runtime evidence](research/minimal-hook-verification.md) |
| 9.2.1 | w22h-ombre / 23.21.20.01 W22H | 106 full E2E checks including reboot | [Runtime evidence](research/minimal-hook-verification.md) |
| 6.5.2 | w21a-kisscurl / 03.53.25.01 W21A | 106 full E2E checks including reboot | [Runtime evidence](research/minimal-hook-verification.md) |
| 7.6.0 | w22p-mullet / 04.60.90.01 W22P | 106 full E2E checks including reboot | [Runtime evidence](research/minimal-hook-verification.md) |

The 8.3.0 VM is not yet device verified. An LG country-change confirmation held key focus
before Home Chooser could select a launcher; the hook was never enabled. See the dated
[verification record](research/minimal-hook-verification.md).
