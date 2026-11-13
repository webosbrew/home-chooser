/* SPDX-License-Identifier: Apache-2.0 */
#include "nameowner.h"

#include <dirent.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#define ROLE_MAX 262144
#define LIST_MAX 256

bool hc_name_glob(const char *p, const char *s)
{
	const char *star = NULL, *mark = NULL;
	while (*s) {
		if (*p == '*') {
			star = p++;
			mark = s;
		} else if (*p == '?' || *p == *s) {
			p++;
			s++;
		} else if (star) {
			p = star + 1;
			s = ++mark;
		} else {
			return false;
		}
	}
	while (*p == '*') p++;
	return *p == '\0';
}

/* ---- a small JSON reader: strings of chosen keys, nothing else is kept ---- */

typedef struct {
	const char *p;
	bool bad;
} Rd;

/* White space and the comments some firmware role files carry (a license header, the hub accepts them). */
static void ws(Rd *r)
{
	for (;;) {
		while (*r->p == ' ' || *r->p == '\t' || *r->p == '\n' || *r->p == '\r') r->p++;
		if (r->p[0] == '/' && r->p[1] == '*') {
			const char *end = strstr(r->p + 2, "*/");
			if (!end) { r->bad = true; r->p += strlen(r->p); return; }
			r->p = end + 2;
		} else if (r->p[0] == '/' && r->p[1] == '/') {
			while (*r->p && *r->p != '\n') r->p++;
		} else {
			return;
		}
	}
}

/* A string literal at r->p into a malloc'ed copy (escapes \" \\ \/ and \uXXXX below 0x80 are decoded). */
static char *read_string(Rd *r)
{
	if (*r->p != '"') {
		r->bad = true;
		return NULL;
	}
	r->p++;
	size_t cap = 32, n = 0;
	char *out = malloc(cap);
	if (!out) {
		r->bad = true;
		return NULL;
	}
	while (*r->p && *r->p != '"') {
		char c = *r->p++;
		if (c == '\\') {
			c = *r->p++;
			switch (c) {
			case 'n': c = '\n'; break;
			case 't': c = '\t'; break;
			case 'r': c = '\r'; break;
			case 'b': c = '\b'; break;
			case 'f': c = '\f'; break;
			case '"': case '\\': case '/': break;
			case 'u': {
				unsigned v = 0;
				for (int i = 0; i < 4; i++) {
					char h = *r->p;
					if (h >= '0' && h <= '9') v = v * 16 + (unsigned)(h - '0');
					else if (h >= 'a' && h <= 'f') v = v * 16 + (unsigned)(h - 'a' + 10);
					else if (h >= 'A' && h <= 'F') v = v * 16 + (unsigned)(h - 'A' + 10);
					else { r->bad = true; free(out); return NULL; }
					r->p++;
				}
				c = v && v < 0x80 ? (char)v : '?'; /* a name with an escape above ASCII never matches */
				break;
			}
			default:
				r->bad = true;
				free(out);
				return NULL;
			}
		}
		if (n + 2 > cap) {
			char *grown = realloc(out, cap *= 2);
			if (!grown) {
				r->bad = true;
				free(out);
				return NULL;
			}
			out = grown;
		}
		out[n++] = c;
	}
	if (*r->p != '"') {
		r->bad = true;
		free(out);
		return NULL;
	}
	r->p++;
	out[n] = '\0';
	return out;
}

typedef struct {
	char *exe;
	char *appid;
	const char *test;  /* the name to match allowedNames against, as they are read (never stored: lists can be long) */
	bool matched;
	bool names_seen;
	char *lists[3][LIST_MAX]; /* roleFiles, roleFilesPub, roleFilesPrv */
	int nlists[3];
} Found;

static void found_free(Found *f)
{
	free(f->exe);
	free(f->appid);
	for (int k = 0; k < 3; k++)
		for (int i = 0; i < f->nlists[k]; i++) free(f->lists[k][i]);
}

static void skip_value(Rd *r, int depth);

