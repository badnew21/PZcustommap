"""White Lake to Muskegon: the frame and the data, for a region built in windows.

A town of a few square kilometres goes through KnoxMap in one go
(build_town.py). The shore from Montague and Whitehall down to Muskegon is
about 21 x 27 km, half a billion tiles, with towns at both ends and farms,
woods and dunes between, so it is drawn and built one window at a time the way
Isle Royale is (isle.py), with the town pipeline's reading of the data
(town.py): every road class, real building heights, businesses inside their
buildings.

* The frame is north-up: turned only by the half degree that UTM's grid north
  is off true north here, so Muskegon's and Whitehall's streets run along the
  tiles instead of stepping every hundred metres.
* Lake Michigan is everything in the frame that is not land. The inland lakes
  (White Lake, Duck Lake, Muskegon Lake, Bear Lake) are mapped water.
* ESA WorldCover land cover is the base the mapped details are painted over:
  forest, scrub, wetland, farmland, and the bare sand of the dunes.
"""
from __future__ import annotations

import json
import math
import os

import numpy as np
import pyarrow.parquet as pq
import pyproj
import shapely
from shapely import wkb
from shapely.affinity import rotate
from shapely.geometry import Point, box
from shapely.ops import unary_union

import isle  # noqa: F401  (KnoxMap on the path)
import town
from generator.osm import OSMFeature

EPSG = 32616
CELL = isle.CELL
# Montague and the White River in the north, Muskegon down to Sherman
# Boulevard in the south; the Lake Michigan shore in the west, US-31 and the
# east side of Muskegon in the east.
BBOX = (43.205, -86.465, 43.445, -86.205)          # south, west, north, east
# Grid north is 0.48 degrees west of true north at 86.3 W; turning the frame
# back by that puts true north up.
ROTATION = -0.48
LAKE_NAME = "Lake Michigan"
GREAT_LAKE_M2 = 1e9

LAND_COVER = {"forest": {"natural": "wood"},
              "shrub": {"natural": "scrub"},
              "wetland": {"natural": "wetland"},
              "crop": {"landuse": "farmland"},
              # Bare ground along this shore is the dunes: Duck Lake, Muskegon
              # State Park, the Pere Marquette beach and the White Lake channel.
              "barren": {"natural": "sand"}}

# What Overture has as a plain building but is something KnoxMap builds its
# own way. Matched by name.
BUILDING_TAGS = {
    # The 1875 light station on the south side of the White Lake channel, a
    # museum now: a brick keeper's house, its tower standing at the lake end.
    # The house is built as a building people can walk into, and the tower
    # beside it (TOWERS) as KnoxMap's lighthouse.
    "White River Light Station": {"tourism": "museum", "historic": "lighthouse",
                                  "building:levels": "2", "building:material": "brick"},
    "The Playhouse at White Lake": {"amenity": "theatre", "building:levels": "2"},
}
# Towers stood against a building's end: name -> (which end, storeys). The
# White River light is 38 feet to the lantern, about four storeys.
TOWERS = {"White River Light Station": ("west", 4)}


# Businesses this far from a building are moved into it, if it is at least
# this big (a shop, not the shed beside it).
PLACE_REACH_M = 25
PLACE_MIN_M2 = 40
# Overture's own confidence that a business is where it says. The town
# pipeline asks for 0.6; here 0.5 takes in another 160 or so.
PLACE_CONFIDENCE = 0.5

# Most footprints here are machine-detected, with no use on them, so the
# detached garage and the shed out the back came out as tiny houses. An
# untagged footprint this small with a house-sized one this near is one,
GARAGE_M2 = (30, 85)
SHED_M2 = (6, 30)
HOUSE_MIN_M2 = 70
OUTBUILDING_REACH_M = 20
SMALL_HOUSE_M2 = 300
HOUSE_CLASSES = {"house", "detached", "bungalow", "cabin", "semidetached_house"}
# ...unless it has this many buildings of about its own size this near.
COLONY_REACH_M = 30
COLONY_PEERS = 3


def place_tags(r) -> dict | None:
    """town.place_tags, at this map's confidence."""
    if (r.get("confidence") or 0) < PLACE_CONFIDENCE:
        return None
    return town.place_tags({**r, "confidence": max(r.get("confidence") or 0,
                                                   town.MIN_PLACE_CONFIDENCE)})


