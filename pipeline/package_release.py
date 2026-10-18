"""Pack a finished mod into a split 7-Zip archive for download.

    python pipeline/package_release.py [MOD_FOLDER] [OUT_DIR]

MOD_FOLDER defaults to work/mod/IsleRoyale; the archive is named after it.

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
    stem = f"{os.path.basename(os.path.normpath(mod))}.7z"
    for f in os.listdir(out):
        if f.startswith(stem):
            os.remove(os.path.join(out, f))
    target = os.path.join(out, stem)
    filters = [{"id": py7zr.FILTER_LZMA2, "preset": 6}]
    with multivolumefile.open(target, mode="wb", volume=PART) as vol:
        with py7zr.SevenZipFile(vol, "w", filters=filters) as z:
            z.writeall(mod, arcname=os.path.basename(mod))
    # multivolumefile numbers the parts .0001; 7-Zip looks for .001.
    target_name = os.path.basename(target)
    for f in os.listdir(out):
        base, _, num = f.rpartition(".")
        if base == target_name and num.isdigit():
            os.rename(os.path.join(out, f), os.path.join(out, f"{base}.{int(num):03d}"))
    parts = sorted(f for f in os.listdir(out) if f.startswith(target_name))
    if parts == [f"{target_name}.001"]:
        # Small enough for one file: an ordinary .7z.
        os.rename(os.path.join(out, parts[0]), target)
        parts = [target_name]
    total = sum(os.path.getsize(os.path.join(out, f)) for f in parts)
    print(f"{len(parts)} parts, {total / 1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
