import {useCallback, useEffect, useRef, useState} from 'react';
import BodyText from '@enact/moonstone/BodyText';
import Scroller from '@enact/moonstone/Scroller';
import Spinner from '@enact/moonstone/Spinner';
import {Header, Panel} from '@enact/moonstone/Panels';
import Spotlight from '@enact/spotlight';

import {STOCK_HOME} from '../homechooser';
import {ConfirmDialog, HomeItem, Toast} from './parts';
import css from './Home.module.less';

const FATAL_TEXT = {
	unreachable: ['Homebrew Channel is needed', 'Home Chooser needs Homebrew Channel on a rooted TV. Install it, make sure it is running, then open Home Chooser again.'],
	denied: ["Homebrew Channel can't set up root access yet", "Your Homebrew Channel doesn't let apps elevate Home Chooser's service yet. Update Homebrew Channel; Home Chooser needs it."],
	notRoot: ['Home Chooser has no root access', "Home Chooser's service did not start as root. Root the TV and make sure Homebrew Channel has root access, then open Home Chooser again."],
	failed: ["Home Chooser can't reach the TV's services", "Something went wrong while setting up Home Chooser's service. Make sure Homebrew Channel is installed and running, then open Home Chooser again."]
};

// Why the list is disabled; one plain line at the top of the list area.
export const rootReason = (kind) => {
	const [title, body] = FATAL_TEXT[kind] || FATAL_TEXT.failed;
	return title + '. ' + body;
};

// The Home key patch exists on webOS 6 to 9 only (patch is null elsewhere).
export const patchFooter = (patch) => {
	if (patch === null) return null;
	if (patch === 'active') return 'Home key patch: active';
	if (patch === 'nomatch') return "Home key patch: not available for this TV's software";
	return 'Home key patch: not active';
};

const MainPanel = ({model, ...rest}) => {
	const {phase, rootError, candidates, currentId, storedId, loadFailed, readFailed, icons, busy, notice, toast, patch} = model;
	const [pending, setPending] = useState(null); // the item awaiting confirmation

	const known = candidates.some((c) => c.id === currentId);
	const current = currentId === STOCK_HOME || known ? currentId : STOCK_HOME;
	const removed = Boolean(storedId) && storedId !== STOCK_HOME && !candidates.some((c) => c.id === storedId);

	const rowId = (id) => (id === STOCK_HOME ? 'home-stock' : 'home-' + candidates.findIndex((c) => c.id === id));

	// initial focus: the current home
	useEffect(() => {
		if (phase !== 'ready' || rootError) return;
		Spotlight.focus(rowId(current));
	}, [phase]); // eslint-disable-line react-hooks/exhaustive-deps

	// Focus never falls off the list: back on the row that opened a dialog when it closes, and on
	// the (new) current row when a change finishes. Rows stay enabled while busy (the spinner
	// blocks input), because a row that turns disabled under the focus drops it.
	const opener = useRef(null);
	const wasBusy = useRef(false);
	useEffect(() => {
		if (!pending && opener.current !== null && !busy) {
			Spotlight.focus(rowId(opener.current));
			opener.current = null;
		}
	}, [pending]); // eslint-disable-line react-hooks/exhaustive-deps
	useEffect(() => {
		if (wasBusy.current && !busy && !rootError) Spotlight.focus(rowId(current));
		wasBusy.current = busy;
	}, [busy]); // eslint-disable-line react-hooks/exhaustive-deps

	const choose = useCallback((id) => {
		if (busy) return;
		if (id === current) {
			model.notify((id === STOCK_HOME ? 'LG Home' : (candidates.find((c) => c.id === id) || {}).title || id) + ' is already your Home.');
			return;
		}
		opener.current = id;
		setPending(id === STOCK_HOME ? {id, title: 'LG Home'} : {id, title: (candidates.find((c) => c.id === id) || {}).title || id});
	}, [busy, candidates, current, model]);
	const cancel = useCallback(() => setPending(null), []);
	const confirm = useCallback(() => {
		const item = pending;
		setPending(null);
		model.choose(item);
	}, [pending, model]);

	const footer = patchFooter(patch);
	const stock = pending && pending.id === STOCK_HOME;
	const blocked = !stock && model.hasPatch && patch !== 'active' && patch !== 'ready';

	return (
		<Panel {...rest}>
			<Header title="Home Chooser" titleBelow="Choose the app your Home button opens." />
			{phase === 'loading' ? <Spinner>Loading...</Spinner> : (
				<Scroller>
					{rootError ? <BodyText className={css.message}>{rootReason(rootError)}</BodyText> : null}
					<HomeItem
						name="LG Home (stock)" appId={STOCK_HOME} stock current={!rootError && current === STOCK_HOME}
						disabled={Boolean(rootError)} spotlightId="home-stock" onChoose={choose}
					/>
					{candidates.map((c, i) => (
						<HomeItem
							key={c.id} name={c.title} appId={c.id} icon={icons[c.id]} current={c.id === current}
							disabled={Boolean(rootError)} spotlightId={'home-' + i} onChoose={choose}
						/>
					))}
					{removed && !rootError ? <BodyText className={css.note}>{'The Home app you chose was removed; using LG Home.'}</BodyText> : null}
					{loadFailed && !rootError ? <BodyText className={css.note}>{readFailed ? "Couldn't read the installed apps." : "Couldn't read everything from the TV. Try again in a moment."}</BodyText> : null}
					{candidates.length === 0 && !loadFailed && !rootError ? (
						<BodyText className={css.note}>
							{'No Home apps are installed. A Home app is a launcher that declares appCategory "home" in its appinfo.json. LG Home stays in use.'}
						</BodyText>
					) : null}
					<div className={css.footerSpace} />
				</Scroller>
			)}
			{phase === 'ready' && !rootError && footer ? <div className={css.footer} data-testid="footer">{footer}</div> : null}
			{busy ? <Spinner blockClickOn="container">{notice}</Spinner> : null}
			<Toast toast={toast} onDone={model.dismissToast} />
			<ConfirmDialog
				open={Boolean(pending)}
				title={blocked ? "Can't change Home here" : stock ? 'Use LG Home?' : `Use ${pending ? pending.title : ''} as Home?`}
				confirmLabel={stock ? 'Use LG Home' : 'Use as Home'}
				onConfirm={blocked ? null : confirm}
				onCancel={cancel}
			>
				{blocked ? "The Home button can't be changed on this TV's software."
					: stock ? 'The Home button will open the LG Home again.'
						: 'The Home button will open this app.' + (model.hasPatch && patch !== 'active' ? ' The screen will blink once while the TV loads the change.' : '')}
			</ConfirmDialog>
		</Panel>
	);
};

export default MainPanel;
