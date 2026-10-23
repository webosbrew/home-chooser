# Launcher fixtures

Six minimal launchers for the end-to-end test, `{native, qml, web} x {normal, overlay}`. Each declares only what a
real launcher declares: `appCategory: ["home"]`, plus `defaultWindowType: "overlay"` (and `transparent: true`, as Dock
does) for the overlay ones. Nothing in them helps Home Chooser: no hint, no extra field, no cooperation with the watcher.

```
sh e2e/fixtures/build.sh [outdir]      # default build/fixtures/; offline; needs the webOS SDK and ares-package
```

| Id | Title | Window |
| --- | --- | --- |
| `org.webosbrew.hcfixture.native.normal` | HC Fixture native normal | opaque full-screen card |
| `org.webosbrew.hcfixture.native.overlay` | HC Fixture native overlay | transparent, one orange bar |
| `org.webosbrew.hcfixture.qml.normal` | HC Fixture qml normal | opaque full-screen card |
| `org.webosbrew.hcfixture.qml.overlay` | HC Fixture qml overlay | transparent, one red bar |
| `org.webosbrew.hcfixture.web.normal` | HC Fixture web normal | opaque full-screen card |
| `org.webosbrew.hcfixture.web.overlay` | HC Fixture web overlay | transparent, one blue bar |

Every fixture draws a large label with its own id and variant and a launch counter, so a `beanjar screenshot` shows
which one is up. The runner picks one with `--launcher fixture:<type>.<window>` or runs all with
`--launchers all-fixtures`.

## native/

`hcfixture.c`, one source for both variants (`-DHC_VARIANT_NORMAL` or `-DHC_VARIANT_OVERLAY`), built with the SDK
that builds `service/` (`/opt/arm-webos-linux-gnueabi_sdk-buildroot`). Windowing is SDL2 on the webOS Wayland shell,
copied from proven code: the normal variant follows `samples/media/common/sdl_shell.c` (EGL/XDG environment, back and
exit access policy, a `SDL_WINDOW_FULLSCREEN` window at the display-mode size); the overlay variant is Dock's
(`Dock.app/src/main.c` hints `SDL_HINT_WEBOS_WINDOW_GROUP_TYPE=_WEBOS_WINDOW_TYPE_OVERLAY` and friends, and
`src/platform/surface.c` sets the overlay type on the `wl_webos_shell_surface`; Dock's working notes record that
appinfo alone left the window a card on the 6.6 VM). Relaunch is Dock's too: `SDL_HINT_WEBOS_REGISTER_APP=false`, then
`applicationManager/registerNativeApp` through libhelpers, whose reply carries `relaunch` and `close`.

Linked libraries (`readelf -d`): `libSDL2-2.0.so.0`, `libhelpers.so.2`, `libc.so.6`, and for the overlay
`libwayland-client.so.0`, `libwayland-webos-client.so.1`; highest glibc version `GLIBC_2.4`. All of them are in the
firmware roots on this machine for 5.6.1, 5.6.2, 6.6.0, 7.6.0, 8.3.0, 9.2.x and 11.2.0; **10.x and 12.x have no rootfs on
this machine, so the sonames are unchecked there.** No SDL_ttf: the label is a built-in bitmap font.

Launch record: one line per event (`launch`, `window`, `register`, `relaunch`, `close`, `exit`, `surface`),
`hcfixture id=<id> variant=<v> event=<e> pid=N t=<epoch> ...`, written to syslog (tag `hcfixture`, so
`/var/log/messages`, which is where the runner already reads sam's lines and which survives a crash) and appended to
`/tmp/<id>.launches` and `/media/developer/temp/<id>.launches` (no rotation; a jailed app may see a private `/tmp`).
The `launch` event carries `argv[1]`, the launch parameters.

## qml/

`main.qml.in` and `appinfo.json.in`, filled in by `build.sh`; the package is `type: "qml"`, `main: "main.qml"`, run
by `/usr/bin/qml-runner`. The root is `WebOSWindow` (`import Eos.Window 0.1`) with the appId hard-coded, as LG's own
apps do (`com.webos.exampleapp.nav`, whose overlay variant sets `defaultWindowType: "overlay"` and
`windowType: "_WEBOS_WINDOW_TYPE_OVERLAY"`; the fixture sets both). Only `QtQuick 2.0`, `Eos.Window`, `PmLog` and
`TextFile` are imported, all present in every dump that has QML apps.

Is `type: "qml"` supported? `support.json` holds the answer per release, with the evidence, from the firmware dumps
only. `sam-conf.json` has `QmlRunnerPath: /usr/bin/qml-runner`, the binary exists, `sam` contains `QmlAppLifeHandler`,
and LG ships `type:"qml"` apps on **5.6.1/5.6.2, 6.3-6.6, 7.4-7.6, 8.3-8.4, 9.2.x and 10.3.1**. On **11.2.0** the runner
and `QmlRunnerPath` are still there and the schema still allows `qml`, but no shipped app uses it, so it is
`unknown`; **12.0.0** has no sam or runner in the dump: `unknown`. Nothing is verified on a device for a third-party
install, on any release. The runner reads `support.json`: `false` for a release skips the fixture with an `XFAIL`;
`unknown` runs it and records a missing launch record as `XFAIL` with the reason. (`samples/qt` is a `type: "native"`
binary that borrows the TV's Qt; it is not a qml package.)

Launch record: `HCFIXTURE <start|activate|deactivate|alive> id=... variant=... count=N params=... args=[...]`
(a 10 s `alive` heartbeat) through `PmLog` (context `hcfixture`, to `/var/log/messages`), `console.log`, and a counter
file `/tmp/hcfixture-<id>.json`. Whether qml-runner passes the launch parameters to QML is unknown (`params=n/a`).

## web/

Plain HTML/JS (`index.html`, `app.js`), packaged by `ares-package`. Listens for `webOSRelaunch`. Launch record:
`HCFIXTURE launch|relaunch id=... params=...` through `console.log`, `webOSSystem.PmLogString` when it exists, and
`localStorage` (`hcfixture.log`, `hcfixture.count`, readable through the web inspector). **Whether WAM forwards
`console.log` to `/var/log/messages` is not established** (the Chromium in the firmware has `LogJsConsoleMessages`,
nothing more was found), and a web app cannot write a file, so this is the weakest record. The runner falls back to
noting sam's own launch lines.

## Launch records and the runner

The runner (`launch_records`) greps `/var/log/messages` for `hcfixture` lines naming the id and reads the native and
qml files, before and after each Home press that should open the fixture, and requires new lines. For native a
missing record is a `FAIL`; for qml and web it is an `XFAIL` with the reason above, until a device run settles it.
