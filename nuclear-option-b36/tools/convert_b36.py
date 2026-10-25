"""Turn the B-36 glTF from noahdemar/b36 into the files the Unity builder needs.

Run with Blender's Python module (bpy), for example:

    cd nuclear-option-b36/tools
    uv run python convert_b36.py --out <BlueprinterProject>/Assets/Blueprinter/Mods/B36Peacemaker/Art

Writes into --out:
    B36.fbx                  the exterior, metres, every named part kept as its own object
    Textures/                colour and normal maps plus Unity metallic/occlusion maps
    b36_rig.json             every hinge, turret, wheel and bay in Unity model space
    b36_materials.json       material settings for the builder
    SOURCE.txt               where the model came from

and copies b36_rig.json to ../plugin/b36_rig.json, where the runtime plugin embeds it.

Unity model space: +X right wing, +Y up, +Z toward the nose, metres, origin at the
model origin (mid-length). glTF space in the source is +X right, +Y up, nose toward -Z,
so a point maps as (x, y, -z). That map is a reflection (the two systems differ in
handedness), so an axis/angle rotation maps as axis (-x, -y, z) with the same angle.
test_rig.py checks this against the source numbers.
"""

import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

SOURCE_REPO = "https://github.com/noahdemar/b36"
SOURCE_COMMIT = "b60dba5e347dd7d3683b42281b7b47ea44694d35"
MODEL = "web/public/models/b36.gltf"

HERE = Path(__file__).resolve().parent
PLUGIN_RIG = HERE.parent / "plugin" / "b36_rig.json"

# Bay limits as distances aft of the nose, from tools/model/params.py in the source (BOMB_BAYS).
BOMB_BAYS = [(13.6, 17.9), (18.1, 22.4), (26.6, 30.0), (30.2, 33.6)]
LENGTH = 49.40  # source params.py, origin at mid-length
CG_GLTF = (0.0, 0.2, -2.4)  # source web/src/sim/params.ts "cg", about 30% MAC
MAX_SURFACE = 25.0  # deg, source web/src/aircraft.ts
MAX_FLAP = 40.0
GEAR_RETRACT = {"Gear_Nose": -1.6, "Gear_Main_L": 1.6, "Gear_Main_R": 1.6}  # rad, source aircraft.ts
# Turret mechanical limits from the source web/src/weapons/turrets.ts (deg).
TURRET_LIMITS = {
    "upper": {"az": None, "el": [-6, 85]},
    "lower": {"az": None, "el": [-6, 85]},
    "nose": {"az": [-30, 30], "el": [-25, 45]},
    "tail": {"az": [-40, 40], "el": [-35, 40]},
}


# ---------------------------------------------------------------- small vector maths

def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def qrot(q, v):
    p = qmul(qmul(q, (v[0], v[1], v[2], 0.0)), (-q[0], -q[1], -q[2], q[3]))
    return p[:3]


def add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def norm(v):
    n = math.sqrt(sum(x * x for x in v))
    return tuple(x / n for x in v)


def to_unity_point(p):
    return [round(p[0], 5), round(p[1], 5), round(-p[2], 5)]


def to_unity_axis(a):
    a = norm(a)
    return [round(-a[0], 6), round(-a[1], 6), round(a[2], 6)]


# ---------------------------------------------------------------- source

def fetch_source(cache: Path) -> Path:
    repo = cache / "b36"
    if not (repo / ".git").exists():
        cache.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--filter=blob:none", SOURCE_REPO, str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", SOURCE_COMMIT], check=True)
    return repo


def world_transforms(gltf):
    """World (translation, rotation) of every node, rotation as (x, y, z, w). The model has no scale."""
    nodes = gltf["nodes"]
    parent = {}
    for i, n in enumerate(nodes):
        for c in n.get("children", []):
            parent[c] = i
    out = {}

    def solve(i):
        if i in out:
            return out[i]
        n = nodes[i]
        if "scale" in n and any(abs(s - 1) > 1e-6 for s in n["scale"]):
            raise SystemExit(f"node {n.get('name')} is scaled; the rig maths assumes it is not")
        t = tuple(n.get("translation", (0, 0, 0)))
        r = tuple(n.get("rotation", (0, 0, 0, 1)))
        if i in parent:
            pt, pr = solve(parent[i])
            t = add(pt, qrot(pr, t))
            r = qmul(pr, r)
        out[i] = (t, r)
        return out[i]

    for i in range(len(nodes)):
        solve(i)
    return out


# ---------------------------------------------------------------- rig

