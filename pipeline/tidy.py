"""What a wilderness needs that KnoxMap's town rules do not give it.

KnoxMap clears trees off roads and out of gardens, because in a town that is
where people walk. On Isle Royale people walk on 165 miles of dirt trail
through solid forest, and KnoxMap plants that forest straight over them. So
after a window is drawn:

* every trail is cleared of trees, with a strip of low grass either side;
* campgrounds keep a few trees, not a wall of them;
* each building stands in a small clearing of mown grass, as the cabins,
  lodges and ranger stations do.

Works in place on the (rows, cols, 3) RGB arrays of a window.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

import window
from generator import pz_colors as C

CLEARING_M = 6        # grass round a building
TREE_VEG = (C.TREES, C.TREES_DARK_GRASS, (200, 0, 0), C.LOT_OF_GRASS_AND_TREES,
            C.BUSHES_TREES_DARK_GRASS)
GRASS_GROUND = (C.DARK_GRASS, C.MEDIUM_GRASS, C.LIGHT_GRASS)

_buildings_px = None


def _is(rgb: np.ndarray, colour) -> np.ndarray:
    return (rgb[:, :, 0] == colour[0]) & (rgb[:, :, 1] == colour[1]) & (rgb[:, :, 2] == colour[2])


def _any(rgb: np.ndarray, colours) -> np.ndarray:
    m = np.zeros(rgb.shape[:2], bool)
    for c in colours:
        m |= _is(rgb, c)
    return m


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


def buildings_px():
    """Every building's footprint in frame tiles, worked out once."""
    global _buildings_px
    if _buildings_px is None:
        fr = window.frame()
        _buildings_px = [fr.utm_to_px(g) for g in window.data().buildings_utm()]
    return _buildings_px


def tidy(land: np.ndarray, veg: np.ndarray, data, sp) -> None:
    h, w = land.shape[:2]
    trees = _any(veg, TREE_VEG)
    grass = _any(land, GRASS_GROUND)

    # Trails: bare dirt, nothing growing on them, short grass at the edges.
    trail = _is(land, C.DIRT)
    veg[trail] = C.VEG_NOTHING
    edge = _grow(trail, 1) & ~trail & trees & grass
    veg[edge] = C.SHORT_GRASS

    # Campgrounds are mapped as grass; thin their woods.
    camp = _is(land, C.MEDIUM_GRASS) & trees
    veg[camp] = C.SPARSE_TREES

    # A clearing round each building in the window.
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    hit = False
    for poly in buildings_px():
        x0, y0, x1, y1 = poly.bounds
        if x1 < sp.ox - 20 or x0 > sp.ox + w + 20 or y1 < sp.oy - 20 or y0 > sp.oy + h + 20:
            continue
        ring = poly.buffer(CLEARING_M, join_style=2)
        for part in getattr(ring, "geoms", None) or [ring]:
            d.polygon([(x - sp.ox, y - sp.oy) for x, y in part.exterior.coords], fill=255)
            hit = True
    if hit:
        near = np.asarray(mask) > 0
        lawn = near & _is(land, C.DARK_GRASS)
        land[lawn] = C.MEDIUM_GRASS
        cut = near & _any(veg, TREE_VEG)
        veg[cut] = C.SHORT_GRASS
