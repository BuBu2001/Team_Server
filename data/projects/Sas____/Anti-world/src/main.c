#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/keysym.h>

#include "renderer.h"
#include "earth.h"
#include "noise.h"

#define WIN_W 1920
#define WIN_H 1080
#define RENDER_W 960
#define RENDER_H 540

#define MOUSE_SENS 0.0010f
#define MOUSE_JUMP_CLAMP 150

#define EYE_HEIGHT 1.7f
#define GRAVITY 22.0f
#define JUMP_SPEED 8.5f
#define WALK_SPEED 7.0f
#define RUN_SPEED 14.0f

#define BANNER_TIME 4.0f

typedef struct {
    Vec3 pos;
    Vec3 vel;
    float yaw;
    float pitch;
    int grounded;
} Player;

static int mask_shift(unsigned long m)
{
    int s = 0;
    if (!m)
        return 0;
    while (!(m & 1)) {
        m >>= 1;
        s++;
    }
    return s;
}

static int mask_size(unsigned long m)
{
    int n = 0;
    if (!m)
        return 0;
    while (!(m & 1))
        m >>= 1;
    while (m & 1) {
        n++;
        m >>= 1;
    }
    return n;
}

static double now_sec(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9;
}

static int key_down(Display *dpy, KeyCode kc)
{
    char keys[32];
    if (!kc)
        return 0;
    XQueryKeymap(dpy, keys);
    return (keys[kc / 8] >> (kc % 8)) & 1;
}

static Cursor make_blank_cursor(Display *dpy, Window win)
{
    Pixmap pm;
    GC gc;
    XGCValues gv;
    XColor col;
    Cursor cur;

    pm = XCreatePixmap(dpy, win, 1, 1, 1);
    gc = XCreateGC(dpy, pm, 0, NULL);
    memset(&gv, 0, sizeof(gv));
    gv.foreground = 0;
    XChangeGC(dpy, gc, GCForeground, &gv);
    XDrawPoint(dpy, pm, gc, 0, 0);

    memset(&col, 0, sizeof(col));
    col.flags = DoRed | DoGreen | DoBlue;
    cur = XCreatePixmapCursor(dpy, pm, pm, &col, &col, 0, 0);

    XFreeGC(dpy, gc);
    XFreePixmap(dpy, pm);
    return cur;
}

static Camera player_camera(const Player *p)
{
    Camera cam;
    cam.pos = p->pos;
    cam.yaw = p->yaw;
    cam.pitch = p->pitch;
    cam.fov = 1.15f;
    return cam;
}

static void player_init(Player *p)
{
    float px = 0.0f, pz = -30.0f;
    int rad = 0;
    while (rad < 400) {
        if (earth_height(px, pz) > 3.0f)
            break;
        px += 3.0f;
        if (px > 180.0f) {
            px = -180.0f;
            pz += 2.0f;
        }
        rad += 3;
    }
    p->pos = v3(px, earth_height(px, pz) + EYE_HEIGHT, pz);
    p->vel = v3(0, 0, 0);
    p->yaw = 0.35f;
    p->pitch = -0.10f;
    p->grounded = 1;
    printf("Spawn: lon=%.1f lat=%.1f h=%.0fm\n",
           px, pz, earth_height(px, pz));
}

static void player_physics(Player *p, float move_x, float move_z,
                           int jump, int run, float dt)
{
    float speed = run ? RUN_SPEED : WALK_SPEED;
    float sy = sinf(p->yaw);
    float cy = cosf(p->yaw);

    Vec3 fwd = v3(sy, 0.0f, cy);
    Vec3 right = v3(cy, 0.0f, -sy);
    Vec3 wish = v3add(v3scale(fwd, move_z), v3scale(right, move_x));

    if (v3dot(wish, wish) > 1e-6f)
        wish = v3scale(v3norm(wish), speed);

    p->vel.x = wish.x;
    p->vel.z = wish.z;

    p->vel.y -= GRAVITY * dt;

    p->pos.x += p->vel.x * dt;
    p->pos.y += p->vel.y * dt;
    p->pos.z += p->vel.z * dt;

    float ground = earth_height(p->pos.x, p->pos.z);
    float surface = ground > 0.0f ? ground : 0.0f;

    if (p->pos.y <= surface + EYE_HEIGHT) {
        p->pos.y = surface + EYE_HEIGHT;
        p->vel.y = 0.0f;
        p->grounded = 1;
    } else {
        p->grounded = 0;
    }

    if (jump && p->grounded) {
        p->vel.y = JUMP_SPEED;
        p->grounded = 0;
    }
}

