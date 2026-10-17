#pragma once

#include "mapgen/MapModel.h"
#include <cstdint>
#include <vector>

namespace sg {

// Procedural texture generator for planets and stars.
// Uses deterministic noise seeded by planet.textureSeed to produce unique
// textures per planet type.  Generates CPU RGBA buffers (no GL dependency)
// which the caller may persist to a PNG disk cache and/or upload via
// textureFromRGBA().  The caller owns the resulting ImageTex.
class PlanetTextureGen {
public:
    // Default resolution of generated textures (square, power of two).
    static constexpr int kTexSize = 512;

    // Bump whenever the generator changes so stale PNG disk caches are
    // invalidated (cache filenames embed this version).
    static constexpr int kGenVersion = 5;

    // Fill `rgba` (w*h*4 bytes) with a sphere albedo map (row 0 = north pole).
    // The sphere fills the square with transparent corners and limbs not baked
    // (lighting is applied by the renderer, not the generator).
    static void generateRGBA(const Planet& pl, int w, int h,
                             std::vector<unsigned char>& rgba);
    static void generateStarRGBA(uint32_t seed, int w, int h,
                                 std::vector<unsigned char>& rgba);

    // --- noise primitives (exposed for persistence/verify) ---
    // Hash-based 2D value noise in [0,1].
    static float hashNoise(uint32_t seed, float x, float y);
    // Fractional Brownian motion (octaves).
    static float fbm(uint32_t seed, float x, float y, int octaves = 4);
    // Horizontally tileable variants: wrap the X lattice so u=0 matches u=1.
    static float hashNoiseP(uint32_t seed, float x, float y, int px, int py);
    static float fbmP(uint32_t seed, float x, float y, int octaves, int period);
};

} // namespace sg