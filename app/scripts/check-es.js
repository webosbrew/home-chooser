// SPDX-License-Identifier: Apache-2.0
// Parses the built JS with acorn at an ECMAScript level, so syntax newer than the oldest supported
// Chromium (53 on webOS 4.5: ES2016, no object spread, no async/await) fails the build.
//   node scripts/check-es.js [dist] [ecmaVersion]
// acorn is a dependency of @enact/cli's webpack; it is resolved from there, not from this package.
const fs = require('fs');
const path = require('path');

const dist = process.argv[2] || 'dist';
const ecmaVersion = Number(process.argv[3] || 2016);
const acorn = require(require.resolve('acorn', {paths: [path.dirname(require.resolve('@enact/cli/package.json'))]}));

let failed = 0;
for (const name of fs.readdirSync(dist).filter((n) => /\.js$/.test(n))) {
	const code = fs.readFileSync(path.join(dist, name), 'utf8');
	try {
		acorn.parse(code, {ecmaVersion, sourceType: 'script'});
		console.log(`${name}: parses as ES${ecmaVersion}`);
	} catch (e) {
		failed++;
		const near = code.slice(Math.max(0, e.pos - 60), e.pos + 60).replace(/\s+/g, ' ');
		console.error(`${name}: not ES${ecmaVersion}: ${e.message}\n  near: ${near}`);
	}
}
process.exit(failed ? 1 : 0);
