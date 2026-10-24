# Home Chooser

*Choose the app your Home button opens.*

Home Chooser lets the owner of a rooted LG webOS TV choose which app the Home button opens. It is a
homebrew app with three parts:

* a small settings app that lists the installed launchers and applies the choice;
* a key filter backport for webOS 6 to 9 (webOS 22 to 24), whose Home key path names the stock
  launcher, so that the Home key follows the platform's default app as webOS 25 and later do;
* one launcher contract across releases: a launcher declares `appCategory: ["home"]` and needs no
  per-release variant.

## Decisions

* The platform's own default-app mechanism decides the home app: the `general/defaultApps`
  settings record, which `sam` reads on every release from webOS 5 and prefers over the system
  profile on webOS 25 and 26. The stock launcher id stays the fallback when no default is set.
* The settings app does not call the settings and application manager services under its own
  identity. It calls Home Chooser's own native service, `org.webosbrew.homechooser.service`, which
  Homebrew Channel elevates to root once (`luna://org.webosbrew.hbchannel.service/elevateService`)
  and which makes the privileged calls itself. The service offers narrow settings operations to Home Chooser and a versioned
  application API to launchers that declare its permission group. Closing and uninstalling apps
  also require the caller to be the selected Home; uninstall asks the owner to confirm. A rooted TV with Homebrew Channel is a
  requirement. On webOS 11.x and 12.x (webOS 26 and later) the current Homebrew Channel refuses
  calls from ordinary apps (`Denied method call`); Homebrew Channel will be updated to allow them
  again.
* The webOS 6 to 9 key filter backport is shipped as diffs keyed by the stock file's sha256. A
  Homebrew Channel boot script applies them to copies of the stock files and bind-mounts the
  copies over the originals. Reviewed sets take precedence; on webOS 6 to 9 with unknown hashes,
  the owner may opt in to a patch generated from the live stock files with strict post-conditions.
  Any refusal leaves the files stock. See [Generated fallback](docs/key-filter-backport.md#generated-fallback).
* Home Chooser does not change other services: no edits to their environment files
  (`/var/systemd/system/env/*.env`), units or binaries. Its boot-time work runs only from
  Homebrew Channel's `/var/lib/webosbrew/init.d`.
* The choice persists through Home Chooser's own file, `/var/lib/webosbrew/homechooser/home` (the app
  id alone), which a watcher (a transient systemd unit started from the init.d entry) enforces
  against the settings record, because the record can lose `home` at boot on 11.2.0. The init.d
  entry is a symlink into the installed app, never a copy. See
  [Persistence](docs/settings-app.md#persistence).
* The stock launcher keeps working when apps are removed. The watcher notices that the chosen app
  is uninstalled and puts the platform back to stock until it returns; when Home Chooser itself is
  uninstalled it undoes everything it wrote (record, override, choice, boot entry, backport
  mounts) from a copy of its scripts in `/tmp`. Each of the two tells the user once, with an alert
  (the chosen app) or a toast (Home Chooser), that the Home button opens LG Home again. See
  [When apps are removed](docs/settings-app.md#when-apps-are-removed), including what is left if
  the watcher was not running at the time; opening a reinstalled Home Chooser repairs a Home that
  still names a removed app ([A leftover Home](docs/settings-app.md#a-leftover-home)).
* While a third-party home is chosen (webOS 6 and later), the watcher keeps LG's stock home,
  `com.webos.app.home`, from being preloaded, so that a force-close of it sticks and its 70 to 105 MB
  stay free: `preloadmanager/setPreloadPolicy` with `isEnabled:false`, then a forced
  `closeByAppId` (not on webOS 6 to 10 when the launcher is an overlay app such as Dock: `sam` would
  relaunch the stock home at once, so it is left running there). The call is made once per boot and once per change, never with `permanent` (the
  preload manager keeps it in memory only and a reboot starts stock), and it is set back to enabled
  when the choice is stock, the chosen app is uninstalled, or Home Chooser is uninstalled. The
  memory manager is not touched. This is an accepted exception to "does not change other
  services": it is a runtime policy call that persists nothing. webOS 5 is left alone. See
  [The stock home is not preloaded](docs/settings-app.md#the-stock-home-is-not-preloaded).
* The UI is built with Enact.

## Launchers

Home Chooser supports a launcher only through its appinfo: an app that declares `appCategory: ["home"]` (and, if it is an
overlay window, `defaultWindowType: "overlay"`) is listed and can be chosen. Apps that patch the system themselves
(AltHome style) are out of scope. The end-to-end test checks this with six fixture launchers
([`e2e/fixtures`](e2e/fixtures/README.md)) besides Dock:

| | normal window | overlay window |
| --- | --- | --- |
| native (C, SDL2) | `org.webosbrew.hcfixture.native.normal` | `org.webosbrew.hcfixture.native.overlay` |
| QML | `org.webosbrew.hcfixture.qml.normal` | `org.webosbrew.hcfixture.qml.overlay` |
| web | `org.webosbrew.hcfixture.web.normal` | `org.webosbrew.hcfixture.web.overlay` |

The fixtures have not run on a device yet; whether `type: "qml"` apps run on each release is recorded, with the
firmware evidence, in `e2e/fixtures/qml/support.json`.

## Documents

* [Launcher API version 1](docs/launcher-api.md): appinfo permissions, application methods, selection
  requests and user-confirmed uninstall for launcher authors.

* [The Home key and the home app, webOS 5 to 26](docs/research/home-key-and-home-app.md): how
  each release picks and launches the home app, verified against the firmware and on emulator
  VMs, with the mod map and the remaining tests.
* [The root service](docs/service.md): `org.webosbrew.homechooser.service`, its methods, allow-lists, sender check, elevation and logs.
* [The key filter backport, webOS 6.6.0 to 9.2.1](docs/key-filter-backport.md): the home id the
  compositor shares, the sites patched per release, the boot script and the install sequence.
