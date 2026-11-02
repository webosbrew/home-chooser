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

* **The list.** Title "Home Chooser", subtitle "Choose which app opens when you press Home". The first row is "LG Home
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

* **Footer.** On webOS 6 to 9 only, and only when something is wrong, one fixed line at the bottom of the panel (not focusable); nothing when all is fine. "The Home button can't be changed on this TV's software." when the structural hook refuses the firmware. "The Home button isn't set up yet. Restart the TV, or choose LG Home and then your app again." when an app is chosen but the live filter is not ours. "Couldn't check whether the Home button opens your chosen app." when an app is chosen and the status could not be read. Nothing when the filter is ours, nothing while LG Home is chosen and the filter can still be installed, nothing on 5.x and 10.x or later, and nothing when root is unavailable. The e2e harness checks the filter with `homechooser-homekey status`, not this line.
* **Problems (no root).** Homebrew Channel missing or not running, refusing `elevateService` (webOS 26 and later),
  or the service not coming up as root. One line per case: "Homebrew Channel isn't installed or isn't running.",
  "Homebrew Channel doesn't give Home Chooser root access on this TV.", "Home Chooser's service isn't running as root.",
  "Home Chooser couldn't set up its service." (anything else). Each states the case and suggests no fix: the panel keeps its layout. A plain one-line reason is at the
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
content never meets a shell. Boot and Home-key entry scripts invoke `homechooser-daemon`. The
watcher policy runs in C and uses luna-send child transport; access-file edits retain the shell helper.

The release comes from `/etc/os-release`, read through the service (`VERSION_ID`, then `VERSION`, then `PRETTY_NAME`).

## What is written

| Release | Settings record `general/defaultApps` | System profile | After |
| --- | --- | --- | --- |
| 5.x | `setSystemSettings` with the existing entries and `home` | - | previous launcher noted for a later close |
| 6.x to 8.x | same | - | previous launcher noted; reload the volatile JS filter |
| 9.x | `applicationManager/setDefaultApp`; on `Unknown method`, the `setSystemSettings` write | - | previous launcher noted; reload the volatile JS filter |
| 10.x to 12.x | `setDefaultApp` as on 9.x | override file with `home` and `idleApp`, `debug/rescan`, read back | previous launcher noted |

