"""Isle Royale for Project Zomboid, built with KnoxMap's generator.

KnoxMap draws a map from OpenStreetMap in one go, which is fine for a town and
too much for an island 73 km long at one tile a metre: its renderer holds the
whole map in memory at about 80 bytes a tile, and Isle Royale is over a billion
tiles. So this keeps one fixed frame for the whole island and hands KnoxMap one
window of it at a time:

* the frame is a rectangle of whole 300-tile cells in UTM 16N, turned so the
  island's long axis runs along the tiles (`Frame`);
* a window is any rectangle of that frame, and `SectionProjector` is a KnoxMap
  `Projector` that puts lat/lon onto the window's pixels exactly where the
  whole frame would put them, so windows drawn separately line up to the tile;
* the features come from Overture Maps (OSM data plus land cover), turned into
  KnoxMap's `OSMFeature` so its own classifier and painters do the work.

Nothing in KnoxMap is changed. `Projector.build` is answered from a registry
while a window is being rendered or built, because both the renderer and
knoxbuild call it with the bbox they were given.
"""
from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass, fields

import numpy as np
import pyarrow.parquet as pq
import pyproj
import shapely
from shapely import wkb
from shapely.affinity import rotate
from shapely.ops import unary_union

HERE = os.path.dirname(os.path.abspath(__file__))
KNOXMAP = os.environ.get("KNOXMAP_DIR", os.path.join(os.path.dirname(os.path.dirname(HERE)),
                                                       "badnew21", "knoxmapdm"))
if KNOXMAP not in sys.path:
    sys.path.insert(0, KNOXMAP)

from generator.osm import OSMFeature  # noqa: E402
from generator.rendering import projection as _projection  # noqa: E402
from generator.rendering.projection import Projector  # noqa: E402

CELL = 300            # KnoxMap's bitmap cell, which the world origin is counted in
EPSG = 32616          # UTM zone 16N covers the whole island
# Turned 30 degrees clockwise the island's long axis (it runs about N60E) lies
# along the tiles: 73 x 15 km instead of 65 x 40 km north-up.
ROTATION = -30.0
MARGIN_M = 900        # open water kept round the outermost islands
PARK_NAME = "Isle Royale National Park"
PARK_REACH_M = 7200
MAINLAND_M2 = 2e9      # nothing in the park is a twentieth of this


# --- the frame ---------------------------------------------------------------

