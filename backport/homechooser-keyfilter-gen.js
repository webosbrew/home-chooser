#!/usr/bin/env node
'use strict';
// Home Chooser key filter backport, generated variant: rewrites the live stock compositor files so
// the Home key follows the chosen home app, on a firmware that has no reviewed diff set.
//
// One definition of the rules, two uses:
//   - on the TV, homechooser-keyfilter runs `generate` as root (webOS 6 to 9, with /usr/bin/node,
//     8.12 or later, which every 6.x to 9.x image ships) when no reviewed set matches and the user
//     has switched the generated patch on; the output goes through the same stage, mount and
//     refresh steps as a reviewed set;
//   - in the lab, `lab-set` turns the same rules into a reviewed set (manifest.json and diffs) for
//     a new firmware, to be read by a person before it is committed under backport/<release>/.
//
//   node homechooser-keyfilter-gen.js check    [--root DIR]
//   node homechooser-keyfilter-gen.js generate [--root DIR] --out DIR [--manifest FILE]
//   node homechooser-keyfilter-gen.js lab-set  [--root DIR] --release NAME --out DIR
//
//   Any command also takes --skip-version-gate, which only the tests use, to prove that the
//   structural checks refuse webOS 5 and 10 without the os-release check; the worker never passes it.
//
//   check     run every rule in memory, write nothing; exit 0 and print `ok files=N` when the rules
//             and all post-conditions hold on the files under DIR (default /)
//   generate  as check, then write the patched files to OUT/<absolute path> and a manifest (path,
//             sha256 of the stock file, sha256 of the generated file) to FILE
//   lab-set   as check, then write OUT/manifest.json and OUT/<path under usr/lib/qml, / as _>.diff
//             (unified diffs against the stock files, applied with `patch -p0` from the root), the
//             layout of backport/<release>/. Needs diff(1); lab use only.
//
// Exit status: 0 done, 2 the rules refuse this firmware (ABORT line on stderr, nothing written),
// 1 usage or I/O error. Written for Node 8.12 (webOS 6): no named groups, lookbehind, dotAll,
// replaceAll or recursive mkdir.
//
// Rules, in short (details in docs/key-filter-backport.md):
//   - targets are discovered: every .qml/.js under usr/lib/qml/{WebOSCompositor,KeyFilters,
//     WebOSCompositorBase/global} that names the stock id (the literal "com.webos.app.home" or
//     HOME_APP_ID), plus Settings.qml and StarfishKeyFilter.qml, which carry the new functions;
//   - comparisons with the stock id become isHomeApp(x); launches of it go through launchHome();
//     values that carry it become getHomeAppId();
//   - Settings.qml gets getHomeAppId, isHomeApp and launchHome, exactly the text of the reviewed
//     sets; StarfishKeyFilter.qml exposes them to the key filter scripts.
// Post-conditions (any failure aborts the whole run, and nothing is mounted): the firmware is
// webOS 6 to 9; no target already routes through isHomeApp/getHomeAppId; every anchor and every
// counted rule fires exactly its expected number of times; no stock literal or HOME_APP_ID is
// left except the one stockHomeAppId in Settings.qml; no stock literal sits in the rest of the
// WebOSCompositorBase tree; files that call Settings.* import WebOSCompositorBase; the brackets
// of every output balance as in the stock file; Settings.qml has its placeholder line once.

var fs = require('fs');
var os = require('os');
var path = require('path');
var crypto = require('crypto');

var BASE = 'usr/lib/qml/';
var SCAN_DIRS = ['WebOSCompositor', 'KeyFilters', 'WebOSCompositorBase/global'];
var OTHER_DIRS = ['WebOSCompositorBase']; // read for stray stock ids only
var LIT = '"com\\.webos\\.app\\.home"';
var LIT_RE = new RegExp(LIT + '|HOME_APP_ID\\b');
var PLACEHOLDER = 'configuredHomeAppId: "@HOMECHOOSER_HOME@"';

