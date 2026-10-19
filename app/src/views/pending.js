// A marker for the one step that can take the window down: on webOS 6 to 9 the first choice
// restarts the compositor so the patch loads, and that may close this overlay before the result
// is known. The marker says what was being set; the next launch reads the real state, shows the
// result once and clears it.
export const PENDING_KEY = 'openhouse.pending';

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
