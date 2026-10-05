"""Build one D&D location's 3D room in Blender from its ASCII layout (table/dnd_assets.json).

Run by scripts/dnd_rooms.py, not by hand:
    blender -b --factory-startup --python scripts/dnd_room_blender.py -- <layout.json> <out_dir>

Writes to out_dir:
  room.glb   the room: 1 unit = one 5-ft square, the grid's (0, 0) corner at the origin, columns along +X and
             rows along +Z in glTF (Blender's -Y), floor at height 0. One mesh per material, so a room is a
             handful of draw calls; every prop is low-poly (Quest 3S budget: ≤ 150k triangles a room).
  map.jpg    a top-down orthographic render, 64 px per square, aligned to the grid (the 2D map's backdrop).
  view.jpg   a 1280×720 perspective view (placeholder key art; the start frame for a Cosmos establishing shot).

Content-neutral: primitives and flat colours only, no external assets. The idea (build the scene in
Blender, render from known cameras) follows Zombay's Blender pipeline; none of its code or assets are used.
"""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

argv = sys.argv[sys.argv.index("--") + 1:]
spec = json.loads(Path(argv[0]).read_text())
OUT = Path(argv[1])
OUT.mkdir(parents=True, exist_ok=True)
rows, theme = spec["layout"], spec["theme"]
H, W = len(rows), max(len(r) for r in rows)
rng = random.Random(spec["id"])                  # the same layout always builds the same room

# ── palettes per theme: (floor, alt floor, wall) and what each blocking character becomes ──────────
INDOOR = {"tavern", "crypt", "keep", "cave"}
FLOOR = {"tavern": ("#6b4a2e", "#5c3f27"), "crypt": ("#4a4c4f", "#424446"), "keep": ("#5a5651", "#514d48"),
         "cave": ("#3d3833", "#36312c"), "road": ("#4f6b34", "#47612f"), "forest": ("#3f5f2c", "#385527"),
         "bridge": ("#4c6a33", "#44602e"), "ruins": ("#5b6640", "#535d3a")}
WALL = {"tavern": "#3e2c1d", "crypt": "#34363b", "keep": "#45413c", "cave": "#4b4540", "ruins": "#7d786c",
        "road": "#6e6a62", "forest": "#6e6a62", "bridge": "#6e6a62"}
DIFFICULT = {"cave": "water", "bridge": "water", "crypt": "water", "ruins": "rubble", "tavern": "rubble",
             "keep": "rubble", "road": "mud", "forest": "brush"}

mats: dict[str, bpy.types.Material] = {}
meshes: dict[str, bmesh.types.BMesh] = {}


def hexcol(h: str) -> tuple:
    """'#6b4a2e' → linear RGBA (Blender's colour inputs are linear; hex colours are sRGB)."""
    h = h.lstrip("#")
    srgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb) + (1.0,)


def mat(name: str, color: str, rough: float = 0.85, metal: float = 0.0, alpha: float = 1.0) -> str:
    if name not in mats:
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        b = m.node_tree.nodes["Principled BSDF"]
        b.inputs["Base Color"].default_value = hexcol(color)
        m.diffuse_color = hexcol(color)          # what the flat map render (Workbench) shows
        b.inputs["Roughness"].default_value = rough
        b.inputs["Metallic"].default_value = metal
        if alpha < 1:
            b.inputs["Alpha"].default_value = alpha
            if hasattr(m, "surface_render_method"):
                m.surface_render_method = "BLENDED"
        mats[name] = m
        meshes[name] = bmesh.new()
    return name


def cell(x: int, y: int, z: float = 0.0) -> Vector:
    return Vector((x + 0.5, -(y + 0.5), z))


def box(m: str, c: Vector, sx: float, sy: float, sz: float, rot: float = 0.0) -> None:
    mtx = Matrix.Translation(c + Vector((0, 0, sz / 2))) @ Matrix.Rotation(rot, 4, "Z") @ Matrix.Diagonal((sx, sy, sz, 1))
    bmesh.ops.create_cube(meshes[m], size=1.0, matrix=mtx)


def cyl(m: str, c: Vector, r1: float, r2: float, h: float, seg: int = 10) -> None:
    mtx = Matrix.Translation(c + Vector((0, 0, h / 2)))
    bmesh.ops.create_cone(meshes[m], cap_ends=True, cap_tris=False, segments=seg, radius1=r1, radius2=r2, depth=h, matrix=mtx)


