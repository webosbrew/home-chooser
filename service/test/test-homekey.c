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
static int sets, gets, fails, corrupt_reads, corrupt_after_set, toasts, starts, app_installed = 1, ready = 1;
static char *fake(const char *uri, const char *payload, int timeout, void *context) {
    (void)context; assert(timeout > 0 && timeout <= 5);
    if (strstr(uri, "getCurrentState")) return g_strdup(ready ? "{\"returnValue\":true,\"currentState\":\"launcher\"}" : "{}");
    if (strstr(uri, "createToast")) { toasts++; if (strstr(payload, "will start shortly")) starts++; return g_strdup("{\"returnValue\":false}"); }
    if (strstr(uri, "getAppInfo")) return g_strdup(app_installed ? "{\"returnValue\":true}" : "{\"returnValue\":false}");
    if (strstr(uri, "setConfigs")) {
        sets++;
        if (corrupt_after_set) { corrupt_reads=1; corrupt_after_set=0; }
        jvalue_ref req = parse(payload); bool vol = false;
        jboolean_get(jobject_get(req, kb("volatile")), &vol); assert(vol);
        jvalue_ref list = jobject_get(jobject_get(req, kb("configs")), kb(KEY));
        assert(jis_array(list) && jarray_size(list) > 0);
        if (fails) { if (fails > 0) fails--; j_release(&req); return g_strdup("{\"returnValue\":false}"); }
        j_release(&current); current = jvalue_duplicate(list); j_release(&req);
        return g_strdup("{\"returnValue\":true}");
    }
    assert(strstr(uri, "getConfigs")); gets++;
    jvalue_ref reply = jobject_create(), configs = jobject_create();
    jobject_set(reply, kb("returnValue"), jboolean_create(true));
    if (sets && corrupt_reads) { corrupt_reads--; jobject_set(configs, kb(KEY), jarray_create(NULL)); }
    else jobject_set(configs, kb(KEY), jvalue_duplicate(current));
    jobject_set(reply, kb("configs"), configs);
    char *text = g_strdup(jvalue_stringify(reply)); j_release(&reply); return text;
}
static int run(HCKeyOps *o, const char *action, gboolean stock) {
    GString *out = g_string_new(NULL); int rc = hc_key_run(o, action, stock, out);
    assert(!strstr(out->str, "mounted="));
    printf("%s rc=%d %s", action, rc, out->str); g_string_free(out, TRUE); return rc;
}
static void status(HCKeyOps *o, const char *expected) {
    GString *out = g_string_new(NULL);
    assert(hc_key_run(o, "status", FALSE, out) == 0);
    assert(strstr(out->str, expected) && !strstr(out->str, "mounted="));
    printf("status %s", out->str); g_string_free(out, TRUE);
}
int main(int argc, char **argv) {
    setbuf(stdout,NULL);
    assert(argc == 3);
    HCKeyOps o = {argv[1], argv[2], NULL, fake, NULL, 3, 0};
    char *os = g_build_filename(o.state_dir, "os-release", NULL); o.osrelease = os;
    char *flag = g_build_filename(o.state_dir, "homekey", NULL), *home = g_build_filename(o.state_dir, "home", NULL);
    g_file_set_contents(os, "VERSION_ID=\"7.6.0\"\n", -1, NULL);
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
    char *gone=g_build_filename(argv[1],"absent-state",NULL); HCKeyOps late=o; late.state_dir=gone; late.osrelease=os;
    const char *restores[]={"stop","clear","refresh"}; int writes=sets;
    for(int i=0;i<3;i++){ assert(run(&late,restores[i],i==2)==0); assert(!g_file_test(gone,G_FILE_TEST_EXISTS)); }
    assert(run(&late,"refresh",FALSE)==0); assert(!g_file_test(gone,G_FILE_TEST_EXISTS)); assert(sets==writes);
    assert(run(&late,"apply",FALSE)==0); assert(g_file_test(gone,G_FILE_TEST_IS_DIR)); /* first install still creates it */
    char *rm=g_strdup_printf("rm -rf '%s'",gone); assert(!system(rm)); g_free(rm); g_free(gone);
    j_release(&current); current=jarray_create(NULL); int before=sets; assert(run(&o,"apply",FALSE)==1); assert(sets==before);
    j_release(&current); j_release(&original); g_free(flag);g_free(home);g_free(os);
    puts("PASS native transaction: boot readiness, failed toast, exact list edits, live change, missing app, write/readback recovery, disable, empty-list refusal");
    return 0;
}
