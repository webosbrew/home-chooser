/* org.webosbrew.homechooser.service: the narrow root service behind the Home Chooser app.
 * See docs/service.md. All decisions live in policy.c; this file is luna-service2, pbnjson and
 * the system calls around them. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <glib.h>
#include <luna-service2/lunaservice.h>
#include <PmLogLib.h>
#include <pbnjson.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include "policy.h"

/* From app/package.json, the one source of the version (service/Makefile). */
#ifndef HC_VERSION
#error "HC_VERSION is set by service/Makefile from app/package.json"
#endif

#define CALL_TIMEOUT_S 20
#define FILE_MAX_BYTES (256 * 1024)
#define ICON_MAX_BYTES 262144
#define OUTPUT_MAX_BYTES (64 * 1024)
#define LOG_LINES 5

static GMainLoop *g_loop;
static LSHandle *g_handle;
static PmLogContext hc_log;

#define LOGI(id, ...) PmLogInfo(hc_log, id, 0, __VA_ARGS__)
#define LOGE(id, ...) PmLogError(hc_log, id, 0, __VA_ARGS__)

/* ---- JSON helpers (pbnjson) ---- */

static raw_buffer key_buf(const char *k) { return j_str_to_buffer(k, strlen(k)); }

static jvalue_ref parse_object(const char *text)
{
	jvalue_ref v = jdom_create(j_str_to_buffer(text ? text : "", text ? strlen(text) : 0), jschema_all(), NULL);
	if (!jis_valid(v) || !jis_object(v)) {
		j_release(&v);
		return NULL;
	}
	return v;
}

/* Copy of a string member; NULL when absent or not a string. Free with g_free. */
static char *get_string(jvalue_ref obj, const char *key)
{
	jvalue_ref v = jobject_get(obj, key_buf(key));
	if (!jis_valid(v) || !jis_string(v)) return NULL;
	raw_buffer b = jstring_get_fast(v);
	return g_strndup(b.m_str, b.m_len);
}

static void put_str(jvalue_ref o, const char *k, const char *v) { jobject_set(o, key_buf(k), jstring_create(v ? v : "")); }
static void put_bool(jvalue_ref o, const char *k, bool v) { jobject_set(o, key_buf(k), jboolean_create(v)); }
static void put_int(jvalue_ref o, const char *k, int v) { jobject_set(o, key_buf(k), jnumber_create_i32(v)); }

/* Send and release the reply object. */
static void send_reply(LSMessage *msg, jvalue_ref obj)
{
	LSError e;
	LSErrorInit(&e);
	if (!LSMessageReply(g_handle, msg, jvalue_stringify(obj), &e)) {
		LOGE("REPLY_FAILED", "%s", e.message ? e.message : "?");
		LSErrorFree(&e);
	}
	j_release(&obj);
}

static void send_error(LSMessage *msg, const char *code, const char *fmt, ...)
{
	char text[512];
	va_list ap;
	va_start(ap, fmt);
	vsnprintf(text, sizeof text, fmt, ap);
	va_end(ap);
	jvalue_ref o = jobject_create();
	put_bool(o, "returnValue", false);
	put_str(o, "errorCode", code);
	put_str(o, "errorText", text);
	send_reply(msg, o);
}

static void send_ok(LSMessage *msg)
{
	jvalue_ref o = jobject_create();
	put_bool(o, "returnValue", true);
	send_reply(msg, o);
}

/* Sender check, then (for privileged methods) the root check. Replies and returns false on refusal. */
static bool gate(LSMessage *msg, const char *method, bool need_root)
{
	const char *app = LSMessageGetApplicationID(msg);
	if (!hc_sender_ok(app)) {
		const char *svc = LSMessageGetSenderServiceName(msg);
		LOGE("REFUSED", "%s from application id '%s', service name '%s'", method, app ? app : "(none)", svc ? svc : "(none)");
		send_error(msg, "forbidden", "Only the Home Chooser app may call this service");
		return false;
	}
	if (need_root && geteuid() != 0) {
		LOGI("NOT_ROOT", "%s refused: uid %d", method, (int)geteuid());
		send_error(msg, "notRoot", "The service is not running as root; elevate it through Homebrew Channel");
		return false;
	}
	LOGI("CALL", "%s", method);
	return true;
}

/* ---- status, quit ---- */

static bool on_status(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	if (!gate(msg, "status", false)) return true;
	jvalue_ref o = jobject_create();
	put_bool(o, "returnValue", true);
	put_bool(o, "root", geteuid() == 0);
	put_int(o, "uid", (int)geteuid());
	put_str(o, "version", HC_VERSION);
	put_int(o, "apiVersion", HC_API_VERSION);
	send_reply(msg, o);
	return true;
}

static gboolean do_quit(gpointer data)
{
	(void)data;
	g_main_loop_quit(g_loop);
	return FALSE;
}

static bool on_quit(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	if (!gate(msg, "quit", false)) return true;
	send_ok(msg);
	g_timeout_add(300, do_quit, NULL); /* let the reply out first */
	return true;
}

/* ---- call: an allow-listed luna call ---- */

/* All privileged calls use the fixed luna-send identity, including the settings app's
 * forwarding and icon lookup. No call runs under this service's own hub identity. */
static int luna_run(const char *, const char *, int, int, int (*)(const char *), GString *);

typedef struct {
	LSMessage *msg;
	char *uri;
	char *icon_app;
} Pending;

static void pending_free(Pending *p)
{
	LSMessageUnref(p->msg);
	g_free(p->uri);
	g_free(p->icon_app);
	g_free(p);
}

static void forward_reply(Pending *p, jvalue_ref parsed)
{
	jvalue_ref o = jobject_create();
	put_bool(o, "returnValue", true);
	jobject_set(o, key_buf("reply"), jvalue_duplicate(parsed));
	send_reply(p->msg, o);
}

static void start_call(Pending *p, const char *payload, void (*cb)(Pending *, jvalue_ref))
{
	GString *out = g_string_new(NULL);
	int code = luna_run(p->uri, payload, 1, CALL_TIMEOUT_S, NULL, out);
	jvalue_ref parsed = code == 0 ? parse_object(out->str) : NULL;
	if (!parsed) {
		LOGE("CALL_FAILED", "%s (exit %d)", p->uri, code);
		send_error(p->msg, code == -2 ? "timeout" : "badReply", "No JSON reply from %s (exit %d)", p->uri, code);
	} else {
		cb(p, parsed);
	}
	j_release(&parsed);
	g_string_free(out, TRUE);
	pending_free(p);
}

/* The member names of an object / the strings of an array, up to max; returns the count or -1. */
static int collect_keys(jvalue_ref v, bool names, char **out, int max)
{
	int n = 0;
	if (names) {
		if (!jis_object(v)) return -1;
		jobject_iter it;
		jobject_key_value kv;
		jobject_iter_init(&it, v);
		while (jobject_iter_next(&it, &kv)) {
			if (n >= max) return -1;
			raw_buffer b = jstring_get_fast(kv.key);
			out[n++] = g_strndup(b.m_str, b.m_len);
		}
	} else {
		if (!jis_array(v)) return -1;
		ssize_t len = jarray_size(v);
		for (ssize_t i = 0; i < len; i++) {
			jvalue_ref s = jarray_get(v, i);
			if (n >= max || !jis_string(s)) return -1;
			raw_buffer b = jstring_get_fast(s);
			out[n++] = g_strndup(b.m_str, b.m_len);
		}
	}
	return n;
}

static bool settings_payload_ok(hc_call_kind kind, jvalue_ref payload)
{
	char *category = get_string(payload, "category");
	char *keys[8] = {0};
	jvalue_ref list = jobject_get(payload, key_buf(kind == HC_CALL_SETTINGS_GET ? "keys" : "settings"));
	int n = jis_valid(list) ? collect_keys(list, kind == HC_CALL_SETTINGS_SET, keys, 8) : -1;
	bool ok = n >= 0 && hc_settings_ok(category, (const char *const *)keys, (size_t)n);
	for (int i = 0; i < 8; i++) g_free(keys[i]);
	g_free(category);
	return ok;
}

