// Stages the native service for ares-package: <out>/services.json and <out>/openhouse-service
// (mode 755: elevation turns its Exec line into the bare binary path, so it has to run as is).
// The binary is built by `make -C service` into build/service.
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..', '..');
const out = path.resolve(process.argv[2] || path.join(root, 'build', 'service-pkg'));
const bin = path.join(root, 'build', 'service', 'openhouse-service');

if (!fs.existsSync(bin)) {
	console.error('stage-service: ' + bin + ' is missing; run `make -C service` first');
	process.exit(1);
}
fs.rmSync(out, {recursive: true, force: true});
fs.mkdirSync(out, {recursive: true});
fs.copyFileSync(path.join(root, 'service', 'services.json'), path.join(out, 'services.json'));
fs.copyFileSync(bin, path.join(out, 'openhouse-service'));
fs.chmodSync(path.join(out, 'openhouse-service'), 0o755);
console.log('stage-service: ' + out);
