// SPDX-License-Identifier: Apache-2.0
// Failures carry the service reply in `reply`; `message` is the text a user should see.

export const replyText = (reply) => {
	if (!reply || typeof reply !== 'object') return String(reply);
	const parts = [];
	if (reply.errorText) parts.push(reply.errorText);
	if (reply.stderrString && reply.stderrString.trim()) parts.push(reply.stderrString.trim());
	if (parts.length === 0) parts.push(JSON.stringify(reply));
	return parts.join(': ');
};

export class ServiceError extends Error {
	constructor(reply, context) {
		super((context ? context + ': ' : '') + replyText(reply));
		this.name = 'ServiceError';
		this.reply = reply;
	}
}

export const errorMessage = (e) => (e && e.message) || String(e);
// The service reply behind an error, when there is one.
export const errorReply = (e) => (e && e.reply) || null;

// The apply and refresh commands exit 1 when the Home key filter could not be activated, and the service answers a command
// that exits non-zero with a failed reply ({returnValue:false, errorCode:'exitStatus', stdoutString}). These reasons printed
// on its stdout are what the owner can act on (another app saved the key filter list persistently); any other failure
// stays a failure. Returns the reason, or null.
export const OVERRIDE_REASONS = ['persistent-override', 'override-not-cleared', 'override-clear-failed'];
export const overrideReasonOf = (e) => {
	const reply = errorReply(e);
	const out = reply && typeof reply.stdoutString === 'string' ? reply.stdoutString : '';
	const m = /^reason=(.*)$/m.exec(out);
	const reason = m ? m[1].trim() : null;
	return OVERRIDE_REASONS.includes(reason) ? reason : null;
};
