// SPDX-License-Identifier: Apache-2.0
import {ELEVATION, HBC_ELEVATE, ensureRoot, restartService, FatalError} from '../elevate';
import {ServiceError} from '../errors';
import {SERVICE_ID} from '../service';

const FAST = {statusTimeoutMs: 50, elevateTimeoutMs: 50, quitTimeoutMs: 50, restartDelayMs: 0, retries: 3, earlyRetries: 2};
const ROOT = {returnValue: true, root: true, uid: 0, version: '0.1.0'};
const USER = {returnValue: true, root: false, uid: 5000, version: '0.1.0'};

// status answers come from a queue; quit and the Homebrew Channel call are recorded.
const setup = ({statuses, elevate, quit} = {}) => {
	const order = [];
	const queue = statuses.slice();
	const service = {
		status: jest.fn(async () => {
			order.push('status');
			const next = queue.length > 1 ? queue.shift() : queue[0];
			if (next instanceof Error) throw next;
			return next;
		}),
		quit: jest.fn(async () => {
			order.push('quit');
			if (quit) throw quit;
		})
	};
	const call = jest.fn(async (uri, payload) => {
		order.push('elevate');
		expect([uri, payload]).toEqual([HBC_ELEVATE, {id: SERVICE_ID}]);
		if (elevate instanceof Error) throw elevate;
		return elevate || {returnValue: true};
	});
	const logs = [];
	return {service, call, order, logs, run: () => ensureRoot({service, call, sleep: async () => {}, log: (t) => logs.push(t), timings: FAST})};
};

describe('ensureRoot', () => {
	test('a service that is root needs nothing', async () => {
		const t = setup({statuses: [ROOT]});
		await expect(t.run()).resolves.toEqual(ROOT);
		expect(t.order).toEqual(['status']);
	});

	test('not root: restart first, then elevate, quit, status again', async () => {
		const t = setup({statuses: [USER, USER, USER, ROOT]});
		await expect(t.run()).resolves.toEqual(ROOT);
		expect(t.order).toEqual(['status', 'quit', 'status', 'status', 'elevate', 'quit', 'status']);
		expect(t.logs.some((l) => /elevating/.test(l))).toBe(true);
	});

	test('a status that fails is elevated too', async () => {
		const t = setup({statuses: [new ServiceError({returnValue: false, errorText: 'Denied method call "status"'}), new Error('gone'), new Error('gone'), ROOT]});
		await expect(t.run()).resolves.toEqual(ROOT);
		expect(t.order).toEqual(['status', 'quit', 'status', 'status', 'elevate', 'quit', 'status']);
	});

	test('a quit that gets no answer is fine', async () => {
		const t = setup({statuses: [USER, USER, USER, ROOT], quit: new Error('Message status unknown.')});
		await expect(t.run()).resolves.toEqual(ROOT);
	});

	test('the new instance may need a moment: retries the status', async () => {
		const t = setup({statuses: [USER, USER, USER, USER, new Error('Service does not exist'), ROOT]});
		await expect(t.run()).resolves.toEqual(ROOT);
		expect(t.service.status).toHaveBeenCalledTimes(6);
	});

	test('Homebrew Channel refusing the call is denied', async () => {
		const t = setup({statuses: [USER], elevate: new ServiceError({returnValue: false, errorText: 'Denied method call "elevateService" for category "/"'})});
		await expect(t.run()).rejects.toMatchObject({kind: 'denied'});
	});

	test('Homebrew Channel missing is unreachable', async () => {
		const t = setup({statuses: [USER], elevate: new Error('Service does not exist: org.webosbrew.hbchannel.service')});
		await expect(t.run()).rejects.toMatchObject({kind: 'unreachable'});
	});

	test('Homebrew Channel reporting a failure is failed, with its text', async () => {
		const t = setup({statuses: [USER], elevate: {returnValue: false, errorText: 'Service file not found'}});
		const e = await t.run().catch((x) => x);
		expect(e).toBeInstanceOf(FatalError);
		expect(e).toMatchObject({kind: 'failed'});
		expect(e.detail).toMatch(/Service file not found/);
	});

	test('a Homebrew Channel call that never answers is unreachable-or-failed, not a hang', async () => {
		const service = {status: async () => USER, quit: async () => {}};
		const call = () => new Promise(() => {});
		await expect(ensureRoot({service, call, sleep: async () => {}, log: () => {}, timings: FAST})).rejects.toBeInstanceOf(FatalError);
	});

	test('still not root after elevation is notRoot', async () => {
		const t = setup({statuses: [USER]});
		await expect(t.run()).rejects.toMatchObject({kind: 'notRoot'});
		expect(t.service.status).toHaveBeenCalledTimes(1 + FAST.earlyRetries + FAST.retries);
	});

	test('no answer after elevation is failed', async () => {
		const t = setup({statuses: [USER, new Error('Service does not exist')]});
		await expect(t.run()).rejects.toMatchObject({kind: 'failed'});
	});
});

