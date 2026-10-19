// Connects the pieces for one session: makes sure Open House's service runs as root (elevating
// it through Homebrew Channel when it does not), reads the release, and builds the chooser and
// (webOS 6 to 9) the key filter backport.
import {createPlatform, errorMessage, FatalError} from '../platform';
import {createHomeChooser} from '.';
import {createBackport} from '../backport';
import {classifyExecFailure} from './diagnose';

export {FatalError};

export const loadEnvironment = async (platform = createPlatform()) => {
	await platform.ensureRoot();
	let release;
	try {
		release = await platform.osRelease();
	} catch (e) {
		throw new FatalError(classifyExecFailure(errorMessage(e)), errorMessage(e));
	}
	const deps = {
		rootLuna: platform.rootLuna,
		exec: platform.exec,
		writeFile: platform.writeFile,
		removeFile: platform.removeFile,
		version: release.version
	};
	const backport = createBackport(deps);
	return {
		version: release.version,
		chooser: createHomeChooser(deps),
		backport: backport.applies ? backport : null,
		readIcon: platform.readIcon
	};
};
