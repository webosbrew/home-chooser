// SPDX-License-Identifier: Apache-2.0
// Home-choice logic. Pure over injected {rootLuna, exec, readFile, writeFile, removeFile, version}; exec takes
// a command name (see persistence.js), not shell text.
// See docs/settings-app.md and docs/research/home-key-and-home-app.md.
import {errorMessage, errorReply} from '../platform/errors';
import {
	HOME_FILE, PREVIOUS_FILE, ensureEntryCommand, homekeyCommand, parsePersistenceStatus, persistenceStatusCommand,
	startWatcherCommand, stopWatcherCommand
} from './persistence';

export const STOCK_HOME = 'com.webos.app.home';
export const STOCK_FULLHOME = 'com.webos.app.fullhome';
export const OVERRIDE_PATH = '/var/webos-profile/override/org.webosbrew.homechooser.json';

const SETTINGS = 'luna://com.webos.settingsservice';
const SAM = 'luna://com.webos.applicationManager';
// The system profile is reached through the service's profileGet / profileRescan commands (luna-send
// run by root): the service's own luna identity is denied getProfiles (12.x) and debug/rescan (11.x, 12.x).

export const STOCK_LABELS = {
	'com.webos.app.home': 'Stock Home',
	'com.webos.app.fullhome': 'Stock New Home (FullHome)',
	'com.webos.app.seniorhome': 'Stock Senior Home',
	'com.webos.app.kidshome': 'Stock Kids Home',
	'com.webos.app.tnativehome': 'Stock Home (tnativehome)',
	'com.webos.app.hotelhome': 'Stock Hotel Home',
	'com.webos.app.appliancehome': 'Stock Appliance Home'
};

export const majorOf = (version) => (Array.isArray(version) ? version[0] : Number(version));

// What the platform needs beyond the settings record, by release.
export const releaseInfo = (version) => {
	const major = majorOf(version);
	return {
		major,
		// 6.x to 9.x: the stock compositor names com.webos.app.home, so the Home key ignores the choice
		// until the Home key hook is installed.
		needsHomeKeyHook: major >= 6 && major <= 9,
		// 10.x and later: the compositor's home id is the system profile's, so write an override too.
		usesProfile: major >= 10,
		// 9.x and later have applicationManager/setDefaultApp.
		hasSetDefaultApp: major >= 9
	};
};

const isAbsentError = (e) => /no matched result/i.test(errorMessage(e));
const isUnknownMethod = (e) => /unknown method/i.test(errorMessage(e));

const parseMaybeJson = (v) => {
	if (typeof v === 'string') {
		try {
			return JSON.parse(v);
		} catch (e) {
			return null;
		}
	}
	return v && typeof v === 'object' ? v : null;
};

// The reply shape of getProfiles is not in the study: look for a `defaultApps` object that has
// `categories`, at the top of the reply or one or two levels down.
const isObject = (v) => Boolean(v) && typeof v === 'object' && !Array.isArray(v);

const findProfile = (reply, name, accept) => {
	const seen = [];
	const search = (node, depth) => {
		if (!node || typeof node !== 'object' || depth > 3 || seen.includes(node)) return null;
		seen.push(node);
		if (isObject(node[name]) && accept(node[name])) return node[name];
		for (const k of Object.keys(node)) {
			const found = search(node[k], depth + 1);
			if (found) return found;
		}
		return null;
	};
	return search(reply, 0);
};

export const extractDefaultAppsProfile = (reply) =>
	findProfile(reply, 'defaultApps', (d) => Boolean(d.categories) && typeof d.categories === 'object');

// The idleApp profile (10.x and later): what sam launches when the last app closes. null when absent.
export const extractIdleAppProfile = (reply) => findProfile(reply, 'idleApp', () => true);

// A profile object without its `_` metadata keys and without the keys in `skip`.
const profileCopy = (profile, skip = []) => {
	const out = {};
	Object.keys(isObject(profile) ? profile : {}).forEach((k) => {
		if (k[0] !== '_' && skip.indexOf(k) < 0) out[k] = profile[k];
	});
	return out;
};

// The override's idleApp: the profile's other keys stay, `support` and `appId` are the choice's.
// A full-screen launcher is the idle app; an overlay launcher turns the idle app off (support false),
// so the last input comes back when the last app closes. The watcher writes the same
// (service/src/watcher/record.c, check_override).
export const buildIdleApp = (idleProfile, appId, overlay) => {
	const out = profileCopy(idleProfile, ['support', 'appId']);
	out.support = !overlay;
	if (!overlay) out.appId = appId;
	return out;
};

