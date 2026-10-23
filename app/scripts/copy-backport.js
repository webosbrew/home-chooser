// Copies the boot scripts and the key filter backport (../backport) into the packaged app: the
// scripts (homechooser, homechooser-keyfilter, homechooser-home-watch, mode 755: the init.d entry is a
// symlink to homechooser in the installed app, and run-parts needs its target executable) and, for
// each release directory, its manifest.json and diffs. The tests are not shipped.
const fs = require('fs');
const path = require('path');

const dist = path.resolve(process.argv[2] || 'dist');
const src = path.resolve(__dirname, '..', '..', 'backport');
const dest = path.join(dist, 'backport');

fs.rmSync(dest, {recursive: true, force: true});
fs.mkdirSync(dest, {recursive: true});
['homechooser', 'homechooser-keyfilter', 'homechooser-keyfilter-gen.js', 'homechooser-home-watch', 'homechooser-access'].forEach((name) => {
	fs.copyFileSync(path.join(src, name), path.join(dest, name));
	fs.chmodSync(path.join(dest, name), 0o755);
});
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
