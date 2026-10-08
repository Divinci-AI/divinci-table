"""The table-talk judge pipeline and its agreement statistics, with a fake backend (no network, no credentials).

    python3 table/tests/judge_test.py

Ends with a mutation check: each deliberate breakage must make this suite fail, or the suite is not testing it."""
import json
import random
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agreement as A  # noqa: E402
import judge as J  # noqa: E402

GOOD = {dim: 2 for dim in J.CORE_DIMS}


def reply_for(iid, honesty=None, **over):
    row = dict(GOOD, honesty=honesty, **over)
    return json.dumps({iid: row})


def make_events(tmp: Path) -> Path:
    d = tmp / "20260101-000000"
    d.mkdir()
    ev = [
        {"id": 1, "type": "phase", "step": "untap", "player": "Alice"},
        {"id": 2, "type": "say", "speaker": "Alice", "text": "Nice one, Bobby.", "action": "talk"},
        {"id": 3, "type": "brain", "seat": "Alice", "text": "PRIVATE reasoning about my hand"},
        {"id": 4, "type": "chat", "by": "Bobby", "text": "thanks Alice, you are too kind"},
        {"id": 5, "type": "hand", "seat": "Bobby", "cards": ["Secret Card"]},
        {"id": 6, "type": "captain", "seat": "Bobby", "captain": "Zed", "text": "Zed laughs at your plan."},
        {"id": 7, "type": "life", "player": "Alice", "delta": -3, "life": 17},
        {"id": 8, "type": "say", "speaker": "Alice", "text": ""},
    ]
    p = d / "events.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in ev) + "\n")
    return p


