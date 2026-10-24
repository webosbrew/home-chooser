// Connects the pieces for one session: makes sure Home Chooser's service runs as root (elevating
// it through Homebrew Channel when it does not), reads the release, and builds the chooser and
// (webOS 6 to 9) the key filter backport.
import {createPlatform, errorMessage, FatalError} from '../platform';
import {createHomeChooser} from '.';
import {createBackport} from '../backport';
import {classifyExecFailure} from './diagnose';
import {startWatcherCommand} from './persistence';

export {FatalError};

export const loadEnvironment = async (platform = createPlatform()) => {
	await platform.ensureRoot();
	try {
		await platform.exec('accessApply');
	} catch (e) {
		// Access setup is retried by the boot entry and watcher; the settings panel still works.
		// eslint-disable-next-line no-console
		console.log(`[HomeChooser] access: ${errorMessage(e)}`);
	}
	try {
		// Starts the watcher if it is not running. Without a stored choice it checks once for a home an
		// earlier Home Chooser left naming a removed app, so a reinstall repairs it (docs/settings-app.md).
		await platform.exec(startWatcherCommand());
	} catch (e) {
		// eslint-disable-next-line no-console
		console.log(`[HomeChooser] watcher: ${errorMessage(e)}`);
	}
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
		readIcon: platform.readIcon,
		rootLuna: platform.rootLuna,
		uninstallConfirmed: platform.uninstallConfirmed,
		rebootPrompt: platform.rebootPrompt,
		rebootConfirmed: platform.rebootConfirmed
	};
};
