#pragma once

#include "mapgen/MapModel.h"
#include <vector>
#include <cstdint>

namespace sg {

// ---------------------------------------------------------------------------
// Trimmed standalone copy of the original game's fleet/Fleet.h. Only the
// types the graphics engine draws (FleetDuty + Convoy) are kept so the engine
// compiles on its own. The full fleet logic lives in the game repository.
// ---------------------------------------------------------------------------

// Fleet members that can be spawned for various tasks.
enum class FleetDuty : int { None, Build, BuildAuto, Explore, Colonize };

struct Convoy {
    Id id = 0;
    FleetDuty duty = FleetDuty::None;
    Vec2 pos;
    std::vector<Id> path;   // node sequence
    Id target = 0;          // final node
    bool awaitingOrder = false; // parked; click to choose destination
    // -- colonial region convoys: owner != 0 means the convoy belongs to a
    //    region (governor-driven); drawn in the region's colour --
    Id owner = 0;                 // 0 = empire convoy, else Region::id
    uint32_t ownerColor = 0;      // RGB24 outline colour when owner != 0
};

} // namespace sg