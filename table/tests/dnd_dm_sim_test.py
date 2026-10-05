"""D7: the AI DM runs the map, and the table keeps it honest. Three simulated encounters (cave, crypt, bridge) with
a fake DM that mixes good moves with bad ones — teleports, walls, off the map, other creatures' squares, players'
characters, out of turn, unknown locations, a mid-fight 'place'. After every reply the invariants must hold:

  • no move is accepted that isn't a legal walk within the creature's speed left this turn;
  • no token ever stands on a wall, off the map, or on another token;
  • the DM never moves a person's character, and in combat only the creature whose turn it is moves;
  • the DM's prompt carries what it needs to run the map (the grid, the locations, the bestiary).

    python3 table/tests/dnd_dm_sim_test.py          (no network: the DM is a script)
"""
from __future__ import annotations

import copy
import json
import os
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
os.environ.pop("DIVINCI_FUSION_API_KEY", None)
sys.argv = ["dnd_server.py", "--players", "Michael,Sam,Ana", "--port", "0"]
import dnd_server as D  # noqa: E402

PASS = FAIL = 0
rng = random.Random(20261004)


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    ok = bool(ok)
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {detail}"))


def table(d: dict) -> str:
    return "The DM narrates.\nTABLE: " + json.dumps(d)


def occupied_ok(m: dict) -> str | None:
    g, seen = D.MAP._grid(m), {}
    for t in m["tokens"].values():
        for sq in D.MAP.footprint(t["size"], t["x"], t["y"]):
            if not (0 <= sq[0] < g["w"] and 0 <= sq[1] < g["h"]):
                return f"{t['name']} off the map at {sq}"
            if sq in g["walls"]:
                return f"{t['name']} on a wall at {sq}"
            if sq in seen:
                return f"{t['name']} on {seen[sq]}'s square {sq}"
            seen[sq] = t["name"]
    return None


