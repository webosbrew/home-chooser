// SPDX-License-Identifier: Apache-2.0
import {HBC_ELEVATE, ensureRoot, restartService, FatalError} from '../elevate';
import {ServiceError} from '../errors';
import {SERVICE_ID} from '../service';

const FAST = {statusTimeoutMs: 50, elevateTimeoutMs: 50, quitTimeoutMs: 50, restartDelayMs: 0, retries: 3};
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

	test('not root: elevate, quit, status again', async () => {
		const t = setup({statuses: [USER, ROOT]});
		await expect(t.run()).resolves.toEqual(ROOT);
		expect(t.order).toEqual(['status', 'elevate', 'quit', 'status']);
		expect(t.logs.some((l) => /elevating/.test(l))).toBe(true);
	});

	test('a status that fails is elevated too', async () => {
		const t = setup({statuses: [new ServiceError({returnValue: false, errorText: 'Denied method call "status"'}), ROOT]});
		await expect(t.run()).resolves.toEqual(ROOT);
		expect(t.order).toEqual(['status', 'elevate', 'quit', 'status']);
	});

	test('a quit that gets no answer is fine', async () => {
		const t = setup({statuses: [USER, ROOT], quit: new Error('Message status unknown.')});
		await expect(t.run()).resolves.toEqual(ROOT);
	});

	test('the new instance may need a moment: retries the status', async () => {
		const t = setup({statuses: [USER, new Error('Service does not exist'), USER, ROOT]});
		await expect(t.run()).resolves.toEqual(ROOT);
		expect(t.service.status).toHaveBeenCalledTimes(4);
	});

	test('Homebrew Channel refusing the call is denied', async () => {
		const t = setup({statuses: [USER], elevate: new ServiceError({returnValue: false, errorText: 'Denied method call "elevateService" for category "/"'})});
		await expect(t.run()).rejects.toMatchObject({kind: 'denied'});
		expect(t.service.quit).not.toHaveBeenCalled();
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
		expect(t.service.status).toHaveBeenCalledTimes(1 + FAST.retries);
	});

	test('no answer after elevation is failed', async () => {
		const t = setup({statuses: [USER, new Error('Service does not exist')]});
		await expect(t.run()).rejects.toMatchObject({kind: 'failed'});
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
