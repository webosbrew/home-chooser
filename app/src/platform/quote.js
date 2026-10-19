// POSIX shell single-quote escaping: wrap in '...' and turn each ' into '\''.
export const shellQuote = (value) => "'" + String(value).replace(/'/g, "'\\''") + "'";

// Quote only when the word holds characters the shell treats specially.
export const shellWord = (value) => (/^[A-Za-z0-9_.:/@%+=,-]+$/.test(value) ? value : shellQuote(value));

// UTF-8 safe base64 that works in browsers (Chromium 68) and in Node/jsdom.
export const toBase64 = (text) => {
	const bytes = unescape(encodeURIComponent(text));
	return btoa(bytes);
};
