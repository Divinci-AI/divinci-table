"""Build one D&D location's 3D room in Blender from its ASCII layout (table/dnd_assets.json).

Run by scripts/dnd_rooms.py, not by hand:
    blender -b --factory-startup --python scripts/dnd_room_blender.py -- <layout.json> <out_dir>

Writes to out_dir:
  room.glb   the room: 1 unit = one 5-ft square, the grid's (0, 0) corner at the origin, columns along +X and
             rows along +Z in glTF (Blender's -Y), floor at height 0. One mesh per material, so a room is a
             handful of draw calls; every prop is low-poly (Quest 3S budget: ≤ 150k triangles a room).
  map.jpg    a top-down orthographic render, 64 px per square, aligned to the grid (the 2D map's backdrop).
  view.jpg   a 1280×720 perspective view (placeholder key art; the start frame for a Cosmos establishing shot).

Pieces come from free CC0 kits (KayKit Dungeon, Kenney Nature / Graveyard / Fantasy Town; scripts/dnd_kits.py
fetches and pins them): each layout character becomes a kit model fitted to its square, and everything is merged
into one mesh (one draw call per material). Without the kits (spec has no "kits") it falls back to plain
primitives. The idea (build the scene in Blender, render from known cameras) follows Zombay's Blender pipeline;
none of its code or assets are used.
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


# ── kit pieces: what each layout character becomes, per theme (kit, model) ──────────────────────────────
KITS = spec.get("kits") or {}
OUTDOOR = {"road", "forest", "bridge", "ruins"}
TREES = [("nature", n) for n in ("tree_pineTallA_detailed", "tree_pineDefaultA", "tree_oak", "tree_detailed", "tree_pineRoundA")]
PIECES = {
    "wall": {"tavern": [("kaykit", "wall")], "crypt": [("kaykit", "wall"), ("kaykit", "wall"), ("kaykit", "wall_cracked")],
             "keep": [("kaykit", "wall"), ("kaykit", "wall"), ("kaykit", "wall_cracked")],
             "ruins": [("kaykit", "wall_broken"), ("kaykit", "wall_half"), ("kaykit", "wall_cracked")],
             "cave": [("kaykit", "rubble_large")], "*": [("nature", "rock_tallB")]},
    "door": {"*": [("kaykit", "wall_doorway")]},
    "T": {"*": TREES},
    "R": {"*": [("nature", "rock_largeA"), ("nature", "rock_largeC"), ("nature", "rock_largeB")],
          "cave": [("kaykit", "rubble_half")]},
    "O": {"ruins": [("kaykit", "column")], "*": [("kaykit", "pillar")]},
    "K": {"tavern": [("kaykit", "table_medium_decorated_A"), ("kaykit", "table_small_decorated_A"),
                     ("kaykit", "table_medium_tablecloth_decorated_B")], "*": [("kaykit", "table_medium")]},
    "B": {"*": [("kaykit", "barrel_large"), ("kaykit", "keg_decorated"), ("kaykit", "barrel_small_stack")]},
    "S": {"*": [("graveyard", "coffin"), ("graveyard", "coffin-old")]},
    "C": {"*": [("town", "cart")]},
    "rubble": {"*": [("kaykit", "rubble_half")]},
    "brush": {"*": [("nature", "plant_bushDetailed"), ("nature", "grass_large"), ("nature", "plant_bush")]},
    "bridge": {"*": [("nature", "bridge_stone")]},
    "floor": {"tavern": [("kaykit", "floor_wood_small")], "crypt": [("kaykit", "floor_tile_small")] * 5
              + [("kaykit", "floor_tile_small_broken_A"), ("kaykit", "floor_tile_small_weeds_A")],
              "keep": [("kaykit", "floor_tile_small")] * 6 + [("kaykit", "floor_tile_small_decorated")],
              "cave": [("kaykit", "floor_dirt_small_A"), ("kaykit", "floor_dirt_small_B"), ("kaykit", "floor_dirt_small_C")]},
    "torch": {"*": [("kaykit", "torch_mounted")]},
    "decor": {"*": [("nature", "grass"), ("nature", "flower_yellowA"), ("nature", "mushroom_redGroup"), ("nature", "grass_leafs")]},
}
# Kenney's Nature Kit is mint and teal by design; a woodland palette suits the table (CC0 allows any change)
RECOLOR = {"leafsGreen": "#4f8a3c", "leafsDark": "#2f5e2e", "grass": "#6a9a45", "woodBark": "#6b4a2e",
           "woodBarkDark": "#4e3726", "dirt": "#7a5a3a", "stone": "#8d8a83", "stoneDark": "#6f6c66"}
lib = bpy.data.collections.new("kit_lib")
bpy.context.scene.collection.children.link(lib)
lib.hide_render = True
templates: dict[str, tuple] = {}
placed: list[bpy.types.Object] = []


def piece(kind: str) -> tuple | None:
    """A (kit, model) for this kind in this theme, or None when there are no kits or no such piece."""
    opts = PIECES.get(kind, {})
    opts = opts.get(theme) or opts.get("*")
    return rng.choice(opts) if KITS and opts else None


def template(kit: str, name: str):
    """Import a kit model once: one object, transforms applied. Returns (object, min corner, max corner)."""
    key = f"{kit}/{name}"
    if key in templates:
        return templates[key]
    folder = Path(KITS[kit])
    f = next((folder / n for n in (f"{name}.glb", f"{name}.gltf.glb") if (folder / n).exists()), None)
    if f is None:
        raise SystemExit(f"kit model missing: {key}")
    before = {o.name for o in bpy.data.objects}
    bpy.ops.import_scene.gltf(filepath=str(f))
    new = [o for o in bpy.data.objects if o.name not in before]
    new_names = [o.name for o in new]
    meshes_ = [o for o in new if o.type == "MESH"]
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes_:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes_[0]
    bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    if len(meshes_) > 1:
        bpy.ops.object.join()
    t = bpy.context.view_layer.objects.active
    for n in new_names:                           # empties left over from the import (joined meshes are gone)
        o = bpy.data.objects.get(n)
        if o is not None and o != t:
            bpy.data.objects.remove(o, do_unlink=True)
    for slot in t.material_slots:
        m = slot.material
        new_col = RECOLOR.get(m.name.split(".")[0]) if m and kit == "nature" else None
        if new_col and m.use_nodes and "Principled BSDF" in m.node_tree.nodes:
            m.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = hexcol(new_col)
            m.diffuse_color = hexcol(new_col)
    for c in list(t.users_collection):
        c.objects.unlink(t)
    lib.objects.link(t)
    vs = [v.co for v in t.data.vertices]
    lo = Vector((min(v.x for v in vs), min(v.y for v in vs), min(v.z for v in vs)))
    hi = Vector((max(v.x for v in vs), max(v.y for v in vs), max(v.z for v in vs)))
    templates[key] = (t, lo, hi)
    return templates[key]


def place(p: tuple, x: float, y: float, w: float = 1.0, h: float = 1.0, *, mode: str = "fit", size: float = 0.8,
          height: float | None = None, rot: float = 0.0, z: float = 0.0) -> None:
    """Put a kit model on the grid region whose top-left square is (x, y), w × h squares.
    mode "fit": uniform scale so its footprint is `size` of the region (keeps proportions; props, trees).
    mode "cell": stretch the footprint to the whole region, height to `height` (walls, floors, the bridge)."""
    t, lo, hi = template(*p)
    d = hi - lo
    q = round(rot / (math.pi / 2)) % 2 == 1                 # turned a quarter: model X runs along world Y
    dx, dy = (d.y, d.x) if q else (d.x, d.y)
    if mode == "cell":
        sxw, syw = w / max(dx, 1e-6), h / max(dy, 1e-6)
        sz = (height / max(d.z, 1e-6)) if height else min(sxw, syw)
        sx, sy = (syw, sxw) if q else (sxw, syw)
    else:
        k = size * min(w / max(dx, 1e-6), h / max(dy, 1e-6))
        if height:
            k = min(k, height / max(d.z, 1e-6))
        sx = sy = sz = k
    pivot = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
    c = Vector((x + w / 2, -(y + h / 2), z))
    o = t.copy()
    o.matrix_world = Matrix.Translation(c) @ Matrix.Rotation(rot, 4, "Z") @ Matrix.Diagonal((sx, sy, sz, 1)) @ Matrix.Translation(-pivot)
    bpy.context.scene.collection.objects.link(o)
    placed.append(o)


def blocks(ch: str) -> list[tuple[int, int, int, int]]:
    """Rectangles of one character (top-left x, y, w, h), so a 2×2 cart or a 5×4 bridge is one piece."""
    seen, out = set(), []
    for y0, row in enumerate(rows):
        for x0, c in enumerate(row):
            if c != ch or (x0, y0) in seen:
                continue
            w = 1
            while x0 + w < len(row) and row[x0 + w] == ch and (x0 + w, y0) not in seen:
                w += 1
            h = 1
            while y0 + h < H and all(x0 + i < len(rows[y0 + h]) and rows[y0 + h][x0 + i] == ch for i in range(w)):
                h += 1
            for yy in range(y0, y0 + h):
                for xx in range(x0, x0 + w):
                    seen.add((xx, yy))
            out.append((x0, y0, w, h))
    return out


# ── the floor: one quad per walkable cell, in two tones so the grid reads without lines ────────────
f0, f1 = FLOOR[theme]
road = mat("road", "#7a6548")
deck = mat("deck", "#6e5134")
for y, row in enumerate(rows):
    for x in range(W):
        ch = row[x] if x < len(row) else "#"
        if ch == "W":
            continue
        tile = piece("floor")
        if tile and ch not in ",=":
            place(tile, x, y, mode="cell", height=0.06, z=-0.06)
            continue
        if ch == "=" and piece("bridge"):
            continue                               # the bridge model is the deck
        m = road if ch == "," else deck if ch == "=" else mat("floor_a" if (x + y) % 2 else "floor_b", f0 if (x + y) % 2 else f1)
        z = 0.12 if ch == "=" else 0.0
        bm = meshes[m]
        vs = [bm.verts.new((x + dx, -(y + dy), z)) for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1))]
        bm.faces.new(vs[::-1])

# ── what each character becomes ─────────────────────────────────────────────────────────────────
wallh = 1.6 if theme in INDOOR else 1.2


def walkable(x: int, y: int) -> bool:
    return 0 <= y < H and 0 <= x < len(rows[y]) and rows[y][x] in ".,=~DPM"


def wall_turn(x: int, y: int) -> float:
    """Face a wall's long side along its run: a quarter turn when its neighbours are above and below."""
    side = lambda a, b: 0 <= b < H and 0 <= a < len(rows[b]) and rows[b][a] in "#D"
    return math.pi / 2 if (side(x, y - 1) or side(x, y + 1)) and not (side(x - 1, y) or side(x + 1, y)) else 0.0


