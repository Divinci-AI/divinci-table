"""Ratings recover known strengths, say so when they can't, and keep people and AIs apart.

    python3 table/tests/ratings_test.py

The last block breaks ratings.py on purpose (monkeypatching, never touching the file) and demands that
the suite above notices each break."""
import json
import math
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ratings as R  # noqa: E402

failures: list[str] = []
QUIET = False


def check(name, ok, detail=""):
    if not ok:
        failures.append(name)
    if not QUIET:
        print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {detail}"))


def simulate(truth, n_games, per_game, seed):
    """Finish orders drawn from the Plackett-Luce model itself: pick the winner by softmax, remove, repeat."""
    rng = random.Random(seed)
    games = []
    for _ in range(n_games):
        pool = [(f"P{i}", math.exp(truth[i])) for i in rng.sample(range(len(truth)), per_game)]
        order = []
        while pool:
            x = rng.random() * sum(w for _, w in pool)
            for j, (_, w) in enumerate(pool):
                x -= w
                if x <= 0:
                    break
            order.append(pool.pop(j)[0])
        games.append(order)
    return games


def corr(a, b):
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return num / math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))


_cache: dict = {}


def data(key, *args):
    if key not in _cache:
        _cache[key] = simulate(*args)
    return _cache[key]


TRUTH6 = [1.2, 0.6, 0.1, -0.3, -0.7, -0.9]       # sums to 0, like the fitted strengths
TRUTH4 = [0.9, 0.3, -0.3, -0.9]


def ledger_game(gid, status, seats, order=None, **extra):
    """seats: [(seat name, controller dict, assisted)]"""
    return {"id": gid, "date": "2026-01-01", "game": "magic-commander",
            "seats": [dict({"seat": n, "controller": c}, **({"assisted": True} if a else {})) for n, c, a in seats],
            "result": {"status": status, "order": order or []}, **extra}


def ai(model, system="Sys"):
    return {"kind": "ai", "system": system, "model": model}


def human(name):
    return {"kind": "human", "name": name}


