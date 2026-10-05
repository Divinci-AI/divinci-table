"""The D&D asset manifest (table/dnd_assets.json) is sound: every layout plays, every asset has a licence,
and nothing is over the headset's budget (docs/DND-3D-GOAL.md, rules 6 and 9).

    python3 table/tests/dnd_assets_test.py            # layouts, licences, and any built files found locally
    python3 table/tests/dnd_assets_test.py --built    # also require every location's built files to exist

Layouts: rectangular; only known characters; at least 4 player spawns and 1 monster spawn; every spawn
reachable from the first player spawn. Files (looked for in table/.cache/dnd/): a room ≤ 150k triangles,
a mini ≤ 20k; an approved location (or any, with --built) has its room, map image and view, plus
the Cosmos art and shot once the manifest names them.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import dnd_map as M  # noqa: E402

CACHE = HERE / ".cache" / "dnd"
KNOWN = set(".,=~DPM#TROKBSCW")
THEMES = {"tavern", "road", "forest", "cave", "crypt", "keep", "bridge", "ruins"}
ROOM_TRIS, MINI_TRIS = 150_000, 20_000
LOC_FILES = ("art", "shot", "room", "map_image", "view")
PASS = FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    ok = bool(ok)
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {detail}"))


def glb_triangles(path: Path) -> int:
    """Triangles drawn by a .glb: index count (or vertex count) / 3 over every triangle primitive, per node use."""
    data = path.read_bytes()
    if data[:4] != b"glTF":
        raise ValueError("not a .glb")
    n = struct.unpack_from("<I", data, 12)[0]
    g = json.loads(data[20:20 + n])
    acc = g.get("accessors", [])
    per_mesh = []
    for mesh in g.get("meshes", []):
        t = 0
        for prim in mesh.get("primitives", []):
            if prim.get("mode", 4) != 4:
                continue
            ix = prim.get("indices")
            t += (acc[ix]["count"] if ix is not None else acc[prim["attributes"]["POSITION"]]["count"]) // 3
        per_mesh.append(t)
    return sum(per_mesh[nd["mesh"]] for nd in g.get("nodes", []) if "mesh" in nd)


def reachable(layout: list[str]) -> tuple[set, set]:
    g = M.parse_layout(layout)
    spawns = set(g["spawn"]["pcs"]) | set(g["spawn"]["monsters"])
    start = g["spawn"]["pcs"][0]
    seen, todo = {start}, [start]
    while todo:
        x, y = todo.pop()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                n = (x + dx, y + dy)
                if n not in seen and 0 <= n[0] < g["w"] and 0 <= n[1] < g["h"] and n not in g["walls"]:
                    if dx and dy and ((x + dx, y) in g["walls"] or (x, y + dy) in g["walls"]):
                        continue
                    seen.add(n)
                    todo.append(n)
    return spawns, seen


def main() -> None:
    built = "--built" in sys.argv
    man = json.loads((HERE / "dnd_assets.json").read_text())
    ids = [x["id"] for x in man["locations"]]
    print("locations")
    check("location ids are unique", len(ids) == len(set(ids)), str(ids))
    for loc in man["locations"]:
        i, lay = loc["id"], loc["layout"]
        check(f"{i}: id is a safe name", i.replace("-", "").isalnum() and i.islower(), i)
        check(f"{i}: layout is rectangular", len({len(r) for r in lay}) == 1, str([len(r) for r in lay]))
        bad = {c for r in lay for c in r} - KNOWN
        check(f"{i}: only known characters", not bad, str(bad))
        g = M.parse_layout(lay)
        check(f"{i}: 4+ player spawns, 1+ monster spawn", len(g["spawn"]["pcs"]) >= 4 and g["spawn"]["monsters"],
              f"P={len(g['spawn']['pcs'])} M={len(g['spawn']['monsters'])}")
        spawns, seen = reachable(lay)
        check(f"{i}: every spawn is reachable from the first player spawn", spawns <= seen, str(sorted(spawns - seen)))
        check(f"{i}: theme is known", loc.get("theme") in THEMES, str(loc.get("theme")))
        check(f"{i}: licence recorded", bool(loc.get("licence")))
        check(f"{i}: status is draft or approved", loc.get("status") in ("draft", "approved"), str(loc.get("status")))
        d = CACHE / "locations" / i
        room = d / "room.glb"
        if room.exists():
            t = glb_triangles(room)
            check(f"{i}: room is {t:,} triangles (≤ {ROOM_TRIS:,})", t <= ROOM_TRIS)
        need = ("room", "map_image", "view") if loc.get("status") == "approved" or built else ()
        need += tuple(k for k in ("art", "shot") if loc.get(k))        # Cosmos files, once generated
        for k in need:
            f = d / {"art": "art.jpg", "shot": "shot.mp4", "room": "room.glb", "map_image": "map.jpg", "view": "view.jpg"}[k]
            check(f"{i}: {f.name} exists", f.exists(), str(f))
    print("minis")
    for mini in man.get("minis", []):
        i = mini.get("id", "?")
        check(f"{i}: licence and credit recorded", bool(mini.get("licence")) and "credit" in mini)
        check(f"{i}: size is a creature size", mini.get("size") in M.SIZES, str(mini.get("size")))
        f = CACHE / "minis" / f"{i}.glb"
        if f.exists():
            t = glb_triangles(f)
            check(f"{i}: {t:,} triangles (≤ {MINI_TRIS:,})", t <= MINI_TRIS)
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
