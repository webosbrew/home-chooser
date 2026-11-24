// SPDX-License-Identifier: Apache-2.0
// What the logic layer gets from the TV: one object, built on the service client.
//   rootLuna(uri, payload)  an allow-listed luna call, made by the root service
//   readFile / writeFile / removeFile  the files the service allows
//   exec(name)              a named command (see COMMANDS in service/src/policy.c, plus the
//                           composite names handled here); resolves {stdout, stderr}
//   release()               {version: [major, minor, patch]} from /etc/starfish-release
//   readIcon(appId, icon)   a data: URL or null
//   ensureRoot()            elevation (startup)
//   restartService()        quit and restart the root service (an access change on 11+)
import {ServiceError, errorMessage} from './errors';
import {createBridgeCall} from './bridge';
import {createServiceClient} from './service';
import {ensureRoot, restartService, FatalError} from './elevate';
import {HOME_FILE, HOMEKEY_FLAG} from '../homechooser/persistence';

// Parse /etc/starfish-release, e.g. "Rockhopper release 4.10.2-29 (goldilocks2-grampians)": the
// first "release MAJOR.MINOR.PATCH". Anything after the patch number is ignored; no match: version null.
export const parseStarfishRelease = (text) => {
	const m = /release (\d+)\.(\d+)\.(\d+)/.exec(text);
	return m ? {version: [Number(m[1]), Number(m[2]), Number(m[3])]} : {version: null};
};

const ICON_TYPES = /\.(png|jpe?g|gif|svg|webp)$/i;

export const createRelease = (service) => () =>
	service.readFile('/etc/starfish-release').then((text) => {
		const parsed = parseStarfishRelease(text || '');
		if (!parsed.version) throw new Error('No version in /etc/starfish-release');
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
		release: createRelease(service),
		readIcon: createReadIcon(service),
		ensureRoot: (opts = {}) => ensureRoot({service, call, ...opts}),
		restartService: () => restartService({service})
	};
};

export {ServiceError, FatalError, errorMessage, createBridgeCall, createServiceClient, ensureRoot, restartService};