describe('ensureRoot on a slow TV', () => {
	const slow = (ms, value) => new Promise((resolve) => setTimeout(() => resolve(value), ms));

	test('the budget covers a 40 to 60 s elevation, with margin', () => {
		expect(ELEVATION.elevateTimeoutMs).toBeGreaterThanOrEqual(100000);
	});

	test('a reply that comes after the old 20 s budget is accepted', async () => {
		jest.useFakeTimers();
		try {
			const statuses = [USER, USER, USER, ROOT];
			const service = {status: async () => (statuses.length > 1 ? statuses.shift() : statuses[0]), quit: async () => {}};
			const call = () => slow(55000, {returnValue: true});
			const logs = [];
			const progress = [];
			const run = ensureRoot({service, call, sleep: async () => {}, log: (l) => logs.push(l), onProgress: (p) => progress.push(p), timings: {...ELEVATION, restartDelayMs: 0}});
			await jest.advanceTimersByTimeAsync(60000);
			await expect(run).resolves.toEqual(ROOT);
			expect(progress).toEqual(['elevating']);
			expect(logs.some((l) => /Homebrew Channel elevated/.test(l))).toBe(true);
		} finally {
			jest.useRealTimers();
		}
	});

	test('an elevateService that times out is followed by quit and status; root then is success', async () => {
		const t = setup({statuses: [USER, USER, USER, ROOT]});
		t.call.mockImplementation(() => { t.order.push('elevate'); return new Promise(() => {}); });
		await expect(t.run()).resolves.toEqual(ROOT);
		expect(t.order).toEqual(['status', 'quit', 'status', 'status', 'elevate', 'quit', 'status']);
		expect(t.logs.some((l) => /elevateService failed.*timed out|elevateService failed/.test(l))).toBe(true);
	});

	test('an elevateService error is followed by quit and status too', async () => {
		const t = setup({statuses: [USER, USER, USER, ROOT], elevate: {returnValue: false, errorText: 'boom'}});
		await expect(t.run()).resolves.toEqual(ROOT);
	});

	test('a registration rewritten earlier: quit and status are enough, no elevateService', async () => {
		const t = setup({statuses: [USER, ROOT]});
		await expect(t.run()).resolves.toEqual(ROOT);
		expect(t.order).toEqual(['status', 'quit', 'status']);
		expect(t.call).not.toHaveBeenCalled();
	});

	test('not elevated by the timeout and still not root: the elevate failure is reported', async () => {
		const t = setup({statuses: [USER]});
		t.call.mockImplementation(() => new Promise(() => {}));
		await expect(t.run()).rejects.toMatchObject({kind: 'failed'});
		expect(t.service.status).toHaveBeenCalledTimes(1 + FAST.earlyRetries + FAST.retries);
	});

	test('onProgress is not called when the restart is enough', async () => {
		const t = setup({statuses: [USER, ROOT]});
		const onProgress = jest.fn();
		await ensureRoot({service: t.service, call: t.call, sleep: async () => {}, log: () => {}, timings: FAST, onProgress});
		expect(onProgress).not.toHaveBeenCalled();
	});
});

describe('restartService', () => {
	test('quit, then status until the new root instance answers', async () => {
		const t = setup({statuses: [new ServiceError('timeout', 'gone'), ROOT]});
		const run = restartService({service: t.service, sleep: async () => {}, log: (l) => t.logs.push(l), timings: FAST});
		await expect(run).resolves.toEqual(ROOT);
		expect(t.order).toEqual(['quit', 'status', 'status']);
		expect(t.call).not.toHaveBeenCalled();
	});
	test('no root instance after the restart is fatal', async () => {
		const t = setup({statuses: [USER]});
		const run = restartService({service: t.service, sleep: async () => {}, log: () => {}, timings: FAST});
		await expect(run).rejects.toEqual(expect.objectContaining({kind: 'notRoot'}));
	});
});
