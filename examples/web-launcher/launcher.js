// SPDX-License-Identifier: Apache-2.0
// Example full-screen launcher for Home Chooser: a grid of the installed apps, then the TV's inputs,
// and above it a Recent row of the apps used last, which this launcher tracks itself.
// Plain ES5, no build step, so it runs as it is in the web runtime of webOS 5 (Chromium 68) and later.
(function () {
	'use strict';

	var KEY = {LEFT: 37, UP: 38, RIGHT: 39, DOWN: 40, OK: 13, BACK: 461};

	// The order of the grid: by title, ignoring ASCII case, then by id; strings compare by UTF-16 code
	// unit. The end-to-end runner (demo_order in e2e/homechooser_e2e.py) repeats this to know which
	// app has the focus after a Home press. Change both together.
	function asciiLower(s) {
		return s.replace(/[A-Z]/g, function (c) { return c.toLowerCase(); });
	}

	function titleOf(app) {
		return app.title || app.id;
	}

	function orderApps(apps, selfId) {
		var shown = apps.filter(function (app) {
			return app.id && app.id !== selfId && app.visible !== false;
		});
		return shown.sort(function (a, b) {
			var ta = asciiLower(titleOf(a)), tb = asciiLower(titleOf(b));
			if (ta !== tb) return ta < tb ? -1 : 1;
			return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
		});
	}

	if (typeof module === 'object' && module.exports) {
		module.exports = {orderApps: orderApps};  // for the order test in e2e/test_e2e_logic.py
		return;
	}

	var HC = window.HomeChooser;
	var selfId = HC.ownAppId();
	var apps = [];            // the grid: the ordered apps, then the inputs ({id, title, inputId})
	var recent = [];          // the Recent row: grid tiles of the apps used last, the latest first
	var focus = 0;            // index into recent, then apps (see tiles()); -1 is the banner button
	var focusHome = true;     // put the focus on the first grid tile at the next render
	var iconCache = {};       // app id@version or input:inputId@icon -> data: URL, or null for the placeholder
	var generation = 0;       // bumped on every refresh; an older icon queue stops
	var refreshTimer = null;
	var toastTimer = null;

	function $(id) {
		return document.getElementById(id);
	}

	function toast(text) {
		$('toast').textContent = text;
		$('toast').hidden = false;
		clearTimeout(toastTimer);
		toastTimer = setTimeout(function () { $('toast').hidden = true; }, 4000);
	}

	// A message above the grid; withAction adds the button that asks Home Chooser to make this app Home.
	// detail, in small text below it, is the error as the client reported it, so that a screenshot is
	// enough to tell a refusal of Home Chooser from one of the system.
	function banner(text, withAction, detail) {
		$('banner').hidden = !text;
		$('banner-text').textContent = text || '';
		$('banner-detail').textContent = detail || '';
		$('banner-detail').hidden = !detail;
		$('banner-action').hidden = !withAction;
		if (!withAction && focus < 0) focus = home();
	}

	// "source errorCode: errorText" of a HomeChooserError (see homechooser.js).
	function errorDetail(e) {
		var code = e.errorCode === undefined ? '' : ' ' + e.errorCode;
		return (e.source || 'error') + code + ': ' + (e.errorText || e.message || String(e));
	}

	// ---- the apps used last ----
	// Home Chooser keeps no history (it never caches), so the launcher keeps its own: every app it
	// launches, and every app the foreground subscription reports (also those opened another way, such
	// as a remote button), newest first, in localStorage. Its own id and "" are ignored: under an
	// overlay launcher the platform may report the app beneath or nothing (docs/launcher-api.md, Patterns).
	var RECENT_KEY = 'recentApps', RECENT_KEPT = 8, RECENT_SHOWN = 5;
	var foregroundSub = null;

	function recentIds() {
		try {
			var ids = JSON.parse(window.localStorage.getItem(RECENT_KEY) || '[]');
			return Array.isArray(ids) ? ids : [];
		} catch (e) {
			return [];
		}
	}

	function noteUsed(id) {
		if (!id || id === selfId) return;
		var ids = [id].concat(recentIds().filter(function (x) { return x !== id; })).slice(0, RECENT_KEPT);
		try {
			window.localStorage.setItem(RECENT_KEY, JSON.stringify(ids));
		} catch (e) {
			// no storage: no Recent row
		}
	}

	// The Recent row: grid tiles of the stored ids, skipping apps no longer installed.
	function recentTiles() {
		var out = [];
		recentIds().forEach(function (id) {
			for (var i = 0; i < apps.length && out.length < RECENT_SHOWN; i++) {
				if (apps[i].id === id) {
					out.push(apps[i]);
					return;
				}
			}
		});
		return out;
	}

	// getForegroundApp {subscribe: true} (level 2): the whole reply after every change of the app in front.
	function watchForeground() {
		if (foregroundSub || level < 2) return;
		foregroundSub = HC.subscribe('getForegroundApp', {}, function (reply) {
			noteUsed(reply.appId);
		}, function (e) {
			foregroundSub = null;  // ended: the next refresh subscribes again
			console.warn('getForegroundApp', e.source, e.errorCode, e.errorText);
		});
	}

	function requestHome() {
		HC.requestHome().then(null, function (e) {
			console.error('requestHome', e);
			toast('Could not open Home Chooser.');
		});
	}

	function launchApp(app) {
		var id = app.id, request = {id: id};
		if (app.inputId) request.params = app.params || {};   // an input: listInputs' params, as LG's input picker sends
		HC.lunaCall('luna://com.webos.applicationManager/launch', request).then(function (reply) {
			if (reply.returnValue !== true) {
				console.error('launch', id, reply);
				toast('Could not open ' + id + '.');
			} else {
				noteUsed(id);
			}
		}, function (e) {
			console.error('launch', id, e);
			toast('Could not open ' + id + '.');
		});
	}

	// ---- the grid ----

	function tile(app) {
		var el = document.createElement('div');
		el.className = 'tile';
		el.setAttribute('data-id', app.id);
		el.setAttribute('data-icon', iconKey(app));
		var icon = document.createElement('div');
		icon.className = 'placeholder';
		icon.textContent = titleOf(app).charAt(0).toUpperCase();
		if (app.bgColor || app.iconColor) icon.style.background = app.bgColor || app.iconColor;
		var label = document.createElement('span');
		label.textContent = titleOf(app);
		el.appendChild(icon);
		el.appendChild(label);
		return el;
	}

	// The icon of an app on every tile that shows it (the grid, and the Recent row).
	function showIcon(app, url) {
		if (!url) return;
		var els = tileElements();
		for (var i = 0; i < els.length; i++) {
			if (els[i].getAttribute('data-icon') === iconKey(app)) setIcon(els[i], app, url);
		}
	}

	function setIcon(el, app, url) {
		var img = document.createElement('img');
		img.alt = '';
		img.onerror = function () {  // data the browser cannot decode: back to the placeholder
			if (img.parentNode) img.parentNode.replaceChild(tile(app).firstChild, img);
		};
		img.src = url;
		el.replaceChild(img, el.firstChild);
	}

	// Focus indices: the Recent row's tiles, then the grid's; home() is the first grid tile.
	function tiles() {
		return recent.concat(apps);
	}

	function home() {
		return recent.length;
	}

	function inRecent(index) {
		return index >= 0 && index < recent.length;
	}

	function tileElements() {
		var out = [], rows = [$('recent-row').children, $('grid').children];
		for (var r = 0; r < rows.length; r++) {
			for (var i = 0; i < rows[r].length; i++) out.push(rows[r][i]);
		}
		return out;
	}

	function render() {
		var oldHome = home(), inGrid = focus >= oldHome;
		recent = recentTiles();
		$('recent').hidden = !recent.length;
		$('recent-row').textContent = '';
		for (var r = 0; r < recent.length; r++) $('recent-row').appendChild(tile(recent[r]));
		var grid = $('grid');
		grid.textContent = '';
		for (var i = 0; i < apps.length; i++) grid.appendChild(tile(apps[i]));
		// The first app of the grid has the focus after a start or a Home press, whatever the Recent row
		// holds; otherwise the focus stays on the same grid position.
		if (focusHome) focus = home();
		else if (inGrid) focus += home() - oldHome;
		else if (focus >= recent.length) focus = recent.length - 1;
		focusHome = false;
		if (focus >= tiles().length) focus = tiles().length - 1;
		if (focus < 0 && $('banner-action').hidden) focus = home();
		paintFocus();
	}

	function paintFocus() {
		var els = tileElements();
		for (var i = 0; i < els.length; i++) {
			els[i].className = i === focus ? 'tile focused' : 'tile';
		}
		$('banner-action').className = focus < 0 ? 'focused' : '';
		if (els[focus]) els[focus].scrollIntoView({block: 'nearest'});
	}

	function columns() {
		var tiles = $('grid').children;
		var n = 0;
		while (n < tiles.length && tiles[n].offsetTop === tiles[0].offsetTop) n++;
		return Math.max(n, 1);
	}

	// The icon cache key and the getIcons item of a tile.
	function iconKey(app) {
		return app.inputId ? 'input:' + app.inputId + '@' + (app.icon || '') : app.id + '@' + (app.version || '');
	}

	function iconItem(app) {
		return app.inputId ? {inputId: app.inputId} : {id: app.id};
	}

	// Home Chooser's apiVersion is a feature level that only grows (docs/launcher-api.md "Stability"):
	// level 1 has getAppIcon and no listInputs or getIcons; level 2 has both.
	var level = 1;

	// Level 1: one getAppIcon call per tile, in grid order; an input has none, so it keeps the placeholder.
	function loadIconsOneByOne(gen) {
		var pending = [];
		for (var i = 0; i < apps.length; i++) {
			if (Object.prototype.hasOwnProperty.call(iconCache, iconKey(apps[i]))) showIcon(apps[i], iconCache[iconKey(apps[i])]);
			else if (!apps[i].inputId) pending.push(i);
		}
		function next() {
			if (gen !== generation || !pending.length) return;
			var index = pending.shift();
			HC.getAppIcon({id: apps[index].id}).then(function (r) {
				var url = 'data:' + r.mimeType + ';base64,' + r.data;
				iconCache[iconKey(apps[index])] = url;
				if (gen === generation) showIcon(apps[index], url);
				next();
			}, function () {
				iconCache[iconKey(apps[index])] = null;
				next();
			});
		}
		next();
	}

	// Icons in batches of up to 64 per getIcons call, in grid order, only for tiles not cached yet.
	// getIcons reads the files as root, so it works for a jailed launcher too; an entry with an
	// errorCode keeps the placeholder (overBudget is asked again by the next batch).
	function loadIcons(gen) {
		if (level < 2) return loadIconsOneByOne(gen);
		var pending = [];
		for (var i = 0; i < apps.length; i++) {
			if (Object.prototype.hasOwnProperty.call(iconCache, iconKey(apps[i]))) showIcon(apps[i], iconCache[iconKey(apps[i])]);
			else pending.push(i);
		}
		function next(retries) {
			if (gen !== generation || !pending.length || retries > 3) return;
			var batch = pending.splice(0, 64);
			HC.getIcons({items: batch.map(function (index) { return iconItem(apps[index]); })}).then(function (r) {
				r.icons.forEach(function (icon, n) {
					if (icon.errorCode === 'overBudget') {
						pending.push(batch[n]);
						return;
					}
					iconCache[iconKey(apps[batch[n]])] = icon.dataUrl || null;
					if (gen === generation) showIcon(apps[batch[n]], icon.dataUrl);
				});
				next(r.icons.length && pending.length && r.icons.every(function (i) { return i.errorCode === 'overBudget'; }) ? retries + 1 : 0);
			}, function (e) {
				console.warn('getIcons', e.source, e.errorCode, e.errorText);
			});
		}
		next(0);
	}

	// ---- start and refresh ----

	// docs/launcher-api.md "Check where you stand": getInfo first, then branch.
	function refresh() {
		var gen = ++generation;
		HC.getInfo().then(function (info) {
			if (gen !== generation) return;
			if (!(info.apiVersion >= 1)) {
				banner('This launcher needs a newer Home Chooser. Update it.', false);
				return;
			}
			level = info.apiVersion;
			if (!info.root) {
				banner('Home Chooser has not finished its setup. Open it once.', true);
				return;
			}
			if (!info.permitted) {
				banner('Open Home Chooser once, then reinstall this launcher so it may list your apps.', true);
				return;
			}
			banner(info.isSelectedHome ? null : 'Make this launcher the app the Home button opens?', !info.isSelectedHome);
			watchForeground();
			// The inputs are optional: listInputs is level 2, so a level 1 Home Chooser gets no input tiles.
			var inputs = level < 2 ? Promise.resolve([]) : HC.listInputs().then(function (r) { return r.inputs; }, function () { return []; });
			return Promise.all([HC.listApps(), inputs]).then(function (results) {
				if (gen !== generation) return;
				apps = orderApps(results[0].apps, selfId).concat(results[1].map(function (input) {
					return {id: input.id, title: input.label || input.inputId, inputId: input.inputId, icon: input.icon, params: input.params};
				}));
				render();
				loadIcons(gen);
			});
		}).then(null, function (e) {
			if (gen !== generation) return;
			console.error('Home Chooser', e.source, e.errorCode, e.errorText);
			if (e.source === 'homechooser') {
				banner('Home Chooser refused the app list (' + e.errorCode + ').', e.errorCode === 'notRoot', errorDetail(e));
			} else {
				banner('Home Chooser did not answer: it is not installed or is too old, or the system denied the call.',
					false, errorDetail(e));
			}
		});
	}

	// webOSRelaunch and visibilitychange can arrive together; refresh once.
	function scheduleRefresh() {
		clearTimeout(refreshTimer);
		refreshTimer = setTimeout(refresh, 100);
	}

	// ---- keys ----

	// The Recent row and the grid are two rows of focus: Left and Right stay in a row, Up and Down cross.
	function onKey(event) {
		var n = tiles().length, start = home();
		var cols = columns();
		var banner = !$('banner-action').hidden;
		switch (event.keyCode) {
		case KEY.LEFT:
			if (focus > 0 && focus !== start) focus--;
			break;
		case KEY.RIGHT:
			if (inRecent(focus) ? focus < recent.length - 1 : focus >= 0 && focus < n - 1) focus++;
			break;
		case KEY.UP:
			if (focus - start >= cols) focus -= cols;
			else if (focus >= start && recent.length) focus = Math.min(focus - start, recent.length - 1);
			else if (focus >= 0 && banner) focus = -1;
			break;
		case KEY.DOWN:
			if (focus < 0) focus = 0;
			else if (inRecent(focus)) focus = Math.min(start + focus, start + Math.min(cols, apps.length) - 1, n - 1);
			else if (focus + cols < n) focus += cols;
			break;
		case KEY.OK:
			if (focus < 0) requestHome();
			else if (tiles()[focus]) launchApp(tiles()[focus]);
			break;
		case KEY.BACK:
			// The Home app is the root: there is nothing to go back to and it must not close itself.
			// appinfo.json sets disableBackHistoryAPI so the key arrives here; Back returns to the first app.
			focus = start;
			break;
		default:
			return;
		}
		event.preventDefault();
		paintFocus();
	}

	document.addEventListener('DOMContentLoaded', function () {
		document.addEventListener('keydown', onKey);
		$('banner-action').addEventListener('click', requestHome);
		// Home pressed while this app runs: start again from the first app with a fresh list.
		document.addEventListener('webOSRelaunch', function () {
			focusHome = true;
			scheduleRefresh();
		});
		document.addEventListener('visibilitychange', function () {
			if (!document.hidden) scheduleRefresh();
		});
		refresh();
	});
}());
