"""Pack game logs into the self-contained replay viewer.

  python3 build_viewer.py games/paired-jev-s1000-seat2.json games/... -o viewer.html
"""
import argparse
import json
import os

ap = argparse.ArgumentParser()
ap.add_argument("games", nargs="+")
ap.add_argument("-o", "--out", default="viewer.html")
ap.add_argument("--footnote", default="Simplified rules: no stack or instants, 40-card decks, "
                "no mulligans. Card names are real; their rules text is cut down to what the engine models.")
args = ap.parse_args()

here = os.path.dirname(os.path.abspath(__file__))
games = []
for path in args.games:
    g = json.load(open(path))
    names = [f"Seat {i + 1} ({c.split(',')[0]})" for i, c in enumerate(g["commanders"])]
    model = [i for i, s in enumerate(g["seats"]) if s in ("jev", "djev", "so1")]
    who = f"{g['seats'][model[0]]} as {names[model[0]]}" if model else "no model seat"
    res = "won" if g["winner"] in model else "lost"
    games.append({
        "title": f"Seed {g['seed']}: {who}, {res}",
        "seed": g["seed"], "seats": g["seats"], "colours": g["colours"], "winner": g["winner"],
        "rounds": g["rounds"], "seatNames": names, "log": g["log"],
    })
payload = json.dumps({"games": games, "footnote": args.footnote}, separators=(",", ":"))
payload = payload.replace("</", "<\\/")                      # keep the JSON inside its <script>
html = open(os.path.join(here, "viewer-template.html")).read().replace("__GAMES__", payload)
open(os.path.join(here, args.out), "w").write(html)
print(f"wrote {args.out}: {len(games)} games, {len(html) / 1e6:.1f} MB")
