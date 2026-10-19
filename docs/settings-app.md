# The settings app

`app/` is an Enact (Sandstone) web app, `org.webosbrew.openhouse`. It lists the installed apps that
declare `appCategory: ["home"]`, shows the current choice, and writes the choice for the running
release. The platform facts behind every write are in the
[study](research/home-key-and-home-app.md); this page says which write is made where.

## Root access

The app runs `luna-send -n 1 -f <uri> '<json>'` as root through Homebrew Channel's
`luna://org.webosbrew.hbchannel.service/exec`, so the settings service whitelist and the bus
permissions do not apply (see [Permissions](research/home-key-and-home-app.md#permissions)). Files are
written as root by piping base64 into `base64 -d`, which is a busybox applet on the images, so
the content (JSON with quotes and newlines) never meets the shell's quoting. If `exec` is
refused (`Denied method call "exec"` on webOS 26 and later with the current Homebrew Channel) or
Homebrew Channel is absent, the panel says so and does nothing else.

The release comes from `/etc/os-release` (`VERSION_ID`, then `VERSION`, then `PRETTY_NAME`).

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
  The panel warns beforehand that apps may close, and `systemctl restart sam` is the last step.
* On 6.x to 9.x the stock key filter launches `com.webos.app.home` by id, so the Home key follows
  the choice only with the [key filter backport](key-filter-backport.md) installed. The result
  carries `needsKeyFilterBackport`; the panel says so unless the backport is mounted, and offers
  Install and Remove for it with the same step log.

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
  link can clean it up). It does two things: on webOS 6 to 9, when the flag file
  `/var/lib/webosbrew/openhouse/keyfilter` exists, it runs `openhouse-keyfilter apply` (the
  [key filter backport](key-filter-backport.md)); and if the choice file exists it starts the watcher.
  `openhouse watch` does only the second, which is what the app runs after a choice.
* **The watcher** is `backport/openhouse-home-watch`, started as a transient unit,
  `systemd-run --unit=openhouse-home --property=Restart=on-failure --property=RestartSec=5 /bin/sh
  <app>/backport/openhouse-home-watch`. No unit file is written and no other service's unit,
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
  * It logs each action with a timestamp to `/tmp/openhouse-home.log` (trimmed to its last 200
    lines above 64 KB), exits 0 when the choice file is gone or unusable and 1 when the
    subscription ends.
* **Restoring stock** stops the watcher (`systemctl stop openhouse-home`), removes the choice file,
  then does the platform restore. On 6.x to 9.x it also runs `openhouse-keyfilter refresh`, which
  clears the choice from the mounted `Settings.qml` (restarting the compositor if it changed).
  Removing `home` from the record is one function in the app, `removeHomeFromRecord()`, which still
  writes the record without `home`; whether that removes it (a partial write probably merges) has
  to be confirmed on a TV.
* **The panel** shows the stored choice, whether the init.d link exists and points at this install
  (or is another install's, broken, or a regular file), whether the watcher is running, and the
  last lines of its log.

Tests: `backport/test-openhouse-home-watch.sh` (a stub `luna-send` plays a stream of replies:
present, missing, different, `Unknown method`, pretty-printed, string records, a storm, the choice
file in each form, the override on 10.x; a fake clock and sleep check the rate limiting),
`backport/test-openhouse.sh` (the boot entry with stubbed `systemd-run` and `systemctl`: the 5.x,
6 to 9 and 10+ paths, the flag, the symlink, an app directory that is gone), run as `sh` and
`busybox sh`, and the Jest specs for `apply`, `restoreStock` and the status parsing.

## Restoring the stock home

* 5.x: both stock homes declare `appCategory: ["home"]` and the key filter launches the record's
  `home` directly, so a record without `home` could leave the Home key with nothing to launch.
  The record is written with `home` set to `com.webos.app.fullhome` if `listApps` shows it
  installed (the factory value on the studied 5.6.1), else `com.webos.app.home`, and the other
  entries are kept ([5.6.1](research/home-key-and-home-app.md#561-supported-through-the-default-app-record)).
* 6.x to 9.x: the record is written again without `home` and with the other entries kept
  (`removeHomeFromRecord()`, unconfirmed, see [Persistence](#persistence)); `setDefaultApp` cannot
  remove an entry. `com.webos.app.home` is never written as `home` there:
  the stock home has no `appCategory` and `sam` refuses it.
* 10.x and later: the record is written without `home` (same function), the override file is removed and the
  profile rescanned; the profile's `home` is the fallback.

The custom home is then closed; a stock home is never closed.

## Step log

Each operation returns its steps: the Luna call and payload, file write or removal, or command,
with the reply or the error. The panel shows them, so a failure is visible with the service's own
text. An absent record (`There is no matched result from DB`) is treated as "not set", not as a
failure.

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
Sandstone's own resources, which are left alone. `scripts/copy-backport.js` then copies `backport/`
(the three scripts at mode 755, and each release's manifest and diffs) into `dist`; the package is
1.0 MB.

The build targets `chrome 68` (the browser of webOS 5) through the `browserslist` entry in
`app/package.json`. `backport/test-*.sh` hold the shell tests (see [Persistence](#persistence)). The Jest tests drive `apply` and `restoreStock` with a fake `rootLuna` for 5.6.1,
6.6.0, 8.3.0, 9.2.1, 10.3.1 and 11.2.0 and assert the payloads.
