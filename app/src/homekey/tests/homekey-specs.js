import {ServiceError} from '../../platform/errors';
import {homeKeyHookApplies, createHomeKeyHook, parseStatus} from '..';
import {BOOT_SCRIPT, HOMEKEY_FLAG, ensureEntryCommand, homekeyCommand, persistenceStatusCommand} from '../../homechooser/persistence';

const RUN_WORKER = 'homekeyApply';
const RUN_WORKER_SAM = 'homekeyApplySam';
const STOP = 'homekeyStop';
const REFRESH = 'homekeyRefresh';
const ENSURE = ensureEntryCommand();
const ABSENT = {returnValue: false, errorCode: -1, errorText: 'There is no matched result from DB'};
const GET = ['luna://com.webos.settingsservice/getSystemSettings', {category: 'general', keys: ['defaultApps']}];
const SET = (defaultApps) => ['luna://com.webos.settingsservice/setSystemSettings', {category: 'general', settings: {defaultApps}}];

// A fake TV: `record` is general/defaultApps (undefined = never written); `statusOut` what the
// script's `status` prints; `installed` is the enable flag file, `entry` the init.d link. `mounted`
// only changes when the test says so (a boot mounts the patch; the app never does).
const fakeTV = ({record, patch = 'possible', dataMissing = false, refreshFails = false, failLuna = null} = {}) => {
	const calls = [];
	const tv = {installed: false, entry: 'none', mounted: false};
	// the worker's reboot line: the flag is on and nothing is mounted (anchors match), or the flag is off and something is
	const statusText = () => {
		if (dataMissing) return 'data=missing\nmounted=no\npatch=refused\nreboot=no\n';
		if (tv.mounted) return `mounted=yes\npatch=possible\nreboot=${tv.installed ? 'no' : 'needed'}\n`;
		const can = patch === 'possible';
		return `mounted=no\npatch=${patch}\nreboot=${tv.installed && can ? 'needed' : 'no'}\n`;
	};
	const rootLuna = async (uri, payload) => {
		calls.push(['luna', uri, payload]);
		if (failLuna) throw new ServiceError(failLuna);
		switch (uri.replace('luna://', '')) {
			case 'com.webos.settingsservice/getSystemSettings':
				if (record === undefined) throw new ServiceError(ABSENT);
				return {returnValue: true, category: 'general', settings: {defaultApps: record}};
			case 'com.webos.settingsservice/setSystemSettings':
				record = payload.settings.defaultApps;
				return {returnValue: true};
			default:
				throw new Error('unexpected call ' + uri);
		}
	};
	const exec = async (command) => {
		calls.push(['exec', command]);
		if (command === persistenceStatusCommand()) {
			const entry = tv.entry === 'ok' ? `link:${BOOT_SCRIPT}` : tv.entry;
			return {stdout: `entry=${entry}\nactive=\nstored=\nhomekey=${tv.installed ? 'yes' : 'no'}\n--log\n`, stderr: ''};
		}
		if (command === homekeyCommand('status')) return {stdout: statusText(), stderr: ''};
		if (command === homekeyCommand('prepare') || command === homekeyCommand('clear')) return {stdout: '', stderr: ''};
		if (command === ENSURE) {
			tv.entry = 'ok';
			return {stdout: '', stderr: ''};
		}
		// the app never mounts, unmounts or restarts anything: these throw so a test sees the call
		if (command === REFRESH) {
			if (refreshFails) throw new ServiceError({returnValue: false, errorText: 'exit 1', stderrString: 'refresh failed'});
			return {stdout: '', stderr: ''};
		}
		throw new Error('unexpected command ' + command);
	};
	const writeFile = async (path, text) => {
		calls.push(['write', path, text]);
		if (path === HOMEKEY_FLAG) tv.installed = true;
		else throw new Error('unexpected writeFile ' + path);
	};
	const removeFile = async (path) => {
		calls.push(['remove', path]);
		if (path === HOMEKEY_FLAG) tv.installed = false;
	};
	return {rootLuna, exec, writeFile, removeFile, calls, tv, record: () => record};
};

const homeKeyHook = (tv, version = [6, 6, 0]) => createHomeKeyHook({
	rootLuna: tv.rootLuna, exec: tv.exec, writeFile: tv.writeFile, removeFile: tv.removeFile, version
});

