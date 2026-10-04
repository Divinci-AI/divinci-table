#!/usr/bin/env python3
"""The results ledger: one record per game, and the public leaderboard built from it (ROADMAP M7).

    python3 table/ledger.py check                       # validate docs/results/ledger.json
    python3 table/ledger.py board                       # print the leaderboard, write cloud/public/leaderboard.json
    python3 table/ledger.py finish --id g4 --research 20261010-200000 --order "Sam,Fusion,Michael,Claude" \\
        --how "Sam's commander damage"                  # record a finished game, then survey its AI seats

The ledger is hand-checked research data, not telemetry: a game is entered from its research folder and
HANDOFF notes, and every number on the board traces back to a record here. Names: people by first name
(as in the docs), AI seats by the SYSTEM that played them and its model, because the board ranks systems.

Confounds are part of the result, not a footnote. A record says when a person piloted an AI seat, when one
system played several seats, when the rules engine was missing a card, and when a game was unfinished or
forked. The board shows them next to the numbers and counts only games that finished.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "docs" / "results" / "ledger.json"
BOARD = ROOT / "cloud" / "public" / "leaderboard.json"
STATUSES = {"finished", "unfinished", "forked", "abandoned"}
KINDS = {"human", "ai"}


def load() -> dict:
    return json.loads(LEDGER.read_text())


def validate(d: dict) -> list[str]:
    """Every problem with the ledger, as sentences. Empty means valid."""
    errs: list[str] = []
    ids = set()
    for i, g in enumerate(d.get("games") or []):
        where = f"game {g.get('id', i)}"
        if g.get("id") in ids:
            errs.append(f"{where}: duplicate id")
        ids.add(g.get("id"))
        for k in ("id", "date", "game", "seats", "result"):
            if k not in g:
                errs.append(f"{where}: missing {k}")
        seats = [s.get("seat") for s in g.get("seats") or []]
        if len(seats) != len(set(seats)):
            errs.append(f"{where}: a seat appears twice")
        for s in g.get("seats") or []:
            c = s.get("controller") or {}
            if c.get("kind") not in KINDS:
                errs.append(f"{where}: seat {s.get('seat')} controller.kind must be human or ai")
            if c.get("kind") == "ai" and not c.get("system"):
                errs.append(f"{where}: AI seat {s.get('seat')} needs controller.system")
        r = g.get("result") or {}
        if r.get("status") not in STATUSES:
            errs.append(f"{where}: result.status must be one of {sorted(STATUSES)}")
        order = r.get("order") or []
        if r.get("status") == "finished":
            if sorted(order) != sorted(seats):
                errs.append(f"{where}: a finished game's order must list every seat exactly once")
            if r.get("winner") and order and r["winner"] != order[0]:
                errs.append(f"{where}: winner must be first in order")
        elif any(o not in seats for o in order):
            errs.append(f"{where}: order names a seat that isn't at the table")
    return errs


def board(d: dict) -> dict:
    """Per AI system: seats played (one game can give a system several seats), finished games, wins, mean placement (1 = won) over finished games.
    Seats a person piloted for the AI, or that one system played alongside others it controlled, are
    counted separately as `assisted` and left out of the placement average."""
    rows: dict[str, dict] = {}
    for g in d.get("games") or []:
        r = g["result"]
        n = len(g["seats"])
        for s in g["seats"]:
            c = s["controller"]
            if c["kind"] != "ai":
                continue
            key = c["system"] + (f" ({c['model']})" if c.get("model") else "")
            row = rows.setdefault(key, {"system": c["system"], "model": c.get("model"), "seats": 0, "finished": 0,
                                        "wins": 0, "placements": [], "assisted": 0, "not_finished": 0})
            row["seats"] += 1
            if s.get("assisted"):
                row["assisted"] += 1
                continue
            if r["status"] != "finished":
                row["not_finished"] += 1
                continue
            place = r["order"].index(s["seat"]) + 1
            row["finished"] += 1
            row["wins"] += place == 1
            row["placements"].append(f"{place}/{n}")
    out = []
    for row in rows.values():
        ps = [int(p.split("/")[0]) for p in row["placements"]]
        row["mean_place"] = round(sum(ps) / len(ps), 2) if ps else None
        out.append(row)
    out.sort(key=lambda x: (-(x["finished"]), x["mean_place"] or 99))
    games = [{"id": g["id"], "date": g["date"], "game": g["game"], "status": g["result"]["status"],
              "winner": g["result"].get("winner"), "how": g["result"].get("how", ""),
              "seats": [{"seat": s["seat"], "commander": s.get("commander"), "by": _who(s)} for s in g["seats"]],
              "confounds": g.get("confounds") or [], "integrity_flags": (g.get("integrity") or {}).get("flags", 0)}
             for g in d.get("games") or []]
    return {"updated": time.strftime("%Y-%m-%d"), "systems": out, "games": games,
            "note": d.get("note", "")}


def _who(s: dict) -> str:
    c = s["controller"]
    if c["kind"] == "human":
        return c.get("name") or "a person"
    return c["system"] + (f" ({c['model']})" if c.get("model") else "") + (" — assisted" if s.get("assisted") else "")


def cmd_check(_a) -> int:
    errs = validate(load())
    for e in errs:
        print("✗", e)
    print(f"{len(load()['games'])} games, {len(errs)} problems")
    return 1 if errs else 0


def cmd_board(_a) -> int:
    d = load()
    errs = validate(d)
    if errs:
        print("ledger has problems; run `check`", file=sys.stderr)
        return 1
    b = board(d)
    BOARD.write_text(json.dumps(b, indent=1, ensure_ascii=False) + "\n")
    for r in b["systems"]:
        print(f"{r['system']:<10} {r['model'] or '':<28} seats {r['seats']}  finished {r['finished']}  wins {r['wins']}  "
              f"mean place {r['mean_place']}  assisted {r['assisted']}  not finished {r['not_finished']}")
    print(f"wrote {BOARD.relative_to(ROOT)}")
    return 0


def cmd_finish(a) -> int:
    """Record a finished game's order on an existing ledger entry, then survey its AI seats."""
    d = load()
    g = next((x for x in d["games"] if x["id"] == a.id), None)
    if not g:
        print(f"no game {a.id} in the ledger: add its seats first", file=sys.stderr)
        return 1
    order = [x.strip() for x in a.order.split(",") if x.strip()]
    g["result"] = {"status": "finished", "order": order, "winner": order[0], "how": a.how}
    if a.research:
        g["research"] = a.research
    errs = validate(d)
    if errs:
        print("\n".join(errs), file=sys.stderr)
        return 1
    LEDGER.write_text(json.dumps(d, indent=1, ensure_ascii=False) + "\n")
    print(f"recorded {a.id}: {' > '.join(order)}")
    if a.no_survey or not g.get("research"):
        return 0
    for s in g["seats"]:                                     # the surveys run at the moment a result is recorded
        c = s["controller"]
        if c["kind"] != "ai":
            continue
        if c["system"] == "Fusion":
            rc = subprocess.call([sys.executable, str(ROOT / "table" / "survey.py"), "--game", g["research"],
                                  "--seat", s["seat"], "--runs", "3"])
            print(f"survey {s['seat']} (Fusion): exit {rc}")
        else:                                                # a fresh-context model answers from this prompt
            out = ROOT / "table" / ".cache" / "research" / g["research"] / f"survey-{s['seat']}-PROMPT.txt"
            txt = subprocess.run([sys.executable, str(ROOT / "table" / "survey.py"), "--game", g["research"],
                                  "--seat", s["seat"], "--print-prompt"], capture_output=True, text=True).stdout
            out.write_text(txt)
            print(f"survey {s['seat']} ({c['system']}): prompt written to {out.relative_to(ROOT)} for a fresh context")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("board")
    f = sub.add_parser("finish")
    f.add_argument("--id", required=True)
    f.add_argument("--order", required=True, help="seats, winner first")
    f.add_argument("--how", default="")
    f.add_argument("--research", default="")
    f.add_argument("--no-survey", action="store_true")
    a = ap.parse_args()
    sys.exit({"check": cmd_check, "board": cmd_board, "finish": cmd_finish}[a.cmd](a))
