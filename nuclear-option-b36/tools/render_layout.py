"""Render the converted B-36 with its weapon stations marked, for the README.

    uv run python render_layout.py <Art folder from convert_b36.py> <out.png>

Red: bomb bay stations (two per bay, on the bay roof), seen through a ghosted fuselage. Yellow: turret stations at the
extended turret rings. Blue: wheel contact points.
"""

import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def unity_to_blender(p):
    # Blender's glTF import puts glTF (x, y, z) at Blender (x, -z, y); Unity is glTF (x, y, -z).
    # So Unity (x, y, z) is Blender (x, z, y). A rotation axis also picks up the glTF/Unity
    # mirror: Unity axis (x, y, z) is Blender axis (-x, -z, -y).
    return Vector((p[0], p[2], p[1]))


def unity_axis_to_blender(a):
    return Vector((-a[0], -a[2], -a[1]))


def marker(loc, radius, color):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, location=loc, segments=16, ring_count=8)
    obj = bpy.context.active_object
    mat = bpy.data.materials.new("m")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = color
    bsdf.inputs["Emission Color"].default_value = color
    bsdf.inputs["Emission Strength"].default_value = 4.0
    obj.data.materials.append(mat)
    obj.show_in_front = True


def main():
    art, out = Path(sys.argv[1]), Path(sys.argv[2])
    rig = json.loads((art / "b36_rig.json").read_text())
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=str(art / "B36.fbx"))
    # Doors open and turrets up, as in a bomb run.
    for p in rig["parts"]:
        if p["kind"] == "bombdoor":
            o = bpy.data.objects[p["name"]]
            ax = unity_axis_to_blender(p["axis"])
            o.rotation_mode = "AXIS_ANGLE"
            o.rotation_axis_angle = (math.radians(p["angle"]), ax.x, ax.y, ax.z)
    for t in rig["turrets"]:
        o = bpy.data.objects[t["name"]]
        o.location = o.location + unity_to_blender(t["up"]) * t["retract"]
    # See-through fuselage so the bay stations show.
    ghost = bpy.data.materials.new("ghost")
    ghost.use_nodes = True
    ghost.node_tree.nodes["Principled BSDF"].inputs["Alpha"].default_value = 0.18
    for name in ["Fuselage"] + [d for bay in rig["bays"] for d in bay["doors"]]:
        o = bpy.data.objects[name]
        for slot in o.material_slots:
            slot.material = ghost
    for b in rig["bays"]:
        z = (b["zFwd"] + b["zAft"]) / 2
        for x in (-0.45, 0.45):
            marker(unity_to_blender([x, b["hardpointY"], z]), 0.32, (1, 0.1, 0.05, 1))
    for t in rig["turrets"]:
        p = [t["pivot"][i] + t["up"][i] * t["retract"] for i in range(3)]
        marker(unity_to_blender(p), 0.42, (1, 0.85, 0.05, 1))
    for leg in rig["legs"]:
        marker(unity_to_blender(leg["contact"]), 0.35, (0.1, 0.4, 1, 1))

    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 24
    scene.cycles.device = "CPU"
    scene.render.resolution_x, scene.render.resolution_y = 1600, 900
    scene.render.film_transparent = False
    world = bpy.data.worlds.new("w")
    scene.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.55, 0.62, 0.72, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 1.2
    bpy.ops.object.light_add(type="SUN", rotation=(math.radians(40), math.radians(20), math.radians(30)))
    bpy.context.active_object.data.energy = 3.5
    # From below and to the left, looking up into the open bays.
    bpy.ops.object.camera_add(location=(-48, 40, -34))
    cam = bpy.context.active_object
    direction = Vector((0, 0, 0)) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    cam.data.lens = 38
    scene.camera = cam
    scene.render.filepath = str(out)
    bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    main()
