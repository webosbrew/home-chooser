/*
 * Home Chooser E2E fixture: a minimal native launcher (appCategory ["home"]).
 *
 * One source, two builds, chosen with -D macros (see build.sh):
 *   -DHC_VARIANT_NORMAL   opaque full-screen card (the pattern of samples/media/common/sdl_shell.c)
 *   -DHC_VARIANT_OVERLAY  transparent overlay window with one visible bar (the pattern of
 *                         Dock.app src/main.c native_hints() + src/platform/surface.c)
 *   -DHC_APP_ID='"..."'   the id shown on screen and written to the launch record
 *
 * It behaves like a real SDL launcher and nothing more: SDL hints, one window, the system SDL's
 * own lifecycle replaced by a registerNativeApp subscription (as Dock does) so a relaunch by SAM
 * is observable. Every launch, relaunch and close is recorded, see record().
 *
 * Only libc/libm, the firmware's libSDL2-2.0.so.0 and libhelpers.so.2 are always linked; the
 * overlay build also needs libwayland-client and libwayland-webos-client (as Dock does).
 */
#define _GNU_SOURCE
#include <SDL.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <syslog.h>
#include <time.h>
#include <unistd.h>
#include <webos-helpers/libhelpers.h>

#if defined(HC_VARIANT_OVERLAY)
#define HC_VARIANT "overlay"
#define HC_TITLE "HC FIXTURE NATIVE OVERLAY"
#define SDL_VIDEO_DRIVER_WAYLAND 1 /* exposes info.wl in SDL_syswm.h, as Dock's surface.c does */
#include <SDL_syswm.h>
#include <wayland-client-core.h>
#include <wayland-client-protocol.h>
#include <wayland-webos-shell-client-protocol.h>
#elif defined(HC_VARIANT_NORMAL)
#define HC_VARIANT "normal"
#define HC_TITLE "HC FIXTURE NATIVE NORMAL"
#else
#error "define HC_VARIANT_NORMAL or HC_VARIANT_OVERLAY"
#endif
#ifndef HC_APP_ID
#error "define HC_APP_ID"
#endif

/* ------------------------------------------------------------------ launch record */

/* Where the record goes, all of them on every event:
 *  1. syslog(3): /dev/log -> /tmp/log -> busybox syslogd -> /var/log/messages, tag "hcfixture".
 *  2. append-only files, one line per event (the line count is the launch counter). Two places
 *     because a jailed native app may see a private /tmp; the second survives in the shared tree.
 *  3. stderr. */
static const char *const record_files[] = {
    "/tmp/" HC_APP_ID ".launches",
    "/media/developer/temp/" HC_APP_ID ".launches",
};

static volatile int launch_count; /* process-local: launch=1, then relaunches */
static int relaunch_count;

static void record(const char *event, const char *detail) {
    char line[1024];
    time_t now = time(NULL);
    snprintf(line, sizeof(line), "hcfixture id=%s variant=%s event=%s pid=%d t=%ld %s", HC_APP_ID,
             HC_VARIANT, event, (int)getpid(), (long)now, detail ? detail : "");
    for (char *p = line; *p; p++)
        if (*p == '\n' || *p == '\r')
            *p = ' ';
    openlog("hcfixture", LOG_PID, LOG_USER);
    syslog(LOG_INFO, "%s", line);
    closelog();
    for (size_t i = 0; i < sizeof(record_files) / sizeof(record_files[0]); i++) {
        FILE *f = fopen(record_files[i], "a");
        if (f) {
            fprintf(f, "%s\n", line);
            fclose(f);
        }
    }
    fprintf(stderr, "%s\n", line);
}

/* ------------------------------------------------------------------ lifecycle */

static Uint32 relaunch_event;
static volatile int close_requested;
static HContext lifecycle;

/* registerNativeApp replies on every lifecycle change: {"returnValue":true,"message":"relaunch"..}.
 * Runs on libhelpers' thread; only records and posts an SDL event. */
static bool lifecycle_cb(LSHandle *sh, LSMessage *reply, void *ctx) {
    (void)sh;
    (void)ctx;
    const char *msg = HLunaServiceMessage(reply);
    if (!msg)
        return true;
    if (strstr(msg, "\"relaunch\"")) {
        char detail[900];
        snprintf(detail, sizeof(detail), "count=%d reply=%.700s", __sync_add_and_fetch(&launch_count, 1),
                 msg);
        record("relaunch", detail);
        SDL_Event e;
        SDL_zero(e);
        e.type = relaunch_event;
        SDL_PushEvent(&e);
    } else if (strstr(msg, "\"close\"")) {
        record("close", msg);
        close_requested = 1;
        SDL_Event e;
        SDL_zero(e);
        e.type = SDL_QUIT;
        SDL_PushEvent(&e);
    }
    return true;
}

