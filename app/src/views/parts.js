import {useCallback, useEffect} from 'react';
import Button from '@enact/moonstone/Button';
import Icon from '@enact/moonstone/Icon';
import SlotItem from '@enact/moonstone/SlotItem';
import Spotlight from '@enact/spotlight';
import SpotlightContainerDecorator from '@enact/spotlight/SpotlightContainerDecorator';
import Cancelable from '@enact/ui/Cancelable';

import css from './Home.module.less';

// The icon box is the same size for every row and is there before the image loads.
export const AppIcon = ({src, stock}) => (
	<span className={css.iconBox} data-testid="icon-box">
		{src ?
			<img className={css.icon} src={src} alt="" /> :
			<Icon className={css.fallback}>{stock ? 'home' : 'plug'}</Icon>}
	</span>
);

// One row of the home list. Every row has the same height and structure whatever its state:
// focus changes colour only (Moonstone's own styling), and the check slot is always reserved.
// The name is a single truncated line (Marquee only scrolls it on focus when it overflows).
export const HomeItem = ({name, icon, stock, current, disabled, spotlightId, onChoose, appId}) => {
	const handleClick = useCallback(() => onChoose(appId), [onChoose, appId]);
	return (
		<SlotItem
			className={css.row}
			inline={false}
			autoHide={null}
			marqueeOn="focus"
			disabled={disabled}
			onClick={handleClick}
			spotlightId={spotlightId}
			data-app-id={appId}
			slotBefore={<AppIcon src={icon} stock={stock} />}
			slotAfter={
				<span className={css.checkSlot}>
					<Icon className={current ? css.check : css.checkHidden} aria-label={current ? 'Current' : undefined} aria-hidden={current ? undefined : 'true'}>check</Icon>
				</span>
			}
		>
			{name}
		</SlotItem>
	);
};

const DIALOG_ID = 'homechooser-dialog';

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
export const ConfirmDialog = ({open, title, children, confirmLabel, cancelLabel = 'Cancel', onConfirm, onCancel}) => {
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
					<Button onClick={onCancel}>{onConfirm ? cancelLabel : 'OK'}</Button>
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
