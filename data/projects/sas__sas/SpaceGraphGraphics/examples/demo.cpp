// SpaceGraphGraphics demo: SDL2 window + GL 2.1 context (the same setup the
// game uses) exercising the standalone graphics engine:
//   1. galaxy map view  - Renderer primitives, text, convoys
//   2. system view      - TextureCache + true-3D sphere rendering
//
// Controls: [Space] toggle view, [ESC] / [Q] quit, [wheel] zoom in system view.

#include "renderer/Renderer.h"
#include "texturegen/TextureCache.h"

#include <SDL2/SDL.h>
#include <GL/gl.h>

#include <cmath>
#include <cstdio>
#include <vector>

namespace {

struct Vec3 { float x = 0.f, y = 0.f, z = 0.f; };

Vec3 svRotX(const Vec3& v, float a) {
    float c = std::cos(a), s = std::sin(a);
    return { v.x, v.y * c - v.z * s, v.y * s + v.z * c };
}
Vec3 svRotY(const Vec3& v, float a) {
    float c = std::cos(a), s = std::sin(a);
    return { v.x * c + v.z * s, v.y, -v.x * s + v.z * c };
}
sg::Vec2 svProject(Vec3 p, float dist, float focal, float ww, float wh) {
    float z = dist - p.z;
    if (z < 1.f) z = 1.f;
    return { ww / 2.f + p.x * focal / z, wh / 2.f - p.y * focal / z };
}

sg::GameMap makeMap() {
    sg::GameMap m;
    auto sys = [&](sg::Id id, const char* name, float x, float y, sg::PlanetType t) {
        sg::System s;
        s.id = id;
        s.name = name;
        s.pos = { x, y };
        s.primary = t;
        s.radius = 22.f;
        return s;
    };
    m.nodes.push_back(sys(0, "Земля", 400.f, 300.f, sg::PlanetType::Continental));
    m.nodes.push_back(sys(1, "Кентавр", 700.f, 220.f, sg::PlanetType::Desert));
    m.nodes.push_back(sys(2, "Мира", 980.f, 500.f, sg::PlanetType::Ice));
    m.nodes.push_back(sys(3, "Гелиос", 600.f, 620.f, sg::PlanetType::GasGiant));

    // planets: one per system (varying types -> varied procedurally generated art)
    auto addPlanet = [&](sg::System& s, sg::PlanetType t, const char* nm) {
        sg::Planet p;
        p.type = t;
        p.name = nm;
        p.textureSeed = 0xC0FFEEu + s.id * 31u;
        s.planets.push_back(p);
    };
    addPlanet(m.nodes[0], sg::PlanetType::Continental, "Терра");
    addPlanet(m.nodes[1], sg::PlanetType::Desert, "Арид");
    addPlanet(m.nodes[2], sg::PlanetType::Ice, "Крион");
    addPlanet(m.nodes[3], sg::PlanetType::GasGiant, "Юпитер-X");

    m.nodes[0].isCapital = true;
    m.nodes[0].hasBeacon = true;
    m.nodes[1].isConnected = true;
    return m;
}

} // namespace