@dataclass
class Frame:
    """The whole map: a rectangle of tiles in UTM, turned about its centre."""
    cx: float           # centre, UTM metres
    cy: float
    width: int          # tiles, whole cells
    height: int
    rotation: float = ROTATION
    meters_per_tile: float = 1.0

    def to_json(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_json(cls, d: dict) -> "Frame":
        return cls(**d)

    def projector(self) -> Projector:
        t = pyproj.Transformer.from_crs("EPSG:4326", f"EPSG:{EPSG}", always_xy=True)
        w_m, h_m = self.width * self.meters_per_tile, self.height * self.meters_per_tile
        p = Projector(0, 0, 0, 0, self.meters_per_tile, self.width, self.height,
                      self.cx - w_m / 2, self.cy - h_m / 2, t, self.rotation)
        s, w, n, e = p.latlon_bbox()
        p.south, p.west, p.north, p.east = s, w, n, e
        return p

    def utm_to_px(self, geom):
        """A shapely geometry in UTM metres, in frame pixels (y down)."""
        g = rotate(geom, self.rotation, origin=(self.cx, self.cy), use_radians=False)
        x0 = self.cx - self.width * self.meters_per_tile / 2
        y1 = self.cy + self.height * self.meters_per_tile / 2
        return shapely.transform(g, lambda c: np.column_stack(
            ((c[:, 0] - x0) / self.meters_per_tile, (y1 - c[:, 1]) / self.meters_per_tile)))

    def px_to_utm(self, geom):
        x0 = self.cx - self.width * self.meters_per_tile / 2
        y1 = self.cy + self.height * self.meters_per_tile / 2
        g = shapely.transform(geom, lambda c: np.column_stack(
            (x0 + c[:, 0] * self.meters_per_tile, y1 - c[:, 1] * self.meters_per_tile)))
        return rotate(g, -self.rotation, origin=(self.cx, self.cy), use_radians=False)


def make_frame(land_utm) -> Frame:
    """The smallest frame of whole cells round the land, plus open water."""
    c = land_utm.centroid
    turned = rotate(land_utm, ROTATION, origin=c)
    x0, y0, x1, y1 = turned.bounds
    # The middle of the turned bounds, turned back: the frame turns about its
    # own centre, so that has to be where the land's box is centred.
    mid = rotate(shapely.Point((x0 + x1) / 2, (y0 + y1) / 2), -ROTATION, origin=c)
    w = math.ceil((x1 - x0 + 2 * MARGIN_M) / CELL) * CELL
    h = math.ceil((y1 - y0 + 2 * MARGIN_M) / CELL) * CELL
    return Frame(mid.x, mid.y, w, h)


class SectionProjector(Projector):
    """A window of the frame, as a KnoxMap Projector of its own."""

    def __init__(self, whole: Projector, ox: int, oy: int, width: int, height: int):
        super().__init__(whole.south, whole.west, whole.north, whole.east,
                         whole.meters_per_tile, width, height, whole.min_x_m,
                         whole.min_y_m, whole._transformer, whole.rotation)
        self.whole, self.ox, self.oy = whole, ox, oy
        s, w, n, e = self.latlon_bbox()
        self.south, self.west, self.north, self.east = s, w, n, e

    def to_px(self, lat, lon):
        x, y = self.whole.to_px(lat, lon)
        return x - self.ox, y - self.oy

    def to_latlon(self, px, py):
        return self.whole.to_latlon(px + self.ox, py + self.oy)

    def latlon_bbox(self):
        pts = [self.to_latlon(x, y) for x in (0, self.width) for y in (0, self.height)]
        lats, lons = zip(*pts)
        return min(lats), min(lons), max(lats), max(lons)


# Projector.build answered from here while a window is being worked on.
_REGISTRY: dict[tuple, SectionProjector] = {}
_original_build = Projector.build.__func__


def _key(south, west, north, east, rotation) -> tuple:
    return tuple(round(v, 9) for v in (south, west, north, east, rotation or 0.0))


def _build(cls, south, west, north, east, meters_per_tile, rotation=0.0):
    hit = _REGISTRY.get(_key(south, west, north, east, rotation))
    if hit is not None:
        return hit
    return _original_build(cls, south, west, north, east, meters_per_tile, rotation)


Projector.build = classmethod(_build)
_projection.Projector.build = Projector.build


def register(sp: SectionProjector) -> tuple:
    """Make Projector.build hand back this window for its own bbox."""
    _REGISTRY[_key(sp.south, sp.west, sp.north, sp.east, sp.rotation)] = sp
    return sp.south, sp.west, sp.north, sp.east


# --- the data ----------------------------------------------------------------

def _tags(row) -> dict:
    st = row.get("source_tags")
    if isinstance(st, list):
        return {k: v for k, v in st}
    if isinstance(st, dict):
        return dict(st)
    return {}


def _name(row) -> str | None:
    n = row.get("names")
    return (n or {}).get("primary") if isinstance(n, dict) else None


class Data:
    """Every Overture feature of the island, in UTM metres, with OSM-style tags."""

    def __init__(self, folder: str):
        self.folder = folder
        self.to_utm = pyproj.Transformer.from_crs(4326, EPSG, always_xy=True)
        self.to_ll = pyproj.Transformer.from_crs(EPSG, 4326, always_xy=True)
        rows = {k: pq.read_table(os.path.join(folder, f"{k}.parquet")).to_pylist()
                for k in ("water", "land", "land_cover", "land_use", "infrastructure",
                          "segment", "building", "place")
                if os.path.exists(os.path.join(folder, f"{k}.parquet"))}
        self.rows = rows
        lu = rows.get("land_use", [])
        park = [r for r in lu if _name(r) == PARK_NAME]
        self.park = self.utm(wkb.loads(park[0]["geometry"])) if park else None

        # Land: the island and every islet the park takes in - its boundary
        # runs 4.5 miles (7.2 km) out into the lake, which is how Passage
        # Island and the Rock of Ages reef belong to it - but not the mainland
        # of Ontario or Minnesota, whose polygons reach into the box too.
        near = self.park.buffer(PARK_REACH_M) if self.park is not None else None
        pieces = []
        for r in rows["land"]:
            # Bare rock out in the lake is land too: the Rock of Ages
            # lighthouse stands on nothing else.
            if r["subtype"] not in ("land", "rock"):
                continue
            g = self.utm(wkb.loads(r["geometry"]))
            if g.geom_type not in ("Polygon", "MultiPolygon"):
                continue
            if g.area > MAINLAND_M2:
                continue
            if near is None or near.contains(g.representative_point()):
                pieces.append(g)
        self.land = unary_union(pieces).buffer(0)

        # (geometry in UTM, tags, OSM-ish kind) for everything that is drawn.
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
        if g is None or g.is_empty:
            return
        self.items.append((g, tags))

    def _on_land(self, g):
        return g.intersection(self.land) if g.geom_type in ("Polygon", "MultiPolygon") else g

    def _collect(self):
        land = self.land
        prepared = shapely.prepared.prep(land.buffer(200))

        # Forest first: the island is nearly all woods, so the land cover is
        # the base the mapped details are painted over.
        for r in self.rows.get("land_cover", []):
            if r["cartography"]["min_zoom"] < 8:      # the world-scale generalisation
                continue
            g = self.utm(wkb.loads(r["geometry"]))
            if not prepared.intersects(g):
                continue
            g = g.intersection(land)
            tags = {"forest": {"natural": "wood"},
                    "shrub": {"natural": "scrub"},
                    "wetland": {"natural": "wetland"},
                    "barren": {"natural": "scrub"},
                    "grass": {"landuse": "grass"}}.get(r["subtype"])
            if tags:
                self._add(g, tags)

        for r in self.rows["land"]:
            tags = _tags(r)
            sub, cls = r["subtype"], r["class"]
            if sub in ("land", "reef", "physical"):
                continue
            g = self.utm(wkb.loads(r["geometry"]))
            if g.geom_type not in ("Polygon", "MultiPolygon") or not prepared.intersects(g):
                continue
            if cls in ("bare_rock", "scree", "cliff", "ridge"):
                # Open rock ridges: PZ has no bare rock ground, so they are
                # left treeless with low scrub, the way they look from a trail.
                tags = {"natural": "scrub"}
            elif cls == "beach":
                tags = {"natural": "beach"}
            self._add(self._on_land(g), tags)

        for r in self.rows["water"]:
            if _name(r) == "Lake Superior" or r["subtype"] == "physical":
                continue          # the lake itself is what is not land, below
            if r["class"] in ("bay", "strait"):
                continue          # parts of Lake Superior, already water
            g = self.utm(wkb.loads(r["geometry"]))
            if not prepared.intersects(g):
                continue
            tags = _tags(r) or {"natural": "water"}
            if g.geom_type in ("Polygon", "MultiPolygon"):
                tags = {**tags, "natural": "water"}
            elif "waterway" not in tags:
                tags = {**tags, "waterway": "stream"}
            self._add(g, tags)

        for r in self.rows.get("land_use", []):
            g = self.utm(wkb.loads(r["geometry"]))
            if not prepared.intersects(g) or g.geom_type not in ("Polygon", "MultiPolygon"):
                continue
            cls = r["class"]
            if cls == "camp_site":
                self._add(self._on_land(g), {"landuse": "grass", "name": _name(r) or ""})
            elif cls == "cemetery":
                self._add(self._on_land(g), {"landuse": "cemetery"})
            elif cls == "quarry":
                self._add(self._on_land(g), {"landuse": "quarry"})

        for r in self.rows.get("infrastructure", []):
            g = self.utm(wkb.loads(r["geometry"]))
            if not prepared.intersects(g):
                continue
            cls, tags = r["class"], _tags(r)
            if cls == "pier":
                self._add(g, {**tags, "man_made": "pier"})
            elif cls in ("bridge", "boardwalk") and g.geom_type == "LineString":
                self._add(g, {"highway": "footway", "surface": "wood",
                              **({"bridge": "yes"} if cls == "bridge" else {})})

        for r in self.rows.get("segment", []):
            if r["subtype"] != "road":
                continue
            g = self.utm(wkb.loads(r["geometry"]))
            if not prepared.intersects(g):
                continue
            surface = None
            rs = r.get("road_surface") or []
            if rs:
                surface = rs[0].get("value")
            cls = r["class"]
            name = _name(r) or ""
            if cls in ("footway", "path", "steps", "cycleway", "bridleway", None):
                # Backcountry trails: dirt unless they are said to be paved,
                # which in Rock Harbor and Windigo a few of them are.
                tags = {"highway": "path",
                        "surface": "paved" if surface == "paved" else "dirt"}
            elif cls == "track":
                tags = {"highway": "track", "surface": surface or "gravel"}
            elif cls in ("residential", "service", "unclassified"):
                tags = {"highway": "service", "surface": surface or "gravel"}
            else:
                tags = {"highway": "service"}
            if name:
                tags["name"] = name
            self._add(g, tags)

        for r in self.rows.get("building", []):
            g = self.utm(wkb.loads(r["geometry"]))
            if not prepared.intersects(g) or g.geom_type not in ("Polygon", "MultiPolygon"):
                continue
            tags = {"building": r["class"] or "yes"}
            if _name(r):
                tags["name"] = _name(r)
            if r.get("num_floors"):
                tags["building:levels"] = str(int(r["num_floors"]))
            elif r.get("height"):
                tags["building:levels"] = str(max(1, round(float(r["height"]) / 3)))
            self._add(g, tags)

    # Features for one window, cut to it (UTM polygon of the window, with margin).
    def features(self, window_utm) -> list[OSMFeature]:
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
                    holes = [[(y, x) for x, y in r.coords] for r in ll.interiors]
                    if holes:
                        f = OSMFeature(ident[0], "relation", dict(tags), [outer] + holes)
                        f.role_geoms = [("outer", outer)] + [("inner", h) for h in holes]
                    else:
                        f = OSMFeature(ident[0], "way", dict(tags), outer)
                elif part.geom_type == "LineString":
                    f = OSMFeature(ident[0], "way", dict(tags), [(y, x) for x, y in ll.coords])
                else:
                    continue
                ident[0] += 1
                out.append(f)

        # The lake: the window less the land, in one piece per part so a
        # bay's outline never punches a hole in a neighbouring part.
        emit(window_utm.difference(self.land), {"natural": "water", "name": "Lake Superior"})
        for i in self.tree.query(window_utm):
            g, tags = self.items[i]
            cut = g.intersection(window_utm)
            if cut.is_empty:
                continue
            if cut.geom_type == "MultiLineString":
                for part in cut.geoms:
                    emit(part, tags)
            elif cut.geom_type == "GeometryCollection":
                for part in cut.geoms:
                    if part.geom_type in ("Polygon", "LineString"):
                        emit(part, tags)
            else:
                emit(cut, tags)
        return out

    def buildings_utm(self) -> list:
        return [g for g, t in self.items if "building" in t]


def save_frame(path: str, frame: Frame) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(frame.to_json(), f, indent=2)


def load_frame(path: str) -> Frame:
    with open(path, encoding="utf-8") as f:
        return Frame.from_json(json.load(f))
