// SPDX-License-Identifier: Apache-2.0
// Plain-language reading of a failed call to Homebrew Channel (elevateService) or to Home Chooser's
// service, for the UI and the log.
export const diagnoseExecFailure = (message) => {
	const text = String(message || '');
	if (/denied method call/i.test(text)) {
		return 'Homebrew Channel refused the elevation call (' + text + '). This is the case on webOS 26 and later with the current Homebrew Channel; Home Chooser needs a Homebrew Channel update that allows calls from an app.';
	}
	if (/does not exist|not running|unknown service|no webos service bridge|ServiceUnknown/i.test(text)) {
		return 'Homebrew Channel is not reachable (' + text + '). Install and start Homebrew Channel on a rooted TV.';
	}
	return 'Elevating the Home Chooser service through Homebrew Channel failed: ' + text;
};

// 'denied' (call refused, webOS 26+), 'unreachable' (no bridge, service missing) or 'failed'.
export const classifyExecFailure = (message) => {
	const text = String(message || '');
	if (/denied method call/i.test(text)) return 'denied';
	if (/does not exist|not running|unknown service|no webos service bridge|ServiceUnknown/i.test(text)) return 'unreachable';
	return 'failed';
};
