// SPDX-License-Identifier: Apache-2.0
// Extend only Home Chooser's installer-generated role so Surface Manager can reach an internal
// com.webos.* callback name. The vendor Surface Manager role is never changed.
const fs = require('fs');

const action = process.argv[2];
const rolePath = process.argv[3];
const sourcePath = process.argv[4];
const aliasPath = process.argv[5];
const alias = 'com.webos.homechooser.alert';
const marker = '# Home Chooser alert callback\n';
if (!['apply', 'remove'].includes(action) || !rolePath || !sourcePath || !aliasPath) {
	throw new Error('usage: alert-access.js apply|remove ROLE SOURCE_SERVICE ALIAS_SERVICE');
}

let changed = false;
function writeIfChanged(path, data) {
	if (fs.existsSync(path) && fs.readFileSync(path, 'utf8') === data) return;
	const temp = path + '.new';
	fs.writeFileSync(temp, data, {mode: 0o644});
	fs.chmodSync(temp, 0o644);
	fs.renameSync(temp, path);
	changed = true;
}

if (fs.existsSync(rolePath)) {
	const role = JSON.parse(fs.readFileSync(rolePath, 'utf8'));
	if (!Array.isArray(role.allowedNames) || !Array.isArray(role.permissions)) {
		throw new Error('unexpected Home Chooser installer role: ' + rolePath);
	}
	const names = role.allowedNames.filter((name) => name !== alias);
	const permissions = role.permissions.filter((entry) => entry.service !== alias);
	if (action === 'apply') {
		names.push(alias);
		permissions.push({service: alias, inbound: ['com.webos.surfacemanager'], outbound: []});
	}
	if (JSON.stringify(names) !== JSON.stringify(role.allowedNames) ||
		JSON.stringify(permissions) !== JSON.stringify(role.permissions)) {
		role.allowedNames = names;
		role.permissions = permissions;
		writeIfChanged(rolePath, JSON.stringify(role) + '\n');
	}
} else if (action === 'apply') {
	throw new Error('Home Chooser installer role missing: ' + rolePath);
}

if (action === 'apply') {
	const source = fs.readFileSync(sourcePath, 'utf8');
	if (!/^Name=org\.webosbrew\.homechooser\.service$/m.test(source)) {
		throw new Error('unexpected Home Chooser service file: ' + sourcePath);
	}
	if (fs.existsSync(aliasPath) && !fs.readFileSync(aliasPath, 'utf8').startsWith(marker)) {
		throw new Error('callback service name is already in use: ' + aliasPath);
	}
	writeIfChanged(aliasPath, marker + source.replace(/^Name=org\.webosbrew\.homechooser\.service$/m,
		'Name=' + alias));
} else if (fs.existsSync(aliasPath) && fs.readFileSync(aliasPath, 'utf8').startsWith(marker)) {
	fs.unlinkSync(aliasPath);
	changed = true;
}

process.stdout.write(changed ? 'changed\n' : 'same\n');
