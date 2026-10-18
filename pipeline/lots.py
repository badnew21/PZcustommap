"""Compiling a KnoxMap project to lot files, and packaging it as a mod.

What KnoxMap's own `compile_rust` and `make_map_mod` do, with three changes a
map from this repository needs:

* it is compiled a few rows of cells at a time, and each batch's cell sources
  are thrown away once knoxlots has turned them into lot files, so a map of
  ten thousand cells never has them all on disk at once;
* open water further than a cell from anything else is left out;
* the map can sit anywhere along the world's x axis, not only at KnoxMap's
  fixed 70,0, so maps from here do not claim the same cells.

Nothing in KnoxMap is changed: what has to differ is set inside each worker
process (a worker started fresh, as on Windows, would not see it set here).
"""
from __future__ import annotations

import json
import multiprocessing
import os
import re
import shutil
import subprocess
from concurrent import futures

import numpy as np

SIDE = 256            # a Build 42 cell
CELL = 300            # a KnoxMap bitmap cell, which world origins are counted in
ROW_BATCH = 4         # rows of cells compiled before their sources are cleared away
WATER_BGR = (255, 138, 0)


def keep_cells(bmp: str, origin_x: int, shore: int = 1) -> set[tuple[int, int]]:
    """Cells to compile: every one with anything but water on it, and `shore`
    cells of water round those. `origin_x` is the map's west edge in world tiles."""
    from knoxbuild import bitmaps
    w, h = bitmaps._bmp_header(bmp)[:2]
    xs = range(origin_x // SIDE, (origin_x + w - 1) // SIDE + 1)
    ys = range(0, (h - 1) // SIDE + 1)
    land = np.zeros((len(ys), len(xs)), bool)
    water = np.array(WATER_BGR[::-1], np.uint8)
    for j, cy in enumerate(ys):
        rows = bitmaps.read_rgb_rows(bmp, cy * SIDE, min(h, cy * SIDE + SIDE), w)
        notwater = (rows != water).any(axis=2).any(axis=0)
        for i, cx in enumerate(xs):
            a, b = max(0, cx * SIDE - origin_x), min(w, cx * SIDE - origin_x + SIDE)
            land[j, i] = notwater[a:b].any()
    keep = land.copy()
    for _ in range(shore):
        k = keep.copy()
        k[1:] |= keep[:-1]
        k[:-1] |= keep[1:]
        k[:, 1:] |= keep[:, :-1]
        k[:, :-1] |= keep[:, 1:]
        k[1:, 1:] |= keep[:-1, :-1]
        k[:-1, :-1] |= keep[1:, 1:]
        k[1:, :-1] |= keep[:-1, 1:]
        k[:-1, 1:] |= keep[1:, :-1]
        keep = k
    return {(xs[i], ys[j]) for j, i in zip(*np.nonzero(keep))}


def _row(rules_file: str, origin_x: int, *args) -> int:
    """map_source's row of cells, with this map's Rules.txt and world origin.
    The origin is in two modules: map_source cuts the cells by it and
    buildheader puts each building's room table in the cell by it."""
    from knoxbuild import buildheader
    from tools import map_source
    map_source.rules_path = lambda: rules_file
    map_source.ORIGIN_X = origin_x
    buildheader.ORIGIN_X = origin_x
    return map_source._row(*args)


def _corner_row(entry) -> int:
    """The row of cells whose header lists this building: the one holding the
    north-west corner of its rooms (as buildheader.cell_tables decides it)."""
    tx, ty, entries = entry
    if entries:
        ty = min(ty, min(r[1] for e in entries for r in e["rects"]))
    return ty // SIDE


def compile_project(project: str, origin_x: int, keep: set, workers: int, scratch: str,
                    done_file: str, rules_file: str, say=print) -> None:
    """Lot files for the cells in `keep`, into <project>/lots."""
    from knoxbuild.buildheader import placed_rooms
    from knoxbuild.buildtiles import parse_tbx
    from tools import map_source
    from tools.compile_map import knoxlots_exe
    from knoxbuild import bitmaps
    name = os.path.basename(os.path.normpath(project))
    exe = knoxlots_exe()
    if exe is None:
        raise SystemExit("knoxlots not found: cargo build --release in rust/knoxlots")
    w, h = bitmaps._bmp_header(os.path.join(project, f"{name}.bmp"))[:2]
    lots_dir = os.path.join(project, "lots")
    os.makedirs(lots_dir, exist_ok=True)
    done = set(json.load(open(done_file))) if os.path.exists(done_file) else set()

    # The building tables, read once, as make_map_source does.
    lots = map_source.read_lots(project)
    parsed = [(parse_tbx(open(os.path.join(project, "buildings", f"{t}.tbx"), encoding="utf-8").read()),
               tx, ty, lv) for t, tx, ty, lv, _w, _h in lots]
    placed = placed_rooms([(b, tx, ty, lv) for b, tx, ty, lv in parsed if b.floors])
    reach = [(b.width, b.height) for b, *_ in parsed]
    del parsed
    # Each row is handed only the room tables it writes. The whole of them for
    # a town of 20,000 buildings, copied to every worker, ran a 16 GB machine
    # out of memory.
    placed_by_row: dict[int, list] = {}
    for p in placed:
        placed_by_row.setdefault(_corner_row(p), []).append(p)
    del placed

    rows = sorted({cy for _cx, cy in keep})
    todo = [cy for cy in rows if cy not in done]
    say(f"compile: {len(keep)} cells in {len(rows)} rows, {len(todo)} rows to do")
    src, out = os.path.join(scratch, "kcell"), os.path.join(scratch, "kcell_lots")
    for start in range(0, len(todo), ROW_BATCH):
        batch = todo[start:start + ROW_BATCH]
        shutil.rmtree(src, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)
        os.makedirs(src)
        # Workers started fresh rather than forked: a fork starts with a copy
        # of everything the parent read, which for a town is gigabytes.
        with futures.ProcessPoolExecutor(max_workers=workers,
                                         mp_context=multiprocessing.get_context("spawn")) as pool:
            jobs = []
            for cy in batch:
                lo, hi = cy * SIDE - map_source.MARGIN, cy * SIDE + SIDE + map_source.MARGIN
                band = [(t, tx, ty, lv) for (t, tx, ty, lv, _w, _h), (_bw, bh) in zip(lots, reach)
                        if ty - map_source.MARGIN < hi and ty + bh + map_source.MARGIN > lo]
                jobs.append(pool.submit(_row, rules_file, origin_x, project, src, cy, band,
                                        placed_by_row.get(cy, []), 1, True, (w, h)))
            for j in jobs:
                j.result()
        for f in os.listdir(src):
            m = re.match(r"cell_(\d+)_(\d+)\.kcell", f)
            if m and (int(m[1]), int(m[2])) not in keep:
                os.remove(os.path.join(src, f))
        shutil.copy2(os.path.join(project, f"{name}_ZombieSpawnMap.bmp"), os.path.join(src, "spawn.bmp"))
        with open(os.path.join(src, "source.txt"), "w") as f:
            f.write(f"format = knoxlots-source 1\norigin_x = {origin_x}\norigin_y = 0\n"
                    "spawn_map = spawn.bmp\n")
        r = subprocess.run([str(exe), "compile", src, out, "--threads", str(workers)],
                           capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f"knoxlots failed: {r.stderr[-2000:]}")
        for f in os.listdir(out):
            shutil.move(os.path.join(out, f), os.path.join(lots_dir, f))
        done.update(batch)
        json.dump(sorted(done), open(done_file, "w"))
        say(f"compile: rows {batch[0]}-{batch[-1]} done ({len(done)} of {len(rows)} rows)")
    shutil.rmtree(src, ignore_errors=True)
    shutil.rmtree(out, ignore_errors=True)


def package(project: str, title: str, mod_id: str, mods_dir: str, description: str):
    """KnoxMap's make_map_mod.package, with the lot files hard-linked rather
    than copied where the mod folder is on the same drive: they run to
    gigabytes, and nothing writes to them after the compile."""
    from tools import make_map_mod
    copy2 = shutil.copy2

    def link_or_copy(src, dst, **kw):
        if src.endswith((".lotpack", ".lotheader", ".bin")):
            try:
                if os.path.exists(dst):
                    os.remove(dst)
                os.link(src, dst)
                return dst
            except OSError:
                pass
        return copy2(src, dst, **kw)

    os.makedirs(mods_dir, exist_ok=True)
    make_map_mod.shutil.copy2 = link_or_copy
    try:
        return make_map_mod.package(project, title, mod_id, mods_dir=mods_dir,
                                    description=description)
    finally:
        make_map_mod.shutil.copy2 = copy2
