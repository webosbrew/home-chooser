# Keeping the chosen launcher open

A study of what closes an owner-chosen launcher when it goes to the background, what keeps it open,
and why Home Chooser does not preload it. It rests on static reading of firmware roots (`memchute`,
`sam` and `webos-preload-manager` disassembly included) and runtime checks on emulator VMs. `[V]`
marks a string, file or code path read directly; `[V, VM]` a behaviour observed at runtime on an
emulator VM of that release; `[I]` an interpretation. Paths are relative to each firmware's
`rootfs/`. Releases are named by webOS version: 4.0 (W18R), 4.5 (W19P), 5.x (5.6.1, W20L), 6.x
(6.5.2, W21P), 7.x to 9.x (7.6, 8.3, 9.2), 10.x (10.3), 11.x (11.2) and 12.0.

## Conclusion

* On 1.5 GB ("lite") sets, the memory manager `memchute` closes a background app that is on none of
  its keep lists whenever another app is launched. A launcher that is to stay open needs its id on a
  keep list.
* The lists are read once, when `memchute` starts, from the first of a file at
  `/home/root/memorymanager-conf.json`, a file at `/etc/palm/memorymanager-conf.json` and configd.
  Changing them takes a restart of `memchute`, which has no visible side effect.
* A keep list protects against the launch-close only. Under real low memory `memchute` closes the
  launcher together with the other background apps.
* Home Chooser builds the keep list: a symlink in `/home/root` to a file in
  `/var/lib/webosbrew/homechooser` on 4.0 to 9.x, a volatile configd value on 10 and later.
* Home Chooser does not preload the launcher. A preloaded launcher opens no faster than a running
  one, and the preload manager takes a third-party app only through several gates of its own.

## What closes the launcher

* `memchute` (`usr/sbin/memchute`, `memchute.service`; registers `com.webos.memorymanager`) closes
  apps through `applicationManager/closeByAppId`; SAM maps that caller to the close reason
  `memoryReclaim`. Its messages are "request close by launch" with the suffix `(lite/policy)` or
  `(lite/all)`, "request close by memory low" and "memory critical", and for the emergency paths
  "Close all background apps by Emergency" and "Close all apps by Emergency". [V]
* configd `com.webos.memorymanager.clearBackgroundApp` is `false` in the base layer and `true` in
  the `*-lite` and `*-mini` distro layers. With it, a launch closes the background apps that are on
  no keep list. [V]
* With the launcher in the background and another app launched (the stock browser, or Live TV where
  the browser would not start), the launcher was gone from SAM's `running` list on the 4.0, 5.x, 6.x
  and 7.x VMs while memory was plentiful (about 900 MB usable on 5.x). [V, VM] On 7.x, launching
  Live TV, which was already running, did not close it every time, so the launch-close is not
  reliably reproduced with an app that is already running. [V, VM] On the 10.x VM a background
  launcher survived the browser launch with stock lists too. [V, VM]
* At default log levels the release logs name nobody who closed the launcher: the
  `request close by ...` lines are absent on every VM checked. [V, VM]

## The keep lists

| Release | Where the lists live | Keep lists |
| --- | --- | --- |
| 4.0 | `etc/palm/memorymanager-conf.json` | `KeepOnLaunch` [systemmusic, voice], `KeepAlive` [livehbbtv, livedmost, liveibb]; no `KeepOnLaunchEx` |
| 4.5 | same | adds `KeepOnLaunchEx` [home, googleassistant, fullhome] |
| 5.x | same | `KeepOnLaunchEx` [home, fullhome, google.assistant] |
| 6.x | same | `KeepOnLaunchEx` [home, google.assistant, amazon.alexapr], no `fullhome`; adds `QuickLaunch`, `HomeGroup`, `KeepAppHib` |
| 7.x to 9.x | same | familycare added to both lists; `QuickLaunch` [google.assistant, amazon.alexapr] |
| 10 and later | configd layer `etc/configd/layers/base/com.webos.memorymanager.json` | the same names; `home` and `seniorhome` in `KeepOnLaunchEx`; no conf file in the root |

All [V]. The ids are shortened: the apps are `com.webos.app.*`, apart from `google.assistant` and
`amazon.alexapr`. On 4.0 the only lists that exempt anything are `KeepOnLaunch` and `KeepAlive`.
`getPolicy` on `com.webos.memorymanager` lists `keepAlive`, `keepAppHib`, `noLaunchThreshold`,
`inputApp` and `keepOnLaunch`, not `KeepOnLaunchEx`, so it cannot confirm an edit of that list. The
luna API has no setter for any list. [V]

What the lists do when `memchute` picks what to close (5.6.1 disassembly, `PriorityGroup::selectList`):

* An id in `KeepOnLaunchEx` is skipped as a close candidate unconditionally; an id in `KeepOnLaunch`
  is skipped unless the memory state is the worst one. `KeepAlive` puts an app in the highest
  priority group. Preloaded apps sit in the lowest group and are reclaimed first. [V]
* The emergency functions (`closeAppsByEmgy`, `closeAllAppsByEmgy`) go through
  `MemUnitListManager::selectMemUnitList` and do not call the keep checks themselves. [I]
