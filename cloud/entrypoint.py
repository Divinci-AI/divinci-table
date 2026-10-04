#!/usr/bin/env python3
"""Start one table room inside a container: the table server plus one brain per AI seat.

The room is described by ROOM_CONFIG (JSON), set by the Cloudflare Worker that owns the room:
  {"humans": ["Michael", "Sam"],
   "ai": [{"name": "Fusion", "commander": "Tuvasa the Sunlit", "deck": "decks/tuvasa.json"}],
   "order": "Michael,Fusion,Sam"}              # optional; otherwise the high roll decides
AI seats are played by Divinci releases (table/fusion_brain.py): DIVINCI_FUSION_API_KEY and
FUSION_CONFIG ({"releases": {"<commander>": "<release id>"}}) come from Worker secrets, never the repo.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
cfg = json.loads(os.environ.get("ROOM_CONFIG") or '{"humans": ["Player 1", "Player 2"], "ai": []}')
cache = ROOT / "table" / ".cache"
cache.mkdir(parents=True, exist_ok=True)
if os.environ.get("FUSION_CONFIG"):
    (cache / "fusion.json").write_text(os.environ["FUSION_CONFIG"])

if cfg.get("game") == "chess":                      # chess: one process, AI seats play through Stockfish (no model spend)
    chess_args = [sys.executable, "-u", "table/chess_server.py", "--host", "0.0.0.0", "--port", os.environ.get("PORT", "8800"),
                  "--white", cfg["white"], "--black", cfg["black"],
                  "--minutes", str(cfg.get("minutes", 15)), "--increment", str(cfg.get("increment", 10))]
    env = dict(os.environ, TABLE_CLOUD="1")
    sys.exit(subprocess.call(chess_args, cwd=ROOT, env=env))

if cfg.get("game") == "dnd":                        # D&D: one process; the AI DM only when the Worker switched it on
    dnd_args = [sys.executable, "-u", "table/dnd_server.py", "--host", "0.0.0.0", "--port", os.environ.get("PORT", "8800"),
                "--players", ",".join(cfg.get("humans") or ["Player 1"])]
    if cfg.get("dm"):
        dnd_args += ["--dm", cfg["dm"]]
    env = dict(os.environ, TABLE_CLOUD="1")
    sys.exit(subprocess.call(dnd_args, cwd=ROOT, env=env))

args = [sys.executable, "-u", "table/server.py", "--any-card", "--brain", "external",
        "--host", "0.0.0.0", "--port", os.environ.get("PORT", "8800"), "--fair-seed", "online",
        "--priority-window", "3"]
for a in cfg.get("ai", []):
    args += ["--ai", f"{a['name']}|{a['commander']}|", "--ai-deck", a["deck"]]
for p in cfg.get("pilots", []):                     # a person playing a virtual deck from /hand (no brain process)
    args += ["--ai", f"{p['name']}|{p['commander']}|", "--ai-deck", p["deck"], "--pilot", p["name"]]
for h in cfg.get("humans", []):
    args += ["--human", h if "|" in h else f"{h}|"]
if cfg.get("order"):
    args += ["--order", cfg["order"]]
env = dict(os.environ, TABLE_CLOUD="1", NO_GEMMA="1", ROUTER="code", REPLIES="code")
procs = [subprocess.Popen(args, cwd=ROOT, env=env)]
time.sleep(4)                                       # the server writes the brain token on start
key = os.environ.get("DIVINCI_FUSION_API_KEY")
for a in cfg.get("ai", []):
    if key:
        procs.append(subprocess.Popen([sys.executable, "-u", "table/fusion_brain.py", "--seat", a["name"]],
                                      cwd=ROOT, env=env))
    else:
        print(f"no DIVINCI_FUSION_API_KEY: AI seat {a['name']} has no brain", flush=True)
while all(p.poll() is None for p in procs):        # if any process dies, stop the container
    time.sleep(2)
for p in procs:
    p.terminate()
sys.exit(1)