if KITS:
    for (x0, y0, w, h) in blocks("C"):
        place(piece("C"), x0, y0, w, h, size=0.95, rot=0.0 if w >= h else math.pi / 2)
    if piece("bridge"):
        for (x0, y0, w, h) in blocks("="):
            wet = lambda a, b: 0 <= b < H and 0 <= a < len(rows[b]) and rows[b][a] in "~W"
            across_ns = wet(x0 - 1, y0) or wet(x0 + w, y0)      # water beside it: people cross north-south
            place(piece("bridge"), x0, y0, w, h, mode="cell", height=0.6, rot=math.pi / 2 if across_ns else 0.0, z=-0.35)
    if theme in OUTDOOR:                           # a little life on open ground
        for y, row in enumerate(rows):
            for x, ch in enumerate(row):
                if ch == "." and rng.random() < 0.12:
                    place(piece("decor"), x + rng.uniform(-.2, .2), y + rng.uniform(-.2, .2), size=rng.uniform(.3, .5))
    if theme in {"tavern", "crypt", "keep"}:       # torches on some walls that face the room
        for y, row in enumerate(rows):
            for x, ch in enumerate(row):
                if ch != "#" or rng.random() > 0.18:
                    continue
                for dx, dy, r in ((0, 1, 0.0), (0, -1, math.pi), (1, 0, math.pi / 2), (-1, 0, -math.pi / 2)):
                    if walkable(x + dx, y + dy):
                        place(piece("torch"), x + dx * 0.55, y + dy * 0.55, size=0.35, height=0.6, rot=r, z=0.9)
                        break

