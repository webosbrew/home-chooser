// SPDX-License-Identifier: Apache-2.0
// Drives api/client/homechooser.js and homechooser-mock.js in node with a fake webOS bridge and prints
// one JSON line per observation. api/tools/test-client.py runs it, checks the lines and validates every
// reply against api/launcher-v1.openrpc.json. Usage: node client-test.js SPEC CLIENT_DIR
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const [specPath, clientDir] = process.argv.slice(2);
const spec = JSON.parse(fs.readFileSync(specPath, 'utf8'));
const clientSource = fs.readFileSync(path.join(clientDir, 'homechooser.js'), 'utf8');
const mockSource = fs.readFileSync(path.join(clientDir, 'homechooser-mock.js'), 'utf8');
const out = (record) => process.stdout.write(JSON.stringify(record) + '\n');

// A browser-like global: `window` is the global object, as in the web runtime.
function context(extra) {
	const sandbox = {setTimeout, clearTimeout, console, JSON, Promise};
	Object.assign(sandbox, extra || {});
	sandbox.window = sandbox;
	return vm.createContext(sandbox);
}

function describe(error) {
	return {
		name: error && error.name, source: error && error.source, errorCode: error && error.errorCode,
		errorText: error && error.errorText, code: error && error.code, reply: error && error.reply,
		isError: error instanceof Error || (error && typeof error.stack === 'string')
	};
}

async function settle(promise) {
	try {
		return {ok: true, value: await promise};
	} catch (e) {
		return {ok: false, error: describe(e)};
	}
}

// A bridge that answers each call with the next scripted message.
function fakeBridge(script, calls) {
	return function Bridge() {
		this.cancelled = false;
		this.call = (uri, payload) => {
			calls.push({uri, payload: JSON.parse(payload)});
			const message = script.shift();
			setTimeout(() => this.onservicecallback(message), 1);
		};
		this.cancel = () => { this.cancelled = true; calls.push({cancel: true}); };
	};
}

async function bridgeTests() {
	// Every spec example, as the bridge's answer to the example's params.
	for (const m of spec.methods) {
		for (const ex of m.examples) {
			const calls = [];
			const ctx = context({WebOSServiceBridge: fakeBridge([JSON.stringify(ex.result.value)], calls)});
			vm.runInContext(clientSource, ctx);
			const params = Object.fromEntries(ex.params.map((p) => [p.name, p.value]));
			const r = await settle(ctx.HomeChooser[m.name](ex.params.length ? params : undefined));
			out({test: 'bridge-example', method: m.name, example: ex.name, expected: ex.result.value, result: r, calls});
		}
	}
	// A platform reply (no apiVersion, numeric errorCode), a non-JSON reply and no bridge at all.
	const platform = {returnValue: false, errorCode: -1, errorText: 'Unknown method "getInfo" for category "/"'};
	for (const [label, script, extra] of [
		['platform', [JSON.stringify(platform)], null],
		['not-json', ['<html>'], null],
		['no-bridge', null, {}]
	]) {
		const calls = [];
		const ctx = context(extra || {PalmServiceBridge: fakeBridge(script, calls)});
		vm.runInContext(clientSource, ctx);
		out({test: 'bridge-failure', label, result: await settle(ctx.HomeChooser.getInfo()), calls});
	}
	// supports(level): false before a reply, then the highest apiVersion a Home Chooser reply carried.
	{
		const ctx = context({WebOSServiceBridge: fakeBridge([JSON.stringify({returnValue: true, apiVersion: 2, version: '1', root: true, permission: 'p', permitted: true, isSelectedHome: true})], [])});
		vm.runInContext(clientSource, ctx);
		const before = ctx.HomeChooser.supports(1);
		await settle(ctx.HomeChooser.getInfo());
		out({test: 'supports', before, level1: ctx.HomeChooser.supports(1), level2: ctx.HomeChooser.supports(2), level3: ctx.HomeChooser.supports(3), apiVersion: ctx.HomeChooser.API_VERSION});
	}
	// requestHome: the x-requestLaunch payload, with the app id from PalmSystem.identifier by default.
	{
		const calls = [];
		const ctx = context({
			WebOSServiceBridge: fakeBridge([JSON.stringify({returnValue: true}), JSON.stringify({returnValue: true})], calls),
			PalmSystem: {identifier: 'org.example.launcher 1234'}
		});
		vm.runInContext(clientSource, ctx);
		const own = ctx.HomeChooser.ownAppId();
		const a = await settle(ctx.HomeChooser.requestHome());
		const b = await settle(ctx.HomeChooser.requestHome('org.example.other'));
		out({test: 'request', own, a, b, calls});
		const bare = context({WebOSServiceBridge: fakeBridge([], [])});
		vm.runInContext(clientSource, bare);
		const c = await settle(bare.HomeChooser.requestHome());
		out({test: 'request-no-id', own: bare.HomeChooser.ownAppId(), result: c,
			typeError: !c.ok && c.error.name === 'TypeError'});
	}
	// CommonJS: require() returns the API without touching a global.
	{
		const mod = {exports: {}};
		new Function('module', 'exports', clientSource)(mod, mod.exports);
		out({test: 'commonjs', methods: spec.methods.map((m) => typeof mod.exports[m.name])});
	}
	// The mock does nothing where a bridge exists.
	{
		const ctx = context({WebOSServiceBridge: fakeBridge([JSON.stringify(spec.methods[0].examples[0].result.value)], [])});
		vm.runInContext(clientSource, ctx);
		vm.runInContext(mockSource, ctx);
		out({test: 'mock-inert', hasMock: 'mock' in ctx.HomeChooser});
	}
}

