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


def zone_encounter(rounds: int = 120) -> None:
    """Theater of the mind (docs/THEATER-GOAL.md T1): a fake DM sends random zone lines, good and bad, mid-fight
    and between fights. After every line: nobody stands in a zone that doesn't exist, the DM never moved a
    person's character, in combat only the creature whose turn it is moved and by at most 2 zones (a Dash), no
    zones were replaced mid-fight, and engaged pairs share a zone."""
    print("zones: a randomized fake DM")
    D.new_game()
    G = D.G
    zones = [{"name": n} for n in ("the gate", "the yard", "the well", "the stair", "the roof")]
    D.apply_table_line("x\nTABLE: " + json.dumps({"zones": zones, "monsters": [{"name": "Orc 1"}, {"name": "Orc 2"}, {"name": "Wolf 1"}]}))
    people = list(D.HUMANS)
    names = list(G["card"]["where"]) + ["Nobody"]
    problems, accepted, refused = [], 0, 0
    for r in range(rounds):
        fight = r % 40 >= 10                                       # some lines between fights, most in one
        if fight and not G["initiative"]["active"]:
            order = [{"name": n, "total": rng.randint(1, 20), "dex": 10} for n in G["card"]["where"]]
            G["initiative"] = {"active": True, "pending": [], "turn": 0, "round": 1, "order": order}
            D.ZONES.new_turn(G["card"], order[0]["name"])
        if not fight and G["initiative"]["active"]:
            G["initiative"] = {"active": False, "order": [], "turn": 0, "round": 1, "pending": []}
        before = dict(G["card"]["where"]); before_zones = [z["name"] for z in G["card"]["zones"]]
        now = D.turn_name()
        line = {}
        k = rng.random()
        if k < 0.15:
            line["zones"] = rng.sample(zones, rng.randint(2, 5)) + ([{"name": "the void"}] if rng.random() < .3 else [])
        if k >= 0.1:
            line["zone"] = {rng.choice(names): rng.choice([z["name"] for z in zones] + ["the moon"])
                            for _ in range(rng.randint(1, 3))}
            if rng.random() < .3:
                w = rng.choice(names); line["zone"][w] = {"to": rng.choice([z["name"] for z in zones]), "dash": True}
        if rng.random() < .3:
            line["engage"] = [[rng.choice(names), rng.choice(names)]]
        D.apply_table_line("x\nTABLE: " + json.dumps(line))
        c, after = G["card"], G["card"]["where"]
        names_now = {z["name"] for z in c["zones"]}
        for who, z in after.items():
            if z not in names_now:
                problems.append(f"{who} in a missing zone {z}")
        for who in people:
            if who in before and before[who] != after.get(who) and (not line.get("zones") or after.get(who) in {x["name"] for x in zones}) \
                    and not line.get("zones"):
                problems.append(f"the DM moved {who}")
        if now:
            if [z["name"] for z in c["zones"]] != before_zones:
                problems.append("zones replaced mid-fight")
            for who in after:
                if who in before and before[who] != after[who]:
                    if who != now:
                        problems.append(f"{who} moved out of turn")
                    elif (D.ZONES.hops(c, before[who], after[who]) or 0) > 2:
                        problems.append(f"{who} moved {D.ZONES.hops(c, before[who], after[who])} zones")
        for a, b in c["engaged"]:
            if after.get(a) != after.get(b):
                problems.append(f"{a} and {b} engaged across zones")
        moved = sum(1 for w in after if w in before and before[w] != after[w])
        accepted += moved
        refused += len(line.get("zone", {})) - moved
        if now and rng.random() < .5:                              # the turn moves on
            ini = G["initiative"]
            ini["turn"] = (ini["turn"] + 1) % len(ini["order"])
            D.ZONES.new_turn(c, ini["order"][ini["turn"]]["name"])
    check(f"zones: {accepted} moves accepted, {refused} refused over {rounds} DM lines, every rule held",
          not problems and accepted > 0 and refused > 0, "; ".join(problems[:4]) + f" (accepted {accepted}, refused {refused})")


zone_encounter()

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
