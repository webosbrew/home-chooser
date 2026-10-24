// Stages the native service for ares-package: <out>/services.json and <out>/homechooser-service
// (mode 755: elevation turns its Exec line into the bare binary path, so it has to run as is).
// The binary is built by `make -C service` into build/service.
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..', '..');
const out = path.resolve(process.argv[2] || path.join(root, 'build', 'service-pkg'));
const bin = path.join(root, 'build', 'service', 'homechooser-service');

if (!fs.existsSync(bin)) {
	console.error('stage-service: ' + bin + ' is missing; run `make -C service` first');
	process.exit(1);
}
fs.rmSync(out, {recursive: true, force: true});
fs.mkdirSync(out, {recursive: true});
fs.copyFileSync(path.join(root, 'service', 'services.json'), path.join(out, 'services.json'));
fs.copyFileSync(bin, path.join(out, 'homechooser-service'));
fs.chmodSync(path.join(out, 'homechooser-service'), 0o755);
const sourceScripts = path.join(root, 'service', 'scripts');
const packagedScripts = path.join(out, 'scripts');
fs.mkdirSync(packagedScripts, {recursive: true});
['homechooser', 'homechooser-access', 'homechooser-home-watch', 'homechooser-homekey', 'homechooser-homekey-hook.js'].forEach((name) => {
	fs.copyFileSync(path.join(sourceScripts, name), path.join(packagedScripts, name));
	fs.chmodSync(path.join(packagedScripts, name), 0o755);
});
const sourceLib = path.join(sourceScripts, 'lib');
const packagedLib = path.join(packagedScripts, 'lib');
fs.mkdirSync(packagedLib, {recursive: true});
fs.readdirSync(sourceLib).filter((name) => /\.(sh|js)$/.test(name)).forEach((name) => {
	fs.copyFileSync(path.join(sourceLib, name), path.join(packagedLib, name));
});
console.log('stage-service: ' + out);
