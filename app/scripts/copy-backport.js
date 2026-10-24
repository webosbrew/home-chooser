// Ships runtime scripts only; reviewed sets and offline tests stay in the repo.
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
console.log('copy-backport: 5 boot/runtime scripts into ' + dest);
