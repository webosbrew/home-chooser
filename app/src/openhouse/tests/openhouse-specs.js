import {ServiceError} from '../../platform/errors';
import {
	OVERRIDE_PATH, buildOverride, createHomeChooser, extractDefaultAppsProfile, releaseInfo
} from '..';
import {diagnoseExecFailure} from '../diagnose';
import {describeMissingChoice} from '../format';
import {
	HOME_FILE, KEYFILTER_SCRIPT, ensureEntryCommand, parsePersistenceStatus, persistenceStatusCommand, startWatcherCommand,
	stopWatcherCommand, ENTRY_SCRIPT
} from '../persistence';

const MINE = 'org.webosbrew.openhouse.launcher';
const OTHER = 'com.example.otherhome';
const STOCK = 'com.webos.app.home';
const ENSURE = ensureEntryCommand();
const WATCH = startWatcherCommand();
const STOP = stopWatcherCommand();
const REFRESH = `sh '${KEYFILTER_SCRIPT}' refresh`;

const ABSENT = {returnValue: false, errorCode: -1, errorText: 'There is no matched result from DB'};
const UNKNOWN = {returnValue: false, errorText: 'Unknown method "setDefaultApp" for category "/"'};
const NOT_SET = {returnValue: false, errorCode: 1, errorText: 'no default app is set for the category'};

const profileReply = (home = STOCK, extra = {}) => ({
	returnValue: true,
	profiles: {defaultApps: Object.assign({_packageIncluded: true, _configuration: 'defaultApps.json', support: true, categories: {home, aod: 'com.webos.app.lifeonscreen'}}, extra)}
});

// A fake TV: `record` is general/defaultApps (undefined = never written); `profile` the getProfiles reply.
const fakeTV = ({record, profile, setDefaultApp = true, failures = {}} = {}) => {
	const luna = [];
	const files = [];
	const commands = [];
	const calls = [];
	const rootLuna = async (uri, payload) => {
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
			case 'com.webos.service.systemprofile/getProfiles':
				if (!profile) throw new ServiceError({returnValue: false, errorText: 'Service does not exist: com.webos.service.systemprofile'});
				return profile;
			case 'com.webos.service.systemprofile/debug/rescan':
				return {returnValue: true};
			default:
				throw new Error('unexpected call ' + uri);
		}
	};
	const writeFile = async (path, text) => {
		calls.push(['write', path]);
		files.push([path, text]);
		if (path === HOME_FILE) return;
		// the real service reads the override on rescan
		const o = JSON.parse(text).defaultApps;
		profile = {returnValue: true, profiles: {defaultApps: Object.assign({}, o)}};
	};
	const removeFile = async (path) => {
		calls.push(['remove', path]);
		files.push(['rm', path]);
		profile = profileReply(STOCK);
	};
	const exec = async (command) => {
		calls.push(['exec', command]);
		commands.push(command);
		return {stdout: '', stderr: ''};
	};
	return {rootLuna, writeFile, removeFile, exec, luna, files, commands, calls, record: () => record};
};

const chooser = (tv, version) => createHomeChooser({
	rootLuna: tv.rootLuna, exec: tv.exec, writeFile: tv.writeFile, removeFile: tv.removeFile, version
});

const GET = ['luna://com.webos.settingsservice/getSystemSettings', {category: 'general', keys: ['defaultApps']}];
const SET = (defaultApps) => ['luna://com.webos.settingsservice/setSystemSettings', {category: 'general', settings: {defaultApps}}];
const SETDEFAULT = (appId) => ['luna://com.webos.applicationManager/setDefaultApp', {category: 'home', appId}];
const CLOSE = (id) => ['luna://com.webos.applicationManager/closeByAppId', {id, forceMode: true}];
const GETPROFILE = ['luna://com.webos.service.systemprofile/getProfiles', {names: ['defaultApps']}];
const RESCAN = ['luna://com.webos.service.systemprofile/debug/rescan', {}];

describe('releaseInfo', () => {
	test.each([
		[5, false, false, false],
		[6, true, false, false],
		[8, true, false, false],
		[9, true, false, true],
		[10, false, true, true],
		[11, false, true, true]
	])('webOS TV %i', (major, backport, profile, setter) => {
		expect(releaseInfo([major, 0, 0])).toMatchObject({needsKeyFilterBackport: backport, usesProfile: profile, hasSetDefaultApp: setter});
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
		expect(res.steps[0].payload).toEqual({});
	});
});