def bad_and_good_moves(m: dict, who: str) -> list[dict]:
    """A DM's reply on `who`'s turn: one plausible move for it, plus a handful of things it must not get away with."""
    g = D.MAP._grid(m)
    t = D.MAP.token_by_name(m, who)
    pcs = [x for x in m["tokens"].values() if x["owner"]]
    moves = []
    if t:
        near = [(t["x"] + dx, t["y"] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]
        rng.shuffle(near)
        moves.append({"name": who, "x": near[0][0], "y": near[0][1]})                  # maybe legal
        moves.append({"name": who, "x": g["w"] - 1 - t["x"], "y": g["h"] - 1 - t["y"]})  # across the map: a teleport
        wall = next(iter(g["walls"]), None)
        if wall:
            moves.append({"name": who, "x": wall[0], "y": wall[1]})                    # into a wall
        moves.append({"name": who, "x": g["w"] + 3, "y": 0})                           # off the map
        if pcs:
            moves.append({"name": who, "x": pcs[0]["x"], "y": pcs[0]["y"]})             # onto someone
    if pcs:
        p = rng.choice(pcs)
        moves.append({"name": p["name"], "x": p["x"] + 1, "y": p["y"]})                # a person's character
    others = [x for x in m["tokens"].values() if x["kind"] == "monster" and x["name"] != who]
    if others:
        o = rng.choice(others)
        moves.append({"name": o["name"], "x": o["x"] + 1, "y": o["y"]})                # out of turn
    rng.shuffle(moves)
    return moves


def encounter(loc: str, monsters: list[dict], rounds: int = 3) -> None:
    print(f"encounter: {loc}")
    D.new_game()
    D.apply_table_line(table({"location": loc, "monsters": monsters}))
    m = D.G["map"]
    check(f"{loc}: the DM's location and monsters are on the map",
          m["location"] == loc and all(D.MAP.token_by_name(m, x["name"]) for x in monsters), str(list(m["tokens"])))
    check(f"{loc}: everyone starts on legal squares", occupied_ok(m) is None, str(occupied_ok(m)))
    D.initiative_start()
    for h in D.HUMANS:
        D.initiative_enter(h, rng.randint(1, 20))
    D.G.pop("after_lock", None)
    ini = D.G["initiative"]
    accepted = rejected = 0
    problems = []
    for _ in range(rounds * len(ini["order"])):
        now = ini["order"][ini["turn"]]["name"]
        before = copy.deepcopy(m["tokens"])
        used_before = dict(m["used"])
        reply = {"move": bad_and_good_moves(m, now)}
        if rng.random() < 0.3:
            reply["place"] = [{"name": now, "x": 0, "y": 0}]                            # a mid-fight 'place'
        if rng.random() < 0.2:
            reply["location"] = rng.choice(["nowhere", "../../etc", 7])                 # not a location
        D.apply_table_line(table(reply))
        for tid, t in m["tokens"].items():
            b = before.get(tid)
            if not b or (b["x"], b["y"]) == (t["x"], t["y"]):
                continue
            if t["owner"]:
                problems.append(f"the DM moved {t['name']}, a person's character")
            if t["name"] != now:
                problems.append(f"{t['name']} moved on {now}'s turn")
            spent = m["used"].get(tid, 0) - used_before.get(tid, 0)
            ghost = copy.deepcopy(m)
            ghost["tokens"][tid]["x"], ghost["tokens"][tid]["y"] = b["x"], b["y"]
            ghost.pop("_grid", None)
            cost = D.MAP.walk_cost(ghost, tid, t["x"], t["y"])
            # A creature may split its movement into legs (5e), so what it's charged covers at least the shortest
            # legal walk from where it stood; a teleport is charged nothing and fails this.
            if cost is None or spent < cost or m["used"].get(tid, 0) > t["speed"]:
                problems.append(f"{t['name']} {b['x']},{b['y']}→{t['x']},{t['y']}: walk {cost} ft, charged {spent}, used {m['used'].get(tid)} of {t['speed']}")
            accepted += 1
        rejected += len(reply["move"]) - sum(1 for tid in m["tokens"] if before.get(tid) and
                                             (before[tid]["x"], before[tid]["y"]) != (m["tokens"][tid]["x"], m["tokens"][tid]["y"]))
        bad = occupied_ok(m)
        if bad:
            problems.append(bad)
        if m["location"] != loc:
            problems.append(f"the location changed to {m['location']}")
        ini["turn"] += 1                                                               # the table advances the turn
        if ini["turn"] >= len(ini["order"]):
            ini["turn"], ini["round"] = 0, ini["round"] + 1
        nxt = D.MAP.token_by_name(m, ini["order"][ini["turn"]]["name"])
        D.MAP.new_turn(m, nxt and nxt["id"])
    check(f"{loc}: {accepted} moves accepted, {rejected} refused — every accepted one a legal walk in its own turn",
          not problems, "; ".join(problems[:4]))
    check(f"{loc}: the fake DM tried bad moves and some good ones were accepted", rejected > 0 and accepted > 0,
          f"accepted {accepted}, rejected {rejected}")


print("the DM's prompt")
D.new_game()
prompt = D.build_prompt("Set the scene.", [])
st = json.loads(prompt.split("\n", 1)[0][len("STATE: "):])
check("the prompt's STATE carries the grid, the locations and the bestiary",
      st["map"]["grid"] and "cave" in st["map"]["locations"] and "Goblin" in st["map"]["bestiary"], str(st["map"])[:200])
check("…the tokens, marking which are people's characters", any(t.get("player") for t in st["map"]["tokens"]))
check("…and how to send location, place and move", all(k in prompt for k in ('"location"', '"place"', '"move"')))
check("…and that players' characters aren't the DM's to move", "never the players' characters" in prompt)

encounter("cave", [{"name": "Goblin 1"}, {"name": "Goblin 2"}, {"name": "Giant Spider 1"}])
encounter("crypt", [{"name": "Skeleton 1"}, {"name": "Skeleton 2"}, {"name": "Ghoul 1"}, {"name": "Zombie 1"}])
encounter("bridge", [{"name": "Bandit 1"}, {"name": "Bandit 2"}, {"name": "Ogre 1"}, {"name": "Wolf 1"}])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
