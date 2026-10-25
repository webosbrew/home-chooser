// SPDX-License-Identifier: Apache-2.0
// The version has one source, app/package.json. appinfo.json (what ares-package and the TV read)
// has to carry the same one; the service gets it from service/Makefile (-DHC_VERSION).
//   node scripts/check-version.js           exit 1 when appinfo.json differs from package.json
//   node scripts/check-version.js --write   set appinfo.json's version to package.json's
// `npm run package` runs the check first; `npm version` runs --write (the "version" script).
const fs = require('fs');
const path = require('path');

const app = path.resolve(__dirname, '..');
const pkgPath = path.join(app, 'package.json');
const infoPath = path.join(app, 'appinfo.json');
const version = JSON.parse(fs.readFileSync(pkgPath, 'utf8')).version;
const text = fs.readFileSync(infoPath, 'utf8');
const current = JSON.parse(text).version;

// webOS takes a plain x.y.z version
if (!/^\d+\.\d+\.\d+$/.test(String(version || ''))) {
	console.error(`check-version: package.json version "${version}" is not x.y.z`);
	process.exit(1);
}
if (current === version) {
	console.log(`check-version: ${version}`);
	process.exit(0);
}
if (process.argv.includes('--write')) {
	// replace only the value, keeping the file's own layout
	const out = text.replace(/("version"\s*:\s*)"[^"]*"/, `$1"${version}"`);
	if (JSON.parse(out).version !== version) {
		console.error('check-version: could not rewrite appinfo.json');
		process.exit(1);
	}
	fs.writeFileSync(infoPath, out);
	console.log(`check-version: appinfo.json ${current} -> ${version}`);
	process.exit(0);
}
console.error(`check-version: appinfo.json has version ${current}, package.json has ${version}; run \`node scripts/check-version.js --write\``);
process.exit(1);
