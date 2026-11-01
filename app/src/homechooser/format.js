// SPDX-License-Identifier: Apache-2.0
// One-line renderings of the step log for the UI.
export const formatStep = (step) => {
	let what;
	switch (step.kind) {
		case 'luna':
			what = `${step.uri} ${JSON.stringify(step.payload)}`;
			break;
		case 'readFile':
			what = `read ${step.path}`;
			break;
		case 'writeFile':
			what = `write ${step.path}`;
			break;
		case 'removeFile':
			what = `remove ${step.path}`;
			break;
		default:
			what = `run ${step.command}`;
	}
	if (step.ok) {
		const reply = step.kind === 'luna' && step.reply ? ' -> ' + JSON.stringify(step.reply) : '';
		return `OK ${what}${reply}`;
	}
	return `FAILED ${what} -> ${step.error}`;
};

// A stored choice that is not among the installed apps that declare appCategory home: the watcher
// has put the platform back to stock and re-applies the choice when the app returns. Empty
// string when there is nothing to say (no stored choice, or the app is there).
export const describeMissingChoice = (stored, candidates) => {
	if (!stored || (candidates || []).some((c) => c.id === stored)) return '';
	return `Chosen app ${stored} is not installed; stock Home in use. It is applied again when the app is installed.`;
};
