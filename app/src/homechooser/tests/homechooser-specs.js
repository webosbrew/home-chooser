// SPDX-License-Identifier: Apache-2.0
import {ServiceError} from '../../platform/errors';
import {
	OVERRIDE_PATH, buildIdleApp, buildOverride, createHomeChooser, extractDefaultAppsProfile, extractIdleAppProfile,
	previousList, releaseInfo, windowOf
} from '..';
import {diagnoseExecFailure} from '../diagnose';
import {describeMissingChoice} from '../format';
import {
	HOME_FILE, HOMEKEY_FLAG, PREVIOUS_FILE, ensureEntryCommand, parsePersistenceStatus, persistenceStatusCommand, startWatcherCommand,
	stopWatcherCommand, BOOT_SCRIPT
} from '../persistence';

const MINE = 'org.webosbrew.homechooser.launcher';
const OTHER = 'com.example.otherhome';
const STOCK = 'com.webos.app.home';
const ENSURE = ensureEntryCommand();
const WATCH = startWatcherCommand();
const STOP = stopWatcherCommand();
const REFRESH = 'homekeyRefresh';

const ABSENT = {returnValue: false, errorCode: -1, errorText: 'There is no matched result from DB'};
const UNKNOWN = {returnValue: false, errorText: 'Unknown method "setDefaultApp" for category "/"'};
const NOT_SET = {returnValue: false, errorCode: 1, errorText: 'no default app is set for the category'};

// The stock idleApp of every 10.3.1 / 11.2.0 device layer, plus a key of no meaning that must survive.
const STOCK_IDLE = {_packageIncluded: true, _configuration: 'idleApp.json', support: true, appId: STOCK, keep: 'me'};

const profileReply = (home = STOCK, extra = {}, idleApp = STOCK_IDLE) => ({
	returnValue: true,
	profiles: Object.assign(
		{defaultApps: Object.assign({_packageIncluded: true, _configuration: 'defaultApps.json', support: true, categories: {home, aod: 'com.webos.app.lifeonscreen'}}, extra)},
		idleApp ? {idleApp} : {}
	)
});

const GETPROFILE = ['luna://com.webos.service.systemprofile/getProfiles', {names: ['defaultApps', 'idleApp']}];
const APPINFO = (id) => ['luna://com.webos.applicationManager/getAppInfo', {id}];
const FULL_IDLE = (id) => ({support: true, appId: id, keep: 'me'});
const RESCAN = ['luna://com.webos.service.systemprofile/debug/rescan', {}];

// A fake TV: `record` is general/defaultApps (undefined = never written); `profile` the getProfiles reply.
// `windows` gives an app's defaultWindowType; getAppInfo knows every id except those in `unknown`.
// `ignoreIdle`: the rescan takes the override's defaultApps but not its idleApp.
const fakeTV = ({record, profile, setDefaultApp = true, failures = {}, windows = {}, unknown = [], ignoreIdle = false, previous = null, homekey = false} = {}) => {
	const luna = [];
	const files = [];
	const commands = [];
	const calls = [];
	// The service's own luna identity is denied systemprofile (12.x getProfiles, 11.x/12.x debug/rescan):
	// the app has to reach it through the profileGet / profileRescan commands.
	const profileCall = (uri, payload) => {
		calls.push(['luna', uri, payload]);
		luna.push([uri, payload]);
		const out = (reply) => ({stdout: JSON.stringify(reply) + '\n', stderr: ''});
		if (uri.endsWith('/getProfiles')) {
			if (failures.getProfiles) return out(failures.getProfiles);
			if (!profile) return out({returnValue: false, errorText: 'Service does not exist: com.webos.service.systemprofile'});
			return out(profile);
		}
		return out(failures.rescan || {returnValue: true});
	};
	const rootLuna = async (uri, payload) => {
		if (uri.startsWith('luna://com.webos.service.systemprofile/')) {
			throw new ServiceError({returnValue: false, errorText: 'Denied method call "' + uri.split('/').pop() + '" for category "/"'});
		}
		calls.push(['luna', uri, payload]);
		luna.push([uri, payload]);
		const m = uri.split('/').pop();
		if (failures[m]) throw new ServiceError(failures[m]);
		switch (uri.replace('luna://', '')) {
			case 'com.webos.settingsservice/getSystemSettings':
				if (record === undefined) throw new ServiceError(ABSENT);
				return {returnValue: true, category: 'general', settings: {defaultApps: record}};
			case 'com.webos.settingsservice/setSystemSettings':
				record = payload.settings.defaultApps;
				return {returnValue: true};
			case 'com.webos.applicationManager/setDefaultApp':
				if (!setDefaultApp) throw new ServiceError(UNKNOWN);
				record = Object.assign({}, record, {[payload.category]: payload.appId});
				return {returnValue: true};
			case 'com.webos.applicationManager/closeByAppId':
				return {returnValue: true};
			case 'com.webos.applicationManager/getAppInfo':
				if (unknown.indexOf(payload.id) >= 0) throw new ServiceError({returnValue: false, errorText: `Cannot find proper launchPoint: ${payload.id}`});
				return {returnValue: true, appId: payload.id, appInfo: Object.assign({id: payload.id}, windows[payload.id] ? {defaultWindowType: windows[payload.id]} : {})};
			default:
				throw new Error('unexpected call ' + uri);
		}
	};
	// The previous-launcher file, as the service reads it: null when absent.
	const readFile = async (path) => {
		calls.push(['read', path]);
		if (failures.readFile) throw new ServiceError(failures.readFile);
		if (path === HOMEKEY_FLAG) return homekey ? '' : null;
		return path === PREVIOUS_FILE ? previous : null;
	};
	const writeFile = async (path, text) => {
		calls.push(['write', path]);
		files.push([path, text]);
		if (path === PREVIOUS_FILE && failures.writePrevious) throw new ServiceError(failures.writePrevious);
		if (path === PREVIOUS_FILE) previous = text;
		if (path === HOME_FILE || path === PREVIOUS_FILE) return;
		// the real service reads the override on rescan; 10.3.1 merges idleApp key by key onto the stock one
		const o = JSON.parse(text);
		profile = {returnValue: true, profiles: {defaultApps: Object.assign({}, o.defaultApps), idleApp: Object.assign({}, STOCK_IDLE, ignoreIdle ? {} : o.idleApp)}};
	};
	const removeFile = async (path) => {
		calls.push(['remove', path]);
		files.push(['rm', path]);
		if (path === PREVIOUS_FILE) {
			previous = null;
			return;
		}
		profile = profileReply(STOCK);
	};
	const exec = async (command) => {
		if (command === 'profileGet') return profileCall(GETPROFILE[0], GETPROFILE[1]);
		if (command === 'profileRescan') return profileCall(RESCAN[0], RESCAN[1]);
		calls.push(['exec', command]);
		commands.push(command);
		return {stdout: '', stderr: ''};
	};
	return {rootLuna, readFile, writeFile, removeFile, exec, luna, files, commands, calls, record: () => record, previous: () => previous};
};

