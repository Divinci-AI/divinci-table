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
pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
dims = hi - lo
k = min(HEIGHT[size] / max(dims.z, 1e-6), 0.95 * SQUARES[size] / max(dims.x, dims.y, 1e-6))
roots = [o for o in bpy.context.scene.objects if o.parent is None]
pivot = bpy.data.objects.new("mini", None)
bpy.context.scene.collection.objects.link(pivot)
for o in roots:
    o.parent = pivot
pivot.scale = (k, k, k)
pivot.location = (-(lo.x + hi.x) / 2 * k, -(lo.y + hi.y) / 2 * k, -lo.z * k)
bpy.context.view_layer.update()

bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", export_image_format="JPEG", export_apply=True,
                          export_animations=True, export_cameras=False, export_lights=False, export_yup=True)
print(f"MINI-BUILT tris_before={before} tris={after} height={dims.z * k:.2f} scale={k:.4f}")
