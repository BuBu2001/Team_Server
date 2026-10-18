#ifndef GMATH_H
#define GMATH_H

#include <math.h>

#define PI 3.14159265358979323846f

typedef struct {
    float x, y, z;
} Vec3;

static inline Vec3 v3(float x, float y, float z)
{
    Vec3 r = { x, y, z };
    return r;
}

static inline Vec3 v3add(Vec3 a, Vec3 b)
{
    return v3(a.x + b.x, a.y + b.y, a.z + b.z);
}

static inline Vec3 v3sub(Vec3 a, Vec3 b)
{
    return v3(a.x - b.x, a.y - b.y, a.z - b.z);
}

static inline Vec3 v3scale(Vec3 a, float s)
{
    return v3(a.x * s, a.y * s, a.z * s);
}

static inline float v3dot(Vec3 a, Vec3 b)
{
    return a.x * b.x + a.y * b.y + a.z * b.z;
}

static inline Vec3 v3cross(Vec3 a, Vec3 b)
{
    return v3(a.y * b.z - a.z * b.y,
              a.z * b.x - a.x * b.z,
              a.x * b.y - a.y * b.x);
}

static inline float v3len(Vec3 a)
{
    return sqrtf(v3dot(a, a));
}

static inline Vec3 v3norm(Vec3 a)
{
    float l = v3len(a);
    if (l < 1e-8f)
        return v3(0, 0, 0);
    return v3scale(a, 1.0f / l);
}

static inline float clampf(float x, float a, float b)
{
    if (x < a) return a;
    if (x > b) return b;
    return x;
}

static inline float lerpf(float a, float b, float t)
{
    return a + (b - a) * t;
}

static inline float smoothstepf(float e0, float e1, float x)
{
    float t = clampf((x - e0) / (e1 - e0), 0.0f, 1.0f);
    return t * t * (3.0f - 2.0f * t);
}

static inline float minf(float a, float b) { return a < b ? a : b; }
static inline float maxf(float a, float b) { return a > b ? a : b; }

#endif
