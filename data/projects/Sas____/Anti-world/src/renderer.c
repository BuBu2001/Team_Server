#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#include "renderer.h"
#include "earth.h"

#define CHUNK_W 32
#define CHUNK_SIDE (CHUNK_W + 1)
#define CHUNK_RADIUS 1
#define MAX_CHUNKS 64
#define CHUNK_VERTS (CHUNK_SIDE * CHUNK_SIDE)

#define NEAR_PLANE 0.15f
#define FOG_NEAR 20.0f
#define FOG_FAR 46.0f

typedef struct {
    float x, y;
    float iz;
    float rz, gz, bz;
} SV;

typedef struct {
    int cx, cz;
    float *heights;
    Vec3 *normals;
    float *ar, *ag, *ab;
} Chunk;

static Chunk chunks[MAX_CHUNKS];
static float tmp_r[CHUNK_VERTS];
static float tmp_g[CHUNK_VERTS];
static float tmp_b[CHUNK_VERTS];
static Vec3 tmp_cam[CHUNK_VERTS];

int img_init(Image *img, int w, int h)
{
    img->w = w;
    img->h = h;
    img->pixels = (uint32_t *)malloc((size_t)w * (size_t)h * sizeof(uint32_t));
    img->zbuf = (float *)malloc((size_t)w * (size_t)h * sizeof(float));
    if (!img->pixels || !img->zbuf) {
        free(img->pixels);
        free(img->zbuf);
        img->pixels = NULL;
        img->zbuf = NULL;
        return 0;
    }
    return 1;
}

void img_free(Image *img)
{
    free(img->pixels);
    free(img->zbuf);
    img->pixels = NULL;
    img->zbuf = NULL;
}

Vec3 cam_forward(const Camera *cam)
{
    float cp = cosf(cam->pitch);
    return v3(cp * sinf(cam->yaw), sinf(cam->pitch), cp * cosf(cam->yaw));
}

Vec3 cam_right(const Camera *cam)
{
    return v3norm(v3cross(v3(0, 1, 0), cam_forward(cam)));
}

Vec3 cam_up(const Camera *cam)
{
    return v3cross(cam_forward(cam), cam_right(cam));
}

static void chunk_free(Chunk *c)
{
    free(c->heights);
    free(c->normals);
    free(c->ar);
    free(c->ag);
    free(c->ab);
    c->heights = NULL;
    c->normals = NULL;
    c->ar = NULL;
    c->ag = NULL;
    c->ab = NULL;
}

void chunks_unload_all(void)
{
    int i;
    for (i = 0; i < MAX_CHUNKS; i++)
        chunk_free(&chunks[i]);
}

static int chunk_find(int cx, int cz)
{
    int i;
    for (i = 0; i < MAX_CHUNKS; i++) {
        if (chunks[i].heights && chunks[i].cx == cx && chunks[i].cz == cz)
            return i;
    }
    return -1;
}

static int chunk_slot(void)
{
    int i;
    for (i = 0; i < MAX_CHUNKS; i++) {
        if (!chunks[i].heights)
            return i;
    }
    return -1;
}

