// SPDX-License-Identifier: Apache-2.0
import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';

import App from '../../App';
import {FatalError} from '../../homechooser/environment';
import {PENDING_KEY} from '../pending';
import {patchStateOf} from '../useHomeChooser';
import {parseLaunchParams} from '../launch';

const ok = (extra) => Object.assign({ok: true, steps: [], warnings: [], errors: []}, extra);

const makeEnv = ({version = [11, 0, 0], candidates, currentHome = null, stored = '', homeKeyHook = null, apply, restore} = {}) => {
	const list = candidates || [
		{id: 'com.webos.app.home', title: 'Home', icon: null, stock: true},
		{id: 'org.fancy.launcher', title: 'Fancy Launcher', icon: '/apps/fancy/icon.png'},
		{id: 'org.plain.launcher', title: 'Plain Launcher', icon: '/apps/plain/icon.png'}
	];
	const chooser = {
		listCandidates: jest.fn(async () => ok({candidates: list})),
		readState: jest.fn(async () => ok({state: {record: null, recordHome: currentHome, profile: null, profileHome: null}})),
		readPersistence: jest.fn(async () => ok({persistence: {stored, entry: 'ok', active: '', log: []}})),
		apply: apply || jest.fn(async () => ok()),
		restoreStock: restore || jest.fn(async () => ok())
	};
	return {
		version,
		chooser,
		homeKeyHook,
		rootLuna: jest.fn(async () => ({returnValue: true, appInfo: {title: 'Example App'}})),
		uninstallConfirmed: jest.fn(async () => ({returnValue: true})),
		rebootPrompt: jest.fn(async () => ({returnValue: true, pending: true, confirm: 'alert'})),
		readIcon: jest.fn(async (id, p) => (p.includes('fancy') ? 'data:image/png;base64,AAAA' : null))
	};
};

const mountApp = (env, onExit = jest.fn()) => {
	const load = jest.fn(async () => env);
	render(<App load={load} onExit={onExit} />);
	return {load, onExit};
};

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