static bool on_call(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	if (!gate(msg, "call", true)) return true;
	jvalue_ref req = parse_object(LSMessageGetPayload(msg));
	char *uri = req ? get_string(req, "uri") : NULL;
	if (!uri) {
		send_error(msg, "badRequest", "call needs a uri");
		j_release(&req);
		return true;
	}
	hc_call_kind kind = hc_call_lookup(uri);
	jvalue_ref inner = jobject_get(req, key_buf("payload"));
	if (!jis_valid(inner) || jis_null(inner)) inner = NULL;
	if (kind == HC_CALL_DENIED) {
		LOGE("CALL_DENIED", "%s", uri);
		send_error(msg, "notAllowed", "%s is not on the list of calls this service makes", uri);
	} else if (inner && !jis_object(inner)) {
		send_error(msg, "badRequest", "payload must be an object");
	} else if ((kind == HC_CALL_SETTINGS_GET || kind == HC_CALL_SETTINGS_SET) && (!inner || !settings_payload_ok(kind, inner))) {
		LOGE("CALL_DENIED", "%s with a payload outside general/defaultApps", uri);
		send_error(msg, "notAllowed", "%s is limited to category general, key defaultApps", uri);
	} else {
		Pending *p = g_new0(Pending, 1);
		p->msg = msg;
		LSMessageRef(msg);
		p->uri = uri;
		uri = NULL;
		LOGI("CALL_FORWARD", "%s", p->uri);
		start_call(p, inner ? jvalue_stringify(inner) : "{}", forward_reply);
	}
	g_free(uri);
	j_release(&req);
	return true;
}

/* ---- files ---- */

static bool mkdir_p(const char *dir)
{
	char buf[512];
	if (strlen(dir) >= sizeof buf) return false;
	strcpy(buf, dir);
	for (char *p = buf + 1; *p; p++) {
		if (*p != '/') continue;
		*p = '\0';
		if (mkdir(buf, 0755) != 0 && errno != EEXIST) return false;
		*p = '/';
	}
	return mkdir(buf, 0755) == 0 || errno == EEXIST;
}

/* Reads up to max bytes of path into a g_malloc'ed, NUL-terminated buffer. -1 on error (errno set). */
static ssize_t slurp(const char *path, size_t max, char **out, bool *too_big)
{
	int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
	if (fd < 0) return -1;
	struct stat st;
	if (fstat(fd, &st) != 0 || !S_ISREG(st.st_mode)) {
		close(fd);
		errno = EINVAL;
		return -1;
	}
	if ((size_t)st.st_size > max) {
		close(fd);
		if (too_big) *too_big = true;
		errno = EFBIG;
		return -1;
	}
	char *buf = g_malloc((size_t)st.st_size + 1);
	size_t got = 0;
	while (got < (size_t)st.st_size) {
		ssize_t r = read(fd, buf + got, (size_t)st.st_size - got);
		if (r < 0 && errno == EINTR) continue;
		if (r <= 0) break;
		got += (size_t)r;
	}
	close(fd);
	buf[got] = '\0';
	*out = buf;
	return (ssize_t)got;
}

static char *request_path(LSMessage *msg, jvalue_ref req, const char *method, bool reading)
{
	char *path = get_string(req, "path");
	if (!(reading ? hc_file_readable(path) : hc_file_allowed(path))) {
		LOGE("PATH_DENIED", "%s %s", method, path ? path : "(none)");
		send_error(msg, "notAllowed", "%s is not a path this service touches", path ? path : "(none)");
		g_free(path);
		return NULL;
	}
	return path;
}

static bool on_read_file(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	if (!gate(msg, "readFile", true)) return true;
	jvalue_ref req = parse_object(LSMessageGetPayload(msg));
	char *path = req ? request_path(msg, req, "readFile", true) : NULL;
	if (req && path) {
		char *buf = NULL;
		const char *open_path = path;
		char real[PATH_MAX];
		ssize_t n;
		if (strcmp(path, HC_OSRELEASE) == 0) {
			/* Read-only and a symlink on webOS 6+: resolve it, accept two known targets, and open
			 * the resolved path (no symlinks left, so O_NOFOLLOW still holds). */
			if (!realpath(path, real)) {
				n = -1;
			} else if (!hc_osrelease_target_ok(real)) {
				LOGE("PATH_DENIED", "readFile %s resolves to %s", path, real);
				send_error(msg, "notAllowed", "%s resolves outside the allowed targets", path);
				g_free(path);
				j_release(&req);
				return true;
			} else {
				open_path = real;
				n = 0;
			}
		} else {
			n = 0;
		}
		if (n == 0) n = slurp(open_path, 65536, &buf, NULL);
		if (n < 0 && errno == ENOENT) {
			jvalue_ref o = jobject_create();
			put_bool(o, "returnValue", true);
			put_bool(o, "exists", false);
			send_reply(msg, o);
		} else if (n < 0) {
			send_error(msg, "readFailed", "Could not read %s: %s", path, strerror(errno));
		} else {
			jvalue_ref o = jobject_create();
			put_bool(o, "returnValue", true);
			put_bool(o, "exists", true);
			put_str(o, "text", buf);
			send_reply(msg, o);
			g_free(buf);
		}
	} else if (!req) {
		send_error(msg, "badRequest", "readFile needs a JSON object");
	}
	g_free(path);
	j_release(&req);
	return true;
}

static bool write_atomic(const char *path, const char *text, size_t len)
{
	char dir[512], tmp[540];
	if (strlen(path) >= sizeof dir) return false;
	strcpy(dir, path);
	char *slash = strrchr(dir, '/');
	if (!slash) return false;
	*slash = '\0';
	if (!mkdir_p(dir)) return false;
	snprintf(tmp, sizeof tmp, "%s.tmp", path);
	unlink(tmp);
	int fd = open(tmp, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0644);
	if (fd < 0) return false;
	size_t done = 0;
	while (done < len) {
		ssize_t w = write(fd, text + done, len - done);
		if (w < 0 && errno == EINTR) continue;
		if (w <= 0) {
			close(fd);
			unlink(tmp);
			return false;
		}
		done += (size_t)w;
	}
	fchmod(fd, 0644);
	fsync(fd);
	close(fd);
	if (rename(tmp, path) != 0) {
		unlink(tmp);
		return false;
	}
	return true;
}

static bool on_write_file(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	if (!gate(msg, "writeFile", true)) return true;
	jvalue_ref req = parse_object(LSMessageGetPayload(msg));
	char *path = req ? request_path(msg, req, "writeFile", false) : NULL;
	if (req && path) {
		char *text = get_string(req, "text");
		if (!text) {
			send_error(msg, "badRequest", "writeFile needs a text string");
		} else if (strlen(text) > FILE_MAX_BYTES) {
			send_error(msg, "tooBig", "text is over %d bytes", FILE_MAX_BYTES);
		} else if (!write_atomic(path, text, strlen(text))) {
			send_error(msg, "writeFailed", "Could not write %s: %s", path, strerror(errno));
		} else {
			LOGI("WROTE", "%s (%d bytes)", path, (int)strlen(text));
			send_ok(msg);
		}
		g_free(text);
	} else if (!req) {
		send_error(msg, "badRequest", "writeFile needs a JSON object");
	}
	g_free(path);
	j_release(&req);
	return true;
}

static bool on_remove_file(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	if (!gate(msg, "removeFile", true)) return true;
	jvalue_ref req = parse_object(LSMessageGetPayload(msg));
	char *path = req ? request_path(msg, req, "removeFile", false) : NULL;
	if (req && path) {
		if (unlink(path) != 0 && errno != ENOENT) {
			send_error(msg, "fileRemoveFailed", "Could not remove %s: %s", path, strerror(errno));
		} else {
			LOGI("REMOVED", "%s", path);
			send_ok(msg);
		}
	} else if (!req) {
		send_error(msg, "badRequest", "removeFile needs a JSON object");
	}
	g_free(path);
	j_release(&req);
	return true;
}

