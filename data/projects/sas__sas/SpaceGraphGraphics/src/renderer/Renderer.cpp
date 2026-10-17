#include "renderer/Renderer.h"

#include <cmath>
#include <GL/gl.h>

namespace sg {

static void glColor(const Color& c) { glColor4f(c.r, c.g, c.b, c.a); }

Vec2 worldToScreen(const Vec2& w, const Camera& cam, float winW, float winH) {
    // screen y grows down, world y grows up
    return { (w.x - cam.centre.x) * cam.zoom + winW/2.f,
             winH/2.f - (w.y - cam.centre.y) * cam.zoom };
}

// Dock awaiting convoys as a vertical square list beside their parked system,
// avoiding the system circle and screen edges. Returns convoyId -> docked pos.
std::map<Id, Vec2> dockConvoys(const GameMap& map, const std::vector<Convoy>& convoys,
                               const Camera& cam, float winW, float winH) {
    std::map<Id, Vec2> docked;
    const float icon = 20.f;     // px square size
    const float gap = 35.f;      // px from system circle to first convoy
    const float step = 28.f;     // px vertical spacing between convoys
    const float marginR = 8.f;
    const float marginB = 120.f; // bottom UI (log) area
    const float topB = 40.f;     // top UI area

    std::map<Id, std::vector<Id>> byNode;
    for (auto& c : convoys) {
        if (!c.awaitingOrder) continue;
        Id node = c.path.empty() ? c.target : c.path[0];
        if (node >= map.nodes.size()) continue;
        byNode[node].push_back(c.id);
    }
    for (auto& kv : byNode) {
        Id node = kv.first;
        if (node >= map.nodes.size()) continue;
        Vec2 sysS = worldToScreen(map.nodes[node].pos, cam, winW, winH);
        float rPx = map.nodes[node].radius * cam.zoom;

        // start list to the right of the system, vertically centered
        float x = sysS.x + rPx + gap;
        float n = static_cast<float>(kv.second.size());
        float listH = (n - 1.f) * step + icon;
        float yTop = sysS.y - listH / 2.f;

        // right edge overflow -> move list to the left of the system
        if (x + icon > winW - marginR) x = sysS.x - rPx - gap - icon;
        if (x < marginR) x = marginR;
        // bottom overflow -> shift list up
        if (yTop + listH > winH - marginB) yTop = winH - marginB - listH;
        if (yTop < topB) yTop = topB;

        for (size_t i = 0; i < kv.second.size(); ++i) {
            Vec2 sc = { x + icon/2.f, yTop + (float)i * step + icon/2.f };
            Vec2 w;
            w.x = cam.centre.x + (sc.x - winW/2.f) / cam.zoom;
            w.y = cam.centre.y + (winH/2.f - sc.y) / cam.zoom;
            docked[kv.second[i]] = w;
        }
    }
    return docked;
}

static void ortho2d(float left, float right, float bottom, float top) {
    glMatrixMode(GL_PROJECTION);
    glLoadIdentity();
    glOrtho(left, right, bottom, top, -1.0, 1.0);
    glMatrixMode(GL_MODELVIEW);
    glLoadIdentity();
}

void Renderer::beginWorld(const Camera& cam, float winW, float winH) {
    glDisable(GL_TEXTURE_2D);
    worldText_ = true;
    float hw = winW / (2.f * cam.zoom);
    float hh = winH / (2.f * cam.zoom);
    ortho2d(cam.centre.x - hw, cam.centre.x + hw,   // left, right
            cam.centre.y - hh, cam.centre.y + hh);  // bottom, top
    glEnable(GL_BLEND);
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
}

void Renderer::endWorld() {
    glDisable(GL_BLEND);
}

void Renderer::beginScreen(float winW, float winH) {
    glDisable(GL_TEXTURE_2D);
    worldText_ = false;
    // screen pixels: origin top-left, y down
    ortho2d(0, winW, winH, 0);
    glEnable(GL_BLEND);
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
}

void Renderer::endScreen() {
    glDisable(GL_BLEND);
}

void Renderer::drawBackground() {
    glDisable(GL_TEXTURE_2D);
    glDisable(GL_BLEND);
    glColor4f(0.03f, 0.03f, 0.06f, 1.f);
    glBegin(GL_QUADS);
    glVertex2f(-100000.f, -100000.f);
    glVertex2f( 100000.f, -100000.f);
    glVertex2f( 100000.f,  100000.f);
    glVertex2f(-100000.f,  100000.f);
    glEnd();
}

void Renderer::drawLine(const Vec2& a, const Vec2& b, const Color& col, float width) {
    glDisable(GL_TEXTURE_2D);
    glColor(col);
    glLineWidth(width);
    glBegin(GL_LINES);
    glVertex2f(a.x, a.y);
    glVertex2f(b.x, b.y);
    glEnd();
    glLineWidth(1.f);
}

void Renderer::drawTriangle(const Vec2& a, const Vec2& b, const Vec2& c, const Color& fill) {
    glDisable(GL_TEXTURE_2D);
    if (fill.a <= 0.f) return;
    glColor(fill);
    glBegin(GL_TRIANGLES);
    glVertex2f(a.x, a.y);
    glVertex2f(b.x, b.y);
    glVertex2f(c.x, c.y);
    glEnd();
}

// Filled convex polygon (triangle fan from the first vertex).
void Renderer::drawPolygon(const std::vector<Vec2>& pts, const Color& fill) {
    if (pts.size() < 3 || fill.a <= 0.f) return;
    glDisable(GL_TEXTURE_2D);
    glColor(fill);
    glBegin(GL_TRIANGLE_FAN);
    glVertex2f(pts[0].x, pts[0].y);
    for (size_t i = 1; i < pts.size(); ++i) {
        glVertex2f(pts[i].x, pts[i].y);
    }
    glEnd();
}

void Renderer::drawPolyline(const std::vector<Vec2>& pts, const Color& col, float width, bool closed) {
    if (pts.size() < 2 || col.a <= 0.f) return;
    glDisable(GL_TEXTURE_2D);
    glColor(col);
    glLineWidth(width);
    glBegin(closed ? GL_LINE_LOOP : GL_LINE_STRIP);
    for (const auto& p : pts) glVertex2f(p.x, p.y);
    glEnd();
    glLineWidth(1.f);
}

void Renderer::drawPoints(const std::vector<Vec2>& pts, const Color& col, float size) {
    if (pts.empty() || col.a <= 0.f) return;
    glDisable(GL_TEXTURE_2D);
    glColor(col);
    glPointSize(size);
    glBegin(GL_POINTS);
    for (const auto& p : pts) glVertex2f(p.x, p.y);
    glEnd();
    glPointSize(1.f);
}

void Renderer::drawLines(const std::vector<Vec2>& pts, const Color& col, float width) {
    if (pts.size() < 2 || col.a <= 0.f) return;
    glDisable(GL_TEXTURE_2D);
    glColor(col);
    glLineWidth(width);
    glBegin(GL_LINES);
    for (const auto& p : pts) glVertex2f(p.x, p.y);
    glEnd();
    glLineWidth(1.f);
}

void Renderer::drawCircle(const Vec2& c, float r, const Color& fill) {
    drawCircle(c, r, fill, Color(0,0,0,0), 0.f);
}

void Renderer::drawCircle(const Vec2& c, float r, const Color& fill, const Color& outline, float outlineW) {
    glDisable(GL_TEXTURE_2D);
    const int SEG = 40;
    if (fill.a > 0.f) {
        glColor(fill);
        glBegin(GL_TRIANGLE_FAN);
        glVertex2f(c.x, c.y);
        for (int i = 0; i <= SEG; ++i) {
            float a = 2.f * 3.14159265f * i / SEG;
            glVertex2f(c.x + r * std::cos(a), c.y + r * std::sin(a));
        }
        glEnd();
    }
    if (outlineW > 0.f && outline.a > 0.f) {
        glColor(outline);
        glLineWidth(outlineW);
        glBegin(GL_LINE_LOOP);
        for (int i = 0; i < SEG; ++i) {
            float a = 2.f * 3.14159265f * i / SEG;
            glVertex2f(c.x + r * std::cos(a), c.y + r * std::sin(a));
        }
        glEnd();
        glLineWidth(1.f);
    }
}

void Renderer::drawRect(float x, float y, float w, float h, const Color& fill) {
    drawRect(x, y, w, h, fill, Color(0,0,0,0), 0.f);
}

void Renderer::drawRect(float x, float y, float w, float h, const Color& fill, const Color& outline, float outlineW) {
    glDisable(GL_TEXTURE_2D);
    if (fill.a > 0.f) {
        glColor(fill);
        glBegin(GL_QUADS);
        glVertex2f(x, y);
        glVertex2f(x + w, y);
        glVertex2f(x + w, y + h);
        glVertex2f(x, y + h);
        glEnd();
    }
    if (outlineW > 0.f && outline.a > 0.f) {
        glColor(outline);
        glLineWidth(outlineW);
        glBegin(GL_LINE_LOOP);
        glVertex2f(x, y);
        glVertex2f(x + w, y);
        glVertex2f(x + w, y + h);
        glVertex2f(x, y + h);
        glEnd();
        glLineWidth(1.f);
    }
}

void Renderer::drawEdge(const Vec2& a, const Vec2& b, bool highlighted) {
    static const Color hi(0.25f, 0.7f, 1.f, 0.8f);
    static const Color lo(0.3f, 0.3f, 0.4f, 0.45f);
    drawLine(a, b, highlighted ? hi : lo, highlighted ? 2.f : 1.f);
}

void Renderer::drawEdgeDashed(const Vec2& a, const Vec2& b, bool highlighted) {
    float dx = b.x - a.x, dy = b.y - a.y;
    float L = std::sqrt(dx*dx + dy*dy);
    if (L < 0.001f) return;
    dx /= L; dy /= L;
    static const Color hi(0.25f, 0.7f, 1.f, 0.8f);
    static const Color lo(0.3f, 0.3f, 0.4f, 0.45f);
    const Color& col = highlighted ? hi : lo;
    const float dash = 8.f, gap = 6.f;
    float t = 0.f;
    while (t < L) {
        float e = std::min(t + dash, L);
        glDisable(GL_TEXTURE_2D);
        glColor(col);
        glLineWidth(1.f);
        glBegin(GL_LINES);
        glVertex2f(a.x + dx*t, a.y + dy*t);
        glVertex2f(a.x + dx*e, a.y + dy*e);
        glEnd();
        t += dash + gap;
    }
}

void Renderer::drawSystem(const System& s) {
    Color fill;
    switch (s.primary) {
        case PlanetType::Continental: fill = Color::from255(80, 200, 80);   break;
        case PlanetType::Desert:      fill = Color::from255(220, 200, 80);  break;
        case PlanetType::Ice:         fill = Color::from255(140, 200, 240); break;
        default:                      fill = Color::from255(180, 180, 180); break;
    }
    if (s.isCapital) fill = Color::from255(220, 60, 220);
    else if (s.isConnected) fill = Color::from255(150, 240, 150);

    Color outline = s.hasBeacon ? Color::from255(255, 230, 0)
                                : Color::from255(90, 90, 90);
    float r = s.radius;
    drawCircle(s.pos, r, fill, outline, s.hasBeacon ? 2.5f : 1.f);
}

// Hexagon marker (used for colonize convoys so they read differently from squares).
static void drawHex(float cx, float cy, float r, const Color& fill,
                    const Color& outline, float outlineW) {
    glDisable(GL_TEXTURE_2D);
    const int N = 6;
    glColor(fill);
    glBegin(GL_TRIANGLE_FAN);
    glVertex2f(cx, cy);
    for (int i = 0; i <= N; ++i) {
        float a = (float)(M_PI / 3.0 * i);
        glVertex2f(cx + r * std::cos(a), cy + r * std::sin(a));
    }
    glEnd();
    if (outlineW > 0.f && outline.a > 0.f) {
        glColor(outline);
        glLineWidth(outlineW);
        glBegin(GL_LINE_LOOP);
        for (int i = 0; i < N; ++i) {
            float a = (float)(M_PI / 3.0 * i);
            glVertex2f(cx + r * std::cos(a), cy + r * std::sin(a));
        }
        glEnd();
        glLineWidth(1.f);
    }
}

void Renderer::drawConvoy(const Convoy& c, const Vec2& pos, float zoom, bool selected) {
    Color outline;
    if (c.owner != 0) {
        // region convoy: drawn in the owning region's colour
        uint32_t rgb = c.ownerColor;
        outline = Color::from255((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF);
    } else switch (c.duty) {
        case FleetDuty::Explore:   outline = Color::from255(60, 160, 255); break; // blue
        case FleetDuty::Build:     outline = Color::from255(255, 200, 40);  break; // yellow
        case FleetDuty::BuildAuto: outline = Color::from255(200, 140, 30);  break; // darker yellow
        case FleetDuty::Colonize:  outline = Color::from255(200, 80, 255);  break; // purple
        default:                   outline = Color::from255(190, 190, 190); break;
    }
    Color fill = c.owner != 0 ? Color::from255(26, 30, 40, 230)
                              : Color::from255(18, 20, 28, 230);

    float half = 10.f / zoom;

    // colonizer: distinct shape (hexagon) so it reads differently from the others
    if (c.duty == FleetDuty::Colonize) {
        drawHex(pos.x, pos.y, half, fill,
                selected ? Color::from255(255, 255, 255) : outline,
                selected ? 2.5f : 2.f);
    } else {
        // square icon, screen-constant 20 px (world size scales with 1/zoom)
        drawRect(pos.x - half, pos.y - half, half*2.f, half*2.f, fill,
                 selected ? Color::from255(255, 255, 255) : outline, selected ? 2.5f : 2.f);
    }

    if (fontsLoaded()) {
        std::string num = std::to_string(c.id);
        unsigned fs = (unsigned)(10.f / zoom);
        float tw = measureText(num, fs);
        font().draw(num, pos.x - tw/2.f, pos.y + 3.f / zoom, fs,
                    selected ? Color::from255(0,0,0)
                             : Color(1.f, 1.f, 1.f),
                    false);
    }
}

} // namespace sg