import {createBridgeCall} from '../bridge';
import {ServiceError} from '../errors';
import {SERVICE_URI, createServiceClient, withTimeout} from '../service';
import {createExec, createOsRelease, createPersistenceStatus, createPlatform, createReadIcon, parseOsRelease} from '..';
import {parsePersistenceStatus} from '../../openhouse/persistence';

const reply = (extra) => Object.assign({returnValue: true}, extra);

// A fake bridge that answers by method name and records every call.
const bridge = (handlers) => {
	const calls = [];
	const call = jest.fn(async (uri, payload) => {
		calls.push([uri, payload]);
		const method = uri.slice(SERVICE_URI.length + 1);
		const h = handlers[method];
		if (!h) throw new Error('unexpected ' + uri);
		return typeof h === 'function' ? h(payload) : h;
	});
	return {call, calls};
};

describe('service client', () => {
	test('every method is a call to the service with its payload', async () => {
		const {call, calls} = bridge({
			status: reply({root: true, uid: 0, version: '0.1.0'}), quit: reply(), readFile: reply({exists: true, text: 'x'}),
			writeFile: reply(), removeFile: reply(), link: reply(), unlink: reply(), linkStatus: reply({entry: 'none', target: ''}),
			run: reply({exitCode: 0, stdoutString: 'o', stderrString: 'e'}), readIcon: reply({mime: 'image/png', data: 'AA=='})
		});
		const s = createServiceClient(call);
		await expect(s.status()).resolves.toMatchObject({root: true});
		await s.quit();
		await expect(s.readFile('/p')).resolves.toBe('x');
		await s.writeFile('/p', 't');
		await s.removeFile('/p');
		await s.link();
		await s.unlink();
		await expect(s.linkStatus()).resolves.toMatchObject({entry: 'none'});
		await expect(s.run('restartSam')).resolves.toEqual({stdout: 'o', stderr: 'e', exitCode: 0});
		await expect(s.readIcon('a.b')).resolves.toEqual({mime: 'image/png', data: 'AA=='});
		expect(calls).toEqual([
			[SERVICE_URI + '/status', {}], [SERVICE_URI + '/quit', {}], [SERVICE_URI + '/readFile', {path: '/p'}],
			[SERVICE_URI + '/writeFile', {path: '/p', text: 't'}], [SERVICE_URI + '/removeFile', {path: '/p'}],
			[SERVICE_URI + '/link', {}], [SERVICE_URI + '/unlink', {}], [SERVICE_URI + '/linkStatus', {}],
			[SERVICE_URI + '/run', {command: 'restartSam'}], [SERVICE_URI + '/readIcon', {appId: 'a.b'}]
		]);
	});

	test('readFile resolves null for a file that is not there', async () => {
		const s = createServiceClient(bridge({readFile: reply({exists: false})}).call);
		await expect(s.readFile('/p')).resolves.toBeNull();
	});

	test('a false returnValue is a ServiceError with the reply', async () => {
		const r = {returnValue: false, errorCode: 'notRoot', errorText: 'not root'};
		const s = createServiceClient(bridge({writeFile: r, status: r}).call);
		await expect(s.writeFile('/p', '')).rejects.toBeInstanceOf(ServiceError);
		await expect(s.writeFile('/p', '')).rejects.toMatchObject({reply: r, message: 'not root'});
	});

	describe('luna', () => {
		test('forwards uri and payload and resolves the inner reply', async () => {
			const {call, calls} = bridge({call: reply({reply: {returnValue: true, apps: [1]}})});
			const s = createServiceClient(call);
			await expect(s.luna('luna://com.webos.applicationManager/listApps', {properties: ['id']})).resolves.toEqual({returnValue: true, apps: [1]});
			expect(calls).toEqual([[SERVICE_URI + '/call', {uri: 'luna://com.webos.applicationManager/listApps', payload: {properties: ['id']}}]]);
		});
		test('an inner returnValue false is a ServiceError carrying the inner reply', async () => {
			const inner = {returnValue: false, errorCode: -1, errorText: 'There is no matched result from DB'};
			const s = createServiceClient(bridge({call: reply({reply: inner})}).call);
			await expect(s.luna('luna://x/y')).rejects.toMatchObject({reply: inner, message: 'There is no matched result from DB'});
		});
		test('a refusal by the service is a ServiceError with its reply', async () => {
			const r = {returnValue: false, errorCode: 'notAllowed', errorText: 'luna://x/y is not on the list'};
			const s = createServiceClient(bridge({call: r}).call);
			await expect(s.luna('luna://x/y', {})).rejects.toMatchObject({reply: r});
		});
		test('a missing payload becomes {}', async () => {
			const {call, calls} = bridge({call: reply({reply: {returnValue: true}})});
			await createServiceClient(call).luna('luna://x/y');
			expect(calls[0][1].payload).toEqual({});
		});
		test('a reply without an object is an error', async () => {
			const s = createServiceClient(bridge({call: reply()}).call);
			await expect(s.luna('luna://x/y')).rejects.toBeInstanceOf(ServiceError);
		});
	});

	test('a call that never answers times out', async () => {
		const s = createServiceClient(() => new Promise(() => {}), {timeoutMs: 20});
		await expect(s.status()).rejects.toThrow(/timed out/);
	});

	test('withTimeout passes values and errors through', async () => {
		await expect(withTimeout(Promise.resolve(3), 50, 'x')).resolves.toBe(3);
		await expect(withTimeout(Promise.reject(new Error('boom')), 50, 'x')).rejects.toThrow('boom');
	});
});

