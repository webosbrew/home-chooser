// Where Home Chooser keeps its choice and boot entry on the TV, and the shell commands that
// manage them. See docs/settings-app.md ("Persistence").

export const APP_DIR = '/media/developer/apps/usr/palm/applications/org.webosbrew.homechooser';
export const SERVICE_DIR = '/media/developer/apps/usr/palm/services/org.webosbrew.homechooser.service';
export const SCRIPTS_DIR = `${SERVICE_DIR}/scripts`;
// The scripts beside the installed service. The init.d entry links to a persistent boot copy.
export const ENTRY_SCRIPT = `${SCRIPTS_DIR}/homechooser`;
export const HOMEKEY_SCRIPT = `${SCRIPTS_DIR}/homechooser-homekey`;
export const WATCH_SCRIPT = `${SCRIPTS_DIR}/homechooser-home-watch`;

export const INIT_DIR = '/var/lib/webosbrew/init.d';
export const INIT_PATH = `${INIT_DIR}/homechooser`;
export const STATE_DIR = '/var/lib/webosbrew/homechooser';
export const BOOT_SCRIPT = `${STATE_DIR}/boot/homechooser`;
// The stored choice: the app id alone, nothing else.
export const HOME_FILE = `${STATE_DIR}/home`;
// Exists while the Home key hook is enabled (webOS 6 to 9).
export const HOMEKEY_FLAG = `${STATE_DIR}/homekey`;

export const WATCH_UNIT = 'homechooser-home';
export const WATCH_LOG = '/tmp/homechooser-home.log';

// The "commands" the logic layer hands to platform.exec. They are names, not shell text: the
// service runs a fixed argv for each (service/src/policy.c) and nothing else.
//
// linkEntry: stage a persistent restore copy and link init.d/homechooser to its boot script.
export const ensureEntryCommand = () => 'linkEntry';

export const removeEntryCommand = () => 'unlinkEntry';

// Start the watcher unless it is running (the entry script runs systemctl is-active, else systemd-run).
export const startWatcherCommand = () => 'watch';

export const stopWatcherCommand = () => 'stopWatcher';

export const homekeyCommand = (arg) => 'homekey' + arg[0].toUpperCase() + arg.slice(1);

// Reads: the entry link (entry=link:<target> | dangling:<target> | file | none), the watcher state,
// the stored choice, the Home key hook flag and the tail of the watcher log, as the lines
// parsePersistenceStatus reads. The platform assembles them from the service's methods.
export const persistenceStatusCommand = () => 'persistenceStatus';

export const parsePersistenceStatus = (text) => {
	const out = {entry: 'none', entryTarget: null, active: '', stored: '', homekeyEnabled: false, log: []};
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
			out.entry = m[1] === 'dangling' ? 'dangling' : (m[2] === BOOT_SCRIPT ? 'ok' : 'other');
			return;
		}
		m = /^entry=(file|none)$/.exec(line);
		if (m) out.entry = m[1];
		m = /^active=(.*)$/.exec(line);
		if (m) out.active = m[1].trim();
		m = /^stored=(.*)$/.exec(line);
		if (m) out.stored = m[1].trim();
		m = /^homekey=(.*)$/.exec(line);
		if (m) out.homekeyEnabled = m[1] === 'yes';
	});
	return out;
};
