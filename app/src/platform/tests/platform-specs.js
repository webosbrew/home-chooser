/* eslint-env node */
import {
	HBCHANNEL_EXEC, buildLunaSend, buildRemoveFile, buildWriteFile, createExec, createOsRelease, createRootLuna,
	createReadIcon, createRootUid, createWriteFile, iconMimeType, buildReadIcon, parseOsRelease, ICON_MAX_BYTES
} from '..';
import {shellQuote, shellWord, toBase64} from '../quote';
import {createBridgeCall} from '../bridge';
import {ServiceError} from '../errors';
import {execFileSync} from 'child_process';

describe('quoting', () => {
	test('shellQuote wraps in single quotes and escapes embedded ones', () => {
		expect(shellQuote('abc')).toBe("'abc'");
		expect(shellQuote("it's")).toBe("'it'\\''s'");
		expect(shellQuote("''")).toBe("''\\'''\\'''");
		expect(shellQuote('')).toBe("''");
	});

	test('round-trips hostile text through a real shell', () => {
		const text = "a'b\"c $HOME `id` \\ \n; rm -rf / {\"k\":\"it's\"}";
		const out = execFileSync('/bin/sh', ['-c', 'printf %s ' + shellQuote(text)]).toString();
		expect(out).toBe(text);
	});

	test('shellWord leaves plain URIs bare and quotes the rest', () => {
		expect(shellWord('luna://com.webos.applicationManager/listApps')).toBe('luna://com.webos.applicationManager/listApps');
		expect(shellWord('a b')).toBe("'a b'");
		expect(shellWord('a;b')).toBe("'a;b'");
	});

	test('toBase64 is UTF-8 safe', () => {
		expect(toBase64('{"a":"é"}')).toBe(Buffer.from('{"a":"é"}', 'utf8').toString('base64'));
	});
});

describe('buildLunaSend', () => {
	test('builds the luna-send command', () => {
		expect(buildLunaSend('luna://com.webos.applicationManager/listApps', {})).toBe(
			"luna-send -n 1 -f luna://com.webos.applicationManager/listApps '{}'"
		);
	});
	test('escapes single quotes in the payload', () => {
		expect(buildLunaSend('luna://x/y', {name: "it's"})).toBe("luna-send -n 1 -f luna://x/y '{\"name\":\"it'\\''s\"}'");
	});
	test('the payload survives a real shell', () => {
		const payload = {a: "it's", b: '$(id)', c: '`x`'};
		const cmd = buildLunaSend('luna://x/y', payload).replace(/^luna-send -n 1 -f \S+ /, 'printf %s ');
		expect(JSON.parse(execFileSync('/bin/sh', ['-c', cmd]).toString())).toEqual(payload);
	});
});

describe('exec', () => {
	test('calls hbchannel exec and resolves stdout and stderr', async () => {
		const call = jest.fn().mockResolvedValue({returnValue: true, stdoutString: 'out', stderrString: 'err'});
		await expect(createExec(call)('id')).resolves.toEqual({stdout: 'out', stderr: 'err'});
		expect(call).toHaveBeenCalledWith(HBCHANNEL_EXEC, {command: 'id'});
	});

	test('rejects with the reply text on returnValue false', async () => {
		const reply = {returnValue: false, errorCode: -1, errorText: 'Denied method call "exec" for category "/"'};
		const err = await createExec(jest.fn().mockResolvedValue(reply))('id').catch((e) => e);
		expect(err.message).toBe('Denied method call "exec" for category "/"');
		expect(err.reply).toEqual(reply);
	});

	test('a failed command carries its stderr', async () => {
		const reply = {returnValue: false, errorText: 'Command failed', stdoutString: '', stderrString: 'boom\n'};
		const err = await createExec(jest.fn().mockResolvedValue(reply))('false').catch((e) => e);
		expect(err.message).toBe('Command failed: boom');
	});
});

