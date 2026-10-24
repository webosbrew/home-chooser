# Minimal-hook verification, 2026-10-24

The implementation is on `minimal-hook`. The VM matrix was stopped during the required 9.2.4
preflight because the assumed recovery signal is insufficient. No full E2E run or later VM was
started. The production rewriter's tier-1 behavior passed the direct runtime probes; the recovery
assumption needs a design decision before continuing device verification.

## Recovery result

A deliberately invalid staged Settings.qml was bind-mounted by hand over the healthy staged copy.
The compositor restart returned success, while its root QML failed to load:

```
broken-restart-rc=0
NRestarts=0
ActiveState=active
SubState=running
[715.982234036] ... main.qml:31:5: Type Views unavailable
[715.982728943] ... Settings.qml:174:1: Syntax error
```

The process was active and systemd considered it ready. The worker's nonzero-restart check would
therefore not invoke rollback. A functional QML readiness signal is needed; systemctl success alone
cannot establish it. No Home key was sent with broken QML because there was no verified Home
Chooser screen. The broken mount was undone immediately; a restart of the healthy copy returned 0
and the compositor API answered again. This is a **product recovery gap**, not a successful recovery
verification. No production design change was made after this result, honoring the stop condition.

## The four 9.2.4 questions

| Question | Observed result |
| --- | --- |
| New applicationManager members reach scripts? | Yes. A temporary diagnostic field set by the added getter became `hcMember: true`; the included script recorded `hcType: "function"`. |
| Included systemUi.js can reassign HOME_APP_ID? | Yes. The script recorded `hcId: "org.webosbrew.hcfixture.web.normal"` after Home; applicationManager life events included `splash` and `launch`, reason `hotKey`, for that launcher. |
| homeAppId starts a subscription and follows changes? | Yes. The compositor logged registration, pending and subscription for `general/defaultApps` at startup. A temporary read-only diagnostic response reported `hcBound: "com.webos.app.home"`, then the fixture id after a record write, before any Home press. |
| Broken patched QML? | systemd reports a running/ready compositor despite root-QML compilation failure. Manual unmount and restart recover the healthy copy. Automatic recovery does not detect this case. |

Diagnostics existed only in temporary staged copies, not in the committed rewriter or packaged app.
Qt console output after initialization was insufficient for these probes, so temporary fields on the
existing getCurrentState reply exposed the actual bound and script values. The saved compositor logs
include the defaultApps subscription before any test Home key.

Artifacts: `e2e/results/w24g-ombre-minimal-preflight-20261024/` (git-ignored), including
`preflight.json`, direct property replies, Home life events, compile/recovery logs, cleanup evidence
and screenshots. Firmware source copies used during diagnosis remain under ignored scratch only.

## Offline checks

Every backport suite passed under both sh and an executable wrapper containing
`exec busybox sh "$@"`:

| Suite | sh | busybox sh |
| --- | ---: | ---: |
| test-generated.sh (compatibility entry) | 502 | 502 |
| test-minimal-hook.sh | 502 | 502 |
| test-homechooser-keyfilter.sh | 170 | 170 |
| test-real-roundtrip.sh | 399 | 399 |
| test-homechooser-home-watch.sh | 551 | 551 |
| test-homechooser.sh | 50 | 50 |
| test-homechooser-access.sh | all checks passed | all checks passed |

There were zero failed or skipped corpus checks. Each minimal-hook invocation covered 19 real
supported builds plus one synthetic anchor skeleton, with 16 matching reviewed-set comparisons
and 2,736 Home-key scenarios. The same 502 checks passed with Node 8.12.0.

`make -C service test`: 298 checks, zero failures. The sandbox's LeakSanitizer cannot operate under
ptrace; the unchanged sanitizer configuration passed outside that sandbox.

`npm ci`, app lint and Jest passed: 213 tests in eight suites. E2E Python unit tests passed: 62.
Packaging succeeded; the ipk contains exactly the five backport runtime scripts and no reviewed
manifests or diffs. Logs are under `scratch/` (`all-backport-tests.log`, `node8-tests.log`,
`app-lint.log`, `app-tests.log`, `e2e-unit.log`, `package.log`).

The worker and rewriter plus formerly shipped reviewed sets shrank from 99 files, 3,797 lines,
191,048 bytes to two files, 835 lines, 35,005 bytes. The entire packaged backport directory shrank
from 102 files, 5,029 lines, 242,186 bytes to five files, 2,067 lines, 86,143 bytes. Reviewed sets
remain tracked as test references; the source repository does not lose those files.

