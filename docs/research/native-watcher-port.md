# Native Home watcher: offline port

Scope: issue webosbrew/home-chooser#5, on the module layout landed in #4
(`3c10238`). This document was started **before implementation**, by reading
`scripts/homechooser-home-watch`, `scripts/lib/watch-*.sh`, `lib/common.sh`,
`scripts/homechooser`, the Home-key worker, and the shell watcher suite.
No TV, VM, SSH, ares command or beanjar is used in this phase.

## Behaviour inventory

All rows below are **verified in source/tests**, unless explicitly marked inferred.
Shell test references are original line ranges in
`service/scripts/tests/test-homechooser-home-watch.sh` at `3c10238`; this is a
stable reference after deleting that suite. Labels identify the scenario heading,
including every loop expansion under it. The old suite contains 628 checks (C001–C628 in the index below);
transport/mechanical checks are identified separately from policy behaviour.

| ID | Behaviour to preserve | Shell checks (original lines/scenarios) |
| --- | --- | --- |
| W01 | Read major release in priority order VERSION_ID, VERSION, PRETTY_NAME; unknown = 0. Read choice on each event; delete CR, turn LF/tab into spaces, trim edges; reject empty or characters outside ASCII letters/digits/dot/underscore/hyphen. | 374–420 choice file forms; 421–430 removed while watching; release loops throughout |
| W02 | Stage runtime files by copy/chmod/rename, then exec runtime watcher. Copy watcher, homekey/access, native executable if present, all .sh/.js helpers. Failure logs and continues installed. Apply launcher access at startup, best effort. | 219–230 already chosen; 638–650 changed choice; 680–705 uninstall; 790–817 restart from runtime |
| W03 | Check Home Chooser directory absence before no-choice exit; confirm twice 15 s apart, using directory absence OR explicit SAM false. Temporary package update leaves state intact. Normal checks also query SAM even if directory exists. Unknown SAM result alone never proves absence. | 580–608 flaps/silent; 680–817 uninstall/runtime; 902–971 both-removal order/races; 1520–1530 update without choice |
| W04 | Without a usable choice inspect current record and (10+) override independently. Confirm a nonstock absent app twice, check install files both times. Stock, installed, unknown and on-disk apps remain untouched. Deduplicate same record/override id. Recheck choice and current record after delay; leave concurrent edits alone. No notifications, then exit 0 unless a choice appeared. | 1213–1372 leftover scenarios; 269–276 null without choice |
| W05 | Initial apps check precedes subscriptions. A chosen app SAM says absent at startup but with appinfo on disk is unknown; later checks trust explicit absence even with files present. Check HC again during chosen-app absence confirmation. | 560–608 missing/start/flap; 902–971 races; 1374–1396 on-disk startup |
| W06 | Subscribe settings/getSystemSettings `{category:general,keys:[defaultApps],subscribe:true}` and SAM/listApps `{subscribe:true}` as com.webos.applicationManager. Settings messages with settings or “no matched result” cause reconcile; other messages ignored. Every complete apps reply triggers an apps check (payload contents irrelevant). | 219–230 identity/payload; 278–286 absent record; 331–359 pretty/noise; apps scenarios throughout |
| W07 | Every 300 s check apps/HC as fallback for lost listApps messages. Apps subscription ending logs and keeps timer. Settings subscription ending or shared event stream ending exits 1. Foreground/life ending logs only. | 219–230 exit 1; 609–620 periodic removal; transport feeder behaviour read in watch-events.sh/main (some end branches lack a direct old assertion) |
| W08 | Matching home performs no write. Null/empty/absent home is unset. Preserve other flat defaultApps members, including string-encoded JSON records. Refuse non-flat/unreadable record. 5–8 use merged setSystemSettings. 9+ first setDefaultApp; fallback to merged settings only for case-insensitive “unknown method”, not other errors. | 232–359 merge/null/absent/9+/unknown/pretty/unreadable |
| W09 | Reconcile mismatch immediately unless last attempt is inside minimum interval. Re-read current home before waiting and again after; a stale subscription may already be corrected. Re-read choice after wait or exit 0. Intervals 10,20,40,80,160,300 s cap; streak increases for attempts less than twice previous interval apart, otherwise resets. Attempt time counts even on failure; check override after attempt. | 361–398 storm/stale; choice removal scenarios |
| W10 | 10+ ensure override names choice at start/reconcile/return. Read defaultApps profile with categories; discard top-level underscore metadata, retain all other members and categories; change only categories.home. Write atomically, rescan. Valid existing override means no call. Earlier releases ignore override. | 432–478 profile/invalid/good/wrong/pre-10 |
| W11 | Chosen absent: confirm twice 15 s apart, revert once, retain choice/flag for return, stop reasserting settings while same choice missing. 5–9 merged stock id (prefer fullhome if SAM confirms it). 10+ drop override/rescan before merged home:null, verify null by readback. Hook refresh stock only 6–9; clear baked cache. No compositor restart/unmount/stop. | 488–579 remove/return versions; 621–678 changed/missing/baked; 1021–1035 no restart |
| W12 | Chosen returns: clear missing, reset backoff, apply only if record differs, repair override, refresh hook. Installed choice changes refresh 6–9 live filter, only cache success; failed refresh retried. Absent/unknown new id does not mark synchronized. Settings choice change while missing clears missing and refreshes immediately. | 209–217 failed refresh; 488–556 return; 621–678 live change |
| W13 | Full uninstall: access remove must succeed, then platform restore and stock hook refresh must succeed before deleting state; failure exits 1 and leaves recovery files. Remove choice, flag, hook directory/stale marker, only our init symlink, boot directory, saved keyfilters/lock, empty state dir. Unrelated entries and regular init files untouched. Toast after cleanup, exit 0. Idempotent stock/null paths avoid writes. | 680–857 uninstall/idempotence; 887–971 clean_end/races; 1163–1211 toast/races; 1458–1518 persistent restore/failure retry |
| W14 | Preload only 6+ and enabled: disable stock home for installed/unknown third-party choice, enable for missing or stock choice. Once per state change. Startup stock choice/clean finish enable even if no prior disable; no-choice early exit skips preload; ordinary first on without start/exit needs no call. Calls use application.id stock home and isEnabled, never permanent. Errors best effort. | 887–900 clean_end preload; 1398–1427 version close/preload; implementation verified in watch-preload.sh (retry timings lacked direct old assertions) |
| W15 | Boot disable asynchronously retries failed calls up to 12 attempts, 10 s apart, each bounded 5 s. After success wait 45 s before ready and attempt close. Cancel pending off when state becomes on; late result cannot mark ready. Nonstartup off succeeds then immediately ready. | 1429–1440 early overlay launch; read in watch-preload.sh |
| W16 | 11+ and preload enabled add SAM/getForegroundAppInfo and getAppLifeEvents subscriptions. Close stock at most once per selected choice after preload ready and installed/nonstock. Card needs chosen app foreground. Overlay needs fresh chosen launch/relaunch/foreground plus nonblank nonstock non-HC card beneath. Cache window kind only on successful info; remember early launch; close/background clears launch. Read current foreground before closing; no close on unknown reply. Mark done before call even if close fails. Log stock relaunch once and never fight it. Reset all close state for a different choice. | 1398–1456 normal/early/unsafe/safe overlay; remaining guards verified in watch-close.sh |
| W17 | Cache title per choice after successful getAppInfo, including empty title. Failed lookup retries later. Fallback first appinfo in app dirs. Reject quotes/backslashes in title, remove control chars and trim. Missing title uses id. One alert after confirmed new removal, after revert/hook; none for startup, transient absence, no-choice repair or app-driven restore. Second removal after return alerts again. | 975–1120 notification/title/suppression/new choice |
| W18 | Alert payload has Home Chooser title and one focused OK. Explicit false retries at most 8 total, 3 s apart; after refusal fallback toast same message (with its own retries). No reply = no retry/fallback. HC uninstall sends only toast after state cleanup. Notification toggle disables title lookups and notices. Fixed caller identity, each call 5 s. | 975–1211 notification payload/retries/no reply/disabled/order |
| W19 | TERM/INT/HUP finishes cleanly (0), enables preload then cleans children/pipes/temp state. Direct no-choice early exit skips preload. Failures (settings EOF, setup errors, restore failures) exit 1 without enable. Unit Restart=on-failure, RestartSec=5. Unit entry idempotent and does not restart an active watcher; 6–9 boot enqueue is nonblocking. | 219–230 EOF; 1458–1518 retry; test-homechooser.sh; signal branch verified in main script, no direct old assertion |
| W20 | Log /tmp/homechooser-home.log with timestamps; default cap 65536 bytes, retain last 200 lines on overflow. Logging errors do not block policy. All one-shot calls bounded (10 s general, 5 s preload/close/notices). Old BusyBox timeout -t/new/absent detection, feed brace joining/FIFOs, worker PIDs and .preload marker files are shell mechanics. | 480–486 log cap; 859–885 timeout forms; pretty reply tests; read cleanup/feed code |

