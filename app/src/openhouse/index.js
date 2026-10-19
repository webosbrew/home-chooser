// Home-choice logic. Pure over injected {rootLuna, exec, writeFile, removeFile, version}.
// See docs/settings-app.md and docs/research/home-key-and-home-app.md.
import {errorMessage, errorReply} from '../platform/errors';
import {shellQuote} from '../platform/quote';
import {
	HOME_FILE, KEYFILTER_SCRIPT, ensureEntryCommand, parsePersistenceStatus, persistenceStatusCommand,
	startWatcherCommand, stopWatcherCommand
} from './persistence';

export const STOCK_HOME = 'com.webos.app.home';
export const STOCK_FULLHOME = 'com.webos.app.fullhome';
export const OVERRIDE_PATH = '/var/webos-profile/override/org.webosbrew.openhouse.json';

const SETTINGS = 'luna://com.webos.settingsservice';
const SAM = 'luna://com.webos.applicationManager';
const PROFILE = 'luna://com.webos.service.systemprofile';

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
		// 6.x to 9.x: the stock key filter names com.webos.app.home, so the Home key ignores the choice
		// until the key filter backport is installed.
		needsKeyFilterBackport: major >= 6 && major <= 9,
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
export const extractDefaultAppsProfile = (reply) => {
	const seen = [];
	const search = (node, depth) => {
		if (!node || typeof node !== 'object' || depth > 3 || seen.includes(node)) return null;
		seen.push(node);
		if (node.defaultApps && typeof node.defaultApps === 'object' && !Array.isArray(node.defaultApps)) {
			const d = node.defaultApps;
			if (d.categories && typeof d.categories === 'object') return d;
		}
		for (const k of Object.keys(node)) {
			const found = search(node[k], depth + 1);
			if (found) return found;
		}
		return null;
	};
	return search(reply, 0);
};

export const buildOverride = (profile, appId) => {
	const out = {};
	Object.keys(profile || {}).forEach((k) => {
		if (k[0] !== '_') out[k] = profile[k];
	});
	out.categories = Object.assign({}, profile && profile.categories, {home: appId});
	return {defaultApps: out};
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

// Clear `home` in the general/defaultApps record, keeping the other entries. Sub-keys of the record
// can never be removed (a write that leaves `home` out only looks like a replace until the settings
// service restarts; delete and reset are no-ops), so `home` is written as null, which reads as unset
// and makes launchDefaultApp {"category":"home"} launch the profile's stock home
// (docs/research/home-key-and-home-app.md, the 11.2.0 / 10.3.1 results).
export const removeHomeFromRecord = (run, record) => writeDefaultApps(run, withNullHome(record));

// listApps replies with every field of every app (over 200 KB with a few dozen apps, more than
// exec's buffer holds), so ask for the few fields the list needs.
const CANDIDATE_PROPERTIES = ['id', 'title', 'icon', 'appCategory', 'folderPath'];

// A runner logs each call into `log`; a failing call is logged with its error and then rethrown.
export const createRunner = ({rootLuna, exec, writeFile, removeFile}) => (log) => {
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
		write: (path, text) => wrap({kind: 'writeFile', path, text}, () => writeFile(path, text)),
		remove: (path) => wrap({kind: 'removeFile', path}, () => removeFile(path)),
		run: (command) => wrap({kind: 'exec', command}, () => exec(command))
	};
};