const chooser = (tv, version) => createHomeChooser({
	rootLuna: tv.rootLuna, exec: tv.exec, readFile: tv.readFile, writeFile: tv.writeFile, removeFile: tv.removeFile, version
});

const GET = ['luna://com.webos.settingsservice/getSystemSettings', {category: 'general', keys: ['defaultApps']}];
const SET = (defaultApps) => ['luna://com.webos.settingsservice/setSystemSettings', {category: 'general', settings: {defaultApps}}];
const SETDEFAULT = (appId) => ['luna://com.webos.applicationManager/setDefaultApp', {category: 'home', appId}];
const CLOSAPI = 'luna://com.webos.applicationManager/closeByAppId';
const overrideOf = (tv) => JSON.parse(tv.files.find((f) => f[0] === OVERRIDE_PATH)[1]);
const NO_CLOSE = (tv) => expect(tv.luna.map((c) => c[0])).not.toContain(CLOSAPI);

describe('previousList', () => {
	test('merges the file and the new ids, drops stock homes, the new choice, invalid ids and repeats', () => {
		expect(previousList('a.b\n\n com.webos.app.home \nx y\nc.d\n', ['c.d', MINE, null, 'com.webos.app.kidshome', 'e.f'], MINE))
			.toEqual(['a.b', 'c.d', 'e.f']);
		expect(previousList(null, [null, undefined], null)).toEqual([]);
	});
});

describe('releaseInfo', () => {
	test.each([
		[5, false, false, false],
		[6, true, false, false],
		[8, true, false, false],
		[9, true, false, true],
		[10, false, true, true],
		[11, false, true, true]
	])('webOS TV %i', (major, homeKeyHook, profile, setter) => {
		expect(releaseInfo([major, 0, 0])).toMatchObject({needsHomeKeyHook: homeKeyHook, usesProfile: profile, hasSetDefaultApp: setter});
	});
});

describe('listCandidates', () => {
	test('keeps apps whose appCategory contains home and labels stock ones', async () => {
		const tv = fakeTV();
		tv.rootLuna = async () => ({
			returnValue: true,
			apps: [
				{id: STOCK, title: 'Home', appCategory: ['home'], icon: '/usr/palm/applications/com.webos.app.home/icon.png'},
				{id: 'com.webos.app.kidshome', title: 'Kids', appCategory: ['home', 'kidsApp']},
				{id: MINE, title: 'Mine', appCategory: ['home'], icon: 'icon.png', folderPath: '/media/developer/apps/usr/palm/applications/x'},
				{id: 'com.webos.app.browser', title: 'Web', appCategory: ['browser']},
				{id: 'com.webos.app.settings', title: 'Settings'}
			]
		});
		const res = await createHomeChooser({...tv, version: [10, 3, 1]}).listCandidates();
		expect(res.candidates.map((c) => c.id)).toEqual([STOCK, 'com.webos.app.kidshome', MINE]);
		expect(res.candidates[0]).toMatchObject({stock: true, stockLabel: 'Stock Home'});
		expect(res.candidates[2]).toMatchObject({stock: false, stockLabel: null, icon: '/media/developer/apps/usr/palm/applications/x/icon.png'});
		expect(res.steps[0].uri).toBe('luna://com.webos.applicationManager/listApps');
		expect(res.steps[0].payload).toEqual({properties: ['id', 'title', 'icon', 'appCategory', 'folderPath']});
	});
});

describe('readState', () => {
	test('absent key is not a failure', async () => {
		const res = await chooser(fakeTV(), [6, 6, 0]).readState();
		expect(res.ok).toBe(true);
		expect(res.state.record).toBeNull();
		expect(res.willRestartSam).toBe(false);
		expect(res.steps[0]).toMatchObject({ok: false, error: 'There is no matched result from DB'});
	});

	test('reads the record and, on 10.x, the profile', async () => {
		const tv = fakeTV({record: {home: MINE, kidsApp: 'k'}, profile: profileReply()});
		const res = await chooser(tv, [10, 3, 1]).readState();
		expect(res.state).toMatchObject({recordHome: MINE, profileHome: STOCK});
		expect(tv.luna).toEqual([GET, GETPROFILE]);
	});

	test('a missing systemprofile service is a warning', async () => {
		const res = await chooser(fakeTV({record: {}}), [10, 3, 1]).readState();
		expect(res.ok).toBe(true);
		expect(res.warnings[0]).toMatch(/Service does not exist/);
	});

	test('other errors are failures', async () => {
		const res = await chooser(fakeTV({failures: {getSystemSettings: {returnValue: false, errorText: 'Access denied'}}}), [6, 6, 0]).readState();
		expect(res.ok).toBe(false);
		expect(res.errors).toEqual(['Access denied']);
	});
});