def _outbuildings(geoms: list, classes: list) -> dict[int, str]:
    """{index: "garage" | "shed"} for the untagged footprints that are a
    house's outbuilding: small, and beside a footprint at least twice their
    size and big enough to be the house."""
    tree = shapely.STRtree(geoms)
    out = {}
    for i, (g, cls) in enumerate(zip(geoms, classes)):
        if cls:
            continue
        a = g.area
        kind = "garage" if GARAGE_M2[0] <= a < GARAGE_M2[1] else \
            "shed" if SHED_M2[0] <= a < SHED_M2[1] else None
        if kind is None:
            continue
        near = [j for j in tree.query(g.buffer(COLONY_REACH_M))
                if j != i and geoms[j].distance(g) <= COLONY_REACH_M]
        # A row of small buildings of a size is a colony of cottages or
        # cabins - the Wabaningo Club's on Murray Road, a campground's - not
        # a house's garages.
        if sum(1 for j in near if 0.5 * a <= geoms[j].area <= 2 * a) >= COLONY_PEERS:
            continue
        if any(geoms[j].area >= max(HOUSE_MIN_M2, 2 * a)
               and geoms[j].distance(g) <= OUTBUILDING_REACH_M for j in near):
            out[i] = kind
    return out


def _name(r):
    return town._name(r)


def make_frame() -> isle.Frame:
    to_utm = pyproj.Transformer.from_crs(4326, EPSG, always_xy=True)
    s, w, n, e = BBOX
    pts = [to_utm.transform(lon, lat) for lat in (s, n) for lon in (w, e)]
    cx = sum(p[0] for p in pts) / 4
    cy = sum(p[1] for p in pts) / 4
    turned = rotate(shapely.MultiPoint(pts), ROTATION, origin=(cx, cy))
    x0, y0, x1, y1 = turned.bounds
    width = math.ceil((x1 - x0) / CELL) * CELL
    height = math.ceil((y1 - y0) / CELL) * CELL
    return isle.Frame(cx, cy, width, height, rotation=ROTATION)


def save_frame(path: str, fr) -> None:
    isle.save_frame(path, fr)


def load_frame(path: str):
    return isle.load_frame(path)


