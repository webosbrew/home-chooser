// SPDX-License-Identifier: Apache-2.0
// Startup elevation: make sure Home Chooser's service runs as root.
//   status -> if not root: quit + status (an earlier elevation may have rewritten the registration
//   already) -> if still not root: Homebrew Channel elevateService -> quit -> status again.
// elevateService is slow on some TVs (a cold start of Homebrew Channel's service, then a rescan: 40 to
// 60 s on a webOS 6.5.2 emulated TV), so it gets a long budget, and a timeout or an error from it is
// not final: the quit and the status probe follow anyway, and only a service that is still not root
// then is a failure.
// Failures are FatalErrors whose `kind` the screen maps to a message:
//   'unreachable' Homebrew Channel is not installed / not running
//   'denied'      Homebrew Channel refuses the call from an app (webOS 26 and later, before 0.8.0)
//   'notRoot'     the service answered after elevation but is still not root
//   'failed'      anything else
import {classifyExecFailure} from '../homechooser/diagnose';
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

const defaultLog = (text) => console.log(`[HomeChooser] ${text}`); // eslint-disable-line no-console

export const ELEVATION = {
	statusTimeoutMs: 5000,
	elevateTimeoutMs: 120000,
	quitTimeoutMs: 3000,
	restartDelayMs: 1000,
	retries: 4,
	earlyRetries: 2 // probes after the first quit, before anything has been elevated
};

const sleepFor = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Asks the running service to quit, then probes `status` until a root instance answers. It may not
// answer the quit ("Message status unknown"), which is expected.
const restartAndProbe = async ({service, probe, sleep, log, timings, what, retries = timings.retries, soft = false}) => {
	try {
		await service.quit(timings.quitTimeoutMs);
		log('asked the service to quit');
	} catch (e) {
		log(`quit: ${errorMessage(e)} (expected)`);
	}

	let last = null;
	for (let i = 0; i < retries; i++) {
		await sleep(timings.restartDelayMs);
		last = await probe();
		if (last.reply && last.reply.root) {
			log(`service is root after ${what} (uid ${last.reply.uid}, version ${last.reply.version})`);
			return last.reply;
		}
		log(`status after ${what} (${i + 1}/${retries}): ${last.reply ? `not root, uid ${last.reply.uid}` : errorMessage(last.error)}`);
	}
	if (soft) return null;
	if (last && last.reply) throw new FatalError('notRoot', `the service runs as uid ${last.reply.uid} after ${what}`);
	throw new FatalError('failed', `the service did not answer after ${what}: ${errorMessage(last && last.error)}`);
};

// restartService({service}) -> the root `status` reply of a fresh instance. On webOS 11 and later a
// service registered before its access file changed keeps the old access groups until it registers
// again (homechooser-access prints restart-service then; docs/service.md#luna-access-files).
export const restartService = ({service, sleep = sleepFor, log = defaultLog, timings = ELEVATION}) => {
	const probe = async () => {
		try {
			return {reply: await service.status(timings.statusTimeoutMs)};
		} catch (e) {
			return {error: e};
		}
	};
	return restartAndProbe({service, probe, sleep, log, timings, what: 'the access change'});
};

// ensureRoot({service, call, onProgress}) -> the root `status` reply. `service` is a createServiceClient.
// onProgress('elevating') is called before the slow elevateService call.
export const ensureRoot = async ({service, call, sleep = sleepFor, log = defaultLog, timings = ELEVATION, onProgress = () => {}}) => {
	const probe = async () => {
		try {
			return {reply: await service.status(timings.statusTimeoutMs)};
		} catch (e) {
			return {error: e};
		}
	};

	const first = await probe();
	if (first.reply && first.reply.root) {
		log(`service is root (uid ${first.reply.uid}, version ${first.reply.version})`);
		return first.reply;
	}
	log(first.reply ? `service is not root (uid ${first.reply.uid})` : `service status failed (${errorMessage(first.error)})`);

	// An earlier elevation (one that outlived its caller, or an earlier open) may have rewritten the
	// registration already, and only the running instance is old: restarting it is cheap, elevating is not.
	// Assumption: the service cannot tell from inside whether its launch line is rewritten, so this is tried blind.
	log('restarting the service before elevating');
	const early = await restartAndProbe({service, probe, sleep, log, timings, what: 'the restart', retries: timings.earlyRetries, soft: true});
	if (early) return early;

	log('elevating');
	onProgress('elevating');
	let elevateError = null;
	try {
		const r = await withTimeout(call(HBC_ELEVATE, {id: SERVICE_ID}), timings.elevateTimeoutMs, 'elevateService');
		if (!r || r.returnValue === false) throw Object.assign(new Error((r && r.errorText) || 'elevateService failed'), {reply: r});
		log('Homebrew Channel elevated the service');
	} catch (e) {
		const text = errorMessage(e);
		log(`elevateService failed: ${text} (checking the service anyway)`);
		elevateError = new FatalError(classifyExecFailure(text), text);
	}

	// The running instance may still be the unelevated one: ask it to exit so the next call starts the
	// elevated binary. An elevation that timed out may have finished since, so this runs after an error too.
	try {
		return await restartAndProbe({service, probe, sleep, log, timings, what: 'elevation'});
	} catch (e) {
		throw elevateError || e;
	}
};