var SETTINGS_IMPORT = 'import WebOSServices 1.0';
// The text the reviewed sets add to Settings.qml after subscribe(); identical in every set.
var SETTINGS_BLOCK = [
	"    // Home Chooser: the home app follows general/defaultApps.home, then the stored choice, then the stock launcher.",
	"    readonly property string stockHomeAppId: \"com.webos.app.home\"",
	"    // Set at boot by homechooser-keyfilter from /var/lib/webosbrew/homechooser/home; empty when no choice is stored.",
	"    readonly property string configuredHomeAppId: \"@HOMECHOOSER_HOME@\"",
	"",
	"    function getHomeAppId() {",
	"        var apps = root.subscribe(\"com.webos.settingsservice\", \"getSystemSettings\",",
	"                                  {\"category\": \"general\", \"keys\": [\"defaultApps\"]});",
	"        if (apps && typeof apps.home === \"string\" && apps.home !== \"\")",
	"            return apps.home;",
	"        if (root.configuredHomeAppId !== \"\")",
	"            return root.configuredHomeAppId;",
	"        return root.stockHomeAppId;",
	"    }",
	"",
	"    function isHomeApp(appId) {",
	"        return appId === root.getHomeAppId();",
	"    }",
	"",
	"    // Launch the home app with the extra fields of the stock call (params, reason, ...).",
	"    // When the chosen app is not the stock one and the launch fails, launch the stock one the same way.",
	"    property var __homeLaunch: null",
	"",
	"    function launchHome(request) {",
	"        var id = root.getHomeAppId();",
	"        request = request || {};",
	"        root.__homeLaunch = (id !== root.stockHomeAppId) ? request : null;",
	"        root.__launchApp(id, request);",
	"    }",
	"",
	"    function __launchApp(id, request) {",
	"        var payload = {\"id\": id};",
	"        for (var key in request)",
	"            payload[key] = request[key];",
	"        homeLaunchService.callService(payload);",
	"    }",
	"",
	"    Service {",
	"        id: homeLaunchService",
	"        appId: root.appId",
	"        service: \"com.webos.applicationManager\"",
	"        method: \"launch\"",
	"        onResponse: {",
	"            var request = root.__homeLaunch;",
	"            root.__homeLaunch = null;",
	"            var reply;",
	"            try {",
	"                reply = JSON.parse(payload);",
	"            } catch (e) {",
	"                return;",
	"            }",
	"            if (request && reply.returnValue === false) {",
	"                console.warn(\"Home app launch failed, launching \" + root.stockHomeAppId + \": \" + payload);",
	"                root.__launchApp(root.stockHomeAppId, request);",
	"            }",
	"        }",
	"    }",
	""
].join('\n') + '\n';

var FILE_SETTINGS = BASE + 'WebOSCompositorBase/global/Settings.qml';
var FILE_KEYFILTER = BASE + 'WebOSCompositor/controllers/StarfishKeyFilter.qml';
var FILE_SYSTEMUI = BASE + 'KeyFilters/systemUi.js';
var CORE = [FILE_SETTINGS, FILE_KEYFILTER, FILE_SYSTEMUI];

function Abort(message) {
	this.message = message;
}

function abort(message) {
	throw new Abort(message);
}

// ---------------------------------------------------------------------------------------------
// The rules. applyRules takes a file's relative path and text and returns the new text.
// fire(rel, name, n, expected) records how often a rule fired; an `expected` makes any other
// count an abort. A rule without one may fire any number of times: the residual check in plan()
// is what proves nothing was missed.

