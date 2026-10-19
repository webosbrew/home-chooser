import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';

import App from '../../App';
import {FatalError} from '../../openhouse/environment';
import {PENDING_KEY} from '../pending';

const ok = (extra) => Object.assign({ok: true, steps: [], warnings: [], errors: []}, extra);

const makeEnv = ({version = [11, 0, 0], candidates, currentHome = null, stored = '', backport = null, apply, restore} = {}) => {
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
		backport,
		readIcon: jest.fn(async (p) => (p.includes('fancy') ? 'data:image/png;base64,AAAA' : null))
	};
};

const mountApp = (env, onExit = jest.fn()) => {
	const load = jest.fn(async () => env);
	render(<App load={load} onExit={onExit} />);
	return {load, onExit};
};

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

describe('Open House screen', () => {
	let log;
	beforeEach(() => { log = jest.spyOn(console, 'log').mockImplementation(() => {}); });
	afterEach(() => { log.mockRestore(); window.localStorage.clear(); });

	test.each([
		['unreachable', /needs Homebrew Channel on a rooted TV/],
		['denied', /doesn't let apps run root commands yet\. Update Homebrew Channel; Open House needs it\./],
		['notRoot', /not running as root/],
		['failed', /can't reach the TV's services/]
	])('no root (%s): the reason on top and a disabled list, same layout', async (kind, text) => {
		const onExit = jest.fn();
		render(<App load={() => Promise.reject(new FatalError(kind, 'x'))} onExit={onExit} />);
		expect(await screen.findByText(text)).toBeTruthy();
		expect(screen.getByText('Find your TV a new home')).toBeTruthy();
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

	test('icons are loaded as data URLs, with a fallback', async () => {
		const env = makeEnv();
		mountApp(env);
		await screen.findByText('Fancy Launcher');
		await waitFor(() => expect(document.querySelector('img[src="data:image/png;base64,AAAA"]')).toBeTruthy());
		expect(env.readIcon).toHaveBeenCalledWith('/apps/fancy/icon.png');
		expect(env.readIcon).toHaveBeenCalledWith('/apps/plain/icon.png');
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
		expect(env.chooser.apply).toHaveBeenCalledWith('org.plain.launcher');
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
		const apply = jest.fn(async () => ok({ok: false, steps: [{kind: 'exec', command: 'x', ok: false, error: 'Denied method call "exec"'}], errors: ['Denied method call "exec"']}));
		mountApp(makeEnv({apply}));
		fireEvent.click(await screen.findByText('Plain Launcher'));
		fireEvent.click(await screen.findByText('Use as Home'));
		expect(await screen.findByText("Couldn't set the Home app. Homebrew Channel didn't respond.")).toBeTruthy();
		expect(screen.queryByText(/Denied/)).toBeNull();
		expect(log.mock.calls.some((c) => /^\[OpenHouse\] apply: FAILED/.test(c[0]))).toBe(true);
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
		const backportOf = (status) => ({
			status: jest.fn(async () => ok({status})),
			install: jest.fn(async () => ok()),
			remove: jest.fn(async () => ok())
		});

		test('patch not active: the dialog mentions the blink and the flow applies first and installs the patch last', async () => {
			const backport = backportOf({mounted: false, enabled: false, match: '8.3.0'});
			const env = makeEnv({version: [8, 3, 0], backport});
			mountApp(env);
			fireEvent.click(await screen.findByText('Plain Launcher'));
			expect(await screen.findByText(/screen will blink once/)).toBeTruthy();
			fireEvent.click(screen.getByText('Use as Home'));
			expect(await screen.findByText('Done. Press Home to try it.')).toBeTruthy();
			expect(backport.install).toHaveBeenCalled();
			expect(backport.install.mock.invocationCallOrder[0]).toBeGreaterThan(env.chooser.apply.mock.invocationCallOrder[0]);
		});

		test('install failure is reported after the choice was applied', async () => {
			const backport = backportOf({mounted: false, match: '8.3.0'});
			backport.install = jest.fn(async () => ok({ok: false, errors: ['boom']}));
			const env = makeEnv({version: [8, 3, 0], backport});
			mountApp(env);
			fireEvent.click(await screen.findByText('Plain Launcher'));
			fireEvent.click(await screen.findByText('Use as Home'));
			expect(await screen.findByText("Couldn't set the Home app.")).toBeTruthy();
			expect(env.chooser.apply).toHaveBeenCalled();
		});

		test('apply failure never installs the patch', async () => {
			const backport = backportOf({mounted: false, match: '8.3.0'});
			const apply = jest.fn(async () => ok({ok: false, errors: ['boom']}));
			mountApp(makeEnv({version: [8, 3, 0], backport, apply}));
			fireEvent.click(await screen.findByText('Plain Launcher'));
			fireEvent.click(await screen.findByText('Use as Home'));
			expect(await screen.findByText("Couldn't set the Home app.")).toBeTruthy();
			expect(backport.install).not.toHaveBeenCalled();
		});

		test('patch active: no blink sentence, no install', async () => {
			const backport = backportOf({mounted: true, enabled: true, match: null, release: '8.3.0'});
			const env = makeEnv({version: [8, 3, 0], backport});
			mountApp(env);
			fireEvent.click(await screen.findByText('Plain Launcher'));
			await screen.findByText('Use Plain Launcher as Home?');
			expect(screen.queryByText(/blink/)).toBeNull();
			fireEvent.click(screen.getByText('Use as Home'));
			await screen.findByText('Done. Press Home to try it.');
			expect(backport.install).not.toHaveBeenCalled();
		});

		test('hash mismatch: says so plainly and applies nothing', async () => {
			const backport = backportOf({mounted: false, enabled: false, match: null});
			const env = makeEnv({version: [8, 3, 0], backport});
			mountApp(env);
			fireEvent.click(await screen.findByText('Plain Launcher'));
			expect((await screen.findAllByText(/can't be changed on this TV's software/)).length).toBeGreaterThan(0);
			expect(screen.queryByText('Use as Home')).toBeNull();
			fireEvent.click(screen.getByText('OK'));
			await flush();
			expect(backport.install).not.toHaveBeenCalled();
			expect(env.chooser.apply).not.toHaveBeenCalled();
		});

		test('restoring stock removes the patch afterwards', async () => {
			const backport = backportOf({mounted: true, enabled: true, release: '8.3.0'});
			const env = makeEnv({version: [8, 3, 0], backport, currentHome: 'org.fancy.launcher'});
			mountApp(env);
			fireEvent.click(await screen.findByText('LG Home (stock)'));
			fireEvent.click(await screen.findByText('Use LG Home'));
			await screen.findByText('Done. Press Home to try it.');
			expect(backport.remove).toHaveBeenCalled();
			expect(backport.remove.mock.invocationCallOrder[0]).toBeGreaterThan(env.chooser.restoreStock.mock.invocationCallOrder[0]);
		});
	});

	describe('footer', () => {
		const withStatus = (version, status) => makeEnv({
			version,
			backport: status === undefined ? null : {status: jest.fn(async () => ok({status})), install: jest.fn(), remove: jest.fn()}
		});
		test.each([
			['active', {mounted: true, enabled: true}, 'Home key patch: active'],
			['not active', {mounted: false, enabled: false, match: '8.3.0'}, 'Home key patch: not active'],
			['no match', {mounted: false, enabled: false, match: null}, "Home key patch: not available for this TV's software"]
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
			expect(screen.queryByText(/Home key patch/)).toBeNull();
		});
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
		const backportOf = (status) => ({
			status: jest.fn(async () => ok({status})),
			install: jest.fn(async () => ok()),
			remove: jest.fn(async () => ok())
		});
		const pending = (id) => window.localStorage.setItem(PENDING_KEY, JSON.stringify({id, title: id, at: 1}));

		test('written before the patch install, cleared when the run finishes', async () => {
			const backport = backportOf({mounted: false, match: '8.3.0'});
			let seen;
			backport.install = jest.fn(async () => { seen = window.localStorage.getItem(PENDING_KEY); return ok(); });
			mountApp(makeEnv({version: [8, 3, 0], backport}));
			fireEvent.click(await screen.findByText('Plain Launcher'));
			fireEvent.click(await screen.findByText('Use as Home'));
			await screen.findByText('Done. Press Home to try it.');
			expect(JSON.parse(seen).id).toBe('org.plain.launcher');
			expect(window.localStorage.getItem(PENDING_KEY)).toBeNull();
		});

		test('no marker when the patch is already active', async () => {
			const backport = backportOf({mounted: true, enabled: true});
			const apply = jest.fn(async () => { expect(window.localStorage.getItem(PENDING_KEY)).toBeNull(); return ok(); });
			mountApp(makeEnv({version: [8, 3, 0], backport, apply}));
			fireEvent.click(await screen.findByText('Plain Launcher'));
			fireEvent.click(await screen.findByText('Use as Home'));
			await screen.findByText('Done. Press Home to try it.');
			expect(apply).toHaveBeenCalled();
		});

		test('next launch: the marker matches the current home, so the success toast shows once and the marker clears', async () => {
			pending('org.plain.launcher');
			mountApp(makeEnv({currentHome: 'org.plain.launcher'}));
			expect(await screen.findByText('Done. Press Home to try it.')).toBeTruthy();
			expect(window.localStorage.getItem(PENDING_KEY)).toBeNull();
		});

		test('next launch: the marker does not match, so the failure toast shows', async () => {
			pending('org.plain.launcher');
			mountApp(makeEnv({currentHome: 'org.fancy.launcher'}));
			expect(await screen.findByText("Couldn't set the Home app.")).toBeTruthy();
			expect(window.localStorage.getItem(PENDING_KEY)).toBeNull();
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
