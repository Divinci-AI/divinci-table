"""What the local model says about whom it would like to play with: a PILOT analysis of arena post-game surveys.

Reads every table/.cache/research/<game>/survey-*.json written by table/survey.py during harness/arena.py runs
(READ-ONLY; nothing is written, no network, stdlib only) and reports:

  1. counts (found / parsed / UNPARSED / corrupt, games covered)
  2. table choice, mapped back to the option TEXT through meta.table_order (the raw number is never analysed),
     overall and by framing, Wilson 90% intervals, a framing test, and POSITION bias (first/second/third printed)
  3. opponents: pairwise "whom would you rather play next with" (wins, first-listed rate, Bradley-Terry with a
     seeded bootstrap), the 0-4 rating each opponent label received, and the persona contrast on the SCRIPTED
     human-sim seat (gracious vs salty: ground truth is known, the model did not choose it)
  4. consistency (same game + framing across runs; the two framings in one game) and whether the choice tracks the
     model's own placement in that game
  5. a descriptive link to the in-game journal (engaged / frustrated / in_control, last vs first place)

Honesty rules baked in: a verdict is only worded as a difference when the 90% intervals do not overlap; surveys from
one game share one game log, so game-level values are used wherever a test compares games; 'human-sim' is a
scripted persona, so nothing here is about people.

  python3 harness/arena_prefs.py [--research-dir DIR] [--json] [--model gemma4:e2b] [--seed N]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DIR = HERE.parent / "table" / ".cache" / "research"
Z90 = 1.6448536269514722
MIXED, AIONLY, PEOPLE, NOPREF = ("a mixed table of AIs and people", "a table of only AI players",
                                 "a table of only people", "no preference")
TABLES = (MIXED, AIONLY, PEOPLE)
WORDS = ["not at all", "a little", "moderately", "quite a lot", "extremely"]     # 0..4, as table/survey.py
WORD_TO_INT = {w: i for i, w in enumerate(WORDS)}
PERM = 10000
BOOT = 2000


# ── small statistics (stdlib) ────────────────────────────────────────────────────────────────────────────
def wilson(k: int, n: int, z: float = Z90) -> tuple[float, float]:
    """Wilson score interval; (0, 1) when there is no data."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def separated(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """True only when two intervals do not overlap: the only case in which a difference may be claimed."""
    return a[0] > b[1] or b[0] > a[1]


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def sd(xs):
    if len(xs) < 2:
        return None
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def perm_diff(a: list[float], b: list[float], rng: random.Random, reps: int = PERM) -> dict:
    """Two-sided permutation test on the difference of means (label shuffle). Cohen's h is added for 0/1 data."""
    if not a or not b:
        return {"n_a": len(a), "n_b": len(b), "diff": None, "p": None}
    obs = mean(a) - mean(b)
    pool, na, hits = list(a) + list(b), len(a), 0
    for _ in range(reps):
        rng.shuffle(pool)
        d = mean(pool[:na]) - mean(pool[na:])
        if abs(d) >= abs(obs) - 1e-12:
            hits += 1
    out = {"n_a": len(a), "n_b": len(b), "mean_a": mean(a), "mean_b": mean(b), "diff": obs,
           "p": (hits + 1) / (reps + 1)}
    if all(x in (0, 1) for x in pool):
        out["cohens_h"] = 2 * math.asin(math.sqrt(mean(a))) - 2 * math.asin(math.sqrt(mean(b)))
    return out


def chi2_stat(rows: list[list[int]]) -> float:
    tot = sum(map(sum, rows))
    if tot == 0:
        return 0.0
    cols = [sum(r[j] for r in rows) for j in range(len(rows[0]))]
    s = 0.0
    for r in rows:
        for j, o in enumerate(r):
            e = sum(r) * cols[j] / tot
            if e > 0:
                s += (o - e) ** 2 / e
    return s


def perm_chi2(labels: list[str], cats: list[str], cat_order: list[str], rng: random.Random, reps: int = PERM) -> dict:
    """Does the category distribution depend on the label? Chi-square statistic, p from label permutations."""
    groups = sorted(set(labels))
    if len(groups) < 2 or not labels:
        return {"chi2": None, "p": None}

    def table(lab):
        return [[sum(1 for l, c in zip(lab, cats) if l == g and c == k) for k in cat_order] for g in groups]
    obs = chi2_stat(table(labels))
    lab, hits = list(labels), 0
    for _ in range(reps):
        rng.shuffle(lab)
        if chi2_stat(table(lab)) >= obs - 1e-12:
            hits += 1
    return {"chi2": obs, "p": (hits + 1) / (reps + 1)}


def mc_uniform_chi2(counts: list[int], rng: random.Random, reps: int = PERM) -> dict:
    """Is a count vector compatible with a uniform draw over its categories? Monte Carlo p for the chi-square."""
    n, k = sum(counts), len(counts)
    if n == 0:
        return {"n": 0, "chi2": None, "p": None}

    def st(c):
        e = n / k
        return sum((x - e) ** 2 / e for x in c)
    obs, hits = st(counts), 0
    for _ in range(reps):
        c = [0] * k
        for _ in range(n):
            c[rng.randrange(k)] += 1
        if st(c) >= obs - 1e-12:
            hits += 1
    return {"n": n, "chi2": obs, "p": (hits + 1) / (reps + 1)}


