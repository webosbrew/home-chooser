// Where Home Chooser keeps its choice and boot entry on the TV, and the shell commands that
// manage them. See docs/settings-app.md ("Persistence").

export const APP_DIR = '/media/developer/apps/usr/palm/applications/org.webosbrew.homechooser';
export const BACKPORT_DIR = `${APP_DIR}/backport`;
// The scripts inside the installed app. The init.d entry is a symlink to ENTRY_SCRIPT.
export const ENTRY_SCRIPT = `${BACKPORT_DIR}/homechooser`;
export const KEYFILTER_SCRIPT = `${BACKPORT_DIR}/homechooser-keyfilter`;
export const WATCH_SCRIPT = `${BACKPORT_DIR}/homechooser-home-watch`;

export const INIT_DIR = '/var/lib/webosbrew/init.d';
export const INIT_PATH = `${INIT_DIR}/homechooser`;
// Earlier design: a copy of the key filter script. There is only the one entry now.
export const OLD_INIT_PATH = `${INIT_DIR}/homechooser-keyfilter`;

export const STATE_DIR = '/var/lib/webosbrew/homechooser';
// The stored choice: the app id alone, nothing else.
export const HOME_FILE = `${STATE_DIR}/home`;
// Exists while the key filter backport is enabled (webOS 6 to 9).
export const KEYFILTER_FLAG = `${STATE_DIR}/keyfilter`;

export const WATCH_UNIT = 'homechooser-home';
export const WATCH_LOG = '/tmp/homechooser-home.log';

// The "commands" the logic layer hands to platform.exec. They are names, not shell text: the
// service runs a fixed argv for each (service/src/policy.c) and nothing else.
//
// linkEntry: make init.d/homechooser a symlink to the script in the app (a regular file from an
// earlier install is replaced; the earlier homechooser-keyfilter entry is removed). The scripts keep
// their executable bit: run-parts follows the link but needs the target executable.
export const ensureEntryCommand = () => 'linkEntry';

export const removeEntryCommand = () => 'unlinkEntry';

// Start the watcher unless it is running (the entry script runs systemctl is-active, else systemd-run).
export const startWatcherCommand = () => 'watch';

export const stopWatcherCommand = () => 'stopWatcher';

export const keyfilterCommand = (arg) => 'keyfilter' + arg[0].toUpperCase() + arg.slice(1);

// Reads: the entry link (entry=link:<target> | dangling:<target> | file | none), the watcher state,
// the stored choice, the key filter flag and the tail of the watcher log, as the lines
// parsePersistenceStatus reads. The platform assembles them from the service's methods.
export const persistenceStatusCommand = () => 'persistenceStatus';

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
