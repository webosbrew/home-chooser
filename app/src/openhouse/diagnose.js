// Plain-language reading of a failed hbchannel exec call, for the UI.
export const diagnoseExecFailure = (message) => {
	const text = String(message || '');
	if (/denied method call/i.test(text)) {
		return 'Homebrew Channel refused the root exec call (' + text + '). This is the case on webOS 26 and later with the current Homebrew Channel; Open House needs a Homebrew Channel update that allows exec from an app.';
	}
	if (/does not exist|not running|unknown service|no webos service bridge|ServiceUnknown/i.test(text)) {
		return 'Homebrew Channel root exec is not reachable (' + text + '). Install and start Homebrew Channel on a rooted TV.';
	}
	return 'Root exec through Homebrew Channel failed: ' + text;
};
