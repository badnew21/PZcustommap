"""Build White Lake to Muskegon, one stage at a time.

    python pipeline/build_whitehall.py [--stages windows,paste,papermap,compile,mod]
                                       [--workers N] [--build-workers N] [--mods-dir DIR]

The region (region.py) is 21 x 27 km of frame at one tile to the metre, too
much for KnoxMap in one go, and it is towns at both ends, so unlike Isle
Royale every part of it has buildings. It is cut into cores of 10 x 10 cells
(3 km a side). Each core is drawn and built as its own KnoxMap map with one
cell (300 m) of margin all round:

* the window is rendered from the whole region's features through a
  SectionProjector, so its tiles line up with the frame's exactly;
* knoxbuild builds everything in it, margin included, so the buildings,
  yards, fences and props near the core's edge are laid out with their
  neighbours in place;
* only what belongs to the core is kept: the core's ground, vegetation and
  zombie spawn map; each building whose lot is centred in the core (with the
  ground under its lot, where that reaches into the margin); and the props,
  fences, parking and town zones of the core's own cells.

A building standing across the line between two cores is built by both and
kept by one, whole. Everything goes under work/whitehall.

    windows    render and build each core's window (in parallel)
    paste      stitch the cores into one ground, vegetation and spawn bitmap,
               and one WorldEd project of everything kept
    papermap   the in-game map (M), the map's info and its spawn points
    compile    lot files with knoxlots, a few rows of cells at a time
    mod        the mod folder, packaged by KnoxMap's own make_map_mod
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from concurrent import futures

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

import isle  # noqa: E402
import region  # noqa: E402

ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data", "whitehall", "overture")
WORK = os.path.join(ROOT, "work", "whitehall")
NAME = "WhiteLakeMuskegon"
PROJECT = os.path.join(WORK, NAME)
TITLE = "White Lake to Muskegon"
MOD_ID = "WhiteLakeMuskegon"
FRAME = os.path.join(ROOT, "data", "whitehall", "frame.json")

CELL = isle.CELL
CORE = 10                 # cells a side of a core
MARGIN = 1                # cells of margin round it
# World cells (300 tiles). Isle Royale holds 70-355, Evanston 358-376 and the
# Central Street test from 380; this starts past them. Even, so the lot
# files' origin falls on a chunk.
WORLD_ORIGIN = (400, 0)
SHORE_CELLS = 1
# Only the coast (region.coast_utm): Lake Michigan's shore 3 km in, and White
# Lake, Muskegon Lake and Bear Lake with their shores. The farms and woods
# inland are built but not compiled into the mod.
COAST_ONLY = True

# KnoxMap settings. Real outlines at their real angles, the streets where they
# are, real heights; a small American town's mix of houses, flats and parking.
SETTINGS = {"preset": "suburb", "max_levels": 12, "apartment_chance": 0.15,
            "apartment_footprint": 450, "building_alignment": "real",
            "straight_roads": 0, "align_streets": 0, "parking_density": 1.3,
            "tree_density": 1.2, "forest_density": 1.0}
# Garden and park trees (details.py): the maples and basswood of a Michigan
# town, with hemlock and pine among them nearer the lake.
YARD_SPECIES = ["redmaple", "americanlinden", "redmaple", "riverbirch", "canadianhemlock",
                "virginiapine", "dogwood"]
PARK_SPECIES = ["redmaple", "americanlinden", "canadianhemlock", "riverbirch", "virginiapine"]
# details.py, tuned for a tidy lakeside town with big old trees: much less
# litter, cracking and grime than the Evanston test, and nearly every single
# garden, park and street tree a tall one.
DETAILS = {"WEAR": {"LITTER_SHARE": 0.5, "CRACK_SHARE": 0.7, "GRIME_SHARE": 0.8},
           "YARD_JUMBO_SHARE": 0.95,
           "STREET_SIZES": [("JUMBO", 0.45), ("JUMBOXL", 0.55)],
           "YARD_SIZES": [("JUMBO", 0.35), ("JUMBOXL", 0.45), ("JUMBOXXL", 0.20)],
           "PARK_SIZES": [("JUMBO", 0.25), ("JUMBOXL", 0.45), ("JUMBOXXL", 0.30)]}
# The forest fill (rules.py): sugar-maple country, so maple first, with
# basswood and birch.
FOREST_SPECIES = ["e_redmaple_1", "e_americanlinden_1", "e_redmaple_1", "e_riverbirch_1"]
LAKE_REACH_M = 2000       # pine and hemlock commonest this near Lake Michigan
DESCRIPTION = ("The Lake Michigan shore from Montague and Whitehall to Muskegon, at one "
               "tile to the metre: White Lake and its channel to the big lake, the "
               "White River Light Station, the beach houses and cottages along the "
               "shore, Duck Lake and Muskegon State Park's dunes, North Muskegon, "
               "Muskegon Lake and the city of Muskegon, with every street and building. "
               "Made from Overture Maps (OpenStreetMap, Microsoft building footprints "
               "and ESA WorldCover) with KnoxMap.")


def say(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def path(suffix: str) -> str:
    return os.path.join(PROJECT, f"{NAME}{suffix}")


_data = None
_frame = None


def frame():
    global _frame
    if _frame is None:
        if os.path.exists(FRAME):
            _frame = region.load_frame(FRAME)
        else:
            _frame = region.make_frame()
            region.save_frame(FRAME, _frame)
    return _frame


def data() -> region.Data:
    global _data
    if _data is None:
        _data = region.Data(DATA, frame())
    return _data


# --- cores and windows ----------------------------------------------------------

def cores(fr) -> list[dict]:
    """Every core of the frame that holds land, with its window."""
    land = region.frame_cells_with_land(fr, data())
    cy_n, cx_n = land.shape
    out = []
    for iy in range(0, cy_n, CORE):
        for ix in range(0, cx_n, CORE):
            if not land[iy:iy + CORE, ix:ix + CORE].any():
                continue
            c0x, c0y = ix * CELL, iy * CELL
            c1x, c1y = min(cx_n, ix + CORE) * CELL, min(cy_n, iy + CORE) * CELL
            w0x, w0y = max(0, c0x - MARGIN * CELL), max(0, c0y - MARGIN * CELL)
            w1x = min(fr.width, c1x + MARGIN * CELL)
            w1y = min(fr.height, c1y + MARGIN * CELL)
            out.append({"name": f"w{ix // CORE:02d}_{iy // CORE:02d}",
                        "core": [c0x, c0y, c1x, c1y], "win": [w0x, w0y, w1x, w1y]})
    return out


def land_cells(fr) -> set[tuple[int, int]]:
    """Build 42 cells holding any land, inland lakes counted as land."""
    return _cells_touching(fr, fr.utm_to_px(data().land))


def coast_cells(fr) -> set[tuple[int, int]]:
    """Build 42 cells that hold any of the coast (region.coast_utm): the
    cells the map is cut down to with COAST_ONLY."""
    return _cells_touching(fr, fr.utm_to_px(region.coast_utm(data())))


def _cells_touching(fr, shape) -> set[tuple[int, int]]:
    """Build 42 cells (world numbering, as lots.keep_cells) touching a shape
    in frame tiles."""
    from shapely import STRtree
    from shapely.geometry import box
    import lots
    parts = list(getattr(shape, "geoms", None) or [shape])
    tree = STRtree(parts)
    ox = WORLD_ORIGIN[0] * CELL
    out = set()
    side = lots.SIDE
    for cy in range(0, -(-fr.height // side)):
        for cx in range(ox // side, (ox + fr.width - 1) // side + 1):
            b = box(cx * side - ox, cy * side, (cx + 1) * side - ox, (cy + 1) * side)
            if any(parts[i].intersects(b) for i in tree.query(b)):
                out.add((cx, cy))
    return out


def _touches(rect, cells) -> bool:
    """Whether frame tiles [x0, x1) x [y0, y1) reach any of these Build 42 cells."""
    import lots
    x0, y0, x1, y1 = rect
    wx = WORLD_ORIGIN[0] * CELL
    return any((cx, cy) in cells
               for cx in range((x0 + wx) // lots.SIDE, (x1 - 1 + wx) // lots.SIDE + 1)
               for cy in range(y0 // lots.SIDE, (y1 - 1) // lots.SIDE + 1))


def window_dir(name: str) -> str:
    return os.path.join(WORK, "windows", name)


BUILDING_LOT = re.compile(r"_\d{4}(_\d{2})?\.tbx$")


def build_window(job: dict) -> dict:
    """Render and build one window. Runs in a worker process."""
    from generator import osm, renderer
    import knoxbuild.build as kbuild
    import knoxbuild.world as kworld
    import garages
    import woods
    import details
    import styles

    name = job["name"]
    x0, y0, x1, y1 = job["win"]
    w, h = x1 - x0, y1 - y0
    wdir = window_dir(name)
    done = os.path.join(wdir, "kept.json")
    if os.path.exists(done):
        return json.load(open(done))
    shutil.rmtree(wdir, ignore_errors=True)
    os.makedirs(wdir)
    t0 = time.time()

    fr = frame()
    sp = isle.SectionProjector(fr.projector(), x0, y0, w, h)
    isle.register(sp)
    bbox = (sp.south, sp.west, sp.north, sp.east)
    from shapely.geometry import Polygon
    win_utm = fr.px_to_utm(Polygon([(x0 - 8, y0 - 8), (x1 + 8, y0 - 8),
                                    (x1 + 8, y1 + 8), (x0 - 8, y1 + 8)]))
    feats = data().features(win_utm)
    from knoxbuild.settings import Settings
    s = Settings.from_dict(SETTINGS)
    with open(os.path.join(wdir, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(s.to_dict(), f, indent=2)
    cache = osm.cache_path(wdir, name)
    osm.save_cache(cache, bbox, feats)
    details.YARD_SPECIES = YARD_SPECIES
    details.PARK_SPECIES = PARK_SPECIES
    for k, v in DETAILS.items():
        setattr(details, k, v)
    details.install()
    woods.near_lake = lake_mask(fr, x0, y0, w, h)
    woods.install()
    garages.install()
    # Buildings laid out here, in this process: knoxbuild's own worker
    # processes are not forked from this one and would not have the garage
    # doors. The windows are built several at a time, so the cores are busy
    # either way.
    os.environ["KNOXBUILD_WORKERS"] = "1"
    renderer.render(feats, *bbox, meters_per_tile=1.0, output_dir=wdir, map_name=name,
                    spawn_density=s.spawn_density, tree_density=s.tree_density,
                    forest_density=s.forest_density, rotation=sp.rotation,
                    osm_cache=os.path.basename(cache), osm_bbox=bbox,
                    straight_roads=False)
    t_render = time.time() - t0
    # Wall styles for the places whose building stock is known (styles.py).
    styles.write_zones(wdir, name, feats, data())

    origin = (WORLD_ORIGIN[0] + x0 // CELL, WORLD_ORIGIN[1] + y0 // CELL)
    kworld.choose_origin = lambda *_a, **_k: origin
    kbuild.choose_origin = kworld.choose_origin
    captured = {}
    real_pzw = kbuild.render_pzw

    def render_pzw(*a, **k):
        captured["placements"] = a[3] if len(a) > 3 else k["placements"]
        captured["zones"] = k.get("zones") or []
        return real_pzw(*a, **k)
    kbuild.render_pzw = render_pzw
    log = os.path.join(wdir, "knoxbuild.log")
    with open(log, "w", encoding="utf-8") as fh:
        old = sys.stdout
        sys.stdout = fh
        try:
            rc = kbuild.main([wdir])
        finally:
            sys.stdout = old
            kbuild.render_pzw = real_pzw
    if rc:
        raise SystemExit(f"knoxbuild failed on {name} ({rc}), see {log}")

    # What this core keeps, in frame tiles.
    c0x, c0y, c1x, c1y = job["core"]

    def in_core(x, y):
        return c0x <= x < c1x and c0y <= y < c1y
    lots, owned = [], []
    for p in captured.get("placements", []):
        fx, fy = p.tile_x + x0, p.tile_y + y0
        is_building = bool(BUILDING_LOT.search(p.tbx_path))
        if is_building:
            keep = in_core(fx + p.width // 2, fy + p.height // 2)
        else:
            keep = in_core(fx, fy)
        if keep:
            lots.append({"tbx": p.tbx_path, "x": fx, "y": fy, "w": p.width, "h": p.height,
                         "on_top": bool(p.on_top), "levels": int(p.levels),
                         "building": is_building})
            if is_building:
                owned.append([fx, fy, p.width, p.height])
    zones = [{"kind": z.kind, "x": z.tile_x + x0, "y": z.tile_y + y0,
              "w": z.width, "h": z.height}
             for z in captured.get("zones", []) if in_core(z.tile_x + x0, z.tile_y + y0)]
    out = {"name": name, "core": job["core"], "win": job["win"], "lots": lots,
           "zones": zones, "render_s": round(t_render), "total_s": round(time.time() - t0)}
    # The intermediate files nobody reads again.
    for f in os.listdir(wdir):
        if f.endswith(("_road_hierarchy.bmp", "_ground_base.bmp", "_veg_base.bmp")):
            os.remove(os.path.join(wdir, f))
    with open(done, "w", encoding="utf-8") as f:
        json.dump(out, f)
    return out


def lake_mask(fr, x0: int, y0: int, w: int, h: int) -> np.ndarray:
    """Tiles of the window within LAKE_REACH_M of Lake Michigan."""
    from PIL import Image, ImageDraw
    from shapely.geometry import box
    d = data()
    global _lake_band
    if _lake_band is None:
        lake = d.extent.difference(d.land)
        _lake_band = fr.utm_to_px(lake.buffer(LAKE_REACH_M).intersection(d.land))
    part = _lake_band.intersection(box(x0, y0, x0 + w, y0 + h))
    img = Image.new("1", (w, h), 0)
    pen = ImageDraw.Draw(img)
    for poly in getattr(part, "geoms", None) or [part]:
        if poly.geom_type != "Polygon" or poly.is_empty:
            continue
        pen.polygon([(x - x0, y - y0) for x, y in poly.exterior.coords], fill=1)
        for hole in poly.interiors:
            pen.polygon([(x - x0, y - y0) for x, y in hole.coords], fill=0)
    return np.asarray(img, dtype=bool)


_lake_band = None


def stage_windows(a) -> None:
    jobs = cores(frame())
    if COAST_ONLY:
        # Windows with nothing of the coast in them are left unbuilt.
        coast = coast_cells(frame())
        jobs = [j for j in jobs if _touches(j["win"], coast)]
    if a.only:
        jobs = [j for j in jobs if j["name"] in a.only.split(",")]
    say(f"windows: {len(jobs)} cores of {CORE}x{CORE} cells")
    n = 0
    # A fresh process for every window: knoxbuild and the details passes keep
    # module state, and one window's must not carry into the next.
    import multiprocessing
    with futures.ProcessPoolExecutor(max_workers=a.build_workers, max_tasks_per_child=1,
                                     mp_context=multiprocessing.get_context("spawn")) as pool:
        for r in pool.map(build_window, jobs):
            n += 1
            nb = sum(1 for l in r["lots"] if l["building"])
            say(f"windows: {r['name']} kept {nb} buildings, {len(r['lots']) - nb} other lots, "
                f"{len(r['zones'])} zones ({r['total_s']}s) - {n} of {len(jobs)}")


# --- stitching ------------------------------------------------------------------

def _records() -> list[dict]:
    out = []
    for job in cores(frame()):
        f = os.path.join(window_dir(job["name"]), "kept.json")
        if not os.path.exists(f):
            say(f"window {job['name']} is not built yet; left as open water")
            continue
        out.append(json.load(open(f)))
    return out


def _fill(p: str, rgb) -> None:
    """Every pixel of a frame BMP set to one colour, a band of rows at a time."""
    from build_island import open_bmp
    mm, w, h = open_bmp(p)
    row = np.tile(np.array(rgb[::-1], np.uint8), w)
    for y in range(0, h, 1024):
        mm[y:y + 1024, :w * 3] = row
    mm.flush()


def _overlap(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    return ix * iy / max(1, min(aw * ah, bw * bh))


def stage_paste(a) -> None:
    from build_island import make_bmp, open_bmp, put
    from generator import pz_colors as C
    from knoxbuild import bitmaps
    from knoxbuild import world as kworld
    from knoxbuild.population import load_footprints, save_footprints
    from knoxbuild.world import Placement, Zone, render_pzw
    from PIL import Image

    fr = frame()
    records = _records()
    os.makedirs(PROJECT, exist_ok=True)
    bdir = os.path.join(PROJECT, "buildings")
    shutil.rmtree(bdir, ignore_errors=True)
    os.makedirs(bdir)
    make_bmp(path(".bmp"), fr.width, fr.height)
    make_bmp(path("_veg.bmp"), fr.width, fr.height)
    _fill(path(".bmp"), C.WATER)
    _fill(path("_veg.bmp"), C.VEG_NOTHING)
    lm, _, _ = open_bmp(path(".bmp"))
    vm, _, _ = open_bmp(path("_veg.bmp"))
    sc = 10
    spawn = np.zeros((fr.height // sc, fr.width // sc), np.uint8)
    placements, zones, owned_fp = [], [], []
    import lots as _lots
    side = _lots.SIDE
    wx = WORLD_ORIGIN[0] * CELL
    coast = coast_cells(fr) if COAST_ONLY else None

    def on_map(x, y, w=1, h=1):
        """Whether a rectangle of frame tiles reaches a cell the map keeps."""
        if coast is None:
            return True
        return any((cx, cy) in coast
                   for cx in range((x + wx) // side, (x + w - 1 + wx) // side + 1)
                   for cy in range(y // side, (y + h - 1) // side + 1))
    kept_boxes: list[tuple] = []
    dropped = 0
    for r in records:
        name = r["name"]
        wdir = window_dir(name)
        x0, y0, x1, y1 = r["win"]
        c0x, c0y, c1x, c1y = r["core"]
        land = bitmaps.read_rgb(os.path.join(wdir, f"{name}.bmp"))
        veg = bitmaps.read_rgb(os.path.join(wdir, f"{name}_veg.bmp"))
        # The ground under each kept building's lot where it reaches past the
        # core, then the core itself over the top.
        for lot in r["lots"]:
            if not lot["building"]:
                continue
            lx0, ly0 = max(lot["x"], x0), max(lot["y"], y0)
            lx1, ly1 = min(lot["x"] + lot["w"], x1), min(lot["y"] + lot["h"], y1)
            if lx1 > lx0 and ly1 > ly0:
                put(lm, lx0, ly0, land[ly0 - y0:ly1 - y0, lx0 - x0:lx1 - x0])
                put(vm, lx0, ly0, veg[ly0 - y0:ly1 - y0, lx0 - x0:lx1 - x0])
        put(lm, c0x, c0y, land[c0y - y0:c1y - y0, c0x - x0:c1x - x0])
        put(vm, c0x, c0y, veg[c0y - y0:c1y - y0, c0x - x0:c1x - x0])
        sp = np.asarray(Image.open(os.path.join(wdir, f"{name}_ZombieSpawnMap.bmp")).convert("L"))
        spawn[c0y // sc:c1y // sc, c0x // sc:c1x // sc] = \
            sp[(c0y - y0) // sc:(c1y - y0) // sc, (c0x - x0) // sc:(c1x - x0) // sc]
        for lot in r["lots"]:
            rect = (lot["x"], lot["y"], lot["w"], lot["h"])
            if not on_map(*rect):
                continue
            if lot["building"]:
                # Built by two windows and kept by both, off by a tile: keep one.
                hits = [b for b in kept_boxes if _overlap(b, rect) > 0.5]
                if hits:
                    dropped += 1
                    continue
                kept_boxes.append(rect)
            fname = os.path.basename(lot["tbx"])
            src = os.path.join(wdir, "buildings", fname)
            try:
                os.link(src, os.path.join(bdir, fname))
            except OSError:
                shutil.copy2(src, os.path.join(bdir, fname))
            placements.append(Placement(f"buildings/{fname}", lot["x"], lot["y"], lot["w"],
                                        lot["h"], on_top=lot["on_top"], levels=lot["levels"]))
        zones += [Zone(z["kind"], z["x"], z["y"], z["w"], z["h"]) for z in r["zones"]
                  if on_map(z["x"], z["y"], z["w"], z["h"])]
        fp = os.path.join(wdir, f"{name}_footprints.npz")
        if os.path.exists(fp):
            for fx, fy, mask, levels, kind in load_footprints(fp):
                h, w = mask.shape
                cx, cy = fx + x0 + w // 2, fy + y0 + h // 2
                if c0x <= cx < c1x and c0y <= cy < c1y and on_map(cx, cy):
                    owned_fp.append((fx + x0, fy + y0, mask, levels, kind))
        say(f"paste: {name}")
    lm.flush()
    vm.flush()
    Image.fromarray(spawn, "L").convert("RGB").save(path("_ZombieSpawnMap.bmp"), format="BMP")
    save_footprints(path("_footprints.npz"), owned_fp)
    kworld.set_origin(WORLD_ORIGIN)
    with open(path(".pzw"), "w", encoding="utf-8") as f:
        f.write(render_pzw(fr.width // CELL, fr.height // CELL, f"{NAME}.bmp", placements,
                           NAME, project_dir=PROJECT, zones=zones))
    nb = sum(1 for p in placements if BUILDING_LOT.search(p.tbx_path))
    say(f"paste: {nb} buildings, {len(placements) - nb} other lots, {len(zones)} zones, "
        f"{dropped} doubled buildings dropped")


def stage_papermap(a) -> None:
    import papermap
    import regions
    import whitehall_map
    from knoxbuild.population import load_footprints
    fr = frame()
    whitehall_map.write(PROJECT, fr, data(), WORLD_ORIGIN,
                        clip=region.coast_utm(data()) if COAST_ONLY else None)
    places = whitehall_map.spawn_places(fr, load_footprints(path("_footprints.npz")))
    regions.save(os.path.join(WORK, "regions.json"), places)
    with open(os.path.join(PROJECT, "spawnpoints.lua"), "w", encoding="utf-8") as f:
        f.write(regions.spawnpoints_lua([p for pts in places.values() for p in pts], WORLD_ORIGIN))
    for place, pts in places.items():
        say(f"  {place}: {len(pts)} spawn points")
    papermap.write_info(PROJECT, NAME, fr)
    # The guaranteed rifle: knoxbuild sets one aside in every window; the map
    # keeps one, in the window nearest downtown Muskegon whose rifle is in a
    # building that window kept and the map has. Its position is in world
    # tiles already.
    import region as _r
    mx, my = _r.point_px(fr, 43.2330, -86.2500)
    with open(path(".pzw"), encoding="utf-8") as fh:
        on_map = set(re.findall(r"buildings/([^\"/]+\.tbx)", fh.read()))
    for r in sorted(_records(), key=lambda r: ((r["core"][0] + r["core"][2]) / 2 - mx) ** 2
                    + ((r["core"][1] + r["core"][3]) / 2 - my) ** 2):
        g = os.path.join(window_dir(r["name"]), f"{r['name']}_guncache.json")
        kept = {os.path.basename(l["tbx"]) for l in r["lots"] if l["building"]} & on_map
        if os.path.exists(g) and json.load(open(g)).get("building") in kept:
            shutil.copy2(g, path("_guncache.json"))
            say(f"  rifle: {r['name']}")
            break


def stage_compile(a) -> None:
    import lots
    import rules
    from tools import map_source
    rules.TREE_SPECIES = FOREST_SPECIES
    ox = WORLD_ORIGIN[0] * CELL
    rules_file = rules.make_rules(map_source.rules_path(), os.path.join(WORK, "rules"))
    # Every cell with land on it and one of open lake round that - and the
    # inland lakes whole: the middle of White Lake or Muskegon Lake is all
    # water, and left out it would be a hole in the lake.
    keep = lots.keep_cells(path(".bmp"), ox, SHORE_CELLS) | land_cells(frame())
    if COAST_ONLY:
        keep &= coast_cells(frame())
    done_file = os.path.join(WORK, "compile_done.json")
    if os.path.exists(done_file):
        # Rows done before cells were added to them are done again.
        have = {tuple(int(v) for v in re.findall(r"\d+", f)[:2])
                for f in os.listdir(os.path.join(PROJECT, "lots")) if f.endswith(".lotheader")}
        stale = {cy for cx, cy in keep - have}
        json.dump(sorted(set(json.load(open(done_file))) - stale), open(done_file, "w"))
        # ...and cells outside the map now are removed.
        for f in os.listdir(os.path.join(PROJECT, "lots")):
            nums = re.findall(r"\d+", f)
            if len(nums) >= 2 and (int(nums[0]), int(nums[1])) not in keep:
                os.remove(os.path.join(PROJECT, "lots", f))
    lots.compile_project(PROJECT, ox, keep, a.workers, WORK,
                         done_file, rules_file, say)


def write_loot(mod_root: str) -> None:
    """The map's loot thinning (whitehall_loot.lua), for this map's tiles."""
    fr = frame()
    x0, y0 = WORLD_ORIGIN[0] * CELL, WORLD_ORIGIN[1] * CELL
    text = open(os.path.join(HERE, "whitehall_loot.lua"), encoding="utf-8").read()
    for key, value in (("X0", x0), ("Y0", y0), ("X1", x0 + fr.width), ("Y1", y0 + fr.height)):
        text = text.replace(f"@{key}@", str(value))
    lua_dir = os.path.join(mod_root, "common", "media", "lua", "server", "KnoxMap")
    os.makedirs(lua_dir, exist_ok=True)
    with open(os.path.join(lua_dir, f"{MOD_ID}_Loot.lua"), "w", encoding="utf-8") as f:
        f.write(text)


def stage_mod(a) -> None:
    import details
    import lots
    import regions
    import whitehall_map
    mods = a.mods_dir or os.path.join(WORK, "mod")
    root, n, extras = lots.package(PROJECT, TITLE, MOD_ID, mods, DESCRIPTION)
    whitehall_map.write_mod_extras(root)
    write_loot(root)
    maps_dir = os.path.join(root, "common", "media", "maps")
    details.write_worldgen_override(os.path.join(maps_dir, TITLE))
    folders = regions.write_folders(maps_dir, regions.load(os.path.join(WORK, "regions.json")),
                                    WORLD_ORIGIN, "The rest of the shore is the same map.",
                                    main=TITLE)
    for f in os.listdir(os.path.join(root, "server")):
        if f.endswith("_spawnregions.lua"):
            regions.write_server_regions(os.path.join(root, "server", f), folders, main=TITLE)
    say(f"mod: {len(folders)} spawn regions beside the map")
    say(f"mod: {root}, {n} cells")
    for e in extras:
        say(f"  {e}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stages", default="windows,paste,papermap,compile,mod")
    ap.add_argument("--only", help="windows: just these cores, by name")
    ap.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 2))
    ap.add_argument("--build-workers", type=int, default=4,
                    help="windows built at once; each takes 2-4 GB")
    ap.add_argument("--mods-dir", help="where the mod folder goes (default work/whitehall/mod)")
    a = ap.parse_args(argv)
    for s in a.stages.split(","):
        t = time.time()
        say(f"== {s}")
        STAGES[s](a)
        say(f"== {s} took {time.time() - t:.0f}s")
    return 0


STAGES = {"windows": stage_windows, "paste": stage_paste, "papermap": stage_papermap,
          "compile": stage_compile, "mod": stage_mod}

if __name__ == "__main__":
    sys.exit(main())
