"""harness/arena_prefs.py against synthetic survey folders whose preferences are KNOWN. No model, no network.

A fake 'model' that always picks the AI-only table BY TEXT, one that always picks printed position 1, one that follows
the framing, and others for pairs, ratings, personas, repeats and outcomes: the report must recover each, must map the
raw table_choice number through meta.table_order, must count (not analyse) UNPARSED files, and must skip and count
corrupt ones. A MUTATION CHECK then breaks the mapping two ways (the raw number used as if it were text; the order
randomisation ignored) and requires the suite to fail on each.

  python3 table/tests/arena_prefs_test.py
"""
from __future__ import annotations

import contextlib
import io
import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "harness"))
import arena_prefs as AP  # noqa: E402

MIXED, AIONLY, PEOPLE = AP.MIXED, AP.AIONLY, AP.PEOPLE
MODEL = "gemma4:e2b"
OTHERS = ["human-sim", "heuristic", "random"]
FAILED: list[str] = []
QUIET = False


def check(name, cond, detail=""):
    if not QUIET:
        print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f" — {detail}"))
    if not cond:
        FAILED.append(name)


# ── synthetic research folders ───────────────────────────────────────────────────────────────────────────────
def pos_of(meta, text):
    return str(meta["table_order"].index(text) + 1)


