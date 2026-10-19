# The root service

`org.webosbrew.openhouse.service` is a small native (C) luna service in the Open House package
(`service/`). Homebrew Channel elevates it once, so it runs as root without the developer-mode
jail, and the app does every privileged thing through it. It replaces the earlier design in which
the app ran `luna-send` and shell commands through Homebrew Channel's `exec`. See the decision in
the [README](../README.md#decisions) and [Root access](settings-app.md#root-access).

## Package

`ares-package dist build/service-pkg` packs the app and the service together
(`npm run package` in `app/` builds the service with `make -C service`, stages
`services.json` and the binary with `app/scripts/stage-service.js`, then packages). The ipk holds
`usr/palm/services/org.webosbrew.openhouse.service/{openhouse-service,services.json}` (binary mode
755) next to the app. `services.json` has `"engine": "native"` and `"executable":
"openhouse-service"`, so `ares-package` marks the ipk `arm`.

The binary is C, linked against `libluna-service2.so.3`, `libpbnjson_c.so.2` (all JSON in the
service is pbnjson), `libPmLogLib.so.3`, `libglib-2.0.so.0` and `libc.so.6`. `make -C service
check-abi` compares its needs with the oldest supported rootfs (webOS 5.6.1): SONAMEs present,
only `GLIBC_2.4` and `GLIBC_2.9` symbol versions, every undefined symbol defined by those
libraries. No libstdc++.

## Elevation

The installer gives a developer-mode native service a launch line of the form
`Exec=/usr/bin/jailer ... <binary>`. Homebrew Channel's `elevateService {"id": <service id>}`
(`services/elevate-service.ts`) rewrites that to `Exec=<binary>`, adds `*` to the role file's
`allowedNames` and outbound permissions, creates the service's `.root.json` client-permission file
and the `.api.public.json` API-permission file (`public: ["<id>/*"]`, which makes its methods
callable by apps), adds both to the manifest, and runs `ls-control scan-services`. Because of the
public permission the service checks its callers itself (below).

The running instance was started from the old launch line and is still jailed and not root. The
app therefore calls `status`; when `root` is false it calls `elevateService`, then `quit`, then
`status` until the new instance (started on demand by the hub from the rewritten line) reports
`root: true`. The sequence follows the AltHome port's auto-elevator.

## Methods

All methods are on category `/`, take and return JSON, and answer `{"returnValue": false,
"errorCode": <string>, "errorText": <string>}` on failure.

| Method | Request | Reply |
| --- | --- | --- |
| `status` | `{}` | `{returnValue, root, uid, version}`; works while not root |
| `quit` | `{}` | `{returnValue}`, then exits after 300 ms; works while not root |
| `call` | `{uri, payload}` | `{returnValue: true, reply: <the called service's reply>}`; the call times out after 20 s (`timeout`) |
| `readFile` | `{path}` | `{returnValue, exists, text}` (at most 64 KiB) |
| `writeFile` | `{path, text}` | `{returnValue}`; creates the directory, writes `<path>.tmp` and renames it, mode 0644; at most 256 KiB |
| `removeFile` | `{path}` | `{returnValue}`; a missing file is fine |
| `readIcon` | `{appId}` | `{returnValue, mime, data}` with `data` base64; at most 256 KiB |
| `link` / `unlink` / `linkStatus` | `{}` | the boot entry, below |
| `run` | `{command}` | `{returnValue, exitCode, stdoutString, stderrString}` |

Every method except `status` and `quit` is privileged: when the service is not root it answers
`errorCode: "notRoot"` and does nothing.

## Who may call

Each method first checks the sender with `LSMessageGetApplicationID`: it must be
`org.webosbrew.openhouse` (an instance suffix after a space is accepted). Anything else, including
a service caller (`luna-send` from a shell has no application id), gets `errorCode: "forbidden"`
and a `REFUSED` log line with the application id and service name the hub reported. A build made
with `make -C service dev` accepts any sender; it is for testing with `luna-send` and is never
packaged.

## Allow-lists

The policy is in `service/src/policy.c` and tested on the host (`make -C service test`).

* **`call` URIs**: `applicationManager` `listApps`, `getAppInfo`, `setDefaultApp`,
  `launchDefaultApp`, `closeByAppId`; `settingsservice` `getSystemSettings` and
  `setSystemSettings`, only for category `general` and the single key `defaultApps` (the `keys`
  array of a get; the keys of `settings` of a set); `com.webos.service.systemprofile`
  `getProfiles` and `debug/rescan`. Anything else is `notAllowed`. The payload is forwarded as is.
* **Files** (`readFile`, `writeFile`, `removeFile`, exact paths):
  `/var/lib/webosbrew/openhouse/home`, `/var/lib/webosbrew/openhouse/keyfilter`,
  `/var/webos-profile/override/org.webosbrew.openhouse.json`. `readFile` also reads
  `/etc/os-release`, because a web app cannot read it and the app needs the release. The
  writable files are opened with `O_NOFOLLOW`. `/etc/os-release` is a symlink to
  `../usr/lib/os-release` (webOS 5.6 to 26 roots checked: W20L, W20H, W21P, W22L, W22P, W22H 43.x), so
  it is resolved with `realpath()` and read only when the result is `/etc/os-release` or
  `/usr/lib/os-release`; any other target is `notAllowed`.
* **Icons**: `readIcon` asks `getAppInfo` for the app's `folderPath` and `icon`, joins them (no
  `..`), requires an image extension (png, jpg, jpeg, gif, svg, webp), resolves symlinks and
  requires the result to lie inside the real `folderPath`, then returns the file if it is a regular
  file of at most 256 KiB.