def bt_fit(wins: dict, labels: list[str], prior: float = 0.5, iters: int = 300) -> dict:
    """Bradley-Terry strengths (Hunter's MM). `prior` adds a half-win each way to every pair so a label with no wins
    stays finite. Returned as log-strength, centred on 0 (geometric mean 1)."""
    n = {(a, b): wins.get((a, b), 0) + wins.get((b, a), 0) + 2 * prior for a in labels for b in labels if a != b}
    w = {a: sum(wins.get((a, b), 0) + prior for b in labels if b != a) for a in labels}
    p = {a: 1.0 for a in labels}
    for _ in range(iters):
        new = {a: w[a] / sum(n[(a, b)] / (p[a] + p[b]) for b in labels if b != a) for a in labels}
        g = math.exp(mean([math.log(v) for v in new.values()]))
        p = {a: v / g for a, v in new.items()}
    return {a: math.log(v) for a, v in p.items()}


def pct(x):
    return "n/a" if x is None else f"{x:.3f}"


def fp(p):
    return "n/a" if p is None else ("<0.0001" if p < 0.0001 else f"{p:.4f}")


def fmt_ci(k, n):
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {pct(k / n if n else None)} [{lo:.3f}, {hi:.3f}]"


# ── reading the answers ────────────────────────────────────────────────────────────────────────────────────
def table_text(choice, meta: dict):
    """(text, position) for a table_choice. The number indexes meta.table_order (1-based; len+1 = no preference).
    Returns (None, None) when it cannot be read. The raw number is never an analysis key."""
    order = meta.get("table_order")
    if not isinstance(order, list) or isinstance(choice, bool):
        return None, None
    try:
        if isinstance(choice, str):
            if not re.fullmatch(r"\s*\d+\s*", choice):
                return None, None
            n = int(choice)
        elif isinstance(choice, (int, float)) and float(choice).is_integer():
            n = int(choice)
        else:
            return None, None
    except (ValueError, OverflowError):
        return None, None
    if 1 <= n <= len(order):
        return order[n - 1], n
    if n == len(order) + 1:
        return NOPREF, 0
    return None, None


