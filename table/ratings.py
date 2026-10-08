#!/usr/bin/env python3
"""Ratings from game results, with the uncertainty left in (docs/arena-vision.md, "Ratings built for multiplayer").

    python3 table/ratings.py [--ledger docs/results/ledger.json] [--boot 500] [--seed 0]

A four-player game is a finish order, not a win/loss pair, so multiplayer games are rated with a
Plackett-Luce model: the first place beat everyone, second beat everyone left, and so on, each step a
softmax over strengths. Two-player results (chess: win / draw / loss) are the same model with two
players, so Bradley-Terry runs through the same code. The fit is the MM algorithm (Hunter 2004).

Strengths are log-strengths ("theta"), centred at 0 over the players in the fit; only differences mean
anything. A rank after three games is mostly noise, so every player also gets a bootstrap interval
(games resampled with replacement, seeded and deterministic), the probability of being ranked first, and a
plain-language verdict that says "too few games to rank" when that is the honest answer.

The prior is a pseudo-game: every player also played one game against a fixed average opponent
(strength 1, theta 0), half won and half lost. Without it a player with no wins or no losses has an
infinite strength; with it they stay finite and a player with no data sits at 0.

Humans-at-table and AI-only games are different conditions (a person at the table changes how the AIs
play), so the ledger loader keeps them apart and nothing here pools them unless the caller does.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "docs" / "results" / "ledger.json"

PRIOR = 1.0              # total weight of the pseudo-game each player plays against the fixed average opponent
DRAW_SCORE = 0.5         # a draw is half a win for each side
MIN_GAMES = 10           # below this a player's verdict is "too few games to rank", whatever the interval says
MIN_LEDGER_GAMES = 10    # below this a whole condition prints no ranking at all
CONFIDENCE = 0.90
ELO_PER_THETA = 400 / math.log(10)     # display scale: 400 Elo points = 10:1 odds = theta difference ln 10
_GHOST = "\0average-opponent"          # the fixed opponent behind the prior; never reported


def elo(theta: float) -> float:
    """Elo-like display number for a log-strength difference: 400/ln(10) * theta, so +400 means 10:1 odds."""
    return ELO_PER_THETA * theta


# ---------------------------------------------------------------- the fit

def fit_pl(games: list[list[str]], prior: float | None = None, tol: float = 1e-9, max_iter: int = 5000,
           start: dict[str, float] | None = None) -> dict[str, float]:
    """Plackett-Luce log-strengths from finish orders (each game: ids, first place -> last), centred at 0."""
    return _fit([[(g, 1.0)] for g in games], prior, tol, max_iter, start)


def fit_bt(results: list[tuple[str, str, float]], prior: float | None = None, **kw) -> dict[str, float]:
    """Bradley-Terry log-strengths from (a, b, score_for_a) with score 1 win, 0.5 draw, 0 loss."""
    return _fit([_bt_unit(r) for r in results], prior, kw.get("tol", 1e-9), kw.get("max_iter", 5000), kw.get("start"))


def chess_results(games: list[tuple[str, str, str]]) -> list[tuple[str, str, float]]:
    """(white, black, "1-0" | "1/2-1/2" | "0-1") -> (a, b, score_for_a); a draw is DRAW_SCORE for each side."""
    score = {"1-0": 1.0, "1/2-1/2": DRAW_SCORE, "0-1": 0.0}
    return [(w, b, score[r]) for w, b, r in games]


def _bt_unit(r: tuple[str, str, float]) -> list[tuple[list[str], float]]:
    """A pairwise result is two weighted two-player games: a>b with weight s, b>a with weight 1-s."""
    a, b, s = r
    if a == b or not 0.0 <= s <= 1.0:
        raise ValueError(f"bad pairwise result {r!r}")
    return [(o, w) for o, w in (([a, b], s), ([b, a], 1.0 - s)) if w > 1e-12]


def _fit(units: list[list[tuple[list[str], float]]], prior: float | None, tol: float, max_iter: int,
         start: dict[str, float] | None, players: list[str] | None = None) -> dict[str, float]:
    """MM iteration on log-strengths. A unit is one observed game as weighted orderings (a PL game has one)."""
    prior = PRIOR if prior is None else prior
    flat = [(o, w) for u in units for o, w in u]
    for o, _ in flat:
        if len(o) < 2 or len(set(o)) != len(o):
            raise ValueError(f"a game needs 2+ distinct players: {o!r}")
    names = sorted({p for o, _ in flat for p in o} | set(players or []))   # a bootstrap sample may miss someone
    ix = {p: i for i, p in enumerate(names)}
    n = len(names)
    ghost = n                                          # index of the fixed average opponent
    merged: dict[tuple, float] = {}                    # identical orderings carry the same information: count them once
    for o, w in flat:
        key = tuple(ix[p] for p in o)
        merged[key] = merged.get(key, 0.0) + w
    games = [(list(k), w) for k, w in merged.items()]
    if prior > 0:
        for i in range(n):
            games += [([i, ghost], prior / 2), ([ghost, i], prior / 2)]
    g = [1.0] * (n + 1)
    if start:
        for p, t in start.items():
            if p in ix:
                g[ix[p]] = math.exp(t)
    for _ in range(max_iter):
        wins = [0.0] * n
        denom = [0.0] * (n + 1)
        for o, w in games:
            m = len(o)
            suffix = [0.0] * (m + 1)                   # suffix[k] = total strength of the players still in at stage k
            for k in range(m - 1, -1, -1):
                suffix[k] = suffix[k + 1] + g[o[k]]
            acc = 0.0
            for k in range(m):
                if k < m - 1:
                    acc += w / suffix[k]               # stage k is a softmax over o[k:]; o[k] is the pick
                    if o[k] != ghost:
                        wins[o[k]] += w
                denom[o[k]] += acc                     # o[k] sits in every stage up to min(k, m-2)
        new = [max(wins[i] / denom[i], 1e-12) if denom[i] > 0 else 1.0 for i in range(n)] + [1.0]
        delta = max(abs(math.log(new[i]) - math.log(g[i])) for i in range(n))
        g = new
        if delta < tol:
            break
    theta = [math.log(x) for x in g[:n]]
    mean = sum(theta) / n
    return {p: theta[ix[p]] - mean for p in names}


def loglik(games: list[list[str]], theta: dict[str, float]) -> float:
    """Plackett-Luce log-likelihood of finish orders; unchanged when every theta shifts by a constant."""
    total = 0.0
    for o in games:
        for k in range(len(o) - 1):
            total += theta[o[k]] - math.log(sum(math.exp(theta[p]) for p in o[k:]))
    return total


# ---------------------------------------------------------------- uncertainty

class Ratings:
    """Fitted strengths plus what is known about how well they are known."""

    def __init__(self, theta, lo, hi, p_top, n_games, n_opponents, boot):
        self.theta, self.lo, self.hi, self.p_top = theta, lo, hi, p_top
        self.n_games, self.n_opponents, self.boot = n_games, n_opponents, boot
        self.players = sorted(theta, key=lambda p: -theta[p])

    def interval(self, p: str) -> tuple[float, float]:
        return self.lo[p], self.hi[p]

    def verdict(self, p: str) -> str:
        """What can honestly be said about this player's rank."""
        n = self.n_games[p]
        if n < MIN_GAMES:
            return f"too few games to rank ({n} so far)"
        others = [q for q in self.players if q != p]
        if not others:
            return "no one to compare against"
        above = sum(self.lo[p] > self.hi[q] for q in others)
        below = sum(self.hi[p] < self.lo[q] for q in others)
        overlap = len(others) - above - below
        if overlap * 2 > len(others):
            return f"interval overlaps {overlap} of {len(others)} others: cannot separate yet"
        return f"clearly above {above} and below {below} of {len(others)} others"

    def summary(self) -> list[dict]:
        return [{"player": p, "theta": round(self.theta[p], 3), "elo": round(elo(self.theta[p])),
                 "lo": round(self.lo[p], 3), "hi": round(self.hi[p], 3), "p_rank1": round(self.p_top[p], 3),
                 "n_games": self.n_games[p], "n_opponents": self.n_opponents[p], "verdict": self.verdict(p)}
                for p in self.players]


