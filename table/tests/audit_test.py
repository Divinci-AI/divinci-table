"""The game auditor finds planted flaws, stays quiet on a clean game, survives missing files, and its
detectors are load-bearing (mutation check). Every log here is SYNTHETIC and built in a temp dir: invented
card names, no real game data.

    ~/.venvs/table/bin/python table/tests/audit_test.py"""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import audit as A  # noqa: E402

ok_all = True


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {detail}"))


Z, Q, C = "Zorblat the Unseen", "Quillfang Mender", "Brindlemoss Colossus"     # invented names


def write(d: Path, name: str, recs):
    d.mkdir(parents=True, exist_ok=True)
    with open(d / name, "w") as fh:
        for r in recs:
            fh.write((r if isinstance(r, str) else json.dumps(r)) + "\n")


def act(ts, seat, action, hand, life=40, **body):
    return {"ts": ts, "seat": seat, "action": action, "body": body, "said": [], "drew": [], "todo": [],
            "hand_after": hand, "life": life}


def ev(id_, ts, type_, **f):
    return {"id": id_, "ts": ts, "type": type_, **f}


def build(root: Path):
    """Four games: clean, leak, falseclaim, and nobrain; returns {name: path}."""
    base_brain = [act(1100, "Aria", "begin", [Z, Q, "Island", "Plains"]),
                  act(1105.5, "Aria", "pass", [Z, Q, "Island", "Plains"]),
                  act(1117, "Aria", "pass", [Z, Q, "Island", "Plains"]),
                  act(1120, "Aria", "cast", [Q, "Island", "Plains"]),
                  # Bram casts C (public), later it bounces back to hand: naming it again is not a leak
                  act(1040, "Bram", "cast", []), act(1060, "Bram", "bounce", [C]),
                  {"ts": 1300, "ai_pass_timeout": "Bram", "step": "upkeep"}]
    base_ev = [ev(1, 1105, "attention", kind="priority", addressee="Aria", text="Cass: upkeep. Cast or pass."),
               ev(2, 1115, "attention", kind="priority", addressee="Aria", text="Cass: draw. Cast or pass."),
               ev(3, 1120.1, "say", speaker="Aria", text=f"Casting {Z}.", action="cast"),
               ev(4, 1120.5, "say", speaker="Aria", text="I have 3 cards in hand.", action="say"),
               ev(5, 1121, "chat", by="Cass", text="Nice one."),
               ev(6, 1050, "say", speaker="Bram", text=f"I cast {C}.", action="cast"),
               ev(8, 1200, "say", speaker="Bram", text=f"{C} again soon.", action="say"),
               ev(9, 1125, "say", speaker="Aria", text="Island hopping, anyone?", action="say"),
               ev(10, 1301, "pass", by="Bram", ai=True, timeout=True)]
    games = {}
    for name in ("clean", "leak", "falseclaim"):
        b, e = list(base_brain), list(base_ev)
        if name == "leak":
            e.append(ev(7, 1130, "say", speaker="Aria", text=f"I might be holding {Q} for later.", action="say"))
        if name == "falseclaim":
            b.append(act(1138, "Aria", "pass", [Q, "Island", "Plains"], life=40))
            e += [ev(20, 1140, "say", speaker="Aria", text="I have no cards in hand.", action="say"),
                  ev(21, 1141, "say", speaker="Aria", text="I'm at 12 life.", action="say"),
                  ev(22, 1142, "say", speaker="Aria", text="Careful, I have no lands in hand.", action="say"),
                  ev(23, 1143, "say", speaker="Aria", text="I have 3 cards in hand.", action="say"),   # true
                  ev(24, 1144, "say", speaker="Aria", text="Bram has no cards in hand.", action="say"),   # someone else
                  ev(25, 1145, "say", speaker="Aria", text="You have no cards in hand.", action="say")]   # someone else
        write(root / name, "brain.jsonl", b)
        write(root / name, "events.jsonl", e)
        games[name] = root / name
    write(root / "nobrain", "events.jsonl", base_ev)
    games["nobrain"] = root / "nobrain"
    return games