export const createHomeChooser = ({rootLuna, exec, writeFile, removeFile, version}) => {
	const info = releaseInfo(version);
	const runner = createRunner({rootLuna, exec, writeFile, removeFile});

	const readRecord = readDefaultApps;

	const readProfile = async (run) => {
		if (!info.usesProfile) return {profile: null, error: null};
		try {
			const reply = await run.luna(`${PROFILE}/getProfiles`, {names: ['defaultApps']});
			const profile = extractDefaultAppsProfile(reply);
			return profile ? {profile, error: null} : {profile: null, error: 'getProfiles reply has no defaultApps.categories'};
		} catch (e) {
			return {profile: null, error: errorMessage(e)};
		}
	};

	const readStateWith = async (run) => {
		const record = await readRecord(run);
		const {profile, error} = await readProfile(run);
		return {
			record,
			recordHome: homeOf(record),
			profile,
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

	// True when this write would create `defaultApps` on a release where sam misses a first write.
	const needsSamRestart = (state) => info.major >= 6 && info.major <= 8 && state.record === null;

	const writeRecord = writeDefaultApps;

	const closeApp = async (run, res, id) => {
		try {
			await run.luna(`${SAM}/closeByAppId`, {id, forceMode: true});
		} catch (e) {
			res.warnings.push(`Could not close ${id}: ${errorMessage(e)}`);
		}
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

		async apply(appId, {onNotice} = {}) {
			// the stock home is "no choice", not a choice (on 6.x to 9.x sam refuses it for lack of appCategory)
			if (appId === STOCK_HOME) return this.restoreStock();
			const log = [];
			const run = runner(log);
			const res = Object.assign(result(log), {
				appId,
				needsKeyFilterBackport: info.needsKeyFilterBackport,
				restartedSam: false
			});
			try {
				const state = await readStateWith(run);
				const record = Object.assign({}, state.record || {}, {home: appId});

				// 0. the stored choice, which the boot entry and the watcher enforce from here on
				await run.write(HOME_FILE, appId);

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

				// 2. the system profile override (10.x and later)
				if (info.usesProfile) {
					if (!state.profile) throw new Error(`Cannot read the defaultApps profile: ${state.profileError}`);
					await run.write(OVERRIDE_PATH, JSON.stringify(buildOverride(state.profile, appId), null, 2));
					await run.luna(`${PROFILE}/debug/rescan`, {});
					const after = await readProfile(run);
					const home = after.profile && after.profile.categories && after.profile.categories.home;
					if (home !== appId) throw new Error(`The system profile still names ${home || 'no home app'} after the rescan`);
				}

				// 3. the boot entry (a symlink into the app) and the watcher, so the choice survives boots
				await run.run(ensureEntryCommand());
				try {
					await run.run(startWatcherCommand());
				} catch (e) {
					res.warnings.push(`Could not start the watcher: ${errorMessage(e)}`);
				}

				// 4. close the previous home
				const previous = state.recordHome || state.profileHome || STOCK_HOME;
				if (previous !== appId) await closeApp(run, res, previous);

				// 5. webOS 6 to 8: the first write of defaultApps is missed by sam until it restarts
				if (needsSamRestart(state)) {
					if (onNotice) onNotice('Restarting sam so it sees the new setting; running apps may close.');
					await run.run('systemctl restart sam');
					res.restartedSam = true;
				}
			} catch (e) {
				fail(res, e);
			}
			return res;
		},

		async restoreStock() {
			const log = [];
			const run = runner(log);
			const res = Object.assign(result(log), {needsKeyFilterBackport: info.needsKeyFilterBackport});
			try {
				const state = await readStateWith(run);
				// the watcher would put the choice back, so stop it and drop the choice first
				try {
					await run.run(stopWatcherCommand());
				} catch (e) {
					res.warnings.push(`Could not stop the watcher: ${errorMessage(e)}`);
				}
				await run.remove(HOME_FILE);
				if (info.major <= 5) {
					// 5.x: the key filter launches the record's home directly, so name a stock home.
					const list = await run.luna(`${SAM}/listApps`, {properties: ['id']});
					const ids = ((list && list.apps) || []).map((a) => a.id);
					const stock = ids.indexOf(STOCK_FULLHOME) >= 0 ? STOCK_FULLHOME : STOCK_HOME;
					if (homeOf(state.record) !== stock) {
						await writeRecord(run, Object.assign({}, state.record || {}, {home: stock}));
					}
				} else if (homeOf(state.record)) {
					await removeHomeFromRecord(run, state.record);
				}
				if (info.usesProfile) {
					await run.remove(OVERRIDE_PATH);
					await run.luna(`${PROFILE}/debug/rescan`, {});
					await readProfile(run);
				}
				if (info.needsKeyFilterBackport) {
					// the patched Settings.qml carries the stored choice as its fallback; clear it if mounted
					try {
						await run.run(`sh ${shellQuote(KEYFILTER_SCRIPT)} refresh`);
					} catch (e) {
						res.warnings.push(`Could not refresh the key filter backport: ${errorMessage(e)}`);
					}
				}
				const custom = [state.recordHome, state.profileHome].filter((id, i, all) => id && !STOCK_LABELS[id] && all.indexOf(id) === i);
				for (const id of custom) await closeApp(run, res, id);
			} catch (e) {
				fail(res, e);
			}
			return res;
		}
	};
};
