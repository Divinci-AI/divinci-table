#!/usr/bin/env python3
"""Add a D&D mini from any 3D model: decimated to the headset budget, scaled to its creature size, and recorded
in table/dnd_assets.json with its licence. Output: table/.cache/dnd/minis/<id>.glb (R2: dnd/minis/<id>.glb).

    python3 scripts/dnd_minis.py add <model.glb|.gltf|.obj|.fbx> --id goblin --size small \\
        --licence "CC-BY-4.0" --credit "\\"Goblin\\" by …, CC-BY-4.0" [--srd Goblin] [--source <url or note>]

A mini without a licence is refused (docs/DND-3D-GOAL.md rule 6). SFW only (rule 7).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "table" / "dnd_assets.json"
OUT = ROOT / "table" / ".cache" / "dnd" / "minis"
BLENDER = shutil.which("blender") or "/Applications/Blender.app/Contents/MacOS/Blender"
MAX_TRIS, MAX_TEX = 20_000, 1024
SIZES = ("tiny", "small", "medium", "large", "huge", "gargantuan")


def build(src: Path, mini_id: str, size: str, max_tris: int = MAX_TRIS) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{mini_id}.glb"
    r = subprocess.run([BLENDER, "-b", "--factory-startup", "--python", str(ROOT / "scripts" / "dnd_mini_blender.py"), "--",
                        str(src), str(out), size, str(max_tris), str(MAX_TEX)], capture_output=True, text=True, timeout=900)
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("MINI-BUILT")), None)
    if r.returncode or not line or not out.exists():
        raise SystemExit(f"Blender failed (exit {r.returncode}):\n" + "\n".join((r.stdout + r.stderr).splitlines()[-25:]))
    info = dict(kv.split("=") for kv in line.split()[1:])
    print(f"{line}  → {out.relative_to(ROOT)} ({out.stat().st_size // 1024} KB)")
    return {"tris": int(info["tris"]), "kb": out.stat().st_size // 1024}


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("model", type=Path)
    a.add_argument("--id", required=True)
    a.add_argument("--size", required=True, choices=SIZES)
    a.add_argument("--licence", required=True, help='e.g. "CC-BY-4.0" or "Meshy output (Divinci account)"')
    a.add_argument("--credit", required=True, help="the credit line to show (may be empty for our own work)")
    a.add_argument("--srd", help="the SRD monster this mini is for (links it in the bestiary)")
    a.add_argument("--source", default="")
    a.add_argument("--name")
    args = ap.parse_args()
    if not re.fullmatch(r"[a-z0-9-]{1,40}", args.id):
        raise SystemExit("--id: lowercase letters, digits and dashes")
    if not args.licence.strip():
        raise SystemExit("--licence is required")
    info = build(args.model, args.id, args.size)
    man = json.loads(MANIFEST.read_text())
    man["minis"] = [m for m in man.get("minis", []) if m["id"] != args.id] + [{
        "id": args.id, "name": args.name or args.id.replace("-", " ").title(), "size": args.size,
        "glb": f"dnd/minis/{args.id}.glb", "tris": info["tris"], "kb": info["kb"],
        "licence": args.licence.strip(), "credit": args.credit, "source": args.source}]
    if args.srd:
        for b in man.get("bestiary", []):
            if b["srd_name"].lower() == args.srd.lower():
                b["mini"] = args.id
    MANIFEST.write_text(json.dumps(man, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    sys.exit(main())
