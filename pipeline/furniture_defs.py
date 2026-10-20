"""The map tools' own furniture list (config/BuildingFurniture.txt), parsed.

Each piece of furniture is a set of entries, one per facing (W, N, E, S...),
each a map of tile offsets ("x,y") to tile names, exactly as BuildingEd
places them. Reading the pieces from here rather than writing tile names by
hand means a two-tile bench or dumpster is put down with the halves that
belong together, facing the way the tools say they face.
"""
from __future__ import annotations

import os
import re


def parse(path: str) -> dict[str, list[dict[str, dict[str, str]]]]:
    """{group label: [furniture: {orient: {"x,y": tile}}]}"""
    groups: dict[str, list] = {}
    label = None
    piece: dict | None = None
    entry: dict | None = None
    orient = None
    depth = 0
    for raw in open(path, encoding="utf-8", errors="replace"):
        line = raw.strip()
        if line == "{":
            depth += 1
            continue
        if line == "}":
            depth -= 1
            if depth == 2 and entry is not None and piece is not None:
                piece[orient or "W"] = entry
                entry, orient = None, None
            elif depth == 1 and piece is not None and label is not None:
                if piece:
                    groups.setdefault(label, []).append(piece)
                piece = None
            continue
        if line == "furniture":
            piece = {}
        elif line == "entry":
            entry, orient = {}, None
        elif line.startswith("label = ") and depth == 1:
            label = line[8:].strip()
        elif line.startswith("orient = ") and entry is not None:
            orient = line[9:].strip()
        elif entry is not None:
            m = re.match(r"(-?\d+),(-?\d+) = (\S+)", line)
            if m:
                entry[f"{m[1]},{m[2]}"] = m[3]
    return groups


def load(tools_config: str) -> dict:
    return parse(os.path.join(tools_config, "BuildingFurniture.txt"))