## VM runs and end state

| VM | Full E2E runs | Preflight | Result directory | End state |
| --- | ---: | --- | --- | --- |
| w24g-ombre, 9.2.4 | 0 | One session; three runtime questions pass, recovery gap found | e2e/results/w24g-ombre-minimal-preflight-20261024/ | Stock record restored, no QML mounts, test apps removed; original developer-app list contains only hbchannel; Off |
| w22h-ombre, 9.2.1 | 0 | Not started | None | Untouched; Off |
| w21a-kisscurl, 6.5.2 | 0 | Not started | None | Untouched; Off |
| w22p-mullet, 7.6.0 | 0 | Not started | None | Untouched; Off |
| w22o-number1, 8.3.0 | 0 | Not started | None | Untouched; Off |

The final Pi inventory reports no running VMs. Only `ssh raspberrypi` was used for the host;
VM operations used ares devices by name. Keys were sent through the runner. The visually identified
`[crash] Fault Occured` popup was dismissed under the explicit exception. No other LG popup was
confirmed, and `/var/palm/jail` was not removed.

## Other failures

* **Firmware/native application crashes, provisional classification:** manual compositor-probe
  restarts produced crashd entries for `com.webos.app.i.2686` (uptime 98.141) and
  `com.webos.app.h.5843` (454.146). The screenshot of the first fault identifies Qt font cleanup in
  libQt5Gui 5.12.12. These are outside boot noise and do not satisfy the requested clean-crash
  criterion. Their root cause was not established by a stock control. No WebAppMgr crash was
  observed in the collected evidence.
* **Runner/probe setup:** an initial page-read wait accepted an empty initializing DOM, and a later
  scratch probe lacked `wait_popup`. Both probe setup errors were corrected before the successful
  member/id proof. A crash popup also held focus during an initial Home attempt; it was identified
  from the screenshot, dismissed under the exception, and the proof repeated with the runner's
  key-focus check. No full E2E runner failure is claimed because no full run was started.

## Follow-up: functional readiness and real-worker rollback

The recovery decision was implemented after the initial stop. The boot worker now polls the stock
QML-served `com.webos.surfacemanager/getCurrentState` method using root identity
`com.webos.applicationManager`. Both `returnValue:true` and a string `currentState` are required.
The window is 24 seconds of monotonic uptime; individual requests are limited to two seconds.
This method exists in the stock QML source on all 19 supported corpus roots. The tier-2 edit only
changes the home comparison; it does not add or register this method.

The 9.2.4 test used `HOMECHOOSER_DEBUG_BREAK_SETTINGS=1` on the real worker. This diagnostic-only
environment variable appends invalid QML to its staged Settings.qml after generation, before the
normal bind mounts. The app never sets it. With main.qml unable to load, the QML method did not
answer even though systemd restart succeeded. The worker detected the missing response, removed
all seven mounts, restarted stock exactly once, and obtained a successful QML response on its first
stock attempt. It retained the enable flag and reported:

```
mounted=no
recovered=stock
reason=QML getCurrentState unavailable after 24 s: Terminated
reboot=needed
```

The restored Settings.qml matched its pre-test hash. The stock API answered
`{"currentState":"launcher","returnValue":true}`. The runner then proved Home opened LG Home:
applicationManager life events contained `launch` with reason `hotKey` and `foreground` for
`com.webos.app.home`, and getForegroundAppInfo named that id. The final worker uses monotonic
uptime so a boot-time wall-clock adjustment cannot extend the readiness wait.

Artifacts for this follow-up are under `scratch/recovery-probe/`. Native Qt input-app crashes at
the manual diagnostic restart are outside the full boot-run results. A Live TV probe also triggered
a legacy-broadcast crash on this VM; the successful Home proof used Homebrew Channel behind Home
Chooser's verified screen. Both visually identified crash popups were dismissed through the runner
under the explicit exception. No other LG popup was confirmed.

Offline follow-up checks passed: 202 worker checks under each shell, 521 matrix checks under each
shell and Node 8.12, 399 real roundtrip checks per shell, 551 watcher checks per shell, 50 boot-entry
checks per shell, and the access suite under both shells. App lint and all 213 Jest tests passed;
298 native service checks and 62 Python runner tests passed.

## Recents cleanup correction

The older 7.4/7.6 Recents implementation excludes `item.appId !== __lastInputApp` at its
cleanup kill site. Replacing only that property's NMRM default with the chosen home would
stop sparing LG Home on NMRM firmware. The prototype's claim that this spared both homes
was therefore incomplete for those builds.

