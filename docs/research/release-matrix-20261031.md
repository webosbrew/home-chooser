# Release matrix record: 2026-10-31

Scope: the end-to-end release validation of Home Chooser 0.1.0 on the
webOS 5.x–12.x emulated matrix, run on 2026-10-31. This is a record of what
was tested and measured. All numbers are taken from the `summary.md`,
`summary.json`, per-VM `results.json` and `run.log` files of the matrices
named below. Matrix directories are given as `matrix-20261031-HHMMSS`; those
under `release-suite` and `release-suite-2` (the two worktrees that ran
them) are kept, untracked, in `e2e/results/release-suite/` and
`e2e/results/release-suite-2/` of the main checkout.

## Summary

The release build, ipk sha256
`dd4d172f94f3ea94967f2a6382ffc821a46e14cb844456ff7eaeac869865cce0` (built
from main `bee3d8a`), passed every row of the final run: 0 FAIL on all 7
matrices and 23 VM rows (the checks of each row are counted in the tables
below). Pass 1 (lifecycle, update-check, API check, reboot check) gave 1
MANUAL per row, the confirm press on the system alert, which the runner never
presses; pass 2 (web fixtures, API check, alert uninstall) covers that path
with an automatic press and has no MANUAL. 5.x, which has no screensaver
endpoint, was covered by an all-fixtures run of 370 checks. Earlier passes
the same day used the previous ipk (sha256 `5dc0f8b7...1076`) and are cited as
background; their failures were environment or harness problems, listed in
the sections below, not product failures.

## The final release run

Build: `org.webosbrew.homechooser_0.1.0_arm.ipk`, sha256
`dd4d172f94f3ea94967f2a6382ffc821a46e14cb844456ff7eaeac869865cce0`, built from
main `bee3d8a`. The same file (identical sha256, checked with `sha256sum`) is
in the `ipk/` directory of all seven matrices below. The artefact was taken
from release-suite-2 `matrix-20261031-123455/ipk`.

The rockpi4a-1 VMs (5.x–7.x) were driven from `release-suite` and the other
hosts from `release-suite-2`. The run logs do not print the harness commit. The harness
commits are `bee3d8a` for release-suite-2 and `2fc0938` for release-suite, as
stated by the operator; `2fc0938` is `bee3d8a` plus `4fc45a4` (the
reopen-diagnostics commit, see the last section).

### Runner flags

Pass 1, lifecycle (default fixture `fixture:web.normal`):

```
--vm <vm> --host <host> --outdir <dir> --hc-ipk <ipk> \
  --update-from <ref> --old-ipk <old ipk> --update-check --api-check --reboot-check
```

Pass 2, web fixtures:

```
--vm <vm> --host <host> --outdir <dir> --hc-ipk <ipk> \
  --launchers fixture:web.normal,fixture:web.overlay --api-check --uninstall-via alert
```

Pass 3, 5.x all fixtures (`matrix-20261031-150329`):

```
--vm <vm> --host <host> --outdir <dir> --hc-ipk <ipk> --launchers all-fixtures
```

The update-check baseline (`--update-from`) was a build of the git ref named
in the run log: `244c36a26c12711027e2fd24002bde3c19dc8f30` for release-suite-2
(`matrix-20261031-123455` and `133949`) and
`056c91aec4033fcbb90ae01099281c6f91b10043` for release-suite
(`matrix-20261031-124749`). The old ipk is built per matrix and is not the same
file in each: sha256 `077bdbd1...a505` (123455), `206d95e8...f4d4` (133949)
and `77c91766...98f4` (124749). All three report version 0.1.0, as does the
current ipk, so the update-check exercises replacing one 0.1.0 build with
another.

### Pass 1: lifecycle, update-check, API check, reboot check