describe('extractDefaultAppsProfile / buildOverride', () => {
	test('finds the profile in common reply shapes', () => {
		const d = {categories: {home: STOCK}};
		expect(extractDefaultAppsProfile({defaultApps: d})).toBe(d);
		expect(extractDefaultAppsProfile({profiles: {defaultApps: d}})).toBe(d);
		expect(extractDefaultAppsProfile({returnValue: true})).toBeNull();
	});

	test('drops underscore keys and keeps the other categories', () => {
		const p = profileReply().profiles.defaultApps;
		expect(buildOverride(p, MINE)).toEqual({defaultApps: {support: true, categories: {home: MINE, aod: 'com.webos.app.lifeonscreen'}}});
	});

	test('finds idleApp beside defaultApps', () => {
		const reply = profileReply();
		expect(extractIdleAppProfile(reply)).toBe(reply.profiles.idleApp);
		expect(extractIdleAppProfile({idleApp: {support: false}})).toEqual({support: false});
		expect(extractIdleAppProfile(profileReply(STOCK, {}, null))).toBeNull();
	});

	test('full-screen launcher: idleApp names it, other idleApp keys stay, metadata goes', () => {
		const {defaultApps, idleApp} = profileReply().profiles;
		expect(buildOverride(defaultApps, MINE, 'fullscreen', idleApp)).toEqual({
			defaultApps: {support: true, categories: {home: MINE, aod: 'com.webos.app.lifeonscreen'}},
			idleApp: {support: true, appId: MINE, keep: 'me'}
		});
	});

	test('overlay launcher: idleApp support false, no appId', () => {
		const {defaultApps, idleApp} = profileReply().profiles;
		expect(buildOverride(defaultApps, MINE, 'overlay', idleApp).idleApp).toEqual({support: false, keep: 'me'});
	});

	test('an earlier override read back is replaced, not carried: support and appId are the choice\'s', () => {
		const {defaultApps} = profileReply(OTHER).profiles;
		const earlier = {support: true, appId: OTHER, keep: 'me'};
		expect(buildOverride(defaultApps, MINE, 'overlay', earlier).idleApp).toEqual({support: false, keep: 'me'});
		expect(buildIdleApp({support: false, keep: 'me'}, MINE, false)).toEqual({support: true, appId: MINE, keep: 'me'});
	});

	test('no idleApp in the profile: only support and appId; no window: no idleApp at all', () => {
		const {defaultApps} = profileReply().profiles;
		expect(buildOverride(defaultApps, MINE, 'fullscreen', null).idleApp).toEqual({support: true, appId: MINE});
		expect(buildOverride(defaultApps, MINE, null, STOCK_IDLE)).not.toHaveProperty('idleApp');
	});

	test('windowOf reads defaultWindowType like the watcher', () => {
		expect(windowOf({appInfo: {defaultWindowType: 'overlay'}})).toBe('overlay');
		expect(windowOf({appInfo: {defaultWindowType: 'card'}})).toBe('fullscreen');
		expect(windowOf({appInfo: {}})).toBe('fullscreen');
	});
});

