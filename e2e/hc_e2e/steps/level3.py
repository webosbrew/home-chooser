# SPDX-License-Identifier: Apache-2.0
"""--api-check, level 3 (docs/launcher-api.md): getAppLifeEvents, the systemApp / mediumLargeIcon /
extraLargeIcon fields and getIcons with the new sizes.  The parsing and the verdicts are hc_e2e.level3."""
import json
import shlex

from .. import level3, timing
from ..api_spec import API_VERSION, DISPOSABLE_ID, api_refused, spec_reply_problems
from ..constants import SVC_ID


class Level3Steps:
    STREAM_LOG = '/tmp/hc-e2e-l3.log'
    STREAM_PID = '/tmp/hc-e2e-l3.pid'

    # -- smoke: the stream refuses a plain call; the new fields are well formed
    def api_level3_smoke(self, label, apps_reply=None):
        r = self.rec
        rep, raw = self.api('getAppLifeEvents', {})
        r.check('%s: getAppLifeEvents without subscribe is refused with badRequest (it is a stream)' % label,
                api_refused(rep) and rep.get('errorCode') == 'badRequest' and rep.get('apiVersion') == API_VERSION,
                raw[:400])
        apps = (apps_reply or {}).get('apps')
        if not isinstance(apps, list):
            apps = (self.api('listApps')[0] or {}).get('apps') or []
        problems = level3.list_field_problems(apps, self.launcher)
        n, system, user, medium, extra = level3.field_census(apps)
        r.check('%s: listApps level 3 fields are well formed (systemApp boolean, mediumLargeIcon and extraLargeIcon strings)'
                % label, not problems and bool(apps),
                '; '.join(problems) or '%d apps; systemApp on %d (true %d, false %d); mediumLargeIcon named by %d, '
                'extraLargeIcon by %d' % (len(apps), n, system, user, medium, extra))
        if n:
            r.check('%s: systemApp tells LG\'s own apps (true) from installed ones (false)' % label, system > 0 and user > 0,
                    'true %d, false %d' % (system, user))
        else:
            r.note('%s: this platform gives no systemApp (the field is omitted, as documented)' % label, '')
        return apps

    # -- full: getIcons with the new sizes, read against what listApps says
    def api_level3_icons(self, label, apps):
        for size in ('extraLargeIcon', 'mediumLargeIcon'):
            self.api_level3_icon_size(label, apps, size)

    def api_level3_icon_size(self, label, apps, size):
        r = self.rec
        named, bare = level3.icon_sample(apps, size, limit=len(apps))
        # what the service must answer comes from the device's own files: a name in appinfo is not a file
        files = level3.icon_files(apps, size)
        if files:
            rc, out = self.dev.sh(level3.icon_probe_command(list(files.values())))
            readable = level3.icon_probe_readable(files, out)
        else:
            readable = set()
        # an app named without a folderPath cannot be looked at: it is left out of the sample
        named = [i for i in named if i in files]
        present = [i for i in named if i in readable][:6]
        missing = [i for i in named if i not in readable][:2]
        census = 'named by %d, file present on %d' % (len(named), len([i for i in named if i in readable]))
        if not present:
            r.note('%s: getIcons size %s: %s; no app has a readable file of this size, so no data is expected'
                   % (label, size, census), '')
        sample = present + missing
        items = [{'id': i, 'size': size} for i in sample + ([bare] if bare else [])]
        if not items:
            r.note('%s: getIcons %s: no app to ask about' % (label, size), '')
            return
        rep, raw = self.api('getIcons', {'items': items})
        if (rep or {}).get('returnValue') is not True:
            problems, data = ['getIcons refused: %s %s' % ((rep or {}).get('errorCode'), (rep or {}).get('errorText'))], 0
        else:
            problems, data = level3.icons_problems(items, rep, set(sample), set(present))
        r.check('%s: getIcons size %s: data for apps that name one, noIcon for one that does not' % (label, size),
                not problems, '; '.join(problems) or '%d of %d items returned a picture (%s; asked about %d with a '
                'file, %d named without a file: noIcon as expected; without a name: %s); %d reply bytes' % (
                    data, len(items), census, len(present), len(missing), bare, len(raw.encode())))

    # -- the live stream: a subscription held across a launch and a close of the disposable app
    def stream_begin(self):
        """Subscribe to Home Chooser's getAppLifeEvents as the launcher (`luna-send -a <launcher> -i`), the
        replies going to a file on the device."""
        cmd = ('kill $(cat %s) 2>/dev/null; rm -f %s %s; nohup sh -c %s > /dev/null 2>&1; sleep 1'
               % (self.STREAM_PID, self.STREAM_LOG, self.STREAM_PID, shlex.quote(
                   'luna-send -a %s -i %s %s > %s 2>&1 < /dev/null & echo $! > %s' % (
                       shlex.quote(self.launcher), shlex.quote('luna://%s/getAppLifeEvents' % SVC_ID),
                       shlex.quote('{"subscribe":true}'), self.STREAM_LOG, self.STREAM_PID))))
        self.dev.sh(cmd)

    def stream_read(self):
        rc, out = self.dev.sh('cat %s 2>/dev/null' % self.STREAM_LOG)
        return level3.parse_stream(out), out

    def stream_end(self):
        self.dev.sh('kill $(cat %s) 2>/dev/null; rm -f %s %s; true' % (self.STREAM_PID, self.STREAM_LOG, self.STREAM_PID))

    def stream_check(self, label, appid=DISPOSABLE_ID):
        """After a launch and a close of `appid` during stream_begin(): the acknowledgement and the events."""
        r = self.rec

        def done():
            replies, _ = self.stream_read()
            _, events = level3.split_stream(replies)
            seen = [e.get('event') for e in events if e.get('appId') == appid]
            return replies if any(k in level3.START_EVENTS for k in seen) and any(k in level3.END_EVENTS for k in seen) else None
        self.wait(done, 20, 2)
        replies, raw = self.stream_read()
        problems = level3.stream_problems(replies, appid, lambda rep: spec_reply_problems('getAppLifeEvents', rep) or [])
        r.check('%s: getAppLifeEvents subscribe: the acknowledgement, then launch and close events of %s, each reply '
                'valid against the spec' % (label, appid), not problems,
                '%s; %s\n%s' % ('; '.join(problems) or 'ok', level3.stream_summary(replies, appid), raw[-1200:]))
        return not problems