class Data:
    """Every Overture feature in the frame, in UTM metres, with OSM tags."""

    def __init__(self, folder: str, fr):
        self.folder = folder
        self.fr = fr
        self.to_utm = pyproj.Transformer.from_crs(4326, EPSG, always_xy=True)
        self.to_ll = pyproj.Transformer.from_crs(EPSG, 4326, always_xy=True)
        self.rows = {k: pq.read_table(os.path.join(folder, f"{k}.parquet")).to_pylist()
                     for k in ("water", "land", "land_cover", "land_use", "infrastructure",
                               "segment", "building", "place")
                     if os.path.exists(os.path.join(folder, f"{k}.parquet"))}
        self.extent = fr.px_to_utm(box(-600, -600, fr.width + 600, fr.height + 600))
        # Overture's land here is a one-degree tile, not a coastline; the
        # shore is the edge of the Lake Michigan polygon in the water theme.
        ll_extent = self.ll(self.extent.buffer(2000))
        lakes = []
        for r in self.rows["water"]:
            if _name(r) == LAKE_NAME and r["class"] == "lake":
                g = wkb.loads(r["geometry"]).intersection(ll_extent)
                if not g.is_empty:
                    lakes.append(self.utm(g))
        self.land = self.extent.difference(unary_union(lakes)).buffer(0)
        self.items: list[tuple] = []
        self._collect()
        self.tree = shapely.STRtree([g for g, _t in self.items])

    def utm(self, g):
        return shapely.transform(g, lambda c: np.column_stack(
            self.to_utm.transform(c[:, 0], c[:, 1])))

    def ll(self, g):
        return shapely.transform(g, lambda c: np.column_stack(
            self.to_ll.transform(c[:, 0], c[:, 1])))

    def _add(self, g, tags):
        if g is not None and not g.is_empty:
            self.items.append((g, tags))

    def _rows(self, kind):
        """(row, UTM geometry) for every row of a kind that reaches the frame."""
        prepared = shapely.prepared.prep(self.extent)
        for r in self.rows.get(kind, []):
            g = self.utm(wkb.loads(r["geometry"]))
            if prepared.intersects(g):
                yield r, g

    def _collect(self):
        land = self.land
        # Land cover first: it is the base everything mapped is drawn over.
        for r, g in self._rows("land_cover"):
            if r["cartography"]["min_zoom"] < 8:
                continue          # the world-scale generalisation
            tags = LAND_COVER.get(r["subtype"])
            if tags:
                self._add(g.intersection(land), dict(tags))

        for r, g in self._rows("land"):
            if r["subtype"] in ("land", "physical", "reef"):
                continue
            tags = town._tags(r)
            if not tags:
                continue
            if g.geom_type in ("Polygon", "MultiPolygon"):
                g = g.intersection(land)
            self._add(g, tags)

        for r, g in self._rows("water"):
            if r["subtype"] == "physical":
                continue
            if _name(r) == LAKE_NAME or g.area > GREAT_LAKE_M2:
                continue          # Lake Michigan is what is not land
            if r["class"] == "swimming_pool":
                tags = {"leisure": "swimming_pool"}
            else:
                tags = town._tags(r)
                if g.geom_type in ("Polygon", "MultiPolygon"):
                    tags = {**tags, "natural": "water"}
                elif "waterway" not in tags:
                    tags = {**tags, "waterway": "stream"}
            if _name(r):
                tags["name"] = _name(r)
            self._add(g, tags)

        for kind in ("land_use", "infrastructure"):
            for r, g in self._rows(kind):
                tags = town._tags(r)
                if not tags:
                    continue
                if _name(r) and "name" not in tags:
                    tags["name"] = _name(r)
                self._add(g, tags)

        for r, g in self._rows("segment"):
            tags = town._segment_tags(r)
            if not tags or g.geom_type != "LineString":
                continue
            ll = wkb.loads(r["geometry"])
            for part, level, flags in town._pieces(ll, r):
                if "is_indoor" in flags:
                    continue
                t = dict(tags)
                if "is_tunnel" in flags or level < 0:
                    t["tunnel"] = "yes"
                    t["layer"] = str(min(-1, level))
                elif "is_bridge" in flags or level > 0:
                    t["bridge"] = "yes"
                    t["layer"] = str(max(1, level))
                self._add(self.utm(part), t)

        buildings = [(r, g) for r, g in self._rows("building")
                     if g.geom_type in ("Polygon", "MultiPolygon") and not r.get("is_underground")]
        outbuildings = _outbuildings([g for _r, g in buildings],
                                     [r.get("class") for r, _g in buildings])
        for i, (r, g) in enumerate(buildings):
            tags = {"building": r.get("class") or outbuildings.get(i) or "yes"}
            if _name(r):
                tags["name"] = _name(r)
            if r.get("height"):
                tags["height"] = f"{float(r['height']):.1f}"
                # A machine-measured height is to the roof ridge, and KnoxMap
                # counts a storey per 3 m of it, so a two-storey house under a
                # steep roof came out three storeys - and at three storeys
                # KnoxMap builds a block of flats. A small house, or a small
                # building nobody has tagged, is a house of two.
                if (r.get("class") or "house") in HOUSE_CLASSES and not r.get("num_floors") \
                        and g.area < SMALL_HOUSE_M2 and float(r["height"]) >= 8.5:
                    tags["building:levels"] = "2"
            if r.get("num_floors"):
                tags["building:levels"] = str(int(r["num_floors"]))
            if r.get("roof_shape"):
                tags["roof:shape"] = r["roof_shape"]
            if r.get("facade_material"):
                tags["building:material"] = r["facade_material"]
            tags.update(BUILDING_TAGS.get(_name(r) or "", {}))
            self._add(g, tags)
            if _name(r) in TOWERS:
                self._add(self._tower_at(g, *TOWERS[_name(r)]),
                          {"man_made": "lighthouse", "building:levels":
                           str(TOWERS[_name(r)][1]), "name": _name(r)})

        # Businesses, moved into the building they are in. Overture puts a
        # good third of them on the street in front, or on the plot beside
        # the shop; knoxbuild only fits out a building with a point inside it.
        footprints = [g for _r, g in buildings]
        tree = shapely.STRtree(footprints)
        for r, g in self._rows("place"):
            tags = place_tags(r)
            if not tags or g.geom_type != "Point":
                continue
            if not any(footprints[i].contains(g) for i in tree.query(g)):
                near = [i for i in tree.query(g.buffer(PLACE_REACH_M))
                        if footprints[i].area >= PLACE_MIN_M2
                        and footprints[i].distance(g) <= PLACE_REACH_M]
                if near:
                    best = min(near, key=lambda i: footprints[i].distance(g))
                    g = footprints[best].representative_point()
            self._add(g, tags)

    @staticmethod
    def _tower_at(g, end: str, _levels: int):
        """A point just outside one end of a footprint, for a tower there."""
        pts = list(g.exterior.coords) if g.geom_type == "Polygon" else \
            list(max(g.geoms, key=lambda p: p.area).exterior.coords)
        x, y = {"west": min(pts), "east": max(pts),
                "north": max(pts, key=lambda p: p[1]),
                "south": min(pts, key=lambda p: p[1])}[end]
        c = g.centroid
        d = math.hypot(x - c.x, y - c.y) or 1.0
        return Point(x + (x - c.x) / d * 2.5, y + (y - c.y) / d * 2.5)

    def features(self, window_utm) -> list[OSMFeature]:
        """Every feature in the window, cut to it, as KnoxMap features."""
        out: list[OSMFeature] = []
        ident = [1]

        def emit(g, tags):
            for part in getattr(g, "geoms", None) or [g]:
                if part.is_empty:
                    continue
                ll = self.ll(part)
                if part.geom_type == "Polygon":
                    if part.area < 2.0:
                        continue
                    outer = [(y, x) for x, y in ll.exterior.coords]
                    holes = [[(y, x) for x, y in ring.coords] for ring in ll.interiors]
                    if holes:
                        f = OSMFeature(ident[0], "relation", dict(tags), [outer] + holes)
                        f.role_geoms = [("outer", outer)] + [("inner", h) for h in holes]
                    else:
                        f = OSMFeature(ident[0], "way", dict(tags), outer)
                elif part.geom_type == "LineString":
                    f = OSMFeature(ident[0], "way", dict(tags), [(y, x) for x, y in ll.coords])
                elif part.geom_type == "Point":
                    f = OSMFeature(ident[0], "node", dict(tags), [(ll.y, ll.x)])
                else:
                    continue
                ident[0] += 1
                out.append(f)

        lake = window_utm.difference(self.land)
        if not lake.is_empty:
            emit(lake, {"natural": "water", "name": LAKE_NAME})
        for i in self.tree.query(window_utm):
            g, tags = self.items[i]
            if "building" in tags:
                # Buildings whole, never cut: a window keeps or drops each one
                # by where it stands (build_whitehall.py).
                if window_utm.intersects(g):
                    emit(g, tags)
                continue
            cut = g.intersection(window_utm)
            if cut.is_empty:
                continue
            for part in getattr(cut, "geoms", None) or [cut]:
                if part.geom_type in ("Polygon", "LineString", "Point"):
                    emit(part, tags)
        return out

    def buildings_utm(self) -> list:
        return [g for g, t in self.items if "building" in t]


