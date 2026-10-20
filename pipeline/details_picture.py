"""A close-up of a built town with the street details marked.

    python pipeline/details_picture.py PROJECT SOUTH WEST NORTH EAST OUT.png [SCALE]

Buildings as knoxbuild built them (compare.py), with every item details.py
placed drawn on top: Jumbo trees in green (darker and larger for XL and XXL),
trash cans grey, dumpsters blue, benches brown.
"""
from __future__ import annotations

import json
import os
import re
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compare  # noqa: E402
from generator.renderer import Projector  # noqa: E402
from tools import map_source  # noqa: E402


def items(project: str) -> list[tuple[int, int, str]]:
    out = []
    for name, tx, ty, _lv, _w, _h in map_source.read_lots(project):
        if "_details_" not in name:
            continue
        text = open(os.path.join(project, "buildings", f"{name}.tbx"), encoding="utf-8").read()
        user = re.findall(r'<tile tile="([^"]+)"/>', text.split("<user_tiles>")[1].split("</user_tiles>")[0])
        grid = text.split('<tiles layer="Furniture">')[1].split("</tiles>")[0].replace("&#10;", "\n")
        for y, row in enumerate(r.rstrip(",") for r in grid.strip().split("\n")):
            for x, v in enumerate(row.split(",")):
                if v.strip() and int(v):
                    out.append((tx + x, ty + y, user[int(v) - 1]))
    return out


def style(tile: str):
    if "JUMBOXXL" in tile:
        return (0, 70, 0), 7
    if "JUMBOXL" in tile:
        return (10, 110, 10), 5
    if "JUMBO" in tile:
        return (40, 160, 40), 4
    if tile.startswith("trashcontainers_01_") and int(tile.rsplit("_", 1)[1]) < 16:
        return (30, 60, 200), 3
    if tile.startswith("trashcontainers"):
        return (90, 90, 90), 2
    return (140, 80, 20), 2


def draw(project: str, box, scale: int = 2) -> Image.Image:
    name = os.path.basename(os.path.normpath(project))
    info = json.load(open(os.path.join(project, f"{name}_info.json")))
    b = info["bbox"]
    proj = Projector.build(b["south"], b["west"], b["north"], b["east"],
                           info["meters_per_tile"], info.get("rotation") or 0.0)
    corners = [proj.to_px(la, lo) for la in (box[0], box[2]) for lo in (box[1], box[3])]
    x0, y0 = int(min(c[0] for c in corners)), int(min(c[1] for c in corners))
    img = compare.picture(project, box)
    img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
    d = ImageDraw.Draw(img)
    for x, y, tile in items(project):
        px, py = (x - x0) * scale + scale // 2, (y - y0) * scale + scale // 2
        if 0 <= px < img.width and 0 <= py < img.height:
            col, r = style(tile)
            d.ellipse((px - r, py - r, px + r, py + r), fill=col)
    return img


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    box = tuple(float(v) for v in argv[1:5])
    draw(argv[0], box, int(argv[6]) if len(argv) > 6 else 2).save(argv[5])
    return 0


if __name__ == "__main__":
    sys.exit(main())