// `windowType`: 'fullscreen' or 'overlay' adds idleApp; anything else leaves idleApp out (the stock one applies).
export const buildOverride = (profile, appId, windowType, idleProfile) => {
	const out = profileCopy(profile);
	out.categories = Object.assign({}, profile && profile.categories, {home: appId});
	const override = {defaultApps: out};
	if (windowType === 'fullscreen' || windowType === 'overlay') override.idleApp = buildIdleApp(idleProfile, appId, windowType === 'overlay');
	return override;
};

// getAppInfo's defaultWindowType, as the watcher reads it: 'overlay' or 'fullscreen'.
export const windowOf = (reply) => {
	const info = (reply && (reply.appInfo || reply)) || {};
	return info.defaultWindowType === 'overlay' ? 'overlay' : 'fullscreen';
};

// null, "" and a missing key all read as "no home set" (docs/research/home-key-and-home-app.md).
export const homeOf = (record) => (record && typeof record.home === 'string' && record.home !== '' ? record.home : null);

const withNullHome = (record) => Object.assign({}, record || {}, {home: null});

// The general/defaultApps record: null when the key was never written.
export const readDefaultApps = async (run) => {
	try {
		const reply = await run.luna(`${SETTINGS}/getSystemSettings`, {category: 'general', keys: ['defaultApps']});
		return parseMaybeJson(reply && reply.settings && reply.settings.defaultApps);
	} catch (e) {
		if (isAbsentError(e)) return null; // key never written
		throw e;
	}
};

export const writeDefaultApps = (run, record) =>
	run.luna(`${SETTINGS}/setSystemSettings`, {category: 'general', settings: {defaultApps: record}});

// webOS 10 and later only (5 to 9 write the stock home id, see restoreStock): clear `home` in the general/defaultApps record, keeping the other entries. Sub-keys of the record
// can never be removed (a write that leaves `home` out only looks like a replace until the settings
// service restarts; delete and reset are no-ops), so `home` is written as null, which reads as unset
// and makes launchDefaultApp {"category":"home"} launch the profile's stock home
// (docs/research/home-key-and-home-app.md, the 11.2.0 / 10.3.1 results).
export const removeHomeFromRecord = (run, record) => writeDefaultApps(run, withNullHome(record));

// listApps replies with every field of every app (over 200 KB with a few dozen apps, more than
// exec's buffer holds), so ask for the few fields the list needs.
const CANDIDATE_PROPERTIES = ['id', 'title', 'icon', 'appCategory', 'folderPath'];

// A runner logs each call into `log`; a failing call is logged with its error and then rethrown.
export const createRunner = ({rootLuna, exec, readFile, writeFile, removeFile}) => (log) => {
	const wrap = async (step, fn) => {
		log.push(step);
		try {
			const value = await fn();
			step.ok = true;
			if (step.kind === 'luna') step.reply = value;
			return value;
		} catch (e) {
			step.ok = false;
			step.error = errorMessage(e);
			if (errorReply(e)) step.reply = errorReply(e);
			throw e;
		}
	};
	return {
		luna: (uri, payload) => wrap({kind: 'luna', uri, payload}, () => rootLuna(uri, payload)),
		read: (path) => wrap({kind: 'readFile', path}, () => (readFile ? readFile(path) : null)),
		write: (path, text) => wrap({kind: 'writeFile', path, text}, () => writeFile(path, text)),
		remove: (path) => wrap({kind: 'removeFile', path}, () => removeFile(path)),
		run: (command) => wrap({kind: 'exec', command}, () => exec(command))
	};
};

// The previous-launcher list: the ids in `text` (the file, one a line) and `add`, without stock homes,
// `except` (the new choice), invalid ids and repeats.
const APP_ID_RE = /^[A-Za-z0-9._-]+$/;
export const previousList = (text, add, except) => String(text || '').split('\n').map((s) => s.trim()).concat(add)
	.filter((id, i, all) => id && APP_ID_RE.test(id) && !STOCK_LABELS[id] && id !== except && all.indexOf(id) === i);