static void skip_container(Rd *r, char close, int depth)
{
	r->p++;
	ws(r);
	if (*r->p == close) {
		r->p++;
		return;
	}
	for (;;) {
		ws(r);
		if (*r->p == close) { r->p++; return; } /* a trailing comma, which the hub accepts */
		if (close == '}') {
			free(read_string(r));
			ws(r);
			if (*r->p != ':') { r->bad = true; return; }
			r->p++;
		}
		skip_value(r, depth + 1);
		if (r->bad) return;
		ws(r);
		if (*r->p == ',') { r->p++; continue; }
		if (*r->p == close) { r->p++; return; }
		r->bad = true;
		return;
	}
}

static void skip_value(Rd *r, int depth)
{
	ws(r);
	if (depth > 16) { r->bad = true; return; }
	if (*r->p == '"') free(read_string(r));
	else if (*r->p == '{') skip_container(r, '}', depth);
	else if (*r->p == '[') skip_container(r, ']', depth);
	else if (*r->p == '\0' || *r->p == ',' || *r->p == '}' || *r->p == ']' || *r->p == ':') r->bad = true;
	else while (*r->p && !strchr(" \t\r\n,]}", *r->p)) r->p++;
}

/* Strings of an array; other elements are skipped. */
static void read_strings(Rd *r, Found *f, char **list, int *count)
{
	ws(r);
	if (*r->p != '[') { skip_value(r, 1); return; }
	r->p++;
	ws(r);
	if (*r->p == ']') { r->p++; return; }
	for (;;) {
		ws(r);
		if (*r->p == ']') { r->p++; return; }
		if (*r->p == '"') {
			char *s = read_string(r);
			if (r->bad) { free(s); return; }
			if (!list) {
				if (f->test && strcmp(s, "*") != 0 && hc_name_glob(s, f->test)) f->matched = true;
				free(s);
			} else if (*count < LIST_MAX) {
				list[(*count)++] = s;
			} else {
				free(s);
				r->bad = true;
				return;
			}
		} else {
			skip_value(r, 2);
			if (r->bad) return;
		}
		ws(r);
		if (*r->p == ',') { r->p++; continue; }
		if (*r->p == ']') { r->p++; return; }
		r->bad = true;
		return;
	}
}

static void walk_object(Rd *r, Found *f, int depth)
{
	ws(r);
	if (*r->p != '{') { r->bad = true; return; }
	r->p++;
	ws(r);
	if (*r->p == '}') { r->p++; return; }
	for (;;) {
		ws(r);
		if (*r->p == '}') { r->p++; return; }
		char *key = read_string(r);
		if (r->bad) { free(key); return; }
		ws(r);
		if (*r->p != ':') { r->bad = true; free(key); return; }
		r->p++;
		ws(r);
		if (depth == 0 && strcmp(key, "role") == 0 && *r->p == '{') {
			walk_object(r, f, 1); /* the legacy ls2 files wrap the role: {"role": {...}, "permissions": [...]} */
		} else if (strcmp(key, "allowedNames") == 0 && *r->p == '[') {
			f->names_seen = true;
			read_strings(r, f, NULL, NULL);
		} else if (strcmp(key, "exeName") == 0 && *r->p == '"') {
			free(f->exe);
			f->exe = read_string(r);
		} else if (strcmp(key, "appId") == 0 && *r->p == '"') {
			free(f->appid);
			f->appid = read_string(r);
		} else if (depth == 0 && strcmp(key, "roleFiles") == 0) {
			read_strings(r, f, f->lists[0], &f->nlists[0]);
		} else if (depth == 0 && strcmp(key, "roleFilesPub") == 0) {
			read_strings(r, f, f->lists[1], &f->nlists[1]);
		} else if (depth == 0 && strcmp(key, "roleFilesPrv") == 0) {
			read_strings(r, f, f->lists[2], &f->nlists[2]);
		} else {
			skip_value(r, depth + 1);
		}
		free(key);
		if (r->bad) return;
		ws(r);
		if (*r->p == ',') { r->p++; continue; }
		if (*r->p == '}') { r->p++; return; }
		r->bad = true;
		return;
	}
}

