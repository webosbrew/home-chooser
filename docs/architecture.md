# Architecture

Home Chooser makes the remote's Home button open a launcher the owner picks, on webOS 5 to 12. One
package holds four programs:

| Program | What it is | Runs |
| --- | --- | --- |
| Settings app `org.webosbrew.homechooser` ([app/](../app/)) | Enact web app, the only UI | When the owner opens it, or a launcher asks to become Home |
| `homechooser-service` ([service/src/service/](../service/src/service/)) | Native LS2 service, elevated to root once by Homebrew Channel | On demand, when the app or a launcher calls it |
| `homechooser-daemon` ([service/src/watcher/](../service/src/watcher/), [homekey.c](../service/src/homekey.c)) | Native, root, no LS2 role of its own | At boot and after every choice, as the transient unit `homechooser-home` (`--boot`), and for one-off Home key commands (`--homekey`) |
| `homechooser-home-gate` ([service/src/gate/](../service/src/gate/)) | Native, started by SAM in place of stock Home | webOS 6–9 only, whenever SAM launches `com.webos.app.home` while a launcher is chosen |

Details: [settings-app.md](settings-app.md), [service.md](service.md),
[home-key-hook.md](home-key-hook.md), [home-gate.md](home-gate.md). The firmware facts behind the
per-release differences are in the [study](research/home-key-and-home-app.md).

## Components

```mermaid
flowchart LR
    subgraph pkg["Home Chooser package"]
        app["Settings app<br/>org.webosbrew.homechooser"]
        svc["homechooser-service<br/>LS2 service, root"]
        scripts["scripts/homechooser,<br/>homechooser-homekey,<br/>homechooser-access"]
        daemon["homechooser-daemon<br/>--boot: watcher, boot apply<br/>--homekey: one-off commands"]
        gate["homechooser-home-gate"]
    end
    subgraph hbc["Homebrew Channel"]
        elevate["elevateService"]
        initd["/var/lib/webosbrew/init.d/homechooser"]
    end
    statefiles[("/var/lib/webosbrew/homechooser<br/>home, homekey, systemUi-*.js")]
    lunasend["/usr/bin/luna-send<br/>-a com.webos.applicationManager"]
    subgraph fw["Firmware"]
        sam["SAM<br/>com.webos.applicationManager"]
        settings["settingsservice<br/>general/defaultApps"]
        profile["systemprofile<br/>override + rescan, 10+"]
        configd["configd<br/>surfacemanager.keyFilters"]
        compositor["surface-manager<br/>key filters"]
        preload["preloadmanager<br/>setPreloadPolicy"]
        eim["eim getLastInput"]
        notif["notification<br/>alert, toast"]
        homebin["/usr/bin/com.webos.app.home<br/>stock Home binary, 6-9"]
    end

    app -- "luna calls" --> svc
    app -- "elevate once" --> elevate
    elevate -. "unjailed root launch line" .-> svc
    svc -- "named commands" --> scripts
    scripts --> daemon
    initd -- "run-parts at boot" --> scripts
    svc -- "files" --> statefiles
    daemon -- "reads, writes" --> statefiles
    svc --> lunasend
    daemon --> lunasend
    lunasend --> sam & settings & profile & configd & preload & notif & eim
    configd -- "volatile list, 6-9" --> compositor
    compositor -- "HOME: launch chosen id, 6-9" --> sam
    daemon -- "bind mount, 6-9" --> homebin
    sam -- "exec on every launch of Home" --> homebin
    homebin -. "is, while mounted" .-> gate
    gate -- "luna-send" --> sam & eim
```

* The **settings app** never calls platform services under its own identity. Every privileged
  operation goes through the service, which answers only this app for settings methods and offers
  the [launcher API](launcher-api.md) to launchers.
* The **service** is elevated by Homebrew Channel's `elevateService`, which rewrites its launch
  line so it runs as root outside the developer jail. It forwards a short allow-list of platform
  calls, reads and writes a fixed list of files, and runs named commands (the boot entry, the
  Home key commands, the access files).
