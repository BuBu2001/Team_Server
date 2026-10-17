#include "renderer/Renderer.h"

#include <png.h>
#include <jpeglib.h>
#include <GL/gl.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <csetjmp>
#include <vector>

namespace sg {

// libjpeg's default error handler calls exit() on corrupt data; install a
// longjmp-based one instead so a bad image never kills the game.
struct JpegFail {
    jpeg_error_mgr pub;
    jmp_buf jb;
};

static void jpegFailExit(j_common_ptr cinfo) {
    JpegFail* e = reinterpret_cast<JpegFail*>(cinfo->err);
    std::longjmp(e->jb, 1);
}

// Upload RGBA rows (top-to-bottom) into an OpenGL texture. row 0 = image top.
// Tightly packed 4-byte RGBA pixels.
static ImageTex uploadRGBA(int w, int h, const std::vector<unsigned char>& rgb) {
    ImageTex out;
    if (w <= 0 || h <= 0 || w > 16384 || h > 16384) return out;
    GLuint tex = 0;
    glGenTextures(1, &tex);
    glBindTexture(GL_TEXTURE_2D, tex);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    // Build mipmaps automatically (GL 1.4 GL_SGIS_generate_mipmap). This works
    // on GL 2.1 without requiring glGenerateMipmap (GL 3.0). Only define the
    // constant if the GL_RENDERER_ header did not already.
#ifndef GL_GENERATE_MIPMAP
#define GL_GENERATE_MIPMAP 0x8191
#endif
    glTexParameteri(GL_TEXTURE_2D, GL_GENERATE_MIPMAP, GL_TRUE);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0,
                 GL_RGBA, GL_UNSIGNED_BYTE, rgb.data());
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR_MIPMAP_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    out.tex = tex;
    out.w = w;
    out.h = h;
    out.ok = true;
    return out;
}

// Public wrapper: upload raw RGBA pixels produced by procedural renderers.
ImageTex textureFromRGBA(int w, int h, const std::vector<unsigned char>& rgba) {
    return uploadRGBA(w, h, rgba);
}

static ImageTex loadPNG(FILE* f) {
    png_structp png = png_create_read_struct(PNG_LIBPNG_VER_STRING, nullptr, nullptr, nullptr);
    if (!png) return ImageTex{};
    png_infop info = png_create_info_struct(png);
    if (!info) { png_destroy_read_struct(&png, nullptr, nullptr); return ImageTex{}; }

    if (setjmp(png_jmpbuf(png))) {
        png_destroy_read_struct(&png, &info, nullptr);
        return ImageTex{};
    }

    png_init_io(png, f);
    png_read_info(png, info);

    png_uint_32 w = 0, h = 0;
    int bitDepth = 0, colorType = 0;
    png_get_IHDR(png, info, &w, &h, &bitDepth, &colorType, nullptr, nullptr, nullptr);

    // will the decoded image already carry an alpha channel? (RGBA / gray+alpha
    // pixel formats and any palette with a tRNS chunk). If so, png_set_filler
    // below must NOT be called — it would append a fifth byte per pixel.
    const bool hasAlpha = (colorType == PNG_COLOR_TYPE_RGBA ||
                           colorType == PNG_COLOR_TYPE_GRAY_ALPHA) ||
                          png_get_valid(png, info, PNG_INFO_tRNS);

    if (colorType == PNG_COLOR_TYPE_PALETTE) png_set_palette_to_rgb(png);
    if (colorType == PNG_COLOR_TYPE_GRAY && bitDepth < 8)
        png_set_expand_gray_1_2_4_to_8(png);
    if (png_get_valid(png, info, PNG_INFO_tRNS)) png_set_tRNS_to_alpha(png);
    if (bitDepth == 16) png_set_strip_16(png);
    if (colorType == PNG_COLOR_TYPE_GRAY || colorType == PNG_COLOR_TYPE_GRAY_ALPHA)
        png_set_gray_to_rgb(png);
    if (!hasAlpha)
        png_set_filler(png, 0xFF, PNG_FILLER_AFTER); // ensure RGBA

    png_read_update_info(png, info);
    png_get_IHDR(png, info, &w, &h, &bitDepth, &colorType, nullptr, nullptr, nullptr);
    if (w == 0 || h == 0 || w > 16384 || h > 16384) {
        png_destroy_read_struct(&png, &info, nullptr);
        return ImageTex{};
    }

    std::vector<png_bytep> rows(h);
    std::vector<std::vector<png_byte>> data(h);
    for (png_uint_32 y = 0; y < h; ++y) data[y].resize(png_get_rowbytes(png, info));
    for (png_uint_32 y = 0; y < h; ++y) rows[y] = data[y].data();
    png_read_image(png, rows.data());

    std::vector<unsigned char> flat((size_t)w * h * 4);
    for (png_uint_32 y = 0; y < h; ++y)
        std::memcpy(&flat[(size_t)y * w * 4], data[y].data(), (size_t)w * 4);

    png_destroy_read_struct(&png, &info, nullptr);
    return uploadRGBA((int)w, (int)h, flat);
}

