#include "earth.h"
#include "noise.h"
#include "gmath.h"
#include "earth_data.h"

#include <math.h>
#include <stdlib.h>

#define DEG (PI / 180.0f)
#define TWO_PI (2.0f * PI)

WorldTime world_time;

#define GAME_MIN_PER_REAL_SEC 6.0f
#define MAX_STORMS 6

static Storm storms[MAX_STORMS];

static float randf(void)
{
    static unsigned s = 987654u;
    s = s * 1664525u + 1013904223u;
    return (float)(s % 10000u) / 10000.0f;
}

float wrap_lon(float x)
{
    while (x > 180.0f) x -= 360.0f;
    while (x < -180.0f) x += 360.0f;
    return x;
}

static float clamp_lat(float lat)
{
    return clampf(lat, -89.0f, 89.0f);
}

static float wrap_rel(float lon, float ref)
{
    while (lon - ref > 180.0f) lon -= 360.0f;
    while (lon - ref < -180.0f) lon += 360.0f;
    return lon;
}

/* ------------------------------------------------------------------ */
/* embedded real Earth grids (0.5 deg, lon -180..180, lat 90..-90)     */

static void eart_cell(float lon, float lat, int *x, int *y)
{
    float fx = (wrap_lon(lon) + 180.0f) * 2.0f;
    float fy = (90.0f - clamp_lat(lat)) * 2.0f;
    if (fx >= EART_GRID_W) fx = EART_GRID_W - 1e-4f;
    if (fy >= EART_GRID_H) fy = EART_GRID_H - 1e-4f;
    if (fy < 0.0f) fy = 0.0f;
    *x = (int)fx;
    *y = (int)fy;
}

static void eart_sample(float lon, float lat, float *land, float *sdf_cells)
{
    int x, y;
    eart_cell(lon, lat, &x, &y);
    int x0 = x, x1 = x + 1, y0 = y, y1 = y;
    if (x1 >= EART_GRID_W) x1 = 0;
    if (y0 + 1 < EART_GRID_H) y1 = y0 + 1;

    float fx = (wrap_lon(lon) + 180.0f) * 2.0f - (float)x0;
    float fy = (90.0f - clamp_lat(lat)) * 2.0f - (float)y0;

    float l00 = EARTH_LAND[y0 * EART_GRID_W + x0] / 255.0f;
    float l10 = EARTH_LAND[y0 * EART_GRID_W + x1] / 255.0f;
    float l01 = EARTH_LAND[y1 * EART_GRID_W + x0] / 255.0f;
    float l11 = EARTH_LAND[y1 * EART_GRID_W + x1] / 255.0f;
    float l = (l00 * (1 - fx) + l10 * fx) * (1 - fy)
            + (l01 * (1 - fx) + l11 * fx) * fy;

    int8_t s00 = (int8_t)EARTH_SDF[y0 * EART_GRID_W + x0];
    int8_t s10 = (int8_t)EARTH_SDF[y0 * EART_GRID_W + x1];
    int8_t s01 = (int8_t)EARTH_SDF[y1 * EART_GRID_W + x0];
    int8_t s11 = (int8_t)EARTH_SDF[y1 * EART_GRID_W + x1];
    float sv = ((s00 * (1 - fx) + s10 * fx) * (1 - fy)
              + (s01 * (1 - fx) + s11 * fx) * fy) * 0.25f;

    *land = l;
    *sdf_cells = sv;
}

/* landform strength 0..1, 0 = open ocean, 1 = inland */
/* ------------------------------------------------------------------ */
/* continental plates: base altitudes                                   */

typedef struct { float lon, lat, wx, wy, base; } Plate;

static const Plate PLATES[] = {
    {  50.0f,  52.0f,  60.0f, 26.0f,    460.0f }, /* Eurasia      */
    {  16.0f,   4.0f,  24.0f, 27.0f,    700.0f }, /* Africa       */
    {  85.0f,  34.0f,  14.0f, 10.0f,   4400.0f }, /* Tibet        */
    { -98.0f,  46.0f,  26.0f, 30.0f,    520.0f }, /* N America    */
    { -62.0f, -14.0f,  12.0f, 30.0f,    640.0f }, /* S America    */
    { 134.0f, -25.0f,  17.0f, 12.0f,    400.0f }, /* Australia    */
    { -45.0f,  71.0f,  15.0f,  9.5f,  1500.0f }, /* Greenland    */
    { 115.0f,  -5.0f,  15.0f,  6.0f,    300.0f }, /* Indonesia    */
    { 139.0f,  37.0f,   5.0f,  3.0f,    550.0f }, /* Japan        */
    { 122.0f,  12.0f,   5.0f,  5.0f,    700.0f }, /* Philippines  */
    {  47.0f, -19.0f,   4.0f,  6.0f,    900.0f }, /* Madagascar   */
    { 143.0f,  -5.0f,   6.0f,  3.0f,    500.0f }, /* New Guinea   */
    { 173.0f, -42.0f,   3.0f,  2.0f,    900.0f }, /* New Zealand  */
};

static float plate_base(float lon, float lat)
{
    float best = 350.0f;
    unsigned i;
    for (i = 0; i < sizeof(PLATES) / sizeof(PLATES[0]); i++) {
        const Plate *p = &PLATES[i];
        float dx = wrap_rel(lon, p->lon) / p->wx;
        float dy = (clamp_lat(lat) - p->lat) / p->wy;
        float g = expf(-(dx * dx + dy * dy));
        if (p->base * g > best)
            best = p->base * g;
    }
    if (lat < -62.0f) {
        float t = 90.0f + lat; /* distance from the pole (0 at -90) */
        float g = expf(-(t * t) / (14.0f * 14.0f));
        float ta = lerpf(2150.0f, 1100.0f, smoothstepf(-85.0f, -64.0f, lat));
        if (ta * g > best)
            best = ta * g;
    }
    return best;
}

