"""Hold'em table: the hand evaluator, side pots, the betting rules, chip conservation, hidden information, the fair
shuffle's commit and reveal, and snapshot/restore (in the engine and through a running server).

    /usr/bin/python3 table/tests/poker_test.py
    ~/.venvs/table/bin/python table/tests/poker_test.py
Stdlib only. No network: the server parts start their own tables on free ports in 9100-9899 (never a live one)."""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import poker_eval as PE  # noqa: E402

PASS = FAIL = 0
RESERVED = {8800, 8801, 8820, 8830, 8883}           # live tables: a test never touches these
NAMES = ["Ana", "Ben", "Cy", "Dee", "Eve", "Flo"]


def check(name: str, ok, detail: str = "") -> None:
    global PASS, FAIL
    ok = bool(ok)
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(("  ✅ " if ok else "  ❌ ") + name + ("" if ok else f" — {detail}"))


def refuses(fn, word: str) -> bool:
    try:
        fn()
    except PE.Illegal as e:
        return word.lower() in str(e).lower()
    return False


# ── helpers: a rigged deck, tables with chosen stacks ─────────────────────────────────────────
def rigged(holes: list, board: list) -> list:
    """A deck that deals `holes` (in deal order: first seat left of the button first) and then `board`."""
    n = len(holes)
    deck = [None] * 52
    for k, (a, b) in enumerate(holes):
        deck[k], deck[n + k] = a, b
    for pos, c in zip((2 * n + 1, 2 * n + 2, 2 * n + 3, 2 * n + 5, 2 * n + 7), board):
        deck[pos] = c
    rest = iter(c for c in PE.DECK if c not in deck)
    return [c if c else next(rest) for c in deck]


def table(stacks, blinds=(10, 20), button=0, holes=None, board=None) -> PE.Game:
    g = PE.Game([{"name": n} for n in NAMES[:len(stacks)]], chips=1000, blinds=blinds, seed=1)
    for s, c in zip(g.seats, stacks):
        s["chips"] = c
    g.button = (button - 1) % len(stacks)            # start_hand moves the button on by one seat
    if holes is not None:
        real = PE.deck_of
        PE.deck_of = lambda seed: rigged(holes, board)
        try:
            g.start_hand()
        finally:
            PE.deck_of = real
    else:
        g.start_hand()
    return g


def chips(g) -> dict:
    return {s["name"]: s["chips"] for s in g.seats}


# ── 1. the evaluator ──────────────────────────────────────────────────────────────────────────
print("hand evaluator")
E = lambda s: PE.evaluate(s.split())                 # noqa: E731
NAME = lambda s: PE.hand_name(E(s))                  # noqa: E731
for hand, want in [("As Ks Qs Js Ts 2d 3c", "Royal flush"), ("9h 8h 7h 6h 5h Ad Ac", "Straight flush, Nine high"),
                   ("Kd Kc Kh Ks 2d 3c 4h", "Four of a kind, Kings"), ("Kd Kc Kh 7s 7d 2c 3h", "Full house, Kings over Sevens"),
                   ("Ah 9h 7h 4h 2h Kd Qc", "Flush, Ace high"), ("9c 8d 7h 6s 5c Ad Kd", "Straight, Nine high"),
                   ("Qd Qc Qh 9s 7d 3c 2h", "Three of a kind, Queens"), ("Kd Kc 7h 7s 2d 3c 9h", "Two pair, Kings and Sevens"),
                   ("Jd Jc 9h 7s 2d 3c 4h", "Pair of Jacks"), ("Ad Jc 9h 7s 5d 3c 2h", "High card, Ace"),
                   ("6d 6c 6h 9s 9d 2c 3h", "Full house, Sixes over Nines")]:
    check(f"{hand} → {want}", NAME(hand) == want, NAME(hand))
cats = [E(h)[0] for h in ("2d 3c 5h 7s 9d Jc Kh", "2d 2c 5h 7s 9d Jc Kh", "2d 2c 5h 5s 9d Jc Kh", "2d 2c 2h 7s 9d Jc Kh",
                          "2d 3c 4h 5s 6d Jc Kh", "2d 4d 5d 7d 9d Jc Kh", "2d 2c 2h 7s 7d Jc Kh", "2d 2c 2h 2s 9d Jc Kh",
                          "2d 3d 4d 5d 6d Jc Kh")]
