#include "generator.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <sstream>
#include <vector>

namespace flaggen {

// Sentinel "no ordinary" layer: flag is just field + division + charge.
static const OrdinaryConfig kNoOrdinary{"none", "none"};

static bool incompatible(const DivisionConfig& d, const OrdinaryConfig& o) {
    for (const auto& x : d.incompatible) if (x == o.id) return true;
    for (const auto& x : o.incompatible) if (x == d.id) return true;
    return false;
}

// Evaluates simple expressions like "width/2", "height/5*3" (only * and /).
static double evalExpr(const std::string& expr, double w, double h) {
    std::string s;
    for (size_t i = 0; i < expr.size();) {
        if (expr.compare(i, 5, "width") == 0) { s += std::to_string(w); i += 5; }
        else if (expr.compare(i, 6, "height") == 0) { s += std::to_string(h); i += 6; }
        else { s += expr[i]; ++i; }
    }
    std::istringstream iss(s);
    double val;
    if (!(iss >> val)) return w / 2;
    char op;
    double rhs;
    while (iss >> op >> rhs) {
        if (op == '/') val /= rhs;
        else if (op == '*') val *= rhs;
    }
    return val;
}

static Alignment computeAlignment(const DivisionConfig& d, const OrdinaryConfig& o,
                                  std::mt19937& rng, double w, double h) {
    Alignment a;
    const double opts[3] = {w / 2, w / 3, h / 2};
    std::uniform_int_distribution<int> pickX(0, 2);
    a.x = opts[pickX(rng)];
    a.y = h / 2;

    // Barry alignment: place the anchor on a stripe so a canton sits on it.
    if (d.render == "barry") {
        if (d.stripes == 5) a.y = h / 5 * 3;
        else if (d.stripes == 7) a.y = h / 7 * 2;
    }

    if (d.rigidX && !d.requiredAlignmentX.empty()) a.x = evalExpr(d.requiredAlignmentX, w, h);
    if (o.rigidX && !o.requiredAlignmentX.empty()) a.x = evalExpr(o.requiredAlignmentX, w, h);
    if (d.rigidY && !d.requiredAlignmentY.empty()) a.y = evalExpr(d.requiredAlignmentY, w, h);
    if (o.rigidY && !o.requiredAlignmentY.empty()) a.y = evalExpr(o.requiredAlignmentY, w, h);
    return a;
}

static ChargePosition pickChargePosition(std::mt19937& rng) {
    std::uniform_real_distribution<double> d(0.0, 1.0);
    const double r = d(rng);
    if (r < 0.5) return ChargePosition::Center;
    if (r < 0.85) return ChargePosition::Canton;
    return ChargePosition::Corner;
}

// True when the ordinary is a "band" type that a charge should sit on.
static bool isBandOrdinary(const OrdinaryConfig& o) {
    const std::string& r = o.render;
    return r == "fess" || r == "chief" || r == "base" || r == "pile" ||
           r == "pale" || r == "pale_dexter" || r == "pale_sinister" ||
           r == "bend" || r == "bend_sinister" || r == "cross" ||
           r == "nordic" || r == "saltire" || r == "pall" || r == "canton";
}

// Computes where a charge should sit on a band ordinary, plus the maximum
// radius it may have before overflowing the band.
static void placementForOrdinary(const OrdinaryConfig& o, const Alignment& a,
                                 double w, double h, double& px, double& py,
                                 double& maxR) {
    const double th = o.thickness;
    const double m = std::min(w, h);
    const std::string& r = o.render;
    px = w / 2;
    py = h / 2;
    maxR = m * 0.2;

    if (r == "fess") {
        py = a.y;
        maxR = h * th / 2;
    } else if (r == "chief") {
        py = h * th / 2;
        maxR = h * th / 2;
    } else if (r == "base") {
        py = h - h * th / 2;
        maxR = h * th / 2;
    } else if (r == "pile") {
        py = h * 0.22;
        maxR = m * 0.2;
    } else if (r == "pale") {
        px = a.x;
        maxR = w * th / 2;
    } else if (r == "pale_dexter") {
        const double t = w * th;
        px = a.x - w * 0.15 - t / 2;
        maxR = t / 2;
    } else if (r == "pale_sinister") {
        const double t = w * th;
        px = a.x + w * 0.15 + t / 2;
        maxR = t / 2;
    } else if (r == "bend" || r == "bend_sinister") {
        px = a.x;
        py = a.y;
        maxR = m * th / 2;
    } else if (r == "cross" || r == "nordic") {
        px = a.x;
        py = a.y;
        maxR = std::min(h * th, w * th) / 2;
    } else if (r == "saltire") {
        px = a.x;
        py = a.y;
        maxR = m * th / 2;
    } else if (r == "pall") {
        px = a.x;
        py = a.y;
        maxR = h * 0.14;
    } else if (r == "canton") {
        const double s = m * o.size;
        px = s / 2;
        py = a.y - s / 2;
        maxR = s / 2;
    }
}

// Arranges charge anchors along a band ordinary (row of 1..3 charges). Returns
// the maximum radius that keeps the row inside the band and the charges apart.
static // Ratio of a charge's largest drawn extent (corner/ray/horn reach) to its
// base radius. Square corners reach ~1.41x baseR; shrinking maxR by this
// factor keeps such charges fully inside the flag near the edges.
double chargeExtentFactor(const ChargeConfig& ch) {
    if (ch.render == "square") return 1.414;
    if (ch.render == "cross_patee") return 0.62;
    if (ch.render == "trefoil") return 0.84;
    if (ch.render == "fleur_de_lis") return 0.98;
    return 1.0;
}

double computeBandAnchors(const OrdinaryConfig& o, const Alignment& a,
                                 double w, double h, double px, double py,
                                 double maxR, int row,
                                 std::vector<std::pair<double, double>>& anchors) {
    const std::string& r = o.render;
    anchors.clear();

    if (r == "fess" || r == "chief" || r == "base" || r == "pile") {
        if (row == 1) anchors.emplace_back(w / 2, py);
        else for (int i = 0; i < row; ++i) anchors.emplace_back(w * (i + 1) / (row + 1.0), py);
        maxR = std::min(maxR, w / (row + 1.0) * 0.45);
    } else if (r == "pale" || r == "pale_dexter" || r == "pale_sinister") {
        if (row == 1) anchors.emplace_back(px, h / 2);
        else for (int i = 0; i < row; ++i) anchors.emplace_back(px, h * (i + 1) / (row + 1.0));
        maxR = std::min(maxR, h / (row + 1.0) * 0.45);
    } else {
        anchors.emplace_back(px, py);
    }
    return maxR;
}

// Renders the three lower layers onto a small off-screen surface and samples
// the background at every charge anchor and around each one.
static std::vector<Color> sampleBackgroundPoints(const GenerationResult& g,
                                                 double w, double h,
                                                 double sampleR,
                                                 const std::vector<std::pair<double, double>>& anchors) {
    const double scale = 0.1;
    const int sw = std::max(1, (int)(w * scale));
    const int sh = std::max(1, (int)(h * scale));
    cairo_surface_t* samp = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, sw, sh);
    cairo_t* s_cr = cairo_create(samp);
    cairo_scale(s_cr, scale, scale);
    drawField(s_cr, w, h, g.palette.a);
    drawDivision(s_cr, g.division, w, h, g.palette.b, g.alignment);
    drawOrdinary(s_cr, g.ordinary, w, h, g.palette.c, g.alignment,
                 &g.ordinaryFimbriation);

