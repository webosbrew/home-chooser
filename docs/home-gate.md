# Home gate, webOS 6–9

While a third-party launcher is chosen, stock LG Home (about 70–90 MB) should not run. On 6–9
SAM relaunches it whenever the foreground becomes empty (the idle relaunch through
`last_input_handler`), so closing it frees nothing for long
([settings-app.md](settings-app.md), "stock home left running"). The home gate takes the binary SAM
starts instead: every launch SAM makes of `com.webos.app.home` runs `homechooser-home-gate`, and the
gate decides what to open.

Scope: webOS 6.x to 9.x only, where stock Home is native at `file:///usr/bin/com.webos.app.home`.
On 5.x the visible home is `com.webos.app.fullhome` (`com.webos.app.home` never shows), and on
10.x and later Home is a Flutter app folder, so neither has a binary to replace. 5.x and 10.x are
unchanged. The 11+ "close LG Home once the launcher is in front" logic is unchanged.

## The gate program

`homechooser-home-gate` ([service/src/gate/](../service/src/gate/)) is native C, built by the same
Makefile and SDK and linked to `libhomechooser.a`. It does not link libluna-service2. Every LS2 request
is a bounded `luna-send -n 1` child (2 s for queries, 3 s for a launch). SAM starts it as root,
with SAM's argv; `argv[1]` is the JSON launch envelope. It decides, acts and exits 0. A clean exit is
what stops SAM retrying and falling back to Live TV.

