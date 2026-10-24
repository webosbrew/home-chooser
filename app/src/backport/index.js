// The key filter backport for webOS 6 to 9: status, install and remove. Pure over injected
// {rootLuna, exec, writeFile, removeFile, version}. See docs/key-filter-backport.md.
import {errorMessage} from '../platform/errors';
import {
	GENERATED_FLAG, KEYFILTER_FLAG, KEYFILTER_SCRIPT, ensureEntryCommand, keyfilterCommand, parsePersistenceStatus,
	persistenceStatusCommand
} from '../homechooser/persistence';
import {createRunner, majorOf, readDefaultApps, writeDefaultApps} from '../homechooser';

export {APP_DIR, INIT_PATH} from '../homechooser/persistence';
export const SCRIPT_PATH = KEYFILTER_SCRIPT;
export const LOG_PATH = '/tmp/homechooser-backport.log';

// The release gate is the major version; which diff set applies is decided by the script, by hash.
export const backportApplies = (version) => {
	const major = majorOf(version);
	return major >= 6 && major <= 9;
};

// Parse the `key=value` lines of `homechooser-keyfilter status`.
export const parseStatus = (text) => {
	const out = {};
	const hashes = [];
	String(text || '').split('\n').forEach((line) => {
		const m = /^([a-z]+)=(.*)$/.exec(line.trim());
		if (m && m[1] === 'hash') hashes.push(m[2]);
		else if (m) out[m[1]] = m[2];
	});
	return {
		dataMissing: out.data === 'missing',
		mounted: out.mounted === 'yes',
		release: out.release && out.release !== 'unknown' ? out.release : null,
		match: out.match && out.match !== 'none' ? out.match : null,
		generated: out.generated || null,
		reason: out.reason || null,
		optin: out.optin === 'yes',
		rebootNeeded: out.reboot === 'needed',
		hashes
	};
};

export const createBackport = ({rootLuna, exec, writeFile, removeFile, version}) => {
	const runner = createRunner({rootLuna, exec, writeFile, removeFile});
	const script = keyfilterCommand;

	// `enabled`: the flag file the boot entry checks; `entry`: the state of the init.d link.
	const readStatus = async (run) => {
		const persistence = parsePersistenceStatus((await run.run(persistenceStatusCommand())).stdout);
		const state = parseStatus((await run.run(script('status'))).stdout);
		return Object.assign({enabled: persistence.keyfilterEnabled, generatedOptIn: persistence.keyfilterGenerated, entry: persistence.entry}, state);
	};

	const result = (log) => ({ok: true, steps: log, warnings: [], errors: []});
	const fail = (res, e) => {
		res.ok = false;
		res.errors.push(errorMessage(e));
		return res;
	};

	return {
		applies: backportApplies(version),

		async setGenerated(enabled) {
			const log = [];
			const run = runner(log);
			const res = result(log);
			try {
				if (enabled) await run.write(GENERATED_FLAG, '');
				else await run.remove(GENERATED_FLAG);
			} catch (e) {
				fail(res, e);
			}
			return res;
		},

		async status() {
			const log = [];
			const res = Object.assign(result(log), {status: null});
			try {
				res.status = await readStatus(runner(log));
			} catch (e) {
				fail(res, e);
			}
			return res;
		},

		// 1. make sure general/defaultApps exists, so the compositor's and sam's subscriptions see later writes
		// 2. enable the backport (flag file) and link the boot entry into init.d
		// Nothing is mounted and nothing restarts: the boot entry mounts the patch on the next boot, and
		// `rebootNeeded` in the result says whether a restart is what finishes the change (the patch is
		// enabled but not mounted).
		async install() {
			const log = [];
			const run = runner(log);
			const res = Object.assign(result(log), {status: null, createdKey: false, rebootNeeded: false});
			try {
				const before = await readStatus(run);
				if (before.dataMissing) throw new Error('The backport files are missing from the app package');
				if (!before.mounted && !before.match && !(before.generated === 'possible' && before.optin && before.generatedOptIn)) {
					throw new Error('No backport set matches this firmware\'s key filter files, so nothing was installed');
				}

				const record = await readDefaultApps(run);
				if (record === null) {
					await writeDefaultApps(run, {});
					res.createdKey = true;
				}

				await run.write(KEYFILTER_FLAG, '');
				await run.run(ensureEntryCommand());

				res.status = await readStatus(run);
				res.rebootNeeded = res.status.rebootNeeded;
			} catch (e) {
				fail(res, e);
			}
			return res;
		},

		// Disable the backport: delete the flag file, so the next boot does not mount it again, and bake
		// the empty home id into the mounted copy (the worker's refresh, which never restarts the
		// compositor). Nothing is unmounted: a mounted patch stays until the next boot, and
		// `rebootNeeded` says so. The init.d link stays: the watcher needs it while a choice is stored.
		async remove() {
			const log = [];
			const run = runner(log);
			const res = Object.assign(result(log), {status: null, rebootNeeded: false});
			try {
				await run.remove(KEYFILTER_FLAG);
			} catch (e) {
				fail(res, e);
			}
			try {
				await run.run(script('refresh'));
			} catch (e) {
				fail(res, e);
			}
			if (res.ok) {
				try {
					res.status = await readStatus(run);
					res.rebootNeeded = res.status.rebootNeeded;
				} catch (e) {
					fail(res, e);
				}
			}
			return res;
		}
	};
};