check("the nine categories rank in order (high card < … < straight flush)", cats == list(range(9)), str(cats))
check("the wheel A-2-3-4-5 is a straight, Five high", E("Ah 2d 3c 4s 5h Kd Qc") == (4, 5) and NAME("Ah 2d 3c 4s 5h Kd Qc") == "Straight, Five high")
check("the wheel is the lowest straight (below 2-6)", E("Ah 2d 3c 4s 5h") < E("2d 3c 4s 5h 6d"))
check("an ace-high straight beats a king-high one", E("Ah Kd Qc Js Th") > E("Kd Qc Js Th 9h"))
check("A-2-3-4-5 suited is a straight flush, Five high", NAME("Ah 2h 3h 4h 5h Kd Kc") == "Straight flush, Five high")
check("no wrap-around straight (Q-K-A-2-3)", E("Qh Kd Ac 2s 3h")[0] == 0)
check("kickers: K-K-A beats K-K-Q", E("Kd Kc Ah 7s 2d") > E("Kh Ks Qh 7d 2c"))
check("kickers: two pair K-K-7-7-A beats K-K-7-7-Q", E("Kd Kc 7h 7s Ad") > E("Kh Ks 7d 7c Qh"))
check("kickers: the fifth card decides high card", E("Ad Kc 9h 7s 5d") > E("Ah Kd 9c 7d 4s"))
check("kickers: flushes compare card by card", E("Ah Jh 9h 7h 3h") > E("Ad Jd 9d 7d 2d"))
check("kickers: quads use the best fifth card", E("Kd Kc Kh Ks 9d 3c 4h") > E("Kd Kc Kh Ks 8d 7c 4h"))
check("ties: the same ranks in other suits are equal", E("Ah Kd Qs Jc 9h") == E("Ad Kc Qh Js 9s"))
check("ties: a royal flush on the board is everyone's hand", E("2c 3d As Ks Qs Js Ts") == E("9h 8d As Ks Qs Js Ts"))
check("7 cards: three pairs play the top two and the best kicker", E("Kd Kc 7h 7s 5d 5c 2h") == (2, 13, 7, 5))
check("7 cards: two sets make a full house of the higher set", E("Kd Kc Kh 7s 7d 7c 2h") == (6, 13, 7))
check("7 cards: a flush beats the straight in the same seven", E("Ah 9h 7h 4h 2h 3c 5d")[0] == 5)
check("7 cards: a straight flush beats a higher plain straight", E("5h 6h 7h 8h 9h Td Jc") == (8, 9))
rng = random.Random(4)
bad = []
for _ in range(3000):
    seven = rng.sample(PE.DECK, 7)
    brute = max(PE.evaluate(list(c)) for c in combinations(seven, 5))
    if PE.evaluate(seven) != brute:
        bad.append(seven)
check("7 cards: the direct evaluator equals the best of all 21 five-card hands (3000 random deals)", not bad, str(bad[:2]))
b5 = PE.best_five("Kd Kc 7h 7s 5d 5c Ah".split())
check("best_five names the cards that play", sorted(b5) == sorted("Kd Kc 7h 7s Ah".split()), str(b5))

# ── 2. side pots and split pots ───────────────────────────────────────────────────────────────
print("side pots")
check("three different all-ins make a main pot and two layers",
      PE.side_pots({0: 100, 1: 300, 2: 500}, {0, 1, 2}) == [(300, [0, 1, 2]), (400, [1, 2]), (200, [2])],
      str(PE.side_pots({0: 100, 1: 300, 2: 500}, {0, 1, 2})))
check("a folded player's chips stay in the pot, but they can't win it",
      PE.side_pots({0: 50, 1: 200, 2: 200}, {1, 2}) == [(450, [1, 2])], str(PE.side_pots({0: 50, 1: 200, 2: 200}, {1, 2})))
check("chips folded above every live player join the pot below",
      PE.side_pots({0: 300, 1: 100, 2: 100}, {1, 2}) == [(500, [1, 2])], str(PE.side_pots({0: 300, 1: 100, 2: 100}, {1, 2})))
# three all-in with stacks 100 / 300 / 500 and the hands in that order of strength: button 0 = UTG three-handed
g = table([100, 300, 500], button=0, holes=[["Kd", "Kc"], ["Qd", "Qc"], ["Ad", "Ac"]], board=["2s", "7h", "9c", "4d", "Jh"])
# deal order: left of the button → seat 1 (Kd Kc), seat 2 (Qd Qc), seat 0 (Ad Ac). Seat 0 has the best hand.
total0 = g.total_chips()
g.act("Ana", "allin")
g.act("Ben", "allin")
g.act("Cy", "allin")
check("3-way all-in: the smallest stack wins the main pot (3 × 100)", g.seats[0]["chips"] == 300, str(chips(g)))
check("…the middle stack wins the side pot (2 × 200)", g.seats[1]["chips"] == 400, str(chips(g)))
check("…and the big stack's uncalled 200 comes back", g.seats[2]["chips"] == 200, str(chips(g)))
check("…every chip accounted for", g.total_chips() == total0 == 900)
pots = g.public()["result"]["pots"]
check("…the result names main pot, side pot and the returned chips", [p["label"] for p in pots] == ["main pot", "side pot 1", "returned"], str(pots))
check("…and every all-in hand is shown", all(s["cards"] for s in g.public()["seats"]), str([s["cards"] for s in g.public()["seats"]]))
# a split pot with an odd chip: blinds 5/10, the small blind folds (5 dead), two players tie on a board royal flush
g = table([500, 500, 500], blinds=(5, 10), button=0, holes=[["2d", "3c"], ["4d", "6c"], ["7d", "8c"]], board=["As", "Ks", "Qs", "Js", "Ts"])
g.act("Ana", "call")       # button, first to act three-handed
g.act("Ben", "fold")       # small blind: 5 dead
g.act("Cy", "check")       # big blind: pot 25
for _ in range(3):
    g.act("Cy", "check")
    g.act("Ana", "check")
