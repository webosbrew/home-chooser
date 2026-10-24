#!/usr/bin/env python3
"""Pattern x firmware matrix for the minimal Home-key hook (offline, read-only).

usage: python3 -I matrix.py corpus.tsv > matrix.txt

Each pattern is counted in one file per build. Output cells:
  1    matches exactly once
  0    no match (n/a when the file is absent: '-')
  N>1  ambiguous
Structure checks print 'ok' / 'BAD'.
"""
import re
import sys
import os

LIT = r'"com\.webos\.app\.home"'
Q = 'usr/lib/qml/'
SET = Q + 'WebOSCompositorBase/global/Settings.qml'
KF = Q + 'WebOSCompositor/controllers/StarfishKeyFilter.qml'
SUI = Q + 'KeyFilters/systemUi.js'
APL = Q + 'KeyFilters/appLaunch.js'
VSC = Q + 'WebOSCompositor/controllers/StarfishViewStateController.qml'
FSC = Q + 'WebOSCompositor/controllers/StarfishFullscreenController.qml'
REC = Q + 'WebOSCompositor/controllers/fullscreen/StarfishRecents.qml'
BLS = Q + 'WebOSCompositor/services/StarfishBaseLunaServiceAPI.qml'
FWI = Q + 'WebOSCompositor/services/base/StarfishFocusedWindowInfoMgr.qml'
OPV = Q + 'WebOSCompositor/services/base/StarfishOverlayPlayVideoStateMgr.qml'
GES = Q + 'WebOSCompositor/views/StarfishGestureUIFeedbackView.qml'
HV = Q + 'WebOSCompositor/views/StarfishHomeView.qml'
NUD = Q + 'WebOSCompositor/views/StarfishNudgeView.qml'

M = re.M
# (id, file, regex, flags, tier, description)
PATTERNS = [
    # --- tier 1: the minimal hook ---------------------------------------------------------
    ('S1', SET, r'^import WebOSCompositorBase 1\.0$', M, 1, 'Settings: import line (WebOSServices goes after it)'),
    ('S2', SET, r'^    function subscribe\(', M, 1, 'Settings: subscribe() exists (helpers use it)'),
    ('S3', SET, r'^}\n?\Z', M, 1, 'Settings: file ends with the root object close (insert before it)'),
    ('S4', SET, r'^    id: root$', M, 1, 'Settings: root id'),
    ('S5', SET, r'        return systemSettings\.subscribeOnDemand\(type, method, key, returnRaw\);\n    \}\n\n', 0, 1, 'Settings: today\'s block anchor'),
    ('K1', KF, r'^[ \t]*readonly property var launch: function\(appId, params, checkUpdateOnLaunch, autoInstallation, reason\) \{$', M, 1, 'KeyFilter: applicationManager.launch header'),
    ('K2', KF, r'^[ \t]*readonly property var checkLaunch:[ \t]+function\(appId, params, checkUpdateOnLaunch, autoInstallation\) \{$', M, 1, 'KeyFilter: applicationManager.checkLaunch header'),
    ('K3', KF, r'^[ \t]*readonly property QtObject applicationManager: QtObject \{$', M, 1, 'KeyFilter: applicationManager group'),
    ('K4', KF, r'^import WebOSCompositorBase 1\.0$', M, 1, 'KeyFilter: imports WebOSCompositorBase (Settings visible)'),
    ('U1', SUI, r'^var HOME_APP_ID = ' + LIT + r';$', M, 1, 'systemUi: HOME_APP_ID definition'),
    ('U2', SUI, r'^function handleHomeKeys\([^)]*\) \{$', M, 1, 'systemUi: handleHomeKeys header'),
    ('U4', SUI, r'applicationManager\.(?:launch|checkLaunch)\(\s*' + LIT, 0, 1, 'systemUi: launches of the stock id (covered by K1/K2)'),
    ('A1', APL, r'applicationManager\.(?:launch|checkLaunch)\(\s*' + LIT, 0, 1, 'appLaunch: launches of the stock id (covered by K1/K2)'),
    # --- tier 2: optional comparisons --------------------------------------------------------
    ('N1', NUD, r'views\.fullscreen\.currentItem\.appId === ' + LIT, 0, 2, 'Nudge: suppress nudges over home'),
    ('B1', BLS, r'views\.fullscreen\.currentItem\.appId === ' + LIT, 0, 2, 'BaseLuna: getCurrentState "launcher"'),
    ('R1', REC, r'[\w.]+\.appId !== ' + LIT, 0, 2, 'Recents: spare home from cleanup kills'),
    ('L1', FSC, r'supportNMRM \? ' + LIT + ' :', 0, 2, 'FullscreenController: NMRM last-input = home'),
    ('L2', VSC, r'supportNMRM \? ' + LIT + ' :', 0, 3, 'VSC: lastInputApp (exposed to key filters as a param)'),
    ('L3', REC, r'supportNMRM \? ' + LIT + ' :', 0, 3, 'Recents 7.6: __lastInputApp'),
    # --- tier 3: cosmetic / dead on 6-9 ----------------------------------------------------
    ('G1', GES, r'focusedFullscreenInfo\.appId === ' + LIT, 0, 3, 'Gesture: swipe icon'),
    ('H1', HV, r'LS\.adhoc\.call\("luna://com\.webos\.applicationManager", "/launch", "\{\\"id\\":\\"com\.webos\.app\.home\\"\}"\);', 0, 3, 'HomeView: openHome (fallback path)'),
    ('H2', HV, r'closeWindowByAppId\(' + LIT, 0, 3, 'HomeView: closeHome (home-type window only)'),
    ('V1', VSC, r'appId != ' + LIT, 0, 3, 'VSC: splash skip'),
    ('V2', VSC, r'showSpinnerOnRelaunch === true && appId === ' + LIT, 0, 3, 'VSC: spinner on relaunch (8.4+)'),
    ('F1', FWI, r'"appId" : ' + LIT, 0, 3, 'FocusedWindowInfoMgr: home view entry'),
    ('O1', OPV, r'appId: ' + LIT, 0, 3, 'OverlayPlayVideo: home view item'),
    ('A2', APL, r'case ' + LIT + ':', 0, 3, 'appLaunch: multiview toolbar case'),
]


