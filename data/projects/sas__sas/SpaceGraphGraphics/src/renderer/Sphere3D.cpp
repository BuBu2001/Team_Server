#include "renderer/Renderer.h"

#include <cmath>
#include <vector>
#include <GL/gl.h>

namespace sg {

// -----------------------------------------------------------------------
// True 3D planet rendering.
// Builds a UV sphere mesh (unit-radius, local +Y = north pole), then for
// each planet applies an axis-alignment + spin rotation, scales it, offsets
// it to the planet's view-space position, projects every vertex through the
// same projection used by the system view (svProject) and draws with
// per-vertex lighting (day/night terminator, limb darkening, atmosphere rim).
// No depth buffer needed: back-facing quads (N.z < 0) are drawn before
// front-facing ones, painter order per convex object.
// -----------------------------------------------------------------------

namespace {

struct SV { float x, y, z, u, v; };

struct Quad { SV a, b, c, d; };

// Build the unit UV sphere once, cached statically.
// lon segments around, lat segments up the side.
const std::vector<Quad>& unitSphere() {
    static const int LON = 40, LAT = 20;
    static std::vector<Quad> mesh = [] {
        std::vector<Quad> q;
        q.reserve(LON * LAT);
        for (int j = 0; j < LAT; ++j) {
            float t0 = (float)j / (float)LAT;
            float t1 = (float)(j + 1) / (float)LAT;
            float th0 = 3.14159265f * t0;              // polar angle 0..pi
            float th1 = 3.14159265f * t1;
            float sy0 = std::sin(th0), cy0 = std::cos(th0);
            float sy1 = std::sin(th1), cy1 = std::cos(th1);
            for (int i = 0; i < LON; ++i) {
                float ph0 = 2.f * 3.14159265f * (float)i / (float)LON;
                float ph1 = 2.f * 3.14159265f * (float)(i + 1) / (float)LON;
                float sx0 = std::sin(ph0), cx0 = std::cos(ph0);
                float sx1 = std::sin(ph1), cx1 = std::cos(ph1);
                float u0 = (float)i / (float)LON;
                float u1 = (float)(i + 1) / (float)LON;
                q.push_back({
                    { sx0 * sy0, cy0, cx0 * sy0, 1.f - u0, t0 },
                    { sx1 * sy0, cy0, cx1 * sy0, 1.f - u1, t0 },
                    { sx1 * sy1, cy1, cx1 * sy1, 1.f - u1, t1 },
                    { sx0 * sy1, cy1, cx0 * sy1, 1.f - u0, t1 },
                });
            }
        }
        return q;
    }();
    return mesh;
}

// 3x3 rotation from axis-angle (Rodrigues), m in row-major (m[r*3+c]).
void rotAxis(float ax, float ay, float az, float ang, float m[9]) {
    float len = std::sqrt(ax*ax + ay*ay + az*az);
    if (len < 1e-5f) { m[0]=1; m[1]=0; m[2]=0; m[3]=0; m[4]=1; m[5]=0; m[6]=0; m[7]=0; m[8]=1; return; }
    ax /= len; ay /= len; az /= len;
    float c = std::cos(ang), s = std::sin(ang), t = 1.f - c;
    float x = ax, y = ay, z = az;
    m[0] = t*x*x + c;  m[1] = t*x*y - s*z; m[2] = t*x*z + s*y;
    m[3] = t*x*y + s*z; m[4] = t*y*y + c;  m[5] = t*y*z - s*x;
    m[6] = t*x*z - s*y; m[7] = t*y*z + s*x; m[8] = t*z*z + c;
}

// Rotation that maps +Y (planet pole) onto `axis` (orbit normal, view space).
void alignPole(float ax, float ay, float az, float m[9]) {
    const float ux = 0.f, uy = 1.f, uz = 0.f;
    float cx = uy*az - uz*ay;
    float cy = uz*ax - ux*az;
    float cz = ux*ay - uy*ax;
    float s = std::sqrt(cx*cx + cy*cy + cz*cz);
    float c = ux*ax + uy*ay + uz*az;   // dot((0,1,0), axis) = ay
    if (s < 1e-4f) {
        if (c > 0.f) {
            m[0]=1; m[1]=0; m[2]=0; m[3]=0; m[4]=1; m[5]=0; m[6]=0; m[7]=0; m[8]=1;
        } else {
            // axis points down: 180 degree flip about X
            m[0]=1; m[1]=0; m[2]=0; m[3]=0; m[4]=-1; m[5]=0; m[6]=0; m[7]=0; m[8]=-1;
        }
        return;
    }
    float ang = std::atan2(s, c);
    rotAxis(cx/s, cy/s, cz/s, ang, m);
}

// Matrix multiply: out = A * B.
void mulMat(const float a[9], const float b[9], float out[9]) {
    for (int r = 0; r < 3; ++r)
        for (int c = 0; c < 3; ++c)
            out[r*3+c] = a[r*3+0]*b[0*3+c] + a[r*3+1]*b[1*3+c] + a[r*3+2]*b[2*3+c];
}

void applyMat(const float m[9], float x, float y, float z,
              float& ox, float& oy, float& oz) {
    ox = m[0]*x + m[1]*y + m[2]*z;
    oy = m[3]*x + m[4]*y + m[5]*z;
    oz = m[6]*x + m[7]*y + m[8]*z;
}

} // namespace

void Renderer::drawSphere3D(const ImageTex& tex,
                            float px, float py, float pz,     // planet centre (view space)
                            float baseR,                      // world radius
                            float axisX, float axisY, float axisZ,
                            float spin,
                            float lightX, float lightY, float lightZ,
                            float atmoR, float atmoG, float atmoB,
                            float dist, float focal,
                            float winW, float winH) {
    if (!tex.ok || !tex.tex || baseR <= 0.f) return;

    // Combined rotation: first align pole (+Y) to the axis, then spin around it.
    float align[9], sp[9], R[9];
    alignPole(axisX, axisY, axisZ, align);
    rotAxis(axisX, axisY, axisZ, spin, sp);
    mulMat(sp, align, R);

    const float ll = std::sqrt(lightX*lightX + lightY*lightY + lightZ*lightZ);
    float lx = lightX, ly = lightY, lz = lightZ;
    if (ll > 1e-5f) { lx /= ll; ly /= ll; lz /= ll; }

    glEnable(GL_TEXTURE_2D);
    glBindTexture(GL_TEXTURE_2D, tex.tex);
    glEnable(GL_BLEND);
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);