check("a split pot: 25 between two players is 13 + 12", sorted([g.seats[0]["chips"], g.seats[2]["chips"]]) == [502, 503], str(chips(g)))
check("…the odd chip goes to the first winner left of the button (Cy, not the button)", g.seats[2]["chips"] == 503 and g.seats[0]["chips"] == 502, str(chips(g)))
g = table([500, 500], button=0, holes=[["2d", "3c"], ["4d", "6c"]], board=["As", "Ks", "Qs", "Js", "Ts"])
g.act("Ana", "call")
g.act("Ben", "check")
for _ in range(3):
    g.act("Ben", "check")
    g.act("Ana", "check")
check("an even split: both get their 20 back", chips(g) == {"Ana": 500, "Ben": 500}, str(chips(g)))

# ── 3. the betting rules ──────────────────────────────────────────────────────────────────────
print("betting")
g = table([1000, 1000], button=0)
check("heads-up: the button posts the small blind", g.seats[0]["bet"] == 10 and g.seats[1]["bet"] == 20)
check("heads-up: the button acts first before the flop", g.h["to_act"] == 0)
g.act("Ana", "call")
check("the big blind gets its option when everyone just calls", g.h["to_act"] == 1 and g.legal(1)["raise"])
g.act("Ben", "check")
check("heads-up: after the flop the big blind acts first", g.h["street"] == "flop" and g.h["to_act"] == 1)
check("out of turn is refused", refuses(lambda: g.act("Ana", "check"), "Ben's turn"))
check("a bet below the big blind is refused", refuses(lambda: g.act("Ben", "bet", 15), "smallest bet is 20"))
check("more than your stack is refused", refuses(lambda: g.act("Ben", "bet", 5000), "more than you have"))
check("'raise' with no bet to raise is refused", refuses(lambda: g.act("Ben", "raise", 60), "no bet to raise"))
check("an unknown action is refused", refuses(lambda: g.act("Ben", "dance"), "isn't an action"))
check("a fractional or wordy amount is refused", refuses(lambda: g.act("Ben", "bet", "lots"), "whole number"))
g.act("Ben", "bet", 50)
check("checking into a bet is refused", refuses(lambda: g.act("Ana", "check"), "to call"))
check("'bet' when there's already a bet is refused", refuses(lambda: g.act("Ana", "bet", 200), "raise instead"))
check("a raise smaller than the last bet is refused (min raise to 100)", refuses(lambda: g.act("Ana", "raise", 90), "smallest raise is to 100"))
g.act("Ana", "raise", 100)
check("the min-raise is the last full raise: after 50 → 100, the next is to 150", g.legal(1)["min_to"] == 150, str(g.legal(1)))
check("calling with nothing to call is refused", refuses(lambda: g.act("Ana", "call"), "turn") and g.h["to_act"] == 1)