const kinds = (tv) => tv.calls.map((c) => c[0] === 'luna' ? c[1].split('/').pop() : c[0] === 'exec' ? c[1] : (c[0] === 'write' ? 'write ' : 'rm ') + c[1]);

describe('homeKeyHookApplies', () => {
	test.each([[5, false], [6, true], [7, true], [9, true], [10, false], [11, false]])('webOS TV %i', (major, applies) => {
		expect(homeKeyHookApplies([major, 0, 0])).toBe(applies);
		expect(homeKeyHook(fakeTV(), [major, 0, 0]).applies).toBe(applies);
	});
});

describe('parseStatus', () => {
	test('reads repeated files, skips and refusal reasons', () => {
		expect(parseStatus('mounted=no\nrecovered=stock\nreason=QML unavailable\n')).toMatchObject({recovered: 'stock', reason: 'QML unavailable'});
		expect(parseStatus('mounted=yes\npatch=possible\nunpatched-sites=1\nskipped=/nudge: anchor\nfile=/a.qml\nfile=/b.js\n')).toMatchObject({mounted: true, patch: 'possible', unpatchedSites: 1, files: ['/a.qml', '/b.js'], skipped: ['/nudge: anchor']});
		expect(parseStatus('patch=refused\nreason=anchor missing\n')).toMatchObject({patch: 'refused', reason: 'anchor missing'});
		expect(parseStatus('hook=stale\ncache-generation=123-45\nreboot=needed\n')).toMatchObject({hook: 'stale', cacheGeneration: '123-45', rebootNeeded: true});
	});
});

describe('status', () => {
	test('reports the flag, the init.d link and the script status', async () => {
		const tv = fakeTV();
		tv.tv.installed = true;
		tv.tv.entry = 'ok';
		const res = await homeKeyHook(tv).status();
		expect(res.ok).toBe(true);
		expect(res.status).toMatchObject({enabled: true, entry: 'ok', dataMissing: false, mounted: false, patch: 'possible'});
		expect(res.steps.map((s) => s.kind)).toEqual(['exec', 'exec']);
	});
});

describe('install', () => {
	test('absent key: create it, enable and link; no worker run, no restart; a reboot finishes it', async () => {
		const tv = fakeTV();
		const res = await homeKeyHook(tv).install();
		expect(res.ok).toBe(true);
		expect(res.createdKey).toBe(true);
		expect(res.rebootNeeded).toBe(true);
		expect(tv.record()).toEqual({});
		expect(tv.calls.filter((c) => c[0] === 'luna').map((c) => [c[1], c[2]])).toEqual([GET, SET({})]);
		const seq = kinds(tv);
		const at = (s) => seq.findIndex((x) => x === s || (x.startsWith && x.startsWith(s)));
		expect(at('getSystemSettings')).toBeLessThan(at('setSystemSettings'));
		expect(at('setSystemSettings')).toBeLessThan(at('write ' + HOMEKEY_FLAG));
		expect(at('write ' + HOMEKEY_FLAG)).toBeLessThan(at(ENSURE));
		[RUN_WORKER, RUN_WORKER_SAM, STOP, REFRESH, 'restartSam'].forEach((x) => expect(seq).not.toContain(x));
		expect(seq.filter((x) => /\bcp\b/.test(x))).toEqual([]);
		expect(tv.tv.installed).toBe(true);
		expect(tv.tv.entry).toBe('ok');
		expect(res.status).toMatchObject({enabled: true, entry: 'ok', mounted: false, rebootNeeded: true});
	});

	test('record present: no write, no worker run, no sam restart', async () => {
		const tv = fakeTV({record: {home: 'x.y', kidsApp: 'k'}});
		const res = await homeKeyHook(tv, [9, 2, 1]).install();
		expect(res.ok).toBe(true);
		expect(res.createdKey).toBe(false);
		expect(res.rebootNeeded).toBe(true);
		expect(tv.record()).toEqual({home: 'x.y', kidsApp: 'k'});
		const seq = kinds(tv);
		expect(seq).not.toContain('setSystemSettings');
		[RUN_WORKER, RUN_WORKER_SAM, 'restartSam'].forEach((x) => expect(seq).not.toContain(x));
	});

	test('patch mounted but the flag was off (restored, not yet restarted): the flag comes back and no reboot is needed', async () => {
		const tv = fakeTV({record: {}, patch: 'possible'});
		tv.tv.mounted = true;
		tv.tv.release = '8.3.0';
		const res = await homeKeyHook(tv, [8, 3, 0]).install();
		expect(res.ok).toBe(true);
		expect(tv.tv.installed).toBe(true);
		expect(res.rebootNeeded).toBe(false);
		expect(res.status).toMatchObject({mounted: true, patch: 'possible'});
	});

	test('anchor mismatch: fails before changing anything', async () => {
		const tv = fakeTV({patch: 'refused'});
		const res = await homeKeyHook(tv).install();
		expect(res.ok).toBe(false);
		expect(res.errors[0]).toMatch(/Home key hook refused/);
		expect(kinds(tv)).not.toContain('setSystemSettings');
		expect(tv.tv.installed).toBe(false);
		expect(tv.tv.entry).toBe('none');
	});

	test('data missing from the package: fails before changing anything', async () => {
		const tv = fakeTV({dataMissing: true});
		const res = await homeKeyHook(tv).install();
		expect(res.ok).toBe(false);
		expect(res.errors[0]).toMatch(/missing from the service package/);
		expect(tv.tv.installed).toBe(false);
	});

	test('already mounted needs no structural recheck', async () => {
		const tv = fakeTV({record: {}, patch: 'possible'});
		tv.tv.mounted = true;
		tv.tv.release = '8.3.0';
		const res = await homeKeyHook(tv, [8, 3, 0]).install();
		expect(res.ok).toBe(true);
		expect(res.status.mounted).toBe(true);
	});

	test('a settings error other than an absent key stops before the copy', async () => {
		const tv = fakeTV({failLuna: {returnValue: false, errorText: 'Access denied'}});
		const res = await homeKeyHook(tv).install();
		expect(res.ok).toBe(false);
		expect(res.errors[0]).toMatch(/Access denied/);
		expect(tv.tv.installed).toBe(false);
		const last = res.steps[res.steps.length - 1];
		expect(last).toMatchObject({kind: 'luna', ok: false});
	});
});