### File inventory (verified)

Reads: `/etc/os-release`; state `home`, `homekey`, `keyfilters-stock.json`,
generated `systemUi-*.js` via the reused native hook; stock systemUi JS; override
`/var/webos-profile/override/org.webosbrew.homechooser.json`; HC application
directory; `<id>/appinfo.json` under developer `/media/developer/apps/usr/palm/applications`,
system `/usr/palm/applications`, and store `/media/cryptofs/apps/usr/palm/applications`
(store only for install presence). Access helper reads installer manifests under
`/var/luna-service2-dev` and `/var/luna-service2`.

Writes/removes: record/profile via Luna; override and atomic temp; hook state and
generated files through homekey.c; `/tmp/homechooser-home.log` and rotation temp;
runtime executable/helpers and `.new` staging files; legacy persistent boot copy;
state `home`, `homekey`, `hook/`, `hook-stale`, `keyfilters-stock.json`, `keyfilter.lock`;
our init symlink. Access helper writes/removes our launcher api permissions and
edits only its own entry in installer manifest, then `ls-control scan-services`.
Old transport-only files: `/tmp/homechooser-home-watch.<pid>/{settings,apps,events,
foreground,life,preload,preload-ready}` and the directory itself. Test-only overrides
also include clock, sleeps, timeout binary and check mark paths; these are not
installed product interfaces.

## Native lifetime decisions

Use the existing executable for boot/apply and watcher. In-process LS2 client uses
the same application identity as `luna-send -a`, without registering the RPC name.
Single GLib thread; dispatch policy events serially, retaining bounded calls and
confirmation/backoff delays with GLib sources. Preload retry/settle remains
independent of the policy event queue. No extra periodic work beyond 300 s ticker.

The running image remains mapped after package unlink (**verified offline by unlinking the running ARM test image and completing restore; device validation pending**). It calls the native key-filter
transaction directly, so removal recovery needs no executable spawn. Atomically
stage the executable plus access/common helpers in `/tmp/homechooser-runtime`
for unit crash/restart fallback. Keep persistent copies of that same executable
and boot/access/common entry on 5/10–12 for stopped-watcher recovery next boot;
6–9 retain only volatile recovery. No second binary or migration shim.
Transport files/feeders/timeout-tool selection become LS2 callbacks, tokens and
bounded GLib timers, with equivalent observable policy rather than filesystem
markers or child-process identities. No change to the service's RPC method table.

## Later device phase — prepared, not executed

Coordinator must run this after offline review, on disposable test images/devices.
Keep the old and new packages built from recorded commits, same app selection,
firmware image, boot sequence and foreground card. Do not measure both at once.
On each build reboot, wait for boot readiness/preload settle and the same idle
period, then record MainPID, cgroup membership, NRestarts, version, selected Home,
foreground, preload state and uptime. Use at least five samples with equal spacing
and repeat each boot three times.

For old watcher include **every process in the transient unit's cgroup**, including
luna-send, awk/feeders and timer/sleep descendants. For new include native watcher
and any transient helper only if present at sample time. Read `/proc/<pid>/smaps_rollup`
(`Rss`, `Pss`, `Private_Clean`, `Private_Dirty`, `Shared_Clean`, `Shared_Dirty`),
or sum those fields in `smaps` on kernels without rollup. Capture `status` VmRSS
as cross-check. Sum PSS across the entire unit tree; show per-process and tree totals
in KiB, median/range across samples. RSS totals double-count shared pages; PSS is
the comparison. Also sample service-only process separately so on-demand RPC
activation cannot distort watcher totals. Report actual values, no estimates.

Run 6.x/7.x/8.x/9.x plus 10.x/11.x (12 if available): root permissions, exactly one
unit, boot readiness (including delayed services), HOME redirect, change choice,
stock disable, missing chosen app and return, update gap shorter/longer than 15 s,
both uninstall orders and lost listApps event via 300 s fallback, stop/no-choice
uninstall and reboot recovery, restore failure/restart, TERM, settings/SAM restart,
preload policy/boot retry, 11+ card and overlay safety/relaunch. Verify no GUI restart,
crash dialog, persistent configd mutation or leftover installed test resources.
For package update compare `/proc/<MainPID>/exe` with the old package image and
the installed binary with the new image, with stable MainPID/NRestarts and one
watcher process. The harness now uses executable hashes; running those probes
and actual memory measurements belongs to the coordinator's phase.

## Offline coverage

`test-watcher.c` tests the policy with a fake Luna transport, clock, confirmation
mutations and temporary filesystem. `test-watcher-luna.c` exercises the actual
`luna.c` client and `watcher/main.c` loop with fake LS2 entry points: registration
identity, exact subscription payloads, deadlines, cancellation, terminal replies,
hub loss, signals, bounded event queue, ticker fallback, source teardown, runtime
staging, actual executable unlink and native 8.x boot/stock-filter transactions.
Both ARM executables run with the same firmware libraries as `test-homekey`.

The runtime test compares task counts before/inside the watcher (no added thread).
A separate tiny probe found that `g_unix_signal_add` increases task count by one
with those firmware GLib libraries; the implementation therefore uses a signal
pipe and `g_unix_fd_add`. Qemu-user resolves guest `/proc/self/exe` through the
original executable pathname, which becomes unreadable after unlink; the unlink
case runs last and compares the cached image against bytes read before unlink.
No production behavior or third-party code was changed to accommodate that
emulator limitation. Recovery itself completes after the executable is unlinked.

