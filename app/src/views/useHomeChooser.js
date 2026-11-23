// SPDX-License-Identifier: Apache-2.0
// The session controller behind the screen: loads the environment, keeps the home list and the
// Home key hook patch state, and runs the apply / restore flows one at a time. Every step of every
// operation goes to console.log with an [HomeChooser] prefix, never to the screen.
import {useCallback, useEffect, useRef, useState} from 'react';

import {errorMessage} from '../platform/errors';
import {STOCK_FULLHOME, STOCK_HOME, isChoosableStock} from '../homechooser';
import {formatStep} from '../homechooser/format';
import {loadEnvironment} from '../homechooser/environment';
import {clearPending, readPending} from './pending';

export const LOG_PREFIX = '[HomeChooser]';

const DONE_TEXT = 'Done. Press Home to try it.';
export const logResult = (label, res) => {
	/* eslint-disable no-console */
	(res.steps || []).forEach((s) => console.log(`${LOG_PREFIX} ${label}: ${formatStep(s)}`));
	(res.warnings || []).forEach((w) => console.log(`${LOG_PREFIX} ${label}: warning: ${w}`));
	(res.errors || []).forEach((x) => console.log(`${LOG_PREFIX} ${label}: error: ${x}`));
	/* eslint-enable no-console */
};

// The live filter is selected, available for immediate apply, or refused.
export const patchStateOf = (homeKeyHook, status) => {
	if (!homeKeyHook) return null;
	if (!status) return 'unknown';
	if (status.active) return 'active';
	// Retain recognition of an old stale status while validating it during installation.
	return status.patch === 'possible' || status.hook === 'stale' ? 'ready' : 'nomatch';
};

