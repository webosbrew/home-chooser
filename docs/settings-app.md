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

## Restoring the stock home

* 5.x: both stock homes declare `appCategory: ["home"]` and the key filter launches the record's
  `home` directly, so a record without `home` could leave the Home key with nothing to launch.
  The record is written with `home` set to `com.webos.app.fullhome` if `listApps` shows it
  installed (the factory value on the studied 5.6.1), else `com.webos.app.home`, and the other
  entries are kept ([5.6.1](research/home-key-and-home-app.md#561-supported-through-the-default-app-record)).
* 6.x to 9.x: the record is written again without `home` and with the other entries kept;
  `setDefaultApp` cannot remove an entry. `com.webos.app.home` is never written as `home` there:
  the stock home has no `appCategory` and `sam` refuses it.
* 10.x and later: the record is written without `home`, the override file is removed and the
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
(the boot script, and each release's manifest and diffs) into `dist`; the package is 2.4 MB.

The build targets `chrome 68` (the browser of webOS 5) through the `browserslist` entry in
`app/package.json`. The tests drive `apply` and `restoreStock` with a fake `rootLuna` for 5.6.1,
6.6.0, 8.3.0, 9.2.1, 10.3.1 and 11.2.0 and assert the payloads.
