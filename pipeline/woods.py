"""Open woods of tall trees, pines on the dunes, and flower beds by the houses.

KnoxMap paints a wood by the map tools' rules: full tree cover is a tree on
every tile, so a wood is a wall of trunks of one height, and in Build 42 they
are all broadleaf (rules.py). The woods along this shore are old second
growth: tall maple, beech and basswood with hemlock and white pine standing
over them, open enough underneath to see between the trunks. On the dunes it
is pine in the sand. This makes the woods look like that:

* The woods are thinned. A tile of full tree cover keeps its tree one time in
  five, and the rest is long grass or open ground; the thinner grades at a
  wood's edge thin in proportion. A single tree in a garden is not touched
  (details.py makes most of those Jumbo trees).
* Tall trees go in among what is left, about one in every 90 m2 of wood:
  Build 42's Jumbo trees at Jumbo, XL and XXL size, mostly maple and basswood
  inland, with more Canadian hemlock and Virginia pine (the two needle-leaved
  trees the game ships) within a couple of kilometres of Lake Michigan. The
  regular trees round each are cleared, so trunks do not stand in each other.
* Scrub is thinned the same way, about a third of it left.
* On open sand and scrub by the lake, a thin scatter of smaller pines.
* Flower beds: a run of flowers along part of the wall of about one building
  in eight, on the lawn side.

Each tall tree is placed as its Jumbo sheet's main `_0` tile, which the game
builds the rest of the tree from (PZ Mapping Tools,
docs/PZ-B42.20-Jumbo-Trees.md), and the map's WorldGenOverride.lua
(details.py) keeps world generation from replacing them. The thinning and the
flowers are colours of the vegetation bitmap that the map tools' own rules
already have (Trees, Grass on dark, Flowers).

It runs inside knoxbuild straight after details.py, through `install()`.
"""
from __future__ import annotations

import os
import random

import numpy as np

from generator import pz_colors as C

# Thinning: what share of each grade of tree cover keeps its tree, and what
# share of the rest becomes long grass.
KEEP = {C.TREES: 0.20, (200, 0, 0): 0.20, C.TREES_DARK_GRASS: 0.13, C.SPARSE_TREES: 0.08,
        # Scrub (bushes and saplings on every tile) is thinned the same way,
        # but what stays is still scrub.
        C.BUSHES_TREES_DARK_GRASS: 0.35}
LONG_GRASS = 0.40
WOOD_MIN = 12             # tree tiles in the 7 x 7 round a tile for it to be a wood

GRID = 9                  # tiles: one tall-tree candidate per GRID x GRID square
WOODS_CHANCE = 0.85       # -> about one tall tree in every 95 m2 of wood
CLEAR = 2                 # regular trees cleared this far round a tall tree
SPACING = 4               # no two tall trees nearer than this
CLEAR_OF_WALLS = 4
WOODS_SIZES = [("JUMBO", 0.30), ("JUMBOXL", 0.45), ("JUMBOXXL", 0.25)]
INLAND_SPECIES = [("redmaple", 0.35), ("americanlinden", 0.20), ("riverbirch", 0.12),
                  ("canadianhemlock", 0.18), ("virginiapine", 0.15)]
SHORE_SPECIES = [("canadianhemlock", 0.30), ("virginiapine", 0.30), ("redmaple", 0.20),
                 ("americanlinden", 0.12), ("riverbirch", 0.08)]
DUNE_GRID = 16
DUNE_CHANCE = 0.4
DUNE_SIZES = [("JUMBO", 0.6), ("JUMBOXL", 0.4)]

FLOWER_BLOCK = 6          # tiles: flower beds come in runs about this long
FLOWER_SHARE = 0.12       # of the stretches of wall that get one
GRASS = (C.DARK_GRASS, C.MEDIUM_GRASS, C.LIGHT_GRASS)
LAYER = "Furniture"

# Where the shore is, in this window's tiles: set by the build for each window
# (a bool mask of tiles within reach of Lake Michigan), or None.
near_lake: np.ndarray | None = None


def _pick(rng, weighted):
    r, acc = rng.random(), 0.0
    for value, w in weighted:
        acc += w
        if r < acc:
            return value
    return weighted[-1][0]


def _is(rgb, colour):
    return (rgb[..., 0] == colour[0]) & (rgb[..., 1] == colour[1]) & (rgb[..., 2] == colour[2])


def grow(mask: np.ndarray, n: int) -> np.ndarray:
    out = mask.copy()
    for _ in range(n):
        g = out.copy()
        g[1:] |= out[:-1]
        g[:-1] |= out[1:]
        g[:, 1:] |= out[:, :-1]
        g[:, :-1] |= out[:, 1:]
        out = g
    return out


def box_count(mask: np.ndarray, r: int) -> np.ndarray:
    """How many set tiles in the (2r+1) square round each tile."""
    s = np.pad(mask.astype(np.int32), ((r + 1, r), (r + 1, r))).cumsum(0).cumsum(1)
    n = 2 * r + 1
    return s[n:, n:] - s[:-n, n:] - s[n:, :-n] + s[:-n, :-n]


