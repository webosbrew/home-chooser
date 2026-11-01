# Launcher fixtures

Six minimal launchers for the end-to-end test, `{native, qml, web} x {normal, overlay}`. Each declares only what a
real launcher declares: `appCategory: ["home"]`, plus `defaultWindowType: "overlay"` (and `transparent: true`, as Dock
does) for the overlay ones. Nothing in them helps Home Chooser: no hint, no extra field, no cooperation with the watcher.

```
sh e2e/fixtures/build.sh [outdir]      # default build/fixtures/; offline; needs the webOS SDK and ares-package
```

| Id | Title | Window |
| --- | --- | --- |
| `org.webosbrew.hcfixture.native.normal` | HC Fixture native normal | opaque solid colour |
| `org.webosbrew.hcfixture.native.overlay` | HC Fixture native overlay | transparent, one small orange strip |
| `org.webosbrew.hcfixture.qml.normal` | HC Fixture qml normal | opaque solid colour |
| `org.webosbrew.hcfixture.qml.overlay` | HC Fixture qml overlay | transparent, one small red strip |
| `org.webosbrew.hcfixture.web.normal` | HC Fixture web normal | opaque solid colour |
| `org.webosbrew.hcfixture.web.overlay` | HC Fixture web overlay | transparent, one small blue strip |

Nothing on screen identifies a fixture and nothing in them writes a record: no label, counter, log line, file or
localStorage. The runner proves a launch from sam (below), never from a picture. The runner picks one with
`--launcher fixture:<type>.<window>` or runs all with `--launchers all-fixtures`.

## native/

`hcfixture.c`, one source for both variants (`-DHC_VARIANT_NORMAL` or `-DHC_VARIANT_OVERLAY`), built with the SDK
that builds `service/` (`/opt/arm-webos-linux-gnueabi_sdk-buildroot`). What remains is what a native webOS app must
do: an SDL2 window (normal: `SDL_WINDOW_FULLSCREEN`, solid colour, as `samples/media/common/sdl_shell.c`; overlay:
the overlay window type through the SDL hints and `wl_webos_shell_surface_set_property`, as Dock's `src/main.c` and
`src/platform/surface.c`, a transparent window with one orange strip), `registerNativeApp` through libhelpers
(`SDL_HINT_WEBOS_REGISTER_APP=false`, as Dock) so sam does not kill the app, a `relaunch` reply raising the window and
a `close` reply or Back/Exit ending the process. Linked libraries: `libSDL2-2.0.so.0`, `libhelpers.so.2`, `libc`, and
for the overlay `libwayland-client.so.0`, `libwayland-webos-client.so.1`. They are in the firmware roots on this
machine for 5.6.1, 5.6.2, 6.6.0, 7.6.0, 8.3.0, 9.2.x and 11.2.0; **10.x and 12.x have no rootfs here, so the sonames
are unchecked there.**

## qml/

`main.qml.in` and `appinfo.json.in`, filled in by `build.sh`; the package is `type: "qml"`, `main: "main.qml"`, run by
`/usr/bin/qml-runner`. The root is a bare `WebOSWindow` (`import Eos.Window 0.1`) with the appId hard-coded, as LG's own
apps do (`com.webos.exampleapp.nav`); the overlay variant sets `defaultWindowType: "overlay"` and
`windowType: "_WEBOS_WINDOW_TYPE_OVERLAY"` and has a transparent window with one strip. The normal variant is a solid
colour.