| Matrix | VM | webOS | Host | PASS | FAIL | MANUAL | Duration |
| --- | --- | --- | --- | --- | --- | --- | --- |
| release-suite-2 `123455` | `w22o-number1-rk2` | 8.x | rockpi4a-2 | 209 | 0 | 1 | 21m58s |
| release-suite-2 `123455` | `w22o-ponytail-us-rk2` | 10.x | rockpi4a-2 | 181 | 0 | 1 | 21m54s |
| release-suite-2 `123455` | `w22o-queue-us-rk2` | 11.x | rockpi4a-2 | 183 | 0 | 1 | 19m49s |
| release-suite-2 `123455` | `w22h-ombre` | 9.x | pi | 223 | 0 | 1 | 12m23s |
| release-suite-2 `123455` | `w24g-ombre` | 9.x | pi | 223 | 0 | 1 | 12m18s |
| release-suite-2 `123455` | `w24p-ombre` | 9.x | pi | 223 | 0 | 1 | 12m20s |
| release-suite-2 `123455` | `w24g-spacebuns` | 12.x | pi | 183 | 0 | 1 | 9m53s |
| release-suite-2 `133949` | `w22o-queue-us` | 11.x | pi | 183 | 0 | 1 | 10m46s |
| release-suite `124749` | `w20l-jhericurl-rk1` | 5.x | rockpi4a-1 | 170 | 0 | 1 | 15m12s |
| release-suite `124749` | `w21a-kisscurl-rk1` | 6.x | rockpi4a-1 | 209 | 0 | 1 | 22m03s |
| release-suite `124749` | `w22p-mullet-rk1` | 7.x | rockpi4a-1 | 209 | 0 | 1 | 21m23s |

`133949` repeats the 11.x row on the Pi (`w22o-queue-us`) in addition to the
rockpi4a-2 copy of the same release in `123455`.

### Pass 2: web fixtures, API check, alert uninstall

| Matrix | VM | webOS | Host | PASS | FAIL | MANUAL | Duration |
| --- | --- | --- | --- | --- | --- | --- | --- |
| release-suite-2 `135059` | `w22o-number1-rk2` | 8.x | rockpi4a-2 | 333 | 0 | 0 | 28m08s |
| release-suite-2 `135059` | `w22o-ponytail-us-rk2` | 10.x | rockpi4a-2 | 288 | 0 | 0 | 28m11s |
| release-suite-2 `135059` | `w22o-queue-us-rk2` | 11.x | rockpi4a-2 | 292 | 0 | 0 | 24m29s |
| release-suite-2 `135059` | `w22h-ombre` | 9.x | pi | 361 | 0 | 0 | 16m33s |
| release-suite-2 `135059` | `w24g-ombre` | 9.x | pi | 361 | 0 | 0 | 16m33s |
| release-suite-2 `135059` | `w24p-ombre` | 9.x | pi | 361 | 0 | 0 | 16m37s |
| release-suite-2 `135059` | `w24g-spacebuns` | 12.x | pi | 292 | 0 | 0 | 11m37s |
| release-suite-2 `151225` | `w22o-queue-us` | 11.x | pi | 292 | 0 | 0 | 12m44s |
| release-suite `134701` | `w20l-jhericurl-rk1` | 5.x | rockpi4a-1 | 272 | 0 | 0 | 20m37s |
| release-suite `134701` | `w21a-kisscurl-rk1` | 6.x | rockpi4a-1 | 333 | 0 | 0 | 28m19s |
| release-suite `134701` | `w22p-mullet-rk1` | 7.x | rockpi4a-1 | 333 | 0 | 0 | 27m17s |

### Pass 3: all fixtures on 5.x

| Matrix | VM | webOS | Host | PASS | FAIL | MANUAL | Duration |
| --- | --- | --- | --- | --- | --- | --- | --- |
| release-suite `150329` | `w20l-jhericurl-rk1` | 5.x | rockpi4a-1 | 370 | 0 | 0 | 48m17s |

The `summary.json` of every matrix above has an empty `problems` list.

## The one MANUAL per lifecycle row

Every pass 1 row carries exactly one MANUAL, with the same text in each run
log:

> uninstall confirm path on the alert route (Uninstall on the system alert ->
> LG remove): the runner never presses OK on a system popup; by hand: call
> uninstall as the launcher, press Uninstall, then check getAppInfo
> org.webosbrew.hcfixture.disposable fails

The lifecycle run asks for the confirmation alert and checks that it is
created, but it does not press the button on the LG system popup, so the
removal itself is left as a manual step. Pass 2 runs with `--uninstall-via
alert`, which does press the button, and covers it. In the `w22h-ombre` log of
`135059` the relevant checks pass in order: the uninstall request replies
pending with a confirm route, `closeAlert` on the Home Chooser "Uninstall ...?"
alert is accepted, the later request is confirmed, and "alert Uninstall press
(automatic press) removed only org.webosbrew.hcfixture.disposable". Every
pass 2 row has 0 MANUAL.

## Earlier passes in the same campaign

