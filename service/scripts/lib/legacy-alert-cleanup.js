// SPDX-License-Identifier: Apache-2.0
// Remove the callback alias from Home Chooser's installer role when upgrading an experimental build.
const fs = require('fs');

const path = process.argv[2];
if (!path) throw new Error('usage: legacy-alert-cleanup.js ROLE');
const role = JSON.parse(fs.readFileSync(path, 'utf8'));
if (!Array.isArray(role.allowedNames) || !Array.isArray(role.permissions)) {
	throw new Error('unexpected Home Chooser installer role: ' + path);
}
const alias = 'com.webos.homechooser.alert';
const names = role.allowedNames.filter((name) => name !== alias);
const permissions = role.permissions.filter((entry) => entry.service !== alias);
if (names.length === role.allowedNames.length && permissions.length === role.permissions.length) {
	process.stdout.write('same\n');
} else {
	role.allowedNames = names;
	role.permissions = permissions;
	const temp = path + '.new';
	fs.writeFileSync(temp, JSON.stringify(role) + '\n', {mode: 0o644});
	fs.chmodSync(temp, 0o644);
	fs.renameSync(temp, path);
	process.stdout.write('changed\n');
}
