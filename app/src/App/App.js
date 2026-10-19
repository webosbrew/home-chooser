import {useCallback, useEffect, useState} from 'react';
import kind from '@enact/core/kind';
import MoonstoneDecorator from '@enact/moonstone/MoonstoneDecorator';
import Panels from '@enact/moonstone/Panels';
import Cancelable from '@enact/ui/Cancelable';

import css from '../views/Home.module.less';

import MainPanel from '../views/MainPanel';
import {ConfirmDialog} from '../views/parts';
import {useOpenHouse} from '../views/useOpenHouse';
import {loadEnvironment} from '../openhouse/environment';

// Always close the window (never webOSSystem.hide()): focus goes back to the app underneath.
const closeApp = () => window.close();

// The version of this app, from the appinfo.json beside index.html (empty where unavailable).
const useAppVersion = () => {
	const [version, setVersion] = useState('');
	useEffect(() => {
		if (typeof fetch !== 'function') return;
		fetch('appinfo.json').then((r) => r.json()).then((j) => setVersion(String(j.version || '')), () => {});
	}, []);
	return version;
};

const AppBase = ({load = loadEnvironment, onExit = closeApp, ...rest}) => {
	const model = useOpenHouse(load);
	const appVersion = useAppVersion();
	const [about, setAbout] = useState(false);
	const openAbout = useCallback(() => setAbout(true), []);
	const closeAbout = useCallback(() => setAbout(false), []);
	return (
		<div {...rest} className={css.app}>
			<div className={css.panel}>
				<Panels index={0} onApplicationClose={onExit}>
					<MainPanel model={model} onAbout={openAbout} />
				</Panels>
				<ConfirmDialog open={about} title="About Open House" onCancel={closeAbout}>
					{[
						appVersion ? `Open House ${appVersion}` : 'Open House',
						model.version ? `webOS TV ${model.version.join('.')}` : '',
						model.hasPatch ? `Home key patch active: ${model.patch === 'active' ? 'yes' : 'no'}` : '',
						'Before uninstalling Open House, choose LG Home.'
					].filter(Boolean).map((line) => <div key={line}>{line}</div>)}
				</ConfirmDialog>
			</div>
		</div>
	);
};

// Back: popups are modal cancelables created after this one, so they take it first and close;
// what reaches here is the end of the line (there is only one panel).
const CancelableApp = Cancelable({
	modal: true,
	onCancel: (ev, props) => {
		(props.onExit || closeApp)();
		ev.stopPropagation();
	}
}, AppBase);

const App = kind({
	name: 'App',
	render: (props) => <CancelableApp {...props} />
});

// overlay: Moonstone paints no full-screen background; the window is transparent around the panel.
export default MoonstoneDecorator({overlay: true}, App);
