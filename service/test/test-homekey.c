/* SPDX-License-Identifier: Apache-2.0 */
#include "homekey.h"
#include <pbnjson.h>
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define KEY "com.webos.surfacemanager.keyFilters"
static raw_buffer kb(const char *s) { return j_str_to_buffer(s, strlen(s)); }
static jvalue_ref parse(const char *s) { return jdom_create(kb(s), jschema_all(), NULL); }
static jvalue_ref current;
/* A persistent layer another app wrote: while mask is set, getConfigs returns it and volatile writes are accepted
 * and dropped. A persistent null clears it (clear_stuck: it does not; clear_needs_reconfigure: only after a
 * reconfigure; clear_reject: the call fails). */
static jvalue_ref mask;
static int persistent_writes, reconfigures, clear_stuck, clear_needs_reconfigure, clear_pending, clear_reject, cannot_toasts, override_toasts, toast_fail, reconf_slow, gap_reads;
static GString *logbuf;
static void klogcap(const char *line, void *context) { (void)context; g_string_append(logbuf, line); g_string_append_c(logbuf, '\n'); }
static int sets, gets, fails, corrupt_reads, corrupt_after_set, toasts, starts, app_installed = 1, ready = 1;
static char *fake(const char *uri, const char *payload, int timeout, void *context) {
    (void)context; assert(timeout > 0 && timeout <= (strstr(uri, "reconfigure") ? 30 : 5));
    if (strstr(uri, "getCurrentState")) return g_strdup(ready ? "{\"returnValue\":true,\"currentState\":\"launcher\"}" : "{}");
    if (strstr(uri, "createToast")) { toasts++; if (strstr(payload, "will start shortly")) starts++; if (strstr(payload, "cannot change Home")) cannot_toasts++; if (strstr(payload, "taken over the Home button")) override_toasts++; return g_strdup(toast_fail > 0 && strstr(payload, "taken over the Home button") && toast_fail-- ? "{\"returnValue\":false}" : "{\"returnValue\":true}"); }
    if (strstr(uri, "getAppInfo")) return g_strdup(app_installed ? "{\"returnValue\":true}" : "{\"returnValue\":false}");
    if (strstr(uri, "reconfigure")) {
        reconfigures++;
        /* a slow configd: reads during the reconfigure give no valid list, the call itself runs into its timeout */
        if (reconf_slow) { gap_reads = 3; return g_strdup("{\"returnValue\":false}"); }
        if (clear_pending) { j_release(&mask); mask = NULL; clear_pending = 0; }
        return g_strdup("{\"returnValue\":true}");
    }
    if (strstr(uri, "setConfigs")) {
        {
            jvalue_ref pr = parse(payload); bool pv = false;
            jboolean_get(jobject_get(pr, kb("volatile")), &pv);
            if (!pv) {
                /* the only persistent write the service may make is the owner-approved null */
                assert(jis_null(jobject_get(jobject_get(pr, kb("configs")), kb(KEY))));
                persistent_writes++; j_release(&pr);
                if (clear_reject) return g_strdup("{\"returnValue\":false}");
                if (mask && !clear_stuck) {
                    if (clear_needs_reconfigure) clear_pending = 1; else { j_release(&mask); mask = NULL; }
                }
                return g_strdup("{\"returnValue\":true}");
            }
            j_release(&pr);
        }
        sets++;
        if (corrupt_after_set) { corrupt_reads=1; corrupt_after_set=0; }
        jvalue_ref req = parse(payload); bool vol = false;
        jboolean_get(jobject_get(req, kb("volatile")), &vol); assert(vol);
        jvalue_ref list = jobject_get(jobject_get(req, kb("configs")), kb(KEY));
        assert(jis_array(list) && jarray_size(list) > 0);
        if (fails) { if (fails > 0) fails--; j_release(&req); return g_strdup("{\"returnValue\":false}"); }
        if (mask) { j_release(&req); return g_strdup("{\"returnValue\":true}"); }
        j_release(&current); current = jvalue_duplicate(list); j_release(&req);
        return g_strdup("{\"returnValue\":true}");
    }
    assert(strstr(uri, "getConfigs")); gets++;
    if (gap_reads > 0 && --gap_reads == 0 && clear_pending) { j_release(&mask); mask = NULL; clear_pending = 0; }
    if (gap_reads > 0) return g_strdup("{\"returnValue\":false}");
    jvalue_ref reply = jobject_create(), configs = jobject_create();
    jobject_set(reply, kb("returnValue"), jboolean_create(true));
    if (sets && corrupt_reads) { corrupt_reads--; jobject_set(configs, kb(KEY), jarray_create(NULL)); }
    else jobject_set(configs, kb(KEY), jvalue_duplicate(mask ? mask : current));
    jobject_set(reply, kb("configs"), configs);
    char *text = g_strdup(jvalue_stringify(reply)); j_release(&reply); return text;
}
static char lastout[1024];
static int run_ex(HCKeyOps *o, const char *action, gboolean stock, gboolean clear) {
    GString *out = g_string_new(NULL); int rc = hc_key_run_ex(o, action, stock, clear, out);
    assert(!strstr(out->str, "mounted=") && out->len < sizeof lastout);
    snprintf(lastout, sizeof lastout, "%s", out->str);
    printf("%s rc=%d %s", action, rc, out->str); g_string_free(out, TRUE); return rc;
}
static int run(HCKeyOps *o, const char *action, gboolean stock) { return run_ex(o, action, stock, FALSE); }
static void status(HCKeyOps *o, const char *expected) {
    GString *out = g_string_new(NULL);
    assert(hc_key_run(o, "status", FALSE, out) == 0);
    assert(strstr(out->str, expected) && !strstr(out->str, "mounted="));
    printf("status %s", out->str); g_string_free(out, TRUE);
}
int main(int argc, char **argv) {
    setbuf(stdout,NULL);
    assert(argc == 3);
    logbuf = g_string_new(NULL);
    HCKeyOps o = {argv[1], argv[2], NULL, fake, NULL, 3, 0, klogcap};
    char *os = g_build_filename(o.state_dir, "starfish-release", NULL); o.release_file = os;
    char *flag = g_build_filename(o.state_dir, "homekey", NULL), *home = g_build_filename(o.state_dir, "home", NULL);
    g_file_set_contents(os, "Rockhopper release 7.6.0-19 (mullet-mullet)\n", -1, NULL);
    g_file_set_contents(flag, "", -1, NULL); g_file_set_contents(home, "test.home\n", -1, NULL);
    current = parse("[{\"file\":\"/other.js\",\"handler\":\"first\",\"extra\":[1,true]}, {\"file\":\"/usr/lib/qt5/qml/KeyFilters/systemUi.js\",\"handler\":\"second\",\"extra\":{\"a\":2}}, {\"file\":\"/usr/lib/qt5/qml/KeyFilters/systemUi.js\",\"handler\":\"third\"}]");
    jvalue_ref original = jvalue_duplicate(current);
    ready = 0; assert(run(&o, "boot", FALSE) == 1); assert(sets == 0 && gets == 3 && starts == 0); ready = 1;
    status(&o, "active=no\n");
    assert(run(&o, "boot", FALSE) == 0); assert(sets == 1); assert(toasts >= 2 && starts == 1);
    status(&o, "active=yes\n");
    assert(jarray_size(current) == jarray_size(original));
    jvalue_ref normalized = jvalue_duplicate(current);
    for (int i = 1; i < 3; i++) {
        jvalue_ref item = jarray_get(current, i);
        raw_buffer b = jstring_get_fast(jobject_get(item, kb("file")));
        char *p = g_strndup(b.m_str,b.m_len); assert(g_file_test(p,G_FILE_TEST_EXISTS)); g_free(p);
        jobject_set(jarray_get(normalized,i),kb("file"),jstring_create("/usr/lib/qt5/qml/KeyFilters/systemUi.js"));
    }
    assert(jvalue_equal(normalized, original)); j_release(&normalized);
    assert(run(&o,"refresh",FALSE)==0); assert(sets==1); /* identical bytes */
    g_file_set_contents(home,"other.home",-1,NULL); assert(run(&o,"refresh",FALSE)==0); assert(sets==2);
    jvalue_ref saved_active=jvalue_duplicate(current);
    jobject_set(jarray_get(current,1),kb("external"),jboolean_create(true));
    int writes_before=sets; assert(run(&o,"refresh",FALSE)==1); assert(sets==writes_before);
    j_release(&current);current=saved_active;
    app_installed=0; assert(run(&o,"refresh",FALSE)==0); assert(jvalue_equal(current,original)); app_installed=1;
    assert(run(&o,"apply",FALSE)==0);
    fails=1; g_file_set_contents(home,"test.home",-1,NULL); assert(run(&o,"refresh",FALSE)==1); assert(jvalue_equal(current,original));
    assert(run(&o,"apply",FALSE)==0);
    corrupt_after_set=1; g_file_set_contents(home,"other.home",-1,NULL); assert(run(&o,"refresh",FALSE)==1); assert(jvalue_equal(current,original));
    assert(run(&o,"apply",FALSE)==0);
    /* A persistent override another app saved: the volatile write is accepted and masked. It is told apart from
     * other failures, logged, recovered as before, and cleared only on request (with a persistent null). */
    {
        g_file_set_contents(home,"test.home",-1,NULL);
        assert(run(&o,"stop",FALSE)==0); assert(jvalue_equal(current,original));
        jvalue_ref luma=parse("{\"file\":\"/var/lib/luma-home/home-filter.js\",\"handler\":\"lumaHomeKey\"}");
        mask=jvalue_duplicate(original); jarray_append(mask,jvalue_duplicate(luma));
        int pw=persistent_writes, w0=sets; g_string_truncate(logbuf,0);
        assert(run(&o,"apply",FALSE)==1); assert(strstr(lastout,"reason=persistent-override\n"));
        assert(sets==w0+2 && persistent_writes==pw && cannot_toasts==0 && mask);
        assert(strstr(logbuf->str,"setConfigs(volatile) reply={\"returnValue\":true}") && strstr(logbuf->str,"first difference at index"));
        assert(strstr(logbuf->str,"persistent keyFilters override masks"));
        status(&o,"active=no\npatch=possible\nreboot=no\noverride=yes\n");
        /* boot: recovered as today, one line toast, nothing cleared */
        int ot=override_toasts; assert(run(&o,"boot",FALSE)==1); assert(strstr(lastout,"reason=persistent-override\n") && override_toasts==ot+1 && persistent_writes==pw && mask);
        {   /* the notification service is not up yet: tried again, the marker records the delivery */
            char *mk=g_build_filename(o.state_dir,"override",NULL),*mt=NULL;
            assert(g_file_get_contents(mk,&mt,NULL,NULL) && !strcmp(mt,"toasted")); g_free(mt);
            ot=override_toasts; toast_fail=2; assert(run(&o,"boot",FALSE)==1); assert(override_toasts==ot+3 && toast_fail==0);
            assert(g_file_get_contents(mk,&mt,NULL,NULL) && !strcmp(mt,"toasted")); g_free(mt);
            unlink(mk); ot=override_toasts; toast_fail=99; assert(run(&o,"boot",FALSE)==1); assert(override_toasts==ot+5);
            assert(g_file_get_contents(mk,&mt,NULL,NULL) && !strcmp(mt,"")); g_free(mt); /* seen, not delivered */
            toast_fail=0; g_free(mk);
        }
        /* the snapshot taken under the override has the other app's entry: that is what the clear replaces */
        char *snap=g_build_filename(o.state_dir,"keyfilters-stock.json",NULL),*sb=NULL;
        assert(g_file_get_contents(snap,&sb,NULL,NULL) && strstr(sb,"lumaHomeKey")); g_free(sb);
        /* a restore or refresh never writes persistently */
        assert(run(&o,"stop",FALSE)==0); assert(run(&o,"refresh",TRUE)==0); assert(persistent_writes==pw);
        /* a plain apply stays refused, unchanged */
        assert(run(&o,"apply",FALSE)==1); assert(persistent_writes==pw && mask);
        /* cleared on request: null, read, a fresh snapshot, then the normal volatile apply */
        g_string_truncate(logbuf,0);
        assert(run_ex(&o,"apply",FALSE,TRUE)==0); assert(strstr(lastout,"active=yes\n") && !strstr(lastout,"reason="));
        status(&o,"active=yes\npatch=possible\nreboot=no\noverride=no\n");
        assert(persistent_writes==pw+1 && !mask && strstr(logbuf->str,"clear: setConfigs(persistent null)"));
        assert(g_file_get_contents(snap,&sb,NULL,NULL) && !strstr(sb,"lumaHomeKey")); g_free(sb);
        assert(run(&o,"stop",FALSE)==0); assert(jvalue_equal(current,original)); assert(persistent_writes==pw+1);
        /* the clear takes only after a reconfigure: read, reconfigure, read */
        mask=jvalue_duplicate(original); jarray_append(mask,jvalue_duplicate(luma)); clear_needs_reconfigure=1; int rc0=reconfigures;
        assert(run_ex(&o,"apply",FALSE,TRUE)==0); assert(strstr(lastout,"active=yes\n") && !mask && reconfigures==rc0+1);
        clear_needs_reconfigure=0; assert(run(&o,"stop",FALSE)==0);
        /* a slow TV: the reconfigure call does not answer true, reads give no valid list for a while; configd is
         * asked once and waited for, and the clear still ends well */
        mask=jvalue_duplicate(original); jarray_append(mask,jvalue_duplicate(luma)); clear_needs_reconfigure=1; reconf_slow=1; rc0=reconfigures;
        assert(run_ex(&o,"apply",FALSE,TRUE)==0); assert(strstr(lastout,"active=yes\n") && !mask && reconfigures==rc0+1);
        assert(strstr(logbuf->str,"no valid list yet"));
        clear_needs_reconfigure=0; reconf_slow=0; gap_reads=0; assert(run(&o,"stop",FALSE)==0);
        /* the null is accepted but does not take: the write is masked again, a distinct reason, stock restored */
        mask=jvalue_duplicate(original); jarray_append(mask,jvalue_duplicate(luma)); clear_stuck=1; rc0=reconfigures;
        assert(run_ex(&o,"apply",FALSE,TRUE)==1); assert(strstr(lastout,"reason=override-not-cleared\n") && mask && reconfigures==rc0+1);
        clear_stuck=0; j_release(&mask); mask=NULL;
        /* the null is refused: nothing else was written */
        mask=jvalue_duplicate(original); jarray_append(mask,jvalue_duplicate(luma)); clear_reject=1; w0=sets;
        assert(run_ex(&o,"apply",FALSE,TRUE)==1); assert(strstr(lastout,"reason=override-clear-failed\n") && sets==w0 && mask);
        clear_reject=0; j_release(&mask); mask=NULL; j_release(&luma); g_free(snap);
        /* a failed write that is not a masked one is not called an override */
        fails=1; assert(run(&o,"apply",FALSE)==1); assert(!strstr(lastout,"persistent-override") && strstr(lastout,"activation failed")); fails=0;
        assert(strstr(logbuf->str,"setConfigs(volatile) reply={\"returnValue\":false}"));
        assert(run(&o,"stop",FALSE)==0); assert(jvalue_equal(current,original));
        /* back to the state this block found: the filter active for other.home (the checks below rely on it) */
        g_file_set_contents(home,"other.home",-1,NULL); assert(run(&o,"apply",FALSE)==0);
    }
    /* No toast when boot restores stock: missing app, no flag, no choice; and a failed write announces first, then reports the error. */
    starts=0; app_installed=0; assert(run(&o,"boot",FALSE)==0); assert(starts==0); app_installed=1;
    assert(run(&o,"apply",FALSE)==0);
    g_file_set_contents(home,"test.home",-1,NULL); fails=1; assert(run(&o,"boot",FALSE)==1); assert(starts==1); starts=0; fails=0; /* announced before the write; the error toast follows */
    assert(run(&o,"apply",FALSE)==0);
    unlink(flag); assert(run(&o,"boot",FALSE)==0); assert(starts==0); g_file_set_contents(flag,"",-1,NULL);
    g_file_set_contents(home,"",-1,NULL); assert(run(&o,"boot",FALSE)==0); assert(starts==0);
    g_file_set_contents(home,"other.home",-1,NULL);
    assert(run(&o,"boot",FALSE)==0); assert(starts==1); starts=0;
    fails=-1; assert(run(&o,"stop",FALSE)==1); /* retain selected file during failed restore */
    jvalue_ref item=jarray_get(current,1); raw_buffer b=jstring_get_fast(jobject_get(item,kb("file")));
    char *p=g_strndup(b.m_str,b.m_len); assert(g_file_test(p,G_FILE_TEST_EXISTS)); g_free(p);
    fails=0; assert(run(&o,"stop",FALSE)==0); assert(jvalue_equal(current,original));
    status(&o, "active=no\n");
    unlink(flag); assert(run(&o,"apply",FALSE)==0); assert(jvalue_equal(current,original));
    g_file_set_contents(flag,"",-1,NULL); assert(run(&o,"apply",FALSE)==0);
    char *unknown=g_build_filename(o.state_dir,"unknown.js",NULL), *bytes=NULL;
    assert(g_file_get_contents(argv[2],&bytes,NULL,NULL));
    char *anchor=strstr(bytes,"function launchHomeApp(key)"); assert(anchor); anchor[9]='X';
    g_file_set_contents(unknown,bytes,-1,NULL);g_free(bytes);
    o.stock_js=unknown;
    assert(run(&o,"prepare",FALSE)==2);
    starts=0; assert(run(&o,"boot",FALSE)==0);assert(jvalue_equal(current,original)); assert(starts==0);
    o.stock_js=argv[2];unlink(unknown);g_free(unknown);
    /* A restore with no state dir (after the uninstall) must not bring the dir and lock back. */
    char *gone=g_build_filename(argv[1],"absent-state",NULL); HCKeyOps late=o; late.state_dir=gone; late.release_file=os;
    const char *restores[]={"stop","clear","refresh"}; int writes=sets;
    for(int i=0;i<3;i++){ assert(run(&late,restores[i],i==2)==0); assert(!g_file_test(gone,G_FILE_TEST_EXISTS)); }
    assert(run(&late,"refresh",FALSE)==0); assert(!g_file_test(gone,G_FILE_TEST_EXISTS)); assert(sets==writes);
    /* An apply with no choice restores stock and leaves nothing behind either. */
    assert(run(&late,"apply",FALSE)==0); assert(!g_file_test(gone,G_FILE_TEST_EXISTS));
    /* Once stock is restored and read back with no choice, the snapshot and lock go, and the dir when empty. */
    {
        HCKeyOps f=o; char *fs=g_build_filename(argv[1],"fresh-state",NULL); f.state_dir=fs;
        char *fhome=g_build_filename(fs,"home",NULL),*fflag=g_build_filename(fs,"homekey",NULL),
            *fsnap=g_build_filename(fs,"keyfilters-stock.json",NULL),*flock=g_build_filename(fs,"keyfilter.lock",NULL);
        assert(!g_mkdir_with_parents(fs,0755));
        g_file_set_contents(fhome,"other.home",-1,NULL); g_file_set_contents(fflag,"",-1,NULL);
        assert(run(&f,"apply",FALSE)==0); assert(!jvalue_equal(current,original));
        assert(g_file_test(fsnap,G_FILE_TEST_EXISTS) && g_file_test(flock,G_FILE_TEST_EXISTS));
        /* choice dropped, but the restore write fails: the snapshot stays (it is the only copy of stock) */
        unlink(fhome); fails=-1; assert(run(&f,"refresh",FALSE)==1);
        assert(g_file_test(fsnap,G_FILE_TEST_EXISTS) && g_file_test(flock,G_FILE_TEST_EXISTS));
        /* restored and verified: snapshot and lock removed, the flag keeps the dir */
        fails=0; assert(run(&f,"refresh",FALSE)==0); assert(jvalue_equal(current,original));
        assert(!g_file_test(fsnap,G_FILE_TEST_EXISTS) && !g_file_test(flock,G_FILE_TEST_EXISTS));
        assert(g_file_test(fflag,G_FILE_TEST_EXISTS) && g_file_test(fs,G_FILE_TEST_IS_DIR));
        /* with nothing else in it the dir goes too; a later apply with a choice snapshots again */
        unlink(fflag); g_file_set_contents(fhome,"other.home",-1,NULL); g_file_set_contents(fflag,"",-1,NULL);
        assert(run(&f,"apply",FALSE)==0); assert(g_file_test(fsnap,G_FILE_TEST_EXISTS));
        unlink(fhome); unlink(fflag); assert(run(&f,"stop",FALSE)==0); assert(jvalue_equal(current,original));
        assert(!g_file_test(fs,G_FILE_TEST_EXISTS));
        g_free(fhome); g_free(fflag); g_free(fsnap); g_free(flock); g_free(fs);
    }
    char *rm=g_strdup_printf("rm -rf '%s'",gone); assert(!system(rm)); g_free(rm); g_free(gone);
    j_release(&current); current=jarray_create(NULL); int before=sets; assert(run(&o,"apply",FALSE)==1); assert(sets==before);
    j_release(&current); j_release(&original); g_free(flag);g_free(home);g_free(os);
    puts("PASS native transaction: boot readiness, failed toast, exact list edits, live change, missing app, write/readback recovery, disable, empty-list refusal");
    return 0;
}
