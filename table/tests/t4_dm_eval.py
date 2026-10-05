"""T4 (docs/THEATER-GOAL.md): the local DM, measured on ten fixed theater-of-the-mind scenes.

Each scene sets a real table state (zones, who's where, a fight or not, recent lines) and a player's line, and the
prompt is the one the table really sends (dnd_server.build_prompt, theater mode). Each model variant answers every
scene, streamed; the script records time to the first finished sentence and the whole reply, and checks the rules
the table can check by itself: did the DM roll dice, move or set a player's hit points, or try to move a person's
character in its TABLE line (the table drops that, but trying is a fault). Vividness and consistency are judged by a
person, blind: it writes a scoring sheet with the replies shuffled and the models hidden.

    ~/.venvs/table/bin/python table/tests/t4_dm_eval.py gemma4:e2b gemma4:e2b+think [out_dir]
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
os.environ.pop("DIVINCI_FUSION_API_KEY", None)
ARGS = sys.argv[1:]                               # read before the server import replaces argv
sys.argv = ["dnd_server.py", "--players", "Ana,Ben", "--companions", "ai:Leonardo:chatty:wizard", "--mode", "theater", "--port", "0"]
import dnd_server as D  # noqa: E402

MODELS = [a for a in ARGS if not a.startswith("/")] or ["gemma4:e2b"]
OUT = Path(next((a for a in ARGS if a.startswith("/")), str(HERE.parent / "docs" / "results" / "t4")))

TAVERN = [{"name": "the bar", "desc": "a long oak counter", "cover": "half"}, {"name": "the hearth", "desc": "a roaring fire"},
          {"name": "the door", "desc": "the way out to the rain"}]
ROAD = [{"name": "the cart", "desc": "an overturned wagon", "cover": "three-quarters"}, {"name": "the road"},
        {"name": "the treeline", "desc": "dark pines", "cover": "half"}]
CAVE = [{"name": "the mouth"}, {"name": "the pool", "desc": "black, still water"}, {"name": "the ledge", "desc": "a high shelf of rock", "cover": "half"}]
SCENES = [
    ("tavern talk", TAVERN, [], {"Ana": "the bar", "Ben": "the hearth", "Leonardo": "the bar"}, None,
     ["DM: The barkeep, a heavy man with a scarred lip, polishes a mug and watches you."],
     "Ana", "I lean on the bar and ask the barkeep if he's heard anything about the missing caravan."),
    ("an ambush", ROAD, [{"name": "Goblin 1"}, {"name": "Goblin 2"}], {"Ana": "the road", "Ben": "the road", "Leonardo": "the cart",
     "Goblin 1": "the treeline", "Goblin 2": "the treeline"}, ("Goblin 1", ["Goblin 1", "Ana", "Ben", "Goblin 2"]),
     ["DM: Arrows hiss out of the pines.", "ROLL Ben: d20 = [12]+3 = 15 (initiative)"],
     None, "It is Goblin 1's turn. Take it."),
    ("a chase", ROAD, [{"name": "Bandit 1"}], {"Ana": "the cart", "Ben": "the cart", "Leonardo": "the cart", "Bandit 1": "the treeline"}, None,
     ["DM: The bandit snatches Ben's purse and bolts for the trees."], "Ben", "I sprint after him and yell for Ana to cut him off."),
    ("a puzzle door", CAVE, [], {"Ana": "the ledge", "Ben": "the ledge", "Leonardo": "the ledge"}, None,
     ["DM: A stone door blocks the ledge. Three carved hands, one palm up, two palms down."], "Ana", "I press the palm-up hand."),
    ("a death save", CAVE, [{"name": "Bugbear"}], {"Ana": "the pool", "Ben": "the ledge", "Leonardo": "the mouth", "Bugbear": "the pool"},
     ("Ana", ["Bugbear", "Ana", "Ben", "Leonardo"]), ["TABLE: Ana: 0/28 HP — down!", "DM: The bugbear's morningstar drops Ana into the shallows."],
     "Ana", "I'm down. Do I roll a death save?"),
    ("a stealth approach", CAVE, [{"name": "Goblin 1"}], {"Ana": "the mouth", "Ben": "the mouth", "Leonardo": "the mouth", "Goblin 1": "the ledge"},
     None, ["DM: A goblin sentry dozes on the ledge above the pool."], "Ben", "I want to sneak along the pool to get under the ledge."),
    ("a persuasion", TAVERN, [{"name": "Bandit 1"}], {"Ana": "the door", "Ben": "the door", "Leonardo": "the door", "Bandit 1": "the door"},
     None, ["DM: A bandit blocks the doorway, hand on his knife."], "Ana", "Easy, friend. We're not looking for trouble. Let us pass and keep the silver."),
    ("an opportunity attack", TAVERN, [{"name": "Bandit 1"}], {"Ana": "the bar", "Ben": "the hearth", "Leonardo": "the bar", "Bandit 1": "the bar"},
     ("Ana", ["Ana", "Bandit 1", "Ben", "Leonardo"]), ["DM: The bandit and Ana trade blows at the bar.", "TABLE: Ana and Bandit 1 are fighting"],
     "Ana", "I break away and run to the door."),
    ("a companion scene", CAVE, [], {"Ana": "the mouth", "Ben": "the mouth", "Leonardo": "the pool"}, None,
     ["DM: Leonardo kneels by the black water, muttering."], "Ben", "Leonardo, what do you see in the water?"),
    ("an unclear action", ROAD, [{"name": "Wolf"}], {"Ana": "the road", "Ben": "the cart", "Leonardo": "the cart", "Wolf": "the treeline"}, None,
     ["DM: A wolf watches from the treeline, ears flat."], "Ana", "I do the thing."),
]


def stream(model: str, prompt: str) -> tuple[str, float | None, float]:
    m, think = model.removesuffix("+think"), model.endswith("+think")
    body = json.dumps({"model": m, "stream": True, "think": think, "keep_alive": "30m", "options": {"temperature": 0.8},
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    t0, first, text = time.time(), None, ""
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        for line in r:
            d = json.loads(line)
            text += (d.get("message") or {}).get("content") or ""
            if first is None and re.search(r"[.!?…][\"'”’)]*\s", text):
                first = time.time() - t0
            if d.get("done"):
                break
    return text, first, time.time() - t0


def rule_faults(reply: str, scene: str = "") -> list[str]:
    """What the table can check by itself: the DM rolling, setting players' numbers, speaking for a player, moving a
    person in TABLE, echoing the state back, and (in the death-save scene) not asking for the death saving throw.
    Added after reading the first run's replies: the metric-only version missed the last three."""
    out, body = [], re.split(r"^\s*TABLE\s*:", reply, flags=re.M)[0]
    if re.search(r"^\s*\**(Ana|Ben)\**\s*:", body, re.M):
        out.append("spoke for a player")
    if scene == "a death save" and not re.search(r"death sav", body, re.I):
        out.append("didn't ask for a death saving throw")
    if re.search(r'"(party|initiative|scene_card)"\s*:', reply):
        out.append("echoed the state into its TABLE line")
    if re.search(r"\b(roll(s|ed)? (a|an) \d+|rolls? (a )?(natural )?\d+|\[\d+\]|d20 ?= ?\d+)", body, re.I):
        out.append("rolled dice itself")
    if re.search(r"\b(Ana|Ben)\b[^.]{0,40}\b(takes?|loses?|suffers?) \d+ (points of )?damage", body):
        out.append("set a player's damage")
    m = re.search(r"^\s*TABLE\s*:(.*)$", reply, re.M | re.S)
    if m:
        try:
            d = json.loads(m.group(1).strip())
            z = d.get("zone") if isinstance(d, dict) else None
            if isinstance(z, dict) and any(k in ("Ana", "Ben") for k in z):
                out.append("tried to move a player's character")
        except ValueError:
            out.append("malformed TABLE line")
    return out


