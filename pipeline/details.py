"""Street-level detail for a town, added after KnoxMap's own props.

KnoxMap already lays kerbs, lamps, hydrants, drains, mailboxes, yard
furniture, fences and parked cars. What a street in vanilla Louisville has on
top is set dressing, and this adds the parts that can be placed from data:

* street trees in the grass strip between pavement and kerb, a species to a
  street the way a city plants them, at about eleven metres;
* the single garden and park trees KnoxMap plants, most of them swapped for
  Build 42's Jumbo, XL and XXL trees, so a street of old houses has old trees;
* trash cans behind the garages along the alleys, and the odd dumpster;
* a bench and a bin at every mapped bus stop, and benches along park paths.

Every tile comes from the map tools' own furniture list
(config/BuildingFurniture.txt, see furniture_defs.py) or is one KnoxMap
already checked against the sprites; nothing is guessed from a sheet.

Jumbo trees: an explicit Jumbo tile in the lot data is a real tree, and the
game derives its trunk and seasonal sprites from the main `_0` tile
(PZ Mapping Tools, docs/PZ-B42.20-Jumbo-Trees.md). WorldGenOverride.lua,
written beside the map, keeps map world-generation from swapping them.

It runs inside knoxbuild, straight after knoxbuild.props.place_props, through
`install()`: the same occupied-square mask, the same tile-only lots, the same
.pzw. Nothing in KnoxMap is changed.
"""
from __future__ import annotations

import math
import os
import random
import zlib

import numpy as np

from generator import pz_colors as C

# Species and how often each size comes up. Linden and maple are the classic
# parkway trees; birch, the island's tree, is in the mix for the yards.
STREET_SPECIES = ["americanlinden", "redmaple", "americanlinden", "redmaple", "riverbirch"]
YARD_SPECIES = ["redmaple", "americanlinden", "riverbirch", "yellowwood", "dogwood"]
PARK_SPECIES = ["redmaple", "americanlinden", "riverbirch", "yellowwood"]
STREET_SIZES = [("JUMBO", 0.70), ("JUMBOXL", 0.30)]
YARD_SIZES = [("JUMBO", 0.70), ("JUMBOXL", 0.25), ("JUMBOXXL", 0.05)]
PARK_SIZES = [("JUMBO", 0.45), ("JUMBOXL", 0.40), ("JUMBOXXL", 0.15)]

STREET_TREE_EVERY_M = 11.0
STREET_TREE_CLEAR_OF_ENDS_M = 9.0   # no trunk in the junction
TREE_SPACING = 5                    # tiles between any two trunks
TREE_CLEAR_OF_WALLS = 3             # tiles between a trunk and a building
YARD_JUMBO_SHARE = 0.65             # of KnoxMap's single yard and park trees

ALLEY_EVERY_M = 9.0
ALLEY_CAN_CHANCE = 0.55
ALLEY_DUMPSTER_SHARE = 0.12         # of what goes in an alley
NEAR_BUILDING = 6                   # tiles: a can stands by a garage, not in a field
PARK_BENCH_EVERY_M = 28.0

# BuildingFurniture.txt: Furniture - Seating - Benches 4 and 6 are the
# outdoor ones; Furniture - Trash Containers 7-9 are the two-tile dumpsters.
# The single cans are trashcontainers_01_16 (KnoxMap's street dustbin, checked
# against the sprite) and its neighbours 17 and 20 from the same group.
BENCH_GROUP, BENCH_PIECES = "Furniture - Seating - Benches", (4, 6)
TRASH_GROUP, DUMPSTER_PIECES = "Furniture - Trash Containers", (7, 9)
CANS = ["trashcontainers_01_16", "trashcontainers_01_17", "trashcontainers_01_20"]

