"""Garage doors on garages.

knoxbuild gives a garage (and a fire station) a way in for a car by widening
its street door into three doors side by side (knoxbuild/layout.py,
_widen_vehicle_entry). In the game that is three house doors in a row: it
reads as a garage only once you know the trick, and a car still cannot get in.
The game's own garages have a garage door, which the map tools place as a
piece of wall three tiles wide ("Walls - Garage" in BuildingFurniture.txt,
walls_garage_01): it opens and closes like any door, and a car drives through.

This puts that garage door where knoxbuild made its three doors, the same way
knoxbuild hangs its lift doors (a Walls-layer piece on the doorway's wall
edges), and clears anything furnished in front of it on the inside. Nothing
in KnoxMap is changed: `install()` wraps the widening step in the process
that builds the map, before knoxbuild starts its workers.
"""
from __future__ import annotations

# The two three-tile garage doors in the map tools' "Walls - Garage" group,
# as BuildingFurniture.txt gives them: W runs along a west wall edge, N along
# a north one.
GARAGE_DOORS = [
    {"W": {"0,0": "walls_garage_01_002", "0,1": "walls_garage_01_001",
           "0,2": "walls_garage_01_000"},
     "N": {"0,0": "walls_garage_01_003", "1,0": "walls_garage_01_004",
           "2,0": "walls_garage_01_005"}},
    {"W": {"0,0": "walls_garage_01_010", "0,1": "walls_garage_01_009",
           "0,2": "walls_garage_01_008"},
     "N": {"0,0": "walls_garage_01_011", "1,0": "walls_garage_01_012",
           "2,0": "walls_garage_01_013"}},
]
ROLES = [f"garage_door_{i}" for i in range(len(GARAGE_DOORS))]
WIDTH = 3


def door_run(doors) -> list | None:
    """Three doors side by side on one wall, in order along it, or None."""
    edges = set(doors)
    for x, y, d in sorted(edges):
        step = (0, 1) if d == "W" else (1, 0)
        run = [(x + step[0] * i, y + step[1] * i, d) for i in range(WIDTH)]
        if all(e in edges for e in run):
            return run
    return None


def to_garage_door(plan) -> bool:
    """Replace a plan's three-door vehicle entry with a garage door."""
    run = door_run(plan.doors)
    if run is None:
        return False
    x, y, d = run[0]
    plan.doors = [e for e in plan.doors if e not in run]
    # The tiles either side of the doorway: whatever the room was furnished
    # with there, around what was then one door, stands in the way of a car.
    if d == "W":
        clear = {(cx, y + i) for cx in (x - 1, x) for i in range(WIDTH)}
    else:
        clear = {(x + i, cy) for cy in (y - 1, y) for i in range(WIDTH)}
    plan.furniture = [f for f in plan.furniture if (f[1], f[2]) not in clear]
    plan.furniture.append((ROLES[(x * 7 + y * 13) % len(ROLES)], x, y, d))
    return True


def install() -> None:
    from knoxbuild import catalog as C
    from knoxbuild import layout as L

    for role, piece in zip(ROLES, GARAGE_DOORS):
        C.FURNITURE[role] = piece
        C.FURNITURE_LAYERS[role] = "Walls"
    original = L._widen_vehicle_entry
    if getattr(original, "_garage_door", False):
        return

    def widen(plan, street):
        original(plan, street)
        to_garage_door(plan)

    widen._garage_door = True
    L._widen_vehicle_entry = widen
