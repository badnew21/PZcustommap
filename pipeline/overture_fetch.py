"""Pull Overture Maps features for a bounding box straight from S3.

Overture publishes GeoParquet on a public S3 bucket. DuckDB is the usual way
in, but it needs extensions from a server that is not always reachable, so this
reads the files with pyarrow over plain HTTPS range requests instead:

1. list the theme's parquet files,
2. read each file's footer and keep the row groups whose bbox statistics
   overlap the box,
3. read only those row groups, then keep only the rows that overlap.

    python overture_fetch.py SOUTH WEST NORTH EAST OUT_DIR [--release R]

Writes one <type>.parquet per Overture type into OUT_DIR.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import threading
from concurrent import futures

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import requests

BUCKET_URL = "https://overturemaps-us-west-2.s3.amazonaws.com"
RELEASE = "2026-08-19.0"

# What a Project Zomboid map of a wild place can use.
TYPES = {
    "water": "base",
    "land": "base",
    "land_cover": "base",
    "land_use": "base",
    "infrastructure": "base",
    "segment": "transportation",
    "building": "buildings",
    "place": "places",
}

_local = threading.local()


def _session() -> requests.Session:
    s = getattr(_local, "s", None)
    if s is None:
        s = _local.s = requests.Session()
    return s


class RangeFile:
    """A read-only, seekable file over HTTP range requests."""

    BLOCK = 1 << 20

    def __init__(self, url: str, size: int):
        self.url, self.size, self.pos = url, size, 0
        self._cache: dict[int, bytes] = {}
        self.closed = False

    def seekable(self): return True
    def readable(self): return True
    def tell(self): return self.pos
    def close(self): self.closed = True

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else self.pos + off if whence == 1 else self.size + off
        return self.pos

    def _get(self, start: int, end: int) -> bytes:
        for attempt in range(5):
            try:
                r = _session().get(self.url, headers={"Range": f"bytes={start}-{end - 1}"},
                                   timeout=120)
                if r.status_code in (200, 206):
                    return r.content
            except requests.RequestException:
                pass
        raise IOError(f"range read failed: {self.url} {start}-{end}")

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        n = max(0, min(n, self.size - self.pos))
        if n == 0:
            return b""
        if n > 4 * self.BLOCK:          # big reads (row groups) go straight through
            data = self._get(self.pos, self.pos + n)
        else:
            out, p, left = [], self.pos, n
            while left:
                b = p // self.BLOCK
                if b not in self._cache:
                    lo = b * self.BLOCK
                    self._cache[b] = self._get(lo, min(lo + self.BLOCK, self.size))
                blk = self._cache[b]
                off = p - b * self.BLOCK
                piece = blk[off:off + left]
                out.append(piece)
                p += len(piece)
                left -= len(piece)
            data = b"".join(out)
        self.pos += len(data)
        return data

    def readinto(self, buf):
        data = self.read(len(buf))
        buf[:len(data)] = data
        return len(data)


def list_files(release: str, theme: str, kind: str) -> list[tuple[str, int]]:
    prefix = f"release/{release}/theme={theme}/type={kind}/"
    out, token = [], None
    while True:
        params = {"list-type": "2", "prefix": prefix}
        if token:
            params["continuation-token"] = token
        xml = requests.get(BUCKET_URL + "/", params=params, timeout=60).text
        for key, size in re.findall(r"<Key>([^<]+)</Key>.*?<Size>(\d+)</Size>", xml):
            if key.endswith(".parquet"):
                out.append((f"{BUCKET_URL}/{key}", int(size)))
        m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", xml)
        if not m:
            return out
        token = m.group(1)


def _bbox_stats(md, rg: int, names: dict) -> tuple | None:
    vals = []
    for field, use in (("xmin", "min"), ("xmax", "max"), ("ymin", "min"), ("ymax", "max")):
        st = md.row_group(rg).column(names[field]).statistics
        if st is None or not st.has_min_max:
            return None
        vals.append(st.min if use == "min" else st.max)
    return tuple(vals)


def fetch_file(url: str, size: int, box: tuple[float, float, float, float]) -> pa.Table | None:
    south, west, north, east = box
    f = RangeFile(url, size)
    pf = pq.ParquetFile(f)
    md = pf.metadata
    names = {}
    for i in range(md.num_columns):
        path = md.schema.column(i).path
        if path.startswith("bbox."):
            names[path.split(".")[1]] = i
    keep = []
    for rg in range(md.num_row_groups):
        st = _bbox_stats(md, rg, names)
        if st is None:
            keep.append(rg)
            continue
        xmin_min, xmax_max, ymin_min, ymax_max = st
        if xmin_min <= east and xmax_max >= west and ymin_min <= north and ymax_max >= south:
            keep.append(rg)
    if not keep:
        return None
    t = pf.read_row_groups(keep)
    bb = t.column("bbox")
    mask = pc.and_(pc.and_(pc.less_equal(pc.struct_field(bb, "xmin"), east),
                           pc.greater_equal(pc.struct_field(bb, "xmax"), west)),
                   pc.and_(pc.less_equal(pc.struct_field(bb, "ymin"), north),
                           pc.greater_equal(pc.struct_field(bb, "ymax"), south)))
    t = t.filter(mask)
    return t if t.num_rows else None


def fetch_type(kind: str, box, release: str = RELEASE, workers: int = 16,
               say=print) -> pa.Table | None:
    files = list_files(release, TYPES[kind], kind)
    say(f"{kind}: {len(files)} files")
    tables = []
    with futures.ThreadPoolExecutor(workers) as ex:
        jobs = {ex.submit(fetch_file, u, s, box): u for u, s in files}
        for job in futures.as_completed(jobs):
            t = job.result()
            if t is not None:
                tables.append(t)
    if not tables:
        say(f"{kind}: nothing in the box")
        return None
    t = pa.concat_tables(tables, promote_options="permissive")
    say(f"{kind}: {t.num_rows} features")
    return t


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("south", type=float)
    ap.add_argument("west", type=float)
    ap.add_argument("north", type=float)
    ap.add_argument("east", type=float)
    ap.add_argument("out_dir")
    ap.add_argument("--release", default=RELEASE)
    ap.add_argument("--types", default=",".join(TYPES))
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    box = (a.south, a.west, a.north, a.east)
    for kind in a.types.split(","):
        path = os.path.join(a.out_dir, f"{kind}.parquet")
        if os.path.exists(path):
            print(f"{kind}: cached")
            continue
        t = fetch_type(kind, box, a.release)
        if t is not None:
            pq.write_table(t, path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
