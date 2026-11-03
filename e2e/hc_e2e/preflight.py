# SPDX-License-Identifier: Apache-2.0
"""Before a VM run: the VM settings, waiting for root SSH, and removing foreign launchers."""
import json
import os
import re
import shlex
import time

from . import timing
from .constants import APP_ID

# Some HOME takeover apps hook micomservice/lginput2 instead of declaring home.
# HomeBack injects Frida into lginput2/micomservice; those hooks survive app removal until reboot.
KNOWN_HOME_TAKEOVERS = ('com.homebrew.homeback',)
# Live 10.3.1 appinfo: "Launcher", "Minimalist home screen", no appCategory.
KNOWN_UNCATEGORIZED_LAUNCHERS = ('works.partridge.webos-launcher',)


def launcher_removal_reason(app):
    """Candidate rule plus documented HOME takeover exceptions, only for developer apps."""
    ident = app['id']
    if ident in (APP_ID, 'org.webosbrew.hbchannel') or ident.startswith('org.webosbrew.hcfixture.'):
        return None
    category = app.get('appCategory') or []
    category = [category] if isinstance(category, str) else category
    if 'home' in category:
        return 'appCategory contains home'
    if ident in KNOWN_HOME_TAKEOVERS:
        return 'known HOME takeover app id'
    if ident in KNOWN_UNCATEGORIZED_LAUNCHERS:
        return 'known launcher without home category'
    return None


def foreign_launcher(app):
    return launcher_removal_reason(app) is not None


def startup_entry_owned(ident, name, target, content):
    """A package symlink, or an exact-id script that executes a package path."""
    roots = ['/media/developer/apps/usr/palm/' + kind + '/' + app
             for kind in ('applications', 'services') for app in (ident, ident + '.service')]
    if target:
        return any(target == root or target.startswith(root + '/') for root in roots)
    # Observed 10.3.1 HomeBack bootstrap calls its remote worker through LS2,
    # rather than executing a package path. Require its exact hook name and call.
    if ident == 'com.homebrew.homeback' and name == 'homeback':
        if re.search(r'(?m)^\s*output=\$\(luna-send\s+-n\s+1\s+-f\s+"luna://com\.homebrew\.homeback\.service/remote/start"\s', content):
            return True
    return (name in (ident, ident + '.service') and
            any(re.search(r'(?m)^\s*(?:exec\s+)?(?:sh\s+|bash\s+)?[\"\']?' + re.escape(root) + r'/', content)
                for root in roots))


def startup_hook_directories(dev):
    # Read the installed script; the package path is used on 10.3.1, where the
    # historical /var/lib copy is absent. Never guess a hook directory.
    for path in ('/var/lib/webosbrew/startup.sh',
                 '/media/developer/apps/usr/palm/services/org.webosbrew.hbchannel.service/startup.sh'):
        rc, startup = dev.sh('cat ' + path)
        if rc:
            continue
        dirs = re.findall(r'(?m)^\s*run-parts\s+(/var/lib/webosbrew/[A-Za-z0-9_./-]+)', startup)
        if not dirs:
            raise RuntimeError('no supported startup hook directory in ' + path)
        return path, list(dict.fromkeys(dirs))
    raise RuntimeError('cannot inspect installed Homebrew Channel startup')


def clean_startup_entries(dev, rec, ident):
    source, dirs = startup_hook_directories(dev)
    rec.note('startup hook directories read from installed script', source)
    for directory in dirs:
        rc, raw = dev.sh('find ' + shlex.quote(directory) + ' -mindepth 1 -maxdepth 1')
        if rc:
            raise RuntimeError('cannot inspect startup entries: ' + raw)
        for path in raw.splitlines():
            if os.path.dirname(path) != directory:
                raise RuntimeError('unexpected startup entry path: ' + path)
            _, target = dev.sh('readlink ' + shlex.quote(path))
            _, content = dev.sh('cat ' + shlex.quote(path))
            if startup_entry_owned(ident, os.path.basename(path), target.strip(), content):
                rc, out = dev.sh('rm -- ' + shlex.quote(path))
                rec.check('remove owned startup entry ' + path, rc == 0, out)
                rec.note('removed startup entry owned by ' + ident, path + ' -> ' + target.strip())
                if rc:
                    raise RuntimeError('startup entry removal failed: ' + path)
            else:
                rec.note('retained startup entry: ownership not proven for ' + ident, path)


def restart_after_launcher_cleanup(scenario, removed):
    if removed:
        scenario.rec.note('preflight restarting after launcher removal',
                          ', '.join(removed) + ': HOME hooks injected into running processes survive uninstall')
        if not scenario.beanjar_reboot('preflight launcher cleanup reboot'):
            raise RuntimeError('VM did not return after launcher cleanup')