static void chunk_generate(int cx, int cz)
{
    int slot = chunk_slot();
    int i, j;
    float wx0 = (float)(cx * CHUNK_W);
    float wz0 = (float)(cz * CHUNK_W);

    if (slot < 0)
        return;

    Chunk *c = &chunks[slot];
    c->heights = (float *)malloc(CHUNK_VERTS * sizeof(float));
    c->normals = (Vec3 *)malloc(CHUNK_VERTS * sizeof(Vec3));
    c->ar = (float *)malloc(CHUNK_VERTS * sizeof(float));
    c->ag = (float *)malloc(CHUNK_VERTS * sizeof(float));
    c->ab = (float *)malloc(CHUNK_VERTS * sizeof(float));

    if (!c->heights || !c->normals || !c->ar || !c->ag || !c->ab) {
        chunk_free(c);
        return;
    }

    c->cx = cx;
    c->cz = cz;

    for (j = 0; j < CHUNK_SIDE; j++) {
        for (i = 0; i < CHUNK_SIDE; i++) {
            c->heights[j * CHUNK_SIDE + i] =
                earth_height(wx0 + (float)i, wz0 + (float)j);
        }
    }

    for (j = 0; j < CHUNK_SIDE; j++) {
        for (i = 0; i < CHUNK_SIDE; i++) {
            int idx = j * CHUNK_SIDE + i;
            float wx = wx0 + (float)i;
            float wz = wz0 + (float)j;
            float h = c->heights[idx];

            float hL = i > 0 ? c->heights[idx - 1]
                             : earth_height(wx - 1.0f, wz);
            float hR = i < CHUNK_W ? c->heights[idx + 1]
                                   : earth_height(wx + 1.0f, wz);
            float hU = j > 0 ? c->heights[idx - CHUNK_SIDE]
                             : earth_height(wx, wz - 1.0f);
            float hD = j < CHUNK_W ? c->heights[idx + CHUNK_SIDE]
                                   : earth_height(wx, wz + 1.0f);

            float dhdx = (hR - hL) * 0.5f;
            float dhdz = (hD - hU) * 0.5f;
            c->normals[idx] = v3norm(v3(-dhdx, 1.0f, -dhdz));

            Biome b = earth_biome(wx, wz, h > 0.0f ? h : 0.0f);
            float r, g, bl;
            if (earth_sea_ice(wx, wz)) {
                r = 0.93f;
                g = 0.95f;
                bl = 0.98f;
            } else {
                earth_biome_color(b, wx, wz, h > 0.0f ? h : 0.0f,
                                  world_time.day_f, &r, &g, &bl);
            }
            c->ar[idx] = r;
            c->ag[idx] = g;
            c->ab[idx] = bl;
        }
    }
}

int chunks_loaded(void)
{
    int i, n = 0;
    for (i = 0; i < MAX_CHUNKS; i++) {
        if (chunks[i].heights)
            n++;
    }
    return n;
}

void chunks_update(float px, float pz)
{
    int pcx = (int)floorf(px / (float)CHUNK_W);
    int pcz = (int)floorf(pz / (float)CHUNK_W);
    int i, dx, dz;

    for (i = 0; i < MAX_CHUNKS; i++) {
        if (!chunks[i].heights)
            continue;
        int ddx = chunks[i].cx - pcx;
        int ddz = chunks[i].cz - pcz;
        if (ddx < 0) ddx = -ddx;
        if (ddz < 0) ddz = -ddz;
        if (ddx > CHUNK_RADIUS || ddz > CHUNK_RADIUS)
            chunk_free(&chunks[i]);
    }

    for (dz = -CHUNK_RADIUS; dz <= CHUNK_RADIUS; dz++) {
        for (dx = -CHUNK_RADIUS; dx <= CHUNK_RADIUS; dx++) {
            int cx = pcx + dx;
            int cz = pcz + dz;
            if (chunk_find(cx, cz) < 0)
                chunk_generate(cx, cz);
        }
    }
}

static uint32_t pack_rgb(float r, float g, float b)
{
    uint32_t ri = (uint32_t)clampf(r, 0.0f, 255.0f);
    uint32_t gi = (uint32_t)clampf(g, 0.0f, 255.0f);
    uint32_t bi = (uint32_t)clampf(b, 0.0f, 255.0f);
    return 0xFF000000u | (ri << 16) | (gi << 8) | bi;
}

static void draw_sky(Image *img, const LightState *ls)
{
    int w = img->w;
    int h = img->h;
    int y;

    for (y = 0; y < h; y++) {
        float t = (float)y / (float)(h - 1);
        float r = lerpf(ls->sky_top_r, ls->fog_r, t);
        float g = lerpf(ls->sky_top_g, ls->fog_g, t);
        float b = lerpf(ls->sky_top_b, ls->fog_b, t);
        uint32_t c = pack_rgb(r, g, b);
        uint32_t *row = img->pixels + (size_t)y * (size_t)w;
        int x;
        for (x = 0; x < w; x++)
            row[x] = c;
    }
}

