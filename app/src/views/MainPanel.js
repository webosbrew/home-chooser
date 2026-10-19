import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import BodyText from '@enact/sandstone/BodyText';
import Button from '@enact/sandstone/Button';
import Item from '@enact/sandstone/Item';
import Scroller from '@enact/sandstone/Scroller';
import {Header, Panel} from '@enact/sandstone/Panels';

import {createPlatform, errorMessage} from '../platform';
import {createHomeChooser} from '../openhouse';
import {createBackport} from '../backport';
import {diagnoseExecFailure} from '../openhouse/diagnose';
import {describeMissingChoice, formatStep} from '../openhouse/format';

const describeChoice = (state) => {
	if (!state) return '';
	const lines = [`Settings record: ${state.recordHome || (state.record ? '(no home entry)' : '(not set)')}`];
	if (state.profile || state.profileError) {
		lines.push(`System profile: ${state.profileHome || state.profileError || '(none)'}`);
	}
	return lines.join('   ');
};

const describeBackport = (status) => {
	if (!status) return '';
	const parts = [`Enabled: ${status.enabled ? 'yes' : 'no'}`, `Mounted now: ${status.mounted ? 'yes' : 'no'}`];
	if (status.mounted) parts.push(`Set: ${status.release || 'unknown'}`);
	else parts.push(status.match ? `Matching set: ${status.match}` : 'No set matches this firmware');
	return 'Key filter backport. ' + parts.join('   ');
};

const ENTRY_TEXT = {
	ok: 'yes, points at this app',
	other: 'yes, but it points at another install',
	dangling: 'broken (its target is gone)',
	file: 'a regular file, not a link (apply a choice to replace it)',
	none: 'no'
};

const describePersistence = (p) => {
	if (!p) return '';
	const state = p.active === 'active' ? 'running' : (p.active || 'not running');
	return `Stored choice: ${p.stored || '(none)'}   Boot entry link: ${ENTRY_TEXT[p.entry] || p.entry}   Watcher: ${state}`;
};

const CandidateItem = ({candidate, current, disabled, onChoose}) => {
	const handleClick = useCallback(() => onChoose(candidate.id), [candidate.id, onChoose]);
	const label = `${candidate.id}${candidate.stockLabel ? ' - ' + candidate.stockLabel : ''}${current ? ' - current' : ''}`;
	return <Item disabled={disabled} onClick={handleClick} label={label}>{candidate.title}</Item>;
};

