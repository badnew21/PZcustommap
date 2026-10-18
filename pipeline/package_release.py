"""Pack the finished mod into a split 7-Zip archive for download.

    python pipeline/package_release.py [MOD_FOLDER] [OUT_DIR]

The mod is 11 GB of lot files, mostly the same water and forest tiles over and
over, so it packs down to a few hundred megabytes. The archive is cut into
parts under GitHub's 100 MB file limit: IsleRoyale.7z.001, .002, ... 7-Zip
opens the .001 part and reads the rest itself.
"""
from __future__ import annotations

import os
import sys

import multivolumefile
import py7zr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PART = 95 * 1024 * 1024


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    mod = argv[0] if argv else os.path.join(ROOT, "work", "mod", "IsleRoyale")
    out = argv[1] if len(argv) > 1 else os.path.join(ROOT, "release")
    os.makedirs(out, exist_ok=True)
    for f in os.listdir(out):
        if f.startswith("IsleRoyale.7z"):
            os.remove(os.path.join(out, f))
    target = os.path.join(out, "IsleRoyale.7z")
    filters = [{"id": py7zr.FILTER_LZMA2, "preset": 6}]
    with multivolumefile.open(target, mode="wb", volume=PART) as vol:
        with py7zr.SevenZipFile(vol, "w", filters=filters) as z:
            z.writeall(mod, arcname=os.path.basename(mod))
    # multivolumefile numbers the parts .0001; 7-Zip looks for .001.
    for f in os.listdir(out):
        stem, _, num = f.rpartition(".")
        if stem == "IsleRoyale.7z" and num.isdigit():
            os.rename(os.path.join(out, f), os.path.join(out, f"{stem}.{int(num):03d}"))
    parts = sorted(f for f in os.listdir(out) if f.startswith("IsleRoyale.7z"))
    total = sum(os.path.getsize(os.path.join(out, f)) for f in parts)
    print(f"{len(parts)} parts, {total / 1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
