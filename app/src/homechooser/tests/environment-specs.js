// SPDX-License-Identifier: Apache-2.0
import {FatalError, loadEnvironment} from '../environment';
import {classifyExecFailure} from '../diagnose';

const platform = (over) => Object.assign({
	osRelease: jest.fn().mockResolvedValue({version: [8, 3, 0]}),
	ensureRoot: jest.fn().mockResolvedValue({root: true}),
	rootLuna: jest.fn(), exec: jest.fn(), writeFile: jest.fn(), removeFile: jest.fn(), readIcon: jest.fn()
}, over);

describe('classifyExecFailure', () => {
	test('kinds', () => {
		expect(classifyExecFailure('Denied method call "elevateService"')).toBe('denied');
		expect(classifyExecFailure('Service does not exist: org.webosbrew.hbchannel.service')).toBe('unreachable');
		expect(classifyExecFailure('No webOS service bridge')).toBe('unreachable');
		expect(classifyExecFailure('whatever')).toBe('failed');
	});
});

describe('loadEnvironment', () => {
	test('builds a chooser, and a homeKeyHook only on webOS 6 to 9', async () => {
		const a = await loadEnvironment(platform());
		expect(a.version).toEqual([8, 3, 0]);
		expect(a.homeKeyHook).not.toBeNull();
		expect(typeof a.chooser.apply).toBe('function');
		const b = await loadEnvironment(platform({osRelease: jest.fn().mockResolvedValue({version: [11, 0, 0]})}));
		expect(b.homeKeyHook).toBeNull();
	});
	test('the restart prompt passes through from the platform; the app has no way to confirm it', async () => {
		const p = platform({rebootPrompt: jest.fn(), rebootConfirmed: jest.fn()});
		const env = await loadEnvironment(p);
		expect(env.rebootPrompt).toBe(p.rebootPrompt);
		expect(env.rebootConfirmed).toBeUndefined();
	});
	test('access setup follows elevation and failures leave the panel usable', async () => {
		const p = platform();
		await loadEnvironment(p);
		expect(p.exec).toHaveBeenCalledWith('accessApply');
		expect(p.exec.mock.invocationCallOrder[0]).toBeGreaterThan(p.ensureRoot.mock.invocationCallOrder[0]);
		const log = jest.spyOn(console, 'log').mockImplementation(() => {});
		try {
			await expect(loadEnvironment(platform({exec: jest.fn().mockRejectedValue(new Error('scan failed'))}))).resolves.toHaveProperty('chooser');
		} finally {
			log.mockRestore();
		}
	});
	test('an access change on 11+ restarts the service before the watcher starts; otherwise no restart', async () => {
		const restartService = jest.fn().mockResolvedValue({root: true});
		const exec = jest.fn((name) => Promise.resolve({stdout: name === 'accessApply' ? 'restart-service\n' : '', stderr: ''}));
		await loadEnvironment(platform({exec, restartService}));
		expect(restartService).toHaveBeenCalledTimes(1);
		expect(restartService.mock.invocationCallOrder[0]).toBeLessThan(exec.mock.invocationCallOrder[1]);
		const quiet = jest.fn();
		await loadEnvironment(platform({exec: jest.fn().mockResolvedValue({stdout: '', stderr: ''}), restartService: quiet}));
		expect(quiet).not.toHaveBeenCalled();
	});
	test('the watcher is started after access setup, for its leftover check, and a failure is not fatal', async () => {
		const p = platform();
		await loadEnvironment(p);
		expect(p.exec.mock.calls.map((c) => c[0])).toEqual(['accessApply', 'watch']);
		const log = jest.spyOn(console, 'log').mockImplementation(() => {});
		try {
			const exec = jest.fn((name) => (name === 'watch' ? Promise.reject(new Error('systemd-run failed')) : Promise.resolve({})));
			await expect(loadEnvironment(platform({exec}))).resolves.toHaveProperty('chooser');
			expect(exec).toHaveBeenCalledWith('watch');
		} finally {
			log.mockRestore();
		}
	});
	test('elevation errors pass through as fatal kinds', async () => {
		const failing = (kind) => platform({ensureRoot: jest.fn().mockRejectedValue(new FatalError(kind, 'x'))});
		for (const kind of ['denied', 'unreachable', 'notRoot', 'failed']) {
			await expect(loadEnvironment(failing(kind))).rejects.toMatchObject({kind});
		}
		const p = platform({ensureRoot: jest.fn().mockRejectedValue(new FatalError('notRoot', 'x'))});
		await expect(loadEnvironment(p)).rejects.toBeInstanceOf(FatalError);
		expect(p.osRelease).not.toHaveBeenCalled();
	});
	test('an unreadable release is a fatal kind', async () => {
		await expect(loadEnvironment(platform({osRelease: jest.fn().mockRejectedValue(new Error('Service does not exist'))}))).rejects.toMatchObject({kind: 'unreachable'});
		await expect(loadEnvironment(platform({osRelease: jest.fn().mockRejectedValue(new Error('No version'))}))).rejects.toMatchObject({kind: 'failed'});
	});
});

// The page can be closed at any point of the first run (the compositor closes an overlay after 120 s
// without a key press). A fake device keeps what the first run left; the second run must finish.
describe('an interrupted first run', () => {
	// eslint-disable-next-line global-require
	const {ensureRoot} = require('../../platform/elevate');
	const makeDevice = () => {
		const dev = {root: false, elevated: false, accessFiles: 0, watcher: false};
		const call = jest.fn(async (uri) => {
			if (uri.endsWith('/elevateService')) {
				dev.elevated = true; // Homebrew Channel rewrites the launch line; the running instance is unchanged
				return {returnValue: true};
			}
			throw new Error(`unexpected ${uri}`);
		});
		const service = {
			status: jest.fn(async () => ({returnValue: true, root: dev.root, uid: dev.root ? 0 : 1000, version: '0.1.2'})),
			quit: jest.fn(async () => { dev.root = dev.elevated; })
		};
		const platformFor = (hook) => ({
			ensureRoot: () => ensureRoot({service, call, sleep: async () => {}, log: () => {}}),
			osRelease: async () => ({version: [8, 3, 0]}),
			exec: async (name) => {
				await hook(name);
				if (name === 'accessApply') dev.accessFiles = 2;
				if (name === 'watch') dev.watcher = true;
				return {stdout: '', stderr: ''};
			},
			rootLuna: jest.fn(), writeFile: jest.fn(), removeFile: jest.fn(), readIcon: jest.fn()
		});
		return {dev, service, platform: platformFor};
	};
	const closed = new Error('page closed');

	test.each([
		['before elevation', 'status'],
		['after elevation, before the service quit', 'quit'],
		['after access setup, before the watcher starts', 'watch']
	])('closed %s: the next open finishes without a manual step', async (_, at) => {
		const d = makeDevice();
		let abort = () => {};
		if (at === 'status') d.service.status.mockRejectedValueOnce(closed);
		if (at === 'quit') d.service.quit.mockRejectedValueOnce(closed);
		if (at === 'watch') abort = (name) => { if (name === 'watch') throw closed; };
		const log = jest.spyOn(console, 'log').mockImplementation(() => {});
		try {
			// The first run may fail or stop anywhere: only what it left on the device matters.
			await loadEnvironment(d.platform(abort)).catch(() => {});
			const second = await loadEnvironment(d.platform(() => {}));
			expect(typeof second.chooser.apply).toBe('function');
		} finally {
			log.mockRestore();
		}
		expect(d.dev.root).toBe(true);
		expect(d.dev.accessFiles).toBe(2);
		expect(d.dev.watcher).toBe(true);
	});
});