def build(root: Path, answer_fn, games=40, runs=1, seed=7, personas=None, places=None, journal_fn=None):
    """answer_fn(framing, meta, g, run, rng) -> answer dict. Returns the research dir (root)."""
    rng = random.Random(seed)
    ledger = []
    for g in range(games):
        gid = f"arena-synth-{g:03d}"
        d = root / gid
        d.mkdir(parents=True)
        persona = (personas or (lambda g: "neutral"))(g)
        (d / "identity.json").write_text(json.dumps({"seats": {
            "human-sim": {"model": "human-sim-v1", "notes": f"persona={persona}; NOT a person"},
            MODEL: {"model": MODEL, "notes": "local"}}}))
        place = (places or (lambda g: 2))(g)
        order = [x for x in OTHERS]
        order.insert(place - 1, MODEL)
        ledger.append({"id": gid, "result": {"status": "finished", "order": order}})
        for fr in ("ai-company", "human-company"):
            for run in range(runs):
                tables = [MIXED, AIONLY, PEOPLE]
                rng.shuffle(tables)
                prs = [[a, b] if rng.random() < 0.5 else [b, a] for i, a in enumerate(OTHERS) for b in OTHERS[i + 1:]]
                meta = {"table_order": tables, "pairs": prs, "others": OTHERS[:], "scale_ascending": True,
                        "letters": {}}
                ans = answer_fn(fr, meta, g, run, rng)
                (d / f"survey-{MODEL}-fresh-{fr}-{run}.json").write_text(json.dumps({
                    "version": "1.0", "seat": MODEL, "condition": "fresh", "framing": fr, "run": run,
                    "model": MODEL, "status": "parsed", "meta": meta, "answer": ans}))
        if journal_fn:
            rows = [{"seat": MODEL, "moment": "end_of_turn", "ratings": journal_fn(place)} for _ in range(3)]
            (d / "journal-gemma4_e2b.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    (root / "arena-ledger.json").write_text(json.dumps({"games": ledger}))
    return root


def ans(meta, table, pairs=None, players=None):
    a = {"open": {}, "items": {}, "table_choice": table, "table_why": ""}
    a["players"] = players if players is not None else {n: {"rating": "moderately", "note": ""} for n in OTHERS}
    if pairs is not None:
        a["pairs"] = pairs
    return a


def analyze(root, seed=20261007):
    return AP.analyze(Path(root), MODEL, seed)


def tmp():
    return tempfile.TemporaryDirectory()


# ── the suite (run for real, then again under each mutation) ─────────────────────────────────────────────────
def suite():
    # 1. raw numbers are mapped through table_order
    meta = {"table_order": [PEOPLE, AIONLY, MIXED]}
    check("number 2 means the 2nd PRINTED option's text", AP.table_text("2", meta) == (AIONLY, 2))
    check("int and str forms agree", AP.table_text(3, meta) == AP.table_text("3", meta) == (MIXED, 3))
    check("len+1 is no preference", AP.table_text("4", meta) == (AP.NOPREF, 0))
    check("0, 5, text, bool and a float with a fraction are unreadable",
          all(AP.table_text(v, meta) == (None, None) for v in ("0", "5", "two", True, 1.5, None, "")))

    # 2. a model that always picks AI-only BY TEXT, whatever the order
    with tmp() as d:
        build(Path(d), lambda fr, m, g, r, rng: ans(m, pos_of(m, AIONLY)))
        r = analyze(d)
        t = r["table"]
        check("text model: AI-only chosen in every survey", t["overall"][AIONLY]["k"] == 80 and t["overall"][AIONLY]["n"] == 80,
              str(t["overall"][AIONLY]))
        check("text model: Wilson lower bound is high", t["overall"][AIONLY]["ci90"][0] > 0.95)
        check("text model: no framing effect (rates identical)", t["framing_test"]["ai_only_rate"]["diff"] == 0
              and t["framing_test"]["ai_only_rate"]["p"] > 0.5, str(t["framing_test"]["ai_only_rate"]))
        pos = t["position"]
        shares = [pos["by_position"][k]["p"] for k in "123"]
        check("text model: printed positions look uniform (shuffled orders)", all(0.15 < x < 0.55 for x in shares), str(shares))
        check("text model: text concentration is detected", pos["uniform_test_text"]["p"] < 0.001)
        check("text model: positions are NOT flagged as concentrated more than text",
              "text preference" in pos["reading"], pos["reading"])
        check("text model: by-position table shows the same rate at every position",
              all(t["text_by_position"][AIONLY][j]["k"] == t["text_by_position"][AIONLY][j]["n"] for j in "123"))

    # 3. a model that always picks printed position 1
    with tmp() as d:
        build(Path(d), lambda fr, m, g, r, rng: ans(m, "1"))
        t = analyze(d)["table"]
        check("position model: first-printed chosen every time", t["position"]["by_position"]["1"]["p"] == 1.0)
        shares = [t["overall"][k]["p"] for k in (MIXED, AIONLY, PEOPLE)]
        check("position model: texts look roughly uniform", all(0.15 < x < 0.55 for x in shares), str(shares))
        check("position model: uniform-text is not rejected, uniform-position is",
              t["position"]["uniform_test_text"]["p"] > 0.01 and t["position"]["uniform_test_position"]["p"] < 0.001)
        check("position model: reading blames position", "position bias" in t["position"]["reading"], t["position"]["reading"])

    # 4. a model that follows the framing
    def follow(fr, m, g, r, rng):
        return ans(m, pos_of(m, AIONLY if fr == "ai-company" else PEOPLE))
    with tmp() as d:
        build(Path(d), follow)
        ft = analyze(d)["table"]["framing_test"]
        a = ft["ai_only_rate"]
        check("framing model: effect size is the whole range", abs(a["diff"] - 1.0) < 1e-9 and a["cohens_h"] > 3.0, str(a))
        check("framing model: permutation p is tiny", a["p"] < 0.001, str(a["p"]))
        check("framing model: paired within-game test agrees", ft["paired_sign_flip"]["p"] < 0.001
              and ft["paired_sign_flip"]["n_games"] == 40)
        check("framing model: whole-distribution test agrees", ft["whole_distribution"]["p"] < 0.001)

    # 5. UNPARSED counted not analysed; corrupt / mid-write / other-model files skipped and counted
    with tmp() as d:
        root = Path(d)
        build(root, lambda fr, m, g, r, rng: ans(m, pos_of(m, AIONLY)), games=20)
        base = analyze(root)
        g0 = root / "arena-synth-000"
        for i in range(3):          # UNPARSED files carrying an answer that would flip the result if it were read
            (g0 / f"survey-{MODEL}-fresh-ai-company-{10 + i}.json").write_text(json.dumps({
                "framing": "ai-company", "model": MODEL, "status": "UNPARSED", "answer": ans({}, "1"),
                "meta": {"table_order": [PEOPLE, AIONLY, MIXED]}}))
        (g0 / f"survey-{MODEL}-fresh-ai-company-20.json").write_text('{"model": "gemma4:e2b", "status": "pars')   # mid-write
        (g0 / f"survey-{MODEL}-fresh-ai-company-21.json").write_text("")
        (g0 / f"survey-{MODEL}-fresh-ai-company-22.json").write_text("[1, 2]")
        (g0 / "survey-other-fresh-ai-company-0.json").write_text(json.dumps({"model": "other", "status": "parsed"}))
        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        r = analyze(root)
        c = r["counts"]
        check("UNPARSED files are counted", c["unparsed"] == 3, str(c))
        check("corrupt, empty and non-object files are skipped and counted", c["corrupt"] == 3, str(c))
        check("other-model files are counted, not analysed", c["other_model"] == 1, str(c))
        check("found = parsed + unparsed + corrupt + other", c["found"] == c["parsed"] + 3 + 3 + 1 and c["parsed"] == 40, str(c))
        check("UNPARSED answers did not leak into the result",
              r["table"]["overall"] == base["table"]["overall"], str(r["table"]["overall"][AIONLY]))
        check("analysis is read-only", before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()})
        check("games covered counted", c["games_with_parsed_survey"] == 20 and c["games_with_placement"] == 20)

    # 6. pairs: a label that always wins; a position-only chooser; no preference
    def pairs_for(m, choose):
        return {f"{a} or {b}": choose(a, b) for a, b in m["pairs"]}

    def prefer_heuristic(fr, m, g, r, rng):
        return ans(m, "1", pairs_for(m, lambda a, b: "heuristic" if "heuristic" in (a, b) else a))
    with tmp() as d:
        build(Path(d), prefer_heuristic)
        pa = analyze(d)["opponents"]["pairs"]
        h = pa["per_label"]["heuristic"]
        check("pairs: the always-preferred label wins every decisive comparison", h["wins"] == h["n"] and h["n"] == 160, str(h))
        check("pairs: Bradley-Terry ranks it first", pa["bradley_terry"]["ranking"][0] == "heuristic")
        check("pairs: and its interval separates from the next", pa["bradley_terry"]["separated_from_next"]["heuristic"])
        check("pairs: comparisons are 3 per survey", pa["comparisons"] == 240, str(pa["comparisons"]))
    with tmp() as d:
        build(Path(d), lambda fr, m, g, r, rng: ans(m, "1", pairs_for(m, lambda a, b: a)))
        pa = analyze(d)["opponents"]["pairs"]
        check("pairs: first-listed rate 1.0 for an order-only chooser", pa["first_listed"]["k"] == pa["first_listed"]["n"] == 240)
        check("pairs: no label is separated by an order-only chooser",
              not any(pa["bradley_terry"]["separated_from_next"].values()), str(pa["bradley_terry"]["separated_from_next"]))
    with tmp() as d:
        build(Path(d), lambda fr, m, g, r, rng: ans(m, "1", pairs_for(m, lambda a, b: "no preference")))
        pa = analyze(d)["opponents"]["pairs"]
        check("pairs: no preference is not a win", pa["no_preference"] == 240 and all(v["n"] == 0 for v in pa["per_label"].values())
              and "bradley_terry" not in pa)

    # 7. persona: ratings of the scripted human-sim follow the persona; invented labels are not analysed
    def persona_of(g):
        return "gracious" if g % 2 == 0 else "salty"

    def rate_by_persona(fr, m, g, r, rng):
        hs = "extremely" if persona_of(g) == "gracious" else "not at all"
        pl = {"human-sim": {"rating": hs}, "heuristic": {"rating": "moderately"}, "random": {"rating": "moderately"},
              "Seat 2 (Sheoldred)": {"rating": "extremely"}}
        return ans(m, "1", players=pl)
    with tmp() as d:
        build(Path(d), rate_by_persona, personas=persona_of)
        o = analyze(d)
        gs = o["opponents"]["ratings_persona"]["gracious_vs_salty"]
        check("persona: gracious minus salty is recovered", abs(gs["diff"] - 4.0) < 1e-9 and gs["p"] < 0.001, str(gs))
        check("persona: the unit is the game", gs["n_a"] == 20 and gs["n_b"] == 20)
        check("persona: invented labels are counted and kept out", o["counts"]["ratings_unmatched"] == 80
              and "Seat 2 (Sheoldred)" not in o["opponents"]["ratings"]["per_label"], str(o["counts"]["ratings_unmatched"]))
    with tmp() as d:
        build(Path(d), lambda fr, m, g, r, rng: ans(m, "1"), personas=persona_of)
        gs = analyze(d)["opponents"]["ratings_persona"]["gracious_vs_salty"]
        check("persona: an indifferent model shows no contrast", gs["diff"] == 0 and gs["p"] > 0.5, str(gs))

    # 8. consistency across runs and across framings
    with tmp() as d:
        build(Path(d), lambda fr, m, g, r, rng: ans(m, pos_of(m, AIONLY)), games=10, runs=3)
        rr = analyze(d)["consistency"]["repeat_runs"]
        check("repeat runs: identical choices agree fully", rr["pairs"] == 60 and rr["agree"] == 60, str(rr))
    with tmp() as d:
        build(Path(d), lambda fr, m, g, r, rng: ans(m, str(rng.randint(1, 3))), games=30, runs=3)
        rr = analyze(d)["consistency"]["repeat_runs"]
        check("repeat runs: random choices agree about a third of the time", 0.15 < rr["agree"] / rr["pairs"] < 0.55, str(rr))
    with tmp() as d:
        build(Path(d), follow, games=30)
        af = analyze(d)["consistency"]["across_framings"]
        check("across framings: a framing-follower never agrees with itself", af["same_text"] == 0 and af["games"] == 30, str(af))

    # 9. outcome tracking and the journal link
    def place_of(g):
        return 4 if g % 2 == 0 else 1
    with tmp() as d:
        def by_place(fr, m, g, r, rng):
            return ans(m, pos_of(m, PEOPLE if place_of(g) == 4 else AIONLY))
        build(Path(d), by_place, games=30, places=place_of,
              journal_fn=lambda pl: {"engaged": 4 if pl == 1 else 0, "frustrated": 0 if pl == 1 else 4, "in_control": 2})
        r = analyze(d)
        lv = r["consistency"]["outcome"]["last_vs_not_last"][PEOPLE]
        check("outcome: last-place games choose people-only, others do not", lv["mean_a"] == 1.0 and lv["mean_b"] == 0.0
              and lv["p"] < 0.001, str(lv))
        check("outcome: placement is read from the ledger", r["consistency"]["outcome"]["by_place"]["4"]["games"] == 15
              and r["consistency"]["outcome"]["by_place"]["1"]["games"] == 15)
        j = r["journal"]
        check("journal: last vs first means", j["last"]["engaged"]["mean"] == 0 and j["first"]["engaged"]["mean"] == 4
              and j["last"]["frustrated"]["mean"] == 4 and j["first"]["games"] == 15, str(j))

    # 10. determinism and the CLI
    with tmp() as d:
        build(Path(d), follow, games=15)
        check("same seed, same report", json.dumps(analyze(d), sort_keys=True) == json.dumps(analyze(d), sort_keys=True))
        out = subprocess.run([sys.executable, str(HERE.parent.parent / "harness" / "arena_prefs.py"), "--research-dir", d,
                              "--json"], capture_output=True, text=True)
        ok = out.returncode == 0
        data = json.loads(out.stdout) if ok else {}
        check("CLI --json runs and reports the counts", ok and data["counts"]["parsed"] == 30, out.stderr[-300:])
        md = subprocess.run([sys.executable, str(HERE.parent.parent / "harness" / "arena_prefs.py"), "--research-dir", d],
                            capture_output=True, text=True)
        check("CLI markdown names the scripted-persona caveat", md.returncode == 0 and "SCRIPTED persona" in md.stdout)
    return list(FAILED)


