"""Paired comparison: does agent X play a seat better than the heuristic would?

For every (seed, seat): game A puts X in that seat with three heuristic opponents; game B is
the SAME seed (same shuffles, same decks) with the heuristic in that seat. Deck/seat strength
is identical in A and B, so the only difference is who made that seat's decisions.

  TYPESAFE_API_KEY=… python3 paired.py --agent jev --seeds 5
  DJEV_URL=… DJEV_AUTH=gcloud python3 paired.py --agent djev --seeds 5
"""
import argparse
import json
import os
import time

from agents import HeuristicAgent, JevAgent, make_agent
from engine import Game

ap = argparse.ArgumentParser()
ap.add_argument("--agent", required=True)
ap.add_argument("--seeds", type=int, default=5)
ap.add_argument("--seed0", type=int, default=1000)
ap.add_argument("--auto-land", action="store_true", help="engine plays a land for every seat")
args = ap.parse_args()

here = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(here, "games"), exist_ok=True)
x = make_agent(args.agent, 0)
h = HeuristicAgent()
rows = []
for seed in range(args.seed0, args.seed0 + args.seeds):
    base = Game([h] * 4, seed=seed, log_state=False, auto_land=args.auto_land)
    bw = base.run()
    for seat in range(4):
        agents = [h] * 4
        agents[seat] = x
        g = Game(agents, seed=seed, auto_land=args.auto_land)
        t0 = time.time()
        w = g.run()
        p = g.players[seat]
        row = {"seed": seed, "seat": seat, "colour": p.colour, "auto_land": args.auto_land,
               "x_won": w == seat, "h_won": bw == seat,
               "x_elim_round": p.eliminated_turn, "h_elim_round": base.players[seat].eliminated_turn,
               "rounds": g.round, "secs": round(time.time() - t0, 1)}
        rows.append(row)
        with open(os.path.join(here, "games", f"paired-{args.agent}{'-autoland' if args.auto_land else ''}-s{seed}-seat{seat + 1}.json"), "w") as f:
            json.dump({"seed": seed, "seats": [a.name for a in agents], "winner": w, "rounds": g.round,
                       "colours": [q.colour for q in g.players],
                       "commanders": [q.commander for q in g.players], "log": g.log}, f)
        print(json.dumps(row), flush=True)

with open(os.path.join(here, f"paired-{args.agent}{'-autoland' if args.auto_land else ''}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"), "w") as f:
    f.writelines(json.dumps(r) + "\n" for r in rows)

n = len(rows)
xw = sum(r["x_won"] for r in rows)
hw = sum(r["h_won"] for r in rows)
only_x = sum(r["x_won"] and not r["h_won"] for r in rows)
only_h = sum(r["h_won"] and not r["x_won"] for r in rows)


def survival(r, k):
    return r[k] if r[k] is not None else 99


longer = sum(survival(r, "x_elim_round") > survival(r, "h_elim_round") for r in rows)
shorter = sum(survival(r, "x_elim_round") < survival(r, "h_elim_round") for r in rows)
print(f"\n{args.agent} vs heuristic, {n} paired seat-games")
print(f"  wins: {args.agent} {xw}/{n} vs heuristic {hw}/{n}  (discordant: {args.agent}-only {only_x}, heuristic-only {only_h})")
print(f"  survived longer: {args.agent} {longer}, heuristic {shorter}, same {n - longer - shorter}")
for c in "UBRG":
    rs = [r for r in rows if r["colour"] == c]
    print(f"  {c}: {args.agent} {sum(r['x_won'] for r in rs)}/{len(rs)}  heuristic {sum(r['h_won'] for r in rs)}/{len(rs)}")
if isinstance(x, JevAgent):
    ms = sorted(x.stats["ms"])
    print(f"  calls={x.stats['calls']} fallbacks={x.stats['fallbacks']} p50={round(ms[len(ms)//2])}ms "
          f"p95={round(ms[int(len(ms)*.95)])}ms input_tokens={x.stats['input_tokens']}")