static void upscale(const uint32_t *src, int sw, int sh,
                    uint32_t *dst, int dw, int dh)
{
    int x, y;
    for (y = 0; y < dh; y++) {
        float fy = (float)y * (sh - 1) / (dh - 1);
        int y0 = (int)fy;
        int y1 = y0 + 1 < sh ? y0 + 1 : y0;
        float vy = fy - y0;
        for (x = 0; x < dw; x++) {
            float fx = (float)x * (sw - 1) / (dw - 1);
            int x0 = (int)fx;
            int x1 = x0 + 1 < sw ? x0 + 1 : x0;
            float ux = fx - x0;

            const uint32_t a = src[y0 * sw + x0];
            const uint32_t b = src[y0 * sw + x1];
            const uint32_t c = src[y1 * sw + x0];
            const uint32_t e = src[y1 * sw + x1];

            float rr = (a >> 16 & 255) * (1 - ux) * (1 - vy)
                     + (b >> 16 & 255) * ux * (1 - vy)
                     + (c >> 16 & 255) * (1 - ux) * vy
                     + (e >> 16 & 255) * ux * vy;
            float gg = (a >> 8 & 255) * (1 - ux) * (1 - vy)
                     + (b >> 8 & 255) * ux * (1 - vy)
                     + (c >> 8 & 255) * (1 - ux) * vy
                     + (e >> 8 & 255) * ux * vy;
            float bl = (a & 255) * (1 - ux) * (1 - vy)
                     + (b & 255) * ux * (1 - vy)
                     + (c & 255) * (1 - ux) * vy
                     + (e & 255) * ux * vy;

            dst[y * dw + x] = 0xFF000000u
                            | ((uint32_t)rr << 16)
                            | ((uint32_t)gg << 8)
                            | (uint32_t)bl;
        }
    }
}

static void upscale_packed(const uint32_t *src, int sw, int sh,
                           uint32_t *dst, int dw, int dh,
                           int rs, int gs, int bs,
                           int rl, int gl, int bl)
{
    int x, y;
    for (y = 0; y < dh; y++) {
        float fy = (float)y * (sh - 1) / (dh - 1);
        int y0 = (int)fy;
        int y1 = y0 + 1 < sh ? y0 + 1 : y0;
        float vy = fy - y0;
        for (x = 0; x < dw; x++) {
            float fx = (float)x * (sw - 1) / (dw - 1);
            int x0 = (int)fx;
            int x1 = x0 + 1 < sw ? x0 + 1 : x0;
            float ux = fx - x0;

            const uint32_t a = src[y0 * sw + x0];
            const uint32_t b = src[y0 * sw + x1];
            const uint32_t c = src[y1 * sw + x0];
            const uint32_t e = src[y1 * sw + x1];

            unsigned r = (unsigned)((a >> 16 & 255) * (1 - ux) * (1 - vy)
                          + (b >> 16 & 255) * ux * (1 - vy)
                          + (c >> 16 & 255) * (1 - ux) * vy
                          + (e >> 16 & 255) * ux * vy);
            unsigned g = (unsigned)((a >> 8 & 255) * (1 - ux) * (1 - vy)
                          + (b >> 8 & 255) * ux * (1 - vy)
                          + (c >> 8 & 255) * (1 - ux) * vy
                          + (e >> 8 & 255) * ux * vy);
            unsigned b2 = (unsigned)((a & 255) * (1 - ux) * (1 - vy)
                          + (b & 255) * ux * (1 - vy)
                          + (c & 255) * (1 - ux) * vy
                          + (e & 255) * ux * vy);

            unsigned out = 0;
            out |= (rl >= 8 ? (r >> (rl - 8)) : (r << (8 - rl))) << rs;
            out |= (gl >= 8 ? (g >> (gl - 8)) : (g << (8 - gl))) << gs;
            out |= (bl >= 8 ? (b2 >> (bl - 8)) : (b2 << (8 - bl))) << bs;
            dst[y * dw + x] = out;
        }
    }
}