/* ---- the boot entry link ---- */

static bool on_link(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	if (!gate(msg, "link", true)) return true;
	static const char *const scripts[] = {HC_ENTRY_SCRIPT, HC_HOMEKEY_SCRIPT, HC_WATCH_SCRIPT, HC_ACCESS_SCRIPT, NULL};
	if (!mkdir_p(HC_INIT_DIR)) {
		send_error(msg, "linkFailed", "Could not create %s: %s", HC_INIT_DIR, strerror(errno));
		return true;
	}
	for (int i = 0; scripts[i]; i++) {
		if (chmod(scripts[i], 0755) != 0) {
			send_error(msg, "linkFailed", "Could not make %s executable: %s", scripts[i], strerror(errno));
			return true;
		}
	}
	/* a link is replaced, a regular file from an earlier install too */
	if (unlink(HC_INIT_LINK) != 0 && errno != ENOENT) {
		send_error(msg, "linkFailed", "Could not replace %s: %s", HC_INIT_LINK, strerror(errno));
		return true;
	}
	if (symlink(HC_ENTRY_SCRIPT, HC_INIT_LINK) != 0) {
		send_error(msg, "linkFailed", "Could not link %s: %s", HC_INIT_LINK, strerror(errno));
		return true;
	}
	LOGI("LINKED", "%s -> %s", HC_INIT_LINK, HC_ENTRY_SCRIPT);
	send_ok(msg);
	return true;
}

static bool on_unlink(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	if (!gate(msg, "unlink", true)) return true;
	bool ok = true;
	if (unlink(HC_INIT_LINK) != 0 && errno != ENOENT) ok = false;
	if (!ok) {
		send_error(msg, "unlinkFailed", "Could not remove the boot entry: %s", strerror(errno));
		return true;
	}
	LOGI("UNLINKED", "%s", HC_INIT_LINK);
	send_ok(msg);
	return true;
}

/* entry: none | file | link | dangling, and the link target. */
static bool on_link_status(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	if (!gate(msg, "linkStatus", true)) return true;
	struct stat st;
	char target[512] = "";
	const char *entry = "none";
	if (lstat(HC_INIT_LINK, &st) == 0) {
		if (S_ISLNK(st.st_mode)) {
			ssize_t n = readlink(HC_INIT_LINK, target, sizeof target - 1);
			target[n > 0 ? n : 0] = '\0';
			entry = stat(HC_INIT_LINK, &st) == 0 ? "link" : "dangling";
		} else {
			entry = "file";
		}
	}
	jvalue_ref o = jobject_create();
	put_bool(o, "returnValue", true);
	put_str(o, "entry", entry);
	put_str(o, "target", target);
	send_reply(msg, o);
	return true;
}

/* ---- run: fixed commands ---- */

static void sanitize(char *s, size_t n)
{
	for (size_t i = 0; i < n; i++) {
		unsigned char c = (unsigned char)s[i];
		if (c == '\n' || c == '\t' || (c >= 0x20 && c < 0x7f)) continue;
		s[i] = c == '\r' ? ' ' : '?';
	}
}

static void drain(int fd, GString *dst)
{
	char buf[4096];
	for (;;) {
		ssize_t r = read(fd, buf, sizeof buf);
		if (r < 0 && errno == EINTR) continue;
		if (r <= 0) return;
		if (dst->len < OUTPUT_MAX_BYTES) g_string_append_len(dst, buf, (gssize)MIN((size_t)r, OUTPUT_MAX_BYTES - dst->len));
	}
}

/* Runs argv without a shell. Returns the exit status, -1 on a spawn failure, -2 on timeout. `done`,
 * when given, sees the output so far on every poll; once it says non-zero the child is killed and
 * the result is 0 (a stream the caller has read to its end). */
static int run_argv(const char *const *argv, int timeout_s, GString *out, GString *err, int (*done)(const char *))
{
	int po[2], pe[2];
	if (pipe2(po, O_CLOEXEC | O_NONBLOCK) != 0) return -1;
	if (pipe2(pe, O_CLOEXEC | O_NONBLOCK) != 0) {
		close(po[0]); close(po[1]);
		return -1;
	}
	pid_t pid = fork();
	if (pid < 0) {
		close(po[0]); close(po[1]); close(pe[0]); close(pe[1]);
		return -1;
	}
	if (pid == 0) {
		static char *const envp[] = {"PATH=/usr/sbin:/usr/bin:/sbin:/bin", "LANG=C", "HOME=/root", NULL};
		int nul = open("/dev/null", O_RDONLY);
		dup2(nul, 0);
		dup2(po[1], 1);
		dup2(pe[1], 2);
		/* Readers poll without blocking; the writer must wait rather than lose a large reply. */
		fcntl(1, F_SETFL, fcntl(1, F_GETFL) & ~O_NONBLOCK);
		fcntl(2, F_SETFL, fcntl(2, F_GETFL) & ~O_NONBLOCK);
		for (int fd = 3; fd < 1024; fd++) close(fd);
		execve(argv[0], (char *const *)argv, envp);
		_exit(127);
	}
	close(po[1]);
	close(pe[1]);
	time_t deadline = time(NULL) + timeout_s;
	int status = 0, result = -2;
	for (;;) {
		struct pollfd fds[2] = {{po[0], POLLIN, 0}, {pe[0], POLLIN, 0}};
		poll(fds, 2, 100);
		drain(po[0], out);
		drain(pe[0], err);
		pid_t w = waitpid(pid, &status, WNOHANG);
		if (w == pid) {
			/* whatever a background child still holds open is not waited for */
			drain(po[0], out);
			drain(pe[0], err);
			result = WIFEXITED(status) ? WEXITSTATUS(status) : -3;
			break;
		}
		if (done && done(out->str)) {
			kill(pid, SIGKILL);
			waitpid(pid, &status, 0);
			result = 0;
			break;
		}
		if (time(NULL) >= deadline) {
			kill(pid, SIGKILL);
			waitpid(pid, &status, 0);
			break;
		}
	}
	close(po[0]);
	close(pe[0]);
	return result;
}

static bool on_run(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	if (!gate(msg, "run", true)) return true;
	jvalue_ref req = parse_object(LSMessageGetPayload(msg));
	char *name = req ? get_string(req, "command") : NULL;
	const hc_command *cmd = hc_command_lookup(name);
	if (!cmd) {
		LOGE("RUN_DENIED", "%s", name ? name : "(none)");
		send_error(msg, "notAllowed", "%s is not a command this service runs", name ? name : "(none)");
	} else {
		GString *out = g_string_new(NULL), *err = g_string_new(NULL);
		int code = 0;
		if (cmd->native_tail) {
			char *buf = NULL;
			ssize_t n = slurp(HC_WATCH_LOG, FILE_MAX_BYTES, &buf, NULL);
			if (n > 0) {
				size_t from = hc_last_lines(buf, (size_t)n, LOG_LINES);
				g_string_append_len(out, buf + from, (gssize)((size_t)n - from));
			}
			g_free(buf);
		} else {
			LOGI("RUN", "%s", cmd->name);
			code = run_argv(cmd->argv, cmd->timeout_s, out, err, NULL);
		}
		sanitize(out->str, out->len);
		sanitize(err->str, err->len);
		if (code == -2) {
			send_error(msg, "timeout", "%s did not finish in %d seconds", cmd->name, cmd->timeout_s);
		} else if (code < 0) {
			send_error(msg, "spawnFailed", "Could not run %s", cmd->name);
		} else if (code != 0 && !cmd->tolerant) {
			jvalue_ref o = jobject_create();
			put_bool(o, "returnValue", false);
			put_str(o, "errorCode", "exitStatus");
			char text[128];
			snprintf(text, sizeof text, "%s exited with status %d", cmd->name, code);
			put_str(o, "errorText", text);
			put_int(o, "exitCode", code);
			put_str(o, "stdoutString", out->str);
			put_str(o, "stderrString", err->str);
			send_reply(msg, o);
		} else {
			jvalue_ref o = jobject_create();
			put_bool(o, "returnValue", true);
			put_int(o, "exitCode", code);
			put_str(o, "stdoutString", out->str);
			put_str(o, "stderrString", err->str);
			send_reply(msg, o);
		}
		g_string_free(out, TRUE);
		g_string_free(err, TRUE);
	}
	g_free(name);
	j_release(&req);
	return true;
}