Inputs: the choice file `/var/lib/webosbrew/homechooser/home`, the Home key flag
`/var/lib/webosbrew/homechooser/homekey`, the envelope's `reason`, and `getAppInfo`'s
`appInfo.defaultWindowType` for the choice (`overlay` is an overlay launcher, anything else
full-screen; the watcher's 11+ close logic uses the same classification).

| Condition | Overlay launcher | Full-screen launcher |
|---|---|---|
| envelope `reason == "hotKey"` | launcher | launcher |
| foreground empty (`getForegroundAppInfo` `appId` empty) | last input | launcher |
| foreground non-empty | launcher | launcher |
| no valid choice, LG Home chosen, flag absent, launcher not installed, or any error | real stock Home | real stock Home |

- The foreground is only asked for an overlay launcher; a full-screen launcher always opens.
- Launches use `luna-send -a com.webos.app.home luna://com.webos.applicationManager/launch {"id":…}`.
- Last input is `lastSourceAppId` from `luna-send -a com.webos.applicationManager
  luna://com.webos.service.eim/getLastInput {}`. An empty value, stock Home, or no answer gives
  `com.webos.app.livetv`.
- A failed or timed-out launch also falls back to real stock Home.
- An envelope carrying `preload` (a background preload, e.g. `"preload":"full"`) opens nothing and
  exits 0. Launching would bring something to the front. The watcher turns the stock Home preload
  off anyway. This row is not in the decision table above; it is a design choice.

### Real stock Home: the gate removes itself

Stock Home cannot run from any other path. ls-hubd gives a process its LS2 role by
`/proc/<pid>/exe`, and Home's role files (`/usr/share/ls2/roles/{pub,prv}/com.webos.app.home.json`)
name `exeName` exactly `/usr/bin/com.webos.app.home`. Exec'd from a private bind it got
`LSHUB_NO_ROLE_FILE`, "Invalid permissions for com.webos.app.home" and a black screen, on 7.6.0
and 9.2.1. So the fallback is:

1. write the release marker `/tmp/homechooser-runtime/home-gate/released` with the stored choice;
2. `umount2("/usr/bin/com.webos.app.home", MNT_DETACH)`, lazy because the gate runs from that file;
3. `luna-send -n 1 -a com.webos.applicationManager luna://com.webos.applicationManager/launch
   '{"id":"com.webos.app.home"}'`, so SAM execs the real `/usr/bin` binary;
4. exit 0.

If the unmount fails (other than "not mounted"), the gate launches nothing and exits 1: a launch
would only start the gate again.

Each decision is one line, appended to the watcher log `/tmp/homechooser-home.log` by the gate itself
(see "Where the gate's log lines go" below), e.g. `home gate: overlay launcher, nothing in front: last input (reason
undefined): opened com.webos.app.hdmi2` or `home gate: gate removed from /usr/bin/com.webos.app.home;
asked SAM to start stock Home: accepted`. The release launch is fire and forget. SAM does not answer a launch of
`com.webos.app.home` until the gate process, which is that app's current instance, has exited (measured on 7.6.0
and 6.5.2: no reply within 3 s, real Home up only after the gate exited), so waiting only delayed the gate's
exit. The gate starts `luna-send -n 1` from a grandchild in its own session (`setsid`, so a kill of the
gate's process group does not reach it; `alarm(10)` bounds it, stdio on `/dev/null`) and exits at once. The log
line says the request was sent, `request sent, no reply awaited`, and nothing confirms it; the launcher
launch of a normal decision still waits (up to 3 s) for its reply.

### Where the gate's log lines go

The gate calls PmLog (`HOME_GATE`) and writes its own line to `/tmp/homechooser-home.log`, opened
`O_APPEND` by the gate process itself, the same file the watcher appends to. That file is the
record to read. On a 7.6.0 and a 6.5.2 device the `HOME_GATE` lines never reached `/var/log/messages` although
the daemon's `START` line and a root-shell `PmLogCtl logkv` to the same context (`HomeChooser`) did. The cause
is not established; one hypothesis is that SAM runs the gate through the jailer and the process cannot reach the PmLog
socket from there. PmLog is therefore not reliable from the gate. The file write is not affected:
it is a plain `open` of `/tmp`, and its lines are present. Behaviour is unchanged, only the documented place to look.

## Mount lifecycle (daemon)

[gate-mount.c](../service/src/gate-mount.c) owns the bind; the watcher
([watcher/gate.c](../service/src/watcher/gate.c)) and the one-off `--homekey` commands
([homekey-main.c](../service/src/homekey-main.c)) call it. It is gated on the os-release major
being 6 to 9, like `homekey.c`.

Mount, when a launcher is chosen and enabled and the installed gate binary
(`<service dir>/homechooser-home-gate`) exists: copy the gate into the private runtime dir
`/tmp/homechooser-runtime/home-gate` (mode 0700) and bind that copy over
`/usr/bin/com.webos.app.home`. There is no second, private bind of the stock binary.

The bind source is a tmpfs copy, not the package file, so no bind ever points into the package and an
uninstall cannot leave one behind. `/proc/self/mountinfo` is read first, so a sync is idempotent; the
mount is verified there after binding and a failed or invisible bind rolls back. If the target is
already mounted by something else, nothing is mounted. A changed installed gate (package update) is
swapped in.

Closing a stock Home that runs while the gate is mounted: a one-off `--homekey` run tries once after
a new mount. The watcher keeps trying until SAM has answered. At boot SAM starts Home (about 50 s)
before the mount and the watcher's first calls can get no reply (on 7.6.0 `running` and
`setPreloadPolicy` got none for about 25 s), so a single close at mount time can be lost, and the
mount is idempotent afterwards. The close is `running`, then `closeByAppId
{"id":"com.webos.app.home","forceMode":true}` as `com.webos.applicationManager`. This includes an
instance the preload manager started before the mount: it keeps running from the old inode until
closed.

- After a close that confirmed by `closeByAppId` (Home really was running), the watcher waits
  `gate_settle_ms` (20 s) and asks `getForegroundAppInfo` once. On 6.5.2 at boot SAM logged
  `LAUNCH_LASTAPP on_foreground_null triggered` about 10 s after the close and launched nothing, leaving a black
  screen until HOME; 7.6.0 relaunched through the gate. If the foreground is still empty, the watcher launches
  `com.webos.app.home` as `com.webos.applicationManager`, so the gate runs and applies its table (overlay: last
  input, full-screen: the launcher). It happens at most once per close, never when something is in front, when
  the foreground did not answer, or once the gate is unmounted or released (those and a clean end cancel the
  timer), and the decision is logged (`home gate: nothing in front after the stock Home close; launched ...`).
  It is also skipped when the gate has already run since the close. Every gate run that opens or releases something (not a preload) rewrites `/tmp/homechooser-runtime/home-gate/decided`; if its mtime is not
  before the close, the watcher logs `home gate: the gate already ran since the stock Home close; nothing launched`
  and skips, without asking the foreground. `getForegroundAppInfo` can answer empty for 10+ s after a web launcher
  opened (7.6.0, and 8.x boots), so SAM's idle relaunch through the gate used to be followed by a second,
  redundant launch about 8 s later. The foreground check stays as the second condition.
