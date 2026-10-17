#include "layers.h"

#include <algorithm>
#include <cmath>

namespace flaggen {

static void setColor(cairo_t* cr, const Color& c) {
    cairo_set_source_rgb(cr, c.r, c.g, c.b);
}

// Fills the current path; if fimbriation is requested, first strokes the path
// with a thick outline in the fimbriation color, then fills with the fill color.
static void finishFill(cairo_t* cr, const Color& col, const Fimbriation* f,
                       double scale) {
    if (f && f->enabled) {
        cairo_set_source_rgb(cr, f->r, f->g, f->b);
        cairo_set_line_width(cr, std::max(0.5, f->width * scale));
        cairo_stroke_preserve(cr);
    }
    cairo_set_source_rgb(cr, col.r, col.g, col.b);
    cairo_fill(cr);
}

// Same as finishFill but for ordinary bands (thicker outline, square joins).
static void fillFimb(cairo_t* cr, const Color& col, const Fimbriation* f,
                     double m) {
    if (f && f->enabled) {
        cairo_set_source_rgb(cr, f->r, f->g, f->b);
        cairo_set_line_width(cr, std::max(1.0, m * f->width));
        cairo_set_line_join(cr, CAIRO_LINE_JOIN_MITER);
        cairo_stroke_preserve(cr);
    }
    cairo_set_source_rgb(cr, col.r, col.g, col.b);
    cairo_fill(cr);
}

// Fills a band of width `bandWidth` between two points (thick diagonal line).
static void fillBand(cairo_t* cr, double x1, double y1, double x2, double y2,
                     double bandWidth, const Color& col, const Fimbriation* f,
                     double m) {
    double dx = x2 - x1, dy = y2 - y1;
    double len = std::hypot(dx, dy);
    if (len <= 0.0) return;
    double nx = -dy / len * bandWidth;
    double ny = dx / len * bandWidth;
    cairo_move_to(cr, x1 + nx, y1 + ny);
    cairo_line_to(cr, x1 - nx, y1 - ny);
    cairo_line_to(cr, x2 - nx, y2 - ny);
    cairo_line_to(cr, x2 + nx, y2 + ny);
    cairo_close_path(cr);
    fillFimb(cr, col, f, m);
}

void drawField(cairo_t* cr, double w, double h, const Color& c) {
    setColor(cr, c);
    cairo_rectangle(cr, 0, 0, w, h);
    cairo_fill(cr);
}

void drawDivision(cairo_t* cr, const DivisionConfig& d,
                  double w, double h, const Color& c, const Alignment& a) {
    (void)a;
    setColor(cr, c);
    const std::string& r = d.render;

    if (r == "horizontal_split") {
        cairo_rectangle(cr, 0, h / 2, w, h / 2);
        cairo_fill(cr);
    } else if (r == "vertical_split") {
        cairo_rectangle(cr, w / 2, 0, w / 2, h);
        cairo_fill(cr);
    } else if (r == "diagonal_bend") {
        cairo_move_to(cr, 0, 0);
        cairo_line_to(cr, w, h);
        cairo_line_to(cr, w, 0);
        cairo_close_path(cr);
        cairo_fill(cr);
    } else if (r == "diagonal_bend_sinister") {
        cairo_move_to(cr, 0, 0);
        cairo_line_to(cr, w, 0);
        cairo_line_to(cr, 0, h);
        cairo_close_path(cr);
        cairo_fill(cr);
    } else if (r == "diagonal_cross") {
        cairo_move_to(cr, 0, 0);
        cairo_line_to(cr, w, 0);
        cairo_line_to(cr, w / 2, h / 2);
        cairo_close_path(cr);
        cairo_fill(cr);
        cairo_move_to(cr, 0, h);
        cairo_line_to(cr, w, h);
        cairo_line_to(cr, w / 2, h / 2);
        cairo_close_path(cr);
        cairo_fill(cr);
    } else if (r == "quarterly") {
        cairo_rectangle(cr, 0, 0, w / 2, h / 2);
        cairo_fill(cr);
        cairo_rectangle(cr, w / 2, h / 2, w / 2, h / 2);
        cairo_fill(cr);
    } else if (r == "chevron_division") {
        cairo_move_to(cr, 0, h);
        cairo_line_to(cr, 0, h * 0.25);
        cairo_line_to(cr, w / 2, h / 2);
        cairo_line_to(cr, w, h * 0.25);
        cairo_line_to(cr, w, h);
        cairo_close_path(cr);
        cairo_fill(cr);
    } else if (r == "chevron_division_inverted") {
        cairo_move_to(cr, 0, 0);
        cairo_line_to(cr, 0, h * 0.75);
        cairo_line_to(cr, w / 2, h / 2);
        cairo_line_to(cr, w, h * 0.75);
        cairo_line_to(cr, w, 0);
        cairo_close_path(cr);
        cairo_fill(cr);
    } else if (r == "barry") {
        const int n = std::max(1, d.stripes);
        const double bh = h / n;
        for (int i = 0; i < n; ++i) {
            if (i % 2 == 0) {
                cairo_rectangle(cr, 0, i * bh, w, bh);
                cairo_fill(cr);
            }
        }
    } else if (r == "paly") {
        const int n = std::max(1, d.stripes);
        const double bw = w / n;
        for (int i = 0; i < n; ++i) {
            if (i % 2 == 0) {
                cairo_rectangle(cr, i * bw, 0, bw, h);
                cairo_fill(cr);
            }
        }
    } else if (r == "bendy") {
        const int n = std::max(1, d.stripes);
        const double step = 2.0 * h / n;
        for (int k = 0; k < n; ++k) {
            if (k % 2 == 0) {
                const double c0 = -h + k * step;
                const double c1 = c0 + step;
                cairo_move_to(cr, 0, c0);
                cairo_line_to(cr, 0, c1);
                cairo_line_to(cr, w, c1 + h);
                cairo_line_to(cr, w, c0 + h);
                cairo_close_path(cr);
                cairo_fill(cr);
            }
        }
    } else if (r == "gyronny") {
        const int n = std::max(4, d.stripes);
        const double R = std::hypot(w, h);
        for (int i = 0; i < n; ++i) {
            if (i % 2 == 0) {
                const double a0 = i * 2.0 * M_PI / n;
                const double a1 = (i + 1) * 2.0 * M_PI / n;
                cairo_move_to(cr, w / 2, h / 2);
                cairo_arc(cr, w / 2, h / 2, R, a0, a1);
                cairo_close_path(cr);
                cairo_fill(cr);
            }
        }
    }
}

void drawOrdinary(cairo_t* cr, const OrdinaryConfig& o,
                  double w, double h, const Color& c, const Alignment& a,
                  const Fimbriation* fimb) {
    setColor(cr, c);
    const std::string& r = o.render;
    if (r == "none") return;
    const double th = o.thickness;
    const double m = std::min(w, h);
    const double fw = (fimb && fimb->enabled) ? m * fimb->width : 0.0;

    if (r == "fess") {
        const double t = h * th;
        cairo_rectangle(cr, 0, a.y - t / 2, w, t);
        fillFimb(cr, c, fimb, m);
    } else if (r == "chief") {
        cairo_rectangle(cr, 0, 0, w, h * th);
        fillFimb(cr, c, fimb, m);
    } else if (r == "base") {
        cairo_rectangle(cr, 0, h - h * th, w, h * th);
        fillFimb(cr, c, fimb, m);
    } else if (r == "pile") {
        cairo_move_to(cr, 0, 0);
        cairo_line_to(cr, w, 0);
        cairo_line_to(cr, w / 2, h * 0.55);
        cairo_close_path(cr);
        fillFimb(cr, c, fimb, m);
    } else if (r == "pale") {
        const double t = w * th;
        cairo_rectangle(cr, a.x - t / 2, 0, t, h);
        fillFimb(cr, c, fimb, m);
    } else if (r == "pale_dexter") {
        const double t = w * th;
        cairo_rectangle(cr, a.x - t - w * 0.15, 0, t, h);
        fillFimb(cr, c, fimb, m);
    } else if (r == "pale_sinister") {
        const double t = w * th;
        cairo_rectangle(cr, a.x + w * 0.15, 0, t, h);
        fillFimb(cr, c, fimb, m);
    } else if (r == "bend") {
        fillBand(cr, a.x - w, a.y - h, a.x + w, a.y + h, m * th, c, fimb, m);
    } else if (r == "bend_sinister") {
        fillBand(cr, a.x - w, a.y + h, a.x + w, a.y - h, m * th, c, fimb, m);
    } else if (r == "cross") {
        const double thh = h * th, thw = w * th;
        cairo_rectangle(cr, 0, a.y - thh / 2, w, thh);
        fillFimb(cr, c, fimb, m);
        cairo_rectangle(cr, a.x - thw / 2, 0, thw, h);
        fillFimb(cr, c, fimb, m);
    } else if (r == "nordic") {
        const double thh = h * th, thw = w * th;
        cairo_rectangle(cr, 0, a.y - thh / 2, w, thh);
        fillFimb(cr, c, fimb, m);
        cairo_rectangle(cr, a.x - thw / 2, 0, thw, h);
        fillFimb(cr, c, fimb, m);
    } else if (r == "saltire") {
        const double bw = m * th;
        fillBand(cr, a.x - w, a.y - h, a.x + w, a.y + h, bw, c, fimb, m);
        fillBand(cr, a.x - w, a.y + h, a.x + w, a.y - h, bw, c, fimb, m);
    } else if (r == "chevron") {
        // True chevron band: two arm quads of perpendicular width `bw`
        // rising from the base corners to the apex point. The trough
        // between the arms stays empty.
        const double bw = h * th;
        const double axp = a.x, ayp = a.y;
        // Left arm: from the bottom-left corner to the apex.
        {
            double ux = axp, uy = ayp - h;
            const double len = std::hypot(ux, uy);
            ux /= len;
            uy /= len;
            const double nx = uy, ny = -ux; // up-left normal
            const double bx = nx * bw, by = ny * bw;
            cairo_move_to(cr, bx, h + by);
            cairo_line_to(cr, axp + bx, ayp + by);
            cairo_line_to(cr, axp - bx, ayp - by);
            cairo_line_to(cr, -bx, h - by);
            cairo_close_path(cr);
            fillFimb(cr, c, fimb, m);
        }
        // Right arm: from the bottom-right corner to the apex.
        {
            double ux = axp - w, uy = ayp - h;
            const double len = std::hypot(ux, uy);
            ux /= len;
            uy /= len;
            const double nx = -uy, ny = ux; // up-right normal
            const double bx = nx * bw, by = ny * bw;
            cairo_move_to(cr, w + bx, h + by);
            cairo_line_to(cr, axp + bx, ayp + by);
            cairo_line_to(cr, axp - bx, ayp - by);
            cairo_line_to(cr, w - bx, h - by);
            cairo_close_path(cr);
            fillFimb(cr, c, fimb, m);
        }
        // Cap the apex: the two arm quads meet exactly at the apex point,
        // which leaves a sub-pixel notch on the tip. A circle of the band
        // half-width rounds the point and keeps it solid.
        cairo_arc(cr, axp, ayp, bw / 2, 0.0, 2.0 * 3.14159265358979323846);
        cairo_fill(cr);
    } else if (r == "pall") {
        const double t = m * th;
        cairo_set_line_cap(cr, CAIRO_LINE_CAP_SQUARE);
        cairo_set_line_join(cr, CAIRO_LINE_JOIN_MITER);
        cairo_move_to(cr, a.x, a.y);
        cairo_line_to(cr, a.x, h);
        cairo_move_to(cr, a.x, a.y);
        cairo_line_to(cr, 0, h * 0.45);
        cairo_move_to(cr, a.x, a.y);
        cairo_line_to(cr, w, h * 0.45);
        if (fw > 0.0) {
            setColor(cr, {fimb->r, fimb->g, fimb->b});
            cairo_set_line_width(cr, t + 2 * fw);
            cairo_stroke_preserve(cr);
        }
        setColor(cr, c);
        cairo_set_line_width(cr, t);
        cairo_stroke(cr);
    } else if (r == "bordure") {
        const double in = m * th;
        cairo_set_fill_rule(cr, CAIRO_FILL_RULE_EVEN_ODD);
        cairo_rectangle(cr, 0, 0, w, h);
        cairo_rectangle(cr, in, in, w - 2 * in, h - 2 * in);
        fillFimb(cr, c, fimb, m);
    } else if (r == "orle") {
        const double in = m * 0.2;
        const double t = m * th;
        cairo_set_fill_rule(cr, CAIRO_FILL_RULE_EVEN_ODD);
        cairo_rectangle(cr, in, in, w - 2 * in, h - 2 * in);
        cairo_rectangle(cr, in + t, in + t, w - 2 * in - 2 * t, h - 2 * in - 2 * t);
        fillFimb(cr, c, fimb, m);
    } else if (r == "canton") {
        const double s = m * o.size;
        cairo_rectangle(cr, 0, a.y - s, s, s);
        fillFimb(cr, c, fimb, m);
    } else if (r == "lozenge") {
        const double d = m * o.size;
        cairo_move_to(cr, a.x, a.y - d);
        cairo_line_to(cr, a.x + d, a.y);
        cairo_line_to(cr, a.x, a.y + d);
        cairo_line_to(cr, a.x - d, a.y);
        cairo_close_path(cr);
        fillFimb(cr, c, fimb, m);
    } else if (r == "lozenge_throughout") {
        cairo_move_to(cr, w / 2, 0);
        cairo_line_to(cr, w, h / 2);
        cairo_line_to(cr, w / 2, h);
        cairo_line_to(cr, 0, h / 2);
        cairo_close_path(cr);
        fillFimb(cr, c, fimb, m);
    }
}

double chargeBaseRadius(const ChargeConfig& ch, double w, double h) {
    const double m = std::min(w, h);
    if (ch.render == "cross_patee") return 0.42 * m;
    return m * ch.outerRadius;
}

void drawCharge(cairo_t* cr, const ChargeConfig& ch,
                double w, double h, const Color& c, const Alignment& a,
                double scale, const Fimbriation& fimb) {
    const std::string& r = ch.render;
    const double m = std::min(w, h);
    // Hollow shapes already read well on busy grounds; skip fimbriation.
    const bool canFimb = r != "crescent" && r != "annulet";
    const Fimbriation* f = canFimb ? &fimb : nullptr;

    if (r == "star") {
        const int n = std::max(3, ch.points);
        const double R = m * ch.outerRadius * scale;
        const double ri = m * ch.innerRadius * scale;
        for (int i = 0; i < 2 * n; ++i) {
            const double ang = i * M_PI / n;
            const double rad = (i % 2 == 0) ? R : ri;
            const double x = a.x + rad * std::cos(ang);
            const double y = a.y + rad * std::sin(ang);
            if (i == 0) cairo_move_to(cr, x, y);
            else cairo_line_to(cr, x, y);
        }
        cairo_close_path(cr);
        finishFill(cr, c, f, scale);
    } else if (r == "crescent") {
        const double R = m * ch.outerRadius * scale;
        const double ri = m * ch.innerRadius * scale;
        const double off = m * ch.offsetX * scale;
        cairo_new_path(cr);
        cairo_arc(cr, a.x, a.y, R, 0, 2 * M_PI);
        cairo_close_path(cr);
        cairo_arc(cr, a.x + off, a.y, ri, 0, 2 * M_PI);
        cairo_close_path(cr);
        cairo_set_fill_rule(cr, CAIRO_FILL_RULE_EVEN_ODD);
        finishFill(cr, c, nullptr, scale);
    } else if (r == "annulet") {
        const double R = m * ch.outerRadius * scale;
        const double ri = m * ch.innerRadius * scale;
        cairo_new_path(cr);
        cairo_arc(cr, a.x, a.y, R, 0, 2 * M_PI);
        cairo_close_path(cr);
        cairo_arc(cr, a.x, a.y, ri, 0, 2 * M_PI);
        cairo_close_path(cr);
        cairo_set_fill_rule(cr, CAIRO_FILL_RULE_EVEN_ODD);
        finishFill(cr, c, nullptr, scale);
    } else if (r == "circle") {
        cairo_arc(cr, a.x, a.y, m * ch.outerRadius * scale, 0, 2 * M_PI);
        finishFill(cr, c, f, scale);
    } else if (r == "sun") {
        const double R = m * ch.outerRadius * scale;
        const double rd = R * 0.72;
        const int n = 16;
        cairo_new_path(cr);
        for (int i = 0; i < n; ++i) {
            const double a0 = i * 2.0 * M_PI / n;
            const double a1 = (i + 1) * 2.0 * M_PI / n;
            const double mid = (a0 + a1) / 2.0;
            cairo_move_to(cr, a.x + rd * std::cos(a0), a.y + rd * std::sin(a0));
            cairo_line_to(cr, a.x + R * std::cos(mid), a.y + R * std::sin(mid));
            cairo_line_to(cr, a.x + rd * std::cos(a1), a.y + rd * std::sin(a1));
            cairo_close_path(cr);
        }
        cairo_arc(cr, a.x, a.y, rd, 0, 2 * M_PI);
        finishFill(cr, c, f, scale);
    } else if (r == "polygon") {
        const int n = std::max(3, ch.points);
        const double R = m * ch.outerRadius * scale;
        for (int i = 0; i < n; ++i) {
            const double ang = -M_PI / 2 + i * 2.0 * M_PI / n;
            const double x = a.x + R * std::cos(ang);
            const double y = a.y + R * std::sin(ang);
            if (i == 0) cairo_move_to(cr, x, y);
            else cairo_line_to(cr, x, y);
        }
        cairo_close_path(cr);
        finishFill(cr, c, f, scale);
    } else if (r == "square") {
        const double s = m * ch.outerRadius * scale;
        cairo_rectangle(cr, a.x - s, a.y - s, 2 * s, 2 * s);
        finishFill(cr, c, f, scale);
    } else if (r == "diamond") {
        const double d = m * ch.outerRadius * scale;
        cairo_move_to(cr, a.x, a.y - d);
        cairo_line_to(cr, a.x + d, a.y);
        cairo_line_to(cr, a.x, a.y + d);
        cairo_line_to(cr, a.x - d, a.y);
        cairo_close_path(cr);
        finishFill(cr, c, f, scale);
    } else if (r == "cross_patee") {
        const double s = m * 0.42 * scale;
        const double n = m * 0.14 * scale;
        const double e = m * 0.52 * scale;
        cairo_move_to(cr, a.x - e / 2, a.y - s);
        cairo_line_to(cr, a.x + e / 2, a.y - s);
        cairo_line_to(cr, a.x + e / 2, a.y - n);
        cairo_line_to(cr, a.x + s, a.y - n);
        cairo_line_to(cr, a.x + s, a.y + e / 2);
        cairo_line_to(cr, a.x + n, a.y + e / 2);
        cairo_line_to(cr, a.x + n, a.y + s);
        cairo_line_to(cr, a.x - n, a.y + s);
        cairo_line_to(cr, a.x - n, a.y + e / 2);
        cairo_line_to(cr, a.x - s, a.y + e / 2);
        cairo_line_to(cr, a.x - s, a.y - n);
        cairo_line_to(cr, a.x - e / 2, a.y - n);
        cairo_close_path(cr);
        finishFill(cr, c, f, scale);
    } else if (r == "trefoil") {
        const double R = m * ch.outerRadius * scale;
        // Three lobes meeting at the centre (heraldic clover).
        const double lobe = R * 0.42;
        cairo_new_path(cr);
        for (int i = 0; i < 3; ++i) {
            const double ang = i * 2.0 * 3.14159265358979323846 / 3.0 - 3.14159265358979323846 / 2.0;
            cairo_arc(cr, a.x + R * 0.42 * std::cos(ang),
                      a.y + R * 0.42 * std::sin(ang), lobe, 0, 2.0 * 3.14159265358979323846);
            cairo_close_path(cr);
        }
        cairo_rectangle(cr, a.x - R * 0.05, a.y + R * 0.40, R * 0.10, R * 0.50);
        finishFill(cr, c, f, scale);
    } else if (r == "fleur_de_lis") {
        const double R = m * ch.outerRadius * scale;
        cairo_new_path(cr);
        // Central pointed petal.
        cairo_move_to(cr, a.x, a.y + R * 0.46);
        cairo_curve_to(cr, a.x + R * 0.26, a.y + R * 0.18,
                       a.x + R * 0.24, a.y - R * 0.42,
                       a.x, a.y - R * 0.98);
        cairo_curve_to(cr, a.x - R * 0.24, a.y - R * 0.42,
                       a.x - R * 0.26, a.y + R * 0.18,
                       a.x, a.y + R * 0.46);
        cairo_close_path(cr);
        // Two curling side petals.
        for (int side = -1; side <= 1; side += 2) {
            cairo_move_to(cr, a.x + side * R * 0.20, a.y + R * 0.30);
            cairo_curve_to(cr, a.x + side * R * 0.55, a.y + R * 0.42,
                           a.x + side * R * 0.82, a.y + R * 0.02,
                           a.x + side * R * 0.60, a.y - R * 0.34);
            cairo_curve_to(cr, a.x + side * R * 0.50, a.y - R * 0.56,
                           a.x + side * R * 0.32, a.y - R * 0.50,
                           a.x + side * R * 0.30, a.y - R * 0.30);
            cairo_close_path(cr);
        }
        // Base bar with a central drop.
        cairo_rectangle(cr, a.x - R * 0.52, a.y + R * 0.50, R * 1.04, R * 0.24);
        cairo_rectangle(cr, a.x - R * 0.12, a.y + R * 0.74, R * 0.24, R * 0.24);
        finishFill(cr, c, f, scale);
    }
}

} // namespace flaggen