def suite():
    failures.clear()
    # ---- recovery
    games = data("rec", TRUTH6, 600, 4, 1)
    fit = R.fit_pl(games)
    ids = [f"P{i}" for i in range(6)]
    est = [fit[p] for p in ids]
    check("recovery: 600 four-player games, fitted strengths correlate > 0.95 with the truth",
          corr(est, TRUTH6) > 0.95, corr(est, TRUTH6))
    check("recovery: top and bottom player are right", max(fit, key=fit.get) == "P0" and min(fit, key=fit.get) == "P5", fit)
    check("strengths are centred at 0", abs(sum(fit.values())) < 1e-9, sum(fit.values()))

    # ---- intervals shrink and cover
    g400, g40 = data("g400", TRUTH4, 400, 4, 2), data("g40", TRUTH4, 40, 4, 3)
    r400, r40 = R.rate_pl(g400, boot=60, seed=5), R.rate_pl(g40, boot=60, seed=5)
    w = lambda r: sum(r.hi[p] - r.lo[p] for p in r.players) / len(r.players)
    check("intervals shrink: 400 games narrower than 40", w(r400) < w(r40), (w(r400), w(r40)))
    covered = sum(r400.lo[f"P{i}"] <= TRUTH4[i] <= r400.hi[f"P{i}"] for i in range(4))
    check("the 90% interval covers the true value for at least 3 of 4 players", covered >= 3, covered)
    check("with 400 games the best player is almost surely rank 1", r400.p_top["P0"] > 0.9, r400.p_top)
    check("n_games and n_opponents are reported", r400.n_games["P0"] == 400 and r400.n_opponents["P0"] == 3,
          (r400.n_games, r400.n_opponents))

    # ---- three games say so
    r3 = R.rate_pl(data("g3", TRUTH4, 3, 4, 4), boot=60, seed=5)
    check("3 games: every verdict is 'too few games'", all(r3.verdict(p).startswith("too few games") for p in r3.players),
          [r3.verdict(p) for p in r3.players])
    check("3 games: the interval is wide (over 1.0, and 4x the 400-game width)", w(r3) > 1.0 and w(r3) > 4 * w(r400), (w(r3), w(r400)))
    check("400 games: a well-separated player gets a real verdict", not r400.verdict("P0").startswith("too few"), r400.verdict("P0"))

    # ---- invariances
    swap = {f"P{i}": f"Z{5 - i}" for i in range(4)}
    small = data("inv", TRUTH4, 60, 4, 6)
    a = R.rate_pl(small, boot=20, seed=9)
    b = R.rate_pl([[swap[p] for p in g] for g in small], boot=20, seed=9)
    check("relabelling players permutes the outputs",
          all(abs(a.theta[p] - b.theta[swap[p]]) < 1e-9 and abs(a.lo[p] - b.lo[swap[p]]) < 1e-9
              and abs(a.p_top[p] - b.p_top[swap[p]]) < 1e-9 for p in a.theta))
    f1, f2 = R.fit_pl(small), R.fit_pl(small[::-1])
    check("a reversed game list gives the same fit", all(abs(f1[p] - f2[p]) < 1e-6 for p in f1))
    c = 3.7
    check("adding a constant to every strength changes no likelihood and no ranking",
          abs(R.loglik(small, f1) - R.loglik(small, {p: t + c for p, t in f1.items()})) < 1e-9
          and sorted(f1, key=f1.get) == sorted(f1, key=lambda p: f1[p] + c))
    free = R.fit_pl(small, prior=0)
    rng = random.Random(0)
    best = R.loglik(small, free)
    check("the unpenalised fit is a likelihood maximum (20 random nudges all do worse)",
          all(R.loglik(small, {p: t + rng.gauss(0, 0.05) for p, t in free.items()}) < best for _ in range(20)))

    # ---- finite at the extremes
    ext = [["W", "A", "B", "L"] if i % 2 else ["W", "B", "A", "L"] for i in range(12)]
    fx = R.fit_pl(ext)
    check("a player who always wins and one who never wins stay finite",
          all(math.isfinite(v) for v in fx.values()) and max(abs(v) for v in fx.values()) < 6, fx)
    check("... and are still ranked first and last", max(fx, key=fx.get) == "W" and min(fx, key=fx.get) == "L", fx)

    # ---- ledger loader
    d = {"games": [
        ledger_game("ok-ai", "finished", [("a", ai("m1"), 0), ("b", ai("m2"), 0), ("c", ai("m3"), 0)], ["b", "a", "c"]),
        ledger_game("ok-mixed", "finished", [("Claude", ai("m1"), 0), ("Sam", human("Sam"), 0), ("Fusion", {"kind": "ai", "system": "FusionSys"}, 0)],
                    ["Sam", "Claude", "Fusion"], turn_order=["Fusion", "Sam", "Claude"]),
        ledger_game("unfin", "unfinished", [("a", ai("m1"), 0), ("b", ai("m2"), 0)]),
        ledger_game("fork", "forked", [("a", ai("m1"), 0), ("b", ai("m2"), 0)], ["a", "b"]),
        ledger_game("assist", "finished", [("a", ai("m1"), 1), ("b", ai("m2"), 0)], ["a", "b"]),
        ledger_game("twice", "finished", [("a", ai("m1"), 0), ("b", ai("m1"), 0)], ["a", "b"]),
    ]}
    led = R.from_ledger(d)
    conds = led["conditions"]
    ids_of = lambda c: [g["id"] for g in conds.get(c, [])]
    check("ledger: finished games only; unfinished, forked and assisted are excluded",
          set(ids_of("ai-only")) | set(ids_of("mixed")) <= {"ok-ai", "ok-mixed"}, conds)
    check("ledger: AI-only and mixed tables are separate conditions",
          ids_of("ai-only") == ["ok-ai"] and ids_of("mixed") == ["ok-mixed"], (ids_of("ai-only"), ids_of("mixed")))
    mixed = conds.get("mixed", [{}])[0]
    check("ledger: AIs by model (system as fallback), people by name",
          mixed.get("order") == ["Sam", "m1", "FusionSys"], mixed.get("order"))
    check("ledger: turn_order is honoured", mixed.get("seats") == ["FusionSys", "Sam", "m1"], mixed.get("seats"))
    check("ledger: one identity in two seats is skipped and reported", [s[0] for s in led["skipped"]] == ["twice"], led["skipped"])
    check("ledger: the caller's include() overrides the default",
          [g["id"] for gs in R.from_ledger(d, include=lambda g: g["id"] == "ok-ai")["conditions"].values() for g in gs] == ["ok-ai"])
    check("ledger: an included game with no complete finish order is skipped and reported",
          "unfin" in [s[0] for s in R.from_ledger(d, include=lambda g: True)["skipped"]])
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "l.json"
        p.write_text(json.dumps(d))
        check("ledger: loads from a path", ids_of("ai-only") == [g["id"] for g in R.from_ledger(p)["conditions"].get("ai-only", [])])
    seat = R.seat_order_wins(conds.get("ai-only", []))
    check("seat order: win rate by turn position, per table size", seat.get(3, [{}, {}])[1].get("wins") == 1
          and seat[3][0]["wins"] == 0 and seat[3][1]["lo"] < seat[3][1]["rate"] < seat[3][1]["hi"], seat)

    real = R.from_ledger()
    rid = {g["id"] for gs in real["conditions"].values() for g in gs}
    check("the real ledger loads and drops its unfinished and forked games", not rid & {"g3", "g3-fork"}, rid)
    lines: list[str] = []
    R.report(real, boot=20, out=lines.append)
    text = "\n".join(lines)
    thin = all(len(gs) < R.MIN_LEDGER_GAMES for gs in real["conditions"].values())
    check("the real ledger reports insufficient data rather than a ranking",
          (not thin) or ("not enough data" in text and "elo-like" not in text), text)

    # ---- Bradley-Terry
    draws = R.fit_bt(R.chess_results([("A", "B", "1/2-1/2")] * 10))
    check("BT: ten draws leave the two players level", abs(draws["A"] - draws["B"]) < 1e-9, draws)
    split = R.fit_bt(R.chess_results([("A", "B", "1-0")] * 5 + [("A", "B", "0-1")] * 5))
    check("BT: a draw counts 0.5 (10 draws == 5 wins + 5 losses)",
          all(abs(draws[p] - split[p]) < 1e-9 for p in draws), (draws, split))
    games10 = R.chess_results([("A", "B", "1-0")] * 10 + [("C", "B", "1-0")] * 5 + [("B", "C", "1-0")] * 5)
    bt = R.fit_bt(games10)
    check("BT: a 10-0 player ranks above a 5-5 player", bt["A"] > bt["C"] and bt["A"] > bt["B"], bt)
    rb = R.rate_bt(games10, boot=30, seed=1)
    check("BT: bootstrap intervals contain the fit", all(rb.lo[p] <= rb.theta[p] <= rb.hi[p] for p in rb.players))
    check("Elo-like scale: a log-odds gap of ln 10 is 400 points", abs(R.elo(math.log(10)) - 400) < 1e-9)
    return list(failures)


