/*
 * Home Chooser E2E fixture: a minimal native launcher (appCategory ["home"]).
 *
 * One source, two builds (see build.sh):
 *   -DHC_VARIANT_NORMAL   opaque full-screen solid colour (the pattern of samples/media/common/sdl_shell.c)
 *   -DHC_VARIANT_OVERLAY  transparent overlay window with one small strip (the pattern of
 *                         Dock.app src/main.c native_hints() + src/platform/surface.c)
 *   -DHC_APP_ID='"..."'   the appinfo id, sent to registerNativeApp
 *
 * It writes nothing and draws no text: the runner proves a launch from sam's own log and
 * applicationManager/running. It does only what a native webOS app must: get a window, register with
 * sam (registerNativeApp, so sam does not kill it), and handle relaunch and close.
 */
#define _GNU_SOURCE
#include <SDL.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <webos-helpers/libhelpers.h>

#if defined(HC_VARIANT_OVERLAY)
#define SDL_VIDEO_DRIVER_WAYLAND 1 /* exposes info.wl in SDL_syswm.h, as Dock's surface.c does */
#include <SDL_syswm.h>
#include <wayland-client-core.h>
#include <wayland-client-protocol.h>
#include <wayland-webos-shell-client-protocol.h>
#elif !defined(HC_VARIANT_NORMAL)
#error "define HC_VARIANT_NORMAL or HC_VARIANT_OVERLAY"
#endif
#ifndef HC_APP_ID
#error "define HC_APP_ID"
#endif

/* ------------------------------------------------------------------ lifecycle */

static Uint32 relaunch_event;
static HContext lifecycle;

/* registerNativeApp replies on every lifecycle change: {"returnValue":true,"message":"relaunch"..}.
 * Runs on libhelpers' thread; only posts an SDL event. */
static bool lifecycle_cb(LSHandle *sh, LSMessage *reply, void *ctx) {
    (void)sh;
    (void)ctx;
    const char *msg = HLunaServiceMessage(reply);
    if (!msg)
        return true;
    SDL_Event e;
    SDL_zero(e);
    if (strstr(msg, "\"relaunch\""))
        e.type = relaunch_event;
    else if (strstr(msg, "\"close\""))
        e.type = SDL_QUIT;
    else
        return true;
    SDL_PushEvent(&e);
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
        return;
    }
    struct wl_registry *registry = wl_display_get_registry(info.info.wl.display);
    static const struct wl_registry_listener listener = {shell_global, shell_removed};
    wl_registry_add_listener(registry, &listener, NULL);
    wl_display_roundtrip(info.info.wl.display);
    wl_registry_destroy(registry);
    if (!g_shell) {
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
}
#endif

/* ------------------------------------------------------------------ main */

static void paint(SDL_Renderer *r, int w, int h) {
#ifdef HC_VARIANT_OVERLAY
    SDL_SetRenderDrawBlendMode(r, SDL_BLENDMODE_NONE);
    SDL_SetRenderDrawColor(r, 0, 0, 0, 0);
    SDL_RenderClear(r); /* fully transparent */
    SDL_Rect strip = {w / 20, h / 20, w * 9 / 10, h / 12};
    SDL_SetRenderDrawColor(r, 230, 120, 0, 255);
    SDL_RenderFillRect(r, &strip);
#else
    (void)w;
    (void)h;
    SDL_SetRenderDrawColor(r, 20, 60, 140, 255);
    SDL_RenderClear(r);
#endif
    SDL_RenderPresent(r);
}

int main(void) {
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
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS) != 0)
        return 1;
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
    SDL_Window *window = SDL_CreateWindow(HC_APP_ID, SDL_WINDOWPOS_UNDEFINED, SDL_WINDOWPOS_UNDEFINED,
                                          mode.w, mode.h, flags);
    SDL_Renderer *renderer = window ? SDL_CreateRenderer(window, -1, SDL_RENDERER_ACCELERATED) : NULL;
    if (window && !renderer)
        renderer = SDL_CreateRenderer(window, -1, SDL_RENDERER_SOFTWARE);
    if (!renderer) {
        SDL_Quit();
        return 1;
    }
#ifdef HC_VARIANT_OVERLAY
    surface_setup(window);
#endif

    lifecycle.callback = lifecycle_cb;
    lifecycle.userdata = NULL;
    lifecycle.multiple = 1;
    lifecycle.pub = 1;
    HLunaServiceCall("luna://com.webos.applicationManager/registerNativeApp",
                     "{\"id\":\"" HC_APP_ID "\"}", &lifecycle);

    bool running = true;
    paint(renderer, mode.w, mode.h);
    while (running) {
        SDL_Event e;
        if (!SDL_WaitEventTimeout(&e, 1000)) {
            paint(renderer, mode.w, mode.h);
            continue;
        }
        if (e.type == SDL_QUIT)
            running = false;
        else if (e.type == relaunch_event) {
            SDL_RaiseWindow(window);
            paint(renderer, mode.w, mode.h);
        } else if (e.type == SDL_KEYDOWN) {
            int sc = (int)e.key.keysym.scancode;
            if (sc == SDL_SCANCODE_ESCAPE || sc == 482 /* webOS Back */ || sc == 505 /* webOS Exit */)
                running = false;
        }
    }
    SDL_DestroyRenderer(renderer);
    SDL_DestroyWindow(window);
    SDL_Quit();
    return 0;
}