/* ---- readIcon: an app's icon, found through getAppInfo ---- */

static void icon_reply(Pending *p, jvalue_ref info)
{
	jvalue_ref inner = info ? jobject_get(info, key_buf("appInfo")) : NULL;
	if (inner && !(jis_valid(inner) && jis_object(inner))) inner = NULL;
	jvalue_ref src = inner ? inner : info;
	char *folder = src ? get_string(src, "folderPath") : NULL;
	char *icon = src ? get_string(src, "icon") : NULL;
	char path[1024], real_icon[PATH_MAX], real_dir[PATH_MAX];
	const char *mime = NULL;
	if (!folder || !icon || !hc_icon_path(folder, icon, path, sizeof path) || !(mime = hc_icon_mime(path))
		|| !realpath(path, real_icon) || !realpath(folder, real_dir) || !hc_path_within(real_icon, real_dir)) {
		LOGI("ICON_NONE", "%s", p->icon_app);
		send_error(p->msg, "noIcon", "No readable icon for %s", p->icon_app);
	} else {
		char *buf = NULL;
		bool big = false;
		ssize_t n = slurp(real_icon, ICON_MAX_BYTES, &buf, &big);
		if (n < 0) {
			send_error(p->msg, big ? "tooBig" : "noIcon", "Could not read the icon of %s", p->icon_app);
		} else {
			size_t outlen = 4 * (((size_t)n + 2) / 3) + 1;
			char *b64 = g_malloc(outlen);
			hc_base64((const unsigned char *)buf, (size_t)n, b64, outlen);
			jvalue_ref o = jobject_create();
			put_bool(o, "returnValue", true);
			put_str(o, "mime", mime);
			put_str(o, "data", b64);
			send_reply(p->msg, o);
			g_free(b64);
			g_free(buf);
		}
	}
	g_free(folder);
	g_free(icon);
}

static bool on_read_icon(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	if (!gate(msg, "readIcon", true)) return true;
	jvalue_ref req = parse_object(LSMessageGetPayload(msg));
	char *app = req ? get_string(req, "appId") : NULL;
	if (!hc_app_id_ok(app)) {
		send_error(msg, "badRequest", "readIcon needs an appId");
	} else {
		Pending *p = g_new0(Pending, 1);
		p->msg = msg;
		LSMessageRef(msg);
		p->uri = g_strdup("luna://com.webos.applicationManager/getAppInfo");
		p->icon_app = app;
		app = NULL;
		jvalue_ref q = jobject_create();
		jobject_set(q, key_buf("id"), jstring_create(p->icon_app));
		start_call(p, jvalue_stringify(q), icon_reply);
		j_release(&q);
	}
	g_free(app);
	j_release(&req);
	return true;
}

/* ---- the launcher API (docs/launcher-api.md) ----
 * getInfo, listApps, listRunning, closeApp, uninstallApp for apps that declare the permission group,
 * and uninstallConfirmed for the confirmation. The caller is the application id the hub reports. */

#define LIST_FIELDS hc_launcher_list_fields
static const char *const RUNNING_FIELDS[] = {"id", "instanceId", "processId", "webProcessId", NULL};

#define SAM_URI "luna://com.webos.applicationManager"
#define TOKEN_TTL_US (120 * G_USEC_PER_SEC)
#define TOKEN_SLOTS 4

typedef struct {
	char token[HC_TOKEN_LEN + 1];
	char id[130];
	char requester[130];
	char folder[512];
	gint64 expires;
	bool live;
} UninstallToken;

static UninstallToken g_tokens[TOKEN_SLOTS];
static int g_token_next;

/* Callback context for the launcher operations. luna-send runs with a bounded timeout. */
typedef struct { void *ctx; int status; } Call;

static void call_start(const char *uri, const char *payload, void (*done)(Call *, jvalue_ref), void *ctx)
{
	Call c = {.ctx = ctx};
	GString *out = g_string_new(NULL);
	int code = luna_run(uri, payload, 1, CALL_TIMEOUT_S, NULL, out);
	c.status = code;
	jvalue_ref parsed = code == 0 ? parse_object(out->str) : NULL;
	if (!parsed) LOGE("CALL_FAILED", "%s (exit %d)", uri, code);
	done(&c, parsed);
	j_release(&parsed);
	g_string_free(out, TRUE);
}

static bool reply_ok(jvalue_ref r)
{
	jvalue_ref rv = r ? jobject_get(r, key_buf("returnValue")) : NULL;
	bool v = false;
	return rv && jis_valid(rv) && jis_boolean(rv) && jboolean_get(rv, &v) == CONV_OK && v;
}

/* A blocking luna-send as HC_LUNA_IDENTITY. Returns the exit status (-2 timeout, -1 spawn failure,
 * 126 uri not on the list). stdout is the reply text. */
static int luna_run(const char *uri, const char *payload, int replies, int timeout_s, int (*done)(const char *), GString *out)
{
	const char *argv[8];
	GString *err = g_string_new(NULL);
	if (!hc_luna_argv(uri, payload, replies, argv)) {
		g_string_free(err, TRUE);
		return 126;
	}
	LOGI("RUN", "luna-send -a %s %s", HC_LUNA_IDENTITY, uri);
	int code = run_argv(argv, timeout_s, out, err, done);
	/* JSON replies retain UTF-8 titles and paths. */
	if (err->len) LOGI("RUN_STDERR", "%s: %s", uri, err->str);
	g_string_free(err, TRUE);
	return code;
}

static char *read_choice(void)
{
	char *buf = NULL;
	return slurp(HC_HOME_FILE, 4096, &buf, NULL) >= 0 ? buf : NULL;
}

static void send_error_for(LSMessage *msg, const char *method, const char *code, const char *text)
{
	LOGI("LAUNCHER_ERROR", "%s: %s: %s", method, code, text);
	jvalue_ref o = jobject_create();
	put_bool(o, "returnValue", false);
	put_int(o, "apiVersion", HC_API_VERSION);
	put_str(o, "errorCode", code);
	put_str(o, "errorText", text);
	send_reply(msg, o);
}

/* One launcher request in flight. */
typedef struct LOp {
	LSMessage *msg;
	const char *method;
	char caller[130];
	char *target;      /* the id parameter */
	bool consent;
	bool force;
	void (*next)(struct LOp *op);
	bool need_consent;
	char *target_title;
	char *target_folder;
	int target_removable;
} LOp;

static void op_free(LOp *op)
{
	LSMessageUnref(op->msg);
	g_free(op->target);
	g_free(op->target_title);
	g_free(op->target_folder);
	g_free(op);
}

/* Root, a real application id, and (need_home) the caller being the selected home. Fills `caller`.
 * Replies and returns false on refusal. */