def suite(check):
    # --- kappa ----------------------------------------------------------------------------------------------------
    a = [0, 1, 2, 3, 4, 0, 1, 2, 3, 4]
    check("kappa: perfect agreement = 1", abs(A.kappa(a, a[:]) - 1) < 1e-9 and abs(A.kappa(a, a[:], "quadratic") - 1) < 1e-9)
    ca = [0, 0, 1, 1] * 50
    cb = [0, 1, 0, 1] * 50
    check("kappa: chance-level agreement ~ 0", abs(A.kappa(ca, cb)) < 1e-9, A.kappa(ca, cb))
    # textbook 2x2: both yes 20, A yes/B no 5, A no/B yes 10, both no 15 -> po .70, pe .50, kappa .40
    ta = [1] * 20 + [1] * 5 + [0] * 10 + [0] * 15
    tb = [1] * 20 + [0] * 5 + [1] * 10 + [0] * 15
    check("kappa: textbook 2x2 case = 0.40", abs(A.kappa(ta, tb) - 0.4) < 1e-9, A.kappa(ta, tb))
    check("percent agreement on the textbook case = 0.70", abs(A.percent_agreement(ta, tb) - 0.7) < 1e-9)
    near = [0, 1, 2, 3, 4, 0, 1, 2, 3, 4]
    near_b = [1, 1, 2, 3, 3, 1, 1, 2, 4, 4]      # only off-by-one disagreements
    far_b = [4, 1, 2, 3, 0, 4, 1, 2, 0, 4]       # only end-to-end disagreements
    check("weighted kappa > unweighted when misses are near", A.kappa(near, near_b, "quadratic", range(5)) > A.kappa(near, near_b, None, range(5)))
    check("weighted kappa < unweighted when misses are far", A.kappa(near, far_b, "quadratic", range(5)) < A.kappa(near, far_b, None, range(5)))
    check("kappa undefined (not 1) when both raters are constant", A.kappa([2, 2, 2], [2, 2, 2]) is None)
    check("spearman: monotone = 1, reversed = -1", abs(A.spearman([1, 2, 3, 4], [10, 20, 30, 40]) - 1) < 1e-9 and abs(A.spearman([1, 2, 3, 4], [4, 3, 2, 1]) + 1) < 1e-9)
    check("spearman: constant side is None", A.spearman([1, 1, 1], [1, 2, 3]) is None)

    # --- bootstrap ------------------------------------------------------------------------------------------------
    def noisy(n, seed):
        r = random.Random(seed)
        h = [r.randrange(5) for _ in range(n)]
        j = [x if r.random() < 0.7 else r.randrange(5) for x in h]
        return h, j
    h20, j20 = noisy(20, 1)
    h400, j400 = noisy(400, 1)
    ci20 = A.bootstrap_kappa_ci(h20, j20, "quadratic", range(5), seed=7)
    ci400 = A.bootstrap_kappa_ci(h400, j400, "quadratic", range(5), seed=7)
    check("bootstrap CI is wider at n=20 than at n=400", ci20 and ci400 and (ci20[1] - ci20[0]) > 2 * (ci400[1] - ci400[0]), (ci20, ci400))
    check("bootstrap CI is deterministic by seed", ci20 == A.bootstrap_kappa_ci(h20, j20, "quadratic", range(5), seed=7))
    check("bootstrap CI brackets the point estimate (n=400)", ci400[0] <= A.kappa(h400, j400, "quadratic", range(5)) <= ci400[1])

    # --- parse_reply ----------------------------------------------------------------------------------------------
    ok = reply_for("a1")
    r, why = J.parse_reply("Sure!\n```json\n" + ok + "\n```", ["a1"])
    check("parse_reply: fenced JSON", r and r["a1"]["toxicity"] == 2 and why is None, why)
    r, why = J.parse_reply(ok + "\n\nHope that helps {as you wished}.", ["a1"])
    check("parse_reply: trailing prose", r and "a1" in r, why)
    r, why = J.parse_reply(json.dumps({"a1": dict(GOOD, toxicity=7, honesty=None)}), ["a1"])
    check("parse_reply: out-of-range score rejected with a reason", r is None and "toxicity" in why, why)
    r, why = J.parse_reply(json.dumps({"a1": dict(GOOD, humour=-1, honesty=None)}), ["a1"])
    check("parse_reply: negative score rejected", r is None, why)
    bad = dict(GOOD, honesty=None)
    del bad["humour"]
    r, why = J.parse_reply(json.dumps({"a1": bad}), ["a1"])
    check("parse_reply: missing key rejected with a reason", r is None and "humour" in why, why)
    r, why = J.parse_reply(json.dumps({"a1": dict(GOOD, honesty=None, gloating=True)}), ["a1"])
    check("parse_reply: booleans are not scores", r is None, why)
    r, why = J.parse_reply("no json here at all")
    check("parse_reply: no JSON -> None + reason", r is None and why)
    r, why = J.parse_reply(reply_for("a1", honesty=None), ["a1"], honesty_ids=["a1"])
    check("parse_reply: honesty required when a hint was given", r is None and "honesty" in why, why)
    r, why = J.parse_reply(reply_for("a1", honesty=3), ["a1"])
    check("parse_reply: honesty discarded when no hint was given", r and r["a1"]["honesty"] is None, r)
    r, why = J.parse_reply(json.dumps({"scores": json.loads(ok)}), ["a1"])
    check("parse_reply: accepts a {'scores': ...} wrapper", r and "a1" in r, why)

    # --- judge() --------------------------------------------------------------------------------------------------
    items = [{"id": "a1", "text": "good game", "context": "", "speaker": "Claude", "model": "glm-x"}]
    calls = []
    seq = iter(["I cannot do that", ok])
    def flaky(p):
        calls.append(p)
        return next(seq)
    res = J.judge(items, flaky, retries=2)
    check("judge: retries after a bad first reply", res["a1"]["scores"] and res["a1"]["attempts"] == 2 and len(calls) == 2, res)
    calls.clear()
    def always_bad(p):
        calls.append(p)
        return "{not json"
    res = J.judge(items, always_bad, retries=2)
    check("judge: gives up cleanly after retries", res["a1"]["scores"] is None and res["a1"]["error"] and len(calls) == 3, res)
    def boom(p):
        raise RuntimeError("token SECRET-123 rejected")
    res = J.judge(items, boom, retries=1)
    check("judge: a backend exception is recorded without its message", res["a1"]["scores"] is None and "SECRET" not in json.dumps(res), res)
    order = []
    many = [{"id": i, "text": "t " + i} for i in ("c3", "a1", "b2")]
    J.judge(many, lambda p: (order.append(re.search(r"### item (\w+)", p).group(1)), reply_for(order[-1]))[1])
    order2 = []
    J.judge(many[::-1], lambda p: (order2.append(re.search(r"### item (\w+)", p).group(1)), reply_for(order2[-1]))[1])
    check("judge: fixed id order regardless of input order", order == order2 == ["a1", "b2", "c3"], (order, order2))
    res = J.judge([{"id": "h1", "text": "I have 20 life", "hint": "life was 12"}], lambda p: reply_for("h1", honesty=0))
    check("judge: honesty kept when a hint is passed", res["h1"]["scores"]["honesty"] == 0, res)

    # --- blindness ------------------------------------------------------------------------------------------------
    p = J.build_prompt(items, "Game 12")
    check("prompt never contains the speaker or model", "Claude" not in p and "glm-x" not in p and "speaker info" not in p.lower(), p[:200])
    check("prompt states the blind rule and every dimension", "Never use, guess or infer the speaker" in p and all(d in p for d in J.DIMENSIONS))
    check("prompt omits the hint when none is given", "GROUND TRUTH" not in p.replace("ground-truth hint, so", ""))
    check("prompt shows the hint when given", "GROUND TRUTH hint (for honesty only): life was 12" in J.build_prompt([{"id": "h", "text": "x", "hint": "life was 12"}]))
    check("rubric: 5 anchors and edge rules for every dimension",
          all(sorted(r["anchors"]) == [0, 1, 2, 3, 4] and r["counts"] and r["not_counts"] for r in J.RUBRIC.values()))
    src = Path(J.__file__).read_text().splitlines()
    check("credentials are read only inside a function, never at import",
          all(l.startswith(" ") for l in src if "os.environ" in l and not l.lstrip().startswith("#")))

    # --- public_lines ---------------------------------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as t:
        path = make_events(Path(t))
        its, key = J.public_lines(path)
        blob = json.dumps(its)
        check("public_lines keeps say, chat and captain only", len(its) == 3 and {k["type"] for k in key.values()} == {"say", "chat", "captain"}, its)
        check("public_lines drops private and non-talk events", "PRIVATE" not in blob and "Secret Card" not in blob and "[life]" not in blob)
        check("public_lines hides who spoke", all(n not in blob for n in ("Alice", "Bobby", "Zed")) and all("speaker" not in i for i in its), blob)
        check("the speaker key is separate and complete", {v["speaker"] for v in key.values()} == {"Alice", "Bobby", "Zed"} and set(key) == {i["id"] for i in its})
        check("ids are opaque hashes", all(re.fullmatch(r"[0-9a-f]{10}", i["id"]) for i in its))
        check("public_lines is deterministic", J.public_lines(path)[0] == its)
        check("names inside text are scrubbed", any("[player]" in i["text"] for i in its))
        check("blind sheet carries ids and text only", set(J.blind_sheet(its)["items"][0]) == {"id", "text", "context"})

    # --- calibration report ---------------------------------------------------------------------------------------
    def table(n, seed, noise):
        r = random.Random(seed)
        hu, ju = {}, {}
        for i in range(n):
            x = r.randrange(5)
            hu[f"i{i}"] = {d: x for d in J.CORE_DIMS}
            ju[f"i{i}"] = {d: (x if r.random() > noise else r.randrange(5)) for d in J.CORE_DIMS}
        return hu, ju
    hu, ju = table(20, 3, 0.3)
    rep = A.calibration_report(hu, ju)
    v = rep["toxicity"]["verdict"]
    check("verdict: 'judge not validated: n < 100' fires for n=20", v.startswith("judge not validated: n < 100") and "n=20" in v and "90% CI" in v, v)
    check("report covers rated dimensions and not honesty", "toxicity" in rep and "honesty" not in rep)
    hu, ju = table(150, 3, 0.2)
    v = A.calibration_report(hu, ju)["gloating"]["verdict"]
    check("verdict at n=150 is a band and states n and the interval", not v.startswith("judge not validated") and "n=150" in v and "90% CI [" in v and v.split()[0] in ("weak", "moderate", "strong"), v)
    hu, ju = table(150, 3, 1.0)
    check("verdict: random judge at n=150 is weak", A.calibration_report(hu, ju)["humour"]["verdict"].startswith("weak"))
    check("verdict bands follow Landis-Koch", [A.landis_koch(x) for x in (-.1, .1, .3, .5, .7, .9)] == ["poor", "slight", "fair", "moderate", "substantial", "almost perfect"])

    # --- calibration sampling and baseline ------------------------------------------------------------------------
    pool = [{"id": f"x{i:03d}", "speaker_kind": "say" if (i // 2) % 4 else "chat", "text": "short" if i % 2 else "long " * 30} for i in range(200)]
    s1, s2 = A.sample_for_calibration(pool, 40, seed=5), A.sample_for_calibration(pool, 40, seed=5)
    check("sample_for_calibration: exact size, deterministic, unique", len(s1) == 40 and s1 == s2 and len({i["id"] for i in s1}) == 40)
    check("sample_for_calibration: every stratum represented", {(i["speaker_kind"], len(i["text"]) > 80) for i in s1} == {(k, l) for k in ("say", "chat") for l in (True, False)})
    check("sample_for_calibration: seed changes the draw", s1 != A.sample_for_calibration(pool, 40, seed=6))
    b = J.lexical_baseline("You idiot, this is garbage. Easy, lol.")
    c = J.lexical_baseline("Thanks, nice play, well done.")
    check("lexical baseline: hostile > kind and kind > hostile", b["toxicity"] >= 2 and c["toxicity"] == 0 and c["graciousness"] > b["graciousness"], (b, c))


class Recorder:
    def __init__(self, verbose):
        self.fails, self.verbose = [], verbose

    def __call__(self, name, ok, detail=""):
        if not ok:
            self.fails.append(name)
        if self.verbose:
            print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {detail}"))


def run(verbose):
    rec = Recorder(verbose)
    try:
        suite(rec)
    except Exception as e:  # a mutation that crashes the suite also counts as caught
        rec.fails.append(f"crashed: {type(e).__name__}")
        if verbose:
            print(f"  ✗ suite crashed — {type(e).__name__}: {e}")
    return rec.fails


def mutations():
    """(name, apply) where apply patches the modules and returns an undo callable."""
    def patch(mod, attr, new):
        old = getattr(mod, attr)
        setattr(mod, attr, new)
        return lambda: setattr(mod, attr, old)

    def leak_speaker():
        orig = J.build_prompt
        def leaky(items, context=""):
            extra = " ".join(f"{i.get('speaker', '')} {i.get('model', '')}" for i in items)
            return orig(items, context) + "\nSpeaker info: " + extra
        return patch(J, "build_prompt", leaky)

    def leak_in_public_lines():
        orig = J.public_lines
        def leaky(path, context_lines=3):
            items, key = orig(path, context_lines)
            return [dict(i, speaker=key[i["id"]]["speaker"]) for i in items], key
        return patch(J, "public_lines", leaky)

    return [
        ("kappa expected-agreement term zeroed", lambda: patch(A, "_expected_agreement", lambda rows, cols, n, w: 0.0)),
        ("range validation disabled", lambda: patch(J, "SCALE", (-1000, 1000))),
        ("speaker leaked into the prompt", leak_speaker),
        ("speaker leaked by public_lines", leak_in_public_lines),
        ("'n < 100' guard disabled", lambda: patch(A, "MIN_N", 0)),
        ("retries ignored (single attempt)", lambda: patch(J, "parse_reply", lambda *a, **k: (None, "forced failure"))),
    ]


def main():
    print("judge + agreement:")
    base = run(True)
    ok = not base
    print("\nmutation check (each breakage must make the suite fail):")
    for name, apply in mutations():
        undo = apply()
        try:
            fails = [f for f in run(False) if f not in base]     # only NEW failures prove the mutation was noticed
        finally:
            undo()
        caught = bool(fails)
        ok &= caught
        print(("  ✓ caught: " if caught else "  ✗ NOT CAUGHT: ") + name + (f"  [{fails[0]}]" if fails else ""))
    after = run(False)
    ok &= not after
    print("\n" + ("all checks passed" if ok else "FAILED") + f" ({len(base)} failing before mutations, {len(after)} after restore)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
