#!/usr/bin/env python3
"""Batch verifier for flaggen.

Usage:
    flaggen --seed S --width 1200 --height 800 --output /tmp/f_S.png >> /tmp/run.txt
    python3 tools/verify.py /tmp/run.txt

Checks every flag in the run file:
  * the charge color is drawn at its anchor (solid core; ring/horn points for
    hollow shapes: annulet, crescent; top lobe for trefoil),
  * hollow interiors are actually empty,
  * the charge (including square corners) stays inside the flag,
and reports row/multi-anchor distributions plus scale/contrast metrics.

Requires Pillow.
"""
import re
import sys
import pathlib
from PIL import Image

# Geometric outer extent of each charge (fraction of min(w,h) at scale 1.0).
EXT = {
    "star_5": 0.16, "star_6": 0.16, "star_8": 0.16,
    "crescent": 0.22, "sun": 0.22, "annulet": 0.20, "circle": 0.18,
    "triangle": 0.20, "square": 0.198, "diamond": 0.20,
    "hexagon": 0.20, "octagon": 0.20, "cross_patee": 0.26,
    "fleur_de_lis": 0.20, "trefoil": 0.20,
}


def hx(s):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def near(a, b, tol=2):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def get(img, px, py, w, h):
    ix, iy = int(px), int(py)
    if 0 <= ix < w and 0 <= iy < h:
        return img.getpixel((ix, iy))
    return None


def main(batch_file):
    lines = pathlib.Path(batch_file).read_text().splitlines()
    rows, multi = {}, {}
    n_ofimb = n_fimb = 0
    scales, contrasts = [], []
    data = []

    for ln in lines:
        f = {}
        for k in ("seed", "division", "ordinary", "charge", "row", "scale", "contrast"):
            m = re.search(k + r"=(\S+)", ln)
            f[k] = m.group(1) if m else None
        f["scale"] = float(f["scale"])
        f["contrast"] = float(f["contrast"])
        f["chargecol"] = hx(re.search(r" charge=#([0-9A-Fa-f]{6})", ln).group(1))
        f["ofimb"] = " ofimb" in ln
        f["fimb"] = " fimbriation" in ln
        f["anchors"] = [tuple(map(float, p.split(","))) for p in re.findall(
            r"\(([\d.,-]+)\)", re.search(r"anchors=(.*?)( -> |$)", ln).group(1))]
        f["out"] = re.search(r"-> (\S+)", ln).group(1)
        rows[f["row"]] = rows.get(f["row"], 0) + 1
        multi[len(f["anchors"])] = multi.get(len(f["anchors"]), 0) + 1
        n_ofimb += f["ofimb"]
        n_fimb += f["fimb"]
        scales.append(f["scale"])
        contrasts.append(f["contrast"])
        data.append(f)

    print(f"flags: {len(data)}  row: {dict(sorted(rows.items()))}  "
          f"multi: {dict(sorted(multi.items()))}")
    print(f"ofimb: {n_ofimb} ({round(100 * n_ofimb / len(data))}%)  "
          f"charge-fimb: {n_fimb}")
    print(f"scale: {min(scales):.3f}/{sum(scales) / len(scales):.3f}/{max(scales):.3f}  "
          f"contrast: {min(contrasts):.3f}/{sum(contrasts) / len(contrasts):.3f}/{max(contrasts):.3f}")
    print(f"scale<0.3: {sum(1 for s in scales if s < 0.3)}  "
          f"contrast<3: {sum(1 for c in contrasts if c < 3)}")

    solid = hollow = oob = 0
    pts = 0
    for f in data:
        img = Image.open(f["out"]).convert("RGB")
        w, h = img.size
        ch = f["charge"]
        m = min(w, h)
        R = EXT[ch] * m * f["scale"]
        for (ax, ay) in f["anchors"]:
            if ch == "annulet":
                cores = [(ax + 0.85 * R, ay), (ax - 0.85 * R, ay)]
            elif ch == "crescent":
                cores = [(ax - 0.9 * R, ay), (ax, ay - 0.9 * R)]
            elif ch == "trefoil":
                cores = [(ax, ay - 0.42 * R)]
            else:
                cores = [(ax, ay)]
            for (cx, cy) in cores:
                g = get(img, cx, cy, w, h)
                if g is None or not near(g, f["chargecol"]):
                    solid += 1
            if ch == "annulet":
                g = get(img, ax, ay, w, h)
                if g is not None and near(g, f["chargecol"]):
                    hollow += 1
            elif ch == "crescent":
                g = get(img, ax + 0.06 * m * f["scale"], ay, w, h)
                if g is not None and near(g, f["chargecol"]):
                    hollow += 1
            for px, py in ((ax - R, ay), (ax + R, ay), (ax, ay - R), (ax, ay + R)):
                if px < -2 or px > w + 2 or py < -2 or py > h + 2:
                    oob += 1
            pts += 1

    print(f"pixels: {pts}  solid_fail: {solid}  hollow_fail: {hollow}  oob_fail: {oob}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/opencode/batch9.txt")
