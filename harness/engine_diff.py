#!/usr/bin/env python3
"""A second opinion on the simple combat rules: the same generated combats through harness/engine.py and through the
table's own block resolution (table/ai_turn.py `resolve_block`), and a diff of what each says happened.

    python3 harness/engine_diff.py [--json]

One attacker, blocked by one creature or by none. Compared: the defender's life lost, whether the blocker dies, whether the
attacker dies. Keywords on the attacker: trample, deathtouch; on the blocker: deathtouch. (Lifelink is NOT compared: the table
applies it outside `resolve_block`; first strike is modelled by neither.) A divergence is "deathtouch" when removing the
deathtouch flags makes the two agree, otherwise "other". The two engines agree on the plain cases; the table's block
resolution is handed only the attacker's power and a trample flag, so it cannot see deathtouch at all: see KNOWN.
No server, no network, no model. This reads rules from two code bases; it does not decide which is right.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(REPO / "table"))


def engine_outcome(a: dict, b: dict | None, life: int = 40) -> dict:
    import cards, engine
    from agents import Result
    cards.CARDS["ZZ_A"] = {"kind": "creature", "cost": (1, 1), "pt": (a["p"], a["t"]), "kw": [k for k in ("trample", "deathtouch") if a.get(k)]}
    if b:
        cards.CARDS["ZZ_B"] = {"kind": "creature", "cost": (1, 1), "pt": (b["p"], b["t"]), "kw": ["deathtouch"] if b.get("deathtouch") else []}

    class Scripted:
        name = "scripted"

        def decide(self, game, p, kind, questions, context):
            out = {}
            for q in questions:
                keys = [k for k, _ in q["options"]]
                if kind == "attack":
                    out[q["id"]] = next(k for k in keys if k.startswith("attack "))
                else:
                    out[q["id"]] = next((k for k in keys if k.startswith("block ")), "no block")
            return Result(out)

    g = engine.Game([Scripted(), Scripted()], colours=("U", "B"), seed=0, log_state=False)
    g.battlefield.clear()
    atk = g.create("ZZ_A", 0, sick=False)
    blk = g.create("ZZ_B", 1, sick=False) if b else None
    g.players[1].life = life
    g.combat(g.players[0])
    return {"def_loss": life - g.players[1].life, "blocker_dead": bool(blk) and blk not in g.battlefield, "attacker_dead": atk not in g.battlefield}


def table_outcome(a: dict, b: dict | None, life: int = 40) -> dict:
    import oracle
    import player as P
    from ai_turn import resolve_block
    v = P.VirtualPlayer(str(REPO / "decks/kaust.json"), "T", seed=1)
    v.life = life
    blk = None
    if b:
        blk = P.Perm(card={"name": "ZZ_B", "power": str(b["p"]), "toughness": str(b["t"]), "types": ["Creature"], "keywords": ["Deathtouch"] if b.get("deathtouch") else []}, sick=False)
        v.battlefield.append(blk)
    real = oracle.card
    oracle.card = lambda name: {"toughness": a["t"]}              # the attacker's toughness, as the card database would give it
    try:
        r = resolve_block(v, blk, a["p"], bool(a.get("trample")), "ZZ_A")
    finally:
        oracle.card = real
    return {"def_loss": life - v.life, "blocker_dead": bool(blk) and blk not in v.battlefield, "attacker_dead": bool(r.get("attacker_died"))}


def cases():
    for ap, at, tr, dt in itertools.product(range(0, 7), range(1, 6), (False, True), (False, True)):
        a = {"p": ap, "t": at, "trample": tr, "deathtouch": dt}
        yield a, None
        for bp, bt, bd in itertools.product(range(0, 7), range(1, 7), (False, True)):
            yield a, {"p": bp, "t": bt, "deathtouch": bd}


def run() -> dict:
    total, diverge, by_cause, examples = 0, 0, {}, {}
    for a, b in cases():
        total += 1
        e, t = engine_outcome(a, b), table_outcome(a, b)
        if e == t:
            continue
        diverge += 1
        plain_a = {**a, "deathtouch": False}; plain_b = {**b, "deathtouch": False} if b else None
        if (a["deathtouch"] or (b and b["deathtouch"])) and engine_outcome(plain_a, plain_b) == table_outcome(plain_a, plain_b):
            cause = "attacker-deathtouch" if a["deathtouch"] and not (b and b["deathtouch"]) else \
                    "blocker-deathtouch" if (b and b["deathtouch"]) and not a["deathtouch"] else "both-deathtouch"
        else:
            cause = "other"
        by_cause[cause] = by_cause.get(cause, 0) + 1
        examples.setdefault(cause, {"attacker": a, "blocker": b, "engine": e, "table": t})
    return {"cases": total, "divergent": diverge, "by_cause": by_cause, "examples": examples}


KNOWN = {"attacker-deathtouch", "blocker-deathtouch", "both-deathtouch"}     # the table's block resolution has no deathtouch input


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--json", action="store_true"); a = ap.parse_args()
    r = run()
    if a.json:
        print(json.dumps(r, indent=1))
    else:
        print(f"engine_diff: {r['cases']} combats, {r['divergent']} divergent {r['by_cause']}")
        for c, ex in r["examples"].items():
            print(f"  {c}: attacker {ex['attacker']} blocker {ex['blocker']}\n     engine {ex['engine']}\n     table  {ex['table']}")
    sys.exit(1 if set(r["by_cause"]) - KNOWN else 0)


if __name__ == "__main__":
    main()