- The attempt is made when the gate is first seen mounted (new mount, or already mounted at watcher
  start), again on `preload_retry_ms` (10 s) up to `preload_tries` (12) times, and at every later gate
  sync, until it is confirmed: `running` replied without Home, or `closeByAppId` returned
  `returnValue` true. A missing reply or a failed close stays unconfirmed. Every attempt is logged
  (`home gate: ... no reply ...`, `close unconfirmed` or `stock Home close confirmed`); after
  confirmation nothing more is logged or sent.
- The gate's own process is never closed. For a short while it runs as `com.webos.app.home`, and
  `closeByAppId` closes every instance of an id. The `running` entry's `processid` is checked: if
  `/proc/<pid>/exe` is the staged gate copy (same device and inode, or an exe marked `(deleted)`
  after a package update replaced the copy), Home is not closed. An entry without a usable pid, or
  whose exe cannot be read, is treated the same way (conservative: not closed, stays unconfirmed).
  If any instance in the list is the gate, nothing is closed in that attempt.
- Unmounting, a release by the gate, and any clean watcher end cancel a pending retry.

The watcher's volatile preload-off for Home stays as it was. SAM's idle handling then goes through the
gate.

The watcher mounts only for a valid, installed, non-stock choice whose live Home key filter was
applied, with the `homekey` flag present. It unmounts:

- when LG Home is chosen again, the launcher is missing, or the filter is not baked for the choice;
- on disable and on `--homekey stop|clear` or `refresh stock` (the app's disable runs these; the
  watcher may see no settings change);
- on uninstall/restore, before the state files are removed. A failed unmount keeps the restore
  pending (watcher exit 1, unit restart), like a failed stock keyFilters restore;
- on any clean watcher end (exit 0). A failed watcher keeps the mount for its restart.

After an uninstall the state directory is gone, and a late `--homekey stop|clear|refresh` run finds
nothing to restore (stock keyFilters are saved in that directory): it reports `active=no` and does not
recreate the directory or `keyfilter.lock`. `apply` and `boot` still create it on a first install.

Choosing LG Home in the app does the same cleanup without an uninstall: once a restore with no choice has
put the stock keyFilters list back and read it back, `keyfilters-stock.json` and `keyfilter.lock` are
removed, and the state directory with them when nothing else is in it. The snapshot is never removed
before that readback. Who logs the mount on a live switch is a race between the watcher and the
`--homekey refresh` the app runs right after starting it; when the refresh mounts first it logs to syslog
only and the watcher's own log has the later `home gate:` close and launch lines but no mount line.

After the gate releases itself, the next sync finds the marker naming the current choice and does
not mount again: it only cleans up what the gate's own unmount left (below) and logs the release once.
A different choice, LG Home (which removes the marker), an explicit `--homekey apply` (the app's
enable) or a reboot (tmpfs) re-arms it.

At boot (`--boot`) the sync runs after the keyFilters step, from the watcher's start-up check, and
again on every app-list event, ticker check and settings (choice) change. A lock file next to the
runtime dir serializes the watcher and a concurrent `--homekey` run.

### Propagation and cleanup

On 9.2.1 the root mount is `shared:1`. The bind over `/usr/bin/com.webos.app.home` propagated into
the devmode SSH jail copies under `/mnt/lg/{user,flash/data,cmn_data}/var/palm/jail/com.palm.devmode.openssh/`,
three extra mounts per bind. Unmounting therefore:

1. unmounts the target while mountinfo shows our gate there (stacked mounts included);
2. then unmounts every other mountinfo entry whose mount point ends in `/usr/bin/com.webos.app.home`
   and whose bind root is our staged copy on our tmpfs (matched by the root's file name and the
   major:minor device of `/tmp/homechooser-runtime`, not by path alone). A mount point that is
   already gone (`EINVAL`) is fine, since unmounting one peer propagates to the others;
