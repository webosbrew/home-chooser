// Startup elevation: make sure Open House's service runs as root.
//   status -> if not root: Homebrew Channel elevateService -> quit -> status again.
// Failures are FatalErrors whose `kind` the screen maps to a message:
//   'unreachable' Homebrew Channel is not installed / not running
//   'denied'      Homebrew Channel refuses the call from an app (webOS 26 and later)
//   'notRoot'     the service answered after elevation but is still not root
//   'failed'      anything else
import {classifyExecFailure} from '../openhouse/diagnose';
import {errorMessage} from './errors';
import {SERVICE_ID, withTimeout} from './service';

export const HBC_ELEVATE = 'luna://org.webosbrew.hbchannel.service/elevateService';

export class FatalError extends Error {
	constructor(kind, detail) {
		super(detail);
		this.name = 'FatalError';
		this.kind = kind; // 'denied' | 'unreachable' | 'notRoot' | 'failed'
		this.detail = detail;
	}
}

const defaultLog = (text) => console.log(`[OpenHouse] ${text}`); // eslint-disable-line no-console

export const ELEVATION = {
	statusTimeoutMs: 5000,
	elevateTimeoutMs: 20000,
	quitTimeoutMs: 3000,
	restartDelayMs: 1000,
	retries: 4
};

const sleepFor = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ensureRoot({service, call}) -> the root `status` reply. `service` is a createServiceClient.
export const ensureRoot = async ({service, call, sleep = sleepFor, log = defaultLog, timings = ELEVATION}) => {
	const probe = async () => {
		try {
			return {reply: await service.status(timings.statusTimeoutMs)};
		} catch (e) {
			return {error: e};
		}
	};

	let first = await probe();
	if (first.reply && first.reply.root) {
		log(`service is root (uid ${first.reply.uid}, version ${first.reply.version})`);
		return first.reply;
	}
	log(first.reply ? `service is not root (uid ${first.reply.uid}); elevating` : `service status failed (${errorMessage(first.error)}); elevating`);

	try {
		const r = await withTimeout(call(HBC_ELEVATE, {id: SERVICE_ID}), timings.elevateTimeoutMs, 'elevateService');
		if (!r || r.returnValue === false) throw Object.assign(new Error((r && r.errorText) || 'elevateService failed'), {reply: r});
		log('Homebrew Channel elevated the service');
	} catch (e) {
		const text = errorMessage(e);
		log(`elevateService failed: ${text}`);
		throw new FatalError(classifyExecFailure(text), text);
	}

	// The running instance is still the unelevated one: ask it to exit so the next call starts the
	// elevated binary. It may not answer ("Message status unknown"), which is expected.
	try {
		await service.quit(timings.quitTimeoutMs);
		log('asked the service to quit');
	} catch (e) {
		log(`quit: ${errorMessage(e)} (expected)`);
	}

	let last = null;
	for (let i = 0; i < timings.retries; i++) {
		await sleep(timings.restartDelayMs);
		last = await probe();
		if (last.reply && last.reply.root) {
			log(`service is root after elevation (uid ${last.reply.uid}, version ${last.reply.version})`);
			return last.reply;
		}
		log(`status after elevation (${i + 1}/${timings.retries}): ${last.reply ? `not root, uid ${last.reply.uid}` : errorMessage(last.error)}`);
	}
	if (last && last.reply) throw new FatalError('notRoot', `the service runs as uid ${last.reply.uid} after elevation`);
	throw new FatalError('failed', `the service did not answer after elevation: ${errorMessage(last && last.error)}`);
};
