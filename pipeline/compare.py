"""Pictures of two builds of the same place, side by side, buildings drawn in.

    python pipeline/compare.py PROJECT_A PROJECT_B SOUTH WEST NORTH EAST OUT.png

Each project's ground preview, with every building knoxbuild placed drawn as
its floor plan (from <name>_footprints.npz), cut to the same lat/lon box. The
box is placed by each project's own projection, so the two pictures show the
same real ground however differently the builds laid it out.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import isle  # noqa: E402,F401  (KnoxMap on the path)
from generator.renderer import Projector  # noqa: E402
from knoxbuild.population import load_footprints  # noqa: E402

ROOF = (150, 60, 45)
EDGE = (60, 25, 20)


def picture(project: str, box: tuple[float, float, float, float]) -> Image.Image:
    name = os.path.basename(os.path.normpath(project))
    info = json.load(open(os.path.join(project, f"{name}_info.json")))
    b = info["bbox"]
    proj = Projector.build(b["south"], b["west"], b["north"], b["east"],
                           info["meters_per_tile"], info.get("rotation") or 0.0)
    s, w, n, e = box
    corners = [proj.to_px(lat, lon) for lat in (s, n) for lon in (w, e)]
    x0, y0 = (int(min(c[i] for c in corners)) for i in (0, 1))
    x1, y1 = (int(max(c[i] for c in corners)) for i in (0, 1))
    img = Image.open(os.path.join(project, f"{name}_preview.png")).convert("RGB")
    img = img.crop((x0, y0, x1, y1))
    px = np.array(img)
    for fx, fy, mask, _levels, _kind in load_footprints(os.path.join(project, f"{name}_footprints.npz")):
        h, wd = mask.shape
        if fx + wd < x0 or fx > x1 or fy + h < y0 or fy > y1:
            continue
        ys, xs = np.nonzero(mask)
        xs, ys = xs + fx - x0, ys + fy - y0
        ok = (xs >= 0) & (ys >= 0) & (xs < px.shape[1]) & (ys < px.shape[0])
        px[ys[ok], xs[ok]] = ROOF
        # A darker edge so neighbouring buildings read as separate.
        edge = mask & ~(np.pad(mask, 1)[2:, 1:-1] & np.pad(mask, 1)[:-2, 1:-1]
                        & np.pad(mask, 1)[1:-1, 2:] & np.pad(mask, 1)[1:-1, :-2])
        ys, xs = np.nonzero(edge)
        xs, ys = xs + fx - x0, ys + fy - y0
        ok = (xs >= 0) & (ys >= 0) & (xs < px.shape[1]) & (ys < px.shape[0])
        px[ys[ok], xs[ok]] = EDGE
    return Image.fromarray(px)


def side_by_side(a: Image.Image, b: Image.Image, labels=("Before", "After"),
                 scale: float = 1.0) -> Image.Image:
    if scale != 1.0:
        a = a.resize((int(a.width * scale), int(a.height * scale)), Image.NEAREST)
        b = b.resize((int(b.width * scale), int(b.height * scale)), Image.NEAREST)
    gap, head = 12, 28
    out = Image.new("RGB", (a.width + b.width + gap, max(a.height, b.height) + head), (255, 255, 255))
    out.paste(a, (0, head))
    out.paste(b, (a.width + gap, head))
    d = ImageDraw.Draw(out)
    d.text((6, 8), labels[0], fill=(0, 0, 0))
    d.text((a.width + gap + 6, 8), labels[1], fill=(0, 0, 0))
    return out


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    a, b = argv[0], argv[1]
    box = tuple(float(v) for v in argv[2:6])
    side_by_side(picture(a, box), picture(b, box)).save(argv[6])
    return 0


if __name__ == "__main__":
    sys.exit(main())