The old shell suite was run against the original scripts before deletion:
**628 passed, 0 failed (sh)**. The following index captures every expanded old
check label, in execution order, with its native coverage. Checks for shell
feeders, timeout executable variants and worker script paths map to native
transport/deadline/copy tests; they do not require compatibility scripts.
The remaining boot script suite covers persistent staging and no-choice recovery.
A C check count includes fixture, JSON and transport assertions as well as policy
assertions; it is not a claim of that many independent scenarios.

<details>
<summary>C001–C001: a failed live apply is retried when the app list changes</summary>

Inventory: W12. Native coverage: `test_baked`.

- C001: failed start retried

</details>

<details>
<summary>C002–C008: record already holds the choice (sh)</summary>

Inventory: W02 W06 W07 W08. Native coverage: `test_record; test_client; test_runtime`.

- C002: subscription ends: exit 1
- C003: access applied at start
- C004: access staged in runtime
- C005: fixed luna identity used
- C006: no calls bypass identity
- C007: subscribed with the settings payload
- C008: no write

</details>

<details>
<summary>C009–C012: home missing, webOS 8: merged setSystemSettings</summary>

Inventory: W08. Native coverage: `test_record`.

- C009: one write
- C010: merged payload
- C011: no setDefaultApp on 8
- C012: logged

</details>

<details>
<summary>C013–C016: home null or empty reads as unset: the choice is written, replacing the null</summary>

Inventory: W08. Native coverage: `test_record`.

- C013: home null: one write
- C014: home null: merged payload, no second home
- C015: home "": one write
- C016: home "": merged payload, no second home

</details>

<details>
<summary>C017–C018: no stored choice: a null home is left alone</summary>

Inventory: W01 W04. Native coverage: `test_choices; test_leftover`.

- C017: exit 0
- C018: no write

</details>

<details>
<summary>C019–C019: record absent entirely</summary>

Inventory: W08. Native coverage: `test_record`.

- C019: creates the record

</details>

<details>
<summary>C020–C021: home different, webOS 9: setDefaultApp</summary>

Inventory: W08. Native coverage: `test_record`.

- C020: setDefaultApp payload
- C021: no setSystemSettings

</details>

<details>
<summary>C022–C023: setDefaultApp unknown method falls back to the merged write</summary>

Inventory: W08. Native coverage: `test_record`.

- C022: tried setDefaultApp
- C023: merged write replaces home

</details>

<details>
<summary>C024–C025: pretty-printed reply and a record that is a JSON string</summary>

Inventory: W06 W08. Native coverage: `test_record`.

- C024: one write
- C025: merged payload

</details>

<details>
<summary>C026–C026: replies that are not the record are ignored</summary>

Inventory: W06. Native coverage: `test_record`.

- C026: no write

</details>

<details>
<summary>C027–C028: unreadable record is not overwritten</summary>

Inventory: W08. Native coverage: `test_record`.

- C027: no setSystemSettings
- C028: logged

</details>

<details>
<summary>C029–C038: storm: one write per interval, doubling, capped</summary>

Inventory: W09. Native coverage: `test_backoff`.

- C029: ten writes for ten mismatches
- C030: first at 1000
- C031: second 10 s later
- C032: third 20 s later
- C033: fourth 40 s later
- C034: fifth 80 s later
- C035: sixth 160 s later
- C036: seventh capped at 300 s
- C037: eighth capped at 300 s
- C038: ninth capped at 300 s

</details>

<details>
<summary>C039–C040: a stale reply during the interval does not write again</summary>

Inventory: W09. Native coverage: `test_backoff`.

- C039: one write
- C040: no sleeping

</details>

<details>
<summary>C041–C075: choice file forms</summary>

Inventory: W01. Native coverage: `test_choices`.

- C041: id trimmed from 'my.home'
- C042: id trimmed from 'my.home\n'
- C043: id trimmed from 'my.home\r\n'
- C044: id trimmed from '  my.home  \n'
- C045: id trimmed from '\t my.home\r\n\n'
- C046: id trimmed from '\n\nmy.home'
- C047: empty file: exit 0
- C048: empty file: nothing subscribed
- C049: blank file: exit 0
- C050: blank file: only the record read for a leftover
- C051: blank file: nothing subscribed
- C052: invalid id 'a b': exit 0
- C053: invalid id 'a b': nothing subscribed
- C054: invalid id 'a b': logged
- C055: invalid id 'x"y': exit 0
- C056: invalid id 'x"y': nothing subscribed
- C057: invalid id 'x"y': logged
- C058: invalid id 'x;y': exit 0
- C059: invalid id 'x;y': nothing subscribed
- C060: invalid id 'x;y': logged
- C061: invalid id 'a/b': exit 0
- C062: invalid id 'a/b': nothing subscribed
- C063: invalid id 'a/b': logged
- C064: invalid id 'a$b': exit 0
- C065: invalid id 'a$b': nothing subscribed
- C066: invalid id 'a$b': logged
- C067: invalid id '$(id)': exit 0
- C068: invalid id '$(id)': nothing subscribed
- C069: invalid id '$(id)': logged
- C070: invalid id 'a\nb': exit 0
- C071: invalid id 'a\nb': nothing subscribed
- C072: invalid id 'a\nb': logged
- C073: no choice file: exit 0
- C074: no choice file: only the record read for a leftover
- C075: no choice file: nothing subscribed, no write

</details>

<details>
<summary>C076–C078: choice file removed while watching</summary>

Inventory: W01 W19. Native coverage: `test_choices; test_end_log`.

- C076: exit 0
- C077: no write
- C078: logged

</details>

<details>
<summary>C079–C089: profile override, webOS 10 and later</summary>

Inventory: W10. Native coverage: `test_profiles`.

- C079: profile read
- C080: override written
- C081: rescan
- C082: no record write when it matches
- C083: good override: no getProfiles
- C084: good override: no rescan
- C085: wrong override rewritten
- C086: stale override rebuilt once, then fine after the write
- C087: profile without categories: no override
- C088: logged
- C089: webOS 8: no profile call

</details>

<details>
<summary>C090–C090: log is capped</summary>

Inventory: W20. Native coverage: `test_end_log`.

- C090: log trimmed

</details>

<details>
<summary>C091–C110: chosen app uninstalled and installed again, webOS 6 to 8</summary>

Inventory: W11 W12. Native coverage: `test_remove_return`.

- C091: 6: exit 1 (subscription ended)
- C092: 6: home written as the stock id (not null), other entries kept
- C093: 6: no home written while the app is missing, then once when it is back
- C094: 6: written again with home
- C095: 6: Home key hook refreshed to stock
- C096: 6: Home key hook refreshed to the choice after
- C097: 6: choice file kept
- C098: 6: logged
- C099: 6: logged return
- C100: 6: no setDefaultApp
- C101: 8: exit 1 (subscription ended)
- C102: 8: home written as the stock id (not null), other entries kept
- C103: 8: no home written while the app is missing, then once when it is back
- C104: 8: written again with home
- C105: 8: Home key hook refreshed to stock
- C106: 8: Home key hook refreshed to the choice after
- C107: 8: choice file kept
- C108: 8: logged
- C109: 8: logged return
- C110: 8: no setDefaultApp

</details>

<details>
<summary>C111–C115: chosen app uninstalled and installed again, webOS 5</summary>

Inventory: W11 W12. Native coverage: `test_remove_return`.

