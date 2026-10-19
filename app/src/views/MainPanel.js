import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import BodyText from '@enact/sandstone/BodyText';
import Button from '@enact/sandstone/Button';
import Item from '@enact/sandstone/Item';
import Scroller from '@enact/sandstone/Scroller';
import {Header, Panel} from '@enact/sandstone/Panels';

import {createPlatform, errorMessage} from '../platform';
import {createHomeChooser} from '../openhouse';
import {diagnoseExecFailure} from '../openhouse/diagnose';
import {formatStep} from '../openhouse/format';

const describeChoice = (state) => {
	if (!state) return '';
	const lines = [`Settings record: ${state.recordHome || (state.record ? '(no home entry)' : '(not set)')}`];
	if (state.profile || state.profileError) {
		lines.push(`System profile: ${state.profileHome || state.profileError || '(none)'}`);
	}
	return lines.join('   ');
};

const CandidateItem = ({candidate, current, disabled, onChoose}) => {
	const handleClick = useCallback(() => onChoose(candidate.id), [candidate.id, onChoose]);
	const label = `${candidate.id}${candidate.stockLabel ? ' - ' + candidate.stockLabel : ''}${current ? ' - current' : ''}`;
	return <Item disabled={disabled} onClick={handleClick} label={label}>{candidate.title}</Item>;
};

const MainPanel = (props) => {
	const [release, setRelease] = useState(null);
	const [chooser, setChooser] = useState(null);
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
		const [list, st] = await Promise.all([c.listCandidates(), c.readState()]);
		if (!mounted.current) return;
		setCandidates(list.candidates);
		setState(st.state);
		setSteps((prev) => prev.concat(list.steps, st.steps));
		setProblems(list.errors.concat(st.errors, st.warnings));
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
			return refresh(c);
		}).catch((e) => {
			if (mounted.current) setFatal(diagnoseExecFailure(errorMessage(e)));
		});
	}, [refresh]);

	const finish = useCallback(async (c, res) => {
		setSteps(res.steps);
		setProblems(res.errors.concat(res.warnings));
		setNotice(res.ok ? 'Done.' + (res.needsKeyFilterBackport ? ' On this release the Home key follows the choice only once the key filter backport is installed.' : '') : 'Failed. See below.');
		await refresh(c);
		setBusy(false);
	}, [refresh]);

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

	const reload = useCallback(() => { if (chooser) refresh(chooser); }, [chooser, refresh]);

	const warnRestart = useMemo(() => Boolean(chooser && state && chooser.needsSamRestart(state)), [chooser, state]);
	const current = state && (state.profileHome || state.recordHome);

	return (
		<Panel {...props}>
			<Header title="Open House" subtitle={release ? `webOS TV ${release.join('.')}` : 'Choose the app the Home button opens'} />
			<Scroller>
				{fatal ? <BodyText>{fatal}</BodyText> : null}
				{state ? <BodyText>{describeChoice(state)}</BodyText> : null}
				{warnRestart ? <BodyText>The first change on this release restarts sam, so running apps may close.</BodyText> : null}
				{notice ? <BodyText>{notice}</BodyText> : null}
				{candidates.map((c) => (
					<CandidateItem key={c.id} candidate={c} current={c.id === current} disabled={busy} onChoose={choose} />
				))}
				{chooser && candidates.length === 0 && !busy ? <BodyText>No installed app declares appCategory home.</BodyText> : null}
				{chooser ? <Button disabled={busy} onClick={restore}>Restore stock Home</Button> : null}
				{chooser ? <Button disabled={busy} onClick={reload}>Refresh</Button> : null}
				{problems.map((p, i) => <Item key={'p' + i}>{p}</Item>)}
				{steps.map((s, i) => <Item key={'s' + i}>{formatStep(s)}</Item>)}
			</Scroller>
		</Panel>
	);
};

export default MainPanel;
