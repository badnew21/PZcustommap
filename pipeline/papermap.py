"""The in-game map (M), spawn points and the mod's own extras.

knoxbuild writes the paper map for each window it builds, but a window is a
town's worth of the island; the map a player opens has to be all of it. This
draws it once for the whole frame with knoxbuild's own writers: Lake Superior
and the inland lakes, the woods, every trail (named, so the map labels them
like streets), the buildings, and the names of the harbours, lakes, islands,
peaks and campgrounds.
"""
from __future__ import annotations

import json
import os

import shapely
from shapely import wkb
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

import isle
from knoxbuild import world as kworld
from knoxbuild import worldmap as kmap

CELL = 300
DESCRIPTION = ("Isle Royale National Park, Michigan, at one tile to the metre: the whole "
               "island and its outer islands, 570 km2 of boreal forest, inland lakes and "
               "bog, the Greenstone and Minong ridge trails, campgrounds, Rock Harbor and "
               "Windigo. Made from Overture Maps (OpenStreetMap and ESA WorldCover) with "
               "KnoxMap.")

# Labels: (style layer, scale) as vanilla's annotations use them.
STYLE = {
    "island": ("text-town", 6.0),
    "settlement": ("text-town", 2.0),
    "lake": ("text-water-medium", 1.0),
    "bay": ("text-water-medium", 0.8),
    "islet": ("text-place", 0.5),
    "peak": ("text-place", 0.6),
    "camp": ("text-place", 0.5),
    "building": ("text-building", 0.5),
}
SETTLEMENTS = {"Rock Harbor": (48.1458, -88.4848), "Windigo": (47.9124, -89.1560)}


def _name(r):
    n = r.get("names")
    return (n or {}).get("primary") if isinstance(n, dict) else None