describe('Home Chooser screen', () => {
	let log;
	beforeEach(() => { log = jest.spyOn(console, 'log').mockImplementation(() => {}); });
	afterEach(() => { log.mockRestore(); window.localStorage.clear(); });


	describe('launch requests', () => {
		const token = '0123456789abcdef0123456789abcdef';
		const launchApp = (env, params) => render(<App load={async () => env} launch={() => params} onExit={jest.fn()} />);

		test('initial request waits for candidates and uses the existing confirmation', async () => {
			const env = makeEnv();
			let release;
			env.chooser.listCandidates.mockImplementationOnce(() => new Promise((resolve) => {
				release = () => resolve(ok({candidates: [{id: 'org.plain.launcher', title: 'Plain Launcher'}]}));
			}));
			launchApp(env, JSON.stringify({request: 'org.plain.launcher'}));
			await waitFor(() => expect(release).toBeTruthy());
			expect(screen.queryByRole('dialog')).toBeNull();
			await act(async () => release());
			expect(await screen.findByText('Use Plain Launcher as Home?')).toBeTruthy();
			expect(env.chooser.apply).not.toHaveBeenCalled();
			fireEvent.click(screen.getByText('Use as Home'));
			await screen.findByText('Done. Press Home to try it.');
			expect(env.chooser.apply).toHaveBeenCalledWith('org.plain.launcher', {deferSamRestart: false});
		});

		test.each(['com.webos.app.home', 'org.unknown', 'org.nonhome', '../bad'])('ignores stock, unknown or non-home request %s', async (request) => {
			const env = makeEnv();
			launchApp(env, {request});
			await screen.findByText('Plain Launcher');
			expect(screen.queryByRole('dialog')).toBeNull();
			expect(env.chooser.apply).not.toHaveBeenCalled();
		});

		test('request for the current home shows the existing toast', async () => {
			launchApp(makeEnv({currentHome: 'org.plain.launcher'}), {request: 'org.plain.launcher'});
			expect(await screen.findByText('Plain Launcher is already your Home.')).toBeTruthy();
			expect(screen.queryByRole('dialog')).toBeNull();
		});

		// webOS dispatches webOSRelaunch on document (bubbles=false); older releases may use window.
		test.each([['document', document], ['window', window]])('PalmSystem launchParams and webOSRelaunch on %s work repeatedly', async (name, target) => {
			window.PalmSystem = {launchParams: '{"request":"org.plain.launcher"}'};
			mountApp(makeEnv());
			await screen.findByText('Use Plain Launcher as Home?');
			fireEvent.click(screen.getByText('Cancel'));
			window.PalmSystem.launchParams = '{"request":"org.fancy.launcher"}';
			fireEvent(target, new CustomEvent('webOSRelaunch'));
			await screen.findByText('Use Fancy Launcher as Home?');
			fireEvent.click(screen.getByText('Cancel'));
			fireEvent(target, new CustomEvent('webOSRelaunch', {detail: {request: 'org.plain.launcher'}}));
			await screen.findByText('Use Plain Launcher as Home?');
			delete window.PalmSystem;
		});

		test('uninstall asks with the platform title and calls only after confirmation', async () => {
			const env = makeEnv();
			launchApp(env, {confirmUninstall: 'org.example.app', token});
			expect(await screen.findByText('Uninstall Example App?')).toBeTruthy();
			expect(env.rootLuna).toHaveBeenCalledWith('luna://com.webos.applicationManager/getAppInfo', {id: 'org.example.app'});
			expect(env.uninstallConfirmed).not.toHaveBeenCalled();
			fireEvent.click(screen.getByText('Uninstall'));
			expect(await screen.findByText('Example App was uninstalled.')).toBeTruthy();
			expect(env.uninstallConfirmed).toHaveBeenCalledTimes(1);
			expect(env.uninstallConfirmed).toHaveBeenCalledWith(token);
			expect(env.chooser.apply).not.toHaveBeenCalled();
		});

		test('uninstall cancel never calls the service', async () => {
			const env = makeEnv();
			launchApp(env, {confirmUninstall: 'org.example.app', token});
			await screen.findByText('Uninstall Example App?');
			fireEvent.click(screen.getByText('Cancel'));
			await flush();
			expect(screen.queryByRole('dialog')).toBeNull();
			expect(env.uninstallConfirmed).not.toHaveBeenCalled();
		});

		test('uninstall failure is shown without claiming success', async () => {
			const env = makeEnv();
			env.uninstallConfirmed.mockRejectedValue(new Error('expired'));
			launchApp(env, {confirmUninstall: 'org.example.app', token});
			await screen.findByText('Uninstall Example App?');
			fireEvent.click(screen.getByText('Uninstall'));
			expect(await screen.findByText("Couldn't uninstall the app.")).toBeTruthy();
		});

		test('missing title does not show an uninstall button', async () => {
			const env = makeEnv();
			env.rootLuna.mockRejectedValue(new Error('gone'));
			launchApp(env, {confirmUninstall: 'org.example.app', token});
			await screen.findByText("Couldn't read the app to uninstall. Try again from your Home app.");
			expect(screen.queryByRole('dialog')).toBeNull();
			expect(env.uninstallConfirmed).not.toHaveBeenCalled();
		});

		test.each(['{bad', '[]', 'null', '{"request":42}', '{"confirmUninstall":"org.example.app","token":"bad"}'])('invalid launch params %s are ignored', (raw) => {
			expect(parseLaunchParams(raw)).toEqual({});
		});
	});

	test('a read that throws shows the failure line, not an endless empty state', async () => {
		const env = makeEnv();
		env.chooser.listCandidates.mockRejectedValue(new Error('boom'));
		mountApp(env);
		expect(await screen.findByText("Couldn't read the installed apps.")).toBeTruthy();
		expect(screen.queryByText(/No Home apps are installed/)).toBeNull();
		expect(log).toHaveBeenCalledWith(expect.stringContaining('[HomeChooser] refresh: boom'));
		const row = screen.getByText('LG Home (stock)').closest('[data-spotlight-id]');
		expect(row.getAttribute('aria-disabled')).not.toBe('true');
	});

	test.each([
		['unreachable', /needs Homebrew Channel on a rooted TV/],
		['denied', /doesn't let apps elevate Home Chooser's service yet\. Update Homebrew Channel; Home Chooser needs it\./],
		['notRoot', /service did not start as root/],
		['failed', /can't reach the TV's services/]
	])('no root (%s): the reason on top and a disabled list, same layout', async (kind, text) => {
		const onExit = jest.fn();
		render(<App load={() => Promise.reject(new FatalError(kind, 'x'))} onExit={onExit} />);
		expect(await screen.findByText(text)).toBeTruthy();
		expect(screen.getByText('Choose the app your Home button opens.')).toBeTruthy();
		const row = screen.getByText('LG Home (stock)').closest('[data-spotlight-id]');
		expect(row.getAttribute('aria-disabled')).toBe('true');
		expect(row.querySelectorAll('[data-testid="icon-box"]')).toHaveLength(1);
		expect(screen.queryByLabelText('Current')).toBeNull();
		expect(screen.queryByTestId('footer')).toBeNull();
		fireEvent.click(screen.getByText('LG Home (stock)'));
		await flush();
		expect(screen.queryByText('Use LG Home?')).toBeNull();
		expect(document.activeElement.closest('[data-spotlight-id^="home-"]')).toBeNull();
		fireEvent.keyUp(screen.getByText('LG Home (stock)'), {keyCode: 27});
		expect(onExit).toHaveBeenCalledTimes(1);
	});

	test('lists the stock home first, then launchers with names only; check mark on current', async () => {
		mountApp(makeEnv({currentHome: 'org.fancy.launcher'}));
		expect(await screen.findByText('LG Home (stock)')).toBeTruthy();
		expect(screen.getByText('Fancy Launcher')).toBeTruthy();
		expect(screen.getByText('Plain Launcher')).toBeTruthy();
		expect(screen.queryByText('org.plain.launcher')).toBeNull();
		expect(screen.queryByText('com.webos.app.home')).toBeNull();
		expect(screen.getAllByLabelText('Current')).toHaveLength(1);
		const names = screen.getAllByText(/Launcher|LG Home/).map((n) => n.textContent);
		expect(names[0]).toBe('LG Home (stock)');
		// the check sits in the current home's row
		expect(screen.getByLabelText('Current').closest('[data-spotlight-id]').textContent).toContain('Fancy Launcher');
	});

	test('rows carry their app id as data-app-id for the e2e runner (not as text)', async () => {
		mountApp(makeEnv());
		await screen.findByText('Plain Launcher');
		const ids = Array.from(document.querySelectorAll('[data-spotlight-id^="home-"]')).map((r) => r.getAttribute('data-app-id'));
		expect(ids).toEqual(['com.webos.app.home', 'org.fancy.launcher', 'org.plain.launcher']);
	});

	test('icons are loaded as data URLs, with a fallback', async () => {
		const env = makeEnv();
		mountApp(env);
		await screen.findByText('Fancy Launcher');
		await waitFor(() => expect(document.querySelector('img[src="data:image/png;base64,AAAA"]')).toBeTruthy());
		expect(env.readIcon).toHaveBeenCalledWith('org.fancy.launcher', '/apps/fancy/icon.png');
		expect(env.readIcon).toHaveBeenCalledWith('org.plain.launcher', '/apps/plain/icon.png');
		expect(document.querySelectorAll('img')).toHaveLength(1);
	});

	test('rows keep one structure in every state and show no app id', async () => {
		mountApp(makeEnv());
		const item = (await screen.findByText('Plain Launcher')).closest('[data-spotlight-id]');
		const shape = (el) => el.innerHTML.replace(/<img[^>]*>/g, '<img>');
		const rows = () => Array.from(document.querySelectorAll('[data-spotlight-id^="home-"]'));
		// the icon box exists in every row, before any icon has loaded
		expect(rows()).toHaveLength(3);
		rows().forEach((r) => expect(r.querySelectorAll('[data-testid="icon-box"]')).toHaveLength(1));
		const before = shape(item);
		fireEvent.focus(item);
		expect(shape(item)).toBe(before);
		fireEvent.blur(item);
		expect(shape(item)).toBe(before);
		['org.plain.launcher', 'org.fancy.launcher', 'com.webos.app.home'].forEach((id) => expect(screen.queryByText(id)).toBeNull());
		// the check slot is reserved in rows that are not current
		rows().forEach((r) => expect(r.querySelectorAll('i,[class*="checkSlot"]').length).toBeGreaterThan(0));
	});

	test('empty list explains launchers', async () => {
		mountApp(makeEnv({candidates: []}));
		expect(await screen.findByText(/declares appCategory "home"/)).toBeTruthy();
		expect(screen.getByText('LG Home (stock)')).toBeTruthy();
	});

	test('a removed launcher gets one line', async () => {
		mountApp(makeEnv({stored: 'org.gone.launcher', currentHome: 'org.gone.launcher'}));
		expect(await screen.findByText(/was removed; using LG Home/)).toBeTruthy();
		expect(screen.getAllByLabelText('Current')).toHaveLength(1);
	});

	test('select, confirm, apply, toast', async () => {
		const env = makeEnv();
		mountApp(env);
		fireEvent.click(await screen.findByText('Plain Launcher'));
		expect(await screen.findByText('Use Plain Launcher as Home?')).toBeTruthy();
		expect(screen.queryByText(/blink/)).toBeNull();
		expect(env.chooser.apply).not.toHaveBeenCalled();
		fireEvent.click(screen.getByText('Use as Home'));
		expect(await screen.findByText('Done. Press Home to try it.')).toBeTruthy();
		expect(env.chooser.apply).toHaveBeenCalledWith('org.plain.launcher', {deferSamRestart: false});
	});

	test('cancel does nothing', async () => {
		const env = makeEnv();
		mountApp(env);
		fireEvent.click(await screen.findByText('Plain Launcher'));
		fireEvent.click(await screen.findByText('Cancel'));
		await flush();
		expect(env.chooser.apply).not.toHaveBeenCalled();
	});

	test('failure is one plain sentence and details go to the console', async () => {
		const apply = jest.fn(async () => ok({ok: false, steps: [{kind: 'exec', command: 'x', ok: false, error: 'Service does not exist: org.webosbrew.homechooser.service'}], errors: ['Service does not exist: org.webosbrew.homechooser.service']}));
		mountApp(makeEnv({apply}));
		fireEvent.click(await screen.findByText('Plain Launcher'));
		fireEvent.click(await screen.findByText('Use as Home'));
		expect(await screen.findByText("Couldn't set the Home app. The Home Chooser service didn't respond.")).toBeTruthy();
		expect(screen.queryByText(/Denied/)).toBeNull();
		expect(log.mock.calls.some((c) => /^\[HomeChooser\] apply: FAILED/.test(c[0]))).toBe(true);
	});

	test('input is blocked while busy', async () => {
		let release;
		const apply = jest.fn(() => new Promise((r) => { release = () => r(ok()); }));
		mountApp(makeEnv({apply}));
		fireEvent.click(await screen.findByText('Plain Launcher'));
		fireEvent.click(await screen.findByText('Use as Home'));
		await waitFor(() => expect(apply).toHaveBeenCalled());
		fireEvent.click(screen.getByText('Fancy Launcher'));
		expect(screen.queryByText('Use Fancy Launcher as Home?')).toBeNull();
		await act(async () => release());
		expect(await screen.findByText('Done. Press Home to try it.')).toBeTruthy();
	});

	test('choosing the stock home restores stock', async () => {
		const env = makeEnv({currentHome: 'org.fancy.launcher'});
		mountApp(env);
		fireEvent.click(await screen.findByText('LG Home (stock)'));
		fireEvent.click(await screen.findByText('Use LG Home'));
		expect(await screen.findByText('Done. Press Home to try it.')).toBeTruthy();
		expect(env.chooser.restoreStock).toHaveBeenCalled();
		expect(env.chooser.apply).not.toHaveBeenCalled();
	});

	describe('webOS 6 to 9', () => {
		const homeKeyHookOf = (status, {install = {rebootNeeded: true}, remove = {rebootNeeded: true}} = {}) => ({
			status: jest.fn(async () => ok({status})),
			install: jest.fn(async () => ok(install)),
			remove: jest.fn(async () => ok(remove))
		});
		const chooseOn = async (name) => {
			fireEvent.click(await screen.findByText(name));
		};

		test('patch not mounted: apply first, install second, then the service is asked for the restart alert; the app shows no dialog', async () => {
			const homeKeyHook = homeKeyHookOf({mounted: false, enabled: false, patch: 'possible'});
			const env = makeEnv({version: [8, 3, 0], homeKeyHook});
			mountApp(env);
			await chooseOn('Plain Launcher');
			await screen.findByText('Use Plain Launcher as Home?');
			expect(screen.queryByText(/blink/)).toBeNull();
			fireEvent.click(screen.getByText('Use as Home'));
			expect(await screen.findByText('Done. Restart the TV to finish.', {selector: '[role="status"]'})).toBeTruthy();
			expect(homeKeyHook.install).toHaveBeenCalledWith();
			expect(homeKeyHook.install.mock.invocationCallOrder[0]).toBeGreaterThan(env.chooser.apply.mock.invocationCallOrder[0]);
			expect(env.chooser.apply).toHaveBeenCalledWith('org.plain.launcher', {deferSamRestart: true});
			expect(env.rebootPrompt).toHaveBeenCalledTimes(1);
			expect(env.rebootPrompt).toHaveBeenCalledWith('apply');
			expect(screen.queryByText('Restart the TV?')).toBeNull();
			expect(screen.queryByText('Reboot')).toBeNull();
			expect(window.localStorage.getItem(PENDING_KEY)).toBeNull();
		});

		test('first choice can prepare a missing hook cache', async () => {
			const homeKeyHook = homeKeyHookOf({mounted: false, enabled: false, hook: 'stale', patch: 'refused', reason: 'missing'});
			const env = makeEnv({version: [9, 2, 4], homeKeyHook});
			mountApp(env);
			await chooseOn('Plain Launcher');
			fireEvent.click(await screen.findByText('Use as Home'));
			await screen.findByText('Done. Restart the TV to finish.');
			expect(homeKeyHook.install).toHaveBeenCalledTimes(1);
		});

		test('the prompt is the same whether the document is hidden or not', async () => {
			const hidden = jest.spyOn(document, 'hidden', 'get').mockReturnValue(true);
			try {
				const env = makeEnv({version: [8, 3, 0], homeKeyHook: homeKeyHookOf({mounted: false, enabled: false, patch: 'possible'})});
				mountApp(env);
				await chooseOn('Plain Launcher');
				fireEvent.click(await screen.findByText('Use as Home'));
				await waitFor(() => expect(env.rebootPrompt).toHaveBeenCalledWith('apply'));
				await screen.findByText('Done. Restart the TV to finish.');
				expect(screen.queryByText('Restart the TV?')).toBeNull();
			} finally {
				hidden.mockRestore();
			}
		});

		test('a failing rebootPrompt shows the plain toast and no dialog', async () => {
			const env = makeEnv({version: [8, 3, 0], homeKeyHook: homeKeyHookOf({mounted: false, enabled: false, patch: 'possible'})});
			env.rebootPrompt.mockRejectedValue(new Error('confirmFailed'));
			mountApp(env);
			await chooseOn('Plain Launcher');
			fireEvent.click(await screen.findByText('Use as Home'));
			expect(await screen.findByText('Restart the TV to finish.')).toBeTruthy();
			expect(screen.queryByText('Restart the TV?')).toBeNull();
		});

		describe('a restart is pending when the screen opens', () => {
			test('rebuilt stale cache raises one restart alert for its generation', async () => {
				const status = {mounted: false, enabled: true, patch: 'possible', hook: 'stale', cacheGeneration: '123-45', rebootNeeded: true};
				const env = makeEnv({version: [8, 3, 0], homeKeyHook: homeKeyHookOf(status)});
				mountApp(env);
				await waitFor(() => expect(env.rebootPrompt).toHaveBeenCalledTimes(1));
				expect(env.rebootPrompt).toHaveBeenCalledWith('apply');
				expect(window.localStorage.getItem('homechooser.hookCachePrompt')).toBe('123-45');
			});
			test('apply: a note and a "Restart now" row; the row asks the service again (reason apply)', async () => {
				const env = makeEnv({version: [8, 3, 0], homeKeyHook: homeKeyHookOf({mounted: false, enabled: true, patch: 'possible', rebootNeeded: true})});
				mountApp(env);
				expect(await screen.findByText('The Home button changes after a restart. Use Restart now — turning the TV off with the remote may not restart it.')).toBeTruthy();
				expect(env.rebootPrompt).not.toHaveBeenCalled();
				fireEvent.click(screen.getByText('Restart now'));
				await waitFor(() => expect(env.rebootPrompt).toHaveBeenCalledWith('apply'));
				expect(await screen.findByText('Check the restart prompt on the screen.')).toBeTruthy();
			});

			test('remove: the patch mounted without the flag is a remove', async () => {
				const env = makeEnv({version: [8, 3, 0], homeKeyHook: homeKeyHookOf({mounted: true, enabled: false, release: '8.3.0', rebootNeeded: true})});
				mountApp(env);
				expect(await screen.findByText('LG Home is on the Home button now. A restart removes the patch.')).toBeTruthy();
				fireEvent.click(screen.getByText('Restart now'));
				await waitFor(() => expect(env.rebootPrompt).toHaveBeenCalledWith('remove'));
			});

			test('a refused prompt (notPending, say) toasts the plain line', async () => {
				const env = makeEnv({version: [8, 3, 0], homeKeyHook: homeKeyHookOf({mounted: false, enabled: true, patch: 'possible', rebootNeeded: true})});
				env.rebootPrompt.mockRejectedValue(new Error('notPending'));
				mountApp(env);
				fireEvent.click(await screen.findByText('Restart now'));
				expect(await screen.findByText('Restart the TV to finish.')).toBeTruthy();
			});

			test('nothing pending: no note and no row', async () => {
				mountApp(makeEnv({version: [8, 3, 0], homeKeyHook: homeKeyHookOf({mounted: true, enabled: true, release: '8.3.0', rebootNeeded: false})}));
				await screen.findByText('Plain Launcher');
				expect(screen.queryByText('Restart now')).toBeNull();
			});

			test('no patch (5.x, 10 and later): nothing to restart, no row', async () => {
				mountApp(makeEnv({version: [11, 0, 0]}));
				await screen.findByText('Plain Launcher');
				expect(screen.queryByText('Restart now')).toBeNull();
			});
		});

		test('mounted and enabled: switching launchers makes no install call and no prompt', async () => {
			const homeKeyHook = homeKeyHookOf({mounted: true, enabled: true, patch: 'possible', release: '8.3.0'});
			const env = makeEnv({version: [8, 3, 0], homeKeyHook});
			mountApp(env);
			await chooseOn('Plain Launcher');
			fireEvent.click(await screen.findByText('Use as Home'));
			expect(await screen.findByText('Done. Press Home to try it.')).toBeTruthy();
			expect(env.chooser.apply).toHaveBeenCalledWith('org.plain.launcher', {deferSamRestart: false});
			expect(homeKeyHook.install).not.toHaveBeenCalled();
			expect(env.rebootPrompt).not.toHaveBeenCalled();
			expect(screen.queryByText('Restart the TV?')).toBeNull();
		});

		test('mounted but disabled (restored, not restarted): the install runs, no restart is asked for', async () => {
			const homeKeyHook = homeKeyHookOf({mounted: true, enabled: false, release: '8.3.0'}, {install: {rebootNeeded: false}});
			const env = makeEnv({version: [8, 3, 0], homeKeyHook});
			mountApp(env);
			await chooseOn('Plain Launcher');
			fireEvent.click(await screen.findByText('Use as Home'));
			expect(await screen.findByText('Done. Press Home to try it.')).toBeTruthy();
			expect(homeKeyHook.install).toHaveBeenCalledTimes(1);
			expect(env.chooser.apply).toHaveBeenCalledWith('org.plain.launcher', {deferSamRestart: false});
			expect(env.rebootPrompt).not.toHaveBeenCalled();
		});

		test('install failure is reported after the choice was applied', async () => {
			const homeKeyHook = homeKeyHookOf({mounted: false, patch: 'possible'});
			homeKeyHook.install = jest.fn(async () => ok({ok: false, errors: ['boom']}));
			const env = makeEnv({version: [8, 3, 0], homeKeyHook});
			mountApp(env);
			fireEvent.click(await screen.findByText('Plain Launcher'));
			fireEvent.click(await screen.findByText('Use as Home'));
			expect(await screen.findByText("Couldn't set the Home app.")).toBeTruthy();
			expect(env.chooser.apply).toHaveBeenCalled();
		});

		test('apply failure never installs the patch', async () => {
			const homeKeyHook = homeKeyHookOf({mounted: false, patch: 'possible'});
			const apply = jest.fn(async () => ok({ok: false, errors: ['boom']}));
			mountApp(makeEnv({version: [8, 3, 0], homeKeyHook, apply}));
			fireEvent.click(await screen.findByText('Plain Launcher'));
			fireEvent.click(await screen.findByText('Use as Home'));
			expect(await screen.findByText("Couldn't set the Home app.")).toBeTruthy();
			expect(homeKeyHook.install).not.toHaveBeenCalled();
		});

		test('patch active: no install, no prompt', async () => {
			const homeKeyHook = homeKeyHookOf({mounted: true, enabled: true, patch: 'refused', release: '8.3.0'});
			const env = makeEnv({version: [8, 3, 0], homeKeyHook});
			mountApp(env);
			fireEvent.click(await screen.findByText('Plain Launcher'));
			await screen.findByText('Use Plain Launcher as Home?');
			fireEvent.click(screen.getByText('Use as Home'));
			await screen.findByText('Done. Press Home to try it.');
			expect(homeKeyHook.install).not.toHaveBeenCalled();
		});

		test('anchor mismatch: says so plainly and applies nothing', async () => {
			const homeKeyHook = homeKeyHookOf({mounted: false, enabled: false, patch: 'refused'});
			const env = makeEnv({version: [8, 3, 0], homeKeyHook});
			mountApp(env);
			fireEvent.click(await screen.findByText('Plain Launcher'));
			expect((await screen.findAllByText(/can't be changed on this TV's software/)).length).toBeGreaterThan(0);
			expect(screen.queryByText('Use as Home')).toBeNull();
			fireEvent.click(screen.getByText('OK'));
			await flush();
			expect(homeKeyHook.install).not.toHaveBeenCalled();
			expect(env.chooser.apply).not.toHaveBeenCalled();
		});

		test('restoring stock with the patch mounted removes the flag afterwards and asks for a restart (reason remove)', async () => {
			const homeKeyHook = homeKeyHookOf({mounted: true, enabled: true, release: '8.3.0'});
			const env = makeEnv({version: [8, 3, 0], homeKeyHook, currentHome: 'org.fancy.launcher'});
			mountApp(env);
			fireEvent.click(await screen.findByText('LG Home (stock)'));
			fireEvent.click(await screen.findByText('Use LG Home'));
			expect(await screen.findByText('Done. Restart the TV to finish.', {selector: '[role="status"]'})).toBeTruthy();
			expect(homeKeyHook.remove).toHaveBeenCalled();
			expect(homeKeyHook.remove.mock.invocationCallOrder[0]).toBeGreaterThan(env.chooser.restoreStock.mock.invocationCallOrder[0]);
			expect(env.rebootPrompt).toHaveBeenCalledWith('remove');
		});

		test('restoring stock when nothing is mounted needs no restart', async () => {
			const homeKeyHook = homeKeyHookOf({mounted: false, enabled: true, patch: 'possible'}, {remove: {rebootNeeded: false}});
			const env = makeEnv({version: [8, 3, 0], homeKeyHook, currentHome: 'org.fancy.launcher'});
			mountApp(env);
			fireEvent.click(await screen.findByText('LG Home (stock)'));
			fireEvent.click(await screen.findByText('Use LG Home'));
			await screen.findByText('Done. Press Home to try it.');
			expect(env.rebootPrompt).not.toHaveBeenCalled();
		});
	});

	test.each([
		[{mounted: false, patch: 'possible'}, 'ready'],
		[{mounted: false, hook: 'stale', patch: 'refused', reason: 'missing'}, 'ready'],
		[{mounted: true}, 'active'],
		[{mounted: false, patch: 'refused'}, 'nomatch']
	])('patch state of %j is %s', (status, expected) => {
		expect(patchStateOf({}, status)).toBe(expected);
	});

	describe('footer', () => {
		const withStatus = (version, status) => makeEnv({
			version,
			homeKeyHook: status === undefined ? null : {status: jest.fn(async () => ok({status})), install: jest.fn(), remove: jest.fn()}
		});
		test.each([
			['active', {mounted: true, enabled: true}, 'Home key hook: active'],
			['not active', {mounted: false, enabled: false, patch: 'possible'}, 'Home key hook: not active'],
			['no match', {mounted: false, enabled: false, patch: 'refused'}, "Home key hook: not available for this TV's software"]
		])('webOS 8: %s', async (name, status, text) => {
			mountApp(withStatus([8, 3, 0], status));
			expect((await screen.findByTestId('footer')).textContent).toBe(text);
			expect(screen.getByTestId('footer').matches('button,a,[tabindex],[data-spotlight-id]')).toBe(false);
		});
		test.each([[[5, 6, 1]], [[10, 3, 1]], [[11, 0, 0]]])('webOS %j: no footer, no About', async (version) => {
			mountApp(withStatus(version));
			await screen.findByText('Plain Launcher');
			expect(screen.queryByTestId('footer')).toBeNull();
			expect(screen.queryByLabelText('About')).toBeNull();
			expect(screen.queryByText(/Home key hook/)).toBeNull();
		});
	});

	const rowOf = (name) => screen.getByText(name).closest('[data-spotlight-id]');
	const focusedRow = () => document.activeElement && document.activeElement.closest('[data-spotlight-id^="home-"]');

	test('no header close button', async () => {
		mountApp(makeEnv());
		await screen.findByText('Plain Launcher');
		expect(screen.queryByLabelText('Exit app')).toBeNull();
		expect(document.querySelector('[class*="close"]')).toBeNull();
	});

	test('OK on the current row keeps focus, opens no dialog, toasts, calls nothing', async () => {
		const env = makeEnv({currentHome: 'org.fancy.launcher'});
		mountApp(env);
		await screen.findByText('Fancy Launcher');
		await waitFor(() => expect(focusedRow()).toBe(rowOf('Fancy Launcher')));
		fireEvent.click(rowOf('Fancy Launcher'));
		expect(await screen.findByText('Fancy Launcher is already your Home.')).toBeTruthy();
		expect(screen.queryByRole('dialog')).toBeNull();
		expect(focusedRow()).toBe(rowOf('Fancy Launcher'));
		expect(env.chooser.apply).not.toHaveBeenCalled();
		expect(env.chooser.restoreStock).not.toHaveBeenCalled();
	});

	test('OK on the stock row when it is current says LG Home', async () => {
		const env = makeEnv();
		mountApp(env);
		await screen.findByText('Plain Launcher');
		fireEvent.click(rowOf('LG Home (stock)'));
		expect(await screen.findByText('LG Home is already your Home.')).toBeTruthy();
		expect(env.chooser.restoreStock).not.toHaveBeenCalled();
	});

	test('focus returns to the opening row after Cancel and after Back', async () => {
		mountApp(makeEnv());
		fireEvent.click(await screen.findByText('Plain Launcher'));
		await screen.findByText('Use Plain Launcher as Home?');
		fireEvent.click(screen.getByText('Cancel'));
		await waitFor(() => expect(screen.queryByText('Use Plain Launcher as Home?')).toBeNull());
		await waitFor(() => expect(focusedRow()).toBe(rowOf('Plain Launcher')));
		fireEvent.click(rowOf('Fancy Launcher'));
		await screen.findByText('Use Fancy Launcher as Home?');
		fireEvent.keyUp(screen.getByText('Cancel'), {keyCode: 27});
		await waitFor(() => expect(screen.queryByText('Use Fancy Launcher as Home?')).toBeNull());
		await waitFor(() => expect(focusedRow()).toBe(rowOf('Fancy Launcher')));
	});

	test('after a successful apply, focus is on the new current row', async () => {
		const env = makeEnv();
		env.chooser.readState.mockImplementation(async () => ok({state: {record: null, recordHome: env.chooser.apply.mock.calls.length ? 'org.plain.launcher' : null, profile: null, profileHome: null}}));
		mountApp(env);
		fireEvent.click(await screen.findByText('Plain Launcher'));
		fireEvent.click(await screen.findByText('Use as Home'));
		await screen.findByText(/Done\. Press Home/);
		expect(rowOf('Plain Launcher').querySelector('[aria-label="Current"]')).toBeTruthy();
		await waitFor(() => expect(focusedRow()).toBe(rowOf('Plain Launcher')));
	});

	test('Back closes the dialog first, then exits', async () => {
		const {onExit} = mountApp(makeEnv());
		fireEvent.click(await screen.findByText('Plain Launcher'));
		const dialogText = await screen.findByText('Use Plain Launcher as Home?');
		fireEvent.keyUp(screen.getByText('Cancel'), {keyCode: 27});
		await waitFor(() => expect(screen.queryByText('Use Plain Launcher as Home?')).toBeNull());
		expect(dialogText).toBeTruthy();
		expect(onExit).not.toHaveBeenCalled();
		fireEvent.keyUp(screen.getByText('Plain Launcher'), {keyCode: 27});
		expect(onExit).toHaveBeenCalled();
	});

	test('Back via keyCode 461 (disableBackHistoryAPI) closes the dialog first, then exits', async () => {
		const {onExit} = mountApp(makeEnv());
		fireEvent.click(await screen.findByText('Plain Launcher'));
		await screen.findByText('Use Plain Launcher as Home?');
		fireEvent.keyUp(screen.getByText('Cancel'), {keyCode: 461});
		await waitFor(() => expect(screen.queryByText('Use Plain Launcher as Home?')).toBeNull());
		expect(onExit).not.toHaveBeenCalled();
		fireEvent.keyUp(screen.getByText('Plain Launcher'), {keyCode: 461});
		expect(onExit).toHaveBeenCalledTimes(1);
	});

	test('without an onExit prop, Back closes the window (never webOSSystem.hide)', async () => {
		const close = jest.spyOn(window, 'close').mockImplementation(() => {});
		window.webOSSystem = {hide: jest.fn()};
		render(<App load={async () => makeEnv()} />);
		fireEvent.keyUp(await screen.findByText('Plain Launcher'), {keyCode: 27});
		expect(close).toHaveBeenCalledTimes(1);
		expect(window.webOSSystem.hide).not.toHaveBeenCalled();
		delete window.webOSSystem;
		close.mockRestore();
	});

	test('after a successful apply the panel stays open with the toast', async () => {
		const {onExit} = mountApp(makeEnv());
		fireEvent.click(await screen.findByText('Plain Launcher'));
		fireEvent.click(await screen.findByText('Use as Home'));
		expect(await screen.findByText('Done. Press Home to try it.')).toBeTruthy();
		expect(onExit).not.toHaveBeenCalled();
		expect(screen.getByText('Fancy Launcher')).toBeTruthy();
	});

	describe('pending result marker (webOS 6 to 9)', () => {
		const homeKeyHookOf = (status) => ({
			status: jest.fn(async () => ok({status})),
			install: jest.fn(async () => ok()),
			remove: jest.fn(async () => ok())
		});
		const pending = (id) => window.localStorage.setItem(PENDING_KEY, JSON.stringify({id, title: id, at: 1}));

		test('never written by a choice that installs the patch', async () => {
			const homeKeyHook = homeKeyHookOf({mounted: false, patch: 'possible'});
			let seen = 'unset';
			homeKeyHook.install = jest.fn(async () => { seen = window.localStorage.getItem(PENDING_KEY); return ok({rebootNeeded: true}); });
			mountApp(makeEnv({version: [8, 3, 0], homeKeyHook}));
			fireEvent.click(await screen.findByText('Plain Launcher'));
			fireEvent.click(await screen.findByText('Use as Home'));
			await screen.findByText('Done. Restart the TV to finish.');
			expect(seen).toBeNull();
			expect(window.localStorage.getItem(PENDING_KEY)).toBeNull();
		});

		test('no marker when the patch is already active', async () => {
			const homeKeyHook = homeKeyHookOf({mounted: true, enabled: true});
			const apply = jest.fn(async () => { expect(window.localStorage.getItem(PENDING_KEY)).toBeNull(); return ok(); });
			mountApp(makeEnv({version: [8, 3, 0], homeKeyHook, apply}));
			fireEvent.click(await screen.findByText('Plain Launcher'));
			fireEvent.click(await screen.findByText('Use as Home'));
			await screen.findByText('Done. Press Home to try it.');
			expect(apply).toHaveBeenCalled();
		});

		test('next launch: the marker matches the current home, so the success toast shows once and the marker clears', async () => {
			pending('org.plain.launcher');
			mountApp(makeEnv({currentHome: 'org.plain.launcher', stored: 'org.plain.launcher'}));
			expect(await screen.findByText('Done. Press Home to try it.')).toBeTruthy();
			expect(window.localStorage.getItem(PENDING_KEY)).toBeNull();
		});

		test('next launch: the marker does not match, so the failure toast shows', async () => {
			// the apply stored the choice but the home is not it: a genuine failure
			pending('org.plain.launcher');
			mountApp(makeEnv({currentHome: 'org.fancy.launcher', stored: 'org.plain.launcher'}));
			expect(await screen.findByText("Couldn't set the Home app.")).toBeTruthy();
			expect(window.localStorage.getItem(PENDING_KEY)).toBeNull();
		});

		test('next launch: a marker from a previous install (nothing stored) is dropped without a toast', async () => {
			pending('org.plain.launcher');
			mountApp(makeEnv({currentHome: null, stored: ''}));
			await screen.findByText('Plain Launcher');
			expect(screen.queryByRole('status')).toBeNull();
			expect(window.localStorage.getItem(PENDING_KEY)).toBeNull();
		});

		test('next launch: a marker overtaken by another stored choice is dropped without a toast', async () => {
			pending('org.plain.launcher');
			mountApp(makeEnv({currentHome: 'org.fancy.launcher', stored: 'org.fancy.launcher'}));
			await screen.findByText('Plain Launcher');
			expect(screen.queryByRole('status')).toBeNull();
			expect(window.localStorage.getItem(PENDING_KEY)).toBeNull();
		});

		test('next launch: a stale restore marker with a launcher stored is dropped without a toast', async () => {
			pending('com.webos.app.home');
			mountApp(makeEnv({currentHome: 'org.fancy.launcher', stored: 'org.fancy.launcher'}));
			await screen.findByText('Plain Launcher');
			expect(screen.queryByRole('status')).toBeNull();
		});

		test('next launch after a restore: stock home is the success', async () => {
			pending('com.webos.app.home');
			mountApp(makeEnv({currentHome: null}));
			expect(await screen.findByText('Done. Press Home to try it.')).toBeTruthy();
		});

		test('no marker, no toast', async () => {
			mountApp(makeEnv());
			await screen.findByText('Plain Launcher');
			expect(screen.queryByRole('status')).toBeNull();
		});
	});
});