def clean_launchers(dev, rec):
    installed = dev.installed()
    kept, removed = [], []
    for ident in installed:
        # Read the developer appinfo itself: SAM may omit appCategory on older releases.
        path = '/media/developer/apps/usr/palm/applications/' + ident + '/appinfo.json'
        rc, raw = dev.sh('cat ' + shlex.quote(path))
        if rc:
            raise RuntimeError('cannot read developer appinfo for %s: %s' % (ident, raw))
        app = json.loads(raw)
        if app.get('id') != ident:
            raise RuntimeError('appinfo id mismatch for ' + ident)
        if foreign_launcher(app):
            rc, out = dev.remove(ident)
            ok = rc == 0 and ident not in dev.installed()
            rec.note('preflight launcher removal %s version %s: %s' %
                     (ident, app.get('version'), launcher_removal_reason(app)), out)
            rec.check('preflight removed foreign launcher ' + ident, ok, out)
            if not ok:
                raise RuntimeError('foreign launcher removal failed: ' + ident)
            removed.append(ident)
            clean_startup_entries(dev, rec, ident)
        else:
            kept.append('%s version %s' % (ident, app.get('version')))
    rec.note('preflight retained developer apps (ours or non-launchers)', ', '.join(kept) or 'none')
    return removed


# ---------------------------------------------------------------------------------------------
# VM preparation: layers, LAN
# ---------------------------------------------------------------------------------------------
def prepare_vm(host, vm, rec, debug_mode='release'):
    """Refuse a busy machine, set the VM's layers, link and debug_mode.  The caller starts it.  debug_mode
    'release' (the default of --debug-mode) makes it a retail set: faultmanager shows no [crash] popup that
    would take key focus; 'keep' leaves the VM's own setting; 'debug' and 'event' set that."""
    try:
        running = host.running_vms()
    except RuntimeError as e:
        raise SystemExit(str(e))
    if running:
        raise SystemExit('VMs already running on %s, not touching anything: %s' % (host.name, running))
    s = host.settings(vm)
    layers = [x for x in (s.get('layers') or '').split(',') if x]
    want = {}
    # devmode: `beanjar ares-TOOL` as the developer-mode user (install, launch, the inspector forward);
    # hbchannel with devmode forwards the guest's root SSH for `beanjar ares-TOOL --root`
    missing = [x for x in ('devmode', 'hbchannel') if x not in layers]
    if missing:
        want['layers'] = ','.join(layers + missing)
    if s.get('link') != 'lan':
        want['link'] = 'lan'
    have = (s.get('debug_mode') or '(default)').strip()
    if debug_mode != 'keep' and have != debug_mode:
        want['debug_mode'] = debug_mode
    if want:
        rec.say('VM %s is off; settings %s' % (vm, want))
        rc, out = host.set_settings(vm, **want)
        rec.say(out.strip()[-300:])
        if rc:
            raise SystemExit('beanjar settings %s set failed (rc %s): %s' % (vm, rc, out.strip()[-200:]))


# wait_for_device states
READY, NO_ROOT_SSH, NO_HBCHANNEL, DOWN = 'ready', 'no-root-ssh', 'no-hbchannel', 'down'
WAIT_STATES = {
    READY: 'root SSH answers and Homebrew Channel is installed',
    NO_ROOT_SSH: "developer mode answers and lists Homebrew Channel, but root SSH (HBC's Dropbear, guest 22 = "
                 "host 6622) does not",
    NO_HBCHANNEL: 'root SSH answers, but Homebrew Channel is not installed',
    DOWN: 'neither root SSH nor developer mode answers',
}


def wait_for_device(dev, rec, timeout=300):
    """One of the states above: READY as soon as it is reached, else the last state seen at the timeout.
    Every change of state is said, so a timeout shows which probe never passed."""
    vm = dev.vm
    end = time.time() + timing.seconds(timeout)
    state = None
    while True:
        rc, out = dev.sh('echo up', 20)
        try:
            installed = dev.installed()
        except RuntimeError:
            installed = []
        if out.strip().endswith('up'):
            now = READY if 'org.webosbrew.hbchannel' in installed else NO_HBCHANNEL
        else:
            # the devmode device (prisoner, 9922) is Developer Mode's own Dropbear, not HBC's
            now = NO_ROOT_SSH if 'org.webosbrew.hbchannel' in installed else DOWN
        if now != state:
            state = now
            rec.say('VM %s: %s (%s)' % (vm, state, WAIT_STATES[state]))
        if state == READY or time.time() >= end:
            return state
        timing.sleep(5)


def failsafe_note(vm):
    return ("root SSH never answered while developer mode did: Homebrew Channel's startup.sh is most likely in "
            "failsafe mode (telnetd only, no Dropbear). It sets /var/luna/preferences/webosbrew_failsafe at the start "
            "of a boot and clears it about 12 s after its boot work, so a boot cut short before that (a VM stopped "
            "within seconds of starting) leaves the flag and the next boot runs failsafe; that boot clears the flag "
            "again after 15 s. Restarting %s once." % vm)
