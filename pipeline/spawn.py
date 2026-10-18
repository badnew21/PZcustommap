"""The zombie spawn map for the island.

KnoxMap puts zombies where people lived and worked, from an estimate of who
was in each building. That is the right answer in Rock Harbor and Windigo and
the wrong one for the other 99% of the island, where nobody lives: it would
leave the backcountry empty. A national park in summer is full of hikers,
paddlers and campers, so the wilderness gets a thin scatter of its own:

* along the trails, now and then one or two;
* at the campgrounds and shelters, a small group;
* in the developed areas, what knoxbuild worked out from the buildings.

Values follow KnoxMap's (and vanilla's) scale: 0-10 zombies a 10 x 10 tile
square, and most of the map 0.
"""
from __future__ import annotations

import os

import numpy as np
from PIL import Image, ImageDraw

SCALE = 10            # tiles per spawn map pixel
TRAIL_CHANCE = 0.05   # of trail pixels with somebody on them
TRAIL_DENSITY = (1, 2)
CAMP_RADIUS = 4       # spawn map pixels, 40 m
CAMP_CHANCE = 0.3
CAMP_DENSITY = (1, 4)
SEED = 0x15E


def write(out_path: str, fr, data, windows: list[dict], windows_dir: str) -> None:
    w, h = fr.width // SCALE, fr.height // SCALE
    trail = Image.new("L", (w, h), 0)
    camp = Image.new("L", (w, h), 0)
    td, cd = ImageDraw.Draw(trail), ImageDraw.Draw(camp)
    for g, tags in data.items:
        hw = tags.get("highway")
        if hw in ("path", "track", "service") and g.geom_type == "LineString":
            p = fr.utm_to_px(g)
            td.line([(x / SCALE, y / SCALE) for x, y in p.coords], fill=255, width=1)
        elif tags.get("landuse") == "grass" and g.geom_type in ("Polygon", "MultiPolygon"):
            for part in getattr(g, "geoms", None) or [g]:
                p = fr.utm_to_px(part)
                cd.polygon([(x / SCALE, y / SCALE) for x, y in p.exterior.coords], fill=255)
    # Campsites mapped only as a point.
    import pyarrow.parquet as pq
    from shapely import wkb
    infra = os.path.join(data.folder, "infrastructure.parquet")
    if os.path.exists(infra):
        for r in pq.read_table(infra).to_pylist():
            if r["class"] != "camp_site":
                continue
            p = fr.utm_to_px(data.utm(wkb.loads(r["geometry"])).centroid)
            cd.ellipse((p.x / SCALE - CAMP_RADIUS, p.y / SCALE - CAMP_RADIUS,
                        p.x / SCALE + CAMP_RADIUS, p.y / SCALE + CAMP_RADIUS), fill=255)

    rng = np.random.default_rng(SEED)
    out = np.zeros((h, w), np.uint8)
    t = np.asarray(trail) > 0
    pick = t & (rng.random((h, w)) < TRAIL_CHANCE)
    out[pick] = rng.integers(TRAIL_DENSITY[0], TRAIL_DENSITY[1] + 1, pick.sum())
    c = np.asarray(camp) > 0
    pick = c & (rng.random((h, w)) < CAMP_CHANCE)
    out[pick] = np.maximum(out[pick], rng.integers(CAMP_DENSITY[0], CAMP_DENSITY[1] + 1, pick.sum()))

    # The developed areas, as knoxbuild counted them.
    for r in windows:
        p = os.path.join(windows_dir, r["name"], f"{r['name']}_ZombieSpawnMap.bmp")
        if not os.path.exists(p):
            continue
        a = np.asarray(Image.open(p).convert("L"))
        x0, y0 = r["ox"] // SCALE, r["oy"] // SCALE
        hh, ww = min(a.shape[0], h - y0), min(a.shape[1], w - x0)
        out[y0:y0 + hh, x0:x0 + ww] = np.maximum(out[y0:y0 + hh, x0:x0 + ww], a[:hh, :ww])

    Image.fromarray(out, "L").save(out_path, format="BMP")
    nz = int((out > 0).sum())
    print(f"spawn map: {w}x{h}, {nz} pixels populated ({100 * nz / out.size:.2f}%), "
          f"density total {int(out.sum())}")
