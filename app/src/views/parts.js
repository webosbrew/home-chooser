import {useCallback, useEffect, useState} from 'react';
import Button from '@enact/moonstone/Button';
import Dialog from '@enact/moonstone/Dialog';
import Icon from '@enact/moonstone/Icon';
import SlotItem from '@enact/moonstone/SlotItem';

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

export const ConfirmDialog = ({open, title, children, confirmLabel, onConfirm, onCancel}) => (
	<Dialog
		open={open}
		onClose={onCancel}
		title={title}
		noAnimation
		buttons={[
			onConfirm ? <Button key="ok" onClick={onConfirm}>{confirmLabel}</Button> : null,
			<Button key="cancel" onClick={onCancel}>{onConfirm ? 'Cancel' : 'OK'}</Button>
		].filter(Boolean)}
	>
		{children}
	</Dialog>
);

export const Toast = ({toast, onDone}) => {
	useEffect(() => {
		if (!toast) return undefined;
		const t = setTimeout(onDone, toast.ok ? 6000 : 10000);
		return () => clearTimeout(t);
	}, [toast, onDone]);
	if (!toast) return null;
	return <div role="status" className={css.toast + (toast.ok ? '' : ' ' + css.fail)}>{toast.text}</div>;
};