def add_woods(out_dir: str, map_name: str, bdir: str, occupied: np.ndarray,
              seed: int = 0xC0F) -> tuple[list, dict]:
    from knoxbuild.bitmaps import read_rgb
    from knoxbuild.props import BLOCKED
    from knoxbuild.structures import CELL, render_tiles_tbx
    from knoxbuild.world import Placement
    from PIL import Image

    ground = read_rgb(os.path.join(out_dir, f"{map_name}.bmp"))
    veg_path = os.path.join(out_dir, f"{map_name}_veg.bmp")
    veg = read_rgb(veg_path)
    height, width = ground.shape[:2]
    occ = occupied[:height, :width]
    blocked = np.zeros((height, width), bool)
    for colour in BLOCKED:
        blocked |= _is(ground, colour)
    # Anything KnoxMap stood there already: lamps, signs, yard furniture.
    props = veg[..., 0] == 12
    lake = near_lake[:height, :width] if near_lake is not None else np.zeros((height, width), bool)
    nrng = np.random.default_rng(seed)
    rng = random.Random(seed)
    counts = {"tree_tiles_thinned": 0, "tall_trees": 0, "dune_pines": 0, "flower_tiles": 0}

    # --- thinning -------------------------------------------------------------
    grades = {colour: _is(veg, colour) for colour in KEEP}
    treey = np.zeros((height, width), bool)
    for m in grades.values():
        treey |= m
    wood = treey & (box_count(treey, 3) >= WOOD_MIN)
    roll = nrng.random((height, width))
    for colour, m in grades.items():
        m = m & wood
        keep = m & (roll < KEEP[colour])
        grass = m & ~keep & (roll < KEEP[colour] + LONG_GRASS)
        bare = m & ~keep & ~grass
        veg[keep] = colour if colour == C.BUSHES_TREES_DARK_GRASS else C.TREES
        veg[grass] = C.GRASS_ON_DARK
        veg[bare] = C.VEG_NOTHING
        counts["tree_tiles_thinned"] += int((grass | bare).sum())

    # --- tall trees -------------------------------------------------------------
    near_walls = grow(occ, CLEAR_OF_WALLS)
    free = ~(blocked | near_walls | props)
    tiles = []
    taken = np.zeros((height, width), bool)

    def place(x, y, tile):
        veg[max(0, y - CLEAR):y + CLEAR + 1, max(0, x - CLEAR):x + CLEAR + 1] = C.GRASS_ON_DARK
        taken[max(0, y - SPACING):y + SPACING + 1, max(0, x - SPACING):x + SPACING + 1] = True
        tiles.append((x, y, 0, LAYER, tile))

    for gy in range(0, height, GRID):
        for gx in range(0, width, GRID):
            x = gx + rng.randrange(GRID)
            y = gy + rng.randrange(GRID)
            if x >= width or y >= height or not (wood[y, x] and free[y, x]) or taken[y, x]:
                continue
            if rng.random() >= WOODS_CHANCE:
                continue
            species = _pick(rng, SHORE_SPECIES if lake[y, x] else INLAND_SPECIES)
            place(x, y, f"e_{species}{_pick(rng, WOODS_SIZES)}_1_0")
            counts["tall_trees"] += 1

    dunes = (_is(ground, C.SAND) | (treey & ~wood)) & lake & ~wood
    for gy in range(0, height, DUNE_GRID):
        for gx in range(0, width, DUNE_GRID):
            x = gx + rng.randrange(DUNE_GRID)
            y = gy + rng.randrange(DUNE_GRID)
            if x >= width or y >= height or not (dunes[y, x] and free[y, x]) or taken[y, x]:
                continue
            if rng.random() >= DUNE_CHANCE:
                continue
            place(x, y, f"e_virginiapine{_pick(rng, DUNE_SIZES)}_1_0")
            counts["dune_pines"] += 1

    # --- flower beds --------------------------------------------------------------
    lawn = np.zeros((height, width), bool)
    for colour in GRASS:
        lawn |= _is(ground, colour)
    open_veg = _is(veg, C.VEG_NOTHING) | _is(veg, C.GRASS_ON_DARK)
    ring = grow(occ, 1) & ~occ & lawn & open_veg & ~blocked & ~props & ~taken
    by, bx = height // FLOWER_BLOCK + 1, width // FLOWER_BLOCK + 1
    chosen = nrng.random((by, bx)) < FLOWER_SHARE
    chosen = np.repeat(np.repeat(chosen, FLOWER_BLOCK, 0), FLOWER_BLOCK, 1)[:height, :width]
    beds = ring & chosen
    veg[beds] = C.FLOWERS
    counts["flower_tiles"] = int(beds.sum())

    Image.fromarray(veg).save(veg_path, format="BMP")
    if not tiles:
        return [], counts
    by_cell: dict[tuple[int, int], list] = {}
    for t in tiles:
        by_cell.setdefault((t[0] // CELL, t[1] // CELL), []).append(t)
    placements = []
    for (cx, cy), group in sorted(by_cell.items()):
        gx0 = min(t[0] for t in group)
        gy0 = min(t[1] for t in group)
        gw = max(t[0] for t in group) - gx0 + 1
        gh = max(t[1] for t in group) - gy0 + 1
        fname = f"{map_name}_woods_{cx}_{cy}.tbx"
        with open(os.path.join(bdir, fname), "w", encoding="utf-8") as fh:
            fh.write(render_tiles_tbx(gw, gh, [(x - gx0, y - gy0, z, layer, tile)
                                               for x, y, z, layer, tile in group]))
        placements.append(Placement(f"buildings/{fname}", gx0, gy0, gw, gh))
    return placements, counts


def install() -> None:
    """Run add_woods after knoxbuild's props and details.py's street details."""
    import knoxbuild.props as kprops

    original = kprops.place_props
    if getattr(original, "_with_woods", False):
        return

    def place_props(out_dir, map_name, bdir, occupied, areas, metres_per_tile, seed=0x6FADE):
        placements, counts = original(out_dir, map_name, bdir, occupied, areas,
                                      metres_per_tile, seed=seed)
        more, extra = add_woods(out_dir, map_name, bdir, occupied, seed=seed + 23)
        print("woods                 : " + ", ".join(f"{v} {k.replace('_', ' ')}"
                                                      for k, v in extra.items()))
        return placements + more, {**counts, **extra}

    place_props._with_woods = True
    kprops.place_props = place_props