These ran on the previous ipk, sha256
`5dc0f8b7d022771b65b4079717c23943b3250e15fbfaa50b6e6c066dbcfc1076`, the file
in release-suite `matrix-20261031-003608/ipk`. The same sha256 is in the
`ipk/` directory of `033402`, `061011`, `084337` and `014333`, and of
release-suite-2 `113827`. It differs from the release ipk. Main `bee3d8a` has
both `244c36a` (reword the subtitle and show the footer only when Home is not
working) and `056c91a` (show the boot toast only when Home is really
redirected) as ancestors, confirmed with `git log` and `git merge-base
--is-ancestor`. The results files do not record the source commit of the
previous ipk, so the record only states that it is a different build.

### All fixtures on the previous ipk

All three used `--launchers all-fixtures` (native, qml and web fixtures, normal
and overlay).

| Matrix | VM | webOS | Host | Result | Duration |
| --- | --- | --- | --- | --- | --- |
| release-suite `033402` | `w21a-kisscurl-rk1` | 6.x | rockpi4a-1 | PASS 537 | 70m23s |
| release-suite `033402` | `w22p-mullet-rk1` | 7.x | rockpi4a-1 | PASS 537 | 68m06s |
| release-suite `033402` | `w24g-ombre` | 9.x | pi | PASS 537 | 38m54s |
| release-suite `033402` | `w24g-spacebuns` | 12.x | pi | PASS 292, XFAIL 2 | 19m43s |
| release-suite `084337` | `w22o-number1-rk2` | 8.x | rockpi4a-2 | PASS 537 | 69m31s |
| release-suite `084337` | `w22o-ponytail-us-rk2` | 10.x | rockpi4a-2 | PASS 418 | 69m46s |
| release-suite `084337` | `w22o-queue-us-rk2` | 11.x | rockpi4a-2 | PASS 424 | 59m22s |

The other rows of `033402` (5.x, and the three rockpi4a-2 VMs) and of
`061011` failed for the environment reasons given below, not on a product
check; `084337` is the clean rockpi4a-2 rerun.

### The 12.x qml XFAIL

On `w24g-spacebuns` (12.0.0) the two qml fixtures (`qml.normal` and
`qml.overlay`) are expected failures, `XFAIL qml fixtures run on 12.x`, in
`033402`. The evidence recorded in the run log is the 12.0.0 rootfs listing
(`scratch/triage-10-12/img/list.txt`, 2026-10-24): no `/usr/bin/qml-runner`, no
Qt libraries, no Eos/Window qmldir or plugin, surface-manager is weston (no
Qt), `booster.service` dangling (no `boosterd`), and 0 shipped `type:qml` apps;
only the sam-conf `QmlRunnerPath` and the sam `QmlAppLifeHandler` remain. The
device run `w24g-spacebuns-20261024-124444` showed a qml launch giving sam
`launch`, `splash` and `stop` events and never `APP_LAUNCHED`. The native and
web fixtures on 12.x pass.

### Screensaver scenario

release-suite `matrix-20261031-014333`, flags `--api-check --scenario
screensaver --reboot-check`, previous ipk:

| VM | webOS | Host | Result | Checks | Duration |
| --- | --- | --- | --- | --- | --- |
| `w20l-jhericurl-rk1` | 5.x | rockpi4a-1 | FAIL | FAIL 1, PASS 48, SKIPPED 1 | 5m26s |
| `w21a-kisscurl-rk1` | 6.x | rockpi4a-1 | PASS | BLOCKED 5, PASS 79 | 11m50s |
| `w22p-mullet-rk1` | 7.x | rockpi4a-1 | PASS | PASS 98 | 17m40s |
| `w24g-ombre` | 9.x | pi | FAIL | BLOCKED 4, FAIL 1, PASS 88 | 6m46s |
| `w24p-ombre` | 9.x | pi | PASS | PASS 107 | 9m59s |
| `w22h-ombre` | 9.x | pi | PASS | PASS 107 | 9m41s |
| `w24g-spacebuns` | 12.x | pi | PASS | BLOCKED 5, PASS 70 | 4m55s |
| `w22o-queue-us` | 11.x | pi | PASS | PASS 86 | 8m23s |
| `w22o-number1-rk2` | 8.x | rockpi4a-2 | PASS | PASS 98 | 17m58s |
| `w22o-ponytail-us-rk2` | 10.x | rockpi4a-2 | PASS | PASS 87 | 17m45s |

