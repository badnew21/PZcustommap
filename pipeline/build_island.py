"""Build the whole Isle Royale map, one stage at a time.

    python pipeline/build_island.py [--stages fetch,terrain,buildings,spawn,papermap,compile,mod]
                                    [--workers N] [--mods-dir DIR]

Everything goes under work/IsleRoyale (the project) and work/mod (the
finished mod). Each stage leaves its results on disk and skips work already
done, so a stage can be run again on its own after a change.

    fetch      Overture Maps data for the park (data/overture/*.parquet)
    terrain    the island in strips through KnoxMap's renderer, stitched into
               one ground and one vegetation bitmap for the whole frame
    buildings  each cluster of buildings as a KnoxMap window: rendered again,
               built by knoxbuild, and pasted back into the frame
    spawn      the zombie spawn map
    papermap   the in-game map (M): shore, lakes, woods, trails, buildings, names
    compile    lot files with knoxlots, a few rows of cells at a time
    mod        the mod folder, packaged by KnoxMap's own make_map_mod
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import struct
import sys
import time
from concurrent import futures

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

import isle  # noqa: E402
import tidy  # noqa: E402
import window  # noqa: E402

ROOT = os.path.dirname(HERE)
WORK = os.path.join(ROOT, "work")
NAME = "IsleRoyale"
PROJECT = os.path.join(WORK, NAME)
TITLE = "Isle Royale"
MOD_ID = "IsleRoyale"

# Fetch box: the park and the lake round it, with room to spare.
FETCH_BOX = (47.80, -89.42, 48.27, -88.28)

STRIP = 2400          # frame tiles per terrain strip; ~4 GB of renderer at a time
WORLD_ORIGIN = (70, 0)  # KnoxMap's (and knoxlots') world origin, in 300-tile cells
SHORE_CELLS = 1       # open-water cells kept round anything that is not water


def say(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def path(suffix: str) -> str:
    return os.path.join(PROJECT, f"{NAME}{suffix}")


# --- big BMPs written straight to disk ----------------------------------------

def make_bmp(p: str, width: int, height: int) -> None:
    """An empty top-down 24-bit BMP of any size.

    The file is past the 4 GB a BMP header can state, so the size fields are
    left at what the format allows (0 for the image size, which uncompressed
    BMPs may do). KnoxMap's reader goes by width, height and file length."""
    stride = (width * 3 + 3) // 4 * 4
    size = 54 + stride * height
    with open(p, "wb") as f:
        f.write(b"BM" + struct.pack("<IHHI", min(size, 0xFFFFFFFF), 0, 0, 54))
        f.write(struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 24, 0, 0, 2835, 2835, 0, 0))
        f.truncate(size)


def open_bmp(p: str):
    """(memmap of rows x stride, width, height); pixels are BGR, top row first."""
    with open(p, "rb") as f:
        head = f.read(54)
    width, height = struct.unpack_from("<ii", head, 18)
    assert height < 0, "expected a top-down BMP"
    height = -height
    stride = (width * 3 + 3) // 4 * 4
    mm = np.memmap(p, dtype=np.uint8, mode="r+", offset=54, shape=(height, stride))
    return mm, width, height


def put(mm, x0: int, y0: int, rgb: np.ndarray) -> None:
    """Write an RGB block into a BMP memmap at (x0, y0)."""
    h, w, _ = rgb.shape
    mm[y0:y0 + h, x0 * 3:(x0 + w) * 3] = rgb[:, :, ::-1].reshape(h, w * 3)


def get(mm, x0: int, y0: int, w: int, h: int) -> np.ndarray:
    return np.ascontiguousarray(mm[y0:y0 + h, x0 * 3:(x0 + w) * 3].reshape(h, w, 3)[:, :, ::-1])


# --- stages -------------------------------------------------------------------

def stage_fetch(_a) -> None:
    import overture_fetch
    out = os.path.join(ROOT, "data", "overture")
    overture_fetch.main([*map(str, FETCH_BOX), out])


def _render_strip(job) -> tuple:
    """One strip of the frame: rendered with a pad, tidied, written into the frame."""
    x0, w, fh = job
    tmp = os.path.join(WORK, "strips", f"s{x0}")
    pad = window.PAD
    window.render_window(x0, 0, w, fh, tmp, f"s{x0}", pad=pad)
    from knoxbuild import bitmaps
    land = bitmaps.read_rgb(os.path.join(tmp, f"s{x0}.bmp"))
    veg = bitmaps.read_rgb(os.path.join(tmp, f"s{x0}_veg.bmp"))
    sp = window.section(x0 - pad, -pad, w + 2 * pad, fh + 2 * pad)
    tidy.tidy(land, veg, window.data(), sp)
    land = land[pad:pad + fh, pad:pad + w]
    veg = veg[pad:pad + fh, pad:pad + w]
    lm, _, _ = open_bmp(path(".bmp"))
    vm, _, _ = open_bmp(path("_veg.bmp"))
    put(lm, x0, 0, land)
    put(vm, x0, 0, veg)
    lm.flush()
    vm.flush()
    del lm, vm
    shutil.rmtree(tmp, ignore_errors=True)
    return x0, w


