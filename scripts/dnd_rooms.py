#!/usr/bin/env python3
"""Build the D&D rooms (room.glb, map.jpg, view.jpg) for the locations in table/dnd_assets.json, with
Blender on this Mac (free). Output: table/.cache/dnd/locations/<id>/ (gitignored; uploaded to R2 separately).

    python3 scripts/dnd_rooms.py              # every location
    python3 scripts/dnd_rooms.py tavern cave  # just these

The manifest gets the files' published paths (dnd/locations/<id>/…) and the room's triangle count.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "table" / "dnd_assets.json"
OUT = ROOT / "table" / ".cache" / "dnd" / "locations"
BLENDER = shutil.which("blender") or "/Applications/Blender.app/Contents/MacOS/Blender"
sys.path.insert(0, str(ROOT / "scripts"))
from dnd_kits import credits as kit_credits, ensure as ensure_kits  # noqa: E402

KITS: dict[str, str] = {}


GLTF_TRANSFORM = "@gltf-transform/cli@4.5.1"     # pinned: weld + meshopt (EXT_meshopt_compression), no simplification


def compress(glb: Path) -> None:
    """Merge duplicate vertices, then quantize + meshopt-compress the geometry: the crypt went 6.5 MB → 1.6 MB.
    dnd-scene.js registers the MeshoptDecoder (table/vendor/jsm/libs). Deploy that page code BEFORE uploading
    compressed rooms, or pages still on the old code can't open them."""
    for cmd in (["weld", str(glb), str(glb)], ["meshopt", str(glb), str(glb)]):
        r = subprocess.run(["npx", "--yes", GLTF_TRANSFORM, *cmd], capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise SystemExit(f"{glb}: gltf-transform {cmd[0]} failed (exit {r.returncode})\n{(r.stdout + r.stderr)[-600:]}")


def build(loc: dict) -> dict:
    out = OUT / loc["id"]
    with tempfile.TemporaryDirectory() as td:
        spec = Path(td) / "spec.json"
        spec.write_text(json.dumps({**{k: loc[k] for k in ("id", "theme", "layout")}, "kits": KITS}))
        t0 = time.time()
        r = subprocess.run([BLENDER, "-b", "--factory-startup", "--python", str(ROOT / "scripts" / "dnd_room_blender.py"),
                            "--", str(spec), str(out)], capture_output=True, text=True, timeout=600)
    done = [ln for ln in r.stdout.splitlines() if ln.startswith("ROOM-BUILT")]
    if r.returncode or not done:
        tail = "\n".join((r.stdout + r.stderr).splitlines()[-25:])
        raise SystemExit(f"{loc['id']}: Blender failed (exit {r.returncode})\n{tail}")
    compress(out / "room.glb")
    info = json.loads((out / "build.json").read_text())
    print(f"{done[0]}  ({time.time() - t0:.1f}s, room.glb {(out / 'room.glb').stat().st_size // 1024} KB)")
    return info


def main() -> None:
    KITS.update(ensure_kits())                       # CC0 model kits, pinned and cached (scripts/dnd_kits.py)
    man = json.loads(MANIFEST.read_text())
    want = set(sys.argv[1:])
    for loc in man["locations"]:
        if want and loc["id"] not in want:
            continue
        info = build(loc)
        base = f"dnd/locations/{loc['id']}/"
        loc.update(room=base + "room.glb", map_image=base + "map.jpg", view=base + "view.jpg", triangles=info["triangles"])
    man["room_credits"] = "3D rooms built from " + kit_credits()
    MANIFEST.write_text(json.dumps(man, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