def frame_cells_with_land(fr, data) -> np.ndarray:
    """(cells_y, cells_x) bool: which 300-tile cells of the frame hold land."""
    land_px = fr.utm_to_px(data.land)
    out = np.zeros((fr.height // CELL, fr.width // CELL), bool)
    prepared = shapely.prepared.prep(land_px)
    for cy in range(out.shape[0]):
        for cx in range(out.shape[1]):
            out[cy, cx] = prepared.intersects(box(cx * CELL, cy * CELL,
                                                  (cx + 1) * CELL, (cy + 1) * CELL))
    return out


def point_px(fr, lat, lon):
    t = pyproj.Transformer.from_crs(4326, EPSG, always_xy=True)
    p = fr.utm_to_px(Point(*t.transform(lon, lat)))
    return p.x, p.y


def dump(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)


# The coast: what is kept when the map is cut down to the shore. Lake
# Michigan's beaches, dunes and shore roads, and round the drowned river
# mouths that open on to it - White Lake and Muskegon Lake, with Bear Lake
# off Muskegon Lake - their shores and the towns on them.
LAKE_MICHIGAN_REACH_M = 3000
COASTAL_LAKES = {"White Lake": 1500, "Muskegon Lake": 1500, "Bear Lake": 1000}


def coast_utm(data):
    """The coast, as a UTM polygon: the shore and the water off it. How much of
    Lake Michigan is kept is up to the compile, which keeps one cell of open
    lake round the land."""
    lake = data.extent.difference(data.land)
    parts = [lake.buffer(LAKE_MICHIGAN_REACH_M)]
    for g, tags in data.items:
        reach = COASTAL_LAKES.get(tags.get("name"))
        if reach and tags.get("natural") == "water" \
                and g.geom_type in ("Polygon", "MultiPolygon"):
            parts.append(g.buffer(reach))   # the lake itself and its shores
    return unary_union(parts).intersection(data.extent)
