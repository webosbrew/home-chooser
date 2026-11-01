/* SPDX-License-Identifier: Apache-2.0 */
/* Real child pipes, GIOChannel readers and reaping under qemu; no LS2 stubs. */
#define _GNU_SOURCE
#include "last-input.h"
#include "luna.h"
#include "log.h"
#include "watcher/internal.h"
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/wait.h>

PmLogContext hc_log;
static unsigned checks, failures;
#define CHECK(id, expr) do { checks++; if (!(expr)) { failures++; fprintf(stderr,"FAIL %s line %d: %s\n",id,__LINE__,#expr); } } while(0)
static int runtime_case, expected_subs, ticks, initial_tasks;
static const char *stock_js, *fixture_source;
static HCWatchConfig config;
static char *test_root, *installed_binary, *fixture_root, *program;
static jvalue_ref record, filters;
static int fifo[4];
static const char *methods[]={"getSystemSettings","listApps","getForegroundAppInfo","getAppLifeEvents"};
static void write_file(const char *p,const char *text) {
    char *dir=g_path_get_dirname(p);mkdir_p(dir);g_free(dir);CHECK("fixture file",g_file_set_contents(p,text,-1,NULL));
}
static void fixture_open(const char *root) {
    fixture_root=g_strdup(root);program=g_build_filename(root,"luna-send",NULL);
    char *source=NULL;CHECK("fixture source",g_file_get_contents(fixture_source,&source,NULL,NULL));write_file(program,source);g_free(source);chmod(program,0755);
    for(unsigned i=0;i<G_N_ELEMENTS(fifo);i++) {
        char *p=g_strdup_printf("%s/%s.fifo",root,methods[i]);CHECK("fixture fifo",mkfifo(p,0600)==0);fifo[i]=open(p,O_RDWR|O_NONBLOCK|O_CLOEXEC);CHECK("fixture pipe",fifo[i]>=0);g_free(p);
    }
}
static void fixture_close(void) {
    for(unsigned i=0;i<G_N_ELEMENTS(fifo);i++)close(fifo[i]);
    g_free(fixture_root);g_free(program);
}
static jvalue_ref state_read(void) {
    char *p=g_build_filename(fixture_root,"state.json",NULL),*text=watch_read(p);jvalue_ref state=parse_object(text);g_free(p);g_free(text);return state;
}
static void save_state(gboolean removed) {
    jvalue_ref state=jobject_create();put_bool(state,"removed",removed);
    jobject_set(state,key_buf("record"),record?jvalue_duplicate(record):jobject_create());
    jobject_set(state,key_buf("filters"),filters?jvalue_duplicate(filters):jarray_create(NULL));
    char *p=g_build_filename(fixture_root,"state.json",NULL);write_file(p,jvalue_stringify(state));g_free(p);j_release(&state);
}
static void load_state(void) {
    jvalue_ref state=state_read();j_release(&record);j_release(&filters);record=jvalue_duplicate(member(state,"record"));filters=jvalue_duplicate(member(state,"filters"));j_release(&state);
}
static void send_line(int stream,const char *line) {
    CHECK("stream write",write(fifo[stream],line,strlen(line))==(ssize_t)strlen(line));
}
static gboolean no_children(void) {
    int status;errno=0;pid_t pid=waitpid(-1,&status,WNOHANG);
    return pid==-1 && errno==ECHILD;
}
static int task_count(void) {
    GDir *d=g_dir_open("/proc/self/task",0,NULL);int n=0;if(!d)return -1;while(g_dir_read_name(d))n++;g_dir_close(d);return n;
}
static void pump_ms(unsigned ms) {
    gint64 end=g_get_monotonic_time()+ms*1000;
    do { while(g_main_context_pending(NULL))g_main_context_iteration(NULL,FALSE);g_usleep(1000); } while(g_get_monotonic_time()<end);
}
static gboolean ready(int n) {
    for(int i=0;i<n;i++) {
        char *p=g_strdup_printf("%s/%s.ready",fixture_root,methods[i]);gboolean exists=g_file_test(p,G_FILE_TEST_EXISTS);g_free(p);if(!exists)return FALSE;
    }
    return TRUE;
}
static int count_calls(const char *method) {
    char *p=g_build_filename(fixture_root,"calls.jsonl",NULL),*text=watch_read(p);int count=0;
    char **lines=g_strsplit(text?text:"","\n",-1);
    for(int i=0;lines[i];i++) { jvalue_ref entry=parse_object(lines[i]);char *uri=get_string(entry,"uri");if(uri&&g_str_has_suffix(uri,method))count++;g_free(uri);j_release(&entry); }
    g_strfreev(lines);g_free(p);g_free(text);return count;
}
static void check_calls(int subscriptions) {
    char *p=g_build_filename(fixture_root,"calls.jsonl",NULL),*text=watch_read(p);char **lines=g_strsplit(text?text:"","\n",-1);int streams=0;
    for(int i=0;lines[i];i++) {
        jvalue_ref entry=parse_object(lines[i]);if(!entry)continue;
        jvalue_ref args=member(entry,"args");
        raw_buffer flag=jstring_get_fast(jarray_get(args,0));CHECK("W06 fixed caller argv",flag.m_len==2&&!memcmp(flag.m_str,"-a",2));
        char *id=NULL;raw_buffer b=jstring_get_fast(jarray_get(args,1));id=g_strndup(b.m_str,b.m_len);CHECK("W06 application identity",eq(id,HC_LUNA_IDENTITY));g_free(id);
        int group=0,parent=0;jnumber_get_i32(member(entry,"group"),&group);jnumber_get_i32(member(entry,"parent"),&parent);
        CHECK("W19 same process group",group==getpgrp());CHECK("W19 direct child",parent==getpid());
        bool stream=FALSE;jboolean_get(member(entry,"stream"),&stream);
        if(stream) {
            streams++;bool subscribe=FALSE;jboolean_get(member(member(entry,"payload"),"subscribe"),&subscribe);CHECK("W06 subscribe payload",subscribe);
            char *uri=get_string(entry,"uri");
            if(g_str_has_suffix(uri,methods[0])) {
                jvalue_ref expected=parse_object("{\"category\":\"general\",\"keys\":[\"defaultApps\"],\"subscribe\":true}");
                CHECK("W06 settings payload",jvalue_equal(member(entry,"payload"),expected));j_release(&expected);
            }
            g_free(uri);
        }
        j_release(&entry);
    }
    CHECK("W06 subscription count",streams==subscriptions);g_strfreev(lines);g_free(text);g_free(p);
}
static gboolean signal_stop(gpointer unused) { (void)unused;kill(getpid(),SIGTERM);return G_SOURCE_REMOVE; }
static gboolean runtime_drive(gpointer context) {
    (void)context;
    if(!ready(expected_subs))return G_SOURCE_CONTINUE;
    ticks++;CHECK("single thread watcher",task_count()==initial_tasks);
    if(runtime_case==0 || runtime_case==5 || runtime_case==6) {
        load_state();save_state(TRUE);watch_remove_tree(config.app_dir);watch_remove_tree(config.scripts_dir);unlink(installed_binary);
        if(runtime_case==0) {
            char *image=g_file_read_link("/proc/self/exe",NULL);CHECK("W02 mapped test image",image&&g_str_has_suffix(image,"/runtime-image"));
            if(image&&g_str_has_suffix(image,"/runtime-image"))CHECK("W02 unlink running image",unlink(image)==0);
            g_free(image);
        }
        if(runtime_case!=6)send_line(1,"{\"returnValue\":true,\"apps\":[]}\n");
    } else if(runtime_case==1)send_line(0,"!exit\n");
    else if(runtime_case==2) { for(int i=1;i<expected_subs;i++)send_line(i,"!exit\n");g_timeout_add(200,signal_stop,NULL); }
    else if(runtime_case==3)send_line(0,"!crash\n");
    else if(runtime_case==4)send_line(0,"!storm\n");
    return G_SOURCE_REMOVE;
}
static int replies,ends;
static void received(const char *text,gboolean ended,void *context) {
    CHECK("W06 subscription context",context==&replies);
    if(ended)ends++;else { jvalue_ref value=parse_object(text);CHECK("W06 valid JSON only",jis_object(value));j_release(&value);replies++; }
}
static gboolean during_call(gpointer unused) { (void)unused;send_line(0,"{\"settings\":{\"defaultApps\":{}}}\n");return G_SOURCE_REMOVE; }
static void test_client(void) {
    char *root=g_build_filename(test_root,"client",NULL);fixture_open(root);save_state(FALSE);
    GMainLoop *loop=g_main_loop_new(NULL,FALSE);HCLuna *c=hc_luna_open(loop,program);CHECK("open child transport",c!=NULL);
    char *s=hc_luna_call(SETTINGS "getSystemSettings",RECORD_GET,2,c);CHECK("bounded reply",s&&strstr(s,"true"));g_free(s);
    CHECK("allowlist rejects",hc_luna_call("luna://bad/method","{}",1,c)==NULL);
    s=hc_luna_call(HC_LAST_INPUT_URI,HC_LAST_INPUT_PAYLOAD,2,c);CHECK("allowlist has eim getLastInput",s&&strstr(s,"true"));g_free(s);
    const char *tests[]={"garbage","no-newline","false","fail"};
    for(unsigned i=0;i<G_N_ELEMENTS(tests);i++) {
        char *payload=g_strdup_printf("{\"test\":\"%s\"}",tests[i]);s=hc_luna_call(SAM "getAppInfo",payload,2,c);
        CHECK("W20 one-shot parse/status",i==3?!s:s!=NULL);g_free(s);g_free(payload);CHECK("W19 one-shot reaped",no_children());
    }
    HCSubscription *sub=hc_luna_subscribe(c,SETTINGS "getSystemSettings","{\"subscribe\":true}",received,&replies);CHECK("subscribe child",sub!=NULL);
    send_line(0,"noise\n{bad json}\n{\"truncated\":\n{\"settings\":{\"home\":\"brace } {\"}}\n");pump_ms(150);
    CHECK("W06 garbage recovery",replies==1&&!ends);
    send_line(0,"{\n  \"settings\": {\n    \"defaultApps\": {\"home\": \"my.home\"}\n  }\n}\n");pump_ms(100);
    CHECK("W06 pretty-printed JSON",replies==2&&!ends);
    send_line(0,"{\n  \"settings\": {\n    \"defaultApps\": [\n      {\"id\":\"one\"},\n      {\"id\":\"two\"}\n    ]\n  }\n}\n");pump_ms(100);
    CHECK("W06 pretty nested objects keep outer frame",replies==3&&!ends);
    g_timeout_add(10,during_call,NULL);gint64 start=g_get_monotonic_time();s=hc_luna_call(SAM "getAppInfo","{\"test\":\"hang\"}",1,c);
    CHECK("W20 hanging child timeout",s==NULL && g_get_monotonic_time()-start>=900000 && g_get_monotonic_time()-start<3*G_USEC_PER_SEC);CHECK("W06 subscriptions run during call",replies==4);
    send_line(0,"{\"subscribed\":false}\n");pump_ms(50);CHECK("W07 JSON false is not EOF",!ends);
    send_line(0,"!close\n");pump_ms(100);CHECK("W07 EOF even while child hangs",ends==1);hc_luna_cancel(sub);CHECK("W19 EOF/hang children reaped",no_children());
    HCLuna *bad=hc_luna_open(loop,program);CHECK("single owner",bad==NULL);
    sub=hc_luna_subscribe(c,SAM "listApps","{\"subscribe\":true}",received,&replies);send_line(1,"!crash\n");pump_ms(100);CHECK("W07 nonzero exit terminal",ends==2);hc_luna_cancel(sub);
    hc_luna_close(c);CHECK("W19 no children at close",no_children());
    c=hc_luna_open(loop,"/not-installed/luna-send");CHECK("W20 missing executable",hc_luna_call(SAM "getAppInfo","{}",1,c)==NULL);hc_luna_close(c);
    g_main_loop_unref(loop);fixture_close();watch_remove_tree(root);g_free(root);
}
static void test_parent_death(void) {
    char *root=g_build_filename(test_root,"parent-death",NULL);fixture_open(root);save_state(FALSE);
    CHECK("subreaper available",prctl(PR_SET_CHILD_SUBREAPER,1)==0);
    pid_t parent=fork();
    if(parent==0) {
        GMainLoop *loop=g_main_loop_new(NULL,FALSE);HCLuna *client=hc_luna_open(loop,program);
        if(!client || !hc_luna_subscribe(client,SETTINGS "getSystemSettings","{\"subscribe\":true}",received,&replies))_exit(3);
        g_main_loop_run(loop);_exit(4);
    }
    CHECK("kill fixture parent created",parent>0);
    for(int i=0;i<3000&&!ready(1);i++)g_usleep(1000);
    char *path=g_build_filename(root,"getSystemSettings.ready",NULL),*text=watch_read(path);
    pid_t child=text?atoi(text):-1;CHECK("kill fixture child started",child>0);
    if(parent>0) { kill(parent,SIGKILL);int status;CHECK("killed parent reaped",waitpid(parent,&status,0)==parent); }
    if(child>0) {
        int status=0;pid_t result=0;
        for(int i=0;i<3000&&result==0;i++) { result=waitpid(child,&status,WNOHANG);if(!result)g_usleep(1000); }
        CHECK("W19 child dies with killed parent",result==child && WIFSIGNALED(status) && WTERMSIG(status)==SIGKILL);
        if(result==0) { kill(child,SIGKILL);waitpid(child,&status,0); }
    }
    CHECK("W19 killed process tree reaped",no_children());CHECK("subreaper reset",prctl(PR_SET_CHILD_SUBREAPER,0)==0);
    g_free(path);g_free(text);fixture_close();watch_remove_tree(root);g_free(root);
}
static void test_runtime(int mode) {
    char *case_name=g_strdup_printf("runtime-%d",mode),*root=g_build_filename(test_root,case_name,NULL);g_free(case_name);
    config=hc_watch_config();
    char *state=g_build_filename(root,"state",NULL),*os=g_build_filename(root,"os-release",NULL),*app=g_build_filename(root,"app",NULL);
    char *override=g_build_filename(root,"override/o.json",NULL),*link=g_build_filename(root,"init/homechooser",NULL),*log=g_build_filename(root,"watch.log",NULL);
    char *rt=g_build_filename(root,"rt",NULL),*scripts=g_build_filename(root,"service/scripts",NULL),*access=g_build_filename(rt,"homechooser-access",NULL);
    char *binary=g_build_filename(root,"service/homechooser-daemon",NULL),*home=g_build_filename(state,"home",NULL);
    char *access_source=g_build_filename(scripts,"homechooser-access",NULL),*common=g_build_filename(scripts,"lib/common.sh",NULL);
    char *gate_dir=g_build_filename(rt,"home-gate",NULL),*gate_binary=g_build_filename(root,"service/homechooser-home-gate",NULL);
    config.gate_dir=gate_dir;config.gate_binary=gate_binary;
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
    fixture_open(root);
    save_state(FALSE);
    config.luna_send=program;
    runtime_case=mode;expected_subs=4;ticks=0;initial_tasks=task_count();
    char *mapped=NULL,*copy=NULL;gsize mapped_size=0,copy_size=0;
    CHECK("W02 running image readable before unlink",g_file_get_contents("/proc/self/exe",&mapped,&mapped_size,NULL));
    guint driver=g_timeout_add(10,runtime_drive,NULL);
    int rc=hc_watch_run(&config,mode==5);
    if(!ticks)g_source_remove(driver);
    load_state();
    CHECK("runtime driver ran",ticks==1);
    CHECK("W19 exit semantics",rc==((mode==1||mode==3||mode==4)?1:0));
    char *cached=g_build_filename(rt,"homechooser-daemon",NULL);struct stat st;
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
    CHECK("W19 all children reaped",no_children());
    check_calls(expected_subs);
    CHECK("W14 exit preload",count_calls("setPreloadPolicy")>=1);
    j_release(&record);j_release(&filters);fixture_close();
    watch_remove_tree(root);
    g_free(gate_dir);g_free(gate_binary);g_free(cached);g_free(common);g_free(access_source);g_free(home);g_free(binary);g_free(access);g_free(scripts);g_free(rt);g_free(log);g_free(link);g_free(override);g_free(app);g_free(os);g_free(state);g_free(root);
}
int main(int argc,char **argv) {
    if(argc!=4)return 2;
    test_root=argv[1];stock_js=argv[2];fixture_source=argv[3];setbuf(stdout,NULL);signal(SIGPIPE,SIG_IGN);test_client();test_parent_death();
    for(int i=1;i<7;i++)test_runtime(i);
    /* qemu-user resolves guest /proc/self/exe via the pathname: unlink last. */
    test_runtime(0);
    printf("%u passed, %u failed (native watcher child transport/runtime)\n",checks-failures,failures);return failures?1:0;
}
