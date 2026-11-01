# Building and testing

One package holds the settings app (`app/`, Enact) and the native service, daemon and home gate
(`service/`, C). `npm run package` in `app/` builds both and runs `ares-package`. Per-component
tests are also listed in [service.md](service.md#tests), [home-key-hook.md](home-key-hook.md#status-and-tests)
and [home-gate.md](home-gate.md#tests); the device end-to-end runner is described in
[`e2e/README.md`](../e2e/README.md).

## Toolchain

On Linux, this tree has been built with Node.js 24.20.0, npm 11.19.0 and GNU Make; the native build needs CMake 3.20 or later. Install the
[webOS Homebrew native toolchain](https://github.com/webosbrew/native-toolchain) for the C service;
the tested SDK reports Buildroot 2025.02.1 and GCC 14.2.0. Download its
`arm-webos-linux-gnueabi_sdk-buildroot` archive for your host, extract it, and run the included
`relocate-sdk.sh`. The [webOS Homebrew setup guide](https://www.webosbrew.org/develop/guides/env-setup/#setup-for-native-app-development)
has the host-specific downloads. Install the [webOS CLI](https://webostv.developer.lge.com/develop/tools/sdk-installation)
so `ares-package` is on `PATH`; it packages the app and service. The official webOS TV web SDK
alone does not supply this native compiler.

```sh
cd app
npm ci
SDK=/path/to/arm-webos-linux-gnueabi_sdk-buildroot npm run package
```

The CMake project in `service/` (`service/cmake/settings.cmake`) defaults `SDK` to
`/opt/arm-webos-linux-gnueabi_sdk-buildroot` and gives a clear error if the compiler or sysroot is
missing. Override `SDK`, or `SYSROOT` (default `$SDK/arm-webos-linux-gnueabi/sysroot`) and `CROSS`
(default `$SDK/bin/arm-webos-linux-gnueabi-`) separately for another layout, in the environment or
as `-D` options to `cmake`. `FIRMWARES` points to unpacked firmware images for
`cmake --build build/service --target check-abi` and the qemu tests only; its default is
`$OPENHOUSE_FIRMWARES`, then `~/Projects/webos-firmwares`. Set `ROOTFS` directly to use one
particular unpacked root filesystem. Normal packaging does not read `FIRMWARES`.

For offline checks, use the native tests (`cmake -S service/test -B build/service-host`,
`cmake --build build/service-host`, `ctest --test-dir build/service-host`; `-DQEMU_TESTS=OFF` on the
first for the policy test alone, without the SDK or firmware), `cd app && npm run lint && CI=true npm test`,
the shell suites in `service/scripts/tests/`, and
`python3 -I -m unittest discover -s e2e -p 'test_*.py'`.

## App build, build variables and version

```
cd app
npm install
npm test                # Enact's jest
npm run pack-p          # production build into app/dist
npm run package         # ares-package into build/ipk (git-ignored)
```

The paths of the build machine are variables. Their defaults are one developer's layout; set them on
the `cmake` command line (`cmake -S service -B build/service -DSDK=/opt/sdk`) or in the environment,
which `npm run package` passes through to CMake (read when CMake configures; a `-D` value stays in
the build directory's cache):

| Variable | Default | Used by |
| --- | --- | --- |
| `SDK` | `/opt/arm-webos-linux-gnueabi_sdk-buildroot` | `service/cmake/settings.cmake`, used by `service/cmake/webos-toolchain.cmake`: the webOS buildroot SDK |
| `SYSROOT` | `$SDK/arm-webos-linux-gnueabi/sysroot` | `service/cmake/webos-toolchain.cmake` |
| `CROSS` | `$SDK/bin/arm-webos-linux-gnueabi-` | `service/cmake/webos-toolchain.cmake` (gcc; CMake takes ar and strip from beside it) and `service/test/check-abi.sh` (readelf, nm; `READELF` and `NM` override one tool) |
| `FIRMWARES` | `$OPENHOUSE_FIRMWARES`, else `~/Projects/webos-firmwares` | `service/cmake/settings.cmake`: where `ROOTFS` and `KEYFILTER_ROOTFS` are |
| `ROOTFS` | `$FIRMWARES/04.63.25.01-HE_DTV_W20L_AFAAJAAA/rootfs` (5.6.1) | `cmake --build build/service --target check-abi` |
| `KEYFILTER_ROOTFS` | `$FIRMWARES/04.60.90.01-HE_DTV_W22P_AFADATAA/rootfs` (7.x) | the qemu tests in `build/service-host` |
| `OPENHOUSE_FIRMWARES`, `OPENHOUSE_HYPERVISOR` | `~/Projects/webos-firmwares`, `~/Projects/webos-hypervisor` | `service/scripts/tests/test-real-roundtrip.sh` and `service/scripts/tests/test-minimal-hook.sh`: the firmware roots they try (a root that is missing is skipped) |
| `OPENHOUSE_S23Z_ROOTFS` | under `OPENHOUSE_HYPERVISOR` | the same two scripts: the unpacked S23Z image |

`test-policy` (configured with `-DQEMU_TESTS=OFF`) and the Jest specs need none of them. The cross
build goes to `build/service`, where `app/scripts/stage-service.js` takes the three binaries from.

The version has one source, `"version"` in `app/package.json`. `app/appinfo.json` (what
`ares-package` and the TV read) must carry the same: `npm run check-version`
(`app/scripts/check-version.js`) fails on a mismatch, `npm run package` runs it first, a Jest spec
checks it with the other tests, and `npm version <x.y.z>` rewrites `appinfo.json` through the
`version` script. The native binaries get it from `service/CMakeLists.txt` (`VERSION`, read from
`package.json` unless `-DVERSION` or the environment sets it, compiled in as `HC_VERSION`;
`service/src/version.h` requires it and an empty version fails the build), and the service reports
it as `version` in `status` and `getInfo`. `services.json` has no
version. The ipk's control file is written by `ares-package`, which fills in
`Maintainer: N/A <nobody@example.com>` itself; that placeholder is left as it is.

Enact copies all of iLib's locale data (83 MB, a 15 MB package) and its CLI has no switch to
limit it for a non-isomorphic build, so `scripts/prune-ilib.js` runs between the build and
`ares-package`. It keeps the top-level data files and the `en`, `und` and `zoneinfo` locale
directories, drops the other locale directories and the character maps, and rewrites iLib's
manifest to the remaining files. The UI strings are English and come from the source and from
Moonstone's own resources. `scripts/prune-fonts.js` then removes Moonstone's bundled font files,
because the TV supplies the fonts its CSS names first
([System font verification](research/system-font-verification.md)); `scripts/check-fonts.js`
checks the result. `scripts/stage-service.js` copies the three binaries from `build/service`, the
three entry scripts from `service/scripts/` (at mode 755) and their `scripts/lib/` helpers into the
service package. The app bundle carries no scripts.

The build targets `chrome 68` (the browser of webOS 5) through the `browserslist` entry in
`app/package.json`. `service/scripts/tests/test-*.sh` hold the shell tests (see [Persistence](settings-app.md#persistence)). The Jest tests drive `apply` and `restoreStock` with a fake `rootLuna` for 5.6.1,
6.6.0, 8.3.0, 9.2.1, 10.3.1 and 11.2.0 and assert the payloads.