function applyRules(rel, text, fire) {
	var base = path.basename(rel);
	var isJs = /\.js$/.test(rel);
	var isc = isJs ? 'global.isHomeApp' : 'Settings.isHomeApp';
	var idc = isJs ? 'global.getHomeAppId()' : 'Settings.getHomeAppId()';
	var t = text;

	function sub(name, re, repl, expected) {
		var k = 0;
		t = t.replace(re, function () {
			k++;
			return typeof repl === 'function' ? repl.apply(null, arguments) : repl;
		});
		fire(rel, name, k, expected);
	}

	function once(name, re, repl) {
		sub(name, re, repl, 1);
	}

	// A. comparisons: x == "com.webos.app.home", x !== HOME_APP_ID
	sub('cmp', new RegExp('([A-Za-z_][\\w.\\[\\]]*)\\s*(===|!==|==|!=)\\s*(?:HOME_APP_ID\\b|' + LIT + ')', 'g'),
		function (all, lhs, op) {
			return (op.charAt(0) === '!' ? '!' : '') + isc + '(' + lhs + ')';
		});

	// B. the key filter launches
	if (base === 'systemUi.js') {
		sub('launch', new RegExp('applicationManager\\.launch\\(\\s*' + LIT + ',\\s*JSON\\.stringify\\((\\{[^;]*?\\})\\)\\);', 'g'),
			function (all, opts) {
				return 'applicationManager.launchHome(' + opts + ');';
			}, 1);
		sub('checkLaunch', new RegExp('(applicationManager\\.checkLaunch\\()' + LIT + ',\\s*', 'g'),
			function (all, head) {
				return head + 'global.getHomeAppId(), ';
			});
		sub('defHOME', /^var HOME_APP_ID = "com\.webos\.app\.home";\n/gm, '');
	}
	if (base === 'appLaunch.js') {
		var head = new RegExp('([ \\t]*)switch \\(targetAppId\\) \\{\\n\\s*case "com\\.webos\\.app\\.home":\\n\\s*applicationManager\\.launch\\("com\\.webos\\.app\\.home",\\n\\s*JSON\\.stringify\\((\\{"activateType": "showOption"\\})\\)\\);\\n\\s*break;\\n\\s*default:\\n');
		var m = head.exec(t);
		var n = 0;
		if (m) {
			var indent = m[1];
			var rest = t.slice(m.index + m[0].length);
			var m2 = /^([\s\S]*?)\n[ \t]*break;\n([ \t]*\}\n[ \t]*return KeyPolicy\.Accepted;)/.exec(rest);
			if (!m2) abort(rel + ': the end of the switch is not where expected');
			t = t.slice(0, m.index) + indent + 'if (global.isHomeApp(targetAppId)) {\n' + indent +
				'    applicationManager.launchHome(' + m[2] + ');\n' + indent + '} else {\n' + m2[1] + '\n' + m2[2] +
				rest.slice(m2[0].length);
			n = 1;
		}
		fire(rel, 'switch', n);
	}

	// H. object values: appId: "com.webos.app.home"
	sub('objval', new RegExp('("?appId"?\\s*:\\s*)' + LIT, 'g'), function (all, lead) {
		return lead + idc;
	});
	// G1. a default: supportNMRM ? "com.webos.app.home" :
	sub('ternary', new RegExp('(supportNMRM \\? )' + LIT + '( :)', 'g'), function (all, a, b) {
		return a + idc + b;
	});

	// I. the home view
	if (base === 'StarfishHomeView.qml') {
		sub('hv-open', /LS\.adhoc\.call\("luna:\/\/com\.webos\.applicationManager", "\/launch", "\{\\"id\\":\\"com\.webos\.app\.home\\"\}"\);/g,
			'Settings.launchHome({});', 1);
		sub('hv-close', new RegExp('(closeWindowByAppId\\()' + LIT, 'g'), function (all, lead) {
			return lead + 'Settings.getHomeAppId()';
		}, 1);
	}
	// G2. the view state controller keeps the home app subscribed
	if (base === 'StarfishViewStateController.qml') {
		once('homeAppId-anchor', /^([ \t]*)(property string lastInputApp:)/gm, function (all, ind, prop) {
			return ind + '// Home Chooser: start the home app subscription with the compositor\n' + ind +
				'property string homeAppId: Settings.getHomeAppId()\n' + ind + prop;
		});
	}
	// E. what the key filter scripts can see
	if (rel === FILE_KEYFILTER) {
		once('kf-global', /^([ \t]*)(readonly property var closeOverlaidItems: viewState\.closeOverlaidItems\n)/gm,
			function (all, ind, line) {
				return ind + line + ind + 'readonly property var getHomeAppId: function() { return Settings.getHomeAppId(); }\n' +
					ind + 'readonly property var isHomeApp: function(appId) { return Settings.isHomeApp(appId); }\n';
			});
		once('kf-am', /(readonly property QtObject applicationManager: QtObject \{\n([ \t]*)\/\/ functions\n)/g,
			function (all, lead, ind) {
				return lead + ind + 'readonly property var launchHome: function(params) {\n' + ind +
					'    Settings.launchHome({"params": params, "checkUpdateOnLaunch": false,\n' + ind +
					'                         "autoInstallation": false, "reason": "hotKey"});\n' + ind + '}\n\n';
			});
	}
	// F. the new functions
	if (rel === FILE_SETTINGS) {
		once('s-import', /^(import WebOSCompositorBase 1\.0\n)/gm, function (all, line) {
			return line + SETTINGS_IMPORT + '\n';
		});
		once('s-block', /(        return systemSettings\.subscribeOnDemand\(type, method, key, returnRaw\);\n    \}\n\n)/g,
			function (all, line) {
				return line + SETTINGS_BLOCK;
			});
	}
	return t;
}

