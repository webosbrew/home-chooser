# Home Chooser

*Choose which app opens when you press Home.*

Home Chooser is a homebrew app for rooted LG webOS TVs. It lets you pick which app the Home button on
the remote opens: LG's own Home, or a launcher you have installed, such as a dock or an alternative
home screen. Your choice stays in place after the TV restarts, and Home Chooser puts LG Home back by
itself if the launcher or Home Chooser is removed.

> **Not released yet.** There is no release package so far. Launcher authors can already build
> against the [launcher API](docs/launcher-api.md): version 1 is frozen.

## Requirements

* An LG TV running webOS 5 to 12. The version is the first number of the webOS TV version shown in
  the TV's settings (for example 7.6.0 is webOS 7, sold as webOS 22).
* The TV must be **rooted**, with [Homebrew Channel](https://github.com/webosbrew/webos-homebrew-channel)
  installed and running with root access. Home Chooser asks Homebrew Channel to give its own
  background service root the first time you open it.
* On webOS 11 and later, this depends on a Homebrew Channel update: the current Homebrew Channel
  refuses the request, and Home Chooser says so. This is a known limitation.
* At least one launcher app. Home Chooser lists every installed app that declares itself a Home
  app; it does not include a launcher of its own.

## Installing

Install the Home Chooser package (`.ipk`) like any other homebrew app, for example with webOS Dev
Manager or `ares-install`. Then open Home Chooser once from the app list. If the panel opens with a
dimmed list and a message at the top, see [Troubleshooting](#troubleshooting).

## Choosing a launcher

1. Install the launcher you want to use.
2. Open Home Chooser. A panel opens on the right of the screen with "LG Home (stock)" and every
   launcher on the TV; a check mark shows the current one.
3. Move to your launcher and press OK, then confirm "Use *name* as Home?".
4. When "Done. Press Home to try it." appears, press Home.

The change takes effect at once; no restart is needed. A launcher can also ask to become your Home
app: Home Chooser then opens and asks you the same question. Nothing changes unless you confirm.

## Going back to LG Home

Open Home Chooser, choose "LG Home (stock)" and confirm. The Home button opens LG Home again
straight away.

## Uninstalling

Uninstall Home Chooser like any other app. It puts LG Home back on the Home button by itself and
shows "Home Chooser was removed. The Home button opens LG Home again." If the TV is switched off
right after the uninstall, LG Home is back after the next start at the latest.

If you uninstall the launcher you chose, the Home button opens LG Home again and a message tells you
so. Your choice is remembered: install the launcher again and it becomes your Home app again.

## What happens when you press Home

* **Home** opens your chosen launcher, from any app.
* **After a restart**, the Home button opens your launcher once the TV has finished starting. On
  webOS 6 to 9 a short message, "Your new home will start shortly", may appear while it gets ready.
* **When the last app closes** and nothing is left on screen, webOS 6 to 9 open your launcher if it
  is a full-screen launcher. If it is an overlay launcher (one that draws over the TV picture, like
  a dock), you go back to the last TV input (Live TV or HDMI) instead. On webOS 5, closing the last
  app goes back to Live TV. Other webOS versions keep their own behaviour.
* Holding Home, the other remote shortcuts and voice commands keep working as LG made them.

## Known limitations

* **webOS 11 and later** need a Homebrew Channel update for root (see [Requirements](#requirements)).
* **LG Home in memory.** On webOS 6 to 9, Home Chooser keeps LG Home from running while another
  launcher is chosen, which frees its memory. On webOS 10, LG Home stays loaded in the background
  while another launcher is chosen; the TV starts it again whenever it is closed. On webOS 5, LG
  Home stays loaded in the background after you choose another launcher, until the TV next
  restarts. On webOS 11 and later Home Chooser closes it once your
  launcher is on screen.
* **Unrecognised TV software (webOS 6 to 9).** Home Chooser changes the Home button only on TV
  software it recognises. If it does not, the Home button keeps opening LG Home and Home Chooser
  says "The Home button can't be changed on this TV's software."
* Only apps that declare themselves Home apps are listed. Home Chooser does not combine with tools
  that change the Home button by patching the system themselves; remove those first.
* Home Chooser is in English only.

## Troubleshooting

* **"Homebrew Channel is needed"** or **"Home Chooser has no root access"**: the TV is not rooted,
  or Homebrew Channel is not installed, not running or has no root access. Fix that, then open Home
  Chooser again.
* **"Homebrew Channel can't set up root access yet"**: your Homebrew Channel refuses the request
  (webOS 11 and later). Update Homebrew Channel when an update that allows it is available.
* **"The Home button isn't set up yet."** at the bottom of the panel (webOS 6 to 9): restart the TV,
  or choose LG Home and then your launcher again.
* **Your launcher is not in the list**: it does not declare itself a Home app. Ask its author.
* **"The Home app you chose was removed; using LG Home."**: the launcher was uninstalled. Install it
  again, or choose another one.
* **Reporting a problem**: say which TV model and webOS version you have, which launcher you chose,
  and what you pressed. If you can open a root shell on the TV, include the file
  `/tmp/homechooser-home.log`.

## For developers

Launcher authors: see [Writing a launcher for Home Chooser](docs/launcher-api.md). How Home Chooser
works, how to build it and how it is tested: see the [developer documentation](docs/README.md).

## Licence

Home Chooser is licensed under the [Apache License 2.0](LICENSE); see also [NOTICE](NOTICE). The package bundles third-party code and fonts under their own licences: see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), which is generated by `app/scripts/third-party-notices.js`.
