"""What the buildings are built of, where that is known.

KnoxMap picks each house's walls from its neighbourhood and how built-up the
place is. Along this shore the building stock is more particular than that:

* the cottages and beach houses round White Lake, along its channel to Lake
  Michigan and on the Lake Michigan shore are timber-framed and sided -
  clapboard, painted boards and vinyl siding in greys, blues, whites and the
  odd yellow or green - with hardly any brick among them;
* the old downtowns (Colby Street in Whitehall, Dowling Street in Montague,
  Western Avenue in Muskegon) are rows of two- and three-storey brick
  commercial blocks.

Each building there gets a KnoxMap zone of its own (knoxbuild/zones.py) the
size of its footprint, pinning its walls to a style drawn from that place's
mix. A building's draw depends only on where it stands, so the two windows
that build a building on a seam pick the same walls for it.
"""
from __future__ import annotations

import json
import os
import zlib

import shapely
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

# Wall styles by weight (knoxbuild/catalog.py HOUSE_STYLES names).
COTTAGES = [("clapboard", 30), ("siding_grey", 20), ("painted", 16), ("siding_blue", 14),
            ("siding_yellow", 7), ("siding_green", 6), ("timber", 5), ("logs", 2)]
DOWNTOWN = [("brick", 85), ("painted", 15)]

SHORE_REACH_M = 250       # beach houses: this far from Lake Michigan
INLAND_LAKE_REACH_M = 130  # cottages: this far from White Lake, Duck Lake, Bear Lake...
COTTAGE_LAKES = ("White Lake", "Duck Lake", "Little Duck Lake", "Bear Lake",
                 "Muskegon Lake", "Lake Michigan")
# Buildings whose walls are known, by name.
NAMED = {"White River Light Station": "brick", "White River Life Saving Station": "clapboard",
         "Montague Armory": "brick", "The Playhouse at White Lake": "clapboard"}
# (south, west, north, east)
DOWNTOWNS = {
    "Whitehall": (43.4085, -86.3505, 43.4115, -86.3425),
    "Montague": (43.4135, -86.3575, 43.4205, -86.3520),
    "Muskegon": (43.2300, -86.2600, 43.2400, -86.2420),
}

_shapes = None


def _areas(data):
    """(downtowns, shore band) in UTM, worked out once per process."""
    global _shapes
    if _shapes is None:
        downtowns = []
        for s, w, n, e in DOWNTOWNS.values():
            downtowns.append(data.utm(box(w, s, e, n)))
        lakes = [data.extent.difference(data.land).boundary.buffer(SHORE_REACH_M)]
        for g, tags in data.items:
            if tags.get("natural") == "water" and tags.get("name") in COTTAGE_LAKES \
                    and g.geom_type in ("Polygon", "MultiPolygon"):
                lakes.append(g.boundary.buffer(INLAND_LAKE_REACH_M))
        # Not the frame's own edge, which is not a shore.
        edge = data.extent.boundary.buffer(SHORE_REACH_M + 5)
        band = unary_union(lakes).difference(edge)
        _shapes = (shapely.prepared.prep(unary_union(downtowns)), shapely.prepared.prep(band))
    return _shapes


def _draw(mix, x: float, y: float) -> str:
    total = sum(w for _s, w in mix)
    r = zlib.crc32(f"{round(x)},{round(y)}".encode()) % total
    for style, w in mix:
        r -= w
        if r < 0:
            return style
    return mix[-1][0]


def write_zones(out_dir: str, map_name: str, feats, data) -> int:
    """A zone per building in the cottage band or a downtown; returns how many."""
    downtown, band = _areas(data)
    zones = []
    for f in feats:
        if "building" not in f.tags or f.kind not in ("way", "relation"):
            continue
        ring = f.geometry[0] if f.kind == "relation" else f.geometry
        if len(ring) < 3:
            continue
        ll = Polygon([(lon, lat) for lat, lon in ring])
        c = ll.centroid
        u = data.utm(Point(c.x, c.y))
        if f.tags.get("name") in NAMED:
            mix, label = [(NAMED[f.tags["name"]], 1)], "named"
        elif downtown.contains(u):
            mix, label = DOWNTOWN, "downtown"
        elif band.contains(u):
            mix, label = COTTAGES, "shore"
        else:
            continue
        outline = ll.buffer(0.00002, join_style=2)
        if outline.geom_type != "Polygon":
            continue
        zones.append({"name": label, "ring": [list(p) for p in outline.exterior.coords[:-1]],
                      "settings": {}, "wall_style": _draw(mix, u.x, u.y)})
    with open(os.path.join(out_dir, f"{map_name}_zones.json"), "w", encoding="utf-8") as fh:
        json.dump(zones, fh)
    return len(zones)
