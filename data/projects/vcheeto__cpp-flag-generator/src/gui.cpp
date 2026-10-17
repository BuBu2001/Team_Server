#include <cairo.h>
#include <cairo-pdf.h>
#include <cairo-svg.h>
#include <gtk/gtk.h>

#include <algorithm>
#include <chrono>
#include <cstring>
#include <filesystem>
#include <random>
#include <string>
#include <vector>

#include "config.h"
#include "generator.h"

namespace fs = std::filesystem;

struct Stroke {
    double r = 1.0, g = 1.0, b = 1.0;
    double width = 6.0;
    std::vector<std::pair<double, double>> pts;
};

struct App {
    flaggen::Config cfg;
    flaggen::GenerationResult current;
    int width = 1200;
    int height = 800;

    GtkWidget* window = nullptr;
    GtkWidget* drawing = nullptr;
    GtkWidget* seedCheck = nullptr;
    GtkWidget* seedSpin = nullptr;
    GtkWidget* infoLabel = nullptr;

    GtkWidget* divCombo = nullptr;
    GtkWidget* ordCombo = nullptr;
    GtkWidget* chargeCombo = nullptr;
    GtkWidget* posCombo = nullptr;

    GtkWidget* brushToggle = nullptr;
    GtkWidget* colorButton = nullptr;
    GtkWidget* widthSpin = nullptr;

    bool hasFlag = false;
    bool hasTransform = false;
    double s = 1.0, ox = 0.0, oy = 0.0;

    std::vector<Stroke> strokes;
    int activeStroke = -1;
};

static const char* kRandomId = "__random__";

static unsigned randomSeed() {
    return static_cast<unsigned>(
        std::chrono::system_clock::now().time_since_epoch().count());
}

static const char* positionName(flaggen::ChargePosition pos) {
    switch (pos) {
        case flaggen::ChargePosition::Center: return "center";
        case flaggen::ChargePosition::Canton: return "canton";
        default: return "corner";
    }
}

static flaggen::ChargePosition positionFromId(const char* id) {
    if (std::strcmp(id, "center") == 0) return flaggen::ChargePosition::Center;
    if (std::strcmp(id, "canton") == 0) return flaggen::ChargePosition::Canton;
    return flaggen::ChargePosition::Corner;
}

static const flaggen::DivisionConfig* findDivision(const flaggen::Config& cfg,
                                                   const char* id) {
    for (const auto& d : cfg.divisions) if (d.id == id) return &d;
    return nullptr;
}
static const flaggen::OrdinaryConfig* findOrdinary(const flaggen::Config& cfg,
                                                   const char* id) {
    for (const auto& o : cfg.ordinaries) if (o.id == id) return &o;
    return nullptr;
}
static const flaggen::ChargeConfig* findCharge(const flaggen::Config& cfg,
                                               const char* id) {
    for (const auto& c : cfg.charges) if (c.id == id) return &c;
    return nullptr;
}

static void updateInfo(App* app, unsigned seed) {
    const flaggen::GenerationResult& g = app->current;
    std::string txt = "seed=" + std::to_string(seed) +
                      "   division=" + g.division.id +
                      "   ordinary=" + g.ordinary.id +
                      "   charge=" + g.charge.id +
                      " (" + positionName(g.chargePosition) + ")" +
                      "   palette=" + flaggen::colorToHex(g.palette.a) +
                      " " + flaggen::colorToHex(g.palette.b) +
                      " " + flaggen::colorToHex(g.palette.c) +
                      "   contrast=" + std::to_string(g.chargeMinContrast);
    gtk_label_set_text(GTK_LABEL(app->infoLabel), txt.c_str());
}

static void generateRandom(App* app) {
    const bool useSeed = gtk_toggle_button_get_active(GTK_TOGGLE_BUTTON(app->seedCheck));
    const unsigned seed = useSeed
        ? static_cast<unsigned>(gtk_spin_button_get_value(GTK_SPIN_BUTTON(app->seedSpin)))
        : randomSeed();
    gtk_spin_button_set_value(GTK_SPIN_BUTTON(app->seedSpin), static_cast<double>(seed));

    std::mt19937 rng(seed);
    app->current = flaggen::generateFlag(app->cfg, rng, app->width, app->height);
    app->hasFlag = true;
    gtk_combo_box_set_active(GTK_COMBO_BOX(app->divCombo), 0);
    gtk_combo_box_set_active(GTK_COMBO_BOX(app->ordCombo), 0);
    gtk_combo_box_set_active(GTK_COMBO_BOX(app->chargeCombo), 0);
    gtk_combo_box_set_active(GTK_COMBO_BOX(app->posCombo), 0);
    gtk_widget_queue_draw(app->drawing);
    updateInfo(app, seed);
}