g = table([1000, 1000, 1000], button=0)
g.act("Ana", "raise", 60)
check("preflop: a raise to 60 is a raise of 40", g.h["min_raise"] == 40 and g.legal(1)["min_to"] == 100)
check("a raise to 90 is below the minimum (to 100)", refuses(lambda: g.act("Ben", "raise", 90), "smallest raise is to 100"))
# a short all-in does not reopen the betting: Ben has 80 in total, Ana opened to 60
g = table([1000, 80, 1000], button=0)
g.act("Ana", "raise", 60)
g.act("Ben", "allin")                                 # to 80: only 20 more, less than the full raise of 40
check("a short all-in raises the bet (to 80) but not the minimum raise", g.h["cur"] == 80 and g.h["min_raise"] == 40)
check("…a player who hasn't acted yet may still raise (the big blind)", g.legal(2)["raise"] and g.legal(2)["min_to"] == 120)
g.act("Cy", "call")
check("…the opener, who already acted, may only call or fold", not g.legal(0)["raise"] and g.legal(0)["call"], str(g.legal(0)))
check("…a raise from the opener is refused", refuses(lambda: g.act("Ana", "raise", 200), "isn't reopened"))
check("…so is an all-in for more than the call", refuses(lambda: g.act("Ana", "allin"), "isn't reopened"))
g.act("Ana", "call")
check("…a call closes the round", g.h["street"] == "flop")
# two short all-ins that add up to a full raise do reopen it (the TDA rule)
g = table([1000, 75, 110, 1000], button=0)            # Ana button, Ben SB, Cy BB, Dee UTG
g.act("Dee", "raise", 60)
g.act("Ana", "call")
g.act("Ben", "allin")                                 # 75: +15
g.act("Cy", "allin")                                  # 110: +35 more; 110 - 60 = 50 ≥ 40 since Dee acted
check("short all-ins that add up to a full raise reopen it for the opener", g.legal(3)["raise"], str(g.legal(3)))
# zero chips sits out, and the game ends when one player has everything
g = table([20, 1000, 1000], button=0, holes=[["4d", "6c"], ["As", "Ad"], ["2d", "3c"]], board=["Ah", "Kd", "9c", "5s", "8h"])   # deal order Ben, Cy, Ana
g.act("Ana", "allin")                                 # all-in for exactly the big blind
g.act("Ben", "fold")
check("the big blind, with nobody left to bet against, isn't asked to act: the board runs out", g.h["street"] == "done")
check("a busted player has no chips", g.seats[0]["chips"] == 0, str(chips(g)))
g.start_hand()
check("…and sits out the next hand: no cards, not in it", not g.seats[0]["in_hand"] and not g.seats[0]["hole"] and g.public()["seats"][0]["out"])
check("…while the others play heads-up", len([s for s in g.seats if s["in_hand"]]) == 2)
g = table([40, 40], button=0, holes=[["2c", "7d"], ["As", "Ad"]], board=["Ah", "Kd", "9c", "5s", "8h"])   # deal order Ben, Ana
g.act("Ana", "allin")
g.act("Ben", "call")
check("the game is over when one player has every chip", g.over and g.winner == "Ana", f"{g.over} {g.winner} {chips(g)}")
check("…and no further hand can start", refuses(g.start_hand, "over"))
# a blind that puts a player all-in: they're still dealt in (two cards each, nobody gets theirs)
g = table([1000, 1000, 15], button=0)                 # Cy's big blind is all-in for 15
check("a player all-in on a blind is still dealt two cards, and nobody gets extra", [len(s["hole"]) for s in g.seats] == [2, 2, 2] and g.seats[2]["allin"],
      str([s["hole"] for s in g.seats]))
check("…and the bet to call is still the full big blind", g.h["cur"] == 20)
# the uncontested pot: nothing is shown
g = table([1000, 1000, 1000], button=0)
g.act("Ana", "raise", 60)
g.act("Ben", "fold")
g.act("Cy", "fold")
p = g.public()
check("an uncontested pot shows nobody's cards", all(s["cards"] is None for s in p["seats"]) and p["result"]["uncontested"])
check("…the winner may choose to show", g.show("Ana") == g.seats[0]["hole"] and g.public()["seats"][0]["cards"])
check("…a folded hand can never be shown", refuses(lambda: g.show("Ben"), "folded"))
# showdown order: without an all-in, a hand that can't win is mucked unseen
g = table([1000, 1000, 1000], button=0, holes=[["Kd", "Kc"], ["2d", "7c"], ["Ad", "Ac"]], board=["As", "9h", "5c", "3d", "Jh"])
for who in ("Ana", "Ben", "Cy"):
    g.act(who, "call" if who != "Cy" else "check")
for _ in range(3):
    g.act("Ben", "check"); g.act("Cy", "check"); g.act("Ana", "check")
shown = {s["name"]: s["cards"] for s in g.public()["seats"]}
check("showdown: the first hand shows, a worse one after it is mucked unseen", shown["Ben"] == ["Kd", "Kc"] and shown["Cy"] is None and shown["Ana"] == ["Ad", "Ac"],
      str(shown))

# ── 4. chip conservation over 200 seeded AI-vs-AI hands ───────────────────────────────────────
print("chip conservation")
hands = actions = 0
broken = []
seed = 0
while hands < 200:
    seed += 1
    g = PE.Game([{"name": n, "ai": True} for n in NAMES[:4 + seed % 3]], chips=500, blinds=(5, 10), seed=seed)
    start = g.total_chips()
    while hands < 200 and not g.over:
        g.start_hand()
        hands += 1
        while g.h["street"] != "done":
            i = g.h["to_act"]
            a, amt = g.ai_choice(i, 40)
            g.act(g.seats[i]["name"], a, amt)
            actions += 1
            if g.total_chips() != start or any(s["chips"] < 0 for s in g.seats):
                broken.append((seed, g.hand_no, g.total_chips()))
        if g.total_chips() != start:
            broken.append((seed, g.hand_no, "end", g.total_chips()))