describe('remove', () => {
	test('delete the flag, refresh the baked id, never stop; the init.d link stays; a mounted patch needs a reboot', async () => {
		const tv = fakeTV({record: {}});
		await homeKeyHook(tv).install();
		tv.tv.mounted = true; // the next boot mounted it
		tv.tv.release = '9.2.1';
		tv.calls.length = 0;
		const res = await homeKeyHook(tv).remove();
		expect(res.ok).toBe(true);
		const seq = kinds(tv);
		expect(seq[0]).toBe('rm ' + HOMEKEY_FLAG);
		expect(seq[1]).toBe(homekeyCommand('clear'));
		expect(seq[2]).toBe(REFRESH);
		expect(seq).not.toContain(STOP);
		[RUN_WORKER, RUN_WORKER_SAM, 'restartSam'].forEach((x) => expect(seq).not.toContain(x));
		expect(seq.filter((x) => x.startsWith('rm '))).toEqual(['rm ' + HOMEKEY_FLAG]);
		expect(res.rebootNeeded).toBe(true);
		expect(res.status).toMatchObject({enabled: false, entry: 'ok', mounted: true, rebootNeeded: true});
	});

	test('nothing mounted: no reboot needed', async () => {
		const tv = fakeTV({record: {}});
		await homeKeyHook(tv).install();
		const res = await homeKeyHook(tv).remove();
		expect(res.ok).toBe(true);
		expect(res.rebootNeeded).toBe(false);
	});

	test('a failed refresh is reported but the flag is still deleted', async () => {
		const tv = fakeTV({record: {}, refreshFails: true});
		await homeKeyHook(tv).install();
		const res = await homeKeyHook(tv).remove();
		expect(res.ok).toBe(false);
		expect(res.errors[0]).toMatch(/refresh failed/);
		expect(tv.tv.installed).toBe(false);
	});
});


describe('parseStatus reboot', () => {
	test('reboot=needed', () => {
		expect(parseStatus('mounted=no\npatch=possible\nreboot=needed\n').rebootNeeded).toBe(true);
		expect(parseStatus('mounted=no\npatch=possible\nreboot=no\n').rebootNeeded).toBe(false);
		expect(parseStatus('mounted=yes\npatch=possible\n').rebootNeeded).toBe(false);
	});
});
