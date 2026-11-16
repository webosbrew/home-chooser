// SPDX-License-Identifier: Apache-2.0
'use strict';
// Behaviour of the generated webOS 4.0 key filter (service/src/homekey-generator.c) against a mock of the
// compositor's key filter scope.   node homekey40-filter.js <fullscreen filter.js> <overlay filter.js>
var fs = require('fs');
var vm = require('vm');
var assert = require('assert');

var TARGET = 'test.home';
var NEXT = 2, ACCEPTED = 1;
var SUPER = 0x01000053, BACK = 0x01200001, OTHER = 0x41;

function scope(file, over) {
	var log = [];
	var timer = {running: false};
	var s = {
		KeyPolicy: {NotAccepted: 0, Accepted: 1, NextPolicy: 2},
		Qt: {Key_Super_L: SUPER},
		WebOS: {Key_webOS_Back: BACK},
		global: {
			activeSurfaceAppId: 'com.webos.app.livetv',
			prepareToGoHome: function () { log.push('prepare'); },
			gotoRecents: function () { log.push('recents'); },
			hideHome: function () { log.push('hideHome'); },
			closeOverlaidItems: function () { log.push('closeOverlays:' + Array.prototype.join.call(arguments, ',')); },
			surfaceAcceptsBackKey: function () { return 'false'; }
		},
		fullscreenView: {appId: 'com.webos.app.livetv'},
		superRibbon: {visible: false},
		modalAlert: {visible: false}, keyboard: {visible: false}, pincodePrompt: {visible: false}, alert: {visible: false},
		advancedAlert: {visible: false}, globalActions: {visible: false}, appSpinner: {visible: false},
		voiceAgent: {isRunning: function () { return false; }},
		bootManager: {bootFirstUse: false}, systemPropertyService: {powerOnlyMode: false},
		settingsService: {lastInputApp: 'com.webos.app.hdmi2'},
		longPressTimer: {
			isRunning: function () { return timer.running; },
			set: function (key, ms) { timer.running = true; log.push('timer:' + ms); },
			cancel: function () { timer.running = false; log.push('cancel'); }
		},
		applicationManager: {launch: function (id, params) { log.push('launch:' + id + ':' + params); }},
		pmLog: {info: function () {}}
	};
	Object.keys(over || {}).forEach(function (k) { s[k] = over[k]; });
	var ctx = vm.createContext(s);
	vm.runInContext(fs.readFileSync(file, 'utf8'), ctx, {filename: file});
	return {ctx: ctx, s: s, log: log, timer: timer, call: function (k, p, r) { return ctx.handleHomeChooserKeys(k, p, r); }};
}

function check(name, fn) {
	try { fn(); console.log('ok   ' + name); } catch (e) { console.log('FAIL ' + name + ': ' + e.message); process.exitCode = 1; }
}

