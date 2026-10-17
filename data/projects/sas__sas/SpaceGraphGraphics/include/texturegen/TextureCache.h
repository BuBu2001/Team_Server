#pragma once

#include "mapgen/MapModel.h"
#include "renderer/Renderer.h"
#include <map>
#include <vector>

namespace sg {

// Manages generated planet textures for the current system view.
// Textures are generated on first access and cached in memory.
// Only one system's textures are held at a time (the viewed system).
class TextureCache {
public:
    // Ensure textures exist for the given system.  Generates missing ones.
    // Caller must pass the Renderer to create GL textures.
    void ensureSystemTextures(Renderer& renderer, const System& sys);

    // Get the texture for a planet index in the current cached system.
    // Returns nullptr if not yet generated.
    ImageTex* getPlanetTexture(int planetIndex);

    // Get the star texture for the current cached system.
    ImageTex* getStarTexture();

    // Clear all cached textures (free GL resources).
    void clear(Renderer& renderer);

    // True if textures are cached for the given system id.
    bool hasSystem(Id sysId) const;

private:
    Id cachedSystemId_ = static_cast<Id>(-1);
    ImageTex starTex_;
    std::vector<ImageTex> planetTex_;
};

} // namespace sg
