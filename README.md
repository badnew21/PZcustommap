# Isle Royale for Project Zomboid

A Build 42 map of Isle Royale National Park, Michigan, at one tile to the
metre. It covers the whole island and its outer islands, from the Rock of Ages
lighthouse in the southwest to Passage Island in the northeast: about 570 km² of
land.

![The whole map](docs/overview.png)

![Rock Harbor at one pixel per tile](docs/rock_harbor.png)

It is built with [KnoxMap](https://github.com/badnew21/KnoxmapDM), which turns
real map data into Project Zomboid maps. KnoxMap is made for towns of a few
square kilometres, and the island is 86 km long once it is laid out on the tile
grid, so this repository adds a pipeline that hands KnoxMap the island one
piece at a time and stitches the pieces back together. KnoxMap itself is not
changed.

## What is on the map

- **The shoreline, over a hundred islands and rocks, and the inland lakes**: Siskiwit,
  Desor, Richie, Chickenbone, Feldtmann and the rest, with the streams between
  them.
- **Forest almost everywhere**, as on the real island, with bogs and wetlands,
  open rock ridges and a few beaches.
- **The trail network**: Greenstone Ridge, Minong Ridge, Feldtmann Ridge,
  Rock Harbor, Indian Portage and the access trails. Trails are bare dirt
  through the trees, and they are named on the in-game map like streets.
- **Rock Harbor, Mott Island and Windigo**: the lodge, cabins, store, ranger
  stations and docks. Each building is a furnished building, made by
  KnoxMap's knoxbuild on its real footprint.
- **Campgrounds** as clearings: Daisy Farm, Moskey Basin, Three Mile, Lake
  Desor, Chickenbone Lake and others.
- **Lighthouses and outlying buildings**: Rock of Ages, Isle Royale Light,
  Rock Harbor Light, Edisen Fishery and the fire towers.
- **An in-game map (M)** of the whole island, labelled with its harbours,
  lakes, islands, peaks, campgrounds and trails.
- **Zombies** where people would be: some at Rock Harbor and Windigo (KnoxMap's
  population estimate), a few along the trails and at the campgrounds, and
  almost none in the deep woods.

New characters start at the Rock Harbor and Windigo ferry landings or at one of
the campgrounds.

### Things to know

- **North is turned 30°.** The island runs southwest to northeast, so the map
  is rotated until that long axis runs left to right. A north-up frame would
  have been 65 × 40 km, mostly water.
- **Open lake is left out.** Every cell holding land keeps one cell (256 m) of
  water around it. Cells further out are not part of the map, since no one can
  get out there. Each of those empty cells would otherwise add about 300 KB of
  water to the mod.
- **The outer islands are out of reach.** Project Zomboid has no boats, so
  the Rock of Ages lighthouse, Passage Island and the outer islands can be
  seen from shore but not visited. The same goes for Mott Island and the
  islands across the Rock Harbor channel.
- **The woods are broadleaf only for now.** Trees are paper birch (most of
  them), red maple and linden, drawn from Build 42's per-species tree sheets.
  The real island is largely spruce and fir. Build 42 has hemlock and pine
  sheets, but they are laid out differently from the broadleaf ones and are
  left out until someone checks them in the game (`pipeline/rules.py`).
- **There is no elevation.** Build 42 maps are flat, so the ridges show only as
  bare rock and scrub.
- **The map is large.** See [Size](#size).

## Installing the ready-made mod

The finished mod is in [`release/`](release) as a split 7-Zip archive:
`IsleRoyale.7z.001`, `.002` and so on.

1. Download every part into one folder.
2. Open `IsleRoyale.7z.001` with [7-Zip](https://www.7-zip.org) (on Linux,
   `7z x IsleRoyale.7z.001`). It reads the other parts by itself.
3. Extract the `IsleRoyale` folder into your Zomboid mods folder:
   `%USERPROFILE%\Zomboid\mods` on Windows, `~/Zomboid/mods` on Linux and
   macOS. Unpacked it is 11 GB.
4. Start Project Zomboid (Build 42), enable **Isle Royale** under Mods, and
   start a **new** game on it.

Read `HOW TO PLAY.txt` in the mod folder first: a map this big needs more
memory than the game gives itself by default.

## Building it

You only need this to change the map or rebuild it from fresher data.
Nothing has to be done by hand. On 4 cores and 16 GB of memory the
whole build took about 40 minutes. It needs about 20 GB of free disk: 9 GB for
the two island-wide bitmaps and 11 GB for the lot files. On the same drive,
the mod folder hard-links the lot files instead of copying them.

You need:

- **KnoxMap** set up as its README describes (`Setup.bat`, or `./setup.sh` on
  Linux and macOS). Setup provides the Python environment and the PZ Mapping
  Tools. The pipeline reads the tools' `Rules.txt` and `Blends.txt`.
- **knoxlots**, KnoxMap's lot compiler. KnoxMap releases ship it beside
  `KnoxMap.exe`. In a git checkout, build it with
  `cargo build --release` in `rust/knoxlots`.
- **pyarrow** in KnoxMap's environment:
  `.venv\Scripts\python -m pip install pyarrow`

Then point the pipeline at KnoxMap and run it:

```bat
set KNOXMAP_DIR=C:\path\to\KnoxMap
%KNOXMAP_DIR%\.venv\Scripts\python pipeline\build_island.py --mods-dir %USERPROFILE%\Zomboid\mods
```

On Linux or macOS:

```sh
export KNOXMAP_DIR=~/KnoxMap
$KNOXMAP_DIR/.venv/bin/python pipeline/build_island.py --mods-dir ~/Zomboid/mods
```

Then enable **Isle Royale** under Mods and start a new game on it.

The stages run in order, and each one keeps its results under `work/`. If a
run stops, run the same command again to carry on. To redo one step after a
change, use `--stages`:

| Stage | What it does |
|---|---|
| `fetch` | Downloads the park's data from Overture Maps into `data/overture`. A copy is already checked in, so this is only needed for fresher data. |
| `terrain` | Draws the island in 2.4 km strips with KnoxMap's renderer and stitches them into one ground and one vegetation bitmap. |
| `buildings` | Draws each cluster of buildings again as its own KnoxMap map, runs knoxbuild on it, and pastes the result back. |
| `spawn` | Makes the zombie spawn map. |
| `papermap` | Writes the in-game map, its labels and the spawn points. |
| `compile` | Compiles the lot files with knoxlots, a few rows of cells at a time. |
| `mod` | Packages the mod with KnoxMap's own `make_map_mod`. |

`--workers N` sets how many processes compile at once. `--render-workers N`
sets how many strips are drawn at once; each takes about 2.5 GB.

## How it works

Everything is placed in one fixed frame: a rectangle of whole 300-tile cells
in UTM zone 16N, turned 30° about its centre (`pipeline/isle.py`). A window is
any rectangle of that frame. `SectionProjector` is a KnoxMap `Projector` that
places latitude and longitude on a window's pixels exactly where the whole
frame would put them, so windows drawn on their own line up to the tile. While
a window is rendered or built, KnoxMap's `Projector.build` returns that
window's projector, so the renderer and knoxbuild work on it as if it were an
ordinary map.

The data is [Overture Maps](https://overturemaps.org): OpenStreetMap's
coastline, lakes, wetlands, trails, docks and buildings, plus ESA WorldCover
forest cover. `pipeline/overture_fetch.py` reads the GeoParquet files straight
from Overture's public S3 bucket with HTTP range requests. It downloads only
the row groups that overlap the park, so no DuckDB is needed.
`pipeline/isle.py` turns the rows into KnoxMap's `OSMFeature`s with OSM tags,
so KnoxMap's own classifier and painters draw them.

The compile uses its own copy of the map tools' `Rules.txt`, with the forest
tiles set to Build 42 tree sheets (`pipeline/rules.py`). The tools' shipped
rules use a tree sheet Build 42 no longer has artwork for. This way the build
doesn't depend on KnoxMap's Setup having patched them, and the tools' own files
stay unchanged.

KnoxMap was written for towns, so `pipeline/tidy.py` adds a few wilderness
rules. Trees are cleared off the trails, campgrounds keep only a few trees,
and each building stands in a small grass clearing.

## Size

| | |
|---|---|
| Frame | 85,800 × 17,100 tiles (286 × 57 KnoxMap cells) |
| Land | about 570 km² |
| Build 42 cells compiled | 10,975 (of 22,500 in the frame; the rest are open lake) |
| Buildings | 198, plus their fences and docks (286 lots), in 25 clusters from Rock of Ages to Passage Island |
| Mod on disk | 11 GB |
| Build time | terrain 14 min, buildings 1 min, compile 27 min (4 cores) |

Project Zomboid needs extra memory for a map this size. KnoxMap writes a
`HOW TO PLAY.txt` into the mod that says how to give the game more.

## Credits and licences

- Map data © OpenStreetMap contributors, under the
  [ODbL](https://www.openstreetmap.org/copyright), as published by Overture
  Maps.
- Forest cover © ESA WorldCover project 2021, contains modified Copernicus
  Sentinel data, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/),
  via Overture Maps.
- [KnoxMap](https://github.com/badnew21/KnoxmapDM) by euclid80tr, MIT. It is
  built on Knoxify by arytek and on the PZ Mapping Tools by Alree/Unjammer
  (TileZed and WorldEd by Tim Baker).
- Project Zomboid is a trademark of The Indie Stone. This is an unofficial fan
  map and contains no game files.

Isle Royale is a real national park and a fragile one. The zombies are made
up. The wolves and moose are not, and they are not in the game either.