static bool parse(const char *text, const char *test, Found *f)
{
	Rd r = {text, false};
	memset(f, 0, sizeof *f);
	f->test = test;
	ws(&r);
	if (!*r.p && !r.bad) return true; /* an empty file (some firmware ships one) allows nothing */
	walk_object(&r, f, 0);
	if (!r.bad) {
		ws(&r);
		if (*r.p) r.bad = true;
	}
	if (r.bad) found_free(f);
	return !r.bad;
}

HcRoleVerdict hc_role_judge(const char *text, const char *name)
{
	Found f;
	if (!text || !name || !name[0] || !parse(text, name, &f)) return HC_ROLE_INVALID;
	/* A file with no allowedNames cannot allow a name (the legacy ls2 directories also hold permission maps). */
	HcRoleVerdict v = HC_ROLE_UNRELATED;
	if (f.matched) {
		char own[512];
		int n = snprintf(own, sizeof own, "%s%s/", HC_DEV_APPS_DIR, name);
		bool inside = n > 0 && (size_t)n < sizeof own && f.exe && strncmp(f.exe, own, (size_t)n) == 0 &&
			f.exe[n] != '\0' && !strstr(f.exe, "/../") && !strstr(f.exe, "//");
		v = inside && !f.appid ? HC_ROLE_OWN : HC_ROLE_FOREIGN;
	}
	found_free(&f);
	return v;
}

/* ---- the files ---- */

typedef struct {
	const char *root, *name;
	bool own, foreign, failed;
	char *why;
	size_t whylen;
} Scan;

static void fail(Scan *s, const char *what, const char *path, const char *detail)
{
	if (!s->failed && !s->foreign) snprintf(s->why, s->whylen, "%s %s%s%s", what, path, detail[0] ? ": " : "", detail);
	s->failed = true;
}

static char *slurp_text(const char *path, int *err)
{
	FILE *fp = fopen(path, "rb");
	if (!fp) { *err = errno; return NULL; }
	char *buf = malloc(ROLE_MAX + 1);
	size_t n = buf ? fread(buf, 1, ROLE_MAX + 1, fp) : 0;
	int bad = ferror(fp);
	fclose(fp);
	if (!buf || bad || n > ROLE_MAX) { free(buf); *err = bad ? EIO : EFBIG; return NULL; }
	buf[n] = '\0';
	if (memchr(buf, '\0', n)) { free(buf); *err = EILSEQ; return NULL; }
	*err = 0;
	return buf;
}

static void judge_file(Scan *s, const char *path)
{
	int err;
	char *text = slurp_text(path, &err);
	if (!text) {
		if (err != ENOENT) fail(s, "cannot read", path, strerror(err));
		return;
	}
	HcRoleVerdict v = hc_role_judge(text, s->name);
	free(text);
	if (v == HC_ROLE_INVALID) fail(s, "not a role file:", path, "");
	else if (v == HC_ROLE_OWN) s->own = true;
	else if (v == HC_ROLE_FOREIGN) {
		if (!s->foreign) snprintf(s->why, s->whylen, "the role %s also allows the name", path);
		s->foreign = true;
	}
}

static bool has_json_suffix(const char *n)
{
	size_t len = strlen(n);
	return len > 5 && strcmp(n + len - 5, ".json") == 0;
}

/* Calls fn(path) for every .json file of a directory; a missing directory is empty. */
static void each_json(Scan *s, const char *dir, void (*fn)(Scan *, const char *))
{
	char path[1024];
	snprintf(path, sizeof path, "%s%s", s->root, dir);
	DIR *d = opendir(path);
	if (!d) {
		if (errno != ENOENT && errno != ENOTDIR) fail(s, "cannot read directory", path, strerror(errno));
		return;
	}
	struct dirent *e;
	while ((e = readdir(d)) != NULL) {
		if (!has_json_suffix(e->d_name)) continue;
		char file[1024];
		if (snprintf(file, sizeof file, "%s/%s", path, e->d_name) >= (int)sizeof file) {
			fail(s, "path too long:", e->d_name, "");
			continue;
		}
		fn(s, file);
	}
	closedir(d);
}