for y, row in enumerate(rows):
    for x in range(W):
        ch = row[x] if x < len(row) else "#"
        c = cell(x, y)
        kind = {"#": "wall", "D": "door"}.get(ch, ch)
        p = piece(kind) if ch in "#DTROKBS" else None
        if p and ch in "#D":
            place(p, x, y, mode="cell", height=wallh * (rng.uniform(.45, 1.0) if theme == "ruins" else 1.0)
                  if theme != "cave" else wallh * rng.uniform(.9, 1.3), rot=wall_turn(x, y))
            continue
        if p and ch == "T":
            place(p, x, y, size=rng.uniform(.85, 1.1), height=rng.uniform(1.8, 2.6), rot=rng.uniform(0, 6.28))
            continue
        if p and ch == "R":
            place(p, x, y, size=rng.uniform(.6, .85), rot=rng.uniform(0, 6.28))
            continue
        if p and ch == "O":
            place(p, x, y, size=0.7, height=wallh * (rng.uniform(.4, .8) if theme == "ruins" else 1.0))
            continue
        if p and ch in "KBS":
            place(p, x, y, size={"K": 0.95, "B": 0.75, "S": 0.95}[ch], rot=0.0 if ch != "B" else rng.uniform(0, 6.28))
            continue
        if ch == "C" and KITS:
            continue
        if ch == "~" and KITS and DIFFICULT[theme] in ("rubble", "brush"):
            place(piece(DIFFICULT[theme]), x, y, size=0.6, rot=rng.uniform(0, 6.28))
            continue
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
keep = {o.name for o in placed} | {o.name for o in lib.objects}
for o in list(bpy.data.objects):
    if o.name not in keep:
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