static void applyConstructor(App* app) {
    const bool useSeed = gtk_toggle_button_get_active(GTK_TOGGLE_BUTTON(app->seedCheck));
    const unsigned seed = useSeed
        ? static_cast<unsigned>(gtk_spin_button_get_value(GTK_SPIN_BUTTON(app->seedSpin)))
        : randomSeed();
    gtk_spin_button_set_value(GTK_SPIN_BUTTON(app->seedSpin), static_cast<double>(seed));

    std::mt19937 rng(seed);
    app->current = flaggen::generateFlag(app->cfg, rng, app->width, app->height);

    const char* dsel = gtk_combo_box_get_active_id(GTK_COMBO_BOX(app->divCombo));
    if (dsel && std::strcmp(dsel, kRandomId) != 0) {
        if (const auto* d = findDivision(app->cfg, dsel)) app->current.division = *d;
    }
    const char* osel = gtk_combo_box_get_active_id(GTK_COMBO_BOX(app->ordCombo));
    if (osel && std::strcmp(osel, kRandomId) != 0) {
        if (const auto* o = findOrdinary(app->cfg, osel)) app->current.ordinary = *o;
    }
    const char* csel = gtk_combo_box_get_active_id(GTK_COMBO_BOX(app->chargeCombo));
    if (csel && std::strcmp(csel, kRandomId) != 0) {
        if (const auto* c = findCharge(app->cfg, csel)) app->current.charge = *c;
    }
    const char* psel = gtk_combo_box_get_active_id(GTK_COMBO_BOX(app->posCombo));
    if (psel && std::strcmp(psel, kRandomId) != 0) {
        app->current.chargePosition = positionFromId(psel);
    }
    flaggen::resolveCharge(app->current, rng, app->width, app->height);

    app->hasFlag = true;
    gtk_widget_queue_draw(app->drawing);
    updateInfo(app, seed);
}

static void renderStrokes(cairo_t* cr, App* app) {
    cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND);
    cairo_set_line_join(cr, CAIRO_LINE_JOIN_ROUND);
    for (const auto& st : app->strokes) {
        cairo_set_source_rgb(cr, st.r, st.g, st.b);
        cairo_set_line_width(cr, st.width);
        if (st.pts.size() == 1) {
            cairo_arc(cr, st.pts[0].first, st.pts[0].second, st.width / 2.0, 0, 2 * M_PI);
            cairo_fill(cr);
            continue;
        }
        if (st.pts.size() < 2) continue;
        cairo_move_to(cr, st.pts[0].first, st.pts[0].second);
        for (size_t i = 1; i < st.pts.size(); ++i) {
            cairo_line_to(cr, st.pts[i].first, st.pts[i].second);
        }
        cairo_stroke(cr);
    }
}

static gboolean onDraw(GtkWidget* widget, cairo_t* cr, gpointer data) {
    App* app = static_cast<App*>(data);
    GtkAllocation alloc;
    gtk_widget_get_allocation(widget, &alloc);

    cairo_set_source_rgb(cr, 0.14, 0.14, 0.17);
    cairo_paint(cr);

    if (!app->hasFlag) return FALSE;

    const double sx = static_cast<double>(alloc.width) / app->width;
    const double sy = static_cast<double>(alloc.height) / app->height;
    const double s = std::min(sx, sy);
    const double ox = (alloc.width - app->width * s) / 2.0;
    const double oy = (alloc.height - app->height * s) / 2.0;
    app->s = s;
    app->ox = ox;
    app->oy = oy;
    app->hasTransform = true;

    cairo_translate(cr, ox, oy);
    cairo_scale(cr, s, s);
    flaggen::renderFlag(cr, app->current, app->width, app->height);
    renderStrokes(cr, app);
    return FALSE;
}

static bool widgetToFlag(App* app, double wx, double wy, double& fx, double& fy) {
    if (!app->hasFlag || !app->hasTransform) return false;
    fx = (wx - app->ox) / app->s;
    fy = (wy - app->oy) / app->s;
    return true;
}

