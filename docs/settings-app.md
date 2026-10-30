# The settings app

`app/` is an Enact (Moonstone) web app, `org.webosbrew.homechooser`. It lists the installed apps that
declare `appCategory: ["home"]`, shows the current choice, and writes the choice for the running
release. The platform facts behind every write are in the
[study](research/home-key-and-home-app.md); this page says which write is made where.

## Using it

One screen, built for the remote (5-way, OK, Back); every action is reachable without a pointer.
Home Chooser is in English only.

* **A side panel.** Like the stock Settings app (`com.palm.app.settings`: `"type": "web"`,
  `"defaultWindowType": "overlay"`, `"transparent": true`), Home Chooser is a transparent overlay
  window with an opaque panel, 800 px wide at 1080p, docked at the right edge and full height. The
  list, the dialogs, the spinner, the toast and the problem messages all draw inside the panel;
  the rest of the screen stays clear and shows what is underneath.

* **The list.** Title "Home Chooser", subtitle "Choose the app your Home button opens". The first row is "LG Home
  (stock)"; the installed launchers (`appCategory: ["home"]`) follow, each with its icon and name.
  A check mark sits on the current home and the focus starts on it. Every row has the same height,
  icon box (one fixed size, before and after the icon loads) and reserved check slot; focus only
  changes colour and long names are truncated. App ids are never shown. If no launcher is installed, a line explains
  what a launcher is and that LG Home stays in use. If the chosen launcher was uninstalled, a line
  under the list says "The Home app you chose was removed; using LG Home."
* **No launch animation.** `appinfo.json` sets `noSplashOnLaunch: true` and `spinnerOnLaunch: false`, as the stock Settings app and many stock apps do. The 6.6.0 web app manager knows no other appinfo key for launch animation (only `noSplash` and `disableBackHistoryAPI` appear in its strings).
* **No close button.** The panel header has no close button; Back is the way to close (a dialog first, then the panel). Focus never leaves the list: it returns to the row that opened a dialog when the dialog closes, and moves to the new current row when a change finishes.
* **Choosing.** OK on the row that is already the current home changes nothing and opens no dialog; focus stays on it and a toast says "<name> is already your Home." ("LG Home is already your Home." for the stock row). OK on any other row asks "Use <name> as Home?". Confirming blocks input behind a spinner
  while the change is made, then a toast says "Done. Press Home to try it." On failure the toast is
  one plain sentence ("Couldn't set the Home app. The Home Chooser service didn't respond."); the details
  are in the console log. Choosing "LG Home (stock)" runs the restore. The panel stays open after
  a success; Back closes it.
* **webOS 6 to 9.** Choosing a launcher validates and reloads a JS-only Home filter immediately.
  Changing launchers and restoring LG Home use the same configd volatile transaction. Nothing
  restarts the compositor or `sam`, including at boot; the panel stays open. There is no restart
  prompt or “Restart now” row. Unknown firmware shapes are refused. The old pending-result marker
  is only read/cleared for compatibility. See [Live Home key filter](home-key-hook.md).

