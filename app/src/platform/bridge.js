// SPDX-License-Identifier: Apache-2.0
// The webOS service bridge. `call(uri, payload)` resolves with the parsed reply, whatever its
// `returnValue`; it rejects only when the bridge is missing or the reply is not JSON.

const findBridgeClass = () => {
	const g = typeof window !== 'undefined' ? window : {};
	return g.WebOSServiceBridge || g.PalmServiceBridge || null;
};

export const createBridgeCall = (BridgeClass = findBridgeClass()) => (uri, payload) =>
	new Promise((resolve, reject) => {
		if (!BridgeClass) {
			reject(new Error('No webOS service bridge (WebOSServiceBridge or PalmServiceBridge) in this browser'));
			return;
		}
		const bridge = new BridgeClass();
		bridge.onservicecallback = (message) => {
			try {
				resolve(JSON.parse(message));
			} catch (e) {
				reject(new Error('Unparseable service reply: ' + message));
			} finally {
				if (typeof bridge.cancel === 'function') bridge.cancel();
			}
		};
		try {
			bridge.call(uri, JSON.stringify(payload || {}));
		} catch (e) {
			reject(e);
		}
	});