The rewriter now retains the stock last-input property and exclusion, adding explicit guards
for both LG Home and the chosen home at the kill site. A missing or ambiguous last-input
exclusion skips the complete Recents file. Tests execute the actual firmware kill predicates
with both home ids, ordinary apps, last-input apps and homeconnect, checking both app-existence
states and stock/chosen configurations. The 541 checks pass under sh, BusyBox sh and Node 8.12;
all 16 Home-key reviewed comparisons still match. On 9.2.4 all seven staged files are byte-identical
before and after this correction.

## Retiring the per-release references

Before deletion, the final oracle run recorded **16 matching reviewed-set comparisons and
2,736 Home-key scenarios** across 19 real supported firmware roots. The 541 offline checks
passed under sh and BusyBox sh, and under Node 8.12 with each shell. The same corpus and
stock-versus-rewriter harness remain; per-release manifests and diffs are no longer needed.
The user approved their deletion before the remaining VM lanes completed.

## Final core and best-effort contract

The later user decision makes only Settings.qml and StarfishKeyFilter atomic. Their imports,
helper block, launch/checkLaunch redirects and exposed getter must match together. systemUi.js
and the four supporting files are independent best-effort rewrites; missing or mismatched files
are left stock and reported. The implementation is named `homechooser-keyfilter-rewrite.js`.
Runtime firmware selection and content-key status caching are gone; the only version gate is
webOS major 6–9. The manifest contains staged paths only.

The retained harness now executes stock, core-only and complete-hook variants. Core-only plans
contain exactly the two dependent files and report all five optional files as skipped. They
match stock foreground comparisons with stock-home launches redirected. On the 16 corpus builds
that use HOME_APP_ID comparisons, 48 of 144 scenarios degrade: Home over the chosen launcher
relaunches it instead of handing it the key, while LG Home in front can retain Home through its
stock comparison. The three older comparison-free scripts have zero degraded scenarios.
Broken systemUi anchors produce partial plans; missing or ambiguous core anchors refuse all output.

After this decision, all 19 normal stock plans remain byte-identical under Node 8.12 to the
implementation used by the completed VM runs. All six retained suites pass under sh and BusyBox
sh, and under Node 8.12 with each shell: minimal-hook 606 checks (19 real supported roots, one
synthetic skeleton, two negative roots and 2,736 scenarios), worker 199, watcher 547, boot entry 50,
real roundtrip 399, and the access suite. There are no failed or skipped corpus checks.
Clean npm install, lint, 213 Jest tests in eight suites, 297 native service checks, 64 Python runner
tests and packaging pass. The ipk contains exactly five current runtime scripts.

Completed full runs after readiness was fixed:

| VM | Full attempts | Successful result | Artifacts |
| --- | ---: | --- | --- |
| w24g-ombre, 9.2.4 | 2 | 154 PASS, one MANUAL-only API alert confirmation; no FAIL | e2e/results/w24g-ombre-20261024-210929/ |
| w22h-ombre, 9.2.1 | 1 | 106 PASS, no FAIL | e2e/results/w22h-ombre-20261024-212139/ |
| w21a-kisscurl, 6.5.2 | 2 (one interrupted for a runner fix) | 106 PASS, no FAIL | e2e/results/w21a-kisscurl-20261024-214245/ |

Each completed VM was returned to stock with test apps removed, leaving hbchannel as its original
developer-app list, and stopped. Screenshots show the green web fixture on 9.2.4 and Dock over
Homebrew Channel on 9.2.1 and 6.5.2; stock Home launch proofs pass after removal and uninstall.

The first 9.2.4 full attempt recorded 150 PASS and three runner failures: the service alert log
rotated before an active-file-only reader saw it, although the screenshot showed the correct prompt.
The corrected rerun passed. The first 6.5.2 attempt exposed a second runner portability defect:
BusyBox 1.29.3 has no `sort -V`. It was interrupted through cleanup (56 provisional PASS), the
reader changed to numeric suffix sorting, and the complete rerun passed. Artifacts are
`e2e/results/w24g-ombre-20261024-205556/` and
`e2e/results/w21a-kisscurl-20261024-213346/`. No product assertion failed in these attempts.
The 6.5.2 firmware accepted Power Off but did not exit QEMU within 90 seconds; Beanjar synced
the disks and stopped it on each shutdown. No crash was observed during these full runs.