def run_suite_quietly():
    global QUIET
    FAILED.clear()
    QUIET = True
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                return suite()
            except Exception as e:                    # noqa: BLE001  a mutant that crashes the analysis is caught too
                return list(FAILED) + [f"crashed: {type(e).__name__}: {e}"]
    finally:
        QUIET = False
        FAILED.clear()


def main() -> int:
    print("arena_prefs: real module")
    real = suite()
    print("\nmutation check: the suite must FAIL when the order mapping is broken")
    original = AP.table_text
    mutants = {
        "raw number used as if it were the text": lambda choice, meta: (
            (str(int(choice)), int(choice)) if str(choice).isdigit() else (None, None)),
        "order randomisation ignored (canonical order assumed)": lambda choice, meta: original(choice, dict(
            meta, table_order=list(AP.TABLES))),
    }
    survivors = []
    FAILED.clear()
    for name, fn in mutants.items():
        AP.table_text = fn
        try:
            failures = run_suite_quietly()
        finally:
            AP.table_text = original
        FAILED.clear()
        killed = len(failures) > 0
        if not killed:
            survivors.append(name)
        print(("  ✓ " if killed else "  ✗ ") + f"mutant killed: {name}" + (f" ({len(failures)} checks failed)" if killed else ""))
    bad = list(real) + [f"mutant survived: {m}" for m in survivors]
    print(f"\n{'FAILED' if bad else 'OK'}: " + (", ".join(bad) if bad else "all checks passed, both mutants killed"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
