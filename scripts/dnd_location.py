#!/usr/bin/env python3
"""Key art and a 5-second establishing shot for D&D locations, from NVIDIA Cosmos 3 (build.nvidia.com).

    infisical run --env=prod --path=/ -- python3 scripts/dnd_location.py --dry-run          # what it would ask
    infisical run --env=prod --path=/ -- python3 scripts/dnd_location.py --yes tavern       # one location
    infisical run --env=prod --path=/ -- python3 scripts/dnd_location.py --yes              # all of them

Needs NVIDIA_API_KEY (build.nvidia.com; free credits — docs/DND-3D-GOAL.md rule 5). Two requests per location:
  art.jpg    text2image from the location's name, theme and tags, in the table's painted style.
  shot.mp4   image2video FROM THE ROOM'S BLENDER VIEW (scripts/dnd_rooms.py), so the shot shows the same room the
             map uses. Cosmos returns VP9-in-MP4: published untouched as shot_orig.mp4 (Chrome and the Quest play
             it, and SynthID is checked on the untouched file) beside an H.264 copy, shot.mp4, for iPhones.

The API shape (POST https://ai.api.nvidia.com/v1/cosmos/nvidia/cosmos3-nano; model_mode, prompt, resolution,
num_frames, num_inference_steps, fps, seed, input_reference; b64_image / b64_video back) is from NVIDIA's
cosmos3-nano model card, read 2026-10-04. NOT YET RUN: there was no key. Expect the first run to need
adjustments (accepted resolutions, frame limits); the script prints the API's error body when it refuses.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "table" / "dnd_assets.json"
OUT = ROOT / "table" / ".cache" / "dnd" / "locations"
URL = "https://ai.api.nvidia.com/v1/cosmos/nvidia/cosmos3-nano"
STYLE = ("Painted fantasy illustration for a tabletop role-playing game, rich warm light, detailed, "
         "no text, no people in the foreground, no logos")
MOOD = {"tavern": "a cosy timber tavern at night, candles and a hearth", "road": "a dirt road through green hills at dusk",
        "forest": "a sunlit clearing ringed by tall pines", "cave": "a damp cave lit by a single torch, pools of water",
        "crypt": "a cold stone crypt with sarcophagi, faint blue light", "keep": "the great hall of a stone keep, banners and a long table",
        "bridge": "an old stone bridge over a fast river, forest on both banks", "ruins": "a ruined watchtower overgrown with grass"}


def ask(body: dict, key: str) -> dict:
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": "Bearer " + key, "Accept": "application/json", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            status, text = r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode(errors="replace")
    try:
        d = json.loads(text)
    except ValueError:
        raise SystemExit(f"Cosmos answered {status} with non-JSON: {text[:300]}")
    if status >= 400:
        raise SystemExit(f"Cosmos refused ({status}): {text[:600]}")
    return d


def requests_for(loc: dict) -> list[tuple[str, dict]]:
    scene = MOOD.get(loc["theme"], loc["name"])
    out = [("art", {"model_mode": "text2image", "prompt": f"{loc['name']}: {scene}. {STYLE}.",
                    "resolution": "480_16_9", "seed": 7})]
    view = OUT / loc["id"] / "view.jpg"
    out.append(("shot", {"model_mode": "image2video",
                         "prompt": f"A slow, steady establishing shot drifting over {scene}. The layout stays exactly as in "
                                   f"the first frame. {STYLE}.",
                         "input_reference": "<view.jpg>" if not view.exists() else
                         "data:image/jpeg;base64," + base64.b64encode(view.read_bytes()).decode(),
                         "resolution": "480_16_9", "num_frames": 121, "fps": 24, "num_inference_steps": 35, "seed": 7}))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--dry-run", action="store_true", help="print the requests; send nothing")
    ap.add_argument("--yes", action="store_true", help="really send them (uses build.nvidia.com credits)")
    ap.add_argument("--only", choices=["art", "shot"])
    a = ap.parse_args()
    man = json.loads(MANIFEST.read_text())
    locs = [x for x in man["locations"] if not a.ids or x["id"] in a.ids]
    jobs = [(loc, kind, body) for loc in locs for kind, body in requests_for(loc) if not a.only or kind == a.only]
    print(f"{len(jobs)} Cosmos request(s) for {', '.join(x['id'] for x in locs)}")
    if a.dry_run or not a.yes:
        for loc, kind, body in jobs:
            shown = {k: (v[:40] + "…" if isinstance(v, str) and len(v) > 80 else v) for k, v in body.items()}
            print(f"  {loc['id']}/{kind}: {json.dumps(shown)}")
        if not a.yes:
            print("Nothing sent. Add --yes to send them.")
        return
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        raise SystemExit("NVIDIA_API_KEY is not set (create it at build.nvidia.com; run under `infisical run`)")
    for loc, kind, body in jobs:
        d = OUT / loc["id"]
        d.mkdir(parents=True, exist_ok=True)
        if kind == "shot" and body["input_reference"] == "<view.jpg>":
            print(f"  {loc['id']}/shot: skipped — build the room first (scripts/dnd_rooms.py {loc['id']})")
            continue
        r = ask(body, key)
        if kind == "art":
            raw = base64.b64decode(r["b64_image"], validate=True)
            (d / "art.orig").write_bytes(raw)
            subprocess.run(["sips", "-s", "format", "jpeg", str(d / "art.orig"), "--out", str(d / "art.jpg")],
                           check=True, capture_output=True)
            loc["art"] = f"dnd/locations/{loc['id']}/art.jpg"
        else:
            (d / "shot_orig.mp4").write_bytes(base64.b64decode(r["b64_video"], validate=True))
            if not shutil.which("ffmpeg"):
                raise SystemExit("ffmpeg is needed to make the H.264 copy players can play")
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(d / "shot_orig.mp4"), "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", "-crf", "23", "-movflags", "+faststart", "-an", str(d / "shot.mp4")], check=True)
            loc["shot"] = f"dnd/locations/{loc['id']}/shot.mp4"
            loc["shot_orig"] = f"dnd/locations/{loc['id']}/shot_orig.mp4"
        loc["credit"] = "Key art and establishing shot built on NVIDIA Cosmos (Cosmos3-Nano, OpenMDW-1.1); SynthID-watermarked."
        loc["status"] = "draft"                          # new pictures: a person looks again before players do
        print(f"  {loc['id']}/{kind}: saved")
        MANIFEST.write_text(json.dumps(man, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    sys.exit(main())