check(f"stacks + pot are constant after every one of {actions} actions in 200 hands", not broken, str(broken[:3]))
g1 = PE.Game([{"name": n, "ai": True} for n in NAMES[:4]], chips=500, seed=99)
g2 = PE.Game([{"name": n, "ai": True} for n in NAMES[:4]], chips=500, seed=99)
for gg in (g1, g2):
    for _ in range(5):
        if gg.over:
            break
        gg.start_hand()
        while gg.h["street"] != "done":
            i = gg.h["to_act"]
            gg.act(gg.seats[i]["name"], *gg.ai_choice(i, 40))
check("--seed makes shuffles and the AI repeatable", g1.log == g2.log and chips(g1) == chips(g2))

# ── 5. hidden information and the commit, in the engine ───────────────────────────────────────
print("hidden information (engine)")


def leaks(text: str, cards) -> list:
    """Cards that appear in text as a card (not inside a longer word or a hex string)."""
    return [c for c in cards if re.search(rf"(?<![A-Za-z0-9]){c}(?![A-Za-z0-9])", text)]


ev = []
g = PE.Game([{"name": n} for n in NAMES[:4]], seed=5, emit=lambda kind, **kw: ev.append(dict(kind=kind, **kw)))
g.start_hand()
seed5 = g.history[1]["seed"]
holes = [c for s in g.seats for c in s["hole"]]
pub = json.dumps(g.public()) + json.dumps(ev)
check("public state and events hold no hole card during the hand", not leaks(pub, holes), str(leaks(pub, holes)))
check("…nor the seed, nor the deck order", seed5 not in pub and " ".join(g.h["deck"][:8]) not in pub)
check("the commit (SHA-256 of the seed) is published before any card is dealt",
      [e["kind"] for e in ev][:2] == ["hand", "deal"] and ev[0]["commit"] == hashlib.sha256(seed5.encode()).hexdigest())
check("the seed stays sealed while the hand is played", g.verify(1)[0] == 409 and "seed" not in g.verify(1)[1])
while g.h["street"] != "done":
    i = g.h["to_act"]
    g.act(g.seats[i]["name"], "fold" if g.legal(i).get("to_call") else "check")
check("…and after the hand while the game goes on", g.verify(1)[0] == 409 and "folded" in g.verify(1)[1]["error"] and g.public()["verifiable"] == [])
g.start_hand()
before = {s["name"]: s["chips"] + s["total"] for s in g.seats}
i = g.h["to_act"]; g.act(g.seats[i]["name"], "raise", g.legal(i)["min_to"])
g.end_game("Ana")
check("ending the game mid-hand hands every bet in it back", {s["name"]: s["chips"] for s in g.seats} == before and g.over,
      f"{before} {[s['chips'] for s in g.seats]}")
check("…and then every hand's seed can be checked", g.verify(1)[0] == 200 and g.verify(2)[0] == 200 and g.public()["verifiable"] == [1, 2])
check("…and no more hands are dealt", refuses(g.start_hand, "over"))

# ── 6. snapshot and restore in the engine ─────────────────────────────────────────────────────
print("snapshot/restore (engine)")
a = PE.Game([{"name": n, "ai": True} for n in NAMES[:4]], seed=21)
a.start_hand()
for _ in range(3):
    i = a.h["to_act"]
    a.act(a.seats[i]["name"], *a.ai_choice(i, 40))
blob = json.loads(json.dumps(a.export(), default=list))   # the room stores it as data: no live objects survive
blob["rng"] = tuple(tuple(x) if isinstance(x, list) else x for x in blob["rng"])
blob["ai_rng"] = {k: tuple(tuple(x) if isinstance(x, list) else x for x in v) for k, v in blob["ai_rng"].items()}
b = PE.Game.restore(blob)
check("a restored game is the same game, mid-hand", b.public() == a.public() and [s["hole"] for s in b.seats] == [s["hole"] for s in a.seats])
for gg in (a, b):
    for _ in range(40):
        if gg.over:
            break
        if gg.h["street"] == "done":
            gg.start_hand()
        i = gg.h["to_act"]
        if i is not None:
            gg.act(gg.seats[i]["name"], *gg.ai_choice(i, 40))
check("…and plays on exactly as the original would (same shuffles, same AI)", a.public() == b.public() and a.log == b.log)


# ── 7. the server: keys, hidden information over HTTP, the reveal, persistence ────────────────
print("the server")


def free_port() -> int:
    while True:
        p = random.randint(9100, 9899)
        if p in RESERVED:
            continue
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            s.close()


