/* SPDX-License-Identifier: Apache-2.0 */
#include <fcntl.h>
#include <glib.h>
#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>
#include "log.h"
#include "policy.h"
#include "revive.h"
#include "service/methods.h"

#define REVIVE_RUN_TIMEOUT_S 25

typedef struct {
	void (*done)(void *self, int code);
	void *self;
	GPid pid;
	guint timeout;
} Run;

static hc_revive g_revive;

static void child_setup(gpointer unused)
{
	(void)unused;
	int nul = open("/dev/null", O_RDONLY);
	if (nul >= 0) dup2(nul, 0);
	for (int fd = 3; fd < 1024; fd++) close(fd);
}

static void child_done(GPid pid, gint status, gpointer data)
{
	Run *run = data;
	if (run->timeout) g_source_remove(run->timeout);
	g_spawn_close_pid(pid);
	int code = WIFEXITED(status) ? WEXITSTATUS(status) : -3;
	void (*done)(void *, int) = run->done;
	void *self = run->self;
	g_free(run);
	done(self, code);
}

static gboolean run_timeout(gpointer data)
{
	Run *run = data;
	run->timeout = 0;
	kill(run->pid, SIGKILL);
	return G_SOURCE_REMOVE;
}

static long env_now(void *ctx)
{
	(void)ctx;
	return (long)(g_get_monotonic_time() / G_USEC_PER_SEC);
}

static bool env_root(void *ctx)
{
	(void)ctx;
	return geteuid() == 0;
}

static bool env_stored(void *ctx)
{
	(void)ctx;
	return g_file_test(HC_HOME_FILE, G_FILE_TEST_IS_REGULAR);
}

static void env_run(void *ctx, const char *command, void (*done)(void *, int), void *self)
{
	(void)ctx;
	char *argv[] = {"/bin/sh", (char *)HC_ENTRY_SCRIPT, (char *)command, NULL};
	char *envp[] = {"PATH=/usr/sbin:/usr/bin:/sbin:/bin", "LANG=C", "HOME=/root", NULL};
	GPid pid;
	if (!g_spawn_async(NULL, argv, envp, G_SPAWN_DO_NOT_REAP_CHILD | G_SPAWN_STDOUT_TO_DEV_NULL | G_SPAWN_STDERR_TO_DEV_NULL,
			child_setup, NULL, &pid, NULL)) {
		done(self, -1);
		return;
	}
	Run *run = g_new0(Run, 1);
	run->done = done;
	run->self = self;
	run->pid = pid;
	run->timeout = g_timeout_add_seconds(REVIVE_RUN_TIMEOUT_S, run_timeout, run);
	g_child_watch_add(pid, child_done, run);
}

static void env_revived(void *ctx)
{
	(void)ctx;
	LOGI("WATCHER_REVIVED", "the watcher was not running with a home choice stored; started it");
}

static void env_failed(void *ctx, int code)
{
	(void)ctx;
	LOGE("WATCHER_REVIVE_FAILED", "starting the watcher exited with %d", code);
}

static const hc_revive_env g_env = {env_now, env_root, env_stored, env_run, env_revived, env_failed, NULL};

void revive_init(void)
{
	hc_revive_init(&g_revive, &g_env);
}

void revive_touch(void)
{
	hc_revive_touch(&g_revive);
}

/* A stopWatcher run or an unlink is the app taking the watcher down on purpose. */
void revive_hold(void)
{
	hc_revive_hold(&g_revive, 60);
}