def rate_pl(games: list[list[str]], boot: int = 500, seed: int = 0, prior: float | None = None) -> Ratings:
    return _rate([[(g, 1.0)] for g in games], boot, seed, prior)


def rate_bt(results: list[tuple[str, str, float]], boot: int = 500, seed: int = 0,
            prior: float | None = None) -> Ratings:
    return _rate([_bt_unit(r) for r in results], boot, seed, prior)


def _rate(units, boot: int, seed: int, prior: float | None) -> Ratings:
    """Fit once, then refit on `boot` resamples of the GAMES (not of players or stages), warm-started."""
    full = _fit(units, prior, 1e-9, 5000, None)
    players = sorted(full)
    rng = random.Random(seed)
    draws: dict[str, list[float]] = {p: [] for p in players}
    top = {p: 0.0 for p in players}
    for _ in range(boot):
        sample = [units[rng.randrange(len(units))] for _ in units]
        t = _fit(sample, prior, 1e-5, 500, full, players)
        for p in players:
            draws[p].append(t[p])
        best = max(draws[p][-1] for p in players)
        winners = [p for p in players if draws[p][-1] == best]
        for p in winners:
            top[p] += 1 / len(winners)
    a = (1 - CONFIDENCE) / 2
    lo = {p: _quantile(draws[p], a) if boot else full[p] for p in players}
    hi = {p: _quantile(draws[p], 1 - a) if boot else full[p] for p in players}
    p_top = {p: top[p] / boot if boot else float("nan") for p in players}
    games_n = {p: 0 for p in players}
    opp: dict[str, set] = {p: set() for p in players}
    for u in units:
        seen = {x for o, _ in u for x in o}
        for p in seen:
            games_n[p] += 1
            opp[p] |= seen - {p}
    return Ratings(full, lo, hi, p_top, games_n, {p: len(s) for p, s in opp.items()}, boot)


