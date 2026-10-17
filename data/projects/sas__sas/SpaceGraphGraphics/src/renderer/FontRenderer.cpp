#include "renderer/FontRenderer.h"

namespace sg {

uint32_t utf8Decode(const std::string& s, size_t& pos, size_t& numBytes) {
    unsigned char c = static_cast<unsigned char>(s[pos]);
    uint32_t cp = 0;
    if (c < 0x80) { cp = c; numBytes = 1; }
    else if ((c >> 5) == 0x6) {              // 110xxxxx
        if (pos + 1 >= s.size()) { numBytes=1; return 0; }
        cp = ((c & 0x1F) << 6) | (s[pos+1] & 0x3F);
        numBytes = 2;
    }
    else if ((c >> 4) == 0xE) {              // 1110xxxx
        if (pos + 2 >= s.size()) { numBytes=1; return 0; }
        cp = ((c & 0x0F) << 12) | ((s[pos+1] & 0x3F) << 6) | (s[pos+2] & 0x3F);
        numBytes = 3;
    }
    else if ((c >> 3) == 0x1E) {             // 11110xxx
        if (pos + 3 >= s.size()) { numBytes=1; return 0; }
        cp = ((c & 0x07) << 18) | ((s[pos+1] & 0x3F) << 12) |
             ((s[pos+2] & 0x3F) << 6) | (s[pos+3] & 0x3F);
        numBytes = 4;
    }
    else { numBytes = 1; return 0; }
    return cp;
}

FontRenderer::FontRenderer() {
    if (FT_Init_FreeType(&lib_) != 0) lib_ = nullptr;
}

FontRenderer::~FontRenderer() {
    for (auto& kv : cache_) {
        if (kv.second.tex) glDeleteTextures(1, &kv.second.tex);
    }
    if (face_) FT_Done_Face(face_);
    if (lib_) FT_Done_FreeType(lib_);
}

bool FontRenderer::load(const char* path, unsigned sizePx) {
    if (!lib_) return false;
    // release the previous face and glyph textures before reloading
    for (auto& kv : cache_) {
        if (kv.second.tex) glDeleteTextures(1, &kv.second.tex);
    }
    cache_.clear();
    if (face_) {
        FT_Done_Face(face_);
        face_ = nullptr;
    }
    if (FT_New_Face(lib_, path, 0, &face_) != 0) { face_ = nullptr; return false; }
    size_ = sizePx;
    FT_Set_Pixel_Sizes(face_, 0, sizePx);
    lineHeight_ = static_cast<float>(face_->size->metrics.height) / 64.f;
    loaded_ = true;
    return true;
}

void FontRenderer::buildGlyph(Glyph& g, unsigned charCode, unsigned sizePx) {
    if (FT_Load_Glyph(face_, FT_Get_Char_Index(face_, charCode),
                      FT_LOAD_RENDER) != 0) return;
    FT_GlyphSlot slot = face_->glyph;
    g.width = slot->bitmap.width;
    g.height = slot->bitmap.rows;
    g.bearingX = slot->bitmap_left;
    g.bearingY = slot->bitmap_top;
    g.advance = static_cast<unsigned>(slot->advance.x >> 6);

    if (g.width <= 0 || g.height <= 0) return;
    if (static_cast<int>(g.width) > 1024) return; // safety

    // upload as GL_LUMINANCE (ALPHA fallback)
    GLuint tex;
    glGenTextures(1, &tex);
    glBindTexture(GL_TEXTURE_2D, tex);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_ALPHA, g.width, g.height, 0,
                 GL_ALPHA, GL_UNSIGNED_BYTE, slot->bitmap.buffer);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    g.tex = tex;
}

FontRenderer::Glyph& FontRenderer::glyph(unsigned charCode, unsigned sizePx) {
    uint64_t key = (static_cast<uint64_t>(sizePx) << 32) | charCode;
    auto it = cache_.find(key);
    if (it != cache_.end()) return it->second;
    Glyph g;
    if (sizePx != size_) { // switch pixel size on-demand
        FT_Set_Pixel_Sizes(face_, 0, sizePx);
        size_ = sizePx;
    }
    buildGlyph(g, charCode, sizePx);
    return cache_.emplace(key, g).first->second;
}

float FontRenderer::measure(const std::string& utf8, unsigned sizePx) {
    if (!loaded_) return 0;
    size_t pos = 0;
    float w = 0.f;
    while (pos < utf8.size()) {
        size_t n;
        uint32_t cp = utf8Decode(utf8, pos, n);
        pos += n;
        if (cp == 0) continue;
        Glyph& g = glyph(cp, sizePx);
        w += static_cast<float>(g.advance);
    }
    return w;
}

float FontRenderer::draw(const std::string& utf8, float x, float y, unsigned sizePx,
                         const Color& c, bool yDown) {
    if (!loaded_) return 0;
    float penX = x;
    size_t pos = 0;
    glEnable(GL_TEXTURE_2D);
    glEnable(GL_BLEND);
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
    glColor4f(c.r, c.g, c.b, c.a);
    while (pos < utf8.size()) {
        size_t n;
        uint32_t cp = utf8Decode(utf8, pos, n);
        pos += n;
        if (cp == 0) continue;
        Glyph& g = glyph(cp, sizePx);
        if (!g.tex) { penX += static_cast<float>(g.advance); continue; }

        float x0 = penX + static_cast<float>(g.bearingX);
        float yTop, yBot;
        if (yDown) {
            // screen convention: y grows down, text top at y
            yTop = y + static_cast<float>(sizePx) - static_cast<float>(g.bearingY);
            yBot = yTop + static_cast<float>(g.height);
        } else {
            // world convention: y grows up, text top at y, glyph below it
            yTop = y;
            yBot = yTop - static_cast<float>(g.height);
        }

        float x1 = x0 + static_cast<float>(g.width);

        glBindTexture(GL_TEXTURE_2D, g.tex);
        glBegin(GL_QUADS);
        // v=0 samples the first bitmap row (the glyph's top) at yTop.
        glTexCoord2f(0, 0); glVertex2f(x0, yTop);
        glTexCoord2f(1, 0); glVertex2f(x1, yTop);
        glTexCoord2f(1, 1); glVertex2f(x1, yBot);
        glTexCoord2f(0, 1); glVertex2f(x0, yBot);
        glEnd();
        penX += static_cast<float>(g.advance);
    }
    glDisable(GL_TEXTURE_2D);
    return penX - x;
}

} // namespace sg
