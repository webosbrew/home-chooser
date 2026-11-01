// SPDX-License-Identifier: Apache-2.0
// Example full-screen launcher for Home Chooser: a grid of the installed apps, then the TV's inputs.
// Plain ES5, no build step, so it runs as it is in the web runtime of webOS 5 (Chromium 68) and later.
//
// PENDING DEVICE CONFIRMATION: two choices here are not yet confirmed on a TV. Apps and inputs are
// started with luna://com.webos.applicationManager/launch {id} and, for an input, its params (launchApp below), and icons are read
// with Home Chooser's getIcons rather than as file:// paths from listApps (loadIcons below).
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
	var apps = [];            // the tiles: the ordered apps, then the inputs ({id, title, inputId})
	var focus = 0;            // index into apps; -1 is the banner button
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
		if (!withAction && focus < 0) focus = 0;
	}

	// "source errorCode: errorText" of a HomeChooserError (see homechooser.js).
	function errorDetail(e) {
		var code = e.errorCode === undefined ? '' : ' ' + e.errorCode;
		return (e.source || 'error') + code + ': ' + (e.errorText || e.message || String(e));
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
		var icon = document.createElement('div');
		icon.className = 'placeholder';
		icon.textContent = titleOf(app).charAt(0).toUpperCase();
		var label = document.createElement('span');
		label.textContent = titleOf(app);
		el.appendChild(icon);
		el.appendChild(label);
		return el;
	}

	function showIcon(index, url) {
		var el = $('grid').children[index];
		if (!el || !url) return;
		var img = document.createElement('img');
		img.alt = '';
		img.onerror = function () {  // data the browser cannot decode: back to the placeholder
			if (img.parentNode && apps[index]) img.parentNode.replaceChild(tile(apps[index]).firstChild, img);
		};
		img.src = url;
		el.replaceChild(img, el.firstChild);
	}

	function render() {
		var grid = $('grid');
		grid.textContent = '';
		for (var i = 0; i < apps.length; i++) grid.appendChild(tile(apps[i]));
		if (focus >= apps.length) focus = apps.length - 1;
		if (focus < 0 && $('banner-action').hidden) focus = 0;
		paintFocus();
	}

	function paintFocus() {
		var tiles = $('grid').children;
		for (var i = 0; i < tiles.length; i++) {
			tiles[i].className = i === focus ? 'tile focused' : 'tile';
		}
		$('banner-action').className = focus < 0 ? 'focused' : '';
		if (tiles[focus]) tiles[focus].scrollIntoView({block: 'nearest'});
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

	// Icons in batches of up to 64 per getIcons call, in grid order, only for tiles not cached yet.
	// getIcons reads the files as root, so it works for a jailed launcher too; an entry with an
	// errorCode keeps the placeholder (overBudget is asked again by the next batch).
	function loadIcons(gen) {
		var pending = [];
		for (var i = 0; i < apps.length; i++) {
			if (Object.prototype.hasOwnProperty.call(iconCache, iconKey(apps[i]))) showIcon(i, iconCache[iconKey(apps[i])]);
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
					if (gen === generation) showIcon(batch[n], icon.dataUrl);
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
			if (info.apiVersion !== HC.API_VERSION) {
				banner('This launcher needs Home Chooser API version ' + HC.API_VERSION + '. Update Home Chooser.', false);
				return;
			}
			if (!info.root) {
				banner('Home Chooser has not finished its setup. Open it once.', true);
				return;
			}
			if (!info.permitted) {
				banner('Open Home Chooser once, then reinstall this launcher so it may list your apps.', true);
				return;
			}
			banner(info.isSelectedHome ? null : 'Make this launcher the app the Home button opens?', !info.isSelectedHome);
			// The inputs are optional: an older Home Chooser has no listInputs.
			var inputs = HC.listInputs().then(function (r) { return r.inputs; }, function () { return []; });
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

	function onKey(event) {
		var n = apps.length;
		var cols = columns();
		switch (event.keyCode) {
		case KEY.LEFT:
			if (focus > 0) focus--;
			break;
		case KEY.RIGHT:
			if (focus >= 0 && focus < n - 1) focus++;
			break;
		case KEY.UP:
			if (focus >= cols) focus -= cols;
			else if (focus >= 0 && !$('banner-action').hidden) focus = -1;
			break;
		case KEY.DOWN:
			if (focus < 0) focus = 0;
			else if (focus + cols < n) focus += cols;
			break;
		case KEY.OK:
			if (focus < 0) requestHome();
			else if (apps[focus]) launchApp(apps[focus]);
			break;
		case KEY.BACK:
			// The Home app is the root: there is nothing to go back to and it must not close itself.
			// appinfo.json sets disableBackHistoryAPI so the key arrives here; Back returns to the first app.
			focus = 0;
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
			focus = 0;
			scheduleRefresh();
		});
		document.addEventListener('visibilitychange', function () {
			if (!document.hidden) scheduleRefresh();
		});
		refresh();
	});
}());
