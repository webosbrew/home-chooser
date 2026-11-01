# Example web launcher

A small full-screen launcher for [Home Chooser](../../README.md): a grid of the installed apps. Plain
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
  (HDMI 1, ...). The first app has the focus.
* Icons come from `getIcons`, up to 64 in one call, cached in memory by app id and version (an input
  by its `inputId` and icon path), so a refresh asks only for new tiles. A tile without a readable
  icon (`noIcon`, `iconTooLarge`) gets a letter placeholder.
* Arrow keys move the focus. OK opens the focused app or switches to the focused input with
  `luna://com.webos.applicationManager/launch {"id": ...}`. Back returns the focus to the first app
  and never closes the launcher (`disableBackHistoryAPI` in `appinfo.json` hands the key to the
  page).
* It refreshes the list when the Home button is pressed while it runs (`webOSRelaunch`, which also
  moves the focus back to the first app) and when the page becomes visible again.

**Pending device confirmation.** Two choices are not yet confirmed on a TV: starting apps with
`applicationManager/launch` from this app, and reading icons with `getIcons` instead of loading
`folderPath` + `icon` as a `file://` URL.

## Try it in a browser

Open `index.html` in a desktop browser. With no webOS service bridge, `homechooser-mock.js` answers
with the example data of [`api/launcher-v1.openrpc.json`](../../api/launcher-v1.openrpc.json). Add
`?hcmock=before-elevation`, `not-permitted`, `not-home` or `absent` to the URL to see the other
start states. `HomeChooser.mock` holds the fake state and can be changed in the console, for example
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

`homechooser.js` and `homechooser-mock.js` are generated from the API description; take new copies
from [`api/client/`](../../api/client) rather than editing them.