def build_rig(gltf):
    nodes = gltf["nodes"]
    world = world_transforms(gltf)
    by_name = {n["name"]: i for i, n in enumerate(nodes)}

    def pos(name):
        return world[by_name[name]][0]

    def axis(name, local):
        return qrot(world[by_name[name]][1], local)

    def extras(name):
        return nodes[by_name[name]].get("extras", {})

    parts = []
    for i, n in enumerate(nodes):
        e = n.get("extras", {})
        kind = e.get("part")
        name = n["name"]
        if kind in ("aileron", "elevator", "rudder", "flap"):
            parts.append({"name": name, "kind": kind, "side": e.get("side", "C"),
                          "pivot": to_unity_point(pos(name)), "axis": to_unity_axis(axis(name, e["axis"])),
                          "angle": MAX_FLAP if kind == "flap" else MAX_SURFACE})
        elif kind == "bombdoor":
            parts.append({"name": name, "kind": kind, "side": e["side"], "bay": e["bay"],
                          "pivot": to_unity_point(pos(name)), "axis": to_unity_axis(axis(name, e["axis"])),
                          "angle": round(math.degrees(e["open_angle"]), 4)})
        elif kind == "gear":
            parts.append({"name": name, "kind": kind, "side": e["side"],
                          "pivot": to_unity_point(pos(name)), "axis": to_unity_axis(axis(name, e["axis"])),
                          # stowed angle: the source rotates by (1 - gear) * this
                          "angle": round(math.degrees(GEAR_RETRACT[name]), 4)})
        elif kind == "prop":
            parts.append({"name": name, "kind": kind, "side": e["side"], "engine": e["engine"],
                          "pivot": to_unity_point(pos(name)), "axis": to_unity_axis(axis(name, e["axis"])),
                          "angle": 0.0})
        elif kind == "wheel":
            parts.append({"name": name, "kind": kind, "side": e["leg"], "radius": e["radius"],
                          "pivot": to_unity_point(pos(name)), "axis": to_unity_axis(axis(name, e["axis"])),
                          "angle": 0.0})

    turrets = []
    for i, n in enumerate(nodes):
        e = n.get("extras", {})
        if e.get("part") != "turret_yaw":
            continue
        name = n["name"]
        guns = name + "_Guns"
        kind = "nose" if "Nose" in name else "tail" if "Tail" in name else "lower" if "Lower" in name else "upper"
        up = axis(name, (0, 1, 0))
        turrets.append({
            "name": name, "guns": guns, "kind": kind,
            "pivot": to_unity_point(pos(name)),
            "gunsPivot": to_unity_point(pos(guns)),
            # Retractable turrets are modelled stowed; they rise this far along the yaw axis.
            "retract": e.get("retract", 0.0),
            "extendedPivot": to_unity_point(add(pos(name), tuple(c * e.get("retract", 0.0) for c in up))),
            # Rotation axes (they map as axes) and the rest basis of the guns (directions map like points).
            # Aim angles follow the source: az = atan2(-right, forward), el = atan2(up, horizontal);
            # yaw is +az about yawAxis, then pitch is +el about pitchAxis.
            "yawAxis": to_unity_axis(up),
            "pitchAxis": to_unity_axis(axis(guns, (1, 0, 0))),
            "up": to_unity_point(norm(up)),
            "right": to_unity_point(norm(axis(guns, (1, 0, 0)))),
            "forward": to_unity_point(norm(axis(guns, (0, 0, -1)))),
            "limits": TURRET_LIMITS[kind],
        })

    # Wheel contact: lowest point of each leg's wheels with the gear down and the oleo unloaded.
    legs = {}
    for p in parts:
        if p["kind"] == "wheel":
            leg = legs.setdefault(p["side"], {"leg": p["side"], "wheels": [], "radius": p["radius"]})
            leg["wheels"].append(p["name"])
    for leg in legs.values():
        cs = [next(q["pivot"] for q in parts if q["name"] == w) for w in leg["wheels"]]
        leg["center"] = [round(sum(c[k] for c in cs) / len(cs), 5) for k in range(3)]
        leg["contact"] = [leg["center"][0], round(leg["center"][1] - leg["radius"], 5), leg["center"][2]]

    nose_z = -LENGTH / 2  # glTF z of the nose
    bays = []
    for k, (a, b) in enumerate(BOMB_BAYS):
        # The doors are skin panels that roll up around the fuselage axis (as on the real
        # aircraft), so their pivot is the axis itself. The fuselage section through each bay
        # is measured from the mesh in export_fbx and filled in there.
        bays.append({"index": k + 1, "zFwd": round(-(nose_z + a), 5), "zAft": round(-(nose_z + b), 5),
                     "doors": [p["name"] for p in parts if p["kind"] == "bombdoor" and p["bay"] == k + 1]})

    landmarks = {k: to_unity_point(pos(k)) for k in ("Turret_Nose", "Turret_Tail", "Prop_L1", "Prop_R1", "Rudder")}

    # Engine exhaust points (piston stacks and jet nozzles) for effects.
    exhausts = [{"name": n["name"], "jet": "jet_exhaust" in n.get("extras", {}),
                 "pos": to_unity_point(pos(n["name"]))}
                for n in nodes if "exhaust" in n.get("extras", {}) or "jet_exhaust" in n.get("extras", {})]

    return {
        "source": f"{SOURCE_REPO} @ {SOURCE_COMMIT}",
        "frame": "Unity model space: +X right wing, +Y up, +Z toward the nose; metres and degrees; origin at mid-length",
        "length": LENGTH, "span": 70.10, "height": 14.25,
        "cg": to_unity_point(CG_GLTF),
        "landmarks": landmarks,
        "parts": parts,
        "turrets": turrets,
        "legs": sorted(legs.values(), key=lambda l: l["leg"]),
        "bays": bays,
        "exhausts": exhausts,
        "wingtips": {},  # filled from the wing mesh in export_fbx
    }