LAYER = "Furniture"
GRASS = (C.DARK_GRASS, C.MEDIUM_GRASS, C.LIGHT_GRASS)
TREE_VEG = (C.TREES, C.TREES_DARK_GRASS, (200, 0, 0), C.SPARSE_TREES)
ROADS = ("road_minor", "road_medium", "road_major")

# More wear on the streets than KnoxMap's defaults, for a town that has been
# lived in: litter, cracks and grime (generator/rendering/furniture.py).
WEAR = {"LITTER_SHARE": 3.0, "CRACK_SHARE": 2.0, "GRIME_SHARE": 1.4}

WORLDGEN_OVERRIDE = """-- Keep the Jumbo trees placed in this map's lot files as they were placed.
-- From the PZ Mapping Tools' notes on Build 42.20 Jumbo trees: an explicit
-- Jumbo tile is a real tree, but map world generation may replace it on a
-- square a biome covers unless the pattern is protected.
local function protectTilePattern(biomes, pattern)
    if not biomes then return end
    for _, biome in pairs(biomes) do
        biome.params = biome.params or {}
        biome.params.protected = biome.params.protected or {}
        local found = false
        for _, current in ipairs(biome.params.protected) do
            if current == pattern then
                found = true
                break
            end
        end
        if not found then
            table.insert(biome.params.protected, pattern)
        end
    end
end

if worldgen then
    protectTilePattern(worldgen.biomes_map, "e_*JUMBO*")
    protectTilePattern(worldgen.subbiomes, "e_*JUMBO*")
end
"""


def _pick(rng, weighted):
    r, acc = rng.random(), 0.0
    for value, w in weighted:
        acc += w
        if r < acc:
            return value
    return weighted[-1][0]


def _tree(species: str, size: str) -> str:
    return f"e_{species}{size}_1_0"


def _is(rgb, colour):
    return (rgb[..., 0] == colour[0]) & (rgb[..., 1] == colour[1]) & (rgb[..., 2] == colour[2])


def _grow(mask: np.ndarray, n: int) -> np.ndarray:
    out = mask.copy()
    for _ in range(n):
        g = out.copy()
        g[1:] |= out[:-1]
        g[:-1] |= out[1:]
        g[:, 1:] |= out[:, :-1]
        g[:, :-1] |= out[:, 1:]
        out = g
    return out


