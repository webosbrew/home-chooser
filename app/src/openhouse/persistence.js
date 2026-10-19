// Where Open House keeps its choice and boot entry on the TV, and the shell commands that
// manage them. See docs/settings-app.md ("Persistence").
import {shellQuote} from '../platform/quote';

export const APP_DIR = '/media/developer/apps/usr/palm/applications/org.webosbrew.openhouse';
export const BACKPORT_DIR = `${APP_DIR}/backport`;
// The scripts inside the installed app. The init.d entry is a symlink to ENTRY_SCRIPT.
export const ENTRY_SCRIPT = `${BACKPORT_DIR}/openhouse`;
export const KEYFILTER_SCRIPT = `${BACKPORT_DIR}/openhouse-keyfilter`;
export const WATCH_SCRIPT = `${BACKPORT_DIR}/openhouse-home-watch`;

export const INIT_DIR = '/var/lib/webosbrew/init.d';
export const INIT_PATH = `${INIT_DIR}/openhouse`;
// Earlier design: a copy of the key filter script. There is only the one entry now.
export const OLD_INIT_PATH = `${INIT_DIR}/openhouse-keyfilter`;

export const STATE_DIR = '/var/lib/webosbrew/openhouse';
// The stored choice: the app id alone, nothing else.
export const HOME_FILE = `${STATE_DIR}/home`;
// Exists while the key filter backport is enabled (webOS 6 to 9).
export const KEYFILTER_FLAG = `${STATE_DIR}/keyfilter`;

export const WATCH_UNIT = 'openhouse-home';
export const WATCH_LOG = '/tmp/openhouse-home.log';

const q = shellQuote;

// Make init.d/openhouse a symlink to the script in the app (a regular file from an earlier
// install is replaced; the earlier openhouse-keyfilter entry is removed). The scripts keep
// their executable bit: run-parts follows the link but needs the target executable.
export const ensureEntryCommand = () =>
	`mkdir -p ${q(INIT_DIR)} && rm -f ${q(OLD_INIT_PATH)} && ` +
	`chmod 755 ${q(ENTRY_SCRIPT)} ${q(KEYFILTER_SCRIPT)} ${q(WATCH_SCRIPT)} && ` +
	`{ [ -L ${q(INIT_PATH)} ] || rm -f ${q(INIT_PATH)}; } && ln -sf ${q(ENTRY_SCRIPT)} ${q(INIT_PATH)}`;

export const removeEntryCommand = () => `rm -f ${q(INIT_PATH)} ${q(OLD_INIT_PATH)}`;

// Start the watcher unless it is running (the entry script runs systemctl is-active, else systemd-run).
export const startWatcherCommand = () => `sh ${q(ENTRY_SCRIPT)} watch`;

export const stopWatcherCommand = () => `systemctl stop ${WATCH_UNIT} || true`;

// Prints entry=link:<target> | dangling:<target> | file | none, and the watcher state.
export const persistenceStatusCommand = () =>
	`if [ -L ${q(INIT_PATH)} ]; then t=$(readlink ${q(INIT_PATH)}); ` +
	`if [ -e ${q(INIT_PATH)} ]; then echo "entry=link:$t"; else echo "entry=dangling:$t"; fi; ` +
	`elif [ -e ${q(INIT_PATH)} ]; then echo entry=file; else echo entry=none; fi; ` +
	`echo "active=$(systemctl is-active ${WATCH_UNIT} 2>/dev/null)"; ` +
	`echo "stored=$(cat ${q(HOME_FILE)} 2>/dev/null)"; ` +
	`echo "keyfilter=$([ -f ${q(KEYFILTER_FLAG)} ] && echo yes || echo no)"; ` +
	`echo --log; tail -n 5 ${q(WATCH_LOG)} 2>/dev/null; true`;

export const parsePersistenceStatus = (text) => {
	const out = {entry: 'none', entryTarget: null, active: '', stored: '', keyfilterEnabled: false, log: []};
	let inLog = false;
	String(text || '').split('\n').forEach((line) => {
		if (inLog) {
			if (line.trim()) out.log.push(line);
			return;
		}
		if (line === '--log') {
			inLog = true;
			return;
		}
		let m = /^entry=(link|dangling):(.*)$/.exec(line);
		if (m) {
			out.entryTarget = m[2];
			out.entry = m[1] === 'dangling' ? 'dangling' : (m[2] === ENTRY_SCRIPT ? 'ok' : 'other');
			return;
		}
		m = /^entry=(file|none)$/.exec(line);
		if (m) out.entry = m[1];
		m = /^active=(.*)$/.exec(line);
		if (m) out.active = m[1].trim();
		m = /^stored=(.*)$/.exec(line);
		if (m) out.stored = m[1].trim();
		m = /^keyfilter=(.*)$/.exec(line);
		if (m) out.keyfilterEnabled = m[1] === 'yes';
	});
	return out;
};
