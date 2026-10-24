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
