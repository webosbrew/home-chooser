# SPDX-License-Identifier: Apache-2.0
"""Ids, device paths, the product texts and the fixed waits the runner asserts against."""
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # e2e/
REPO = os.path.dirname(HERE)
APP_ID = 'org.webosbrew.homechooser'
SVC_ID = APP_ID + '.service'
TVPOWER = 'luna://com.webos.service.tvpower/'
SCREENSAVERS = ('com.webos.app.screensaver', 'com.webos.app.videoads')
TVPOWER_REBOOT_URI = 'luna://com.webos.service.tvpower/power/reboot'
STOCK = 'com.webos.app.home'
CHOICE = '/var/lib/webosbrew/homechooser/home'
FLAG = '/var/lib/webosbrew/homechooser/homekey'
HOOK_CACHE = '/var/lib/webosbrew/homechooser/hook'
HOOK_STALE = '/var/lib/webosbrew/homechooser/hook-stale'
INITD = '/var/lib/webosbrew/init.d/homechooser'
BOOT_DIR = '/var/lib/webosbrew/homechooser/boot'
BOOT_SCRIPT = '/media/developer/apps/usr/palm/services/' + SVC_ID + '/scripts/homechooser'
OVERRIDE = '/var/webos-profile/override/org.webosbrew.homechooser.json'
GET_PROFILES = 'luna://com.webos.service.systemprofile/getProfiles'
APPDIR = '/media/developer/apps/usr/palm/applications/' + APP_ID
SCRIPTS_DIR = '/media/developer/apps/usr/palm/services/' + SVC_ID + '/scripts'
HB_ELEVATE = '/media/developer/apps/usr/palm/services/org.webosbrew.hbchannel.service/elevate-service'


def uninstall_remnant_paths():
    """Files owned by Home Chooser that must disappear after watcher restoration."""
    paths = [HOOK_CACHE, HOOK_STALE, BOOT_DIR]
    for root in ('/var/luna-service2-dev', '/var/luna-service2'):
        paths.append(root + '/api-permissions.d/org.webosbrew.homechooser.launcher.api.json')
    return paths

# Stock-home preload policy (service/src/watcher/preload.c; docs/settings-app.md "The stock home is
# not preloaded").  On 6.x to 9.x the home gate (docs/home-gate.md) keeps it away: the gate is mounted,
# the watcher logs "home gate mounted" and closes a stock home the boot started (a bounded retry), and
# none runs afterwards.  On 10.x and later the profile override's idleApp names the launcher (support
# false for an overlay launcher), so sam no longer relaunches LG Home as the idle app, and the watcher
# closes it once per boot and per choice, after the launcher has a safe foreground.  The skip line is
# only logged on 6.x to 9.x without a gate.
SKIP_LINE = 'stock home left running'
GATE_LINE = 'home gate mounted'
GATE_TARGET = '/usr/bin/com.webos.app.home'
PRELOAD_SETTLE = 60   # seconds to wait after a switch/boot before sampling
PRELOAD_SAMPLE = 60   # seconds the stock home is watched for
# 10+, overlay launcher: once per boot the watcher launches the last input (service/src/watcher/input.c),
# about 20 s after it starts, longer while SAM does not answer (retries every 10 s, 12 at most).
BOOT_INPUT_LAUNCHED = 'last input: launched '
BOOT_INPUT_FAILED = 'last input: launch of '
BOOT_INPUT_WAIT = 240  # seconds after the boot checks start
PANEL_FRONT_WAIT = 30  # seconds one Home Chooser open may take to reach the front before the one retry
PANEL_HOLD = 4  # seconds Home Chooser must stay in front after it got there (a card mapping beneath closes it)
FOREGROUND_SETTLE = 20  # seconds to wait for an app just launched (Home key) to be SAM's foreground app
FULLHOME = 'com.webos.app.fullhome'


# ---------------------------------------------------------------------------------------------
# What the product says and writes (pure; e2e/test_e2e_logic.py checks them against the sources)
# ---------------------------------------------------------------------------------------------
DONE_TEXT = 'Done. Press Home to try it.'
# The app has no restart dialog of its own: the service raises a system alert, the app toasts this and,
# while a restart is pending, shows RESTART_NOTE and a "Restart now" row (app/src/views/useHomeChooser.js).
REBOOT_ASKED_TEXT = 'Done. Restart the TV to finish.'
RESTART_NOTE = {'apply': 'The Home button changes after a restart. Use Restart now — turning the TV off with the remote may not restart it.',
                'remove': 'LG Home is on the Home button now. A restart removes the hook.'}
RESTART_NOW = 'Restart now'
REBOOT_BUTTONS = ('Reboot', 'Later')
# the service's system alert (or the toast when the alert is refused; service/src/policy.c hc_reboot_message)
REBOOT_PROMPT = {'apply': 'Restart the TV to finish setting up the Home button.',
                 'remove': 'Restart the TV to finish removing the Home button patch.'}
# the watcher (service/src/watcher/notify.c): the same on every release, no restart mentioned
REMOVAL_ALERT = '%s was removed, so the Home button opens LG Home again.'
UNINSTALL_TOAST = 'Home Chooser was removed. The Home button opens LG Home again.'
OLD_RESTART_TEXTS = ('Restart the TV to finish the change.', 'Restart the TV to put LG Home back on the Home button.',
                     'The screen will blink once.', 'Restart the TV later to finish.', 'Restart the TV?')
# (text, file relative to the repo that must contain it)
TEXT_SOURCES = [
    (DONE_TEXT, 'app/src/views/useHomeChooser.js'),
    (REBOOT_PROMPT['apply'], 'service/src/policy.c'),
    (REBOOT_PROMPT['remove'], 'service/src/policy.c'),
    (REMOVAL_ALERT, 'service/src/watcher/notify.c'),
    (UNINSTALL_TOAST, 'service/src/watcher/restore.c'),
    (BOOT_INPUT_LAUNCHED, 'service/src/watcher/input.c'),
    (BOOT_INPUT_FAILED, 'service/src/watcher/input.c'),
]

SCREENSAVER_ID = 'com.webos.app.screensaver'
