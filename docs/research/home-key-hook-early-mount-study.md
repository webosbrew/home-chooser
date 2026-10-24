# Home key hook: first-load timing study

This is a study, not a boot-model change. Measurements are from the W24G 9.2.4 Beanjar VM on
2026-10-25; no LG file or persistent unit was changed. A temporary Homebrew Channel init.d
timestamp probe was removed after one reboot.

## Crash control

The same `legacy-broadcast-dvb` fault at `API_SI_GetLinkageUriInfo+0x197` occurred with the
boot compositor restart on main and this branch. In standardized plain-reboot batches:

| Variant | Boots | DVB crashes | Local logs |
| --- | ---: | ---: | --- |
| main `b472a38` | 10 | 1 | `build/test-tmp/boot-count-main.log`, `boot-count-main-late.log` |
| branch, normal preload | 10 | 1 | `boot-count-branch-late.log`, `boot-count-normal-probe.log` |
| branch, preload disabled temporarily | 10 | 0 | `boot-count-no-preload.log`, `boot-count-no-preload-probe.log` |
| stock-only, no Home Chooser hook | 10 | 0 | `boot-count-stock-late.log` |

Some batches made early status probes and others only checked after uptime 65 s, so these ratios
do not isolate preload or quantify a reliable crash rate. A full main E2E run also reproduced the
fault (`e2e/results/w24g-ombre-20261025-040724/run.log`, 153 PASS, 1 MANUAL, 1 FAIL), as did a
full branch run (`e2e/results/w24g-ombre-20261025-035224/run.log`, 175 PASS, 1 MANUAL, 1 FAIL).
The strict E2E failure assertion remains.
The stock-only VM still loaded Beanjar's Homebrew Channel layer; its init.d directory had no
Home Chooser entry.

## First-load order

On the measured W24G stock boot, `surface-manager-daemon.service` began at **6.236 s** and
reported ready at **9.886 s**. `mount-readwrite.service` finished at **7.271 s**;
`emulator-hbchannel.service` began at **8.326 s** and finished at **21.120 s**. Its installed
`startup.sh` sleeps two seconds before calling `run-parts /var/lib/webosbrew/init.d`.

The one-boot init.d probe recorded **10.60 s**. On that boot the compositor began at **6.308 s**,
reported ready at **10.129 s**, `/var` and `/media` finished mounting at **7.396 s**, and
Homebrew startup began at **8.400 s**. Thus the normal init.d entry ran after the compositor
process started. Systemd readiness does not establish when the root QML finished loading: in
comparable retained E2E boots, the compositor logged “Using main QML” at **7.82–7.87 s** and
“Loaded main QML” at **10.76–11.06 s** (`e2e/results/w24g-ombre-20261025-035224/run.log` and
`w24g-ombre-20261025-040724/run.log`). The 10.60 s probe can therefore fall *during* the
first QML load. The probe and `/tmp` output were deleted. The VM was powered off.

Saved 7.6.0 W22P logs provide a cheaper corroborating check without starting that VM: the
compositor began at **5.031 s**, Homebrew install began at **6.562 s**, and systemd marked
Homebrew startup active at **27.744 s**. They do not record the exact init.d call. The saved 6.5.2 W21A log
shows compositor ready at **8.443 s**, but lacks a usable Homebrew startup marker; it gives no
ordering conclusion.

The VM's Homebrew service starts after its install service, which starts after
`mount-readwrite.service` and is ordered only before `sam.service`. The compositor starts through
`surface-manager.service` after `ls-hubd.service`; it does not wait for Homebrew. The installed
[Homebrew Channel startup script](https://github.com/webosbrew/webos-homebrew-channel/blob/main/services/startup.sh)
confirms that init.d is called late in its startup work. These are VM measurements; startup
ordering on a physical TV would need its own trace before treating the times as universal.

## Options

| Option | Feasibility from current evidence |
| --- | --- |
| Homebrew Channel init.d as it runs today | Cannot mount before first compositor *process start* on measured 9.2.4: its 10.60 s call followed the 6.308 s start. It may race the first QML load. |
| Start Homebrew earlier | Would need a boot-chain ordering change outside Home Chooser's init.d interface, plus access to the cache after `/var` and `/media` mount. No supported app-level ordering point was found before the compositor's 6.3 s start. |
| Transient `systemd-run` service with `Before=surface-manager-daemon.service` from init.d | A transient service can be created, but at 10.60 s the compositor has already started; `Before=` cannot reverse completed startup. A transient unit created in one boot does not persist into the next. This cannot solve first load from the current entry point. |
| Wait for compositor, bind before its first Home frame | Home surface mapping was logged around 15.7 s in a prior W24G boot, but QML loading began around 7.8 s and finished around 11 s in comparable boots. Binding at 10.60 s can race parsing and cannot reliably replace already loaded objects. It would need a verified synchronization or live QML reload path. |
| Restart only the QML layer | No such exposed method was found in the installed surface-manager Luna API permissions, QML service source, or compositor executable strings. `LocalSettings.reloadAllFiles()` only rereads JSON settings. This is an absence of a verified interface, not proof one cannot exist. |

[systemd-run](https://www.freedesktop.org/software/systemd/man/253/systemd-run.html) creates
transient units; [systemd.unit](https://www.freedesktop.org/software/systemd/man/253/systemd.unit.html)
defines `Before=` as start-job ordering. Neither provides a way for an init.d job that begins
after compositor startup to precede that completed start.