// What a marker left by an earlier window means now. The marker is only written after the apply
// went through, and the apply stores the choice first, so a marker that belongs to a live apply
// has the stored choice equal to its id (empty for a restore). When the stored choice is something
// else, the marker is from an earlier install (WAM's localStorage outlives an uninstall, the
// uninstall cleanup removed the choice) or was overtaken by a later choice: it says nothing about
// this launch, so it is dropped without a toast. 'done', 'failed' or 'stale'.
export const judgePending = (left, state) => {
	if (state && state.stored !== null && state.stored !== undefined) {
		const mine = isChoosableStock(left.id) ? state.stored === '' : state.stored === left.id;
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
	const [overridePrompt, setOverridePrompt] = useState(null); // {id, title}: another app holds the Home button list
	const mounted = useRef(true);
	const iconsAsked = useRef({});

	useEffect(() => {
		mounted.current = true;
		return () => { mounted.current = false; };
	}, []);

	const readInto = async (e, quiet) => {
		const [list, st, per, patchRes] = await Promise.all([
			e.chooser.listCandidates(), e.chooser.readState(), e.chooser.readPersistence(),
			e.homeKeyHook ? e.homeKeyHook.status() : null
		]);
		[['candidates', list], ['state', st], ['persistence', per], ['patch', patchRes]].forEach(([l, r]) => r && logResult(l, r));
		if (!mounted.current) return;
		// A refresh in the background (the panel came back to the front) changes nothing when the read
		// did not work: the screen keeps what it shows.
		if (quiet && (!list.ok || !st.ok)) return null;
		setReadFailed(false);
		setLoadFailed(!list.ok || !st.ok);
		setCandidates(list.candidates);
		const state = st.state;
		// Where fullhome is installed (4.5, 5.x) there are two stock homes, and no home in the record says which
		// one is in use: nothing is shown as current then, rather than a guess.
		const twoStock = list.candidates.some((c) => c.id === STOCK_FULLHOME);
		setCurrentId((state && (state.profileHome || state.recordHome)) || (twoStock ? null : STOCK_HOME));
		setStoredId(per.persistence ? per.persistence.stored : '');
		setPatchStatus(patchRes ? patchRes.status : null);
		const status = patchRes ? patchRes.status : null;
		return {
			candidates: list.candidates,
			override: Boolean(status && status.override && !status.active),
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

	// The panel comes back to the front (webOSRelaunch, or the page turns visible): read again, so an
	// app installed or removed meanwhile shows. Never while an operation runs (it reads again when it
	// ends), one read at a time, and a failed read leaves the screen as it is.
	const busyRef = useRef(false);
	busyRef.current = busy;
	useEffect(() => {
		if (!env) return undefined;
		let reading = false;
		let again = false;
		const refresh = async () => {
			if (busyRef.current) return;
			if (reading) { again = true; return; }
			reading = true;
			try {
				do {
					again = false;
					try {
						await readInto(env, true);
					} catch (err) {
						// eslint-disable-next-line no-console
						console.log(`${LOG_PREFIX} refresh on return: ${errorMessage(err)}`);
					}
				} while (again && mounted.current && !busyRef.current);
			} finally {
				reading = false;
			}
		};
		const onVisible = () => { if (document.visibilityState !== 'hidden') refresh(); };
		const targets = [document, window];
		targets.forEach((t) => t.addEventListener('webOSRelaunch', refresh));
		document.addEventListener('visibilitychange', onVisible);
		return () => {
			targets.forEach((t) => t.removeEventListener('webOSRelaunch', refresh));
			document.removeEventListener('visibilitychange', onVisible);
		};
	}, [env]); // eslint-disable-line react-hooks/exhaustive-deps

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
			// the service met another app's persistent keyFilters list (at boot or while running): ask once per start
			const stored = state && state.stored;
			if (state && state.override && stored && !isChoosableStock(stored)) {
				const known = (state.candidates || []).find((c) => c.id === stored);
				setOverridePrompt({id: stored, title: (known && known.title) || stored});
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

	const patch = env ? patchStateOf(env.homeKeyHook, patchStatus) : null;

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
		if (!mounted.current) return;
		setNotice('');
		if (outcome.prompt) setOverridePrompt(outcome.prompt);
		else setToast(outcome);
		setBusy(false);
	}, [env, busy, refreshWith]);

	const restoreWork = async (e, stock, title) => {
		setNotice(`Restoring ${title || 'LG Home'}...`);
		const res = await e.chooser.restoreStock(stock);
		logResult('restore', res);
		if (!res.ok) return {ok: false, text: plainFailure('restore LG Home', res.errors)};
		// restoring stock takes the Home key hook patch out as well
		// (configd restores stock before generated files are removed)
		if (e.homeKeyHook && (patch === 'active' || (patchStatus && patchStatus.enabled))) {
			const rem = await e.homeKeyHook.remove();
			logResult('remove patch', rem);
			if (!rem.ok) return {ok: false, text: plainFailure('restore LG Home', rem.errors)};
		}
		return {ok: true, text: DONE_TEXT};
	};

	const choose = useCallback((candidate) => run('set the Home app', async (e) => {
		if (!candidate || isChoosableStock(candidate.id)) return restoreWork(e, candidate && candidate.id, candidate && candidate.title);
		// Enable the boot flag and select the live filter when either is missing.
		const activeNow = Boolean(patchStatus && patchStatus.active);
		const needPatch = Boolean(e.homeKeyHook) && !(activeNow && patchStatus.enabled);
		if (needPatch && !activeNow && patch !== 'ready') return {ok: false, text: "The Home button can't be changed on this TV's software."};
		// Apply and subsequent launcher changes take effect without a reboot.
		setNotice(`Setting ${candidate.title} as Home...`);
		const res = await e.chooser.apply(candidate.id);
		logResult('apply', res);
		if (!res.ok) return {ok: false, text: plainFailure('set the Home app', res.errors)};
		if (needPatch) {
			const inst = await e.homeKeyHook.install();
			logResult('install patch', inst);
			if (!inst.ok) return {ok: false, text: plainFailure('set the Home app', inst.errors)};
			// the filter was refused because another app saved its own list: ask before clearing it
			if (inst.persistentOverride) return {ok: false, prompt: {id: candidate.id, title: candidate.title}};
		}
		return {ok: true, text: DONE_TEXT};
	}), [run, patch, patchStatus]); // eslint-disable-line react-hooks/exhaustive-deps

	// The owner agreed: clear the other app's list and apply the stored choice again.
	const confirmOverride = useCallback((item) => {
		setOverridePrompt(null);
		return run('set the Home app', async (e) => {
			setNotice(`Setting ${item.title} as Home...`);
			const inst = await e.homeKeyHook.install({clearOverride: true});
			logResult('install patch (clear override)', inst);
			if (!inst.ok) return {ok: false, text: plainFailure('set the Home app', inst.errors)};
			if (inst.persistentOverride || inst.clearFailed) return {ok: false, text: "Couldn't clear the other app's Home button setting."};
			return {ok: true, text: DONE_TEXT};
		});
	}, [run]);
	const dismissOverride = useCallback(() => setOverridePrompt(null), []);

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
		phase, rootError, hasPatch: Boolean(env && env.homeKeyHook), patch,
		// webOS 4.0: LG Home is the compositor's ribbon, there is no Home app
		ribbonOnly: Boolean(env && env.chooser && env.chooser.info && env.chooser.info.filterOnly),
		patchActive: Boolean(patchStatus && patchStatus.active),
		candidates, currentId, storedId, loadFailed, readFailed, icons,
		overridePrompt, confirmOverride, dismissOverride,
		busy, notice, toast, choose, getUninstallTitle, uninstall, notify, dismissToast: () => setToast(null)
	};
};