/* ------------------------------------------------------------------ */
/* mountain belts connected via polyline gaussians                      */

typedef struct { int n; const float *pts; float w; float h; float b[4]; } Belt;

static void init_belt(Belt *bt)
{
    int i;
    bt->b[0] = 1e9f; bt->b[1] = -1e9f;
    bt->b[2] = 1e9f; bt->b[3] = -1e9f;
    for (i = 0; i < bt->n; i++) {
        float la = bt->pts[2 * i], lo = bt->pts[2 * i + 1];
        if (la < bt->b[0]) bt->b[0] = la;
        if (la > bt->b[1]) bt->b[1] = la;
        if (lo < bt->b[2]) bt->b[2] = lo;
        if (lo > bt->b[3]) bt->b[3] = lo;
    }
}

static Belt BELTS[32];
static int belts_n = 0;

static void belt_add(int n, const float *pts, float w, float h)
{
    if (belts_n >= (int)(sizeof(BELTS) / sizeof(BELTS[0])))
        return;
    Belt *b = &BELTS[belts_n++];
    b->n = n / 2;
    b->pts = pts;
    b->w = w;
    b->h = h;
    init_belt(b);
}

static float seg_dist(float lon, float lat, float ax, float ay, float bx, float by)
{
    float dx = bx - ax; /* lon delta wrapped, dateline-safe */
    if (dx > 180.0f) dx -= 360.0f;
    if (dx < -180.0f) dx += 360.0f;
    float px = wrap_rel(lon, ax) - ax;
    float py = lat - ay;
    float ux = dx, uy = by - ay;
    float t;
    float denom = ux * ux + uy * uy;
    if (denom < 1e-8f)
        t = 0.0f;
    else
        t = clampf((px * ux + py * uy) / denom, 0.0f, 1.0f);
    float cx = px - t * ux;
    float cy = py - t * uy;
    return sqrtf(cx * cx + cy * cy);
}

static float belt_height(float lon, float lat)
{
    float total = 0.0f;
    int i;
    for (i = 0; i < belts_n; i++) {
        const Belt *b = &BELTS[i];
        if (lon < b->b[0] - b->w * 2.0f || lon > b->b[1] + b->w * 2.0f ||
            lat < b->b[2] - b->w * 2.0f || lat > b->b[3] + b->w * 2.0f)
            continue;
        float dmin = 1e9f;
        int j;
        for (j = 0; j + 1 < b->n; j++) {
            float d = seg_dist(lon, lat,
                               b->pts[2 * j], b->pts[2 * j + 1],
                               b->pts[2 * j + 2], b->pts[2 * j + 3]);
            if (d < dmin) dmin = d;
        }
        if (b->n == 1) {
            float d = seg_dist(lon, lat, b->pts[0], b->pts[1],
                               b->pts[0], b->pts[1]);
            if (d < dmin) dmin = d;
        }
        float g = expf(-(dmin * dmin) / (b->w * b->w));
        total += b->h * g;
    }
    return total;
}

