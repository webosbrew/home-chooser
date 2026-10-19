// The key filter backport for webOS 6 to 9: status, install and remove. Pure over injected
// {rootLuna, exec, writeFile, removeFile, version}. See docs/key-filter-backport.md.
import {errorMessage} from '../platform/errors';
import {shellQuote} from '../platform/quote';
import {
	KEYFILTER_FLAG, KEYFILTER_SCRIPT, STATE_DIR, ensureEntryCommand, parsePersistenceStatus,
	persistenceStatusCommand
} from '../openhouse/persistence';
import {createRunner, majorOf, readDefaultApps, writeDefaultApps} from '../openhouse';

export {APP_DIR, INIT_PATH} from '../openhouse/persistence';
export const SCRIPT_PATH = KEYFILTER_SCRIPT;
export const LOG_PATH = '/tmp/openhouse-backport.log';

// The release gate is the major version; which diff set applies is decided by the script, by hash.
export const backportApplies = (version) => {
	const major = majorOf(version);
	return major >= 6 && major <= 9;
};

// Parse the `key=value` lines of `openhouse-keyfilter status`.
export const parseStatus = (text) => {
	const out = {};
	String(text || '').split('\n').forEach((line) => {
		const m = /^([a-z]+)=(.*)$/.exec(line.trim());
		if (m) out[m[1]] = m[2];
	});
	return {
		dataMissing: out.data === 'missing',
		mounted: out.mounted === 'yes',
		release: out.release && out.release !== 'unknown' ? out.release : null,
		match: out.match && out.match !== 'none' ? out.match : null
	};
};

export const createBackport = ({rootLuna, exec, writeFile, removeFile, version}) => {
	const runner = createRunner({rootLuna, exec, writeFile, removeFile});
	const script = (arg) => `sh ${shellQuote(SCRIPT_PATH)}${arg ? ' ' + arg : ''}`;

	// `enabled`: the flag file the boot entry checks; `entry`: the state of the init.d link.
	const readStatus = async (run) => {
		const persistence = parsePersistenceStatus((await run.run(persistenceStatusCommand())).stdout);
		const state = parseStatus((await run.run(script('status'))).stdout);
		return Object.assign({enabled: persistence.keyfilterEnabled, entry: persistence.entry}, state);
	};

	const result = (log) => ({ok: true, steps: log, warnings: [], errors: []});
	const fail = (res, e) => {
		res.ok = false;
		res.errors.push(errorMessage(e));
		return res;
	};

	return {
		applies: backportApplies(version),

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
		// 3. run the worker once  4. restart sam if step 1 created the key
		async install({onNotice} = {}) {
			const log = [];
			const run = runner(log);
			const res = Object.assign(result(log), {status: null, createdKey: false});
			try {
				const before = await readStatus(run);
				if (before.dataMissing) throw new Error('The backport files are missing from the app package');
				if (!before.mounted && !before.match) {
					throw new Error('No backport set matches this firmware\'s key filter files, so nothing was installed');
				}

				const record = await readDefaultApps(run);
				if (record === null) {
					await writeDefaultApps(run, {});
					res.createdKey = true;
				}

				await run.run(`mkdir -p ${shellQuote(STATE_DIR)} && touch ${shellQuote(KEYFILTER_FLAG)}`);
				await run.run(ensureEntryCommand());

				if (onNotice) onNotice('Applying the backport and restarting the compositor; running apps may close.');
				await run.run(script('apply'));

				if (res.createdKey) {
					if (onNotice) onNotice('Restarting sam so it sees the new setting; running apps may close.');
					await run.run('systemctl restart sam');
				}

				res.status = await readStatus(run);
				if (!res.status.mounted) throw new Error(`The backport did not mount; see ${LOG_PATH}`);
			} catch (e) {
				fail(res, e);
			}
			return res;
		},

		// Disable the backport (delete the flag file, so the next boot does not mount it again), then
		// unmount and restart the compositor. The flag is deleted even if the unmount fails. The
		// init.d link stays: the watcher needs it while a choice is stored.
		async remove() {
			const log = [];
			const run = runner(log);
			const res = Object.assign(result(log), {status: null});
			try {
				await run.remove(KEYFILTER_FLAG);
			} catch (e) {
				fail(res, e);
			}
			try {
				await run.run(script('stop'));
			} catch (e) {
				fail(res, e);
			}
			if (res.ok) {
				try {
					res.status = await readStatus(run);
					if (res.status.mounted) fail(res, new Error('The backport is still mounted'));
				} catch (e) {
					fail(res, e);
				}
			}
			return res;
		}
	};
};
