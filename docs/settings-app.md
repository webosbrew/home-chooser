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
* **webOS 6 to 9.** The key filter backport is not shown as a feature, and nothing restarts the compositor
  or `sam` while the TV runs, so no step can close the panel. Choosing a launcher applies the choice
  first. If the patch is mounted and enabled, that is all: the patched QML reads `general/defaultApps`
  first, so the record decides live and switching launchers needs no install and no restart. Otherwise
  (not mounted, or mounted with the flag off after a restore that no restart has finished yet) the app
  then calls `backport.install()`, which writes the flag file and links the boot entry and runs nothing
  else; its result says `rebootNeeded` when the flag is on and nothing is mounted (the worker's
  `reboot=needed` status line). The patch is mounted by the boot entry on the next boot.
  See [Restart prompt](#restart-prompt). Restoring LG Home removes the flag and bakes the empty id into
  the mounted copy (`backport.remove()`, which runs `homechooser-keyfilter refresh` and never `stop`); a
  mounted patch stays until the next boot, so a restart prompt follows. If no patch set matches the
  firmware (hash mismatch), the dialog says the Home button can't be changed on this TV's software and
  applies nothing. The earlier `localStorage` marker (`homechooser.pending`) is no longer written; on
  startup the app still reads one left by an earlier version, judges it once against the actual home
  and the stored choice (a marker whose id differs from the stored choice is stale and dropped without a
  toast) and clears it.
* **Footer.** On webOS 6 to 9 only, one fixed line at the bottom of the panel (not focusable): "Home key patch: active", "Home key patch: not active", or "Home key patch: not available for this TV's software" when no patch set matches the firmware. Nothing on 5.x and 10.x or later, or when root is unavailable.
* **Before uninstalling Home Chooser, choose LG Home.** Uninstalling while a launcher is the Home app leaves the Home key pointing at a missing app (see the failure table under Persistence).
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
content never meets a shell. The shell scripts (`backport/homechooser`, the watcher, the key filter
worker) are unchanged; the service runs them with a fixed argv.

The release comes from `/etc/os-release`, read through the service (`VERSION_ID`, then `VERSION`, then `PRETTY_NAME`).

## What is written

| Release | Settings record `general/defaultApps` | System profile | After |
| --- | --- | --- | --- |
| 5.x | `setSystemSettings` with the existing entries and `home` | - | close previous home |
| 6.x to 8.x | same | - | close previous home; `systemctl restart sam` last if the record did not exist |
| 9.x | `applicationManager/setDefaultApp`; on `Unknown method`, the `setSystemSettings` write | - | close previous home |
| 10.x, 11.x | `setDefaultApp` as on 9.x | override file, `debug/rescan`, read back | close previous home |

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
* The previous home is the record's `home`, else the profile's, else `com.webos.app.home`. It is
  closed with `closeByAppId {"forceMode": true}`, as the 5.6.1 Home Style pane does, except that
  `com.webos.app.home` is not closed on 6.x to 10.x (`sam` relaunches it at once; see
  [The stock home is not preloaded](#the-stock-home-is-not-preloaded)). A failure
  there is shown as a warning.
* On 6.x to 8.x, `sam` does not see the first write that creates `defaultApps` until it restarts
  ([How `sam` resolves `launchDefaultApp`](research/home-key-and-home-app.md#how-sam-resolves-launchdefaultapp)).
  `systemctl restart sam` is the last step; when the key filter backport still has to be installed, a TV
  restart follows, which restarts `sam` as well, so the step is skipped (below).
* On 6.x to 9.x the stock key filter launches `com.webos.app.home` by id, so the Home key follows
  the choice only with the [key filter backport](key-filter-backport.md) installed. The screen
  enables it as part of choosing a launcher when it is not mounted and disables it again when
  LG Home is restored; there are no separate Install and Remove buttons. Enabling takes a TV restart to
  mount it, disabling takes one to unmount it.

Before these writes `apply` stores the choice, and after them it makes sure the boot entry and the
watcher are in place; see [Persistence](#persistence). The sequence is: read the state, write
`/var/lib/webosbrew/homechooser/home`, the platform writes, link the init.d entry and start the
watcher, close the previous home, and `systemctl restart sam` last if it applies.

### Why the sam restart is skipped on 6 to 9 when the patch is installed

The `sam` restart brings the stock home forward as the last-input app and closes the panel's window.
When the patch is not mounted yet, the choice is followed by a TV restart (the boot entry mounts the
patch), and that restart restarts `sam` as well, so the panel passes `apply(id, {deferSamRestart: true})`,
which skips the restart and returns `samRestartDeferred`. It passes it only then: with the patch
mounted and enabled, or when no restart will follow, the apply restarts `sam` itself when it has to, as
before. The panel never restarts the compositor: `backport.install()` writes the flag and links the entry
and runs no worker, so there is nothing that could close the panel between the choice and the install.

### Restart prompt

After `backport.install()` or `backport.remove()` reports `rebootNeeded`, the choose, restore and
generated patch flows ask for a TV restart. The reason is `apply` after an install and `remove` after a
restore or after disabling the generated patch. Only webOS 6 to 9 have a patch, so only they ever ask;
on 5.x and 10 and later `backport` is null, no step reports `rebootNeeded` and nothing changes.

The prompt is always the service's system alert, never a dialog of the app. Home Chooser is an overlay
window and surface-manager closes an overlay after about 90 to 120 s without a key press
(`userTimeout`, measured on 6.5.2 and 9.2.1), so a restart dialog inside the app is lost on an idle
user, while the alert belongs to the notification service and stays.

* The app calls `rebootPrompt {"reason"}` ([service](service.md)). The service raises the alert (title
  "Home Chooser", the message for the reason, buttons "Reboot" and "Later"); the Reboot button calls
  `rebootConfirmed` with a one-shot token that only the alert holds. If the alert is refused the service
  shows a toast with the message instead.
* The screen shows only its normal result toast, "Done. Restart the TV to finish." (no focus change, no
  spinner left). If `rebootPrompt` fails (nothing pending any more, or the notification service refuses
  both), the toast is "Restart the TV to finish."
* While a restart is pending (`status` says `reboot=needed`, read at startup and after every run) the
  list ends with a note, "The Home button changes after a restart." (`apply`: the flag file is set and
  nothing is mounted) or "LG Home is on the Home button now. A restart removes the patch." (`remove`:
  the patch is mounted and the flag file is gone), and a "Restart now" row. The row calls `rebootPrompt`
  again with the same reason and toasts "Check the restart prompt on the screen."; a refusal toasts
  "Restart the TV to finish." and re-reads the state. This is what a user gets after pressing Later,
  after the alert was closed by something else, or on opening Home Chooser later. Opening the screen does
  not raise the alert by itself: an alert out of nowhere on every launch would be a nag, and the row is
  one key press away.

The service re-checks that a restart is needed before it asks and again before it restarts the TV.

## Persistence

On 11.2.0 the record's `home` can be lost at any boot: `accountmanager` calls `setDefaultApp` for
another category 12 to 17 s into every boot and, racing `sam`, the write can drop the other entries
([study](research/home-key-and-home-app.md#results-2026-10-19)). A write made once is not enough, so
Home Chooser keeps its own copy of the choice and enforces it.

* **The stored choice** is `/var/lib/webosbrew/homechooser/home`, a plain text file holding the app
  id and nothing else, written as root by the app. An absent file means stock: nothing is
  enforced. Every reader (the watcher, the boot entry, the key filter worker) trims spaces, tabs,
  CR and LF, treats an empty result as no choice, and accepts only letters, digits, `.`, `-` and
  `_`; anything else is logged and ignored, never put into a luna payload or into QML.
* **The boot entry** is `/var/lib/webosbrew/init.d/homechooser`, a symlink (never a copy) to
  `backport/homechooser` in the installed app, which the app creates (`ln -sf`; a regular file from an
  earlier install is replaced, and the earlier `homechooser-keyfilter` entry is removed). Homebrew
  Channel's `run-parts` runs it as root once per boot. The package keeps the scripts executable
  (`ares-package` keeps mode 755, checked in the ipk), and `apply` runs `chmod 755` on them
  anyway. The script finds its files next to its real path (`readlink -f`, else `readlink`, else the
  fixed app path) and exits 0 without output when the app directory is gone (the app was
  uninstalled and the link dangles; the link then does nothing and `apply` or an uninstall of the
  link can clean it up; see [When apps are removed](#when-apps-are-removed)). It does two things: on webOS 6 to 9, when the flag file
  `/var/lib/webosbrew/homechooser/keyfilter` exists, it runs `homechooser-keyfilter apply` (the
  [key filter backport](key-filter-backport.md)); and if the choice file exists it starts the watcher.
  `homechooser watch` does only the second, and starts the watcher with or without a stored choice:
  the app runs it after a choice and each time it opens, so a reinstalled Home Chooser looks for a
  [leftover Home](#a-leftover-home) without waiting for a choice or a boot.
* **The watcher** is `backport/homechooser-home-watch`, started as a transient unit,
  `systemd-run --unit=homechooser-home --property=Restart=on-failure --property=RestartSec=5 /bin/sh -c
  '<wrapper>' homechooser-home-watch <app>/backport/homechooser-home-watch /tmp/homechooser-runtime/homechooser-home-watch`,
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
* **Restoring stock** stops the watcher (`systemctl stop homechooser-home`), removes the choice file,
  then does the platform restore. On 6.x to 9.x it also runs `homechooser-keyfilter refresh`, which
  clears the choice from the mounted `Settings.qml` copy (it does not restart the compositor: the
  running compositor keeps its loaded id until the next start, and the settings record is read first).
  On 5.x to 9.x the restore writes `home` as the stock launcher instead (`com.webos.app.fullhome` if
  `listApps` shows it, else `com.webos.app.home`), because the patched QML on 6.x to 9.x reads the
  record first and the running compositor would otherwise fall back to the id it loaded at start
  (the old launcher, until a reboot); stock firmware ignores the record for the Home key, so the
  explicit stock id may stay. On 10.x and later clearing `home` is one function in the app,
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

Tests: `backport/test-homechooser-home-watch.sh` (a stub `luna-send` plays a stream of replies:
present, missing, different, `Unknown method`, pretty-printed, string records, a storm, the choice
file in each form, the override on 10.x; a fake clock and sleep check the rate limiting; markers in
the stream install and uninstall apps and wait for the watcher's app check, so the removal cases
are deterministic: the chosen app removed and back on 5, 6 to 9 and 10+, Home Chooser removed with
the full restore, both in either order and one removed during the other's confirmation wait with its message delivered or lost, the alert and toast triggers (once, not on a transient absence, not at a start, not after a restore from the app), a transient absence, a silent `getAppInfo`, the periodic
check, the restore from the runtime copy after the app directory is deleted, the leftover Home
without a stored choice on 5, 6 to 9 and 10+ with what it leaves alone, and the start-time
files-on-disk guard),
`backport/test-homechooser.sh` (the boot entry with stubbed `systemd-run` and `systemctl`: the 5.x,
6 to 9 and 10+ paths, the flag, the symlink, an app directory that is gone, the unit's wrapper),
`backport/test-homechooser-keyfilter.sh` and `backport/test-real-roundtrip.sh` (see the
[backport](key-filter-backport.md#deployment)), run as `sh` and `busybox sh`, and the Jest specs
for `apply`, `restoreStock`, the status parsing and the missing-choice message.

## When apps are removed

The stock launcher has to keep working when the chosen app is uninstalled, when Home Chooser is
uninstalled, and when the second follows the first. Both writes the platform keeps (the `home`
entry, and on 10.x and later the profile override) name an app, and a name that is missing makes
things worse than no name: on 10.3.1 and 11.2.0 `launchDefaultApp` fails for a record entry that
names a missing app and does **not** fall back to the profile, and on 5.6.1 the key filter launches
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
(`HOMECHOOSER_CONFIRM_DELAY`), because updating an app removes it first and a revert restarts the
compositor on webOS 6 to 9. A `getAppInfo` that does not answer changes nothing. At a watcher start
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
| 6.x to 9.x | `home` = the stock launcher (`com.webos.app.fullhome` if `getAppInfo` finds it, else `com.webos.app.home`) by the merged `setSystemSettings` write (other entries kept; not null, which would let the running compositor fall back to its baked old id); written only if `home` is not that already; `homechooser-keyfilter refresh stock` empties the id baked into the mounted `Settings.qml` | the backport's `getHomeAppId()` reads the record first and gets the stock id, so LG Home opens at once; without the backport the stock key filter never used the record |
| 10.x and later | override file removed, `debug/rescan`, then `home` written as `null` in the record | the profile's `home`, which is the stock launcher |

On return: 5.x and 6.x to 8.x write the merged record with `home`; 9.x and later `setDefaultApp`
(the merged write on `Unknown method`); 10.x and later also rebuild the override from
`getProfiles`; 6.x to 9.x run `homechooser-keyfilter refresh`, which bakes the id back in. The
screen says "The Home app you chose was removed; using LG Home." under the list when the stored choice is not
among the installed apps that declare `appCategory: ["home"]`.

**The user is told once.** After the revert (no compositor restart on any release), the watcher shows an alert with one OK button:
`luna-send -n 1 luna://com.webos.notification/createAlert '{"sourceId":"org.webosbrew.homechooser","title":"Home Chooser","message":"<title> was removed, so the Home button opens LG Home again.","buttons":[{"label":"OK","focus":true}]}'`.
The text is the same on every release: on 6.x to 9.x the record already names the stock home, so the
running compositor (which reads the record before its baked id) opens LG Home at once, with
`launchHome`'s failure path as a second line of defence.
The title of an app that is gone cannot be looked up, so the watcher reads it from `getAppInfo`
(failing that `appinfo.json`) while the app is installed and keeps it per choice, like the window
type; a title it does not have, or one with a quote or backslash in it, is replaced by the app id.
The wording is the same on 5.x (the stock home there is `fullhome`). It is sent once per removal
event: the later checks find the app already marked missing and say nothing, and a return followed
by a new removal is a new event. It is not sent for a temporary absence (the confirmation delay
comes first), when the user restores stock from the app (the choice file is removed and the watcher
stopped before anything is missing), when the choice is changed to another app, or when a watcher
starts and finds the app already missing (a boot is not a new removal). The call is a plain root
`luna-send` without `-a`, because `-a <appid>` would make the caller that app and `createAlert` is
not in a public group, under a 5 s timeout; its reply is logged (`notification createAlert: ...`) and
nothing waits on it. A reply that says `returnValue:false` (a refusal) is logged
(`the alert was refused`) and the same message is shown as a toast; no reply is logged and not
retried. `HOMECHOOSER_NOTIFY=0` turns it off. What the notification service really does with the
call is not tested here; it was read from the firmware (5.6.1 to 11.2.0) and has not been run on a
device.

On 10.x and later clearing `home` is the `"home": null` write of `removeHomeFromRecord()` (see
[Persistence](#persistence)); the watcher reads the record back and logs when `home` is still set.
On 5.x to 9.x `home` is set to the stock id (the merged write), not cleared. The key filter
backport's `Settings.qml` takes only a non-empty string as the record's `home` and reads it before
the baked id, which is why null is not used on 6.x to 9.x.

### Home Chooser is uninstalled

The app's files go with it, so the watcher cannot run anything from the app directory. At start it
copies itself, `homechooser-keyfilter` and every release's `manifest.json` into
`/tmp/homechooser-runtime/` (replacing each file by rename) and runs from there. When Home Chooser is
gone it does, in order:

1. the platform revert of the table above (webOS 5 to 9: `home` = stock launcher; 10 and
   later: override removed, `home` null);
2. removes the choice file (and its directory if empty) and the key filter flag file;
3. removes the `init.d/homechooser` symlink (and the earlier `homechooser-keyfilter` link), only if it
   is a symlink;
4. on webOS 6 to 9, nothing is unmounted and nothing restarts: the bind mounts stay until the next
   boot (their sources are under `/tmp/homechooser-backport/stage`, not in the app directory, so they
   survive the uninstall), and at that boot nothing re-applies because the init.d link and the flag
   are gone;
5. shows a toast, once:
   `luna-send -n 1 luna://com.webos.notification/createToast '{"sourceId":"org.webosbrew.homechooser","noaction":true,"message":"Home Chooser was removed. The Home button opens LG Home again."}'`
   (the same text on every release; the record names the stock home on 6.x to 9.x, so no restart is needed for the Home button, only to drop the mounts)
   (plain root, no `-a`, no `iconUrl` because the app's icon is being deleted; 5 s timeout, reply
   logged, never blocks);
6. logs `stock Home restored, exiting` and exits 0, so `Restart=on-failure` leaves the unit
   stopped.

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

### What is left when the watcher is not running

If either app is removed while the watcher is not running, nothing undoes the writes, because the
init.d link of an uninstalled Home Chooser dangles (`run-parts` runs nothing) and the unit's wrapper
has nothing to run. This happens when the watcher was stopped by hand or never started
(`systemd-run` failed), when it died and has not been restarted yet (the 5 s `RestartSec`, and
after a reboot until the boot entry has run), or when Home Chooser is removed during that window. The
state it leaves:

| Release | Left behind | Effect |
| --- | --- | --- |
| 5.x | record `home` names the missing app | the key filter launches it directly: the Home key launches nothing |
| 6.x to 9.x | record `home` names the missing app; the backport stays mounted until reboot | the stock key filter ignores the record, so after a reboot (the mounts go) the Home key is the stock one; until then the backport falls back to the stock launcher when the launch fails. Other `launchDefaultApp` callers would fail. Repair: write `home` as the stock id |
| 10.x and later | record `home` and the profile override name the missing app, both persistent | `launchDefaultApp` fails and does not fall back: the Home key launches nothing, across reboots |

Repair: install Home Chooser again and open it once. Opening it starts the watcher: with the choice
file still there (the chosen app was removed while the watcher was down) it sees the chosen app
missing and reverts; without one (Home Chooser itself was removed and its restore could not write,
e.g. the luna bus was down) it makes the [leftover Home](#a-leftover-home) check. The link in
`init.d` of a Home Chooser removed without its watcher is valid again after the reinstall, so the
next boot does the first case too. Or by hand on
10.x and later, as root: remove `/var/webos-profile/override/org.webosbrew.homechooser.json`,
`luna-send -n 1 luna://com.webos.service.systemprofile/debug/rescan '{}'`, and write the
`general/defaultApps` record again with `"home": null` (`setSystemSettings`, keeping the other
entries). On 5.x to 9.x write `home` as `com.webos.app.home`. With the key filter backport enabled but no
choice stored the watcher is not started, so uninstalling Home Chooser then leaves the flag file, a
dangling link and, until reboot, mounts that behave as stock.

### A leftover Home

Home Chooser removed while its restore could not run or could not write (the watcher stopped, the
luna bus down during `restore_all`, which still removes the choice file, the flag and the link)
leaves the record's `home`, and on 10.x and later the override file, naming a launcher, and nothing
of Home Chooser's to say so. Once that launcher is gone too the Home key is broken (the table above).
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
  `homechooser-keyfilter refresh stock`; 10.x and later remove the override and rescan when the
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
luna-send -n 1 luna://com.webos.applicationManager/closeByAppId '{"id":"com.webos.app.home","forceMode":true}'
```

(the second only if the first answered `returnValue:true`, and not on 6.x to 10.x, for any launcher, see below), and back to `"isEnabled":true` when it
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
* **The close at boot.** On 5.x to 9.x `snapshot-boot-components.service` launches the stock home
  outside the preload manager, and Homebrew Channel's boot hook (`init.d`) runs at
  `minimal-boot-done`, which may be before or after that launch. The preload manager does not answer
  that early either: on 9.2.1, 7.x, 11.2.0 and 12.0.0 the first call at boot timed out. So at start
  the policy call runs in the background, retried every `HOMECHOOSER_PRELOAD_RETRY` seconds (10) up
  to `HOMECHOOSER_PRELOAD_TRIES` times (12) until it answers `returnValue:true`; then the boot is
  given `HOMECHOOSER_PRELOAD_SETTLE` seconds (45) and the stock home is force-closed, only if the
  policy is still off by then. Changes later on are made at once and close at once. The close of a
  stock home that is not running is a harmless `returnValue:false`. On 6.x to 10.x there is no close
  at all, at boot or later (next item).
* **Best effort.** Each call runs under `timeout 5` when the image has it (as the app checks do),
  its reply goes to `/tmp/homechooser-home.log`, and only the boot call is retried: a refusal
  (`Cannot find com.webos.app.home` when the manager does not list the app, or a denied call) or a
  hang costs at most 5 s per call and changes nothing else: the home switch, the restore and the
  uninstall cleanup go on. `HOMECHOOSER_PRELOAD=0` turns the feature off.
* **webOS 5 is left alone.** There the Home key starts `com.webos.app.fullhome` as an overlay and
  `com.webos.app.home` is not running, so there is nothing to free; the preload lists of 5.6.1 name
  both homes, and the 5.x restore path hands the key filter either of them, so a policy change would
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
  the next reboot (sam relaunches it as the last-input app)`. The same holds for the app: choosing a
  launcher does not close `com.webos.app.home` there (the previous home is closed only when it is
  not that one).

  So on 6.x to 10.x **the stock home can stay resident after a launcher is chosen, until the next
  reboot**. It is not a defect of the choice: the system puts it back at once whatever is done to it.
  After a reboot it is gone, because the watcher turns the stock home's preload off at every boot
  (in memory only, never `permanent`) and nothing closes and reopens it any more. On 6.x to 9.x the
  patch needs a reboot to be applied anyway, so the memory is freed by a step the user takes in any
  case. On 5.x nothing is done (below). On 11.x and 12.x the close does stick (11.2.0 and 12.0.0, with
  Dock too), so it is kept there.
* **Measured (2026-10-23, Beanjar VMs).** Root `luna-send` may call `getPreloadPolicy` and
  `setPreloadPolicy`; `getPreloadPolicy` answers `{"applications":[{"id":"com.webos.app.home",
  "isEnabled":...,...},...]}` (an empty list right after boot). On 9.2.1 with the policy disabled, a
  force-closed stock home (Homebrew Channel in front) stayed closed for 120 s and about 80 MB was
  freed; re-enabled, the preload manager launched it again about 8 s later; after a reboot the
  policy was back to enabled. End-to-end with `--reboot-check`: 11.2.0 (103 MB freed) and 12.0.0
  (71 MB) keep the stock home closed after the switch and after a reboot; 7.x, 8.3.0, 9.2.1 and 10.3.1 pass
  with the stock home left running (for every launcher since 2026-10-24, as above); 5.6.1 makes no preload call.

## Restoring the stock home

* 5.x: both stock homes declare `appCategory: ["home"]` and the key filter launches the record's
  `home` directly, so a record without `home` could leave the Home key with nothing to launch.
  The record is written with `home` set to `com.webos.app.fullhome` if `listApps` shows it
  installed (the factory value on the studied 5.6.1), else `com.webos.app.home`, and the other
  entries are kept ([5.6.1](research/home-key-and-home-app.md#561-supported-through-the-default-app-record)).
* 6.x to 9.x: like 5.x, the record is written with `home` set to the stock launcher
  (`com.webos.app.fullhome` if `listApps` shows it, else `com.webos.app.home`) and the other entries
  kept, through `setSystemSettings` (not null: the patched QML reads the record first, and the running
  compositor would fall back to the old launcher it loaded at start until a reboot). The stock id stays
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
| `OPENHOUSE_FIRMWARES`, `OPENHOUSE_HYPERVISOR` | `~/Projects/webos-firmwares`, `~/Projects/webos-hypervisor` | `backport/test-real-roundtrip.sh` and `backport/test-generated.sh`: the firmware roots they try (a root that is missing is skipped) |
| `OPENHOUSE_S23Z_ROOTFS` | under `OPENHOUSE_HYPERVISOR` | the same two scripts: the unpacked S23Z image |

`make -C service test` and the Jest specs need none of them.

The version has one source, `"version"` in `app/package.json`. `app/appinfo.json` (what
`ares-package` and the TV read) must carry the same: `npm run check-version`
(`app/scripts/check-version.js`) fails on a mismatch, `npm run package` runs it first, a Jest spec
checks it with the other tests, and `npm version <x.y.z>` rewrites `appinfo.json` through the
`version` script. The service binary gets it from `service/Makefile` (`VERSION`, read from
`package.json`, compiled in as `HC_VERSION`; `main.c` has no copy of its own and an empty version
fails the build), and reports it as `version` in `status` and `getInfo`. `services.json` has no
version. The ipk's control file is written by `ares-package`, which fills in
`Maintainer: N/A <nobody@example.com>` itself; that placeholder is left as it is.

Enact copies all of iLib's locale data (83 MB, a 15 MB package) and its CLI has no switch to
limit it for a non-isomorphic build, so `scripts/prune-ilib.js` runs between the build and
`ares-package`. It keeps the top-level data files and the `en`, `und` and `zoneinfo` locale
directories, drops the other locale directories and the character maps, and rewrites iLib's
manifest to the remaining files. The UI strings are English and come from the source and from
Moonstone's own resources, which are left alone (Moonstone's fonts, 1.3 MB, are kept). `scripts/copy-backport.js` then copies `backport/`
(the three scripts at mode 755, and each release's manifest and diffs) into `dist`; the package is
0.97 MB.

The build targets `chrome 68` (the browser of webOS 5) through the `browserslist` entry in
`app/package.json`. `backport/test-*.sh` hold the shell tests (see [Persistence](#persistence)). The Jest tests drive `apply` and `restoreStock` with a fake `rootLuna` for 5.6.1,
6.6.0, 8.3.0, 9.2.1, 10.3.1 and 11.2.0 and assert the payloads.