// ---------------------------------------------------------------------------------------------
// Files and checks

function readText(file) {
	return fs.readFileSync(file, 'latin1'); // bytes in, bytes out: the rules are ASCII
}

function sha256(text) {
	return crypto.createHash('sha256').update(Buffer.from(text, 'latin1')).digest('hex');
}

function rel(root, full) {
	return path.relative(root, full).split(path.sep).join('/');
}

function walk(dir, out) {
	var entries;
	try {
		entries = fs.readdirSync(dir);
	} catch (e) {
		return out;
	}
	entries.sort();
	entries.forEach(function (name) {
		var full = path.join(dir, name);
		var st = fs.lstatSync(full);
		if (st.isDirectory()) walk(full, out);
		else if (st.isFile() && /\.(qml|js)$/.test(name)) out.push(full);
	});
	return out;
}

function mkdirp(dir) {
	if (fs.existsSync(dir)) return;
	mkdirp(path.dirname(dir));
	fs.mkdirSync(dir);
}

// opens minus closes of {}, () and [], as one string
function balance(text) {
	function n(c) {
		return text.split(c).length - 1;
	}
	return [n('{') - n('}'), n('(') - n(')'), n('[') - n(']')].join(',');
}

function osMajor(root) {
	var text;
	try {
		text = readText(path.join(root, 'etc/os-release'));
	} catch (e) {
		return null;
	}
	var m = /^VERSION_ID="?(\d+)\./m.exec(text);
	return m ? Number(m[1]) : null;
}

// Run all rules over the tree under `root`. Returns {targets: [{path, stock, text, stockSha, sha}]}
// or throws Abort.
function plan(root, skipVersionGate) {
	var major = osMajor(root);
	if (!skipVersionGate && major === null) abort('cannot read the webOS version from etc/os-release');
	if (!skipVersionGate && (major < 6 || major > 9)) abort('webOS ' + major + ' is outside 6 to 9, where the generated patch applies');

	function fire(file, name, k, expected) {
		if (expected !== undefined && k !== expected) abort(file + ': rule ' + name + ' fired ' + k + ' times, expected ' + expected);
	}

	var targets = [];
	var seen = {};
	SCAN_DIRS.forEach(function (d) {
		walk(path.join(root, BASE, d), []).forEach(function (full) {
			var r = rel(root, full);
			if (seen[r]) return;
			var text = readText(full);
			if (LIT_RE.test(text) || CORE.indexOf(r) >= 0) {
				seen[r] = true;
				targets.push({path: r, stock: text});
			}
		});
	});
	CORE.forEach(function (r) {
		if (!seen[r]) abort(r + ' is missing: this is not the compositor the rules were written for');
	});

	// a stock id in the compositor tree outside the directories the rules read
	OTHER_DIRS.forEach(function (d) {
		walk(path.join(root, BASE, d), []).forEach(function (full) {
			var r = rel(root, full);
			if (seen[r]) return;
			if (LIT_RE.test(readText(full))) abort(r + ' names the stock home id outside the directories the rules cover');
		});
	});

	targets.forEach(function (tg) {
		if (/\b(isHomeApp|getHomeAppId)\b/.test(tg.stock)) {
			abort(tg.path + ' already routes the home id through isHomeApp/getHomeAppId; refusing to patch it twice');
		}
		tg.text = applyRules(tg.path, tg.stock, fire);
		tg.stockSha = sha256(tg.stock);
		tg.sha = sha256(tg.text);
		// no stock id left, except Settings.qml's own stockHomeAppId
		tg.text.split('\n').forEach(function (line) {
			if (LIT_RE.test(line) && line.indexOf('stockHomeAppId') < 0) {
				abort(tg.path + ': a stock home id is left after the rewrite: ' + line.trim().slice(0, 80));
			}
		});
		if (tg.text === tg.stock) abort(tg.path + ' was not changed by any rule');
		if (balance(tg.text) !== balance(tg.stock)) abort(tg.path + ': the brackets no longer balance as in the stock file');
		if (/\.qml$/.test(tg.path) && tg.path !== FILE_SETTINGS && /\bSettings\.(isHomeApp|getHomeAppId|launchHome)\b/.test(tg.text) &&
			!/^import WebOSCompositorBase /m.test(tg.text)) {
			abort(tg.path + ' calls Settings.* but does not import WebOSCompositorBase');
		}
	});

	var settings = targets.filter(function (tg) { return tg.path === FILE_SETTINGS; })[0];
	if (settings.text.split(PLACEHOLDER).length !== 2) abort(FILE_SETTINGS + ': the placeholder line is not there exactly once');
	var stockLines = settings.text.split('\n').filter(function (l) { return LIT_RE.test(l); });
	if (stockLines.length !== 1 || stockLines[0].indexOf('stockHomeAppId') < 0) {
		abort(FILE_SETTINGS + ': the stock id must appear once, as stockHomeAppId');
	}
	targets.sort(function (a, b) { return a.path < b.path ? -1 : a.path > b.path ? 1 : 0; });
	return {targets: targets};
}

