"""Upload the APPROVED D&D locations' files to R2, where the cloud Worker serves them (/dnd-assets/…).

    python3 scripts/dnd_upload.py          # show what would go up (nothing is sent)
    python3 scripts/dnd_upload.py --yes    # upload

Drafts never leave the laptop: a location goes up only once a person approved it at /dnd/review
(docs/DND-3D-GOAL.md D2). The Worker serves whatever is in R2 under dnd/, so this is the gate.
Uses the cloud project's wrangler (its login, the divinci-table-assets bucket).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "table" / "dnd_assets.json"
CACHE = ROOT / "table" / ".cache"
BUCKET = "divinci-table-assets"
TYPES = {".glb": "model/gltf-binary", ".jpg": "image/jpeg", ".png": "image/png", ".mp4": "video/mp4"}
FILES = ("room", "map_image", "view", "art", "shot")


def plan() -> tuple[list[tuple[str, Path]], list[str]]:
    man = json.loads(MANIFEST.read_text())
    ups, skipped = [], []
    for loc in man["locations"]:
        if loc.get("status") != "approved":
            skipped.append(f"{loc['id']} ({loc.get('status', 'draft')})")
            continue
        for k in FILES:
            rel = loc.get(k)
            if not rel:
                continue
            if not rel.startswith(f"dnd/locations/{loc['id']}/"):
                raise SystemExit(f"{loc['id']}.{k}: unexpected path {rel!r}")
            f = CACHE / rel
            if not f.is_file():
                raise SystemExit(f"{loc['id']}.{k}: {f} is missing; build it with scripts/dnd_rooms.py")
            ups.append((rel, f))
    return ups, skipped


def main() -> None:
    ups, skipped = plan()
    total = sum(f.stat().st_size for _, f in ups)
    for key, f in ups:
        print(f"  {BUCKET}/{key}  ({f.stat().st_size // 1024} KB)")
    print(f"{len(ups)} files, {total / 1e6:.1f} MB" + (f"; not uploaded (not approved): {', '.join(skipped)}" if skipped else ""))
    if "--yes" not in sys.argv:
        print("dry run: nothing sent. Add --yes to upload.")
        return
    for key, f in ups:
        r = subprocess.run(["npx", "wrangler", "r2", "object", "put", f"{BUCKET}/{key}", "--file", str(f),
                            "--content-type", TYPES[f.suffix], "--remote"], cwd=ROOT / "cloud", capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f"{key}: upload failed (exit {r.returncode})\n{(r.stdout + r.stderr)[-600:]}")
        print(f"  ✓ {key}")
    print(f"uploaded {len(ups)} files")


if __name__ == "__main__":
    main()