* `setSystemSettings` replaces the whole `defaultApps` object, so the other entries (11.2.0 ships
  `kidsApp` in it) are read first and written back. `setDefaultApp` changes only `home`
  ([Writing the choice](research/home-key-and-home-app.md#writing-the-choice)).
* The profile override is `/var/webos-profile/override/org.webosbrew.homechooser.json`, holding the
  current `defaultApps` profile with `categories.home` set to the chosen id, the other keys and
  categories kept and the `_`-prefixed keys dropped. 10.3.1 replaces the second-level object
  wholesale, so the full `categories` has to be there. The compositor's home id is the profile's,
  not the record's, which is why the override is needed for the Home key to behave on these
  releases ([10.3.1 and 11.2.0](research/home-key-and-home-app.md#1031-and-1120)). After the
  rescan the app reads `getProfiles` again and reports an error if `categories.home` is not the
  chosen id.
* The same override also carries `idleApp`, the app SAM launches when the foreground becomes
  empty (SAM reads it live from the system profile on 10.3.1 and 11.2.0):
  * a full-screen launcher: `{"support": true, "appId": "<launcher id>"}`;
  * an overlay launcher (`defaultWindowType` `overlay` in `getAppInfo`, the test the watcher and
    the home gate use): `{"support": false}`, which makes SAM bring back the last input instead
    (after a boot the watcher opens it once; see
    [The last input after a boot](#the-last-input-after-a-boot-webos-10-and-later));
  * LG Home: no override at all.

  The other keys of the current `idleApp` profile are kept, without the `_` keys and without the
  old `support` and `appId`; on 10.3.1 a key the override leaves out keeps the stock layer's value.
  Both writers (the app's `buildOverride` and the watcher's `check_override`) build the same
  content, and the app asks `getAppInfo` first: an empty id, or one `getAppInfo` does not know,
  stops the apply before anything is written, and the watcher then writes no `idleApp` at all. An
  `idleApp` that names a missing app (never installed, uninstalled, or an empty `appId`) leaves a
  black screen when the last app closes, with no fallback to LG Home, so the override, `idleApp`
  included, is removed and the profile rescanned whenever the launcher goes missing, LG Home is
  chosen, Home Chooser is uninstalled or a leftover is repaired. After the rescan the app warns if
  `getProfiles` shows another `idleApp`.
* The app closes nothing, on any release. The previous homes (the record's `home` and, on 10.x
  and later, the profile's) that are custom launchers are written to
  `/var/lib/webosbrew/homechooser/previous`, and the watcher closes them once Home Chooser is off
  screen; see [The previous launcher](#the-previous-launcher). A stock home (`home`, `fullhome`,
  `kidshome`, `seniorhome`, and the other stock ids) is never closed as a previous launcher. On 10.x
  and later the watcher closes `com.webos.app.home` later, after the chosen launcher is in front,
  and on 6–9 it closes a real stock Home once the gate is mounted; see
  [The stock home is not preloaded](#the-stock-home-is-not-preloaded).
* On 6.x–9.x, the filter launches an explicit chosen app ID. This avoids SAM's first-write
  `defaultApps` subscription issue on webOS 6
  ([study](research/home-key-and-home-app.md#writing-the-choice)), so no SAM restart is needed. Enabling validates the
  stock source, writes the flag and boot entry, then applies the filter live; restoring LG Home
  restores the exact stock list before removing generated files.

Before these writes `apply` stores the choice and writes the `previous` file, and afterward it
ensures the boot entry and watcher are in place and refreshes the live filter on 6–9.

### The previous launcher

The rule: while Home Chooser is on screen, applying a choice does nothing that can empty the
screen or bring a full-screen app up beneath the panel. Closing the launcher in front would do
both: with nothing full-screen left SAM relaunches its idle app (on 6–9 the home gate, which opens
the last input for an overlay launcher; on 10.x and later the profile's `idleApp`), and
surface-manager then closes the panel (`windowPolicy`) 1.5 to 3 s after the click, before or during
its "Done" toast (seen on 9.2.4 and 8.3.0 when the app still closed the previous launcher itself).

* **What the app writes.** At every change of Home, right after the choice file and before any
  platform write, the app writes `/var/lib/webosbrew/homechooser/previous`: the custom launchers
  the change replaces, one id a line, with any ids already there, never the new choice or a stock
  home. A change from LG Home writes it empty. Choosing LG Home (the restore) writes it after the
  restore, and starts the watcher when it names a launcher.
* **The hold.** While the file is fresh, the watcher closes nothing: not the previous launcher, not
  the 6–9 gate's real stock Home, not the 10.x LG Home, and the 10.x last-input check of the boot
  records "Home Chooser" and launches nothing (a change made in the panel is an app launch, so
  SAM's own fallback already works). The app's one-off `homekey refresh` mounts the 6–9 gate but
  leaves the stock Home close to the watcher whenever the file exists. The hold ends when Home
  Chooser is off screen: its own `close`, `stop` or `background` life event; a foreground event
  naming an app other than the one in front when the hold began (a full-screen app coming up
  closes the panel anyway); or, at the 5-minute check, Home Chooser missing from SAM's `running`
  list. The held stock Home closes then follow their own rules.
* **The close.** After the hold, each previous launcher is closed with `closeByAppId
  {"forceMode": true}` the first time SAM names a full-screen app in front (`getAppInfo`
  `defaultWindowType` not `overlay`) that is neither that launcher nor Home Chooser. Closing an app
  that is not the one in front cannot empty the foreground, so nothing is relaunched and the screen
  does not change. Looked at on every foreground event, life event and app-list event, and at the
  5-minute check. The id leaves the list (and the file) before the call, so it is closed at most
  once per change; with the list empty the file is removed.
* **What closes it, per switch.** From a full-screen launcher the panel sits over that launcher, so
  when the panel closes the old launcher is still in front and stays; it is closed when the owner
  presses Home and a full-screen launcher comes up, or opens any other full-screen app (an overlay
  launcher draws over the old one, which stays in front until then). From an overlay launcher the
  panel sits over the input or app beneath, so the old launcher is closed as soon as the panel
  closes. Every release (5.x included) subscribes to the foreground and life events for this.
* **Never closed.** The launcher chosen now (also when the owner switches back before the close), a
  stock home (`com.webos.app.*home`), Home Chooser, and an invalid id. A launcher the owner opens
  again (`launch` or `relaunch`), one that closes by itself, and one that is uninstalled are dropped
  from the list.
* **Restarts and reboots.** The file is the pending list, so a watcher restart reads it again and
  holds again until the next of the events above, then closes what is left; a launcher already
  closed is not in the file any more. A file written before the current boot (its mtime before
  `btime` in `/proc/stat`) is dropped at the next look: what it names is not running after a boot.
* **LG Home chosen.** With no choice stored and a launcher left to close, the watcher stays only for
  that: no record, override, filter, gate or preload work. It exits 0 once the list is empty (closed,
  dropped or uninstalled), and becomes the full watcher if a launcher is chosen meanwhile.

The log lines start with `previous launcher:`, for example `previous launcher: Home changed in
Home Chooser; to close later: <id>; closes wait until Home Chooser is off screen (in front: <id>)`,
`previous launcher: Home Chooser is off screen (its close event); held closes may go ahead`,
`previous launcher: <id> is in front; closed once another full-screen app is` and `previous
launcher: closeByAppId <id> with <front> in front: {"returnValue":true}`. The 6–9 gate logs `home
gate: stock Home close waits until Home Chooser is off screen` while it holds.

### Live changes on webOS 6–9

The first apply and later changes take effect immediately. Hook status always returns `reboot=no`.
The service's `rebootPrompt` compatibility endpoint refuses with `notPending`; the app does
not call it. See [the native implementation and recovery policy](home-key-hook.md).

## Persistence

On 9.x and later the record's `home` can be lost at any boot: `accountmanager` calls `setDefaultApp`
for another category early in every boot and, racing `sam`, the write can drop the other entries
([study](research/home-key-and-home-app.md#the-records-home-can-be-lost-at-boot-921-and-later)). A write made once is not enough, so
Home Chooser keeps its own copy of the choice and enforces it.

* **The stored choice** is `/var/lib/webosbrew/homechooser/home`, a plain text file holding the app
  id and nothing else, written as root by the app. An absent file means stock: nothing is
  enforced. Every reader (the watcher, the boot entry, the Home key hook worker) trims spaces, tabs,
  CR and LF, treats an empty result as no choice, and accepts only letters, digits, `.`, `-` and
  `_`; anything else is logged and ignored, never put into a Luna payload or generated JS.
* **The boot entry** is `/var/lib/webosbrew/init.d/homechooser`. On 6–9 it links to the installed
  `scripts/homechooser`, which enqueues a nonblocking transient unit running
  `homechooser-daemon --boot`. Readiness waits run inside that unit. After volatile apply the native
  process runs the watcher; its unit wrapper can use the same binary's runtime copy after removal.
  On 5 and 10–12, the existing persistent watcher restore copy remains under
  `/var/lib/webosbrew/homechooser/boot/`. `homechooser watch` starts the same lifecycle without
  waiting in the app's RPC request.
* **The watcher** is `service/src/watcher/`, inside `homechooser-daemon`. The boot entry queues
  `systemd-run --no-block --unit=homechooser-home --property=Restart=on-failure --property=RestartSec=5`
  with a wrapper that runs the installed `homechooser-daemon --boot`, then its runtime copy if
  the installed image is gone. It preserves an already active unit. No unit file is written.
  A clean exit stays stopped; transport/restore failures restart after five seconds.
  * It uses a direct `luna-send -i` child subscription to `settingsservice/getSystemSettings` with
    `{"category":"general","keys":["defaultApps"],"subscribe":true}` using the privileged
    application identity. GIOChannel reads and frames JSON messages, including pretty output and string-encoded records.
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
    `categories.home` and carries the `idleApp` the choice's window type asks for, and rewrites it
    from `getProfiles` (same rules as the app) with a `debug/rescan` if not. When `getAppInfo` does
    not know the choice, it accepts an override with no `idleApp`, with `support` false or naming the
    choice, and writes none.
  * It also watches the apps it depends on; see [When apps are removed](#when-apps-are-removed).
    It subscribes to `applicationManager/listApps {"subscribe":true}` as a trigger only, checks the
    chosen app and `org.webosbrew.homechooser` with `getAppInfo` on every message and every 5 min,
    and stages restart recovery in `/tmp/homechooser-runtime/`. The running image survives package unlink.
  * It keeps the stock home from being preloaded while a third-party home is chosen; see
    [The stock home is not preloaded](#the-stock-home-is-not-preloaded).
  * On 10.x and later, with an overlay launcher chosen, it opens the last input once per boot; see
    [The last input after a boot](#the-last-input-after-a-boot-webos-10-and-later).
  * It closes the launcher a change of Home replaced, once Home Chooser is off screen, and holds
    its other closes until then; see [The previous launcher](#the-previous-launcher).
  * It logs each action with a timestamp to `/tmp/homechooser-home.log` (trimmed to its last 200
    lines above 64 KB), exits 0 when the choice file is gone or unusable and 1 when the
    subscription ends. Started without a usable choice it makes the one
    [leftover Home](#a-leftover-home) check and exits 0.
* **Restoring stock** stops the watcher, removes the choice, and restores the platform record/profile.
  On 6–9 it restores and verifies the exact stock keyFilters list through a volatile configd write
  before deleting generated JS. On 5–9 the record names an installed stock launcher; the stock
  6–9 key handler ignores that record, but it remains useful to SAM and the watcher.
  On 10.x and later clearing `home` is one function in the app,
  `removeHomeFromRecord()`, and `merged_write(w, NULL)` in the watcher: the record is written with `"home": null` and the other entries kept. Sub-keys of
  `general/defaultApps` can never be removed (a write that leaves `home` out only looks like a
  replace until the settings service restarts; `deleteSystemSettings` and `resetSystemSettings`
  are no-ops), but a stored `null` reads as unset and `launchDefaultApp {"category":"home"}`
  launches the profile's stock home, live and after a restart
  ([study](research/home-key-and-home-app.md#sub-keys-of-the-settings-record)). Everywhere the record's `home` is read, `null`,
  `""` and a missing key mean "not set": with a stored choice that is a mismatch the watcher
  repairs, without one there is nothing to do.
* **The screen** shows none of this; the init.d link, the watcher and its log are for SSH users
  (`/tmp/homechooser-home.log`).

Tests: `ctest --test-dir build/service-host -R '^test-watcher'` runs the C policy and
child-transport/runtime tests under qemu-arm with firmware libraries and a fake transport. It
covers record merging, backoff, per-version restore, package removal, notifications, preload
retry/settle, foreground safety, the 10+ last-input launch (L01–L07) and the previous-launcher
hold and close on every release (P01–P14).
`service/scripts/tests/test-homechooser.sh` verifies boot enqueue, legacy staging and cached fallback.

### The last input after a boot, webOS 10 and later

With an overlay launcher the override's `idleApp` is `{"support": false}`, so SAM brings back the
last input when the foreground empties. After a boot SAM refuses that fallback until the first app
launch of the boot (`LAUNCH_LASTINPUT_ERR {"reason":"not_in_mode"}`, `first_app_launched: false`,
seen on 11.2.0). The overlay launcher, the boot's first app, then draws over an empty foreground and
nothing else comes up; when surface-manager later closes the idle overlay (`userTimeout`) the
fallback is refused again and the screensaver follows. Any app launch unlocks the fallback. So the
watcher makes that launch itself, the same one the [home gate](home-gate.md) makes on 6–9 for an
overlay launcher with nothing in front:

* **Once per boot.** At watcher start, unless `/tmp/homechooser-runtime/last-input` exists, a check
  is scheduled `input_settle_ms` (20 s) later. Whatever it decides (launched, not needed, an app in
  front, no answer) is written to that marker before anything is launched. `/tmp` is tmpfs, so the
  marker is gone after a reboot; a watcher restart, a choice change or an app-list event later in the
  same boot launch nothing. A choice changed mid-session needs no launch: it is made in Home Chooser
  or from a launcher, which is an app launch, so SAM's fallback is already working. A check that
  falls inside the [previous-launcher hold](#the-previous-launcher) launches nothing for that reason.
* **Only for an overlay launcher** that is installed and is not LG Home (`getAppInfo`
  `defaultWindowType` `overlay`), on 10.x and later. A full-screen launcher, LG Home and releases
  before 10 get nothing (on 6–9 the gate does this job).
* **Only when nothing full-screen is in front.** `getForegroundAppInfo` must answer with an empty
  `appId` or the overlay launcher's own id. Any other app in front (Home Chooser, LG Home, an input,
  an app the TV or the owner opened) is left there. A foreground event naming such an app during the
  settle period ends the check the same way.
* **When SAM is ready.** If `getAppInfo` or `getForegroundAppInfo` does not answer, the check runs
  again every `preload_retry_ms` (10 s), up to `preload_tries` (12) times, then gives up for the boot
  (`did not answer`). The 20 s settle leaves the boot's own first app time to come up, and the launch
  still lands well inside the few minutes before surface-manager closes an idle overlay. The last
  check starts 130 s after the watcher does (20 s and eleven 10 s retries), plus whatever the
  unanswered calls took (5 s or 10 s bounds); with SAM answering, it is the first check, 20 s in.
* **Which input.** `luna://com.webos.service.eim/getLastInput`'s `lastSourceAppId`, by the rule the
  gate uses ([last-input.c](../service/src/last-input.c)): Live TV (`com.webos.app.livetv`) when eim
  does not answer or refuses, or the id is empty, invalid or a stock Home. The fallback is logged.
* **The launch.** `applicationManager/launch {"id": <input>}` as `com.webos.applicationManager`, with
  a 5 s bound. A refusal or no reply is logged and never retried.
* **What the owner sees.** Surface-manager closes the overlay as the input comes up, so a boot ends
  on the last input (Live TV or HDMI) without the launcher on screen; Home opens the launcher over
  it. That is what a boot shows on 6–9 too.

The log lines start with `last input:`, for example `last input: launched com.webos.app.livetv over
an empty foreground: {"returnValue":true}`, `last input: launch of <id> failed: no reply; not retried
this boot`, `last input: <id> in front; nothing launched this boot` or `last input: eim did not
answer, using com.webos.app.livetv`.

The [LG Home close](#the-stock-home-is-not-preloaded) is unaffected and still fires at most once per boot or choice: for an overlay
it waits for the overlay's own launch over a nonstock full-screen app, which the last input now
provides. This launch never names LG Home or the launcher, and the close never names the input.

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
Unsupported Application Type`). The same look is repeated every 5 min in case
the subscription missed something, and once at start. Home Chooser counts as gone when `getAppInfo`
says so or its app directory is missing. An app counts as gone only if it is still gone 15 s later, because an app update temporarily removes its directory and should not trigger a restore. A `getAppInfo` that does not answer changes nothing. At a watcher start
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
| 6.x to 9.x | Set a stock launcher in the record, keeping other entries, then `homechooser-homekey refresh stock` restores the exact stock keyFilters array with `volatile:true` and the home gate is unmounted | LG Home opens immediately; verified readback precedes deletion of unused generated JS |
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
retried. The notification can be turned off at build time (`notify` in `hc_watch_config()`).

On 10.x and later clearing `home` is the `"home": null` write of `removeHomeFromRecord()` (see
[Persistence](#persistence)); the watcher reads the record back and logs when `home` is still set.
On 5–9 the record names a stock launcher. The stock 5.x handler uses that record directly;
on 6–9 the stock filter is restored separately and the record keeps the watcher/SAM policy consistent.

### Home Chooser is uninstalled

The app's files go with it, so the watcher cannot run anything from the app directory. At start it
stages `homechooser-daemon`, `homechooser-access` and `lib/common.sh` into
`/tmp/homechooser-runtime/` by rename. Its mapped executable continues after package unlink,
and its native Home-key code restores configd without spawning a removed binary. When Home Chooser is
gone it does, in order:

1. Removes launcher access and reverts the platform record/profile as above.
2. On 6–9, restores the exact stock filter array volatile and verifies its readback, then
   unmounts the [home gate](home-gate.md#mount-lifecycle-daemon). Failure keeps the source files
   and state available for retry (exit 1, unit restart).
3. Removes the choice, flag, unused generated versions, saved stock list and init.d link. Legacy
   watcher restore copies and old cache markers are cleaned up too.
4. Shows “Home Chooser was removed. The Home button opens LG Home again.” through a best-effort
   toast using the fixed privileged Luna identity, then exits successfully.

No GUI restart is required. Runtime copies on all releases provide crash/restart recovery.

Every step tests before it writes, so a second run (a unit restart half way, or both apps gone)
changes nothing: with the record's `home` already the stock id (5.x to 9.x) or unset (10.x and later: null, empty or missing), no write
is made. The unit's command is a small wrapper that runs the app's copy of the watcher, else the
runtime copy, else exits 0, so a watcher that crashes and is restarted after the app is gone still
finishes the restore, from `/tmp`, instead of failing to find its executable every 5 s. The log
(`/tmp/homechooser-home.log`) and the runtime copy stay in `/tmp` until reboot.

Both removed, in either order and at any spacing while the watcher runs, ends at the same clean
state (no choice file, flag or `init.d` link, the record `home` unset, the override gone, the
unit stopped, the stock home's preload policy enabled): the watcher looks at Home Chooser first in
every check and again after the chosen app's confirmation wait, so a Home Chooser removed during that
wait is found whether or not its own `listApps` message arrives. A chosen app confirmed gone is
reverted before that second look, so when the owner removes the launcher and then Home Chooser seconds
later, the record's `home` (on 10.x and later also the profile override and `idleApp`; on 6–9 the
keyFilters list, and the home gate is unmounted) are stock about 15 s after the launcher's removal, as
when the launcher alone is removed; they never name the removed launcher while Home
Chooser's own confirmation runs. The cleanup and the toast follow that confirmation, about 15 s per
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

* The ids looked at are the record's `home` and, on 10.x and later, the `categories.home` and the
  `idleApp.appId` (unless `idleApp.support` is false) of
  `/var/webos-profile/override/org.webosbrew.homechooser.json`. Before 10.x the override file is not
  read. `null`, `""` and a missing key are no id. Each distinct id is confirmed once.
* An id counts as not installed only if `getAppInfo` answers `returnValue:false` twice,
  15 s apart (`confirm_ms`), and its `appinfo.json` is in none of the install
  directories above. `com.webos.app.home` and `com.webos.app.fullhome` are never acted on, and an
  id `getAppInfo` does not answer for is left as it is (logged `getAppInfo did not answer`).
* An id that is installed is left alone even though no choice is stored: someone may have chosen it
  by other means, and Home Chooser only repairs a name that cannot work.
* A gone id is reverted the way the removal of a chosen app is, without the notification (it is not
  a removal the user just made): 5.x to 9.x write `home` as the stock launcher by the merged write
  (`com.webos.app.fullhome` if installed, else `com.webos.app.home`); 6.x to 9.x also run
  `homechooser-homekey refresh stock`; 10.x and later remove the override and rescan when the
  override names the gone id (as `home` or as `idleApp`), and write `home` as `null` when the record does. On 10.x the two are
  separate: a stale override with a record naming an installed app removes only the override, and
  the other way round.
* The record is read again right before the write, and nothing is written if it changed during the
  confirmation (`home changed meanwhile`). If a choice is stored during the confirmation (the user
  picked a launcher), nothing is repaired and the same process goes on as the ordinary watcher of
  that choice.
* With a stored choice none of this applies: the choice is enforced, and a record or override
  naming something else is overwritten by it (the [Persistence](#persistence) rules).

On 6–9 the native boot role can perform this check even without a choice. Legacy installed
boot entries start it when a choice exists; explicit `watch` and cached uninstall recovery start
it regardless of a stored choice.

## The stock home is not preloaded

LG's stock home, `com.webos.app.home`, is kept resident by the preload manager (and on 5.x to 9.x
launched at boot by `snapshot-boot-components.service`), which costs 70 to 105 MB while a third-party
home is in use, and closing it by hand does not last because it is preloaded again. While a third-party
home is chosen the watcher (webOS 6 and later) runs, as root:

```
luna-send -n 1 luna://com.webos.service.preloadmanager/setPreloadPolicy '{"application":{"id":"com.webos.app.home","isEnabled":false}}'
```

It sets the policy back to `"isEnabled":true` when it stops applying. Never `"permanent"`: the
preload manager keeps the policy in memory, nothing is written, and after a reboot the stock policy
is back until the watcher re-applies it. The memory manager is not touched. This is the one runtime
policy call Home Chooser makes on another service; it persists nothing.

What happens to a stock home that is already running depends on the release:

| Release | Stock home while a launcher is chosen |
| --- | --- |
| 5.x | Left alone: no preload call, no close. The visible home, `com.webos.app.fullhome`, stays loaded until the next restart |
| 6.x to 9.x | Replaced by the [home gate](home-gate.md); the watcher closes the real stock Home once the gate is mounted, after Home Chooser left the screen when the change was made there ([The previous launcher](#the-previous-launcher)) |
| 10.x, 11.x, 12.x | Preload off, closed once per boot and per choice after the chosen launcher is safely in front; the override's `idleApp` keeps SAM from starting it again. 10.x starts it at boot, so it is closed again after each boot. Under an overlay launcher the watcher opens the last input once after boot ([above](#the-last-input-after-a-boot-webos-10-and-later)), which is the full-screen app the close waits for |

* **When.** The watcher keeps one state, off or on, and calls only when it changes: once at start
  (so once per boot, and once more after a restart of the unit), when the choice changes between a
  third-party home and a stock one (`com.webos.app.home` or `com.webos.app.fullhome` in the choice
  file), when the chosen app is found uninstalled (on) and when it returns (off), and at every exit
  with status 0: the choice file is gone or unusable, the unit is stopped (Restore to stock runs
  `systemctl stop homechooser-home` first, so the watcher gets SIGTERM and sets the policy back
  before the choice file is removed), and the Home Chooser uninstall path, which runs from the
  `/tmp` copy. A restart after a failure (exit 1) leaves the policy as it is; the new instance
  applies it again.
* **At boot.** The preload manager may not answer when the boot entry starts. The policy call is
  retried every 10 s (`preload_retry_ms`), up to 12 times (`preload_tries`), until it answers
  `returnValue:true`. A 45 s settle delay (`preload_settle_ms`) then passes before a 10+ close is
  eligible.
* **Best effort.** Each call has a 5 s bound and its reply goes to `/tmp/homechooser-home.log`. A
  refusal (`Cannot find com.webos.app.home` when the manager does not list the app, or a denied
  call) or a hang changes nothing else: the home switch, the restore and the uninstall cleanup go
  on. The defaults are in `hc_watch_config()` ([watcher/core.c](../service/src/watcher/core.c)).
* **webOS 5 is left alone.** There the Home key starts `com.webos.app.fullhome` as an overlay and
  `com.webos.app.home` is not the visible home; the preload lists of 5.6.1 name both homes, and the
  5.x restore path hands the Home key either of them, so a policy change would carry risk. A
  `fullhome` that was running when another launcher is chosen stays loaded (about 45 MB) until the
  next restart; when the last app closes, webOS 5 goes back to Live TV, not to the home, so nothing
  starts it again.
* **Why a plain close does not work without an idle app of our own.** With the stock profile, `sam`
  relaunches the stock home as the idle/last-input app whenever the foreground empties:
  `closeByAppId` is followed by `APP_CLOSED`,
  `LAUNCH_LASTAPP {"status":"run_to_launch_last_app"} on_foreground_null` and
  `NL_APP_LAUNCH_BEGIN {"app_id":"com.webos.app.home","caller_app_id":"last_input_handler","mode":"last_input"}`
  within about 2 s, with `getPreloadPolicy` still reading `isEnabled:false`, so it is not the preload
  manager. A close frees nothing and only makes it restart. On 6–9 the home gate answers that
  relaunch instead of the stock binary. On 10.x and later the app `sam` launches there is the system
  profile's `idleApp`, which the override sets to the launcher (or turns off for an overlay
  launcher, so the last input comes back), so once LG Home is closed nothing starts it again until
  the next boot. The app never closes any stock home during selection on any release.
* **10.x and later: close after the launcher is in front.** The watcher subscribes to SAM's
  `getForegroundAppInfo` and `getAppLifeEvents`. A normal launcher qualifies when SAM reports it
  as foreground. An overlay qualifies only on its own launch event and only if SAM reports a
  nonstock full-screen card beneath it. With LG Home or a blank foreground beneath an overlay,
  the close is skipped. The watcher makes at most one close attempt per boot or choice change, and
  none during the [previous-launcher hold](#the-previous-launcher).
  If SAM relaunches LG Home, it logs that event and does not close it again in that boot.

  The close has to wait because of the idle fallback: the foreground must not empty because of
  it, and closing LG Home beneath an overlay also closes the overlay. Closing it during selection would let the compositor close Home Chooser's own panel
  before its completion toast, which is why the hold also covers it. With a nonstock full-screen app in front, or beneath the overlay,
  the close sticks.

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
  profile rescanned; the profile's `home` is the fallback, and its `idleApp` is the stock one again.

The custom home is not closed here: it goes to the `previous` file and the watcher, started for
it, closes it once Home Chooser is off screen and then exits ([The previous
launcher](#the-previous-launcher)). A stock home is never closed.

## Step log

Each operation returns its steps: the Luna call and payload, file write or removal, or command,
with the reply or the error. The screen does not show them; the app writes each one to
`console.log` with an `[HomeChooser]` prefix (web inspector, device log), so a failure keeps the
service's own text. An absent record (`There is no matched result from DB`) is treated as "not
set", not as a failure.


## Build and test

The app build, the build variables and the version source are in [build.md](build.md).
