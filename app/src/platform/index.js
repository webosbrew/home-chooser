import {ServiceError, errorMessage} from './errors';
import {shellQuote, shellWord, toBase64} from './quote';
import {createBridgeCall} from './bridge';

export const HBCHANNEL_EXEC = 'luna://org.webosbrew.hbchannel.service/exec';

const dirname = (path) => {
	const i = path.lastIndexOf('/');
	return i > 0 ? path.slice(0, i) : '/';
};

// exec(command) -> {stdout, stderr}. Rejects with a ServiceError (reply in `.reply`) when
// returnValue is false, e.g. `Denied method call "exec" ...` or a non-zero exit status.
export const createExec = (call = createBridgeCall()) => (command) =>
	call(HBCHANNEL_EXEC, {command}).then((reply) => {
		if (!reply || reply.returnValue === false) throw new ServiceError(reply);
		return {stdout: reply.stdoutString || '', stderr: reply.stderrString || ''};
	});

export const buildLunaSend = (uri, payload) =>
	'luna-send -n 1 -f ' + shellWord(uri) + ' ' + shellQuote(JSON.stringify(payload === undefined ? {} : payload));

// rootLuna(uri, payload) -> parsed reply. Runs luna-send as root through exec.
const parseLunaReply = (text) => {
	try {
		const v = JSON.parse(String(text).trim());
		return v && typeof v === 'object' && !Array.isArray(v) ? v : null;
	} catch (e) {
		return null;
	}
};

// rootLuna(uri, payload) -> parsed reply. Runs luna-send as root through exec. When exec itself
// fails (a non-zero exit of luna-send), a luna reply in its stdoutString is the error to report.
export const createRootLuna = (exec) => (uri, payload) =>
	exec(buildLunaSend(uri, payload)).then(({stdout, stderr}) => {
		const reply = parseLunaReply(stdout);
		if (!reply) {
			throw new ServiceError({returnValue: false, errorText: 'luna-send printed no JSON', stderrString: stderr || stdout});
		}
		if (reply.returnValue === false) throw new ServiceError(reply);
		return reply;
	}, (e) => {
		const reply = e && e.reply && parseLunaReply(e.reply.stdoutString || '');
		if (reply) {
			if (reply.returnValue === false) throw new ServiceError(reply);
			return reply;
		}
		throw e;
	});

export const buildWriteFile = (path, text) =>
	'mkdir -p ' + shellQuote(dirname(path)) + ' && printf %s ' + shellQuote(toBase64(text)) + ' | base64 -d > ' + shellQuote(path);

export const buildRemoveFile = (path) => 'rm -f ' + shellQuote(path);

export const createWriteFile = (exec) => (path, text) => exec(buildWriteFile(path, text));
export const createRemoveFile = (exec) => (path) => exec(buildRemoveFile(path));

// Parse /etc/os-release. VERSION_ID, then VERSION, then PRETTY_NAME, whichever has dotted digits.
export const parseOsRelease = (text) => {
	const fields = {};
	text.split('\n').forEach((line) => {
		const m = /^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$/.exec(line);
		if (!m) return;
		let v = m[2].trim();
		if ((v[0] === '"' && v[v.length - 1] === '"') || (v[0] === "'" && v[v.length - 1] === "'")) v = v.slice(1, -1);
		fields[m[1]] = v;
	});
	for (const key of ['VERSION_ID', 'VERSION', 'PRETTY_NAME']) {
		const m = fields[key] && /(\d+)(?:\.(\d+))?(?:\.(\d+))?/.exec(fields[key]);
		if (m) return {version: [Number(m[1]), Number(m[2] || 0), Number(m[3] || 0)], source: key, fields};
	}
	return {version: null, source: null, fields};
};

export const createOsRelease = (exec) => () =>
	exec('cat /etc/os-release').then(({stdout}) => {
		const parsed = parseOsRelease(stdout);
		if (!parsed.version) throw new Error('No version in /etc/os-release');
		return parsed;
	});

export const createPlatform = (call) => {
	const exec = createExec(call);
	return {
		exec,
		rootLuna: createRootLuna(exec),
		writeFile: createWriteFile(exec),
		removeFile: createRemoveFile(exec),
		osRelease: createOsRelease(exec)
	};
};

export {ServiceError, errorMessage, shellQuote, shellWord, toBase64, createBridgeCall};