* **Footer.** On webOS 6 to 9 only, one fixed line at the bottom of the panel (not focusable): "Home key hook: active", "Home key hook: not active", or "Home key hook: not available for this TV's software" when the structural hook refuses the firmware. Nothing on 5.x and 10.x or later, or when root is unavailable.
* **Problems (no root).** Homebrew Channel missing or not running, refusing `elevateService` (webOS 26 and later:
  "Your Homebrew Channel doesn't let apps elevate Home Chooser's service yet. Update Homebrew Channel; Home Chooser
  needs it."), or the service not coming up as root ("Home Chooser's service did not start as root"): the panel keeps its layout. A plain one-line reason is at the
  top of the list area and the list is disabled (Moonstone `disabled`: rows are dimmed, cannot be
  focused or clicked, no confirmation opens), with nothing focused; Back still closes the panel. Without
  root the app cannot tell which installed apps declare `appCategory: ["home"]` (`listApps` needs
  root; the public `listLaunchPoints` does not carry `appCategory`), so only the "LG Home (stock)"
  row is shown, with no check mark and no footer.
* **Back** (Escape, or keyCode 461: `appinfo.json` sets `disableBackHistoryAPI`, as Settings does, so the remote's Back arrives as a key event instead of history navigation) closes a dialog; with none open it closes the panel with `window.close()` (never
  `webOSSystem.hide()`, which would keep the key focus on a hidden window), returning focus to the app underneath.
* **Icons.** Another app's icon file is not readable by a developer web app, so each launcher's icon
  is read by the service (`readIcon`, at most 256 KB, once per session) and shown as a `data:` URL; a generic
  icon stands in when that fails.

## Root access

The app does not call the settings service, `sam` or the system profile under its own identity,
and it does not run shell commands. It calls Home Chooser's own native service,
`org.webosbrew.homechooser.service`, which runs as root and makes the privileged calls itself, so the
settings service whitelist and the bus permissions do not apply (see
[Permissions](research/home-key-and-home-app.md#permissions)). The service offers a fixed set of
narrow operations and answers only this app; see [the service](service.md) for the methods, the
allow-lists and the logs.

At startup the app makes sure the service is root (`[HomeChooser]` lines in the console log):
`status`; if the service is not root, Homebrew Channel's
`luna://org.webosbrew.hbchannel.service/elevateService {"id":"org.webosbrew.homechooser.service"}`
(which rewrites the service's launch line so it is no longer jailed and runs as root); `quit`, so
the next call starts the elevated binary; `status` again, retried a few times. Every step has a
timeout. The failures map to the problem screens: Homebrew Channel absent (`unreachable`),
`elevateService` refused with `Denied method call` (`denied`, webOS 26 and later with the current
Homebrew Channel), the service still not root after elevation (`notRoot`), anything else
(`failed`). Files are written by the service (`writeFile`, text in a JSON string), so their
content never meets a shell. The shell scripts (`service/scripts/homechooser`, the watcher, the Home key hook
worker) are unchanged; the service runs them with a fixed argv.

The release comes from `/etc/os-release`, read through the service (`VERSION_ID`, then `VERSION`, then `PRETTY_NAME`).

## What is written

| Release | Settings record `general/defaultApps` | System profile | After |
| --- | --- | --- | --- |
| 5.x | `setSystemSettings` with the existing entries and `home` | - | close previous custom home |
| 6.x to 8.x | same | - | close previous custom home; reload the volatile JS filter |
| 9.x | `applicationManager/setDefaultApp`; on `Unknown method`, the `setSystemSettings` write | - | close previous custom home |
| 10.x to 12.x | `setDefaultApp` as on 9.x | override file, `debug/rescan`, read back | close previous custom home |

* `setSystemSettings` replaces the whole `defaultApps` object, so the other entries (11.2.0 ships
  `kidsApp` in it) are read first and written back. `setDefaultApp` changes only `home`
  ([Settings app](research/home-key-and-home-app.md#settings-app)).
* The profile override is `/var/webos-profile/override/org.webosbrew.homechooser.json`, holding the
  current `defaultApps` profile with `categories.home` set to the chosen id, the other keys and
  categories kept and the `_`-prefixed keys dropped. 10.3.1 replaces the second-level object
  wholesale, so the full `categories` has to be there. The compositor's home id is the profile's,
  not the record's, which is why the override is needed for the Home key to behave on these
  releases ([10.3.1 and 11.2.0](research/home-key-and-home-app.md#1031-and-1120-1)). After the
  rescan the app reads `getProfiles` again and reports an error if `categories.home` is not the
  chosen id.
* The previous home is the record's `home`, else the profile's, else `com.webos.app.home`. If it
  is a custom launcher, the app closes it with `closeByAppId {"forceMode": true}` and reports a
  failure as a warning. The app never closes a stock home (`home`, `fullhome`, `kidshome`,
  `seniorhome`, and the other stock ids), on any release. On 11.x and 12.x the watcher can close
  `com.webos.app.home` later, after the chosen launcher is in front; see
  [The stock home is not preloaded](#the-stock-home-is-not-preloaded).
* On 6.x–9.x, the filter launches an explicit chosen app ID. This avoids the old first-write
  `defaultApps` subscription issue in SAM, so no SAM restart is needed. Enabling validates the
  stock source, writes the flag and boot entry, then applies the filter live; restoring LG Home
  restores the exact stock list before removing generated files.

Before these writes `apply` stores the choice, and afterward it ensures the boot entry and watcher
are in place, closes a previous custom Home if necessary, and refreshes the live filter on 6–9.

### Live changes on webOS 6–9

The first apply and later changes take effect immediately. Hook status always returns `reboot=no`.
The retained service `rebootPrompt` compatibility endpoint refuses with `notPending`; the app does
not call it. [The native implementation and recovery policy](home-key-hook.md) replace the earlier
QML mounts, generated boot cache, compositor restart and restart prompt.

## Persistence

On 11.2.0 the record's `home` can be lost at any boot: `accountmanager` calls `setDefaultApp` for
another category 12 to 17 s into every boot and, racing `sam`, the write can drop the other entries
([study](research/home-key-and-home-app.md#results-2026-10-19)). A write made once is not enough, so
Home Chooser keeps its own copy of the choice and enforces it.

* **The stored choice** is `/var/lib/webosbrew/homechooser/home`, a plain text file holding the app
  id and nothing else, written as root by the app. An absent file means stock: nothing is
  enforced. Every reader (the watcher, the boot entry, the Home key hook worker) trims spaces, tabs,
  CR and LF, treats an empty result as no choice, and accepts only letters, digits, `.`, `-` and
  `_`; anything else is logged and ignored, never put into a Luna payload or generated JS.
* **The boot entry** is `/var/lib/webosbrew/init.d/homechooser`. On 6–9 it links to the installed
  `scripts/homechooser`, which enqueues a nonblocking transient unit running the existing native
  service's `--boot` mode. Readiness waits run inside that unit. After volatile apply the native
  process becomes the watcher; its unit wrapper can use the same binary's runtime copy after removal.
  On 5 and 10–12, the existing persistent watcher restore copy remains under
  `/var/lib/webosbrew/homechooser/boot/`. `homechooser watch` starts the same lifecycle without
  waiting in the app's RPC request.
* **The watcher** is `service/scripts/homechooser-home-watch`. On 6–9 the native boot process
  becomes it via `exec`. The legacy 5/10–12 transient unit starts it directly:
  `systemd-run --unit=homechooser-home --property=Restart=on-failure --property=RestartSec=5 /bin/sh -c
  '<wrapper>' homechooser-home-watch <service>/scripts/homechooser-home-watch /tmp/homechooser-runtime/homechooser-home-watch`,
  where the wrapper runs the first script that exists and exits 0 if neither does. No unit file is written and no other service's unit,
  environment file or binary is touched. The flags are those of systemd 230 and later, which is
  what is assumed for webOS 5; the newest flag (`--collect`, 236) is not used, and a failed unit is
  cleared with `reset-failed` before the next start. It is `on-failure`, not `always`, because the
  watcher exits 0 when the choice is removed and has to stay stopped then.
  * It subscribes with `luna-send -i luna://com.webos.settingsservice/getSystemSettings
    {"category":"general","keys":["defaultApps"],"subscribe":true}`. It reads a reply as complete
    when its braces balance, so both one-line and pretty-printed output work, and takes `home` from
    an object or from a JSON string record.
  * When `home` is missing or differs from the choice it writes the choice again:
    `applicationManager/setDefaultApp` on 9.x and later (the merged `setSystemSettings` write on
    `Unknown method`), the merged `setSystemSettings` write (existing entries read back and kept) on
    5.x to 8.x. A record it cannot read as a flat object is not overwritten.
  * At most one write per 10 s. Writes that follow the last one within twice the interval double
    it, to a cap of 5 min; a quiet spell resets it. Within the interval it first reads the record
    again (the subscription may show a stale reply), then waits out the rest and reads once more
    before writing.
  * On 10.x and later it checks at start and after each write that
    `/var/webos-profile/override/org.webosbrew.homechooser.json` names the choice as
    `categories.home`, and rewrites it from `getProfiles` (same rules as the app) with a
    `debug/rescan` if not.
  * It also watches the apps it depends on; see [When apps are removed](#when-apps-are-removed).
    It subscribes to `applicationManager/listApps {"subscribe":true}` as a trigger only, checks the
    chosen app and `org.webosbrew.homechooser` with `getAppInfo` on every message and every 5 min,
    and runs from a copy of itself in `/tmp/homechooser-runtime/`.
  * It keeps the stock home from being preloaded while a third-party home is chosen; see
    [The stock home is not preloaded](#the-stock-home-is-not-preloaded).
  * It logs each action with a timestamp to `/tmp/homechooser-home.log` (trimmed to its last 200
    lines above 64 KB), exits 0 when the choice file is gone or unusable and 1 when the
    subscription ends. Started without a usable choice it makes the one
    [leftover Home](#a-leftover-home) check and exits 0.
* **Restoring stock** stops the watcher, removes the choice, and restores the platform record/profile.
  On 6–9 it restores and verifies the exact stock keyFilters list through a volatile configd write
  before deleting generated JS. On 5–9 the record names an installed stock launcher; the stock
  6–9 key handler ignores that record, but it remains useful to SAM and the watcher.
  On 10.x and later clearing `home` is one function in the app,
  `removeHomeFromRecord()`, and `merged_write ""` in the watcher: the record is written with `"home": null` and the other entries kept. Sub-keys of
  `general/defaultApps` can never be removed (a write that leaves `home` out only looks like a
  replace until the settings service restarts; `deleteSystemSettings` and `resetSystemSettings`
  are no-ops), but a stored `null` reads as unset and `launchDefaultApp {"category":"home"}`
  launches the profile's stock home, live and after a restart
  ([study](research/home-key-and-home-app.md)). Everywhere the record's `home` is read, `null`,
  `""` and a missing key mean "not set": with a stored choice that is a mismatch the watcher
  repairs, without one there is nothing to do.
* **The screen** shows none of this; the init.d link, the watcher and its log are for SSH users
  (`/tmp/homechooser-home.log`).

The watcher does not use a mocked preload manager in its tests; the preload calls only have to be tolerated by the stub.

Tests: `service/scripts/tests/test-homechooser-home-watch.sh` (a stub `luna-send` plays a stream of replies:
present, missing, different, `Unknown method`, pretty-printed, string records, a storm, the choice
file in each form, the override on 10.x; a fake clock and sleep check the rate limiting; markers in
the stream install and uninstall apps and wait for the watcher's app check, so the removal cases
are deterministic: the chosen app removed and back on 5, 6 to 9 and 10+, Home Chooser removed with
the full restore, both in either order and one removed during the other's confirmation wait with its message delivered or lost, the alert and toast triggers (once, not on a transient absence, not at a start, not after a restore from the app), a transient absence, a silent `getAppInfo`, the periodic
check, the restore from the runtime copy after the app directory is deleted, the leftover Home
without a stored choice on 5, 6 to 9 and 10+ with what it leaves alone, and the start-time
files-on-disk guard),
`service/scripts/tests/test-homechooser.sh` (the boot entry with stubbed `systemd-run` and `systemctl`: the 5.x,
6 to 9 and 10+ paths, the flag, the symlink, a service scripts directory that is gone, the unit's wrapper),
`service/scripts/tests/test-homechooser-homekey.sh` and `service/scripts/tests/test-real-roundtrip.sh` (see the
[Home key hook](home-key-hook.md#deployment)), run as `sh` and `busybox sh`, and the Jest specs
for `apply`, `restoreStock`, the status parsing and the missing-choice message.

## When apps are removed

The stock launcher has to keep working when the chosen app is uninstalled, when Home Chooser is
uninstalled, and when the second follows the first. Both writes the platform keeps (the `home`
entry, and on 10.x and later the profile override) name an app, and a name that is missing makes
things worse than no name: on 10.3.1 and 11.2.0 `launchDefaultApp` fails for a record entry that
names a missing app and does **not** fall back to the profile, and on 5.6.1 the Home key hook launches
the record's `home` directly
([How `sam` resolves `launchDefaultApp`](research/home-key-and-home-app.md#how-sam-resolves-launchdefaultapp)).
So the watcher notices a removal and undoes the writes itself. Nothing here relies on the format of
`listApps` change messages.

**Detection.** The watcher subscribes to `luna://com.webos.applicationManager/listApps
{"subscribe":true}` (`luna-send -i`) and treats every message as a reason to look, nothing more. A
look is `luna://com.webos.applicationManager/getAppInfo {"id":"..."}` for `org.webosbrew.homechooser`
and for the chosen app; an uninstalled id answers `returnValue:false` (`Invalid appId specified OR
Unsupported Application Type`). The same look is repeated every 5 min (`HOMECHOOSER_RECHECK`) in case
the subscription missed something, and once at start. Home Chooser counts as gone when `getAppInfo`
says so or its app directory is missing. An app counts as gone only if it is still gone 15 s later
(`HOMECHOOSER_CONFIRM_DELAY`), because an app update temporarily removes its directory and should not trigger a restore. A `getAppInfo` that does not answer changes nothing. At a watcher start
(a boot, where `sam` may not have loaded every app yet) an app whose `appinfo.json` is still in
`/media/developer/apps/usr/palm/applications`, `/usr/palm/applications` or
`/media/cryptofs/apps/usr/palm/applications` is not taken as gone either, whatever `getAppInfo`
says; after the start a removal is judged by `getAppInfo` alone, as before. The look at Home
Chooser is repeated after the chosen app's confirmation delay, so a Home Chooser removed while that
wait lasts is found without depending on a `listApps` message of its own.

### The chosen app is uninstalled

The platform goes back to stock, the choice file stays (it is the user's choice), the watcher logs
it, tells the user (below) and stops writing. When the app is installed again it applies the choice again, with the same
writes as a new choice. A new choice made while the old app is missing replaces it at once.

| Release | Put back to stock | Home key while the app is missing |
| --- | --- | --- |
| 5.x | `home` = `com.webos.app.fullhome` if `getAppInfo` finds it, else `com.webos.app.home` (as `restoreStock`); written only if `home` is not that already | stock launcher |
| 6.x to 9.x | Set a stock launcher in the record, keeping other entries, then `homechooser-homekey refresh stock` restores the exact stock keyFilters array with `volatile:true` | LG Home opens immediately; verified readback precedes deletion of unused generated JS |
| 10.x and later | override file removed, `debug/rescan`, then `home` written as `null` in the record | the profile's `home`, which is the stock launcher |

On return: 5.x and 6.x to 8.x write the merged record with `home`; 9.x and later `setDefaultApp`
(the merged write on `Unknown method`); 10.x and later also rebuild the override from
`getProfiles`; 6.x to 9.x run `homechooser-homekey refresh` to generate and select the live filter. The
screen says "The Home app you chose was removed; using LG Home." under the list when the stored choice is not
among the installed apps that declare `appCategory: ["home"]`.

**The user is told once.** After the revert (no compositor restart on any release), the watcher shows an alert with one OK button:
`luna-send -n 1 luna://com.webos.notification/createAlert '{"sourceId":"org.webosbrew.homechooser","title":"Home Chooser","message":"<title> was removed, so the Home button opens LG Home again.","buttons":[{"label":"OK","focus":true}]}'`.
The text is the same on every release. On 6–9, configd selects the exact stock filter list before
this notification, so HOME opens LG Home immediately.
The title of an app that is gone cannot be looked up, so the watcher reads it from `getAppInfo`
(failing that `appinfo.json`) while the app is installed and keeps it per choice, like the window
type; a title it does not have, or one with a quote or backslash in it, is replaced by the app id.
The wording is the same on 5.x (the stock home there is `fullhome`). It is sent once per removal
event: the later checks find the app already marked missing and say nothing, and a return followed
by a new removal is a new event. It is not sent for a temporary absence (the confirmation delay
comes first), when the user restores stock from the app (the choice file is removed and the watcher
stopped before anything is missing), when the choice is changed to another app, or when a watcher
starts and finds the app already missing (a boot is not a new removal). The call uses root `luna-send -a com.webos.applicationManager`, under a 5 s timeout; its reply is logged (`notification createAlert: ...`) and
nothing waits on it. A reply that says `returnValue:false` (a refusal) is logged
(`the alert was refused`) and the same message is shown as a toast; no reply is logged and not
retried. `HOMECHOOSER_NOTIFY=0` turns it off. What the notification service really does with the
call is not tested here; it was read from the firmware (5.6.1 to 11.2.0) and has not been run on a
device.

On 10.x and later clearing `home` is the `"home": null` write of `removeHomeFromRecord()` (see
[Persistence](#persistence)); the watcher reads the record back and logs when `home` is still set.
On 5–9 the record names a stock launcher. The stock 5.x handler uses that record directly;
on 6–9 the stock filter is restored separately and the record keeps the watcher/SAM policy consistent.

### Home Chooser is uninstalled

The app's files go with it, so the watcher cannot run anything from the app directory. At start it
copies itself, `homechooser-homekey` and `homechooser-access` into
`/tmp/homechooser-runtime/` (replacing each file by rename) and runs from there. When Home Chooser is
gone it does, in order:

1. Removes launcher access and reverts the platform record/profile as above.
2. On 6–9, restores the exact stock filter array volatile and verifies its readback. Failure keeps
   the source files and state available for retry.
3. Removes the choice, flag, unused generated versions, saved stock list and init.d link. Legacy
   watcher restore copies and old cache markers are cleaned up too.
4. Shows “Home Chooser was removed. The Home button opens LG Home again.” through a best-effort
   toast using the fixed privileged Luna identity, then exits successfully.

No GUI restart or unmount is required. The runtime copy includes the existing native executable
on 6–9 so removal does not delete the recovery code out from under the watcher.

Every step tests before it writes, so a second run (a unit restart half way, or both apps gone)
changes nothing: with the record's `home` already the stock id (5.x to 9.x) or unset (10.x and later: null, empty or missing), "home is already ..." / "nothing to remove" is logged and no write
is made. The unit's command is a small wrapper that runs the app's copy of the watcher, else the
runtime copy, else exits 0, so a watcher that crashes and is restarted after the app is gone still
finishes the restore, from `/tmp`, instead of failing to find its script every 5 s. The log
(`/tmp/homechooser-home.log`) and the runtime copy stay in `/tmp` until reboot.

Both removed, in either order and at any spacing while the watcher runs, ends at the same clean
state (no choice file, flag or `init.d` link, the record `home` unset, the override gone, the
unit stopped, the stock home's preload policy enabled): the watcher looks at Home Chooser first in
every check and again after the chosen app's confirmation wait, so a Home Chooser removed during that
wait is found whether or not its own `listApps` message arrives (before this was added, a lost
message left the choice file, the flag and the link until the 5 min check). It takes about 15 s per
removal it has to confirm, one after the other, so up to about 30 s after the second removal; a
watcher stopped before that (a TV or VM powered off right after the second removal) leaves the
state described below. Home Chooser first (or both at once, which is checked in that order) is the full
restore, after which the chosen app's removal finds nothing of ours. The chosen app first is the
revert above; the later removal of Home Chooser repeats it harmlessly and cleans up. So B followed by
A, and A followed by B, end at the same stock state, and a removal of the chosen app after Open
House left the platform alone breaks nothing.

### When the watcher is not running

On 6–9, reboot discards the volatile override and returns to the firmware's stock keyFilters list.
Generated sources live outside the package, so an uninstall never leaves a selected path pointing
into a removed package. An active watcher handles normal uninstall immediately; without a watcher,
opening a reinstalled Home Chooser also performs leftover repair.

On 5 and 10–12, the existing persistent boot restore copy handles uninstall with the watcher stopped:
it confirms absence, restores the record/profile and preload policy, and removes the link and copy.
Failed restoration retains that state for retry.

### A leftover Home

An earlier installation or interrupted restore may leave the record's `home`, and on 10.x and
later the override file, naming a launcher with no stored choice. The current boot copy keeps
retrying an uninstall restore if the Luna write fails. A separate leftover can still arise if
state was removed outside the normal flow; once the named launcher is gone, Home is broken.
The rule, applied when the watcher starts without a usable stored choice (that is, when the app is
opened after a reinstall, through `homechooser watch`):

* The ids looked at are the record's `home` and, on 10.x and later, the `categories.home` of
  `/var/webos-profile/override/org.webosbrew.homechooser.json`. Before 10.x the override file is not
  read. `null`, `""` and a missing key are no id.
* An id counts as not installed only if `getAppInfo` answers `returnValue:false` twice,
  `HOMECHOOSER_CONFIRM_DELAY` (15 s) apart, and its `appinfo.json` is in none of the install
  directories above. `com.webos.app.home` and `com.webos.app.fullhome` are never acted on, and an
  id `getAppInfo` does not answer for is left as it is (logged `getAppInfo did not answer`).
* An id that is installed is left alone even though no choice is stored: someone may have chosen it
  by other means, and Home Chooser only repairs a name that cannot work.
* A gone id is reverted the way the removal of a chosen app is, without the notification (it is not
  a removal the user just made): 5.x to 9.x write `home` as the stock launcher by the merged write
  (`com.webos.app.fullhome` if installed, else `com.webos.app.home`); 6.x to 9.x also run
  `homechooser-homekey refresh stock`; 10.x and later remove the override and rescan when the
  override names the gone id, and write `home` as `null` when the record does. On 10.x the two are
  separate: a stale override with a record naming an installed app removes only the override, and
  the other way round.
* The record is read again right before the write, and nothing is written if it changed during the
  confirmation (`home changed meanwhile`). If a choice is stored during the confirmation (the user
  picked a launcher), nothing is repaired and the same process goes on as the ordinary watcher of
  that choice.
* With a stored choice none of this applies: the choice is enforced, and a record or override
  naming something else is overwritten by it (the [Persistence](#persistence) rules).

The boot entry does not start the watcher without a choice, so this check does not run at every
boot: the leftover comes from an uninstall, which removes the `init.d` link, so nothing would run at
boot anyway until the app is opened again.

What only a live test settles: that
`getAppInfo` of an uninstalled id answers `returnValue:false` on every release, and that `sam`
answers it at boot when the init.d entry runs; how `listApps` change messages arrive (only their
arrival is used) and that `timeout` accepts `timeout 10 <command>` in the images' busybox;
and the real uninstall path through Homebrew Channel and `ares-install --remove`, including that
an update of Home Chooser does not look like an uninstall for 15 s.

## The stock home is not preloaded

LG's stock home, `com.webos.app.home`, is kept resident by the preload manager (and on 5.x to 9.x
launched at boot by `snapshot-boot-components.service`), which costs 70 to 105 MB while a third-party
home is in use, and closing it by hand does not last because it is preloaded again. While a third-party
home is chosen the watcher (webOS 6 and later) runs, as root:

```
luna-send -n 1 luna://com.webos.service.preloadmanager/setPreloadPolicy '{"application":{"id":"com.webos.app.home","isEnabled":false}}'
```

The watcher closes the stock home separately on 11.x and 12.x, after a safe launcher foreground
event and a successful preload-policy call. It sets the policy back to `"isEnabled":true` when it
stops applying. Never `"permanent"`: the preload manager keeps the policy in memory, nothing is
written, and after a reboot the stock policy is back until the watcher re-applies it. The memory
manager is not touched.

* **When.** The watcher keeps one state, off or on, and calls only when it changes: once at start
  (so once per boot, and once more after a restart of the unit), when the choice changes between a
  third-party home and a stock one (`com.webos.app.home` or `com.webos.app.fullhome` in the choice
  file), when the chosen app is found uninstalled (on) and when it returns (off), and at every exit
  with status 0: the choice file is gone or unusable, the unit is stopped (Restore to stock runs
  `systemctl stop homechooser-home` first, so the watcher gets SIGTERM and sets the policy back
  before the choice file is removed), and the Home Chooser uninstall path, which runs from the
  `/tmp` copy. A restart after a failure (exit 1) leaves the policy as it is; the new instance
  applies it again.
* **At boot.** The preload manager may not answer when the init.d entry starts. The policy call runs
  in the background, retried every `HOMECHOOSER_PRELOAD_RETRY` seconds (10) up to
  `HOMECHOOSER_PRELOAD_TRIES` times (12) until it answers `returnValue:true`. The existing
  `HOMECHOOSER_PRELOAD_SETTLE` delay (45 seconds) then passes before a close is eligible. No
  stock-home close is tied to that timer. On 6.x to 10.x there is no close at boot or later.
* **11.x and 12.x: close after the launcher is in front.** The watcher subscribes to SAM's
  `getForegroundAppInfo` and `getAppLifeEvents`. A normal launcher qualifies when SAM reports it
  as foreground. An overlay qualifies only on its own launch event and only if SAM reports a
  nonstock full-screen card beneath it. With LG Home or a blank foreground beneath an overlay,
  the close is skipped. The watcher makes at most one close attempt per boot or choice change.
  If SAM relaunches LG Home, it logs that event and does not close it again in that boot. The
  selected launcher's page and completion toast can therefore remain visible during selection.
* **Best effort.** Each call runs under `timeout 5` when the image has it (as the app checks do),
  its reply goes to `/tmp/homechooser-home.log`, and only the boot call is retried: a refusal
  (`Cannot find com.webos.app.home` when the manager does not list the app, or a denied call) or a
  hang costs at most 5 s per call and changes nothing else: the home switch, the restore and the
  uninstall cleanup go on. `HOMECHOOSER_PRELOAD=0` turns the feature off.
* **webOS 5 is left alone.** There the Home key starts `com.webos.app.fullhome` as an overlay and
  `com.webos.app.home` is not running, so there is nothing to free; the preload lists of 5.6.1 name
  both homes, and the 5.x restore path hands the Home key hook either of them, so a policy change would
  have risk and no gain. The watcher makes no preload call on 5.x.
* **6.x to 10.x: the stock home is not closed, for any launcher.** On these releases `sam` relaunches
  the stock home immediately as the "last input" app (`NL_APP_LAUNCH_BEGIN`, `caller_app_id:
  last_input_handler`, mode `last_input`, right after `LAUNCH_LASTAPP run_to_launch_last_app`
  on_foreground_null) whenever it is closed. Measured on 9.2.1 and 8.3.0 (about 89 MB) and on 10.3.1
  (about 75 MB; the stock home was back 1.5 s after the close and stayed for 60 s) with an overlay
  launcher (Dock), and again on 10.3.1 and 9.2.1 with a normal launcher: it was back within about 2 s.
  On 10.3.1 the log shows `closeByAppId` from `com.webos.lunasend`, `APP_CLOSED` for the stock home,
  `LAUNCH_LASTAPP {"status":"run_to_launch_last_app"} on_foreground_null`, then
  `NL_APP_LAUNCH_BEGIN {"app_id":"com.webos.app.home","caller_app_id":"last_input_handler","mode":"last_input"}`;
  the preload manager's only launch in that window was `com.webos.app.livetv`, and `getPreloadPolicy`
  still read `isEnabled:false` for the stock home, so it is not the preload manager. The close frees
  nothing and only makes it restart, so on 6.x to 10.x the watcher never closes the stock home, at boot
  or on a change; it still sets the preload policy and logs `webOS <major>: stock home left running until
  the next reboot (sam relaunches it as the last-input app)`. The app never closes any stock home
  during selection on any release.

  So on 6.x to 10.x **the stock home can stay resident after a launcher is chosen, until the next
  reboot**. It is not a defect of the choice: the system puts it back at once whatever is done to it.
  After a reboot it can be gone, because the watcher turns the stock home's preload off at every boot
  (in memory only, never `permanent`) and nothing closes and reopens it any more. The 6–9 live
  filter needs no reboot; stock Home residency is independent of activation. On 5.x nothing is done (below). On 11.x and 12.x the close does stick (11.2.0 and 12.0.0, with
  Dock too), so it is kept there.
* **Measured (2026-10-23, Beanjar VMs).** Root `luna-send` may call `getPreloadPolicy` and
  `setPreloadPolicy`; `getPreloadPolicy` answers `{"applications":[{"id":"com.webos.app.home",
  "isEnabled":...,...},...]}` (an empty list right after boot). On 9.2.1 with the policy disabled, a
  force-closed stock home (Homebrew Channel in front) stayed closed for 120 s and about 80 MB was
  freed; re-enabled, the preload manager launched it again about 8 s later; after a reboot the
  policy was back to enabled. End-to-end with `--reboot-check`: 11.2.0 (103 MB freed) and 12.0.0
  (71 MB) kept the stock home closed in those earlier runs; 7.x, 8.3.0, 9.2.1 and 10.3.1 pass
  with the stock home left running (for every launcher since 2026-10-24, as above); 5.6.1 makes no preload call.

  The 2026-10-26 11.2.0 probe showed why the close must wait: with a normal fixture in the
  foreground, LG Home closed and stayed down. Closing the fixture then produced SAM
  `LAUNCH_LASTAPP ... on_foreground_null` and an `NL_APP_LAUNCH_BEGIN` for LG Home from
  `last_input_handler` about one second later. With the web overlay over LG Home, closing LG Home
  closed the overlay and caused the same fallback. With Homebrew Channel beneath that overlay,
  the stock close stuck. On 12.0.0 SAM likewise recorded `on_foreground_null` and a
  `last_input_handler` Home launch after the normal launcher closed; the old Home PID was gone
  from `/proc` although SAM briefly kept it in `running`. Closing LG Home beneath the overlay
  again produced the idle fallback and left an empty full-screen foreground. These observations
  are why a running overlay alone is insufficient evidence for a close. In the 12.0.0 full fixture
  matrix, the web overlay over Homebrew Channel stayed visible after the watcher closed LG Home;
  no live stock-home process remained.

## Restoring the stock home

* 5.x: both stock homes declare `appCategory: ["home"]` and the Home key hook launches the record's
  `home` directly, so a record without `home` could leave the Home key with nothing to launch.
  The record is written with `home` set to `com.webos.app.fullhome` if `listApps` shows it
  installed (the factory value on the studied 5.6.1), else `com.webos.app.home`, and the other
  entries are kept ([5.6.1](research/home-key-and-home-app.md#561-supported-through-the-default-app-record)).
* 6.x to 9.x: like 5.x, the record is written with `home` set to the stock launcher
  (`com.webos.app.fullhome` if `listApps` shows it, else `com.webos.app.home`) and the other entries
  kept, through `setSystemSettings`. The volatile keyFilters list is restored separately. The stock id stays
  in the record after an uninstall; stock firmware ignores the record for the Home key.
  `setDefaultApp` is not used for this: the stock home has no `appCategory` and `sam` refuses it as a
  `setDefaultApp` target. For that reason choosing
  `com.webos.app.home` in the candidate list runs this restore instead of `apply` on every
  release (elsewhere it is simply "stock"); other stock homes such as `seniorhome` stay ordinary
  choices.
* 10.x and later: the record is written with `"home": null` (same function), the override file is removed and the
  profile rescanned; the profile's `home` is the fallback.

The custom home is then closed; a stock home is never closed.

## Step log

Each operation returns its steps: the Luna call and payload, file write or removal, or command,
with the reply or the error. The screen does not show them; the app writes each one to
`console.log` with an `[HomeChooser]` prefix (web inspector, device log), so a failure keeps the
service's own text. An absent record (`There is no matched result from DB`) is treated as "not
set", not as a failure.

## Build and test

```
cd app
npm install
npm test                # Enact's jest
npm run pack-p          # production build into app/dist
npm run package         # ares-package into build/ipk (git-ignored)
```

The paths of the build machine are variables. Their defaults are one developer's layout; set them on
the `make` command line (`make -C service SDK=/opt/sdk`) or in the environment, which `npm run
package` passes through to `make -C service`:

| Variable | Default | Used by |
| --- | --- | --- |
| `SDK` | `/opt/arm-webos-linux-gnueabi_sdk-buildroot` | `service/Makefile`: the webOS buildroot SDK |
| `SYSROOT` | `$(SDK)/arm-webos-linux-gnueabi/sysroot` | `service/Makefile` |
| `CROSS` | `$(SDK)/bin/arm-webos-linux-gnueabi-` | `service/Makefile` (gcc, strip) and `service/test/check-abi.sh` (readelf, nm; `READELF` and `NM` override one tool) |
| `FIRMWARES` | `$OPENHOUSE_FIRMWARES`, else `~/Projects/webos-firmwares` | `service/Makefile`: where `ROOTFS` is |
| `ROOTFS` | `$(FIRMWARES)/04.63.25.01-HE_DTV_W20L_AFAAJAAA/rootfs` (5.6.1) | `make -C service check-abi` |
| `OUT` | `../build/service` | `service/Makefile`: where the binary goes |
| `OPENHOUSE_FIRMWARES`, `OPENHOUSE_HYPERVISOR` | `~/Projects/webos-firmwares`, `~/Projects/webos-hypervisor` | `service/scripts/tests/test-real-roundtrip.sh` and `service/scripts/tests/test-minimal-hook.sh`: the firmware roots they try (a root that is missing is skipped) |
| `OPENHOUSE_S23Z_ROOTFS` | under `OPENHOUSE_HYPERVISOR` | the same two scripts: the unpacked S23Z image |

`make -C service test` and the Jest specs need none of them.

The version has one source, `"version"` in `app/package.json`. `app/appinfo.json` (what
`ares-package` and the TV read) must carry the same: `npm run check-version`
(`app/scripts/check-version.js`) fails on a mismatch, `npm run package` runs it first, a Jest spec
checks it with the other tests, and `npm version <x.y.z>` rewrites `appinfo.json` through the
`version` script. The service binary gets it from `service/Makefile` (`VERSION`, read from
`package.json`, compiled in as `HC_VERSION`; `service/src/version.h` requires it and an empty
version fails the build), and reports it as `version` in `status` and `getInfo`. `services.json` has no
version. The ipk's control file is written by `ares-package`, which fills in
`Maintainer: N/A <nobody@example.com>` itself; that placeholder is left as it is.

Enact copies all of iLib's locale data (83 MB, a 15 MB package) and its CLI has no switch to
limit it for a non-isomorphic build, so `scripts/prune-ilib.js` runs between the build and
`ares-package`. It keeps the top-level data files and the `en`, `und` and `zoneinfo` locale
directories, drops the other locale directories and the character maps, and rewrites iLib's
manifest to the remaining files. The UI strings are English and come from the source and from
Moonstone's own resources, which are left alone (Moonstone's fonts, 1.3 MB, are kept). `scripts/stage-service.js` copies the five runtime scripts from `service/scripts/` into
the service package beside the native binary, at mode 755. The app bundle carries no scripts;
reviewed release manifests and diffs stay in the offline tests.

The build targets `chrome 68` (the browser of webOS 5) through the `browserslist` entry in
`app/package.json`. `service/scripts/tests/test-*.sh` hold the shell tests (see [Persistence](#persistence)). The Jest tests drive `apply` and `restoreStock` with a fake `rootLuna` for 5.6.1,
6.6.0, 8.3.0, 9.2.1, 10.3.1 and 11.2.0 and assert the payloads.

The webOS 6–9 hook has a two-file core (Settings.qml and StarfishKeyFilter.qml). The systemUi refresh and
four supporting files are best effort; status lists skipped files. If systemUi cannot be rewritten,
Home over the chosen launcher relaunches it instead of handing it the key, and LG Home in front
can keep the key through the stock comparison. See [the minimal hook](home-key-hook.md).