# kit pieces: real copies with transforms applied, then everything is one object (one draw call per material)
for o in placed:
    o.data = o.data.copy()
    room.objects.link(o)
    bpy.context.scene.collection.objects.unlink(o)
parts = list(room.objects)
if len(parts) > 1:
    bpy.ops.object.select_all(action="DESELECT")
    for o in parts:
        o.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.ops.object.join()
    whole = bpy.context.view_layer.objects.active
    # copies of one kit share a texture: merge their material slots so the room stays a few draw calls
    def mkey(m):
        img = next((n.image.name for n in (m.node_tree.nodes if m and m.use_nodes else []) if n.type == "TEX_IMAGE" and n.image), None)
        return ("img", img.split(".")[0]) if img else ("mat", m.name if m else "")
    first, remap = {}, {}
    for i, m in enumerate(whole.data.materials):
        remap[i] = first.setdefault(mkey(m), i)
    for poly in whole.data.polygons:
        poly.material_index = remap.get(poly.material_index, poly.material_index)
    bpy.ops.object.material_slot_remove_unused()
    whole.name = "room"
for c in bpy.data.collections:
    if c.name == "kit_lib":
        for o in list(c.objects):
            bpy.data.objects.remove(o, do_unlink=True)

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
# the same renderer as the view (Workbench can't show the kits' factor-only colours), lit evenly from above
sun_energy, world_strength = sun.data.energy, world.node_tree.nodes["Background"].inputs[1].default_value
sun.data.energy = 2.6
world.node_tree.nodes["Background"].inputs[1].default_value = 1.0
glossy = {}                                        # water mirrors an overhead sun as white: matte for the map only
for m in bpy.data.materials:
    bsdf = m.node_tree.nodes.get("Principled BSDF") if m.use_nodes else None
    if bsdf and m.name.split(".")[0] in ("deep_water", "shallows"):
        glossy[m.name] = (bsdf.inputs["Roughness"].default_value, bsdf.inputs["Specular IOR Level"].default_value)
        bsdf.inputs["Roughness"].default_value, bsdf.inputs["Specular IOR Level"].default_value = 1.0, 0.0
cam.data.type = "ORTHO"
cam.data.ortho_scale = max(W, H)
cam.data.sensor_fit = "AUTO"
cam.location = (W / 2, -H / 2, 20)
cam.rotation_euler = (0, 0, 0)
sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = W * 64, H * 64, 100
sc.render.filepath = str(OUT / "map.jpg")
bpy.ops.render.render(write_still=True)

sun.rotation_euler, sun.data.use_shadow = sun_rot, sun_shadow
sun.data.energy = sun_energy
for name, (rough, spec_level) in glossy.items():
    bsdf = bpy.data.materials[name].node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value, bsdf.inputs["Specular IOR Level"].default_value = rough, spec_level
world.node_tree.nodes["Background"].inputs[1].default_value = world_strength
for o in lamps:
    o.hide_render = False
cam.data.type = "PERSP"
cam.data.lens = 28
cam.location = (W / 2, -H - max(W, H) * 0.35, max(W, H) * 0.55)
target = Vector((W / 2, -H / 2, 0))
cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
sc.render.resolution_x, sc.render.resolution_y = 1280, 720
sc.render.filepath = str(OUT / "view.jpg")
bpy.ops.render.render(write_still=True)

(OUT / "build.json").write_text(json.dumps({"id": spec["id"], "w": W, "h": H, "triangles": tris,
                                            "materials": sum(len(o.data.materials) for o in room.objects),
                                            "kits": bool(KITS)}))
print(f"ROOM-BUILT {spec['id']} {W}x{H} tris={tris}")