/* ------------------------------------------------------------------ overlay surface (Dock) */

#ifdef HC_VARIANT_OVERLAY
/* Copied from Dock.app src/platform/surface.c: a second reference to the webOS shell to set the
 * overlay type and key policies directly, and clear the opaque region so alpha 0 shows through. */
static struct wl_webos_shell *g_shell;
static struct wl_webos_shell_surface *g_shell_surface;

static void shell_global(void *data, struct wl_registry *reg, uint32_t name, const char *iface,
                         uint32_t version) {
    (void)data;
    (void)version;
    if (!g_shell && !strcmp(iface, "wl_webos_shell"))
        g_shell = wl_registry_bind(reg, name, &wl_webos_shell_interface, 1);
}
static void shell_removed(void *data, struct wl_registry *reg, uint32_t name) {
    (void)data;
    (void)reg;
    (void)name;
}

static void surface_setup(SDL_Window *window) {
    SDL_SysWMinfo info;
    memset(&info, 0, sizeof(info));
    info.version.major = 2;
    info.version.minor = 0;
    info.version.patch = 9;
    if (!SDL_GetWindowWMInfo(window, &info) || info.subsystem != SDL_SYSWM_WAYLAND) {
        record("surface", "no-wayland-syswm");
        return;
    }
    struct wl_registry *registry = wl_display_get_registry(info.info.wl.display);
    static const struct wl_registry_listener listener = {shell_global, shell_removed};
    wl_registry_add_listener(registry, &listener, NULL);
    wl_display_roundtrip(info.info.wl.display);
    wl_registry_destroy(registry);
    if (!g_shell) {
        record("surface", "webos-shell-unavailable");
        return;
    }
    g_shell_surface = wl_webos_shell_get_shell_surface(g_shell, info.info.wl.surface);
    wl_webos_shell_surface_set_property(g_shell_surface, "_WEBOS_WINDOW_TYPE",
                                        "_WEBOS_WINDOW_TYPE_OVERLAY");
    wl_webos_shell_surface_set_property(g_shell_surface, "_WEBOS_ACCESS_POLICY_KEYS_BACK", "true");
    wl_webos_shell_surface_set_property(g_shell_surface, "_WEBOS_ACCESS_POLICY_KEYS_EXIT", "true");
    wl_webos_shell_surface_set_key_mask(g_shell_surface, UINT32_MAX);
    wl_proxy_marshal((struct wl_proxy *)info.info.wl.surface, WL_SURFACE_SET_OPAQUE_REGION, NULL);
    wl_display_flush(info.info.wl.display);
    record("surface", "overlay-properties-set");
}
#endif

/* ------------------------------------------------------------------ block text */

/* 5x7 font, 7 rows of 5 bits (bit 4 = left). Only what the labels need. */
struct glyph {
    char c;
    unsigned char row[7];
};
static const struct glyph font[] = {
    {' ', {0, 0, 0, 0, 0, 0, 0}},
    {'.', {0, 0, 0, 0, 0, 0x0C, 0x0C}},
    {':', {0, 0x0C, 0x0C, 0, 0x0C, 0x0C, 0}},
    {'-', {0, 0, 0, 0x1F, 0, 0, 0}},
    {'#', {0x0A, 0x0A, 0x1F, 0x0A, 0x1F, 0x0A, 0x0A}},
    {'0', {0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E}},
    {'1', {0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E}},
    {'2', {0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F}},
    {'3', {0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E}},
    {'4', {0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02}},
    {'5', {0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E}},
    {'6', {0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E}},
    {'7', {0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08}},
    {'8', {0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E}},
    {'9', {0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C}},
    {'A', {0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11}},
    {'B', {0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E}},
    {'C', {0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E}},
    {'D', {0x1E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1E}},
    {'E', {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F}},
    {'F', {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10}},
    {'G', {0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F}},
    {'H', {0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11}},
    {'I', {0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E}},
    {'J', {0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C}},
    {'K', {0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11}},
    {'L', {0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F}},
    {'M', {0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11}},
    {'N', {0x11, 0x11, 0x19, 0x15, 0x13, 0x11, 0x11}},
    {'O', {0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E}},
    {'P', {0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10}},
    {'Q', {0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D}},
    {'R', {0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11}},
    {'S', {0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E}},
    {'T', {0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04}},
    {'U', {0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E}},
    {'V', {0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04}},
    {'W', {0x11, 0x11, 0x11, 0x15, 0x15, 0x15, 0x0A}},
    {'X', {0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11}},
    {'Y', {0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04}},
    {'Z', {0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F}},
};