* On small-RAM profiles `lowMemory.forceCloseAppList` (`home`, `inputcommon`, `teletext`, `livetv`,
  `hdmi1` and others) names apps that are force-closed on low memory. [V]

### Real low memory

A launcher on `KeepOnLaunchEx` survived the launch of another app on 5.x, 6.x and 7.x, and did not
survive real low memory. Under bounded memory pressure (tmpfs fills in steps) it stayed listed
through the "medium" level and was closed together with the other background apps as usage reached
the "low" level: at about 87 MB usable on 5.x, 90 to 98 MB on 6.x and 107 to 117 MB on 7.x. The
foreground app stayed, and the launcher was not started again after the pressure was released.
[V, VM] An unbounded allocator froze a 1.5 GB guest without `memchute` preventing it. [V, VM] A keep
list is protection against the launch-close, and no more.

### SAM's `keepAliveApps` does not help

configd `com.webos.applicationManager.keepAliveApps` (5.6.1 base layer: `tvguidejpn`, `voice`,
`voiceagent`, `livemenu`, `tvhotkey`, `dvrpopup`, `home`, `fullhome`, `airplay`, `google.assistant`,
`amazon.alexapr`; in the system profile on 11.2) is read by `Settings::IsKeepAliveApp`. In
`AppLifeManager::close_by_app_id` a listed app with the force flag false is still closed when the
caller is `com.webos.memorymanager`, `com.webos.appInstallService` or `com.webos.applicationManager`,
closed from `com.webos.surfacemanager.windowext` only for the reason `recent`, and merely paused for
every other caller. The list blunts closes from other callers (Back, a launcher's own close) and
does not protect against `memchute`. [V] The appinfo field `keepAlive` is not read by SAM;
`keepAlive` is a launch parameter. [V]

## Where `memchute` reads the lists

* `PolicyManager::earlyLoadPolicy` and `loadPolicy` use `/home/root/memorymanager-conf.json` if it
  exists, else `/etc/palm/memorymanager-conf.json`, else (6.x and later) the reply of
  `configd/getConfigs` for `com.webos.memorymanager.*`. A file replaces the lists whole, with no
  merge. Read in the 5.6.1 and 10.3 disassembly and in the strings of every binary from 6.x to 12.0.
  [V]
* There is no subscription. The file or the configd value is read once at start. [V] A restart
  (`systemctl restart memchute`; `initctl restart memchute` on 4.0) returns at once, and
  `getCurrentMemState` answers on the first call after it. [V, VM]
* 12.0, a file made of the 60 configd keys with the prefix stripped and an id added, in place of the
  configd lists: `getPolicy` showed the id under `keepOnLaunch`; with the file removed and `memchute`
  restarted the reply was byte-identical to stock. [V, VM]
* The volatile configd route, 12.0: `setConfigs` on `com.webos.memorymanager.KeepOnLaunch` with
  `"volatile": true` (called as `com.webos.applicationManager`), then a restart of `memchute`,
  showed the id in `getPolicy`. `KeepOnLaunchEx` took a volatile value and read it back
  (`getPolicy` cannot show it). A volatile `null` returns a key to the layer value, and after a
  reboot the stock values are back. [V, VM] On the 10.x VM the same route held the id in
  `KeepOnLaunchEx` while the launcher was chosen and dropped it when LG Home was chosen. [V, VM]

### Restart side effects

* `webos-preload-manager` (`preload-manager.service`, which requires `memchute.service`) restarts
  with `memchute` on every release. [V, VM] A restart drops the preload manager's non-permanent
  changes, such as a `setPreloadPolicy` that disables the stock home's preload. [V, VM]
* The foreground app, the running list and the screen are the same before and after, and no popup
  appears. The launcher kept its pid through the launch that followed. [V, VM]
* `memchute` logs `Attempt to get array size of non-array` (PBNJSON) at every start, stock or not.
  [V, VM]
* A failed start of `memchute` is brought back by the unit's `Restart=on-failure`. [V, VM]
* The file survives a reboot, and the boot reads it with no restart. [V, VM]

## Symlinks and invalid files

The conf file in `/home/root` can be a symlink to a file in a directory that Home Chooser owns, so
that removing the directory removes the list.

* 4.0, 5.x and 6.x: the link is followed, on a restart and at boot (6.x logs `Load conf file from :
  /home/root/memorymanager-conf.json`), and the launcher survives the launch of another app. [V, VM]
* 12.0: a symlink at that path is ignored, absolute or relative, to a file in another directory or
  beside it. The same bytes as a regular file are read. Nothing is logged. [V, VM] `memchute`
  presumably checks for a regular file without following links. [I]
* A dangling link: on 6.x the log says `Load conf file from : /etc/palm/memorymanager-conf.json` and
  the stock lists apply, with no line about the missing target; on 4.0 and 5.x the launcher is
  closed by the launch again. Restarts and reboots stay healthy (`NRestarts=0`). On 12.0 the reply
  is the stock one, as for any link. [V, VM]
* An empty or truncated file (regular or linked), 6.x: the log still says the file was loaded, with
  no error, and the policy is not the stock one: the launcher survived the launch, and the close
  reason in the log changed from `launch by policy(lite)` to `launch by required memory(adv)`. [V, VM]
  On 12.0 an empty or truncated regular file left `getPolicy` identical to stock. [V, VM] The file
  must therefore be valid JSON, since an invalid one changes the policy on 6.x.
* `/home` and `/var` are one filesystem on the releases tried (on 12.0 both are bind mounts of the
  same partition, and a hard link between them fails with a cross-device error), so the pointer is a
  symlink. [V, VM]

## Preloading

`webos-preload-manager` (`usr/bin/webos-preload-manager`, service `com.webos.service.preloadmanager`;
present on 4.0 to 12.0) launches apps hidden so that they open quickly.

* The candidates are the `supportAppList` entries (`id`, `staticPriority`, `requiredMemory`,
  `preloadType`, `preloadMode`, `isEnabled`, `isPinUp`, `extPolicy`), from configd on 4.0 to 9.x and
  from the system profile `preloadApp` on 11 and later (10.3 has both). A third-party id is in none.
  `setPreloadPolicy` takes `application{id, isEnabled}` and a `permanent` flag and answers
  "Cannot find <id>" for an id not in the list, so it cannot add an app. [V]
* The list and `maxPreloadApp` (3 on 1 GB sets, 5 or 6 elsewhere) are read once, as is
  `/var/preferences/webos-preload-manager-conf.json`, whose `allowedApps` map is the "allowed" gate:
  an app that is not allowed is skipped ("Not allowed") even with a list entry. The manager marks a
  container app allowed once it has seen it in the foreground, and an app whose appinfo has a
  category qualifies only as the user's default for that category. [V, I]
* On 10, 11 and 12 the manager also subscribes to `systemprofile/getProfiles` for `preloadApp`. With
  an extra file under `/var/webos-profile/override/` and `systemprofile/debug/rescan`, `getProfiles`
  listed the added app at once, and the running manager did not act on it within 20 to 90 s on
  10.3.1, 11.2.0 and 12.0. After a restart of the manager the app was a candidate, with `isAllowed`
  false until its id was added to `allowedApps`. [V, VM]
* Further gates met with the app added: `maxPreload(2)` on 10.3.1, with the stock home, Live TV and
  an input already holding the slots; and, with the browser or the screensaver in front, `memchute`
  answering `getCustomThreshold` with errorCode 9 ("get custom threshold blocked") and the manager
  logging "Not preload this time". With the Homebrew Channel in front the app was preloaded
  (`launchHidden`, `preload: "partial"`, `noSplash`; SAM state `preloading`, then `background`), and
  it stayed through the browser coming to the front. [V, VM]
* On 10.3.1 and 11.2.0 the manager preloaded the app again within about 10 s of every `closeByAppId`
  while it was not the foreground app. A launch of a preloaded app makes SAM mark it `relaunching`,
  not `launching` (12.0). [V, VM]
* Preloaded apps are the first to be reclaimed unless they are also on a keep list. [V, I]

### Timing

Milliseconds from the Home key to the launcher's window shown, five runs each, 10.3.1 and 11.2.0
VMs, the key sent through the hypervisor's remote:

| State of the launcher | 10.3.1 | 11.2.0 |
| --- | --- | --- |
| Not running (cold) | 8140 to 8171 | 8150 to 8176 |
| Running in the background, not preloaded | 118 to 149 | 122 to 170 |
| Preloaded | 128 to 174 | 115 to 134 |

[V, VM] The Home request reaches SAM after 80 to 125 ms in every case. In the cold case WAM reports
`firstFrameVisuallyCommitted` after about 0.2 s while the compositor shows the window at about
8.15 s, constant to within 30 ms. That wait looks like a fixed delay or a display effect of the VM
and is not a TV figure. [I] Preloaded and merely running agree within the noise of tens of
milliseconds: a preloaded app is a running background app. Preloading only helps against a cold
start, which the keep list avoids.

## Decision

* Home Chooser builds the keep list. The launcher's id goes on `KeepOnLaunchEx` where that list
  exists (4.5 and later) and on `KeepOnLaunch` on 4.0: through a file linked from
  `/home/root/memorymanager-conf.json` into `/var/lib/webosbrew/homechooser` on 4.0 to 9.x, and
  through a volatile configd value on 10 and later, where the root has no conf file and, on 12.0,
  a link would be ignored. `memchute` is restarted when the list changes. Choosing LG Home or
  uninstalling takes the list back to stock: on the VMs the link and the directory were gone and
  `memchute` restarted within about 30 seconds of an uninstall. [V, VM] A reboot with the list in
  place needs no restart on 4.0 to 9.x; on 10 and later the value is set again after boot. [V, VM]
* Home Chooser does not preload the launcher. The preload manager's list does not take a
  third-party id, its configuration is read once, and its gates (`allowedApps`, the slots, the
  memory manager's block) are its own. Against a running launcher the gain is nothing, and a keep
  list already keeps the launcher running.
* Nothing the platform offers keeps the launcher through real low memory.
