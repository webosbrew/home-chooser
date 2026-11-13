// SPDX-License-Identifier: Apache-2.0
// Plain-language reading of a failed call to Homebrew Channel (elevateService) or to Home Chooser's
// service, for the UI and the log.
export const diagnoseExecFailure = (message) => {
	const text = String(message || '');
	if (/denied method call/i.test(text)) {
		return 'Homebrew Channel refused the elevation call (' + text + '). On webOS 26 and later, Homebrew Channel older than 0.8.0 does not allow this call from an app.';
	}
	if (/does not exist|not running|unknown service|no webos service bridge|ServiceUnknown/i.test(text)) {
		return 'Homebrew Channel is not reachable (' + text + ').';
	}
	return 'Elevating the Home Chooser service through Homebrew Channel failed: ' + text;
};

// 'denied' (call refused, webOS 26+ with Homebrew Channel before 0.8.0), 'unreachable' (no bridge, service missing) or 'failed'.
export const classifyExecFailure = (message) => {
	const text = String(message || '');
	if (/denied method call/i.test(text)) return 'denied';
	if (/does not exist|not running|unknown service|no webos service bridge|ServiceUnknown/i.test(text)) return 'unreachable';
	return 'failed';
};
