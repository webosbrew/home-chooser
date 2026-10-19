import {useCallback, useEffect, useState} from 'react';
import Button from '@enact/moonstone/Button';
import Icon from '@enact/moonstone/Icon';
import SlotItem from '@enact/moonstone/SlotItem';
import Spotlight from '@enact/spotlight';
import SpotlightContainerDecorator from '@enact/spotlight/SpotlightContainerDecorator';
import Cancelable from '@enact/ui/Cancelable';

import css from './Home.module.less';

export const AppIcon = ({src, stock}) => (
	src ?
		<img className={css.icon} src={src} alt="" /> :
		<Icon className={css.icon}>{stock ? 'home' : 'plug'}</Icon>
);

// One row of the home list. The app id shows as small text on the focused row only.
export const HomeItem = ({name, appId, icon, stock, current, disabled, spotlightId, onChoose}) => {
	const [focused, setFocused] = useState(false);
	const handleClick = useCallback(() => onChoose(appId), [onChoose, appId]);
	const handleFocus = useCallback(() => setFocused(true), []);
	const handleBlur = useCallback(() => setFocused(false), []);
	return (
		<SlotItem
			disabled={disabled}
			onClick={handleClick}
			onFocus={handleFocus}
			onBlur={handleBlur}
			spotlightId={spotlightId}
			slotBefore={<AppIcon src={icon} stock={stock} />}
			slotAfter={current ? <Icon className={css.check} aria-label="Current">check</Icon> : null}
		>
			<span className={css.name}>{name}</span>
			{focused ? <span className={css.sub}>{appId}</span> : null}
		</SlotItem>
	);
};

const DIALOG_ID = 'openhouse-dialog';

const DialogBox = SpotlightContainerDecorator(
	{enterTo: 'default-element', defaultElement: 'button', spotlightRestrict: 'self-only'},
	({children, ...rest}) => <div {...rest}>{children}</div>
);

// Back closes the dialog before anything behind it sees the key.
const CancelableBox = Cancelable({
	modal: true,
	onCancel: (ev, props) => {
		props.onClose();
		ev.stopPropagation();
	}
}, DialogBox);

// A dialog drawn inside the panel (Moonstone's Dialog and Popup cover the whole screen).
export const ConfirmDialog = ({open, title, children, confirmLabel, onConfirm, onCancel}) => {
	useEffect(() => {
		if (open) Spotlight.focus(DIALOG_ID);
	}, [open]);
	if (!open) return null;
	return (
		<div className={css.scrim}>
			<CancelableBox className={css.dialog} role="dialog" aria-label={title} spotlightId={DIALOG_ID} onClose={onCancel}>
				<h2 className={css.dialogTitle}>{title}</h2>
				<div className={css.dialogBody}>{children}</div>
				<div className={css.buttons}>
					{onConfirm ? <Button onClick={onConfirm}>{confirmLabel}</Button> : null}
					<Button onClick={onCancel}>{onConfirm ? 'Cancel' : 'OK'}</Button>
				</div>
			</CancelableBox>
		</div>
	);
};

export const Toast = ({toast, onDone}) => {
	useEffect(() => {
		if (!toast) return undefined;
		const t = setTimeout(onDone, toast.ok ? 6000 : 10000);
		return () => clearTimeout(t);
	}, [toast, onDone]);
	if (!toast) return null;
	return <div role="status" className={css.toast + (toast.ok ? '' : ' ' + css.fail)}>{toast.text}</div>;
};