3. reads mountinfo again and fails if any of ours remains.

Only mount points are unmounted; nothing under `/var/palm/jail` is read, written or removed.

Making the new mount `MS_PRIVATE` right after creating it would not help. Propagation happens when the
mount is created, so the jail copies already exist by then. A private mount would also stop our later
unmount from propagating to them, which leaves more to clean up. Avoiding the copies entirely would
need the parent mount private (changing `/` for the whole system) or a private mount namespace, which
SAM would not see. So the bind stays shared and cleanup is explicit.

Nothing here restarts SAM or the compositor, and nothing writes configd or settings. The runtime dir
is on `/tmp`, which every 6–9 rootfs mounts as tmpfs (`etc/fstab`: `tmpfs /tmp tmpfs rw`, no
`noexec`; checked on W21P 6.x, W22P/W22L 7.6.0 and W22H 9.x), so a reboot clears the mount, the
copy and the marker, and the firmware's stock Home is back. `/run` is tmpfs too; `/tmp` was chosen
because the daemon already stages its runtime copy in `/tmp/homechooser-runtime` and the bind probes
ran from `/tmp`.

## Evidence

Local, untracked probe notes (`scratch/stock-home-kill/`, 2026-10-31):

- `bind/notes.md`: on 7.6.0, `mount --bind` of a stub over `/usr/bin/com.webos.app.home` took effect
  with no SAM restart (SAM executed the stub as Home, uid 0, `argv[1]` the envelope). A stub that
  exits 0 stopped SAM's retry and the Live TV fallback; a stub that launches a fixture as
  `com.webos.app.home` was accepted (`caller_id com.webos.app.home`). `umount` restored stock Home.
- `fallback/notes.md`: stock Home exec'd from a private bind has no LS2 role
  (`LSHUB_NO_ROLE_FILE`, black screen) on 7.6.0 and 9.2.1; after unmounting, a launch runs the real
  `/usr/bin` binary. On 9.2.1 the binds propagated into the devmode SSH jail copies; an unmount loop
  removed them. A preloaded Home started before the mount kept running until closed.
- `notes.md`: SAM's idle relaunch comes from `profile.idleApp` via `last_input_handler`; closing Home
  leads to `LAUNCH_LASTAPP … on_foreground_null` and a relaunch about 1 s later.
- `nav/notes.md`, `lsmon/notes.md`: what the stub and stock Home receive on 5.6.1, 7.6.0 and 9.x.
  Only the HOME key differs (`reason:"hotKey"`, `parameters.launchType:"homeKey"`). The idle
  relaunch and explicit launches have identical argv (`reason:"undefined"`), and the foreground is
  empty at stub time in both, which is why the table keys on the foreground and the launcher type
  rather than on the trigger.

## Tests

```sh
make -C service test-gate        # decision table and self-release through real luna-send children (fake-luna-send.py)
make -C service test-gate-mount  # mount/unmount, idempotence, rollback, foreign/stacked mounts, jail copies, release marker
make -C service test-watcher     # G11-G19: when the watcher mounts and unmounts; the stock Home close and its retries
```

## Open device validation

- The self-release: while the gate process (SAM's `com.webos.app.home` instance) is still alive,
  SAM handles `launch com.webos.app.home` by starting the real binary, not by sending a relaunch
  event to the gate's pid.
- The bind made by the daemon's transient systemd unit (and by `--homekey` from the service) is
  visible to SAM, i.e. all share one mount namespace.
- The gate's `launch` as `com.webos.app.home` and `getLastInput` as `com.webos.applicationManager`
  are accepted on 6.x, 8.x and 9.x (7.6.0 accepted a launch as `com.webos.app.home` in the probe).
- `closeByAppId` with `forceMode` after mounting, and the following idle relaunch through the gate,
  show no fault dialog; memory is actually freed.
- The jail-copy cleanup leaves mountinfo with no gate entries on 9.x; whether 6.x to 8.x propagate
  the same way.
- Preload (CRIU on 7.x, `preloadMode criu` for Home) cannot restore a stock Home image past the gate.
- An overlay launcher with an empty foreground gets the last input under it as intended.
