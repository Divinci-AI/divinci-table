"""Texas Hold'em for the Divinci Table: cards, the 7-card evaluator, side pots, one hand's betting, the AI.

Pure stdlib and no I/O, so tests drive it directly; poker_server.py adds seats, HTTP and timing. Play chips only.

    g = Game([{"name": "Ana"}, {"name": "Leonardo", "ai": True}], chips=1000, blinds=(10, 20), seed=7)
    g.start_hand(); g.act("Ana", "call"); ...

Hidden information lives in two places only: each seat's "hole" and the hand's deck/seed. public() never copies
either, except hole cards a showdown (or the player) revealed. The shuffle is committed before the deal and its
seed revealed when the hand is over (fair.py's Fisher-Yates), so anyone can rebuild the deck and check the commit.

Rules, and the ones deliberately simplified:
  * no-limit; the big blind is always the full amount to call, even when the big blind is all-in for less
  * heads-up: the button posts the small blind and acts first before the flop, last after it
  * the minimum raise is the last full bet or raise; an all-in for less than that does not reopen the betting for
    a player who already acted, unless the increases since their action add up to a full raise (TDA)
  * side pots for every all-in level; split pots give the odd chip to the first winner left of the button
  * showdown: the river's last aggressor shows first (else the first player left of the button); later players
    show only a hand that beats or ties the best shown so far, the rest are mucked unseen. With an all-in, every
    hand still in is shown. An uncontested pot shows nothing (the winner may choose to show)
  * the button moves to the next player with chips (a moving button: no dead button or dead small blind)
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import math
import random
import secrets
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fair import fair_order  # noqa: E402 — the table's one shuffle, so one verifier covers Magic and poker

RANKS = "23456789TJQKA"
SUITS = "shdc"
DECK = [r + s for r in RANKS for s in SUITS]          # the canonical order the shuffle permutes
RV = {r: i + 2 for i, r in enumerate(RANKS)}
WORD = {2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight", 9: "Nine", 10: "Ten",
        11: "Jack", 12: "Queen", 13: "King", 14: "Ace"}
SUIT_WORD = {"s": "spades", "h": "hearts", "d": "diamonds", "c": "clubs"}
BETTING = ("preflop", "flop", "turn", "river")
ALGORITHM = ("commit = SHA-256 of the seed's 64 hex characters (as text). deck = fair.py's fair_order: the 52 cards "
             "in the order 2s 2h 2d 2c 3s … Ac; for i from 51 down to 1, swap position i with position "
             "int(SHA-256('<seed>:<i>')) mod (i+1). Cards are dealt from the front: one to each player in turn starting "
             "left of the button, a second round, then burn+3 (flop), burn+1 (turn), burn+1 (river).")


class Illegal(Exception):
    """An action the rules don't allow; the message says why, for the player."""


# ── cards and hands ──────────────────────────────────────────────────────────────────────────
def plural(r: int) -> str:
    return "Sixes" if r == 6 else WORD[r] + "s"


def card_name(c: str) -> str:
    return f"{WORD[RV[c[0]]]} of {SUIT_WORD[c[1]]}"


def _straight_top(ranks: set) -> int:
    for top in range(14, 5, -1):
        if all(top - k in ranks for k in range(5)):
            return top
    return 5 if {14, 2, 3, 4, 5} <= ranks else 0     # the wheel: the ace plays low