describe('exec', () => {
	test('a command name runs that command', async () => {
		const run = jest.fn().mockResolvedValue({stdout: 'out', stderr: '', exitCode: 0});
		await expect(createExec({run})('keyfilterApply')).resolves.toMatchObject({stdout: 'out'});
		expect(run).toHaveBeenCalledWith('keyfilterApply');
	});
	test('linkEntry and unlinkEntry use the link methods', async () => {
		const service = {link: jest.fn().mockResolvedValue({}), unlink: jest.fn().mockResolvedValue({}), run: jest.fn()};
		const exec = createExec(service);
		await expect(exec('linkEntry')).resolves.toEqual({stdout: '', stderr: ''});
		await exec('unlinkEntry');
		expect(service.link).toHaveBeenCalledTimes(1);
		expect(service.unlink).toHaveBeenCalledTimes(1);
		expect(service.run).not.toHaveBeenCalled();
	});
});

describe('persistence status', () => {
	const service = (over) => Object.assign({
		linkStatus: async () => ({entry: 'link', target: '/app/backport/openhouse'}),
		run: async (c) => ({stdout: c === 'watcherActive' ? 'active\n' : 'line one\nline two\n', stderr: ''}),
		readFile: async (p) => (p.endsWith('/home') ? 'my.home\n' : '')
	}, over);

	test('assembles the text parsePersistenceStatus reads', async () => {
		const {stdout} = await createPersistenceStatus(service())();
		expect(parsePersistenceStatus(stdout)).toMatchObject({
			entry: 'other', entryTarget: '/app/backport/openhouse', active: 'active', stored: 'my.home', keyfilterEnabled: true, log: ['line one', 'line two']
		});
	});
	test('no files, no entry', async () => {
		const {stdout} = await createPersistenceStatus(service({
			linkStatus: async () => ({entry: 'none', target: ''}), readFile: async () => null, run: async () => ({stdout: '', stderr: ''})
		}))();
		expect(parsePersistenceStatus(stdout)).toMatchObject({entry: 'none', active: '', stored: '', keyfilterEnabled: false, log: []});
	});
});

describe('os-release', () => {
	test('parses VERSION_ID', () => {
		expect(parseOsRelease('NAME="webOS"\nVERSION_ID="6.6.0"\n').version).toEqual([6, 6, 0]);
	});
	test('falls back to VERSION and PRETTY_NAME', () => {
		expect(parseOsRelease('VERSION="9.2.1 (x)"\n').source).toBe('VERSION');
		expect(parseOsRelease('PRETTY_NAME="webOS TV 11.2.0"\n').version).toEqual([11, 2, 0]);
	});
	test('pads short versions', () => {
		expect(parseOsRelease('VERSION_ID=6.5\n').version).toEqual([6, 5, 0]);
	});
	test('osRelease reads the file through the service, and rejects without a version', async () => {
		const readFile = jest.fn().mockResolvedValueOnce('VERSION_ID="8.3.0"\n').mockResolvedValueOnce('NAME=x\n').mockResolvedValueOnce(null);
		const osRelease = createOsRelease({readFile});
		await expect(osRelease()).resolves.toMatchObject({version: [8, 3, 0]});
		expect(readFile).toHaveBeenCalledWith('/etc/os-release');
		await expect(osRelease()).rejects.toThrow(/No version/);
		await expect(osRelease()).rejects.toThrow(/No version/);
	});
});

describe('bridge', () => {
	test('resolves the parsed reply and sends JSON', async () => {
		let sent;
		class Bridge {
			call(uri, payload) { sent = [uri, payload]; setTimeout(() => this.onservicecallback('{"returnValue":true}'), 0); }
			cancel() {}
		}
		await expect(createBridgeCall(Bridge)('luna://x/y', {a: 1})).resolves.toEqual({returnValue: true});
		expect(sent).toEqual(['luna://x/y', '{"a":1}']);
	});
	test('rejects without a bridge', async () => {
		await expect(createBridgeCall(null)('luna://x/y', {})).rejects.toThrow(/No webOS service bridge/);
	});
});

describe('icons', () => {
	test('returns a data URL, caches, and falls back to null', async () => {
		const readIcon = jest.fn().mockResolvedValue({mime: 'image/png', data: 'AAEC'});
		const read = createReadIcon({readIcon});
		await expect(read('a.b', '/x/icon.png')).resolves.toBe('data:image/png;base64,AAEC');
		await read('a.b', '/x/icon.png');
		expect(readIcon).toHaveBeenCalledTimes(1);
		expect(readIcon).toHaveBeenCalledWith('a.b');
		await expect(read('a.b', '/x/icon.txt')).resolves.toBeNull();
		await expect(read('a.b', null)).resolves.toBeNull();
		await expect(createReadIcon({readIcon: jest.fn().mockRejectedValue(new Error('x'))})('a', 'i.png')).resolves.toBeNull();
		await expect(createReadIcon({readIcon: jest.fn().mockResolvedValue({mime: 'image/png', data: 'not base64!'})})('a', 'i.png')).resolves.toBeNull();
	});
});

describe('createPlatform', () => {
	test('wires the logic layer to the service', async () => {
		const {call, calls} = bridge({call: reply({reply: {returnValue: true}}), writeFile: reply(), run: reply({exitCode: 0})});
		const p = createPlatform(call);
		await p.rootLuna('luna://x/y', {});
		await p.writeFile('/p', 't');
		await p.exec('restartSam');
		expect(calls.map((c) => c[0].slice(SERVICE_URI.length))).toEqual(['/call', '/writeFile', '/run']);
		expect(typeof p.ensureRoot).toBe('function');
	});
});