static int clip_near(const Vec3 *pin, const float *rin, const float *gin,
                     const float *bin, int n,
                     Vec3 *pout, float *rout, float *gout, float *bout)
{
    int i, m = 0;

    for (i = 0; i < n; i++) {
        int j = (i + 1) % n;
        int in_i = pin[i].z >= NEAR_PLANE;
        int in_j = pin[j].z >= NEAR_PLANE;

        if (in_i) {
            pout[m] = pin[i];
            rout[m] = rin[i];
            gout[m] = gin[i];
            bout[m] = bin[i];
            m++;
        }

        if (in_i != in_j) {
            float t = (NEAR_PLANE - pin[i].z) / (pin[j].z - pin[i].z);
            pout[m] = v3(lerpf(pin[i].x, pin[j].x, t),
                         lerpf(pin[i].y, pin[j].y, t),
                         lerpf(pin[i].z, pin[j].z, t));
            rout[m] = lerpf(rin[i], rin[j], t);
            gout[m] = lerpf(gin[i], gin[j], t);
            bout[m] = lerpf(bin[i], bin[j], t);
            m++;
        }
    }

    return m;
}

static SV project(Vec3 v, float r, float g, float b, float f, int w, int h)
{
    float invz = 1.0f / v.z;
    SV s;
    s.x = w * 0.5f + f * v.x * invz;
    s.y = h * 0.5f - f * v.y * invz;
    s.iz = invz;
    s.rz = r * invz;
    s.gz = g * invz;
    s.bz = b * invz;
    return s;
}

