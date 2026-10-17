#include "texturegen/TextureCache.h"
#include "texturegen/PlanetTextureGen.h"

#include <cstdio>
#include <string>

// C++17 filesystem for cache directory management
#include <filesystem>
namespace fs = std::filesystem;

namespace sg {

static const char* CACHE_DIR = "texture_cache";

// Ensure the cache directory exists (called once).
static void ensureCacheDir() {
    static bool created = false;
    if (created) return;
    created = true;
    std::error_code ec;
    fs::create_directories(CACHE_DIR, ec);
    if (ec) std::fprintf(stderr, "[texture cache] could not create %s: %s\n",
                         CACHE_DIR, ec.message().c_str());
}

// Build a cache file path for a planet texture.
static std::string planetPath(const Planet& p) {
    char buf[128];
    std::snprintf(buf, sizeof(buf), "%s/planet_V%d_T%u_%08X.png",
                  CACHE_DIR, PlanetTextureGen::kGenVersion,
                  (unsigned)p.type, p.textureSeed);
    return buf;
}

// Build a cache file path for a star texture.
static std::string starPath(uint32_t seed) {
    char buf[128];
    std::snprintf(buf, sizeof(buf), "%s/star_V%d_%08X.png",
                  CACHE_DIR, PlanetTextureGen::kGenVersion, seed);
    return buf;
}

// Try to load from disk; on miss, call the generator to produce RGBA,
// save a PNG cache file, and upload to GL.  Returns the GL texture handle.
static ImageTex loadOrGenerate(Renderer& renderer,
                               const std::string& path,
                               bool isPlanet,
                               const Planet& pl,
                               uint32_t starSeed,
                               int texSize) {
    ensureCacheDir();

    // 1. Try disk cache
    {
        ImageTex cached = renderer.loadImage(path.c_str());
        if (cached.ok) return cached;
    }

    // 2. Generate RGBA (CPU-only)
    std::vector<unsigned char> rgba;
    if (isPlanet) {
        PlanetTextureGen::generateRGBA(pl, texSize, texSize, rgba);
    } else {
        PlanetTextureGen::generateStarRGBA(starSeed, texSize, texSize, rgba);
    }

    // 3. Write disk cache (ignore failure — non-critical)
    savePNG(path.c_str(), texSize, texSize, rgba.data());

    // 4. Upload to GL
    return textureFromRGBA(texSize, texSize, rgba);
}

// ---- TextureCache public API -----------------------------------------------

void TextureCache::ensureSystemTextures(Renderer& renderer, const System& sys) {
    if (hasSystem(sys.id)) return;

    clear(renderer);
    cachedSystemId_ = sys.id;

    const int sz = PlanetTextureGen::kTexSize;

    // Star: seed derived from system id
    uint32_t starSeed = (uint32_t)sys.id * 2654435761u + 0x9E3779B9u;
    starTex_ = loadOrGenerate(renderer, starPath(starSeed),
                              false, Planet{}, starSeed, sz);

    // Planets
    planetTex_.resize(sys.planets.size());
    for (size_t i = 0; i < sys.planets.size(); ++i) {
        const Planet& p = sys.planets[i];
        planetTex_[i] = loadOrGenerate(renderer, planetPath(p),
                                       true, p, 0, sz);
    }
}

ImageTex* TextureCache::getPlanetTexture(int planetIndex) {
    if (planetIndex < 0 || planetIndex >= (int)planetTex_.size()) return nullptr;
    if (!planetTex_[planetIndex].ok) return nullptr;
    return &planetTex_[planetIndex];
}

ImageTex* TextureCache::getStarTexture() {
    if (!starTex_.ok) return nullptr;
    return &starTex_;
}

void TextureCache::clear(Renderer& renderer) {
    if (starTex_.ok) {
        renderer.destroyImage(starTex_);
        starTex_ = {};
    }
    for (auto& t : planetTex_) {
        if (t.ok) renderer.destroyImage(t);
    }
    planetTex_.clear();
    cachedSystemId_ = static_cast<Id>(-1);
}

bool TextureCache::hasSystem(Id sysId) const {
    return cachedSystemId_ == sysId;
}

} // namespace sg