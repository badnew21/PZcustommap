"""The map tools' Rules.txt, with the island's own trees.

The PZ Mapping Tools paint forest with `vegetation_trees_01_*`, a sheet Build 42
still defines but ships no artwork for. KnoxMap's Setup repoints those rules at
Build 42's per-species sheets (worlded/patch_rules_b42_trees.py), but only once
it has checked the sheets against the game's own files. A build that leans on
that having happened, on whichever PC, can end up with a forest of trees nobody
can see.

So the compile uses its own copy of Rules.txt and Blends.txt, written here from
the tools' copy, with every forest tile set explicitly. The species are the
ones KnoxMap verified against Build 42 (index 8-11 is the green tree on each),
weighted to birch: paper birch is the commonest broadleaf on the island. The
conifer sheets (e_canadianhemlock_1, e_virginiapine_1) would suit the spruce
and fir better, but they are laid out differently and are not used until that
can be checked in the game.

The tools' own files are not changed.
"""
from __future__ import annotations

import os
import re
import shutil

TREE_SPECIES = ["e_riverbirch_1", "e_redmaple_1", "e_riverbirch_1", "e_americanlinden_1"]
KNOXMAP_SPECIES = r"e_(?:redmaple|riverbirch|americanlinden|dogwood)_1"
FLOWER_SHEET = "f_flowerbed_1"
FLOWER_INDICES = [0, 1, 2, 3, 4, 5]
# The tools' grass rules (long grass, the sparse woods) mix a few groundcover
# plants in with the grass, and those are what becomes the flower sheet. Off,
# they are left out of everything but the Flowers alias itself, so long grass
# is grass and flowers grow only where a map paints flowers.
WILD_FLOWERS = True
GROUNDCOVER = re.compile(r"\s*(?:vegetation_groundcover_01|f_flowerbed_1)_\d+\s*$")


def make_rules(tools_rules: str, out_dir: str) -> str:
    """Write Rules.txt and Blends.txt into out_dir; returns the Rules.txt path."""
    os.makedirs(out_dir, exist_ok=True)
    text = open(tools_rules, encoding="utf-8", errors="replace").read()
    n = [0]

    def tree(m):
        species = TREE_SPECIES[n[0] % len(TREE_SPECIES)]
        n[0] += 1
        return f"{species}_{m.group(1)}"

    # Either the shipped rules or ones KnoxMap's Setup already patched.
    text = re.sub(r"vegetation_trees_01_(\d+)", tree, text)
    text = re.sub(KNOXMAP_SPECIES + r"_(\d+)", tree, text)
    if not WILD_FLOWERS:
        text = _without_wild_flowers(text)
    f = [0]

    def flower(_m):
        i = FLOWER_INDICES[f[0] % len(FLOWER_INDICES)]
        f[0] += 1
        return f"{FLOWER_SHEET}_{i}"

    text = re.sub(r"vegetation_groundcover_01_\d+", flower, text)
    out = os.path.join(out_dir, "Rules.txt")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)
    shutil.copy2(os.path.join(os.path.dirname(tools_rules), "Blends.txt"),
                 os.path.join(out_dir, "Blends.txt"))
    return out


def _without_wild_flowers(text: str) -> str:
    """The rules with their groundcover plants taken out of every tile list
    but the Flowers alias's."""
    out, block, name = [], None, ""
    for line in text.splitlines(keepends=True):
        word = line.strip()
        if word in ("alias", "rule"):
            block, name = word, ""
        elif word.startswith("name ="):
            name = word.split("=", 1)[1].strip()
        if GROUNDCOVER.match(line) and not (block == "alias" and name == "flowers"):
            continue
        out.append(line)
    return "".join(out)