- C111: stock fullhome named while missing
- C112: re-applied after
- C113: two writes
- C114: no Home key hook on 5
- C115: no fullhome installed: com.webos.app.home

</details>

<details>
<summary>C116–C118: chosen app uninstalled and installed again, webOS 9</summary>

Inventory: W11 W12. Native coverage: `test_remove_return`.

- C116: removal is a setSystemSettings write
- C117: return uses setDefaultApp
- C118: one setDefaultApp

</details>

<details>
<summary>C119–C122: chosen app uninstalled, webOS 6 to 9: fullhome is the stock id when installed</summary>

Inventory: W11. Native coverage: `test_remove_return`.

- C119: 6: fullhome named while the app is missing
- C120: 6: never null
- C121: 9: fullhome named while the app is missing
- C122: 9: never null

</details>

<details>
<summary>C123–C136: chosen app uninstalled and installed again, webOS 10 and later</summary>

Inventory: W10 W11 W12. Native coverage: `test_remove_return`.

- C123: 10: removal wrote home as null
- C124: 10: rescan after removing the override
- C125: 10: override rebuilt when it came back
- C126: 10: setDefaultApp when it came back
- C127: 10: no Home key hook
- C128: 10: rescan before the record write
- C129: 11: removal wrote home as null
- C130: 11: rescan after removing the override
- C131: 11: override rebuilt when it came back
- C132: 11: setDefaultApp when it came back
- C133: 11: no Home key hook
- C134: 11: rescan before the record write
- C135: override file removed
- C136: nothing re-asserted while missing

</details>

<details>
<summary>C137–C139: chosen app already missing at start</summary>

Inventory: W05 W11. Native coverage: `test_remove_return`.

- C137: reverted at start
- C138: one write, the removal
- C139: no getProfiles (override not rebuilt)

</details>

<details>
<summary>C140–C146: a transient absence is not acted on</summary>

Inventory: W03 W05. Native coverage: `test_uninstall; test_remove_return`.

- C140: no write
- C141: checked twice (and asked once for its title)
- C142: no revert logged
- C143: Home Chooser answered again: no restore
- C144: choice file kept
- C145: link kept
- C146: no write

</details>

<details>
<summary>C147–C149: getAppInfo does not answer</summary>

Inventory: W05. Native coverage: `test_remove_return`.

- C147: nothing reverted
- C148: logged
- C149: choice kept

</details>

<details>
<summary>C150–C151: the periodic check notices a removal without a listApps message</summary>

Inventory: W07 W11. Native coverage: `test_remove_return; test_runtime (lost app event)`.

- C150: reverted by the timer
- C151: mark says timer

</details>

<details>
<summary>C152–C154: choice changed while the old one is missing</summary>

Inventory: W12. Native coverage: `test_baked`.

- C152: the new choice is written at once
- C153: two writes: the removal and the new choice
- C154: Home key hook refreshed for the new choice

</details>

<details>
<summary>C155–C169: choice changed while the app is installed: baked id refreshed without a restart</summary>

Inventory: W02 W12. Native coverage: `test_baked; test_runtime; test-homekey`.

- C155: 6: record carries the new choice
- C156: 6: the start refresh and the one for the new choice
- C157: 6: no refresh asks for a restart
- C158: 6: no stop
- C159: 6: the refresh ran from the runtime copy
- C160: 8: record carries the new choice
- C161: 8: the start refresh and the one for the new choice
- C162: 8: no refresh asks for a restart
- C163: 8: no stop
- C164: 8: the refresh ran from the runtime copy
- C165: 9: record carries the new choice
- C166: 9: the start refresh and the one for the new choice
- C167: 9: no refresh asks for a restart
- C168: 9: no stop
- C169: 9: the refresh ran from the runtime copy

</details>

<details>
<summary>C170–C170: unchanged choice: no refresh on later settings messages</summary>

Inventory: W12. Native coverage: `test_baked`.

- C170: only the start refresh

</details>

<details>
<summary>C171–C171: choice changed to an app that is not installed: left to the removal path</summary>

Inventory: W12. Native coverage: `test_baked`.

- C171: only the start refresh for an uninstalled app

</details>

<details>
<summary>C172–C173: choice changed on webOS 5 and 10: the Home key hook is not involved</summary>

Inventory: W12. Native coverage: `test_baked`.

- C172: 5: no Home key hook call
- C173: 10: no Home key hook call

</details>

<details>
<summary>C174–C201: Home Chooser uninstalled</summary>

Inventory: W02 W13. Native coverage: `test_uninstall; test_runtime`.

- C174: 6: exit 0
- C175: 6: home is the stock id in the record, not null
- C176: 6: other entries kept
- C177: 6: choice file removed
- C178: 6: Home key hook flag removed
- C179: 6: init.d link removed
- C180: 6: app directory is gone
- C181: 6: no Home key hook stop, no unmount, no restart
- C182: 6: the worker was not asked to restart anything
- C183: 6: no worker call after the revert
- C184: 6: the watcher itself ran from the runtime
- C185: 6: logged
- C186: 6: nothing written after the restore
- C187: 9: exit 0
- C188: 9: home is the stock id in the record, not null
- C189: 9: other entries kept
- C190: 9: choice file removed
- C191: 9: Home key hook flag removed
- C192: 9: init.d link removed
- C193: 9: app directory is gone
- C194: 9: no Home key hook stop, no unmount, no restart
- C195: 9: the worker was not asked to restart anything
- C196: 9: no worker call after the revert
- C197: 9: the watcher itself ran from the runtime
- C198: 9: logged
- C199: 9: nothing written after the restore
- C200: an unrelated init.d link is left alone
- C201: a regular file in init.d is left alone

</details>

<details>
<summary>C202–C206: Home Chooser uninstalled, webOS 5</summary>

Inventory: W13. Native coverage: `test_uninstall`.

- C202: exit 0
- C203: home names the stock launcher
- C204: choice removed
- C205: link removed
- C206: no Home key hook on 5

</details>

<details>
<summary>C207–C213: Home Chooser uninstalled, webOS 10 and later</summary>

Inventory: W10 W13. Native coverage: `test_uninstall`.

- C207: exit 0
- C208: override removed
- C209: rescanned
- C210: home removed
- C211: access removed from runtime after uninstall
- C212: choice, flag and link removed
- C213: no Home key hook on 11

</details>

<details>
<summary>C214–C226: both removed, either order</summary>

Inventory: W03 W13. Native coverage: `test_restore_races`.

- C214: chosen first: exit 0
- C215: chosen first: one write in all
- C216: chosen first: stock id in the record
- C217: chosen first: second revert found nothing to do
- C218: chosen first: all cleaned
- C219: both at once: exit 0
- C220: both at once: one write
- C221: both at once: stock state, nothing left for a later removal to break
- C222: both at once: all cleaned
- C223: both at once: no stop
- C224: Home Chooser first: exit 0, then my.home may go any time
- C225: Home Chooser first: record is stock
- C226: Home Chooser first: no choice for anything to enforce

</details>

<details>
<summary>C227–C236: the restore runs from the runtime copy after the app is gone (unit restart)</summary>

Inventory: W02 W13. Native coverage: `test_runtime`.