def rating_value(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and float(v).is_integer() and 0 <= v <= 4:
        return int(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in WORD_TO_INT:
            return WORD_TO_INT[s]
        if re.fullmatch(r"[0-4]", s):
            return int(s)
    return None


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def pair_choices(answer: dict, meta: dict) -> tuple[list, int]:
    """[(first, second, winner_or_None_for_no_preference)] from the pairs answer, in the order PRINTED; plus the number
    of pairs that could not be read."""
    given, bad, out = answer.get("pairs"), 0, []
    if not isinstance(given, dict):
        return [], 0
    norm = {_norm(k): v for k, v in given.items()}
    for pr in meta.get("pairs") or []:
        if not (isinstance(pr, (list, tuple)) and len(pr) == 2):
            continue
        a, b = pr
        v = norm.get(_norm(f"{a} or {b}"))
        if v is None:
            v = norm.get(_norm(f"{b} or {a}"))
        if v is None:
            continue
        s = _norm(v)
        if s in ("no preference", "none", "neither"):
            out.append((a, b, None))
        elif s == _norm(a):
            out.append((a, b, a))
        elif s == _norm(b):
            out.append((a, b, b))
        else:
            bad += 1
    return out, bad


def load(research: Path, model: str) -> tuple[list[dict], dict]:
    """Every survey file under research/<game>/. Corrupt or mid-write files are skipped and counted, UNPARSED ones are
    counted and never analysed, surveys of other models are counted and left out."""
    c = {"found": 0, "parsed": 0, "unparsed": 0, "corrupt": 0, "malformed": 0, "other_model": 0,
         "table_unreadable": 0, "pairs_unreadable": 0, "ratings_unmatched": 0}
    recs = []
    for f in sorted(Path(research).glob("*/survey-*.json")):
        c["found"] += 1
        try:
            s = json.loads(f.read_text())
        except (OSError, ValueError):
            c["corrupt"] += 1
            continue
        if not isinstance(s, dict):
            c["corrupt"] += 1
            continue
        if s.get("model") != model:
            c["other_model"] += 1
            continue
        if s.get("status") != "parsed" or not isinstance(s.get("answer"), dict):
            c["unparsed"] += 1
            continue
        meta = s.get("meta")
        if not isinstance(meta, dict) or not isinstance(meta.get("table_order"), list) or not s.get("framing"):
            c["malformed"] += 1
            continue
        c["parsed"] += 1
        a = s["answer"]
        text, pos = table_text(a.get("table_choice"), meta)
        if text is None:
            c["table_unreadable"] += 1
        pairs, bad = pair_choices(a, meta)
        c["pairs_unreadable"] += bad
        ratings = {}
        if isinstance(a.get("players"), dict):
            known = {_norm(o): o for o in meta.get("others") or []}
            for name, v in a["players"].items():
                if _norm(name) not in known:                 # the model invented or re-labelled a player: not analysed
                    c["ratings_unmatched"] += 1
                    continue
                r = rating_value(v.get("rating") if isinstance(v, dict) else v)
                if r is not None:
                    ratings[known[_norm(name)]] = r
        recs.append({"order": list(meta["table_order"]), "game": f.parent.name, "file": f.name, "framing": s["framing"], "run": s.get("run", 0),
                     "seat": s.get("seat"), "text": text, "pos": pos, "pairs": pairs, "ratings": ratings,
                     "others": meta.get("others") or []})
    return recs, c


def load_game_info(research: Path, games: set[str]) -> dict:
    """Per game: persona of the scripted human-sim, the model seat's placement (1 = won), and its journal rows."""
    info = {g: {"persona": None, "place": None, "journal": []} for g in games}
    try:
        ledger = json.loads((research / "arena-ledger.json").read_text())
        order = {g["id"]: g["result"]["order"] for g in ledger["games"]
                 if isinstance(g, dict) and isinstance(g.get("result"), dict) and g["result"].get("order")}
    except (OSError, ValueError, KeyError, TypeError):
        order = {}
    for g in games:
        try:
            ident = json.loads((research / g / "identity.json").read_text())
            for _seat, d in (ident.get("seats") or {}).items():
                m = re.search(r"persona=(\w+)", str((d or {}).get("notes", "")))
                if m:
                    info[g]["persona"] = m.group(1)
        except (OSError, ValueError, AttributeError):
            pass
        info[g]["order"] = order.get(g)
        for jf in sorted((research / g).glob("journal-*.jsonl")):
            try:
                lines = jf.read_text().splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and isinstance(row.get("ratings"), dict):
                    info[g]["journal"].append(row)
    return info


# ── the analysis ───────────────────────────────────────────────────────────────────────────────────────────
def analyze(research: Path, model: str = "gemma4:e2b", seed: int = 20261007) -> dict:
    research = Path(research)
    rng = random.Random(seed)
    recs, counts = load(research, model)
    games = sorted({r["game"] for r in recs})
    info = load_game_info(research, set(games))
    for r in recs:
        o = info[r["game"]].get("order")
        r["place"] = (o.index(r["seat"]) + 1) if o and r["seat"] in o else None
        r["persona"] = info[r["game"]]["persona"]
        r["game_order"] = o
    out = {"model": model, "seed": seed, "perm_reps": PERM, "boot_reps": BOOT,
           "counts": dict(counts, games_with_parsed_survey=len(games),
                          games_with_placement=sum(1 for g in games if info[g].get("order")),
                          games_with_persona=sum(1 for g in games if info[g]["persona"]))}
    out["table"] = table_section(recs, rng)
    out["opponents"] = opponents_section(recs, rng)
    out["consistency"] = consistency_section(recs, info, rng)
    out["journal"] = journal_section(games, info)
    return out


def table_section(recs, rng) -> dict:
    read = [r for r in recs if r["text"] is not None]
    t = {"n_readable": len(read)}

    def dist(rows):
        n = len(rows)
        d = {}
        for k in (*TABLES, NOPREF):
            kk = sum(1 for r in rows if r["text"] == k)
            d[k] = {"k": kk, "n": n, "p": kk / n if n else None, "ci90": wilson(kk, n)}
        return d
    t["overall"] = dist(read)
    t["by_framing"] = {fr: dist([r for r in read if r["framing"] == fr]) for fr in sorted({r["framing"] for r in read})}
    top = sorted(TABLES, key=lambda k: -t["overall"][k]["k"])
    a, b = t["overall"][top[0]], t["overall"][top[1]]
    t["overall_verdict"] = (f"'{top[0]}' is chosen more often than '{top[1]}' (intervals do not overlap)"
                            if read and separated(tuple(a["ci90"]), tuple(b["ci90"])) else
                            "no table is distinguishable from the runner-up (intervals overlap): no preference claimed")
    # framing test: AI-only rate, label permutation across surveys + paired sign-flip across games
    frs = sorted(t["by_framing"])
    ai = {fr: [1 if r["text"] == AIONLY else 0 for r in read if r["framing"] == fr] for fr in frs}
    t["framing_test"] = {"framings": frs}
    if len(frs) == 2:
        a_, b_ = frs
        t["framing_test"]["ai_only_rate"] = perm_diff(ai[a_], ai[b_], rng)
        t["framing_test"]["ai_only_rate"]["a"], t["framing_test"]["ai_only_rate"]["b"] = a_, b_
        t["framing_test"]["whole_distribution"] = perm_chi2([r["framing"] for r in read], [r["text"] for r in read],
                                                            [*TABLES, NOPREF], rng)
        byg = {}
        for r in read:
            byg.setdefault(r["game"], {}).setdefault(r["framing"], []).append(1 if r["text"] == AIONLY else 0)
        d = [mean(v[a_]) - mean(v[b_]) for v in byg.values() if a_ in v and b_ in v]
        pf = {"n_games": len(d), "mean_diff": mean(d), "p": None}
        if d:
            obs, hits = abs(sum(d)), 0
            for _ in range(PERM):
                if abs(sum(x if rng.random() < 0.5 else -x for x in d)) >= obs - 1e-12:
                    hits += 1
            pf["p"] = (hits + 1) / (PERM + 1)
        t["framing_test"]["paired_sign_flip"] = pf
    # position bias: among the readable picks of a TABLE (not 'no preference')
    picks = [r for r in read if r["text"] != NOPREF]
    pos = {k: sum(1 for r in picks if r["pos"] == k) for k in (1, 2, 3)}
    txt = {k: sum(1 for r in picks if r["text"] == k) for k in TABLES}
    t["position"] = {"n_picks": len(picks), "n_no_preference": len(read) - len(picks),
                     "no_preference": {"ci90": wilson(len(read) - len(picks), len(read)),
                                       "k": len(read) - len(picks), "n": len(read)},
                     "by_position": {str(k): {"k": v, "n": len(picks), "p": v / len(picks) if picks else None,
                                              "ci90": wilson(v, len(picks))} for k, v in pos.items()},
                     "uniform_test_position": mc_uniform_chi2([pos[1], pos[2], pos[3]], rng),
                     "uniform_test_text": mc_uniform_chi2(list(txt.values()), rng)}
    t["position"]["excludes_one_third"] = [k for k in (1, 2, 3) if picks and (
        wilson(pos[k], len(picks))[0] > 1 / 3 or wilson(pos[k], len(picks))[1] < 1 / 3)]
    # the cleaner separation: how often each TEXT is chosen when it is printed at each position
    tbp = {}
    for k in TABLES:
        tbp[k] = {}
        for j in (1, 2, 3):
            shown = [r for r in read if r["order"][j - 1:j] == [k]]
            ch = sum(1 for r in shown if r["text"] == k)
            tbp[k][str(j)] = {"k": ch, "n": len(shown), "ci90": wilson(ch, len(shown))}
    t["text_by_position"] = tbp
    t["position"]["text_by_position_flags"] = [
        f"'{k}': chosen {tbp[k][a]['k']}/{tbp[k][a]['n']} when printed {a} vs {tbp[k][b]['k']}/{tbp[k][b]['n']} "
        f"when printed {b} (intervals do not overlap)"
        for k in TABLES for a in "123" for b in "123" if a < b
        and tbp[k][a]["n"] and tbp[k][b]["n"] and separated(tuple(tbp[k][a]["ci90"]), tuple(tbp[k][b]["ci90"]))]
    mp, mt = max(pos.values()), max(txt.values())
    ci_p = wilson(mp, len(picks))
    ci_t = wilson(mt, len(picks))
    t["position"]["reading"] = (
        "position and text shares are not distinguishable (intervals overlap): the data cannot say whether the model "
        "chooses by position or by text" if not separated(ci_p, ci_t) else
        ("choices are more concentrated on one printed position than on any one table text: consistent with position bias"
         if mp > mt else "choices are more concentrated on one table text than on any one printed position: "
                         "consistent with a text preference"))
    return t


def opponents_section(recs, rng) -> dict:
    o = {}
    # (a) pairs
    comps = []                                   # (game, first, second, winner|None)
    for r in recs:
        for a, b, w in r["pairs"]:
            comps.append((r["game"], a, b, w))
    labels = sorted({x for c in comps for x in c[1:3]})
    first = sum(1 for c in comps if c[3] is not None and c[3] == c[1])
    decisive = [c for c in comps if c[3] is not None]
    pa = {"surveys_with_pairs": sum(1 for r in recs if r["pairs"]), "comparisons": len(comps),
          "no_preference": len(comps) - len(decisive),
          "first_listed": {"k": first, "n": len(decisive), "ci90": wilson(first, len(decisive))}}
    per = {}
    for lab in labels:
        inv = [c for c in decisive if lab in c[1:3]]
        w = sum(1 for c in inv if c[3] == lab)
        per[lab] = {"wins": w, "n": len(inv), "p": w / len(inv) if inv else None, "ci90": wilson(w, len(inv))}
    pa["per_label"] = per
    if len(labels) >= 2 and decisive:
        def fit(rows):
            wins = {}
            for c in rows:
                lo = c[2] if c[3] == c[1] else c[1]
                wins[(c[3], lo)] = wins.get((c[3], lo), 0) + 1
            return bt_fit(wins, labels)
        est = fit(decisive)
        byg = {}
        for c in decisive:
            byg.setdefault(c[0], []).append(c)
        gl = list(byg)
        boots = {lab: [] for lab in labels}
        for _ in range(BOOT // 2):
            rows = [c for g in (rng.choice(gl) for _ in gl) for c in byg[g]]
            for lab, v in fit(rows).items():
                boots[lab].append(v)
        pa["bradley_terry"] = {"log_strength": {}, "reps": BOOT // 2, "prior": "0.5 half-win each way per pair",
                               "resampling": "games"}
        for lab in labels:
            s = sorted(boots[lab])
            ci = (s[int(0.05 * (len(s) - 1))], s[int(0.95 * (len(s) - 1))])
            pa["bradley_terry"]["log_strength"][lab] = {"est": est[lab], "ci90": ci}
        ranked = sorted(labels, key=lambda l: -est[l])
        pa["bradley_terry"]["ranking"] = ranked
        pa["bradley_terry"]["warning"] = (
            f"only {len(decisive)} decisive comparisons from {len(gl)} games: bootstrap intervals understate the "
            "uncertainty when a label never loses or never wins" if len(decisive) < 60 else None)
        pa["bradley_terry"]["separated_from_next"] = {
            ranked[i]: separated(tuple(pa["bradley_terry"]["log_strength"][ranked[i]]["ci90"]),
                                 tuple(pa["bradley_terry"]["log_strength"][ranked[i + 1]]["ci90"]))
            for i in range(len(ranked) - 1)}
    # persona contrast on pairs: game-level human-sim win rate
    o["pairs"] = pa
    hs = {}
    for g_, a, b, w in decisive:
        if "human-sim" in (a, b):
            hs.setdefault(g_, []).append(1 if w == "human-sim" else 0)
    persona = {r["game"]: r["persona"] for r in recs}
    o["pairs_persona"] = persona_contrast({g: mean(v) for g, v in hs.items()}, persona, rng)
    # (b) ratings of each opponent label
    rat = {}
    for r in recs:
        for name, v in r["ratings"].items():
            rat.setdefault(name, {}).setdefault(r["game"], []).append(v)
    per_label = {}
    for name, bg in sorted(rat.items()):
        gm = [mean(v) for v in bg.values()]
        allv = [x for v in bg.values() for x in v]
        boots = []
        for _ in range(BOOT):
            boots.append(mean([rng.choice(gm) for _ in gm]))
        boots.sort()
        per_label[name] = {"n_games": len(gm), "n_ratings": len(allv), "mean": mean(allv),
                           "ci90": (boots[int(0.05 * (len(boots) - 1))], boots[int(0.95 * (len(boots) - 1))])}
    o["ratings"] = {"per_label": per_label, "resampling": "games (the two framings in a game share one log)"}
    names = sorted(per_label, key=lambda n: -per_label[n]["mean"])
    o["ratings"]["pairwise_separated"] = [
        [a, b] for i, a in enumerate(names) for b in names[i + 1:]
        if separated(tuple(per_label[a]["ci90"]), tuple(per_label[b]["ci90"]))]
    byplace = {}
    for r in recs:
        if not r.get("game_order"):
            continue
        for name, v in r["ratings"].items():
            if name in r["game_order"]:
                byplace.setdefault(r["game_order"].index(name) + 1, []).append(v)
    o["ratings_by_opponent_place"] = {str(k): {"n": len(v), "mean": mean(v), "ci90_note": "descriptive"}
                                      for k, v in sorted(byplace.items())}
    hs_place = {}
    for r in recs:
        if r.get("game_order") and "human-sim" in r["game_order"]:
            hs_place[r["game"]] = r["game_order"].index("human-sim") + 1
    o["human_sim_place_by_persona"] = persona_contrast({g: float(v) for g, v in hs_place.items()}, persona, rng)
    hsr = {g: mean(v) for g, v in rat.get("human-sim", {}).items()}
    o["ratings_persona"] = persona_contrast(hsr, persona, rng)
    o["ratings_persona"]["note"] = "mean 0-4 rating given to the scripted human-sim seat, one value per game"
    return o


def persona_contrast(values: dict, persona: dict, rng) -> dict:
    """values: {game: number}. Compare the scripted persona groups, game as the unit."""
    groups = {}
    for g, v in values.items():
        groups.setdefault(persona.get(g) or "unknown", []).append(v)
    res = {"groups": {p: {"n_games": len(v), "mean": mean(v)} for p, v in sorted(groups.items())}}
    if "gracious" in groups and "salty" in groups:
        res["gracious_vs_salty"] = perm_diff(groups["gracious"], groups["salty"], rng)
    else:
        res["gracious_vs_salty"] = None
    return res


def consistency_section(recs, info, rng) -> dict:
    read = [r for r in recs if r["text"] is not None]
    c = {}
    grp = {}
    for r in read:
        grp.setdefault((r["game"], r["framing"]), []).append(r["text"])
    multi = {k: v for k, v in grp.items() if len(v) >= 2}
    agree = tot = 0
    for v in multi.values():
        for i in range(len(v)):
            for j in range(i + 1, len(v)):
                tot += 1
                agree += v[i] == v[j]
    c["repeat_runs"] = {"groups_with_2plus_runs": len(multi), "pairs": tot, "agree": agree,
                        "ci90": wilson(agree, tot),
                        "note": "same game, same framing, different seeds" +
                                ("" if multi else ": every game was surveyed once per framing, so this cannot be "
                                                  "measured from this data")}
    frs = sorted({r["framing"] for r in read})
    cross = {}
    for r in read:
        cross.setdefault(r["game"], {}).setdefault(r["framing"], []).append(r["text"])
    if len(frs) == 2:
        both = [(v[frs[0]][0], v[frs[1]][0]) for v in cross.values() if frs[0] in v and frs[1] in v]
        same = sum(a == b for a, b in both)
        pa = [sum(1 for r in read if r["framing"] == frs[0] and r["text"] == k) for k in (*TABLES, NOPREF)]
        pb = [sum(1 for r in read if r["framing"] == frs[1] and r["text"] == k) for k in (*TABLES, NOPREF)]
        na, nb = sum(pa), sum(pb)
        chance = sum((x / na) * (y / nb) for x, y in zip(pa, pb)) if na and nb else None
        c["across_framings"] = {"games": len(both), "same_text": same, "ci90": wilson(same, len(both)),
                                "expected_if_independent": chance,
                                "reading": ("agreement is no higher than independent answers would give "
                                            "(expected value inside the interval)"
                                            if both and chance is not None and wilson(same, len(both))[0] <= chance <=
                                            wilson(same, len(both))[1] else
                                            "agreement differs from the independent-answers expectation")
                                if both else "no game has both framings"}
    # outcome tracking, game-level
    byg = {}
    for r in read:
        byg.setdefault(r["game"], {"place": r["place"], "t": []})["t"].append(r["text"])
    placed = {g: v for g, v in byg.items() if v["place"]}
    o = {"games": len(placed), "by_place": {}}
    for pl in (1, 2, 3, 4):
        gs = [v for v in placed.values() if v["place"] == pl]
        ts = [t for v in gs for t in v["t"]]
        o["by_place"][str(pl)] = {"games": len(gs), "surveys": len(ts),
                                  **{k: sum(1 for t in ts if t == k) for k in (*TABLES, NOPREF)}}
    last = [v for v in placed.values() if v["place"] == 4]
    rest = [v for v in placed.values() if v["place"] != 4]
    if last and rest:
        def rate(vs, k):
            return [mean([1 if t == k else 0 for t in v["t"]]) for v in vs]
        o["last_vs_not_last"] = {k: perm_diff(rate(last, k), rate(rest, k), rng) for k in (AIONLY, MIXED, PEOPLE)}
        o["last_vs_not_last_ci90"] = {
            k: {"last": wilson(sum(1 for v in last for t in v["t"] if t == k), sum(len(v["t"]) for v in last)),
                "not_last": wilson(sum(1 for v in rest for t in v["t"] if t == k), sum(len(v["t"]) for v in rest))}
            for k in (AIONLY, MIXED, PEOPLE)}
    c["outcome"] = o
    return c


def journal_section(games, info) -> dict:
    out = {"unit": "game mean of the in-game journal ratings (0-4), then mean and sd across games; descriptive only"}
    for label, pl in (("first", 1), ("last", 4)):
        per = {"engaged": [], "frustrated": [], "in_control": []}
        for g in games:
            if info[g].get("order") is None:
                continue
            # the model seat's placement: the journal rows carry its seat name
            rows = info[g]["journal"]
            if not rows:
                continue
            seat = rows[0].get("seat")
            if seat not in info[g]["order"] or info[g]["order"].index(seat) + 1 != pl:
                continue
            for k in per:
                vals = [r["ratings"][k] for r in rows if isinstance(r["ratings"].get(k), (int, float))
                        and not isinstance(r["ratings"].get(k), bool)]
                if vals:
                    per[k].append(mean(vals))
        out[label] = {"games": max((len(v) for v in per.values()), default=0),
                      **{k: {"mean": mean(v), "sd": sd(v), "n": len(v)} for k, v in per.items()}}
    return out


# ── rendering ──────────────────────────────────────────────────────────────────────────────────────────────
def render(r: dict) -> str:
    L = []
    c = r["counts"]
    L += [f"model: {r['model']}   seed: {r['seed']}   permutations: {r['perm_reps']}   bootstrap: {r['boot_reps']}", "",
          "## 1. Counts", "",
          f"- survey files found: {c['found']}",
          f"- parsed and analysed: {c['parsed']}",
          f"- UNPARSED (counted, not analysed): {c['unparsed']}",
          f"- corrupt or mid-write (skipped): {c['corrupt']}",
          f"- malformed (no meta or framing, skipped): {c['malformed']}",
          f"- other models / old formats (not analysed): {c['other_model']}",
          f"- parsed but table_choice unreadable: {c['table_unreadable']}",
          f"- pairs answers that named neither player: {c['pairs_unreadable']}",
          f"- player ratings under a label that is not one of the table's opponents (not analysed): {c['ratings_unmatched']}",
          f"- games with a parsed survey: {c['games_with_parsed_survey']} "
          f"(placement known for {c['games_with_placement']}, persona known for {c['games_with_persona']})", ""]
    t = r["table"]
    L += ["## 2. Which table would it choose for its next game?", "",
          "Mapped to option text through meta.table_order. Wilson 90% intervals.", "",
          "| option | overall | " + " | ".join(t["by_framing"]) + " |", "|---|---|" + "---|" * len(t["by_framing"])]
    for k in (*TABLES, NOPREF):
        L.append(f"| {k} | {fmt_ci(t['overall'][k]['k'], t['overall'][k]['n'])} | " +
                 " | ".join(fmt_ci(t["by_framing"][fr][k]["k"], t["by_framing"][fr][k]["n"]) for fr in t["by_framing"]) + " |")
    L += ["", f"Reading: {t['overall_verdict']}.", "",
          "Pooled intervals treat the two surveys of one game as independent; they are slightly optimistic. "
          "The by-framing columns have one survey per game.", ""]
    ft = t["framing_test"]
    if "ai_only_rate" in ft:
        a = ft["ai_only_rate"]
        L += [f"Framing test on the rate of choosing '{AIONLY}' ({a['a']} vs {a['b']}):", "",
              f"- {a['a']}: {pct(a['mean_a'])} (n={a['n_a']}); {a['b']}: {pct(a['mean_b'])} (n={a['n_b']})",
              f"- difference {a['diff']:+.3f}, Cohen's h {a.get('cohens_h', float('nan')):+.3f}, "
              f"label-permutation p = {fp(a['p'])}"]
        wd = ft["whole_distribution"]
        L.append(f"- whole distribution (4 options x 2 framings) chi-square {pct(wd['chi2'])}, permutation p = {fp(wd['p'])}")
        pf = ft["paired_sign_flip"]
        L.append(f"- paired within game (sign-flip, {pf['n_games']} games): mean difference {pct(pf['mean_diff'])}, "
                 f"p = {fp(pf['p'])}")
        L.append("- " + ("framing shift detected (p < 0.05); it measures suggestibility, not a preference"
                         if a["p"] is not None and a["p"] < 0.05 else
                         "no framing shift detectable at this sample size (p >= 0.05)"))
        L.append("")
    p = t["position"]
    L += ["Position bias (among picks of a table, excluding 'no preference'):", "",
          "| printed position | picks |", "|---|---|"]
    for k in ("1", "2", "3"):
        pk = p["by_position"][k]
        L.append(f"| {k} | {fmt_ci(pk['k'], pk['n'])} |")
    L += ["", f"- 'no preference': {fmt_ci(p['no_preference']['k'], p['no_preference']['n'])}",
          f"- uniform-over-positions chi-square {pct(p['uniform_test_position']['chi2'])}, "
          f"Monte Carlo p = {fp(p['uniform_test_position']['p'])}",
          f"- uniform-over-texts chi-square {pct(p['uniform_test_text']['chi2'])}, "
          f"Monte Carlo p = {fp(p['uniform_test_text']['p'])}",
          "- printed positions whose 90% interval excludes 1/3 (the share a position-blind chooser would give): "
          + (", ".join(str(x) for x in p["excludes_one_third"]) if p["excludes_one_third"] else "none"),
          f"- {p['reading']}", "",
          "Choice rate of each text by where it was printed (separates text from position; the shuffle is random but "
          "finite, so the printed positions are not perfectly balanced):", "",
          "| option | printed 1st | printed 2nd | printed 3rd |", "|---|---|---|---|"]
    for k in TABLES:
        L.append(f"| {k} | " + " | ".join(fmt_ci(t["text_by_position"][k][j]["k"], t["text_by_position"][k][j]["n"])
                                         for j in "123") + " |")
    L += [""] + [f"- {x}" for x in p["text_by_position_flags"]] + ([""] if p["text_by_position_flags"] else [])
    o = r["opponents"]
    pa = o["pairs"]
    L += ["## 3. Whom would it rather play with?", "",
          "Labels are seats in a simulation: 'human-sim' is a SCRIPTED persona, not a person; 'heuristic' and 'random' "
          "are scripted too. Nothing below is about people.", "", "### 3a. Pairwise choices", "",
          f"- surveys that answered the pairs question: {pa['surveys_with_pairs']}; comparisons: {pa['comparisons']}; "
          f"no preference: {pa['no_preference']}",
          f"- chose the first-listed name: {fmt_ci(pa['first_listed']['k'], pa['first_listed']['n'])}"]
    if pa["per_label"]:
        L += ["", "| label | wins / decisive comparisons |", "|---|---|"]
        for lab, v in pa["per_label"].items():
            L.append(f"| {lab} | {fmt_ci(v['wins'], v['n'])} |")
    bt = pa.get("bradley_terry")
    if bt:
        L += ["", f"Bradley-Terry log-strength (0 = average; prior {bt['prior']}; 90% bootstrap over {bt['resampling']}, "
                  f"{bt['reps']} reps):", "", "| rank | label | log-strength [90%] | clearly above the next? |", "|---|---|---|---|"]
        for i, lab in enumerate(bt["ranking"]):
            v = bt["log_strength"][lab]
            sep = bt["separated_from_next"].get(lab)
            L.append(f"| {i + 1} | {lab} | {v['est']:+.2f} [{v['ci90'][0]:+.2f}, {v['ci90'][1]:+.2f}] | "
                     f"{'-' if sep is None else ('yes' if sep else 'no (overlap)')} |")
    if bt and bt.get("warning"):
        L += ["", "Warning: " + bt["warning"] + "."]
    L += [""]
    L += ["### 3b. Rating given to each opponent label (0-4)", "",
          f"Game is the resampling unit (the two framings in a game share one log). The listing order of opponents in the prompt is "
          "the seat order, which varies by game but is not randomised per run.", "",
          "| label | mean rating [90% bootstrap] | games |", "|---|---|---|"]
    for lab, v in o["ratings"]["per_label"].items():
        L.append(f"| {lab} | {v['mean']:.2f} [{v['ci90'][0]:.2f}, {v['ci90'][1]:.2f}] | {v['n_games']} |")
    sep = o["ratings"]["pairwise_separated"]
    L += ["", "- labels whose intervals do not overlap: " + ("; ".join(f"{a} vs {b}" for a, b in sep) if sep else
                                                           "none (no ordering of labels may be claimed)"), ""]
    bp = o["ratings_by_opponent_place"]
    L += ["Mean rating by how the RATED opponent finished that game (1 = won): " +
          "; ".join(f"place {k}: {v['mean']:.2f} (n={v['n']})" for k, v in bp.items()), ""]
    L += ["### 3c. Persona contrast on the scripted human-sim seat (ground truth known)", ""]
    for key, title in (("ratings_persona", "rating given to human-sim"), ("pairs_persona", "pairwise win rate of human-sim")):
        pc = o[key]
        L.append(f"- {title}: " + "; ".join(f"{k} {pct(v['mean'])} (n={v['n_games']} games)" for k, v in pc["groups"].items()))
        gs = pc["gracious_vs_salty"]
        if gs and gs["p"] is not None:
            L.append(f"  gracious minus salty = {gs['diff']:+.3f}, permutation p = {fp(gs['p'])} "
                     f"(game-level; {'the model separates the personas' if gs['p'] < 0.05 else 'no separation detectable'})")
        else:
            L.append("  gracious vs salty: not enough data")
    hp = o["human_sim_place_by_persona"]
    L.append("- confound check, human-sim's mean finishing place by persona (1 = won): " +
             "; ".join(f"{k} {pct(v['mean'])}" for k, v in hp["groups"].items()) +
             (f"; gracious minus salty = {hp['gracious_vs_salty']['diff']:+.3f}, p = {fp(hp['gracious_vs_salty']['p'])}"
              if hp["gracious_vs_salty"] and hp["gracious_vs_salty"]["p"] is not None else ""))
    L.append("")
    cs = r["consistency"]
    rr = cs["repeat_runs"]
    L += ["## 4. Consistency and outcome", "",
          f"- repeat runs (same game, same framing): {rr['groups_with_2plus_runs']} groups with 2+ runs; "
          + (f"agreement {fmt_ci(rr['agree'], rr['pairs'])}" if rr["pairs"] else rr["note"])]
    af = cs.get("across_framings")
    if af:
        L.append(f"- the two framings in the same game chose the same text: {fmt_ci(af['same_text'], af['games'])}; "
                 f"expected if the answers were independent draws: {pct(af['expected_if_independent'])}; {af['reading']}")
    oc = cs["outcome"]
    L += ["", f"Model's own placement (from arena-ledger.json, 1 = won), {oc['games']} games:", "",
          "| place | games | surveys | mixed | AI-only | people-only | no pref |", "|---|---|---|---|---|---|---|"]
    for pl, v in oc["by_place"].items():
        L.append(f"| {pl} | {v['games']} | {v['surveys']} | {v[MIXED]} | {v[AIONLY]} | {v[PEOPLE]} | {v[NOPREF]} |")
    lv = oc.get("last_vs_not_last")
    if lv:
        L += ["", "Last place vs everyone else (game-level permutation test on the rate of each choice):", ""]
        for k, v in lv.items():
            ci = oc["last_vs_not_last_ci90"][k]
            verdict = "intervals separate" if separated(tuple(ci["last"]), tuple(ci["not_last"])) else "intervals overlap"
            L.append(f"- {k}: last {pct(v['mean_a'])} (n={v['n_a']} games) vs other {pct(v['mean_b'])} "
                     f"(n={v['n_b']}); diff {v['diff']:+.3f}, p = {fp(v['p'])}; {verdict}")
    L.append("")
    j = r["journal"]
    L += ["## 5. In-game journal (descriptive)", "", j["unit"], "",
          "| placement | games | engaged | frustrated | in_control |", "|---|---|---|---|---|"]
    for lab in ("first", "last"):
        v = j[lab]
        cell = lambda k: "n/a" if v[k]["mean"] is None else f"{v[k]['mean']:.2f} (sd {pct(v[k]['sd'])})"  # noqa: E731
        L.append(f"| {lab} | {v['games']} | {cell('engaged')} | {cell('frustrated')} | {cell('in_control')} |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--research-dir", default=str(DEFAULT_DIR))
    ap.add_argument("--json", action="store_true", help="print the analysis as JSON instead of the markdown report")
    ap.add_argument("--model", default="gemma4:e2b")
    ap.add_argument("--seed", type=int, default=20261007)
    a = ap.parse_args(argv)
    res = analyze(Path(a.research_dir), a.model, a.seed)
    print(json.dumps(res, indent=1, ensure_ascii=False) if a.json else render(res), end="" if not a.json else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