rows = []
for model in MODELS:
    print(f"\n{model}")
    for (label, zones, monsters, where, fight, recent, who, line) in SCENES:
        D.new_game()
        D.EVENTS.items.clear()                    # each scene's RECENT is its own (new_game keeps the event log)
        G = D.G
        G["monsters"] = [{"name": m["name"], "ac": 13, "hp": 11, "max_hp": 11, "size": "medium", "speed": 30} for m in monsters]
        D.ZONES.set_zones(G["card"], zones)
        for c, z in where.items():
            D.ZONES.place(G["card"], c, z)
        if fight:
            now, order = fight
            G["initiative"] = {"active": True, "pending": [], "round": 1, "turn": order.index(now),
                               "order": [{"name": n, "total": 20 - i, "dex": 10} for i, n in enumerate(order)]}
        if label == "a death save":
            G["sheets"]["Ana"]["hp"] = 0
        if label == "an opportunity attack":
            D.ZONES.engage(G["card"], "Ana", "Bandit 1")
        for r in recent:
            D.EVENTS.emit("dm", text=r[4:]) if r.startswith("DM: ") else D.EVENTS.emit("roll", text=r)
        if who:
            D.EVENTS.emit("say", by=who, text=line, ic=True)
        prompt = D.build_prompt(f"{who} acts or speaks (latest line in RECENT)." if who else line, ["Leonardo"] if "Leonardo," in (line or "") else [])
        reply, first, total = stream(model, prompt)
        faults = rule_faults(reply, label)
        spoken = D.plain(re.split(r"^\s*TABLE\s*:", reply, flags=re.M)[0]).strip()
        rows.append({"model": model, "scene": label, "player": who, "line": line, "first_sentence_s": first and round(first, 2),
                     "total_s": round(total, 2), "faults": faults, "reply": reply, "spoken": spoken})
        print(f"  {label:22} first {first and round(first, 2)} s · total {total:.1f} s · faults {faults or '-'} · {spoken[:70]!r}")

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "replies.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))
rng = random.Random(20261005)
blind = [{"scene": r["scene"], "line": r["line"] or "(the DM's own turn)", "spoken": r["spoken"]} for r in rows]
order = list(range(len(blind)))
rng.shuffle(order)
key = {f"R{i + 1:02d}": rows[j]["model"] for i, j in enumerate(order)}
(OUT / "blind-key.json").write_text(json.dumps(key, indent=1))
cards = "".join(f"""<section><h3>{k} · {blind[j]['scene']}</h3><p class=line>Player: {blind[j]['line']}</p><p>{blind[j]['spoken']}</p>
<div class=score data-id="{k}">Vivid <select name=vivid><option></option>{''.join(f'<option>{n}</option>' for n in range(1, 6))}</select>
 Consistent with the scene <select name=consistent><option></option><option>yes</option><option>no</option></select>
 Would you keep playing? <select name=keep><option></option><option>yes</option><option>no</option></select></div></section>"""
                for k, j in zip(key, order))