def _quantile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    pos = q * (len(s) - 1)
    i = int(math.floor(pos))
    j = min(i + 1, len(s) - 1)
    return s[i] + (s[j] - s[i]) * (pos - i)


# ---------------------------------------------------------------- the ledger

def default_include(game: dict) -> bool:
    """Finished, not forked, and no seat piloted by a person on an AI's behalf."""
    r = game.get("result") or {}
    return (r.get("status") == "finished" and not game.get("forked")
            and not any(s.get("assisted") for s in game.get("seats") or []))


def player_id(controller: dict) -> str:
    """AI seats by the model (the thing being rated), else the system; people by name."""
    if controller.get("kind") == "ai":
        return controller.get("model") or controller["system"]
    return controller.get("name") or "a person"


def condition_of(game: dict) -> str:
    """Tables with a person are a different game from AI-only tables: never pool them by accident."""
    return "mixed" if any((s.get("controller") or {}).get("kind") == "human" for s in game["seats"]) else "ai-only"


def from_ledger(path=None, include=None) -> dict:
    """{"conditions": {condition: [game, ...]}, "skipped": [(id, why)]}.

    Each game is {"id", "order": [player ids, first -> last], "seats": [player ids in turn order]}. Turn
    order is the game's optional `turn_order` (seat names); lacking one, the order the ledger lists the
    seats, which is an assumption and the report says so. `path` may also be an already-loaded dict."""
    include = include or default_include
    d = path if isinstance(path, dict) else json.loads(Path(path or LEDGER).read_text())
    out: dict[str, list[dict]] = {}
    skipped: list[tuple[str, str]] = []
    for g in d.get("games") or []:
        if not include(g):
            continue
        who = {s["seat"]: player_id(s["controller"]) for s in g["seats"]}
        if sorted(g["result"].get("order") or []) != sorted(who):
            skipped.append((g.get("id"), "no complete finish order (every seat exactly once)"))
            continue
        order = [who[s] for s in g["result"]["order"]]
        if len(set(who.values())) != len(who):
            skipped.append((g.get("id"), "one identity holds two seats, so finish order is not between distinct players"))
            continue
        turn = g.get("turn_order") or [s["seat"] for s in g["seats"]]
        out.setdefault(condition_of(g), []).append(
            {"id": g.get("id"), "order": order, "seats": [who[s] for s in turn],
             "turn_order_known": bool(g.get("turn_order"))})
    return {"conditions": out, "skipped": skipped}


