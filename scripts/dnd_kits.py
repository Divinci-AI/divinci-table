"""The free 3D model kits the D&D rooms are built from: fetched once, checked, unpacked.

    python3 scripts/dnd_kits.py          # download anything missing into table/.cache/dnd/kits/

Every kit is CC0 (public domain): free for any use, no attribution required. We credit the makers anyway
(/api/dnd/credits). The kits stay out of git; scripts/dnd_rooms.py bakes the pieces it uses into each room's
room.glb, which is what players download. Each archive is pinned by SHA-256, so a changed upstream file is
refused rather than silently built into the rooms.
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KITS_DIR = ROOT / "table" / ".cache" / "dnd" / "kits"

KITS = {
    "kaykit": {
        "name": "KayKit Dungeon Remastered 1.0", "author": "Kay Lousberg", "license": "CC0-1.0",
        "page": "https://github.com/KayKit-Game-Assets/KayKit-Dungeon-Remastered-1.0",
        "zip": "https://codeload.github.com/KayKit-Game-Assets/KayKit-Dungeon-Remastered-1.0/zip/"
               "b0ca9bd96a8072ab36a3a5464f00ed1e06a16d07",
        "sha256": "e96a65ce4040b6b630f04470a60fd523d621c124cfbad062809cf7b94ccf714b",
        "models": "*/addons/kaykit_dungeon_remastered/Assets/gltf",
    },
    "nature": {
        "name": "Nature Kit 2.1", "author": "Kenney", "license": "CC0-1.0",
        "page": "https://kenney.nl/assets/nature-kit",
        "zip": "https://kenney.nl/media/pages/assets/nature-kit/37ac38a37b-1677698939/kenney_nature-kit.zip",
        "sha256": "fa7974a0d342bfe63c38664ba9f8ec1a4aab8ea25f099bdc56870e33588c4d9d",
        "models": "Models/GLTF format",
    },
    "graveyard": {
        "name": "Graveyard Kit 5.0", "author": "Kenney", "license": "CC0-1.0",
        "page": "https://kenney.nl/assets/graveyard-kit",
        "zip": "https://kenney.nl/media/pages/assets/graveyard-kit/ba8d4b4517-1760691807/kenney_graveyard-kit_5.0.zip",
        "sha256": "1a93613f2e5675f3310acf49ec9ef13ae7adeb756ac3b205bfb6cc9311a81062",
        "models": "Models/GLB format",
    },
    "town": {
        "name": "Fantasy Town Kit 2.0", "author": "Kenney", "license": "CC0-1.0",
        "page": "https://kenney.nl/assets/fantasy-town-kit",
        "zip": "https://kenney.nl/media/pages/assets/fantasy-town-kit/efe948d309-1754222374/kenney_fantasy-town-kit_2.0.zip",
        "sha256": "1a7530c09f4d2fa2cdee259876f089334f8b1f27fa86a0c4f54ef86cdd8676ef",
        "models": "Models/GLB format",
    },
}


def credits() -> str:
    return " ".join(f"{k['name']} by {k['author']} ({k['page']}), CC0." for k in KITS.values())


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure() -> dict[str, str]:
    """Download, verify and unpack any missing kit. Returns {kit: absolute model directory}."""
    KITS_DIR.mkdir(parents=True, exist_ok=True)
    dirs = {}
    for kid, k in KITS.items():
        z, d = KITS_DIR / f"{kid}.zip", KITS_DIR / kid
        if not z.exists() or _sha(z) != k["sha256"]:
            print(f"fetching {k['name']} …", file=sys.stderr)
            req = urllib.request.Request(k["zip"], headers={"User-Agent": "Mozilla/5.0 (divinci-table dnd_kits.py)"})
            with urllib.request.urlopen(req, timeout=120) as r, z.open("wb") as f:
                f.write(r.read())
            got = _sha(z)
            if got != k["sha256"]:
                z.unlink()
                raise SystemExit(f"{kid}: checksum mismatch ({got[:16]}…), refusing to build from it")
        if not d.exists():
            with zipfile.ZipFile(z) as zf:
                zf.extractall(d)
        found = sorted(d.glob(k["models"]))
        if not found:
            raise SystemExit(f"{kid}: no model folder matching {k['models']!r} in {d}")
        dirs[kid] = str(found[0])
    return dirs


if __name__ == "__main__":
    for kid, path in ensure().items():
        n = len(list(Path(path).glob("*.glb")))
        print(f"{kid:10} {n:4} models  {path}")
