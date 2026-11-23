// SPDX-License-Identifier: Apache-2.0
// The Home key hook for webOS 4.0 and 6 to 9: status, install and remove. Pure over injected
// {rootLuna, exec, writeFile, removeFile, version}. See docs/home-key-hook.md.
import {errorMessage} from '../platform/errors';
import {
	HOMEKEY_FLAG, HOMEKEY_SCRIPT, ensureEntryCommand, homekeyCommand, parsePersistenceStatus,
	persistenceStatusCommand
} from '../homechooser/persistence';
import {createRunner, isWebos40, majorOf, readDefaultApps, writeDefaultApps} from '../homechooser';

export {APP_DIR, INIT_PATH} from '../homechooser/persistence';
export const SCRIPT_PATH = HOMEKEY_SCRIPT;
export const LOG_PATH = '/tmp/homechooser-homekey.log';

// The worker checks unique structural anchors on webOS 6-9 and the filter list on 4.0 (4.5 and 5.x use the record).
export const homeKeyHookApplies = (version) => {
	const major = majorOf(version);
	return (major >= 6 && major <= 9) || isWebos40(version);
};

// Parse the `key=value` lines of `homechooser-homekey status`.
export const parseStatus = (text) => {
	const out = {};
	const files = [], skipped = [];
	String(text || '').split('\n').forEach((line) => {
		const m = /^([a-z-]+)=(.*)$/.exec(line.trim());
		if (m && m[1] === 'file') files.push(m[2]);
		else if (m && m[1] === 'skipped') skipped.push(m[2]);
		else if (m) out[m[1]] = m[2];
	});
	return {
		dataMissing: out.data === 'missing',
		active: out.active === 'yes',
		patch: out.patch || null,
		hook: out.hook || null,
		cacheGeneration: out['cache-generation'] || null,
		recovered: out.recovered || null,
		reason: out.reason || null,
		// another app saved keyFilters persistently and the filter is not active (the service's marker)
		override: out.override === 'yes',
		unpatchedSites: Number(out['unpatched-sites'] || 0),
		rebootNeeded: false,
		files, skipped
	};
};

export const createHomeKeyHook = ({rootLuna, exec, writeFile, removeFile, version}) => {
	const runner = createRunner({rootLuna, exec, writeFile, removeFile});
	const script = homekeyCommand;

	// `enabled`: the flag file the boot entry checks; `entry`: the state of the init.d link.
	const readStatus = async (run) => {
		const persistence = parsePersistenceStatus((await run.run(persistenceStatusCommand())).stdout);
		const state = parseStatus((await run.run(script('status'))).stdout);
		return Object.assign({enabled: persistence.homekeyEnabled, entry: persistence.entry}, state);
	};

	const result = (log) => ({ok: true, steps: log, warnings: [], errors: []});
	const fail = (res, e) => {
		res.ok = false;
		res.errors.push(errorMessage(e));
		return res;
	};

	return {
		applies: homeKeyHookApplies(version),

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

		// Validate this firmware, enable boot apply, and reload the filter immediately. `clearOverride`: the owner
		// agreed to clear another app's persistent keyFilters list first (the service never does it unasked).
		// res.persistentOverride: the filter could not be applied because of such a list; res.clearFailed: the
		// clearing did not take (reasons in docs/home-key-hook.md).
		async install({clearOverride = false} = {}) {
			const log = [];
			const run = runner(log);
			const res = Object.assign(result(log), {status: null, createdKey: false, rebootNeeded: false, reason: null, persistentOverride: false, clearFailed: false});
			try {
				await run.run(script('prepare'));
				const before = await readStatus(run);
				if (before.dataMissing) throw new Error('The Home key hook files are missing from the service package');
				if (!before.active && before.patch !== 'possible') {
					throw new Error('The Home key hook refused this firmware: ' + (before.reason || 'unknown reason'));
				}

				// 4.0 has no settings record to create
				if (!isWebos40(version)) {
					const record = await readDefaultApps(run);
					if (record === null) {
						await writeDefaultApps(run, {});
						res.createdKey = true;
					}
				}

				await run.write(HOMEKEY_FLAG, '');
				await run.run(ensureEntryCommand());
				const applied = parseStatus((await run.run(script(clearOverride ? 'applyClear' : 'apply'))).stdout);
				res.reason = applied.reason;
				res.persistentOverride = applied.reason === 'persistent-override';
				res.clearFailed = applied.reason === 'override-not-cleared' || applied.reason === 'override-clear-failed';

				res.status = await readStatus(run);
				res.rebootNeeded = res.status.rebootNeeded;
			} catch (e) {
				fail(res, e);
			}
			return res;
		},

		// Restore the exact stock list through configd before removing generated files.
		async remove() {
			const log = [];
			const run = runner(log);
			const res = Object.assign(result(log), {status: null, rebootNeeded: false});
			try {
				await run.remove(HOMEKEY_FLAG);
				await run.run(script('clear'));
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