static void belts_register(void)
{
    static int done = 0;
    if (done) return;
    done = 1;

#define BP(...) (const float[]){ __VA_ARGS__ }
    /* Himalayas-Karakoram-Hindu Kush */
    static const float p_himal[] = {65,33, 73,30, 80,28, 93,27, 100,26};
    belt_add((int)sizeof(p_himal)/sizeof(float), p_himal, 2.4f, 6800.0f);
    /* Pamir / Tian Shan */
    static const float p_pamir[] = {69,38, 75,40, 85,43};
    belt_add((int)sizeof(p_pamir)/sizeof(float), p_pamir, 1.8f, 4800.0f);
    /* Altai / Sayan */
    static const float p_altai[] = {85,45, 90,47, 97,51};
    belt_add((int)sizeof(p_altai)/sizeof(float), p_altai, 1.6f, 2800.0f);
    /* Urals */
    static const float p_ural[] = {58,52, 61,58, 65,66};
    belt_add((int)sizeof(p_ural)/sizeof(float), p_ural, 1.6f, 1500.0f);
    /* Caucasus / Alborz */
    static const float p_kav[] = {48,41, 52,42, 57,36};
    belt_add((int)sizeof(p_kav)/sizeof(float), p_kav, 1.3f, 4800.0f);
    /* Alps */
    static const float p_alps[] = {4,46, 10,46, 16,47};
    belt_add((int)sizeof(p_alps)/sizeof(float), p_alps, 0.8f, 3400.0f);
    /* Carpathians */
    static const float p_carpat[] = {16,48, 21,47, 25,46};
    belt_add((int)sizeof(p_carpat)/sizeof(float), p_carpat, 0.8f, 2100.0f);
    /* Dinarides / Apennines */
    static const float p_dinar[] = {14,44, 17,42, 21,40, 16,38};
    belt_add((int)sizeof(p_dinar)/sizeof(float), p_dinar, 0.6f, 1900.0f);
    /* Pyrenees */
    static const float p_pyr[] = {-1,42, 3,42};
    belt_add((int)sizeof(p_pyr)/sizeof(float), p_pyr, 0.6f, 2900.0f);
    /* Scandinavia */
    static const float p_scand[] = {7,62, 12,66, 17,69, 22,69};
    belt_add((int)sizeof(p_scand)/sizeof(float), p_scand, 1.2f, 1900.0f);
    /* Rockies / Canadian ranges */
    static const float p_rock[] = {-166,62, -141,62, -126,55, -115,52, -106,42};
    belt_add((int)sizeof(p_rock)/sizeof(float), p_rock, 2.0f, 3800.0f);
    /* Sierra / Cascades */
    static const float p_sierra[] = {-121,38, -121,42, -123,47};
    belt_add((int)sizeof(p_sierra)/sizeof(float), p_sierra, 1.0f, 3000.0f);
    /* Appalachians */
    static const float p_appal[] = {-84,36, -80,39, -71,43, -66,48};
    belt_add((int)sizeof(p_appal)/sizeof(float), p_appal, 0.9f, 1700.0f);
    /* Alaska */
    static const float p_ak[] = {-150,62, -147,65, -141,61};
    belt_add((int)sizeof(p_ak)/sizeof(float), p_ak, 1.4f, 4700.0f);
    /* Andes */
    static const float p_andes[] = {-78,8, -75,1, -72,-8, -69,-15, -67,-22,
                                    -69,-28, -70,-33, -71,-40, -69,-48, -66,-55};
    belt_add((int)sizeof(p_andes)/sizeof(float), p_andes, 1.8f, 6200.0f);
    /* East African volcanic peaks (Kilimanjaro area) */
    static const float p_east[] = { 36, -3, 38, -3 };
    belt_add(4, p_east, 1.4f, 4600.0f);
    /* Ethiopian highlands */
    static const float p_eth[] = {37,12, 39,7};
    belt_add((int)sizeof(p_eth)/sizeof(float), p_eth, 1.2f, 3200.0f);
    /* East African rift */
    static const float p_rift[] = {36,-4, 35,-8, 33,-11};
    belt_add((int)sizeof(p_rift)/sizeof(float), p_rift, 0.9f, 2600.0f);
    /* Atlas */
    static const float p_atlas[] = {-8,33, 0,32, 8,33};
    belt_add((int)sizeof(p_atlas)/sizeof(float), p_atlas, 1.3f, 3600.0f);
    /* Drakensberg */
    static const float p_drake[] = {29,-28, 32,-29, 34,-31};
    belt_add((int)sizeof(p_drake)/sizeof(float), p_drake, 0.9f, 2900.0f);
    /* New Guinea */
    static const float p_ng[] = {140,-6, 144,-5, 146,-3};
    belt_add((int)sizeof(p_ng)/sizeof(float), p_ng, 0.9f, 4500.0f);
    /* Great Dividing Range */
    static const float p_gdr[] = {145,-37, 147,-30, 146,-16};
    belt_add((int)sizeof(p_gdr)/sizeof(float), p_gdr, 0.9f, 1900.0f);
    /* Japanese Alps */
    static const float p_jap[] = {137,36, 138,36, 141,38};
    belt_add((int)sizeof(p_jap)/sizeof(float), p_jap, 0.7f, 2800.0f);
    /* Kamchatka */
    static const float p_kam[] = {158,53, 161,57, 163,60};
    belt_add((int)sizeof(p_kam)/sizeof(float), p_kam, 1.0f, 3300.0f);
    /* Iceland ridge */
    static const float p_ice[] = {-20,64, -16,65};
    belt_add((int)sizeof(p_ice)/sizeof(float), p_ice, 0.6f, 1200.0f);
    /* Transantarctic */
    static const float p_ant[] = {30,-74, 60,-77, 90,-78, 120,-77, 150,-74,
                                  178,-72, -152,-74, -120,-77, -90,-80, -62,-75};
    belt_add((int)sizeof(p_ant)/sizeof(float), p_ant, 1.6f, 4000.0f);
#undef BP
}

/* ------------------------------------------------------------------ */
/* ocean deeps                                                          */

typedef struct { float lon, lat, r, extra; } Trench;

static const Trench TRENCHES[] = {
    { 143.0f,  14.0f, 1.2f, 4800.0f }, /* Mariana       */
    { 128.0f,  12.0f, 1.5f, 3800.0f }, /* Philippine    */
    { 150.0f,  37.0f, 1.6f, 2800.0f }, /* Japan         */
    { 157.0f,  50.0f, 1.6f, 2600.0f }, /* Kuril         */
    { -168.0f, 52.0f, 1.8f, 2000.0f }, /* Aleutian      */
    {  -66.0f, 19.0f, 1.0f, 3000.0f }, /* Puerto Rico   */
    {  -88.0f, 10.0f, 1.2f, 1400.0f }, /* Middle America*/
    {  -73.0f,-25.0f, 1.6f, 1400.0f }, /* Peru-Chile    */
    {  110.0f, -8.0f, 1.4f, 2200.0f }, /* Java          */
    {  173.0f,-21.0f, 1.2f, 4300.0f }, /* Tonga         */
    {  -28.0f,-56.0f, 1.4f, 2000.0f }, /* S Sandwich    */
};

static float ocean_depth(float lon, float lat, float sdf_cells)
{
    float dsea = -sdf_cells;
    if (dsea < 0.0f) dsea = 0.0f;

    float base = 3.0f + 46.0f * powf(dsea, 1.7f);
    if (base > 5200.0f) base = 5200.0f;
    base -= fbm2(lon * 0.030f + 7.0f, lat * 0.030f - 4.0f, 2) * 180.0f;

    float deep = base;
    unsigned i;
    for (i = 0; i < sizeof(TRENCHES) / sizeof(TRENCHES[0]); i++) {
        const Trench *t = &TRENCHES[i];
        float dx = wrap_rel(lon, t->lon);
        float dy = lat - t->lat;
        float g = expf(-(dx * dx + dy * dy) / (t->r * t->r));
        deep = maxf(deep, t->extra * g); /* pull trench floor downwards */
    }
    {
        float h = maxf(base, deep);
        float cap = 1e9f;
        if (lat > 67.0f) cap = 350.0f;
        if (lat < -63.0f) cap = 500.0f;
        if (h > cap) h = cap;
        return -h;
    }
}

