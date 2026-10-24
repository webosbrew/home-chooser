#!/usr/bin/env node
'use strict';
// the minimal, hash-free Home-key hook for webOS 6 to 9.
//
//   node homechooser-keyfilter-gen.js check    --root DIR [--skip-version-gate]
//   node homechooser-keyfilter-gen.js generate --root DIR --out DIR
//
// Tier 1 (all or nothing; any failed guard -> exit 2, nothing written, stock stays):
//   Settings.qml          one constant block before the root's closing brace + one import line
//   StarfishKeyFilter.qml applicationManager.launch / checkLaunch redirect the stock id, and expose getHomeAppId
//   systemUi.js           only where `var HOME_APP_ID = "com.webos.app.home";` exists: one line at the top of
//                         handleHomeKeys refreshes HOME_APP_ID from the live home id
// Tier 2 (each file on its own; a file whose anchors do not match exactly is left stock and reported):
//   StarfishNudgeView, StarfishBaseLunaServiceAPI, StarfishRecents, StarfishFullscreenController
// Written for Node 8.12: no named groups, lookbehind, dotAll, replaceAll or recursive mkdir.

var fs = require('fs');
var path = require('path');
var crypto = require('crypto');

var Q = 'usr/lib/qml/';
var STOCK = 'com.webos.app.home';
var LIT = '"com\\.webos\\.app\\.home"';
var F = {
	settings: Q + 'WebOSCompositorBase/global/Settings.qml',
	keyfilter: Q + 'WebOSCompositor/controllers/StarfishKeyFilter.qml',
	systemui: Q + 'KeyFilters/systemUi.js',
	nudge: Q + 'WebOSCompositor/views/StarfishNudgeView.qml',
	baseluna: Q + 'WebOSCompositor/services/StarfishBaseLunaServiceAPI.qml',
	recents: Q + 'WebOSCompositor/controllers/fullscreen/StarfishRecents.qml',
	fullscreen: Q + 'WebOSCompositor/controllers/StarfishFullscreenController.qml'
};
var MARK = 'Home Chooser';
var PLACEHOLDER = '@HOMECHOOSER_HOME@';

// The one block of new logic. Settings.qml is byte-identical on every 6.x-9.x build seen, so this
// text never varies with the firmware; only where it is inserted is matched.
var SETTINGS_BLOCK = [
	'',
	'    // ' + MARK + ': the home app follows general/defaultApps.home, then the stored choice, then the stock launcher.',
	'    readonly property string stockHomeAppId: "' + STOCK + '"',
	'    // Set at boot by homechooser-keyfilter from /var/lib/webosbrew/homechooser/home; empty when no choice is stored.',
	'    readonly property string configuredHomeAppId: "' + PLACEHOLDER + '"',
	'    // Starts the defaultApps subscription with the compositor, so the first Home press sees the record.',
	'    readonly property string homeAppId: root.getHomeAppId()',
	'',
	'    function getHomeAppId() {',
	'        var apps = root.subscribe("com.webos.settingsservice", "getSystemSettings",',
	'                                  {"category": "general", "keys": ["defaultApps"]});',
	'        if (apps && typeof apps.home === "string" && apps.home !== "")',
	'            return apps.home;',
	'        if (root.configuredHomeAppId !== "")',
	'            return root.configuredHomeAppId;',
	'        return root.stockHomeAppId;',
	'    }',
	'',
	'    function isHomeApp(appId) {',
	'        return appId === root.getHomeAppId();',
	'    }',
	'',
	'    // Key filter launches of the stock id: false when the stock home is the home (the stock path runs);',
	'    // otherwise the chosen home is launched with the same params, falling back to the stock one.',
	'    function redirectHomeLaunch(appId, params, reason) {',
	'        if (appId !== root.stockHomeAppId || root.getHomeAppId() === root.stockHomeAppId)',
	'            return false;',
	'        var p = params;',
	'        if (typeof p === "string") {',
	'            try {',
	'                p = (p === "") ? {} : JSON.parse(p);',
	'            } catch (e) {',
	'                return false;',
	'            }',
	'        }',
	'        root.launchHome({"params": p || {}, "checkUpdateOnLaunch": false, "autoInstallation": false,',
	'                         "reason": typeof reason === "string" ? reason : "hotKey"});',
	'        return true;',
	'    }',
	'',
	'    property var __homeLaunch: null',
	'',
	'    function launchHome(request) {',
	'        var id = root.getHomeAppId();',
	'        request = request || {};',
	'        root.__homeLaunch = (id !== root.stockHomeAppId) ? request : null;',
	'        root.__launchApp(id, request);',
	'    }',
	'',
	'    function __launchApp(id, request) {',
	'        var payload = {"id": id};',
	'        for (var key in request)',
	'            payload[key] = request[key];',
	'        homeLaunchService.callService(payload);',
	'    }',
	'',
	'    Service {',
	'        id: homeLaunchService',
	'        appId: root.appId',
	'        service: "com.webos.applicationManager"',
	'        method: "launch"',
	'        onResponse: {',
	'            var request = root.__homeLaunch;',
	'            root.__homeLaunch = null;',
	'            var reply;',
	'            try {',
	'                reply = JSON.parse(payload);',
	'            } catch (e) {',
	'                return;',
	'            }',
	'            if (request && reply.returnValue === false) {',
	'                console.warn("Home app launch failed, launching " + root.stockHomeAppId + ": " + payload);',
	'                root.__launchApp(root.stockHomeAppId, request);',
	'            }',
	'        }',
	'    }',
	''
].join('\n');

