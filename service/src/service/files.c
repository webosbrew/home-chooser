/* SPDX-License-Identifier: Apache-2.0 */
#define _GNU_SOURCE
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "fs.h"
#include "service/files.h"
#include "json.h"
#include "log.h"
#include "policy.h"
#include "service/methods.h"
#include "service/reply.h"

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

bool on_read_file(LSHandle *sh, LSMessage *msg, void *ctx)
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

bool on_write_file(LSHandle *sh, LSMessage *msg, void *ctx)
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

bool on_remove_file(LSHandle *sh, LSMessage *msg, void *ctx)
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