def evaluate(cards) -> tuple:
    """The best five-card hand in 5-7 cards, as a tuple that compares correctly: (category, ranks…).
    Categories 0 high card … 8 straight flush. Counts ranks and suits directly (no 21-way search) so the AI's
    Monte-Carlo stays cheap."""
    rs = sorted((RV[c[0]] for c in cards), reverse=True)
    suits: dict = {}
    for c in cards:
        suits.setdefault(c[1], []).append(RV[c[0]])
    flush = next((sorted(v, reverse=True) for v in suits.values() if len(v) >= 5), None)
    if flush:
        top = _straight_top(set(flush))
        if top:
            return (8, top)
    counts: dict = {}
    for r in rs:
        counts[r] = counts.get(r, 0) + 1
    groups = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    if groups[0][1] == 4:
        q = groups[0][0]
        return (7, q, max(r for r in rs if r != q))
    trips = [r for r, n in groups if n == 3]
    pairs = [r for r, n in groups if n == 2]
    if trips and (len(trips) > 1 or pairs):
        return (6, trips[0], max(trips[1:] + pairs))
    if flush:
        return (5, *flush[:5])
    top = _straight_top(set(rs))
    if top:
        return (4, top)
    if trips:
        return (3, trips[0], *[r for r in rs if r != trips[0]][:2])
    if len(pairs) >= 2:
        hi, lo = pairs[0], pairs[1]
        return (2, hi, lo, max(r for r in rs if r not in (hi, lo)))
    if pairs:
        return (1, pairs[0], *[r for r in rs if r != pairs[0]][:3])
    return (0, *rs[:5])


def hand_name(score: tuple) -> str:
    cat, a = score[0], score[1]
    if cat == 8:
        return "Royal flush" if a == 14 else f"Straight flush, {WORD[a]} high"
    if cat == 7:
        return f"Four of a kind, {plural(a)}"
    if cat == 6:
        return f"Full house, {plural(a)} over {plural(score[2])}"
    if cat == 5:
        return f"Flush, {WORD[a]} high"
    if cat == 4:
        return f"Straight, {WORD[a]} high"
    if cat == 3:
        return f"Three of a kind, {plural(a)}"
    if cat == 2:
        return f"Two pair, {plural(a)} and {plural(score[2])}"
    if cat == 1:
        return f"Pair of {plural(a)}"
    return f"High card, {WORD[a]}"


def best_five(cards) -> list:
    """The five cards that make the hand (for showing which ones play); showdown only, so a plain search is fine."""
    return list(max(combinations(cards, 5), key=evaluate))


# ── the shuffle ──────────────────────────────────────────────────────────────────────────────
def commit_of(seed_hex: str) -> str:
    return hashlib.sha256(seed_hex.encode()).hexdigest()


def deck_of(seed_hex: str) -> list:
    return [DECK[i] for i in fair_order(DECK, seed_hex)]


# ── pots ─────────────────────────────────────────────────────────────────────────────────────
def side_pots(contrib: dict, live) -> list:
    """[(amount, [eligible seats])], main pot first, from what each seat put in this hand. `live`: seats not folded.
    Each all-in level makes a layer; a layer only one live seat reached is that seat's uncalled bet coming back, and
    chips folded players put in above every live seat join the pot below them."""
    live = set(live)
    pots: list = []
    prev = 0
    for lv in sorted({v for v in contrib.values() if v > 0}):
        amt = sum(min(v, lv) - min(v, prev) for v in contrib.values())
        elig = [s for s in sorted(contrib) if s in live and contrib[s] >= lv]
        if not elig and pots:
            pots[-1][0] += amt
        elif not elig:
            pots.append([amt, sorted(live)])
        elif pots and pots[-1][1] == elig:
            pots[-1][0] += amt
        else:
            pots.append([amt, elig])
        prev = lv
    return [(a, e) for a, e in pots]


# ── the AI seat ──────────────────────────────────────────────────────────────────────────────
def chen(hole) -> int:
    """Bill Chen's preflop score: -1 (7-2 offsuit) to 20 (aces)."""
    a, b = sorted((RV[c[0]] for c in hole), reverse=True)
    pts = {14: 10, 13: 8, 12: 7, 11: 6}.get(a, a / 2)
    if a == b:
        return max(5, int(pts * 2))
    if hole[0][1] == hole[1][1]:
        pts += 2
    gap = a - b - 1
    pts -= (0, 1, 2, 4)[gap] if gap < 4 else 5
    if gap <= 1 and a < 12:
        pts += 1
    return math.ceil(pts)