static bool launcher_gate(LSMessage *msg, const char *method, bool need_root, bool need_home, char *caller, size_t callerlen)
{
	const char *app = LSMessageGetApplicationID(msg);
	if (!hc_caller_id(app, caller, callerlen)) {
		const char *svc = LSMessageGetSenderServiceName(msg);
		LOGE("REFUSED", "%s from application id '%s', service name '%s'", method, app ? app : "(none)", svc ? svc : "(none)");
		send_error_for(msg, method, "forbidden", app && app[0] ? "The caller has a malformed application id" : "This method is for apps; the caller has no application id");
		return false;
	}
	if (need_root && geteuid() != 0) {
		LOGI("NOT_ROOT", "%s refused: uid %d", method, (int)geteuid());
		send_error_for(msg, method, "notRoot", "The service is not running as root; open Home Chooser once to set it up");
		return false;
	}
	if (need_home) {
		char *choice = read_choice();
		bool home = hc_is_selected_home(caller, choice);
		g_free(choice);
		if (!home) {
			LOGE("REFUSED", "%s from %s, which is not the selected home", method, caller);
			send_error_for(msg, method, "notHome", "Only the app selected as Home may call this method");
			return false;
		}
	}
	LOGI("CALL", "%s from %s", method, caller);
	return true;
}

static void consent_done(Call *c, jvalue_ref reply)
{
	LOp *op = c->ctx;
	if (op->need_consent && (!reply || !reply_ok(reply))) {
		char *sam_text = reply ? get_string(reply, "errorText") : NULL;
		const char *code = !reply ? hc_unusable_reply_code(c->status) : "lookupFailed";
		char *message = g_strdup_printf("Could not read the caller's appinfo%s%s", sam_text ? ": " : "", sam_text ? sam_text : "");
		send_error_for(op->msg, op->method, code, message);
		g_free(message);
		g_free(sam_text);
		op_free(op);
		return;
	}
	jvalue_ref info = reply ? jobject_get(reply, key_buf("appInfo")) : NULL;
	if (!info || !jis_valid(info) || !jis_object(info)) info = reply;
	jvalue_ref perms = info ? jobject_get(info, key_buf("requiredPermissions")) : NULL;
	char *folder = info ? get_string(info, "folderPath") : NULL;
	char *file = NULL;
	jvalue_ref file_json = NULL;
	if ((!perms || !jis_valid(perms) || !jis_array(perms)) && folder && folder[0] == '/' && !strstr(folder, "..")) {
		/* getAppInfo did not carry the appinfo's own fields: read the file the app was installed with */
		char *path = g_strdup_printf("%s/appinfo.json", folder);
		ssize_t n = slurp(path, 65536, &file, NULL);
		g_free(path);
		file_json = n >= 0 ? parse_object(file) : NULL;
		perms = file_json ? jobject_get(file_json, key_buf("requiredPermissions")) : NULL;
	}
	GPtrArray *list = g_ptr_array_new_with_free_func(g_free);
	if (perms && jis_valid(perms) && jis_array(perms)) {
		ssize_t len = jarray_size(perms);
		for (ssize_t i = 0; i < len && i < 256; i++) {
			jvalue_ref s = jarray_get(perms, i);
			if (!jis_string(s)) continue;
			raw_buffer b = jstring_get_fast(s);
			g_ptr_array_add(list, g_strndup(b.m_str, b.m_len));
		}
	}
	op->consent = hc_perms_grant((const char *const *)list->pdata, list->len);
	g_ptr_array_free(list, TRUE);
	j_release(&file_json);
	g_free(file);
	g_free(folder);
	if (op->need_consent && !op->consent) {
		LOGE("REFUSED", "%s from %s, whose appinfo does not request %s", op->method, op->caller, HC_GROUP);
		char *message = g_strdup_printf("The app's appinfo.json must list \"%s\" in requiredPermissions", HC_GROUP);
		send_error_for(op->msg, op->method, "noPermission", message);
		g_free(message);
		op_free(op);
		return;
	}
	op->next(op);
}

/* Reads the caller's appinfo for its consent, then runs next(op). Takes over op. */
static void launcher_consent(LOp *op)
{
	jvalue_ref q = jobject_create();
	put_str(q, "id", op->caller);
	call_start(SAM_URI "/getAppInfo", jvalue_stringify(q), consent_done, op);
	j_release(&q);
}

/* Starts a launcher request: gate, optional id parameter, then the consent check. */
static void launcher_begin(LSMessage *msg, const char *method, bool need_root, bool need_home, bool need_consent, bool need_id, void (*next)(LOp *))
{
	char caller[130];
	if (!launcher_gate(msg, method, need_root, need_home, caller, sizeof caller)) return;
	LOp *op = g_new0(LOp, 1);
	op->msg = msg;
	LSMessageRef(msg);
	op->method = method;
	op->next = next;
	op->need_consent = need_consent;
	op->target_removable = -1;
	g_strlcpy(op->caller, caller, sizeof op->caller);
	if (need_id) {
		jvalue_ref req = parse_object(LSMessageGetPayload(msg));
		op->target = req ? get_string(req, "id") : NULL;
		jvalue_ref f = req && strcmp(method, "closeApp") == 0 ? jobject_get(req, key_buf("force")) : NULL;
		bool force = false;
		if (f && jis_valid(f) && jis_boolean(f)) jboolean_get(f, &force);
		op->force = force;
		j_release(&req);
		if (!hc_app_id_ok(op->target)) {
			send_error_for(msg, method, "badRequest", "This method needs an id: the app id");
			op_free(op);
			return;
		}
	}
	launcher_consent(op);
}

/* Keep only the listed fields of each object of an array. */
static jvalue_ref pick_fields(jvalue_ref arr, const char *const *fields)
{
	jvalue_ref out = jarray_create(NULL);
	ssize_t len = arr && jis_valid(arr) && jis_array(arr) ? jarray_size(arr) : 0;
	for (ssize_t i = 0; i < len; i++) {
		jvalue_ref app = jarray_get(arr, i);
		if (!jis_object(app)) continue;
		jvalue_ref o = jobject_create();
		for (int f = 0; fields[f]; f++) {
			jvalue_ref v = jobject_get(app, key_buf(fields[f]));
			if (v && jis_valid(v) && !jis_null(v)) jobject_set(o, key_buf(fields[f]), jvalue_duplicate(v));
		}
		jarray_append(out, o);
	}
	return out;
}

/* SAM uses lowercase pid keys. Publish stable names and numeric types. */
static jvalue_ref pick_running(jvalue_ref arr)
{
	jvalue_ref out = jarray_create(NULL);
	ssize_t len = jarray_size(arr);
	for (ssize_t i = 0; i < len; i++) {
		jvalue_ref item = jarray_get(arr, i);
		if (!jis_object(item)) continue;
		jvalue_ref o = jobject_create();
		for (int f = 0; RUNNING_FIELDS[f]; f++) {
			const char *source = f == 2 ? "processid" : f == 3 ? "webprocessid" : RUNNING_FIELDS[f];
			jvalue_ref v = jobject_get(item, key_buf(source));
			if (!v || !jis_valid(v) || jis_null(v)) continue;
			if (f < 2) {
				if (jis_string(v)) jobject_set(o, key_buf(RUNNING_FIELDS[f]), jvalue_duplicate(v));
			} else if (jis_number(v)) {
				int64_t pid = 0;
				if (jnumber_get_i64(v, &pid) == CONV_OK && pid >= 0)
					jobject_set(o, key_buf(RUNNING_FIELDS[f]), jnumber_create_i64(pid));
			} else if (jis_string(v)) {
				char *value = get_string(item, source);
				char *end = NULL;
				errno = 0;
				long long pid = value ? strtoll(value, &end, 10) : 0;
				if (value && end && end != value && !*end && !errno && pid >= 0)
					jobject_set(o, key_buf(RUNNING_FIELDS[f]), jnumber_create_i64(pid));
				g_free(value);
			}
		}
		jarray_append(out, o);
	}
	return out;
}

