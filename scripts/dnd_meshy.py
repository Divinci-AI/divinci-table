#!/usr/bin/env python3
"""Generate D&D minis and location art with Meshy, then import them (scripts/dnd_minis.py) — the pipeline the
2026-11-15 pilot settled on:

  mini:  concept image (nano-banana-2, 6 cr) → image-to-3D (latest, textured, 30 cr) → for a rigged creature:
         remesh to 18k triangles (5 cr; Meshy ignores target_polycount on image-to-3D) → auto-rig (5 cr, walk + run)
  art:   one 16:9 painting (nano-banana-2, 6 cr) → table/.cache/dnd/locations/<id>/art.jpg

    python3 scripts/dnd_meshy.py jobs.json --out DIR --key-file KEYFILE --budget 960 [--dry-run]

jobs.json: {"style": "...", "minis": [{"id","name","size","srd"?,"rig":bool,"height":m,"prompt"}],
            "art": [{"location","prompt"}]}
Every task id is saved to DIR/state.json, so a rerun resumes instead of paying twice. The run stops before any
step that would take the spend past --budget. The key is read from a file and never printed.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.meshy.ai/openapi/v1"
COST = {"image": 6, "model": 30, "remesh": 5, "rig": 5}

ap = argparse.ArgumentParser()
ap.add_argument("jobs", type=Path)
ap.add_argument("--out", type=Path, required=True)
ap.add_argument("--key-file", type=Path, required=True)
ap.add_argument("--budget", type=int, required=True, help="credits this run may spend at most")
ap.add_argument("--dry-run", action="store_true")
ap.add_argument("--workers", type=int, default=6)
a = ap.parse_args()
KEY = a.key_file.read_text().strip()
a.out.mkdir(parents=True, exist_ok=True)
STATE_F = a.out / "state.json"
STATE = json.loads(STATE_F.read_text()) if STATE_F.exists() else {"spent": 0, "tasks": {}}
LOCK = threading.Lock()
IMPORT = threading.Lock()                                 # the importer rewrites the manifest: one at a time


def log(*m):
    print(time.strftime("%H:%M:%S"), *m, flush=True)


def save():
    with LOCK:
        STATE_F.write_text(json.dumps(STATE, indent=1))


def req(method, path, body=None):
    r = urllib.request.Request(API + path, data=None if body is None else json.dumps(body).encode(), method=method,
                               headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(r, timeout=60) as x:
                return json.loads(x.read())
        except urllib.error.HTTPError as e:
            msg = e.read()[:300].decode(errors="replace")
            if e.code in (429, 500, 502, 503) and attempt < 4:
                time.sleep(5 * (attempt + 1)); continue
            raise RuntimeError(f"{method} {path}: HTTP {e.code} {msg}")
        except (urllib.error.URLError, TimeoutError):
            if attempt < 4:
                time.sleep(5 * (attempt + 1)); continue
            raise


def step(key, kind, path, body, cost):
    """Create (once) and wait for a task; returns its finished JSON. Refuses to start past the budget."""
    with LOCK:
        tid = STATE["tasks"].get(key)
        if not tid:
            if STATE["spent"] + cost > a.budget:
                raise RuntimeError(f"budget: {key} would take spend to {STATE['spent'] + cost} > {a.budget}")
            if a.dry_run:
                log("DRY", key, path, cost); return None
            STATE["spent"] += cost
    if not tid:
        tid = req("POST", path, body)["result"]
        with LOCK:
            STATE["tasks"][key] = tid
        save()
        log("started", key, tid)
    for _ in range(240):
        d = req("GET", f"{path}/{tid}")
        if d.get("status") == "SUCCEEDED":
            return d
        if d.get("status") in ("FAILED", "CANCELED", "EXPIRED"):
            raise RuntimeError(f"{key}: {d.get('status')} {d.get('task_error')}")
        time.sleep(8)
    raise RuntimeError(f"{key}: timed out")


def fetch(url, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=300) as r:
        dest.write_bytes(r.read())
    return dest


def mini(job, style):
    jid = job["id"]
    img = step(f"{jid}:image", "image", "/text-to-image",
               {"ai_model": "nano-banana-2", "prompt": f"{job['prompt']} {style}"[:600],
                **({"pose_mode": "t-pose"} if job.get("rig") else {})}, COST["image"])
    if img is None:
        return jid, "dry"
    url = img["image_urls"][0]
    fetch(url, a.out / jid / "concept.png")
    mdl = step(f"{jid}:model", "model", "/image-to-3d",
               {"image_url": url, "ai_model": "latest", "should_texture": True, "target_formats": ["glb"],
                **({"pose_mode": "t-pose"} if job.get("rig") else {})}, COST["model"])
    if job.get("rig"):
        rm = step(f"{jid}:remesh", "remesh", "/remesh",
                  {"input_task_id": STATE["tasks"][f"{jid}:model"], "topology": "triangle", "target_polycount": 18000,
                   "target_formats": ["glb"]}, COST["remesh"])
        rig = step(f"{jid}:rig", "rig", "/rigging",
                   {"input_task_id": STATE["tasks"][f"{jid}:remesh"], "height_meters": job.get("height", 1.7)}, COST["rig"])
        res = rig.get("result") or {}
        src = (res.get("basic_animations") or {}).get("walking_glb_url") or res.get("rigged_character_glb_url")
        glb = fetch(src, a.out / jid / "rigged-walk.glb")
    else:
        glb = fetch(mdl["model_urls"]["glb"], a.out / jid / "model.glb")
    cmd = [sys.executable, str(ROOT / "scripts" / "dnd_minis.py"), "add", str(glb), "--id", jid, "--size", job["size"],
           "--licence", "Meshy output (Divinci account)", "--credit", "", "--name", job["name"],
           "--source", f"Meshy image-to-3D{' + auto-rig (walk)' if job.get('rig') else ''} from a Meshy concept image, {time.strftime('%Y-%m-%d')}"]
    if job.get("srd"):
        cmd += ["--srd", job["srd"]]
    with IMPORT:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    line = next((l for l in r.stdout.splitlines() if l.startswith("MINI-BUILT")), r.stderr.strip()[-200:])
    return jid, line


def art(job, style):
    loc = job["location"]
    img = step(f"art:{loc}", "image", "/text-to-image",
               {"ai_model": "nano-banana-2", "prompt": job["prompt"][:600], "aspect_ratio": "16:9"}, COST["image"])
    if img is None:
        return loc, "dry"
    png = fetch(img["image_urls"][0], a.out / "art" / f"{loc}.png")
    jpg = ROOT / "table" / ".cache" / "dnd" / "locations" / loc / "art.jpg"
    subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "85", str(png), "--out", str(jpg)],
                   capture_output=True, check=True)
    return loc, f"art → {jpg.relative_to(ROOT)} ({jpg.stat().st_size // 1024} KB)"


jobs = json.loads(a.jobs.read_text())
style = jobs.get("style", "")
plan = sum(COST["image"] + COST["model"] + (COST["remesh"] + COST["rig"] if j.get("rig") else 0) for j in jobs.get("minis", [])) \
    + COST["image"] * len(jobs.get("art", []))
log(f"plan: {len(jobs.get('minis', []))} minis, {len(jobs.get('art', []))} paintings, ~{plan} credits; budget {a.budget}; "
    f"already spent in this run dir: {STATE['spent']}")
if plan > a.budget and not STATE["tasks"]:
    sys.exit(f"the plan ({plan}) is over the budget ({a.budget})")
results, failed = [], []
with ThreadPoolExecutor(a.workers) as ex:
    futs = [ex.submit(mini, j, style) for j in jobs.get("minis", [])] + [ex.submit(art, j, style) for j in jobs.get("art", [])]
    for f in futs:
        try:
            results.append(f.result()); log("done", *results[-1])
        except Exception as e:                                  # one creature's failure doesn't stop the others
            failed.append(str(e)); log("FAILED", str(e)[:300])
save()
log(f"finished: {len(results)} ok, {len(failed)} failed; credits spent by this run dir: {STATE['spent']}")
sys.exit(1 if failed else 0)