* The **daemon** has no LS2 role. Every platform request it makes is a `luna-send -a
  com.webos.applicationManager` child, because the installer's `regular` role cannot call on behalf
  of an application ([service.md](service.md#package)).
* The **boot entry** is a link in Homebrew Channel's `init.d`: to the installed script on 6–9, and
  on 5 and 10+ to a persistent copy under `/var/lib/webosbrew/homechooser/boot`, so an uninstall
  made while the watcher was stopped is still undone at the next boot. It only queues
  `systemd-run --unit=homechooser-home … homechooser-daemon --boot` and returns; all waiting
  happens inside the unit.
* On **webOS 6–9** the daemon also selects a generated copy of the stock key filter through configd
  (volatile, so a reboot returns the firmware's own list) and bind-mounts the home gate over
  `/usr/bin/com.webos.app.home`. The bind source is a copy in `/tmp/homechooser-runtime/home-gate`,
  so a reboot clears it.

## The HOME key and the idle path, per webOS family

"Idle" is what SAM does when the foreground becomes empty, for example when the last app closes.

```mermaid
flowchart LR
    subgraph w5["webOS 5.x"]
        k5["HOME"] --> f5["stock key filter launches<br/>the record's home"] --> l5["chosen launcher"]
        i5["foreground empty"] --> s5["Live TV"]
    end
    subgraph w69["webOS 6-9"]
        k69["HOME"] --> f69["generated systemUi-*.js<br/>selected through configd"] --> l69["chosen launcher"]
        i69["foreground empty"] --> s69["SAM relaunches<br/>com.webos.app.home"] --> g69["home gate"]
        g69 -- "full-screen launcher" --> l69b["chosen launcher"]
        g69 -- "overlay launcher" --> t69["last input<br/>Live TV or HDMI"]
    end
    subgraph w10["webOS 10.x"]
        k10["HOME"] --> f10["launchDefaultApp:<br/>record, profile override"] --> l10["chosen launcher"]
        i10["foreground empty"] --> s10["SAM launches the profile's idleApp"]
        s10 -- "full-screen launcher" --> l10b["chosen launcher"]
        s10 -- "overlay launcher<br/>(support false)" --> t10["last input<br/>Live TV or HDMI"]
        w10c["watcher"] -- "closes the LG Home the boot started,<br/>once, after the launcher is in front" --> h10["stock LG Home"]
        w10c -- "overlay launcher: once per boot,<br/>nothing full-screen in front" --> t10
    end
    subgraph w11["webOS 11 and later"]
        k11["HOME"] --> f11["C++ key filter,<br/>launchDefaultApp"] --> l11["chosen launcher"]
        i11["foreground empty"] --> s11["SAM launches the profile's idleApp"]
        s11 -- "full-screen launcher" --> l11b["chosen launcher"]
        s11 -- "overlay launcher<br/>(support false)" --> t11["last input<br/>Live TV or HDMI"]
        w11c["watcher"] -- "closes LG Home once,<br/>after the launcher is in front" --> h11["stock LG Home"]
        w11c -- "overlay launcher: once per boot,<br/>nothing full-screen in front" --> t11
    end
```

On 10.x and later SAM takes the idle app live from the system profile's `idleApp`, so the same
override that names the home also names the idle app ([settings-app.md](settings-app.md#what-is-written)).
An `idleApp` that names a missing app leaves a black screen when the last app closes, so the
override goes whenever the chosen launcher does. With `support` false SAM only falls back to the
last input once an app has been launched in the boot, so under an overlay launcher the watcher opens
the last input itself once after each boot, by the gate's rule
([settings-app.md](settings-app.md#the-last-input-after-a-boot-webos-10-and-later)).

| Family | HOME follows the choice through | Stock LG Home while a launcher is chosen |
| --- | --- | --- |
| 5.x | `general/defaultApps.home`, which the stock key filter launches directly | Not touched: the visible home, `com.webos.app.fullhome`, stays loaded until the next restart |
| 6–9 | A generated key filter that launches the chosen id ([home-key-hook.md](home-key-hook.md)) | Replaced by the home gate; the running instance is closed once |
| 10.x | The settings record and a system profile override with `home` and `idleApp` ([settings-app.md](settings-app.md#what-is-written)) | Preload off; closed once per boot and per choice after the launcher is safely in front; `idleApp` keeps SAM from starting it again |
| 11+ | Same as 10.x | Same as 10.x |

## The home gate decision

`homechooser-home-gate` runs as SAM's `com.webos.app.home` instance with the launch envelope as
`argv[1]`, decides, acts and exits 0 ([home-gate.md](home-gate.md#the-gate-program)).

```mermaid
flowchart TD
    begin(["SAM launches com.webos.app.home"]) --> env{"launch envelope<br/>parses?"}
    env -- "no" --> release
    env -- "yes" --> pre{"preload launch?"}
    pre -- "yes" --> nothing(["open nothing, exit 0"])
    pre -- "no" --> choice{"valid choice, not LG Home,<br/>homekey flag present?"}
    choice -- "no" --> release
    choice -- "yes" --> info{"getAppInfo of<br/>the choice answers?"}
    info -- "no" --> release
    info -- "yes" --> hot{"reason is hotKey?"}
    hot -- "yes" --> launcher["launch the chosen launcher"]
    hot -- "no" --> type{"defaultWindowType<br/>is overlay?"}
    type -- "no, full-screen" --> launcher
    type -- "yes" --> fg{"getForegroundAppInfo"}
    fg -- "no answer" --> release
    fg -- "an app in front" --> launcher
    fg -- "empty" --> last["launch the last input<br/>from eim, else Live TV"]
    launcher --> ok{"launch accepted?"}
    last --> ok
    ok -- "yes" --> done(["exit 0"])
    ok -- "no" --> release
    release["release: write the marker,<br/>lazy-unmount the gate,<br/>ask SAM for real stock Home"] --> done
```

Launches are made as `com.webos.app.home`. A release writes
`/tmp/homechooser-runtime/home-gate/released`, so the daemon does not mount again for the same
choice until the choice changes, the app applies again, or the TV restarts.

## Daemon lifecycle

```mermaid
stateDiagram-v2
    [*] --> Queued: boot, or the app starts the watcher
    Queued --> BootApply: systemd-run homechooser-home, daemon --boot
    BootApply --> Start: 6-9 wait for compositor and configd, select the generated filter, toast
    BootApply --> Start: 5 and 10+ nothing to apply
    Start --> Exit0: no usable choice, after the leftover-Home check
    Start --> Watching: access files applied, record and override checked, subscriptions open
    Watching --> Watching: choice change, re-apply record, override, filter and gate
    Watching --> Watching: settings event or 5 min tick, record repaired with backoff
    Watching --> StockWhileMissing: chosen app gone for 15 s, revert to stock and alert
    StockWhileMissing --> Watching: app installed again, re-apply
    Watching --> Restore: Home Chooser gone for 15 s
    StockWhileMissing --> Restore: Home Chooser gone for 15 s
    Restore --> Exit0: access removed, platform and filter stock, gate unmounted, state removed, toast
    Restore --> Exit1: a step failed, state kept
    Watching --> Exit0: SIGTERM from restore to LG Home, or the choice file removed
    Watching --> Exit1: settings subscription ended
    Exit1 --> BootApply: unit restart after 5 s, from the runtime copy if the package is gone
    Exit0 --> [*]: preload policy back on, gate unmounted
```

Alongside that state machine, while a third-party launcher is chosen:

* **Preload.** On 6 and later the watcher calls `setPreloadPolicy {isEnabled:false}` for
  `com.webos.app.home` at start and on each change, retried every 10 s up to 12 times at boot, and
  sets it back to enabled on every clean exit. Nothing is written; a reboot starts with the stock
  policy ([settings-app.md](settings-app.md#the-stock-home-is-not-preloaded)).
* **Stock Home close on 6–9**, after the gate is mounted:

```mermaid
flowchart TD
    m(["gate mounted, new or found at start"]) --> run{"SAM running list<br/>answers?"}
    run -- "no reply" --> retry["retry in 10 s,<br/>up to 12 times,<br/>and at every later gate sync"]
    retry --> run
    run -- "stock Home not running" --> confirmed(["confirmed, nothing more sent"])
    run -- "an instance is the gate itself" --> retry
    run -- "real stock Home running" --> close["closeByAppId com.webos.app.home<br/>forceMode true"]
    close -- "refused or no reply" --> retry
    close -- "returnValue true" --> settle["wait 20 s"]
    settle --> ran{"gate ran since<br/>the close?"}
    ran -- "yes" --> confirmed
    ran -- "no" --> fg{"foreground empty?"}
    fg -- "no, or no answer" --> confirmed
    fg -- "yes" --> launch["launch com.webos.app.home,<br/>so the gate decides"] --> confirmed
```

* **Stock Home close on 10 and later.** The watcher subscribes to SAM's foreground and life events and closes
  LG Home once per boot or choice change: for a full-screen launcher when it is in front, for an
  overlay launcher only on its own launch with a nonstock full-screen app beneath it.
* **Last input after boot on 10 and later.** With an overlay launcher chosen, 20 s after the watcher
  starts (retried every 10 s, up to 12 times, while SAM does not answer) and only if the foreground
  is empty or the overlay itself, the watcher launches the last input from eim (else Live TV), once
  per boot: a marker in `/tmp/homechooser-runtime` records the decision before the launch.
* **Restoring LG Home from the app** stops the unit first (SIGTERM: preload back on, gate
  unmounted), then removes the choice and restores the record, the profile override and the stock
  key filter list.

## Design rules

These hold across the code and are the reasons behind several choices above.

* **The platform's own default-app mechanism decides the home app** where the firmware has one: the
  `general/defaultApps` record, which SAM reads on every release from webOS 5, plus the system
  profile override that the compositor uses on 10 and later. The 6–9 key filter edit only changes
  the launch target; it adds no logic.
* **One launcher contract on every release.** A launcher declares `appCategory: ["home"]` and needs
  no per-release variant. Apps that patch the system themselves are not supported alongside Home
  Chooser.
* **Nothing persistent is changed in other services.** No edits to other services' environment
  files, units or binaries; no non-volatile configd writes. The key filter selection, the preload
  policy and the gate bind mount are runtime state that a reboot clears. Boot-time work runs only
  from Homebrew Channel's `init.d`. Home Chooser's own state lives in
  `/var/lib/webosbrew/homechooser`.
* **No restarts.** Nothing restarts the compositor, SAM or the TV, at selection or at boot.
* **LG Home always works.** A removed launcher, a removed Home Chooser, an unknown firmware shape or
  any error in the gate falls back to stock LG Home, and the owner is told once when a removal
  changed the Home button.
* **Root through Homebrew Channel only.** A rooted TV with Homebrew Channel is a requirement; the
  service is elevated once with `elevateService`.
