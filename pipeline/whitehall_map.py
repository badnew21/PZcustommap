"""The in-game map (M) and the starting places, for White Lake to Muskegon.

knoxbuild writes a paper map for each window it builds, but a window is 3 km
of a 21 x 27 km map; the one a player opens has to be all of it. This draws it
once for the whole frame with knoxbuild's own writers, the way Isle Royale's
papermap.py does: Lake Michigan and the inland lakes, the woods, every road
and street (named, so the map labels them), the buildings, and the names of
the towns, lakes, parks and landmarks.

Starting places are spawn regions (regions.py): each town gets a folder of
its own beside the map's, with start points on floor tiles inside homes
knoxbuild built there.
"""
from __future__ import annotations

import os

import numpy as np
from shapely import wkb
from shapely.geometry import box
from shapely.ops import unary_union

import papermap
from knoxbuild import world as kworld
from knoxbuild import worldmap as kmap

CELL = 300
STYLE = papermap.STYLE | {"town": ("text-town", 3.0), "lake_big": ("text-water-medium", 2.0),
                          "park": ("text-place", 0.8)}
TOWNS = {"Whitehall": (43.4100, -86.3440), "Montague": (43.4165, -86.3580),
         "North Muskegon": (43.2560, -86.2680), "Muskegon": (43.2330, -86.2500),
         "Lakewood Club": (43.3750, -86.2620), "Lakeside": (43.2140, -86.3020)}
BIG_LAKES = {"Lake Michigan": (43.3000, -86.4500)}
PARKS = ("Muskegon State Park", "Duck Lake State Park", "Pere Marquette Park",
         "Hart-Montague Trail State Park", "Funnell Field", "Goodrich Park",
         "Hackley Park", "Kruse Park", "Lake Michigan Beach")
ROAD_WIDTH = {"motorway": 10.0, "trunk": 9.0, "primary": 8.0, "secondary": 7.0,
              "tertiary": 6.0, "residential": 5.0, "unclassified": 5.0, "road": 5.0,
              "living_street": 4.0, "service": 3.0, "track": 2.0, "path": 1.5,
              "footway": 1.5, "cycleway": 1.5, "steps": 1.5, "pedestrian": 3.0,
              "bridleway": 1.5}
ROAD_VALUE = {"motorway": "primary", "trunk": "primary", "primary": "primary",
              "secondary": "secondary", "tertiary": "tertiary"}

# Where a player can start: (lat, lon, metres round it to look for homes).
PLACES = {"Whitehall": (43.4090, -86.3420, 900), "Montague": (43.4170, -86.3580, 800),
          "White Lake Channel": (43.3800, -86.4150, 1200),
          "Lakewood Club": (43.3750, -86.2620, 900),
          "North Muskegon": (43.2560, -86.2700, 900),
          "Muskegon": (43.2330, -86.2500, 1200),
          "Lakeside": (43.2140, -86.3020, 700)}
SPAWNS_PER_PLACE = 6


def write(project: str, fr, data, origin) -> None:
    kworld.set_origin(origin)
    W, H = fr.width, fr.height
    features = []
    land_px = fr.utm_to_px(data.land).buffer(0)
    lake = box(0, 0, W, H).difference(land_px)
    pieces: list = []
    papermap._split(lake, 0, 0, W, H, pieces)
    features += [(p, "water", "river") for p in pieces]

    woods, waters, streets = [], [], {}
    for g, tags in data.items:
        poly = g.geom_type in ("Polygon", "MultiPolygon")
        if tags.get("natural") == "wood" and poly:
            woods.append(fr.utm_to_px(g))
        elif tags.get("natural") == "water" and poly:
            waters.append(fr.utm_to_px(g))
        elif tags.get("highway") in ROAD_WIDTH and g.geom_type == "LineString" \
                and tags.get("tunnel") != "yes":
            line = fr.utm_to_px(g)
            hw = tags["highway"]
            width = ROAD_WIDTH[hw]
            value = ROAD_VALUE.get(hw, "trail" if width < 2.5 else "tertiary")
            strip = line.buffer(width / 2, cap_style=2, join_style=2)
            if strip.area >= 1:
                features.append((strip, "highway", value))
            if tags.get("name") and width >= 3:
                streets.setdefault(tags["name"], []).append((line, width))
        elif tags.get("railway") == "rail" and g.geom_type == "LineString":
            strip = fr.utm_to_px(g).buffer(1.5, cap_style=2, join_style=2)
            if strip.area >= 1:
                features.append((strip, "railway", "rail"))
        elif "building" in tags and poly:
            for part in getattr(g, "geoms", None) or [g]:
                features.append((fr.utm_to_px(part), "building", "yes"))
    if woods:
        pieces = []
        papermap._split(unary_union(woods).buffer(0), 0, 0, W, H, pieces)
        features += [(p, "natural", "forest") for p in pieces]
    for wpoly in waters:
        for part in getattr(wpoly, "geoms", None) or [wpoly]:
            features.append((part, "water", "river"))

    order = {"natural": 0, "water": 1, "railway": 2, "highway": 3, "building": 4}
    features.sort(key=lambda f: order.get(f[1], 9))
    cleaned = [(kmap._clean(p), k, v) for p, k, v in features]
    cells = kmap._write_worldmap(os.path.join(project, "worldmap.xml"),
                                 [(p, k, v) for p, k, v in cleaned if p is not None], W, H)
    named = kmap._write_streets(os.path.join(project, "streets.xml"), streets)
    labels = _labels(fr, data)
    kmap._write_annotations(os.path.join(project, "worldmap-annotations.lua"), labels, W, H)
    print(f"paper map: {len(features)} features over {cells} cells, {named} named streets, "
          f"{len(labels)} labels")