    // Weak-perspective projection: scale everything by the distance to the
    // planet centre, not per-vertex depth.  A per-vertex depth divide makes
    // near faces of a close sphere balloon while far faces shrink, stretching
    // the silhouette off a circle when zoomed in.  Scaling by the centre depth
    // matches svProject() at the centre, so the sphere stays circular and
    // lines up with the orbit/outline circles at any distance.
    const float depthC = dist - pz;
    if (depthC < 1.f) return;
    const float scale = focal / depthC;

    // Two passes: back-facing quads first, then front-facing (no z-buffer).
    for (int pass = 0; pass < 2; ++pass) {
        bool backPass = (pass == 0);
        glBegin(GL_QUADS);
        for (const Quad& qd : unitSphere()) {
            const SV* verts[4] = { &qd.a, &qd.b, &qd.c, &qd.d };
            float nxs = 0, nys = 0, nzs = 0;   // sum of normals -> front face test
            float prj[4][2];
            for (int k = 0; k < 4; ++k) {
                float dx, dy, dz;
                applyMat(R, verts[k]->x, verts[k]->y, verts[k]->z, dx, dy, dz);
                nxs += dx; nys += dy; nzs += dz;
                float vx = px + dx * baseR;
                float vy = py + dy * baseR;
                prj[k][0] = winW / 2.f + vx * scale;
                prj[k][1] = winH / 2.f - vy * scale;
            }
            // front faces have summed normal pointing toward camera (+z)
            bool front = (nzs > 0.f);
            if (front == backPass) continue;

            for (int k = 0; k < 4; ++k) {
                float nx, ny, nz;
                applyMat(R, verts[k]->x, verts[k]->y, verts[k]->z, nx, ny, nz);
                // per-vertex lighting
                float ndl = nx*lx + ny*ly + nz*lz;
                float diff = ndl > 0.f ? ndl : 0.f;
                // soft terminator penumbra
                float term = (ndl < -0.08f) ? 0.f : (ndl > 0.14f) ? 1.f
                             : (ndl + 0.08f) / 0.22f;
                float ambient = 0.07f;
                float sun = ambient + term * (1.f - ambient);
                // specular glint from the star
                float vzdir = 1.f; // camera down +z
                float hx = lx, hy = ly, hz = lz + vzdir;
                float hl = std::sqrt(hx*hx + hy*hy + hz*hz);
                if (hl > 1e-5f) { hx /= hl; hy /= hl; hz /= hl; }
                float spec = std::pow(nx*hx + ny*hy + nz*hz > 0.f
                                      ? nx*hx + ny*hy + nz*hz : 0.f, 26.f) * 0.35f;
                // atmosphere rim on the limb
                float rim = std::pow(1.f - nz, 2.4f);
                float ar = atmoR * rim * (0.25f + diff * 0.75f);
                float ag = atmoG * rim * (0.25f + diff * 0.75f);
                float ab = atmoB * rim * (0.25f + diff * 0.75f);

                float cr = sun + spec + ar;
                float cg = sun + spec + ag;
                float cb = sun + spec + ab;
                if (cr > 1.f) cr = 1.f;
                if (cg > 1.f) cg = 1.f;
                if (cb > 1.f) cb = 1.f;

                glColor4f(cr, cg, cb, 1.f);
                glTexCoord2f(verts[k]->u, verts[k]->v);
                glVertex2f(prj[k][0], prj[k][1]);
            }
        }
        glEnd();
    }
    glDisable(GL_TEXTURE_2D);
    glColor4f(1.f, 1.f, 1.f, 1.f);
}

} // namespace sg