static void list_done(Call *c, jvalue_ref reply)
{
	LOp *op = c->ctx;
	bool running = strcmp(op->method, "listRunning") == 0;
	if (!reply || !reply_ok(reply)) {
		char *sam_text = reply ? get_string(reply, "errorText") : NULL;
		const char *code = !reply ? hc_unusable_reply_code(c->status) : hc_sam_refusal_code(false, sam_text);
		char *message = g_strdup_printf("%s%s%s", reply ? "The application manager refused the call" : "No reply from the application manager", sam_text ? ": " : "", sam_text ? sam_text : "");
		send_error_for(op->msg, op->method, code, message);
		g_free(message);
		g_free(sam_text);
	} else if (!jis_array(jobject_get(reply, key_buf(running ? "running" : "apps")))) {
		send_error_for(op->msg, op->method, "badReply", "The application manager did not return an app array");
	} else {
		jvalue_ref o = jobject_create();
		put_bool(o, "returnValue", true);
		put_int(o, "apiVersion", HC_API_VERSION);
		jobject_set(o, key_buf(running ? "running" : "apps"), running ?
			pick_running(jobject_get(reply, key_buf("running"))) : pick_fields(jobject_get(reply, key_buf("apps")), LIST_FIELDS));
		send_reply(op->msg, o);
	}
	op_free(op);
}

static void list_next(LOp *op)
{
	if (strcmp(op->method, "listRunning") == 0) {
		call_start(SAM_URI "/running", "{}", list_done, op);
	} else {
		/* the reply is cut to LIST_FIELDS; asking for just those keeps it small */
		jvalue_ref q = jobject_create();
		jvalue_ref props = jarray_create(NULL);
		for (int i = 0; LIST_FIELDS[i]; i++) jarray_append(props, jstring_create(LIST_FIELDS[i]));
		jobject_set(q, key_buf("properties"), props);
		call_start(SAM_URI "/listApps", jvalue_stringify(q), list_done, op);
		j_release(&q);
	}
}

static bool on_launcher_list_apps(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	launcher_begin(msg, "listApps", true, false, true, false, list_next);
	return true;
}

static bool on_launcher_running(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	launcher_begin(msg, "listRunning", true, false, true, false, list_next);
	return true;
}

/* getInfo: for any app; says what the caller may do. */
static void info_next(LOp *op)
{
	char *choice = read_choice();
	jvalue_ref o = jobject_create();
	put_bool(o, "returnValue", true);
	put_int(o, "apiVersion", HC_API_VERSION);
	put_str(o, "version", HC_VERSION);
	put_bool(o, "root", geteuid() == 0);
	put_str(o, "permission", HC_GROUP);
	put_bool(o, "permitted", op->consent);
	put_bool(o, "isSelectedHome", hc_is_selected_home(op->caller, choice));
	send_reply(op->msg, o);
	g_free(choice);
	op_free(op);
}

static bool on_launcher_get_info(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	launcher_begin(msg, "getInfo", false, false, false, false, info_next);
	return true;
}

/* closeApp */
static void close_done(Call *c, jvalue_ref reply)
{
	LOp *op = c->ctx;
	if (reply && reply_ok(reply)) {
		jvalue_ref o = jobject_create();
		put_bool(o, "returnValue", true);
		put_int(o, "apiVersion", HC_API_VERSION);
		put_str(o, "id", op->target);
		send_reply(op->msg, o);
	} else {
		char *sam_text = reply ? get_string(reply, "errorText") : NULL;
		const char *code = !reply ? hc_unusable_reply_code(c->status) : hc_sam_refusal_code(false, sam_text);
		char *message = g_strdup_printf("%s%s%s", reply ? "The application manager did not close the app" : "No reply from the application manager", sam_text ? ": " : "", sam_text ? sam_text : "");
		send_error_for(op->msg, op->method, code, message);
		g_free(message);
		g_free(sam_text);
	}
	op_free(op);
}

static void close_next(LOp *op)
{
	jvalue_ref q = jobject_create();
	put_str(q, "id", op->target);
	if (op->force) put_bool(q, "forceMode", true);
	call_start(SAM_URI "/closeByAppId", jvalue_stringify(q), close_done, op);
	j_release(&q);
}

static bool on_launcher_close_by_id(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	launcher_begin(msg, "closeApp", true, true, true, true, close_next);
	return true;
}

/* uninstall: look the app up, apply the refusal list, then ask the user. */
static void new_token(char *out)
{
	unsigned char raw[HC_TOKEN_LEN / 2];
	int fd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
	size_t got = 0;
	while (fd >= 0 && got < sizeof raw) {
		ssize_t r = read(fd, raw + got, sizeof raw - got);
		if (r < 0 && errno == EINTR) continue;
		if (r <= 0) break;
		got += (size_t)r;
	}
	if (fd >= 0) close(fd);
	if (got < sizeof raw) { /* no entropy: do not hand out a guessable token */
		out[0] = '\0';
		return;
	}
	for (size_t i = 0; i < sizeof raw; i++) snprintf(out + 2 * i, 3, "%02x", raw[i]);
}

static int never_stop(const char *text) { (void)text; return 0; }

static void uninstall_ask(LOp *op)
{
	UninstallToken *t = &g_tokens[g_token_next++ % TOKEN_SLOTS];
	memset(t, 0, sizeof *t);
	new_token(t->token);
	if (!hc_token_ok(t->token)) {
		send_error_for(op->msg, op->method, "internal", "Could not make a confirmation token");
		return;
	}
	g_strlcpy(t->id, op->target, sizeof t->id);
	g_strlcpy(t->requester, op->caller, sizeof t->requester);
	g_strlcpy(t->folder, op->target_folder ? op->target_folder : "", sizeof t->folder);
	t->expires = g_get_monotonic_time() + TOKEN_TTL_US;
	t->live = true;

	/* The alert: only Uninstall calls the service, with the token. */
	char *question = g_strdup_printf("Uninstall %s?", op->target_title ? op->target_title : op->target);
	jvalue_ref a = jobject_create();
	put_str(a, "sourceId", HC_APP_ID);
	put_str(a, "title", "Home Chooser");
	put_str(a, "message", question);
	jvalue_ref buttons = jarray_create(NULL);
	jvalue_ref yes = jobject_create();
	put_str(yes, "label", "Uninstall");
	put_str(yes, "onclick", "luna://" HC_SERVICE_ID "/uninstallConfirmed");
	jvalue_ref yes_params = jobject_create();
	put_str(yes_params, "token", t->token);
	jobject_set(yes, key_buf("params"), yes_params);
	jarray_append(buttons, yes);
	jvalue_ref no = jobject_create();
	put_str(no, "label", "Cancel");
	put_bool(no, "focus", true);
	jarray_append(buttons, no);
	jobject_set(a, key_buf("buttons"), buttons);
	GString *out = g_string_new(NULL);
	int code = luna_run("luna://com.webos.notification/createAlert", jvalue_stringify(a), 1, 8, never_stop, out);
	bool alert = code == 0 && hc_remove_reply_class(out->str) == 1;
	LOGI("UNINSTALL_ASK", "%s by %s: alert %s (exit %d): %s", op->target, op->caller, alert ? "shown" : "refused", code, out->str);
	g_string_free(out, TRUE);
	j_release(&a);
	g_free(question);

	const char *how = "alert";
	if (!alert) {
		/* Alerts are refused to root on some releases: Home Chooser's own screen asks instead. */
		jvalue_ref l = jobject_create();
		put_str(l, "id", HC_APP_ID);
		jvalue_ref params = jobject_create();
		put_str(params, "confirmUninstall", op->target);
		put_str(params, "token", t->token);
		jobject_set(l, key_buf("params"), params);
		out = g_string_new(NULL);
		code = luna_run("luna://com.webos.applicationManager/launch", jvalue_stringify(l), 1, 15, never_stop, out);
		bool launched = code == 0 && hc_remove_reply_class(out->str) == 1;
		LOGI("UNINSTALL_ASK", "%s: launch the confirmation screen (exit %d): %s", op->target, code, out->str);
		g_string_free(out, TRUE);
		j_release(&l);
		if (!launched) {
			t->live = false;
			send_error_for(op->msg, op->method, "confirmFailed", "Could not ask the user: neither the alert nor the Home Chooser screen could be shown");
			return;
		}
		how = "screen";
	}
	jvalue_ref o = jobject_create();
	put_bool(o, "returnValue", true);
	put_int(o, "apiVersion", HC_API_VERSION);
	put_bool(o, "pending", true);
	put_str(o, "id", op->target);
	put_str(o, "confirmation", how);
	send_reply(op->msg, o);
}

