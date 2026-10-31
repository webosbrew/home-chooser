// SPDX-License-Identifier: Apache-2.0
// Stage the LS2 service, the role-free daemon and the 6-9 home gate; only services.json registers a service.
// (mode 755: elevation turns its Exec line into the bare binary path, so it has to run as is).
// All three binaries are built by `make -C service` into build/service.
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..', '..');
const out = path.resolve(process.argv[2] || path.join(root, 'build', 'service-pkg'));
const binaries = ['homechooser-service', 'homechooser-daemon', 'homechooser-home-gate'];

for (const name of binaries) {
	const bin = path.join(root, 'build', 'service', name);
	if (!fs.existsSync(bin)) {
		console.error('stage-service: ' + bin + ' is missing; run `make -C service` first');
		process.exit(1);
	}
}
fs.rmSync(out, {recursive: true, force: true});
fs.mkdirSync(out, {recursive: true});
fs.copyFileSync(path.join(root, 'service', 'services.json'), path.join(out, 'services.json'));
binaries.forEach((name) => {
	fs.copyFileSync(path.join(root, 'build', 'service', name), path.join(out, name));
	fs.chmodSync(path.join(out, name), 0o755);
});
const sourceScripts = path.join(root, 'service', 'scripts');
const packagedScripts = path.join(out, 'scripts');
fs.mkdirSync(packagedScripts, {recursive: true});
['homechooser', 'homechooser-access', 'homechooser-homekey'].forEach((name) => {
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
