import {ServiceError} from '../../platform/errors';
import {backportApplies, createBackport, parseStatus} from '..';
import {ENTRY_SCRIPT, KEYFILTER_FLAG, ensureEntryCommand, keyfilterCommand, persistenceStatusCommand} from '../../homechooser/persistence';

const RUN_WORKER = 'keyfilterApply';
const RUN_WORKER_SAM = 'keyfilterApplySam';
const ENSURE = ensureEntryCommand();
const ABSENT = {returnValue: false, errorCode: -1, errorText: 'There is no matched result from DB'};
const GET = ['luna://com.webos.settingsservice/getSystemSettings', {category: 'general', keys: ['defaultApps']}];
const SET = (defaultApps) => ['luna://com.webos.settingsservice/setSystemSettings', {category: 'general', settings: {defaultApps}}];

// A fake TV: `record` is general/defaultApps (undefined = never written); `statusOut` what the
// script's `status` prints; `installed` is the enable flag file, `entry` the init.d link; running
// the worker flips `mounted`, `stop` flips it back.
const fakeTV = ({record, match = '9.2.1', dataMissing = false, mountWorks = true, stopFails = false, failLuna = null} = {}) => {
	const calls = [];
	const tv = {installed: false, entry: 'none', mounted: false, release: null};
	const statusText = () => {
		if (dataMissing) return 'data=missing\nmounted=no\nmatch=none\n';
		return tv.mounted ? `mounted=yes\nrelease=${tv.release}\n` : `mounted=no\nmatch=${match || 'none'}\n`;
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
			const entry = tv.entry === 'ok' ? `link:${ENTRY_SCRIPT}` : tv.entry;
			return {stdout: `entry=${entry}\nactive=\nstored=\nkeyfilter=${tv.installed ? 'yes' : 'no'}\n--log\n`, stderr: ''};
		}
		if (command === keyfilterCommand('status')) return {stdout: statusText(), stderr: ''};
		if (command === ENSURE) {
			tv.entry = 'ok';
			return {stdout: '', stderr: ''};
		}
		if (command === RUN_WORKER || command === RUN_WORKER_SAM) {
			if (mountWorks) {
				tv.mounted = true;
				tv.release = match;
			}
			return {stdout: '', stderr: ''};
		}
		if (command === keyfilterCommand('stop')) {
			if (stopFails) throw new ServiceError({returnValue: false, errorText: 'exit 1', stderrString: 'umount: busy'});
			tv.mounted = false;
			return {stdout: '', stderr: ''};
		}
		if (command === 'restartSam') return {stdout: '', stderr: ''};
		throw new Error('unexpected command ' + command);
	};
	const writeFile = async (path, text) => {
		calls.push(['write', path, text]);
		if (path !== KEYFILTER_FLAG) throw new Error('unexpected writeFile ' + path);
		tv.installed = true;
	};
	const removeFile = async (path) => {
		calls.push(['remove', path]);
		if (path === KEYFILTER_FLAG) tv.installed = false;
	};
	return {rootLuna, exec, writeFile, removeFile, calls, tv, record: () => record};
};

const backport = (tv, version = [6, 6, 0]) => createBackport({
	rootLuna: tv.rootLuna, exec: tv.exec, writeFile: tv.writeFile, removeFile: tv.removeFile, version
});

const kinds = (tv) => tv.calls.map((c) => c[0] === 'luna' ? c[1].split('/').pop() : c[0] === 'exec' ? c[1] : (c[0] === 'write' ? 'write ' : 'rm ') + c[1]);

describe('backportApplies', () => {
	test.each([[5, false], [6, true], [7, true], [9, true], [10, false], [11, false]])('webOS TV %i', (major, applies) => {
		expect(backportApplies([major, 0, 0])).toBe(applies);
		expect(backport(fakeTV(), [major, 0, 0]).applies).toBe(applies);
	});
});

describe('parseStatus', () => {
	test('reads the script output', () => {
		expect(parseStatus('mounted=yes\nrelease=9.2.1\n')).toEqual({dataMissing: false, mounted: true, release: '9.2.1', match: null});
		expect(parseStatus('mounted=no\nmatch=7.6.0\n')).toEqual({dataMissing: false, mounted: false, release: null, match: '7.6.0'});
		expect(parseStatus('mounted=no\nmatch=none\n')).toMatchObject({match: null});
		expect(parseStatus('data=missing\nmounted=no\nmatch=none\n')).toMatchObject({dataMissing: true});
		expect(parseStatus('mounted=yes\nrelease=unknown\n')).toMatchObject({mounted: true, release: null});
	});
});

describe('status', () => {
	test('reports the flag, the init.d link and the script status', async () => {
		const tv = fakeTV();
		tv.tv.installed = true;
		tv.tv.entry = 'ok';
		const res = await backport(tv).status();
		expect(res.ok).toBe(true);
		expect(res.status).toEqual({enabled: true, entry: 'ok', dataMissing: false, mounted: false, release: null, match: '9.2.1'});
		expect(res.steps.map((s) => s.kind)).toEqual(['exec', 'exec']);
	});
});

