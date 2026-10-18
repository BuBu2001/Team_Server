#ifndef RENDERER_H
#define RENDERER_H

#include <stdint.h>
#include "gmath.h"

typedef struct {
    int w, h;
    uint32_t *pixels;
    float *zbuf;
} Image;

typedef struct {
    Vec3 pos;
    float yaw;
    float pitch;
    float fov;
} Camera;

typedef struct {
    Vec3 sun;
    float ambient;
    float diffuse;
    float fog_r, fog_g, fog_b;
    float sky_top_r, sky_top_g, sky_top_b;
    float water_time;
} LightState;

int img_init(Image *img, int w, int h);
void img_free(Image *img);

Vec3 cam_forward(const Camera *cam);
Vec3 cam_right(const Camera *cam);
Vec3 cam_up(const Camera *cam);

void chunks_update(float px, float pz);
int chunks_loaded(void);
void chunks_unload_all(void);

void render_frame(Image *img, const Camera *cam, const LightState *ls);
void write_ppm(const char *path, const uint32_t *pixels, int w, int h);

#endif