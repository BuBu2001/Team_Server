#pragma once

#include "mapgen/MapModel.h"
#include "fleet/Fleet.h"
#include "core/Types.h"
#include "renderer/FontRenderer.h"

#include <map>
#include <vector>

namespace sg {

// Camera: world-space centre + zoom. Rendering sets up an orthographic
// projection accordingly; UI drawing resets to screen space.
class Camera {
public:
    Vec2 centre{800, 500};
    float zoom = 1.f;
    void reset() { centre = {800, 500}; zoom = 1.f; }
    Vec2 screenToWorld(float sx, float sy, float winW, float winH) const {
        // OpenGL world: y grows upward. SDL mouse: y grows downward.
        // Flip sy before converting.
        float wy = winH - sy;
        return { centre.x + (sx - winW / 2.f) / zoom,
                 centre.y + (wy - winH / 2.f) / zoom };
    }
};

// A GPU texture holding a loaded image (PNG background etc.).
struct ImageTex {
    unsigned tex = 0;   // OpenGL texture id (0 = invalid)
    int w = 0, h = 0;   // pixel dimensions
    bool ok = false;
};

// Draws world primitives (map, convoys). Camera projectable.
class Renderer {
public:
    // Must be called inside an active GL context.
    void beginWorld(const Camera& cam, float winW, float winH);
    void endWorld();
    void beginScreen(float winW, float winH);
    void endScreen();

    void drawBackground();
    void drawEdge(const Vec2& a, const Vec2& b, bool highlighted);
    // Dashed line between a and b.
    void drawEdgeDashed(const Vec2& a, const Vec2& b, bool highlighted);
    void drawSystem(const System& s);
    void drawConvoy(const Convoy& c, const Vec2& pos, float zoom, bool selected = false);
    void drawCircle(const Vec2& c, float r, const Color& fill);
    void drawCircle(const Vec2& c, float r, const Color& fill, const Color& outline, float outlineW = 1.f);
    // Batched points in one GL pass (for large dot clouds, e.g. 100k systems).
    void drawPoints(const std::vector<Vec2>& pts, const Color& col, float size = 2.f);
    // Batched line segments in one GL pass (pts is a flat a0,b0,a1,b1 list).
    void drawLines(const std::vector<Vec2>& pts, const Color& col, float width = 1.f);
    void drawRect(float x, float y, float w, float h, const Color& fill);
    void drawRect(float x, float y, float w, float h, const Color& fill, const Color& outline, float outlineW = 1.f);
    void drawLine(const Vec2& a, const Vec2& b, const Color& col, float width = 1.f);
    void drawTriangle(const Vec2& a, const Vec2& b, const Vec2& c, const Color& fill);
    void drawPolygon(const std::vector<Vec2>& pts, const Color& fill);
    void drawPolyline(const std::vector<Vec2>& pts, const Color& col, float width = 1.f, bool closed = true);

    // Texture-backed image (e.g. the main menu background). Loaded via libpng
    // into an OpenGL RGBA texture; freed on destroyImage().
    ImageTex loadImage(const char* path);
    void destroyImage(const ImageTex& t);
    // Draw the whole texture stretched into (x,y,w,h) in screen coords.
    void drawImage(const ImageTex& t, float x, float y, float w, float h);
    // Draw a sub-region of the texture (uv in 0..1) onto (x,y,w,h). Used for
    // cover-cropping so the background keeps its aspect ratio (no stretching).
    void drawImageUV(const ImageTex& t, float x, float y, float w, float h,
                     float u0, float v0, float u1, float v1);

    // True 3D sphere mesh renderer (system-view planets).
    // px,py,pz = planet centre in view space, baseR = world radius.
    // axis (view space) is the planet's rotation pole; the sphere is aligned
    // so +Y maps onto it, then spun by `spin`. lightDir = unit vector toward
    // the star (view space). Atmosphere rim colour atmoR/G/B fades on the limb.
    // Projected with the system-view projection (dist/focal/winW/winH).
    void drawSphere3D(const ImageTex& t, float px, float py, float pz, float baseR,
                      float axisX, float axisY, float axisZ, float spin,
                      float lightDirX, float lightDirY, float lightDirZ,
                      float atmoR, float atmoG, float atmoB,
                      float dist, float focal, float winW, float winH);

    // Text helpers (rely on FontRenderer in current projection). World vs
    // screen y-orientation is tracked automatically (see beginWorld/beginScreen).
    float drawText(const std::string& s, float x, float y, unsigned size, const Color& c = Color(1,1,1)) {
        return font().draw(s, x, y, size, c, !worldText_);
    }
    float measureText(const std::string& s, unsigned size) { return font().measure(s, size); }

    // Expose fonts: Loads LiberationSans + JetBrainsMono fallback.
    FontRenderer& font() { return sans_; }
    FontRenderer& mono() { return mono_; }
    bool fontsLoaded() const { return sans_.loaded() && mono_.loaded(); }

private:
    FontRenderer sans_;
    FontRenderer mono_;
    bool worldText_ = false; // true inside world projection (y grows up)
};

// World<->screen conversion helpers (screen: y grows down, world: y grows up).
Vec2 worldToScreen(const Vec2& w, const Camera& cam, float winW, float winH);

// Upload raw RGBA pixels (tightly packed, row 0 = image top) into an OpenGL
// texture. Used for procedural art (e.g. Cairo-rendered flags).
ImageTex textureFromRGBA(int w, int h, const std::vector<unsigned char>& rgba);

// Write RGBA image to a PNG file (disk cache).
bool savePNG(const char* path, int w, int h, const unsigned char* rgba);

// Dock awaiting convoys as a vertical square list beside their parked system,
// avoiding the system circle and screen edges. Returns convoyId -> docked pos (world).
std::map<Id, Vec2> dockConvoys(const GameMap& map, const std::vector<Convoy>& convoys,
                               const Camera& cam, float winW, float winH);

} // namespace sg