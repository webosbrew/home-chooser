/* SPDX-License-Identifier: Apache-2.0 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include "proc.h"

#define OUTPUT_MAX_BYTES (64 * 1024)

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
int run_argv(const char *const *argv, int timeout_s, GString *out, GString *err, int (*done)(const char *))
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
