import {ServiceError} from '../../platform/errors';
import {
	INIT_PATH, SCRIPT_PATH, backportApplies, createBackport, parseStatus
} from '..';

const RUN_INIT = `sh '${INIT_PATH}'`;
const ABSENT = {returnValue: false, errorCode: -1, errorText: 'There is no matched result from DB'};
const GET = ['luna://com.webos.settingsservice/getSystemSettings', {category: 'general', keys: ['defaultApps']}];
const SET = (defaultApps) => ['luna://com.webos.settingsservice/setSystemSettings', {category: 'general', settings: {defaultApps}}];

// A fake TV: `record` is general/defaultApps (undefined = never written); `statusOut` what the
// script's `status` prints; running the init.d copy flips `mounted`, `stop` flips it back.
const fakeTV = ({record, match = '9.2.1', dataMissing = false, mountWorks = true, stopFails = false, failLuna = null} = {}) => {
	const calls = [];
	const tv = {installed: false, mounted: false, release: null};
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
		if (command.startsWith('[ -f')) return {stdout: tv.installed ? 'yes\n' : 'no\n', stderr: ''};
		if (command.endsWith(' status')) return {stdout: statusText(), stderr: ''};
		if (command.startsWith('mkdir -p')) {
			tv.installed = true;
			return {stdout: '', stderr: ''};
		}
		if (command === RUN_INIT) {
			if (mountWorks) {
				tv.mounted = true;
				tv.release = match;
			}
			return {stdout: '', stderr: ''};
		}
		if (command.endsWith(' stop')) {
			if (stopFails) throw new ServiceError({returnValue: false, errorText: 'exit 1', stderrString: 'umount: busy'});
			tv.mounted = false;
			return {stdout: '', stderr: ''};
		}
		if (command === 'systemctl restart sam') return {stdout: '', stderr: ''};
		throw new Error('unexpected command ' + command);
	};
	const writeFile = async () => { throw new Error('unexpected writeFile'); };
	const removeFile = async (path) => {
		calls.push(['remove', path]);
		if (path === INIT_PATH) tv.installed = false;
	};
	return {rootLuna, exec, writeFile, removeFile, calls, tv, record: () => record};
};

const backport = (tv, version = [6, 6, 0]) => createBackport({
	rootLuna: tv.rootLuna, exec: tv.exec, writeFile: tv.writeFile, removeFile: tv.removeFile, version
});

const kinds = (tv) => tv.calls.map((c) => c[0] === 'luna' ? c[1].split('/').pop() : c[0] === 'exec' ? c[1] : 'rm ' + c[1]);

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
	test('reports init.d and the script status', async () => {
		const tv = fakeTV();
		tv.tv.installed = true;
		const res = await backport(tv).status();
		expect(res.ok).toBe(true);
		expect(res.status).toEqual({installed: true, dataMissing: false, mounted: false, release: null, match: '9.2.1'});
		expect(res.steps.map((s) => s.kind)).toEqual(['exec', 'exec']);
	});
});

describe('install', () => {
	test('absent key: create it, copy, run, restart sam last', async () => {
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
		expect(at('setSystemSettings')).toBeLessThan(at('mkdir -p'));
		expect(at('mkdir -p')).toBeLessThan(at(RUN_INIT));
		expect(at(RUN_INIT)).toBeLessThan(at('systemctl restart sam'));
		expect(seq.filter((x) => x === 'systemctl restart sam')).toHaveLength(1);
		expect(tv.calls.find((c) => String(c[1]).startsWith('mkdir -p'))[1]).toContain(`cp -f '${SCRIPT_PATH}' '${INIT_PATH}' && chmod 755 '${INIT_PATH}'`);
		expect(res.status).toMatchObject({installed: true, mounted: true, release: '9.2.1'});
		expect(notices).toHaveLength(2);
	});

	test('record present: no write, no sam restart', async () => {
		const tv = fakeTV({record: {home: 'x.y', kidsApp: 'k'}});
		const res = await backport(tv, [9, 2, 1]).install();
		expect(res.ok).toBe(true);
		expect(res.createdKey).toBe(false);
		expect(tv.record()).toEqual({home: 'x.y', kidsApp: 'k'});
		expect(kinds(tv)).not.toContain('setSystemSettings');
		expect(kinds(tv)).not.toContain('systemctl restart sam');
	});

	test('no matching set: fails before changing anything', async () => {
		const tv = fakeTV({match: null});
		const res = await backport(tv).install();
		expect(res.ok).toBe(false);
		expect(res.errors[0]).toMatch(/No backport set matches/);
		expect(kinds(tv)).not.toContain('setSystemSettings');
		expect(tv.tv.installed).toBe(false);
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
	test('stop first, then delete from init.d', async () => {
		const tv = fakeTV({record: {}});
		await backport(tv).install();
		tv.calls.length = 0;
		const res = await backport(tv).remove();
		expect(res.ok).toBe(true);
		const seq = kinds(tv);
		expect(seq[0]).toMatch(/ stop$/);
		expect(seq[1]).toBe('rm ' + INIT_PATH);
		expect(res.status).toMatchObject({installed: false, mounted: false});
	});

	test('a failed stop is reported but init.d is still cleaned', async () => {
		const tv = fakeTV({record: {}, stopFails: true});
		await backport(tv).install();
		const res = await backport(tv).remove();
		expect(res.ok).toBe(false);
		expect(res.errors[0]).toMatch(/umount: busy/);
		expect(tv.tv.installed).toBe(false);
	});
});
