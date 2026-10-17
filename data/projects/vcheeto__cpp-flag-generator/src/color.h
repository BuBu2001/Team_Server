#pragma once

#include <random>
#include <string>

namespace flaggen {

struct Color {
    double r = 0.0, g = 0.0, b = 0.0; // components in [0, 1]
};

// Converts HSL (h in [0,360), s/l in [0,1]) to RGB.
Color hslToRgb(double h, double s, double l);

// WCAG relative luminance with gamma correction.
double relativeLuminance(const Color& c);

// WCAG contrast ratio (L1 + 0.05) / (L2 + 0.05), always >= 1.
double contrastRatio(const Color& a, const Color& b);

// True if contrastRatio(a, b) >= minRatio.
bool hasGoodContrast(const Color& a, const Color& b, double minRatio = 1.5);

struct Palette {
    Color a; // field
    Color b; // division
    Color c; // ordinary
};

// Generates a 3-color palette from HSL with contrast checks (WCAG > 1.5).
Palette generatePalette(std::mt19937& rng);

std::string colorToHex(const Color& c);

} // namespace flaggen
