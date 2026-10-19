// The key filter backport for webOS 6 to 9: status, install and remove. Pure over injected
// {rootLuna, exec, writeFile, removeFile, version}. See docs/key-filter-backport.md.
import {errorMessage} from '../platform/errors';
import {shellQuote} from '../platform/quote';
import {createRunner, majorOf, readDefaultApps, writeDefaultApps} from '../openhouse';

export const APP_DIR = '/media/developer/apps/usr/palm/applications/org.webosbrew.openhouse';
export const SCRIPT_PATH = `${APP_DIR}/backport/openhouse-keyfilter`;
export const INIT_PATH = '/var/lib/webosbrew/init.d/openhouse-keyfilter';
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

	const readStatus = async (run) => {
		const installed = (await run.run(`[ -f ${shellQuote(INIT_PATH)} ] && echo yes || echo no`)).stdout.trim() === 'yes';
		const state = parseStatus((await run.run(script('status'))).stdout);
		return Object.assign({installed}, state);
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
		// 2. copy the script into init.d  3. run it once  4. restart sam if step 1 created the key
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

				await run.run(`mkdir -p ${shellQuote('/var/lib/webosbrew/init.d')} && cp -f ${shellQuote(SCRIPT_PATH)} ${shellQuote(INIT_PATH)} && chmod 755 ${shellQuote(INIT_PATH)}`);

				if (onNotice) onNotice('Applying the backport and restarting the compositor; running apps may close.');
				await run.run(`sh ${shellQuote(INIT_PATH)}`);

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

		// Unmount and restart the compositor, then delete the script from init.d. The deletion is
		// attempted even if the unmount failed, so the next boot does not mount it again.
		async remove() {
			const log = [];
			const run = runner(log);
			const res = Object.assign(result(log), {status: null});
			try {
				await run.run(script('stop'));
			} catch (e) {
				fail(res, e);
			}
			try {
				await run.remove(INIT_PATH);
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