    unsigned char* data = cairo_image_surface_get_data(samp);
    const int stride = cairo_image_surface_get_stride(samp);

    const auto read = [&](double wx, double wy) -> Color {
        const int px = std::clamp((int)(wx * scale), 0, sw - 1);
        const int py = std::clamp((int)(wy * scale), 0, sh - 1);
        uint32_t word;
        std::memcpy(&word, data + py * stride + px * 4, 4);
        // CAIRO_FORMAT_ARGB32 word layout is 0xAARRGGBB.
        Color c;
        c.r = static_cast<double>((word >> 16) & 0xFF) / 255.0;
        c.g = static_cast<double>((word >> 8) & 0xFF) / 255.0;
        c.b = static_cast<double>(word & 0xFF) / 255.0;
        return c;
    };

    std::vector<Color> points;
    const double R = std::max(sampleR, 1.0);
    for (const auto& anchor : anchors) {
        points.push_back(read(anchor.first, anchor.second));
        for (int dir = 0; dir < 8; ++dir) {
            const double ang = dir * 2.0 * M_PI / 8;
            for (double k : {0.5, 1.0}) {
                points.push_back(read(anchor.first + std::cos(ang) * R * k,
                                      anchor.second + std::sin(ang) * R * k));
            }
        }
    }

    cairo_destroy(s_cr);
    cairo_surface_destroy(samp);
    return points;
}