const MainPanel = (props) => {
	const [release, setRelease] = useState(null);
	const [chooser, setChooser] = useState(null);
	const [backport, setBackport] = useState(null);
	const [backportStatus, setBackportStatus] = useState(null);
	const [persistence, setPersistence] = useState(null);
	const [candidates, setCandidates] = useState([]);
	const [state, setState] = useState(null);
	const [fatal, setFatal] = useState(null);
	const [busy, setBusy] = useState(false);
	const [notice, setNotice] = useState('');
	const [problems, setProblems] = useState([]);
	const [steps, setSteps] = useState([]);
	const mounted = useRef(true);

	useEffect(() => () => { mounted.current = false; }, []);

	const refresh = useCallback(async (c) => {
		const [list, st, per] = await Promise.all([c.listCandidates(), c.readState(), c.readPersistence()]);
		if (!mounted.current) return;
		setCandidates(list.candidates);
		setState(st.state);
		setPersistence(per.persistence);
		setSteps((prev) => prev.concat(list.steps, st.steps, per.steps));
		setProblems(list.errors.concat(st.errors, st.warnings, per.errors));
	}, []);

	const refreshBackport = useCallback(async (b) => {
		const res = await b.status();
		if (!mounted.current) return;
		setBackportStatus(res.status);
		setSteps((prev) => prev.concat(res.steps));
		setProblems((prev) => prev.concat(res.errors));
	}, []);

	useEffect(() => {
		const platform = createPlatform();
		platform.osRelease().then((parsed) => {
			if (!mounted.current) return;
			setRelease(parsed.version);
			const c = createHomeChooser({
				rootLuna: platform.rootLuna,
				exec: platform.exec,
				writeFile: platform.writeFile,
				removeFile: platform.removeFile,
				version: parsed.version
			});
			setChooser(c);
			const b = createBackport({
				rootLuna: platform.rootLuna,
				exec: platform.exec,
				writeFile: platform.writeFile,
				removeFile: platform.removeFile,
				version: parsed.version
			});
			if (b.applies) setBackport(b);
			return Promise.all([refresh(c), b.applies ? refreshBackport(b) : null]);
		}).catch((e) => {
			if (mounted.current) setFatal(diagnoseExecFailure(errorMessage(e)));
		});
	}, [refresh, refreshBackport]);

	const finish = useCallback(async (c, res) => {
		setSteps(res.steps);
		setProblems(res.errors.concat(res.warnings));
		setNotice(res.ok ? 'Done.' + (res.needsKeyFilterBackport && !(backportStatus && backportStatus.mounted) ? ' On this release the Home key follows the choice only once the key filter backport is installed.' : '') : 'Failed. See below.');
		await refresh(c);
		setBusy(false);
	}, [refresh, backportStatus]);

	const choose = useCallback((id) => {
		if (!chooser || busy) return;
		setBusy(true);
		setNotice(`Making ${id} the Home app...`);
		chooser.apply(id, {onNotice: setNotice}).then((res) => finish(chooser, res));
	}, [chooser, busy, finish]);

	const restore = useCallback(() => {
		if (!chooser || busy) return;
		setBusy(true);
		setNotice('Restoring the stock Home...');
		chooser.restoreStock().then((res) => finish(chooser, res));
	}, [chooser, busy, finish]);

	const reload = useCallback(() => {
		if (chooser) refresh(chooser);
		if (backport) refreshBackport(backport);
	}, [chooser, refresh, backport, refreshBackport]);

	const runBackport = useCallback(async (action) => {
		if (!backport || busy) return;
		setBusy(true);
		const installing = action === 'install';
		setNotice(installing ? 'Installing the key filter backport...' : 'Removing the key filter backport...');
		const res = installing ? await backport.install({onNotice: setNotice}) : await backport.remove();
		if (!mounted.current) return;
		setSteps(res.steps);
		setProblems(res.errors.concat(res.warnings));
		setNotice(res.ok ? 'Done.' : 'Failed. See below.');
		await refreshBackport(backport);
		if (mounted.current) setBusy(false);
	}, [backport, busy, refreshBackport]);

	const installBackport = useCallback(() => runBackport('install'), [runBackport]);
	const removeBackport = useCallback(() => runBackport('remove'), [runBackport]);

	const warnRestart = useMemo(() => Boolean(chooser && state && chooser.needsSamRestart(state)), [chooser, state]);
	const current = state && (state.profileHome || state.recordHome);

	return (
		<Panel {...props}>
			<Header title="Open House" subtitle={release ? `webOS TV ${release.join('.')}` : 'Choose the app the Home button opens'} />
			<Scroller>
				{fatal ? <BodyText>{fatal}</BodyText> : null}
				{state ? <BodyText>{describeChoice(state)}</BodyText> : null}
				{persistence ? <BodyText>{describePersistence(persistence)}</BodyText> : null}
				{persistence && describeMissingChoice(persistence.stored, candidates) ? <BodyText>{describeMissingChoice(persistence.stored, candidates)}</BodyText> : null}
				{persistence && persistence.log.length ? <BodyText>{'Watcher log: ' + persistence.log.join(' | ')}</BodyText> : null}
				{warnRestart ? <BodyText>The first change on this release restarts sam, so running apps may close.</BodyText> : null}
				{notice ? <BodyText>{notice}</BodyText> : null}
				{candidates.map((c) => (
					<CandidateItem key={c.id} candidate={c} current={c.id === current} disabled={busy} onChoose={choose} />
				))}
				{chooser && candidates.length === 0 && !busy ? <BodyText>No installed app declares appCategory home.</BodyText> : null}
				{backportStatus ? <BodyText>{describeBackport(backportStatus)}</BodyText> : null}
				{backport && backportStatus ? <BodyText>Installing or removing the backport restarts the compositor, so running apps may close.</BodyText> : null}
				{backport && backportStatus && !backportStatus.enabled ? <Button disabled={busy} onClick={installBackport}>Install key filter backport</Button> : null}
				{backport && backportStatus && backportStatus.enabled ? <Button disabled={busy} onClick={removeBackport}>Remove key filter backport</Button> : null}
				{chooser ? <Button disabled={busy} onClick={restore}>Restore stock Home</Button> : null}
				{chooser ? <Button disabled={busy} onClick={reload}>Refresh</Button> : null}
				{problems.map((p, i) => <Item key={'p' + i}>{p}</Item>)}
				{steps.map((s, i) => <Item key={'s' + i}>{formatStep(s)}</Item>)}
			</Scroller>
		</Panel>
	);
};

export default MainPanel;
