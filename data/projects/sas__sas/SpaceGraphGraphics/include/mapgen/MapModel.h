#pragma once

#include "core/Types.h"
#include <vector>
#include <string>
#include <cstdint>

namespace sg {

// ---------------------------------------------------------------------------
// Trimmed standalone copy of the original game's mapgen/MapModel.h.
// Only the types the graphics engine actually touches are kept so the engine
// compiles on its own. The full game model lives in the game repository.
// ---------------------------------------------------------------------------

// --- Planets --------------------------------------------------------
enum class PlanetType : int {
    Continental, Desert, Ice, AsteroidBelt, GasGiant,
    COUNT
};

struct Planet {
    std::string name;            // proper name (e.g. "Земля"); empty if generic
    PlanetType type = PlanetType::Desert;
    uint32_t textureSeed = 0;    // deterministic seed for procedural texture generation
};

// --- Systems (nodes) ------------------------------------------------
struct System {
    Id id = 0;
    std::string name;
    Vec2 pos;
    float radius = 20.f;
    PlanetType primary = PlanetType::Desert;

    std::vector<Planet> planets;

    bool isCapital = false;
    bool hasBeacon = false;     // manually built beacon
    bool isConnected = false;   // connected to capital network via beacons
};

// --- GameMap --------------------------------------------------------
struct GameMap {
    std::vector<System> nodes;
};

} // namespace sg