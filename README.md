# Open House

*Find your TV a new home.*

Open House lets the owner of a rooted LG webOS TV choose which app the Home button opens. It is a
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
  identity. It runs `luna-send` as root through Homebrew Channel's root exec
  (`luna://org.webosbrew.hbchannel.service/exec`), which passes both the bus permissions and
  settings service's per-key whitelist. A rooted TV with Homebrew Channel is a requirement.
* The UI is built with Enact.

## Documents

* [The Home key and the home app, webOS 5 to 26](docs/research/home-key-and-home-app.md): how
  each release picks and launches the home app, verified against the firmware and on emulator
  VMs, with the mod map and the remaining tests.
