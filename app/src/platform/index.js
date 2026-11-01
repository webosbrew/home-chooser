// SPDX-License-Identifier: Apache-2.0
// What the logic layer gets from the TV: one object, built on the service client.
//   rootLuna(uri, payload)  an allow-listed luna call, made by the root service
//   readFile / writeFile / removeFile  the files the service allows
//   exec(name)              a named command (see COMMANDS in service/src/policy.c, plus the
//                           composite names handled here); resolves {stdout, stderr}
//   osRelease()             {version: [major, minor, patch], ...} from /etc/os-release
//   readIcon(appId, icon)   a data: URL or null
//   ensureRoot()            elevation (startup)
//   restartService()        quit and restart the root service (an access change on 11+)
import {ServiceError, errorMessage} from './errors';
import {createBridgeCall} from './bridge';
import {createServiceClient} from './service';
import {ensureRoot, restartService, FatalError} from './elevate';
import {HOME_FILE, HOMEKEY_FLAG} from '../homechooser/persistence';

// Parse /etc/os-release. VERSION_ID, then VERSION, then PRETTY_NAME, whichever has dotted digits.
export const parseOsRelease = (text) => {
	const fields = {};
	text.split('\n').forEach((line) => {
		const m = /^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$/.exec(line);
		if (!m) return;
		let v = m[2].trim();
		if ((v[0] === '"' && v[v.length - 1] === '"') || (v[0] === "'" && v[v.length - 1] === "'")) v = v.slice(1, -1);
		fields[m[1]] = v;
	});
	for (const key of ['VERSION_ID', 'VERSION', 'PRETTY_NAME']) {
		const m = fields[key] && /(\d+)(?:\.(\d+))?(?:\.(\d+))?/.exec(fields[key]);
		if (m) return {version: [Number(m[1]), Number(m[2] || 0), Number(m[3] || 0)], source: key, fields};
	}
	return {version: null, source: null, fields};
};

const ICON_TYPES = /\.(png|jpe?g|gif|svg|webp)$/i;

export const createOsRelease = (service) => () =>
	service.readFile('/etc/os-release').then((text) => {
		const parsed = parseOsRelease(text || '');
		if (!parsed.version) throw new Error('No version in /etc/os-release');
		return parsed;
	});

// readIcon(appId, iconPath) -> Promise<data: URL or null>. Never rejects; results (including
// misses) are cached for the life of the platform object.
export const createReadIcon = (service) => {
	const cache = new Map();
	return (appId, icon) => {
		if (!appId || !icon || !ICON_TYPES.test(icon)) return Promise.resolve(null);
		const key = appId + '\n' + icon;
		if (!cache.has(key)) {
			cache.set(key, service.readIcon(appId).then(
				({mime, data}) => (mime && /^[A-Za-z0-9+/=]+$/.test(data || '') ? `data:${mime};base64,${data}` : null),
				() => null
			));
		}
		return cache.get(key);
	};
};

// The text parsePersistenceStatus reads: the boot entry link, the watcher, the stored choice,
// the Home key hook flag, and the tail of the watcher's log.
export const createPersistenceStatus = (service) => async () => {
	const lines = [];
	const link = await service.linkStatus();
	lines.push(link.entry === 'link' || link.entry === 'dangling' ? `entry=${link.entry}:${link.target}` : `entry=${link.entry}`);
	const active = await service.run('watcherActive');
	lines.push(`active=${active.stdout.trim()}`);
	const stored = await service.readFile(HOME_FILE);
	lines.push(`stored=${stored === null ? '' : stored.trim()}`);
	lines.push(`homekey=${(await service.readFile(HOMEKEY_FLAG)) === null ? 'no' : 'yes'}`);
	lines.push('--log');
	const log = await service.run('watchLog');
	return {stdout: lines.join('\n') + '\n' + log.stdout, stderr: ''};
};

export const createExec = (service) => {
	const persistenceStatus = createPersistenceStatus(service);
	return (name) => {
		switch (name) {
			case 'persistenceStatus': return persistenceStatus();
			case 'linkEntry': return service.link().then(() => ({stdout: '', stderr: ''}));
			case 'unlinkEntry': return service.unlink().then(() => ({stdout: '', stderr: ''}));
			default: return service.run(name);
		}
	};
};

export const createPlatform = (call = createBridgeCall()) => {
	const service = createServiceClient(call);
	return {
		service,
		rootLuna: service.luna,
		uninstallConfirmed: service.uninstallConfirmed,
		rebootPrompt: service.rebootPrompt,
		readFile: service.readFile,
		writeFile: service.writeFile,
		removeFile: service.removeFile,
		exec: createExec(service),
		osRelease: createOsRelease(service),
		readIcon: createReadIcon(service),
		ensureRoot: () => ensureRoot({service, call}),
		restartService: () => restartService({service})
	};
};

export {ServiceError, FatalError, errorMessage, createBridgeCall, createServiceClient, ensureRoot, restartService};
