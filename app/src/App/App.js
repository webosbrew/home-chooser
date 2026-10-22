import kind from '@enact/core/kind';
import MoonstoneDecorator from '@enact/moonstone/MoonstoneDecorator';
import Panels from '@enact/moonstone/Panels';
import Cancelable from '@enact/ui/Cancelable';

import css from '../views/Home.module.less';

import MainPanel from '../views/MainPanel';
import {useHomeChooser} from '../views/useHomeChooser';
import {loadEnvironment} from '../homechooser/environment';

// Always close the window (never webOSSystem.hide()): focus goes back to the app underneath.
const closeApp = () => window.close();

const AppBase = ({load = loadEnvironment, onExit = closeApp, ...rest}) => {
	const model = useHomeChooser(load);
	return (
		<div {...rest} className={css.app}>
			<div className={css.panel}>
				<Panels index={0} noCloseButton onApplicationClose={onExit}>
					<MainPanel model={model} />
				</Panels>
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