def read(path):
    try:
        with open(path, 'rb') as f:
            return f.read().decode('latin1')
    except OSError:
        return None


def block_end(text, open_idx):
    """index of the brace closing the one at open_idx (naive: ignores strings/comments)."""
    depth = 0
    for i in range(open_idx, len(text)):
        c = text[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return i
    return -1


def structure(files):
    out = {}
    sui = files.get(SUI)
    if sui is not None:
        m = re.search(r'^function handleHomeKeys\([^)]*\) \{$', sui, M)
        uses = [x.start() for x in re.finditer(r'\bHOME_APP_ID\b', sui)]
        defs = [x.start() for x in re.finditer(r'^var HOME_APP_ID =', sui, M)]
        body = None
        if m:
            s = m.end() - 1
            body = (s, block_end(sui, s))
        other = [u for u in uses if u not in [d + 4 for d in defs]]
        inside = [u for u in other if body and body[0] < u < body[1]]
        out['U3'] = '%d/%d' % (len(inside), len(other))  # HOME_APP_ID uses inside handleHomeKeys / all uses
        out['Ulit'] = str(len(re.findall(LIT, sui)))
        # any comparison with the literal (not through HOME_APP_ID)?
        out['Ucmp'] = str(len(re.findall(r'(?:===|!==|==|!=)\s*' + LIT, sui)))
    kf = files.get(KF)
    if kf is not None:
        m = re.search(r'^[ \t]*readonly property QtObject applicationManager: QtObject \{$', kf, M)
        ok = 'BAD'
        if m:
            s = m.end() - 1
            e = block_end(kf, s)
            grp = kf[s:e]
            l = re.search(r'readonly property var launch: function\(appId, params, checkUpdateOnLaunch, autoInstallation, reason\) \{', grp)
            c = re.search(r'readonly property var checkLaunch:[ \t]+function\(appId, params, checkUpdateOnLaunch, autoInstallation\) \{', grp)
            ok = 'ok' if (l and c) else ('ok-noCL' if l else 'BAD')
        out['K5'] = ok  # K1 and K2 sit inside the K3 group
        out['Kexist'] = '1' if re.search(r'\b(isHomeApp|getHomeAppId|launchHome)\b', kf) else '0'
    st = files.get(SET)
    if st is not None:
        out['Sexist'] = '1' if re.search(r'\b(isHomeApp|getHomeAppId|launchHome|stockHomeAppId)\b', st) else '0'
        out['Ssvc'] = str(len(re.findall(r'^import WebOSServices', st, M)))
    return out


def main():
    rows = []
    for line in open(sys.argv[1]):
        if line.startswith('#') or not line.strip():
            continue
        bid, ver, kind, root = line.rstrip('\n').split('\t')
        rows.append((bid, ver, kind, root))
    ids = [p[0] for p in PATTERNS]
    extra = ['U3', 'Ulit', 'Ucmp', 'K5', 'Kexist', 'Sexist', 'Ssvc']
    print('build\tver\t' + '\t'.join(ids + extra))
    for bid, ver, kind, root in rows:
        files = {}
        cells = []
        for pid, rel, rx, fl, tier, desc in PATTERNS:
            if rel not in files:
                files[rel] = read(os.path.join(root, rel))
            t = files[rel]
            cells.append('-' if t is None else str(len(re.findall(rx, t, fl))))
        st = structure(files)
        cells += [st.get(k, '-') for k in extra]
        print(bid + '\t' + ver + '\t' + '\t'.join(cells))
    print()
    for pid, rel, rx, fl, tier, desc in PATTERNS:
        print('%s\ttier %d\t%s\t%s' % (pid, tier, rel[len(Q):], desc))


main()