# ---------------------------------------------------------------- materials and textures

def build_materials(gltf, src_dir: Path, tex_out: Path):
    from PIL import Image

    tex_out.mkdir(parents=True, exist_ok=True)
    images = [img["uri"] for img in gltf["images"]]

    def image_of(ref):
        return images[gltf["textures"][ref["index"]]["source"]]

    converted = {}

    def metallic_occlusion(orm_uri):
        """glTF ORM (R occlusion, G roughness, B metal) to Unity Standard maps."""
        if orm_uri in converted:
            return converted[orm_uri]
        stem = Path(orm_uri).stem.replace("_orm", "")
        orm = Image.open(src_dir / orm_uri).convert("RGB")
        r, g, b = orm.split()
        smooth = g.point(lambda v: 255 - v)
        black = Image.new("L", orm.size, 0)
        Image.merge("RGBA", (b, black, black, smooth)).save(tex_out / f"{stem}_metallic.png")
        r.save(tex_out / f"{stem}_occlusion.png")
        converted[orm_uri] = (f"{stem}_metallic.png", f"{stem}_occlusion.png")
        return converted[orm_uri]

    out = {}
    for m in gltf["materials"]:
        name = m["name"]
        if name in out:
            continue  # the source repeats Skin_Wing and Skin_Fuselage with identical settings
        pbr = m.get("pbrMetallicRoughness", {})
        entry = {
            "name": name,
            "color": [round(c, 4) for c in pbr.get("baseColorFactor", [1, 1, 1, 1])],
            "metallic": round(pbr.get("metallicFactor", 1.0), 4),
            "smoothness": round(1.0 - pbr.get("roughnessFactor", 1.0), 4),
            "transparent": m.get("alphaMode") == "BLEND",
            "emissive": name.startswith("Lamp"),
        }
        if "baseColorTexture" in pbr:
            uri = image_of(pbr["baseColorTexture"])
            shutil.copy2(src_dir / uri, tex_out / Path(uri).name)
            entry["colorMap"] = Path(uri).name
        if "normalTexture" in m:
            uri = image_of(m["normalTexture"])
            shutil.copy2(src_dir / uri, tex_out / Path(uri).name)
            entry["normalMap"] = Path(uri).name
        if "metallicRoughnessTexture" in pbr:
            entry["metallicMap"], entry["occlusionMap"] = metallic_occlusion(image_of(pbr["metallicRoughnessTexture"]))
            entry["metallic"] = 1.0
            entry["smoothness"] = 1.0
        out[name] = entry
    return list(out.values())


# ---------------------------------------------------------------- FBX