- C227: first run staged the copies
- C228: first run staged sourced helpers
- C229: first run staged the native dispatch script
- C230: exit 0
- C231: record is stock
- C232: choice removed
- C233: runtime restore removes the populated hook cache
- C234: runtime restore removes the stale marker
- C235: link removed
- C236: no stop from the runtime copy either

</details>

<details>
<summary>C237–C239: idempotent: a second restore changes nothing</summary>

Inventory: W13. Native coverage: `test_restore_races`.

- C237: runs from the repository copy, app dir missing: exit 0
- C238: 6 to 9: a record without home gets the stock id
- C239: 6 to 9: record is stock, other entries kept

</details>

<details>
<summary>C240–C240: idempotent: 10 and later, a record without home changes nothing</summary>

Inventory: W13. Native coverage: `test_restore_races`.

- C240: no write (nothing to remove)

</details>

<details>
<summary>C241–C241: idempotent: 6 to 9, home already the stock id changes nothing</summary>

Inventory: W13. Native coverage: `test_restore_races`.

- C241: no write (already stock)

</details>

<details>
<summary>C242–C246: idempotent: 10 and later, home already null changes nothing; 6 to 9 replace the null with the stock id</summary>

Inventory: W13. Native coverage: `test_restore_races`.

- C242: exit 0
- C243: no write (nothing to clear)
- C244: exit 0
- C245: null replaced by the stock id
- C246: record is stock

</details>

<details>
<summary>C247–C254: timeout in both forms (BusyBox before 1.30 wants -t)</summary>

Inventory: W20. Native coverage: `test_client (native deadline replaces timeout command selection)`.

- C247: new: removal still detected and reverted
- C248: new: called as timeout 10
- C249: new: no -t call used
- C250: old: removal still detected and reverted
- C251: old: called as timeout -t 10
- C252: none: removal still detected and reverted
- C253: none: no timeout used
- C254: a timeout that works in neither form is not used

</details>

<details>
<summary>C255–C312: both removed: Home Chooser goes while the chosen app is being confirmed</summary>

Inventory: W03 W13 W14. Native coverage: `test_restore_races`.

- C255: 5/UNINSTALL: record names the stock launcher
- C256: 5/UNINSTALL: exit 0
- C257: 5/UNINSTALL: choice, flag and link removed
- C258: 5/UNINSTALL: the chosen app's revert was not made first and then repeated
- C259: 5/QUIET-UNINSTALL: record names the stock launcher
- C260: 5/QUIET-UNINSTALL: exit 0
- C261: 5/QUIET-UNINSTALL: choice, flag and link removed
- C262: 5/QUIET-UNINSTALL: the chosen app's revert was not made first and then repeated
- C263: 8/UNINSTALL: exit 0 (the unit stays stopped)
- C264: 8/UNINSTALL: choice file removed
- C265: 8/UNINSTALL: init.d link removed
- C266: 8/UNINSTALL: flag removed
- C267: 8/UNINSTALL: record restored (stock id)
- C268: 8/UNINSTALL: stock restored line logged
- C269: 8/UNINSTALL: stock home preload policy enabled again
- C270: 8/UNINSTALL: the chosen app's revert was not made first and then repeated
- C271: 8/QUIET-UNINSTALL: exit 0 (the unit stays stopped)
- C272: 8/QUIET-UNINSTALL: choice file removed
- C273: 8/QUIET-UNINSTALL: init.d link removed
- C274: 8/QUIET-UNINSTALL: flag removed
- C275: 8/QUIET-UNINSTALL: record restored (stock id)
- C276: 8/QUIET-UNINSTALL: stock restored line logged
- C277: 8/QUIET-UNINSTALL: stock home preload policy enabled again
- C278: 8/QUIET-UNINSTALL: the chosen app's revert was not made first and then repeated
- C279: 9/UNINSTALL: exit 0 (the unit stays stopped)
- C280: 9/UNINSTALL: choice file removed
- C281: 9/UNINSTALL: init.d link removed
- C282: 9/UNINSTALL: flag removed
- C283: 9/UNINSTALL: record restored (stock id)
- C284: 9/UNINSTALL: stock restored line logged
- C285: 9/UNINSTALL: stock home preload policy enabled again
- C286: 9/UNINSTALL: the chosen app's revert was not made first and then repeated
- C287: 9/QUIET-UNINSTALL: exit 0 (the unit stays stopped)
- C288: 9/QUIET-UNINSTALL: choice file removed
- C289: 9/QUIET-UNINSTALL: init.d link removed
- C290: 9/QUIET-UNINSTALL: flag removed
- C291: 9/QUIET-UNINSTALL: record restored (stock id)
- C292: 9/QUIET-UNINSTALL: stock restored line logged
- C293: 9/QUIET-UNINSTALL: stock home preload policy enabled again
- C294: 9/QUIET-UNINSTALL: the chosen app's revert was not made first and then repeated
- C295: 11/UNINSTALL: exit 0 (the unit stays stopped)
- C296: 11/UNINSTALL: choice file removed
- C297: 11/UNINSTALL: init.d link removed
- C298: 11/UNINSTALL: flag removed
- C299: 11/UNINSTALL: record restored (null)
- C300: 11/UNINSTALL: stock restored line logged
- C301: 11/UNINSTALL: override removed
- C302: 11/UNINSTALL: stock home preload policy enabled again
- C303: 11/UNINSTALL: the chosen app's revert was not made first and then repeated
- C304: 11/QUIET-UNINSTALL: exit 0 (the unit stays stopped)
- C305: 11/QUIET-UNINSTALL: choice file removed
- C306: 11/QUIET-UNINSTALL: init.d link removed
- C307: 11/QUIET-UNINSTALL: flag removed
- C308: 11/QUIET-UNINSTALL: record restored (null)
- C309: 11/QUIET-UNINSTALL: stock restored line logged
- C310: 11/QUIET-UNINSTALL: override removed
- C311: 11/QUIET-UNINSTALL: stock home preload policy enabled again
- C312: 11/QUIET-UNINSTALL: the chosen app's revert was not made first and then repeated

</details>

<details>
<summary>C313–C342: both removed: the chosen app goes while Home Chooser is being confirmed</summary>

Inventory: W03 W13 W14. Native coverage: `test_restore_races`.

- C313: 8/UNINSTALL: exit 0 (the unit stays stopped)
- C314: 8/UNINSTALL: choice file removed
- C315: 8/UNINSTALL: init.d link removed
- C316: 8/UNINSTALL: flag removed
- C317: 8/UNINSTALL: record restored (stock id)
- C318: 8/UNINSTALL: stock restored line logged
- C319: 8/UNINSTALL: stock home preload policy enabled again
- C320: 8/QUIET-UNINSTALL: exit 0 (the unit stays stopped)
- C321: 8/QUIET-UNINSTALL: choice file removed
- C322: 8/QUIET-UNINSTALL: init.d link removed
- C323: 8/QUIET-UNINSTALL: flag removed
- C324: 8/QUIET-UNINSTALL: record restored (stock id)
- C325: 8/QUIET-UNINSTALL: stock restored line logged
- C326: 8/QUIET-UNINSTALL: stock home preload policy enabled again
- C327: 11/UNINSTALL: exit 0 (the unit stays stopped)
- C328: 11/UNINSTALL: choice file removed
- C329: 11/UNINSTALL: init.d link removed
- C330: 11/UNINSTALL: flag removed
- C331: 11/UNINSTALL: record restored (null)
- C332: 11/UNINSTALL: stock restored line logged
- C333: 11/UNINSTALL: override removed
- C334: 11/UNINSTALL: stock home preload policy enabled again
- C335: 11/QUIET-UNINSTALL: exit 0 (the unit stays stopped)
- C336: 11/QUIET-UNINSTALL: choice file removed
- C337: 11/QUIET-UNINSTALL: init.d link removed
- C338: 11/QUIET-UNINSTALL: flag removed
- C339: 11/QUIET-UNINSTALL: record restored (null)
- C340: 11/QUIET-UNINSTALL: stock restored line logged
- C341: 11/QUIET-UNINSTALL: override removed
- C342: 11/QUIET-UNINSTALL: stock home preload policy enabled again