describe('rootLuna', () => {
	const execReturning = (stdout) => jest.fn().mockResolvedValue({stdout, stderr: ''});

	test('runs luna-send and parses the JSON', async () => {
		const exec = execReturning('{"returnValue":true,"apps":[]}\n');
		await expect(createRootLuna(exec)('luna://a/b', {x: 1})).resolves.toEqual({returnValue: true, apps: []});
		expect(exec).toHaveBeenCalledWith("luna-send -n 1 -f luna://a/b '{\"x\":1}'");
	});

	test('rejects on returnValue false with the reply', async () => {
		const reply = {returnValue: false, errorCode: 1, errorText: 'no default app is set for the category'};
		const err = await createRootLuna(execReturning(JSON.stringify(reply)))('luna://a/b', {}).catch((e) => e);
		expect(err.message).toBe('no default app is set for the category');
		expect(err.reply).toEqual(reply);
	});

	test('rejects when luna-send prints no JSON', async () => {
		await expect(createRootLuna(execReturning('Error: oops'))('luna://a/b', {})).rejects.toThrow('luna-send printed no JSON');
	});

	test('non-zero exit with a luna reply on stdout rejects with the luna reply', async () => {
		const luna = {returnValue: false, errorText: 'Unknown method "setDefaultApp" for category "/"'};
		const execFailure = new ServiceError({returnValue: false, errorText: 'Command failed: luna-send ...', stdoutString: JSON.stringify(luna) + '\n', stderrString: ''});
		const err = await createRootLuna(jest.fn().mockRejectedValue(execFailure))('luna://a/b', {}).catch((e) => e);
		expect(err.message).toBe('Unknown method "setDefaultApp" for category "/"');
		expect(err.reply).toEqual(luna);
	});

	test('non-zero exit without a luna reply keeps the exec error', async () => {
		const execFailure = new ServiceError({returnValue: false, errorText: 'Command failed: luna-send', stdoutString: '', stderrString: 'luna-send: not found'});
		const err = await createRootLuna(jest.fn().mockRejectedValue(execFailure))('luna://a/b', {}).catch((e) => e);
		expect(err).toBe(execFailure);
	});

	test('propagates an exec failure unchanged', async () => {
		const exec = jest.fn().mockRejectedValue(new Error('Denied method call "exec"'));
		await expect(createRootLuna(exec)('luna://a/b', {})).rejects.toThrow('Denied method call "exec"');
	});
});

describe('files', () => {
	test('writeFile pipes base64 through base64 -d, and the result matches', () => {
		const text = JSON.stringify({defaultApps: {categories: {home: "x'y"}}}, null, 2);
		const cmd = buildWriteFile('/tmp/openhouse-spec-out.json', text);
		expect(cmd).toMatch(/^mkdir -p '\/tmp' && printf %s '[A-Za-z0-9+/=]+' \| base64 -d > '\/tmp\/openhouse-spec-out.json'$/);
		execFileSync('/bin/sh', ['-c', cmd]);
		expect(require('fs').readFileSync('/tmp/openhouse-spec-out.json', 'utf8')).toBe(text);
		execFileSync('/bin/sh', ['-c', buildRemoveFile('/tmp/openhouse-spec-out.json')]);
		expect(require('fs').existsSync('/tmp/openhouse-spec-out.json')).toBe(false);
	});

	test('removeFile quotes the path', () => {
		expect(buildRemoveFile("/var/a b/it's.json")).toBe("rm -f '/var/a b/it'\\''s.json'");
	});

	test('writeFile goes through exec', async () => {
		const exec = jest.fn().mockResolvedValue({stdout: '', stderr: ''});
		await createWriteFile(exec)('/var/x/y.json', '{}');
		expect(exec).toHaveBeenCalledWith(buildWriteFile('/var/x/y.json', '{}'));
	});
});

describe('os-release', () => {
	test('parses VERSION_ID', () => {
		expect(parseOsRelease('NAME="webOS TV"\nVERSION_ID="6.6.0"\n').version).toEqual([6, 6, 0]);
	});
	test('falls back to VERSION and PRETTY_NAME', () => {
		expect(parseOsRelease("VERSION='5.6.1-1'\n").version).toEqual([5, 6, 1]);
		expect(parseOsRelease('PRETTY_NAME="webOS TV 11.2.0"\n').version).toEqual([11, 2, 0]);
	});
	test('pads short versions and handles 6.5.2', () => {
		expect(parseOsRelease('VERSION_ID=10.3\n').version).toEqual([10, 3, 0]);
		expect(parseOsRelease('VERSION_ID=6.5.2\n').version).toEqual([6, 5, 2]);
	});
	test('no version yields null, and osRelease rejects', async () => {
		expect(parseOsRelease('NAME=x\n').version).toBeNull();
		const exec = jest.fn().mockResolvedValue({stdout: 'NAME=x\n', stderr: ''});
		await expect(createOsRelease(exec)()).rejects.toThrow('No version');
	});
	test('osRelease cats the file', async () => {
		const exec = jest.fn().mockResolvedValue({stdout: 'VERSION_ID="8.3.0"\n', stderr: ''});
		expect((await createOsRelease(exec)()).version).toEqual([8, 3, 0]);
		expect(exec).toHaveBeenCalledWith('cat /etc/os-release');
	});
});

