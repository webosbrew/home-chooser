# Example web launcher

A small full-screen launcher for [Home Chooser](../../README.md): a grid of the installed apps and the
TV's inputs, with a row of the apps used last above it. Plain
HTML, CSS and ES5 JavaScript, no framework and no build step; it runs in the web runtime of webOS 5
(Chromium 68) and later. Copy this directory to start your own launcher.

| File | What it is |
| --- | --- |
| `appinfo.json` | `appCategory: ["home"]` (Home Chooser lists it) and `requiredPermissions` (it may call the API) |
| `launcher.js` | The launcher |
| `homechooser.js` | The API client, a copy of [`api/client/homechooser.js`](../../api/client/homechooser.js) |
| `homechooser-mock.js` | Fake Home Chooser for a desktop browser; it does nothing on a TV |
| `index.html`, `style.css`, `icon.png` | Page, layout and app icon |

What it does:

* At start it calls `getInfo` and branches as [the guide](../../docs/launcher-api.md#4-check-where-you-stand-then-use-the-api)
  says. Home Chooser missing or too old, setup not finished, permission missing, or this app not
  the Home app: it shows a message, and where Home Chooser can help, a button that asks the owner
  to make this launcher the Home app (the `request` launch). When a call fails, a line in small
  type under the message gives the error as the client reports it: `source`, `errorCode` and
  `errorText`.
* It lists the apps with `listApps`, without itself and without apps marked `visible: false`,
  sorted by title (ignoring case), then by id, and after them the TV's inputs from `listInputs`
  (HDMI 1, ...). The first app of the grid has the focus.
* Above the grid, a **Recent** row shows up to five of the apps used last, the latest first; it is
  hidden until there is one. Home Chooser keeps no history, so the launcher keeps its own in
  `localStorage`: every app it launches, and every app that
  [`getForegroundApp {subscribe: true}`](../../docs/launcher-api.md#getforegroundapp-subscribe) reports
  in front (level 2), which also catches apps opened another way, such as from a remote button. It
  ignores its own id and an empty `appId`, which the platform can report while an overlay is up
  ([Patterns](../../docs/launcher-api.md#patterns)). The focus still starts on the first app of the
  grid, not on the Recent row.
* It accepts `apiVersion >= 1`. On level 1 it has no input tiles (`listInputs` is level 2) and reads icons with `getAppIcon`, one call per app; on level 2 it uses the rest of this list.
* Icons come from `getIcons`, up to 64 in one call, cached in memory by app id and version (an input
  by its `inputId` and icon path), so a refresh asks only for new tiles. A tile without a readable
  icon (`noIcon`, `iconTooLarge`) gets a letter placeholder, in the app's `bgColor` or `iconColor` when
  `listApps` gives one.
* Arrow keys move the focus: Left and Right within a row, Up and Down between the Recent row and the
  grid. OK opens the focused app or switches to the focused input with
  `luna://com.webos.applicationManager/launch {"id": ...}` (for an input, with the `params` that `listInputs` gives). Back returns the focus to the first app of the grid
  and never closes the launcher (`disableBackHistoryAPI` in `appinfo.json` hands the key to the
  page).
* It refreshes the list when the Home button is pressed while it runs (`webOSRelaunch`, which also
  moves the focus back to the first app) and when the page becomes visible again.

Starting other apps with `applicationManager/launch` from a third-party app id is verified on webOS
5.6.1, 6.5.2, 7.6.0 and 9.2.1 (an input's app on 5.6.1, 6.5.2 and 7.6.0), and `getIcons` from a
launcher page on 10.3.1, 11.2.0 and 12.0.0
([Verification status](../../docs/launcher-api.md#verification-status)).

## Try it in a browser

Open `index.html` in a desktop browser. With no webOS service bridge, `homechooser-mock.js` answers
with the example data of [`api/launcher-v1.openrpc.json`](../../api/launcher-v1.openrpc.json). Add
`?hcmock=before-elevation`, `not-permitted`, `not-home` or `absent` to the URL to see the other
start states. The mock lists 20 apps (`?hcmock-apps=<n>` for another number), and
`?hcmock-changes=2000` brings another app to the foreground every two seconds, which fills the Recent
row. `HomeChooser.mock` holds the fake state and can be changed in the console, for example
`HomeChooser.mock.apps.push({id: 'org.example.more', title: 'More'})`; then simulate the Home button
with `document.dispatchEvent(new Event('webOSRelaunch'))` to refresh.

## Build and install

Order matters. Install Home Chooser first and open it once (that finishes its setup and installs the
permission group), then install the launcher. Installed the other way round, the install is refused
with `Not allowed permission is used`; open Home Chooser and install the launcher again.

```sh
ares-package examples/web-launcher --outdir build
ares-install -d <device> build/org.example.launcher_1.0.0_all.ipk
```

Then open Home Chooser, move to Example Launcher and press OK, or open the launcher and choose
**Open Home Chooser** in its banner. From then on the Home button opens it.

## Make it your own

Change `id`, `title` and `vendor` in `appinfo.json` (and `version` when you release). Nothing in
the code names the id: the launcher takes its own id from the web runtime
(`HomeChooser.ownAppId()`) to hide itself and to send the request. Keep `appCategory` and
`requiredPermissions`; merge your own entries with them. Reinstall after any change to
`requiredPermissions`.

**"OK opens X".** What OK opens on a fresh start is the tile that has the focus first, so to make OK
open a given app at once, such as the app used last, put the initial focus on that tile: in
`render()`, where `focusHome` is handled, set `focus` to that tile's index (`0` is the first tile of
the Recent row, `home()` the first of the grid) instead of `home()`. The launcher draws nothing else
differently. This repository's end-to-end test of the demo (step 3d) presses OK after a Home press and
expects the first app of the grid to open, so it keeps `home()`.

To drop the Recent row, set `RECENT_SHOWN` to `0` in `launcher.js`, and remove the call of
`watchForeground()` if you do not need the history either.

`homechooser.js` and `homechooser-mock.js` are generated from the API description; take new copies
from [`api/client/`](../../api/client) rather than editing them.
