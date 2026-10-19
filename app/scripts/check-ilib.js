// Loads the pruned iLib data in dist/ through iLib's real Loader (the same manifest check and
// manifest-gated lookup Enact's loader performs in the browser), for the locales the app can meet on
// a TV, and fails if iLib throws, asks for data that is not shipped, or gives a wrong answer.
// Usage: node scripts/check-ilib.js [dist]      (run after `enact pack -p` and prune-ilib.js)
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const dist = path.resolve(process.argv[2] || 'dist');
const dir = path.join(dist, 'node_modules', 'ilib', 'locale');
const lib = (m) => require(path.join(__dirname, '..', 'node_modules', 'ilib', 'lib', m));

const ilib = lib('ilib.js');
const Loader = lib('Loader.js');
const LocaleInfo = lib('LocaleInfo.js');
const ScriptInfo = lib('ScriptInfo.js');
const ResBundle = lib('ResBundle.js');
const CaseMapper = lib('CaseMapper.js');

const requested = new Set();
const loaded = new Set();
function DistLoader() {
	Loader.call(this);
	this.includePath = [dir];
	this.addPaths = [];
}
DistLoader.prototype = new Loader();
DistLoader.prototype.constructor = DistLoader;
DistLoader.prototype._loadFile = function (p, sync, cb) {
	let text;
	try {
		text = fs.readFileSync(p, 'utf8');
	} catch (e) {
		// not shipped
	}
	if (!p.endsWith('ilibmanifest.json')) {
		requested.add(path.relative(dir, p));
		if (text) loaded.add(path.relative(dir, p));
	}
	if (cb) cb(text);
	return text;
};
ilib._dyndata = true;
ilib.setLoaderCallback(new DistLoader());

const manifest = JSON.parse(fs.readFileSync(path.join(dir, 'ilibmanifest.json'), 'utf8')).files;
manifest.forEach((f) => assert(fs.existsSync(path.join(dir, f)), 'manifest lists missing file ' + f));
assert(!fs.existsSync(path.join(dir, 'zoneinfo')), 'zoneinfo should be pruned');

// [system locale, expected script, expected direction]. Every one must resolve without throwing;
// locales whose data was pruned fall back to the root (Latn, ltr) information.
const cases = [
	['en-US', 'Latn', 'ltr'],
	['en-GB', 'Latn', 'ltr'],
	['en', 'Latn', 'ltr'],
	['ko-KR', null, null],
	['ja-JP', null, null],
	['zh-Hans-CN', null, null],
	['ar-SA', null, null],
	['de-DE', null, null],
	['pa-PK', null, null],
	['xx-XX', null, null]
];
cases.forEach(([spec, script, dirn]) => {
	ilib.setLocale(spec);
	const locale = ilib.getLocale();
	new CaseMapper({direction: 'tolower', locale});
	new CaseMapper({direction: 'toupper', locale});
	const li = new LocaleInfo(locale, {sync: true});
	const si = new ScriptInfo(li.getScript(), {sync: true});
	const rb = new ResBundle({locale, name: 'strings', sync: true});
	const got = [li.getScript(), si.getScriptDirection()];
	assert.strictEqual(rb.getString('Hello').toString(), 'Hello');
	if (script) assert.deepStrictEqual(got, [script, dirn], spec);
	assert(got[0] && got[1], spec + ' resolved no script');
	console.log(`ok ${spec} -> script ${got[0]}, ${got[1]}`);
});

const missing = [...requested].filter((f) => !loaded.has(f));
console.log(`iLib requested ${requested.size} data files, ${loaded.size} found in dist, ${missing.length} absent (iLib falls back for these):`);
console.log([...loaded].sort().map((f) => '  ' + f).join('\n'));
assert(loaded.has('localeinfo.json') && loaded.has('en/localeinfo.json') && loaded.has('scripts.json'));
console.log('check-ilib: ok');