def stage_terrain(a) -> None:
    fr = window.frame()
    os.makedirs(PROJECT, exist_ok=True)
    done_file = os.path.join(WORK, "terrain_done.json")
    done = set(json.load(open(done_file))) if os.path.exists(done_file) else set()
    if not os.path.exists(path(".bmp")):
        make_bmp(path(".bmp"), fr.width, fr.height)
        make_bmp(path("_veg.bmp"), fr.width, fr.height)
        done = set()
    jobs = [(x0, min(STRIP, fr.width - x0), fr.height)
            for x0 in range(0, fr.width, STRIP) if x0 not in done]
    say(f"terrain: frame {fr.width}x{fr.height}, {len(jobs)} strips to draw")
    with futures.ProcessPoolExecutor(max_workers=a.render_workers) as pool:
        for x0, w in pool.map(_render_strip, jobs):
            done.add(x0)
            json.dump(sorted(done), open(done_file, "w"))
            say(f"terrain: strip at {x0} done ({len(done)} of {-(-fr.width // STRIP)})")


def building_windows(fr) -> list[tuple[int, int, int, int]]:
    """Windows of whole cells round each cluster of buildings."""
    from shapely.geometry import box
    from shapely.ops import unary_union
    polys = [fr.utm_to_px(g) for g in window.data().buildings_utm()]
    if not polys:
        return []
    blobs = unary_union([p.buffer(150) for p in polys])
    boxes = []
    for b in getattr(blobs, "geoms", None) or [blobs]:
        x0, y0, x1, y1 = b.bounds
        x0 = max(0, int(x0 // isle.CELL) * isle.CELL)
        y0 = max(0, int(y0 // isle.CELL) * isle.CELL)
        x1 = min(fr.width, -(-int(x1) // isle.CELL) * isle.CELL)
        y1 = min(fr.height, -(-int(y1) // isle.CELL) * isle.CELL)
        boxes.append(box(x0, y0, x1, y1))
    # Windows that touch become one, so no building is cut by a window edge.
    merged = unary_union([b.buffer(1, join_style=2) for b in boxes])
    out = []
    for m in getattr(merged, "geoms", None) or [merged]:
        x0, y0, x1, y1 = (int(round(v)) for v in m.bounds)
        x0, y0 = (x0 + 1) // isle.CELL * isle.CELL, (y0 + 1) // isle.CELL * isle.CELL
        x1, y1 = -(-(x1 - 1) // isle.CELL) * isle.CELL, -(-(y1 - 1) // isle.CELL) * isle.CELL
        out.append((x0, y0, x1 - x0, y1 - y0))
    return sorted(out)


def stage_buildings(a) -> None:
    import knoxbuild.build as kbuild
    import knoxbuild.world as kworld
    from knoxbuild import bitmaps
    fr = window.frame()
    wins = building_windows(fr)
    say(f"buildings: {len(wins)} windows")
    lm, _, _ = open_bmp(path(".bmp"))
    vm, _, _ = open_bmp(path("_veg.bmp"))
    bdir = os.path.join(PROJECT, "buildings")
    os.makedirs(bdir, exist_ok=True)
    record = []
    for i, (ox, oy, w, h) in enumerate(wins):
        name = f"w{i:02d}"
        wdir = os.path.join(WORK, "windows", name)
        shutil.rmtree(wdir, ignore_errors=True)
        window.render_window(ox, oy, w, h, wdir, name)
        sp = window.section(ox, oy, w, h)
        # The window's ground as the frame has it: drawn with the same
        # features, but tidied like everywhere else before knoxbuild sees it.
        land = bitmaps.read_rgb(os.path.join(wdir, f"{name}.bmp"))
        veg = bitmaps.read_rgb(os.path.join(wdir, f"{name}_veg.bmp"))
        tidy.tidy(land, veg, window.data(), sp)
        for suffix in (".bmp", "_ground_base.bmp"):
            _save_rgb(os.path.join(wdir, f"{name}{suffix}"), land)
        for suffix in ("_veg.bmp", "_veg_base.bmp"):
            _save_rgb(os.path.join(wdir, f"{name}{suffix}"), veg)
        origin = (WORLD_ORIGIN[0] + ox // isle.CELL, WORLD_ORIGIN[1] + oy // isle.CELL)
        kworld.choose_origin = lambda *_a, _o=origin, **_k: _o
        kbuild.choose_origin = kworld.choose_origin
        rc = kbuild.main([wdir, "--preset", "rural", "--set", f"seed={1000 + i}"])
        if rc:
            raise SystemExit(f"knoxbuild failed on {name}")
        put(lm, ox, oy, bitmaps.read_rgb(os.path.join(wdir, f"{name}.bmp")))
        put(vm, ox, oy, bitmaps.read_rgb(os.path.join(wdir, f"{name}_veg.bmp")))
        for t in glob.glob(os.path.join(wdir, "buildings", "*.tbx")):
            shutil.copy2(t, bdir)
        record.append({"name": name, "ox": ox, "oy": oy, "w": w, "h": h})
        say(f"buildings: {name} at {ox},{oy} ({w}x{h}) built")
    lm.flush()
    vm.flush()
    json.dump(record, open(os.path.join(WORK, "windows.json"), "w"), indent=1)
    write_pzw(fr, record)


def _save_rgb(p: str, rgb: np.ndarray) -> None:
    from PIL import Image
    Image.fromarray(rgb).save(p, format="BMP")


def write_pzw(fr, record: list[dict]) -> None:
    """One project for the whole frame: every window's lots and zones, moved
    to their cells in the frame. Only what map_source and make_map_mod read."""
    cells: dict[tuple[int, int], list[str]] = {}
    for r in record:
        dx, dy = r["ox"] // isle.CELL, r["oy"] // isle.CELL
        cur = None
        for line in open(os.path.join(WORK, "windows", r["name"], f"{r['name']}.pzw"),
                         encoding="utf-8"):
            m = re.match(r'\s*<cell x="(-?\d+)" y="(-?\d+)"', line)
            if m:
                cur = cells.setdefault((int(m[1]) + dx, int(m[2]) + dy), [])
                continue
            if cur is not None and ("<lot " in line or "<object " in line):
                cur.append(line.rstrip("\n"))
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<world version="1.0" width="{fr.width // isle.CELL}" height="{fr.height // isle.CELL}">',
           " <GenerateLots>",
           f'  <worldOrigin origin="{WORLD_ORIGIN[0]},{WORLD_ORIGIN[1]}"/>',
           " </GenerateLots>",
           '  <objecttype name="ParkingStall"/>',
           '  <objecttype name="TownZone"/>']
    for (cx, cy) in sorted(cells, key=lambda c: (c[1], c[0])):
        out.append(f' <cell x="{cx}" y="{cy}" map="">')
        out += cells[(cx, cy)]
        out.append(" </cell>")
    out.append("</world>")
    with open(path(".pzw"), "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    say(f"pzw: {sum(1 for c in cells.values() for l in c if '<lot ' in l)} lots in {len(cells)} cells")


def stage_spawn(a) -> None:
    import spawn
    fr = window.frame()
    record = json.load(open(os.path.join(WORK, "windows.json")))
    spawn.write(path("_ZombieSpawnMap.bmp"), fr, window.data(), record,
                os.path.join(WORK, "windows"))


def stage_papermap(a) -> None:
    import papermap
    fr = window.frame()
    papermap.write(PROJECT, fr, window.data(), WORLD_ORIGIN)
    papermap.write_spawnpoints(PROJECT, fr, window.data(), WORLD_ORIGIN)
    papermap.write_info(PROJECT, NAME, fr)
    # knoxbuild sets one rifle aside in every window; the map keeps the one in
    # the biggest settlement. The first one found was the Rock of Ages
    # lighthouse, on a rock 4 km out in the lake that nobody can reach.
    def size(cache):
        return len(glob.glob(os.path.join(os.path.dirname(cache), "buildings", "*.tbx")))
    caches = glob.glob(os.path.join(WORK, "windows", "*", "*_guncache.json"))
    if caches:
        shutil.copy2(max(sorted(caches), key=size), path("_guncache.json"))


def stage_compile(a) -> None:
    import lots
    import rules
    from tools import map_source
    ox = WORLD_ORIGIN[0] * isle.CELL
    rules_file = rules.make_rules(map_source.rules_path(), os.path.join(WORK, "rules"))
    keep = lots.keep_cells(path(".bmp"), ox, SHORE_CELLS)
    lots.compile_project(PROJECT, ox, keep, a.workers, WORK,
                         os.path.join(WORK, "compile_done.json"), rules_file, say)


def stage_mod(a) -> None:
    import lots
    import papermap
    mods = a.mods_dir or os.path.join(WORK, "mod")
    root, n, extras = lots.package(PROJECT, TITLE, MOD_ID, mods, papermap.DESCRIPTION)
    papermap.write_mod_extras(root)
    say(f"mod: {root}, {n} cells")
    for e in extras:
        say(f"  {e}")


STAGES = {"fetch": stage_fetch, "terrain": stage_terrain, "buildings": stage_buildings,
          "spawn": stage_spawn, "papermap": stage_papermap, "compile": stage_compile,
          "mod": stage_mod}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stages", default=",".join(STAGES))
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2)))
    ap.add_argument("--render-workers", type=int, default=2,
                    help="strips drawn at once; each takes about 4 GB")
    ap.add_argument("--mods-dir", help="where the mod folder goes (default work/mod)")
    a = ap.parse_args(argv)
    for s in a.stages.split(","):
        t = time.time()
        say(f"== {s}")
        STAGES[s](a)
        say(f"== {s} took {time.time() - t:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