static void manifest_file(Scan *s, const char *path)
{
	int err;
	char *text = slurp_text(path, &err);
	if (!text) {
		if (err != ENOENT) fail(s, "cannot read", path, strerror(err));
		return;
	}
	Found f;
	if (!parse(text, NULL, &f)) {
		free(text);
		fail(s, "not a manifest:", path, "");
		return;
	}
	free(text);
	for (int k = 0; k < 3; k++)
		for (int i = 0; i < f.nlists[k]; i++) {
			if (f.lists[k][i][0] != '/') continue;
			char full[1024];
			if (snprintf(full, sizeof full, "%s%s", s->root, f.lists[k][i]) < (int)sizeof full) judge_file(s, full);
		}
	found_free(&f);
}

/* Parent directories of the hub's configured manifest lists, from its own configuration. */
static void conf_dirs(Scan *s, void (*fn)(Scan *, const char *))
{
	char path[1024];
	snprintf(path, sizeof path, "%s/etc/luna-service2/ls-hubd.conf", s->root);
	int err;
	char *text = slurp_text(path, &err);
	if (!text) return;
	for (char *line = text; line && *line;) {
		char *next = strchr(line, '\n');
		if (next) *next++ = '\0';
		static const char *const keys[] = {"ManifestsDirectories", "ManifestsVolatileDirectories"};
		for (size_t k = 0; k < 2; k++) {
			size_t kl = strlen(keys[k]);
			if (strncmp(line, keys[k], kl) != 0) continue;
			const char *v = line + kl;
			while (*v == ' ' || *v == '\t') v++;
			if (*v++ != '=') continue;
			char *list = strdup(v), *save = NULL;
			for (char *d = list ? strtok_r(list, ";\r ", &save) : NULL; d; d = strtok_r(NULL, ";\r ", &save))
				if (d[0] == '/' && !strstr(d, "..")) fn(s, d);
			free(list);
		}
		line = next;
	}
	free(text);
}

static void manifest_dir(Scan *s, const char *dir) { each_json(s, dir, manifest_file); }

static const char *const MANIFEST_DIRS[] = {
	"/usr/share/luna-service2/manifests.d", "/mnt/otycabi/usr/share/luna-service2/manifests.d",
	"/mnt/otncabi/usr/share/luna-service2/manifests.d", "/var/luna-service2/manifests.d",
	"/var/luna-service2-dev/manifests.d", "/ap/mount/usr/share/luna-service2/manifests.d", NULL};
static const char *const ROLE_DIRS[] = {
	"/var/palm/ls2-dev/roles/pub", "/var/palm/ls2-dev/roles/prv", "/var/palm/ls2/roles/pub", "/var/palm/ls2/roles/prv",
	"/usr/share/ls2/roles/pub", "/usr/share/ls2/roles/prv", "/mnt/otycabi/usr/share/ls2/roles/pub",
	"/mnt/otycabi/usr/share/ls2/roles/prv", "/mnt/otncabi/usr/share/ls2/roles/pub", "/mnt/otncabi/usr/share/ls2/roles/prv",
	"/var/luna-service2/roles.d", "/var/luna-service2-dev/roles.d", "/usr/share/luna-service2/roles.d",
	"/mnt/otycabi/usr/share/luna-service2/roles.d", "/mnt/otncabi/usr/share/luna-service2/roles.d",
	"/ap/mount/usr/share/luna-service2/roles.d", NULL};

bool hc_name_owned(const char *root, const char *name, char *why, size_t whylen)
{
	Scan s = {root ? root : "", name, false, false, false, why, whylen};
	if (whylen) why[0] = '\0';
	if (!name || !name[0] || strchr(name, '/')) {
		snprintf(why, whylen, "no usable service name");
		return false;
	}
	conf_dirs(&s, manifest_dir);
	for (size_t i = 0; MANIFEST_DIRS[i]; i++) manifest_dir(&s, MANIFEST_DIRS[i]);
	for (size_t i = 0; ROLE_DIRS[i]; i++) each_json(&s, ROLE_DIRS[i], judge_file);
	if (s.foreign || s.failed) return false;
	if (!s.own) {
		snprintf(why, whylen, "no role of the app's own executable allows the name");
		return false;
	}
	return true;
}