Color pickChargeColorAgainst(const Palette& p, const std::vector<Color>& background) {
    const Color* options[3] = {&p.a, &p.b, &p.c};
    const Color* best = &p.c;
    double bestScore = -1.0;
    for (const auto* c : options) {
        double score = 1e18;
        for (const auto& b : background) {
            score = std::min(score, contrastRatio(*c, b));
        }
        if (score > bestScore) {
            bestScore = score;
            best = c;
        }
    }
    return *best;
}

void resolveCharge(GenerationResult& g, std::mt19937& rng, double w, double h) {
    const double m = std::min(w, h);
    double px = w / 2, py = h / 2, maxR = m * 0.3;

    if (isBandOrdinary(g.ordinary)) {
        placementForOrdinary(g.ordinary, g.alignment, w, h, px, py, maxR);
        g.chargePosition = ChargePosition::Center;

        // Occasionally arrange several charges in a row along the band.
        g.chargeRow = 1;
        const std::string& r = g.ordinary.render;
        const bool canRow = r == "fess" || r == "chief" || r == "base" ||
                            r == "pale" || r == "pale_dexter" || r == "pale_sinister";
        if (canRow) {
            std::uniform_real_distribution<double> u(0.0, 1.0);
            const double rr = u(rng);
            if (rr < 0.2) g.chargeRow = 3;
            else if (rr < 0.32) g.chargeRow = 2;
        }
        maxR = computeBandAnchors(g.ordinary, g.alignment, w, h, px, py,
                                  maxR, g.chargeRow, g.chargeAnchors);
    } else {
        g.chargeAnchors.clear();
        const bool openField = g.ordinary.render == "none" ||
                               g.ordinary.render == "canton" ||
                               g.ordinary.render == "orle" ||
                               g.ordinary.render == "bordure";
        bool fieldArranged = false;
        if (openField && g.chargePosition == ChargePosition::Center) {
            std::uniform_real_distribution<double> u(0.0, 1.0);
            if (u(rng) < 0.6) {
                fieldArranged = true;
                std::uniform_int_distribution<int> layout(0, 4);
                switch (layout(rng)) {
                    case 0:  // three charges in a horizontal row
                        for (int i = 0; i < 3; ++i)
                            g.chargeAnchors.emplace_back(w * (i + 1) / 4.0, h / 2);
                        maxR = std::min({m * 0.2, w / 4.0 * 0.4, h * 0.25});
                        break;
                    case 1:  // two charges in a horizontal row
                        for (int i = 0; i < 2; ++i)
                            g.chargeAnchors.emplace_back(w * (2 * i + 1) / 4.0, h / 2);
                        maxR = std::min({m * 0.2, w / 4.0 * 0.45, h * 0.25});
                        break;
                    case 2:  // three charges in a vertical column
                        for (int i = 0; i < 3; ++i)
                            g.chargeAnchors.emplace_back(w / 2, h * (i + 1) / 4.0);
                        maxR = std::min({m * 0.2, h / 4.0 * 0.4, w * 0.25});
                        break;
                    case 3:  // two charges in a vertical column
                        for (int i = 0; i < 2; ++i)
                            g.chargeAnchors.emplace_back(w / 2, h * (2 * i + 1) / 4.0);
                        maxR = std::min({m * 0.2, h / 4.0 * 0.45, w * 0.25});
                        break;
                    case 4:  // 2x2 grid
                        for (int j = 0; j < 2; ++j)
                            for (int i = 0; i < 2; ++i)
                                g.chargeAnchors.emplace_back(w * (2 * i + 1) / 4.0,
                                                             h * (2 * j + 1) / 4.0);
                        maxR = std::min({m * 0.16, w / 4.0 * 0.4, h / 4.0 * 0.4});
                        break;
                }
                g.chargePosition = ChargePosition::Center;
            }
        }
        if (!fieldArranged) {
            if (g.ordinary.render == "chevron") {
                // A chevron is a V band: the charge reads best in the empty
                // trough below the apex point, not on the sharp tip.
                px = w / 2;
                py = h * 0.62;
                maxR = m * 0.11;
                g.chargePosition = ChargePosition::Center;
            } else {
                switch (g.chargePosition) {
                    case ChargePosition::Canton:
                        px = w * 0.25;
                        py = h * 0.25;
                        maxR = m * 0.2;
                        break;
                    case ChargePosition::Corner: {
                        std::uniform_int_distribution<int> sideX(0, 1), sideY(0, 1);
                        px = sideX(rng) ? w * 0.25 : w * 0.75;
                        py = sideY(rng) ? h * 0.25 : h * 0.75;
                        maxR = m * 0.16;
                        break;
                    }
                    default:
                        break;
                }
            }
            g.chargeAnchors.emplace_back(px, py);
        }
    }
g.chargeX = px;
    g.chargeY = py;

    // Keep the charge (including its corners) fully inside the flag.
    double minDist = 1e18;
    for (const auto& an : g.chargeAnchors) {
        minDist = std::min({minDist, an.first, w - an.first,
                            an.second, h - an.second});
    }
    if (minDist > 0.0) {
        maxR = std::min(maxR, minDist / chargeExtentFactor(g.charge));
    }

    const double baseR = chargeBaseRadius(g.charge, w, h);
    g.chargeScale = std::clamp(maxR / std::max(baseR, 1.0), 0.05, 1.0);

    const auto bg = sampleBackgroundPoints(g, w, h, baseR * g.chargeScale,
                                           g.chargeAnchors);
    g.chargeColor = pickChargeColorAgainst(g.palette, bg);

    g.chargeMinContrast = 1e18;
    for (const auto& b : bg) {
        g.chargeMinContrast = std::min(g.chargeMinContrast,
                                       contrastRatio(g.chargeColor, b));
    }

    // Fimbriation: heraldic outline when the charge would blend into the
    // background (fixes colour-on-colour tincture violations).
    g.fimbriation = Fimbriation();
    if (g.chargeMinContrast < 1.8) {
        double lum = 0.0;
        for (const auto& b : bg) lum += relativeLuminance(b);
        lum /= static_cast<double>(bg.size());
        g.fimbriation.enabled = true;
        if (lum < 0.4) {
            g.fimbriation.r = 1.0;
            g.fimbriation.g = 1.0;
            g.fimbriation.b = 1.0;
        } else {
            g.fimbriation.r = 0.07;
            g.fimbriation.g = 0.07;
            g.fimbriation.b = 0.07;
        }
        g.fimbriation.width = m * 0.018;
    }

    // Ordinary fimbriation: a heraldic outline around the band layer. It is
    // used functionally when the band blends with the field/division, and
    // decoratively otherwise (classic flags like the Union Jack or Brittany).
    g.ordinaryFimbriation = Fimbriation();
    {
        const double ccA = contrastRatio(g.palette.c, g.palette.a);
        const double ccB = contrastRatio(g.palette.c, g.palette.b);
        const bool functional = std::min(ccA, ccB) < 2.0;
        std::uniform_real_distribution<double> u(0.0, 1.0);
        const bool decorative = !functional && u(rng) < 0.16;
        if (functional || decorative) {
            g.ordinaryFimbriation.enabled = true;
            g.ordinaryFimbriation.width = 0.03;
            if (relativeLuminance(g.palette.c) < 0.5) {
                g.ordinaryFimbriation.r = 1.0;
                g.ordinaryFimbriation.g = 1.0;
                g.ordinaryFimbriation.b = 1.0;
            } else {
                g.ordinaryFimbriation.r = 0.07;
                g.ordinaryFimbriation.g = 0.07;
                g.ordinaryFimbriation.b = 0.07;
            }
        }
    }
}

