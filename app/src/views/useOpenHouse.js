// The session controller behind the screen: loads the environment, keeps the home list and the
// key filter patch state, and runs the apply / restore flows one at a time. Every step of every
// operation goes to console.log with an [OpenHouse] prefix, never to the screen.
import {useCallback, useEffect, useRef, useState} from 'react';

import {errorMessage} from '../platform/errors';
import {STOCK_HOME} from '../openhouse';
import {formatStep} from '../openhouse/format';
import {loadEnvironment} from '../openhouse/environment';
import {clearPending, readPending, writePending} from './pending';

const DONE_TEXT = 'Done. Press Home to try it.';

export const LOG_PREFIX = '[OpenHouse]';

export const logResult = (label, res) => {
	/* eslint-disable no-console */
	(res.steps || []).forEach((s) => console.log(`${LOG_PREFIX} ${label}: ${formatStep(s)}`));
	(res.warnings || []).forEach((w) => console.log(`${LOG_PREFIX} ${label}: warning: ${w}`));
	(res.errors || []).forEach((x) => console.log(`${LOG_PREFIX} ${label}: error: ${x}`));
	/* eslint-enable no-console */
};

// Where the key filter patch stands (webOS 6 to 9 only; null elsewhere):
// 'active' (mounted), 'ready' (a patch set matches this firmware), 'nomatch' (hash mismatch), 'unknown'.
export const patchStateOf = (backport, status) => {
	if (!backport) return null;
	if (!status) return 'unknown';
	if (status.mounted) return 'active';
	return status.match ? 'ready' : 'nomatch';
};

// One plain sentence for the screen; the detail is in the log.
export const plainFailure = (what, errors) => {
	const text = (errors || []).join(' ');
	const silent = /denied method call|does not exist|not running|unknown service|no webos service bridge|ServiceUnknown|timed? ?out/i.test(text);
	return `Couldn't ${what}.` + (silent ? " Homebrew Channel didn't respond." : '');
};

export const useOpenHouse = (load = loadEnvironment) => {
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
		return {home: (state && (state.profileHome || state.recordHome)) || STOCK_HOME};
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
			// a result left by a run the restart cut short: judge it by the actual state, once
			const left = readPending();
			if (left) {
				clearPending();
				const done = state && state.home === left.id;
				// eslint-disable-next-line no-console
				console.log(`${LOG_PREFIX} pending: ${left.id} -> ${state ? state.home : 'unknown'} (${done ? 'done' : 'not applied'})`);
				setToast(done ? {ok: true, text: DONE_TEXT} : {ok: false, text: "Couldn't set the Home app."});
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
					url = await env.readIcon(c.icon);
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
		clearPending(); // the run finished in this window; no restart cut it short
		if (!mounted.current) return;
		setNotice('');
		setToast(outcome);
		setBusy(false);
	}, [env, busy, refreshWith]);

	const restoreWork = async (e) => {
		setNotice('Restoring LG Home...');
		const res = await e.chooser.restoreStock();
		logResult('restore', res);
		if (!res.ok) return {ok: false, text: plainFailure('restore LG Home', res.errors)};
		// restoring stock takes the key filter patch out as well
		if (e.backport && (patch === 'active' || (patchStatus && patchStatus.enabled))) {
			setNotice('Restoring LG Home... The screen will blink once.');
			writePending(STOCK_HOME, 'LG Home');
			const rem = await e.backport.remove();
			logResult('remove patch', rem);
			if (!rem.ok) return {ok: false, text: plainFailure('restore LG Home', rem.errors)};
		}
		return {ok: true, text: DONE_TEXT};
	};

	const choose = useCallback((candidate) => run('set the Home app', async (e) => {
		if (!candidate || candidate.id === STOCK_HOME) return restoreWork(e);
		const needPatch = Boolean(e.backport) && patch !== 'active';
		if (needPatch && patch !== 'ready') return {ok: false, text: "The Home button can't be changed on this TV's software."};
		// The choice is applied first; installing the patch restarts the compositor and may close
		// this window, so nothing of the choice may depend on code that runs after it.
		setNotice(`Setting ${candidate.title} as Home...`);
		const res = await e.chooser.apply(candidate.id);
		logResult('apply', res);
		if (!res.ok) return {ok: false, text: plainFailure('set the Home app', res.errors)};
		if (needPatch) {
			setNotice('Setting up. The screen will blink once.');
			writePending(candidate.id, candidate.title);
			const inst = await e.backport.install();
			logResult('install patch', inst);
			if (!inst.ok) return {ok: false, text: plainFailure('set the Home app', inst.errors)};
		}
		return {ok: true, text: DONE_TEXT};
	}), [run, patch, patchStatus]); // eslint-disable-line react-hooks/exhaustive-deps

	return {
		phase, rootError, hasPatch: Boolean(env && env.backport), patch,
		candidates: candidates.filter((c) => c.id !== STOCK_HOME), currentId, storedId, loadFailed, readFailed, icons,
		busy, notice, toast, choose, dismissToast: () => setToast(null)
	};
};
