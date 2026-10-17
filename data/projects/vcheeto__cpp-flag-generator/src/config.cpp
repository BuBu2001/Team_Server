#include "config.h"

#include <fstream>
#include <stdexcept>

#include <nlohmann/json.hpp>

namespace flaggen {

using json = nlohmann::json;

static std::vector<std::string> parseList(const json& j) {
    std::vector<std::string> out;
    if (j.is_array()) {
        for (const auto& v : j) out.push_back(v.get<std::string>());
    }
    return out;
}

static DivisionConfig parseDivision(const json& j) {
    DivisionConfig d;
    d.id = j.value("id", "");
    d.render = j.value("render", "");
    d.rigidX = j.value("rigidX", false);
    d.rigidY = j.value("rigidY", false);
    d.requiredAlignmentX = j.value("requiredAlignmentX", "");
    d.requiredAlignmentY = j.value("requiredAlignmentY", "");
    d.incompatible = parseList(j.value("incompatible", json::array()));
    d.stripes = j.value("stripes", 0);
    return d;
}

static OrdinaryConfig parseOrdinary(const json& j) {
    OrdinaryConfig o;
    o.id = j.value("id", "");
    o.render = j.value("render", "");
    o.rigidX = j.value("rigidX", false);
    o.rigidY = j.value("rigidY", false);
    o.requiredAlignmentX = j.value("requiredAlignmentX", "");
    o.requiredAlignmentY = j.value("requiredAlignmentY", "");
    o.incompatible = parseList(j.value("incompatible", json::array()));
    o.thickness = j.value("thickness", 1.0 / 3.0);
    o.size = j.value("size", 0.33);
    o.defaultPosition = j.value("defaultPosition", "");
    return o;
}

static ChargeConfig parseCharge(const json& j) {
    ChargeConfig c;
    c.id = j.value("id", "");
    c.render = j.value("render", "");
    c.points = j.value("points", 0);
    c.outerRadius = j.value("outerRadius", 0.15);
    c.innerRadius = j.value("innerRadius", 0.06);
    c.offsetX = j.value("offsetX", 0.0);
    return c;
}

Config loadConfig(const std::string& path) {
    std::ifstream in(path);
    if (!in.is_open()) {
        throw std::runtime_error("cannot open config file: " + path);
    }
    json j;
    in >> j;

    Config cfg;
    if (j.contains("divisions")) {
        for (const auto& v : j["divisions"]) cfg.divisions.push_back(parseDivision(v));
    }
    if (j.contains("ordinaries")) {
        for (const auto& v : j["ordinaries"]) cfg.ordinaries.push_back(parseOrdinary(v));
    }
    if (j.contains("charges")) {
        for (const auto& v : j["charges"]) cfg.charges.push_back(parseCharge(v));
    }
    return cfg;
}

} // namespace flaggen
