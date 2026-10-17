#pragma once

#include <cairo.h>

#include "color.h"
#include "config.h"

namespace flaggen {

// Anchor point (in pixels) used by flexible layers. Rigid layers force it.
struct Alignment {
    double x = 0.0;
    double y = 0.0;
};

// Thin outline (heraldic fimbriation) drawn around a charge to guarantee
// contrast when its color is too close to the background.
struct Fimbriation {
    bool enabled = false;
    double width = 0.0; // in flag pixels
    double r = 1.0, g = 1.0, b = 1.0;
};

// Approximate rendered radius (in pixels) of a charge at scale 1.0.
double chargeBaseRadius(const ChargeConfig& ch, double w, double h);

void drawField(cairo_t* cr, double w, double h, const Color& c);

void drawDivision(cairo_t* cr, const DivisionConfig& d,
                  double w, double h, const Color& c, const Alignment& a);

void drawOrdinary(cairo_t* cr, const OrdinaryConfig& o,
                  double w, double h, const Color& c, const Alignment& a,
                  const Fimbriation* fimb = nullptr);

void drawCharge(cairo_t* cr, const ChargeConfig& ch,
                double w, double h, const Color& c, const Alignment& a,
                double scale = 1.0, const Fimbriation& fimb = Fimbriation());

} // namespace flaggen