// A bridge that answers each call with all of its scripted messages, one by one (a subscription).
function streamBridge(script, calls) {
	return function Bridge() {
		this.call = (uri, payload) => {
			calls.push({uri, payload: JSON.parse(payload)});
			script.forEach((message, i) => setTimeout(() => { if (!this.cancelled) this.onservicecallback(message); }, 1 + i));
		};
		this.cancel = () => { this.cancelled = true; calls.push({cancel: true}); };
	};
}

const tick = (ms) => new Promise((resolve) => setTimeout(resolve, ms || 5));

async function subscribeTests() {
	const listInputs = spec.methods.find((m) => m.name === 'listInputs');
	const first = listInputs.examples.find((ex) => ex.name === 'subscribe').result.value;
	const failure = {returnValue: false, apiVersion: 2, errorCode: 'tooManySubscriptions', errorText: 'full'};
	for (const [label, script] of [
		['two-replies', [first, first].map((v) => JSON.stringify(v))],
		['refused', [JSON.stringify(failure), JSON.stringify(first)]],
		['not-json', ['<html>']]
	]) {
		const calls = [], replies = [], errors = [];
		const ctx = context({WebOSServiceBridge: streamBridge(script, calls)});
		vm.runInContext(clientSource, ctx);
		const sub = ctx.HomeChooser.subscribe('listInputs', {}, (r) => replies.push(r), (e) => errors.push(describe(e)));
		await tick(20);
		sub.cancel();
		sub.cancel();
		out({test: 'bridge-subscribe', label, replies, errors, calls});
	}
	// The mock: a fresh first reply, the list again on notify, nothing after cancel, the subscriber cap.
	const ctx = context({location: {search: ''}});
	vm.runInContext(clientSource, ctx);
	vm.runInContext(mockSource, ctx);
	const HC = ctx.HomeChooser;
	HC.mock.delayMs = 0;
	const replies = [], errors = [];
	const sub = HC.subscribe('listInputs', null, (r) => replies.push(r), (e) => errors.push(describe(e)));
	await tick();
	HC.mock.inputs[0].connected = !HC.mock.inputs[0].connected;
	HC.mock.notify('listInputs');
	await tick();
	sub.cancel();
	HC.mock.notify('listInputs');
	await tick();
	const subs = [];
	for (let i = 0; i < spec['x-luna'].subscribersMax; i++) subs.push(HC.subscribe('listApps', {}, () => {}));
	const over = [];
	HC.subscribe('listApps', {}, () => {}, (e) => over.push(describe(e)));
	await tick();
	subs.forEach((s) => s.cancel());
	out({test: 'mock-subscribe', replies, errors, over});
}

async function mockTests() {
	for (const scenario of ['selected-home', 'before-elevation', 'not-permitted', 'not-home', 'absent']) {
		const ctx = context({location: {search: '?hcmock=' + scenario}});
		vm.runInContext(clientSource, ctx);
		vm.runInContext(mockSource, ctx);
		const HC = ctx.HomeChooser;
		HC.mock.delayMs = 0;
		const replies = {};
		for (const m of spec.methods) {
			replies[m.name] = [];
			const params = m.examples.map((ex) => Object.fromEntries(ex.params.map((p) => [p.name, p.value])));
			params.push({}, {id: '../bad'}, {id: 'org.example.app', size: null});
			for (const p of params) {
				replies[m.name].push({params: p, result: await settle(HC[m.name](p))});
			}
		}
		const launch = await settle(HC.lunaCall('luna://com.webos.applicationManager/launch', {id: 'org.example.app'}));
		const request = await settle(HC.requestHome());
		out({test: 'mock', scenario, own: HC.ownAppId(), replies, launch, request, launches: HC.mock.launches});
	}
}

(async () => {
	await bridgeTests();
	await subscribeTests();
	await mockTests();
	out({test: 'done'});
})().catch((e) => { console.error(e); process.exit(1); });
