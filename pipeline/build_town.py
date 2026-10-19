"""Build a town map with KnoxMap, from Overture Maps data.

    python pipeline/build_town.py evanston [--stages fetch,render,buildings,compile,mod]
                                           [--workers N] [--mods-dir DIR]

A town of a few tens of square kilometres is what KnoxMap is made for, so this
runs KnoxMap's own steps on it the way the app does - render, knoxbuild,
compile, package - and changes only what the app cannot do here:

* the data is Overture Maps (town.py) instead of a live Overpass download,
  written into KnoxMap's download cache so every later step finds it;
* the map is cut to the town's real boundary, from Overture's divisions;
* it is placed in the world beside the other maps in this repository, not on
  KnoxMap's fixed 70,0, so they can be installed together;
* the compile is done in row batches by lots.py.

Each stage keeps its results under work/towns/<Name>.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import isle  # noqa: E402,F401  (KnoxMap on the path)
import town  # noqa: E402
from shapely.geometry import box, mapping  # noqa: E402

ROOT = os.path.dirname(HERE)

TOWNS = {
    "evanston": {
        "name": "Evanston",
        "title": "Evanston, IL",
        "mod_id": "Evanston",
        "outline": "Evanston",
        "fetch": (42.012, -87.745, 42.078, -87.650),
        # Mostly houses with gardens and garages, a downtown of towers, and
        # Northwestern's halls. Real heights are used wherever Overture has
        # them, up to max_levels.
        "settings": {"preset": "suburb", "max_levels": 14, "apartment_chance": 0.35,
                     "apartment_footprint": 300},
        # World cells (300 tiles): Isle Royale holds 70-355, so this town
        # starts two cells past its east edge.
        "origin": (358, 0),
        "description": ("Evanston, Illinois, at one tile to the metre: the whole city inside "
                        "its real boundary, from the Lake Michigan shore to the North Shore "
                        "Channel, with Northwestern University, downtown, the Purple Line and "
                        "Metra, and every street and building furnished by what is in it. "
                        "Made from Overture Maps (OpenStreetMap) with KnoxMap."),
    },
    # A trial of the closest match KnoxMap can make to the real buildings:
    # every outline at its real angle ("real" alignment, where the default
    # squares most of them to the grid), the streets where they are rather
    # than in straight runs (so the buildings beside them do not move), and
    # heights uncapped. North Evanston round Central Street: the shops, both
    # stations, Evanston Hospital and Ryan Field.
    "evanston_central": {
        "name": "EvanstonCentral",
        "title": "Evanston, IL - Central Street (true-to-life test)",
        "mod_id": "EvanstonCentral",
        "data": "evanston",
        "outline": "Evanston",
        "clip": (42.0575, -87.7120, 42.0705, -87.6760),
        "fetch": (42.012, -87.745, 42.078, -87.650),
        "settings": {"preset": "suburb", "max_levels": 30, "apartment_chance": 0.35,
                     "apartment_footprint": 300, "building_alignment": "real",
                     "straight_roads": 0},
        # Past Evanston's 19 cells (358-376); even, see Town.__init__.
        "origin": (380, 0),
        "description": ("North Evanston round Central Street, at one tile to the metre, built "
                        "as close to the real buildings as KnoxMap gets: real outlines and "
                        "angles, real street lines and real heights. Made from Overture Maps "
                        "(OpenStreetMap) with KnoxMap."),
    },
}


def say(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class Town:
    def __init__(self, key: str, mods_dir: str | None):
        self.cfg = TOWNS[key]
        self.name = self.cfg["name"]
        # The lot files' origin has to fall on a chunk (8 tiles), and a world
        # cell is 300 tiles, so only an even cell number does.
        if self.cfg["origin"][0] % 2 or self.cfg["origin"][1] % 2:
            raise SystemExit(f"{key}: origin {self.cfg['origin']} must be even cell numbers")
        self.data = os.path.join(ROOT, "data", self.cfg.get("data", key), "overture")
        self.work = os.path.join(ROOT, "work", "towns")
        self.project = os.path.join(self.work, self.name)
        self.mods = mods_dir or os.path.join(ROOT, "work", "mod")

    def path(self, suffix: str) -> str:
        return os.path.join(self.project, f"{self.name}{suffix}")

    def settings(self):
        from knoxbuild.settings import Settings
        return Settings.from_dict(self.cfg["settings"])

    def set_origin(self) -> None:
        import knoxbuild.build as kbuild
        import knoxbuild.world as kworld
        o = tuple(self.cfg["origin"])
        kworld.choose_origin = lambda *_a, **_k: o
        kbuild.choose_origin = kworld.choose_origin
        kworld.set_origin(o)


def stage_fetch(t: Town, a) -> None:
    import overture_fetch
    if os.path.exists(os.path.join(t.data, "building.parquet")):
        say("fetch: already have the data")
        return
    overture_fetch.main([*map(str, t.cfg["fetch"]), t.data])


def stage_render(t: Town, a) -> None:
    from generator import osm, renderer
    outline = town.outline(t.data, t.cfg["outline"])
    if t.cfg.get("clip"):
        cs, cw, cn, ce = t.cfg["clip"]
        outline = outline.intersection(box(cw, cs, ce, cn))
    west, south, east, north = outline.bounds
    fs, fw, fn, fe = t.cfg["fetch"]
    feats = town.features(t.data, box(fw, fs, fe, fn))
    say(f"render: {len(feats)} features")
    os.makedirs(t.project, exist_ok=True)
    s = t.settings()
    with open(os.path.join(t.project, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(s.to_dict(), f, indent=2)
    rotation = 0.0
    if s.align_streets:
        angle, strength = renderer.dominant_road_angle(feats, south, west, north, east)
        if strength >= renderer.ALIGN_MIN_STRENGTH and abs(angle) >= 0.5:
            rotation = -angle
    rotation += float(s.rotate_degrees)
    cache = osm.cache_path(t.project, t.name)
    osm.save_cache(cache, t.cfg["fetch"], feats)
    r = renderer.render(feats, south, west, north, east, meters_per_tile=1.0,
                        output_dir=t.project, map_name=t.name,
                        spawn_density=s.spawn_density, tree_density=s.tree_density,
                        forest_density=s.forest_density, rotation=rotation,
                        osm_cache=os.path.basename(cache), osm_bbox=tuple(t.cfg["fetch"]),
                        shape=mapping(outline), straight_roads=bool(s.straight_roads))
    say(f"render: {r.width}x{r.height} tiles ({r.cells_x}x{r.cells_y} cells), "
        f"turned {rotation:.1f} degrees")


def stage_buildings(t: Town, a) -> None:
    import knoxbuild.build as kbuild
    t.set_origin()
    rc = kbuild.main([t.project])
    if rc:
        raise SystemExit(f"knoxbuild failed ({rc})")


def stage_compile(t: Town, a) -> None:
    import lots
    import rules
    from tools import map_source
    ox = t.cfg["origin"][0] * lots.CELL
    rules_file = rules.make_rules(map_source.rules_path(), os.path.join(t.work, "rules"))
    keep = lots.keep_cells(t.path(".bmp"), ox, 1)
    lots.compile_project(t.project, ox, keep, a.workers, t.work,
                         os.path.join(t.work, f"{t.name}_compile_done.json"), rules_file, say)


def stage_mod(t: Town, a) -> None:
    import lots
    root, n, extras = lots.package(t.project, t.cfg["title"], t.cfg["mod_id"], t.mods,
                                   t.cfg["description"])
    with open(os.path.join(root, "ATTRIBUTION.txt"), "a", encoding="utf-8") as f:
        f.write("\nThe streets, buildings, land use, water and businesses are OpenStreetMap\n"
                "data as published by Overture Maps (https://overturemaps.org, ODbL),\n"
                "and the city boundary is from Overture Maps' divisions theme.\n")
    say(f"mod: {root}, {n} cells")
    for e in extras:
        say(f"  {e}")


STAGES = {"fetch": stage_fetch, "render": stage_render, "buildings": stage_buildings,
          "compile": stage_compile, "mod": stage_mod}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("town", choices=sorted(TOWNS))
    ap.add_argument("--stages", default=",".join(STAGES))
    ap.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 2))
    ap.add_argument("--mods-dir", help="where the mod folder goes (default work/mod)")
    a = ap.parse_args(argv)
    t = Town(a.town, a.mods_dir)
    for s in a.stages.split(","):
        t0 = time.time()
        say(f"== {s}")
        STAGES[s](t, a)
        say(f"== {s} took {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