</details>

<details>
<summary>C343–C357: both removed, far apart: the chosen app, then Home Chooser at the periodic check</summary>

Inventory: W03 W13 W14. Native coverage: `test_restore_races`.

- C343: 8/far apart: exit 0 (the unit stays stopped)
- C344: 8/far apart: choice file removed
- C345: 8/far apart: init.d link removed
- C346: 8/far apart: flag removed
- C347: 8/far apart: record restored (stock id)
- C348: 8/far apart: stock restored line logged
- C349: 8/far apart: stock home preload policy enabled again
- C350: 11/far apart: exit 0 (the unit stays stopped)
- C351: 11/far apart: choice file removed
- C352: 11/far apart: init.d link removed
- C353: 11/far apart: flag removed
- C354: 11/far apart: record restored (null)
- C355: 11/far apart: stock restored line logged
- C356: 11/far apart: override removed
- C357: 11/far apart: stock home preload policy enabled again

</details>

<details>
<summary>C358–C359: Home Chooser removed while the chosen app is confirmed, then put back: nothing is removed</summary>

Inventory: W03. Native coverage: `test_uninstall`.

- C358: the choice file stays (an update)
- C359: the link stays

</details>

<details>
<summary>C360–C383: the chosen app removed: one alert, after the revert, with its title</summary>

Inventory: W17 W18. Native coverage: `test_remove_return; test_notifications`.

- C360: 5: one alert, not repeated by the later checks
- C361: 5: no toast
- C362: 5: payload
- C363: 5: called through the fixed identity
- C364: 5: after the revert write
- C365: 5: reply logged
- C366: 8: one alert, not repeated by the later checks
- C367: 8: no toast
- C368: 8: payload
- C369: 8: called through the fixed identity
- C370: 8: after the revert write
- C371: 8: reply logged
- C372: 10: one alert, not repeated by the later checks
- C373: 10: no toast
- C374: 10: payload
- C375: 10: called through the fixed identity
- C376: 10: after the revert write
- C377: 10: reply logged
- C378: 11: one alert, not repeated by the later checks
- C379: 11: no toast
- C380: 11: payload
- C381: 11: called through the fixed identity
- C382: 11: after the revert write
- C383: 11: reply logged

</details>

<details>
<summary>C384–C387: the alert text is the same on 6 and 9 as everywhere: no restart sentence</summary>

Inventory: W17. Native coverage: `test_remove_return; test_notifications; E2E Texts`.

- C384: 6: same text, no restart sentence
- C385: 6: no restart sentence
- C386: 9: same text, no restart sentence
- C387: 9: no restart sentence

</details>

<details>
<summary>C388–C393: the revert of a removed chosen app never restarts the compositor (6 to 9)</summary>

Inventory: W11 W17. Native coverage: `test_remove_return; test-homekey`.

- C388: 6: refresh stock ran before the alert
- C389: 6: refresh stock without restart
- C390: 6: one alert
- C391: 9: refresh stock ran before the alert
- C392: 9: refresh stock without restart
- C393: 9: one alert

</details>

<details>
<summary>C394–C394: the alert names the id when the title is not known</summary>

Inventory: W17. Native coverage: `test_notifications`.

- C394: id in the message

</details>

<details>
<summary>C395–C395: the title is read from appinfo.json when getAppInfo carries none</summary>

Inventory: W17. Native coverage: `test_notifications`.

- C395: title from the file

</details>

<details>
<summary>C396–C396: a title with quotes or backslashes is not put into the payload</summary>

Inventory: W17. Native coverage: `test_notifications`.

- C396: falls back to the id

</details>

<details>
<summary>C397–C403: no alert for a transient absence, for a watcher start with the app missing, or for a restore from the app</summary>

Inventory: W17. Native coverage: `test_remove_return; test_choices`.

- C397: transient absence: no alert
- C398: transient absence: no toast
- C399: missing at start: reverted
- C400: missing at start: no alert (not a new removal)
- C401: restored from the app, then the app removed: exit 0
- C402: restore from the app: no alert
- C403: restore from the app: no toast

</details>

<details>
<summary>C404–C404: one alert per removal event: back and removed again is a second one</summary>

Inventory: W17. Native coverage: `test_remove_return`.

- C404: two removals, two alerts

</details>

<details>
<summary>C405–C406: a new choice replaces the missing one: no alert for it</summary>

Inventory: W12 W17. Native coverage: `test_notifications`.

- C405: the title cached for the new choice is used
- C406: one alert per removal

</details>

<details>
<summary>C407–C411: a refused alert is shown as a toast with the same message (our fallback)</summary>

Inventory: W18. Native coverage: `test_notifications`.

- C407: a refused alert is tried 8 times (a transient refusal while the compositor restarts), then falls back
- C408: one toast with the same message
- C409: one toast
- C410: the refusal is logged
- C411: the restore itself is unaffected

</details>

<details>
<summary>C412–C414: a notification call that gives no answer is logged and not retried</summary>

Inventory: W18. Native coverage: `test_notifications`.

- C412: tried once
- C413: no toast instead
- C414: logged without a reply

</details>

<details>
<summary>C415–C415: notifications can be turned off</summary>

Inventory: W18. Native coverage: `test_notifications`.

- C415: none sent

</details>

<details>
<summary>C416–C451: Home Chooser uninstalled: one toast, the Home key hook left mounted (6 to 9)</summary>

Inventory: W13 W18. Native coverage: `test_uninstall; test_runtime`.

- C416: 5: exit 0
- C417: 5: one toast
- C418: 5: no alert
- C419: 5: payload
- C420: 5: fixed identity, one reply
- C421: 5: after the record revert
- C422: 5: no worker stop, no restart
- C423: 5: the only worker calls are refreshes
- C424: 5: files gone before the toast is the last word
- C425: 6: exit 0
- C426: 6: one toast
- C427: 6: no alert
- C428: 6: payload
- C429: 6: fixed identity, one reply
- C430: 6: after the record revert
- C431: 6: no worker stop, no restart
- C432: 6: the only worker calls are refreshes
- C433: 6: files gone before the toast is the last word
- C434: 9: exit 0
- C435: 9: one toast
- C436: 9: no alert
- C437: 9: payload
- C438: 9: fixed identity, one reply
- C439: 9: after the record revert
- C440: 9: no worker stop, no restart
- C441: 9: the only worker calls are refreshes
- C442: 9: files gone before the toast is the last word
- C443: 10: exit 0
- C444: 10: one toast
- C445: 10: no alert
- C446: 10: payload
- C447: 10: fixed identity, one reply
- C448: 10: after the record revert
- C449: 10: no worker stop, no restart
- C450: 10: the only worker calls are refreshes
- C451: 10: files gone before the toast is the last word

