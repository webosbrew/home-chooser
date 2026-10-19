// One-line renderings of the step log for the UI.
export const formatStep = (step) => {
	let what;
	switch (step.kind) {
		case 'luna':
			what = `${step.uri} ${JSON.stringify(step.payload)}`;
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