def add_details(out_dir: str, map_name: str, bdir: str, occupied: np.ndarray,
                areas, metres_per_tile: float, seed: int = 0xE7A) -> tuple[list, dict]:
    """Place the details; returns (placements, counts) like place_props."""
    import knoxfiles
    from shapely.geometry import LineString, Point
    from generator import osm
    from generator.renderer import Projector, _is_polygon, classify
    from generator.rendering.widths import _verge_width_m, _way_width_m
    from knoxbuild.bitmaps import read_rgb
    from knoxbuild.props import BLOCKED
    from knoxbuild.structures import CELL, render_tiles_tbx
    from knoxbuild.world import Placement
    from PIL import Image

    import furniture_defs
    from tools.terrain_source import rules_path

    counts = {"street_trees": 0, "tall_trees": 0, "alley_cans": 0, "dumpsters": 0,
              "bus_stop_benches": 0, "park_benches": 0}
    info = knoxfiles.read(os.path.join(out_dir, f"{map_name}_info.json"), "the map", default={})
    b = info["bbox"]
    proj = Projector.build(b["south"], b["west"], b["north"], b["east"],
                           info["meters_per_tile"], info.get("rotation") or 0.0)
    feats = osm.load_cache(os.path.join(out_dir, info["osm_cache"]), tuple(info["osm_bbox"]),
                           filters=None) or []
    pieces = furniture_defs.load(os.path.dirname(rules_path()))
    benches = [pieces[BENCH_GROUP][i] for i in BENCH_PIECES]
    dumpsters = [pieces[TRASH_GROUP][i] for i in DUMPSTER_PIECES]

    ground = read_rgb(os.path.join(out_dir, f"{map_name}.bmp"))
    veg_path = os.path.join(out_dir, f"{map_name}_veg.bmp")
    veg = read_rgb(veg_path)
    height, width = ground.shape[:2]
    blocked = np.zeros((height, width), bool)
    for colour in BLOCKED:
        blocked |= _is(ground, colour)
    walls = _grow(occupied[:height, :width], TREE_CLEAR_OF_WALLS)
    blocked |= occupied[:height, :width]
    grass = np.zeros((height, width), bool)
    for colour in GRASS:
        grass |= _is(ground, colour)
    trees = np.zeros((height, width), bool)
    for colour in TREE_VEG:
        trees |= _is(veg, colour)
    # The things KnoxMap stands on the street as colours in the vegetation
    # layer: lamps, hydrants, drains, mailboxes and bins (12, 36, *), yard
    # furniture (12, 37, *) and signs (12, 38, *). Not the kerbs (12, 34, *),
    # the road lines (12, 35, *) or the grime, cracks and litter painted on
    # the road (12, 36, 200/201/208), which a trunk can stand beside.
    marks = veg[..., 0] == 12
    furniture = marks & ((veg[..., 1] == 37) | (veg[..., 1] == 38)
                         | ((veg[..., 1] == 36) & ~np.isin(veg[..., 2], (200, 201, 208))))
    trunk_grid: dict[tuple[int, int], list[tuple[int, int]]] = {}
    rng = random.Random(seed)

    def spaced(x, y):
        gx, gy = x // TREE_SPACING, y // TREE_SPACING
        return all((x - tx) ** 2 + (y - ty) ** 2 >= TREE_SPACING ** 2
                   for ix in (gx - 1, gx, gx + 1) for iy in (gy - 1, gy, gy + 1)
                   for tx, ty in trunk_grid.get((ix, iy), ()))

    def add_trunk(x, y):
        trunk_grid.setdefault((x // TREE_SPACING, y // TREE_SPACING), []).append((x, y))
    tiles: list[tuple[int, int, int, str, str]] = []

    def free(x, y):
        return 0 <= x < width and 0 <= y < height and not blocked[y, x]

    def put(x, y, tile):
        blocked[y, x] = True
        occupied[y, x] = True
        veg[y, x] = C.VEG_NOTHING
        tiles.append((x, y, 0, LAYER, tile))

    def room_for_tree(x, y):
        if not free(x, y) or walls[y, x] or not grass[y, x]:
            return False
        y0, y1, x0, x1 = max(0, y - 2), y + 3, max(0, x - 2), x + 3
        if furniture[y0:y1, x0:x1].any():
            return False
        return spaced(x, y)

    def put_piece(piece, x, y, orient):
        entry = piece.get(orient)
        if not entry:
            return False
        squares = [(x + int(k.split(",")[0]), y + int(k.split(",")[1]), t) for k, t in entry.items()]
        if not all(free(px, py) and grass[py, px] for px, py, _ in squares):
            return False
        for px, py, t in squares:
            put(px, py, t)
        return True

    def line_px(feat):
        pts = [proj.to_px(la, lo) for la, lo in feat.geometry]
        return LineString(pts) if len(pts) >= 2 else None

    def sample(line, every_px, clear_px):
        n = int((line.length - 2 * clear_px) // every_px)
        for i in range(n + 1):
            d = clear_px + i * every_px
            if d > line.length - clear_px:
                break
            yield line.interpolate(d)

    # --- street trees -----------------------------------------------------
    every = STREET_TREE_EVERY_M / metres_per_tile
    clear = STREET_TREE_CLEAR_OF_ENDS_M / metres_per_tile
    roads = []
    for f in feats:
        if f.kind != "way" or _is_polygon(f):
            continue
        cat = classify(f.tags, False)
        if cat in ROADS and f.tags.get("bridge") != "yes" and f.tags.get("tunnel") != "yes":
            line = line_px(f)
            if line is not None and line.length > 2 * clear:
                roads.append((f, cat, line))
    for f, cat, line in roads:
        verge = _verge_width_m(f, cat) / metres_per_tile
        if verge < 1:
            continue
        half = _way_width_m(f, cat) / metres_per_tile / 2
        # The verge starts at the kerb: look for its first grass tile there,
        # then one further out, either side, at each spot along the street.
        reach = [half + 0.3 + k for k in range(int(math.ceil(verge)))]
        street = f.tags.get("name") or str(f.osm_id)
        srng = random.Random(zlib.crc32(street.encode()))
        species = srng.choice(STREET_SPECIES)
        for p in sample(line, every, clear):
            d = line.project(p)
            q = line.interpolate(min(line.length, d + 1.0))
            r = line.interpolate(max(0.0, d - 1.0))
            tx, ty = q.x - r.x, q.y - r.y
            n = math.hypot(tx, ty)
            if n == 0:
                continue
            nx, ny = -ty / n, tx / n
            for side in (1, -1):
                for dist in reach:
                    x = int(math.floor(p.x + side * dist * nx))
                    y = int(math.floor(p.y + side * dist * ny))
                    if room_for_tree(x, y):
                        put(x, y, _tree(species, _pick(srng, STREET_SIZES)))
                        add_trunk(x, y)
                        counts["street_trees"] += 1
                        break

    # --- taller garden and park trees ---------------------------------------
    parks = [s for s, p in getattr(areas, "_items", []) if p.get("category") in ("park", "playground")]
    ys, xs = np.nonzero(trees)
    for x, y in zip(xs.tolist(), ys.tolist()):
        y0, y1, x0, x1 = max(0, y - 3), y + 4, max(0, x - 3), x + 4
        if trees[y0:y1, x0:x1].sum() > 1:
            continue          # part of a wood, not a single tree
        if rng.random() >= YARD_JUMBO_SHARE or walls[y, x]:
            continue
        if not spaced(x, y):
            continue
        in_park = any(s.contains(Point(x + 0.5, y + 0.5)) for s in parks
                      if s.bounds[0] <= x <= s.bounds[2] and s.bounds[1] <= y <= s.bounds[3])
        species = rng.choice(PARK_SPECIES if in_park else YARD_SPECIES)
        size = _pick(rng, PARK_SIZES if in_park else YARD_SIZES)
        blocked[y, x] = False         # the regular tree's square, now the Jumbo's
        put(x, y, _tree(species, size))
        add_trunk(x, y)
        counts["tall_trees"] += 1

    # --- alleys -----------------------------------------------------------
    building_near = _grow(occupied[:height, :width], NEAR_BUILDING)
    alley_every = ALLEY_EVERY_M / metres_per_tile
    for f in feats:
        if f.kind != "way" or f.tags.get("service") != "alley":
            continue
        line = line_px(f)
        if line is None:
            continue
        half = _way_width_m(f, "road_service") / metres_per_tile / 2 + 1
        for side in (half, -half):
            try:
                edge = line.offset_curve(side, join_style=2)
            except Exception:  # noqa: BLE001
                continue
            if edge.is_empty or edge.geom_type != "LineString":
                continue
            for p in sample(edge, alley_every, alley_every / 2):
                x, y = int(round(p.x)), int(round(p.y))
                if not (free(x, y) and building_near[y, x]) or rng.random() >= ALLEY_CAN_CHANCE:
                    continue
                if rng.random() < ALLEY_DUMPSTER_SHARE:
                    piece = rng.choice(dumpsters)
                    if put_piece(piece, x, y, "N") or put_piece(piece, x, y, "W"):
                        counts["dumpsters"] += 1
                        continue
                if grass[y, x] or _is(ground[y:y + 1, x:x + 1], C.DIRT).any():
                    put(x, y, rng.choice(CANS))
                    counts["alley_cans"] += 1

    # --- bus stops ----------------------------------------------------------
    for f in feats:
        if f.kind != "node" or f.tags.get("highway") != "bus_stop":
            continue
        sx, sy = proj.to_px(*f.geometry[0])
        sx, sy = int(round(sx)), int(round(sy))
        done = False
        for r in range(0, 6):
            for dx in range(-r, r + 1):
                for dy in (-r, r) if abs(dx) != r else range(-r, r + 1):
                    x, y = sx + dx, sy + dy
                    for orient in ("W", "N"):
                        if not done and put_piece(rng.choice(benches), x, y, orient):
                            bx, by = (x, y + 2) if orient == "W" else (x + 2, y)
                            if free(bx, by) and grass[by, bx]:
                                put(bx, by, CANS[0])
                            counts["bus_stop_benches"] += 1
                            done = True
            if done:
                break

    # --- park benches -------------------------------------------------------
    bench_every = PARK_BENCH_EVERY_M / metres_per_tile
    for f in feats:
        if f.kind != "way" or f.tags.get("highway") not in ("footway", "path"):
            continue
        line = line_px(f)
        if line is None or not any(s.intersects(line) for s in parks):
            continue
        for p in sample(line, bench_every, bench_every / 2):
            if not any(s.contains(p) for s in parks):
                continue
            ahead = line.interpolate(min(line.length, line.project(p) + 1))
            along_x = abs(ahead.x - p.x) >= abs(ahead.y - p.y)
            orient = "N" if along_x else "W"
            nx, ny = (0, 2) if along_x else (2, 0)
            x, y = int(round(p.x)) + nx, int(round(p.y)) + ny
            if put_piece(rng.choice(benches), x, y, orient):
                counts["park_benches"] += 1

    if not tiles:
        return [], counts
    Image.fromarray(veg).save(veg_path, format="BMP")
    by_cell: dict[tuple[int, int], list] = {}
    for t in tiles:
        by_cell.setdefault((t[0] // CELL, t[1] // CELL), []).append(t)
    placements = []
    for (cx, cy), group in sorted(by_cell.items()):
        gx0 = min(t[0] for t in group)
        gy0 = min(t[1] for t in group)
        gw = max(t[0] for t in group) - gx0 + 1
        gh = max(t[1] for t in group) - gy0 + 1
        fname = f"{map_name}_details_{cx}_{cy}.tbx"
        with open(os.path.join(bdir, fname), "w", encoding="utf-8") as fh:
            fh.write(render_tiles_tbx(gw, gh, [(x - gx0, y - gy0, z, layer, tile)
                                               for x, y, z, layer, tile in group]))
        placements.append(Placement(f"buildings/{fname}", gx0, gy0, gw, gh))
    return placements, counts


def install() -> None:
    """Run add_details straight after knoxbuild's own props, and turn up the wear."""
    from generator.rendering import furniture
    import knoxbuild.props as kprops

    for name, factor in WEAR.items():
        setattr(furniture, name, getattr(furniture, name) * factor)

    original = kprops.place_props
    if getattr(original, "_with_details", False):
        return

    def place_props(out_dir, map_name, bdir, occupied, areas, metres_per_tile, seed=0x6FADE):
        placements, counts = original(out_dir, map_name, bdir, occupied, areas,
                                      metres_per_tile, seed=seed)
        more, extra = add_details(out_dir, map_name, bdir, occupied, areas, metres_per_tile,
                                  seed=seed + 11)
        print("street details        : " + ", ".join(f"{v} {k.replace('_', ' ')}"
                                                      for k, v in extra.items()))
        return placements + more, {**counts, **extra}

    place_props._with_details = True
    kprops.place_props = place_props


def write_worldgen_override(map_folder: str) -> None:
    with open(os.path.join(map_folder, "WorldGenOverride.lua"), "w", encoding="utf-8") as f:
        f.write(WORLDGEN_OVERRIDE)