(OUT / "score.html").write_text(f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>T4 blind scoring</title><style>body{{font:16px/1.5 Georgia,serif;max-width:760px;margin:24px auto;padding:0 16px;background:#fbf8f2;color:#222}}
section{{border-top:1px solid #ddd;padding:10px 0}} .line{{color:#666;font-style:italic}} select{{margin-right:10px}} button{{font-size:16px;padding:8px 14px}}</style>
<h1>T4: the local DM, scored blind</h1><p>Each reply is a DM's answer to the player's line. The model behind each is hidden (the key is in
blind-key.json; don't open it until you're done). Score vividness 1–5, whether it stays consistent with the scene, and whether you'd keep playing.
Your scores stay in this browser; <b>Export</b> downloads them.</p>{cards}
<p><button id=exp>Export scores</button></p>
<script>const K="t4-scores";const S=JSON.parse(localStorage.getItem(K)||"{{}}");
document.querySelectorAll(".score").forEach(d=>{{d.querySelectorAll("select").forEach(s=>{{s.value=(S[d.dataset.id]||{{}})[s.name]||"";
s.onchange=()=>{{(S[d.dataset.id]||=({{}}))[s.name]=s.value;localStorage.setItem(K,JSON.stringify(S));}};}});}});
exp.onclick=()=>{{const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([JSON.stringify(S,null,1)],{{type:"application/json"}}));a.download="t4-scores.json";a.click();}};</script>""")

print(f"\nwrote {OUT}/replies.json, score.html (blind), blind-key.json")
for model in MODELS:
    rs = [r for r in rows if r["model"] == model]
    fs = sorted(r["first_sentence_s"] for r in rs if r["first_sentence_s"] is not None)
    print(f"{model}: first sentence median {fs[len(fs) // 2] if fs else None} s (max {max(fs) if fs else None}); "
          f"rule faults in {sum(bool(r['faults']) for r in rs)}/{len(rs)} replies: {[f for r in rs for f in r['faults']]}")
