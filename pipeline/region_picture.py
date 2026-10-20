"""A picture of part of the stitched White Lake to Muskegon map.

    python pipeline/region_picture.py SOUTH WEST NORTH EAST OUT.png [--scale S]

The frame's ground and vegetation bitmaps as KnoxMap's preview colours them,
with every kept building drawn as the floor plan knoxbuild built, cut to a
lat/lon box. Trees are darkened ground, bushes a lighter green.
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_whitehall as B  # noqa: E402
import region  # noqa: E402
from build_island import get, open_bmp  # noqa: E402

ROOF = (150, 60, 45)
EDGE = (60, 25, 20)


def picture(box) -> Image.Image:
    fr = B.frame()
    s, w, n, e = box
    corners = [region.point_px(fr, lat, lon) for lat in (s, n) for lon in (w, e)]
    x0 = max(0, int(min(c[0] for c in corners)))
    y0 = max(0, int(min(c[1] for c in corners)))
    x1 = min(fr.width, int(max(c[0] for c in corners)))
    y1 = min(fr.height, int(max(c[1] for c in corners)))
    lm, _, _ = open_bmp(B.path(".bmp"))
    vm, _, _ = open_bmp(B.path("_veg.bmp"))
    ground = get(lm, x0, y0, x1 - x0, y1 - y0).astype(np.int16)
    veg = get(vm, x0, y0, x1 - x0, y1 - y0)
    px = ground.copy()
    treey = (veg[..., 0] > 0) & (veg[..., 1] == 0) & (veg[..., 2] == 0)
    px[treey] = (px[treey] * 0.45 + np.array([20, 70, 20]) * 0.55)
    bush = (veg[..., 0] > 0) & (veg[..., 1] == 0) & (veg[..., 2] > 0)
    px[bush] = (px[bush] * 0.6 + np.array([60, 110, 40]) * 0.4)
    px = px.clip(0, 255).astype(np.uint8)
    from knoxbuild.population import load_footprints
    for fx, fy, mask, _lv, _k in load_footprints(B.path("_footprints.npz")):
        h, wd = mask.shape
        if fx + wd < x0 or fx > x1 or fy + h < y0 or fy > y1:
            continue
        ys, xs = np.nonzero(mask)
        xs, ys = xs + fx - x0, ys + fy - y0
        ok = (xs >= 0) & (ys >= 0) & (xs < px.shape[1]) & (ys < px.shape[0])
        px[ys[ok], xs[ok]] = ROOF
        edge = mask & ~(np.pad(mask, 1)[2:, 1:-1] & np.pad(mask, 1)[:-2, 1:-1]
                        & np.pad(mask, 1)[1:-1, 2:] & np.pad(mask, 1)[1:-1, :-2])
        ys, xs = np.nonzero(edge)
        xs, ys = xs + fx - x0, ys + fy - y0
        ok = (xs >= 0) & (ys >= 0) & (xs < px.shape[1]) & (ys < px.shape[0])
        px[ys[ok], xs[ok]] = EDGE
    return Image.fromarray(px)


def overview(step: int) -> Image.Image:
    """The whole frame, one pixel every `step` tiles, buildings as dots."""
    fr = B.frame()
    lm, w, h = open_bmp(B.path(".bmp"))
    vm, _, _ = open_bmp(B.path("_veg.bmp"))
    rows = []
    for y in range(0, h, step):
        g = np.asarray(lm[y, :w * 3]).reshape(w, 3)[::step, ::-1].astype(np.int16)
        v = np.asarray(vm[y, :w * 3]).reshape(w, 3)[::step, ::-1]
        treey = (v[:, 0] > 0) & (v[:, 1] == 0) & (v[:, 2] == 0)
        g[treey] = g[treey] * 0.45 + np.array([20, 70, 20]) * 0.55
        rows.append(g.clip(0, 255).astype(np.uint8))
    px = np.stack(rows)
    from knoxbuild.population import load_footprints
    for fx, fy, mask, _lv, _k in load_footprints(B.path("_footprints.npz")):
        y, x = (fy + mask.shape[0] // 2) // step, (fx + mask.shape[1] // 2) // step
        if 0 <= y < px.shape[0] and 0 <= x < px.shape[1]:
            px[y, x] = ROOF
    del fr
    return Image.fromarray(px)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for k in ("south", "west", "north", "east"):
        ap.add_argument(k, type=float)
    ap.add_argument("out")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--overview", type=int, default=0,
                    help="the whole map instead, one pixel every N tiles")
    a = ap.parse_args(argv)
    if a.overview:
        im = overview(a.overview)
        im.save(a.out)
        print(f"{a.out}: {im.width}x{im.height}")
        return 0
    im = picture((a.south, a.west, a.north, a.east))
    if a.scale != 1.0:
        im = im.resize((int(im.width * a.scale), int(im.height * a.scale)), Image.LANCZOS)
    im.save(a.out)
    print(f"{a.out}: {im.width}x{im.height}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