def _labels(fr, data) -> list:
    proj = fr.projector()
    out = []

    def inside(x, y):
        return 0 <= x < fr.width and 0 <= y < fr.height

    def add(name, kind, g_utm):
        p = fr.utm_to_px(g_utm.representative_point() if g_utm.geom_type != "Point" else g_utm)
        if name and inside(p.x, p.y):
            out.append((name, *STYLE[kind], p.x, p.y))

    for name, (lat, lon) in TOWNS.items():
        x, y = proj.to_px(lat, lon)
        out.append((name, *STYLE["town"], x, y))
    for name, (lat, lon) in BIG_LAKES.items():
        x, y = proj.to_px(lat, lon)
        out.append((name.upper(), *STYLE["lake_big"], x, y))
    rows = data.rows
    seen = set()
    for r in rows["water"]:
        n = papermap._name(r)
        if not n or n == "Lake Michigan" or n in seen:
            continue
        g = data.utm(wkb.loads(r["geometry"]))
        if r["class"] in ("lake", "pond", "river", "reservoir") and g.geom_type != "LineString" \
                and g.area > 20000:
            seen.add(n)
            add(n, "lake_big" if g.area > 2e6 else "lake", g)
    for r in rows.get("land_use", []):
        n = papermap._name(r)
        if n in PARKS and n not in seen:
            seen.add(n)
            add(n, "park", data.utm(wkb.loads(r["geometry"])))
    for g, tags in data.items:
        n = tags.get("name")
        if "building" in tags and n and not n[0].isdigit() and n not in seen \
                and (tags.get("amenity") or tags.get("tourism") or tags.get("historic")
                     or tags.get("building") in ("school", "church", "college", "university",
                                                 "civic", "government", "hospital",
                                                 "public", "train_station")):
            seen.add(n)
            add(n, "building", g)
    out.append(("Map data (c) OpenStreetMap contributors, Overture Maps, Microsoft, "
                "ESA WorldCover", "text-note", 0.35, min(fr.width - 1, 900), min(fr.height - 1, 40)))
    return out


def spawn_places(fr, owned_footprints) -> dict[str, list]:
    """{place: [(x, y) frame tiles]}: inside homes knoxbuild built in each town.

    `owned_footprints` is [(x0, y0, mask, levels, kind)] in frame tiles, for
    the buildings the map kept."""
    proj = fr.projector()
    homes = [b for b in owned_footprints if b[4] in ("house", "apartment")]
    out = {}
    for place, (lat, lon, reach) in PLACES.items():
        cx, cy = proj.to_px(lat, lon)
        near = []
        for x0, y0, mask, _lv, _k in homes:
            h, w = mask.shape
            mx, my = x0 + w / 2, y0 + h / 2
            if (mx - cx) ** 2 + (my - cy) ** 2 <= reach ** 2 and mask.sum() >= 40:
                near.append((x0, y0, mask))
        picked: list[tuple[int, int]] = []
        # The building furthest from those picked so far, each time.
        pool = sorted(near, key=lambda b: (b[0] - cx) ** 2 + (b[1] - cy) ** 2)
        while pool and len(picked) < SPAWNS_PER_PLACE:
            if picked:
                pool.sort(key=lambda b: -min((b[0] - px) ** 2 + (b[1] - py) ** 2
                                              for px, py in picked))
            x0, y0, mask = pool.pop(0)
            tile = _inner_tile(mask)
            if tile:
                picked.append((x0 + tile[0], y0 + tile[1]))
        if picked:
            out[place] = picked
    return out


def _inner_tile(mask):
    m = mask.astype(bool)
    core = m.copy()
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
        core &= np.roll(np.roll(m, dy, 0), dx, 1)
    ys, xs = np.nonzero(core if core.any() else m)
    if len(xs) == 0:
        return None
    my, mx = ys.mean(), xs.mean()
    i = int(np.argmin((xs - mx) ** 2 + (ys - my) ** 2))
    return int(xs[i]), int(ys[i])


def write_mod_extras(mod_root: str) -> None:
    p = os.path.join(mod_root, "ATTRIBUTION.txt")
    extra = """
WHITE LAKE TO MUSKEGON
The shoreline, lakes, rivers, streets, land use, piers and buildings are
OpenStreetMap data as published by Overture Maps (https://overturemaps.org,
ODbL). Most building outlines and heights are Microsoft's machine-detected
building footprints as published by Overture Maps (ODbL). Land cover is from
ESA WorldCover 2021 via Overture Maps: (c) ESA WorldCover project / Contains
modified Copernicus Sentinel data (2021) processed by ESA WorldCover
consortium, CC BY 4.0.

Scale: one tile is one metre. North is up.
"""
    with open(p, "a", encoding="utf-8") as f:
        f.write(extra)
