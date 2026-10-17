#include "texturegen/PlanetTextureGen.h"

#include <cmath>
#include <cstring>

namespace sg {

// --- hash & noise primitives -----------------------------------------------

static uint32_t hash(uint32_t x, uint32_t y, uint32_t seed) {
    uint32_t h = x * 374761393u + y * 668265263u + seed * 1274126177u;
    h = (h ^ (h >> 13)) * 1274126177u;
    h = h ^ (h >> 16);
    return h;
}

float PlanetTextureGen::hashNoise(uint32_t seed, float x, float y) {
    int ix = (int)std::floor(x);
    int iy = (int)std::floor(y);
    float fx = x - (float)ix;
    float fy = y - (float)iy;
    // smoothstep interpolation
    fx = fx * fx * (3.f - 2.f * fx);
    fy = fy * fy * (3.f - 2.f * fy);

    float v00 = (float)(hash(ix,     iy,     seed) & 0xFFFFu) / 65535.f;
    float v10 = (float)(hash(ix + 1, iy,     seed) & 0xFFFFu) / 65535.f;
    float v01 = (float)(hash(ix,     iy + 1, seed) & 0xFFFFu) / 65535.f;
    float v11 = (float)(hash(ix + 1, iy + 1, seed) & 0xFFFFu) / 65535.f;

    return v00 * (1-fx) * (1-fy)
         + v10 * fx     * (1-fy)
         + v01 * (1-fx) * fy
         + v11 * fx     * fy;
}

float PlanetTextureGen::fbm(uint32_t seed, float x, float y, int octaves) {
    float val = 0.f, amp = 0.5f, freq = 1.f;
    for (int i = 0; i < octaves; ++i) {
        val += amp * hashNoise(seed + i * 137u, x * freq, y * freq);
        amp *= 0.5f;
        freq *= 2.f;
    }
    return val; // roughly [0, 1]
}

// Hash noise whose lattice wraps around the x-axis (mod `px` cells) so the
// resulting texture is horizontally tileable (u=0 matches u=1), making the
// sphere mapping seamless. `py` may wrap the y-axis too (kept 0 here).
float PlanetTextureGen::hashNoiseP(uint32_t seed, float x, float y, int px, int py) {
    if (px > 0) x = x - std::floor(x / (float)px) * (float)px;
    if (py > 0) y = y - std::floor(y / (float)py) * (float)py;
    int ix = (int)std::floor(x);
    int iy = (int)std::floor(y);
    float fx = x - (float)ix;
    float fy = y - (float)iy;
    fx = fx * fx * (3.f - 2.f * fx);
    fy = fy * fy * (3.f - 2.f * fy);

    int ix1 = (px > 0 && ix + 1 >= px) ? 0 : ix + 1;
    int iy1 = (py > 0 && iy + 1 >= py) ? 0 : iy + 1;

    float v00 = (float)(hash(ix,  iy,  seed) & 0xFFFFu) / 65535.f;
    float v10 = (float)(hash(ix1, iy,  seed) & 0xFFFFu) / 65535.f;
    float v01 = (float)(hash(ix,  iy1, seed) & 0xFFFFu) / 65535.f;
    float v11 = (float)(hash(ix1, iy1, seed) & 0xFFFFu) / 65535.f;

    return v00 * (1-fx) * (1-fy)
         + v10 * fx     * (1-fy)
         + v01 * (1-fx) * fy
         + v11 * fx     * fy;
}

// fbm with horizontal period: the noise lattice wraps every `period` x-cells,
// and each octave wraps its own (doubling) cell grid. Call with the texture's
// horizontal coordinate measured in "per-turn-of-the-planet" multiples, e.g.
// fbmP(seed, u*3, v*3, 5, 3) for a pattern repeating 3x around the sphere.
float PlanetTextureGen::fbmP(uint32_t seed, float x, float y, int octaves, int period) {
    float val = 0.f, amp = 0.5f;
    for (int i = 0; i < octaves; ++i) {
        val += amp * PlanetTextureGen::hashNoiseP(seed + i * 137u, x, y, period * (1 << i), 0);
        amp *= 0.5f;
        x *= 2.f;
        y *= 2.f;
    }
    return val;
}

// --- helpers ----------------------------------------------------------------

static uint8_t clamp255(float v) {
    return (uint8_t)(v < 0.f ? 0 : v > 255.f ? 255 : v);
}

static float lerp(float a, float b, float t) { return a + (b - a) * t; }

// Domain warping: offsets the sample coordinates with low-freq noise so
// features (coasts, dunes, cloud bands) become organic instead of blocky.
static void warpCoords(uint32_t seed, float u, float v,
                       float& ow, float& ph) {
    float wx = PlanetTextureGen::fbmP(seed, u * 2.f, v * 2.f, 3, 2);
    float wy = PlanetTextureGen::fbmP(seed + 17u, v * 2.f, u * 2.f + 4.4f, 3, 2);
    ph = (wx - 0.5f) * 0.22f;
    ow = (wy - 0.5f) * 0.22f;
}

struct Pixel { uint8_t r, g, b, a; };
static Pixel mix(Pixel a, Pixel b, float t) {
    t = t < 0.f ? 0.f : t > 1.f ? 1.f : t;
    return { (uint8_t)lerp(a.r, b.r, t), (uint8_t)lerp(a.g, b.g, t),
             (uint8_t)lerp(a.b, b.b, t), 255 };
}

// Spherical albedo: maps our square texture onto a sphere.  We keep the whole
// square opaque (alpha 255); the spherical mask + lighting live in the renderer.
// Row 0 = north pole (v clamp handles the top seam).

// --- surface detail ramps ---------------------------------------------------
// Each returns the albedo pixel for fbm sample `t` at latitude `lat` (0=equator,
// 1=pole) with domain-warped coordinates available as `u`,`v` (0..1).

// Continental: ocean depth, coast lines, forests, mountains, polar ice.
static Pixel continental(uint32_t seed, float u, float v, float t, float lat) {
    // warp for organic continents
    float ow, ph;
    warpCoords(seed, u, v, ow, ph);
    float tu = u * 3.f + ow;
    float tv = v * 3.f + ph;
    float terr = PlanetTextureGen::fbmP(seed ^ 0x5F3759DFu, tu, tv, 5, 3);
    // raised continents (more distinct from ocean than plain threshold)
    terr = (terr - 0.5f) * 1.6f + 0.5f;
    terr = terr < 0.f ? 0.f : terr > 1.f ? 1.f : terr;

    // pole ice: coverage grows toward the poles
    float pole = lat * lat;                       // 0 equator..1 pole
    float iceCov = pole * 0.95f + (t - 0.55f) * 0.25f;
    iceCov = iceCov < 0.f ? 0.f : iceCov > 1.f ? 1.f : iceCov;

    // mountains via high-frequency ridge
    float ridge = PlanetTextureGen::fbmP(seed + 51u, tu * 8.f, tv * 8.f, 4, 24);
    ridge = (ridge - 0.5f) * 2.f;                 // -1..1

    if (iceCov > 0.65f && terr < 0.55f) {
        // polar cap (white with faint shading)
        Pixel w = { (uint8_t)lerp(225.f, 245.f, t),
                    (uint8_t)lerp(225.f, 245.f, t),
                    (uint8_t)lerp(235.f, 250.f, t), 255 };
        return mix(w, { 210, 215, 228, 255 }, ridge * 0.15f + 0.2f);
    }
    if (terr < 0.44f) {
        // ocean: depth shading + coastal shallow rim
        float d = t * 0.6f + terr * 0.4f;
        if (terr > 0.40f) {
            // shallow coastal water
            return { clamp255(lerp(40.f, 60.f, d)), clamp255(lerp(120.f, 150.f, d)),
                     clamp255(lerp(180.f, 210.f, d)), 255 };
        }
        return { clamp255(lerp(15.f, 30.f, d)), clamp255(lerp(45.f, 80.f, d)),
                 clamp255(lerp(110.f, 150.f, d)), 255 };
    }
    if (terr < 0.60f) {
        // lowlands: vegetation (grass / meadows), greener in warmer bands
        float veg = t * 0.5f + (1.f - lat) * 0.5f;
        return { clamp255(lerp(55.f, 90.f, veg)), clamp255(lerp(130.f, 175.f, veg)),
                 clamp255(lerp(45.f, 70.f, veg)), 255 };
    }
    if (terr < 0.70f) {
        // highlands / hills
        return { clamp255(lerp(95.f, 120.f, t)), clamp255(lerp(130.f, 150.f, t)),
                 clamp255(lerp(80.f, 95.f, t)), 255 };
    }
    // mountains: rock with snow on the highest peaks (ridge high)
    float snow = ridge > 0.35f ? (ridge - 0.35f) * 2.f : 0.f;
    Pixel rock = { clamp255(lerp(105.f, 160.f, t)), clamp255(lerp(98.f, 150.f, t)),
                   clamp255(lerp(88.f, 135.f, t)), 255 };
    return mix(rock, { 240, 243, 250, 255 }, snow);
}

// Desert: sand dunes, darker big craters, warmer regolith streaks.
static Pixel desert(uint32_t seed, float u, float v, float t, float lat) {
    float ow, ph;
    warpCoords(seed, u, v, ow, ph);
    float tu = u * 4.f + ow * 2.f;
    float tv = v * 4.f + ph * 2.f;
    float dune = PlanetTextureGen::fbmP(seed ^ 0xC0FFEEu, tu, tv, 5, 4);
    // banding across dunes (period-4 coordinate keeps the wrap seamless)
    float band = std::sin(tu * 11.f + dune * 2.5f) * 0.5f + 0.5f;

    // big impact craters: soft dark circles placed by hash of seed grid
    float craters = 0.f;
    {
        const float gs = 6.f;
        int cx0 = (int)(u * gs), cy0 = (int)(v * gs);
        for (int gx = cx0 - 1; gx <= cx0 + 1; ++gx)
        for (int gy = cy0 - 1; gy <= cy0 + 1; ++gy) {
            float hx = gx + PlanetTextureGen::hashNoiseP(seed, (float)gx, (float)gy, 6, 0);
            float hy = gy + PlanetTextureGen::hashNoiseP(seed, (float)gx, (float)gy + 7.3f, 6, 0);
            float dx = (u * gs - hx), dy = (v * gs - hy);
            float dd = std::sqrt(dx*dx + dy*dy);
            float cr = 0.18f + 0.22f * PlanetTextureGen::hashNoiseP(seed, (float)gx + 3.f, (float)gy + 9.f, 6, 0);
            if (dd < cr) craters = std::max(craters, 1.f - dd / cr);
        }
    }

    // base sand palette: hotter/darker regolith near equator
    float warm = (1.f - lat) * 0.5f;
    Pixel sand = { clamp255(lerp(170.f, 225.f, band * 0.5f + t * 0.3f + warm)),
                   clamp255(lerp(135.f, 185.f, band * 0.5f + t * 0.3f + warm)),
                   clamp255(lerp(80.f, 115.f, band * 0.3f + t * 0.2f)), 255 };
    // craters: cooler, darker interior with a lighter raised rim
    Pixel crater = mix({ 150, 120, 80, 255 }, { 110, 88, 58, 255 }, t);
    return mix(sand, crater, craters);
}

// Ice: blue-white surface, darker fracture lines, albedo variation.
static Pixel ice(uint32_t seed, float u, float v, float t, float lat) {
    float ow, ph;
    warpCoords(seed, u, v, ow, ph);
    float tu = u * 5.f + ow * 2.f;
    float tv = v * 5.f + ph * 2.f;
    float n = PlanetTextureGen::fbmP(seed ^ 0x1CEu, tu, tv, 5, 5);
    // fractures: thin dark lines from high-freq noise threshold
    float crack = PlanetTextureGen::fbmP(seed ^ 0xFAC1, tu * 3.f, tv * 3.f, 4, 15);
    float line = (crack - 0.55f) * 6.f;           // sharp line boost
    line = line < 0.f ? 0.f : line;

    float base = lerp(200.f, 240.f, n);
    float r = clamp255(base + line * 40.f);        // highlight
    float g = clamp255(lerp(215.f, 245.f, n) + line * 20.f);
    float b = clamp255(lerp(225.f, 255.f, n));
    // polar shading darkest at equator? Ice is colder at poles - keep slight tint
    Pixel p = { r, g, b, 255 };
    // bigger multi-hued cracks / ridges
    float ridge = (PlanetTextureGen::fbmP(seed ^ 0xBEEF, tu * 2.f, tv * 2.f, 4, 10) - 0.5f);
    return mix(p, { clamp255(120.f + ridge*80.f), clamp255(140.f + ridge*80.f),
                    clamp255(170.f + ridge*60.f), 255 }, (crack - 0.45f) * 1.5f > 0.f ?
                         (crack - 0.45f) * 1.5f : 0.f);
}

// Gas giant: strong horizontal banding sheared by jets, with advected storms.
static Pixel gasGiant(uint32_t seed, float u, float v, float t, float lat) {
    // storm vortices: a handful of cyclones scattered by the seed.
    // Longitude distances are wrapped to [-0.5,0.5] so storms crossing the
    // u=0/u=1 seam behave like they do anywhere else on the sphere.
    float d0[6], d1[6];
    for (int k = 0; k < 6; ++k) {
        d0[k] = PlanetTextureGen::hashNoise(seed + k * 37u, 1.f, 1.f);
        d1[k] = PlanetTextureGen::hashNoise(seed + k * 37u, 2.f, 2.f);
    }
    float du = u, dv = v;
    {
        for (int k = 0; k < 6; ++k) {
            float scx = d0[k], scy = d1[k];
            float srad = 0.05f + 0.09f * PlanetTextureGen::hashNoise(seed + k * 37u, 3.f, 3.f);
            float str = -0.6f + 1.2f * PlanetTextureGen::hashNoise(seed + k * 37u, 4.f, 4.f);
            float dx = u - scx;
            if (dx >  0.5f) dx -= 1.f;      // wrap shortest way around
            else if (dx < -0.5f) dx += 1.f;
            float dy = (v - scy) * 1.4f;    // stretch into ellipses
            float dd = std::sqrt(dx*dx + dy*dy);
            float swirl = (srad > 0.f) ? str * (1.f - dd / srad) : 0.f;
            if (swirl > 0.f) {
                float cs = std::cos(swirl * 3.5f), sn = std::sin(swirl * 3.5f);
                float ru = dx * cs - dy * sn + scx;
                float rv = dx * sn + dy * cs + scy;
                if (ru < 0.f) ru += 1.f; else if (ru > 1.f) ru -= 1.f;  // wrap
                du = ru; dv = rv;
                break;
            }
        }
    }

    // jet shear: horizontal bands shift in longitude (u) by latitude-shaped sign
    float shear = std::sin(dv * 6.2831853f) * 0.35f
                + std::sin(dv * 12.566f + 1.3f) * 0.12f;
    du += shear;

    float ow, ph;
    warpCoords(seed, du, dv, ow, ph);
    float tu = du * 4.f + ow * 2.f;
    float tv = dv * 6.f + ph * 2.f;
    float b = PlanetTextureGen::fbmP(seed ^ 0xC001D, tu, tv, 5, 4);
    float band = std::sin(dv * 20.f + b * 4.f) * 0.5f + 0.5f;

    // band palette alternates pale/rich; storms get a cream brightening
    float storm = 0.f;
    for (int k = 0; k < 6; ++k) {
        float dx = u - d0[k];
        if (dx >  0.5f) dx -= 1.f; else if (dx < -0.5f) dx += 1.f;
        float dy = (v - d1[k]) * 1.4f;
        float dd = std::sqrt(dx*dx + dy*dy);
        if (dd < 0.10f) storm = std::max(storm, 1.f - dd / 0.10f);
    }

    float c1 = band * 0.6f + t * 0.25f + b * 0.15f;   // 0..1
    Pixel p1 = { clamp255(lerp(150.f, 205.f, c1)), clamp255(lerp(95.f, 155.f, c1)),
                 clamp255(lerp(175.f, 215.f, c1)), 255 };
    float c2 = (1.f - band) * 0.6f + t * 0.25f + b * 0.15f;
    Pixel p2 = { clamp255(lerp(120.f, 185.f, c2)), clamp255(lerp(75.f, 130.f, c2)),
                 clamp255(lerp(140.f, 195.f, c2)), 255 };
    // blend then brighten storm core
    Pixel bg = mix(p1, p2, std::fabs(band * 2.f - 1.f) > 0.5f ? 0.5f : 0.2f);
    Pixel stormPx = { clamp255(225.f + storm * 30.f), clamp255(195.f + storm * 30.f),
                      clamp255(200.f + storm * 20.f), 255 };
    return mix(bg, stormPx, storm * 0.85f);
}

// Asteroid: grey rocky surface with darker craters and bright speckles.
static Pixel asteroid(uint32_t seed, float u, float v, float t, float base) {
    float n = PlanetTextureGen::fbmP(seed ^ 0x5057, u * 6.f, v * 6.f, 4, 6);
    float speck = (PlanetTextureGen::fbmP(seed ^ 0x7EED, u * 12.f, v * 12.f, 3, 12) - 0.5f);
    float g = base + (n - 0.5f) * 0.8f;
    // craters punch small dark spots
    float bone = 0.f;
    {
        const float gs = 10.f;
        for (int gx = (int)(u*gs)-1; gx <= (int)(u*gs)+1; ++gx)
        for (int gy = (int)(v*gs)-1; gy <= (int)(v*gs)+1; ++gy) {
            float hx = gx + PlanetTextureGen::hashNoiseP(seed, (float)gx, (float)gy, 10, 0);
            float hy = gy + PlanetTextureGen::hashNoiseP(seed, (float)gx, (float)gy+5.f, 10, 0);
            float dx = u*gs - hx, dy = v*gs - hy;
            float dd = std::sqrt(dx*dx+dy*dy);
            float cr = 0.08f;
            if (dd < cr) bone = std::max(bone, 1.f - dd / cr);
        }
    }
    float lr = clamp255(lerp(90.f, 160.f, g) - bone * 45.f);
    float lg = clamp255(lerp(82.f, 150.f, g) - bone * 40.f);
    float lb = clamp255(lerp(75.f, 140.f, g) - bone * 35.f);
    if (speck > 0.25f) {
        lr = clamp255(lr + speck * 40.f);
        lg = clamp255(lg + speck * 40.f);
        lb = clamp255(lb + speck * 40.f);
    }
    return { lr, lg, lb, 255 };
}

// Star: bright yellow-white core, granulation, edge darkening, hot corona.
// `mu` is the cosine of the angle between the surface normal and the view
// direction (1 = centre of disc), used for limb darkening on the sphere.
static Pixel star(uint32_t seed, float u, float v, float t, float mu) {
    // granulation: convective cells (period-12 lattice wraps longitude)
    float gran = PlanetTextureGen::fbmP(seed, u * 12.f, v * 12.f, 4, 12);
    gran = (gran - 0.5f) * 1.6f + 0.5f;
    // sunspots: darker patches from clustered noise (wrapped 7x7 grid)
    float spots = 0.f;
    {
        const float gs = 7.f;
        for (int gx = (int)(u*gs)-1; gx <= (int)(u*gs)+1; ++gx)
        for (int gy = (int)(v*gs)-1; gy <= (int)(v*gs)+1; ++gy) {
            float hx = gx + PlanetTextureGen::hashNoiseP(seed+1u, (float)gx, (float)gy, 7, 0);
            float hy = gy + PlanetTextureGen::hashNoiseP(seed+2u, (float)gx, (float)gy+3.f, 7, 0);
            float dx = u*gs - hx, dy = v*gs - hy;
            float dd = std::sqrt(dx*dx+dy*dy);
            float cr = 0.06f + 0.05f * PlanetTextureGen::hashNoiseP(seed+3u, (float)gx, (float)gy, 7, 0);
            if (dd < cr) spots = std::max(spots, 1.f - dd / cr);
        }
    }
    // limb darkening: intensity falls toward the silhouette (mu -> 0).
    // A small floor keeps the rim from going fully black (corona glow adds on top).
    float mu2 = mu > 0.f ? mu : 0.f;
    float limb = 0.12f + 0.88f * std::pow(mu2, 0.55f);
    float b = (t * 0.45f + gran * 0.45f + 0.1f) * limb;
    // colour: white hot core -> yellow -> orange -> faint red edge
    float r = clamp255((210.f + gran * 45.f) * b);
    float g = clamp255((170.f + gran * 30.f) * b);
    float bl = clamp255((70.f + gran * 20.f) * b);
    if (spots > 0.55f) {
        r = clamp255(r * 0.55f);
        g = clamp255(g * 0.6f);
        bl = clamp255(bl * 0.7f);
    }
    return { r, g, bl, 255 };
}

// --- public generators ------------------------------------------------------

void PlanetTextureGen::generateRGBA(const Planet& planet, int w, int h,
                                    std::vector<unsigned char>& rgba) {
    const uint32_t seed = planet.textureSeed ? planet.textureSeed : 0xDEADBEEFu;
    rgba.assign((size_t)w * h * 4, 0);

    for (int y = 0; y < h; ++y) {
        const float v = ((float)y + 0.5f) / (float)h;   // 0..1 (north->south)
        const float lat = std::fabs(2.f * v - 1.f);     // 0 equator..1 pole
        for (int x = 0; x < w; ++x) {
            const float u = ((float)x + 0.5f) / (float)w;
            // longitude-wrapped texture: sample warped coords
            Pixel px{ 165, 165, 165, 255 };
            switch (planet.type) {
                case PlanetType::Continental:
                    px = continental(seed, u, v, PlanetTextureGen::fbmP(seed, u*2.f, v*2.f, 4, 2), lat);
                    break;
                case PlanetType::Desert:
                    px = desert(seed, u, v, PlanetTextureGen::fbmP(seed, u*2.f, v*2.f, 4, 2), lat);
                    break;
                case PlanetType::Ice:
                    px = ice(seed, u, v, PlanetTextureGen::fbmP(seed, u*2.f, v*2.f, 4, 2), lat);
                    break;
                case PlanetType::GasGiant:
                    px = gasGiant(seed, u, v, PlanetTextureGen::fbmP(seed, u*2.f, v*2.f, 4, 2), lat);
                    break;
                case PlanetType::AsteroidBelt:
                    px = asteroid(seed, u, v, PlanetTextureGen::fbmP(seed, u*2.f, v*2.f, 4, 2),
                                  PlanetTextureGen::hashNoiseP(seed, u*8.f, v*8.f, 8, 0));
                    break;
                default:
                    px = { 165, 165, 165, 255 };
                    break;
            }
            rgba[(size_t)(y * w + x) * 4 + 0] = px.r;
            rgba[(size_t)(y * w + x) * 4 + 1] = px.g;
            rgba[(size_t)(y * w + x) * 4 + 2] = px.b;
            rgba[(size_t)(y * w + x) * 4 + 3] = 255;   // opaque full square
        }
    }
}

void PlanetTextureGen::generateStarRGBA(uint32_t seed, int w, int h,
                                        std::vector<unsigned char>& rgba) {
    rgba.assign((size_t)w * h * 4, 0);
    for (int y = 0; y < h; ++y) {
        const float v = ((float)y + 0.5f) / (float)h;   // 0..1 (north->south)
        const float th = 3.14159265f * v;               // polar angle 0..pi
        const float mu = std::cos(th);                  // pole faces the camera (+Z)
        for (int x = 0; x < w; ++x) {
            const float u = ((float)x + 0.5f) / (float)w;
            // Texture generated with the pole (+Y) as view axis (+Z): the renderer
            // aligns +Y to +Z and spins only around that axis, so mu here equals
            // the normal's z the renderer sees — limb darkening stays glued to the
            // silhouette while the granulation spins around the line of sight.
            Pixel p = star(seed, u, v, PlanetTextureGen::fbmP(seed, u*3.f, v*3.f, 4, 3), mu);
            rgba[(size_t)(y * w + x) * 4 + 0] = p.r;
            rgba[(size_t)(y * w + x) * 4 + 1] = p.g;
            rgba[(size_t)(y * w + x) * 4 + 2] = p.b;
            rgba[(size_t)(y * w + x) * 4 + 3] = 255;    // opaque full square
        }
    }
}

} // namespace sg