/* ------------------------------------------------------------------ */
/* rivers carve (real polylines)                                       */

typedef struct { int n; const float *pts; float w, depth; float b[4]; } River;

static River RIVERS[20];
static int rivers_n = 0;

static void river_add(int n, const float *pts, float w, float depth)
{
    if (rivers_n >= (int)(sizeof(RIVERS) / sizeof(RIVERS[0])))
        return;
    River *r = &RIVERS[rivers_n++];
    r->n = n;
    r->pts = pts;
    r->w = w;
    r->depth = depth;
    r->b[0] = 1e9f; r->b[1] = -1e9f;
    r->b[2] = 1e9f; r->b[3] = -1e9f;
    int i;
    for (i = 0; i < n; i++) {
        float la = pts[2 * i], lo = pts[2 * i + 1];
        if (la < r->b[0]) r->b[0] = la;
        if (la > r->b[1]) r->b[1] = la;
        if (lo < r->b[2]) r->b[2] = lo;
        if (lo > r->b[3]) r->b[3] = lo;
    }
}

static float river_cut(float lon, float lat)
{
    float worst = 0.0f;
    int i;
    for (i = 0; i < rivers_n; i++) {
        const River *r = &RIVERS[i];
        if (lon < r->b[0] - r->w || lon > r->b[1] + r->w ||
            lat < r->b[2] - r->w || lat > r->b[3] + r->w)
            continue;
        float dmin = 1e9f;
        int j;
        for (j = 0; j + 1 < r->n; j++) {
            float d = seg_dist(lon, lat,
                               r->pts[2 * j], r->pts[2 * j + 1],
                               r->pts[2 * j + 2], r->pts[2 * j + 3]);
            if (d < dmin) dmin = d;
        }
        float g = expf(-powf(dmin / (r->w * 1.6f), 2.2f));
        if (g * r->depth > worst)
            worst = g * r->depth;
    }
    return worst;
}

static void rivers_register(void)
{
    static int done = 0;
    if (done) return;
    done = 1;

#define RP(...) (const float[]){ __VA_ARGS__ }
    static const float r_amazon[] = { -51,-0.5, -57,-1, -64,-4, -70,-5, -73,-10 };
    river_add((int)sizeof(r_amazon)/sizeof(float)/2, r_amazon, 0.60f, 26.0f);
    static const float r_nile[] = { 31.5,31.5, 32.5,24, 33,18, 34,12, 32,4, 31,-1 };
    river_add((int)sizeof(r_nile)/sizeof(float)/2, r_nile, 0.40f, 16.0f);
    static const float r_congo[] = { 10,-6, 13,-3, 16,-1, 22,-1, 27,-2 };
    river_add((int)sizeof(r_congo)/sizeof(float)/2, r_congo, 0.50f, 18.0f);
    static const float r_niger[] = { 6,5, 0,12, -5,15, 1,18 };
    river_add((int)sizeof(r_niger)/sizeof(float)/2, r_niger, 0.40f, 14.0f);
    static const float r_missi[] = { -95,47, -93,44, -90,38, -90,34, -91,31, -90,29 };
    river_add((int)sizeof(r_missi)/sizeof(float)/2, r_missi, 0.50f, 22.0f);
    static const float r_yangtze[] = { 121,31, 115,30, 109,28, 104,27, 98,30 };
    river_add((int)sizeof(r_yangtze)/sizeof(float)/2, r_yangtze, 0.50f, 22.0f);
    static const float r_yellow[] = { 119,37, 112,36, 108,35, 104,36 };
    river_add((int)sizeof(r_yellow)/sizeof(float)/2, r_yellow, 0.30f, 8.0f);
    static const float r_lena[] = { 126,73, 126,70, 119,66, 112,59, 110,54 };
    river_add((int)sizeof(r_lena)/sizeof(float)/2, r_lena, 0.35f, 9.0f);
    static const float r_ob[] = { 73,67, 72,63, 69,57, 72,52, 75,47 };
    river_add((int)sizeof(r_ob)/sizeof(float)/2, r_ob, 0.40f, 10.0f);
    static const float r_volga[] = { 48,46, 49,50, 45,54, 38,57, 32,57 };
    river_add((int)sizeof(r_volga)/sizeof(float)/2, r_volga, 0.30f, 10.0f);
    static const float r_danube[] = { 29,45, 26,44, 22,44, 18,46, 13,46, 10,47 };
    river_add((int)sizeof(r_danube)/sizeof(float)/2, r_danube, 0.30f, 9.0f);
    static const float r_ganges[] = { 91,22, 87,25, 83,26, 79,31 };
    river_add((int)sizeof(r_ganges)/sizeof(float)/2, r_ganges, 0.50f, 20.0f);
    static const float r_indus[] = { 68,25, 71,29, 74,33, 78,36 };
    river_add((int)sizeof(r_indus)/sizeof(float)/2, r_indus, 0.35f, 16.0f);
    static const float r_mekong[] = { 107,11, 104,15, 101,18, 99,23, 97,29, 94,33 };
    river_add((int)sizeof(r_mekong)/sizeof(float)/2, r_mekong, 0.40f, 15.0f);
    static const float r_murray[] = { 139,-35, 142,-36, 146,-36, 147,-35 };
    river_add((int)sizeof(r_murray)/sizeof(float)/2, r_murray, 0.30f, 7.0f);
    static const float r_amur[] = { 141,53, 135,50, 127,48, 118,46 };
    river_add((int)sizeof(r_amur)/sizeof(float)/2, r_amur, 0.35f, 9.0f);
    static const float r_dnieper[] = { 32,47, 33,51, 31,53, 31,55 };
    river_add((int)sizeof(r_dnieper)/sizeof(float)/2, r_dnieper, 0.25f, 7.0f);
    static const float r_euphrat[] = { 48,31, 44,34, 39,36, 35,36 };
    river_add((int)sizeof(r_euphrat)/sizeof(float)/2, r_euphrat, 0.25f, 6.0f);
#undef RP
}