describe('readState', () => {
	test('absent key is not a failure', async () => {
		const res = await chooser(fakeTV(), [6, 6, 0]).readState();
		expect(res.ok).toBe(true);
		expect(res.state.record).toBeNull();
		expect(res.willRestartSam).toBe(true);
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
});

describe('apply', () => {
	test('5.6.1: carries other entries, closes the previous home, no sam restart', async () => {
		const tv = fakeTV({record: {home: 'com.webos.app.fullhome', other: 'x'}});
		const res = await chooser(tv, [5, 6, 1]).apply(MINE);
		expect(res.ok).toBe(true);
		expect(tv.luna).toEqual([GET, SET({home: MINE, other: 'x'}), CLOSE('com.webos.app.fullhome')]);
		expect(tv.files).toEqual([[HOME_FILE, MINE]]);
		expect(tv.commands).toEqual([ENSURE, WATCH]);
		expect(res.needsKeyFilterBackport).toBe(false);
		expect(res.restartedSam).toBe(false);
	});

	test('6.6.0, record absent: write, close stock home, restart sam last', async () => {
		const tv = fakeTV();
		const notices = [];
		const res = await chooser(tv, [6, 6, 0]).apply(MINE, {onNotice: (n) => notices.push(n)});
		expect(res.ok).toBe(true);
		expect(tv.luna).toEqual([GET, SET({home: MINE}), CLOSE(STOCK)]);
		expect(tv.commands).toEqual([ENSURE, WATCH, 'systemctl restart sam']);
		expect(tv.calls[tv.calls.length - 1]).toEqual(['exec', 'systemctl restart sam']);
		expect(notices).toHaveLength(1);
		expect(res.restartedSam).toBe(true);
		expect(res.needsKeyFilterBackport).toBe(true);
	});

	test('6.6.0, record present: no restart', async () => {
		const tv = fakeTV({record: {home: OTHER, kidsApp: 'k'}});
		const res = await chooser(tv, [6, 6, 0]).apply(MINE);
		expect(tv.luna).toEqual([GET, SET({home: MINE, kidsApp: 'k'}), CLOSE(OTHER)]);
		expect(tv.commands).toEqual([ENSURE, WATCH]);
		expect(res.restartedSam).toBe(false);
	});

	test('8.3.0, record absent: restart sam', async () => {
		const tv = fakeTV();
		await chooser(tv, [8, 3, 0]).apply(MINE);
		expect(tv.luna).toEqual([GET, SET({home: MINE}), CLOSE(STOCK)]);
		expect(tv.commands).toEqual([ENSURE, WATCH, 'systemctl restart sam']);
	});

	test('a failed close is a warning, not an error', async () => {
		const tv = fakeTV({record: {home: OTHER}, failures: {closeByAppId: {returnValue: false, errorText: 'app is not running'}}});
		const res = await chooser(tv, [8, 3, 0]).apply(MINE);
		expect(res.ok).toBe(true);
		expect(res.warnings).toEqual([`Could not close ${OTHER}: app is not running`]);
	});

	test('choosing the current home does not close it', async () => {
		const tv = fakeTV({record: {home: MINE}});
		await chooser(tv, [8, 3, 0]).apply(MINE);
		expect(tv.luna).toEqual([GET, SET({home: MINE})]);
	});

	test('9.2.1: setDefaultApp, no profile, no restart', async () => {
		const tv = fakeTV({record: {kidsApp: 'k'}});
		const res = await chooser(tv, [9, 2, 1]).apply(MINE);
		expect(res.ok).toBe(true);
		expect(tv.luna).toEqual([GET, SETDEFAULT(MINE), CLOSE(STOCK)]);
		expect(tv.record()).toEqual({kidsApp: 'k', home: MINE});
		expect(tv.files).toEqual([[HOME_FILE, MINE]]);
		expect(tv.commands).toEqual([ENSURE, WATCH]);
		expect(res.needsKeyFilterBackport).toBe(true);
	});

	test('9.2.1: Unknown method falls back to setSystemSettings with the other entries', async () => {
		const tv = fakeTV({record: {kidsApp: 'k'}, setDefaultApp: false});
		const res = await chooser(tv, [9, 2, 1]).apply(MINE);
		expect(res.ok).toBe(true);
		expect(tv.luna).toEqual([GET, SETDEFAULT(MINE), SET({kidsApp: 'k', home: MINE}), CLOSE(STOCK)]);
		expect(res.warnings[0]).toMatch(/setDefaultApp is unknown/);
		expect(tv.commands).toEqual([ENSURE, WATCH]);
	});

	test('9.2.1: another setDefaultApp error is a failure', async () => {
		const tv = fakeTV({record: {}, failures: {setDefaultApp: {returnValue: false, errorText: 'Fail to update default apps to settings service'}}});
		const res = await chooser(tv, [9, 2, 1]).apply(MINE);
		expect(res.ok).toBe(false);
		expect(res.errors).toEqual(['Fail to update default apps to settings service']);
		expect(tv.luna).toEqual([GET, SETDEFAULT(MINE)]);
	});

	test.each([[[10, 3, 1]], [[11, 2, 0]]])('%j: record, override file, rescan, verify, close', async (version) => {
		const tv = fakeTV({record: {kidsApp: 'k'}, profile: profileReply()});
		const res = await chooser(tv, version).apply(MINE);
		expect(res.ok).toBe(true);
		expect(res.needsKeyFilterBackport).toBe(false);
		expect(tv.luna).toEqual([GET, GETPROFILE, SETDEFAULT(MINE), RESCAN, GETPROFILE, CLOSE(STOCK)]);
		expect(tv.files).toHaveLength(2);
		expect(tv.files[0]).toEqual([HOME_FILE, MINE]);
		expect(tv.files[1][0]).toBe('/var/webos-profile/override/org.webosbrew.openhouse.json');
		expect(JSON.parse(tv.files[1][1])).toEqual({
			defaultApps: {support: true, categories: {home: MINE, aod: 'com.webos.app.lifeonscreen'}}
		});
		expect(tv.calls.map((c) => c[0] + ':' + (c[1].split('/').pop()))).toEqual([
			'luna:getSystemSettings', 'luna:getProfiles', 'write:home', 'luna:setDefaultApp', 'write:org.webosbrew.openhouse.json',
			'luna:rescan', 'luna:getProfiles', 'exec:' + ENSURE.split('/').pop(), 'exec:' + WATCH.split('/').pop(), 'luna:closeByAppId'
		]);
		expect(tv.commands).toEqual([ENSURE, WATCH]);
	});

	test('10.3.1: previous home is the record home, not the profile home', async () => {
		const tv = fakeTV({record: {home: OTHER}, profile: profileReply()});
		await chooser(tv, [10, 3, 1]).apply(MINE);
		expect(tv.luna[tv.luna.length - 1]).toEqual(CLOSE(OTHER));
	});

	test('10.3.1: an earlier custom profile home is the previous when the record has none', async () => {
		const tv = fakeTV({record: {}, profile: profileReply(OTHER)});
		await chooser(tv, [10, 3, 1]).apply(MINE);
		expect(tv.luna[tv.luna.length - 1]).toEqual(CLOSE(OTHER));
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
		expect(tv.files.map((f) => f[0])).toEqual([HOME_FILE]);
		expect(tv.commands).toEqual([]);
	});

	test('the step log records calls, replies and errors', async () => {
		const tv = fakeTV({record: {home: OTHER}});
		const res = await chooser(tv, [8, 3, 0]).apply(MINE);
		expect(res.steps.map((s) => [s.kind, s.ok])).toEqual([
			['luna', true], ['writeFile', true], ['luna', true], ['exec', true], ['exec', true], ['luna', true]
		]);
		expect(res.steps[2]).toMatchObject({uri: SET({})[0], reply: {returnValue: true}});
	});
});

describe('restoreStock', () => {
	const LIST = ['luna://com.webos.applicationManager/listApps', {}];
	const withApps = (tv, ids) => {
		const inner = tv.rootLuna;
		tv.rootLuna = async (uri, payload) => (uri.endsWith('/listApps') ? (tv.luna.push([uri, payload]), {returnValue: true, apps: ids.map((id) => ({id}))}) : inner(uri, payload));
		return tv;
	};

	test('5.6.1: writes fullhome when installed, keeps other entries, closes the custom home', async () => {
		const tv = withApps(fakeTV({record: {home: MINE, kidsApp: 'k'}}), [STOCK, 'com.webos.app.fullhome']);
		const res = await chooser(tv, [5, 6, 1]).restoreStock();
		expect(res.ok).toBe(true);
		expect(tv.luna).toEqual([GET, LIST, SET({home: 'com.webos.app.fullhome', kidsApp: 'k'}), CLOSE(MINE)]);
		expect(tv.files).toEqual([['rm', HOME_FILE]]);
		expect(tv.commands).toEqual([STOP]);
	});

	test('5.x: falls back to com.webos.app.home when fullhome is not installed', async () => {
		const tv = withApps(fakeTV({record: {home: MINE}}), [STOCK]);
		await chooser(tv, [5, 4, 0]).restoreStock();
		expect(tv.luna).toEqual([GET, LIST, SET({home: STOCK}), CLOSE(MINE)]);
	});

	test('5.x: absent record is created with the stock home; a stock home is not closed', async () => {
		const tv = withApps(fakeTV(), [STOCK, 'com.webos.app.fullhome']);
		await chooser(tv, [5, 6, 1]).restoreStock();
		expect(tv.luna).toEqual([GET, LIST, SET({home: 'com.webos.app.fullhome'})]);
		const tv2 = withApps(fakeTV({record: {home: 'com.webos.app.fullhome'}}), [STOCK, 'com.webos.app.fullhome']);
		await chooser(tv2, [5, 6, 1]).restoreStock();
		expect(tv2.luna).toEqual([GET, LIST]);
	});

	test('8.3.0: write home as null, keep the rest, close the custom home', async () => {
		const tv = fakeTV({record: {home: MINE, kidsApp: 'k'}});
		const res = await chooser(tv, [8, 3, 0]).restoreStock();
		expect(res.ok).toBe(true);
		expect(tv.luna).toEqual([GET, SET({home: null, kidsApp: 'k'}), CLOSE(MINE)]);
		expect(tv.files).toEqual([['rm', HOME_FILE]]);
		expect(tv.commands).toEqual([STOP, REFRESH]);
		expect(JSON.stringify(tv.luna)).not.toContain('"home":"com.webos.app.home"');
	});

	test('no home entry and nothing to close: no writes', async () => {
		const tv = fakeTV({record: {kidsApp: 'k'}});
		await chooser(tv, [8, 3, 0]).restoreStock();
		expect(tv.luna).toEqual([GET]);
	});

	test('9.2.1 uses setSystemSettings, never setDefaultApp', async () => {
		const tv = fakeTV({record: {home: MINE}});
		await chooser(tv, [9, 2, 1]).restoreStock();
		expect(tv.luna).toEqual([GET, SET({home: null}), CLOSE(MINE)]);
	});

	test.each([[[10, 3, 1]], [[11, 2, 0]]])('%j: also removes the override and rescans', async (version) => {
		const tv = fakeTV({record: {home: MINE, kidsApp: 'k'}, profile: profileReply(MINE)});
		const res = await chooser(tv, version).restoreStock();
		expect(res.ok).toBe(true);
		expect(tv.luna).toEqual([GET, GETPROFILE, SET({home: null, kidsApp: 'k'}), RESCAN, GETPROFILE, CLOSE(MINE)]);
		expect(tv.files).toEqual([['rm', HOME_FILE], ['rm', OVERRIDE_PATH]]);
		expect(tv.commands).toEqual([STOP]);
	});

	test.each([[null], ['']])('home %j reads as unset: nothing to clear on 8.x, the stock id written on 5.x', async (value) => {
		const tv = fakeTV({record: {home: value, kidsApp: 'k'}});
		const c = chooser(tv, [8, 3, 0]);
		expect((await c.readState()).state.recordHome).toBeNull();
		await c.restoreStock();
		expect(tv.luna).toEqual([GET, GET]);
		const tv5 = withApps(fakeTV({record: {home: value}}), [STOCK]);
		await chooser(tv5, [5, 6, 1]).restoreStock();
		expect(tv5.luna).toEqual([GET, LIST, SET({home: STOCK})]);
	});

	test.each([[[6, 6, 0]], [[9, 2, 1]], [[10, 3, 1]], [[5, 6, 1]]])('%j: choosing com.webos.app.home runs restoreStock', async (version) => {
		const tv = withApps(fakeTV({record: {home: MINE}, profile: profileReply(MINE)}), [STOCK]);
		const res = await chooser(tv, version).apply(STOCK);
		expect(res.ok).toBe(true);
		expect(tv.files[0]).toEqual(['rm', HOME_FILE]);
		expect(JSON.stringify(tv.luna)).not.toContain('setDefaultApp');
		expect(tv.commands).toContain(STOP);
	});

	test('other stock homes stay ordinary choices', async () => {
		const tv = fakeTV({record: {}});
		await chooser(tv, [8, 3, 0]).apply('com.webos.app.seniorhome');
		expect(tv.files[0]).toEqual([HOME_FILE, 'com.webos.app.seniorhome']);
		expect(tv.record()).toEqual({home: 'com.webos.app.seniorhome'});
	});

	test('profile-only choice: closes the custom profile home once', async () => {
		const tv = fakeTV({record: {}, profile: profileReply(MINE)});
		await chooser(tv, [11, 2, 0]).restoreStock();
		expect(tv.luna).toEqual([GET, GETPROFILE, RESCAN, GETPROFILE, CLOSE(MINE)]);
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
		await chooser(tv, [8, 3, 0]).restoreStock();
		expect(tv.calls.slice(0, 4).map((c) => c[0] + ':' + (c[1].split('/').pop()))).toEqual([
			'luna:getSystemSettings', 'exec:' + STOP.split('/').pop(), 'remove:home', 'luna:setSystemSettings'
		]);
	});

	test('restore: a failing stop is a warning', async () => {
		const tv = fakeTV({record: {home: MINE}});
		const inner = tv.exec;
		tv.exec = async (c) => {
			if (c === STOP) throw new Error('boom');
			return inner(c);
		};
		const res = await chooser(tv, [8, 3, 0]).restoreStock();
		expect(res.ok).toBe(true);
		expect(res.warnings).toContain('Could not stop the watcher: boom');
	});

	test('restore on 10.x and later does not touch the key filter', async () => {
		const tv = fakeTV({record: {home: MINE}, profile: profileReply(MINE)});
		await chooser(tv, [11, 2, 0]).restoreStock();
		expect(tv.commands).toEqual([STOP]);
	});

	test('the entry command links, never copies', () => {
		const c = ensureEntryCommand();
		expect(c).toContain(`ln -sf '${ENTRY_SCRIPT}' '/var/lib/webosbrew/init.d/openhouse'`);
		expect(c).not.toMatch(/\bcp\b/);
		expect(c).toContain("rm -f '/var/lib/webosbrew/init.d/openhouse-keyfilter'");
	});

	test('persistence status is parsed', async () => {
		const tv = fakeTV({record: {}});
		tv.exec = async (c) => ({
			stdout: c === persistenceStatusCommand()
				? `entry=link:${ENTRY_SCRIPT}\nactive=active\nstored=my.home\nkeyfilter=yes\n--log\nline one\nline two\n`
				: '',
			stderr: ''
		});
		const res = await chooser(tv, [8, 3, 0]).readPersistence();
		expect(res.ok).toBe(true);
		expect(res.persistence).toEqual({entry: 'ok', entryTarget: ENTRY_SCRIPT, active: 'active', stored: 'my.home', keyfilterEnabled: true, log: ['line one', 'line two']});
	});

	test.each([
		['entry=link:/somewhere/else\nactive=inactive\nstored=\nkeyfilter=no\n--log\n', 'other'],
		['entry=dangling:/gone\nactive=\nstored=\nkeyfilter=no\n--log\n', 'dangling'],
		['entry=file\nactive=\nstored=\nkeyfilter=no\n--log\n', 'file'],
		['entry=none\nactive=\nstored=\nkeyfilter=no\n--log\n', 'none']
	])('entry state of %j is %s', (text, entry) => {
		expect(parsePersistenceStatus(text).entry).toBe(entry);
	});
});

describe('diagnoseExecFailure', () => {
	test('names the webOS 26 denial', () => {
		expect(diagnoseExecFailure('Denied method call "exec" for category "/"')).toMatch(/webOS 26 and later/);
	});
	test('names a missing Homebrew Channel', () => {
		expect(diagnoseExecFailure('Service does not exist: org.webosbrew.hbchannel.service')).toMatch(/Install and start Homebrew Channel/);
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
