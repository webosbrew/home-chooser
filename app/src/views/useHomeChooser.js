// SPDX-License-Identifier: Apache-2.0
// The session controller behind the screen: loads the environment, keeps the home list and the
// Home key hook patch state, and runs the apply / restore flows one at a time. Every step of every
// operation goes to console.log with an [HomeChooser] prefix, never to the screen.
import {useCallback, useEffect, useRef, useState} from 'react';

import {errorMessage} from '../platform/errors';
import {STOCK_HOME} from '../homechooser';
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
	if (status.mounted) return 'active';
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
			e.homeKeyHook ? e.homeKeyHook.status() : null
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
		setToast(outcome);
		setBusy(false);
	}, [env, busy, refreshWith]);

	const restoreWork = async (e) => {
		setNotice('Restoring LG Home...');
		const res = await e.chooser.restoreStock();
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
		if (!candidate || candidate.id === STOCK_HOME) return restoreWork(e);
		// The patch is needed unless it is mounted and enabled (mounted but disabled: a restore was done
		// and no restart yet; the install just sets the flag again).
		const mountedNow = Boolean(patchStatus && patchStatus.mounted);
		const needPatch = Boolean(e.homeKeyHook) && !(mountedNow && patchStatus.enabled);
		if (needPatch && !mountedNow && patch !== 'ready') return {ok: false, text: "The Home button can't be changed on this TV's software."};
		// Apply and subsequent launcher changes take effect without a reboot.
		setNotice(`Setting ${candidate.title} as Home...`);
		const res = await e.chooser.apply(candidate.id);
		logResult('apply', res);
		if (!res.ok) return {ok: false, text: plainFailure('set the Home app', res.errors)};
		if (needPatch) {
			const inst = await e.homeKeyHook.install();
			logResult('install patch', inst);
			if (!inst.ok) return {ok: false, text: plainFailure('set the Home app', inst.errors)};
		}
		return {ok: true, text: DONE_TEXT};
	}), [run, patch, patchStatus]); // eslint-disable-line react-hooks/exhaustive-deps

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
		patchMounted: Boolean(patchStatus && patchStatus.mounted),
		candidates: candidates.filter((c) => c.id !== STOCK_HOME), currentId, storedId, loadFailed, readFailed, icons,
		busy, notice, toast, choose, getUninstallTitle, uninstall, notify, dismissToast: () => setToast(null)
	};
};