/* ------------------------------------------------------------------ */
/* temperature (needed for ice/snow). cycle-free version                */

static float base_season(float lat, float day)
{
    float lr = fabsf(clamp_lat(lat)) * DEG;
    float base = 26.0f * powf(maxf(0.0f, cosf(lr)), 1.15f) - 0.045f * fabsf(lat);
    float amp = 1.5f + 9.5f * powf(fabsf(sinf(lr)), 1.3f);
    float seas = amp * cosf(TWO_PI * (day - 172.0f) / 365.0f);
    float polar = 14.0f * smoothstepf(58.0f, 76.0f, fabsf(lat));
    float pfac;
    if (lat >= 0.0f)
        pfac = 0.35f + 0.65f * (1.0f - cosf(TWO_PI * (day - 172.0f) / 365.0f)) * 0.5f;
    else
        pfac = 0.35f + 0.65f * (1.0f + cosf(TWO_PI * (day - 172.0f) / 365.0f)) * 0.5f;
    return base + seas + (-polar * pfac);
}

/* temperature used by the world (with lapse + ocean currents) */
static float temperature_at(float lon, float lat, float elev, float day)
{
    float t = base_season(lat, day);
    if (elev > 0.0f)
        t -= 6.5f * elev / 1000.0f;
    t += earth_current_temp_offset(lon, lat);
    return t;
}

/* ice-state temperature, no ocean-current feedback (no recursion) */
static float pure_temperature(float lon, float lat, float elev, float day)
{
    (void)lon;
    float t = base_season(lat, day);
    if (elev > 0.0f)
        t -= 6.5f * elev / 1000.0f;
    return t;
}

float earth_temperature_c(float lon, float lat, float elev)
{
    return temperature_at(lon, lat, elev, world_time.day_f);
}

/* ------------------------------------------------------------------ */
/* height                                                            */

float earth_sea_ice(float lon, float lat);

float earth_height(float lon, float lat)
{
    static int order = -1;
    float l, s;
    eart_sample(lon, lat, &l, &s);

    if (s >= 0.0f || l > 0.7f) { /* land (or coast inside) */
        float w = smoothstepf(-1.0f, 1.6f, s);
        w = clampf(w, 0.0f, 1.0f);

        if (order < 0) {
            belts_register();
            rivers_register();
            order = 1;
        }

        float base = plate_base(lon, lat) + belt_height(lon, lat);
        float n1 = 1.0f + fbm2(lon * 0.045f + 13.0f, lat * 0.045f - 7.0f, 3) * 0.5f;
        float mm = n1 * 280.0f;
        float n2 = fbm2(lon * 0.30f + 5.0f, lat * 0.30f + 9.0f, 2) * 26.0f;

        float h = base + mm + n2;
        h = h * w;
        if (h < 1.5f)
            h = 1.5f;

        float cut = river_cut(lon, lat);
        if (cut > 0.0f) {
            float f = clampf(cut / 6.0f, 0.0f, 1.0f);
            h = lerpf(h, -cut * 0.6f, f);
        }

        if (lat < -62.0f) /* Antarctic ice sheet cap */
            h += 900.0f * smoothstepf(-70.0f, -83.0f, lat) + 300.0f;
        if (lat > 58.0f && lat < 85.0f) {
            float dx = wrap_rel(lon, -45.0f);
            float dy = lat - 71.0f;
            if (dx * dx + dy * dy < 150.0f)
                h += 600.0f * expf(-(dx * dx + dy * dy) / 150.0f);
        }

        if (h < -300.0f)
            h = -300.0f;
        return h;
    }

    /* sea */
    if (earth_sea_ice(lon, lat))
        return 3.0f + 0.5f * fbm2(lon * 0.12f, lat * 0.12f, 2);
    return ocean_depth(lon, lat, s);
}

float earth_sea_ice(float lon, float lat)
{
    float l, s;
    eart_sample(lon, lat, &l, &s);
    if (s > 0.5f)
        return 0.0f;
    return pure_temperature(lon, lat, 0.0f, world_time.day_f) < -5.0f;
}

/* snow line in meters for the current day */
static float snowline_m(float lat)
{
    float sl = 5000.0f - 82.0f * fabsf(lat);
    if (sl < 0.0f) sl = 0.0f;
    float seas = (lat >= 0.0f ? 1.0f : -1.0f) * cosf(TWO_PI * (world_time.day_f - 172.0f) / 365.0f);
    return sl + seas * 480.0f;
}

/* ------------------------------------------------------------------ */
/* precipitation / biomes                                               */