static void uninstall_info_done(Call *c, jvalue_ref reply)
{
	LOp *op = c->ctx;
	jvalue_ref info = reply ? jobject_get(reply, key_buf("appInfo")) : NULL;
	if (!info || !jis_valid(info) || !jis_object(info)) info = reply;
	if (!reply || !reply_ok(reply) || !info) {
		char *sam_text = reply ? get_string(reply, "errorText") : NULL;
		const char *code = !reply ? hc_unusable_reply_code(c->status) : hc_sam_refusal_code(true, sam_text);
		char *message = g_strdup_printf("%s%s%s", !reply ? "No reply from the application manager" :
			strcmp(code, "notFound") == 0 ? "No such app" : "The application manager refused the lookup",
			sam_text ? ": " : "", sam_text ? sam_text : "");
		send_error_for(op->msg, op->method, code, message);
		g_free(message);
		g_free(sam_text);
	} else {
		jvalue_ref rem = jobject_get(info, key_buf("removable"));
		bool removable = true;
		int rflag = -1;
		if (rem && jis_valid(rem) && jis_boolean(rem) && jboolean_get(rem, &removable) == CONV_OK) rflag = removable ? 1 : 0;
		op->target_folder = get_string(info, "folderPath");
		op->target_title = get_string(info, "title");
		hc_remove_verdict v = hc_remove_check(op->target, op->caller, rflag, op->target_folder);
		if (v != HC_REMOVE_OK) {
			LOGE("REFUSED", "uninstallApp %s by %s: %s", op->target, op->caller, hc_remove_verdict_text(v));
			send_error_for(op->msg, op->method, hc_remove_verdict_code(v), hc_remove_verdict_text(v));
		} else {
			uninstall_ask(op);
		}
	}
	op_free(op);
}

static void uninstall_next(LOp *op)
{
	jvalue_ref q = jobject_create();
	put_str(q, "id", op->target);
	call_start(SAM_URI "/getAppInfo", jvalue_stringify(q), uninstall_info_done, op);
	j_release(&q);
}

static bool on_launcher_uninstall(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	launcher_begin(msg, "uninstallApp", true, true, true, true, uninstall_next);
	return true;
}

/* uninstallConfirmed {token}: the user said yes (the alert's button, or Home Chooser's screen). */
static int stop_progress(const char *text) { return hc_progress_state(text) != 0; }

static bool on_uninstall_confirmed(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	const char *app = LSMessageGetApplicationID(msg);
	/* The alert's button comes from the notification service (no application id); the screen
	 * from the Home Chooser app. Any other app is refused, and the token is the capability. */
	if (app && app[0] && !hc_sender_ok(app)) {
		LOGE("REFUSED", "uninstallConfirmed from application id '%s'", app);
		send_error(msg, "forbidden", "Only the confirmation may call this method");
		return true;
	}
	if (geteuid() != 0) {
		send_error(msg, "notRoot", "The service is not running as root");
		return true;
	}
	jvalue_ref req = parse_object(LSMessageGetPayload(msg));
	char *token = req ? get_string(req, "token") : NULL;
	j_release(&req);
	UninstallToken *t = NULL;
	for (int i = 0; hc_token_ok(token) && i < TOKEN_SLOTS; i++) {
		if (g_tokens[i].live && strcmp(g_tokens[i].token, token) == 0) t = &g_tokens[i];
	}
	g_free(token);
	if (!t || g_get_monotonic_time() > t->expires) {
		if (t) t->live = false;
		LOGE("REFUSED", "uninstallConfirmed with an unknown or expired token");
		send_error(msg, "badToken", "The confirmation is unknown, used or expired");
		return true;
	}
	t->live = false; /* one shot */
	char *choice = read_choice();
	bool still = hc_is_selected_home(t->requester, choice);
	g_free(choice);
	if (!still) {
		send_error(msg, "notHome", "The app that asked is no longer the selected Home");
		return true;
	}
	char id[130], folder[512];
	g_strlcpy(id, t->id, sizeof id);
	g_strlcpy(folder, t->folder, sizeof folder);
	const char *route = NULL;
	const char *failure = "The removal failed";
	bool refused = false;
	for (int step = 0; step < 2 && !route; step++) {
		hc_remove_call rc;
		if (!hc_remove_step(step, id, folder, &rc)) break;
		GString *out = g_string_new(NULL);
		int code = luna_run(rc.uri, rc.payload, rc.progress ? 20 : 1, rc.timeout_s, rc.progress ? stop_progress : never_stop, out);
		int state = rc.progress ? hc_progress_state(out->str) : hc_remove_reply_class(out->str);
		LOGI("UNINSTALL", "%s step %d %s (exit %d, state %d): %s", id, step, rc.uri, code, state, out->str);
		g_string_free(out, TRUE);
		if (state == 1) {
			route = step == 0 ? "launchPoint" : "appInstallService";
		} else if (state == 0 && !rc.progress) {
			refused = true; /* LG says it is not removable: no other route */
			break;
		}
	}
	if (route) {
		jvalue_ref o = jobject_create();
		put_bool(o, "returnValue", true);
		put_str(o, "id", id);
		put_str(o, "route", route);
		send_reply(msg, o);
	} else if (refused) {
		send_error_for(msg, "uninstallConfirmed", "notRemovable", "The platform says this app is not removable");
	} else {
		send_error_for(msg, "uninstallConfirmed", "removeFailed", failure);
	}
	return true;
}

/* ---- rebootPrompt / rebootConfirmed ----
 * The patch needs a restart of the TV and nothing else restarts anything while the TV runs. Only
 * Home Chooser can ask (rebootPrompt) and only while the worker reports reboot=needed; the reboot
 * itself goes through tvpowerd (no systemctl fallback: it would skip the power-off sequence and its
 * busy check). The one-shot token is the capability of the alert's button. */

#define REBOOT_TTL_US (600 * G_USEC_PER_SEC)
#define REBOOT_URI "luna://com.webos.service.tvpower/power/reboot"

typedef struct {
	char token[HC_TOKEN_LEN + 1];
	gint64 expires;
	bool live;
} RebootToken;

static RebootToken g_reboot_tokens[TOKEN_SLOTS];
static int g_reboot_next;

/* True when the worker's status says reboot=needed right now. */
static bool reboot_pending(void)
{
	const hc_command *cmd = hc_command_lookup("homekeyStatus");
	if (!cmd) return false;
	GString *out = g_string_new(NULL), *err = g_string_new(NULL);
	int code = run_argv(cmd->argv, cmd->timeout_s, out, err, NULL);
	bool needed = code == 0 && hc_reboot_needed(out->str);
	g_string_free(out, TRUE);
	g_string_free(err, TRUE);
	return needed;
}

/* A luna call that must come back with a true returnValue; reply text is logged. */
static bool reboot_notify(const char *tag, const char *uri, jvalue_ref payload, int timeout_s)
{
	GString *out = g_string_new(NULL);
	int code = luna_run(uri, jvalue_stringify(payload), 1, timeout_s, never_stop, out);
	bool ok = code == 0 && hc_remove_reply_class(out->str) == 1;
	LOGI(tag, "%s %s (exit %d): %s", uri, ok ? "accepted" : "refused", code, out->str);
	g_string_free(out, TRUE);
	return ok;
}

