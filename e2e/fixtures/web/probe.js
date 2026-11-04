// SPDX-License-Identifier: Apache-2.0
// Only in the --api-check build of the web fixtures (HC_FIXTURE_PERMISSION set; the plain fixtures stay inert).
// Holds listRunning, getForegroundApp and getAppLifeEvents subscriptions through the client, as a launcher page
// does with its own bus permissions, and keeps what arrived in window.__hcProbe for the runner to read through
// the web inspector (hc_e2e/page_probe.py).  ES5.
(function () {
	'use strict';
	var T0 = Date.now();
	var probe = window.__hcProbe = {started: T0, hasClient: !!window.HomeChooser, subs: {}};
	function slim(method, reply) {
		var out = {t: Date.now() - T0, apiVersion: reply.apiVersion, subscribed: reply.subscribed};
		if (method === 'listRunning') {
			out.ids = (reply.running || []).map(function (x) { return x.id; });
		} else if (method === 'getForegroundApp') {
			out.appId = reply.appId;
		} else {
			out.event = reply.event;
			out.appId = reply.appId;
		}
		return out;
	}
	function hold(method) {
		var rec = probe.subs[method] = {replies: [], error: null};
		window.HomeChooser.subscribe(method, {}, function (reply) {
			rec.replies.push(slim(method, reply));
		}, function (e) {
			rec.error = {source: e.source, errorCode: e.errorCode, errorText: e.errorText || e.message || String(e)};
		});
	}
	if (probe.hasClient) {
		['listRunning', 'getForegroundApp', 'getAppLifeEvents'].forEach(hold);
	}
}());
