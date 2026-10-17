#include "color.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <iomanip>
#include <sstream>

namespace flaggen {

Color hslToRgb(double h, double s, double l) {
    h = std::fmod(h, 360.0);
    if (h < 0.0) h += 360.0;
    s = std::clamp(s, 0.0, 1.0);
    l = std::clamp(l, 0.0, 1.0);

    double c = (1.0 - std::fabs(2.0 * l - 1.0)) * s;
    double x = c * (1.0 - std::fabs(std::fmod(h / 60.0, 2.0) - 1.0));
    double m = l - c / 2.0;

    double r = 0.0, g = 0.0, b = 0.0;
    if (h < 60.0) { r = c; g = x; }
    else if (h < 120.0) { r = x; g = c; }
    else if (h < 180.0) { g = c; b = x; }
    else if (h < 240.0) { g = x; b = c; }
    else if (h < 300.0) { r = x; b = c; }
    else { r = c; b = x; }

    return {r + m, g + m, b + m};
}

static double gammaChannel(double v) {
    if (v <= 0.03928) return v / 12.92;
    return std::pow((v + 0.055) / 1.055, 2.4);
}

double relativeLuminance(const Color& c) {
    return 0.2126 * gammaChannel(c.r) +
           0.7152 * gammaChannel(c.g) +
           0.0722 * gammaChannel(c.b);
}

double contrastRatio(const Color& a, const Color& b) {
    double la = relativeLuminance(a);
    double lb = relativeLuminance(b);
    if (la < lb) std::swap(la, lb);
    return (la + 0.05) / (lb + 0.05);
}

bool hasGoodContrast(const Color& a, const Color& b, double minRatio) {
    return contrastRatio(a, b) >= minRatio;
}

Palette generatePalette(std::mt19937& rng) {
    struct Col { double h, s, l; };
    // Classic heraldic tinctures: metals and colours (rule of tincture).
    static const Col metals[] = {
        {45.0, 0.85, 0.55},  // or (gold)
        {0.0, 0.0, 0.93},    // argent (white)
    };
    static const Col colours[] = {
        {0.0, 0.80, 0.42},   // gules (red)
        {220.0, 0.80, 0.36}, // azure (blue)
        {125.0, 0.55, 0.32}, // vert (green)
        {280.0, 0.45, 0.36}, // purpure (purple)
        {0.0, 0.0, 0.10},    // sable (black)
    };
    static const Col modern[] = {
        {350.0, 0.65, 0.38}, // crimson
        {160.0, 0.50, 0.35}, // teal
        {265.0, 0.60, 0.40}, // violet
    };
    std::uniform_int_distribution<int> metalPick(0, 1);
    std::uniform_int_distribution<int> colPick(0, 4);
    std::uniform_int_distribution<int> modernPick(0, 2);
    std::uniform_real_distribution<double> pick(0.0, 1.0);
    std::uniform_real_distribution<double> jit(-0.04, 0.04);

    for (int attempt = 0; attempt < 200; ++attempt) {
        // Field.
        const bool aMetal = pick(rng) < 0.4;
        Color a;
        if (aMetal) {
            const Col& t = metals[metalPick(rng)];
            a = hslToRgb(t.h, t.s, t.l + jit(rng));
        } else {
            const Col& t = colours[colPick(rng)];
            a = hslToRgb(t.h, t.s + jit(rng), t.l + jit(rng));
        }

        // Division: alternates tincture class with the field; occasionally a
        // modern colour-on-colour is allowed if luminance contrast is strong.
        bool bMetal = !aMetal;
        if (pick(rng) < 0.15) bMetal = aMetal;
        Color b;
        if (bMetal) {
            const Col& t = metals[metalPick(rng)];
            b = hslToRgb(t.h, t.s, t.l + jit(rng));
        } else {
            const Col& t = (pick(rng) < 0.75) ? colours[colPick(rng)]
                                              : modern[modernPick(rng)];
            b = hslToRgb(t.h, t.s + jit(rng), t.l + jit(rng));
        }

        if (!hasGoodContrast(a, b, 1.7)) continue;

        // Ordinary: contrasts with the layer beneath it (division).
        bool cMetal = !bMetal;
        if (pick(rng) < 0.2) cMetal = bMetal;
        Color c;
        if (cMetal) {
            const Col& t = metals[metalPick(rng)];
            c = hslToRgb(t.h, t.s, t.l + jit(rng));
        } else {
            const Col& t = (pick(rng) < 0.75) ? colours[colPick(rng)]
                                              : modern[modernPick(rng)];
            c = hslToRgb(t.h, t.s + jit(rng), t.l + jit(rng));
        }

        if (hasGoodContrast(a, b, 1.7) && hasGoodContrast(c, b, 1.6) &&
            hasGoodContrast(c, a, 1.3)) {
            return {a, b, c};
        }
    }

    // Deterministic fallback palette (blue / gold / white).
    return {hslToRgb(210, 0.7, 0.35), hslToRgb(45, 0.8, 0.5), hslToRgb(0, 0.0, 0.92)};
}

std::string colorToHex(const Color& c) {
    auto byte = [](double v) -> int {
        return static_cast<int>(std::lround(std::clamp(v, 0.0, 1.0) * 255.0));
    };
    std::ostringstream oss;
    oss << "#" << std::hex << std::uppercase << std::setfill('0')
        << std::setw(2) << byte(c.r)
        << std::setw(2) << byte(c.g)
        << std::setw(2) << byte(c.b);
    return oss.str();
}

} // namespace flaggen
