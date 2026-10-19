"""Starting places a player can choose between on Isle Royale.

Project Zomboid lists spawn regions on the new-game screen, one per map folder
that has a spawnpoints.lua. Vanilla does its towns this way: Riverside, KY is
a folder with nothing but a map.info borrowing Muldraugh's lot files
(`lots=Muldraugh, KY`) and its own spawnpoints.lua. So each place here gets a
folder beside the island's own:

    Isle Royale - Rock Harbor/
        map.info          lots=Isle Royale, lots=Muldraugh, KY
        spawnpoints.lua   tiles in and around that place

Both lots lines are written out rather than relying on the game following
Isle Royale's own `lots=` on to Muldraugh. The island's folder keeps every
point, so choosing "Isle Royale" itself is a random start.

The tiles are checked against the map as built, not taken from the source
data: in the settlements they are floor tiles inside buildings knoxbuild
actually made (KnoxMap's own choice of a tile away from the walls), and at
the campgrounds they are open grass with no tree, bush, building or water on
them, on the main island (a start on Mott Island or Passage Island would be
a start with no way off).
"""
from __future__ import annotations

import json
import os
import shutil

import numpy as np
from shapely import wkb
from shapely.geometry import Point

import isle

MAIN = "Isle Royale"
# The ferry landings, with the window knoxbuild built each in.
SETTLEMENTS = {"Rock Harbor": (48.1458, -88.4848), "Windigo": (47.9124, -89.1560)}
SETTLEMENT_SPAWNS = 6
CAMP_SPAWNS = 3
CAMP_SEARCH = 60          # tiles round a campground to look for open ground
CLEAR_OF_BUILDINGS = 4    # tiles
SPREAD = 6                # tiles between spawn points at one campground

GROUND_OK = {(90, 100, 35), (117, 117, 47), (145, 135, 60), (120, 70, 20)}   # grass, dirt
VEG_OK = {(0, 0, 0), (0, 250, 0), (0, 255, 0)}                               # nothing, grass


def _name(r):
    n = r.get("names")
    return (n or {}).get("primary") if isinstance(n, dict) else None


def _folder(place: str) -> str:
    return f"{MAIN} - {place}"


def settlement_points(fr, windows: list[dict], windows_dir: str) -> dict[str, list]:
    """Floor tiles inside buildings at each landing, in frame tiles."""
    from tools.make_map_mod import _spawn_tiles
    proj = fr.projector()
    out = {}
    for place, (lat, lon) in SETTLEMENTS.items():
        x, y = proj.to_px(lat, lon)
        best = min(windows, key=lambda r: (max(r["ox"] - x, 0, x - r["ox"] - r["w"]) ** 2
                                           + max(r["oy"] - y, 0, y - r["oy"] - r["h"]) ** 2))
        tiles = _spawn_tiles(os.path.join(windows_dir, best["name"]), SETTLEMENT_SPAWNS)
        out[place] = [(tx + best["ox"], ty + best["oy"]) for tx, ty in tiles]
    return out


def campgrounds(fr, data) -> dict[str, Point]:
    """Every named campground on the main island, in frame tiles."""
    main = max(data.land.geoms, key=lambda g: g.area)
    found = {}
    for kind in ("land_use", "infrastructure"):
        for r in data.rows.get(kind, []):
            name = _name(r)
            if r["class"] != "camp_site" or not name or name.startswith(("Group", "#")):
                continue
            g = data.utm(wkb.loads(r["geometry"]))
            spot = g.representative_point()
            if not main.contains(spot):
                continue
            label = name if "ampground" in name else f"{name} Campground"
            found.setdefault(label, fr.utm_to_px(spot))
    return found


