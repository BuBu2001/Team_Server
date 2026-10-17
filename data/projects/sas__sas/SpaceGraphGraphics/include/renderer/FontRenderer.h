#pragma once

#include "core/Types.h"
#include <ft2build.h>
#include <string>
#include <map>
#include <cstdint>
#include <vector>

#include FT_FREETYPE_H
#include <GL/gl.h>

namespace sg {

struct Color {
    float r = 1.f, g = 1.f, b = 1.f, a = 1.f;
    Color() {}
    Color(float r, float g, float b, float a = 1.f) : r(r), g(g), b(b), a(a) {}
    static Color from255(int r, int g, int b, int a = 255) {
        return Color(r/255.f, g/255.f, b/255.f, a/255.f);
    }
};

// Renders UTF-8 text using FreeType into GL textures.
// Draws in the currently active (screen-space) GL projection with
// y DOWN being positive rows (top-left origin) unless flipY requested.
class FontRenderer {
public:
    FontRenderer();
    ~FontRenderer();
    bool load(const char* path, unsigned sizePx);

    // Measure the advance width of a UTF-8 string (in px).
    float measure(const std::string& utf8, unsigned sizePx);

    // Draw text; x,y is top-left in current projection (y-down if yDown=true).
    // Returns total width.
    float draw(const std::string& utf8, float x, float y, unsigned sizePx,
               const Color& c = Color(1,1,1), bool yDown = true);

    unsigned size() const { return size_; }
    float lineHeight() const { return lineHeight_; }
    bool loaded() const { return loaded_; }

private:
    struct Glyph {
        GLuint tex = 0;
        int width = 0, height = 0;
        int bearingX = 0, bearingY = 0;
        unsigned advance = 0;
    };
    FT_Library lib_ = nullptr;
    FT_Face face_ = nullptr;
    bool loaded_ = false;
    unsigned size_ = 16;
    float lineHeight_ = 16.f;
    // per-size glyph cache
    std::map<uint64_t, Glyph> cache_;

    Glyph& glyph(unsigned charCode, unsigned sizePx);
    void buildGlyph(Glyph& g, unsigned charCode, unsigned sizePx);
};

// Decodes one UTF-8 codepoint from s[pos]; returns 0 if invalid.
uint32_t utf8Decode(const std::string& s, size_t& pos, size_t& numBytes);

} // namespace sg
