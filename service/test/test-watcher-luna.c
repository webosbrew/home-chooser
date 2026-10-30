/* SPDX-License-Identifier: Apache-2.0 */
/* Exercise the actual Luna client and runtime loop against an in-process hub
 * double. No firmware daemon, VM, device or live bus is used. */
#define _GNU_SOURCE
#include "luna.h"
#include "log.h"
#include "watcher/internal.h"
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>

PmLogContext hc_log;
static unsigned checks, failures;
#define CHECK(id, expr) do { checks++; if (!(expr)) { failures++; fprintf(stderr,"FAIL %s line %d: %s\n",id,__LINE__,#expr); } } while(0)
typedef struct { LSFilterFunc callback; void *context; LSMessageToken token; guint source; gboolean subscription; char *text; } Pending;
typedef struct { const char *text; gboolean hub_error; } Message;
static Pending pending[32];
static int handle_stub, calls, registrations, unregistrations, cancellations;
static gboolean register_fail, attach_fail, send_fail, silent, runtime_mode, removed;
static LSDisconnectHandler disconnect_handler;
static void *disconnect_context;
static GMainLoop *attached_loop;
static int runtime_case, expected_subs, subscriptions, ticks, preloads, initial_tasks;
static const char *stock_js;
static jvalue_ref filters;
static HCWatchConfig config;
static char *test_root, *installed_binary;
static jvalue_ref record;
static gboolean deliver(gpointer context) {
    Pending *p=context; p->source=0;
    LSFilterFunc cb=p->callback; void *data=p->context;
    Message m={p->text,FALSE};
    CHECK("W06 callback present",cb!=NULL);
    if(cb) cb((LSHandle *)&handle_stub,(LSMessage *)&m,data);
    return G_SOURCE_REMOVE;
}
static Pending *allocate(LSFilterFunc cb,void *context,LSMessageToken *token,gboolean subscription) {
    for(unsigned i=0;i<G_N_ELEMENTS(pending);i++) if(!pending[i].callback) {
        Pending *p=&pending[i]; p->callback=cb; p->context=context; p->token=i+1; p->subscription=subscription; *token=p->token; return p;
    }
    CHECK("tokens bounded",FALSE); return NULL;
}
bool LSRegisterApplicationService(const char *name,const char *id,LSHandle **handle,LSError *e) {
    (void)e; registrations++; CHECK("W06 anonymous client",name==NULL); CHECK("W06 privileged app identity",eq(id,HC_LUNA_IDENTITY));
    if(register_fail)return false;
    *handle=(LSHandle *)&handle_stub; return true;
}
bool LSGmainAttach(LSHandle *handle,GMainLoop *loop,LSError *e) {
    (void)e; CHECK("W06 client attached",handle==(LSHandle *)&handle_stub); attached_loop=loop; return !attach_fail;
}
bool LSSetDisconnectHandler(LSHandle *handle,LSDisconnectHandler callback,void *context,LSError *e) {
    (void)e; (void)handle; disconnect_handler=callback; disconnect_context=context; return true;
}
bool LSUnregister(LSHandle *handle,LSError *e) {
    (void)e; (void)handle; unregistrations++; attached_loop=NULL;
    for(unsigned i=0;i<G_N_ELEMENTS(pending);i++) {
        if(pending[i].source)g_source_remove(pending[i].source);
        g_free(pending[i].text); memset(&pending[i],0,sizeof pending[i]);
    }
    return true;
}
bool LSCallCancel(LSHandle *handle,LSMessageToken token,LSError *e) {
    (void)handle;(void)e; cancellations++;
    CHECK("W19 cancel live token",token>0 && token<=G_N_ELEMENTS(pending));
    Pending *p=&pending[token-1];
    if(p->source)g_source_remove(p->source);
    g_free(p->text); memset(p,0,sizeof *p); return true;
}
const char *LSMessageGetPayload(LSMessage *message) { return ((Message *)message)->text; }
bool LSMessageIsHubErrorMessage(LSMessage *message) { return ((Message *)message)->hub_error; }
static char *runtime_reply(const char *uri,const char *payload) {
    jvalue_ref p=parse_object(payload),r=jobject_create(); put_bool(r,"returnValue",true);
    const char *method=strrchr(uri,'/')+1;
    if(eq(method,"getAppInfo")) {
        char *id=get_string(p,"id"); put_bool(r,"returnValue",!removed || !eq(id,HC_APP_ID));
        if(!removed || !eq(id,HC_APP_ID)) { jvalue_ref info=jobject_create(); put_str(info,"title","Native Test Home"); jobject_set(r,key_buf("appInfo"),info); }
        g_free(id);
    } else if(eq(method,"getSystemSettings")) {
        jvalue_ref settings=jobject_create(); jobject_set(settings,key_buf("defaultApps"),jvalue_duplicate(record)); jobject_set(r,key_buf("settings"),settings);
    } else if(eq(method,"setSystemSettings")) {
        j_release(&record); record=jvalue_duplicate(member(member(p,"settings"),"defaultApps"));
    } else if(eq(method,"getCurrentState")) put_str(r,"currentState","normal");
    else if(eq(method,"getConfigs")) {
        jvalue_ref configs=jobject_create();jobject_set(configs,key_buf("com.webos.surfacemanager.keyFilters"),jvalue_duplicate(filters));jobject_set(r,key_buf("configs"),configs);
    } else if(eq(method,"setConfigs")) {
        bool value=false;jboolean_get(member(p,"volatile"),&value);CHECK("W12 volatile native transaction",value);
        j_release(&filters);filters=jvalue_duplicate(member(member(p,"configs"),"com.webos.surfacemanager.keyFilters"));
    } else if(eq(method,"getProfiles")) {
        j_release(&r);r=parse_object("{\"returnValue\":true,\"defaultApps\":{\"categories\":{\"home\":\"com.webos.app.home\"}}}");
    } else if(eq(method,"getForegroundAppInfo")) put_str(r,"appId","my.home");
    else if(eq(method,"setPreloadPolicy"))preloads++;
    char *text=g_strdup(jvalue_stringify(r)); j_release(&r);j_release(&p);return text;
}
bool LSCallOneReply(LSHandle *handle,const char *uri,const char *payload,LSFilterFunc cb,void *context,LSMessageToken *token,LSError *e) {
    (void)handle;(void)e; calls++;
    if(send_fail)return false;
    Pending *p=allocate(cb,context,token,FALSE); if(!p)return false;
    p->text=runtime_mode?runtime_reply(uri,payload):g_strdup("{\"returnValue\":true}");
    if(!silent)p->source=g_idle_add(deliver,p);
    return true;
}
static void emit(Pending *p,const char *text,gboolean hub_error) {
    if(!p->callback)return;
    Message m={text,hub_error};p->callback((LSHandle *)&handle_stub,(LSMessage *)&m,p->context);
}
static Pending *nth_subscription(int number) {
    for(unsigned i=0;i<G_N_ELEMENTS(pending);i++)if(pending[i].callback && pending[i].subscription && !number--)return &pending[i];
    return NULL;
}
static int task_count(void) {
    GDir *d=g_dir_open("/proc/self/task",0,NULL);int n=0;
    if(!d)return -1;
    while(g_dir_read_name(d))n++;
    g_dir_close(d);return n;
}
static gboolean runtime_drive(gpointer context) {
    (void)context;ticks++;
    CHECK("single thread watcher",task_count()==initial_tasks);
    CHECK("W06 correct subscriptions",subscriptions==expected_subs);
    if(runtime_case==0 || runtime_case==5 || runtime_case==6) { /* Ordinary package unlink while mapped, no source helper left. */
        removed=TRUE; watch_remove_tree(config.app_dir); watch_remove_tree(config.scripts_dir); unlink(installed_binary);
        if(runtime_case==0) {
            char *image=g_file_read_link("/proc/self/exe",NULL);
            CHECK("W02 mapped test image",image && g_str_has_suffix(image,"/runtime-image"));
            if(image && g_str_has_suffix(image,"/runtime-image")) CHECK("W02 unlink running image",unlink(image)==0);
            g_free(image);
        }
        Pending *p=nth_subscription(1); if(p && runtime_case!=6)emit(p,"{\"returnValue\":true,\"apps\":[]}",FALSE);
    } else if(runtime_case==1) {
        Pending *p=nth_subscription(0); if(p)emit(p,"{\"returnValue\":false,\"errorText\":\"service down\"}",TRUE);
    } else if(runtime_case==2) {
        for(int i=1;i<expected_subs;i++) { Pending *p=nth_subscription(i); if(p)emit(p,"{\"subscribed\":false}",FALSE); }
        kill(getpid(),SIGTERM);
    } else if(runtime_case==3) disconnect_handler((LSHandle *)&handle_stub,disconnect_context);
    else if(runtime_case==4) {
        Pending *p=nth_subscription(0);
        for(int i=0;i<140;i++)if(p)emit(p,"{\"settings\":{\"defaultApps\":{\"home\":\"my.home\"}}}",FALSE);
    }
    return G_SOURCE_REMOVE;
}
bool LSCall(LSHandle *handle,const char *uri,const char *payload,LSFilterFunc cb,void *context,LSMessageToken *token,LSError *e) {
    (void)handle;(void)e;
    if(send_fail)return false;
    Pending *p=allocate(cb,context,token,TRUE); if(!p)return false;
    if(runtime_mode) {
        const char *suffixes[]={"getSystemSettings","listApps","getForegroundAppInfo","getAppLifeEvents"};
        CHECK("W06/W16 subscription URI",g_str_has_suffix(uri,suffixes[subscriptions]));
        CHECK("W06/W16 subscribe flag",strstr(payload,"\"subscribe\":true")!=NULL);
        if(!subscriptions)CHECK("W06 settings payload",eq(payload,"{\"category\":\"general\",\"keys\":[\"defaultApps\"],\"subscribe\":true}"));
        subscriptions++;
        if(subscriptions==expected_subs)g_timeout_add(1,runtime_drive,NULL);
    }
    return true;
}
static int replies,ends;
static void received(const char *text,gboolean ended,void *context) {
    CHECK("W06 subscription context",context==&replies);CHECK("W06 payload",text!=NULL);replies++;ends+=ended;
}
static void test_client(void) {
    GMainLoop *loop=g_main_loop_new(NULL,FALSE);
    register_fail=TRUE; CHECK("registration failure",hc_luna_open(loop)==NULL); register_fail=FALSE;
    attach_fail=TRUE; CHECK("attach failure cleaned",hc_luna_open(loop)==NULL); attach_fail=FALSE;
    HCLuna *c=hc_luna_open(loop);CHECK("open client",c!=NULL);
    char *s=hc_luna_call(SETTINGS "getSystemSettings",RECORD_GET,1,c);CHECK("bounded reply",s && strstr(s,"true"));g_free(s);
    int before=calls;CHECK("allowlist rejects",hc_luna_call("luna://bad/method","{}",1,c)==NULL && calls==before);
    silent=TRUE;s=hc_luna_call(SETTINGS "getSystemSettings",RECORD_GET,0,c);CHECK("W20 deadline",s==NULL);silent=FALSE;
    send_fail=TRUE;CHECK("send failure",hc_luna_call(SETTINGS "getSystemSettings",RECORD_GET,1,c)==NULL);send_fail=FALSE;
    HCSubscription *sub=hc_luna_subscribe(c,SETTINGS "getSystemSettings","{}",received,&replies);CHECK("subscribe",sub!=NULL);
    Pending *p=nth_subscription(0);emit(p,"{\"subscribed\":true}",FALSE);emit(p,"{\"subscribed\":false}",FALSE);emit(p,"{}",TRUE);
    CHECK("W07 terminal only once",replies==2 && ends==1);hc_luna_cancel(sub);
    sub=hc_luna_subscribe(c,SAM "listApps","{}",received,&replies);p=nth_subscription(0);emit(p,"{}",TRUE);CHECK("W07 hub error ends",ends==2);hc_luna_cancel(sub);
    disconnect_handler((LSHandle *)&handle_stub,disconnect_context);
    CHECK("W07 disconnected calls refused",hc_luna_call(SETTINGS "getSystemSettings",RECORD_GET,1,c)==NULL);
    hc_luna_close(c);g_main_loop_unref(loop);
    CHECK("W19 no live tokens",nth_subscription(0)==NULL);
}
static void write_file(const char *p,const char *text) {
    char *dir=g_path_get_dirname(p);mkdir_p(dir);g_free(dir);CHECK("fixture file",g_file_set_contents(p,text,-1,NULL));
}
static void test_runtime(int mode) {
    char *case_name=g_strdup_printf("runtime-%d",mode),*root=g_build_filename(test_root,case_name,NULL);g_free(case_name);
    config=hc_watch_config();
    char *state=g_build_filename(root,"state",NULL),*os=g_build_filename(root,"os-release",NULL),*app=g_build_filename(root,"app",NULL);
    char *override=g_build_filename(root,"override/o.json",NULL),*link=g_build_filename(root,"init/homechooser",NULL),*log=g_build_filename(root,"watch.log",NULL);
    char *rt=g_build_filename(root,"rt",NULL),*scripts=g_build_filename(root,"service/scripts",NULL),*access=g_build_filename(rt,"homechooser-access",NULL);
    char *binary=g_build_filename(root,"service/homechooser-service",NULL),*home=g_build_filename(state,"home",NULL);
    char *access_source=g_build_filename(scripts,"homechooser-access",NULL),*common=g_build_filename(scripts,"lib/common.sh",NULL);
    config.state_dir=state;config.osrelease=os;config.app_dir=app;config.override=override;config.init_link=link;
    config.log=log;config.runtime=rt;config.scripts_dir=scripts;config.access_script=access;
    config.confirm_ms=config.preload_retry_ms=config.preload_settle_ms=config.notify_retry_ms=0;
    config.recheck_s=mode==6?1:300;config.preload=TRUE;config.stock_js=stock_js;
    write_file(home,"my.home");write_file(os,mode==5?"VERSION_ID=\"8.0\"\n":"VERSION_ID=\"11.0\"\n");mkdir_p(app);
    char *flag=g_build_filename(state,"homekey",NULL);write_file(flag,"");g_free(flag);
    filters=jdom_create(key_buf("[{\"file\":\"/usr/lib/qt5/qml/KeyFilters/systemUi.js\",\"handler\":\"handleKeyEvent\",\"metadata\":true}]"),jschema_all(),NULL);
    write_file(access_source,"#!/bin/sh\n. \"$(dirname \"$0\")/lib/common.sh\"\n[ \"$HC_TEST_COMMON\" = yes ]\n");write_file(common,"HC_TEST_COMMON=yes\n");
    write_file(binary,"installed binary sentinel"); installed_binary=binary;
    char *link_dir=g_path_get_dirname(link);mkdir_p(link_dir);g_free(link_dir);symlink(binary,link);
    record=parse_object("{\"home\":\"my.home\",\"kidsApp\":\"k.app\"}");
    runtime_case=mode;runtime_mode=TRUE;removed=FALSE;subscriptions=0;expected_subs=mode==5?2:4;ticks=0;preloads=0;initial_tasks=task_count();
    char *mapped=NULL,*copy=NULL;gsize mapped_size=0,copy_size=0;
    CHECK("W02 running image readable before unlink",g_file_get_contents("/proc/self/exe",&mapped,&mapped_size,NULL));
    int rc=hc_watch_run(&config,mode==5);
    CHECK("runtime driver ran",ticks==1);
    CHECK("W19 exit semantics",rc==((mode==1||mode==3||mode==4)?1:0));
    char *cached=g_build_filename(rt,"homechooser-service",NULL);struct stat st;
    CHECK("W02 native image staged",!stat(cached,&st) && (st.st_mode & 0111) && st.st_size>10000);
    CHECK("W02 runtime image readable",g_file_get_contents(cached,&copy,&copy_size,NULL));
    CHECK("W02 restart image matches running image",mapped && copy && mapped_size==copy_size && !memcmp(mapped,copy,mapped_size));
    g_free(mapped);g_free(copy);
    if(mode==0 || mode==5 || mode==6) {
        CHECK("W02 removal kept recovery helper",g_file_test(access,G_FILE_TEST_IS_REGULAR));
        CHECK("W13 removal clean state",!g_file_test(home,G_FILE_TEST_EXISTS) && !g_file_test(link,G_FILE_TEST_IS_SYMLINK));
        if(mode==5) {
            char *id=get_string(record,"home");CHECK("W13 stock record on 8",eq(id,FULLHOME));g_free(id);
            char *file=get_string(jarray_get(filters,0),"file");CHECK("W13 actual hook stock restored",eq(file,"/usr/lib/qt5/qml/KeyFilters/systemUi.js"));g_free(file);
        } else CHECK("W13 restored stock record",jis_null(member(record,"home")));
    }
    CHECK("W19 all subscriptions cancelled",nth_subscription(0)==NULL);
    CHECK("W14 exit preload",preloads>=1);
    runtime_mode=FALSE;j_release(&record);j_release(&filters);
    watch_remove_tree(root);
    g_free(cached);g_free(common);g_free(access_source);g_free(home);g_free(binary);g_free(access);g_free(scripts);g_free(rt);g_free(log);g_free(link);g_free(override);g_free(app);g_free(os);g_free(state);g_free(root);
}
int main(int argc,char **argv) {
    if(argc!=3)return 2;
    test_root=argv[1];stock_js=argv[2];setbuf(stdout,NULL);test_client();
    for(int i=1;i<7;i++)test_runtime(i);
    /* qemu-user resolves guest /proc/self/exe by the original pathname, so run
     * the actual-unlink case last. Production has one run per process. */
    test_runtime(0);
    CHECK("W19 register/unregister balance",registrations==unregistrations+1);
    printf("%u passed, %u failed (native watcher Luna/runtime)\n",checks-failures,failures);return failures?1:0;
}