describe('apply', () => {
	test('5.6.1: carries other entries, leaves the previous stock home running, no sam restart', async () => {
		const tv = fakeTV({record: {home: 'com.webos.app.fullhome', other: 'x'}});
		const res = await chooser(tv, [5, 6, 1]).apply(MINE);
		expect(res.ok).toBe(true);
		expect(tv.luna).toEqual([GET, SET({home: MINE, other: 'x'})]);
		expect(tv.files).toEqual([[HOME_FILE, MINE], [PREVIOUS_FILE, '']]);
		expect(tv.commands).toEqual([ENSURE, WATCH]);
		expect(res.needsHomeKeyHook).toBe(false);
		expect(res.restartedSam).toBe(false);
	});

	test('6.6.0, record absent: write, stock home left running, restart sam last', async () => {
		const tv = fakeTV();
		const notices = [];
		const res = await chooser(tv, [6, 6, 0]).apply(MINE, {onNotice: (n) => notices.push(n)});
		expect(res.ok).toBe(true);
		expect(tv.luna).toEqual([GET, SET({home: MINE})]);
		expect(tv.commands).toEqual([ENSURE, WATCH, REFRESH]);
		expect(tv.calls[tv.calls.length - 1]).toEqual(['exec', REFRESH]);
		expect(notices).toHaveLength(0);
		expect(res.restartedSam).toBe(false);
		expect(res.needsHomeKeyHook).toBe(true);
	});

	test('6.6.0, record absent, sam restart deferred (a TV restart follows): nothing restarts, the caller is told', async () => {
		const tv = fakeTV();
		const notices = [];
		const res = await chooser(tv, [6, 6, 0]).apply(MINE, {deferSamRestart: true, onNotice: (n) => notices.push(n)});
		expect(res.ok).toBe(true);
		expect(tv.luna).toEqual([GET, SET({home: MINE})]);
		expect(tv.commands).toEqual([ENSURE, WATCH, REFRESH]);
		expect(res.restartedSam).toBe(false);
		expect(res.samRestartDeferred).toBe(false);
		expect(notices).toEqual([]);
	});

	test('6.6.0, record present, deferral asked for: nothing to defer', async () => {
		const tv = fakeTV({record: {home: OTHER}});
		const res = await chooser(tv, [6, 6, 0]).apply(MINE, {deferSamRestart: true});
		expect(res.samRestartDeferred).toBe(false);
		expect(res.restartedSam).toBe(false);
	});

	test('6.6.0, record present: no restart', async () => {
		const tv = fakeTV({record: {home: OTHER, kidsApp: 'k'}});
		const res = await chooser(tv, [6, 6, 0]).apply(MINE);
		expect(tv.luna).toEqual([GET, SET({home: MINE, kidsApp: 'k'})]);
		expect(tv.previous()).toBe(OTHER + '\n');
		expect(tv.commands).toEqual([ENSURE, WATCH, REFRESH]);
		expect(res.restartedSam).toBe(false);
	});

	test('8.3.0, record absent: restart sam', async () => {
		const tv = fakeTV();
		await chooser(tv, [8, 3, 0]).apply(MINE);
		expect(tv.luna).toEqual([GET, SET({home: MINE})]);
		expect(tv.commands).toEqual([ENSURE, WATCH, REFRESH]);
	});

	test('a failed previous-launcher write is a warning, not an error', async () => {
		const tv = fakeTV({record: {home: OTHER}, failures: {writePrevious: {returnValue: false, errorText: 'disk full'}}});
		const res = await chooser(tv, [8, 3, 0]).apply(MINE);
		expect(res.ok).toBe(true);
		expect(res.warnings).toEqual(['Could not note the previous launcher, so it stays open: disk full']);
		NO_CLOSE(tv);
	});

	test('an unreadable previous-launcher file is a warning; the list is written anew', async () => {
		const tv = fakeTV({record: {home: OTHER}, failures: {readFile: {returnValue: false, errorText: 'readFailed'}}});
		const res = await chooser(tv, [8, 3, 0]).apply(MINE);
		expect(res.ok).toBe(true);
		expect(res.warnings).toEqual(['Could not read the previous launchers: readFailed']);
		expect(tv.previous()).toBe(OTHER + '\n');
	});

	test.each([[[5, 6, 1]], [[6, 6, 0]], [[8, 3, 0]], [[9, 2, 4]], [[10, 3, 1]], [[11, 2, 0]], [[12, 0, 0]]])('%j: switching launchers closes nothing; the previous one is left to the watcher', async (version) => {
		const tv = fakeTV({record: {home: OTHER}, profile: profileReply(OTHER)});
		const res = await chooser(tv, version).apply(MINE);
		expect(res.ok).toBe(true);
		NO_CLOSE(tv);
		expect(tv.previous()).toBe(OTHER + '\n');
		// written right after the choice, before any platform write the watcher or the gate reacts to
		const order = tv.calls.map((c) => c[0] + ':' + c[1].split('/').pop());
		expect(order.indexOf('write:previous')).toBe(order.indexOf('write:home') + 2);
	});

	test('a second switch before the watcher closed the first keeps both, never the new choice', async () => {
		const tv = fakeTV({record: {home: OTHER}, previous: `older.home\n${MINE}\n`});
		await chooser(tv, [8, 3, 0]).apply(MINE);
		expect(tv.previous()).toBe(`older.home\n${OTHER}\n`);
	});

	test('choosing the current home does not close it', async () => {
		const tv = fakeTV({record: {home: MINE}});
		await chooser(tv, [8, 3, 0]).apply(MINE);
		expect(tv.luna).toEqual([GET, SET({home: MINE})]);
	});

	test.each([5, 6, 10, 11, 12])('webOS %d: choosing a launcher never closes a previous stock home', async (major) => {
		for (const id of ['com.webos.app.home', 'com.webos.app.fullhome', 'com.webos.app.kidshome', 'com.webos.app.seniorhome']) {
			const tv = fakeTV({record: {home: id}, profile: profileReply()});
			const res = await chooser(tv, [major, 0, 0]).apply(MINE);
			expect(res.ok).toBe(true);
			NO_CLOSE(tv);
			expect(tv.previous()).toBe('');
		}
	});

	test('9.2.1: setDefaultApp, no profile, no restart', async () => {
		const tv = fakeTV({record: {kidsApp: 'k'}});
		const res = await chooser(tv, [9, 2, 1]).apply(MINE);
		expect(res.ok).toBe(true);
		expect(tv.luna).toEqual([GET, SETDEFAULT(MINE)]);
		expect(tv.record()).toEqual({kidsApp: 'k', home: MINE});
		expect(tv.files).toEqual([[HOME_FILE, MINE], [PREVIOUS_FILE, '']]);
		expect(tv.commands).toEqual([ENSURE, WATCH, REFRESH]);
		expect(res.needsHomeKeyHook).toBe(true);
	});

	test('9.2.1: Unknown method falls back to setSystemSettings with the other entries', async () => {
		const tv = fakeTV({record: {kidsApp: 'k'}, setDefaultApp: false});
		const res = await chooser(tv, [9, 2, 1]).apply(MINE);
		expect(res.ok).toBe(true);
		expect(tv.luna).toEqual([GET, SETDEFAULT(MINE), SET({kidsApp: 'k', home: MINE})]);
		expect(res.warnings[0]).toMatch(/setDefaultApp is unknown/);
		expect(tv.commands).toEqual([ENSURE, WATCH, REFRESH]);
	});

	test('9.2.1: another setDefaultApp error is a failure', async () => {
		const tv = fakeTV({record: {}, failures: {setDefaultApp: {returnValue: false, errorText: 'Fail to update default apps to settings service'}}});
		const res = await chooser(tv, [9, 2, 1]).apply(MINE);
		expect(res.ok).toBe(false);
		expect(res.errors).toEqual(['Fail to update default apps to settings service']);
		expect(tv.luna).toEqual([GET, SETDEFAULT(MINE)]);
	});

	test.each([[[10, 3, 1]], [[11, 2, 0]]])('%j: record, override file, rescan, verify, leave stock home running', async (version) => {
		const tv = fakeTV({record: {kidsApp: 'k'}, profile: profileReply()});
		const res = await chooser(tv, version).apply(MINE);
		expect(res.ok).toBe(true);
		expect(res.needsHomeKeyHook).toBe(false);
		expect(tv.luna).toEqual([GET, GETPROFILE, APPINFO(MINE), SETDEFAULT(MINE), RESCAN, GETPROFILE]);
		expect(tv.files).toHaveLength(3);
		expect(tv.files[0]).toEqual([HOME_FILE, MINE]);
		expect(tv.files[1]).toEqual([PREVIOUS_FILE, '']);
		expect(tv.files[2][0]).toBe('/var/webos-profile/override/org.webosbrew.homechooser.json');
		expect(overrideOf(tv)).toEqual({
			defaultApps: {support: true, categories: {home: MINE, aod: 'com.webos.app.lifeonscreen'}},
			idleApp: FULL_IDLE(MINE)
		});
		expect(tv.calls.map((c) => c[0] + ':' + (c[1].split('/').pop()))).toEqual([
			'luna:getSystemSettings', 'luna:getProfiles', 'luna:getAppInfo', 'write:home', 'read:previous', 'write:previous', 'luna:setDefaultApp',
			'write:org.webosbrew.homechooser.json', 'luna:rescan', 'luna:getProfiles', 'exec:' + ENSURE.split('/').pop(),
			'exec:' + WATCH.split('/').pop()
		]);
		expect(tv.commands).toEqual([ENSURE, WATCH]);
		expect(res.warnings).toEqual([]);
	});

	test.each([[[10, 3, 1]], [[11, 2, 0]]])('%j: an overlay launcher turns the idle app off', async (version) => {
		const tv = fakeTV({record: {}, profile: profileReply(), windows: {[MINE]: 'overlay'}});
		const res = await chooser(tv, version).apply(MINE);
		expect(res.ok).toBe(true);
		expect(overrideOf(tv).idleApp).toEqual({support: false, keep: 'me'});
		expect(res.warnings).toEqual([]);
	});

	test('10.3.1: from an overlay launcher to a full-screen one, the earlier idleApp is not carried', async () => {
		const tv = fakeTV({record: {}, profile: profileReply(OTHER, {}, {support: false, appId: STOCK, keep: 'me'})});
		const res = await chooser(tv, [10, 3, 1]).apply(MINE);
		expect(res.ok).toBe(true);
		expect(overrideOf(tv).idleApp).toEqual(FULL_IDLE(MINE));
	});

	test('10.3.1: an id getAppInfo does not know is refused before anything is written', async () => {
		const tv = fakeTV({record: {}, profile: profileReply(), unknown: [MINE]});
		const res = await chooser(tv, [10, 3, 1]).apply(MINE);
		expect(res.ok).toBe(false);
		expect(res.errors[0]).toMatch(/Cannot read the app info of org.webosbrew.homechooser.launcher/);
		expect(tv.files).toEqual([]);
		expect(tv.commands).toEqual([]);
	});

	test('10.3.1: an empty id is refused before anything is written', async () => {
		const tv = fakeTV({record: {}, profile: profileReply()});
		const res = await chooser(tv, [10, 3, 1]).apply('');
		expect(res.ok).toBe(false);
		expect(tv.files).toEqual([]);
		expect(JSON.stringify(tv.luna)).not.toContain('getAppInfo');
	});

	test('10.3.1: a profile whose idleApp does not follow is a warning', async () => {
		const tv = fakeTV({record: {}, profile: profileReply(), ignoreIdle: true});
		const res = await chooser(tv, [10, 3, 1]).apply(MINE);
		expect(res.ok).toBe(true);
		expect(res.warnings[0]).toMatch(/idleApp is .*com.webos.app.home/);
	});

	test('5.x to 9.x: no getAppInfo, no idleApp', async () => {
		for (const version of [[5, 6, 1], [8, 3, 0], [9, 2, 1]]) {
			const tv = fakeTV({record: {}});
			await chooser(tv, version).apply(MINE);
			expect(JSON.stringify(tv.luna)).not.toContain('getAppInfo');
		}
	});

	test('11.2.0 / 12.0.0: the profile is read and rescanned through root commands, never through call', async () => {
		const tv = fakeTV({record: {}, profile: profileReply()});
		const res = await chooser(tv, [12, 0, 0]).apply(MINE);
		expect(res.ok).toBe(true);
		expect(tv.luna.map((c) => c[0].split('/').pop())).toEqual(['getSystemSettings', 'getProfiles', 'getAppInfo', 'setDefaultApp', 'rescan', 'getProfiles']);
		expect(tv.commands).toEqual([ENSURE, WATCH]);
	});

	test('a false reply from the rescan command fails the apply before the boot entry', async () => {
		const tv = fakeTV({record: {}, profile: profileReply(), failures: {rescan: {returnValue: false, errorText: 'rescan refused'}}});
		const res = await chooser(tv, [12, 0, 0]).apply(MINE);
		expect(res.ok).toBe(false);
		expect(res.errors).toEqual(['profileRescan: rescan refused']);
		expect(tv.commands).toEqual([]);
	});

	test('a denied getProfiles stops the apply before the override and the boot entry', async () => {
		const denied = {returnValue: false, errorText: 'Denied method call "getProfiles" for category "/"'};
		const tv = fakeTV({record: {}, profile: profileReply(), failures: {getProfiles: denied}});
		const res = await chooser(tv, [12, 0, 0]).apply(MINE);
		expect(res.ok).toBe(false);
		expect(res.errors).toEqual(['Cannot read the defaultApps profile: profileGet: Denied method call "getProfiles" for category "/"']);
		expect(tv.files.map((f) => f[0])).toEqual([HOME_FILE, PREVIOUS_FILE]);
		expect(tv.commands).toEqual([]);
	});

	test('10.3.1: the previous homes are the record home and the profile home', async () => {
		const tv = fakeTV({record: {home: OTHER}, profile: profileReply('com.example.thirdhome')});
		await chooser(tv, [10, 3, 1]).apply(MINE);
		expect(tv.previous()).toBe(`${OTHER}\ncom.example.thirdhome\n`);
		NO_CLOSE(tv);
	});

	test('10.3.1: an earlier custom profile home is the previous when the record has none', async () => {
		const tv = fakeTV({record: {}, profile: profileReply(OTHER)});
		await chooser(tv, [10, 3, 1]).apply(MINE);
		expect(tv.previous()).toBe(OTHER + '\n');
		NO_CLOSE(tv);
	});

	test('10.3.1: a profile that does not take the override is an error', async () => {
		const tv = fakeTV({record: {}, profile: profileReply()});
		tv.writeFile = async () => {};
		const res = await createHomeChooser({...tv, version: [10, 3, 1]}).apply(MINE);
		expect(res.ok).toBe(false);
		expect(res.errors[0]).toMatch(/still names com.webos.app.home/);
	});

	test('10.3.1: no readable profile stops before writing the override', async () => {
		const tv = fakeTV({record: {}});
		const res = await chooser(tv, [10, 3, 1]).apply(MINE);
		expect(res.ok).toBe(false);
		expect(res.errors[0]).toMatch(/Cannot read the defaultApps profile/);
		expect(tv.files.map((f) => f[0])).toEqual([HOME_FILE, PREVIOUS_FILE]);
		expect(tv.commands).toEqual([]);
	});

	test('the step log records calls, replies and errors', async () => {
		const tv = fakeTV({record: {home: OTHER}});
		const res = await chooser(tv, [8, 3, 0]).apply(MINE);
		expect(res.steps.map((s) => [s.kind, s.ok])).toEqual([
			['luna', true], ['writeFile', true], ['readFile', true], ['writeFile', true], ['luna', true], ['exec', true], ['exec', true], ['exec', true]
		]);
		expect(res.steps[4]).toMatchObject({uri: SET({})[0], reply: {returnValue: true}});
	});
});