def seat_order_wins(records: list[dict]) -> dict[int, list[dict]]:
    """Win rate by turn position, separately for each table size (position means different things at 3 and 4)."""
    out: dict[int, list[dict]] = {}
    for size in sorted({len(r["seats"]) for r in records}):
        rs = [r for r in records if len(r["seats"]) == size]
        rows = []
        for k in range(size):
            wins = sum(r["order"][0] == r["seats"][k] for r in rs)
            lo, hi = _wilson(wins, len(rs))
            rows.append({"position": k + 1, "games": len(rs), "wins": wins, "rate": wins / len(rs), "lo": lo, "hi": hi})
        out[size] = rows
    return out


def _wilson(k: int, n: int, z: float = 1.645) -> tuple[float, float]:
    """90% Wilson interval for a win rate: honest about n = 2."""
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - h) / d, (c + h) / d


# ---------------------------------------------------------------- report

def report(data: dict, boot: int = 500, seed: int = 0, out=print) -> None:
    for cond in ("mixed", "ai-only"):
        recs = data["conditions"].get(cond, [])
        out(f"\n== {cond}: {len(recs)} finished game(s) ==")
        if not recs:
            out("  no games yet")
            continue
        games = [r["order"] for r in recs]
        if len(games) < MIN_LEDGER_GAMES:
            counts: dict[str, int] = {}
            for o in games:
                for p in o:
                    counts[p] = counts.get(p, 0) + 1
            out(f"  not enough data to rate: need at least {MIN_LEDGER_GAMES} finished games, have {len(games)}. No ranking.")
            for p in sorted(counts):
                out(f"    {p:<28} {counts[p]} game(s)")
        else:
            r = rate_pl(games, boot=boot, seed=seed)
            out(f"  {'player':<28} {'strength':>8} {'elo-like':>8}  {'90% interval':<16} {'P(rank 1)':>9} {'games':>5} {'opp':>3}  verdict")
            for p in r.players:
                lo, hi = r.interval(p)
                out(f"  {p:<28} {r.theta[p]:>8.2f} {elo(r.theta[p]):>8.0f}  [{lo:>6.2f},{hi:>6.2f}]   {r.p_top[p]:>9.2f} "
                    f"{r.n_games[p]:>5} {r.n_opponents[p]:>3}  {r.verdict(p)}")
        known = all(r["turn_order_known"] for r in recs)
        out("  win rate by turn position" + ("" if known else " (turn order assumed = seat listing order in the ledger)") + ":")
        for size, rows in seat_order_wins(recs).items():
            cells = "  ".join(f"#{x['position']}: {x['wins']}/{x['games']}" for x in rows)
            out(f"    {size} players: {cells}  (too few games to call an effect)" if rows[0]["games"] < MIN_GAMES else
                f"    {size} players: " + "  ".join(f"#{x['position']}: {x['rate']:.0%} [{x['lo']:.0%}-{x['hi']:.0%}]" for x in rows))
    for gid, why in data["skipped"]:
        out(f"\nskipped {gid}: {why}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ledger", default=str(LEDGER))
    ap.add_argument("--boot", type=int, default=500, help="bootstrap resamples of the games")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    report(from_ledger(a.ledger), a.boot, a.seed)