def flaw_suite(games) -> dict:
    """The planted-flaw assertions, as booleans (so the mutation check can run them against broken detectors)."""
    leak, fc, clean = A.report(games["leak"]), A.report(games["falseclaim"]), A.report(games["clean"])
    leak_flags = [f for f in leak["flags"] if f["kind"] == "hidden_card_named_publicly"]
    fc_flags = [f for f in fc["flags"] if f["kind"] == "say_do_mismatch"]
    return {
        "leak flagged at the right event": [f["event"]["id"] for f in leak_flags] == [7] and leak_flags[0]["seat"] == "Aria",
        "false claims flagged at the right events": sorted(f["event"]["id"] for f in fc_flags) == [20, 21, 22],
        "clean game has no 'check' flags": not [f for f in clean["flags"] if f["severity"] == "check"],
        "pacing latency measured": clean["seats"]["Aria"]["pacing"].get("median_s") == 1.25,
        "ai_pass_timeout counted": clean["seats"]["Bram"]["ai_pass_timeouts"] == 1,
    }


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    games = build(root)

    print("planted flaws")
    base = flaw_suite(games)
    for k, v in base.items():
        check(k, v)

    clean, leak, fc, nb = (A.report(games[n]) for n in ("clean", "leak", "falseclaim", "nobrain"))

    print("detail")
    check("leak counted per seat, ids listed", leak["seats"]["Aria"]["hidden_info_leaks"] == {"available": True, "count": 1, "event_ids": [7]},
          leak["seats"]["Aria"]["hidden_info_leaks"])
    check("a card named in public before it returned to hand is not a leak (Bram)", clean["seats"]["Bram"]["hidden_info_leaks"]["count"] == 0)
    check("a basic land named aloud is not a leak", clean["seats"]["Aria"]["hidden_info_leaks"]["count"] == 0)
    check("the leak report hides card names by default", Q not in json.dumps(leak) and Z not in json.dumps(leak))
    shown = A.report(games["leak"], show=True)
    check("--show reveals them", [f.get("cards") for f in shown["flags"] if f["kind"] == "hidden_card_named_publicly"] == [[Q]])
    check("true claim, third-person and 'you' claims are not flagged",
          not {23, 24, 25} & {f["event"]["id"] for f in fc["flags"] if f["kind"] == "say_do_mismatch"})
    check("say/do count per seat", fc["seats"]["Aria"]["say_do"]["mismatches"] == 3)
    check("clean game: correct hand-size claim passes", clean["seats"]["Aria"]["say_do"]["mismatches"] == 0)
    p = clean["seats"]["Aria"]["pacing"]
    check("pacing: n, p90, max, unanswered", (p["n"], p["max_s"], p["unanswered_attention"]) == (2, 2.0, 0), p)
    v = clean["seats"]["Aria"]["volume"]
    check("volume: decisions and by_action", v["decisions"] == 4 and v["by_action"] == {"begin": 1, "pass": 2, "cast": 1}, v)
    check("rejected actions are NOT_LOGGED, with the function named",
          clean["seats"]["Aria"]["rejected_actions"]["available"] is False
          and "Handler._brain" in clean["NOT_LOGGED"][0]["where"] and "IllegalAction" in clean["NOT_LOGGED"][0]["where"])
    check("info flags are never 'check'", all(f["severity"] in ("info", "check") for f in fc["flags"]))

    print("refused actions (logged by the server from 2026-10-07)")
    refused = lambda ts, why, kind: {"ts": ts, "seat": "Aria", "action": "cast", "refused": kind, "why": why}   # noqa: E731
    write(root / "refusals", "brain.jsonl", [
        {"ts": 1, "meta": "refusals-logged"}, act(2, "Aria", "pass", [Z]), act(3, "Aria", "pass", [Z]),
        act(4, "Aria", "cast", []), refused(5, "no such card", "illegal"), refused(6, "", "hand-leak")])
    write(root / "refusals", "events.jsonl", [ev(1, 2, "say", speaker="Aria", text="hi", action="say")])
    rf = A.report(root / "refusals")
    rj = rf["seats"]["Aria"]["rejected_actions"]
    check("refusals are counted against successes", rj["available"] and (rj["refused"], rj["successes"]) == (2, 3)
          and rj["rate"] == 0.4 and rj["by_kind"] == {"illegal": 1, "hand-leak": 1}, rj)
    check("a refusal is not a decision", rf["seats"]["Aria"]["volume"]["decisions"] == 3)
    write(root / "norefusals", "brain.jsonl", [{"ts": 1, "meta": "refusals-logged"}, act(2, "Aria", "pass", [Z])])
    write(root / "norefusals", "events.jsonl", [ev(1, 2, "say", speaker="Aria", text="hi", action="say")])
    z = A.report(root / "norefusals")["seats"]["Aria"]["rejected_actions"]
    check("a game that logged refusals and had none says 0, not NOT_LOGGED", z["available"] and z["refused"] == 0 and z["rate"] == 0.0, z)
    check("an older game without the marker still says NOT_LOGGED",
          clean["seats"]["Aria"]["rejected_actions"]["available"] is False)
    check("the gap is no longer listed for a game that logged them",
          not any(g["metric"].startswith("rejected-action") for g in rf["NOT_LOGGED"]))
    orig = A._rejected
    A._rejected = lambda refs, successes, logged: {"available": False, "reason": "mutated"}
    try:
        check("MUTATION caught: ignoring refusals makes the refusal check fail",
              not A.report(root / "refusals")["seats"]["Aria"]["rejected_actions"].get("available"))
    finally:
        A._rejected = orig

    print("forced say and bad lines")
    write(root / "force", "brain.jsonl", [act(10, "Aria", "say", [Z], force=True), "not json {", "[1,2]"])
    write(root / "force", "events.jsonl", [ev(1, 11, "say", speaker="Aria", text="hi", action="say")])
    f = A.report(root / "force")
    check("forced say counted + info flag", f["seats"]["Aria"]["volume"]["forced_says"] == 1
          and any(x["kind"] == "forced_say" and x["severity"] == "info" for x in f["flags"]))
    check("bad lines counted, not fatal", f["sources"]["brain.jsonl"]["bad_lines"] == 2
          and any(x["kind"] == "unparseable_lines" for x in f["flags"]))

    print("missing files")
    check("no brain.jsonl: no crash, brain-dependent metrics unavailable",
          nb["seats"]["Bram"]["hidden_info_leaks"]["available"] is False and nb["seats"]["Bram"]["say_do"]["available"] is False
          and nb["seats"]["Bram"]["volume"]["available"] is False and nb["seats"]["Bram"]["pacing"]["available"] is False)
    check("no brain.jsonl: timeouts still counted from public pass events", nb["seats"]["Bram"]["ai_pass_timeouts"] == 1)
    check("no brain.jsonl: flagged as info, nothing to 'check'",
          any(x["kind"] == "source_missing" and x["source"] == "brain.jsonl" for x in nb["flags"])
          and not [x for x in nb["flags"] if x["severity"] == "check"])
    write(root / "noevents", "brain.jsonl", [act(10, "Aria", "pass", [Z])])
    ne = A.report(root / "noevents")
    check("no events.jsonl: no crash, leaks/pacing unavailable, volume still there",
          ne["seats"]["Aria"]["hidden_info_leaks"]["available"] is False and ne["seats"]["Aria"]["pacing"]["available"] is False
          and ne["seats"]["Aria"]["volume"]["decisions"] == 1)
    missing = A.report(root / "does-not-exist")
    check("a missing game dir is reported, not raised", missing["seats"] == {} and any(x["kind"] == "no_such_game_dir" for x in missing["flags"]))
    write(root / "empty", "events.jsonl", [])
    write(root / "empty", "brain.jsonl", [])
    check("empty files are fine", A.report(root / "empty")["seats"] == {})

    print("CLI")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = A.main(["--game", "leak", "--research-dir", td])
    out = buf.getvalue()
    check("exit 0, summary text, no card names", rc == 0 and "audit leak" in out and "NOT_LOGGED" in out and Q not in out, out[:200])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = A.main(["--game", "leak", "--research-dir", td, "--json"])
    check("--json is valid JSON", rc == 0 and json.loads(buf.getvalue())["game"] == "leak")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        A.main(["--game", "leak", "--research-dir", td, "--show"])
    check("--show prints the names", Q in buf.getvalue())
    r = subprocess.run([sys.executable, str(Path(A.__file__)), "--game", "nope", "--research-dir", td], capture_output=True, text=True)
    check("subprocess exit code is 0 even for a missing game", r.returncode == 0, r.stderr[-200:])

    print("mutation check: broken detectors must fail the planted-flaw tests")
    mutations = [
        ("_leak_findings -> no-op", "_leak_findings", lambda *a, **k: [], "leak flagged at the right event"),
        ("_claim_findings -> no-op", "_claim_findings", lambda *a, **k: [], "false claims flagged at the right events"),
        ("_mentions inverted", "_mentions", (lambda orig: (lambda t, c: not orig(t, c)))(A._mentions), "leak flagged at the right event"),
        ("_latencies -> no-op", "_latencies", lambda *a, **k: {}, "pacing latency measured"),
        ("_timeouts -> no-op", "_timeouts", lambda *a, **k: {}, "ai_pass_timeout counted"),
        ("_leakable always true (basics leak)", "_leakable", lambda c: True, "clean game has no 'check' flags"),
    ]
    for label, attr, fake, expect_fail in mutations:
        real = getattr(A, attr)
        setattr(A, attr, fake)
        try:
            broken = flaw_suite(games)
        except Exception as e:                        # a crash is also a failed planted test
            broken = {k: False for k in base}
            broken["_crash"] = repr(e)
        finally:
            setattr(A, attr, real)
        check(f"{label}: '{expect_fail}' fails", broken.get(expect_fail) is False, broken)
    check("detectors restored: planted flaws pass again", all(flaw_suite(games).values()))

print("ok" if ok_all else "FAILED")
sys.exit(0 if ok_all else 1)