def _split(geom, x0, y0, x1, y1, out: list) -> None:
    """Cut a large polygon into one piece per map cell, halving the box each
    time: the map writer clips every feature against every cell it spans, and
    Lake Superior spans all of them."""
    if geom.is_empty:
        return
    nx, ny = (x1 - x0) // CELL, (y1 - y0) // CELL
    if nx <= 1 and ny <= 1:
        for part in getattr(geom, "geoms", None) or [geom]:
            if part.geom_type == "Polygon" and part.area >= 1:
                out.append(part)
        return
    if nx >= ny:
        xm = x0 + (nx // 2) * CELL
        halves = ((x0, y0, xm, y1), (xm, y0, x1, y1))
    else:
        ym = y0 + (ny // 2) * CELL
        halves = ((x0, y0, x1, ym), (x0, ym, x1, y1))
    for b in halves:
        _split(shapely.clip_by_rect(geom, *b), *b, out)


def write(project: str, fr, data, origin) -> None:
    kworld.set_origin(origin)
    W, H = fr.width, fr.height
    features: list[tuple[Polygon, str, str]] = []
    land_px = fr.utm_to_px(data.land).buffer(0)

    lake = box(0, 0, W, H).difference(land_px)
    pieces: list = []
    _split(lake, 0, 0, W, H, pieces)
    features += [(p, "water", "river") for p in pieces]

    woods, waters, streets = [], [], {}
    for g, tags in data.items:
        if tags.get("natural") == "wood" and g.geom_type in ("Polygon", "MultiPolygon"):
            woods.append(fr.utm_to_px(g))
        elif tags.get("natural") == "water" and g.geom_type in ("Polygon", "MultiPolygon"):
            waters.append(fr.utm_to_px(g))
        elif tags.get("highway") and g.geom_type == "LineString":
            line = fr.utm_to_px(g)
            value = "trail" if tags["highway"] in ("path", "footway", "track") else "tertiary"
            width = 2.0 if value == "trail" else 4.0
            strip = line.buffer(width / 2, cap_style=2, join_style=2)
            if strip.area >= 1:
                features.append((strip, "highway", value))
            if tags.get("name"):
                streets.setdefault(tags["name"], []).append((line, width))
        elif "building" in tags and g.geom_type in ("Polygon", "MultiPolygon"):
            for part in getattr(g, "geoms", None) or [g]:
                features.append((fr.utm_to_px(part), "building", "yes"))
    wood = unary_union(woods).buffer(0) if woods else None
    if wood is not None:
        pieces = []
        _split(wood, 0, 0, W, H, pieces)
        features += [(p, "natural", "forest") for p in pieces]
    for wpoly in waters:
        for part in getattr(wpoly, "geoms", None) or [wpoly]:
            features.append((part, "water", "river"))

    # Buildings and roads drawn last, over the woods.
    order = {"water": 0, "natural": 1, "highway": 2, "building": 3}
    features.sort(key=lambda f: order.get(f[1], 9))
    cleaned = [(kmap._clean(p), k, v) for p, k, v in features]
    cells = kmap._write_worldmap(os.path.join(project, "worldmap.xml"),
                                 [(p, k, v) for p, k, v in cleaned if p is not None], W, H)
    named = kmap._write_streets(os.path.join(project, "streets.xml"), streets)
    labels = _labels(fr, data)
    kmap._write_annotations(os.path.join(project, "worldmap-annotations.lua"), labels, W, H)
    print(f"paper map: {len(features)} features over {cells} cells, {named} named trails, "
          f"{len(labels)} labels")


def _labels(fr, data) -> list[tuple[str, str, float, float, float]]:
    proj = fr.projector()
    reach = data.park.buffer(isle.PARK_REACH_M) if data.park is not None else None
    out = []

    def add(name, kind, g_utm):
        if not name or (reach is not None and not reach.contains(g_utm.representative_point())):
            return
        p = fr.utm_to_px(g_utm.representative_point() if g_utm.geom_type != "Point" else g_utm)
        layer, scale = STYLE[kind]
        out.append((name, layer, scale, p.x, p.y))

    c = fr.utm_to_px(data.land.centroid)
    out.append(("ISLE ROYALE", *STYLE["island"], c.x, c.y))
    for name, (lat, lon) in SETTLEMENTS.items():
        x, y = proj.to_px(lat, lon)
        out.append((name, *STYLE["settlement"], x, y))

    rows = data.rows
    for r in rows["water"]:
        n = _name(r)
        if n == "Lake Superior":
            continue
        g = data.utm(wkb.loads(r["geometry"]))
        if r["class"] in ("lake", "pond") or (r["subtype"] == "water" and g.area > 40000 and n):
            add(n, "lake", g)
        elif r["class"] in ("bay", "strait") and n:
            add(n, "bay", g)
    for r in rows["land"]:
        n, cls = _name(r), r["class"]
        if not n:
            continue
        g = data.utm(wkb.loads(r["geometry"]))
        if cls in ("island", "islet") and n != "Isle Royale":
            add(n, "islet", g)
        elif cls in ("peak", "ridge"):
            add(n, "peak", g)
    seen = set()
    for key in ("land_use", "infrastructure"):
        for r in rows.get(key, []):
            n = _name(r)
            if r["class"] == "camp_site" and n and n not in seen:
                seen.add(n)
                add(n, "camp", data.utm(wkb.loads(r["geometry"])))
    for r in rows.get("building", []):
        n = _name(r)
        if n and not n[0].isdigit():
            add(n, "building", data.utm(wkb.loads(r["geometry"])))
    out.append(("Map data (c) OpenStreetMap contributors, Overture Maps, ESA WorldCover",
                "text-note", 0.35, min(fr.width - 1, 600), min(fr.height - 1, 40)))
    return out


def write_info(project: str, name: str, fr) -> None:
    """What make_map_mod reads about the map: its size and where it came from."""
    proj = fr.projector()
    s, w, n, e = proj.latlon_bbox()
    info = {"map_name": name, "bbox": {"south": s, "west": w, "north": n, "east": e},
            "osm_bbox": [s, w, n, e], "rotation": fr.rotation,
            "meters_per_tile": fr.meters_per_tile, "width_tiles": fr.width,
            "height_tiles": fr.height, "cells_x": fr.width // CELL,
            "cells_y": fr.height // CELL}
    with open(os.path.join(project, f"{name}_info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2)


def write_mod_extras(mod_root: str) -> None:
    """Credit the data the island is made from beyond what KnoxMap writes."""
    p = os.path.join(mod_root, "ATTRIBUTION.txt")
    extra = """
ISLE ROYALE
The island's shoreline, lakes, wetlands, trails, docks, campgrounds and
buildings are OpenStreetMap data as published by Overture Maps
(https://overturemaps.org, ODbL). Forest cover is from ESA WorldCover 2021
via Overture Maps: (c) ESA WorldCover project / Contains modified Copernicus
Sentinel data (2021) processed by ESA WorldCover consortium, CC BY 4.0.

Scale: one tile is one metre. North is turned 30 degrees: the island's long
axis, which runs about N60E, lies left to right on the map.
"""
    with open(p, "a", encoding="utf-8") as f:
        f.write(extra)
