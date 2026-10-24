// The session controller behind the screen: loads the environment, keeps the home list and the
// key filter patch state, and runs the apply / restore flows one at a time. Every step of every
// operation goes to console.log with an [HomeChooser] prefix, never to the screen.
import {useCallback, useEffect, useRef, useState} from 'react';

import {errorMessage} from '../platform/errors';
import {STOCK_HOME} from '../homechooser';
import {formatStep} from '../homechooser/format';
import {loadEnvironment} from '../homechooser/environment';
import {clearPending, readPending} from './pending';

export const LOG_PREFIX = '[HomeChooser]';

const DONE_TEXT = 'Done. Press Home to try it.';
const RESTART_ASKED_TEXT = 'Done. Restart the TV to finish.';
const RESTART_FAILED_TEXT = 'Restart the TV to finish.';

// The line above the "Restart now" row while a restart is pending, by reason ('apply' or 'remove').
export const RESTART_NOTE = {
	apply: 'The Home button changes after a restart.',
	remove: 'LG Home is on the Home button now. A restart removes the patch.'
};

// Ask the service for the restart prompt, a system alert (docs/service.md "Restarting the TV"): the
// app has no dialog of its own for it, because an overlay is closed after a stretch without a key
// press and the alert is not. Resolves true when the prompt was raised.
const askRestart = async (e, reason) => {
	try {
		await e.rebootPrompt(reason);
		return true;
	} catch (err) {
		// eslint-disable-next-line no-console
		console.log(`${LOG_PREFIX} reboot prompt: ${errorMessage(err)}`);
		return false;
	}
};

export const logResult = (label, res) => {
	/* eslint-disable no-console */
	(res.steps || []).forEach((s) => console.log(`${LOG_PREFIX} ${label}: ${formatStep(s)}`));
	(res.warnings || []).forEach((w) => console.log(`${LOG_PREFIX} ${label}: warning: ${w}`));
	(res.errors || []).forEach((x) => console.log(`${LOG_PREFIX} ${label}: error: ${x}`));
	/* eslint-enable no-console */
};

// Where the key filter patch stands (webOS 6 to 9 only; null elsewhere):
// 'active' (reviewed, mounted), 'ready' (reviewed, available), 'unreviewed' (offered),
// 'generated' (opted in or mounted), 'nomatch' (refused), 'unknown'.
export const patchStateOf = (backport, status) => {
	if (!backport) return null;
	if (!status) return 'unknown';
	if (status.mounted) return status.release === 'generated' ? 'generated' : 'active';
	if (status.match) return 'ready';
	if (status.generated === 'possible') return status.optin ? 'generated' : 'unreviewed';
	return 'nomatch';
};

// What a marker left by an earlier window means now. The marker is only written after the apply
// went through, and the apply stores the choice first, so a marker that belongs to a live apply
// has the stored choice equal to its id (empty for a restore). When the stored choice is something
// else, the marker is from an earlier install (WAM's localStorage outlives an uninstall, the
// uninstall cleanup removed the choice) or was overtaken by a later choice: it says nothing about
// this launch, so it is dropped without a toast. 'done', 'failed' or 'stale'.
export const judgePending = (left, state) => {
	if (state && state.stored !== null && state.stored !== undefined) {
		const mine = left.id === STOCK_HOME ? state.stored === '' : state.stored === left.id;
		if (!mine) return 'stale';
	}
	return state && state.home === left.id ? 'done' : 'failed';
};

// One plain sentence for the screen; the detail is in the log.
export const plainFailure = (what, errors) => {
	const text = (errors || []).join(' ');
	const silent = /does not exist|not running|unknown service|no webos service bridge|ServiceUnknown|timed? ?out/i.test(text);
	return `Couldn't ${what}.` + (silent ? " The Home Chooser service didn't respond." : '');
};