def open_tiles(near: Point, land, veg, blocked, n: int) -> list[tuple[int, int]]:
    """Up to n open grass tiles nearest `near`, at least SPREAD apart."""
    h, w = land.shape[:2]
    cx, cy = int(near.x), int(near.y)
    x0, y0 = max(0, cx - CAMP_SEARCH), max(0, cy - CAMP_SEARCH)
    x1, y1 = min(w, cx + CAMP_SEARCH + 1), min(h, cy + CAMP_SEARCH + 1)
    ok_ground = np.zeros((y1 - y0, x1 - x0), bool)
    for c in GROUND_OK:
        ok_ground |= (land[y0:y1, x0:x1] == c).all(axis=2)
    ok_veg = np.zeros_like(ok_ground)
    for c in VEG_OK:
        ok_veg |= (veg[y0:y1, x0:x1] == c).all(axis=2)
    ok = ok_ground & ok_veg & ~blocked[y0:y1, x0:x1]
    ys, xs = np.nonzero(ok)
    order = np.argsort((xs + x0 - cx) ** 2 + (ys + y0 - cy) ** 2)
    picked: list[tuple[int, int]] = []
    for i in order:
        p = (int(xs[i]) + x0, int(ys[i]) + y0)
        if all((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 >= SPREAD ** 2 for q in picked):
            picked.append(p)
            if len(picked) == n:
                break
    return picked


def compute(fr, data, windows: list[dict], windows_dir: str, bmp_open) -> dict[str, list]:
    """{place: [(x, y) frame tiles]} for every place a player can start."""
    from PIL import Image, ImageDraw
    regions = settlement_points(fr, windows, windows_dir)
    lm, w, h = bmp_open(".bmp")
    vm, _, _ = bmp_open("_veg.bmp")
    import tidy
    for place, spot in sorted(campgrounds(fr, data).items()):
        x0 = max(0, int(spot.x) - CAMP_SEARCH - 1)
        y0 = max(0, int(spot.y) - CAMP_SEARCH - 1)
        size = min(2 * CAMP_SEARCH + 3, w - x0)
        land = np.ascontiguousarray(lm[y0:y0 + size, x0 * 3:(x0 + size) * 3]
                                    .reshape(-1, size, 3)[:, :, ::-1])
        veg = np.ascontiguousarray(vm[y0:y0 + size, x0 * 3:(x0 + size) * 3]
                                   .reshape(-1, size, 3)[:, :, ::-1])
        mask = Image.new("L", (size, land.shape[0]), 0)
        d = ImageDraw.Draw(mask)
        for poly in tidy.buildings_px():
            bx0, by0, bx1, by1 = poly.bounds
            if bx1 < x0 or bx0 > x0 + size or by1 < y0 or by0 > y0 + size:
                continue
            ring = poly.buffer(CLEAR_OF_BUILDINGS, join_style=2)
            for part in getattr(ring, "geoms", None) or [ring]:
                d.polygon([(x - x0, y - y0) for x, y in part.exterior.coords], fill=255)
        blocked = np.asarray(mask) > 0
        tiles = open_tiles(Point(spot.x - x0, spot.y - y0), land, veg, blocked, CAMP_SPAWNS)
        if tiles:
            regions[place] = [(x + x0, y + y0) for x, y in tiles]
    return regions


def save(path: str, regions: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(regions, f, indent=1)


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return {k: [tuple(p) for p in v] for k, v in json.load(f).items()}


def spawnpoints_lua(points, origin) -> str:
    ox, oy = origin[0] * isle.CELL, origin[1] * isle.CELL
    body = ",\n".join(f"      {{ posX = {x + ox}, posY = {y + oy}, posZ = 0 }}" for x, y in points)
    return ("function SpawnPoints()\n  return {\n    unemployed = {\n"
            + body + "\n    }\n  }\nend\n")


def write_folders(maps_dir: str, regions: dict, origin, description: str = "") -> list[str]:
    """A spawn-region folder per place beside the island's map folder."""
    for old in os.listdir(maps_dir):
        if old.startswith(f"{MAIN} - "):
            shutil.rmtree(os.path.join(maps_dir, old))
    made = []
    for place, points in regions.items():
        folder = _folder(place)
        d = os.path.join(maps_dir, folder)
        os.makedirs(d)
        with open(os.path.join(d, "map.info"), "w", encoding="utf-8") as f:
            f.write(f"title={folder}\n"
                    f"lots={MAIN}\n"
                    "lots=Muldraugh, KY\n"
                    "fixed2x=true\n"
                    f"description=Isle Royale, starting at {place}. {description}\n")
        with open(os.path.join(d, "spawnpoints.lua"), "w", encoding="utf-8") as f:
            f.write(spawnpoints_lua(points, origin))
        made.append(folder)
    return made


def write_server_regions(path: str, folders: list[str]) -> None:
    """A dedicated server's spawn region list: the island at random, then each place."""
    lines = ["-- Copy this into Zomboid/Server/ and rename the file to",
             "-- <your server name>_spawnregions.lua - the name has to match",
             "-- the server, not the map.",
             "function SpawnRegions()", "    return {",
             f'        {{ name = "{MAIN}", file = "media/maps/{MAIN}/spawnpoints.lua" }},']
    lines += [f'        {{ name = "{f}", file = "media/maps/{f}/spawnpoints.lua" }},' for f in folders]
    lines += ["    }", "end"]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