static const unsigned char *glyph_rows(char c) {
    if (c >= 'a' && c <= 'z')
        c = (char)(c - 'a' + 'A');
    for (size_t i = 0; i < sizeof(font) / sizeof(font[0]); i++)
        if (font[i].c == c)
            return font[i].row;
    return font[0].row;
}

static int text_cells(const char *s) {
    return (int)strlen(s) * 6 - 1;
}

/* Draws s with cell size `scale` pixels, x/y = top-left. */
static void draw_text(SDL_Renderer *r, const char *s, int x, int y, int scale) {
    for (; *s; s++, x += 6 * scale) {
        const unsigned char *rows = glyph_rows(*s);
        for (int ry = 0; ry < 7; ry++)
            for (int cx = 0; cx < 5; cx++)
                if (rows[ry] & (1 << (4 - cx))) {
                    SDL_Rect cell = {x + cx * scale, y + ry * scale, scale, scale};
                    SDL_RenderFillRect(r, &cell);
                }
    }
}

/* Largest scale at which s fits in 90% of width w. */
static int fit_scale(const char *s, int w) {
    int scale = (w * 9 / 10) / text_cells(s);
    return scale < 1 ? 1 : scale;
}

static void draw_centered(SDL_Renderer *r, const char *s, int w, int y, int scale) {
    draw_text(r, s, (w - text_cells(s) * scale) / 2, y, scale);
}

static void draw(SDL_Renderer *r, int w, int h) {
    char id[128], count[64];
    snprintf(id, sizeof(id), "%s", HC_APP_ID);
    snprintf(count, sizeof(count), "LAUNCH %d  RELAUNCH %d", 1, relaunch_count);
    int idscale = fit_scale(id, w);
    int titlescale = idscale * 3 / 2;
    if (text_cells(HC_TITLE) * titlescale > w * 9 / 10)
        titlescale = fit_scale(HC_TITLE, w);
    int countscale = idscale;
    int block = 7 * titlescale + 7 * idscale + 7 * countscale + 6 * idscale;

#ifdef HC_VARIANT_OVERLAY
    SDL_SetRenderDrawBlendMode(r, SDL_BLENDMODE_NONE);
    SDL_SetRenderDrawColor(r, 0, 0, 0, 0);
    SDL_RenderClear(r); /* fully transparent */
    int top = h / 20;
    SDL_Rect bar = {w / 20, top, w * 9 / 10, block + 4 * idscale};
    SDL_SetRenderDrawColor(r, 230, 120, 0, 255); /* the visible bar */
    SDL_RenderFillRect(r, &bar);
    int y = top + 2 * idscale;
#else
    SDL_SetRenderDrawColor(r, 20, 60, 140, 255); /* opaque card */
    SDL_RenderClear(r);
    int y = (h - block) / 2;
#endif
    SDL_SetRenderDrawColor(r, 255, 255, 255, 255);
    draw_centered(r, HC_TITLE, w, y, titlescale);
    y += 7 * titlescale + 3 * idscale;
    draw_centered(r, id, w, y, idscale);
    y += 7 * idscale + 3 * idscale;
    draw_centered(r, count, w, y, countscale);
    SDL_RenderPresent(r);
}

/* ------------------------------------------------------------------ main */