* **Boot entry**: `link` creates the directory `/var/lib/webosbrew/init.d`, removes the earlier
  `openhouse-keyfilter` entry, sets mode 755 on the three scripts in the app's `backport/`
  directory, and makes `/var/lib/webosbrew/init.d/openhouse` a symlink to the app's
  `backport/openhouse` (replacing a link or a regular file). `unlink` removes both names.
  `linkStatus` returns `entry` (`none`, `file`, `link`, `dangling`) and `target`. The app directory
  is the fixed `/media/developer/apps/usr/palm/applications/org.webosbrew.openhouse`.
* **Commands** (`run`, by name, fixed argv, no shell, minimal environment, stdin from `/dev/null`,
  output capped at 64 KiB per stream, a timeout each):

| Name | argv | Timeout |
| --- | --- | --- |
| `restartSam` | `/bin/systemctl restart sam` | 60 s |
| `watch` | `/bin/sh <app>/backport/openhouse watch` | 20 s |
| `keyfilterStatus` | `/bin/sh <app>/backport/openhouse-keyfilter status` | 20 s |
| `keyfilterApply` | same with `apply` | 90 s |
| `keyfilterStop` | same with `stop` | 90 s |
| `keyfilterRefresh` | same with `refresh` | 90 s |
| `stopWatcher` | `/bin/systemctl stop openhouse-home` (a non-zero exit is not an error) | 20 s |
| `watcherActive` | `/bin/systemctl is-active openhouse-home` (a non-zero exit is not an error) | 10 s |
| `watchLog` | not run: the last 5 lines of `/tmp/openhouse-home.log` | - |

A non-zero exit of any other command is `errorCode: "exitStatus"` with the output attached; a
command that does not finish is killed (`timeout`). The service does not wait for a background
process that outlives the command (the `watch` script starts a transient unit).

## Logs

Every operation, and every refusal, goes to PmLog with the context `OpenHouse`
(read it from the journal or with `PmLogCtl`; not verified on a TV): message ids `START`, `CALL`, `CALL_FORWARD`,
`CALL_DENIED`, `CALL_TIMEOUT`, `RUN`, `RUN_DENIED`, `WROTE`, `REMOVED`, `LINKED`, `UNLINKED`,
`PATH_DENIED`, `REFUSED`, `NOT_ROOT`, `ICON_NONE`, `EXIT`. The app logs its side with `[OpenHouse]`
in the web console.

## Tests

* `make -C service test`: the pure policy (sender, file and call allow-lists, argv table, icon
  path rules, base64, log tail) compiled natively with address and undefined-behaviour sanitizers.
* `app/src/platform/tests`: the service client and the elevation flow against a mocked bridge.
* Needs a TV: the sender id the hub reports for the app, `elevateService` rewriting this
  package's service, the role file's outbound permissions for the forwarded calls, and each
  method end to end.