function manifestOf(p, release) {
	return JSON.stringify({
		release: release,
		files: p.targets.map(function (tg) {
			return {path: '/' + tg.path, sha256: tg.stockSha, generated: tg.sha, diff: 'generated'};
		})
	}, null, 2) + '\n';
}

function writeTree(p, out) {
	p.targets.forEach(function (tg) {
		var dest = path.join(out, tg.path);
		mkdirp(path.dirname(dest));
		fs.writeFileSync(dest, Buffer.from(tg.text, 'latin1'));
	});
}

function labSet(p, root, release, out) {
	var cp = require('child_process');
	mkdirp(out);
	var tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'hk-gen-'));
	var newFile = path.join(tmp, 'new');
	var files = [];
	p.targets.forEach(function (tg) {
		var diffName = tg.path.slice(BASE.length).split('/').join('_') + '.diff';
		fs.writeFileSync(newFile, Buffer.from(tg.text, 'latin1'));
		var r = cp.spawnSync('diff', ['-u', '--label', tg.path, '--label', tg.path, path.join(root, tg.path), newFile], {maxBuffer: 1 << 26});
		if (r.status !== 1) throw new Error('diff failed for ' + tg.path);
		fs.writeFileSync(path.join(out, diffName), r.stdout);
		files.push({path: '/' + tg.path, sha256: tg.stockSha, diff: diffName});
	});
	fs.unlinkSync(newFile);
	fs.rmdirSync(tmp);
	fs.writeFileSync(path.join(out, 'manifest.json'), JSON.stringify({release: release, files: files}, null, 2) + '\n');
}

var USAGE = 'usage: homechooser-keyfilter-gen.js check|generate|lab-set [--root DIR] [--out DIR] [--manifest FILE] [--release NAME] [--skip-version-gate]\n';

function main(argv) {
	var cmd = argv[0];
	var opt = {root: '/'};
	for (var i = 1; i < argv.length; i += 2) {
		var key = argv[i];
		if (key === '--skip-version-gate') {
			opt.skip = true;
			i--;
			continue;
		}
		if (!/^--(root|out|manifest|release)$/.test(key) || i + 1 >= argv.length) {
			process.stderr.write(USAGE);
			return 1;
		}
		opt[key.slice(2)] = argv[i + 1];
	}
	if (['check', 'generate', 'lab-set'].indexOf(cmd) < 0 || (cmd !== 'check' && !opt.out) || (cmd === 'lab-set' && !opt.release)) {
		process.stderr.write(USAGE);
		return 1;
	}
	var p;
	try {
		p = plan(opt.root, opt.skip);
	} catch (e) {
		if (e instanceof Abort) {
			process.stderr.write('ABORT: ' + e.message + '\n');
			return 2;
		}
		process.stderr.write('ERROR: ' + (e && e.message) + '\n');
		return 1;
	}
	try {
		if (cmd === 'generate') {
			writeTree(p, opt.out);
			if (opt.manifest) fs.writeFileSync(opt.manifest, manifestOf(p, 'generated'));
		} else if (cmd === 'lab-set') {
			labSet(p, opt.root, opt.release, opt.out);
		}
	} catch (e) {
		process.stderr.write('ERROR: ' + (e && e.message) + '\n');
		return 1;
	}
	process.stdout.write('ok files=' + p.targets.length + '\n');
	return 0;
}

process.exitCode = main(process.argv.slice(2));
