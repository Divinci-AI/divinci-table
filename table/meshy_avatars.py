#!/usr/bin/env python3
"""Generate the stage avatars with Meshy: text-to-3D (T-pose) → texture → rig → animate → GLB on disk.

    MESHY_API_KEY=… /usr/bin/python3 table/meshy_avatars.py [seat …]      (table/play.sh never calls this)

Resumable: every task id is saved to table/.cache/avatars/state.json, so a rerun picks up where it
stopped instead of paying twice. Output: table/.cache/avatars/<Seat>.glb (rigged mesh + one clip per
action), served to the stage by table/stage_server.py. Nothing here runs during a game.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

API = "https://api.meshy.ai"
OUT = Path(__file__).parent / ".cache" / "avatars"
STATE = OUT / "state.json"
POSE = ("Full-body game character standing in a T-pose, arms straight out to the sides, legs slightly apart, "
        "facing forward, clearly separated limbs, stylized, friendly expressive face. ")
AVATARS = {   # described in our own words — inspired by each deck, not copies of card art
    "Claude": POSE + "A young fantasy sorceress in flowing layered robes of white, deep blue and black, silver trim, "
                     "a glowing coral-orange starburst crown floating just above her dark hair, calm warm smile.",
    "Fusion": POSE + "A dryad detective: moss-green skin with bark texture, a crown of leaves for hair, a long "
                     "forest-green trench coat, and a brass detective's monocle over one eye.",
    "Michael": POSE + "A friendly robot artificer: blue and red enamelled metal plates, a glowing cyan visor for eyes, "
                      "an antenna with a red light, small brass gears on the shoulders, a tool belt.",
    "Sam": POSE + "A squid-faced psychic pirate captain: lavender skin, four short tentacles hanging from the lower "
                  "face, glowing white eyes, a black tricorn hat, a dark navy captain's coat with gold buttons.",
}
ACTIONS = [0, 308, 36, 59, 28]      # Idle, Talking, Confused_Scratch (thinking), Victory_Cheer, Big_Wave_Hello
CLIPS = ["idle", "talk", "think", "cheer", "wave"]


def call(method, path, body=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": "Bearer " + os.environ["MESHY_API_KEY"],
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            text = r.read().decode()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} → HTTP {e.code}: {e.read().decode()[:300]}") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError(f"{method} {path}: not JSON: {text[:200]}") from None


def load():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save(seat, key, val):
    st = load()
    st.setdefault(seat, {})[key] = val
    STATE.write_text(json.dumps(st, indent=1))


def wait(path, seat, label):
    while True:
        t = call("GET", path)
        st = t.get("status")
        print(f"  {seat:8} {label:8} {st} {t.get('progress', '')}", flush=True)
        if st == "SUCCEEDED":
            return t
        if st in ("FAILED", "CANCELED", "EXPIRED"):
            raise RuntimeError(f"{seat} {label} {st}: {json.dumps(t.get('task_error'))[:200]}")
        time.sleep(15)


def build(seat):
    s = load().get(seat, {})
    if "preview" not in s:
        r = call("POST", "/openapi/v2/text-to-3d", {"mode": "preview", "prompt": AVATARS[seat], "ai_model": "latest",
                                                    "pose_mode": "t-pose", "should_remesh": True, "topology": "triangle",
                                                    "target_polycount": 30000, "art_style": "realistic"})
        save(seat, "preview", r["result"]); s = load()[seat]
    wait(f"/openapi/v2/text-to-3d/{s['preview']}", seat, "preview")
    if "refine" not in s:
        r = call("POST", "/openapi/v2/text-to-3d", {"mode": "refine", "preview_task_id": s["preview"],
                                                    "enable_pbr": True, "ai_model": "latest"})
        save(seat, "refine", r["result"]); s = load()[seat]
    ref = wait(f"/openapi/v2/text-to-3d/{s['refine']}", seat, "texture")
    if "rig" not in s:
        r = call("POST", "/openapi/v1/rigging", {"input_task_id": s["refine"], "height_meters": 1.75})
        save(seat, "rig", r["result"]); s = load()[seat]
    rig = wait(f"/openapi/v1/rigging/{s['rig']}", seat, "rig")
    if "anim" not in s:
        r = call("POST", "/openapi/v1/animations", {"rig_task_id": s["rig"], "action_ids": ACTIONS})
        save(seat, "anim", r["result"]); s = load()[seat]
    anim = wait(f"/openapi/v1/animations/{s['anim']}", seat, "animate")
    res = anim.get("result") or anim
    url = res.get("animation_glb_url") or anim.get("animation_glb_url")
    urllib.request.urlretrieve(url, OUT / f"{seat}.glb")
    if ref.get("thumbnail_url"):
        urllib.request.urlretrieve(ref["thumbnail_url"], OUT / f"{seat}.png")
    save(seat, "done", {"glb": f"{seat}.glb", "clips": CLIPS, "actions": ACTIONS,
                        "rigged_glb": (rig.get("result") or rig).get("rigged_character_glb_url")})
    print(f"✅ {seat}: {OUT / (seat + '.glb')}", flush=True)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    if "--balance" in sys.argv:
        print(call("GET", "/openapi/v1/balance")); sys.exit()
    seats = [a for a in sys.argv[1:] if a in AVATARS] or list(AVATARS)
    with ThreadPoolExecutor(len(seats)) as ex:
        for f in [ex.submit(build, s) for s in seats]:
            try:
                f.result()
            except Exception as e:  # noqa: BLE001 — one avatar failing must not stop the others
                print(f"❌ {e}", flush=True)
