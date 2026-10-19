# The settings app

`app/` is an Enact (Moonstone) web app, `org.webosbrew.openhouse`. It lists the installed apps that
declare `appCategory: ["home"]`, shows the current choice, and writes the choice for the running
release. The platform facts behind every write are in the
[study](research/home-key-and-home-app.md); this page says which write is made where.

## Using it

One screen, built for the remote (5-way, OK, Back); every action is reachable without a pointer.
Open House is in English only.

* **A side panel.** Like the stock Settings app (`com.palm.app.settings`: `"type": "web"`,
  `"defaultWindowType": "overlay"`, `"transparent": true`), Open House is a transparent overlay
  window with an opaque panel, 800 px wide at 1080p, docked at the right edge and full height. The
  list, the dialogs, the spinner, the toast and the problem messages all draw inside the panel;
  the rest of the screen stays clear and shows what is underneath.

* **The list.** Title "Open House", subtitle "Find your TV a new home". The first row is "LG Home
  (stock)"; the installed launchers (`appCategory: ["home"]`) follow, each with its icon and name.
  A check mark sits on the current home and the focus starts on it. Every row has the same height,
  icon box (one fixed size, before and after the icon loads) and reserved check slot; focus only
  changes colour and long names are truncated. App ids are never shown. If no launcher is installed, a line explains
  what a launcher is and that LG Home stays in use. If the chosen launcher was uninstalled, a line
  under the list says "The Home app you chose was removed; using LG Home."
* **Own window group, so keys arrive.** `appinfo.json` has a `windowGroup` that makes Open House the owner of its own group (`name: org.webosbrew.openhouse`, `owner: true`, `ownerInfo: {allowAnonymous: false, layers: [{name: layer1, z: 1}]}`, the shape of the stock web owners `com.webos.app.miracast` and `com.webos.app.appcasting`). On 6.6.0 the compositor sets the Wayland keyboard focus explicitly only for a surface that is part of a window group; a plain overlay depends on the view's `takeFocus()` and sometimes never received keys (surface-manager logged `Focused none-group nor grabbed item` and dropped them). An overlay that owns a group is shown by the compositor's overlay group view (`StarfishOverlayGroupView`, which takes focus when the surface appears) and gets keys the group way. The layer is declared only because every stock owner declares one; no client attaches to it. Research: `scratch/notes/floating-focus.md`.
* **No launch animation.** `appinfo.json` sets `noSplashOnLaunch: true` and `spinnerOnLaunch: false`, as the stock Settings app and many stock apps do. The 6.6.0 web app manager knows no other appinfo key for launch animation (only `noSplash` and `disableBackHistoryAPI` appear in its strings).
* **No close button.** The panel header has no close button; Back is the way to close (a dialog first, then the panel). Focus never leaves the list: it returns to the row that opened a dialog when the dialog closes, and moves to the new current row when a change finishes.
* **Choosing.** OK on the row that is already the current home changes nothing and opens no dialog; focus stays on it and a toast says "<name> is already your Home." ("LG Home is already your Home." for the stock row). OK on any other row asks "Use <name> as Home?". Confirming blocks input behind a spinner
  while the change is made, then a toast says "Done. Press Home to try it." On failure the toast is
  one plain sentence ("Couldn't set the Home app. The Open House service didn't respond."); the details
  are in the console log. Choosing "LG Home (stock)" runs the restore. The panel stays open after
  a success; Back closes it.
