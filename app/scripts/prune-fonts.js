// SPDX-License-Identifier: Apache-2.0
// Enact copies Moonstone's URL fallbacks into dist. The TV supplies the fonts named by the
// preceding local() sources, so the packaged copies are unnecessary.
const fs = require('fs');
const path = require('path');

const dist = path.resolve(process.argv[2] || 'dist');
if (!fs.existsSync(dist)) throw new Error('missing dist directory: ' + dist);

const fonts = path.join(dist, 'node_modules', '@enact', 'moonstone', 'fonts');
for (const name of ['Miso', 'MuseoSans', 'Moonstone.ttf']) {
	fs.rmSync(path.join(fonts, name), {recursive: true, force: true});
}
console.log('prune-fonts: removed Moonstone font fallbacks from dist');