int main(int argc, char** argv) {
    (void)argc; (void)argv;
    if (SDL_Init(SDL_INIT_VIDEO) != 0) {
        std::fprintf(stderr, "SDL_Init failed: %s\n", SDL_GetError());
        return 1;
    }

    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION, 2);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION, 1);
    SDL_GL_SetAttribute(SDL_GL_DOUBLEBUFFER, 1);

    SDL_Window* win = SDL_CreateWindow("SpaceGraphGraphics demo",
        SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
        1280, 800, SDL_WINDOW_OPENGL | SDL_WINDOW_SHOWN);
    if (!win) {
        std::fprintf(stderr, "SDL_CreateWindow failed: %s\n", SDL_GetError());
        SDL_Quit();
        return 1;
    }
    SDL_GLContext glc = SDL_GL_CreateContext(win);
    if (!glc) {
        std::fprintf(stderr, "SDL_GL_CreateContext failed: %s\n", SDL_GetError());
        SDL_DestroyWindow(win);
        SDL_Quit();
        return 1;
    }
    int winW = 1280, winH = 800;
    SDL_GetWindowSize(win, &winW, &winH);
    glViewport(0, 0, winW, winH);

    sg::GameMap map = makeMap();
    std::vector<sg::Convoy> convoys;
    sg::Convoy c;
    c.id = 1; c.duty = sg::FleetDuty::Explore; c.awaitingOrder = true;
    c.path = { 0 }; c.target = 1; c.pos = { 460.f, 300.f };
    convoys.push_back(c);

    sg::Renderer rnd;
    rnd.font().load("/usr/share/fonts/liberation/LiberationSans-Regular.ttf", 16);
    rnd.mono().load("/usr/share/fonts/liberation/LiberationMono-Regular.ttf", 12);

    sg::TextureCache texCache;
    sg::Camera cam;

    enum class View { Galaxy, System } view =
        (argc > 1 && std::string(argv[1]) == "system") ? View::System : View::Galaxy;
    float yaw = 0.f, pitch = 0.4f, dist = 300.f, anim = 0.f;
    bool running = true;

    while (running) {
        SDL_Event ev;
        while (SDL_PollEvent(&ev)) {
            if (ev.type == SDL_QUIT) running = false;
            else if (ev.type == SDL_KEYDOWN) {
                if (ev.key.keysym.sym == SDLK_ESCAPE || ev.key.keysym.sym == SDLK_q) running = false;
                else if (ev.key.keysym.sym == SDLK_SPACE)
                    view = (view == View::Galaxy) ? View::System : View::Galaxy;
            } else if (ev.type == SDL_MOUSEWHEEL) {
                if (view == View::System) {
                    dist *= (ev.wheel.y > 0) ? 0.9f : 1.1f;
                    if (dist < 80.f) dist = 80.f;
                    if (dist > 800.f) dist = 800.f;
                }
            }
        }
        anim += 0.016f;

        glClearColor(0.03f, 0.03f, 0.06f, 1.f);
        glClear(GL_COLOR_BUFFER_BIT);

        if (view == View::Galaxy) {
            rnd.beginWorld(cam, (float)winW, (float)winH);
            rnd.drawBackground();

            // edges between systems
            for (size_t i = 0; i + 1 < map.nodes.size(); ++i)
                rnd.drawEdge(map.nodes[i].pos, map.nodes[i + 1].pos,
                             map.nodes[i].isConnected && map.nodes[i + 1].isConnected);

            // docked convoy list (uses the Renderer's dockConvoys helper)
            auto docked = sg::dockConvoys(map, convoys, cam, (float)winW, (float)winH);
            for (auto& kv : docked)
                rnd.drawConvoy(convoys[0], kv.second, cam.zoom);

            for (auto& s : map.nodes) rnd.drawSystem(s);
            rnd.endWorld();

            rnd.beginScreen((float)winW, (float)winH);
            rnd.drawText(u8"SpaceGraphGraphics — демо", 16.f, 12.f, 28,
                         sg::Color::from255(120, 200, 255));
            rnd.drawText(u8"Пробел — вид системы   Q/Esc — выход", 16.f, 56.f, 16,
                         sg::Color(0.8f, 0.8f, 0.8f));
            rnd.endScreen();
        } else {
            const sg::System& sys = map.nodes[0];
            const float focal = 600.f;
            texCache.ensureSystemTextures(rnd, sys);

            rnd.beginScreen((float)winW, (float)winH);
            rnd.drawRect(0, 0, (float)winW, (float)winH,
                         sg::Color(0.03f, 0.03f, 0.06f, 1.f));

            // star at origin (warm corona, front-lit)
            if (sg::ImageTex* st = texCache.getStarTexture())
                rnd.drawSphere3D(*st, 0.f, 0.f, 0.f, 22.f,
                                 0.f, 0.f, 1.f, anim * 0.004f,
                                 0.f, 0.f, 1.f,
                                 1.0f, 0.7f, 0.35f,
                                 dist, focal, (float)winW, (float)winH);

            // orbit rings
            Vec3 orbitAxis = svRotX(svRotY({0.f, 1.f, 0.f}, yaw), pitch);
            for (size_t i = 0; i < sys.planets.size(); ++i) {
                const float orbitR = 50.f + (float)i * 40.f;
                std::vector<sg::Vec2> ring;
                ring.reserve(64);
                for (int j = 0; j < 64; ++j) {
                    float a = (float)j * 2.f * 3.14159265f / 64.f;
                    Vec3 rp = svRotX(svRotY({orbitR * std::cos(a), 0.f,
                                             orbitR * std::sin(a)}, yaw), pitch);
                    ring.push_back(svProject(rp, dist, focal, (float)winW, (float)winH));
                }
                rnd.drawPolyline(ring, sg::Color(0.3f, 0.35f, 0.4f, 0.4f), 1.f, true);
            }

            // planets: procedural textures + true-3D lit spheres
            for (size_t i = 0; i < sys.planets.size(); ++i) {
                const sg::Planet& pl = sys.planets[i];
                const float orbitR = 50.f + (float)i * 40.f;
                Vec3 rp = svRotX(svRotY({orbitR, 0.f, 0.f}, yaw), pitch);
                float baseR = (pl.type == sg::PlanetType::GasGiant) ? 16.f : 9.f;
                float nl = 1.f / (std::sqrt(rp.x*rp.x + rp.y*rp.y + rp.z*rp.z) + 1e-5f);

                if (sg::ImageTex* tex = texCache.getPlanetTexture((int)i)) {
                    float ar = 0.45f, ag = 0.65f, ab = 1.0f;
                    if (pl.type == sg::PlanetType::Desert) { ar = 0.95f; ag = 0.70f; ab = 0.45f; }
                    else if (pl.type == sg::PlanetType::Ice) { ar = 0.65f; ag = 0.80f; ab = 1.00f; }
                    else if (pl.type == sg::PlanetType::GasGiant) { ar = 0.75f; ag = 0.65f; ab = 1.00f; }
                    rnd.drawSphere3D(*tex, rp.x, rp.y, rp.z, baseR,
                                     orbitAxis.x, orbitAxis.y, orbitAxis.z,
                                     (float)i * 1.3f + anim * 0.004f,
                                     -rp.x * nl, -rp.y * nl, -rp.z * nl,
                                     ar, ag, ab,
                                     dist, focal, (float)winW, (float)winH);
                }
            }

            rnd.drawText(u8"Система: " + sys.name, 16.f, 12.f, 26,
                         sg::Color::from255(120, 200, 255));
            rnd.endScreen();
        }

        SDL_GL_SwapWindow(win);
    }

    texCache.clear(rnd);
    SDL_GL_DeleteContext(glc);
    SDL_DestroyWindow(win);
    SDL_Quit();
    return 0;
}