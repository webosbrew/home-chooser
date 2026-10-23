// Client for Home Chooser's native root service, org.webosbrew.homechooser.service (docs/service.md).
// Every method answers {returnValue, ...}; a false returnValue becomes a ServiceError whose
// `reply` is the service reply (errorCode: forbidden, notRoot, notAllowed, ...).
import {ServiceError} from './errors';

export const SERVICE_ID = 'org.webosbrew.homechooser.service';
export const SERVICE_URI = `luna://${SERVICE_ID}`;

export const CALL_TIMEOUT_MS = 30000;
// Commands that restart the compositor or sam can take a while.
export const RUN_TIMEOUT_MS = 120000;

export const withTimeout = (promise, ms, label) =>
	new Promise((resolve, reject) => {
		const timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms} ms`)), ms);
		promise.then((v) => { clearTimeout(timer); resolve(v); }, (e) => { clearTimeout(timer); reject(e); });
	});

const ok = (reply) => {
	if (!reply || reply.returnValue !== true) throw new ServiceError(reply);
	return reply;
};

export const createServiceClient = (call, {timeoutMs = CALL_TIMEOUT_MS, runTimeoutMs = RUN_TIMEOUT_MS} = {}) => {
	const invoke = (method, payload, ms = timeoutMs) =>
		withTimeout(call(`${SERVICE_URI}/${method}`, payload || {}), ms, `${SERVICE_ID}/${method}`);
	return {
		invoke,
		// {root, uid, version}; works while the service is not root
		status: (ms) => invoke('status', {}, ms).then(ok),
		quit: (ms) => invoke('quit', {}, ms),
		// A luna call from the service's allow-list; resolves with the called service's reply and
		// rejects (ServiceError carrying that reply) when it says returnValue false.
		luna: (uri, payload) => invoke('call', {uri, payload: payload === undefined ? {} : payload}).then((r) => {
			ok(r);
			const inner = r.reply;
			if (!inner || typeof inner !== 'object') throw new ServiceError({returnValue: false, errorText: `No reply object from ${uri}`});
			if (inner.returnValue === false) throw new ServiceError(inner);
			return inner;
		}),
		// text, or null when the file does not exist
		readFile: (path) => invoke('readFile', {path}).then(ok).then((r) => (r.exists ? r.text : null)),
		writeFile: (path, text) => invoke('writeFile', {path, text}).then(ok),
		removeFile: (path) => invoke('removeFile', {path}).then(ok),
		readIcon: (appId) => invoke('readIcon', {appId}).then(ok).then((r) => ({mime: r.mime, data: r.data})),
		uninstallConfirmed: (token) => invoke('uninstallConfirmed', {token}, runTimeoutMs).then(ok),
		link: () => invoke('link').then(ok),
		unlink: () => invoke('unlink').then(ok),
		linkStatus: () => invoke('linkStatus').then(ok),
		// A named command; resolves {stdout, stderr, exitCode}
		run: (command) => invoke('run', {command}, runTimeoutMs).then(ok).then((r) => ({
			stdout: r.stdoutString || '', stderr: r.stderrString || '', exitCode: r.exitCode || 0
		}))
	};
};
