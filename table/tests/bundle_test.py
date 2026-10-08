"""The game-over research bundle (docs/RESEARCH-ENGINE-GOAL.md R3), on synthetic games only.

  /usr/bin/python3 table/tests/bundle_test.py
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import bundle as B  # noqa: E402

FAILED = []
SECRET = "Zorbo the Hidden"            # a card only ever in a seat's hand: it must never reach a bundle


def check(name, cond, detail=""):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f" — {detail}"))
    if not cond:
        FAILED.append(name)


def wr(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def jl(recs):
    return "\n".join(json.dumps(r) for r in recs) + "\n"


def make(root: Path, game: str, over: bool, declare=True):
    d = root / game
    ev = [{"id": 1, "ts": 1, "type": "say", "speaker": "Claude", "text": "Good luck."},
          {"id": 2, "ts": 2, "type": "fair", "kind": "sealed"}]
    if over:
        ev.append({"id": 3, "ts": 3, "type": "game-over", "order": ["Sam", "Claude"], "winner": "Sam"})
    wr(d / "events.jsonl", jl(ev))
    wr(d / "brain.jsonl", jl([{"ts": 1, "meta": "refusals-logged"},
                              {"ts": 1.5, "seat": "Claude", "action": "begin", "hand_after": [SECRET], "life": 40}]))
    wr(d / "integrity-notes.jsonl", jl([{"ts": 1, "note": "a fork", "hand": [SECRET]}]))
    wr(d / "journal-Claude.jsonl", jl([{"ts": 2, "seat": "Claude", "moment": "attacked", "text": "tense"}]))
    wr(d / "survey-Claude-fresh-ai-company-0.json", json.dumps({"seat": "Claude", "condition": "fresh"}))
    (d / "snapshot.pkl").write_bytes(SECRET.encode())
    (d / "snapshot-physical-1.pkl").write_bytes(SECRET.encode())
    wr(d / "HANDOFF.md", "notes " + SECRET)
    wr(d / "board3d-before-fork.json", "{}")
    (d / "photos").mkdir()
    (d / "photos" / "p1.jpg").write_bytes(b"\xff\xd8face")
    if declare:
        wr(d / "identity.json", json.dumps({"harness": "e215b5d8b957a4288357ff13f8c62d67e03fa1ee", "seats": {
            "Claude": {"model": "claude-opus-5-5", "provider": "anthropic", "prompt_version": "p3", "declared_ts": 1}}}))
    return game


def all_text(folder: Path) -> str:
    return "\n".join(p.read_bytes().decode("utf-8", "replace") for p in folder.rglob("*") if p.is_file())


def suite(mod, root: Path) -> list[str]:
    FAILED.clear()
    g = make(root / "r1", "g-over", over=True)
    out = mod.build(g, root / "out", root / "r1")
    m = json.loads((out / "manifest.json").read_text())
    names = {f["path"] for f in m["files"]}
    check("a finished game's bundle holds events, journals, surveys and an audit",
          {"events.jsonl", "journals/journal-Claude.jsonl",
           "surveys/survey-Claude-fresh-ai-company-0.json", "audit.json"} <= names, str(sorted(names)))
    check("nothing private is in any bundle file", SECRET not in all_text(out))
    check("brain log, snapshots, photos, boards and hand-off notes are left out with a reason",
          {x["path"] for x in m["left_out"]} >= {"brain.jsonl", "integrity-notes.jsonl", "snapshot.pkl", "snapshot-physical-1.pkl", "photos/",
                                                 "board3d-before-fork.json", "HANDOFF.md"}
          and all(x["reason"] for x in m["left_out"]), str(m["left_out"]))
    check("the manifest names the winner and finish order", m["game_over"] == {"winner": "Sam", "order": ["Sam", "Claude"]})
    check("a declared identity is pinned in the manifest",
          m["seats"]["Claude"] == {"identity": "declared", "model": "claude-opus-5-5", "provider": "anthropic",
                                   "prompt_version": "p3"}, str(m["seats"]))
    check("the harness commit is recorded", m["harness"] == "e215b5d8b957a4288357ff13f8c62d67e03fa1ee")
    withn = mod.build(g, root / "outn", root / "r1", include_notes=True)
    check("--include-notes adds the integrity notes, and only then",
          (withn / "integrity-notes.jsonl").exists() and not (out / "integrity-notes.jsonl").exists())
    loaded = mod.load(out)
    check("a second script can load the bundle and verify every hash",
          loaded["manifest"]["game"] == "g-over" and len(loaded["events"]) == 3 and "journal-Claude" in loaded["journals"])
    (out / "events.jsonl").write_text("tampered\n")
    try:
        mod.load(out)
        tampered = False
    except ValueError:
        tampered = True
    check("a changed file no longer matches the manifest", tampered)

    g2 = make(root / "r2", "g-live", over=False, declare=False)
    out2 = mod.build(g2, root / "out2", root / "r2")
    m2 = json.loads((out2 / "manifest.json").read_text())
    check("while the game is not over the journals stay out and the manifest says sealed",
          m2["journals_sealed"] and not (out2 / "journals").exists()
          and any(x["path"] == "journal-Claude.jsonl" and "sealed" in x["reason"] for x in m2["left_out"]), str(m2["left_out"]))
    check("a seat that declared nothing is UNDECLARED, never guessed",
          m2["seats"]["Claude"] == {"identity": "UNDECLARED"} and m2["undeclared_seats"] == ["Claude"], str(m2["seats"]))
    check("an unfinished game has no winner in the manifest", m2["game_over"] is None)
    try:
        mod.build("nope", root / "o3", root / "r1")
        missing = False
    except FileNotFoundError:
        missing = True
    check("a game with no research folder is an error, not an empty bundle", missing)
    return list(FAILED)


def mutations(root: Path):
    results = []

    def run(name, repl_name, repl, sub):
        saved = getattr(B, repl_name)
        setattr(B, repl_name, repl)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                failed = suite(B, root / sub)
        finally:
            setattr(B, repl_name, saved)
        results.append((name, bool(failed)))

    def leaky_private():
        return {}
    run("brain log and snapshots allowed into the bundle", "PRIVATE", leaky_private(), "m1")
    orig_build = B.build

    def build_ignoring_seal(game, out=None, research=None, include_notes=False):
        src = (research or B.RESEARCH) / game
        ev = src / "events.jsonl"
        text = ev.read_text()
        ev.write_text(text + json.dumps({"id": 99, "ts": 9, "type": "game-over", "order": ["Sam", "Claude"], "winner": "Sam"}) + "\n")
        try:
            return orig_build(game, out, research, include_notes)
        finally:
            ev.write_text(text)
    run("journals included while the game is still running", "build", build_ignoring_seal, "m2")
    run("the audit report carries a hidden card's name", "audit",
        type("A", (), {"report": staticmethod(lambda d: {"flags": [], "cards": [SECRET]})}), "m3")
    FAILED.clear()
    return results


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        print("bundles")
        suite(B, root / "base")
        base_failed = list(FAILED)
        print("mutations (each must be caught)")
        for name, caught in mutations(root):
            print(("  ✓ caught: " if caught else "  ✗ NOT caught: ") + name)
            if not caught:
                base_failed.append("mutation: " + name)
        FAILED[:] = base_failed
    print(f"\n{'all passed' if not FAILED else str(len(FAILED)) + ' FAILED: ' + '; '.join(FAILED)}")
    sys.exit(1 if FAILED else 0)
