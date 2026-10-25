"""Check b36_rig.json against the source glTF: every rotation in Unity space must move the
same points the source rotation moves, and turret aim angles must agree.

    python test_rig.py <b36 checkout> <b36_rig.json>
"""

import json
import math
import sys
from pathlib import Path

from convert_b36 import MODEL, add, norm, qrot, world_transforms


def rodrigues(p, pivot, axis, deg):
    """Right-hand rule rotation; Unity's Quaternion.AngleAxis builds the same matrix."""
    k = norm(axis)
    v = tuple(a - b for a, b in zip(p, pivot))
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    dot = sum(a * b for a, b in zip(k, v))
    cross = (k[1] * v[2] - k[2] * v[1], k[2] * v[0] - k[0] * v[2], k[0] * v[1] - k[1] * v[0])
    r = tuple(v[i] * c + cross[i] * s + k[i] * dot * (1 - c) for i in range(3))
    return add(r, pivot)


def u(p):
    return (p[0], p[1], -p[2])


def close(a, b, tol=2e-4):
    return all(abs(x - y) < tol for x, y in zip(a, b))


def main():
    repo, rig_path = Path(sys.argv[1]), Path(sys.argv[2])
    gltf = json.loads((repo / MODEL).read_text())
    rig = json.loads(rig_path.read_text())
    world = world_transforms(gltf)
    nodes = gltf["nodes"]
    idx = {n["name"]: i for i, n in enumerate(nodes)}
    failures = 0
    samples = [(1.3, -0.9, 0.4), (-0.7, 0.5, 2.0), (0.2, -2.5, -1.1)]

    for part in rig["parts"]:
        if part["kind"] in ("wheel", "prop"):
            angle = 33.0  # any angle: these spin freely
        else:
            angle = part["angle"]
        n = nodes[idx[part["name"]]]
        t, r = world[idx[part["name"]]]
        axis_g = qrot(r, n["extras"]["axis"])
        for off in samples:
            p = add(t, off)
            want = u(rodrigues(p, t, axis_g, angle))
            got = rodrigues(u(p), part["pivot"], part["axis"], angle)
            if not close(want, got):
                failures += 1
                print(f"FAIL {part['name']}: {want} != {got}")

    for tur in rig["turrets"]:
        t, r = world[idx[tur["name"]]]
        up_g, right_g, fwd_g = qrot(r, (0, 1, 0)), qrot(world[idx[tur["guns"]]][1], (1, 0, 0)), qrot(world[idx[tur["guns"]]][1], (0, 0, -1))
        for d_g in [(0.3, 0.5, -0.8), (-0.9, 0.1, 0.2), (0.0, -0.4, 0.9)]:
            d_g = norm(d_g)
            # Source (turrets.ts solve): local x = right, y = up, z = -forward.
            x = sum(a * b for a, b in zip(d_g, right_g))
            y = sum(a * b for a, b in zip(d_g, up_g))
            f = sum(a * b for a, b in zip(d_g, fwd_g))
            az_g, el_g = math.atan2(-x, f), math.atan2(y, math.hypot(x, f))
            d_u = u(d_g)
            x = sum(a * b for a, b in zip(d_u, tur["right"]))
            y = sum(a * b for a, b in zip(d_u, tur["up"]))
            f = sum(a * b for a, b in zip(d_u, tur["forward"]))
            az_u, el_u = math.atan2(-x, f), math.atan2(y, math.hypot(x, f))
            if abs(az_g - az_u) > 1e-4 or abs(el_g - el_u) > 1e-4:
                failures += 1
                print(f"FAIL aim {tur['name']}")
            # Yaw by az then pitch by el must turn the gun line onto the target direction.
            yawed_f = rodrigues(tur["forward"], (0, 0, 0), tur["yawAxis"], math.degrees(az_u))
            yawed_pitch_axis = rodrigues(tur["pitchAxis"], (0, 0, 0), tur["yawAxis"], math.degrees(az_u))
            aimed = rodrigues(yawed_f, (0, 0, 0), yawed_pitch_axis, math.degrees(el_u))
            if not close(aimed, d_u, 1e-4):
                failures += 1
                print(f"FAIL aim pose {tur['name']}: {aimed} != {d_u}")
        ext = add(u(t), tuple(c * tur["retract"] for c in tur["up"]))
        if not close(ext, tur["extendedPivot"]):
            failures += 1
            print(f"FAIL extension {tur['name']}")

    nose, tail = rig["landmarks"]["Turret_Nose"], rig["landmarks"]["Turret_Tail"]
    if not (nose[2] > 0 > tail[2] and rig["landmarks"]["Prop_L1"][0] < 0):
        failures += 1
        print("FAIL frame: nose must be +Z and the left wing -X")

    print(f"{'ok' if not failures else 'FAILED'}: {len(rig['parts'])} parts, {len(rig['turrets'])} turrets, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