def spawn(token: str = "") -> tuple:
    port = free_port()
    env = dict(os.environ, TABLE_RESEARCH_DIR=tempfile.mkdtemp(prefix="poker-test-"), ROOM_TOKEN=token)
    proc = subprocess.Popen([sys.executable, str(HERE / "poker_server.py"), "--players", "Ana,Ben", "--ai", "ai:Leonardo,ai:Fusion",
                             "--seed", "11", "--pause", "60", "--ai-think", "0.02", "--ai-iters", "40", "--port", str(port)],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/api/poker", timeout=2).read()
            break
        except OSError:
            time.sleep(0.1)
    return proc, base


def call(base, path, body=None, headers=None, ua="phone-a", raw=False):
    req = urllib.request.Request(base + path, data=None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode()),
                                 headers={"Content-Type": "application/json", "User-Agent": ua, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
            return r.status, data if raw else json.loads(data or b"{}")
    except urllib.error.HTTPError as e:
        data = e.read()
        try:
            return e.code, data if raw else json.loads(data or b"{}")
        except ValueError:
            return e.code, {"raw": data[:200]}


def all_events(base) -> list:
    out, n = [], 0
    while True:
        d = call(base, f"/api/events?since={n}")[1]
        if not d["events"]:
            return out
        out += d["events"]
        n = d["events"][-1]["id"]


def my_deck(seed: str) -> list:                       # an independent rebuild: the published algorithm, written again here
    d = [r + s for r in "23456789TJQKA" for s in "shdc"]
    for i in range(51, 0, -1):
        j = int.from_bytes(hashlib.sha256(f"{seed}:{i}".encode()).digest(), "big") % (i + 1)
        d[i], d[j] = d[j], d[i]
    return d


def this_hand(text: str) -> list:
    """A public snapshot's parts, with the hand log cut to this hand: lines from the previous hand's showdown name its
    cards, and those cards can be dealt again in the next hand."""
    d = json.loads(text)
    log = d.pop("log", [])
    start = max([k for k, line in enumerate(log) if line.startswith(f"Hand {d['hand']}:")] or [0])
    return [("state", json.dumps(d)), ("log", json.dumps(log[start:])), ("earlier log", "")]


def dealt(deck, seats, button) -> tuple:
    n = len(seats)
    order = [(button + k) % n for k in range(1, n + 1) if seats[(button + k) % n]["in_hand"]]
    holes, k = {i: [] for i in order}, 0
    for _ in range(2):
        for i in order:
            holes[i].append(deck[k])
            k += 1
    return holes, [deck[k + 1], deck[k + 2], deck[k + 3], deck[k + 5], deck[k + 7]]


proc, base = spawn()
try:
    _, ana = call(base, "/api/seat/claim", {"name": "Ana"}, ua="phone-a")
    _, ben = call(base, "/api/seat/claim", {"name": "Ben"}, ua="phone-b")
    KEY = {"Ana": ana["key"], "Ben": ben["key"]}
    check("GET /api/game/id names the game", call(base, "/api/game/id")[1].get("game") == "poker")
    check("POST /api/seat/check knows a seat's key", call(base, "/api/seat/check", {}, {"X-Seat-Key": KEY["Ana"]})[1].get("seat") == "Ana")
    check("…and refuses an unknown one", call(base, "/api/seat/check", {}, {"X-Seat-Key": "nope"})[0] == 403)
    check("nobody can start without a seat key", call(base, "/api/poker/start", {"by": "Ana"})[0] == 403)
    call(base, "/api/poker/start", {"by": "Ana"}, {"X-Seat-Key": KEY["Ana"]})
    me = lambda who, key: call(base, "/api/poker/me?player=" + who, headers={"X-Seat-Key": key} if key else {})  # noqa: E731
    check("your own cards come with your key", me("Ana", KEY["Ana"])[0] == 200 and len(me("Ana", KEY["Ana"])[1]["hole"]) == 2)
    check("another seat's key can't read your cards (403)", me("Ana", KEY["Ben"])[0] == 403)
    check("no key can't read anyone's cards (403)", me("Ana", "")[0] == 403 and me("Ana", "x" * 20)[0] == 403)
    check("no key can't act (403)", call(base, "/api/poker/act", {"by": "Ana", "action": "fold"})[0] == 403)
    check("another seat's key can't act for you (403)", call(base, "/api/poker/act", {"by": "Ana", "action": "fold"}, {"X-Seat-Key": KEY["Ben"]})[0] == 403)
    check("the room snapshot (every card) needs the room token", call(base, "/api/room/snapshot", raw=True)[0] == 404)
    t0 = time.time()
    while time.time() - t0 < 10 and call(base, "/api/poker")[1].get("to_act_name") not in KEY:
        time.sleep(0.03)                                # a human to act: the other human can't become next meanwhile
    other = "Ben" if call(base, "/api/poker")[1].get("to_act_name") == "Ana" else "Ana"
    code, out = call(base, "/api/poker/act", {"by": other, "action": "call"}, {"X-Seat-Key": KEY[other]})
    check("out of turn is refused with a reason (409)", code == 409 and "turn" in out.get("error", ""), f"{code} {out.get('error')}")

    leaks_found, sealed, hands_seen, folded_seen, shown_seen, checked_me, commit_ok, finals = [], [], 0, 0, 0, [], [], {}
    sealed_after, per_hand = [], []
    for hand_no in range(1, 7):
        snaps, mine = [], {}
        t0 = time.time()
        while time.time() - t0 < 60:
            s = call(base, "/api/poker")[1]
            snaps.append((s["street"], json.dumps(s)))
            for who in KEY:
                c, m = me(who, KEY[who])
                if c == 200 and m["hole"]:
                    mine[who] = m["hole"]
            if s["street"] == "done":
                break
            if s.get("to_act_name") in KEY:
                who = s["to_act_name"]
                if not sealed or len(sealed) < hand_no:
                    v = call(base, f"/api/poker/verify?hand={s['hand']}")
                    sealed.append(v[0] == 409 and "seed" not in v[1])
                lg = me(who, KEY[who])[1]["legal"]
                call(base, "/api/poker/act", {"by": who, "action": "check" if lg.get("check") else "call"}, {"X-Seat-Key": KEY[who]})
            else:
                time.sleep(0.03)
        final = call(base, "/api/poker")[1]
        finals[final["hand"]] = final
        vc, vo = call(base, f"/api/poker/verify?hand={final['hand']}")
        sealed_after.append(vc == 409 and "seed" not in vo and not final["verifiable"])
        per_hand.append((final, mine, snaps))
        hands_seen += 1
        call(base, "/api/poker/next", {"by": "Ana"}, {"X-Seat-Key": KEY["Ana"]})
    code, _ = call(base, "/api/poker/end", {"by": "Ana"})
    check("ending the game needs the seat's key (403)", code == 403, str(code))
    code, out = call(base, "/api/poker/end", {"by": "Ana"}, {"X-Seat-Key": KEY["Ana"]})
    check("a player ends the game", code == 200 and out["over"] and len(out["verifiable"]) >= 6, f"{code} {out.get('error')}")
    check("the chips are all still there after a called-off hand", sum(x["chips"] for x in out["seats"]) == 1000 * len(out["seats"]),
          str([x["chips"] for x in out["seats"]]))
    for final, mine, snaps in per_hand:
        v = call(base, f"/api/poker/verify?hand={final['hand']}")[1]
        deck = my_deck(v["seed"])
        holes, board = dealt(deck, final["seats"], final["button"])
        commit_ok.append(hashlib.sha256(v["seed"].encode()).hexdigest() == v["commit"] == final["commit"] and deck == v["deck"]
                         and final["board"] == board[:len(final["board"])])
        for who, h in mine.items():
            checked_me.append(holes[[x["name"] for x in final["seats"]].index(who)] == h)
        every = [c for h in holes.values() for c in h]
        for street, text in snaps[:-1]:
            if street != "done":
                leaks_found += [f"{c} in {where}" for where, t in this_hand(text) for c in leaks(t, every)] + ([v["seed"]] if v["seed"] in text else [])
        shown = {i: x["cards"] for i, x in enumerate(final["seats"]) if x["cards"]}
        hidden = [c for i, h in holes.items() if i not in shown for c in h]
        leaks_found += [f"{c} in {where} (end of hand {final['hand']})" for where, t in this_hand(json.dumps(final)) for c in leaks(t, hidden)]
        for i, x in enumerate(final["seats"]):
            folded_seen += x["folded"]
            shown_seen += bool(x["cards"])
            if x["folded"] and x["cards"]:
                leaks_found.append(f"folded seat {x['name']} shown")
            if x["cards"] and x["cards"] != holes[i]:
                leaks_found.append(f"{x['name']} shown wrongly")
    evs = all_events(base)
    check(f"{hands_seen} hands played through the server", hands_seen == 6)
    check(f"public state never showed a hidden card or a seed before its hand ended ({folded_seen} folds, {shown_seen} hands shown)",
          not leaks_found and shown_seen > 0 and folded_seen > 0, str(leaks_found[:5]))
    ev_leaks = []
    by_hand: dict = {}
    for e in evs:
        if e["type"] == "poker" and "hand" in e:
            by_hand.setdefault(e["hand"], []).append(e)
    for no, es in by_hand.items():
        v = call(base, f"/api/poker/verify?hand={no}")[1]
        if "seed" not in v:
            continue
        res = next((e for e in es if e["kind"] == "result"), None)
        before = [e for e in es if res is None or e["id"] < res["id"]]
        deckn = my_deck(v["seed"])
        hand_ev = next(e for e in es if e["kind"] == "hand")
        if no not in finals:
            continue
        dealt_holes, _ = dealt(deckn, finals[no]["seats"], finals[no]["button"])
        ev_leaks += leaks(json.dumps(before), [c for h in dealt_holes.values() for c in h])
        if res:
            for name, cs in res["shown"].items():
                if any(e["kind"] == "act" and e["by"] == name and e["action"] == "fold" for e in es):
                    ev_leaks.append(f"folded {name} shown in hand {no}")
        if hand_ev["id"] > min(e["id"] for e in es if e["kind"] == "deal"):
            ev_leaks.append(f"hand {no}: deal before commit")
    check("events never carried a hole card before the hand's result, nor a folded hand after", not ev_leaks, str(ev_leaks[:5]))
    check("each hand's commit came before its deal, and the seed stayed sealed until the hand ended", all(sealed) and sealed, str(sealed))
    check("a finished hand's seed stays sealed while the game goes on (it would show the folded cards)", all(sealed_after) and len(sealed_after) == 6, str(sealed_after))
    check("at the end of the game: SHA-256(seed) = the commit, and the seed rebuilds the deck that was dealt", all(commit_ok) and len(commit_ok) == 6, str(commit_ok))
    check("…the players' own cards are exactly what that deck dealt them", all(checked_me) and len(checked_me) >= 6, str(checked_me))
    chat = [e for e in evs if e["type"] == "chat" and e.get("ai")]
    check("the AI's table talk never names a card", not leaks(json.dumps(chat), PE.DECK), str(leaks(json.dumps(chat), PE.DECK)))
    code, out = call(base, "/api/poker/act", {"by": "Ana", "action": "raise", "amount": 7}, {"X-Seat-Key": KEY["Ana"]})
    check("an illegal action comes back with a reason", code in (400, 409) and out.get("error"), f"{code} {out.get('error')}")
finally:
    proc.terminate()
    proc.wait(5)

# persistence: snapshot mid-hand on one process, restore on another, carry on
tok = "t-" + str(random.random())
p1, b1 = spawn(tok)
p2, b2 = spawn(tok)
try:
    _, ana = call(b1, "/api/seat/claim", {"name": "Ana"}, ua="phone-a")
    _, ben = call(b1, "/api/seat/claim", {"name": "Ben"}, ua="phone-b")
    KEY = {"Ana": ana["key"], "Ben": ben["key"]}
    call(b1, "/api/poker/start", {"by": "Ana"}, {"X-Seat-Key": KEY["Ana"]})
    s, t0 = {}, time.time()
    while time.time() - t0 < 30:                        # until a human is to act after the flop (nothing moves meanwhile)
        s = call(b1, "/api/poker")[1]
        if s["street"] == "done":
            call(b1, "/api/poker/next", {"by": "Ana"}, {"X-Seat-Key": KEY["Ana"]})
            continue
        who = s.get("to_act_name")
        if who in KEY and s["street"] != "preflop":
            break
        if who in KEY:
            lg = call(b1, "/api/poker/me?player=" + who, headers={"X-Seat-Key": KEY[who]})[1]["legal"]
            call(b1, "/api/poker/act", {"by": who, "action": "check" if lg.get("check") else "call"}, {"X-Seat-Key": KEY[who]})
        time.sleep(0.05)
    code, blob = call(b1, "/api/room/snapshot", headers={"X-Room-Token": tok}, raw=True)
    check("the room token reads a snapshot mid-hand", code == 200 and len(blob) > 100, str(code))
    code, out = call(b2, "/api/room/restore", blob, headers={"X-Room-Token": tok, "Content-Type": "application/octet-stream"})
    check("another process restores it", code == 200, f"{code} {out}")
    strip = lambda d: {k: v for k, v in d.items() if k not in ("rev", "next_hand_at")}  # noqa: E731
    s1, s2 = call(b1, "/api/poker")[1], call(b2, "/api/poker")[1]
    check("…the same hand, board, stacks and turn", strip(s1) == strip(s2) and s2["street"] not in ("preflop", "done"),
          f"{s1.get('street')} {s2.get('street')}")
    who = s2.get("to_act_name")
    m1 = call(b1, "/api/poker/me?player=Ana", headers={"X-Seat-Key": KEY["Ana"]})[1]
    m2 = call(b2, "/api/poker/me?player=Ana", headers={"X-Seat-Key": KEY["Ana"]})
    check("…the seat keys came along: Ana's key reads the same two cards there", m2[0] == 200 and m2[1]["hole"] == m1["hole"], str(m2[0]))
    leg = call(b2, "/api/poker/me?player=" + who, headers={"X-Seat-Key": KEY[who]})[1]["legal"]
    code, out = call(b2, "/api/poker/act", {"by": who, "action": "check" if leg.get("check") else "call"}, {"X-Seat-Key": KEY[who]})
    check("…and the hand plays on there", code == 200, f"{code} {out.get('error')}")
    check("a process that holds a game won't be overwritten", call(b2, "/api/room/restore", blob, headers={"X-Room-Token": tok})[0] == 409)
finally:
    for pr in (p1, p2):
        pr.terminate()
        pr.wait(5)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
