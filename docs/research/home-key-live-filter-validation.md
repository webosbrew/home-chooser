# Native live-filter validation, 2026-10-30

Implementation and device checks were performed in the `live-keyfilter` worktree. No commit was
made. Only the assigned webOS 7.6.0 RK VM was operated. Source portability checks used read-only
firmware corpus files. The main checkout was not modified.

The mechanism and its earlier experimental evidence are described in
[the live-filter design](../home-key-hook.md). Original feasibility evidence is
`/home/mariotaku/scratch/hc-live-reload/report.md` and `source-matrix.json`; restart-crash analysis
is `/tmp/maliit-verify/sol-conclusion.md`. This run adds production-native implementation evidence
and the previously missing volatile-reboot check.

## Implementation

- `service/src/homekey.c`, `homekey-generator.c`, `homekey-shapes.inc`, `homekey.h`: native readiness,
  guarded JS generation, versioned files and checksum checks, exact list editing/readback, volatile
  activation and stock recovery. Internal configd calls are allowlisted without exposing generic
  config writes through the app's forwarding method.
- `service/src/main.c`, `service/scripts/homechooser`, `homechooser-homekey`: dispatch `--boot` and
  `--homekey` before RPC registration. HBC queues one nonblocking transient unit. Native boot apply
  becomes the watcher through `exec`; the unit can use the same binary's runtime copy after removal.
- `service/scripts/lib/watch-base.sh`, `watch-restore.sh`, `homechooser-home-watch`: retain uninstall
  recovery code in `/tmp`, restore stock before deleting selected sources, and retry failed live apply.
  Legacy watcher boot recovery on 5/10–12 remains. The unused SAM restart command was also removed.
- `app/src/homechooser/`, `app/src/homekey/`, `app/src/views/`: first apply and later choices are live;
  no SAM restart, reboot prompt or restart row. Stock restore is immediate.
- The former JS/QML rewriter and homekey mount/cache/restart/status/settings shell helpers are
  removed. `app/scripts/stage-service.js` no longer packages that generator.
- Native, corpus, watcher, boot, app and E2E logic tests were updated. The E2E runner no longer
  exempts the previously known legacy-broadcast crash.

## Device results

Raw evidence from this worktree is under `build/device-evidence/`; host probe logs are
`build/device-*.log`. These generated artifacts are not committed. Process pairs below are
`PID / /proc/PID/stat field 22`, not inferred identity matches from process names alone.

| Check | Result and evidence |
| --- | --- |
| Initial apply and HOME | **Verified:** exact list edits selected a generated file; HOME opened the normal web fixture. `first-apply-config.txt`, `first-home-foreground.txt`, `first-home.png`. |
| Live launcher change | **Verified:** a new version selected the overlay fixture; `getForegroundApps` showed its overlay above the normal card. `live-switch-config.txt`, `second-home-foreground.txt`, `second-home.png`. SAM's singular foreground method reports the underlying card and is insufficient for this check. |
| Core continuity, apply/switch/disable | **Verified:** compositor `254/4867`, SAM `1718/5768`, WAM `1917/5949`, actual MaliitServer `2671/6749` retained identities. Unit MainPID data distinguishes WAM from its renderer processes. `baseline`, `first-apply`, `live-switch`, `disabled` process snapshots. |
| Disable | **Verified:** exact saved stock list restored live; HOME opened LG Home; factory database absent. `disable-config.txt`, `disabled-home.png`, `disabled-storage.txt`. |
| Volatile override across reboot | **Verified:** an active custom filter was left selected, with no boot entry to reapply it. After reboot, getConfigs exactly equaled the saved original stock list; HOME opened LG Home; factory database absent. `volatile-before-reboot.txt`, `volatile-after-reboot.txt`, `volatile-cleared-storage.txt`, `volatile-stock-home.png`. |
| Production boot entry | **Verified:** HBC elevation and the actual `link` RPC succeeded; the init.d target was the installed entry. `elevate.txt`, `link.txt`, `boot-link.txt`. |
| Boot apply | **Verified:** the temporary read-only audit first observed stock at uptime 42.86 s, and the generated filter by 52.36 s. Compositor `273/3604`, SAM `1923/4588`, WAM `2210/4779` were each observed with a single identity. MaliitServer started later (`3616/5624`, first observed at 58.21 s) and retained that identity. All corresponding unit restart counts were zero. `boot-audit.txt`, `boot-applied-processes.txt`. This does not claim Maliit existed before filter activation. |
| HOME after boot | **Verified:** the normal fixture became foreground and its green surface was visible. `boot-home-foreground.txt`, `boot-home.png`. |
| Uninstall | **Verified:** the running watcher restored the exact stock list before removing generated files; HOME opened LG Home. All four core identities above remained unchanged. `uninstall-config.txt`, `before-uninstall-processes.txt`, `uninstalled-processes.txt`, `uninstalled-home.png`. |
| Recovery after package removal | **Verified:** the production unit wrapper selected the retained native executable and runtime watcher after the installed package was absent; restoration completed and the core identities stayed unchanged. `final-unit.txt`, `fallback-completed.txt`, `final-recovery-after-processes.txt`, `fallback-stock-home.png`. The successful original unit had already been garbage-collected, so this check recreated a transient unit with the production command rather than restarting a nonexistent unit. |
| Crash checks and visuals | **Verified at the acceptance snapshots:** no crash fault in faultmanager and no visible crash dialog. Every generated screenshot was inspected. Normal fixture images show the green surface; overlay images show the blue overlay above it; stock images show LG Home and its unaccepted user-agreement invitation. No LG popup was confirmed. |
| Final cleanup | **Verified:** Home Chooser and both fixtures were removed; a final reboot returned exact stock keyFilters and stock HOME. Factory database and init.d entry absent; watcher MainPID=0, LoadState=not-found. The audit hook/unit had already been removed/stopped. VNC stopped and Beanjar confirmed the assigned VM Off. `final-stock-config.txt`, `final-stock-home.png`, `final-cleanliness.txt`, `final-vnc-stop.txt`, `final-stop.txt`, `final-power.txt`. |