</details>

<details>
<summary>C452–C454: both apps removed: the alert for the chosen app, then the toast for Home Chooser</summary>

Inventory: W03 W18. Native coverage: `test_restore_races`.

- C452: one alert
- C453: one toast
- C454: alert first

</details>

<details>
<summary>C455–C456: Home Chooser removed during the chosen app's confirmation: the toast only, the alert is skipped</summary>

Inventory: W03 W18. Native coverage: `test_restore_races`.

- C455: no alert
- C456: one toast

</details>

<details>
<summary>C457–C489: leftover: no choice, the record names a missing app, webOS 5 to 9: the stock id</summary>

Inventory: W04. Native coverage: `test_leftover`.

- C457: 5: exit 0
- C458: 5: stock id written, other entries kept
- C459: 5: one write, by setSystemSettings
- C460: 5: confirmed after the delay
- C461: 5: nothing subscribed
- C462: 5: no alert or toast
- C463: 5: logged as a leftover
- C464: 5: no Home key hook
- C465: 6: exit 0
- C466: 6: stock id written, other entries kept
- C467: 6: one write, by setSystemSettings
- C468: 6: confirmed after the delay
- C469: 6: nothing subscribed
- C470: 6: no alert or toast
- C471: 6: logged as a leftover
- C472: 6: baked id emptied
- C473: 8: exit 0
- C474: 8: stock id written, other entries kept
- C475: 8: one write, by setSystemSettings
- C476: 8: confirmed after the delay
- C477: 8: nothing subscribed
- C478: 8: no alert or toast
- C479: 8: logged as a leftover
- C480: 8: baked id emptied
- C481: 9: exit 0
- C482: 9: stock id written, other entries kept
- C483: 9: one write, by setSystemSettings
- C484: 9: confirmed after the delay
- C485: 9: nothing subscribed
- C486: 9: no alert or toast
- C487: 9: logged as a leftover
- C488: 9: baked id emptied
- C489: fullhome named when installed

</details>

<details>
<summary>C490–C508: leftover: webOS 10 and later, record and override name a missing app</summary>

Inventory: W04. Native coverage: `test_leftover`.

- C490: 10: exit 0
- C491: 10: override removed
- C492: 10: rescan
- C493: 10: home written as null
- C494: 10: asked once per id
- C495: 10: no Home key hook
- C496: 11: exit 0
- C497: 11: override removed
- C498: 11: rescan
- C499: 11: home written as null
- C500: 11: asked once per id
- C501: 11: no Home key hook
- C502: override only: removed
- C503: override only: no record write
- C504: override stale, record installed: override removed
- C505: override stale, record installed: the record is left alone
- C506: record stale, override installed: override kept
- C507: record stale, override installed: home null
- C508: an override file before 10 is not looked at

</details>

<details>
<summary>C509–C524: leftover: what is left alone</summary>

Inventory: W04. Native coverage: `test_leftover`.

- C509: an installed app: no write
- C510: an installed app: logged
- C511: stock id com.webos.app.home: no write
- C512: stock id com.webos.app.home: not asked about
- C513: stock id com.webos.app.fullhome: no write
- C514: stock id com.webos.app.fullhome: not asked about
- C515: getAppInfo silent: no write
- C516: getAppInfo silent: logged
- C517: getAppInfo silent, 10: override kept
- C518: getAppInfo silent, 10: no write
- C519: a transient absence: no write
- C520: a transient absence: asked twice
- C521: files on disk, sam does not know it yet: no write
- C522: files on disk: logged
- C523: files on disk: no wait
- C524: home null: nothing asked

</details>

<details>
<summary>C525–C529: leftover: a choice stored during the confirmation takes over</summary>

Inventory: W04. Native coverage: `test_leftover`.

- C525: not repaired
- C526: logged
- C527: runs as the watcher: subscribed
- C528: the choice is written
- C529: the subscription ends: exit 1

</details>

<details>
<summary>C530–C531: leftover: the record changed during the confirmation is left alone</summary>

Inventory: W04. Native coverage: `test_leftover`.

- C530: no write
- C531: logged

</details>

<details>
<summary>C532–C534: a stored choice at start: files on disk keep it from being taken as gone</summary>

Inventory: W05. Native coverage: `test_remove_return`.

- C532: no revert at start
- C533: logged
- C534: after start the files do not hold back a removal

</details>

<details>
<summary>C535–C554: only 11 and later close stock Home after the chosen normal launcher is in front</summary>

Inventory: W14 W16. Native coverage: `test_preload_close`.

- C535: 5: no preload call
- C536: 5: stock home not closed
- C537: 6: policy turned off
- C538: 6: stock home not closed
- C539: 6: logged
- C540: 8: policy turned off
- C541: 8: stock home not closed
- C542: 8: logged
- C543: 9: policy turned off
- C544: 9: stock home not closed
- C545: 9: logged
- C546: 10: policy turned off
- C547: 10: stock home not closed
- C548: 10: logged
- C549: 11: policy turned off
- C550: 11: stock home closed once
- C551: 11: relaunch logged, no retry
- C552: 12: policy turned off
- C553: 12: stock home closed once
- C554: 12: relaunch logged, no retry

</details>

<details>
<summary>C555–C555: a launch before the boot preload policy settles is remembered until it is ready</summary>

Inventory: W15 W16. Native coverage: `test_preload_close`.

- C555: early overlay launch: one deferred close

</details>

<details>
<summary>C556–C559: on 11 and 12 an overlay waits for a fresh launch over a nonstock full-screen card</summary>

Inventory: W16. Native coverage: `test_preload_close`.

- C556: 11: unsafe overlay close skipped
- C557: 11: safe overlay close once
- C558: 12: unsafe overlay close skipped
- C559: 12: safe overlay close once

</details>

<details>
<summary>C560–C619: uninstall while chosen or unchosen: running copy or next boot restores stock and leaves no residue</summary>

Inventory: W02 W13. Native coverage: `test_uninstall; test_runtime; test-homechooser.sh (persistent boot fallback)`.

