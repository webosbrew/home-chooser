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

export const useLaunchParams = (read = readLaunchParams) => {
	const [params, setParams] = useState(() => parseLaunchParams(read()));
	useEffect(() => {
		const relaunch = (event) => setParams(parseLaunchParams(event.detail || read()));
		window.addEventListener('webOSRelaunch', relaunch);
		return () => window.removeEventListener('webOSRelaunch', relaunch);
	}, [read]);
	return params;
};