static float coastal(float lon, float lat)
{
    int landn = 0, i;
    for (i = 0; i < 8; i++) {
        float a2 = (float)i * 45.0f * DEG;
        float dx = cosf(a2) * 1.6f;
        float dz = sinf(a2) * 1.6f;
        if (earth_height(lon + dx, lat + dz) > 0.0f)
            landn++;
    }
    return (float)landn / 8.0f;
}

float earth_current_temp_offset(float lon, float lat)
{
    float lr = clamp_lat(lat) * DEG;
    float ph = fbm2(lon * 0.050f + 50.0f, lat * 0.050f - 20.0f, 3);
    float g = fbm2(lon * 0.050f + 51.0f, lat * 0.050f - 21.0f, 3) - ph;
    float off = 2.2f * cosf(lr) * sinf(lon * 0.20f + lat * 0.14f) + 2.2f * g;
    if (earth_height(lon, lat) > 0.0f)
        off = 0.0f;
    return clampf(off, -4.0f, 4.0f);
}

float earth_current_speed(float lon, float lat)
{
    float ph = fbm2(lon * 0.050f + 50.0f, lat * 0.050f - 20.0f, 3);
    float gx = fbm2(lon * 0.050f + 51.0f, lat * 0.050f - 20.0f, 3) - ph;
    float gy = fbm2(lon * 0.050f + 50.0f, lat * 0.050f - 21.0f, 3) - ph;
    float s = sqrtf(gx * gx + gy * gy) * 6.0f;
    if (earth_height(lon, lat) > 0.0f)
        return 0.0f;
    return clampf(s, 0.0f, 2.5f);
}

float earth_current_bearing(float lon, float lat)
{
    float ph = fbm2(lon * 0.050f + 50.0f, lat * 0.050f - 20.0f, 3);
    float gx = fbm2(lon * 0.050f + 51.0f, lat * 0.050f - 20.0f, 3) - ph;
    float gy = fbm2(lon * 0.050f + 50.0f, lat * 0.050f - 21.0f, 3) - ph;
    float brg = atan2f(gx, gy);
    if (earth_height(lon, lat) > 0.0f)
        return -1.0f;
    while (brg < 0.0f) brg += TWO_PI;
    return brg * (180.0f / PI);
}

static float precip_at(float lon, float lat, float elev, float day)
{
    float itcz = 23.44f * sinf(TWO_PI * (day - 81.0f) / 365.0f);
    float c = clamp_lat(lat);
    float rf = expf(-powf((c - itcz) / 13.0f, 2.0f));
    float eq = expf(-powf(c / 18.0f, 2.0f));
    float wl = expf(-powf((fabsf(c) - 48.0f) / 17.0f, 2.0f));
    float dry = expf(-powf((fabsf(c) - 23.0f) / 6.0f, 2.0f));
    float coast = 1.0f - coastal(lon, lat);

    float p = eq * 1500.0f + rf * 1200.0f + wl * 750.0f
            + coast * 260.0f - dry * 1100.0f;

    if (rf < 0.2f && p < 450.0f)
        p *= 0.5f;

    if (elev > 0.0f)
        p += elev * 0.06f;

    return clampf(p, 0.0f, 3300.0f);
}

float earth_precip_mmday(float lon, float lat, float elev)
{
    return precip_at(lon, lat, elev, world_time.day_f);
}

Biome earth_biome(float lon, float lat, float elev)
{
    float t = temperature_at(lon, lat, elev, 172.0f);
    float p = (precip_at(lon, lat, elev, 45.0f)
             + precip_at(lon, lat, elev, 135.0f)
             + precip_at(lon, lat, elev, 225.0f)
             + precip_at(lon, lat, elev, 315.0f)) * 0.25f;

    if (t < -12.0f) return BIOME_ICE;
    if (t < -5.0f)  return BIOME_TUNDRA;
    if (t < 3.0f)   return p > 220.0f ? BIOME_TAIGA : BIOME_STEPPE;
    if (p > 1600.0f && t > 18.0f) return BIOME_RAINFOREST;
    if (p > 800.0f && t > 5.0f)  return BIOME_TEMPERATE_FOREST;
    if (p > 450.0f && t > 18.0f) return BIOME_SAVANNA;
    if (p < 230.0f && t > 16.0f) return BIOME_DESERT;
    if (fabsf(lat) > 25.0f && fabsf(lat) < 48.0f && p >= 230.0f && p < 480.0f)
        return BIOME_MEDITERRANEAN;
    return BIOME_STEPPE;
}

const char *earth_biome_name(Biome b)
{
    switch (b) {
    case BIOME_ICE:              return "ЛЕДНИКИ";
    case BIOME_TUNDRA:           return "ТУНДРА";
    case BIOME_TAIGA:            return "ТАЙГА";
    case BIOME_TEMPERATE_FOREST: return "ЛЕС УМЕРЕННЫЙ";
    case BIOME_STEPPE:           return "СТЕПЬ";
    case BIOME_DESERT:           return "ПУСТЫНЯ";
    case BIOME_SAVANNA:          return "САВАННА";
    case BIOME_MEDITERRANEAN:    return "СРЕДИЗЕМНОМОРЬЕ";
    case BIOME_RAINFOREST:       return "ДЖУНГЛИ";
    default:                     return "ОКЕАН";
    }
}

const char *earth_zone_name(float lat)
{
    float a = fabsf(lat);
    if (a < 10.0f)  return "ЭКВАТОРИАЛЬНЫЙ";
    if (a < 24.0f)  return "ТРОПИЧЕСКИЙ";
    if (a < 34.0f)  return "СУБТРОПИЧЕСКИЙ";
    if (a < 46.0f)  return "УМЕРЕННЫЙ";
    if (a < 66.0f)  return "СУБПОЛЯРНЫЙ";
    return "ПОЛЯРНЫЙ";
}

