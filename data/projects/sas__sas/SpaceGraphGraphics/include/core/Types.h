#pragma once

#include <cstdint>
#include <cmath>

namespace sg {

using Id = uint32_t;

struct Vec2 {
    float x = 0.f;
    float y = 0.f;
};

inline Vec2 operator+(const Vec2& a, const Vec2& b) { return {a.x+b.x, a.y+b.y}; }
inline Vec2 operator-(const Vec2& a, const Vec2& b) { return {a.x-b.x, a.y-b.y}; }
inline Vec2 operator*(const Vec2& a, float s) { return {a.x*s, a.y*s}; }
inline Vec2 operator*(float s, const Vec2& a) { return {a.x*s, a.y*s}; }
inline float dot(const Vec2& a, const Vec2& b) { return a.x*b.x + a.y*b.y; }
inline float cross(const Vec2& a, const Vec2& b) { return a.x*b.y - a.y*b.x; }
inline float len(const Vec2& a) { return std::sqrt(dot(a,a)); }

} // namespace sg