static ImageTex loadJPEG(FILE* f) {
    struct jpeg_decompress_struct cinfo;
    JpegFail jerr;
    cinfo.err = jpeg_std_error(&jerr.pub);
    jerr.pub.error_exit = jpegFailExit;
    jpeg_create_decompress(&cinfo);
    if (setjmp(jerr.jb)) {            // on decode error: clean up and bail out
        jpeg_destroy_decompress(&cinfo);
        return ImageTex{};
    }
    jpeg_stdio_src(&cinfo, f);
    jpeg_read_header(&cinfo, TRUE);
    cinfo.out_color_space = JCS_RGB;
    jpeg_start_decompress(&cinfo);

    const int w = cinfo.output_width, h = cinfo.output_height;
    if (w <= 0 || h <= 0 || w > 16384 || h > 16384) {
        jpeg_destroy_decompress(&cinfo);
        return ImageTex{};
    }
    std::vector<unsigned char> flat((size_t)w * h * 3);
    std::vector<unsigned char> row((size_t)w * 3);
    int y = 0;
    while (cinfo.output_scanline < cinfo.output_height) {
        JSAMPROW rp = row.data();
        jpeg_read_scanlines(&cinfo, &rp, 1);
        std::memcpy(&flat[(size_t)y * w * 3], row.data(), (size_t)w * 3);
        ++y;
    }
    jpeg_finish_decompress(&cinfo);
    jpeg_destroy_decompress(&cinfo);

    // convert RGB (row 0 = image top) -> RGBA
    std::vector<unsigned char> rgba((size_t)w * h * 4);
    for (size_t i = 0, j = 0; j < flat.size(); i += 4, j += 3) {
        rgba[i] = flat[j];
        rgba[i + 1] = flat[j + 1];
        rgba[i + 2] = flat[j + 2];
        rgba[i + 3] = 255;
    }
    return uploadRGBA(w, h, rgba);
}

ImageTex Renderer::loadImage(const char* path) {
    FILE* f = std::fopen(path, "rb");
    if (!f) return ImageTex{};

    // sniff the format from the magic bytes (some assets are JPEG despite a
    // ".png" extension)
    unsigned char magic[8] = {0};
    size_t got = std::fread(magic, 1, 8, f);
    std::rewind(f);

    ImageTex out;
    if (got >= 8 && std::memcmp(magic, "\x89PNG\r\n\x1a\n", 8) == 0) {
        out = loadPNG(f);
    } else if (got >= 3 && magic[0] == 0xFF && magic[1] == 0xD8 && magic[2] == 0xFF) {
        out = loadJPEG(f);
    }
    std::fclose(f);
    return out;
}

void Renderer::destroyImage(const ImageTex& t) {
    if (t.tex) glDeleteTextures(1, &t.tex);
}

void Renderer::drawImage(const ImageTex& t, float x, float y, float w, float h) {
    drawImageUV(t, x, y, w, h, 0.f, 0.f, 1.f, 1.f);
}

// Draw a sub-region (uv 0..1) of the texture onto (x,y,w,h). Coordinates
// follow the screen convention of drawRect (y grows down; x,y = top-left).
void Renderer::drawImageUV(const ImageTex& t, float x, float y, float w, float h,
                           float u0, float v0, float u1, float v1) {
    if (!t.ok || !t.tex || h <= 0.f || w <= 0.f) return;
    glEnable(GL_TEXTURE_2D);
    glEnable(GL_BLEND);
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
    glColor4f(1.f, 1.f, 1.f, 1.f);
    glBindTexture(GL_TEXTURE_2D, t.tex);
    glBegin(GL_QUADS);
    glTexCoord2f(u0, v0); glVertex2f(x,     y);
    glTexCoord2f(u1, v0); glVertex2f(x + w, y);
    glTexCoord2f(u1, v1); glVertex2f(x + w, y + h);
    glTexCoord2f(u0, v1); glVertex2f(x,     y + h);
    glEnd();
    glDisable(GL_TEXTURE_2D);
}

// -----------------------------------------------------------------------
// PNG writer for disk cache
// -----------------------------------------------------------------------

bool savePNG(const char* path, int w, int h, const unsigned char* rgba) {
    FILE* f = std::fopen(path, "wb");
    if (!f) return false;

    png_structp png = png_create_write_struct(PNG_LIBPNG_VER_STRING,
                                              nullptr, nullptr, nullptr);
    if (!png) { std::fclose(f); return false; }
    png_infop info = png_create_info_struct(png);
    if (!info) { png_destroy_write_struct(&png, nullptr); std::fclose(f); return false; }

    if (setjmp(png_jmpbuf(png))) {
        png_destroy_write_struct(&png, &info);
        std::fclose(f);
        return false;
    }

    png_init_io(png, f);
    png_set_IHDR(png, info, w, h, 8, PNG_COLOR_TYPE_RGBA,
                 PNG_INTERLACE_NONE, PNG_COMPRESSION_TYPE_DEFAULT,
                 PNG_FILTER_TYPE_DEFAULT);
    png_write_info(png, info);

    // libpng expects writable row pointers (png_bytep)
    std::vector<png_bytep> rows(h);
    for (int y = 0; y < h; ++y)
        rows[y] = const_cast<png_bytep>(&rgba[(size_t)y * w * 4]);
    png_write_image(png, rows.data());
    png_write_end(png, info);

    png_destroy_write_struct(&png, &info);
    std::fclose(f);
    return true;
}

} // namespace sg
