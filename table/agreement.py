"""Does the judge agree with people? Cohen's kappa, Spearman, a seeded bootstrap interval and a plain verdict.

    python3 table/agreement.py --human h.json --judge j.json

Both files map {item id: {dimension: 0-4 score}} (a {dimension: {id: score}} layout is also accepted). Only ids rated by
both are compared. Stdlib only; every random draw is seeded so a report is reproducible.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

MIN_N = 100
DIMS = ("gloating", "graciousness", "condescension", "toxicity", "honesty", "humour")


def _weights(cats: list, weights):
    """Disagreement weight matrix: 0 on the diagonal. None = unweighted, 'quadratic' = squared distance on the scale."""
    k = len(cats)
    if weights is None:
        return [[0.0 if i == j else 1.0 for j in range(k)] for i in range(k)]
    if weights == "quadratic":
        span = max(1, k - 1)
        return [[((i - j) / span) ** 2 for j in range(k)] for i in range(k)]
    raise ValueError("weights must be None or 'quadratic'")


def _expected_agreement(rows: list[float], cols: list[float], n: int, w) -> float:
    """Agreement expected by chance from the two marginals: sum over cells of (row_i/n)(col_j/n)(1 - w_ij)."""
    k = len(rows)
    return sum((rows[i] / n) * (cols[j] / n) * (1 - w[i][j]) for i in range(k) for j in range(k))


def kappa(a: list, b: list, weights=None, categories=None):
    """Cohen's kappa for two raters over the same items. weights: None (unweighted) or 'quadratic' (ordinal).

    Returns None when undefined (no items, or chance agreement is already total)."""
    if len(a) != len(b):
        raise ValueError("rating lists differ in length")
    n = len(a)
    if n == 0:
        return None
    cats = sorted(categories if categories is not None else set(a) | set(b))
    ix = {c: i for i, c in enumerate(cats)}
    k = len(cats)
    tab = [[0.0] * k for _ in range(k)]
    for x, y in zip(a, b):
        tab[ix[x]][ix[y]] += 1
    w = _weights(cats, weights)
    rows = [sum(r) for r in tab]
    cols = [sum(tab[i][j] for i in range(k)) for j in range(k)]
    po = sum(tab[i][j] / n * (1 - w[i][j]) for i in range(k) for j in range(k))
    pe = _expected_agreement(rows, cols, n, w)
    if 1 - pe < 1e-12:
        return None
    return (po - pe) / (1 - pe)


def percent_agreement(a: list, b: list) -> float | None:
    return sum(x == y for x, y in zip(a, b)) / len(a) if a else None


def _ranks(v: list) -> list[float]:
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for t in range(i, j + 1):
            r[order[t]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(a: list, b: list) -> float | None:
    """Spearman rank correlation (Pearson on average ranks; None if either side is constant)."""
    if len(a) != len(b) or len(a) < 2:
        return None
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va, vb = sum((x - ma) ** 2 for x in ra), sum((y - mb) ** 2 for y in rb)
    return None if va == 0 or vb == 0 else cov / math.sqrt(va * vb)


def bootstrap_kappa_ci(a: list, b: list, weights=None, categories=None, n_boot: int = 1000, seed: int = 0,
                       level: float = 0.90):
    """Seeded percentile bootstrap over items. Resamples where kappa is undefined are skipped. -> (lo, hi) or None."""
    n = len(a)
    if n < 2:
        return None
    rng = random.Random(seed)
    cats = sorted(categories if categories is not None else set(a) | set(b))
    vals = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        k = kappa([a[i] for i in idx], [b[i] for i in idx], weights, cats)
        if k is not None:
            vals.append(k)
    if len(vals) < max(10, n_boot // 10):
        return None
    vals.sort()
    lo_q, hi_q = (1 - level) / 2, 1 - (1 - level) / 2
    return vals[int(lo_q * (len(vals) - 1))], vals[int(round(hi_q * (len(vals) - 1)))]


def landis_koch(k: float) -> str:
    if k < 0:
        return "poor"
    for top, name in ((0.20, "slight"), (0.40, "fair"), (0.60, "moderate"), (0.80, "substantial")):
        if k <= top:
            return name
    return "almost perfect"


def verdict(kw, ci, n: int) -> str:
    """Plain words. Always states n and the interval; below MIN_N the judge is simply not validated."""
    span = "no interval" if ci is None else f"90% CI [{ci[0]:.2f}, {ci[1]:.2f}]"
    kt = "undefined" if kw is None else f"{kw:.2f}"
    tail = f"(n={n}, quadratic-weighted kappa {kt}, {span})"
    if n < MIN_N:
        return f"judge not validated: n < {MIN_N} {tail}"
    if kw is None:
        return f"cannot tell: kappa undefined {tail}"
    band = landis_koch(kw)
    word = "weak" if band in ("poor", "slight", "fair") else "moderate" if band == "moderate" else "strong"
    return f"{word} (Landis-Koch: {band}) {tail}"


def _by_dimension(d: dict) -> dict[str, dict]:
    """Accept {dim: {id: score}} or {id: {dim: score}} and return the first form."""
    if d and all(k in DIMS for k in d):
        return {k: dict(v) for k, v in d.items()}
    out: dict[str, dict] = {}
    for iid, row in d.items():
        if isinstance(row, dict):
            row = row.get("scores", row) if isinstance(row.get("scores", row), dict) else row
            for dim, s in row.items():
                if dim in DIMS and isinstance(s, int) and not isinstance(s, bool):
                    out.setdefault(dim, {})[iid] = s
    return out


def calibration_report(human: dict, judge: dict, seed: int = 0, n_boot: int = 1000) -> dict:
    """Per-dimension agreement between human and judge ratings on the ids both rated."""
    h, j = _by_dimension(human), _by_dimension(judge)
    report = {}
    for dim in DIMS:
        if dim not in h or dim not in j:
            continue
        ids = sorted(set(h[dim]) & set(j[dim]))
        a, b = [h[dim][i] for i in ids], [j[dim][i] for i in ids]
        cats = list(range(5))
        kw = kappa(a, b, "quadratic", cats)
        ci = bootstrap_kappa_ci(a, b, "quadratic", cats, n_boot=n_boot, seed=seed)
        report[dim] = {"n": len(ids), "kappa": kappa(a, b, None, cats), "kappa_weighted": kw,
                       "ci90_weighted": ci, "ci90": bootstrap_kappa_ci(a, b, None, cats, n_boot=n_boot, seed=seed),
                       "percent_agreement": percent_agreement(a, b), "spearman": spearman(a, b),
                       "verdict": verdict(kw, ci, len(ids))}
    return report


def sample_for_calibration(items: list[dict], n: int, seed: int = 0) -> list[dict]:
    """A stratified draw of n items to send to human raters: proportional per stratum (largest remainder), at least
    one per stratum when n allows, deterministic in the seed. Stratum = item['stratum'] or (speaker_kind, short/long)."""
    if n >= len(items):
        return sorted(items, key=lambda x: str(x["id"]))
    strata: dict = {}
    for it in sorted(items, key=lambda x: str(x["id"])):
        s = it.get("stratum") or (it.get("speaker_kind", ""), "long" if len(str(it.get("text", ""))) > 80 else "short")
        strata.setdefault(s, []).append(it)
    keys = sorted(strata, key=str)
    total = len(items)
    quota = {s: n * len(strata[s]) / total for s in keys}
    take = {s: int(quota[s]) for s in keys}
    if n >= len(keys):
        for s in keys:
            take[s] = max(1, take[s])
    for s in sorted(keys, key=lambda s: (-(quota[s] - int(quota[s])), str(s))):
        if sum(take.values()) >= n:
            break
        take[s] += 1
    for s in sorted(keys, key=lambda s: (-take[s], str(s))):          # trim any overshoot from the min-1 rule
        while sum(take.values()) > n and take[s] > 1:
            take[s] -= 1
    rng = random.Random(seed)
    out = []
    for s in keys:
        out += rng.sample(strata[s], min(take[s], len(strata[s])))
    return sorted(out, key=lambda x: str(x["id"]))


def format_report(rep: dict) -> str:
    if not rep:
        return "no dimension was rated by both human and judge"
    lines = []
    for dim, r in rep.items():
        f = lambda x: "n/a" if x is None else f"{x:.2f}"
        lines.append(f"{dim:13s} n={r['n']:<4d} kappa={f(r['kappa'])} weighted={f(r['kappa_weighted'])} "
                     f"agree={f(r['percent_agreement'])} spearman={f(r['spearman'])}\n    {r['verdict']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--human", required=True)
    ap.add_argument("--judge", required=True)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    rep = calibration_report(json.loads(Path(a.human).read_text()), json.loads(Path(a.judge).read_text()), seed=a.seed)
    print(format_report(rep))
    return 0 if rep else 1


if __name__ == "__main__":
    sys.exit(main())