static void raster_tri(Image *img, SV a, SV b, SV c)
{
    int w = img->w;
    int h = img->h;

    if (a.y > b.y) { SV t = a; a = b; b = t; }
    if (a.y > c.y) { SV t = a; a = c; c = t; }
    if (b.y > c.y) { SV t = b; b = c; c = t; }

    float area = (b.x - a.x) * (c.y - a.y) - (c.x - a.x) * (b.y - a.y);
    if (fabsf(area) < 1e-8f)
        return;
    float inv_area = 1.0f / area;

    float dyab = b.y - a.y;
    float dyac = c.y - a.y;
    float dybc = c.y - b.y;
    if (dyac <= 0.0f)
        return;

    float xab = dyab > 1e-9f ? (b.x - a.x) / dyab : 0.0f;
    float xac = (c.x - a.x) / dyac;
    float xbc = dybc > 1e-9f ? (c.x - b.x) / dybc : 0.0f;

    /* attribute gradients: V(x,y) = Va + vx*(x-a.x) + vy*(y-a.y) */
    float vx_iz = ((b.iz - a.iz) * (c.y - a.y) - (c.iz - a.iz) * (b.y - a.y)) * inv_area;
    float vy_iz = ((c.iz - a.iz) * (b.x - a.x) - (b.iz - a.iz) * (c.x - a.x)) * inv_area;
    float vx_rz = ((b.rz - a.rz) * (c.y - a.y) - (c.rz - a.rz) * (b.y - a.y)) * inv_area;
    float vy_rz = ((c.rz - a.rz) * (b.x - a.x) - (b.rz - a.rz) * (c.x - a.x)) * inv_area;
    float vx_gz = ((b.gz - a.gz) * (c.y - a.y) - (c.gz - a.gz) * (b.y - a.y)) * inv_area;
    float vy_gz = ((c.gz - a.gz) * (b.x - a.x) - (b.gz - a.gz) * (c.x - a.x)) * inv_area;
    float vx_bz = ((b.bz - a.bz) * (c.y - a.y) - (c.bz - a.bz) * (b.y - a.y)) * inv_area;
    float vy_bz = ((c.bz - a.bz) * (b.x - a.x) - (b.bz - a.bz) * (c.x - a.x)) * inv_area;

    int ymin = (int)ceilf(a.y);
    int ymax = (int)floorf(c.y);
    if (ymin < 0) ymin = 0;
    if (ymax >= h) ymax = h - 1;

    int y;
    for (y = ymin; y <= ymax; y++) {
        float yf = y + 0.5f;
        float x1, x2;
        if (yf <= b.y) {
            x1 = a.x + (yf - a.y) * xab;
            x2 = a.x + (yf - a.y) * xac;
        } else {
            x1 = b.x + (yf - b.y) * xbc;
            x2 = a.x + (yf - a.y) * xac;
        }
        if (x1 > x2) { float t = x1; x1 = x2; x2 = t; }

        int xmin = (int)ceilf(x1);
        int xmax = (int)floorf(x2);
        if (xmin < 0) xmin = 0;
        if (xmax >= w) xmax = w - 1;

        int idx = y * w + xmin;
        float dxp = (float)xmin + 0.5f - a.x;
        float dyp = yf - a.y;
        float izv = a.iz + vx_iz * dxp + vy_iz * dyp;
        float rzv = a.rz + vx_rz * dxp + vy_rz * dyp;
        float gzv = a.gz + vx_gz * dxp + vy_gz * dyp;
        float bzv = a.bz + vx_bz * dxp + vy_bz * dyp;

        int x;
        for (x = xmin; x <= xmax; x++) {
            if (izv > img->zbuf[idx]) {
                float invz = 1.0f / izv;
                img->pixels[idx] = pack_rgb(rzv * invz, gzv * invz, bzv * invz);
                img->zbuf[idx] = izv;
            }
            izv += vx_iz;
            rzv += vx_rz;
            gzv += vx_gz;
            bzv += vx_bz;
            idx++;
        }
    }
}
static void draw_tri(Image *img, float f,
                     Vec3 p0, Vec3 p1, Vec3 p2,
                     float r0, float g0, float b0,
                     float r1, float g1, float b1,
                     float r2, float g2, float b2)
{
    Vec3 pin[3], pout[8];
    float rin[3], gin[3], bin[3];
    float rout[8], gout[8], bout[8];
    int n, i;

    pin[0] = p0; pin[1] = p1; pin[2] = p2;
    rin[0] = r0; rin[1] = r1; rin[2] = r2;
    gin[0] = g0; gin[1] = g1; gin[2] = g2;
    bin[0] = b0; bin[1] = b1; bin[2] = b2;

    n = clip_near(pin, rin, gin, bin, 3, pout, rout, gout, bout);
    if (n < 3)
        return;

    for (i = 1; i + 1 < n; i++) {
        SV a = project(pout[0], rout[0], gout[0], bout[0], f, img->w, img->h);
        SV b = project(pout[i], rout[i], gout[i], bout[i], f, img->w, img->h);
        SV c = project(pout[i + 1], rout[i + 1], gout[i + 1], bout[i + 1], f, img->w, img->h);
        raster_tri(img, a, b, c);
    }
}

