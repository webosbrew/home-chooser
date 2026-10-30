/* SPDX-License-Identifier: Apache-2.0 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <glib.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "fs.h"

bool mkdir_p(const char *dir)
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
ssize_t slurp(const char *path, size_t max, char **out, bool *too_big)
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

bool write_atomic(const char *path, const char *text, size_t len)
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
