// package.json is the one source of the version; appinfo.json must carry the same (scripts/check-version.js).
import pkg from '../../../package.json';
import appinfo from '../../../appinfo.json';

describe('version', () => {
	test('appinfo.json carries package.json\'s version, as x.y.z', () => {
		expect(pkg.version).toMatch(/^\d+\.\d+\.\d+$/);
		expect(appinfo.version).toBe(pkg.version);
	});
});
