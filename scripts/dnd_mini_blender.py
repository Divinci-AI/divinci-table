"""Turn any 3D model into a D&D mini that fits the headset's budget. Run by scripts/dnd_minis.py:

    blender -b --factory-startup --python scripts/dnd_mini_blender.py -- <in> <out.glb> <size> <max_tris> <max_tex>

Imports .glb/.gltf/.obj/.fbx; decimates every mesh to fit max_tris in total (keeping UVs, weights, the rig and its
animations); scales the textures down to max_tex px; scales the model to its creature size (1 unit = one 5-ft
square: height by size, and it never spills off its size×size squares); stands it at the origin, facing +Z
in glTF; exports a .glb with JPEG textures. Prints MINI-BUILT with the triangle count.
"""
from __future__ import annotations

import sys
from pathlib import Path

import bpy
from mathutils import Vector

src, out, size, max_tris, max_tex = sys.argv[sys.argv.index("--") + 1:][:5]
max_tris, max_tex = int(max_tris), int(max_tex)
HEIGHT = {"tiny": 0.4, "small": 0.75, "medium": 1.15, "large": 1.9, "huge": 2.8, "gargantuan": 4.0}   # squares
SQUARES = {"tiny": 1, "small": 1, "medium": 1, "large": 2, "huge": 3, "gargantuan": 4}

bpy.ops.wm.read_factory_settings(use_empty=True)
ext = Path(src).suffix.lower()
if ext in (".glb", ".gltf"):
    bpy.ops.import_scene.gltf(filepath=src)
elif ext == ".obj":
    bpy.ops.wm.obj_import(filepath=src)
elif ext == ".fbx":
    bpy.ops.import_scene.fbx(filepath=src)
else:
    raise SystemExit(f"unsupported format {ext}")

meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
_arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
if _arms:                                             # a rigged model: only the skinned meshes are the creature. Meshy's
    for o in list(meshes):                            # rigged files carry a stray 2-unit "Icosphere" helper, which made
        if not (o.parent in _arms or any(m.type == "ARMATURE" for m in o.modifiers)):   # the mini half-size and floating
            print(f"dropping {o.name}: not part of the rig")
            bpy.data.objects.remove(o, do_unlink=True)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not meshes:
    raise SystemExit("no meshes in the file")


def tris_of(objs) -> int:
    dg = bpy.context.evaluated_depsgraph_get()
    n = 0
    for o in objs:
        me = o.evaluated_get(dg).to_mesh()
        n += sum(len(p.vertices) - 2 for p in me.polygons)
        o.evaluated_get(dg).to_mesh_clear()
    return n


before = tris_of(meshes)
if before > max_tris:
    ratio = max_tris / before * 0.97                  # a little under, since collapse lands near, not on, the target
    for o in meshes:
        bpy.context.view_layer.objects.active = o
        mod = o.modifiers.new("fit", "DECIMATE")
        mod.decimate_type, mod.ratio, mod.use_collapse_triangulate = "COLLAPSE", ratio, True
        bpy.ops.object.modifier_apply(modifier=mod.name)
after = tris_of(meshes)

for img in bpy.data.images:                           # ≤ max_tex on the long side
    if img.size[0] and max(img.size) > max_tex:
        k = max_tex / max(img.size)
        img.scale(max(1, int(img.size[0] * k)), max(1, int(img.size[1] * k)))

# scale and place: the whole model (every root object), by its world bounding box
bpy.context.view_layer.update()
# Measured on the mesh DATA (the rest pose), which is what a glTF viewer draws for a rigged mini at rest. Blender's
# evaluated (posed) copy of a Meshy auto-rig came out twice its real height and centred on the origin, and both the
# bound box and the evaluated mesh carried that: the mini was built half-size and floating (2026-11-15). For a plain
# mesh this equals the bound box.
pts = [o.matrix_world @ v.co for o in meshes for v in o.data.vertices]
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
dims = hi - lo
k = min(HEIGHT[size] / max(dims.z, 1e-6), 0.95 * SQUARES[size] / max(dims.x, dims.y, 1e-6))
roots = [o for o in bpy.context.scene.objects if o.parent is None]
arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
if not arms:                                          # a plain model: one scaled pivot over everything
    pivot = bpy.data.objects.new("mini", None)
    bpy.context.scene.collection.objects.link(pivot)
    for o in roots:
        o.parent = pivot
    pivot.scale = (k, k, k)
    pivot.location = (-(lo.x + hi.x) / 2 * k, -(lo.y + hi.y) / 2 * k, -lo.z * k)
else:
    # A rigged model (Meshy's auto-rig, 2026-11-15): its armature came in at centimetre scale (0.01). Under a scaled
    # pivot, three.js bound the skin against that node chain and drew the mini at half size, floating. So the scale is
    # APPLIED into the armature and the meshes (every node ends at scale 1), and the animation's location keys are
    # scaled by the armature's own factor, or the walk's hip motion would come out hundreds of times too big.
    arm_k = {a.name: a.matrix_world.to_scale().x * k for a in arms}
    shift = Vector(((lo.x + hi.x) / 2 * k, (lo.y + hi.y) / 2 * k, lo.z * k))
    for o in roots:
        o.location = o.location * k - shift
        o.scale = o.scale * k
    bpy.context.view_layer.update()
    # Every node ends as an identity: glTF says a skinned mesh's own node transform is ignored, three.js folds it into
    # the bind matrix, and a transform left on the armature node made the two disagree (the mini floated by exactly it).
    bpy.ops.object.select_all(action="SELECT")
    bpy.context.view_layer.objects.active = arms[0]
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    s = next(iter(arm_k.values()))
    def fcurves_of(act):                              # Blender 4.4+ keeps them in layered actions
        if hasattr(act, "fcurves"):
            return list(act.fcurves)
        return [fc for layer in act.layers for strip in layer.strips for bag in getattr(strip, "channelbags", [])
                for fc in bag.fcurves]
    for act in bpy.data.actions:
        for fc in fcurves_of(act):
            if fc.data_path.endswith(".location"):
                for kp in fc.keyframe_points:
                    kp.co[1] *= s
                    kp.handle_left[1] *= s
                    kp.handle_right[1] *= s
bpy.context.view_layer.update()

bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", export_image_format="JPEG", export_apply=True,
                          export_animations=True, export_cameras=False, export_lights=False, export_yup=True)
print(f"MINI-BUILT tris_before={before} tris={after} height={dims.z * k:.2f} scale={k:.4f}")