static void water_vert(const LightState *ls, float wx, float wz, float depth,
                       Vec3 *camv, float *cr, float *cg, float *cb,
                       const Camera *cam, const Vec3 *fwd,
                       const Vec3 *right, const Vec3 *up)
{
    float t = ls->water_time;
    float w1 = 1.2f * sinf(wx * 0.6f + t * 1.3f);
    float w2 = 1.1f * cosf(wz * 0.5f - t * 0.9f);
    float dwdx = 1.2f * 0.6f * cosf(wx * 0.6f + t * 1.3f);
    float dwdz = -1.1f * 0.5f * sinf(wz * 0.5f - t * 0.9f);
    float wy = w1 + w2;

    Vec3 n = v3norm(v3(-dwdx, 1.0f, -dwdz));
    Vec3 sun = ls->sun;
    Vec3 half = v3norm(v3add(sun, v3(0, 1, 0)));

    float deep = clampf(-depth / 900.0f, 0.0f, 1.0f);
    float br = lerpf(0.05f, 0.02f, deep);
    float bg = lerpf(0.32f, 0.11f, deep);
    float bb = lerpf(0.35f, 0.18f, deep);

    float li = ls->ambient + ls->diffuse * maxf(0.0f, v3dot(n, sun));
    float se = clampf((sun.y - 0.80f) * 8.0f, 0.0f, 1.0f);
    float spec = powf(maxf(0.0f, v3dot(n, half)), 26.0f)
                 * ls->diffuse * (0.45f * (1.0f - 0.9f * se));

    Vec3 d = v3(wx - cam->pos.x, wy - cam->pos.y, wz - cam->pos.z);
    float cz = v3dot(d, *fwd);

    float r = (br * li + spec) * 255.0f;
    float g = (bg * li + spec) * 255.0f;
    float b = (bb * li + spec) * 255.0f;

    if (cz > NEAR_PLANE) {
        float fog = clampf((cz - FOG_NEAR) / (FOG_FAR - FOG_NEAR), 0.0f, 1.0f);
        fog = fog * fog * (3.0f - 2.0f * fog);
        r = lerpf(r, ls->fog_r, fog);
        g = lerpf(g, ls->fog_g, fog);
        b = lerpf(b, ls->fog_b, fog);
    }

    *camv = v3(v3dot(d, *right), v3dot(d, *up), cz);
    *cr = r;
    *cg = g;
    *cb = b;
}