On 6.x and 12.x the first screensaver step (S0) is BLOCKED by beanjar#56
("Screen Saver Ready") and the four later steps (S1–S4) are BLOCKED on that
prerequisite. On 5.x S0 is SKIPPED: "No evidenced screensaver endpoint for
webOS <6". The remaining 5.x FAIL is the pointer-click problem below. On 7.x,
8.x, 10.x, 11.x and the 9.x VMs `w24p-ombre` and `w22h-ombre` the scenario
passes with no BLOCKED step. The 9.x FAIL of `w24g-ombre` is the 9.2.4 item
below.

## Environment findings that were not product failures

- **10.x audiooutputd crash dialog, beanjar#183.** In the web-fixture pass
  `073157` on `w22o-ponytail-us-rk2`, LG's "[crash] Fault Occured" dialog
  for `audiooutputd` (`RDXDErr3_audiooutputd__Linux Crash`) took key focus
  while Home Chooser's request dialog was open; the runner refused to press
  either button and stopped (3 FAIL). The text is only in the screenshot
  `04-fixture-web.overlay-keys-blocked.png`, not in the logs. It did not
  recur on that VM in `084337`, `123455` or `135059`.
- **9.2.4 screensaver request just after boot, beanjar#184.** In `014333` on `w24g-ombre`
  (os-release 9.2.4) the screensaver start request returned `returnValue:
  true`, but power and power2 stayed `Active` (`lifeOnScreenMode: none`) and
  tvpowerd logged `Screen On`, so the run reported "Screensaver did not reach
  the native shown state" at S0 and S1–S4 were BLOCKED on it. The tvpowerd
  lines are stamped about 32.8 s of uptime (`run.log:52-59`). The 9.x VMs
  `w24p-ombre` and `w22h-ombre` passed the same scenario in that matrix.
- **5.x pointer clicks at (0,0), fixed in beanjar#181.** The 5.x VM failed
  `click effect not observed: launcher row` in `033402` (FAIL 6, PASS 78) and
  in `014333`. With the fix the 5.x rows pass: `124749`, `134701` and `150329`.
- **rk2 admission refusals, fixed by the 896 MiB headroom (beanjar d15a570).**
  `033402` and `061011` on rockpi4a-2 aborted at runner setup, "VM ... has no
  LAN address", because beanjar refused to start the VM: "not enough free
  memory to start VM w22o-number1-rk2: it needs 2560 MiB and 1024 MiB of
  headroom, and the host has 3565 MiB available" (3575 MiB for
  `w22o-ponytail-us-rk2`). After the headroom change all rockpi4a-2 rows
  start: `084337`, `123455` and `135059`. The 896 MiB figure and the commit
  are not printed in the result files; the 1024 MiB headroom in the refusal
  text is.
- **One-off 5.x relaunch miss, cause undetermined.** release-suite-2
  `113827` (`w20l-jhericurl-rk1`, `--launchers all-fixtures`, previous ipk):
  FAIL 1, PASS 369 in 54m13s. The failure is "re-open after the apply: the
  chosen row is ticked", with "ticked rows: None" and a DOM of `{"toast":
  null}` at 12:20:32, during the first fixture (`fixture:web.normal`). It did
  not recur: the same fixture set passes 370 checks on the release ipk in
  `150329`. The check had no capture of the foreground or running state at the
  time (the diagnostics in `4fc45a4` came afterwards), so the cause is not
  established.

## Harness fixes made during the campaign

- `4e1621b` Harden E2E toast, alert-band and foreground checks; fix README.
  The "already your Home" toast is read from the DOM or the screen, with a
  dead Inspector forward recreated, and the source is recorded. Only buttons
  overlapping the alert dialog rectangle count as extra alert controls. The
  screenshot and UI are logged before "click effect not observed". For overlay
  launchers `getForegroundApp` is probed through the OVERLAY life event and
  SAM's reply. `e2e/README.md` is updated for the live keyFilters design (no
  mounts, no restart prompt, daemon md5).
- `fba1227` e2e: make the warm-relaunch toast check robust. The Inspector is
  replaced before the selected request launch and recreated on every
  connection failure inside the deadline, the OCR read is capped and
  interleaved, and the per-round timeline is in the miss evidence.
- `4fc45a4` e2e: make the "re-open after the apply" check diagnosable. The rc
  and output of both `ares-launch` calls are recorded, the Inspector is
  recreated on connection-type read failures (within the 120 s), and a miss
  records the SAM foreground app, SAM running state, Inspector `last_error`
  and failed-read count. It prints "no DOM read" instead of a fabricated DOM.
  Pass criteria are unchanged. It is the head of main at `2fc0938`.
