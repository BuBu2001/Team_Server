#pragma once

#include <random>
#include <utility>
#include <vector>

#include "color.h"
#include "config.h"
#include "layers.h"

namespace flaggen {

enum class ChargePosition { Center, Canton, Corner };

struct GenerationResult {
    DivisionConfig division;
    OrdinaryConfig ordinary;
    ChargeConfig charge;
    Palette palette;
    Alignment alignment;
    ChargePosition chargePosition = ChargePosition::Center;
    double chargeX = 0.0;
    double chargeY = 0.0;
    double chargeScale = 1.0;
    Color chargeColor;
    double chargeMinContrast = 0.0;
    Fimbriation fimbriation;
    Fimbriation ordinaryFimbriation; // outline around the ordinary layer
    int chargeRow = 1; // charges arranged in a row on a band ordinary
    std::vector<std::pair<double, double>> chargeAnchors;
};

// Randomly composes a flag (best of several candidates) respecting
// incompatibilities, alignment rules, the rule of tincture and readability.
GenerationResult generateFlag(const Config& cfg, std::mt19937& rng,
                              double w, double h);

// Recomputes charge placement, scale, color and fimbriation for the current
// layers (used by the constructor mode after overriding layers).
void resolveCharge(GenerationResult& g, std::mt19937& rng, double w, double h);

// Picks the charge color from the palette that contrasts best against the
// sampled background colors (maximizes the worst-case contrast).
Color pickChargeColorAgainst(const Palette& p, const std::vector<Color>& background);

// Renders all four layers onto the Cairo context.
void renderFlag(cairo_t* cr, const GenerationResult& g, double w, double h);

} // namespace flaggen
