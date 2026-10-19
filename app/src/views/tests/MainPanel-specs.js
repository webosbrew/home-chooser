import {render, screen} from '@testing-library/react';

import App from '../../App';

describe('App', () => {
	test('renders the title and, with no service bridge, the plain exec failure', async () => {
		render(<App />);
		expect(await screen.findByText('Open House')).toBeTruthy();
		expect(await screen.findByText(/Homebrew Channel root exec is not reachable/)).toBeTruthy();
	});
});