export const createHomeChooser = ({rootLuna, exec, readFile, writeFile, removeFile, version}) => {
	const info = releaseInfo(version);
	const runner = createRunner({rootLuna, exec, readFile, writeFile, removeFile});

	const readRecord = readDefaultApps;

	// luna-send exits 0 whatever the reply, so the reply says whether the call worked.
	const lunaSend = async (run, command) => {
		const out = await run.run(command);
		const reply = parseMaybeJson(String((out && out.stdout) || '').trim());
		if (!reply) throw new Error(`${command}: no JSON reply from luna-send`);
		if (reply.returnValue === false) throw new Error(`${command}: ${reply.errorText || 'the call failed'}`);
		return reply;
	};
	const rescan = (run) => lunaSend(run, 'profileRescan');

	const readProfile = async (run) => {
		if (!info.usesProfile) return {profile: null, idleApp: null, error: null};
		try {
			const reply = await lunaSend(run, 'profileGet');
			const profile = extractDefaultAppsProfile(reply);
			const idleApp = extractIdleAppProfile(reply);
			return profile ? {profile, idleApp, error: null} : {profile: null, idleApp, error: 'getProfiles reply has no defaultApps.categories'};
		} catch (e) {
			return {profile: null, idleApp: null, error: errorMessage(e)};
		}
	};

	// Never an idleApp for an id sam does not know: it leaves a black screen when the last app closes.
	const windowFor = async (run, appId) => {
		if (!appId) throw new Error('No app id to set as the Home app');
		let reply;
		try {
			reply = await run.luna(`${SAM}/getAppInfo`, {id: appId});
		} catch (e) {
			throw new Error(`Cannot read the app info of ${appId}: ${errorMessage(e)}`);
		}
		if (!reply || reply.returnValue === false) throw new Error(`Cannot read the app info of ${appId}`);
		return windowOf(reply);
	};

	const readStateWith = async (run) => {
		const record = await readRecord(run);
		const {profile, idleApp, error} = await readProfile(run);
		return {
			record,
			recordHome: homeOf(record),
			profile,
			idleApp,
			profileHome: (profile && profile.categories && profile.categories.home) || null,
			profileError: error
		};
	};

	const result = (log) => ({ok: true, steps: log, warnings: [], errors: [], release: info});

	const fail = (res, e) => {
		res.ok = false;
		res.errors.push(errorMessage(e));
		return res;
	};

	// Explicit JS launch targets on 6-9 remove the old first-write SAM restart requirement.
	const needsSamRestart = () => false;

	const writeRecord = writeDefaultApps;

	// Never a close while this panel is on screen: closing the launcher in front empties the foreground,
	// SAM brings up its idle app (6-9: the gate, the last input for an overlay launcher) and
	// surface-manager closes the panel before the "Done" toast. The previous launchers go to a file
	// (the ones still listed are kept), and the watcher closes each once the panel is gone; while the
	// file is fresh it also holds its own stock-home closes (service/src/watcher/previous.c). Returns
	// the list written.
	const recordPrevious = async (run, res, add, except) => {
		let text = '';
		try {
			text = await run.read(PREVIOUS_FILE);
		} catch (e) {
			res.warnings.push(`Could not read the previous launchers: ${errorMessage(e)}`);
		}
		const ids = previousList(text, add, except);
		try {
			await run.write(PREVIOUS_FILE, ids.map((id) => id + '\n').join(''));
		} catch (e) {
			res.warnings.push(`Could not note the previous launcher, so it stays open: ${errorMessage(e)}`);
		}
		return ids;
	};

	return {
		info,
		needsSamRestart,

		async listCandidates() {
			const log = [];
			const res = Object.assign(result(log), {candidates: []});
			try {
				const reply = await runner(log).luna(`${SAM}/listApps`, {properties: CANDIDATE_PROPERTIES});
				res.candidates = ((reply && reply.apps) || [])
					.filter((a) => [].concat(a.appCategory || []).indexOf('home') >= 0)
					.map((a) => ({
						id: a.id,
						title: a.title || a.id,
						icon: a.icon && a.icon[0] !== '/' && a.folderPath ? `${a.folderPath}/${a.icon}` : a.icon || null,
						stock: Object.prototype.hasOwnProperty.call(STOCK_LABELS, a.id),
						stockLabel: STOCK_LABELS[a.id] || null
					}));
			} catch (e) {
				fail(res, e);
			}
			return res;
		},

		async readState() {
			const log = [];
			const res = Object.assign(result(log), {state: null});
			try {
				res.state = await readStateWith(runner(log));
				if (res.state.profileError) res.warnings.push(`System profile: ${res.state.profileError}`);
				res.willRestartSam = needsSamRestart(res.state);
			} catch (e) {
				fail(res, e);
			}
			return res;
		},

		// The boot entry link, the watcher and the stored choice.
		async readPersistence() {
			const log = [];
			const res = Object.assign(result(log), {persistence: null});
			try {
				const out = await runner(log).run(persistenceStatusCommand());
				res.persistence = parsePersistenceStatus(out.stdout);
			} catch (e) {
				fail(res, e);
			}
			return res;
		},

		async apply(appId) {
			// the stock home is "no choice", not a choice (on 6.x to 9.x sam refuses it for lack of appCategory)
			if (appId === STOCK_HOME) return this.restoreStock();
			const log = [];
			const run = runner(log);
			const res = Object.assign(result(log), {
				appId,
				needsHomeKeyHook: info.needsHomeKeyHook,
				restartedSam: false,
				samRestartDeferred: false
			});
			try {
				const state = await readStateWith(run);
				const record = Object.assign({}, state.record || {}, {home: appId});
				// 10.x and later: full-screen or overlay decides the override's idleApp
				const windowType = info.usesProfile ? await windowFor(run, appId) : null;

				// 0. the stored choice, which the boot entry and the watcher enforce from here on, and the
				// previous launcher, which the watcher closes later (nothing is closed here)
				await run.write(HOME_FILE, appId);
				await recordPrevious(run, res, [state.recordHome, state.profileHome], appId);

				// 1. the settings record
				if (info.hasSetDefaultApp) {
					try {
						await run.luna(`${SAM}/setDefaultApp`, {category: 'home', appId});
					} catch (e) {
						if (!isUnknownMethod(e)) throw e;
						res.warnings.push('setDefaultApp is unknown here; wrote the record with setSystemSettings');
						await writeRecord(run, record);
					}
				} else {
					await writeRecord(run, record);
				}

				// 2. the system profile override (10.x and later): the home and the idle app
				if (info.usesProfile) {
					if (!state.profile) throw new Error(`Cannot read the defaultApps profile: ${state.profileError}`);
					const override = buildOverride(state.profile, appId, windowType, state.idleApp);
					await run.write(OVERRIDE_PATH, JSON.stringify(override, null, 2));
					await rescan(run);
					const after = await readProfile(run);
					const home = after.profile && after.profile.categories && after.profile.categories.home;
					if (home !== appId) throw new Error(`The system profile still names ${home || 'no home app'} after the rescan`);
					const idle = after.idleApp;
					if (idle && (idle.support !== override.idleApp.support || (override.idleApp.support && idle.appId !== appId))) {
						res.warnings.push(`The system profile's idleApp is ${JSON.stringify(profileCopy(idle))} after the rescan`);
					}
				}

				// 3. the boot entry (a symlink into the app) and the watcher, so the choice survives boots
				await run.run(ensureEntryCommand());
				try {
					await run.run(startWatcherCommand());
				} catch (e) {
					res.warnings.push(`Could not start the watcher: ${errorMessage(e)}`);
				}

				// The 6-9 filter launches an explicit id; SAM never needs restarting.
				if (info.needsHomeKeyHook) await run.run(homekeyCommand('refresh'));
			} catch (e) {
				fail(res, e);
			}
			return res;
		},

		async restoreStock() {
			const log = [];
			const run = runner(log);
			const res = Object.assign(result(log), {needsHomeKeyHook: info.needsHomeKeyHook});
			try {
				const state = await readStateWith(run);
				// the watcher would put the choice back, so stop it and drop the choice first
				try {
					await run.run(stopWatcherCommand());
				} catch (e) {
					res.warnings.push(`Could not stop the watcher: ${errorMessage(e)}`);
				}
				await run.remove(HOME_FILE);
				if (info.major <= 9) {
					// 5.x: the stock Home handler launches the record's home directly, so name a stock home.
					// 6 to 9: keep a stock record for the watcher; the volatile JS filter is restored below.
					const list = await run.luna(`${SAM}/listApps`, {properties: ['id']});
					const ids = ((list && list.apps) || []).map((a) => a.id);
					const stock = ids.indexOf(STOCK_FULLHOME) >= 0 ? STOCK_FULLHOME : STOCK_HOME;
					// (a record that was never written on 6 to 9 has no patched compositor reading it, and the
					// first write of the key is what sam misses, so it is left absent)
					if (homeOf(state.record) !== stock && !(info.major >= 6 && state.record === null)) {
						await writeRecord(run, Object.assign({}, state.record || {}, {home: stock}));
					}
				} else if (homeOf(state.record)) {
					await removeHomeFromRecord(run, state.record);
				}
				if (info.usesProfile) {
					await run.remove(OVERRIDE_PATH);
					await rescan(run);
					await readProfile(run);
				}
				if (info.needsHomeKeyHook) {
					// Restore the exact stock keyFilters list immediately.
					try {
						await run.run(homekeyCommand('refresh'));
					} catch (e) {
						res.warnings.push(`Could not refresh the Home key hook: ${errorMessage(e)}`);
					}
				}
				// The custom home is closed later, by a watcher that runs only for that and then exits.
				const custom = await recordPrevious(run, res, [state.recordHome, state.profileHome], null);
				if (custom.length) {
					try {
						await run.run(startWatcherCommand());
					} catch (e) {
						res.warnings.push(`Could not start the watcher to close ${custom.join(', ')}: ${errorMessage(e)}`);
					}
				} else {
					await run.remove(PREVIOUS_FILE);
				}
			} catch (e) {
				fail(res, e);
			}
			return res;
		}
	};
};