void render_frame(Image *img, const Camera *cam, const LightState *ls)
{
    Vec3 fwd = cam_forward(cam);
    Vec3 right = cam_right(cam);
    Vec3 up = cam_up(cam);
    float f = (img->h * 0.5f) / tanf(cam->fov * 0.5f);
    float cull_r = CHUNK_W * 0.75f;
    int ci, i, j;

    draw_sky(img, ls);
    memset(img->zbuf, 0, (size_t)img->w * (size_t)img->h * sizeof(float));

    for (ci = 0; ci < MAX_CHUNKS; ci++) {
        Chunk *c = &chunks[ci];
        if (!c->heights)
            continue;

        float wx0 = (float)(c->cx * CHUNK_W);
        float wz0 = (float)(c->cz * CHUNK_W);
        float wcx = wx0 + CHUNK_W * 0.5f;
        float wcz = wz0 + CHUNK_W * 0.5f;
        float wcy = c->heights[(CHUNK_SIDE / 2) * CHUNK_SIDE + CHUNK_SIDE / 2];
        if (wcy < 0.0f)
            wcy = 0.0f;

        Vec3 to = v3(wcx - cam->pos.x, wcy - cam->pos.y, wcz - cam->pos.z);
        if (v3dot(to, fwd) < -cull_r)
            continue;

        for (j = 0; j < CHUNK_SIDE; j++) {
            for (i = 0; i < CHUNK_SIDE; i++) {
                int idx = j * CHUNK_SIDE + i;
                float wx = wx0 + (float)i;
                float wz = wz0 + (float)j;
                float wy = c->heights[idx];

                Vec3 d = v3(wx - cam->pos.x, wy - cam->pos.y, wz - cam->pos.z);
                float cz = v3dot(d, fwd);

                Vec3 n = c->normals[idx];
                float li = ls->ambient + ls->diffuse * maxf(0.0f, v3dot(n, ls->sun));
                float r = c->ar[idx] * li * 255.0f;
                float g = c->ag[idx] * li * 255.0f;
                float b = c->ab[idx] * li * 255.0f;

                if (cz > NEAR_PLANE) {
                    float fog = clampf((cz - FOG_NEAR) / (FOG_FAR - FOG_NEAR), 0.0f, 1.0f);
                    fog = fog * fog * (3.0f - 2.0f * fog);
                    r = lerpf(r, ls->fog_r, fog);
                    g = lerpf(g, ls->fog_g, fog);
                    b = lerpf(b, ls->fog_b, fog);
                }

                tmp_r[idx] = r;
                tmp_g[idx] = g;
                tmp_b[idx] = b;
                tmp_cam[idx] = v3(v3dot(d, right), v3dot(d, up), cz);
            }
        }

        for (j = 0; j < CHUNK_W; j++) {
            for (i = 0; i < CHUNK_W; i++) {
                int a = j * CHUNK_SIDE + i;
                int b = a + 1;
                int cc = a + CHUNK_SIDE + 1;
                int d = a + CHUNK_SIDE;

                draw_tri(img, f,
                         tmp_cam[a], tmp_cam[b], tmp_cam[cc],
                         tmp_r[a], tmp_g[a], tmp_b[a],
                         tmp_r[b], tmp_g[b], tmp_b[b],
                         tmp_r[cc], tmp_g[cc], tmp_b[cc]);

                draw_tri(img, f,
                         tmp_cam[a], tmp_cam[cc], tmp_cam[d],
                         tmp_r[a], tmp_g[a], tmp_b[a],
                         tmp_r[cc], tmp_g[cc], tmp_b[cc],
                         tmp_r[d], tmp_g[d], tmp_b[d]);
            }
        }

        /* water pass (half resolution for speed) */
        for (j = 0; j < CHUNK_W; j += 2) {
            for (i = 0; i < CHUNK_W; i += 2) {
                int a = j * CHUNK_SIDE + i;
                int b = a + 2;
                int cc = a + 2 * CHUNK_SIDE + 2;
                int d = a + 2 * CHUNK_SIDE;
                float h[4] = { c->heights[a], c->heights[b], c->heights[cc], c->heights[d] };
                int wet = 0, k;
                for (k = 0; k < 4; k++)
                    if (h[k] < 0.0f)
                        wet = 1;
                if (!wet)
                    continue;

                float wx = wx0 + (float)i;
                float wz = wz0 + (float)j;
                Vec3 cv[4];
                float cr[4], cg[4], cb[4];

                water_vert(ls, wx, wz, h[0], &cv[0], &cr[0], &cg[0], &cb[0],
                           cam, &fwd, &right, &up);
                water_vert(ls, wx + 2.0f, wz, h[1], &cv[1], &cr[1], &cg[1], &cb[1],
                           cam, &fwd, &right, &up);
                water_vert(ls, wx + 2.0f, wz + 2.0f, h[2], &cv[2], &cr[2], &cg[2], &cb[2],
                           cam, &fwd, &right, &up);
                water_vert(ls, wx, wz + 2.0f, h[3], &cv[3], &cr[3], &cg[3], &cb[3],
                           cam, &fwd, &right, &up);

                draw_tri(img, f, cv[0], cv[1], cv[2],
                         cr[0], cg[0], cb[0], cr[1], cg[1], cb[1], cr[2], cg[2], cb[2]);
                draw_tri(img, f, cv[0], cv[2], cv[3],
                         cr[0], cg[0], cb[0], cr[2], cg[2], cb[2], cr[3], cg[3], cb[3]);
            }
        }
    }
}

void write_ppm(const char *path, const uint32_t *pixels, int w, int h)
{
    FILE *fp = fopen(path, "wb");
    int i, n;

    if (!fp)
        return;

    fprintf(fp, "P6\n%d %d\n255\n", w, h);
    n = w * h;
    for (i = 0; i < n; i++) {
        uint32_t p = pixels[i];
        unsigned char rgb[3];
        rgb[0] = (unsigned char)((p >> 16) & 0xFF);
        rgb[1] = (unsigned char)((p >> 8) & 0xFF);
        rgb[2] = (unsigned char)(p & 0xFF);
        fwrite(rgb, 1, 3, fp);
    }

    fclose(fp);
}