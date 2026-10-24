// PalmSystem supplies launchParams as JSON text. A relaunch may carry it in detail or update
// PalmSystem.launchParams. A new object marks each launch, including repeated requests.
import {useEffect, useState} from 'react';

const APP_ID = /^[A-Za-z0-9._-]{1,128}$/;
const TOKEN = /^[a-f0-9]{32}$/;

export const parseLaunchParams = (raw) => {
	let params;
	try {
		params = typeof raw === 'string' ? JSON.parse(raw) : raw;
	} catch (e) {
		return {};
	}
	if (!params || typeof params !== 'object' || Array.isArray(params)) return {};
	if (typeof params.confirmUninstall === 'string' && APP_ID.test(params.confirmUninstall) && typeof params.token === 'string' && TOKEN.test(params.token)) {
		return {confirmUninstall: params.confirmUninstall, token: params.token};
	}
	return typeof params.request === 'string' && APP_ID.test(params.request) ? {request: params.request} : {};
};

export const readLaunchParams = () => window.PalmSystem && window.PalmSystem.launchParams;

// webOS dispatches webOSRelaunch on `document` (bubbles=false, detail {request}); Enact's own
// `on('webOSRelaunch', ...)` listens there too (@enact/core/dispatcher: the default target is
// `document`), as do the stock Enact apps in the firmware. Older releases may use `window`, so
// both are listened to; an event that reaches both (a bubbling one) is handled once.
const RELAUNCH_TARGETS = () => [document, window];

export const useLaunchParams = (read = readLaunchParams) => {
	const [params, setParams] = useState(() => parseLaunchParams(read()));
	useEffect(() => {
		let handled = null;
		const relaunch = (event) => {
			if (event === handled) return;
			handled = event;
			// The request is in the event detail; otherwise it is in the updated launchParams.
			const fromDetail = parseLaunchParams(event && event.detail);
			setParams(Object.keys(fromDetail).length ? fromDetail : parseLaunchParams(read()));
		};
		const targets = RELAUNCH_TARGETS();
		targets.forEach((t) => t.addEventListener('webOSRelaunch', relaunch));
		return () => targets.forEach((t) => t.removeEventListener('webOSRelaunch', relaunch));
	}, [read]);
	return params;
};
