# Notes for coding agents

Home Chooser is webOS homebrew for rooted LG TVs: the owner chooses which app the Home button
opens. It is a settings app (`app/`, Enact) and a root service (`service/`, C) that also offers a
small luna API to launchers.

## Minimum for a grid launcher

* Calls: `getInfo` (branch on `root`, `permitted`, `isSelectedHome`), `listApps`, and `getIcons` for
  the icons, up to 64 per call. Launching is the platform's `applicationManager/launch`.
* Start from a copy of [`examples/web-launcher/`](examples/web-launcher/README.md): change the id in
  `appinfo.json` and edit `launcher.js`.
* Don't declare `requiredACG`; if you do, list `application.query` and `application.launcher`.
* The app used last and "OK opens X" are the launcher's own job:
  [Patterns](docs/launcher-api.md#patterns).

## Building a launcher that works with Home Chooser

Read these, in this order:

1. [`api/launcher-v1.openrpc.json`](api/launcher-v1.openrpc.json): the API, `apiVersion` 3 (a feature level that only grows; check `apiVersion >= N`, never `== 1`).
   Methods, parameters, every reply and every `errorCode` as JSON Schema, with examples. The
   top-level `x-` fields hold the appinfo declarations, the permission and the request launch.
2. [`api/client/`](api/client): `homechooser.js`, a one-file client to copy into a web launcher
   (ES5, no dependencies), its types, and `homechooser-mock.js` for a desktop browser.
   [`api/README.md`](api/README.md#client-and-mock) explains both.
3. [`examples/web-launcher/`](examples/web-launcher/README.md): a small complete full-screen
   launcher using the client. Copy it and change the id.
4. The checklist at the top of [`docs/launcher-api.md`](docs/launcher-api.md#checklist), then the
   parts of that guide it links to. The guide is the prose contract and wins where the two differ.

Two things that are easy to get wrong: install Home Chooser and open it once before installing the
launcher (otherwise the install fails with `Not allowed permission is used`), and launching other
apps, Back and `webOSRelaunch` are platform calls, not part of this API
([Platform calls you'll need](docs/launcher-api.md#platform-calls-youll-need)). On webOS 11 and later, a launcher
that declares `requiredACG` must list `application.query` and `application.launcher` there, or the
bus refuses its calls ([Permission](docs/launcher-api.md#permission-on-5x-to-10x-and-on-11x-and-12x)).

Self-test:

* In a desktop browser: include `homechooser-mock.js` after `homechooser.js` and open the page;
  `?hcmock=before-elevation|not-permitted|not-home|absent` selects other start states, and
  `?hcmock-apps=<n>` the number of apps (20 by default).
* On a rooted TV with Home Chooser installed and an ares device configured:
  `python3 -I e2e/homechooser_e2e.py --device <device> --api-check --launcher <your id> --launcher-ipk <your ipk>`
  checks the launcher methods as your launcher, every reply against the API description
  ([Testing your launcher](docs/launcher-api.md#testing-your-launcher)). It calls through `luna-send -a <your id>`,
  which does not carry your app's bus permissions, so also open your launcher on the TV: the demo
  shows a refused call's `errorCode` and `errorText` under its banner.

## Working on Home Chooser itself

Start at [`docs/README.md`](docs/README.md); the build and the offline tests are in
[`docs/build.md`](docs/build.md):

```sh
cmake -S service/test -B build/service-host && cmake --build build/service-host && ctest --test-dir build/service-host --output-on-failure
cd app && npm ci && npm run lint && CI=true npm test
python3 -I -m unittest discover -s e2e -p 'test_*.py'
```

Add `-DQEMU_TESTS=OFF` to the first `cmake` when the webOS SDK or the firmware images are not
available; the launcher API checks still run. After changing `api/launcher-v1.openrpc.json`, run
`python3 api/tools/gen-client.py` and keep `docs/launcher-api.md` in step (`test-launcher-doc`).