describe('install', () => {
	test('absent key: create it, enable and link, run the worker, which restarts sam first', async () => {
		const tv = fakeTV();
		const notices = [];
		const res = await backport(tv).install({onNotice: (n) => notices.push(n)});
		expect(res.ok).toBe(true);
		expect(res.createdKey).toBe(true);
		expect(tv.record()).toEqual({});
		expect(tv.calls.filter((c) => c[0] === 'luna').map((c) => [c[1], c[2]])).toEqual([GET, SET({})]);
		const seq = kinds(tv);
		const at = (s) => seq.findIndex((x) => x === s || (x.startsWith && x.startsWith(s)));
		expect(at('getSystemSettings')).toBeLessThan(at('setSystemSettings'));
		expect(at('setSystemSettings')).toBeLessThan(at('write ' + KEYFILTER_FLAG));
		expect(at('write ' + KEYFILTER_FLAG)).toBeLessThan(at(ENSURE));
				expect(at(ENSURE)).toBeLessThan(at(RUN_WORKER_SAM));
		expect(seq).not.toContain(RUN_WORKER);
		// sam is restarted inside the worker's run, not by a call after it that a closed panel would never make
		expect(seq).not.toContain('restartSam');
		expect(seq.filter((x) => /\bcp\b/.test(x))).toEqual([]);
		expect(res.status).toMatchObject({enabled: true, entry: 'ok', mounted: true, release: '9.2.1'});
		expect(notices).toHaveLength(1);
	});

	test('record present: no write, no sam restart', async () => {
		const tv = fakeTV({record: {home: 'x.y', kidsApp: 'k'}});
		const res = await backport(tv, [9, 2, 1]).install();
		expect(res.ok).toBe(true);
		expect(res.createdKey).toBe(false);
		expect(tv.record()).toEqual({home: 'x.y', kidsApp: 'k'});
		expect(kinds(tv)).not.toContain('setSystemSettings');
		expect(kinds(tv)).not.toContain('restartSam');
		expect(kinds(tv)).toContain(RUN_WORKER);
	});

	test('record present, sam restart asked for: the same one run of the worker, flag and link first', async () => {
		const tv = fakeTV({record: {home: 'x.y'}});
		const res = await backport(tv, [8, 3, 0]).install({samRestart: true});
		expect(res.ok).toBe(true);
		const seq = kinds(tv);
		expect(seq.filter((x) => x === RUN_WORKER_SAM)).toHaveLength(1);
		expect(seq).not.toContain(RUN_WORKER);
		expect(seq).not.toContain('restartSam');
		expect(seq.indexOf('write ' + KEYFILTER_FLAG)).toBeLessThan(seq.indexOf(RUN_WORKER_SAM));
		expect(seq.indexOf(ENSURE)).toBeLessThan(seq.indexOf(RUN_WORKER_SAM));
		expect(tv.record()).toEqual({home: 'x.y'});
	});

	test('no matching set: fails before changing anything', async () => {
		const tv = fakeTV({match: null});
		const res = await backport(tv).install();
		expect(res.ok).toBe(false);
		expect(res.errors[0]).toMatch(/No backport set matches/);
		expect(kinds(tv)).not.toContain('setSystemSettings');
		expect(tv.tv.installed).toBe(false);
		expect(tv.tv.entry).toBe('none');
	});

	test('data missing from the package: fails before changing anything', async () => {
		const tv = fakeTV({dataMissing: true});
		const res = await backport(tv).install();
		expect(res.ok).toBe(false);
		expect(res.errors[0]).toMatch(/missing from the app package/);
		expect(tv.tv.installed).toBe(false);
	});

	test('already mounted counts as a match', async () => {
		const tv = fakeTV({record: {}, match: '8.3.0'});
		tv.tv.mounted = true;
		tv.tv.release = '8.3.0';
		const res = await backport(tv, [8, 3, 0]).install();
		expect(res.ok).toBe(true);
		expect(res.status.release).toBe('8.3.0');
	});

	test('a script that does not mount is an error, with the step log', async () => {
		const tv = fakeTV({record: {}, mountWorks: false});
		const res = await backport(tv).install();
		expect(res.ok).toBe(false);
		expect(res.errors[0]).toMatch(/did not mount/);
		expect(res.steps.length).toBeGreaterThan(4);
	});

	test('a settings error other than an absent key stops before the copy', async () => {
		const tv = fakeTV({failLuna: {returnValue: false, errorText: 'Access denied'}});
		const res = await backport(tv).install();
		expect(res.ok).toBe(false);
		expect(res.errors[0]).toMatch(/Access denied/);
		expect(tv.tv.installed).toBe(false);
		const last = res.steps[res.steps.length - 1];
		expect(last).toMatchObject({kind: 'luna', ok: false});
	});
});

describe('remove', () => {
	test('delete the flag, then stop; the init.d link stays', async () => {
		const tv = fakeTV({record: {}});
		await backport(tv).install();
		tv.calls.length = 0;
		const res = await backport(tv).remove();
		expect(res.ok).toBe(true);
		const seq = kinds(tv);
		expect(seq[0]).toBe('rm ' + KEYFILTER_FLAG);
		expect(seq[1]).toBe('keyfilterStop');
		expect(seq.filter((x) => x.startsWith('rm '))).toEqual(['rm ' + KEYFILTER_FLAG]);
		expect(res.status).toMatchObject({enabled: false, entry: 'ok', mounted: false});
	});

	test('a failed stop is reported but the flag is still deleted', async () => {
		const tv = fakeTV({record: {}, stopFails: true});
		await backport(tv).install();
		const res = await backport(tv).remove();
		expect(res.ok).toBe(false);
		expect(res.errors[0]).toMatch(/umount: busy/);
		expect(tv.tv.installed).toBe(false);
	});
});
