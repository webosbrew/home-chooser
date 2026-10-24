'use strict';
// Behavioural comparison of handleHomeKeys: stock vs core-only vs complete hook.
//   node harness.js <variant>=<systemUi.js path> ...   (variant: stock | core | min)
// Runs every scenario in each variant, with the key filter's applicationManager emulated the way
// each variant's StarfishKeyFilter.qml defines it, and prints one line per scenario with the actions.
var fs = require('fs');

var STOCK = 'com.webos.app.home';
var LAUNCHER = 'org.example.launcher';
var KEYS = {Key_Super_L: 0x01000053, Key_Meta: 0x01000022, Key_Menu: 0x01000055};

function deepStub(name) {
	var fn = function () { return undefined; };
	return new Proxy(fn, {
		get: function (t, p) {
			if (p === Symbol.toPrimitive) return function () { return ''; };
			if (p === 'toString' || p === 'valueOf') return function () { return ''; };
			if (p === 'then') return undefined;
			if (p === 'length') return 0;
			return deepStub(name + '.' + String(p));
		},
		apply: function () { return undefined; }
	});
}

function load(file) {
	var src = fs.readFileSync(file, 'latin1');
	var declared = {};
	src.replace(/^(?:var|function)\s+([A-Za-z_$][\w$]*)/gm, function (a, n) { declared[n] = true; });
	var env = {};
	var builtins = {JSON: 1, Object: 1, Array: 1, Math: 1, String: 1, Number: 1, parseInt: 1, Date: 1, undefined: 1, isNaN: 1, Boolean: 1, RegExp: 1, Error: 1, console: 1};
	var proxy = new Proxy(env, {
		has: function (t, p) { return typeof p === 'string' && !declared[p] && !builtins[p]; },
		get: function (t, p) {
			if (p === Symbol.unscopables) return undefined;
			if (p in t) return t[p];
			return deepStub(String(p));
		},
		set: function (t, p, v) { t[p] = v; return true; }
	});
	var names = Object.keys(declared).filter(function (n) { return /^[A-Za-z_$][\w$]*$/.test(n); });
	var body = 'with (__env) {\n' + src + '\n;return {' + names.map(function (n) {
		return JSON.stringify(n) + ': function(){ return typeof ' + n + ' === "undefined" ? undefined : ' + n + '; }';
	}).join(',') + '};\n}';
	/* eslint no-new-func: 0 */
	var make = new Function('__env', body);
	return {env: env, proxy: proxy, make: make};
}

function scenario(variant, mod, sc) {
	var log = [];
	var home = sc.choice || STOCK; // live record (defaultApps.home); no choice -> stock id written (34a4ff9)
	function getHomeAppId() { return home; }
	function lg(s) { log.push(s); }
	function launchLog(kind, id, params) {
		var p = params;
		if (typeof p === 'string') { try { p = JSON.parse(p); } catch (e) {} }
		lg(kind + ' ' + id + ' ' + JSON.stringify(p));
	}
	var am = {
		launch: function (appId, params) {
			if (variant !== 'stock' && appId === STOCK && getHomeAppId() !== STOCK) return launchLog('launch', getHomeAppId(), params);
			launchLog('launch', appId, params);
		},
		checkLaunch: function (appId, params) {
			if (variant !== 'stock' && appId === STOCK) appId = getHomeAppId();
			launchLog('launch', appId, params);
		},
		close: function (id) { lg('close ' + id); }
	};
	if (variant !== 'stock') am.getHomeAppId = getHomeAppId;
	var e = mod.env;
	Object.keys(e).forEach(function (k) { delete e[k]; });
	e.Qt = new Proxy(KEYS, {get: function (t, p) { return p in t ? t[p] : 0x7000; }});
	e.KeyPolicy = {Accepted: 'Accepted', NotAccepted: 'NotAccepted', NextPolicy: 'NextPolicy'};
	e.applicationManager = am;
	e.access = {ribbon: true, keyFilter: {home: !!sc.kfHome, back: false, exit: false}, keyAction: {launchApp: true}};
	e.fullscreenView = {appId: sc.front, activeFocus: !sc.overlay && !sc.other, isMultiViewMode: false};
	e.overlayView = {activeFocus: !!sc.overlay, access: {ribbon: true, keyFilter: {home: !!sc.overlayKfHome}}};
	e.homeView = {appId: '', activeFocus: false, visible: false, access: {keyFilter: {home: false}, ribbon: true}, close: function () {}};
	e.settingsService = {disableHome: false, lastInputApp: ''};
	e.bootManager = {bootFirstUse: false};
	e.systemPropertyService = {powerOnlyMode: false};
	e.modalAlert = {visible: false};
	e.supportScreenType = '';
	e.rollingScreenViewState = {state: ''};
	e.configdService = {supportNMRM: !!sc.nmrm};
	e.voiceAgent = {isRunning: function () { return !!sc.voice; }, close: function () { lg('voiceAgent.close'); }};
	e.longPressTimer = {set: function () {}, isRunning: function () { return true; }, cancel: function () {}};
	e.nativeKeyCode = {KEY_HOME: 102};
	e.pmLog = {info: function () {}, warning: function () {}, error: function () {}};
	e.global = {
		specialUIVisible: !!sc.special, closeSpecialUI: function () { lg('closeSpecialUI'); }, zoomON: false,
		surfaceAcceptsMetaKey: function () { return 'false'; },
		closeOverlaidItems: function () { lg('closeOverlaidItems'); },
		sendKeyToFocusWindow: function (k, p) { lg('sendKey ' + k.toString(16) + ' ' + p); },
		prepareToGoHome: function () {}
	};
	var api = mod.make(mod.proxy);
	var h = api.handleHomeKeys();
	var r1 = h(sc.key, true, false);
	var r2 = h(sc.key, false, false);
	return 'press=' + r1 + ' release=' + r2 + ' | ' + log.join('; ');
}

var SCENARIOS = [];
[null, LAUNCHER].forEach(function (choice) {
	[STOCK, LAUNCHER, 'com.webos.app.livetv'].forEach(function (front) {
		['Key_Super_L', 'Key_Meta', 'Key_Menu'].forEach(function (key) {
			[{}, {overlay: 1}, {overlay: 1, overlayKfHome: 1}, {other: 1}, {voice: 1}, {kfHome: 1}, {special: 1}, {nmrm: 1}].forEach(function (x) {
				var sc = {choice: choice, front: front, key: KEYS[key], keyName: key};
				Object.keys(x).forEach(function (k) { sc[k] = x[k]; });
				SCENARIOS.push(sc);
			});
		});
	});
});

var variants = process.argv.slice(2).map(function (a) {
	var i = a.indexOf('=');
	return {name: a.slice(0, i), mod: load(a.slice(i + 1))};
});
SCENARIOS.forEach(function (sc) {
	var label = 'choice=' + (sc.choice ? 'L' : '-') + ' front=' + (sc.front === STOCK ? 'stock' : sc.front === LAUNCHER ? 'L' : 'tv') + ' ' + sc.keyName +
		Object.keys(sc).filter(function (k) { return ['choice', 'front', 'key', 'keyName'].indexOf(k) < 0; }).map(function (k) { return ' ' + k; }).join('');
	variants.forEach(function (v) {
		var out;
		try { out = scenario(v.name, v.mod, sc); } catch (e) { out = 'THROW ' + e.message; }
		console.log(v.name + '\t' + label + '\t' + out);
	});
});