* **webOS 6 to 9.** The key filter backport is not shown as a feature. If it is not mounted when a
  launcher is chosen, the confirmation adds that the screen will blink once while the TV loads
  the change, and confirming applies the choice first and installs the backport last (the restart is the last step, so nothing of the choice depends on code that runs after it). That restart may
  close the panel. Before it, the app writes a small marker to `localStorage` (`openhouse.pending`,
  the id being chosen); on the next launch it reads the actual current home, shows the result toast
  once ("Done. Press Home to try it." if the home is the one chosen, otherwise "Couldn't set the Home
  app.") and clears the marker. A run that finishes in the same window clears the marker itself. Restoring LG Home
  removes the backport after the restore. If no patch set matches the firmware (hash mismatch),
  the dialog says the Home button can't be changed on this TV's software and applies nothing.
* **Footer.** On webOS 6 to 9 only, one fixed line at the bottom of the panel (not focusable): "Home key patch: active", "Home key patch: not active", or "Home key patch: not available for this TV's software" when no patch set matches the firmware. Nothing on 5.x and 10.x or later, or when root is unavailable.
* **Before uninstalling Open House, choose LG Home.** Uninstalling while a launcher is the Home app leaves the Home key pointing at a missing app (see the failure table under Persistence).
* **Problems (no root).** Homebrew Channel missing or not running, refusing `elevateService` (webOS 26 and later:
  "Your Homebrew Channel doesn't let apps elevate Open House's service yet. Update Homebrew Channel; Open House
  needs it."), or the service not coming up as root ("Open House's service did not start as root"): the panel keeps its layout. A plain one-line reason is at the
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
and it does not run shell commands. It calls Open House's own native service,
`org.webosbrew.openhouse.service`, which runs as root and makes the privileged calls itself, so the
settings service whitelist and the bus permissions do not apply (see
[Permissions](research/home-key-and-home-app.md#permissions)). The service offers a fixed set of
narrow operations and answers only this app; see [the service](service.md) for the methods, the
allow-lists and the logs.

At startup the app makes sure the service is root (`[OpenHouse]` lines in the console log):
`status`; if the service is not root, Homebrew Channel's
`luna://org.webosbrew.hbchannel.service/elevateService {"id":"org.webosbrew.openhouse.service"}`
(which rewrites the service's launch line so it is no longer jailed and runs as root); `quit`, so
the next call starts the elevated binary; `status` again, retried a few times. Every step has a
timeout. The failures map to the problem screens: Homebrew Channel absent (`unreachable`),
`elevateService` refused with `Denied method call` (`denied`, webOS 26 and later with the current
Homebrew Channel), the service still not root after elevation (`notRoot`), anything else
(`failed`). Files are written by the service (`writeFile`, text in a JSON string), so their
content never meets a shell. The shell scripts (`backport/openhouse`, the watcher, the key filter
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
* The profile override is `/var/webos-profile/override/org.webosbrew.openhouse.json`, holding the
  current `defaultApps` profile with `categories.home` set to the chosen id, the other keys and
  categories kept and the `_`-prefixed keys dropped. 10.3.1 replaces the second-level object
  wholesale, so the full `categories` has to be there. The compositor's home id is the profile's,
  not the record's, which is why the override is needed for the Home key to behave on these
  releases ([10.3.1 and 11.2.0](research/home-key-and-home-app.md#1031-and-1120-1)). After the
  rescan the app reads `getProfiles` again and reports an error if `categories.home` is not the
  chosen id.
* The previous home is the record's `home`, else the profile's, else `com.webos.app.home`. It is
  closed with `closeByAppId {"forceMode": true}`, as the 5.6.1 Home Style pane does. A failure
  there is shown as a warning.
* On 6.x to 8.x, `sam` does not see the first write that creates `defaultApps` until it restarts
  ([How `sam` resolves `launchDefaultApp`](research/home-key-and-home-app.md#how-sam-resolves-launchdefaultapp)).
  `systemctl restart sam` is the last step.
* On 6.x to 9.x the stock key filter launches `com.webos.app.home` by id, so the Home key follows
  the choice only with the [key filter backport](key-filter-backport.md) installed. The screen
  installs it as part of choosing a launcher when it is not mounted, and removes it again when
  LG Home is restored; there are no separate Install and Remove buttons.

Before these writes `apply` stores the choice, and after them it makes sure the boot entry and the
watcher are in place; see [Persistence](#persistence). The sequence is: read the state, write
`/var/lib/webosbrew/openhouse/home`, the platform writes, link the init.d entry and start the
watcher, close the previous home, and `systemctl restart sam` last if it applies.

## Persistence

On 11.2.0 the record's `home` can be lost at any boot: `accountmanager` calls `setDefaultApp` for
another category 12 to 17 s into every boot and, racing `sam`, the write can drop the other entries
([study](research/home-key-and-home-app.md#results-2026-10-19)). A write made once is not enough, so
Open House keeps its own copy of the choice and enforces it.

* **The stored choice** is `/var/lib/webosbrew/openhouse/home`, a plain text file holding the app
  id and nothing else, written as root by the app. An absent file means stock: nothing is
  enforced. Every reader (the watcher, the boot entry, the key filter worker) trims spaces, tabs,
  CR and LF, treats an empty result as no choice, and accepts only letters, digits, `.`, `-` and
  `_`; anything else is logged and ignored, never put into a luna payload or into QML.
* **The boot entry** is `/var/lib/webosbrew/init.d/openhouse`, a symlink (never a copy) to
  `backport/openhouse` in the installed app, which the app creates (`ln -sf`; a regular file from an
  earlier install is replaced, and the earlier `openhouse-keyfilter` entry is removed). Homebrew
  Channel's `run-parts` runs it as root once per boot. The package keeps the scripts executable
  (`ares-package` keeps mode 755, checked in the ipk), and `apply` runs `chmod 755` on them
  anyway. The script finds its files next to its real path (`readlink -f`, else `readlink`, else the
  fixed app path) and exits 0 without output when the app directory is gone (the app was
  uninstalled and the link dangles; the link then does nothing and `apply` or an uninstall of the
  link can clean it up; see [When apps are removed](#when-apps-are-removed)). It does two things: on webOS 6 to 9, when the flag file
  `/var/lib/webosbrew/openhouse/keyfilter` exists, it runs `openhouse-keyfilter apply` (the
  [key filter backport](key-filter-backport.md)); and if the choice file exists it starts the watcher.
  `openhouse watch` does only the second, which is what the app runs after a choice.
* **The watcher** is `backport/openhouse-home-watch`, started as a transient unit,
  `systemd-run --unit=openhouse-home --property=Restart=on-failure --property=RestartSec=5 /bin/sh -c
  '<wrapper>' openhouse-home-watch <app>/backport/openhouse-home-watch /tmp/openhouse-runtime/openhouse-home-watch`,
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
    `/var/webos-profile/override/org.webosbrew.openhouse.json` names the choice as
    `categories.home`, and rewrites it from `getProfiles` (same rules as the app) with a
    `debug/rescan` if not.
  * It also watches the apps it depends on; see [When apps are removed](#when-apps-are-removed).
    It subscribes to `applicationManager/listApps {"subscribe":true}` as a trigger only, checks the
    chosen app and `org.webosbrew.openhouse` with `getAppInfo` on every message and every 5 min,
    and runs from a copy of itself in `/tmp/openhouse-runtime/`.
  * It logs each action with a timestamp to `/tmp/openhouse-home.log` (trimmed to its last 200
    lines above 64 KB), exits 0 when the choice file is gone or unusable and 1 when the
    subscription ends.
* **Restoring stock** stops the watcher (`systemctl stop openhouse-home`), removes the choice file,
  then does the platform restore. On 6.x to 9.x it also runs `openhouse-keyfilter refresh`, which
  clears the choice from the mounted `Settings.qml` (restarting the compositor if it changed).
  Clearing `home` is one function in the app, `removeHomeFromRecord()`, and `merged_write ""` in the
  watcher: the record is written with `"home": null` and the other entries kept. Sub-keys of
  `general/defaultApps` can never be removed (a write that leaves `home` out only looks like a
  replace until the settings service restarts; `deleteSystemSettings` and `resetSystemSettings`
  are no-ops), but a stored `null` reads as unset and `launchDefaultApp {"category":"home"}`
  launches the profile's stock home, live and after a restart
  ([study](research/home-key-and-home-app.md)). Everywhere the record's `home` is read, `null`,
  `""` and a missing key mean "not set": with a stored choice that is a mismatch the watcher
  repairs, without one there is nothing to do.
* **The screen** shows none of this; the init.d link, the watcher and its log are for SSH users
  (`/tmp/openhouse-home.log`).

Tests: `backport/test-openhouse-home-watch.sh` (a stub `luna-send` plays a stream of replies:
present, missing, different, `Unknown method`, pretty-printed, string records, a storm, the choice
file in each form, the override on 10.x; a fake clock and sleep check the rate limiting; markers in
the stream install and uninstall apps and wait for the watcher's app check, so the removal cases
are deterministic: the chosen app removed and back on 5, 6 to 9 and 10+, Open House removed with
the full restore, both in either order, a transient absence, a silent `getAppInfo`, the periodic
check, the restore from the runtime copy after the app directory is deleted),
`backport/test-openhouse.sh` (the boot entry with stubbed `systemd-run` and `systemctl`: the 5.x,
6 to 9 and 10+ paths, the flag, the symlink, an app directory that is gone, the unit's wrapper),
`backport/test-openhouse-keyfilter.sh` and `backport/test-real-roundtrip.sh` (see the
[backport](key-filter-backport.md#deployment)), run as `sh` and `busybox sh`, and the Jest specs
for `apply`, `restoreStock`, the status parsing and the missing-choice message.

## When apps are removed

The stock launcher has to keep working when the chosen app is uninstalled, when Open House is
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
look is `luna://com.webos.applicationManager/getAppInfo {"id":"..."}` for `org.webosbrew.openhouse`
and for the chosen app; an uninstalled id answers `returnValue:false` (`Invalid appId specified OR
Unsupported Application Type`). The same look is repeated every 5 min (`OPENHOUSE_RECHECK`) in case
the subscription missed something, and once at start. Open House counts as gone when `getAppInfo`
says so or its app directory is missing. An app counts as gone only if it is still gone 15 s later
(`OPENHOUSE_CONFIRM_DELAY`), because updating an app removes it first and a revert restarts the
compositor on webOS 6 to 9. A `getAppInfo` that does not answer changes nothing.

### The chosen app is uninstalled

The platform goes back to stock, the choice file stays (it is the user's choice), the watcher logs
it and stops writing. When the app is installed again it applies the choice again, with the same
writes as a new choice. A new choice made while the old app is missing replaces it at once.

| Release | Put back to stock | Home key while the app is missing |
| --- | --- | --- |
| 5.x | `home` = `com.webos.app.fullhome` if `getAppInfo` finds it, else `com.webos.app.home` (as `restoreStock`); written only if `home` is not that already | stock launcher |
| 6.x to 9.x | `home` written as `null` by the merged `setSystemSettings` write (other entries kept); `openhouse-keyfilter refresh stock` empties the id baked into the mounted `Settings.qml` | the backport's `getHomeAppId()` has no record `home` and no baked id, so the stock id; without the backport the stock key filter never used the record |
| 10.x and later | override file removed, `debug/rescan`, then `home` written as `null` in the record | the profile's `home`, which is the stock launcher |

On return: 5.x and 6.x to 8.x write the merged record with `home`; 9.x and later `setDefaultApp`
(the merged write on `Unknown method`); 10.x and later also rebuild the override from
`getProfiles`; 6.x to 9.x run `openhouse-keyfilter refresh`, which bakes the id back in. The
screen says "The Home app you chose was removed; using LG Home." under the list when the stored choice is not
among the installed apps that declare `appCategory: ["home"]`.

Clearing `home` is the `"home": null` write of `removeHomeFromRecord()` (see
[Persistence](#persistence)); the watcher reads the record back and logs when `home` is still set.
The key filter backport's `Settings.qml` already takes only a non-empty string as the record's
`home`, so `null` falls through to the baked id and then the stock launcher.

### Open House is uninstalled

The app's files go with it, so the watcher cannot run anything from the app directory. At start it
copies itself, `openhouse-keyfilter` and every release's `manifest.json` into
`/tmp/openhouse-runtime/` (replacing each file by rename) and runs from there. When Open House is
gone it does, in order:

1. the platform revert of the table above (webOS 5: `home` = stock launcher; 6 to 9 and 10 and
   later: the same steps minus the key filter refresh);
2. removes the choice file (and its directory if empty) and the key filter flag file;
3. removes the `init.d/openhouse` symlink (and the earlier `openhouse-keyfilter` link), only if it
   is a symlink;
4. on webOS 6 to 9, `openhouse-keyfilter stop` from the runtime copy: it unmounts the bind mounts
   it finds in the manifests and restarts the compositor, and does nothing if nothing is mounted;
5. logs `stock Home restored, exiting` and exits 0, so `Restart=on-failure` leaves the unit
   stopped.

Every step tests before it writes, so a second run (a unit restart half way, or both apps gone)
changes nothing: with the record's `home` already unset (null, empty or missing), "nothing to remove" is logged and no write
is made. The unit's command is a small wrapper that runs the app's copy of the watcher, else the
runtime copy, else exits 0, so a watcher that crashes and is restarted after the app is gone still
finishes the restore, from `/tmp`, instead of failing to find its script every 5 s. The log
(`/tmp/openhouse-home.log`) and the runtime copy stay in `/tmp` until reboot.

Both removed: Open House first (or both at once, which is checked in that order) is the full
restore, after which the chosen app's removal finds nothing of ours. The chosen app first is the
revert above; the later removal of Open House repeats it harmlessly and cleans up. So B followed by
A, and A followed by B, end at the same stock state, and a removal of the chosen app after Open
House left the platform alone breaks nothing.

### What is left when the watcher is not running

If either app is removed while the watcher is not running, nothing undoes the writes, because the
init.d link of an uninstalled Open House dangles (`run-parts` runs nothing) and the unit's wrapper
has nothing to run. This happens when the watcher was stopped by hand or never started
(`systemd-run` failed), when it died and has not been restarted yet (the 5 s `RestartSec`, and
after a reboot until the boot entry has run), or when Open House is removed during that window. The
state it leaves:

| Release | Left behind | Effect |
| --- | --- | --- |
| 5.x | record `home` names the missing app | the key filter launches it directly: the Home key launches nothing |
| 6.x to 9.x | record `home` names the missing app; the backport stays mounted until reboot | the stock key filter ignores the record, so after a reboot (the mounts go) the Home key is the stock one; until then the backport falls back to the stock launcher when the launch fails. Other `launchDefaultApp` callers would fail |
| 10.x and later | record `home` and the profile override name the missing app, both persistent | `launchDefaultApp` fails and does not fall back: the Home key launches nothing, across reboots |

Repair: install Open House again (the link in `init.d` is valid again, and the next boot or
opening the app starts the watcher, which sees the chosen app missing and reverts), or by hand on
10.x and later, as root: remove `/var/webos-profile/override/org.webosbrew.openhouse.json`,
`luna-send -n 1 luna://com.webos.service.systemprofile/debug/rescan '{}'`, and write the
`general/defaultApps` record again with `"home": null` (`setSystemSettings`, keeping the other
entries). On 5.x write `home` as `com.webos.app.home`. With the key filter backport enabled but no
choice stored the watcher is not started, so uninstalling Open House then leaves the flag file, a
dangling link and, until reboot, mounts that behave as stock.

What only a live test settles: that
`getAppInfo` of an uninstalled id answers `returnValue:false` on every release, and that `sam`
answers it at boot when the init.d entry runs; how `listApps` change messages arrive (only their
arrival is used) and that `timeout` accepts `timeout 10 <command>` in the images' busybox;
and the real uninstall path through Homebrew Channel and `ares-install --remove`, including that
an update of Open House does not look like an uninstall for 15 s.

## Restoring the stock home

* 5.x: both stock homes declare `appCategory: ["home"]` and the key filter launches the record's
  `home` directly, so a record without `home` could leave the Home key with nothing to launch.
  The record is written with `home` set to `com.webos.app.fullhome` if `listApps` shows it
  installed (the factory value on the studied 5.6.1), else `com.webos.app.home`, and the other
  entries are kept ([5.6.1](research/home-key-and-home-app.md#561-supported-through-the-default-app-record)).
* 6.x to 9.x: the record is written again with `"home": null` and the other entries kept
  (`removeHomeFromRecord()`, see [Persistence](#persistence)); `setDefaultApp` cannot
  remove an entry. `com.webos.app.home` is never written as `home` there:
  the stock home has no `appCategory` and `sam` refuses it. For that reason choosing
  `com.webos.app.home` in the candidate list runs this restore instead of `apply` on every
  release (elsewhere it is simply "stock"); other stock homes such as `seniorhome` stay ordinary
  choices.
* 10.x and later: the record is written with `"home": null` (same function), the override file is removed and the
  profile rescanned; the profile's `home` is the fallback.

The custom home is then closed; a stock home is never closed.

## Step log

Each operation returns its steps: the Luna call and payload, file write or removal, or command,
with the reply or the error. The screen does not show them; the app writes each one to
`console.log` with an `[OpenHouse]` prefix (web inspector, device log), so a failure keeps the
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