/* rebootPrompt {reason: "apply"|"remove"}: always a system alert (a toast when it is refused); the token never leaves the service */
static bool on_reboot_prompt(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	if (!gate(msg, "rebootPrompt", true)) return true;
	jvalue_ref req = parse_object(LSMessageGetPayload(msg));
	char *reason = req ? get_string(req, "reason") : NULL;
	j_release(&req);
	const char *message = hc_reboot_message(reason);
	g_free(reason);
	if (!reboot_pending()) {
		send_error_for(msg, "rebootPrompt", "notPending", "No restart is pending");
		return true;
	}
	if (!message) {
		send_error_for(msg, "rebootPrompt", "badRequest", "reason must be apply or remove");
		return true;
	}
	RebootToken *t = &g_reboot_tokens[g_reboot_next++ % TOKEN_SLOTS];
	memset(t, 0, sizeof *t);
	new_token(t->token);
	if (!hc_token_ok(t->token)) {
		send_error_for(msg, "rebootPrompt", "internal", "Could not make a confirmation token");
		return true;
	}
	t->expires = g_get_monotonic_time() + REBOOT_TTL_US;
	t->live = true;

	jvalue_ref a = jobject_create();
	put_str(a, "sourceId", HC_APP_ID);
	put_str(a, "title", "Home Chooser");
	put_str(a, "message", message);
	jvalue_ref buttons = jarray_create(NULL);
	jvalue_ref yes = jobject_create();
	put_str(yes, "label", "Reboot");
	put_str(yes, "onclick", "luna://" HC_SERVICE_ID "/rebootConfirmed");
	jvalue_ref yes_params = jobject_create();
	put_str(yes_params, "token", t->token);
	jobject_set(yes, key_buf("params"), yes_params);
	jarray_append(buttons, yes);
	jvalue_ref no = jobject_create();
	put_str(no, "label", "Later");
	put_bool(no, "focus", true);
	jarray_append(buttons, no);
	jobject_set(a, key_buf("buttons"), buttons);
	/* The payload holds the token, so it is not logged; this line says what was asked (the e2e runner reads it). */
	LOGI("REBOOT_ASK", "createAlert request: message: %s; buttons: Reboot (rebootConfirmed), Later (focused)", message);
	bool alert = reboot_notify("REBOOT_ASK", "luna://com.webos.notification/createAlert", a, 8);
	j_release(&a);
	const char *how = "alert";
	if (!alert) {
		t->live = false; /* nobody holds the token */
		jvalue_ref toast = jobject_create();
		put_str(toast, "sourceId", HC_APP_ID);
		put_bool(toast, "noaction", true);
		put_str(toast, "message", message);
		bool shown = reboot_notify("REBOOT_ASK", "luna://com.webos.notification/createToast", toast, 8);
		j_release(&toast);
		if (!shown) {
			send_error_for(msg, "rebootPrompt", "confirmFailed", "Could not ask the user: neither the alert nor the toast could be shown");
			return true;
		}
		how = "toast";
	}
	jvalue_ref o = jobject_create();
	put_bool(o, "returnValue", true);
	put_bool(o, "pending", true);
	put_str(o, "confirm", how);
	send_reply(msg, o);
	return true;
}

/* rebootConfirmed {token}: the user chose Reboot on the alert. */
static bool on_reboot_confirmed(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	const char *app = LSMessageGetApplicationID(msg);
	/* Same rule as uninstallConfirmed: the notification service has no application id. */
	if (app && app[0] && !hc_sender_ok(app)) {
		LOGE("REFUSED", "rebootConfirmed from application id '%s'", app);
		send_error(msg, "forbidden", "Only the confirmation may call this method");
		return true;
	}
	if (geteuid() != 0) {
		send_error(msg, "notRoot", "The service is not running as root");
		return true;
	}
	jvalue_ref req = parse_object(LSMessageGetPayload(msg));
	char *token = req ? get_string(req, "token") : NULL;
	j_release(&req);
	RebootToken *t = NULL;
	for (int i = 0; hc_token_ok(token) && i < TOKEN_SLOTS; i++) {
		if (g_reboot_tokens[i].live && strcmp(g_reboot_tokens[i].token, token) == 0) t = &g_reboot_tokens[i];
	}
	g_free(token);
	if (!t || g_get_monotonic_time() > t->expires) {
		if (t) t->live = false;
		LOGE("REFUSED", "rebootConfirmed with an unknown or expired token");
		send_error(msg, "badToken", "The confirmation is unknown, used or expired");
		return true;
	}
	t->live = false; /* one shot */
	if (!reboot_pending()) {
		send_error_for(msg, "rebootConfirmed", "notPending", "No restart is pending any more");
		return true;
	}
	GString *out = g_string_new(NULL);
	int code = luna_run(REBOOT_URI, "{\"reason\":\"remoteKey\"}", 1, 30, never_stop, out);
	bool ok = code == 0 && hc_remove_reply_class(out->str) == 1;
	LOGI("REBOOT", "%s (exit %d): %s", ok ? "accepted" : "refused", code, out->str);
	g_string_free(out, TRUE);
	if (ok) send_ok(msg);
	else send_error_for(msg, "rebootConfirmed", "rebootFailed", "The TV did not accept the restart");
	return true;
}

/* ---- registration ---- */

static LSMethod methods[] = {
	{"status", on_status, LUNA_METHOD_FLAGS_NONE},
	{"quit", on_quit, LUNA_METHOD_FLAGS_NONE},
	{"call", on_call, LUNA_METHOD_FLAGS_NONE},
	{"readFile", on_read_file, LUNA_METHOD_FLAGS_NONE},
	{"writeFile", on_write_file, LUNA_METHOD_FLAGS_NONE},
	{"removeFile", on_remove_file, LUNA_METHOD_FLAGS_NONE},
	{"readIcon", on_read_icon, LUNA_METHOD_FLAGS_NONE},
	{"link", on_link, LUNA_METHOD_FLAGS_NONE},
	{"unlink", on_unlink, LUNA_METHOD_FLAGS_NONE},
	{"linkStatus", on_link_status, LUNA_METHOD_FLAGS_NONE},
	{"run", on_run, LUNA_METHOD_FLAGS_NONE},
	/* the launcher API: the caller is checked in each handler, not by hc_sender_ok */
	{"getInfo", on_launcher_get_info, LUNA_METHOD_FLAGS_NONE},
	{"listApps", on_launcher_list_apps, LUNA_METHOD_FLAGS_NONE},
	{"listRunning", on_launcher_running, LUNA_METHOD_FLAGS_NONE},
	{"closeApp", on_launcher_close_by_id, LUNA_METHOD_FLAGS_NONE},
	{"uninstallApp", on_launcher_uninstall, LUNA_METHOD_FLAGS_NONE},
	{"uninstallConfirmed", on_uninstall_confirmed, LUNA_METHOD_FLAGS_NONE},
	{"rebootPrompt", on_reboot_prompt, LUNA_METHOD_FLAGS_NONE},
	{"rebootConfirmed", on_reboot_confirmed, LUNA_METHOD_FLAGS_NONE},
	{NULL, NULL, LUNA_METHOD_FLAGS_NONE}
};

int main(void)
{
	signal(SIGPIPE, SIG_IGN);
	PmLogGetContext("HomeChooser", &hc_log);
	LOGI("START", "version %s, uid %d", HC_VERSION, (int)geteuid());

	g_loop = g_main_loop_new(NULL, FALSE);
	LSError e;
	LSErrorInit(&e);
	if (!LSRegister(HC_SERVICE_ID, &g_handle, &e)
		|| !LSRegisterCategory(g_handle, "/", methods, NULL, NULL, &e)
		|| !LSGmainAttach(g_handle, g_loop, &e)) {
		LOGE("REGISTER_FAILED", "%s", e.message ? e.message : "?");
		LSErrorFree(&e);
		return 1;
	}
	g_main_loop_run(g_loop);
	LSUnregister(g_handle, &e);
	LSErrorFree(&e);
	g_main_loop_unref(g_loop);
	LOGI("EXIT", "bye");
	return 0;
}
