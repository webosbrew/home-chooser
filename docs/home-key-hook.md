# Live Home key filter, webOS 6–9 and 4.0

On webOS 6 to 9 the stock Home key handler launches `com.webos.app.home` by its fixed id and ignores
the default-app record ([study](research/home-key-and-home-app.md#660-to-921)). Home Chooser copies
**this device's** stock `KeyFilters/systemUi.js`, changes the Home launch target, and selects the
copy through configd. No compositor, SAM, WAM or input-method restart is required, including at
boot. No QML source is changed or bind mounted. webOS 4.0 has no `launchHomeApp` to change and uses a
filter entry of its own instead ([webOS 4.0](#webos-40-a-filter-entry-of-our-own)). webOS 4.5, 5 and 10–12 use the settings record and
system profile ([What is written](settings-app.md#what-is-written)).

The native implementation is [homekey.c](../service/src/homekey.c), with the guarded generator in
[homekey-generator.c](../service/src/homekey-generator.c). Both are compiled into the shared static archive and linked into
`homechooser-daemon`. `scripts/homechooser-homekey` only dispatches its `--homekey` mode.

On 6–9 the [home gate](home-gate.md) also replaces the stock Home binary SAM starts, through a
bind mount, so stock Home does not stay resident while a launcher is chosen.

## Why a live configd reload

These firmware facts decide the design ([study](research/home-key-and-home-app.md#key-filter-loading-660-to-921)):

- The compositor loads each key filter script by path with `Qt.include`, from the
  `com.webos.surfacemanager.keyFilters` config, and there is no QML disk cache. It re-reads the
  list when that config value changes: configd's change notification destroys and recreates the
  key-filter loaders, and a new JS file then runs without any QML edit.
- Homebrew Channel runs its init.d entries after the compositor has started, so a boot-time change
  has to be picked up live. Restarting the compositor to reload files was tried and dropped: it
  disturbs running clients and set off LG process crashes.
- A `setConfigs` write without `volatile` persists into `/var/preferences/configd_factory_db.json`
  as a user override with `usrDefined` bookkeeping. A `volatile:true` write is held in memory only,
  and a reboot returns the firmware's own list.
- Compositor, SAM, WAM and input-method processes keep their identities across a live reload.

## Boot and watcher lifetime

On 6–9, `/var/lib/webosbrew/init.d/homechooser` links to the installed `scripts/homechooser`.
The HBC run-parts entry enqueues one `systemd-run --no-block --unit=homechooser-home` transient unit
and returns immediately. It contains no readiness sleeps. The unit runs `homechooser-daemon --boot`; this dispatch happens **before** the normal RPC service name is registered.

Inside the unit, bounded calls wait for both `surfacemanager/getCurrentState` (a successful reply with
`currentState`) and configd's valid, nonempty keyFilters list. There are at most 30 attempts, with
one second between attempts and a timeout on every call. Once a launcher redirect is certain (flag present, valid choice, generation succeeded, app installed), a
best-effort `createToast` says “Your new home will start shortly”, with `sourceId: org.webosbrew.homechooser`, `noaction: true` and
the privileged `com.webos.applicationManager` identity. Toast failure does not block apply.

After the attempt, the daemon runs the native watcher in `service/src/watcher/`
([service.md](service.md#native-source-layout), [Persistence](settings-app.md#persistence)).
The daemon's running image remains mapped after package removal, so it can still restore stock
after an uninstall; restoration calls `homekey.c` directly. The access helper/common file and
`homechooser-daemon` are staged in `/tmp/homechooser-runtime` for crash/restart recovery. On 6–9
recovery is volatile and needs no persistent copy: a reboot resets the filter anyway.

## Guarded JS generation

The stock file is `/usr/lib/qt5/qml/KeyFilters/systemUi.js`. The generator recognizes the complete
`launchHomeApp(key)` function shapes observed in the firmware corpus using SHA256 fingerprints
([homekey-shapes.inc](../service/src/homekey-shapes.inc)); no vendor JS is shipped. Its edits are:

- Change `HOME_APP_ID` when the stock file defines it.
- Change only the stock-home first argument to `applicationManager.launch` and, where present,
  `applicationManager.checkLaunch` inside `launchHomeApp`.

The 7.1/7.4 variants without `HOME_APP_ID`, the older launch-only form and the NMRM/checkLaunch form
are covered. Stock access restrictions, disableHome/first-use gates, branch decisions, parameters,
timers, subscriptions and all other key handling remain intact. An unknown function or constant
shape is refused. Boot retains/restores stock and posts a best-effort notice; interactive preparation
fails before the hook is enabled. A missing or invalid choice, or an app SAM cannot confirm installed,
selects stock behavior. With the filter stock for any such reason (`active=no`) the home gate is not
mounted either, so Home opens LG Home; the redirect, filter and gate, returns on the next refresh or boot
once the shape is known again. On webOS 10 and later the guard removes the generated `systemUi-*.js` and
`keyfilters-stock.json` that an update from 6-9 left in the state directory.

Each changed version receives a fresh `systemUi-XXXXXX.js` filename under
`/var/lib/webosbrew/homechooser/`. The service reads it back and compares SHA256 before selection.
An unchanged choice and identical generated bytes avoid unnecessary loader recreation.

Only `launchHomeApp` is edited. Long-press, voice, multiview and other sites that compare against
the stock id keep their firmware behaviour.

## Configd transaction and restoration

The service reads `com.webos.surfacemanager.keyFilters` from `getConfigs`. It saves the entire exact
stock array in `keyfilters-stock.json`, including entry order and every field. It replaces only the
`file` fields of existing systemUi entries. It accepts either firmware spelling, `/usr/lib/qml/…`
or `/usr/lib/qt5/qml/…`, without assuming a fixed handler list.

Every write sends `setConfigs` with `configs.com.webos.surfacemanager.keyFilters` set to that
validated nonempty array of original objects, and the top-level `volatile` set to `true`. A successful write must be followed by an exactly equal `getConfigs` readback.
Only then are unused generated versions deleted. A rejected/ambiguous activation attempts an exact
stock-list restore, also volatile and verified. If recovery cannot be verified, all files are retained.
Unexpected external edits to the active list cause refusal instead of overwriting them.

Changing launchers invokes the same transaction immediately. Disable, missing-launcher recovery
and uninstall restore the exact saved stock array **before** removing generated files. Restoration
uses an exact stock-list volatile write, rather than relying on unverified `null`-override semantics.
There are no non-volatile writes. After a verified restore with no choice, `keyfilters-stock.json`
and `keyfilter.lock` are removed, and the state directory with them when nothing else is in it.

If the package and its watcher are both absent, the volatile filter still references an existing
file outside the package; reboot resets it to firmware defaults without a recovery executable.
An active watcher handles normal uninstall immediately.

## Configd puts the stock list back

A volatile `setConfigs` value lives in configd's memory next to its layered configuration. When configd
re-selects its layers (log line `Broadcast Profile default ==> tunerless_exception: selection is changed`),
it can insert the firmware's own keyFilters list again and the volatile entry is gone. Seen once, on a W22O
VM's first boot after a simulated 7.4.0 to 8.3.0 firmware update: the watcher had applied the filter about 25 s
after boot, configd re-selected about 6 s later, and the live list was stock for the rest of the boot
(`homechooser-homekey status` said `active=no`, the settings app said the Home button was not set up).
The mounted home gate still opened the launcher. Nothing in the 218 "active=yes after the boot" checks of
the saved matrix runs (55 runs, 6-9 VMs) shows it on an ordinary boot, but those runs keep no guest
`/var/log/messages`, so whether an ordinary boot never re-selects is not established.

Before this, the watcher learned of the live list only when it applied it: at boot, on a settings or
app-list event that left `baked` unset or different, and never again once `baked` named the choice.

The watcher now subscribes to `getConfigs` for the keyFilters config (`subscribe:true`, configd's schema
accepts it; the compositor's own reload relies on the same change notification) on 6-9, and every reply
goes to `key_follow` ([restore.c](../service/src/watcher/restore.c)). The 300 s ticker polls the same
config as a fallback for a lost subscription or a change configd does not announce. `key_follow` acts only when
all of these hold:

- the filter is believed baked (`baked` is set: the last refresh made `active=yes`), and the stored choice is still that app;
- the reply is a list with a stock `systemUi.js` entry and none of ours (an error, an empty or foreign
  list, or our own file is left alone, so the echo of our own write does nothing);
- fewer than 5 re-applies in the last 10 minutes and the last was 20 s or more ago. A configd that keeps
  re-inserting gets one log line and is left alone until the quiet period or the next boot.

It then runs the ordinary `refresh`, which is the same volatile, read-back-verified transaction as at boot (stock
list saved again, a new `systemUi-*.js`), and re-syncs the gate. A stock list that is stock on purpose is never
touched: a failed or refused refresh, a disabled hook, an unknown stock shape, a missing app or no choice all
leave `baked` unset (or the choice different), so the stock list stays and the gate stays unmounted. No service
is restarted, nothing is written to configd's factory database, and webOS 5 and 10+ do not subscribe.

## Another app saved keyFilters persistently

Other launchers (Luma, Glasshouse) write `com.webos.surfacemanager.keyFilters` to configd **persistently** (a
`setConfigs` without `volatile`). Home Chooser writes it volatile. Reproduced on a 6.5.2 VM: while a persistent value
exists, a volatile `setConfigs` returns `returnValue:true` but `getConfigs` keeps returning the persistent list, so
the exact read-back after the write fails. configd has no delete method; a persistent `setConfigs` of
`{"configs":{"<key>":null}}` makes `getConfigs` return the layered base list again (once only after a following
`reconfigure {}`); `reconfigure` alone and a volatile null do not clear it.

**Detection** (every key-filter mode: 6-9 patch and the 4.0 inserted entry). When the volatile write is accepted, the
read-back differs from what was written and equals the list from before the write, the failure is classified as a
persistent override. Recovery is as for any failed write (the saved stock list is written back and read back), the apply
output says `reason=persistent-override`, and the toast "cannot change Home on this firmware" is not shown. Every failed
write logs the `setConfigs` reply, the entry counts and the first differing index (syslog id `HOMEKEY_CONFIGD`).
The service writes the state directory file `override` when it meets this case (at boot or apply, or when the watcher sees
it); `status` prints `override=yes` while it exists and the filter is not active. Any apply that ends with the filter
active or stock restored removes it.

**Clearing is never automatic.** The settings app asks (the other app's change is removed, the list goes back to LG's
own) and repeats the apply as `homekeyApplyClear` (`homechooser-homekey apply clear-override`). Then, for an apply
with a stored choice that has none of our entries: a persistent null for the key; `getConfigs`; when the list has not
changed, `reconfigure {}` and a read again (three reads, `retry_ms` apart); the stock snapshot is taken again from the
result (the old one may hold the other app's entry); then the usual volatile apply. Each step is logged. An unchanged
valid list is accepted after the retries (a persistent list equal to stock cannot be told from stock by reading), so
the volatile write is the proof. Reasons: `override-clear-failed` (the null was refused or no valid list came back;
nothing else written) and `override-not-cleared` (cleared, and the write is still masked; stock restored).
Restore, stop, clear, refresh with stock and uninstall never write persistently, and the flag is ignored for them.
The consent is not remembered: each clearing is asked for.

**Boot and runtime.** The boot apply and the watcher never clear. At boot a persistent override recovers as above and
shows the toast "Another app has taken over the Home button. Open Home Chooser to change it." While the filter is
believed baked, the watcher's keyFilters subscription (the `K` events above) shows the same toast once when a list with
none of ours and no stock `systemUi.js` entry arrives, or when its stock re-apply ends with `persistent-override`;
it does not re-apply in a loop. The toast is due again only after our own list was back in between. Opening the app
with `override=yes` and a stored choice asks the same question as a refused apply.

## webOS 4.0: a filter entry of our own

webOS 4.0 (starfish 4.4.x, W18R verified) has no Home app and no `defaultApps` record. The Home key is
handled in the compositor: the stock `KeyFilters/systemUi.js` calls `global.prepareToGoHome()` on press,
arms a 1000 ms `longPressTimer` for `global.gotoRecents`, and calls `global.goHome()` (LG's ribbon) on
release. There is no `launchHomeApp` to retarget, so none of the 6-9 shape fingerprints match and the stock
file stays untouched. Home Chooser inserts **its own entry** instead.

Evidence ([research notes](research/home-key-and-home-app.md) hold the 6-9 side): the compositor's
`WebOSKeyFilter` loads each file of `com.webos.surfacemanager.keyFilters` into its own `QQmlEngine` with
`QJSEngine::evaluate` (not `Qt.include`), calls the named handler for press and release, and reads the
result as 0 NotAccepted, 1 Accepted (consumed), anything else the next entry. A volatile `setConfigs`
reloads the list in about 0.4 s. Verified live on the emulated W18R (05.50.70.01, 4.4.3): the entry
consumed Home, the ribbon never appeared, the launcher opened, long press still opened the recents ribbon,
and a volatile restore of the saved list read back exactly equal.

**Gating.** The mode is not decided by a version number alone ([homekey.c](../service/src/homekey.c),
`hc_key_mode`): the release major is 4 **and** the stock `systemUi.js` has `handleSystemKeys`,
`prepareToGoHome` and `gotoRecents` but no `launchHomeApp`. webOS 4.5 (starfish 4.10.x) has
`launchHomeApp` and keeps the 5.x mechanism (the settings record), so it never takes this path: its
status is `patch=not-needed`, and a generated 4.0 file left by a 4.0 to 4.5 update is deleted at the
next apply. The app only shows the 4.0 setup on 4.x releases below 4.10; the service has the last word.
The release is read from `/etc/starfish-release` only.

**The generated file** (`homekey40-XXXXXX.js` in the state directory, a new name for each change, mode 0644,
SHA256 read back before selection) holds one handler, `handleHomeChooserKeys`, with the launcher id and
whether it is an overlay launcher (`defaultWindowType` from `getAppInfo`, the same classification as the
[home gate](home-gate.md)) baked in. The template is in [homekey-generator.c](../service/src/homekey-generator.c);
its behaviour is tested against a mock of the filter scope (`service/test/homekey40-filter.js`).

- **Home** (press and release are both consumed, as the stock handler does when the app does not take
  Home): press runs `prepareToGoHome()` and arms the same `longPressTimer`, so a long press still opens
  recents; a release with the timer still running hides the ribbon if it is showing, and, unless the launcher is already in
  front, closes overlays (`global.closeOverlaidItems`, as stock Home does) and launches the launcher with
  `applicationManager.launch(id, {"launchType":"homeKey","activateType":"home"})`. Under a modal alert, and during the
  first-use setup, the key goes on to the stock handler.
- **Back** (only while the launcher is the front surface and no alert, keyboard, pincode prompt, spinner, quick
  settings, voice agent or ribbon is up): when the launcher accepts Back itself (`surfaceAcceptsBackKey` is
  `"true"`) the platform handles it as usual. When it does not, stock Back would call `goHome()` and
  draw LG's ribbon over it, so the entry consumes it: a **full-screen** launcher swallows it (nothing
  happens); an **overlay** launcher goes to the last input (`settingsService.lastInputApp`, else Live TV), the
  same rule as for overlay launchers on 6-9. Back anywhere else is left alone.
- **Insert rule.** The entry goes immediately before the stock `handleSystemKeys` entry, found by **handler name**
  among the entries of the stock `systemUi.js`, never by index (device layers differ: one has no
  `handleMagicNumber`, another lists other handlers). Everything before it (screen saver, accessibility,
  magnifier, ...) still runs first. A list without that entry, or in the legacy `{ "<path>": "<handler>" }` form of
  the `commercial` layer, is refused without a write (stock stays); a refusal at boot posts the usual notice.

**Transaction, restore and watcher** are the 6-9 ones: the exact stock array is saved in
`keyfilters-stock.json`; every write is a volatile `setConfigs` followed by an exactly equal `getConfigs`; a
failed or ambiguous write restores the saved list; an external edit of the live list is not overwritten; a
choice change, disable, missing launcher and uninstall restore the exact saved list before any file is
deleted. The watcher subscribes to the list and applies it again after boot and whenever configd puts the
stock list back (rate limited, as on 6-9). Nothing on 4.0 touches the settings record or the profile, restarts
a process or writes configd's factory database, and there is no home gate (there is no Home binary to bind).

**No systemd on 4.0.** webOS 4.0 and 4.5 run upstart, so `systemd-run` and `systemctl` do not exist. With
neither present, `scripts/homechooser watch` starts the watcher as a detached shell loop in its own session
(the unit's `Restart=on-failure` as a loop with a five second pause) and records its pid in
`/tmp/homechooser-runtime/watcher.pid`; the same script answers `stop` and `active`, which the service's
`stopWatcher` and `watcherActive` commands now call (they ran `systemctl` before). The HBC init.d entry starts the
same loop at boot.

**Measured on the emulated W18R** (rockpi4a-1): after a restart the boot entry applied the filter 29.5 s
after the boot began (guest uptime) and configd showed the entry about 5 s later at the first poll; Home opened the
launcher afterwards. Choosing "LG Home (Ribbon)" and uninstalling Home Chooser each restored the 12-entry stock list,
read back exactly equal.

## Status and tests

`active=yes` means that configd selects a Home Chooser JS version; `active=no` means it does not.
`patch=possible/refused` reports the native generator check, `reboot=no` is unconditional, and `override=yes|no`
says whether a persistent keyFilters override was met (above).
The app has no restart prompt or “Restart now” row.

```sh
cmake -S service/test -B build/service-host -DKEYFILTER_ROOTFS=/path/to/7.6/rootfs
cmake --build build/service-host && ctest --test-dir build/service-host  # policy, homekey, watcher, gate
OPENHOUSE_STOCK_COPIES=/path/to/stock sh service/scripts/tests/test-minimal-hook.sh
sh service/scripts/tests/test-homechooser.sh
sh service/scripts/tests/test-homechooser-access.sh
npm --prefix app test -- --runInBand
python3 -m unittest discover -s e2e -p test_e2e_logic.py
```

The generator checks exact-byte edits, syntax, executed launch branches/gates and unknown-shape
refusal against `service/scripts/tests/corpus.tsv`. Missing firmware paths are explicitly reported.
`test-fwupdate` ([fwupdate-guard.py](../service/test/fwupdate-guard.py), [test-fwupdate.c](../service/test/test-fwupdate.c))
simulates a firmware update: two or more boots on one persistent state directory, with the stock
`systemUi.js` and `/etc/starfish-release` swapped and configd back at the firmware's own list in between. It uses real
adjacent releases from the corpus (6.3.1 to 9.2.4, plus files of other majors that must fall back to stock)
and synthetic updates (unknown shape, file removed, version string only, live swap). The guard keeps no
version, hash or mtime of the stock file: every run re-reads it, so the check is the shape fingerprint alone.
Missing corpus sources skip their pairs.

The native ARM transaction test runs under qemu with real firmware libraries and fake Luna:
readiness, failed toast, list metadata/order, live changes, missing apps, failed writes/readbacks,
verified restoration, retained files on recovery failure and empty-list refusal.

The E2E runner expects the first apply to be live and verifies Home after boot; see
[`e2e/README.md`](../e2e/README.md).

## Known LG crash on 9.2.4

On webOS 9.2.4, LG's `legacy-broadcast-dvb` process intermittently crashes at
`API_SI_GetLinkageUriInfo+0x197` early in a boot (about 1 boot in 10 on a VM), and LG shows a
"[crash] Fault Occured" popup. It was first seen with the compositor restart that earlier Home
Chooser builds made at boot, which the live reload replaced. It is LG's fault and an accepted
limitation: the E2E runner records exactly that crash (crashd's `legacy-broadcas.<pid>` line confirmed
by a faultmanager entry of the same pid at that location) as a note, not a failure, and dismisses its
popup with BACK at most once. Any other crash still fails the run.
