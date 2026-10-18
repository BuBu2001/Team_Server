#include "noise.h"
#include "gmath.h"

static int perm[512];

void noise_init(unsigned seed)
{
    int i;
    int p[256];

    for (i = 0; i < 256; i++)
        p[i] = i;

    for (i = 255; i > 0; i--) {
        seed = seed * 1664525u + 1013904223u;
        int j = (int)(seed % (unsigned)(i + 1));
        int t = p[i];
        p[i] = p[j];
        p[j] = t;
    }

    for (i = 0; i < 512; i++)
        perm[i] = p[i & 255];
}

static float fade(float t)
{
    return t * t * t * (t * (t * 6.0f - 15.0f) + 10.0f);
}

static float grad(int hash, float x, float y)
{
    switch (hash & 3) {
    case 0:  return  x + y;
    case 1:  return -x + y;
    case 2:  return  x - y;
    default: return -x - y;
    }
}

float perlin2(float x, float y)
{
    float fx = floorf(x);
    float fy = floorf(y);
    int X = ((int)fx) & 255;
    int Y = ((int)fy) & 255;

    float xf = x - fx;
    float yf = y - fy;

    float u = fade(xf);
    float v = fade(yf);

    int aa = perm[perm[X] + Y];
    int ba = perm[perm[X + 1] + Y];
    int ab = perm[perm[X] + Y + 1];
    int bb = perm[perm[X + 1] + Y + 1];

    float x1 = lerpf(grad(aa, xf, yf),       grad(ba, xf - 1.0f, yf),       u);
    float x2 = lerpf(grad(ab, xf, yf - 1.0f), grad(bb, xf - 1.0f, yf - 1.0f), u);

    return lerpf(x1, x2, v);
}

float fbm2(float x, float y, int octaves)
{
    float sum = 0.0f;
    float amp = 0.5f;
    float tot = 0.0f;
    int i;

    for (i = 0; i < octaves; i++) {
        sum += amp * perlin2(x, y);
        tot += amp;
        amp *= 0.5f;
        x *= 2.0f;
        y *= 2.0f;
    }

    return sum / tot;
}
