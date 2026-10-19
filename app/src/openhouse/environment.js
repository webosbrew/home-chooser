// Connects the pieces for one session: reads the release, checks that Homebrew Channel's exec
// works and runs as root, and builds the chooser and (webOS 6 to 9) the key filter backport.
import {createPlatform, errorMessage} from '../platform';
import {createHomeChooser} from '.';
import {createBackport} from '../backport';
import {classifyExecFailure} from './diagnose';

export class FatalError extends Error {
	constructor(kind, detail) {
		super(detail);
		this.name = 'FatalError';
		this.kind = kind; // 'denied' | 'unreachable' | 'notRoot' | 'failed'
		this.detail = detail;
	}
}

export const loadEnvironment = async (platform = createPlatform()) => {
	let release;
	try {
		release = await platform.osRelease();
	} catch (e) {
		throw new FatalError(classifyExecFailure(errorMessage(e)), errorMessage(e));
	}
	let uid;
	try {
		uid = await platform.rootUid();
	} catch (e) {
		throw new FatalError(classifyExecFailure(errorMessage(e)), errorMessage(e));
	}
	if (uid !== 0) throw new FatalError('notRoot', `exec runs as uid ${uid}`);
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
