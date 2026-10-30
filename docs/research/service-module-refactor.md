# Native service module refactor

Offline verification for [issue #4](https://github.com/webosbrew/home-chooser/issues/4), against
`b17aad7`. One executable retains the on-demand LS2 service and root `--boot` / `--homekey` roles.
The shell watcher is unchanged. No TV, VM, Beanjar or ares command was used.

## Section map

Paths below are under `service/src/`. Sections previously lived in `main.c` unless noted.

| Old section | New file |
| --- | --- |
| Startup and role selection | `main.c`, `log.h`, `version.h`, `roles.h` |
| Root one-off worker and watcher exec | `homekey-main.c` |
| JSON parsing, strings, setters, reply validation | `json.c`, `json.h` |
| Bounded privileged Luna calls and callback context | `luna.c`, `luna.h` |
| `run_argv`, pipes, output cap, timeout and reaping | `proc.c`, `proc.h` |
| `mkdir_p`, `slurp`, atomic writes | `fs.c`, `fs.h` |
| Settings gate, LS2 responses and launcher errors | `service/reply.c`, `service/reply.h` |
| Status, quit, registration and main loop | `service/main.c`, `service/methods.h` |
| File request validation and handlers | `service/files.c`, `service/files.h` |
| Forwarded calls and settings `readIcon` | `service/call.c` |
| Boot link, staging and link status | `bootlink.c` |
| Named commands, output sanitization and log tail | `service/run.c` |
| Home choice, icons, app/process field projection | `apps.c`, `apps.h` |
| Launcher gates, consent, operations and uninstall tokens | `service/launcher.c` |
| Notification acceptance and logging | `notify.c`, `notify.h` |
| Reboot pending check and prompt | `service/system.c` |
| Existing policy, Home-key transaction and generator | `policy.*`, `homekey.*`, `homekey-generator.c`, `homekey-shapes.inc` remain in place |

The issue's suggested layout is followed with three additional seams: shared file primitives in
`fs.c`, LS2 responses/gates in `service/reply.c`, and the named command handler in `service/run.c`.
Boot link gates and RPC replies remain beside the link operations. The one-off worker has its own
`homekey-main.c`; no empty watcher implementation is added. A dispatch comment records the place
for the native watcher role in issue #5. Luna transport stays synchronous and bounded; there is no
subscription or watcher port in this change.

The worker emits `active=yes/no` instead of `mounted=yes/no`. The app's parsed property is `active`,
and its view hook returns `patchActive`. App fixtures, native assertions, E2E status checks and
current documentation use the new field. No alias or migration code was added. Historical
research transcripts retain their original output.

## Interface and behaviour comparison

A source comparison checked the complete registered `LSMethod` table byte for byte: all 21 names,
handler mappings, flags, ordering and the sentinel match. Registration still uses category `/`.
All 83 original helper/handler definitions match after ignoring comments/whitespace, removing
`static` on functions shared across modules, and renaming `reboot_notify` to `notify_call`.
This includes argument validation, gates and replies. The original main's initialization, worker
argument/root/action handling, shell watcher exec, and LS2 lifecycle were separately compared to
the extracted role bodies. Unknown arguments still enter the service role.

`policy.c`, `policy.h`, the generator and its shapes match the baseline byte for byte. The
Home-key transaction implementation differs only in the emitted status field name. Existing
function bodies, callback lifetimes, command timeouts, token state and error text were retained.
The new native assertions cover inactive status, active status and stock restoration, and reject
emission of the old field.

## Test results

| Command | Output/result |
| --- | --- |
| `ASAN_OPTIONS=detect_leaks=0 make -C service test` | `344 checks, 0 failed` |
| `make -C service test-homekey` | `PASS native transaction: boot readiness, failed toast, exact list edits, live change, missing app, write/readback recovery, disable, empty-list refusal` |
| `make -C service check-abi` (production binary) | Five needed libraries present; all three required GLIBC versions offered; undefined symbols absent from rootfs libraries: `none` |
| `sh service/scripts/tests/test-homechooser-access.sh` | `access: all checks passed (sh)`; also passed with `busybox sh` as the shell argument |
| `sh service/scripts/tests/test-homechooser-home-watch.sh` | `628 passed, 0 failed (sh)` and `628 passed, 0 failed (busybox sh)` |
| `sh service/scripts/tests/test-homechooser-homekey.sh` | Native transaction PASS |
| `sh service/scripts/tests/test-homechooser.sh` | Boot entry PASS; also passed with `busybox sh` as the shell argument |
| `sh service/scripts/tests/test-minimal-hook.sh` | `18 firmware sources passed; 1 missing sources` |
| `sh service/scripts/tests/test-real-roundtrip.sh` | `18 firmware sources passed; 1 missing sources` |
| `npm --prefix app test -- --runInBand` | `Test Suites: 8 passed, 8 total`; `Tests: 216 passed, 216 total` |
| `python3 -m unittest discover -s e2e -p test_e2e_logic.py` | `Ran 110 tests`; `OK` |

All nine script invocations passed. The three scripts accepting a shell argument were run under
both host sh and BusyBox sh; the other scripts wrap native/Python tests. The corpus runs used
`OPENHOUSE_STOCK_COPIES=/home/mariotaku/Projects/webosbrew/OpenHouse/scratch/hk-universal/rw/stock`,
read-only. Both explicitly reported the absent 6.3.1-W21Z `systemUi.js` listed in
`service/scripts/tests/corpus.tsv`; no synthetic replacement was supplied.

Temporary files were directed inside the worktree. The ABI script needed sandbox permission for
its hardcoded `/tmp` paths. E2E logic needed local Unix socket permission and a short
`TMPDIR="$PWD/build/t"`: the initial worktree scratch socket path was 110 bytes, exceeding the
Unix socket pathname limit. The final unmodified test command passed with that environment.
App dependencies were copied from the existing checkout into this worktree for the offline run.

## Production binary

Both builds used the same Makefile production flags, SDK and strip tool.

| | Stripped bytes |
| --- | ---: |
| Before | 71,888 |
| After | 67,792 |
| Reduction | 4,096 |

`readelf -d` reports the identical five dependencies: `libluna-service2.so.3`, `libpbnjson_c.so.2`,
`libPmLogLib.so.3`, `libglib-2.0.so.0` and `libc.so.6`. `readelf -V` reports the identical version
set: `GLIBC_2.4`, `GLIBC_2.9`, `GLIBC_2.11`. No strong undefined dynamic symbols were added.
`GLIBC_2.11` was already required before the refactor; `docs/service.md` now reflects that measured
baseline. No libraries or symbol versions were added.
