// Enact copies all of iLib's locale data (about 83 MB) into dist/node_modules/ilib/locale and the
// CLI has no switch to limit it for a non-isomorphic build. Home Chooser's UI is English-only and
// shows no dates, times, numbers or time zones, so the only iLib data it can request is what
// LocaleInfo (script and direction of the system locale), ScriptInfo and ResBundle ask for:
//
//   localeinfo.json, scripts.json, pseudomap.json, localematch.json       top level
//   {en,und}/**/localeinfo.json and {en,und}/**/plurals.json              English and region roots
//
// localematch.json (544 KB) is only read by LocaleInfo for pa-PK, ha-CM and ha-SD, but without it
// iLib throws for those system locales, so it stays. Everything else (zoneinfo, date, number, unit
// and address data, other languages) is removed, and the
// manifest is rewritten so iLib never tries to fetch a file that is gone. On a webOS device iLib
// reads time zones from /usr/share/zoneinfo anyway, never from this directory.
// scripts/check-ilib.js loads the result through iLib's real loader to prove it.
const fs = require('fs');
const path = require('path');

const keepTop = new Set(['localeinfo.json', 'scripts.json', 'pseudomap.json', 'localematch.json', 'ilibmanifest.json']);
const keepTrees = new Set(['en', 'und']);
const keepInTrees = new Set(['localeinfo.json', 'plurals.json']);

const size = (p) => {
	const st = fs.statSync(p);
	return st.isDirectory() ? fs.readdirSync(p).reduce((n, f) => n + size(path.join(p, f)), 0) : st.size;
};

// Prune a kept tree in place; returns true when anything is left in it.
const pruneTree = (dir) => {
	let left = false;
	fs.readdirSync(dir).forEach((name) => {
		const full = path.join(dir, name);
		if (fs.statSync(full).isDirectory()) {
			if (pruneTree(full)) left = true;
			else fs.rmSync(full, {recursive: true, force: true});
		} else if (keepInTrees.has(name)) {
			left = true;
		} else {
			fs.rmSync(full);
		}
	});
	return left;
};

const prune = (dist) => {
	const locale = path.join(dist, 'node_modules', 'ilib', 'locale');
	if (!fs.existsSync(locale)) {
		console.log('prune-ilib: nothing at ' + locale);
		return;
	}
	const before = size(locale);
	fs.readdirSync(locale).forEach((name) => {
		const full = path.join(locale, name);
		if (fs.statSync(full).isDirectory()) {
			if (keepTrees.has(name)) pruneTree(full);
			else fs.rmSync(full, {recursive: true, force: true});
		} else if (!keepTop.has(name)) {
			fs.rmSync(full);
		}
	});
	const manifestPath = path.join(locale, 'ilibmanifest.json');
	const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
	const total = manifest.files.length;
	manifest.files = manifest.files.filter((f) => fs.existsSync(path.join(locale, f)));
	fs.writeFileSync(manifestPath, JSON.stringify(manifest));
	console.log(`prune-ilib: ${(before / 1048576).toFixed(1)} MB -> ${(size(locale) / 1024).toFixed(0)} KB; manifest ${total} -> ${manifest.files.length} files`);
};

prune(path.resolve(process.argv[2] || 'dist'));
