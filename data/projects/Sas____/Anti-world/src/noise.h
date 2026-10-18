#ifndef NOISE_H
#define NOISE_H

void noise_init(unsigned seed);
float perlin2(float x, float y);
float fbm2(float x, float y, int octaves);

#endif