def export_fbx(gltf_path: Path, fbx_path: Path, rig):
    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(gltf_path))

    # Fold the duplicate materials (Skin_Wing.001 and so on) into the first copy so the
    # FBX carries one material per source name, which is what the builder remaps.
    for obj in bpy.data.objects:
        for slot in getattr(obj, "material_slots", []):
            mat = slot.material
            if mat and "." in mat.name and mat.name.rsplit(".", 1)[1].isdigit():
                base = bpy.data.materials.get(mat.name.rsplit(".", 1)[0])
                if base:
                    slot.material = base

    # Blender names an object after its glTF node; the mesh data can differ. Unity uses
    # object names for the GameObjects, which is what the builder and the plugin look up.
    names = {o.name for o in bpy.data.objects}
    measure_bays([bpy.data.objects[n] for n in ["Fuselage"] + [d for b in rig["bays"] for d in b["doors"]]], rig)
    wing = bpy.data.objects["Wing"]
    tips = [wing.matrix_world @ v.co for v in wing.data.vertices]
    for side, sign in (("L", -1), ("R", 1)):
        far = max(sign * p.x for p in tips)
        edge = [p for p in tips if sign * p.x > far - 0.3]
        rig["wingtips"][side] = [round(sum(p.x for p in edge) / len(edge), 4), round(sum(p.z for p in edge) / len(edge), 4),
                                 round(sum(p.y for p in edge) / len(edge), 4)]  # Blender (x, y, z) is Unity (x, z, y)
    needed = {p["name"] for p in rig["parts"]} | {t["name"] for t in rig["turrets"]} | {t["guns"] for t in rig["turrets"]}
    missing = sorted(needed - names)
    if missing:
        raise SystemExit(f"glTF import lost nodes: {missing}")

    bpy.ops.object.select_all(action="SELECT")
    fbx_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.fbx(
        filepath=str(fbx_path),
        use_selection=False,
        object_types={"EMPTY", "MESH"},
        apply_unit_scale=True,
        apply_scale_options="FBX_SCALE_UNITS",
        axis_forward="-Z",
        axis_up="Y",
        bake_space_transform=False,
        use_mesh_modifiers=True,
        mesh_smooth_type="FACE",
        add_leaf_bones=False,
        bake_anim=False,
        path_mode="STRIP",
        embed_textures=False,
    )
    return sorted(names)


def measure_bays(skin, rig):
    """Fuselage section (skin plus bay doors) through each bay, in Unity model space.

    Blender's glTF importer maps glTF (x, y, z) to Blender (x, -z, y), so a Blender world
    point (bx, by, bz) is Unity (bx, bz, by).
    """
    pts = [o.matrix_world @ v.co for o in skin for v in o.data.vertices]
    for bay in rig["bays"]:
        lo, hi = sorted((bay["zAft"], bay["zFwd"]))
        band = [(p.x, p.z) for p in pts if lo <= p.y <= hi]  # (unity x, unity y)
        centre = [y for x, y in band if abs(x) < 0.15]
        if not band or not centre:
            raise SystemExit(f"no fuselage vertices across bay {bay['index']}")
        top, bottom = max(centre), min(centre)
        bay["topY"] = round(top, 4)
        bay["bottomY"] = round(bottom, 4)
        bay["halfWidth"] = round(max(abs(x) for x, y in band), 4)
        # Shackles hang from the bay roof, a little under the top skin, so stores hang inside
        # the closed doors.
        bay["hardpointY"] = round(top - 0.35, 4)


def check_fbx(fbx_path: Path, rig):
    """Re-import the FBX and check the landmarks still sit where the rig says (up to a rotation)."""
    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=str(fbx_path))
    objs = {o.name: o for o in bpy.data.objects}
    for name in ("Turret_Nose", "Turret_Tail", "Prop_L1"):
        if name not in objs:
            raise SystemExit(f"FBX lost {name}")
    nose = objs["Turret_Nose"].matrix_world.translation
    tail = objs["Turret_Tail"].matrix_world.translation
    dist = (nose - tail).length
    want = math.dist(rig["landmarks"]["Turret_Nose"], rig["landmarks"]["Turret_Tail"])
    if abs(dist - want) > 0.01:
        raise SystemExit(f"FBX scale is off: nose to tail {dist:.3f} m, want {want:.3f} m")
    return dist


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, type=Path, help="folder to write the FBX, textures and rig into")
    ap.add_argument("--source", type=Path, help="an existing checkout of noahdemar/b36 (default: clone it)")
    ap.add_argument("--cache", type=Path, default=HERE / ".cache", help="where to clone the source")
    args = ap.parse_args()

    repo = args.source or fetch_source(args.cache)
    gltf_path = repo / MODEL
    gltf = json.loads(gltf_path.read_text())

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    rig = build_rig(gltf)
    mats = build_materials(gltf, gltf_path.parent, out / "Textures")
    export_fbx(gltf_path, out / "B36.fbx", rig)
    dist = check_fbx(out / "B36.fbx", rig)

    (out / "b36_rig.json").write_text(json.dumps(rig, indent=1))
    (out / "b36_materials.json").write_text(json.dumps({"materials": mats}, indent=1))
    PLUGIN_RIG.write_text(json.dumps(rig, indent=1))
    (out / "SOURCE.txt").write_text(
        f"Converted from {SOURCE_REPO} at commit {SOURCE_COMMIT} ({MODEL}).\n"
        "The source repository has no licence file. Ask its author before you publish anything built from it.\n")
    print(f"wrote {out}: {len(rig['parts'])} moving parts, {len(rig['turrets'])} turrets, "
          f"{len(rig['bays'])} bays, {len(mats)} materials, nose to tail turret {dist:.2f} m")


if __name__ == "__main__":
    sys.exit(main())
