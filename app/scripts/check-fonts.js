// SPDX-License-Identifier: Apache-2.0
// Verify that the package contains no copied font files after prune-fonts.js.
const fs = require('fs');
const path = require('path');

const dist = path.resolve(process.argv[2] || 'dist');
if (!fs.statSync(dist).isDirectory()) throw new Error('missing dist directory: ' + dist);

const found = [];
const walk = (dir) => {
	for (const entry of fs.readdirSync(dir, {withFileTypes: true})) {
		const file = path.join(dir, entry.name);
		if (entry.isDirectory()) walk(file);
		else if (/\.(?:ttf|otf|woff2?|eot)$/i.test(entry.name)) found.push(path.relative(dist, file));
	}
};
walk(dist);
if (found.length) throw new Error('font files remain in dist:\n' + found.join('\n'));
console.log('check-fonts: no font files in dist');
