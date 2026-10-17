#include <cairo.h>
#include <cairo-pdf.h>
#include <cairo-svg.h>

#include <chrono>
#include <filesystem>
#include <iostream>
#include <random>
#include <string>

#include "config.h"
#include "generator.h"

namespace fs = std::filesystem;

static void printUsage(const char* prog) {
    std::cout << "Usage: " << prog
              << " [--seed N] [--width W] [--height H] --output <file.png|file.svg|file.pdf>\n";
}

static std::string findConfig() {
    const std::string candidates[] = {
        "config/flags.json",
        "flags.json",
    };
    for (const auto& path : candidates) {
        if (fs::exists(path)) return path;
    }
    return candidates[0];
}

int main(int argc, char** argv) {
    using clock = std::chrono::system_clock;
    unsigned seed = static_cast<unsigned>(clock::now().time_since_epoch().count());
    int width = 1200;
    int height = 800;
    std::string output = "flag.png";

    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        const auto next = [&](const char* name) -> std::string {
            if (i + 1 >= argc) {
                std::cerr << "Missing value for " << name << "\n";
                std::exit(1);
            }
            return argv[++i];
        };

        if (arg == "--seed") seed = static_cast<unsigned>(std::stoul(next("--seed")));
        else if (arg == "--width") width = std::stoi(next("--width"));
        else if (arg == "--height") height = std::stoi(next("--height"));
        else if (arg == "--output") output = next("--output");
        else if (arg == "--help" || arg == "-h") { printUsage(argv[0]); return 0; }
        else {
            std::cerr << "Unknown argument: " << arg << "\n";
            printUsage(argv[0]);
            return 1;
        }
    }

    if (width <= 0 || height <= 0) {
        std::cerr << "Invalid dimensions\n";
        return 1;
    }

    flaggen::Config cfg;
    try {
        cfg = flaggen::loadConfig(findConfig());
    } catch (const std::exception& e) {
        std::cerr << "Failed to load config: " << e.what() << "\n";
        return 1;
    }

    std::mt19937 rng(seed);
    const flaggen::GenerationResult g = flaggen::generateFlag(cfg, rng, width, height);

    const std::string ext = fs::path(output).extension().string();
    cairo_surface_t* surface = nullptr;
    if (ext == ".svg") surface = cairo_svg_surface_create(output.c_str(), width, height);
    else if (ext == ".pdf") surface = cairo_pdf_surface_create(output.c_str(), width, height);
    else surface = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, width, height);

    if (!surface || cairo_surface_status(surface) != CAIRO_STATUS_SUCCESS) {
        std::cerr << "Failed to create surface for " << output << "\n";
        return 1;
    }

    cairo_t* cr = cairo_create(surface);
    flaggen::renderFlag(cr, g, width, height);
    cairo_destroy(cr);

    if (ext == ".svg" || ext == ".pdf") {
        cairo_surface_finish(surface);
    } else {
        if (cairo_surface_write_to_png(surface, output.c_str()) != CAIRO_STATUS_SUCCESS) {
            std::cerr << "Failed to write PNG: " << output << "\n";
            cairo_surface_destroy(surface);
            return 1;
        }
    }
    cairo_surface_destroy(surface);

    const char* pos = g.chargePosition == flaggen::ChargePosition::Center ? "center"
                      : g.chargePosition == flaggen::ChargePosition::Canton ? "canton"
                                                                             : "corner";
    std::cout << "seed=" << seed
              << " division=" << g.division.id
              << " ordinary=" << g.ordinary.id
              << " charge=" << g.charge.id
              << " (" << pos << ")"
              << " row=" << g.chargeRow
              << " palette=(" << flaggen::colorToHex(g.palette.a)
              << " " << flaggen::colorToHex(g.palette.b)
              << " " << flaggen::colorToHex(g.palette.c) << ")"
              << " charge=" << flaggen::colorToHex(g.chargeColor)
              << " scale=" << g.chargeScale
              << " contrast=" << g.chargeMinContrast
              << (g.fimbriation.enabled ? " fimbriation" : "")
              << (g.ordinaryFimbriation.enabled ? " ofimb" : "")
              << " anchors=";
    for (size_t i = 0; i < g.chargeAnchors.size(); ++i) {
        if (i) std::cout << ";";
        std::cout << "(" << g.chargeAnchors[i].first << "," << g.chargeAnchors[i].second << ")";
    }
    std::cout << " -> " << output << "\n";
    return 0;
}