static gboolean onPress(GtkWidget*, GdkEventButton* ev, gpointer data) {
    App* app = static_cast<App*>(data);
    if (!gtk_toggle_button_get_active(GTK_TOGGLE_BUTTON(app->brushToggle))) return FALSE;
    if (ev->button != 1) return FALSE;

    double fx = 0, fy = 0;
    if (!widgetToFlag(app, ev->x, ev->y, fx, fy)) return FALSE;

    GdkRGBA col;
    gtk_color_chooser_get_rgba(GTK_COLOR_CHOOSER(app->colorButton), &col);

    Stroke st;
    st.r = col.red;
    st.g = col.green;
    st.b = col.blue;
    st.width = gtk_spin_button_get_value(GTK_SPIN_BUTTON(app->widthSpin));
    st.pts.emplace_back(fx, fy);
    app->strokes.push_back(std::move(st));
    app->activeStroke = static_cast<int>(app->strokes.size()) - 1;
    gtk_widget_queue_draw(app->drawing);
    return TRUE;
}

static gboolean onMotion(GtkWidget*, GdkEventMotion* ev, gpointer data) {
    App* app = static_cast<App*>(data);
    if (app->activeStroke < 0) return FALSE;
    if (!(ev->state & GDK_BUTTON1_MASK)) return FALSE;

    double fx = 0, fy = 0;
    if (!widgetToFlag(app, ev->x, ev->y, fx, fy)) return FALSE;
    auto& st = app->strokes[static_cast<size_t>(app->activeStroke)];
    st.pts.emplace_back(fx, fy);
    gtk_widget_queue_draw(app->drawing);
    return TRUE;
}

static gboolean onRelease(GtkWidget*, GdkEventButton*, gpointer data) {
    App* app = static_cast<App*>(data);
    app->activeStroke = -1;
    return FALSE;
}

static bool exportFlag(App* app, const std::string& path) {
    const std::string ext = fs::path(path).extension().string();
    cairo_surface_t* surface = nullptr;
    if (ext == ".svg") surface = cairo_svg_surface_create(path.c_str(), app->width, app->height);
    else if (ext == ".pdf") surface = cairo_pdf_surface_create(path.c_str(), app->width, app->height);
    else surface = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, app->width, app->height);

    if (!surface || cairo_surface_status(surface) != CAIRO_STATUS_SUCCESS) return false;

    cairo_t* cr = cairo_create(surface);
    flaggen::renderFlag(cr, app->current, app->width, app->height);
    renderStrokes(cr, app);
    cairo_destroy(cr);

    bool ok = true;
    if (ext == ".svg" || ext == ".pdf") {
        cairo_surface_finish(surface);
    } else {
        ok = cairo_surface_write_to_png(surface, path.c_str()) == CAIRO_STATUS_SUCCESS;
    }
    cairo_surface_destroy(surface);
    return ok;
}

static void onGenerate(GtkWidget*, gpointer data) { generateRandom(static_cast<App*>(data)); }
static void onApply(GtkWidget*, gpointer data) { applyConstructor(static_cast<App*>(data)); }

static void onClearStrokes(GtkWidget*, gpointer data) {
    App* app = static_cast<App*>(data);
    app->strokes.clear();
    app->activeStroke = -1;
    gtk_widget_queue_draw(app->drawing);
}

static void onBrushToggled(GtkWidget*, gpointer data) {
    App* app = static_cast<App*>(data);
    GdkWindow* win = gtk_widget_get_window(app->drawing);
    if (win) {
        const bool active = gtk_toggle_button_get_active(GTK_TOGGLE_BUTTON(app->brushToggle));
        GdkCursorType type = active ? GDK_CROSSHAIR : GDK_LEFT_PTR;
        GdkCursor* cur = gdk_cursor_new_for_display(gdk_display_get_default(), type);
        gdk_window_set_cursor(win, cur);
        g_object_unref(cur);
    }
}

static void onSave(GtkWidget*, gpointer data) {
    App* app = static_cast<App*>(data);
    if (!app->hasFlag) return;

    GtkWidget* dialog = gtk_file_chooser_dialog_new(
        "Сохранить флаг", GTK_WINDOW(app->window),
        GTK_FILE_CHOOSER_ACTION_SAVE,
        "_Отмена", GTK_RESPONSE_CANCEL,
        "_Сохранить", GTK_RESPONSE_ACCEPT, nullptr);
    gtk_file_chooser_set_current_name(GTK_FILE_CHOOSER(dialog), "flag.png");

    GtkFileFilter* f = gtk_file_filter_new();
    gtk_file_filter_set_name(f, "Изображения (PNG / SVG / PDF)");
    gtk_file_filter_add_pattern(f, "*.png");
    gtk_file_filter_add_pattern(f, "*.svg");
    gtk_file_filter_add_pattern(f, "*.pdf");
    gtk_file_chooser_add_filter(GTK_FILE_CHOOSER(dialog), f);

    if (gtk_dialog_run(GTK_DIALOG(dialog)) == GTK_RESPONSE_ACCEPT) {
        gchar* path = gtk_file_chooser_get_filename(GTK_FILE_CHOOSER(dialog));
        if (path) {
            gtk_label_set_text(GTK_LABEL(app->infoLabel),
                               exportFlag(app, path)
                                   ? ("Сохранено: " + std::string(path)).c_str()
                                   : ("Ошибка сохранения: " + std::string(path)).c_str());
            g_free(path);
        }
    }
    gtk_widget_destroy(dialog);
}

