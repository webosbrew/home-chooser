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