function Abort(m) { this.message = m; }
function abort(m) { throw new Abort(m); }

function count(re, t) {
	var g = new RegExp(re.source, re.flags.indexOf('g') < 0 ? re.flags + 'g' : re.flags);
	return (t.match(g) || []).length;
}

// exactly one match of re in t, or abort(what)
function one(t, re, what) {
	var n = count(re, t);
	if (n !== 1) abort(what + ': anchor matched ' + n + ' times, expected 1');
	return re.exec(t);
}

// index of the brace that closes the one at i (no string/comment awareness; guarded by balance()).
function closeOf(t, i) {
	var d = 0;
	for (var k = i; k < t.length; k++) {
		if (t[k] === '{') d++;
		else if (t[k] === '}' && --d === 0) return k;
	}
	return -1;
}

function balance(t) {
	function n(c) { return t.split(c).length - 1; }
	return [n('{') - n('}'), n('(') - n(')'), n('[') - n(']')].join(',');
}

function insertAt(t, i, s) { return t.slice(0, i) + s + t.slice(i); }

// ------------------------------------------------------------------------------------------
// Tier 1

function patchSettings(t) {
	if (/\b(getHomeAppId|isHomeApp|launchHome|stockHomeAppId|redirectHomeLaunch)\b/.test(t)) abort('Settings.qml already has home helpers');
	one(t, /^pragma Singleton$/m, 'Settings.qml singleton');
	one(t, /^    id: root$/m, 'Settings.qml id: root');
	one(t, /^    function subscribe\(type, method, key, returnRaw\) \{$/m, 'Settings.qml subscribe()');
	var imp = one(t, /^import WebOSCompositorBase 1\.0\n/m, 'Settings.qml import');
	if (!/^import WebOSServices /m.test(t)) {
		t = insertAt(t, imp.index + imp[0].length, 'import WebOSServices 1.0\n');
	}
	var end = one(t, /\n\}\n?$/, 'Settings.qml closing brace');
	return insertAt(t, end.index + 1, SETTINGS_BLOCK);
}