def blob(m: str, c: Vector, r: float, squash: float = 0.7) -> None:
    mtx = Matrix.Translation(c + Vector((0, 0, r * squash * 0.8))) @ Matrix.Diagonal((r, r * rng.uniform(.8, 1.1), r * squash, 1))
    bmesh.ops.create_icosphere(meshes[m], subdivisions=1, radius=1.0, matrix=mtx)


# ── the floor: one quad per walkable cell, in two tones so the grid reads without lines ────────────
f0, f1 = FLOOR[theme]
road = mat("road", "#7a6548")
deck = mat("deck", "#6e5134")
for y, row in enumerate(rows):
    for x in range(W):
        ch = row[x] if x < len(row) else "#"
        if ch == "W":
            continue
        m = road if ch == "," else deck if ch == "=" else mat("floor_a" if (x + y) % 2 else "floor_b", f0 if (x + y) % 2 else f1)
        z = 0.12 if ch == "=" else 0.0
        bm = meshes[m]
        vs = [bm.verts.new((x + dx, -(y + dy), z)) for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1))]
        bm.faces.new(vs[::-1])

# ── what each character becomes ─────────────────────────────────────────────────────────────────
wallh = 1.6 if theme in INDOOR else 1.2
for y, row in enumerate(rows):
    for x in range(W):
        ch = row[x] if x < len(row) else "#"
        c = cell(x, y)
        if ch == "#":
            if theme == "cave":
                blob(mat("cave_rock", WALL["cave"], 0.95), c, 0.75, 1.3)
            elif theme == "ruins":
                box(mat("wall", WALL[theme], 0.9), c, 1.0, 1.0, wallh * rng.uniform(.35, 1.0))
            else:
                box(mat("wall", WALL[theme], 0.9), c, 1.0, 1.0, wallh)
        elif ch == "T":
            cyl(mat("bark", "#4a3423"), c, 0.12, 0.09, 0.9, 6)
            cyl(mat("leaves", rng.choice(["#2f5a2a", "#356530", "#2a4f25"])), c + Vector((0, 0, 0.7)), 0.55, 0.0, 1.6, 8)
        elif ch == "R":
            blob(mat("rock", "#6d6a64", 0.95), c, rng.uniform(.32, .45))
        elif ch == "O":
            hgt = wallh * (rng.uniform(.3, .7) if theme == "ruins" else 1.0)
            cyl(mat("stone", "#8d877d", 0.8), c, 0.3, 0.27, hgt, 10)
        elif ch == "K":
            wood = mat("wood", "#7b5634", 0.7)
            box(wood, c + Vector((0, 0, 0.42)), 0.9, 0.9, 0.08)
            for dx, dy in ((-.35, -.35), (.35, -.35), (-.35, .35), (.35, .35)):
                box(wood, c + Vector((dx, dy, 0)), 0.07, 0.07, 0.42)
        elif ch == "B":
            cyl(mat("barrel", "#6a4426", 0.75), c, 0.3, 0.3, 0.55, 10)
            cyl(mat("hoop", "#3b3b3b", 0.5, 0.6), c + Vector((0, 0, 0.12)), 0.31, 0.31, 0.04, 10)
        elif ch == "S":
            stone = mat("tomb", "#7c7a76", 0.8)
            box(stone, c, 0.8, 0.95, 0.45)
            box(stone, c + Vector((0, 0, 0.45)), 0.85, 1.0, 0.08)
        elif ch == "C":
            wood = mat("wood", "#7b5634", 0.7)
            box(wood, c + Vector((0, 0, 0.3)), 0.95, 0.95, 0.35)
            for dx in (-.42, .42):
                cyl(mat("hoop", "#3b3b3b", 0.5, 0.6), c + Vector((dx, 0, 0)), 0.22, 0.22, 0.06, 10)
        elif ch == "W":
            water = mat("deep_water", "#1d4f6b", 0.15, 0.0, 0.85)
            bm = meshes[water]
            vs = [bm.verts.new((x + dx, -(y + dy), -0.15)) for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1))]
            bm.faces.new(vs[::-1])
        elif ch == "~":
            kind = DIFFICULT[theme]
            if kind == "water":
                water = mat("shallows", "#3f7d8c", 0.2, 0.0, 0.7)
                bm = meshes[water]
                vs = [bm.verts.new((x + dx, -(y + dy), 0.03)) for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1))]
                bm.faces.new(vs[::-1])
            elif kind == "mud":
                box(mat("mud", "#4a3a28", 1.0), c, 0.95, 0.95, 0.02)
            else:                                  # rubble or brush: a few low lumps
                m = mat("brush", "#3d5a26") if kind == "brush" else mat("rubble", "#77736a", 0.95)
                for _ in range(3):
                    blob(m, c + Vector((rng.uniform(-.3, .3), rng.uniform(-.3, .3), 0)), rng.uniform(.1, .18), .6)
        elif ch == "D":
            wood = mat("door", "#5a3b22", 0.7)
            horiz = (x > 0 and rows[y][x - 1] not in ".,=~DPM") or (x < W - 1 and rows[y][x + 1] not in ".,=~DPM")
            box(wood, c, 0.9 if horiz else 0.12, 0.12 if horiz else 0.9, 1.4 if theme in INDOOR else 1.1)