const LIST = ['luna://com.webos.applicationManager/listApps', {properties: ['id']}];
const withApps = (tv, ids) => {
	const inner = tv.rootLuna;
	tv.rootLuna = async (uri, payload) => (uri.endsWith('/listApps') ? (tv.luna.push([uri, payload]), {returnValue: true, apps: ids.map((id) => ({id}))}) : inner(uri, payload));
	return tv;
};

describe('restoreStock', () => {

	test('5.6.1: writes fullhome when installed, keeps other entries, closes the custom home', async () => {
		const tv = withApps(fakeTV({record: {home: MINE, kidsApp: 'k'}}), [STOCK, 'com.webos.app.fullhome']);
		const res = await chooser(tv, [5, 6, 1]).restoreStock();
		expect(res.ok).toBe(true);
		expect(tv.luna).toEqual([GET, LIST, SET({home: 'com.webos.app.fullhome', kidsApp: 'k'})]);
		expect(tv.files).toEqual([['rm', HOME_FILE], [PREVIOUS_FILE, MINE + '\n']]);
		expect(tv.commands).toEqual([STOP, WATCH]);
	});

	test('5.x: falls back to com.webos.app.home when fullhome is not installed', async () => {
		const tv = withApps(fakeTV({record: {home: MINE}}), [STOCK]);
		await chooser(tv, [5, 4, 0]).restoreStock();
		expect(tv.luna).toEqual([GET, LIST, SET({home: STOCK})]);
	});

	test('5.x: absent record is created with the stock home; a stock home is not closed', async () => {
		const tv = withApps(fakeTV(), [STOCK, 'com.webos.app.fullhome']);
		await chooser(tv, [5, 6, 1]).restoreStock();
		expect(tv.luna).toEqual([GET, LIST, SET({home: 'com.webos.app.fullhome'})]);
		const tv2 = withApps(fakeTV({record: {home: 'com.webos.app.fullhome'}}), [STOCK, 'com.webos.app.fullhome']);
		await chooser(tv2, [5, 6, 1]).restoreStock();
		expect(tv2.luna).toEqual([GET, LIST]);
	});

	test('8.3.0: write the stock home id (not null), keep the rest, close the custom home', async () => {
		const tv = withApps(fakeTV({record: {home: MINE, kidsApp: 'k'}}), [STOCK]);
		const res = await chooser(tv, [8, 3, 0]).restoreStock();
		expect(res.ok).toBe(true);
		expect(tv.luna).toEqual([GET, LIST, SET({home: STOCK, kidsApp: 'k'})]);
		expect(tv.files).toEqual([['rm', HOME_FILE], [PREVIOUS_FILE, MINE + '\n']]);
		expect(tv.commands).toEqual([STOP, REFRESH, WATCH]);
		expect(JSON.stringify(tv.luna)).not.toContain('"home":null');
	});

	test.each([[[6, 6, 0]], [[7, 6, 0]], [[8, 4, 0]], [[9, 2, 4]]])('%j: fullhome is the stock id when installed', async (version) => {
		const tv = withApps(fakeTV({record: {home: MINE}}), [STOCK, 'com.webos.app.fullhome']);
		await chooser(tv, version).restoreStock();
		expect(tv.luna).toEqual([GET, LIST, SET({home: 'com.webos.app.fullhome'})]);
	});

	test('6 to 9: a record without home gets the stock id; a record already stock is not written; an absent record is left absent', async () => {
		const tv = withApps(fakeTV({record: {kidsApp: 'k'}}), [STOCK]);
		await chooser(tv, [8, 3, 0]).restoreStock();
		expect(tv.luna).toEqual([GET, LIST, SET({home: STOCK, kidsApp: 'k'})]);
		const tv2 = withApps(fakeTV({record: {home: STOCK}}), [STOCK]);
		await chooser(tv2, [8, 3, 0]).restoreStock();
		expect(tv2.luna).toEqual([GET, LIST]);
		const tv3 = withApps(fakeTV(), [STOCK]);
		await chooser(tv3, [8, 3, 0]).restoreStock();
		expect(tv3.luna).toEqual([GET, LIST]);
	});

	test('9.2.1 uses setSystemSettings, never setDefaultApp', async () => {
		const tv = withApps(fakeTV({record: {home: MINE}}), [STOCK]);
		await chooser(tv, [9, 2, 1]).restoreStock();
		expect(tv.luna).toEqual([GET, LIST, SET({home: STOCK})]);
		expect(JSON.stringify(tv.luna)).not.toContain('setDefaultApp');
	});

	test.each([[[10, 3, 1]], [[11, 2, 0]]])('%j: also removes the override and rescans', async (version) => {
		const tv = fakeTV({record: {home: MINE, kidsApp: 'k'}, profile: profileReply(MINE)});
		const res = await chooser(tv, version).restoreStock();
		expect(res.ok).toBe(true);
		expect(tv.luna).toEqual([GET, GETPROFILE, SET({home: null, kidsApp: 'k'}), RESCAN, GETPROFILE]);
		expect(tv.files).toEqual([['rm', HOME_FILE], ['rm', OVERRIDE_PATH], [PREVIOUS_FILE, MINE + '\n']]);
		expect(tv.commands).toEqual([STOP, WATCH]);
	});

	test.each([[null], ['']])('home %j reads as unset: the stock id is written on 5.x to 9.x, nothing on 10.x', async (value) => {
		const tv = withApps(fakeTV({record: {home: value, kidsApp: 'k'}}), [STOCK]);
		const c = chooser(tv, [8, 3, 0]);
		expect((await c.readState()).state.recordHome).toBeNull();
		await c.restoreStock();
		expect(tv.luna).toEqual([GET, GET, LIST, SET({home: STOCK, kidsApp: 'k'})]);
		const tv5 = withApps(fakeTV({record: {home: value}}), [STOCK]);
		await chooser(tv5, [5, 6, 1]).restoreStock();
		expect(tv5.luna).toEqual([GET, LIST, SET({home: STOCK})]);
		const tv10 = fakeTV({record: {home: value, kidsApp: 'k'}, profile: profileReply(STOCK)});
		await chooser(tv10, [10, 3, 1]).restoreStock();
		expect(JSON.stringify(tv10.luna)).not.toContain('setSystemSettings');
	});

	test.each([[[6, 6, 0]], [[9, 2, 1]], [[10, 3, 1]], [[5, 6, 1]]])('%j: choosing com.webos.app.home runs restoreStock', async (version) => {
		const tv = withApps(fakeTV({record: {home: MINE}, profile: profileReply(MINE)}), [STOCK]);
		const res = await chooser(tv, version).apply(STOCK);
		expect(res.ok).toBe(true);
		expect(tv.files[0]).toEqual(['rm', HOME_FILE]);
		expect(JSON.stringify(tv.luna)).not.toContain('setDefaultApp');
		expect(tv.commands).toContain(STOP);
		// LG Home: no override at all, so no idleApp of Home Chooser's
		expect(tv.files.filter((f) => f[0] !== 'rm' && f[0] !== PREVIOUS_FILE)).toEqual([]);
		// the launcher it replaces is closed later by the watcher, never here
		NO_CLOSE(tv);
		expect(tv.previous()).toBe(MINE + '\n');
		expect(tv.files.some((f) => f[1] === OVERRIDE_PATH)).toBe(version[0] >= 10);
	});

	test('other stock homes stay ordinary choices', async () => {
		const tv = fakeTV({record: {}});
		await chooser(tv, [8, 3, 0]).apply('com.webos.app.seniorhome');
		expect(tv.files[0]).toEqual([HOME_FILE, 'com.webos.app.seniorhome']);
		expect(tv.record()).toEqual({home: 'com.webos.app.seniorhome'});
	});

	test('profile-only choice: the custom profile home is left to the watcher, once', async () => {
		const tv = fakeTV({record: {}, profile: profileReply(MINE)});
		await chooser(tv, [11, 2, 0]).restoreStock();
		expect(tv.luna).toEqual([GET, GETPROFILE, RESCAN, GETPROFILE]);
		expect(tv.previous()).toBe(MINE + '\n');
		expect(tv.commands).toEqual([STOP, WATCH]);
	});

	test('no custom home to close: the previous-launcher file is removed and no watcher is started', async () => {
		const tv = withApps(fakeTV({record: {home: STOCK}, previous: ''}), [STOCK]);
		const res = await chooser(tv, [8, 3, 0]).restoreStock();
		expect(res.ok).toBe(true);
		expect(tv.previous()).toBeNull();
		expect(tv.commands).toEqual([STOP, REFRESH, 'unlinkEntry']);
		NO_CLOSE(tv);
	});

	test('no custom home to close, the Home key hook enabled: the boot entry stays', async () => {
		const tv = withApps(fakeTV({record: {home: STOCK}, previous: '', homekey: true}), [STOCK]);
		const res = await chooser(tv, [8, 3, 0]).restoreStock();
		expect(res.ok).toBe(true);
		expect(tv.commands).toEqual([STOP, REFRESH]);
	});

	test('a custom home to close: the watcher removes the boot entry, not the app', async () => {
		const tv = withApps(fakeTV({record: {home: MINE}}), [STOCK]);
		await chooser(tv, [8, 3, 0]).restoreStock();
		expect(tv.commands).not.toContain('unlinkEntry');
	});

	test('a launcher still waiting from an earlier switch is kept with the custom home', async () => {
		const tv = withApps(fakeTV({record: {home: MINE}, previous: `${OTHER}\n`}), [STOCK]);
		await chooser(tv, [9, 2, 4]).restoreStock();
		expect(tv.previous()).toBe(`${OTHER}\n${MINE}\n`);
		expect(tv.commands).toEqual([STOP, REFRESH, WATCH]);
	});

	test('a watcher that does not start for the close is a warning', async () => {
		const tv = withApps(fakeTV({record: {home: MINE}}), [STOCK]);
		const inner = tv.exec;
		tv.exec = async (c) => {
			if (c === WATCH) throw new Error('systemd-run failed');
			return inner(c);
		};
		const res = await chooser(tv, [8, 3, 0]).restoreStock();
		expect(res.ok).toBe(true);
		expect(res.warnings).toEqual([`Could not start the watcher to close ${MINE}: systemd-run failed`]);
	});
});