def equity(hole, board, opponents: int, iters: int, rng) -> float:
    """My share of the pot against `opponents` random hands, by Monte-Carlo over the unseen cards. Only my own cards
    and the board go in: the AI never sees anyone else's hand."""
    known = set(hole) | set(board)
    deck = [c for c in DECK if c not in known]
    need_b = 5 - len(board)
    got = 0.0
    for _ in range(iters):
        draw = rng.sample(deck, need_b + 2 * opponents)
        b = list(board) + draw[:need_b]
        mine = evaluate(list(hole) + b)
        best = max(evaluate(draw[need_b + 2 * k:need_b + 2 * k + 2] + b) for k in range(opponents))
        got += 1.0 if mine > best else 0.5 if mine == best else 0.0
    return got / max(1, iters)


def ai_decide(v: dict, rng, iters: int = 300) -> tuple:
    """(action, amount) from a seat's view: its own cards, the board and the betting. Preflop by Chen score,
    afterwards by equity against pot odds, with a little randomness so it can be bluffed and can bluff."""
    to_call, pot, bb = v["to_call"], v["pot"], v["bb"]
    passive = ("check", 0) if v["check"] else ("call", to_call)
    push = v["raise"] or v["bet"]

    def bet_to(x):
        if not push:
            return passive
        x = max(int(x), v["min_to"])
        return ("allin", v["max_to"]) if x >= v["max_to"] else ("raise" if v["cur"] else "bet", x)

    if v["street"] == "preflop":
        score = chen(v["hole"])
        if score >= 10 and rng.random() < 0.85:
            return bet_to(max(v["cur"] * 3, v["min_to"]))
        if score >= 7 and to_call <= max(4 * bb, 0.12 * v["stack"]):
            return bet_to(v["min_to"]) if rng.random() < 0.2 else passive
        if score >= 5 and to_call <= 2 * bb:
            return passive
        if v["check"]:
            return ("check", 0)
        return bet_to(v["min_to"]) if rng.random() < 0.04 else ("fold", 0)   # an occasional steal
    eq = equity(v["hole"], v["board"], max(1, min(5, v["opponents"])), iters, rng)
    if v["check"]:
        if eq > 0.72 and rng.random() < 0.85:
            return bet_to(pot * rng.uniform(0.55, 0.9))
        if eq > 0.5 and rng.random() < 0.35:
            return bet_to(max(pot * 0.5, bb))
        return bet_to(max(pot * 0.5, bb)) if rng.random() < 0.08 else ("check", 0)   # 8%: a bluff
    if eq > 0.8 and rng.random() < 0.7:
        return bet_to(v["cur"] + max(pot, v["min_to"] - v["cur"]))
    if eq >= to_call / (pot + to_call) + 0.04:
        return ("call", to_call)
    return bet_to(v["min_to"]) if rng.random() < 0.03 else ("fold", 0)


# ── the game ─────────────────────────────────────────────────────────────────────────────────
HAND_FIELDS = dict(hole=[], bet=0, total=0, folded=False, allin=False, acted=False, level=0, last="", shown=False,
                   in_hand=False)