static void blend3(float *c, const float *a, float t)
{
    c[0] = lerpf(c[0], a[0], t);
    c[1] = lerpf(c[1], a[1], t);
    c[2] = lerpf(c[2], a[2], t);
}

void earth_biome_color(Biome b, float lon, float lat, float elev, float day,
                       float *r, float *g, float *bl)
{
    static const float alb[BIOME_COUNT][3] = {
        { 0.92f, 0.93f, 0.97f }, /* ice    */
        { 0.60f, 0.58f, 0.53f }, /* tundra */
        { 0.21f, 0.33f, 0.18f }, /* taiga  */
        { 0.26f, 0.46f, 0.19f }, /* temper */
        { 0.66f, 0.60f, 0.28f }, /* steppe */
        { 0.83f, 0.73f, 0.46f }, /* desert */
        { 0.67f, 0.61f, 0.24f }, /* savanna*/
        { 0.53f, 0.58f, 0.27f }, /* medit  */
        { 0.15f, 0.38f, 0.11f }, /* jungle */
    };
    static const float snow[3] = { 0.93f, 0.95f, 0.98f };
    static const float brown[3] = { 0.60f, 0.48f, 0.26f };

    float c[3];
    float tex = 0.88f + 0.22f * fbm2(lon * 0.9f + 5.0f, lat * 0.9f - 3.0f, 2);
    c[0] = alb[b][0] * tex;
    c[1] = alb[b][1] * tex;
    c[2] = alb[b][2] * tex;

    /* seasonal vegetation: greener in the warm half of the year */
    float cyc = cosf(TWO_PI * (day - 172.0f) / 365.0f);
    float gseason = lat >= 0.0f ? 0.5f + 0.5f * cyc : 0.5f - 0.5f * cyc;
    if (b == BIOME_TAIGA || b == BIOME_TEMPERATE_FOREST ||
        b == BIOME_STEPPE || b == BIOME_SAVANNA || b == BIOME_MEDITERRANEAN) {
        float green = 0.35f + 0.65f * gseason;
        c[1] *= 1.0f + 0.30f * (green - 0.5f);
        c[0] *= 1.0f + 0.10f * (green - 0.5f);
    }

    /* dry season browning */
    if (b == BIOME_SAVANNA || b == BIOME_STEPPE || b == BIOME_MEDITERRANEAN) {
        float pn = precip_at(lon, lat, elev, day);
        float dry = clampf((180.0f - pn) / 180.0f, 0.0f, 1.0f) * 0.8f;
        blend3(c, brown, dry);
    }

    /* snow cover: by altitude + latitude + season */
    float dryf = 0.0f;
    if (b == BIOME_DESERT || b == BIOME_STEPPE)
        dryf = clampf(1.0f - precip_at(lon, lat, elev, day) / 90.0f, 0.0f, 0.4f);
    float sl = snowline_m(lat);
    float sn = smoothstepf(sl, sl + 350.0f, elev);
    sn = clampf(sn * (1.0f - dryf), 0.0f, 1.0f);
    if (b == BIOME_ICE)
        sn = 1.0f;

    /* polar land always snow-covered in winter */
    if (elev > 40.0f && gseason < 0.45f && fabsf(lat) > 66.0f)
        sn = maxf(sn, 0.55f);

    blend3(c, snow, sn);

    *r = c[0];
    *g = c[1];
    *bl = c[2];
}

/* ------------------------------------------------------------------ */
/* weather                                                              */

void weather_init(void)
{
    int i;
    for (i = 0; i < MAX_STORMS; i++)
        storms[i].active = 0;
    belts_register();
    rivers_register();
}

void weather_update(float dt_days)
{
    static float next_spawn = 2.0f;
    int i, count = 0;

    for (i = 0; i < MAX_STORMS; i++)
        if (storms[i].active)
            count++;

    next_spawn -= dt_days;
    if (next_spawn <= 0.0f && count < MAX_STORMS) {
        int tries;
        for (tries = 0; tries < 8; tries++) {
            float lon = randf() * 360.0f - 180.0f;
            float lat = (randf() < 0.5f ? 1.0f : -1.0f) * (8.0f + randf() * 15.0f);
            if (earth_height(lon, lat) < -300.0f) {
                for (i = 0; i < MAX_STORMS; i++) {
                    if (!storms[i].active)
                        break;
                }
                if (i < MAX_STORMS) {
                    storms[i].active = 1;
                    storms[i].lon = lon;
                    storms[i].lat = lat;
                    storms[i].radius_deg = 3.0f + randf() * 4.0f;
                    storms[i].intensity = 0.35f + randf() * 0.3f;
                    storms[i].category = 1;
                    storms[i].life_day = 4.0f + randf() * 5.0f;
                    storms[i].wind_ms = 34.0f + randf() * 15.0f;
                    storms[i].bearing_deg = 270.0f + (lat >= 0 ? 15.0f : -15.0f);
                }
                break;
            }
        }
        next_spawn = 2.0f + randf() * 3.0f;
    }

    for (i = 0; i < MAX_STORMS; i++) {
        Storm *s = &storms[i];
        if (!s->active)
            continue;

        s->life_day -= dt_days;
        s->lon = wrap_lon(s->lon - 1.2f * dt_days);
        float pole = s->lat >= 0.0f ? 1.0f : -1.0f;
        s->lat += 0.25f * pole * dt_days;

        float phase = sinf((3.0f - s->life_day) * 0.6f);
        if (earth_height(s->lon, s->lat) > 0.0f)
            s->intensity -= 0.5f * dt_days;
        else
            s->intensity = clampf(s->intensity + 0.03f * phase * dt_days, 0.25f, 1.0f);

        s->wind_ms = 33.0f + 58.0f * s->intensity;
        int cat = 1 + (int)(s->intensity * 4.99f);
        if (cat > 5) cat = 5;
        s->category = cat;

        if (s->life_day <= 0.0f || s->intensity < 0.08f)
            s->active = 0;
    }
}