static int font_ready = 0;
static XFontSet hud_font = NULL;
static XFontStruct *hud_font_struct = NULL;

static void hud_init(Display *dpy)
{
    char **missing;
    int n_missing;
    char *default_string;
    char pat[] = "-*-*-*-*-*-*-*-*-*-*-*-*-iso10646-1";
    hud_font = XCreateFontSet(dpy, pat, &missing, &n_missing, &default_string);
    if (hud_font) {
        font_ready = 1;
        return;
    }
    hud_font_struct = XLoadQueryFont(dpy, "fixed");
    if (hud_font_struct)
        font_ready = 1;
}

static void hud_text(Display *dpy, Window win, GC gc, int x, int y,
                     unsigned long color, const char *s)
{
    XSetForeground(dpy, gc, 0x000000);
    if (hud_font)
        Xutf8DrawString(dpy, win, hud_font, gc, x + 1, y + 1, s, (int)strlen(s));
    else if (hud_font_struct)
        XDrawString(dpy, win, gc, x + 1, y + 1, s, (int)strlen(s));

    XSetForeground(dpy, gc, color);
    if (hud_font)
        Xutf8DrawString(dpy, win, hud_font, gc, x, y, s, (int)strlen(s));
    else if (hud_font_struct)
        XDrawString(dpy, win, gc, x, y, s, (int)strlen(s));
}

static void print_controls(void)
{
    printf("Anti-world - Земля: климат, океаны, циклоны, день/ночь\n");
    printf("Resolution: %dx%d (render %dx%d)\n", WIN_W, WIN_H, RENDER_W, RENDER_H);
    printf("Controls:\n");
    printf("  WASD - move | Shift - run | Space - jump\n");
    printf("  Arrows - look | LMB - grab mouse | RMB - release | ESC - quit\n");
    printf("Bench: ./antiworld --bench [N] --out file.ppm\n");
}