Is `type: "qml"` supported? `support.json` holds the answer per release, with the evidence, from the firmware dumps
only. `sam-conf.json` has `QmlRunnerPath: /usr/bin/qml-runner`, the binary exists, `sam` contains `QmlAppLifeHandler`,
and LG ships `type:"qml"` apps on **5.6.1/5.6.2, 6.3-6.6, 7.4-7.6, 8.3-8.4, 9.2.x and 10.3.1**. On **11.2.0** the runner
and `QmlRunnerPath` are still there and the schema still allows `qml`, but no shipped app uses it, so it is
`unknown`; **12.0.0** is `false`: its rootfs has no `qml-runner`, no Qt libs, no `Eos.Window` module and no booster
unit (only the `QmlRunnerPath` setting and sam's `QmlAppLifeHandler` remain), and a device run saw sam launch, splash
and stop without ever reaching `APP_LAUNCHED`, so these fixtures XFAIL on 12.x. Nothing is verified on a device for a third-party
install, on any release. The runner reads `support.json`: `false` for a release skips the fixture with an `XFAIL`;
otherwise it runs and the launch proof decides.

## web/

`index.html` only: a solid-colour page, or for the overlay a transparent page with one strip. No script: a web app
needs none to stay registered with sam and WAM. `build.sh` sets the body class per variant.
`HC_FIXTURE_PERMISSION=org.webosbrew.homechooser.applications sh web/build.sh <outdir>` adds
`requiredPermissions: ["org.webosbrew.homechooser.applications"]`, the launcher API's opt-in (`docs/launcher-api.md`); the runner's
web fixture also declares a valid oversized `largeIcon` to check `getAppIcon`'s `iconTooLarge` reply.
`--api-check` builds them so into `build/fixtures-api/`, apart from the plain ones.

## disposable/

`build.sh <outdir>` packages `org.webosbrew.hcfixture.disposable`, an ordinary web app with no `home` category and no
`requiredPermissions` (`HC_FIXTURE_PERMISSION` is ignored). `--api-check` uses it as the caller without the
permission, as the app `closeApp` closes, and as the target of `uninstallApp`. It is never a launcher and is not part of
the six built by `build.sh`.

## What the runner proves

The same for native, qml, web and any `--launcher-ipk` app (`homechooser_e2e.py` `launch_proof`, run after each Home press that should
open the launcher): sam's own record naming the id, plus the id in `applicationManager/running` with a live pid.

* `/var/log/messages` lines of the form `<UTC ts>Z [<uptime>] user.info sam [] SAM NL_APP_LAUNCH_BEGIN
  {"app_id":"<id>","caller_app_id":"<caller>","mode":"<mode>"}` (seen in `e2e/results/*/run.log` for the stock home on
  7.6.0 w22p-mullet, 8.3.0 w22o-number1 and 9.2.1 w22h-ombre; `caller_id`/`bootManager` at boot). The strings
  `NL_APP_LAUNCH_BEGIN`, `APP_LAUNCHED`, `APP_CLOSED`, `APP_LAUNCH_ERR`, `RelaunchAsCommon` and the Luna method
  `getAppLifeEvents` are in `/usr/sbin/sam` of 5.6.1 (W20L), 6.6.0 (W21P), 7.6.0 (W22P), 9.2.4 (ombre), 10.3.1
  (ponytail) and 11.2.0 (W22H); 12.0.0 has no sam in any dump here, and no run log has a sam launch line from a 5.x,
  10.x, 11.x or 12.x VM. The exact `APP_LAUNCHED` and `RelaunchAsCommon` message layouts are not in any log on this
  machine (only the bare names), so the runner matches the name plus the id on the line.
* `getAppLifeEvents` (subscribe) replies `{"returnValue":true,"subscribed":true,"appId":"<id>","event":"<launch|relaunch|
  foreground|splash|stop|close|...>",...}`; the event names `launch`, `relaunch`, `foreground`, `splash`, `stop` are
  strings in sam (`reLaunch` also appears in 11.2.0). The key names are LG's documented ones, not captured here.
* A relaunch of a launcher that is already running (overlay launchers stay running) shows as a `relaunch` event and,
  where sam logs it, a `RelaunchAsCommon` line naming the id. The runner closes a launcher before each press, so the
  normal path is a fresh launch; the relaunch signals are accepted too.