// Quality score: prefer charges that fit inside the flag, read clearly against
// the background, and are reasonably large.
static double scoreFlag(const GenerationResult& g, double w, double h) {
    double score = 0.0;
    const double R = chargeBaseRadius(g.charge, w, h) * g.chargeScale;
    bool inBounds = true;
    for (const auto& anchor : g.chargeAnchors) {
        if (anchor.first - R < -2.0 || anchor.first + R > w + 2.0 ||
            anchor.second - R < -2.0 || anchor.second + R > h + 2.0) {
            inBounds = false;
            break;
        }
    }
    score += inBounds ? 2.0 : -10.0;
    score += std::min(g.chargeMinContrast, 4.0);
    if (g.chargeScale < 0.3) score -= 2.0;
    return score;
}

static GenerationResult generateCandidate(const Config& cfg, std::mt19937& rng,
                                          double w, double h) {
    GenerationResult g;
    if (cfg.divisions.empty() || cfg.ordinaries.empty() || cfg.charges.empty()) {
        return g;
    }

    std::uniform_int_distribution<size_t> pick(0, cfg.divisions.size() - 1);
    g.division = cfg.divisions[pick(rng)];

    std::uniform_real_distribution<double> u01(0.0, 1.0);
    if (u01(rng) < 0.15) {
        g.ordinary = kNoOrdinary;
    } else {
        pick = std::uniform_int_distribution<size_t>(0, cfg.ordinaries.size() - 1);
        for (int tries = 0; tries < 200; ++tries) {
            g.ordinary = cfg.ordinaries[pick(rng)];
            if (!incompatible(g.division, g.ordinary)) break;
        }
        // Never emit a clash: if every draw was incompatible, drop the
        // ordinary layer rather than combine contradictory elements.
        if (incompatible(g.division, g.ordinary)) g.ordinary = kNoOrdinary;
    }

    pick = std::uniform_int_distribution<size_t>(0, cfg.charges.size() - 1);
    g.charge = cfg.charges[pick(rng)];

    g.palette = generatePalette(rng);
    g.alignment = computeAlignment(g.division, g.ordinary, rng, w, h);
    g.chargePosition = pickChargePosition(rng);
    resolveCharge(g, rng, w, h);
    return g;
}

GenerationResult generateFlag(const Config& cfg, std::mt19937& rng,
                              double w, double h) {
    GenerationResult best;
    double bestScore = -1e18;
    for (int i = 0; i < 12; ++i) {
        GenerationResult g = generateCandidate(cfg, rng, w, h);
        if (g.division.id.empty()) return g;
        const double s = scoreFlag(g, w, h);
        if (s > bestScore) {
            bestScore = s;
            best = std::move(g);
        }
    }
    return best;
}

void renderFlag(cairo_t* cr, const GenerationResult& g, double w, double h) {
    drawField(cr, w, h, g.palette.a);
    drawDivision(cr, g.division, w, h, g.palette.b, g.alignment);
    drawOrdinary(cr, g.ordinary, w, h, g.palette.c, g.alignment,
                 &g.ordinaryFimbriation);

    for (const auto& anchor : g.chargeAnchors) {
        const Alignment chargeAnchor{anchor.first, anchor.second};
        drawCharge(cr, g.charge, w, h, g.chargeColor, chargeAnchor,
                   g.chargeScale, g.fimbriation);
    }
}

} // namespace flaggen