describe('persistence', () => {
	test('apply writes the bare id first, before any platform write', async () => {
		const tv = fakeTV({record: {}});
		await chooser(tv, [8, 3, 0]).apply(MINE);
		expect(tv.calls[1]).toEqual(['write', HOME_FILE]);
		expect(tv.files[0]).toEqual([HOME_FILE, MINE]);
		expect(tv.files[0][1]).not.toMatch(/\s/);
	});

	test('apply: a watcher that does not start is a warning', async () => {
		const tv = fakeTV({record: {}});
		const inner = tv.exec;
		tv.exec = async (c) => {
			if (c === WATCH) throw new Error('systemd-run failed');
			return inner(c);
		};
		const res = await chooser(tv, [8, 3, 0]).apply(MINE);
		expect(res.ok).toBe(true);
		expect(res.warnings).toContain('Could not start the watcher: systemd-run failed');
	});

	test('apply: a failed choice file write stops before the platform writes', async () => {
		const tv = fakeTV({record: {}});
		tv.writeFile = async () => { throw new Error('read-only file system'); };
		const res = await chooser(tv, [8, 3, 0]).apply(MINE);
		expect(res.ok).toBe(false);
		expect(tv.luna).toEqual([GET]);
		expect(tv.commands).toEqual([]);
	});

	test('restore: the watcher is stopped and the choice removed before the record changes', async () => {
		const tv = fakeTV({record: {home: MINE, kidsApp: 'k'}});
		tv.rootLuna = ((inner) => async (uri, payload) => (uri.endsWith('/listApps') ? (tv.calls.push(['luna', uri]), {returnValue: true, apps: [{id: STOCK}]}) : inner(uri, payload)))(tv.rootLuna);
		await chooser(tv, [8, 3, 0]).restoreStock();
		expect(tv.calls.slice(0, 5).map((c) => c[0] + ':' + (c[1].split('/').pop()))).toEqual([
			'luna:getSystemSettings', 'exec:' + STOP.split('/').pop(), 'remove:home', 'luna:listApps', 'luna:setSystemSettings'
		]);
	});

	test('restore: a failing stop is a warning', async () => {
		const tv = fakeTV({record: {home: MINE}});
		const inner = tv.exec;
		tv.exec = async (c) => {
			if (c === STOP) throw new Error('boom');
			return inner(c);
		};
		const res = await chooser(withApps(tv, [STOCK]), [8, 3, 0]).restoreStock();
		expect(res.ok).toBe(true);
		expect(res.warnings).toContain('Could not stop the watcher: boom');
	});

	test('restore on 10.x and later does not touch the Home key hook', async () => {
		const tv = fakeTV({record: {home: MINE}, profile: profileReply(MINE)});
		await chooser(tv, [11, 2, 0]).restoreStock();
		expect(tv.commands).toEqual([STOP, WATCH]);
	});

	test('the entry is a named operation of the service, never a shell command', () => {
		expect(ensureEntryCommand()).toBe('linkEntry');
		expect(startWatcherCommand()).toBe('watch');
		expect(STOP).toBe('stopWatcher');
		[ensureEntryCommand(), startWatcherCommand(), STOP, persistenceStatusCommand()].forEach((c) => expect(c).toMatch(/^[A-Za-z]+$/));
	});

	test('persistence status is parsed', async () => {
		const tv = fakeTV({record: {}});
		tv.exec = async (c) => ({
			stdout: c === persistenceStatusCommand()
				? `entry=link:${BOOT_SCRIPT}\nactive=active\nstored=my.home\nhomekey=yes\n--log\nline one\nline two\n`
				: '',
			stderr: ''
		});
		const res = await chooser(tv, [8, 3, 0]).readPersistence();
		expect(res.ok).toBe(true);
		expect(res.persistence).toEqual({entry: 'ok', entryTarget: BOOT_SCRIPT, active: 'active', stored: 'my.home', homekeyEnabled: true, log: ['line one', 'line two']});
	});

	test.each([
		['entry=link:/somewhere/else\nactive=inactive\nstored=\nhomekey=no\n--log\n', 'other'],
		['entry=dangling:/gone\nactive=\nstored=\nhomekey=no\n--log\n', 'dangling'],
		['entry=file\nactive=\nstored=\nhomekey=no\n--log\n', 'file'],
		['entry=none\nactive=\nstored=\nhomekey=no\n--log\n', 'none']
	])('entry state of %j is %s', (text, entry) => {
		expect(parsePersistenceStatus(text).entry).toBe(entry);
	});
});