int main(int argc, char **argv)
{
    int i;
    int bench = 0;
    const char *out_path = "shot.ppm";

    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--bench") && i + 1 < argc) {
            bench = atoi(argv[++i]);
            if (bench < 1)
                bench = 1;
        } else if (!strcmp(argv[i], "--out") && i + 1 < argc) {
            out_path = argv[++i];
        }
    }

    noise_init(20260923u);
    weather_init();

    world_time.year = 0;
    world_time.day = 1;
    world_time.hour = 8;
    world_time.minute = 0;
    world_time.day_f = 1.0f + 8.0f / 24.0f;

    Image img;
    if (!img_init(&img, RENDER_W, RENDER_H)) {
        fprintf(stderr, "Failed to allocate framebuffer\n");
        return 1;
    }

    uint32_t *fb_full = (uint32_t *)malloc((size_t)WIN_W * WIN_H * sizeof(uint32_t));
    if (!fb_full) {
        fprintf(stderr, "Failed to allocate full framebuffer\n");
        img_free(&img);
        return 1;
    }

    Player player;
    player_init(&player);
    chunks_update(player.pos.x, player.pos.z);

    if (bench > 0) {
        Camera cam = player_camera(&player);
        LightState ls;
        memset(&ls, 0, sizeof(ls));
        world_time.day = 165;
        world_time.hour = 12;
        world_time.day_f = 165.5f;
        Vec3 sun = earth_sun_dir(player.pos.z);
        float el = asinf(clampf(sun.y, -1.0f, 1.0f));
        float amb, df;
        earth_light(el, 0.0f, &amb, &df, &ls.sky_top_r, &ls.sky_top_g,
                    &ls.sky_top_b, &ls.fog_r, &ls.fog_g, &ls.fog_b);
        ls.ambient = amb;
        ls.diffuse = df;
        ls.sun = sun;
        ls.water_time = 0.0f;
        for (i = 0; i < bench; i++) {
            cam.yaw = player.yaw + (float)i * 0.02f;
            ls.water_time = (float)i * 0.4f;
            render_frame(&img, &cam, &ls);
        }
        upscale(img.pixels, img.w, img.h, fb_full, WIN_W, WIN_H);
        write_ppm(out_path, fb_full, WIN_W, WIN_H);
        printf("Bench: %d frames, %d chunks -> %s (%dx%d)\n",
               bench, chunks_loaded(), out_path, WIN_W, WIN_H);
        chunks_unload_all();
        img_free(&img);
        free(fb_full);
        return 0;
    }

    Display *dpy = XOpenDisplay(NULL);
    if (!dpy) {
        fprintf(stderr, "Cannot open X11 display\n");
        chunks_unload_all();
        img_free(&img);
        free(fb_full);
        return 1;
    }

    int screen = DefaultScreen(dpy);
    Visual *visual = DefaultVisual(dpy, screen);
    int depth = DefaultDepth(dpy, screen);
    Window root = RootWindow(dpy, screen);

    int rs = mask_shift(visual->red_mask);
    int gs = mask_shift(visual->green_mask);
    int bs = mask_shift(visual->blue_mask);
    int rl = mask_size(visual->red_mask);
    int gl = mask_size(visual->green_mask);
    int bl = mask_size(visual->blue_mask);

    Window win = XCreateSimpleWindow(dpy, root, 0, 0, WIN_W, WIN_H, 0,
                                     BlackPixel(dpy, screen),
                                     BlackPixel(dpy, screen));
    XStoreName(dpy, win, "Anti-world");
    XSelectInput(dpy, win,
                 ExposureMask | KeyPressMask | KeyReleaseMask |
                 ButtonPressMask | ButtonReleaseMask | PointerMotionMask);

    XSizeHints hints;
    memset(&hints, 0, sizeof(hints));
    hints.flags = PMinSize | PMaxSize;
    hints.min_width = hints.max_width = WIN_W;
    hints.min_height = hints.max_height = WIN_H;
    XSetWMNormalHints(dpy, win, &hints);

    Atom wm_delete = XInternAtom(dpy, "WM_DELETE_WINDOW", False);
    XSetWMProtocols(dpy, win, &wm_delete, 1);

    XMapWindow(dpy, win);

    XImage *ximg = XCreateImage(dpy, visual, depth, ZPixmap, 0,
                                NULL, WIN_W, WIN_H, 32, WIN_W * 4);
    if (!ximg) {
        fprintf(stderr, "Failed to create XImage\n");
        chunks_unload_all();
        XDestroyWindow(dpy, win);
        XCloseDisplay(dpy);
        img_free(&img);
        free(fb_full);
        return 1;
    }
    ximg->data = (char *)malloc((size_t)WIN_W * WIN_H * sizeof(uint32_t));
    if (!ximg->data) {
        fprintf(stderr, "Failed to allocate XImage data\n");
        chunks_unload_all();
        XDestroyImage(ximg);
        XDestroyWindow(dpy, win);
        XCloseDisplay(dpy);
        img_free(&img);
        free(fb_full);
        return 1;
    }

    GC gc = XCreateGC(dpy, win, 0, NULL);
    Cursor blank_cursor = make_blank_cursor(dpy, win);
    hud_init(dpy);

    KeyCode kc_esc = XKeysymToKeycode(dpy, XK_Escape);
    KeyCode kc_w = XKeysymToKeycode(dpy, XK_w);
    KeyCode kc_a = XKeysymToKeycode(dpy, XK_a);
    KeyCode kc_s = XKeysymToKeycode(dpy, XK_s);
    KeyCode kc_d = XKeysymToKeycode(dpy, XK_d);
    KeyCode kc_space = XKeysymToKeycode(dpy, XK_space);
    KeyCode kc_shift = XKeysymToKeycode(dpy, XK_Shift_L);
    KeyCode kc_shift_r = XKeysymToKeycode(dpy, XK_Shift_R);
    KeyCode kc_up = XKeysymToKeycode(dpy, XK_Up);
    KeyCode kc_down = XKeysymToKeycode(dpy, XK_Down);
    KeyCode kc_left = XKeysymToKeycode(dpy, XK_Left);
    KeyCode kc_right = XKeysymToKeycode(dpy, XK_Right);

    int running = 1;
    int grabbed = 0;
    int first_motion = 0;
    int latest_mx = 0, latest_my = 0;
    double start_time = now_sec();
    double last = start_time;
    double fps_timer = start_time;
    int frames = 0;
    char title[80];
    int banner_timer = 0; /* game minutes the banner is shown */
    char banner_text[128];
    banner_text[0] = '\0';

    print_controls();

    while (running) {
        while (XPending(dpy)) {
            XEvent e;
            XNextEvent(dpy, &e);

            switch (e.type) {
            case ClientMessage:
                if ((Atom)e.xclient.data.l[0] == wm_delete)
                    running = 0;
                break;

            case ButtonPress:
                if (e.xbutton.button == Button1 && !grabbed) {
                    XGrabPointer(dpy, win, True,
                                 ButtonPressMask | ButtonReleaseMask |
                                 PointerMotionMask,
                                 GrabModeAsync, GrabModeAsync,
                                 win, None, CurrentTime);
                    latest_mx = WIN_W / 2;
                    latest_my = WIN_H / 2;
                    XDefineCursor(dpy, win, blank_cursor);
                    grabbed = 1;
                    first_motion = 1;
                } else if (e.xbutton.button == Button3 && grabbed) {
                    XUngrabPointer(dpy, CurrentTime);
                    XUndefineCursor(dpy, win);
                    grabbed = 0;
                }
                break;

            case MotionNotify:
                if (grabbed) {
                    latest_mx = e.xmotion.x;
                    latest_my = e.xmotion.y;
                }
                break;
            }
        }

        if (key_down(dpy, kc_esc))
            running = 0;

        double t = now_sec();
        float real_dt = (float)(t - last);
        last = t;
        if (real_dt > 0.05f)
            real_dt = 0.05f;

        int prev_day = world_time.day;
        int prev_month = (world_time.day - 1) / 30;
        game_tick(real_dt);
        if (world_time.day != prev_day) {
            snprintf(banner_text, sizeof(banner_text),
                     "ДЕНЬ %d  |  ГОД %d", world_time.day, world_time.year);
            banner_timer = 8;
            printf("== новый день: %d день %d год ==\n",
                   world_time.day, world_time.year);
        }
        if (banner_timer > 0)
            banner_timer--;

        if ((world_time.day - 1) / 30 != prev_month) {
            /* season shifted: ice/snow extents change, rebuild the world */
            chunks_unload_all();
        }

        float dt_days = real_dt * 6.0f / 1440.0f;
        weather_update(dt_days);

        if (grabbed) {
            int dx = latest_mx - WIN_W / 2;
            int dy = latest_my - WIN_H / 2;
            if (dx > MOUSE_JUMP_CLAMP) dx = MOUSE_JUMP_CLAMP;
            if (dx < -MOUSE_JUMP_CLAMP) dx = -MOUSE_JUMP_CLAMP;
            if (dy > MOUSE_JUMP_CLAMP) dy = MOUSE_JUMP_CLAMP;
            if (dy < -MOUSE_JUMP_CLAMP) dy = -MOUSE_JUMP_CLAMP;

            if (first_motion) {
                first_motion = 0;
            } else if (dx != 0 || dy != 0) {
                player.yaw += (float)dx * MOUSE_SENS;
                player.pitch -= (float)dy * MOUSE_SENS;
            }
            player.pitch = clampf(player.pitch, -1.5f, 1.5f);
            XWarpPointer(dpy, None, win, 0, 0, 0, 0,
                         WIN_W / 2, WIN_H / 2);
            latest_mx = WIN_W / 2;
            latest_my = WIN_H / 2;
        }

        float move_x = 0.0f;
        float move_z = 0.0f;
        if (key_down(dpy, kc_w)) move_z += 1.0f;
        if (key_down(dpy, kc_s)) move_z -= 1.0f;
        if (key_down(dpy, kc_a)) move_x -= 1.0f;
        if (key_down(dpy, kc_d)) move_x += 1.0f;

        int jump = key_down(dpy, kc_space);
        int run = key_down(dpy, kc_shift) || key_down(dpy, kc_shift_r);

        player_physics(&player, move_x, move_z, jump, run, real_dt);

        float look = 0.9f * real_dt;
        if (key_down(dpy, kc_left))  player.yaw -= look;
        if (key_down(dpy, kc_right)) player.yaw += look;
        if (key_down(dpy, kc_up))    player.pitch += look;
        if (key_down(dpy, kc_down))  player.pitch -= look;
        player.pitch = clampf(player.pitch, -1.5f, 1.5f);

        chunks_update(player.pos.x, player.pos.z);

        float elon = player.pos.x;
        float elat = player.pos.z;
        float elev = earth_height(elon, elat);
        float ground_alt = elev > 0.0f ? elev : 0.0f;

        float precip = earth_precip_mmday(elon, elat, ground_alt);
        float rain = clampf(precip / 140.0f, 0.0f, 0.7f);

        Vec3 sun = earth_sun_dir(elat);
        float el = asinf(clampf(sun.y, -1.0f, 1.0f));

        LightState ls;
        memset(&ls, 0, sizeof(ls));
        earth_light(el, rain, &ls.ambient, &ls.diffuse,
                    &ls.sky_top_r, &ls.sky_top_g, &ls.sky_top_b,
                    &ls.fog_r, &ls.fog_g, &ls.fog_b);
        ls.sun = sun;
        ls.water_time = (float)(t - start_time) * 0.35f;

        Camera cam = player_camera(&player);
        render_frame(&img, &cam, &ls);
        upscale_packed(img.pixels, img.w, img.h,
                       (uint32_t *)ximg->data, WIN_W, WIN_H,
                       rs, gs, bs, rl, gl, bl);
        XPutImage(dpy, win, gc, ximg, 0, 0, 0, 0, WIN_W, WIN_H);

        /* HUD */
        {
            char line[160];
            unsigned long col_text = 0xEFEFFF;
            unsigned long col_banner = 0xFFD060;
            int y = 20;
            int xm = 24;

            snprintf(line, sizeof(line), "ДЕНЬ %d  ·  %02d:%02d  ·  ГОД %d",
                     world_time.day, world_time.hour, world_time.minute,
                     world_time.year);
            hud_text(dpy, win, gc, xm, y, col_text, line);
            y += 18;

            snprintf(line, sizeof(line), "ПОЗИЦИЯ: %+.1f %c  %+.1f %c  ·  ВЫСОТА: %+.0f м",
                     elon, elon >= 0.0f ? 'E' : 'W',
                     elat, elat >= 0.0f ? 'N' : 'S', elev);
            hud_text(dpy, win, gc, xm, y, col_text, line);
            y += 18;

            {
                Biome b = earth_biome(elon, elat, elev > 0.0f ? elev : 0.0f);
                snprintf(line, sizeof(line), "МЕСТНОСТЬ: %s%s",
                         earth_biome_name(b),
                         earth_sea_ice(elon, elat) ? " · ЛЬДЫ" : "");
            }
            hud_text(dpy, win, gc, xm, y, col_text, line);
            y += 18;

            snprintf(line, sizeof(line), "ПОГОДА: дождь %d%%",
                     (int)(rain * 100.0f));
            hud_text(dpy, win, gc, xm, y, col_text, line);

            if (banner_timer > 0 && banner_text[0]) {
                int bx = WIN_W / 2 - 180;
                int by = WIN_H / 2;
                hud_text(dpy, win, gc, bx + 2, by + 2, 0x0000A0, banner_text);
                hud_text(dpy, win, gc, bx, by, col_banner, banner_text);
            }
        }

        frames++;
        if (t - fps_timer >= 0.5) {
            double fps = frames / (t - fps_timer);
            snprintf(title, sizeof(title),
                     "Anti-world - %.0f FPS - %d chunks - д.%d г.%d",
                     fps, chunks_loaded(), world_time.day, world_time.year);
            XStoreName(dpy, win, title);
            frames = 0;
            fps_timer = t;
        }

        XFlush(dpy);

        double frame_time = now_sec() - t;
        if (frame_time < 1.0 / 60.0) {
            struct timespec ts;
            double sleep_s = 1.0 / 60.0 - frame_time;
            ts.tv_sec = (time_t)sleep_s;
            ts.tv_nsec = (long)((sleep_s - (double)ts.tv_sec) * 1e9);
            nanosleep(&ts, NULL);
        }
    }

    if (grabbed)
        XUngrabPointer(dpy, CurrentTime);

    XFreeGC(dpy, gc);
    free(ximg->data);
    ximg->data = NULL;
    XDestroyImage(ximg);
    XFreeCursor(dpy, blank_cursor);
    XDestroyWindow(dpy, win);
    XCloseDisplay(dpy);
    chunks_unload_all();
    img_free(&img);
    free(fb_full);

    printf("Bye.\n");
    return 0;
}