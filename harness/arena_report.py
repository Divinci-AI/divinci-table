#!/usr/bin/env python3
"""What the arena did: the morning report.   python3 harness/arena_report.py [--research-dir DIR] [--last-hours H]

Reads arena-games.jsonl (one record per game), arena-ledger.json and arena-run.log in the research folder. It reports
counts, not conclusions: with few games every rating interval is wide, and ratings.py says so. Aborted and capped games
are listed but never rated. Nothing here calls a model."""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RESEARCH = Path(os.environ.get("TABLE_RESEARCH_DIR") or REPO / "table" / ".cache" / "research")


def load(path: Path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def report(research: Path, last_hours: float = 0) -> str:
    games = load(research / "arena-games.jsonl")
    if last_hours:
        cutoff = time.time() - last_hours * 3600
        games = [g for g in games if _ts(g["id"]) >= cutoff]
    L = [f"arena report: {research}"]
    if not games:
        return "\n".join(L + ["  no arena games recorded"])
    status = Counter(g["status"] for g in games)
    L.append(f"  games: {len(games)}  " + "  ".join(f"{k} {v}" for k, v in sorted(status.items())))
    L.append(f"  wall time: {sum(g['wall_s'] for g in games) / 3600:.2f} h  median game {statistics.median(g['wall_s'] for g in games):.0f} s"
             f"  decisions {sum(g['decisions'] for g in games)}  talk lines {sum(g['talk_lines'] for g in games)}")
    j = [g["journals"] for g in games]
    sv = [g["surveys"] for g in games]
    L.append(f"  journals: {sum(x['ok'] for x in j)} answered of {sum(x['asked'] for x in j)} asked, "
             f"{sum(x['rejected'] for x in j)} rejected;  surveys: {sum(x['parsed'] for x in sv)} parsed of {sum(x['asked'] for x in sv)}")
    wins, played, falls, calls = Counter(), Counter(), Counter(), Counter()
    lat = defaultdict(list)
    for g in games:
        for lab, st in g["agents"].items():
            base = lab.split("#")[0]
            played[base] += 1
            falls[base] += st.get("fallbacks", 0) or 0
            calls[base] += st.get("calls", 0) or 0
            if st.get("ms"):
                lat[base].append(st["ms"])
    ledger = {g["id"]: g for g in (json.loads((research / "arena-ledger.json").read_text()).get("games", [])
                                   if (research / "arena-ledger.json").exists() else [])}
    for gid, g in ledger.items():
        if g["result"]["winner"]:
            wins[g["result"]["winner"].split("#")[0]] += 1
    L.append("  per seat identity (finished games only count for wins):")
    for lab in sorted(played):
        extra = ""
        if calls[lab]:
            extra = f"  model decisions {calls[lab]}, fell back to the heuristic {falls[lab]} ({falls[lab] / calls[lab]:.0%}), median {statistics.median(lat[lab]):.0f} ms"
        L.append(f"    {lab:<14} played {played[lab]:>4}  won {wins[lab]:>4}{extra}")
    aborted = [g for g in games if g["status"] == "aborted"]
    if aborted:
        L.append("  aborted: " + ", ".join(f"{g['id']} ({g['abort']})" for g in aborted[:5]))
    log = research / "arena-run.log"
    if log.exists():
        bad = [l for l in log.read_text().splitlines() if "CRASHED" in l or "stopping" in l or "STOP" in l]
        if bad:
            L.append("  from the run log: " + " | ".join(bad[-4:]))
    if (research / "arena-ledger.json").exists():
        r = subprocess.run([sys.executable, str(REPO / "table" / "ratings.py"), "--ledger", str(research / "arena-ledger.json")],
                           capture_output=True, text=True)
        L.append("\n" + r.stdout.strip())
    return "\n".join(L)


def _ts(gid: str) -> float:
    try:
        return time.mktime(time.strptime(gid.split("-")[1] + gid.split("-")[2], "%Y%m%d%H%M%S"))
    except Exception:                                    # noqa: BLE001
        return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--research-dir")
    ap.add_argument("--last-hours", type=float, default=0)
    a = ap.parse_args()
    print(report(Path(a.research_dir) if a.research_dir else RESEARCH, a.last_hours))
