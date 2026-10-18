#ifndef EARTH_H
#define EARTH_H

#include "gmath.h"

typedef enum {
    BIOME_ICE,
    BIOME_TUNDRA,
    BIOME_TAIGA,
    BIOME_TEMPERATE_FOREST,
    BIOME_STEPPE,
    BIOME_DESERT,
    BIOME_SAVANNA,
    BIOME_MEDITERRANEAN,
    BIOME_RAINFOREST,
    BIOME_COUNT
} Biome;

typedef struct {
    int year;
    int day;     /* 1..365 */
    int hour;    /* 0..23 */
    int minute;  /* 0..59 */
    float day_f; /* 1..365 with fraction of the day */
} WorldTime;

extern WorldTime world_time;

typedef struct {
    int active;
    float lon, lat;
    float radius_deg;
    float wind_ms;
    float intensity;
    float bearing_deg;
    float life_day;
    int category;
} Storm;

void game_tick(float real_dt);

void weather_init(void);
void weather_update(float dt_days);

/* world geometry: lon = x (degrees), lat = z (degrees), y = meters (sea level 0) */
float earth_height(float lon, float lat);
float earth_sea_ice(float lon, float lat);
float wrap_lon(float lon);

float earth_temperature_c(float lon, float lat, float elev);
float earth_precip_mmday(float lon, float lat, float elev);
Biome earth_biome(float lon, float lat, float elev);
const char *earth_biome_name(Biome b);
const char *earth_zone_name(float lat);
void earth_biome_color(Biome b, float lon, float lat, float elev, float day,
                       float *r, float *g, float *bl);

float earth_current_temp_offset(float lon, float lat);
float earth_current_speed(float lon, float lat);
float earth_current_bearing(float lon, float lat);

Storm earth_nearest_cyclone(float lon, float lat, float *dist_deg);

Vec3 earth_sun_dir(float lat);
void earth_light(float sun_elev_rad, float rain,
                 float *ambient, float *diffuse,
                 float *top_r, float *top_g, float *top_b,
                 float *hor_r, float *hor_g, float *hor_b);

#endif