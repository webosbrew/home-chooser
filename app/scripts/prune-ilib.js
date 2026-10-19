// Enact copies all of iLib's locale data (about 83 MB) into dist/node_modules/ilib/locale and the
// CLI has no switch to limit it for a non-isomorphic build. Open House's UI is English, so keep the
// top-level data files, the English and root ("und") locales and the time zone data, drop the
// other locale directories and the character maps, and rewrite the manifest to match.
const fs = require('fs');
const path = require('path');

const dist = path.resolve(process.argv[2] || 'dist');
const locale = path.join(dist, 'node_modules', 'ilib', 'locale');
const keepDirs = new Set(['en', 'und', 'zoneinfo']);

const size = (p) => {
	const st = fs.statSync(p);
	return st.isDirectory() ? fs.readdirSync(p).reduce((n, f) => n + size(path.join(p, f)), 0) : st.size;
};

if (!fs.existsSync(locale)) {
	console.log('prune-ilib: nothing at ' + locale);
	process.exit(0);
}
const before = size(locale);
fs.readdirSync(locale).forEach((name) => {
	const full = path.join(locale, name);
	if (fs.statSync(full).isDirectory() && !keepDirs.has(name)) fs.rmSync(full, {recursive: true, force: true});
});
const manifestPath = path.join(locale, 'ilibmanifest.json');
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
const total = manifest.files.length;
manifest.files = manifest.files.filter((f) => fs.existsSync(path.join(locale, f)));
fs.writeFileSync(manifestPath, JSON.stringify(manifest));
console.log(`prune-ilib: ${(before / 1048576).toFixed(1)} MB -> ${(size(locale) / 1048576).toFixed(1)} MB; manifest ${total} -> ${manifest.files.length} files`);
