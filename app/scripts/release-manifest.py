#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write the apps-repo release manifest next to a built ipk.

    python3 -I app/scripts/release-manifest.py build/ipk/org.webosbrew.homechooser_0.1.0_arm.ipk

The manifest has the shape of Homebrew Channel's release manifest and is attached to the GitHub
release next to the ipk. id, version and title come from app/appinfo.json; the version must equal
the one in app/package.json (the single source). The output is
<dir of the ipk>/<id>.manifest.json and its path is printed.
"""
import hashlib
import json
import os
import re
import sys

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
README = os.path.join(APP, '..', 'README.md')
SOURCE_URL = 'https://github.com/webosbrew/home-chooser'
ICON_URI = 'https://raw.githubusercontent.com/webosbrew/home-chooser/main/app/largeIcon.png'


def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def description(readme_path=README):
    """The README tagline (its first italic line), without the final full stop."""
    with open(readme_path, encoding='utf-8') as f:
        m = re.search(r'^\*([^*\n]+)\*\s*$', f.read(), re.M)
    if not m:
        raise SystemExit('no italic tagline line in README.md')
    return m.group(1).strip().rstrip('.')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def build(ipk, appinfo, package, desc):
    if appinfo['version'] != package['version']:
        raise SystemExit('appinfo.json version %s != package.json version %s'
                         % (appinfo['version'], package['version']))
    name = os.path.basename(ipk)
    if appinfo['version'] not in name or not name.startswith(appinfo['id']):
        raise SystemExit('%s does not look like the ipk of %s %s'
                         % (name, appinfo['id'], appinfo['version']))
    return {
        'id': appinfo['id'],
        'version': appinfo['version'],
        'type': appinfo['type'],
        'title': appinfo['title'],
        'appDescription': desc,
        'iconUri': ICON_URI,
        'sourceUrl': SOURCE_URL,
        'rootRequired': True,
        'ipkUrl': name,
        'ipkHash': {'sha256': sha256(ipk)},
    }


def main(argv):
    if len(argv) != 2 or not os.path.isfile(argv[1]):
        raise SystemExit('usage: release-manifest.py <built.ipk>')
    ipk = argv[1]
    manifest = build(ipk, load(os.path.join(APP, 'appinfo.json')),
                     load(os.path.join(APP, 'package.json')), description())
    out = os.path.join(os.path.dirname(os.path.abspath(ipk)), manifest['id'] + '.manifest.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, separators=(',', ':'))
        f.write('\n')
    print(out)


if __name__ == '__main__':
    main(sys.argv)