[['fullscreen', process.argv[2], false], ['overlay', process.argv[3], true]].forEach(function (v) {
	var kind = v[0], file = v[1], overlay = v[2];
	var t = function (n) { return kind + ': ' + n; };
	check(t('baked id'), function () {
		var f = scope(file);
		assert.strictEqual(f.ctx.HC_TARGET, TARGET); assert.strictEqual(f.ctx.HC_OVERLAY, overlay);
	});
	check(t('other keys pass on'), function () {
		assert.strictEqual(scope(file).call(OTHER, true, false), NEXT);
	});
	check(t('Home press consumes, arms the long press and prepares'), function () {
		var f = scope(file);
		assert.strictEqual(f.call(SUPER, true, false), ACCEPTED);
		assert.deepStrictEqual(f.log, ['prepare', 'timer:1000']);
		assert.strictEqual(f.call(SUPER, true, true), ACCEPTED); // auto repeat
		assert.deepStrictEqual(f.log, ['prepare', 'timer:1000']);
	});
	check(t('Home release closes overlays and launches the launcher once'), function () {
		var f = scope(file);
		f.call(SUPER, true, false); f.log.length = 0;
		assert.strictEqual(f.call(SUPER, false, false), ACCEPTED);
		assert.deepStrictEqual(f.log, ['cancel', 'closeOverlays:false,false,false,false,true',
			'launch:' + TARGET + ':{"launchType":"homeKey","activateType":"home"}']);
	});
	check(t('Home over the launcher does not launch it again'), function () {
		var f = scope(file, {fullscreenView: {appId: TARGET}});
		f.ctx.global.activeSurfaceAppId = TARGET;
		f.call(SUPER, true, false); f.log.length = 0;
		assert.strictEqual(f.call(SUPER, false, false), ACCEPTED);
		assert.deepStrictEqual(f.log, ['cancel']);
	});
	check(t('long press: the recents timer fired, the release does nothing'), function () {
		var f = scope(file);
		f.call(SUPER, true, false); f.timer.running = false; f.log.length = 0;
		assert.strictEqual(f.call(SUPER, false, false), ACCEPTED);
		assert.deepStrictEqual(f.log, []);
	});
	check(t('Home hides a showing ribbon'), function () {
		var f = scope(file);
		f.call(SUPER, true, false); f.ctx.superRibbon.visible = true; f.log.length = 0;
		f.call(SUPER, false, false);
		assert.ok(f.log.indexOf('hideHome') >= 0 && f.log.some(function (l) { return l.indexOf('launch:') === 0; }));
	});
	check(t('Home passes on under a modal alert and during first use'), function () {
		var f = scope(file); f.ctx.modalAlert.visible = true;
		assert.strictEqual(f.call(SUPER, true, false), NEXT);
		f = scope(file); f.ctx.bootManager.bootFirstUse = true;
		assert.strictEqual(f.call(SUPER, true, false), NEXT);
		f = scope(file); f.ctx.bootManager.bootFirstUse = true; f.ctx.systemPropertyService.powerOnlyMode = true;
		assert.strictEqual(f.call(SUPER, true, false), ACCEPTED);
	});
	check(t('a failing launch still consumes Home'), function () {
		var f = scope(file, {applicationManager: {launch: function () { throw new Error('no'); }}});
		f.call(SUPER, true, false);
		assert.strictEqual(f.call(SUPER, false, false), ACCEPTED);
	});
	check(t('Back elsewhere is left to the platform'), function () {
		var f = scope(file);
		assert.strictEqual(f.call(BACK, true, false), NEXT); assert.strictEqual(f.call(BACK, false, false), NEXT);
	});
	var front = function (f) { f.ctx.global.activeSurfaceAppId = TARGET; f.ctx.fullscreenView.appId = TARGET; return f; };
	check(t('Back on the launcher that does not take it is consumed' + (overlay ? ', release opens the last input' : ', nothing else')), function () {
		var f = front(scope(file));
		assert.strictEqual(f.call(BACK, true, false), ACCEPTED);
		assert.deepStrictEqual(f.log, []);
		assert.strictEqual(f.call(BACK, false, false), ACCEPTED);
		if (overlay) {
			assert.deepStrictEqual(f.log, ['closeOverlays:false,false,false,false,true',
				'launch:com.webos.app.hdmi2:{"launchType":"homeKey","activateType":"home"}']);
		} else {
			assert.deepStrictEqual(f.log, []);
		}
	});
	if (overlay) {
		check(t('no last input known: Live TV'), function () {
			var f = front(scope(file, {settingsService: {}}));
			f.call(BACK, false, false);
			assert.ok(f.log.some(function (l) { return l.indexOf('launch:com.webos.app.livetv:') === 0; }));
		});
	}
	check(t('Back on a launcher that takes Back itself is left alone'), function () {
		var f = front(scope(file));
		f.ctx.global.surfaceAcceptsBackKey = function (id) { assert.strictEqual(id, TARGET); return 'true'; };
		assert.strictEqual(f.call(BACK, true, false), NEXT); assert.strictEqual(f.call(BACK, false, false), NEXT);
		assert.deepStrictEqual(f.log, []);
	});
	['modalAlert', 'keyboard', 'pincodePrompt', 'alert', 'advancedAlert', 'globalActions', 'appSpinner', 'superRibbon'].forEach(function (name) {
		check(t('Back with ' + name + ' up is left alone'), function () {
			var f = front(scope(file)); f.ctx[name].visible = true;
			assert.strictEqual(f.call(BACK, false, false), NEXT); assert.deepStrictEqual(f.log, []);
		});
	});
	check(t('Back with the voice agent running is left alone'), function () {
		var f = front(scope(file)); f.ctx.voiceAgent.isRunning = function () { return true; };
		assert.strictEqual(f.call(BACK, false, false), NEXT);
	});
	check(t('Back with an unknown scope object is left alone, never thrown'), function () {
		var f = front(scope(file)); f.ctx.keyboard = undefined;
		assert.strictEqual(f.call(BACK, false, false), NEXT);
	});
});
