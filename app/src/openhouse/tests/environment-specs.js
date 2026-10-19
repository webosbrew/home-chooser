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
	test('builds a chooser, and a backport only on webOS 6 to 9', async () => {
		const a = await loadEnvironment(platform());
		expect(a.version).toEqual([8, 3, 0]);
		expect(a.backport).not.toBeNull();
		expect(typeof a.chooser.apply).toBe('function');
		const b = await loadEnvironment(platform({osRelease: jest.fn().mockResolvedValue({version: [11, 0, 0]})}));
		expect(b.backport).toBeNull();
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
