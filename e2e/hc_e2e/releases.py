# SPDX-License-Identifier: Apache-2.0
"""What each webOS release is expected to do: the Home key hook, the gate, idleApp, the stock id."""
import json
import re

from .constants import BOOT_INPUT_FAILED, BOOT_INPUT_LAUNCHED, FULLHOME, STOCK
from .evidence import parse_luna


def device_text_forms(text):
    """A minified JS bundle can spell Unicode as JSON escapes while source files use UTF-8."""
    escaped = json.dumps(text, ensure_ascii=True)[1:-1]
    return (text,) if escaped == text else (text, escaped)


def uses_home_key_hook(major):
    return 6 <= major <= 9


def uses_home_gate(major):
    """6.x to 9.x with a launcher chosen: the daemon binds homechooser-home-gate over stock Home's
    binary (docs/home-gate.md), so stock Home is not left running."""
    return 6 <= major <= 9


def closes_stock_home(major):
    """10.x and later: the watcher closes LG Home once per boot and per choice, after the chosen launcher
    is in front (service/src/watcher/preload.c); idleApp keeps sam from bringing it back."""
    return major >= 10


def boot_input_launches(lines):
    """The watcher's last-input launches in a log (service/src/watcher/input.c): [(app id, accepted)]."""
    out = []
    for line in lines:
        for prefix, accepted in ((BOOT_INPUT_LAUNCHED, True), (BOOT_INPUT_FAILED, False)):
            at = line.find(prefix)
            if at >= 0:
                rest = line[at + len(prefix):].split()
                out.append((rest[0] if rest else '', accepted))
    return out


def boot_input_verdict(lines, foreground):
    """(ok, detail) after a boot with an overlay launcher on 10+: this boot's watcher log has exactly one
    last-input launch, accepted, and that app is SAM's foreground (the overlay is not named there)."""
    launches = boot_input_launches(lines)
    if len(launches) != 1:
        return False, '%d last-input launches in the log, want 1: %r' % (len(launches), launches)
    app, accepted = launches[0]
    if not accepted:
        return False, 'the launch of %s failed' % app
    if foreground != app:
        return False, 'foreground %r, not the launched %s' % (foreground, app)
    return True, '%s launched once and in front' % app


def profile_idle_app(text):
    """The idleApp profile in a getProfiles reply (luna-send output); None when absent or unreadable."""
    reply = parse_luna(text) or {}
    for node in (reply.get('profiles'), reply):
        if isinstance(node, dict) and isinstance(node.get('idleApp'), dict):
            return node['idleApp']
    return None


def idle_app_verdict(idle, launcher, overlay):
    """(ok, what is wanted) for the system profile's idleApp with a launcher chosen on 10.x and later
    (docs/settings-app.md "What is written"): the launcher, or support false for an overlay launcher."""
    if overlay:
        return isinstance(idle, dict) and idle.get('support') is False, 'support false (overlay launcher)'
    return (isinstance(idle, dict) and idle.get('support') is True and idle.get('appId') == launcher,
            'support true, appId ' + launcher)


def idle_app_stock(idle):
    """idleApp back to the stock value of every 10.3.1 / 11.2.0 device layer: LG Home, support true."""
    return isinstance(idle, dict) and idle.get('support') is True and idle.get('appId') == STOCK


def platform_stock(major, record_home, stock_id, override, idle):
    """Nothing Home Chooser writes names a launcher any more: the record's home is stock (5-9: the stock id,
    10+: null or unset) and, on 10+, the override file is gone and getProfiles' idleApp is LG Home again."""
    if not stock_record_ok(major, record_home, stock_id):
        return False
    return major < 10 or (override == 'no' and idle_app_stock(idle))


def stock_home_id(major, fullhome_installed):
    """The id restore/removal/uninstall write as `home` on 5.x to 9.x (docs/settings-app.md): fullhome
    if it is installed, else com.webos.app.home.  10+ write null: None."""
    if 5 <= major <= 9:
        return FULLHOME if fullhome_installed else STOCK
    return None


def stock_record_ok(major, home, stock_id=None):
    """The record's `home` after a restore, a launcher removal or a Home Chooser uninstall.
    5.x: a stock launcher (unchanged).  6.x to 9.x: the stock id, not null (34a4ff9); exactly
    `stock_id` when it is known.  10+: null or unset."""
    if major == 5:
        return home in (FULLHOME, STOCK)
    if uses_home_key_hook(major):
        return home == stock_id if stock_id else home in (FULLHOME, STOCK)
    return home in (None, '', 'null')


def stock_baseline_ok(major, home):
    """A stock image before Home Chooser: on 6.x to 9.x the record may be unset (fresh image) or the
    stock id an earlier uninstall left (it stays: stock firmware ignores it)."""
    if uses_home_key_hook(major):
        return home in (None, '', 'null', FULLHOME, STOCK)
    return stock_record_ok(major, home)


def expects_reboot_dialog(major, mounted_before):
    """Every supported release applies immediately; no reboot prompt is expected."""
    return False


def parse_reboot_ask(text):
    """The service's REBOOT_ASK PmLog lines (service/src/service/system.c on_reboot_prompt) -> what was asked and how
    the notification service answered:
    {'message', 'buttons' (list), 'alert' ('accepted'|'refused'|None), 'alert_id', 'toast' ('accepted'|'refused'|None)}.
    The request line carries no private parameters; replies are the createAlert / createToast lines with their JSON."""
    out = {'message': None, 'buttons': [], 'alert': None, 'alert_id': None, 'toast': None}
    for line in (text or '').splitlines():
        if 'REBOOT_ASK' not in line:
            continue
        m = re.search(r'createAlert request: message: (.*?); buttons: (.*)$', line)
        if m:
            out['message'] = m.group(1).strip()
            out['buttons'] = [re.sub(r'\s*\(.*$', '', b).strip() for b in m.group(2).split(',')]
            continue
        m = re.search(r'notification/(createAlert|createToast) (accepted|refused)', line)
        if m:
            kind = 'alert' if m.group(1) == 'createAlert' else 'toast'
            out[kind] = m.group(2)
            if kind == 'alert':
                a = re.findall(r'\\?"alertId\\?"\s*:\s*\\?"([^"\\]+)', line)
                if a:
                    out['alert_id'] = a[-1]
    return out
