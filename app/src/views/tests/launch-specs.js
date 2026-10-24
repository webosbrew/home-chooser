import {act, renderHook} from '@testing-library/react';

import {useLaunchParams} from '../launch';

describe('useLaunchParams relaunch', () => {
	test('reads the request from the detail of an event on document', () => {
		const read = jest.fn(() => '{}');
		const {result} = renderHook(() => useLaunchParams(read));
		act(() => {
			document.dispatchEvent(new CustomEvent('webOSRelaunch', {detail: {request: 'org.dock.app'}}));
		});
		expect(result.current).toEqual({request: 'org.dock.app'});
	});

	test('falls back to the updated launchParams when the detail has no request', () => {
		let raw = '{}';
		const read = () => raw;
		const {result} = renderHook(() => useLaunchParams(read));
		raw = '{"request":"org.fancy.launcher"}';
		act(() => {
			document.dispatchEvent(new CustomEvent('webOSRelaunch', {detail: {}}));
		});
		expect(result.current).toEqual({request: 'org.fancy.launcher'});
	});

	test('an event that reaches document and window is handled once', () => {
		const read = jest.fn(() => '{"request":"org.fancy.launcher"}');
		renderHook(() => useLaunchParams(read));
		read.mockClear();
		act(() => {
			document.dispatchEvent(new CustomEvent('webOSRelaunch', {bubbles: true}));
		});
		expect(read).toHaveBeenCalledTimes(1);
	});

	test('stops listening on unmount', () => {
		const read = jest.fn(() => '{}');
		const {unmount} = renderHook(() => useLaunchParams(read));
		unmount();
		read.mockClear();
		document.dispatchEvent(new CustomEvent('webOSRelaunch'));
		window.dispatchEvent(new CustomEvent('webOSRelaunch'));
		expect(read).not.toHaveBeenCalled();
	});
});