Storm earth_nearest_cyclone(float lon, float lat, float *dist_deg)
{
    Storm best;
    best.active = 0;
    float bd = 1e9f;
    int i;

    for (i = 0; i < MAX_STORMS; i++) {
        Storm *s = &storms[i];
        if (!s->active)
            continue;
        float dx = wrap_lon(s->lon - lon);
        float dy = s->lat - lat;
        float d = sqrtf(dx * dx + dy * dy);
        if (d < bd) {
            bd = d;
            best = *s;
        }
    }
    *dist_deg = bd;
    return best;
}

void game_tick(float real_dt)
{
    float mins = real_dt * GAME_MIN_PER_REAL_SEC;
    world_time.day_f += mins / 1440.0f;

    while (world_time.day_f >= 366.0f) {
        world_time.day_f -= 365.0f;
        world_time.year++;
    }
    if (world_time.day_f < 1.0f)
        world_time.day_f = 1.0f;

    int d = (int)world_time.day_f;
    if (d > 365) d = 365;
    world_time.day = d;

    float hfrac = (world_time.day_f - (float)d) * 24.0f;
    int h = (int)hfrac;
    if (h > 23) h = 23;
    world_time.hour = h;
    world_time.minute = (int)((hfrac - (float)h) * 60.0f);
}

Vec3 earth_sun_dir(float lat)
{
    float dl = 23.44f * DEG * sinf(TWO_PI * (world_time.day_f - 81.0f) / 365.0f);
    float lr = clamp_lat(lat) * DEG;
    float hour = world_time.hour + world_time.minute / 60.0f;
    float hr = (hour - 12.0f) * 15.0f * DEG;

    float sinel = sinf(dl) * sinf(lr) + cosf(dl) * cosf(lr) * cosf(hr);
    float el = asinf(clampf(sinel, -1.0f, 1.0f));
    float cel = cosf(el);
    float cosL = cosf(lr);

    float ca = 1.0f;
    if (cel > 1e-4f && cosL > 1e-4f)
        ca = (sinf(dl) - sinf(el) * sinf(lr)) / (cel * cosL);
    ca = clampf(ca, -1.0f, 1.0f);
    float sa = cel > 1e-4f ? cosf(dl) * sinf(hr) / cel : 0.0f;
    float az = atan2f(sa, ca);

    return v3(cel * sinf(az), sinf(el), cel * cosf(az));
}

void earth_light(float e, float rain,
                 float *ambient, float *diffuse,
                 float *top_r, float *top_g, float *top_b,
                 float *hor_r, float *hor_g, float *hor_b)
{
    float t = smoothstepf(-0.18f, 0.12f, e);
    float tn = smoothstepf(-0.18f, -0.05f, e);

    float dr = 88.0f, dg = 150.0f, db = 230.0f;
    float dh = 190.0f, dhh = 212.0f, dhb = 236.0f;
    float nr = 6.0f, ng = 10.0f, nb = 26.0f;
    float nh = 10.0f, nhh = 16.0f, nhb = 42.0f;
    float tr2 = 36.0f + 52.0f * (1.0f - tn), tg = 30.0f + 30.0f * tn, tb = 44.0f + 70.0f * tn;
    float thr = 250.0f, thg = 150.0f, thb = 60.0f;

    float top_c[3] = { lerpf(nr, tr2, tn), lerpf(ng, tg, tn), lerpf(nb, tb, tn) };
    float hor_c[3] = { lerpf(nh, thr, tn), lerpf(nhh, thg, tn), lerpf(nhb, thb, tn) };

    top_c[0] = lerpf(top_c[0], dr, t);
    top_c[1] = lerpf(top_c[1], dg, t);
    top_c[2] = lerpf(top_c[2], db, t);
    hor_c[0] = lerpf(hor_c[0], dh, t);
    hor_c[1] = lerpf(hor_c[1], dhh, t);
    hor_c[2] = lerpf(hor_c[2], dhb, t);

    *ambient = lerpf(0.10f, 0.42f, t);
    *diffuse = lerpf(0.06f, 0.80f, t);

    if (rain > 0.0f) {
        float g2[3] = { 0.78f, 0.79f, 0.80f };
        top_c[0] = lerpf(top_c[0], g2[0] * 120.0f, rain);
        top_c[1] = lerpf(top_c[1], g2[1] * 120.0f, rain);
        top_c[2] = lerpf(top_c[2], g2[2] * 120.0f, rain);
        hor_c[0] = lerpf(hor_c[0], g2[0] * 130.0f, rain);
        hor_c[1] = lerpf(hor_c[1], g2[1] * 130.0f, rain);
        hor_c[2] = lerpf(hor_c[2], g2[2] * 130.0f, rain);
        *ambient -= 0.12f * rain;
        *diffuse -= 0.35f * rain;
        if (*ambient < 0.05f) *ambient = 0.05f;
        if (*diffuse < 0.05f) *diffuse = 0.05f;
    }

    *top_r = top_c[0];
    *top_g = top_c[1];
    *top_b = top_c[2];
    *hor_r = hor_c[0];
    *hor_g = hor_c[1];
    *hor_b = hor_c[2];
}