static GtkWidget* headerLabel(const char* text) {
    GtkWidget* l = gtk_label_new(nullptr);
    gtk_label_set_markup(GTK_LABEL(l), (std::string("<b>") + text + "</b>").c_str());
    gtk_label_set_xalign(GTK_LABEL(l), 0.0);
    return l;
}

static GtkWidget* comboFromList(App* app) {
    GtkWidget* cb = gtk_combo_box_text_new();
    gtk_combo_box_text_append(GTK_COMBO_BOX_TEXT(cb), kRandomId, "Случайное");
    return cb;
}

int main(int argc, char** argv) {
    gtk_init(&argc, &argv);

    App app;
    try {
        app.cfg = flaggen::loadConfig("config/flags.json");
    } catch (const std::exception& e) {
        g_printerr("Failed to load config: %s\n", e.what());
        return 1;
    }

    app.window = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    gtk_window_set_title(GTK_WINDOW(app.window), "Flag Generator — конструктор");
    gtk_window_set_default_size(GTK_WINDOW(app.window), 1180, 680);
    g_signal_connect(app.window, "destroy", G_CALLBACK(gtk_main_quit), nullptr);

    GtkWidget* mainBox = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    gtk_container_set_border_width(GTK_CONTAINER(mainBox), 8);
    gtk_container_add(GTK_CONTAINER(app.window), mainBox);

    // ---- Left control panel ----
    GtkWidget* panel = gtk_box_new(GTK_ORIENTATION_VERTICAL, 6);
    gtk_widget_set_size_request(panel, 280, -1);
    gtk_box_pack_start(GTK_BOX(mainBox), panel, FALSE, FALSE, 0);

    gtk_box_pack_start(GTK_BOX(panel), headerLabel("Генерация"), FALSE, FALSE, 0);

    GtkWidget* genBtn = gtk_button_new_with_label("Случайный флаг");
    g_signal_connect(genBtn, "clicked", G_CALLBACK(onGenerate), &app);
    gtk_box_pack_start(GTK_BOX(panel), genBtn, FALSE, FALSE, 0);

    GtkWidget* seedRow = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
    gtk_box_pack_start(GTK_BOX(panel), seedRow, FALSE, FALSE, 0);
    app.seedCheck = gtk_check_button_new_with_label("Сид:");
    gtk_box_pack_start(GTK_BOX(seedRow), app.seedCheck, FALSE, FALSE, 0);
    app.seedSpin = gtk_spin_button_new_with_range(0, 0x7FFFFFFF, 1);
    gtk_spin_button_set_value(GTK_SPIN_BUTTON(app.seedSpin), 1);
    gtk_box_pack_start(GTK_BOX(seedRow), app.seedSpin, TRUE, TRUE, 0);

    gtk_box_pack_start(GTK_BOX(panel), headerLabel("Конструктор"), FALSE, FALSE, 0);

    app.divCombo = comboFromList(&app);
    for (const auto& d : app.cfg.divisions)
        gtk_combo_box_text_append(GTK_COMBO_BOX_TEXT(app.divCombo), d.id.c_str(), d.id.c_str());
    gtk_combo_box_set_active(GTK_COMBO_BOX(app.divCombo), 0);
    gtk_box_pack_start(GTK_BOX(panel), app.divCombo, FALSE, FALSE, 0);

    app.ordCombo = comboFromList(&app);
    for (const auto& o : app.cfg.ordinaries)
        gtk_combo_box_text_append(GTK_COMBO_BOX_TEXT(app.ordCombo), o.id.c_str(), o.id.c_str());
    gtk_combo_box_set_active(GTK_COMBO_BOX(app.ordCombo), 0);
    gtk_box_pack_start(GTK_BOX(panel), app.ordCombo, FALSE, FALSE, 0);

    app.chargeCombo = comboFromList(&app);
    for (const auto& c : app.cfg.charges)
        gtk_combo_box_text_append(GTK_COMBO_BOX_TEXT(app.chargeCombo), c.id.c_str(), c.id.c_str());
    gtk_combo_box_set_active(GTK_COMBO_BOX(app.chargeCombo), 0);
    gtk_box_pack_start(GTK_BOX(panel), app.chargeCombo, FALSE, FALSE, 0);

    app.posCombo = comboFromList(&app);
    gtk_combo_box_text_append(GTK_COMBO_BOX_TEXT(app.posCombo), "center", "Центр");
    gtk_combo_box_text_append(GTK_COMBO_BOX_TEXT(app.posCombo), "canton", "Кантон");
    gtk_combo_box_text_append(GTK_COMBO_BOX_TEXT(app.posCombo), "corner", "Угол");
    gtk_combo_box_set_active(GTK_COMBO_BOX(app.posCombo), 0);
    gtk_box_pack_start(GTK_BOX(panel), app.posCombo, FALSE, FALSE, 0);

    GtkWidget* applyBtn = gtk_button_new_with_label("Применить");
    g_signal_connect(applyBtn, "clicked", G_CALLBACK(onApply), &app);
    gtk_box_pack_start(GTK_BOX(panel), applyBtn, FALSE, FALSE, 0);

    gtk_box_pack_start(GTK_BOX(panel), headerLabel("Рисование"), FALSE, FALSE, 0);

    app.brushToggle = gtk_toggle_button_new_with_label("Кисть (рисовать мышью)");
    g_signal_connect(app.brushToggle, "toggled", G_CALLBACK(onBrushToggled), &app);
    gtk_box_pack_start(GTK_BOX(panel), app.brushToggle, FALSE, FALSE, 0);

    GtkWidget* penRow = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
    gtk_box_pack_start(GTK_BOX(panel), penRow, FALSE, FALSE, 0);
    app.colorButton = gtk_color_button_new();
    GdkRGBA white = {1, 1, 1, 1};
    gtk_color_button_set_rgba(GTK_COLOR_BUTTON(app.colorButton), &white);
    gtk_box_pack_start(GTK_BOX(penRow), app.colorButton, FALSE, FALSE, 0);
    app.widthSpin = gtk_spin_button_new_with_range(1, 60, 1);
    gtk_spin_button_set_value(GTK_SPIN_BUTTON(app.widthSpin), 8);
    gtk_box_pack_start(GTK_BOX(penRow), app.widthSpin, TRUE, TRUE, 0);

    GtkWidget* clearBtn = gtk_button_new_with_label("Стереть рисунок");
    g_signal_connect(clearBtn, "clicked", G_CALLBACK(onClearStrokes), &app);
    gtk_box_pack_start(GTK_BOX(panel), clearBtn, FALSE, FALSE, 0);

    GtkWidget* saveBtn = gtk_button_new_with_label("Сохранить…");
    g_signal_connect(saveBtn, "clicked", G_CALLBACK(onSave), &app);
    gtk_box_pack_start(GTK_BOX(panel), saveBtn, FALSE, FALSE, 0);

    app.infoLabel = gtk_label_new("");
    gtk_label_set_ellipsize(GTK_LABEL(app.infoLabel), PANGO_ELLIPSIZE_END);
    gtk_label_set_line_wrap(GTK_LABEL(app.infoLabel), TRUE);
    gtk_label_set_xalign(GTK_LABEL(app.infoLabel), 0.0);
    gtk_box_pack_start(GTK_BOX(panel), app.infoLabel, FALSE, FALSE, 0);

    // ---- Drawing area ----
    app.drawing = gtk_drawing_area_new();
    gtk_widget_set_size_request(app.drawing, 720, 480);
    g_signal_connect(app.drawing, "draw", G_CALLBACK(onDraw), &app);
    gtk_widget_add_events(app.drawing,
                          GDK_BUTTON_PRESS_MASK | GDK_BUTTON_RELEASE_MASK |
                              GDK_POINTER_MOTION_MASK);
    g_signal_connect(app.drawing, "button-press-event", G_CALLBACK(onPress), &app);
    g_signal_connect(app.drawing, "motion-notify-event", G_CALLBACK(onMotion), &app);
    g_signal_connect(app.drawing, "button-release-event", G_CALLBACK(onRelease), &app);
    gtk_box_pack_start(GTK_BOX(mainBox), app.drawing, TRUE, TRUE, 0);

    gtk_widget_show_all(app.window);
    generateRandom(&app);

    gtk_main();
    return 0;
}