int main(int argc, char **argv) {
    char detail[700];
    snprintf(detail, sizeof(detail), "count=1 argc=%d params=%.500s", argc,
             argc > 1 && argv[1] ? argv[1] : "");
    launch_count = 1;
    record("launch", detail);

    /* A native webOS app is not started with these (samples/media/common/sdl_shell.c). */
    setenv("EGL_PLATFORM", "wayland", 0);
    setenv("XDG_RUNTIME_DIR", "/tmp/xdg", 0);

    /* Hints are plain strings, ignored by firmware that lacks them. Without the first two the TV
     * keeps Back/Exit for itself. REGISTER_APP=false and the registerNativeApp call below are
     * Dock's way to see "relaunch" itself (Dock main.c native_hints, webos_bus.c bus_start). */
    SDL_SetHint("SDL_WEBOS_ACCESS_POLICY_KEYS_BACK", "true");
    SDL_SetHint("SDL_WEBOS_ACCESS_POLICY_KEYS_EXIT", "true");
    SDL_SetHint("SDL_HINT_WEBOS_REGISTER_APP", "false");
#ifdef HC_VARIANT_OVERLAY
    /* Dock.app src/main.c:81-88; surface_setup() adds the direct property. */
    SDL_SetHint("SDL_HINT_WEBOS_WINDOW_GROUP_TYPE", "_WEBOS_WINDOW_TYPE_OVERLAY");
    SDL_SetHint("SDL_HINT_WEBOS_WINDOW_GROUP_OWNER", "false");
    SDL_SetHint("SDL_HINT_WEBOS_WINDOW_KEYMASK", "-1");
    SDL_SetHint("SDL_HINT_WEBOS_WINDOW_GROUP_LAYER", "hcfixture");
#endif
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS) != 0) {
        record("window", "SDL_Init-failed");
        fprintf(stderr, "SDL_Init: %s\n", SDL_GetError());
        return 1;
    }
    relaunch_event = SDL_RegisterEvents(1);

    SDL_DisplayMode mode;
    if (SDL_GetCurrentDisplayMode(0, &mode) != 0) {
        mode.w = 1920;
        mode.h = 1080;
    }
#ifdef HC_VARIANT_OVERLAY
    SDL_GL_SetAttribute(SDL_GL_ALPHA_SIZE, 8);
    Uint32 flags = SDL_WINDOW_FULLSCREEN_DESKTOP; /* Dock main.c */
#else
    Uint32 flags = SDL_WINDOW_FULLSCREEN; /* samples sdl_shell.c: DESKTOP does not fill the panel */
#endif
    SDL_Window *window = SDL_CreateWindow(HC_TITLE, SDL_WINDOWPOS_UNDEFINED, SDL_WINDOWPOS_UNDEFINED,
                                          mode.w, mode.h, flags);
    SDL_Renderer *renderer = window ? SDL_CreateRenderer(window, -1, SDL_RENDERER_ACCELERATED) : NULL;
    if (window && !renderer)
        renderer = SDL_CreateRenderer(window, -1, SDL_RENDERER_SOFTWARE);
    if (!renderer) {
        record("window", "create-failed");
        fprintf(stderr, "window/renderer: %s\n", SDL_GetError());
        SDL_Quit();
        return 1;
    }
#ifdef HC_VARIANT_OVERLAY
    surface_setup(window);
#endif
    snprintf(detail, sizeof(detail), "created %dx%d", mode.w, mode.h);
    record("window", detail);

    lifecycle.callback = lifecycle_cb;
    lifecycle.userdata = NULL;
    lifecycle.multiple = 1;
    lifecycle.pub = 1;
    char payload[256];
    snprintf(payload, sizeof(payload), "{\"id\":\"%s\"}", HC_APP_ID);
    int rc = HLunaServiceCall("luna://com.webos.applicationManager/registerNativeApp", payload, &lifecycle);
    snprintf(detail, sizeof(detail), "registerNativeApp rc=%d", rc);
    record("register", detail);

    bool running = true;
    draw(renderer, mode.w, mode.h);
    while (running && !close_requested) {
        SDL_Event e;
        if (!SDL_WaitEventTimeout(&e, 1000)) {
            draw(renderer, mode.w, mode.h);
            continue;
        }
        if (e.type == SDL_QUIT)
            running = false;
        else if (e.type == relaunch_event) {
            relaunch_count++;
            SDL_RaiseWindow(window);
            draw(renderer, mode.w, mode.h);
        } else if (e.type == SDL_KEYDOWN) {
            int sc = (int)e.key.keysym.scancode;
            if (sc == SDL_SCANCODE_ESCAPE || sc == 482 /* webOS Back */ || sc == 505 /* webOS Exit */)
                running = false;
        }
    }
    record("exit", "");
    if (lifecycle.ret_token || rc == 0)
        HUnregisterServiceCallback(&lifecycle);
    SDL_DestroyRenderer(renderer);
    SDL_DestroyWindow(window);
    SDL_Quit();
    return 0;
}
