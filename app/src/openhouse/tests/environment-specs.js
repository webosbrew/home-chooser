import {FatalError, loadEnvironment} from '../environment';
import {classifyExecFailure} from '../diagnose';

const platform = (over) => Object.assign({
	osRelease: jest.fn().mockResolvedValue({version: [8, 3, 0]}),
	rootUid: jest.fn().mockResolvedValue(0),
	rootLuna: jest.fn(), exec: jest.fn(), writeFile: jest.fn(), removeFile: jest.fn(), readIcon: jest.fn()
}, over);

describe('classifyExecFailure', () => {
	test('kinds', () => {
		expect(classifyExecFailure('Denied method call "exec"')).toBe('denied');
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
	test('exec refused, unreachable and not root become fatal kinds', async () => {
		await expect(loadEnvironment(platform({osRelease: jest.fn().mockRejectedValue(new Error('Denied method call "exec"'))}))).rejects.toMatchObject({kind: 'denied'});
		await expect(loadEnvironment(platform({osRelease: jest.fn().mockRejectedValue(new Error('Service does not exist'))}))).rejects.toMatchObject({kind: 'unreachable'});
		await expect(loadEnvironment(platform({rootUid: jest.fn().mockResolvedValue(1000)}))).rejects.toBeInstanceOf(FatalError);
		await expect(loadEnvironment(platform({rootUid: jest.fn().mockResolvedValue(1000)}))).rejects.toMatchObject({kind: 'notRoot'});
	});
});
