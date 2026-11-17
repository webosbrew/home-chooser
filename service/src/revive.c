/* SPDX-License-Identifier: Apache-2.0 */
#include "revive.h"

enum { IDLE, ASKING, STARTING };

void hc_revive_init(hc_revive *r, const hc_revive_env *env)
{
	r->env = env;
	r->next_at = 0;
	r->started = false;
	r->phase = IDLE;
	r->epoch = r->run_epoch = 0;
}

static void on_done(void *self, int code)
{
	hc_revive *r = self;
	const hc_revive_env *e = r->env;
	if (r->epoch != r->run_epoch) {
		r->phase = IDLE;
		return;
	}
	if (r->phase == ASKING) {
		/* active exits 0 when the watcher runs; start it only while the choice is still stored (a restore removes it) */
		if (code == 0 || !e->choice_stored(e->ctx)) {
			r->phase = IDLE;
			return;
		}
		r->phase = STARTING;
		e->run(e->ctx, "watch", on_done, r);
		return;
	}
	r->phase = IDLE;
	if (code == 0) e->revived(e->ctx);
	else e->failed(e->ctx, code);
}

void hc_revive_touch(hc_revive *r)
{
	const hc_revive_env *e = r->env;
	if (r->phase != IDLE || !e->is_root(e->ctx)) return;
	long now = e->now(e->ctx);
	if (r->started && now < r->next_at) return;
	r->started = true;
	r->next_at = now + HC_REVIVE_INTERVAL_S;
	if (!e->choice_stored(e->ctx)) return;
	r->phase = ASKING;
	r->run_epoch = r->epoch;
	e->run(e->ctx, "active", on_done, r);
}

void hc_revive_hold(hc_revive *r, int seconds)
{
	r->epoch++;
	r->started = true;
	r->next_at = r->env->now(r->env->ctx) + seconds;
}
