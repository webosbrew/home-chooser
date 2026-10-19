// Copies the key filter backport (../backport) into the packaged app: the boot script and, for
// each release directory, its manifest.json and diffs. The script's tests are not shipped.
const fs = require('fs');
const path = require('path');

const dist = path.resolve(process.argv[2] || 'dist');
const src = path.resolve(__dirname, '..', '..', 'backport');
const dest = path.join(dist, 'backport');

fs.rmSync(dest, {recursive: true, force: true});
fs.mkdirSync(dest, {recursive: true});
fs.copyFileSync(path.join(src, 'openhouse-keyfilter'), path.join(dest, 'openhouse-keyfilter'));
fs.chmodSync(path.join(dest, 'openhouse-keyfilter'), 0o755);
let sets = 0;
let files = 0;
fs.readdirSync(src, {withFileTypes: true}).forEach((entry) => {
	if (!entry.isDirectory() || !fs.existsSync(path.join(src, entry.name, 'manifest.json'))) return;
	fs.mkdirSync(path.join(dest, entry.name));
	fs.readdirSync(path.join(src, entry.name)).forEach((name) => {
		fs.copyFileSync(path.join(src, entry.name, name), path.join(dest, entry.name, name));
		files++;
	});
	sets++;
});
console.log('copy-backport: ' + sets + ' sets, ' + files + ' files into ' + dest);
