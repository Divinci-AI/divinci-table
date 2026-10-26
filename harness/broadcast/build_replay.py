#!/usr/bin/env python3
"""Turn a FINISHED game's run folder into a static replay for the broadcast page.

    python3 harness/broadcast/build_replay.py RUN_DIR --out DIR [--mode public|spectator] [--model-label TEXT]

Reads RUN_DIR/events.json (the public event stream) and RUN_DIR/state.jsonl (public board snapshots, once a second).
  --mode public     (default) only what a person at the table could see: boards, life, counts, phase, table talk.
  --mode spectator  also each seat's hand, read from RUN_DIR/research/*/brain.jsonl. For finished ALL-AI games only (Michael's
                    decision D3); the moment a human sat in, use public.
Writes DIR/replay.json. The page and its server never see the run folder: this file is all they get.

Not in the replay, ever: seeds, entropy, tokens, server logs, refusals, reasoning, journals. A seed is left out because arena ids
embed it; the rest because they are private. Table talk passes through `clean_caption` (control characters, URLs, @mentions and
over-long lines are removed) because a model wrote it and it will be on a public screen.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

TITLE = "Divinci Table: AI's Playing Cards"
BANNER = "AI-controlled players · simulated games · replay of a finished game"
MAX_GAP = 2.5          # seconds of idle time kept between two frames in playback
MAX_LINE = 140


def clean_caption(text: str) -> str:
    t = re.sub(r"[\x00-\x1f\x7f​-‏‪-‮⁦-⁩]", " ", str(text or ""))   # control + bidi characters
    t = re.sub(r"https?://\S+|www\.\S+", "[link]", t)
    t = re.sub(r"(?<!\w)@\w+", "@someone", t)
    t = re.sub(r"\s+", " ", t).strip()
    return (t[:MAX_LINE - 1] + "…") if len(t) > MAX_LINE else t


def read_jsonl(p: Path) -> list[dict]:
    out = []
    if p.exists():
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def seat_state(st: dict, hand=None) -> dict:
    lands = sum(1 for p in st["perms"] if p[2] and "Land" in p[2])
    other = [{"name": p[1], "pt": p[3], "tapped": bool(p[4]), "counters": p[5] or 0, "token": bool(p[6]), "aura": p[7] is not None}
             for p in st["perms"] if not (p[2] and "Land" in p[2])]
    out = {"life": st["life"], "hand": st["hand"], "library": st["library"], "gy": st["gy"], "lands": lands, "perms": other,
           "cmdr": st.get("cmdr"), "cmdr_in_zone": st.get("cmdr_in_zone")}
    if hand is not None:
        out["hand_cards"] = hand
    return out


def build(run: Path, mode="public", model_label="") -> dict:
    ev = json.loads((run / "events.json").read_text())["events"] if (run / "events.json").exists() else []
    snaps = sorted(read_jsonl(run / "state.jsonl"), key=lambda x: x["ts"])
    brain = []
    if mode == "spectator":
        for f in sorted((run / "research").glob("*/brain.jsonl")):
            brain += [r for r in read_jsonl(f) if r.get("seat") and r.get("hand_after") is not None]
        brain.sort(key=lambda r: r.get("ts", 0))
    seats = list(snaps[0]["seats"]) if snaps else []
    talk = [(e["ts"], e.get("speaker"), clean_caption(e.get("text"))) for e in ev if e.get("type") == "say" and e.get("speaker") and e.get("text")]
    phases = [(e["ts"], e.get("player"), e.get("step")) for e in ev if e.get("type") == "phase"]
    over = next((e for e in ev if e.get("type") == "game-over"), None)
    frames, t, prev_ts, prev_key = [], 0.0, None, None
    for s in snaps:
        hands = {}
        if mode == "spectator":
            for seat in seats:
                last = [r for r in brain if r["seat"] == seat and r["ts"] <= s["ts"]]
                hands[seat] = list(last[-1]["hand_after"]) if last else []
        board = {n: seat_state(st, hands.get(n) if mode == "spectator" else None) for n, st in s["seats"].items()}
        ph = [p for p in phases if p[0] <= s["ts"]]
        lines = [{"who": w, "text": x} for ts, w, x in talk if ts <= s["ts"]][-6:]
        key = json.dumps([board, ph[-1][1:] if ph else None, lines], sort_keys=True)
        if key == prev_key:
            continue                                   # nothing changed since the last frame: no new frame
        t += min(MAX_GAP, (s["ts"] - prev_ts)) if prev_ts is not None else 0.0
        prev_ts, prev_key = s["ts"], key
        frames.append({"t": round(t, 2), "phase": {"player": ph[-1][1], "step": ph[-1][2]} if ph else None, "seats": board, "talk": lines})
    result = None
    if over:
        result = {"winner": over.get("winner"), "order": over.get("order")}
    return {"title": TITLE, "banner": BANNER, "mode": mode,
            "now_playing": {"seats": [{"name": n, "commander": (snaps[0]["seats"][n].get("cmdr") if snaps else None)} for n in seats],
                            "model": model_label or "AI players"},
            "frames": frames, "duration": round(t + 3, 2), "result": result}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("run"); ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=("public", "spectator"), default="public"); ap.add_argument("--model-label", default="")
    a = ap.parse_args()
    r = build(Path(a.run), a.mode, a.model_label)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (out / "replay.json").write_text(json.dumps(r))
    print(f"replay: {len(r['frames'])} frames, {r['duration']}s, mode {r['mode']} -> {out / 'replay.json'}")
    if not r["frames"]:
        sys.exit("no board snapshots in the run folder (state.jsonl): play the game with a current pilot_game.py")


if __name__ == "__main__":
    main()
