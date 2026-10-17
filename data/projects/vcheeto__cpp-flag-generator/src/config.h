#pragma once

#include <string>
#include <vector>

namespace flaggen {

struct DivisionConfig {
    std::string id;
    std::string render;
    bool rigidX = false;
    bool rigidY = false;
    std::string requiredAlignmentX;
    std::string requiredAlignmentY;
    std::vector<std::string> incompatible;
    int stripes = 0; // barry/paly/bendy stripe count or gyronny sector count
};

struct OrdinaryConfig {
    std::string id;
    std::string render;
    bool rigidX = false;
    bool rigidY = false;
    std::string requiredAlignmentX;
    std::string requiredAlignmentY;
    std::vector<std::string> incompatible;
    double thickness = 1.0 / 3.0; // fraction of the relevant dimension
    double size = 0.33;           // for canton/lozenge
    std::string defaultPosition;
};

struct ChargeConfig {
    std::string id;
    std::string render;
    int points = 0;
    double outerRadius = 0.15;
    double innerRadius = 0.06;
    double offsetX = 0.0;
};

struct Config {
    std::vector<DivisionConfig> divisions;
    std::vector<OrdinaryConfig> ordinaries;
    std::vector<ChargeConfig> charges;
};

Config loadConfig(const std::string& path);

} // namespace flaggen