class Game:
    """2-6 seats of play chips. All state is plain data (export()/restore()) so a cloud room can carry it."""

    def __init__(self, players, chips: int = 1000, blinds=(10, 20), seed=None, emit=None):
        if not 2 <= len(players) <= 6:
            raise ValueError("Hold'em here seats 2 to 6 players")
        names = [str(p["name"]) for p in players]
        if len({n.lower() for n in names}) != len(names):
            raise ValueError("every seat needs its own name")
        self.seats = [dict(name=str(p["name"]), ai=bool(p.get("ai")), chips=int(chips), **copy.deepcopy(HAND_FIELDS))
                      for p in players]
        self.sb, self.bb = int(blinds[0]), int(blinds[1])
        self.seed = seed
        self.started = False
        self.over = False
        self.winner = None
        self.hand_no = 0
        self.button = None
        self.h: dict = {}
        self.history: dict = {}                       # hand number → {seed, commit, done}; seeds are public once the GAME ends
        self.log: list = []
        self.emit = emit or (lambda kind, **kw: None)
        self.rng = random.Random(f"poker-table:{seed}") if seed is not None else random.Random(secrets.randbits(128))
        self.ai_rng = {s["name"]: random.Random(f"poker-ai:{seed}:{s['name']}") if seed is not None
                       else random.Random(secrets.randbits(128)) for s in self.seats}

    # ── helpers
    @property
    def n(self) -> int:
        return len(self.seats)

    def index(self, name: str):
        return next((i for i, s in enumerate(self.seats) if s["name"].lower() == str(name or "").lower()), None)

    def _in(self, i) -> bool:
        s = self.seats[i]
        return s["in_hand"] and not s["folded"]

    def _live(self, i) -> bool:                     # still makes betting decisions
        return self._in(i) and not self.seats[i]["allin"]

    def _next(self, i, test) -> int:
        for k in range(1, self.n + 1):
            j = (i + k) % self.n
            if test(j):
                return j
        return i

    def total_chips(self) -> int:
        return sum(s["chips"] + s["total"] for s in self.seats)

    def pot_total(self) -> int:
        return sum(s["total"] for s in self.seats)

    def say(self, text: str) -> None:
        self.log.append(text)
        del self.log[:-60]

    def _put(self, i, amt: int) -> None:
        s = self.seats[i]
        amt = max(0, min(int(amt), s["chips"]))
        s["chips"] -= amt
        s["bet"] += amt
        s["total"] += amt
        if s["chips"] == 0:
            s["allin"] = True

    # ── a hand
    def start_hand(self) -> None:
        if self.over:
            raise Illegal("the game is over")
        if self.h and self.h["street"] != "done":
            raise Illegal("a hand is already being played")
        active = [i for i, s in enumerate(self.seats) if s["chips"] > 0]
        if len(active) < 2:
            raise Illegal("there aren't two players with chips")
        self.started = True
        for s in self.seats:
            s.update(copy.deepcopy(HAND_FIELDS))
            s["in_hand"] = s["chips"] > 0                 # a seat with no chips sits out
        has = lambda j: self.seats[j]["in_hand"]         # noqa: E731 — fixed before the blinds: a blind can put a seat all-in
        self.button = self.rng.choice(active) if self.button is None else self._next(self.button, has)
        self.hand_no += 1
        no = self.hand_no
        seed = (hmac.new(f"poker-seed:{self.seed}".encode(), f"hand:{no}".encode(), hashlib.sha256).hexdigest()
                if self.seed is not None else secrets.token_hex(32))
        commit = commit_of(seed)
        self.history[no] = {"seed": seed, "commit": commit, "done": False}
        for old in sorted(self.history)[:-200]:
            del self.history[old]
        heads_up = len(active) == 2
        sb_i = self.button if heads_up else self._next(self.button, has)
        bb_i = self._next(sb_i, has)
        self.h = dict(no=no, commit=commit, deck=deck_of(seed), pos=0, board=[], street="preflop", cur=self.bb,
                      min_raise=self.bb, to_act=None, sb_i=sb_i, bb_i=bb_i, aggr=None, river_aggr=None, result=None,
                      version=0)
        self.say(f"Hand {no}: {self.seats[self.button]['name']} has the button. Shuffle commit {commit[:12]}…")
        self.emit("hand", hand=no, commit=commit, button=self.button, sb=sb_i, bb=bb_i)   # before a card is dealt
        for i, amt, what in ((sb_i, self.sb, "small blind"), (bb_i, self.bb, "big blind")):
            self._put(i, amt)
            s = self.seats[i]
            s["last"] = f"{what} {s['bet']}"
            self.say(f"{s['name']} posts the {what}, {s['bet']}" + (" (all-in)" if s["allin"] else ""))
        first = self._next(self.button, has)
        for _ in range(2):                                # one card each, twice, starting left of the button
            j = first
            for _k in range(len(active)):
                self.seats[j]["hole"].append(self._draw())
                j = self._next(j, has)
        self.emit("deal", hand=no)
        start = self.button if heads_up else self._next(bb_i, has)
        self.h["to_act"] = self._next_to_act(start)
        if self.h["to_act"] is None:
            self._end_street()

    def _draw(self) -> str:
        c = self.h["deck"][self.h["pos"]]
        self.h["pos"] += 1
        return c

    def _next_to_act(self, start):
        """The first seat from `start` (inclusive) that still has to act this round, or None when the round is over."""
        h = self.h
        live = [i for i in range(self.n) if self._live(i)]
        if not live or (len(live) == 1 and self.seats[live[0]]["bet"] >= h["cur"]):
            return None                                   # nobody left to bet against: no decision to make
        for k in range(self.n):
            i = (start + k) % self.n
            s = self.seats[i]
            if self._live(i) and (not s["acted"] or s["bet"] < h["cur"]):
                return i
        return None

    def _reopened(self, i) -> bool:
        """May this seat raise? Yes if it hasn't acted this round, or the bet has gone up by at least a full raise since."""
        s = self.seats[i]
        return not s["acted"] or self.h["cur"] - s["level"] >= self.h["min_raise"]

    def legal(self, i) -> dict:
        h = self.h
        if not h or h["street"] not in BETTING or h["to_act"] != i:
            return {"your_turn": False}
        s, cur = self.seats[i], h["cur"]
        max_to = s["bet"] + s["chips"]
        to_call = max(0, cur - s["bet"])
        can_raise = cur > 0 and max_to > cur and self._reopened(i)
        min_to = cur + h["min_raise"] if cur else self.bb
        return {"your_turn": True, "to_call": min(to_call, s["chips"]), "check": to_call == 0, "call": to_call > 0,
                "bet": cur == 0 and max_to > 0, "raise": can_raise, "min_to": min(min_to, max_to), "max_to": max_to,
                "cur": cur, "pot": self.pot_total(), "bet_now": s["bet"]}

    def act(self, name: str, action: str, amount=0) -> str:
        i = self.index(name)
        h = self.h
        if i is None:
            raise Illegal("no such seat")
        if self.over:
            raise Illegal("the game is over")
        if not h or h["street"] not in BETTING:
            raise Illegal("no betting round is open")
        if h["to_act"] != i:
            raise Illegal(f"it's {self.seats[h['to_act']]['name']}'s turn, not yours")
        try:
            amount = int(amount or 0)
        except (TypeError, ValueError):
            raise Illegal("the amount must be a whole number of chips") from None
        s, cur = self.seats[i], h["cur"]
        to_call, max_to = cur - s["bet"], s["bet"] + s["chips"]
        action = str(action or "").lower().replace("-", "")
        if action == "fold":
            s["folded"] = True
            text = "folds"
        elif action == "check":
            if to_call > 0:
                raise Illegal(f"you can't check: it's {min(to_call, s['chips'])} to call")
            text = "checks"
        elif action == "call" or (action == "allin" and max_to <= cur):
            if to_call <= 0:
                raise Illegal("there's nothing to call: check instead")
            pay = min(to_call, s["chips"])
            self._put(i, pay)
            text = f"calls {pay}" + (" (all-in)" if s["allin"] else "")
        elif action in ("bet", "raise", "allin"):
            target = max_to if action == "allin" else amount
            if action == "bet" and cur > 0:
                raise Illegal(f"there's already a bet of {cur}: raise instead")
            if action == "raise" and cur == 0:
                raise Illegal("there's no bet to raise: bet instead")
            if target > max_to:
                raise Illegal(f"that's more than you have: the most you can make it is {max_to}")
            if target <= cur:
                raise Illegal(f"a raise must make it more than {cur}")
            if cur > 0 and not self._reopened(i):
                raise Illegal("you can only call or fold: the all-in since you acted was less than a full raise, "
                              "so the betting isn't reopened")
            full = h["min_raise"] if cur else self.bb
            if target - cur < full and target < max_to:
                raise Illegal(f"the smallest {'raise is to' if cur else 'bet is'} {cur + full}")
            self._put(i, target - s["bet"])
            if target - cur >= full:
                h["min_raise"] = target - cur
            h["cur"] = target
            h["aggr"] = i
            if h["street"] == "river":
                h["river_aggr"] = i
            text = ("bets " if cur == 0 else "raises to ") + str(target) + (" (all-in)" if s["allin"] else "")
        else:
            raise Illegal(f"'{action[:12]}' isn't an action here: fold, check, call, bet, raise or allin")
        s["acted"] = True
        s["level"] = s["bet"]
        s["last"] = text
        h["version"] += 1
        self.say(f"{s['name']} {text}")
        self.emit("act", hand=h["no"], by=s["name"], action=action, text=text, street=h["street"])
        self._advance(i)
        return text

    def _advance(self, i) -> None:
        alive = [j for j in range(self.n) if self._in(j)]
        if len(alive) == 1:
            return self._settle_uncontested(alive[0])
        nxt = self._next_to_act((i + 1) % self.n)
        if nxt is None:
            return self._end_street()
        self.h["to_act"] = nxt

    def _end_street(self) -> None:
        h = self.h
        while True:
            for s in self.seats:
                s.update(bet=0, acted=False, level=0)
            h.update(cur=0, min_raise=self.bb, aggr=None, to_act=None)
            if h["street"] == "river":
                return self._showdown()
            h["street"] = BETTING[BETTING.index(h["street"]) + 1]
            self._draw()                                   # burn
            h["board"] += [self._draw() for _ in range(3 if h["street"] == "flop" else 1)]
            self.say(f"{h['street'].capitalize()}: {' '.join(h['board'])}")
            self.emit("street", hand=h["no"], street=h["street"], board=list(h["board"]))
            h["to_act"] = self._next_to_act((self.button + 1) % self.n)
            if h["to_act"] is not None:
                return
            # nobody can bet: run the board out

    def _order_from_button(self, seats) -> list:
        return sorted(seats, key=lambda j: (j - self.button - 1) % self.n)

    def _award(self, pots_out: list) -> None:
        for s in self.seats:
            s.update(bet=0, total=0)
        h = self.h
        h.update(street="done", to_act=None, result={"pots": pots_out, **(h.get("result") or {})})
        self.history[h["no"]]["done"] = True
        self.emit("result", hand=h["no"], pots=pots_out, shown={self.seats[i]["name"]: list(self.seats[i]["hole"])
                                                              for i in range(self.n) if self.seats[i]["shown"]})
        live = [s for s in self.seats if s["chips"] > 0]
        if len(live) == 1:
            self.over, self.winner = True, live[0]["name"]
            self.say(f"{self.winner} has every chip at the table. Game over.")
            self.emit("over", winner=self.winner)

    def end_game(self, by: str) -> None:
        """Stop the game. A hand in progress is called off and every bet in it goes back to whoever made it; then
        the seeds of every hand become verifiable (that is what a seed may reveal only once play has stopped)."""
        if self.over:
            raise Illegal("the game is over")
        h = self.h
        if h and h["street"] != "done":
            for s in self.seats:
                s["chips"] += s["total"]
                s.update(bet=0, total=0)
            h.update(street="done", to_act=None, result={"called_off": True, "pots": []})
            self.history[h["no"]]["done"] = True
            self.say(f"{by} ended the game during hand {h['no']}: every bet in it went back")
        lead = max(self.seats, key=lambda s: s["chips"])
        tied = [s for s in self.seats if s["chips"] == lead["chips"]]
        self.over, self.winner = True, (lead["name"] if len(tied) == 1 else None)
        self.say(f"{by} ended the game." + (f" {self.winner} has the most chips." if self.winner else "")
                 + " Every hand's shuffle can now be checked.")
        self.emit("over", winner=self.winner, ended_by=by)

    def _settle_uncontested(self, w) -> None:
        amt = self.pot_total()
        self.seats[w]["chips"] += amt
        self.say(f"{self.seats[w]['name']} wins {amt} (everyone else folded)")
        self.h["result"] = {"uncontested": True, "names": {}}
        self._award([{"amount": amt, "winners": [self.seats[w]["name"]], "hand": None}])

    def _showdown(self) -> None:
        h = self.h
        live = [j for j in range(self.n) if self._in(j)]
        score = {j: evaluate(self.seats[j]["hole"] + h["board"]) for j in live}
        if any(self.seats[j]["allin"] for j in live):
            show = set(live)                               # an all-in: every hand still in is turned over
        else:
            first = h["river_aggr"] if h["river_aggr"] in live else self._order_from_button(live)[0]
            order = [first] + [j for j in self._order_from_button(live) if j != first]
            show, best = set(), None
            for j in order:
                if best is None or score[j] >= best:
                    show.add(j)
                    best = score[j] if best is None else max(best, score[j])
        for j in show:
            self.seats[j]["shown"] = True
        for j in live:
            if j not in show:
                self.seats[j]["last"] = "mucks"
        names = {self.seats[j]["name"]: hand_name(score[j]) for j in show}
        h["result"] = {"uncontested": False, "names": names,
                       "best": {self.seats[j]["name"]: best_five(self.seats[j]["hole"] + h["board"]) for j in show}}
        out = []
        pots = side_pots({j: self.seats[j]["total"] for j in range(self.n) if self.seats[j]["total"]}, live)
        for k, (amt, elig) in enumerate(pots):
            top = max(score[j] for j in elig)
            winners = self._order_from_button([j for j in elig if score[j] == top])
            share, odd = divmod(amt, len(winners))
            for m, j in enumerate(winners):
                self.seats[j]["chips"] += share + (1 if m < odd else 0)
            label = "returned" if len(elig) == 1 and k == len(pots) - 1 and len(pots) > 1 else \
                ("main pot" if k == 0 else f"side pot {k}")
            wn = [self.seats[j]["name"] for j in winners]
            hn = None if len(elig) == 1 else hand_name(top)
            out.append({"amount": amt, "winners": wn, "hand": hn, "label": label})
            self.say(f"{' and '.join(wn)} {'win' if len(wn) > 1 else 'wins'} {amt}"
                     + (f" ({label})" if len(pots) > 1 else "") + (f" with {hn}" if hn else ""))
        for j in sorted(show):
            self.say(f"{self.seats[j]['name']} shows {' '.join(self.seats[j]['hole'])}: {names[self.seats[j]['name']]}")
        self._award(out)

    def show(self, name: str) -> list:
        """After a hand: a player who was still in may turn their cards over. A folded hand stays face down."""
        i = self.index(name)
        if i is None or not self.h or self.h["street"] != "done":
            raise Illegal("you can show your cards when the hand is over")
        s = self.seats[i]
        if not s["in_hand"] or s["folded"]:
            raise Illegal("a folded hand stays face down")
        if not s["shown"]:
            s["shown"] = True
            self.say(f"{s['name']} shows {' '.join(s['hole'])}")
            self.emit("show", hand=self.h["no"], by=s["name"], cards=list(s["hole"]))
        return list(s["hole"])

    # ── what the AI may see, and what everyone may see
    def ai_view(self, i) -> dict:
        lg = self.legal(i)
        s = self.seats[i]
        return {**lg, "hole": list(s["hole"]), "board": list(self.h["board"]), "street": self.h["street"],
                "bb": self.bb, "stack": s["chips"], "opponents": sum(1 for j in range(self.n) if j != i and self._in(j))}

    def ai_choice(self, i, iters: int = 300) -> tuple:
        return ai_decide(self.ai_view(i), self.ai_rng[self.seats[i]["name"]], iters)

    def public(self) -> dict:
        """Everything any screen may see. Never a hole card that hasn't been shown, never the deck or the seed."""
        h = self.h
        done = bool(h) and h["street"] == "done"
        seats = [{"name": s["name"], "ai": s["ai"], "chips": s["chips"], "bet": s["bet"], "total": s["total"],
                  "folded": s["folded"], "allin": s["allin"], "in_hand": s["in_hand"], "out": s["chips"] == 0 and not s["in_hand"],
                  "has_cards": bool(s["hole"]) and s["in_hand"] and not s["folded"], "last": s["last"],
                  "cards": list(s["hole"]) if s["shown"] else None} for s in self.seats]
        res = None
        if done and h.get("result"):
            r = h["result"]
            res = {"pots": r.get("pots", []), "names": r.get("names", {}), "uncontested": r.get("uncontested", False),
                   "best": r.get("best", {})}
        bets = sum(s["bet"] for s in self.seats)
        return {"hand": h.get("no", 0), "street": h.get("street", "waiting"), "board": list(h.get("board", [])),
                "button": self.button, "sb_seat": h.get("sb_i"), "bb_seat": h.get("bb_i"), "to_act": h.get("to_act"),
                "cur": h.get("cur", 0), "min_raise": h.get("min_raise", self.bb), "pot": self.pot_total() - bets,
                "pot_total": self.pot_total(), "blinds": [self.sb, self.bb], "seats": seats, "commit": h.get("commit"),
                "result": res, "started": self.started, "over": self.over, "winner": self.winner,
                "commits": {k: v["commit"] for k, v in sorted(self.history.items())[-20:]},
                "verifiable": sorted(k for k, v in self.history.items() if v["done"]) if self.over else [],
                "log": self.log[-40:]}

    def verify(self, no: int):
        rec = self.history.get(int(no))
        if not rec:
            return 404, {"error": f"no hand {no} at this table"}
        if not rec["done"]:
            return 409, {"error": f"hand {no} isn't over yet: its seed stays sealed until it is", "commit": rec["commit"]}
        if not self.over:                         # a seed rebuilds the whole deck, folded hands included
            return 409, {"error": "every hand's seed is revealed when the game ends: sooner would show the folded cards",
                         "commit": rec["commit"]}
        deck = deck_of(rec["seed"])
        return 200, {"hand": int(no), "seed": rec["seed"], "commit": rec["commit"], "deck": deck,
                     "matches": commit_of(rec["seed"]) == rec["commit"], "algorithm": ALGORITHM}

    # ── persistence: plain data only
    def export(self) -> dict:
        d = {k: copy.deepcopy(getattr(self, k)) for k in ("seats", "sb", "bb", "seed", "started", "over", "winner",
                                                           "hand_no", "button", "h", "history", "log")}
        d["rng"] = self.rng.getstate()
        d["ai_rng"] = {k: r.getstate() for k, r in self.ai_rng.items()}
        return d

    @classmethod
    def restore(cls, d: dict, emit=None) -> "Game":
        g = cls([{"name": s["name"], "ai": s["ai"]} for s in d["seats"]], blinds=(d["sb"], d["bb"]), seed=d["seed"], emit=emit)
        for k in ("seats", "started", "over", "winner", "hand_no", "button", "h", "history", "log"):
            setattr(g, k, copy.deepcopy(d[k]))
        g.history = {int(k): v for k, v in g.history.items()}   # JSON turns the hand numbers into strings
        g.rng.setstate(d["rng"])
        for k, st in d.get("ai_rng", {}).items():
            if k in g.ai_rng:
                g.ai_rng[k].setstate(st)
        return g
