// A marker left by earlier versions, for the one step that could take the window down: the first
// choice on webOS 6 to 9 restarted the compositor so the patch loaded. Nothing writes it any more
// (no step restarts the compositor while the TV runs); the startup still reads an old one, shows
// its result once and clears it.
export const PENDING_KEY = 'homechooser.pending';

const store = () => {
	try {
		return window.localStorage || null;
	} catch (e) {
		return null;
	}
};

// target: the app id being chosen (the stock home id for a restore)
export const writePending = (target, title) => {
	const s = store();
	if (!s) return;
	try {
		s.setItem(PENDING_KEY, JSON.stringify({id: target, title: title || target, at: Date.now()}));
	} catch (e) {
		// no storage: the result just will not survive a restart
	}
};

export const readPending = () => {
	const s = store();
	if (!s) return null;
	try {
		const v = JSON.parse(s.getItem(PENDING_KEY));
		return v && typeof v.id === 'string' ? v : null;
	} catch (e) {
		return null;
	}
};

export const clearPending = () => {
	const s = store();
	if (!s) return;
	try {
		s.removeItem(PENDING_KEY);
	} catch (e) {
		// ignore
	}
};