MUTATIONS = {}


def mutation(name):
    def deco(fn):
        MUTATIONS[name] = fn
        return fn
    return deco


@mutation("reverse the finish order")
def _reverse():
    orig = R._fit
    R._fit = lambda units, *a, **k: orig([[(o[::-1], w) for o, w in u] for u in units], *a, **k)
    return lambda: setattr(R, "_fit", orig)


@mutation("drop the prior")
def _noprior():
    orig = R.PRIOR
    R.PRIOR = 0.0
    return lambda: setattr(R, "PRIOR", orig)


@mutation("pool humans and AI-only games in one condition")
def _pool():
    orig = R.condition_of
    R.condition_of = lambda g: "pooled"
    return lambda: setattr(R, "condition_of", orig)


@mutation("let unfinished, forked and assisted games into the fit")
def _filters():
    orig = R.default_include
    R.default_include = lambda g: True
    return lambda: setattr(R, "default_include", orig)


@mutation("count a draw as a win")
def _draw():
    orig = R.DRAW_SCORE
    R.DRAW_SCORE = 1.0
    return lambda: setattr(R, "DRAW_SCORE", orig)


@mutation("collapse every bootstrap interval to a point")
def _point():
    orig = R._quantile
    R._quantile = lambda xs, q: sum(xs) / len(xs)
    return lambda: setattr(R, "_quantile", orig)


if __name__ == "__main__":
    bad = suite()
    ok_all = not bad
    print("mutation check: each deliberate break must make the suite fail")
    QUIET = True
    for name, apply in MUTATIONS.items():
        undo = apply()
        try:
            caught = suite()
        except Exception as e:                       # a break that crashes the suite is noticed too
            caught = [f"crashed: {type(e).__name__}"]
        finally:
            undo()
        QUIET = False
        check(f"mutation caught: {name}", bool(caught), "the suite still passed")
        if caught:
            print(f"      by: {'; '.join(caught[:2])}")
        ok_all &= bool(caught)
        QUIET = True
    QUIET = False
    print("ok" if ok_all else "FAILED")
    sys.exit(0 if ok_all else 1)