describe('bridge', () => {
	test('resolves the parsed reply and sends JSON', async () => {
		let sent;
		class Fake {
			call(uri, json) {
				sent = [uri, json];
				setTimeout(() => this.onservicecallback('{"returnValue":false,"errorText":"x"}'), 0);
			}
		}
		await expect(createBridgeCall(Fake)('luna://a/b', {k: 1})).resolves.toEqual({returnValue: false, errorText: 'x'});
		expect(sent).toEqual(['luna://a/b', '{"k":1}']);
	});
	test('rejects without a bridge', async () => {
		await expect(createBridgeCall(null)('luna://a/b', {})).rejects.toThrow('No webOS service bridge');
	});
});

describe('icons and root check', () => {
	test('mime type comes from the extension', () => {
		expect(iconMimeType('/a/b.PNG')).toBe('image/png');
		expect(iconMimeType('/a/b.svg')).toBe('image/svg+xml');
		expect(iconMimeType('/a/b.bmp')).toBeNull();
		expect(iconMimeType(null)).toBeNull();
	});
	test('the command reads a small file as base64 and refuses a big one', () => {
		const dir = require('fs').mkdtempSync(require('path').join(require('os').tmpdir(), 'oh-icon-'));
		const small = dir + "/it's.png";
		require('fs').writeFileSync(small, Buffer.from([1, 2, 3, 250]));
		const big = dir + '/big.png';
		require('fs').writeFileSync(big, Buffer.alloc(ICON_MAX_BYTES + 1));
		const run = (p) => {
			try {
				return execFileSync('sh', ['-c', buildReadIcon(p)], {encoding: 'utf8'});
			} catch (e) {
				return 'FAIL';
			}
		};
		expect(run(small)).toBe(Buffer.from([1, 2, 3, 250]).toString('base64'));
		expect(run(big)).toBe('FAIL');
		expect(run(dir + '/none.png')).toBe('FAIL');
		require('fs').rmSync(dir, {recursive: true});
	});
	test('readIcon returns a data URL, caches, and falls back to null', async () => {
		const exec = jest.fn((cmd) => (cmd.includes('good') ? Promise.resolve({stdout: 'AQID+g==\n', stderr: ''}) : Promise.reject(new Error('exit 1'))));
		const read = createReadIcon(exec);
		expect(await read('/x/good.png')).toBe('data:image/png;base64,AQID+g==');
		expect(await read('/x/good.png')).toBe('data:image/png;base64,AQID+g==');
		expect(exec).toHaveBeenCalledTimes(1);
		expect(await read('/x/bad.png')).toBeNull();
		expect(await read('/x/bad.png')).toBeNull();
		expect(exec).toHaveBeenCalledTimes(2);
		expect(await read('/x/icon.bmp')).toBeNull();
		expect(await read(null)).toBeNull();
		expect(exec).toHaveBeenCalledTimes(2);
	});
	test('garbage output is not a data URL', async () => {
		const read = createReadIcon(jest.fn().mockResolvedValue({stdout: '<html>', stderr: ''}));
		expect(await read('/x/a.png')).toBeNull();
	});
	test('rootUid parses id -u', async () => {
		expect(await createRootUid(jest.fn().mockResolvedValue({stdout: '0\n', stderr: ''}))()).toBe(0);
		expect(await createRootUid(jest.fn().mockResolvedValue({stdout: '1000\n', stderr: ''}))()).toBe(1000);
		await expect(createRootUid(jest.fn().mockResolvedValue({stdout: 'x', stderr: ''}))()).rejects.toThrow('Unexpected');
	});
});