describe('diagnoseExecFailure', () => {
	test('names the webOS 26 denial', () => {
		expect(diagnoseExecFailure('Denied method call "exec" for category "/"')).toMatch(/webOS 26 and later/);
	});
	test('names a missing Homebrew Channel', () => {
		expect(diagnoseExecFailure('Service does not exist: org.webosbrew.hbchannel.service')).toMatch(/Homebrew Channel is not reachable/);
	});
});

describe('stock replies from the study', () => {
	test('launchDefaultApp refusal text is carried as-is', async () => {
		const tv = fakeTV({failures: {getSystemSettings: NOT_SET}});
		const res = await chooser(tv, [6, 6, 0]).readState();
		expect(res.errors).toEqual(['no default app is set for the category']);
	});
});

describe('describeMissingChoice', () => {
	const candidates = [{id: 'com.webos.app.home'}, {id: 'my.home'}];
	test('says so when the stored choice is not an installed home app', () => {
		expect(describeMissingChoice('gone.home', candidates)).toMatch(/gone\.home is not installed; stock Home in use/);
	});
	test('is silent with no stored choice or with the app present', () => {
		expect(describeMissingChoice('', candidates)).toBe('');
		expect(describeMissingChoice(undefined, candidates)).toBe('');
		expect(describeMissingChoice('my.home', candidates)).toBe('');
	});
});
