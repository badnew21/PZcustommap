"""Hemlock and pine through the hardwoods, and pines on the dunes.

The woods along this shore are not one kind of tree. Back from the lake they
are beech, maple and basswood with hemlock in the cool hollows and tall white
pine standing over the rest; on the dunes it is pine and juniper scrub in the
sand. KnoxMap paints woodland from the map tools' rules, which in Build 42 are
broadleaf sheets (rules.py), so on their own the woods are all hardwood of one
height.

This scatters Build 42's Jumbo conifers through them: Canadian hemlock and
Virginia pine, the two needle-leaved trees the game ships, at Jumbo, XL and XXL
size, so a stand of pines rises over the canopy the way white pines do here.

* In the woods (full tree cover), one conifer in about every 200 m2, more of
  them within a couple of kilometres of Lake Michigan where pine and hemlock
  are commonest. The regular trees right round each one are cleared, so trunks
  do not stand inside each other.
* On open sand and scrub by the lake, a thin scatter of smaller pines.

Each is placed as the Jumbo sheet's main `_0` tile, which the game builds the
rest of the tree from (PZ Mapping Tools, docs/PZ-B42.20-Jumbo-Trees.md), and
the map's WorldGenOverride.lua (details.py) keeps world generation from
replacing them. Nothing is guessed from a sheet layout: `_0` is the one index
the tools document for every Jumbo sheet.

It runs inside knoxbuild straight after details.py, through `install()`.
"""
from __future__ import annotations

import os
import random

import numpy as np

from generator import pz_colors as C

GRID = 14                 # tiles: one candidate per GRID x GRID square
WOODS_CHANCE = 0.9        # of candidates in the woods -> ~1 conifer per 220 m2
NEAR_LAKE_CHANCE = 1.0
DUNE_GRID = 18
DUNE_CHANCE = 0.35
CLEAR = 1                 # regular trees cleared this far round a conifer
CLEAR_OF_WALLS = 4
WOODS_SIZES = [("JUMBO", 0.45), ("JUMBOXL", 0.40), ("JUMBOXXL", 0.15)]
DUNE_SIZES = [("JUMBO", 0.8), ("JUMBOXL", 0.2)]
WOODS_SPECIES = [("canadianhemlock", 0.5), ("virginiapine", 0.5)]
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


def add_conifers(out_dir: str, map_name: str, bdir: str, occupied: np.ndarray,
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
    blocked = np.zeros((height, width), bool)
    for colour in BLOCKED:
        blocked |= _is(ground, colour)
    blocked |= grow(occupied[:height, :width], CLEAR_OF_WALLS)
    # Anything KnoxMap stood there already: lamps, signs, yard furniture.
    blocked |= veg[..., 0] == 12
    woods = _is(veg, C.TREES)
    dunes = (_is(ground, C.SAND) | _is(veg, C.BUSHES_TREES_DARK_GRASS)
             | _is(veg, C.SPARSE_TREES)) & ~woods
    lake = near_lake[:height, :width] if near_lake is not None else np.zeros_like(woods)
    rng = random.Random(seed)
    tiles = []
    counts = {"woods_conifers": 0, "dune_pines": 0}

    def place(x, y, tile):
        veg[max(0, y - CLEAR):y + CLEAR + 1, max(0, x - CLEAR):x + CLEAR + 1] = C.VEG_NOTHING
        blocked[y, x] = True
        tiles.append((x, y, 0, LAYER, tile))

    for gy in range(0, height, GRID):
        for gx in range(0, width, GRID):
            x = gx + rng.randrange(GRID)
            y = gy + rng.randrange(GRID)
            if x >= width or y >= height or blocked[y, x] or not woods[y, x]:
                continue
            chance = NEAR_LAKE_CHANCE if lake[y, x] else WOODS_CHANCE
            if rng.random() >= chance:
                continue
            species = _pick(rng, WOODS_SPECIES)
            place(x, y, f"e_{species}{_pick(rng, WOODS_SIZES)}_1_0")
            counts["woods_conifers"] += 1
    for gy in range(0, height, DUNE_GRID):
        for gx in range(0, width, DUNE_GRID):
            x = gx + rng.randrange(DUNE_GRID)
            y = gy + rng.randrange(DUNE_GRID)
            if x >= width or y >= height or blocked[y, x] or not (dunes[y, x] and lake[y, x]):
                continue
            if rng.random() >= DUNE_CHANCE:
                continue
            place(x, y, f"e_virginiapine{_pick(rng, DUNE_SIZES)}_1_0")
            counts["dune_pines"] += 1

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
        fname = f"{map_name}_conifers_{cx}_{cy}.tbx"
        with open(os.path.join(bdir, fname), "w", encoding="utf-8") as fh:
            fh.write(render_tiles_tbx(gw, gh, [(x - gx0, y - gy0, z, layer, tile)
                                               for x, y, z, layer, tile in group]))
        placements.append(Placement(f"buildings/{fname}", gx0, gy0, gw, gh))
    return placements, counts


def install() -> None:
    """Run add_conifers after knoxbuild's props and details.py's street details."""
    import knoxbuild.props as kprops

    original = kprops.place_props
    if getattr(original, "_with_conifers", False):
        return

    def place_props(out_dir, map_name, bdir, occupied, areas, metres_per_tile, seed=0x6FADE):
        placements, counts = original(out_dir, map_name, bdir, occupied, areas,
                                      metres_per_tile, seed=seed)
        more, extra = add_conifers(out_dir, map_name, bdir, occupied, seed=seed + 23)
        print("conifers              : " + ", ".join(f"{v} {k.replace('_', ' ')}"
                                                      for k, v in extra.items()))
        return placements + more, {**counts, **extra}

    place_props._with_conifers = True
    kprops.place_props = place_props