# ── one object per material ─────────────────────────────────────────────────────────────────────
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
room = bpy.data.collections.new("room")
bpy.context.scene.collection.children.link(room)
for name, bm in meshes.items():
    if not bm.faces:
        continue
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mats[name])
    ob = bpy.data.objects.new(name, me)
    room.objects.link(ob)

bpy.ops.object.select_all(action="SELECT")
bpy.ops.export_scene.gltf(filepath=str(OUT / "room.glb"), export_format="GLB", use_selection=True, export_yup=True,
                          export_apply=True, export_cameras=False, export_lights=False)
tris = sum(len(p.vertices) - 2 for o in room.objects for p in o.data.polygons)

# ── renders: top-down map (aligned to the grid) and a perspective view ──────────────────────────
sc = bpy.context.scene
sc.view_settings.view_transform = "Standard"      # theme colours as chosen (AgX washes them out)
for engine in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
    try:
        sc.render.engine = engine
        break
    except TypeError:
        continue
world = bpy.data.worlds.new("w")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.55, 0.62, 0.7, 1) if theme not in INDOOR else (0.08, 0.08, 0.1, 1)
world.node_tree.nodes["Background"].inputs[1].default_value = 0.6 if theme not in INDOOR else 0.35
sc.world = world
sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
sun.data.energy = 3.5 if theme not in INDOOR else 2.2
sun.rotation_euler = (math.radians(35), math.radians(-25), math.radians(30))
sc.collection.objects.link(sun)
if theme in INDOOR:                                # a few warm lights so rooms aren't flat
    for i in range(max(1, (W * H) // 60)):
        lamp = bpy.data.objects.new(f"lamp{i}", bpy.data.lights.new(f"lamp{i}", "POINT"))
        lamp.data.energy, lamp.data.color = 120, (1.0, 0.78, 0.5)
        lamp.location = (rng.uniform(1, W - 1), -rng.uniform(1, H - 1), 2.2)
        sc.collection.objects.link(lamp)

cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
sc.collection.objects.link(cam)
sc.camera = cam
sc.render.image_settings.file_format = "JPEG"
sc.render.image_settings.quality = 88

# the map: light from straight above, no shadows, so every square reads the same
sun_rot, sun_shadow = tuple(sun.rotation_euler), sun.data.use_shadow
sun.rotation_euler, sun.data.use_shadow = (0, 0, 0), False
lamps = [o for o in sc.collection.objects if o.name.startswith("lamp")]
for o in lamps:
    o.hide_render = True
view_engine = sc.render.engine
sc.render.engine = "BLENDER_WORKBENCH"            # flat: the map shows each material's colour exactly
sc.display.shading.light = "FLAT"
sc.display.shading.color_type = "MATERIAL"
cam.data.type = "ORTHO"
cam.data.ortho_scale = max(W, H)
cam.data.sensor_fit = "AUTO"
cam.location = (W / 2, -H / 2, 20)
cam.rotation_euler = (0, 0, 0)
sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = W * 64, H * 64, 100
sc.render.filepath = str(OUT / "map.jpg")
bpy.ops.render.render(write_still=True)

sun.rotation_euler, sun.data.use_shadow = sun_rot, sun_shadow
for o in lamps:
    o.hide_render = False
sc.render.engine = view_engine
cam.data.type = "PERSP"
cam.data.lens = 28
cam.location = (W / 2, -H - max(W, H) * 0.35, max(W, H) * 0.55)
target = Vector((W / 2, -H / 2, 0))
cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
sc.render.resolution_x, sc.render.resolution_y = 1280, 720
sc.render.filepath = str(OUT / "view.jpg")
bpy.ops.render.render(write_still=True)

(OUT / "build.json").write_text(json.dumps({"id": spec["id"], "w": W, "h": H, "triangles": tris,
                                            "materials": len([b for b in meshes if b in mats])}))
print(f"ROOM-BUILT {spec['id']} {W}x{H} tris={tris}")
