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


def build(loc: dict) -> dict:
    out = OUT / loc["id"]
    with tempfile.TemporaryDirectory() as td:
        spec = Path(td) / "spec.json"
        spec.write_text(json.dumps({k: loc[k] for k in ("id", "theme", "layout")}))
        t0 = time.time()
        r = subprocess.run([BLENDER, "-b", "--factory-startup", "--python", str(ROOT / "scripts" / "dnd_room_blender.py"),
                            "--", str(spec), str(out)], capture_output=True, text=True, timeout=600)
    done = [ln for ln in r.stdout.splitlines() if ln.startswith("ROOM-BUILT")]
    if r.returncode or not done:
        tail = "\n".join((r.stdout + r.stderr).splitlines()[-25:])
        raise SystemExit(f"{loc['id']}: Blender failed (exit {r.returncode})\n{tail}")
    info = json.loads((out / "build.json").read_text())
    print(f"{done[0]}  ({time.time() - t0:.1f}s, room.glb {(out / 'room.glb').stat().st_size // 1024} KB)")
    return info


def main() -> None:
    man = json.loads(MANIFEST.read_text())
    want = set(sys.argv[1:])
    for loc in man["locations"]:
        if want and loc["id"] not in want:
            continue
        info = build(loc)
        base = f"dnd/locations/{loc['id']}/"
        loc.update(room=base + "room.glb", map_image=base + "map.jpg", view=base + "view.jpg", triangles=info["triangles"])
    MANIFEST.write_text(json.dumps(man, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