export const useHomeChooser = (load = loadEnvironment) => {
	const [phase, setPhase] = useState('loading');
	const [rootError, setRootError] = useState(null); // why root is unavailable, or null
	const [env, setEnv] = useState(null);
	const [candidates, setCandidates] = useState([]);
	const [currentId, setCurrentId] = useState(STOCK_HOME);
	const [storedId, setStoredId] = useState('');
	const [patchStatus, setPatchStatus] = useState(null);
	const [loadFailed, setLoadFailed] = useState(false);
	const [readFailed, setReadFailed] = useState(false); // the read itself threw
	const [busy, setBusy] = useState(false);
	const [notice, setNotice] = useState('');
	const [toast, setToast] = useState(null);
	const [icons, setIcons] = useState({});
	const mounted = useRef(true);
	const iconsAsked = useRef({});

	useEffect(() => {
		mounted.current = true;
		return () => { mounted.current = false; };
	}, []);

	const readInto = async (e) => {
		const [list, st, per, patchRes] = await Promise.all([
			e.chooser.listCandidates(), e.chooser.readState(), e.chooser.readPersistence(),
			e.backport ? e.backport.status() : null
		]);
		[['candidates', list], ['state', st], ['persistence', per], ['patch', patchRes]].forEach(([l, r]) => r && logResult(l, r));
		if (!mounted.current) return;
		setReadFailed(false);
		setLoadFailed(!list.ok || !st.ok);
		setCandidates(list.candidates);
		const state = st.state;
		setCurrentId((state && (state.profileHome || state.recordHome)) || STOCK_HOME);
		setStoredId(per.persistence ? per.persistence.stored : '');
		setPatchStatus(patchRes ? patchRes.status : null);
		return {
			home: (state && (state.profileHome || state.recordHome)) || STOCK_HOME,
			stored: per.persistence ? per.persistence.stored : null // null: the stored choice could not be read
		};
	};

	const refreshWith = useCallback(async (e) => {
		try {
			return await readInto(e);
		} catch (err) {
			// eslint-disable-next-line no-console
			console.log(`${LOG_PREFIX} refresh: ${errorMessage(err)}`);
			if (mounted.current) {
				setReadFailed(true);
				setLoadFailed(true);
			}
			return null;
		}
	}, []); // eslint-disable-line react-hooks/exhaustive-deps

	useEffect(() => {
		let cancelled = false;
		Promise.resolve().then(() => load()).then(async (e) => {
			if (cancelled || !mounted.current) return;
			setEnv(e);
			const state = await refreshWith(e);
			if (cancelled || !mounted.current) return;
			// a marker left by an earlier version, whose compositor restart cut the run short: judge it by the actual state, once
			const left = readPending();
			if (left) {
				clearPending();
				const verdict = judgePending(left, state);
				// eslint-disable-next-line no-console
				console.log(`${LOG_PREFIX} pending: ${left.id} -> ${state ? state.home : 'unknown'}, stored '${state && state.stored}' (${verdict})`);
				if (verdict === 'done') setToast({ok: true, text: DONE_TEXT});
				else if (verdict === 'failed') setToast({ok: false, text: "Couldn't set the Home app."});
			}
			setPhase('ready');
		}, (err) => {
			if (cancelled || !mounted.current) return;
			// eslint-disable-next-line no-console
			console.log(`${LOG_PREFIX} startup: ${errorMessage(err)}`);
			// no root: the screen stays, with the list disabled and the reason on top
			setRootError(err.kind || 'failed');
			setPhase('ready');
		});
		return () => { cancelled = true; };
	}, [load, refreshWith]);

	// Icons: one at a time, each read once per session (the platform caches as well).
	useEffect(() => {
		if (!env || !env.readIcon) return undefined;
		let cancelled = false;
		(async () => {
			for (const c of candidates) {
				if (cancelled || !c.icon || iconsAsked.current[c.id] === c.icon) continue;
				iconsAsked.current[c.id] = c.icon;
				let url = null;
				try {
					url = await env.readIcon(c.id, c.icon);
				} catch (e) {
					url = null;
				}
				if (cancelled || !mounted.current) return;
				setIcons((prev) => Object.assign({}, prev, {[c.id]: url}));
			}
		})();
		return () => { cancelled = true; };
	}, [env, candidates]);

	const patch = env ? patchStateOf(env.backport, patchStatus) : null;

	const run = useCallback(async (label, work) => {
		if (!env || busy) return;
		setBusy(true);
		setToast(null);
		let outcome;
		try {
			outcome = await work(env);
		} catch (e) {
			// eslint-disable-next-line no-console
			console.log(`${LOG_PREFIX} ${label}: ${errorMessage(e)}`);
			outcome = {ok: false, text: `Couldn't ${label}.`};
		}
		try {
			await refreshWith(env);
		} catch (e) {
			// the outcome stands; the list may be stale
		}
		clearPending(); // an old marker is moot: the run finished in this window
		// The change is done but needs a TV restart to take effect (outcome.reboot is the reason, 'apply'
		// or 'remove'): the service puts up the system alert, and the screen only says so.
		if (outcome.ok && outcome.reboot) {
			outcome = {ok: true, text: (await askRestart(env, outcome.reboot)) ? RESTART_ASKED_TEXT : RESTART_FAILED_TEXT};
		}
		if (!mounted.current) return;
		setNotice('');
		setToast(outcome);
		setBusy(false);
	}, [env, busy, refreshWith]);

	// A restart is pending (webOS 6 to 9; the worker's status says reboot=needed): the flag file without
	// the patch mounted is an apply, the patch mounted without the flag file is a remove.
	const restartPending = Boolean(patchStatus && patchStatus.rebootNeeded);
	const restartReason = patchStatus && patchStatus.enabled ? 'apply' : 'remove';
	const askReboot = useCallback(async () => {
		if (!env || busy || !restartPending) return;
		const asked = await askRestart(env, restartReason);
		if (!mounted.current) return;
		setToast(asked ? {ok: true, text: 'Check the restart prompt on the screen.'} : {ok: false, text: RESTART_FAILED_TEXT});
		if (!asked) refreshWith(env); // it may have been restarted or settled in the meantime
	}, [env, busy, restartPending, restartReason, refreshWith]);

	const restoreWork = async (e) => {
		setNotice('Restoring LG Home...');
		const res = await e.chooser.restoreStock();
		logResult('restore', res);
		if (!res.ok) return {ok: false, text: plainFailure('restore LG Home', res.errors)};
		// restoring stock takes the key filter patch out as well
		// (the flag goes; the mounted copy stays until the next boot, so a restart finishes it)
		if (e.backport && (patch === 'active' || (patchStatus && patchStatus.enabled))) {
			const rem = await e.backport.remove();
			logResult('remove patch', rem);
			if (!rem.ok) return {ok: false, text: plainFailure('restore LG Home', rem.errors)};
			if (rem.rebootNeeded) return {ok: true, text: DONE_TEXT, reboot: 'remove'};
		}
		return {ok: true, text: DONE_TEXT};
	};

	const choose = useCallback((candidate) => run('set the Home app', async (e) => {
		if (!candidate || candidate.id === STOCK_HOME) return restoreWork(e);
		// The patch is needed unless it is mounted and enabled (mounted but disabled: a restore was done
		// and no restart yet; the install just sets the flag again).
		const mountedNow = Boolean(patchStatus && patchStatus.mounted);
		const needPatch = Boolean(e.backport) && !(mountedNow && patchStatus.enabled);
		if (needPatch && !mountedNow && patch !== 'ready' && patch !== 'generated') return {ok: false, text: "The Home button can't be changed on this TV's software."};
		// Nothing restarts the compositor while the TV runs, so no step here can close this window.
		// Only a patch that is not mounted yet needs a TV restart (the boot entry mounts it), and
		// that restart restarts sam too, so the apply skips its own sam restart then.
		setNotice(`Setting ${candidate.title} as Home...`);
		const res = await e.chooser.apply(candidate.id, {deferSamRestart: needPatch && !mountedNow});
		logResult('apply', res);
		if (!res.ok) return {ok: false, text: plainFailure('set the Home app', res.errors)};
		if (needPatch) {
			const inst = await e.backport.install();
			logResult('install patch', inst);
			if (!inst.ok) return {ok: false, text: plainFailure('set the Home app', inst.errors)};
			if (inst.rebootNeeded) return {ok: true, text: DONE_TEXT, reboot: 'apply'};
		}
		return {ok: true, text: DONE_TEXT};
	}), [run, patch, patchStatus]); // eslint-disable-line react-hooks/exhaustive-deps

	const toggleGenerated = useCallback((enabled) => run('change the generated patch', async (e) => {
		const flag = await e.backport.setGenerated(enabled);
		logResult('generated opt-in', flag);
		let reboot;
		if (!flag.ok) return {ok: false, text: plainFailure('change the generated patch', flag.errors)};
		if (enabled && storedId && storedId !== STOCK_HOME) {
			setNotice('Setting up...');
			const inst = await e.backport.install();
			logResult('install generated patch', inst);
			if (!inst.ok) return {ok: false, text: plainFailure('install the generated patch', inst.errors)};
			reboot = inst.rebootNeeded ? 'apply' : undefined;
		} else if (!enabled) {
			setNotice('Removing the Home key patch...');
			const rem = await e.backport.remove();
			logResult('remove generated patch', rem);
			if (!rem.ok) return {ok: false, text: plainFailure('remove the generated patch', rem.errors)};
			reboot = rem.rebootNeeded ? 'remove' : undefined;
		}
		return {ok: true, reboot, text: enabled ? (storedId ? DONE_TEXT : 'Generated patch enabled. Choose a Home app.') : 'Generated patch disabled. The Home button opens LG Home.'};
	}), [run, storedId]);
	const notify = useCallback((text) => setToast({ok: true, text}), []);
	const getUninstallTitle = useCallback(async (id) => {
		const reply = await env.rootLuna('luna://com.webos.applicationManager/getAppInfo', {id});
		const info = reply.appInfo || reply;
		if (reply.returnValue === false || !info.title) throw new Error('No app title');
		return info.title;
	}, [env]);
	const uninstall = useCallback((item) => run('uninstall the app', async (e) => {
		setNotice(`Uninstalling ${item.title}...`);
		try {
			await e.uninstallConfirmed(item.token);
			return {ok: true, text: `${item.title} was uninstalled.`};
		} catch (err) {
			// eslint-disable-next-line no-console
			console.log(`${LOG_PREFIX} uninstall: ${errorMessage(err)}`);
			return {ok: false, text: plainFailure('uninstall the app', [errorMessage(err)])};
		}
	}), [run]);

	return {
		phase, rootError, hasPatch: Boolean(env && env.backport), patch,
		patchMounted: Boolean(patchStatus && patchStatus.mounted),
		showGenerated: Boolean(patchStatus && (patchStatus.generated === 'possible' || patchStatus.release === 'generated' || patchStatus.generatedOptIn)),
		generatedOptIn: Boolean(patchStatus && patchStatus.generatedOptIn), toggleGenerated,
		candidates: candidates.filter((c) => c.id !== STOCK_HOME), currentId, storedId, loadFailed, readFailed, icons,
		restartPending, restartReason, askReboot,
		busy, notice, toast, choose, getUninstallTitle, uninstall, notify, dismissToast: () => setToast(null)
	};
};
