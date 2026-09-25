"""Play Commander games with a configurable seat line-up.

  python3 run.py --seats heuristic,random,heuristic,random --games 20        # offline
  TYPESAFE_API_KEY=… python3 run.py --seats jev,heuristic,heuristic,heuristic
  DJEV_URL=https://… DJEV_AUTH=gcloud python3 run.py --seats djev,jev,heuristic,heuristic

Writes games/<stamp>-seed<N>.json (full log incl. per-decision snapshots) per game and prints
a summary. Seats rotate turn order across games (--rotate) so no agent always goes first.
"""
import argparse
import json
import os
import statistics
import time
from collections import Counter

from agents import JevAgent, make_agent
from engine import Game

ap = argparse.ArgumentParser()
ap.add_argument("--seats", default="heuristic,random,heuristic,random")
ap.add_argument("--games", type=int, default=1)
ap.add_argument("--seed", type=int, default=1)
ap.add_argument("--round-cap", type=int, default=25)
ap.add_argument("--rotate", action="store_true", help="rotate agents across seats each game")
ap.add_argument("--no-save", action="store_true")
args = ap.parse_args()

specs = args.seats.split(",")
assert len(specs) == 4, "need exactly 4 seats"
here = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(here, "games"), exist_ok=True)
stamp = time.strftime("%Y%m%d-%H%M%S")

agents_by_spec = {}
wins, rounds, reasons = Counter(), [], Counter()
for g in range(args.games):
    order = specs[g % 4:] + specs[:g % 4] if args.rotate else specs
    seats = []
    for i, s in enumerate(order):
        # one agent object per spec so JevAgent stats aggregate across seats/games
        key = s if s in ("jev", "djev", "so1", "heuristic") else f"{s}{i}"
        if key not in agents_by_spec:
            agents_by_spec[key] = make_agent(s, args.seed + i)
        seats.append(agents_by_spec[key])
    game = Game(seats, seed=args.seed + g, round_cap=args.round_cap)
    t0 = time.time()
    w = game.run()
    rounds.append(game.round)
    wins[order[w] if w is not None else "draw"] += 1
    for p in game.players:
        if p.eliminated_reason:
            reasons[p.eliminated_reason.split(" from ")[0].split(" reached")[0]] += 1
    out = {
        "seed": args.seed + g, "seats": order, "winner": w, "rounds": game.round,
        "colours": [p.colour for p in game.players], "commanders": [p.commander for p in game.players],
        "eliminations": [{"seat": p.seat, "round": p.eliminated_turn, "reason": p.eliminated_reason}
                         for p in game.players if not p.alive],
        "log": game.log,
    }
    if not args.no_save:
        path = os.path.join(here, "games", f"{stamp}-seed{args.seed + g}.json")
        with open(path, "w") as f:
            json.dump(out, f)
    decisions = sum(1 for e in game.log if e["t"] == "decision")
    print(f"game {g + 1}: seats={order} winner={order[w] if w is not None else 'draw'} "
          f"(seat {w + 1 if w is not None else '-'}) rounds={game.round} decisions={decisions} "
          f"{time.time() - t0:.1f}s")

print("\nwins by agent:", dict(wins))
print("rounds: median", statistics.median(rounds), "range", min(rounds), "-", max(rounds))
print("elimination causes:", dict(reasons))
for k, a in agents_by_spec.items():
    if isinstance(a, JevAgent):
        ms = sorted(a.stats["ms"])
        p50 = ms[len(ms) // 2] if ms else None
        p95 = ms[int(len(ms) * 0.95)] if ms else None
        print(f"{k}: calls={a.stats['calls']} fallbacks={a.stats['fallbacks']} "
              f"p50={p50 and round(p50)}ms p95={p95 and round(p95)}ms input_tokens={a.stats['input_tokens']}")