Restoration deliberately uses the exact saved stock array with `volatile:true`. That path left no
factory database and reboot returned firmware defaults. `null` with volatile was not needed or
certified. No non-volatile setConfigs call or database-file edit was performed.

Initial device probing exposed a missing internal configd URI allowlist entry; apply refused before
selecting a file. The allowlist and its policy regression checks were fixed before acceptance.
Cold web-app launch also exceeded an initial three-second probe window; bounded foreground-window
polling replaced that test assumption. These initial probe failures are retained in the host logs.

## Local validation

Commands were run from the worktree; firmware roots are supplied explicitly where required.

| Command | Observed final result |
| --- | --- |
| `ASAN_OPTIONS=detect_leaks=0 make -C service test` | `344 checks, 0 failed`. Address/undefined sanitizers enabled; LeakSanitizer disabled because this host's tracing environment rejected it. |
| `make -C service test-homekey ROOTFS=/home/mariotaku/Projects/webos-firmwares/04.60.90.01-HE_DTV_W22P_AFADATAA/rootfs` | Native transaction PASS under qemu with firmware libraries and fake Luna: bounded readiness, failed toast, exact list metadata/order, live changes, missing app, write/readback recovery, external-edit refusal, disable, unknown-shape stock recovery, empty-list refusal and retained files after failed recovery. |
| `OPENHOUSE_STOCK_COPIES=/home/mariotaku/Projects/webosbrew/OpenHouse/scratch/hk-universal/rw/stock sh service/scripts/tests/test-minimal-hook.sh` | `18 firmware sources passed; 1 missing sources` (the configured 6.3.1 root). Exact-byte edit oracle, executed branches/gates, syntax and mutation refusal. Corpus copies were only read. |
| `sh service/scripts/tests/test-real-roundtrip.sh` with the same corpus environment | Same 18-source pass and explicit missing-source report. |
| `sh service/scripts/tests/test-homechooser.sh` | Boot entry PASS: one nonblocking native unit, no boot waits, existing unit retained; also passed with `busybox sh`. |
| `sh service/scripts/tests/test-homechooser-home-watch.sh` | `628 passed, 0 failed (sh)` and `628 passed, 0 failed (busybox sh)`. |
| `sh service/scripts/tests/test-homechooser-access.sh` | `access: all checks passed`, under both shells. |
| `npm --prefix app test -- --runInBand --cacheDirectory=../build/jest` | `8 passed` suites, `216 passed` tests. |
| `npm --prefix app run lint` | No code errors or warnings after the unused import was removed; only the existing Browserslist age notice. |
| `python3 -m unittest discover -s e2e -p test_e2e_logic.py` | `Ran 110 tests`, `OK`. Unix-socket QMP tests required running outside the sandbox. |
| `make -C service check-abi` | Oldest supported 5.6.1 rootfs supplies all needed libraries/symbols; GLIBC_2.4, 2.9, 2.11 offered; missing undefined symbols: `none`. GLib minimum version is pinned so SDK headers do not introduce newer GLib symbols. |
| `npm --prefix app run package` | Production ARM package built. |
| `git diff --check` | Passed. |

The full UI-driven E2E scenario matrix was not run; device acceptance used ares-install and the
native production commands, with the production elevation/link RPC exercised separately. The
final removal of the unused `restartSam` command was covered by the native policy and app tests;
the device trials exercised the unchanged live-filter code.

## Remaining certification boundary

**Inferred:** matching controller subscription/loader architecture and passing source tests support
portability to the available 6/8/9 firmware variants. They do not certify runtime permissions,
Qt include caching, boot timing, fault behavior or launcher semantics there.

**Unverified:** live device coverage for 6.x, 8.x and 9.x, retail TVs, the absent 6.3.1 source,
long-press/voice/multiview behavior, cold-boot timing variation and arbitrary launcher failure races.
The full matrix needs the same first apply, boot continuity, HOME, live change, missing-app recovery,
disable/uninstall, crash-dialog and volatile-reboot checks. Optional QML semantics from the former
broad patch are intentionally outside this JS-only implementation.

Suggested commit message: `Reload Home key filters live on webOS 6–9`
