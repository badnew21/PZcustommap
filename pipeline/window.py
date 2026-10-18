"""Render one window of the island's frame with KnoxMap's renderer.

    python window.py OX OY WIDTH HEIGHT OUT_DIR [--name NAME]

OX, OY, WIDTH and HEIGHT are frame tiles and must be whole 300-tile cells. The
window becomes an ordinary KnoxMap map folder (bitmaps, geojson, _info.json),
so knoxbuild can build its buildings like any other map's.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import isle  # noqa: E402  (sets up KnoxMap on the path)
from generator import renderer  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "overture")
FRAME = os.path.join(ROOT, "data", "frame.json")

# Painted past the window on every side and then thrown away, so woods, shore
# blends and wild growth carry on across a seam instead of stopping at it.
PAD = 64

_data = None


def data() -> isle.Data:
    global _data
    if _data is None:
        _data = isle.Data(DATA)
    return _data


def frame() -> isle.Frame:
    if not os.path.exists(FRAME):
        f = isle.make_frame(data().land)
        isle.save_frame(FRAME, f)
        return f
    return isle.load_frame(FRAME)


def window_utm(fr: isle.Frame, ox, oy, w, h, pad=PAD):
    px = Polygon([(ox - pad, oy - pad), (ox + w + pad, oy - pad),
                  (ox + w + pad, oy + h + pad), (ox - pad, oy + h + pad)])
    return fr.px_to_utm(px)


def section(ox: int, oy: int, w: int, h: int) -> isle.SectionProjector:
    """Frame tiles [ox, ox+w) x [oy, oy+h) as a registered KnoxMap projector."""
    sp = isle.SectionProjector(frame().projector(), ox, oy, w, h)
    isle.register(sp)
    return sp


def render_window(ox: int, oy: int, w: int, h: int, out_dir: str, name: str,
                  pad: int = 0, **kw):
    """Draw frame tiles [ox, ox+w) x [oy, oy+h) (plus `pad` all round) as a map."""
    fr = frame()
    sp = section(ox - pad, oy - pad, w + 2 * pad, h + 2 * pad)
    bbox = (sp.south, sp.west, sp.north, sp.east)
    t = time.time()
    feats = data().features(window_utm(fr, ox - pad, oy - pad, w + 2 * pad, h + 2 * pad, pad=8))
    t_feat = time.time() - t
    r = renderer.render(feats, *bbox, meters_per_tile=1.0, output_dir=out_dir, map_name=name,
                        rotation=sp.rotation, straight_roads=False,
                        spawn_density=kw.get("spawn_density", 10),
                        tree_density=kw.get("tree_density", 1.0),
                        forest_density=kw.get("forest_density", 1.0))
    print(f"{name}: {len(feats)} features ({t_feat:.1f}s), {r.width}x{r.height} tiles, "
          f"{time.time() - t:.1f}s")
    return r


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for k in ("ox", "oy", "width", "height"):
        ap.add_argument(k, type=int)
    ap.add_argument("out_dir")
    ap.add_argument("--name")
    a = ap.parse_args(argv)
    for v in (a.ox, a.oy, a.width, a.height):
        if v % isle.CELL:
            ap.error("window edges must be whole 300-tile cells")
    name = a.name or os.path.basename(os.path.normpath(a.out_dir))
    render_window(a.ox, a.oy, a.width, a.height, a.out_dir, name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
