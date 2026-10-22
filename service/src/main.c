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

#ifndef HC_VERSION
#define HC_VERSION "0.1.0"
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

typedef struct {
	LSMessage *msg;
	LSMessageToken token;
	guint timer;
	char *uri;
	char *icon_app; /* set for readIcon: the reply is turned into the icon */
} Pending;

static void pending_free(Pending *p)
{
	LSMessageUnref(p->msg);
	g_free(p->uri);
	g_free(p->icon_app);
	g_free(p);
}

static gboolean pending_timeout(gpointer data)
{
	Pending *p = data;
	LSError e;
	LSErrorInit(&e);
	LSCallCancel(g_handle, p->token, &e);
	LSErrorFree(&e);
	LOGE("CALL_TIMEOUT", "%s", p->uri);
	send_error(p->msg, "timeout", "No reply from %s in %d seconds", p->uri, CALL_TIMEOUT_S);
	pending_free(p);
	return FALSE;
}

static bool forward_reply(LSHandle *sh, LSMessage *reply, void *ctx)
{
	(void)sh;
	Pending *p = ctx;
	g_source_remove(p->timer);
	jvalue_ref parsed = parse_object(LSMessageGetPayload(reply));
	if (!parsed) {
		send_error(p->msg, "badReply", "The reply from %s is not a JSON object", p->uri);
	} else {
		jvalue_ref o = jobject_create();
		put_bool(o, "returnValue", true);
		jobject_set(o, key_buf("reply"), parsed);
		send_reply(p->msg, o);
	}
	pending_free(p);
	return true;
}

/* Start the luna call and arm the timeout; takes over `p` (replies with an error itself on failure). */
static void start_call(Pending *p, const char *payload, LSFilterFunc cb)
{
	LSError e;
	LSErrorInit(&e);
	if (!LSCallOneReply(g_handle, p->uri, payload, cb, p, &p->token, &e)) {
		LOGE("CALL_FAILED", "%s: %s", p->uri, e.message ? e.message : "?");
		send_error(p->msg, "callFailed", "Could not call %s: %s", p->uri, e.message ? e.message : "?");
		LSErrorFree(&e);
		pending_free(p);
		return;
	}
	p->timer = g_timeout_add_seconds(CALL_TIMEOUT_S, pending_timeout, p);
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
			send_error(msg, "removeFailed", "Could not remove %s: %s", path, strerror(errno));
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
	static const char *const scripts[] = {HC_ENTRY_SCRIPT, HC_KEYFILTER_SCRIPT, HC_WATCH_SCRIPT, NULL};
	if (!mkdir_p(HC_INIT_DIR)) {
		send_error(msg, "linkFailed", "Could not create %s: %s", HC_INIT_DIR, strerror(errno));
		return true;
	}
	unlink(HC_OLD_INIT_LINK);
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
	if (unlink(HC_OLD_INIT_LINK) != 0 && errno != ENOENT) ok = false;
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

/* Runs argv without a shell. Returns the exit status, -1 on a spawn failure, -2 on timeout. */
static int run_argv(const char *const *argv, int timeout_s, GString *out, GString *err)
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
			code = run_argv(cmd->argv, cmd->timeout_s, out, err);
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

static bool app_id_ok(const char *id)
{
	size_t n = id ? strlen(id) : 0;
	if (n == 0 || n > 128) return false;
	for (size_t i = 0; i < n; i++) {
		char c = id[i];
		if (!g_ascii_isalnum(c) && c != '.' && c != '-' && c != '_') return false;
	}
	return true;
}

static bool icon_reply(LSHandle *sh, LSMessage *reply, void *ctx)
{
	(void)sh;
	Pending *p = ctx;
	g_source_remove(p->timer);
	jvalue_ref info = parse_object(LSMessageGetPayload(reply));
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
	j_release(&info);
	pending_free(p);
	return true;
}

static bool on_read_icon(LSHandle *sh, LSMessage *msg, void *ctx)
{
	(void)sh; (void)ctx;
	if (!gate(msg, "readIcon", true)) return true;
	jvalue_ref req = parse_object(LSMessageGetPayload(msg));
	char *app = req ? get_string(req, "appId") : NULL;
	if (!app_id_ok(app)) {
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