function patchKeyFilter(t, needCheckLaunch) {
	if (/\b(getHomeAppId|isHomeApp|launchHome|stockHomeAppId|redirectHomeLaunch)\b/.test(t)) abort('StarfishKeyFilter.qml already routes the home id');
	one(t, /^import WebOSCompositorBase 1\.0$/m, 'StarfishKeyFilter.qml import');
	var g = one(t, /^[ \t]*readonly property QtObject applicationManager: QtObject \{$/m, 'applicationManager group');
	var gOpen = g.index + g[0].length - 1;
	var gEnd = closeOf(t, gOpen);
	if (gEnd < 0) abort('applicationManager group does not close');
	var reL = /^([ \t]*)readonly property var launch: function\(appId, params, checkUpdateOnLaunch, autoInstallation, reason\) \{\n/m;
	var reC = /^([ \t]*)readonly property var checkLaunch:[ \t]+function\(appId, params, checkUpdateOnLaunch, autoInstallation\) \{\n/m;
	var l = one(t, reL, 'applicationManager.launch');
	if (l.index < gOpen || l.index > gEnd) abort('applicationManager.launch is outside its group');
	var c = null;
	var nc = count(reC, t);
	if (nc === 1) {
		c = reC.exec(t);
		if (c.index < gOpen || c.index > gEnd) abort('applicationManager.checkLaunch is outside its group');
	} else if (nc > 1 || needCheckLaunch) {
		abort('applicationManager.checkLaunch matched ' + nc + ' times');
	}
	var ind = l[1];
	// edits from the end backwards so indices stay valid
	var edits = [];
	edits.push([l.index + l[0].length, ind + '    if (Settings.redirectHomeLaunch(appId, params, reason)) return; // ' + MARK + '\n']);
	edits.push([l.index, ind + 'readonly property var getHomeAppId: function() { return Settings.getHomeAppId(); } // ' + MARK + '\n']);
	if (c) {
		edits.push([c.index + c[0].length, c[1] + '    if (appId === Settings.stockHomeAppId) appId = Settings.getHomeAppId(); // ' + MARK + '\n']);
	}
	edits.sort(function (a, b) { return b[0] - a[0]; });
	edits.forEach(function (e) { t = insertAt(t, e[0], e[1]); });
	return t;
}

// returns {text, changed, checkLaunch} ; text === null when the file is left stock
function patchSystemUi(t) {
	if (/\b(getHomeAppId|isHomeApp|launchHome|stockHomeAppId|redirectHomeLaunch)\b/.test(t)) abort('systemUi.js already routes the home id');
	var defRe = /^var HOME_APP_ID = "com\.webos\.app\.home";\n/m;
	var nDef = count(defRe, t);
	var usesAll = count(/\bHOME_APP_ID\b/, t);
	// every literal must be the definition or the first argument of a key filter launch/checkLaunch
	var lits = count(new RegExp(LIT), t);
	var launches = count(new RegExp('applicationManager\\.launch\\(\\s*' + LIT + ',\\s*JSON\\.stringify\\('), t);
	var checks = count(new RegExp('applicationManager\\.checkLaunch\\(\\s*' + LIT + ',\\s*JSON\\.stringify\\('), t);
	if (launches < 1) abort('systemUi.js: no launch of the stock home found');
	if (lits !== nDef + launches + checks) abort('systemUi.js: a stock id is used other than in HOME_APP_ID or a launch (' + lits + ' literals)');
	if (nDef === 0) {
		if (usesAll !== 0) abort('systemUi.js uses HOME_APP_ID without the expected definition');
		return {text: null, checkLaunch: checks > 0};
	}
	if (nDef > 1) abort('systemUi.js defines HOME_APP_ID ' + nDef + ' times');
	var h = one(t, /^function handleHomeKeys\([^)]*\) \{\n/m, 'handleHomeKeys');
	var open = h.index + h[0].length - 2;
	var close = closeOf(t, open);
	if (close < 0) abort('handleHomeKeys does not close');
	var re = /\bHOME_APP_ID\b/g, m, inside = 0, outside = 0;
	var defAt = defRe.exec(t).index + 4;
	while ((m = re.exec(t))) {
		if (m.index === defAt) continue;
		if (m.index > open && m.index < close) inside++; else outside++;
	}
	if (outside) abort('systemUi.js uses HOME_APP_ID outside handleHomeKeys (' + outside + ')');
	// every use must be a comparison of an app id with it; anything else has unknown meaning
	var cmpUses = count(/[A-Za-z_][\w.]*\s*(?:===|!==|==|!=)\s*HOME_APP_ID\b/, t);
	if (cmpUses !== inside) abort('systemUi.js uses HOME_APP_ID other than in a comparison (' + (inside - cmpUses) + ')');
	if (!inside) abort('systemUi.js defines HOME_APP_ID but handleHomeKeys does not use it');
	var line = '    if (applicationManager.getHomeAppId) HOME_APP_ID = applicationManager.getHomeAppId(); // ' + MARK + ': the live home id\n';
	return {text: insertAt(t, h.index + h[0].length, line), checkLaunch: checks > 0};
}

// ------------------------------------------------------------------------------------------
// Tier 2: each returns new text or throws Abort (caller leaves the file stock)

function needImport(t) {
	if (!/^import WebOSCompositorBase 1\.0$/m.test(t)) abort('does not import WebOSCompositorBase');
}

function exactSub(t, re, repl, n, what) {
	var k = count(re, t);
	if (k !== n) abort(what + ' matched ' + k + ' times, expected ' + n);
	return t.replace(new RegExp(re.source, 'g'), repl);
}

var TIER2 = [
	{file: F.nudge, name: 'nudge', fn: function (t) {
		needImport(t);
		if (count(new RegExp(LIT), t) !== 1) abort('expected one stock id');
		return exactSub(t, new RegExp('views\\.fullscreen\\.currentItem\\.appId === ' + LIT),
			'Settings.isHomeApp(views.fullscreen.currentItem.appId)', 1, 'nudge compare');
	}},
	{file: F.baseluna, name: 'currentState', fn: function (t) {
		needImport(t);
		if (count(new RegExp(LIT), t) !== 1) abort('expected one stock id');
		return exactSub(t, new RegExp('views\\.fullscreen\\.currentItem\\.appId === ' + LIT),
			'Settings.isHomeApp(views.fullscreen.currentItem.appId)', 1, 'currentState compare');
	}},
	{file: F.recents, name: 'recents', fn: function (t) {
		needImport(t);
		var spare = new RegExp('([A-Za-z_][\\w.]*)\\.appId !== ' + LIT);
		var nmrm = new RegExp('(supportNMRM \\? )' + LIT + '( :)');
		var ns = count(spare, t), nn = count(nmrm, t);
		if (count(new RegExp(LIT), t) !== ns + nn || ns + nn === 0) abort('stock id in an unexpected form');
		// spare the chosen home as well as the stock one (sam brings the stock home back anyway)
		t = t.replace(new RegExp(spare.source, 'g'), function (all, x) {
			return x + '.appId !== "' + STOCK + '" && !Settings.isHomeApp(' + x + '.appId)';
		});
		if (nn > 0) {
			if (nn !== 1) abort('expected one recents NMRM default');
			// Older recents excludes its last input instead of naming LG Home at the kill site.
			// Keep that input and its stock default, and explicitly spare both homes there.
			t = exactSub(t, /([A-Za-z_][\w.]*)\.appId !== __lastInputApp/,
				function (all, x) { return all + ' && ' + x + '.appId !== "' + STOCK + '" && !Settings.isHomeApp(' + x + '.appId)'; },
				1, 'recents last-input exclusion');
		}
		return t;
	}},
	{file: F.fullscreen, name: 'lastInputApp', fn: function (t) {
		needImport(t);
		if (count(new RegExp(LIT), t) !== 1) abort('expected one stock id');
		return exactSub(t, new RegExp('(supportNMRM \\? )' + LIT + '( :)'), '$1Settings.getHomeAppId()$2', 1, 'NMRM default');
	}}
];

// ------------------------------------------------------------------------------------------

function read(root, rel) {
	try { return fs.readFileSync(path.join(root, rel), 'latin1'); } catch (e) { return null; }
}

function osMajor(root) {
	var t = read(root, 'etc/os-release') || read(root, 'usr/lib/os-release');
	var m = t && /^VERSION_ID="?(\d+)\./m.exec(t);
	return m ? Number(m[1]) : null;
}

function plan(root, opt) {
	var major = osMajor(root);
	if (!opt.skip && (major === null || major < 6 || major > 9)) abort('webOS ' + major + ' is outside 6 to 9');
	var out = [], skipped = [];
	var s = read(root, F.settings), k = read(root, F.keyfilter), u = read(root, F.systemui);
	if (s === null || k === null || u === null) abort('a tier-1 file is missing');
	var su = patchSystemUi(u);
	var kk = patchKeyFilter(k, su.checkLaunch);
	var ss = patchSettings(s);
	out.push({path: F.settings, stock: s, text: ss});
	out.push({path: F.keyfilter, stock: k, text: kk});
	if (su.text !== null) out.push({path: F.systemui, stock: u, text: su.text});
	if (opt.tier2) {
		TIER2.forEach(function (r) {
			var t = read(root, r.file);
			if (t === null) return;
			if (!new RegExp(LIT).test(t)) return;
			try {
				var patched = r.fn(t);
				if (patched === t || balance(patched) !== balance(t)) abort('unchanged output or bracket mismatch');
				out.push({path: r.file, stock: t, text: patched, tier: 2});
			} catch (e) {
				if (!(e instanceof Abort)) throw e;
				skipped.push(r.file + ': ' + e.message);
			}
		});
	}
	out.forEach(function (o) {
		if (o.text === o.stock) abort(o.path + ' unchanged');
		if (balance(o.text) !== balance(o.stock)) abort(o.path + ': brackets no longer balance as in stock');
		if (/\.qml$/.test(o.path) && o.path !== F.settings && /\bSettings\./.test(o.text) && !/^import WebOSCompositorBase /m.test(o.text)) {
			abort(o.path + ' uses Settings without importing WebOSCompositorBase');
		}
		o.stockSha = crypto.createHash('sha256').update(Buffer.from(o.stock, 'latin1')).digest('hex');
	});
	if (ss.split(PLACEHOLDER).length !== 2) abort('placeholder not once');
	return {targets: out, skipped: skipped};
}

function mkdirp(d) { if (!fs.existsSync(d)) { mkdirp(path.dirname(d)); fs.mkdirSync(d); } }

// check and generate share the same plan; a refusal writes no staged files.
function main(argv) {
	var cmd = argv[0], opt = {root: '/', tier2: true};
	for (var i = 1; i < argv.length; i++) {
		if (argv[i] === '--skip-version-gate') opt.skip = true; // offline negative controls only
		else if (/^--(root|out|manifest)$/.test(argv[i]) && i + 1 < argv.length) opt[argv[i].slice(2)] = argv[++i];
		else { process.stderr.write('bad argument ' + argv[i] + '\n'); return 1; }
	}
	if (['check', 'generate'].indexOf(cmd) < 0 || (cmd === 'generate' && !opt.out)) {
		process.stderr.write('usage: homechooser-keyfilter-gen.js check|generate [--root DIR] [--out DIR] [--manifest FILE]\n');
		return 1;
	}
	try {
		var p = plan(opt.root, opt);
		if (cmd === 'generate') {
			p.targets.forEach(function (o) {
				var d = path.join(opt.out, o.path);
				mkdirp(path.dirname(d));
				fs.writeFileSync(d, Buffer.from(o.text, 'latin1'));
			});
			if (opt.manifest) fs.writeFileSync(opt.manifest, JSON.stringify({
				files: p.targets.map(function (o) { return {path: '/' + o.path, sha256: o.stockSha, diff: 'minimal'}; }),
				skipped: p.skipped
			}, null, 2) + '\n');
		}
		process.stdout.write('patch=possible\nunpatched-sites=' + p.skipped.length + '\n');
		p.skipped.forEach(function (s) { process.stdout.write('skipped=' + s + '\n'); });
		p.targets.forEach(function (o) { process.stdout.write('file=/' + o.path + '\n'); });
		return 0;
	} catch (e) {
		process.stderr.write((e instanceof Abort ? 'ABORT: ' : 'ERROR: ') + e.message + '\n');
		return e instanceof Abort ? 2 : 1;
	}
}

process.exitCode = main(process.argv.slice(2));