- C560: 5/chosen/running: boot copy staged
- C561: 5/chosen/running: restore exits 0
- C562: 5/chosen/running: stock record
- C563: 5/chosen/running: access removed
- C564: 5/chosen/running: entry, cache, flag, marker and stored choice gone
- C565: 5/chosen/stopped: boot copy staged
- C566: 5/chosen/stopped: restore exits 0
- C567: 5/chosen/stopped: stock record
- C568: 5/chosen/stopped: access removed
- C569: 5/chosen/stopped: entry, cache, flag, marker and stored choice gone
- C570: 5/unchosen/running: boot copy staged
- C571: 5/unchosen/running: restore exits 0
- C572: 5/unchosen/running: stock record
- C573: 5/unchosen/running: access removed
- C574: 5/unchosen/running: entry, cache, flag, marker and stored choice gone
- C575: 5/unchosen/stopped: boot copy staged
- C576: 5/unchosen/stopped: restore exits 0
- C577: 5/unchosen/stopped: stock record
- C578: 5/unchosen/stopped: access removed
- C579: 5/unchosen/stopped: entry, cache, flag, marker and stored choice gone
- C580: 10/chosen/running: boot copy staged
- C581: 10/chosen/running: restore exits 0
- C582: 10/chosen/running: cleared record
- C583: 10/chosen/running: access removed
- C584: 10/chosen/running: entry, cache, flag, marker and stored choice gone
- C585: 10/chosen/stopped: boot copy staged
- C586: 10/chosen/stopped: restore exits 0
- C587: 10/chosen/stopped: cleared record
- C588: 10/chosen/stopped: access removed
- C589: 10/chosen/stopped: entry, cache, flag, marker and stored choice gone
- C590: 10/unchosen/running: boot copy staged
- C591: 10/unchosen/running: restore exits 0
- C592: 10/unchosen/running: cleared record
- C593: 10/unchosen/running: access removed
- C594: 10/unchosen/running: entry, cache, flag, marker and stored choice gone
- C595: 10/unchosen/stopped: boot copy staged
- C596: 10/unchosen/stopped: restore exits 0
- C597: 10/unchosen/stopped: cleared record
- C598: 10/unchosen/stopped: access removed
- C599: 10/unchosen/stopped: entry, cache, flag, marker and stored choice gone
- C600: 12/chosen/running: boot copy staged
- C601: 12/chosen/running: restore exits 0
- C602: 12/chosen/running: cleared record
- C603: 12/chosen/running: access removed
- C604: 12/chosen/running: entry, cache, flag, marker and stored choice gone
- C605: 12/chosen/stopped: boot copy staged
- C606: 12/chosen/stopped: restore exits 0
- C607: 12/chosen/stopped: cleared record
- C608: 12/chosen/stopped: access removed
- C609: 12/chosen/stopped: entry, cache, flag, marker and stored choice gone
- C610: 12/unchosen/running: boot copy staged
- C611: 12/unchosen/running: restore exits 0
- C612: 12/unchosen/running: cleared record
- C613: 12/unchosen/running: access removed
- C614: 12/unchosen/running: entry, cache, flag, marker and stored choice gone
- C615: 12/unchosen/stopped: boot copy staged
- C616: 12/unchosen/stopped: restore exits 0
- C617: 12/unchosen/stopped: cleared record
- C618: 12/unchosen/stopped: access removed
- C619: 12/unchosen/stopped: entry, cache, flag, marker and stored choice gone

</details>

<details>
<summary>C620–C625: a failed Home write keeps the boot restore copy for a retry</summary>

Inventory: W13 W19. Native coverage: `test_uninstall; test_restore_races; test-homechooser.sh`.

- C620: 10: failed write exits nonzero
- C621: 10: entry and cache kept
- C622: 10: record still names the launcher
- C623: 10: retry succeeds
- C624: 10: null record
- C625: 10: retry removes boot state

</details>

<details>
<summary>C626–C628: no-choice startup during an update waits for Home Chooser to return</summary>

Inventory: W03. Native coverage: `test_uninstall`.

- C626: temporary absence: no stock write
- C627: temporary absence: state kept
- C628: temporary absence: return logged

</details>

## Observed offline results

| Command/check | Observed result |
| --- | --- |
| Original watcher suite, before deletion | 628 passed, 0 failed (sh) |
| `make -C service test-watcher`, policy executable | 8269 passed, 0 failed |
| Same target, actual Luna client/runtime executable | 446 passed, 0 failed |
| `ASAN_OPTIONS=detect_leaks=0 make -C service test` | 349 checks, 0 failed |
| `make -C service test-homekey` | PASS native transaction (suite prints no numeric assertion count) |
| `make -C service check-abi` | All five SONAMEs present; GLIBC_2.4/2.9/2.11 offered; no undefined symbols missing |
| All five remaining `sh service/scripts/tests/test-*.sh` suites | Passed; each corpus wrapper reports 10 firmware sources passed, 9 missing sources |
| `npm --prefix app test -- --runInBand` | 8 suites, 216 tests passed |
| `TMPDIR=/tmp/nw python3 -m unittest discover -s e2e -p test_e2e_logic.py` | 110 tests, OK |
| Packaging stage | One executable, services.json, three entry/access scripts and common.sh; no watcher shell/helper files |
| RPC method table comparison with base | Unchanged, 21 registered methods |
| Stripped executable | 67,792 bytes before; 92,540 bytes after; +24,748 bytes |

`check-abi.sh` now honors TMPDIR and fails on temporary-file creation errors.
Its earlier hard-coded `/tmp` scratch files failed in this restricted workspace,
while the script misleadingly returned success; the results above are from the
corrected helper with writable temporary files. This is a validation fix, not an
ABI exemption.

The missing corpus paths are printed by the existing suites and retained in
`service/scripts/tests/corpus.tsv`: 6.3.1-W21Z, 6.5.2-W21A, 6.6.0-12-W21U,
7.6.0-18-W22H, 8.3.0-1063-S23Z, 8.3.0-2301-W22O, 8.3.0-2401-W22H,
9.2.1-40-W22H and 9.2.4-W24P. No missing source is treated as a device pass.

The native queue retains at most 128 events and each retained payload at most
64 KiB (app-list contents are discarded because they are only a trigger).
Overflow is an explicit failure/restart, replacing the shell pipes' backpressure;
normal event ordering, including confirmation and backoff, is serialized. This
transport overload rule is additional to the shell policy and is tested explicitly.
Foreground/life and app-list subscription termination degrades; settings or hub
loss restarts. Endpoints needed by the watcher were added only to the private
Luna allowlist; the RPC forwarding allowlist and method surface are unchanged.

The E2E harness's installed-text, process-count and package-update probes now use
the native executable and `/proc/<MainPID>/exe` hash. Those device-facing probes
were updated but not run here. Device acceptance, actual hub permissions, RSS/PSS
and release portability remain the coordinator's separate phase.

### Read-only memory collection example for that later phase

Run inside the coordinator's VM session only, after the equal settling period
above. Adapt the cgroup mount prefix if the firmware uses a unified hierarchy.
The command reads state and prints samples; it does not alter preload or Home.

```sh
cg=$(systemctl show homechooser-home -p ControlGroup | sed -n 's/^ControlGroup=//p')
systemctl show homechooser-home -p MainPID -p NRestarts -p ActiveState
# cgroup.procs gives processes rather than counting each thread separately.
pids=$(find "/sys/fs/cgroup/systemd$cg" -name cgroup.procs -exec cat '{}' \; | sort -nu)
for pid in $pids; do
    [ -r "/proc/$pid/smaps" ] || continue
    source=/proc/$pid/smaps_rollup
    [ -r "$source" ] || source=/proc/$pid/smaps
    printf 'pid=%s ' "$pid"
    awk '$1 == "Rss:" {rss += $2} $1 == "Pss:" {pss += $2}
         $1 == "Private_Clean:" {pc += $2} $1 == "Private_Dirty:" {pd += $2}
         END {printf "rss_kib=%d pss_kib=%d private_kib=%d\n", rss, pss, pc+pd}' "$source"
    sed -n '/^VmRSS:/p' "/proc/$pid/status"
done
```

Archive the printed per-process values with firmware/build/boot-state metadata,
then sum each sample's RSS and PSS and compare repeated-boot medians/ranges.
A PID disappearing during a sample invalidates that sample; repeat after the
same idle interval rather than silently omitting a member of the old tree.
