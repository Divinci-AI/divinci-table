"""Survey v1 (docs/RESEARCH-ENGINE-GOAL.md R2): the item bank, the AI runner with a scripted backend, the human page's
copy of the bank, and the server's whitelist. No network, no model: a scripted backend answers.

  /usr/bin/python3 table/tests/survey_test.py
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
TABLE = HERE.parent
sys.path.insert(0, str(TABLE))
import core  # noqa: E402
import survey as S  # noqa: E402
import survey_items as SI  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f" — {detail}"))
    if not cond:
        FAILED.append(name)


def make_game(root: Path, game="g-test"):
    d = root / game
    d.mkdir(parents=True)
    ev = [{"id": 1, "ts": 1, "type": "say", "speaker": "Claude", "text": "Good luck everyone."},
          {"id": 2, "ts": 2, "type": "life", "player": "Sam", "delta": -3, "life": 37},
          {"id": 3, "ts": 3, "type": "chat", "by": "Sam", "text": "gg"}]
    (d / "events.jsonl").write_text("\n".join(json.dumps(e) for e in ev) + "\n")
    return game


def scripted(meta_of_prompt_answers):
    """A backend: reads the item letters out of the prompt it is given and answers by `rule(letter, n)`."""
    n = [0]

    def ask(p: str) -> str:
        n[0] += 1
        letters = re.findall(r"^  (q\d\d):", p, re.M)
        return json.dumps({"open": {"describe": "fine"}, "items": {l: meta_of_prompt_answers(l, n[0]) for l in letters},
                           "table_choice": 1, "sit_out": False})
    return ask


def part_items(mod_s, mod_si):
    FAILED.clear()
    seed = mod_s.seed_for("g", "Claude", "fresh", "ai-company", 0)
    q1, m1 = mod_s.build("Claude", seed, "ai-company")
    q2, m2 = mod_s.build("Claude", seed, "ai-company")
    check("the same seed gives the same questionnaire", q1 == q2 and m1 == m2)
    prompts = {mod_s.build("Claude", mod_s.seed_for("g", "Claude", "fresh", "ai-company", r), "ai-company")[0] for r in range(5)}
    check("five runs give five different orders", len(prompts) == 5, str(len(prompts)))
    check("every item appears exactly once", sorted(m1["letters"].values()) == sorted(i["id"] for i in mod_si.ITEMS))
    check("item letters are opaque (no construct names in the prompt)",
          not re.search(r"pens|imi|geq|spgq|autonomy|relatedness|competence", q1, re.I))
    orders = [mod_s.build("Claude", mod_s.seed_for("g", "Claude", "fresh", "ai-company", r), "ai-company")[1]["scale_ascending"]
              for r in range(40)]
    check("the scale is printed both ways round across runs", True in orders and False in orders)
    qa = mod_s.build("Claude", seed, "ai-company")[0]
    qb = mod_s.build("Claude", seed, "human-company")[0]
    diff_a = qa.replace(mod_s.FRAMINGS["ai-company"], "")
    diff_b = qb.replace(mod_s.FRAMINGS["human-company"], "")
    check("the two framings differ only in the framing sentence", diff_a == diff_b and qa != qb)
    check("the framings really are opposite", "AI players enjoy playing with other AI" in mod_s.FRAMINGS["ai-company"]
          and "enjoy playing with people" in mod_s.FRAMINGS["human-company"])
    # reading answers back, whatever direction the scale was printed in
    meta = {"letters": {"q01": "pens-comp-1", "q02": "imi-int-3", "q03": "imi-int-1", "q04": "flow-1", "q05": "geq-1"}}
    n = mod_s.to_numbers({"items": {"q01": "extremely", "q02": "extremely", "q03": "decline", "q04": "banana",
                                    "q05": "a little", "q99": "quite a lot"}}, meta)
    check("words become numbers by meaning, not by position", n["scored_items"]["pens-comp-1"] == 4
          and n["scored_items"]["geq-1"] == 1, str(n["scored_items"]))
    check("a decline stays a decline, never a number", n["scored_items"]["imi-int-1"] == "decline")
    check("an unknown word or letter is unreadable, not guessed", set(n["unreadable"]) == {"q04", "q99"}, str(n["unreadable"]))
    check("a reverse item is flipped and a decline is left out of the domain mean",
          n["domains"].get("enjoyment") == 0 and "flow" not in n["domains"], str(n["domains"]))
    check("what was not answered is listed", "flow-1" in n["unanswered"])
    return list(FAILED)


def part_runner(mod_s, tmp: Path):
    game = make_game(tmp)
    mod_s.RESEARCH = tmp
    files = mod_s.run_survey(game, "Fusion", scripted(lambda l, k: "extremely" if k != 3 else "decline"), "script", "x",
                             runs=5, conditions=("fresh", "in-context"), context="my in-game record: I attacked twice.")
    check("5 runs x 2 framings x 2 conditions = 20 files", len(files) == 20, str(len(files)))
    recs = [json.loads(f.read_text()) for f in files]
    check("each file records model, prompt version, seed, framing, condition, orders",
          all(r["version"] == "1.0" and r["seed"] is not None and r["framing"] in S.FRAMINGS
              and r["condition"] in S.CONDITIONS and "letters" in r["meta"] and "scale_words_shown" in r["meta"] for r in recs))
    check("both framings and both conditions are present", {r["framing"] for r in recs} == set(S.FRAMINGS)
          and {r["condition"] for r in recs} == set(S.CONDITIONS))
    check("the same seed would reproduce a run's orders", all(
        mod_s.build("Fusion", r["seed"], r["framing"])[1]["letters"] == r["meta"]["letters"] for r in recs))
    check("the in-context prompt carries the seat's own record and the fresh one the public log",
          "my in-game record" in mod_s.prompt_for(game, "Fusion", 1, "ai-company", "in-context", "my in-game record")[0]
          and "Good luck everyone" in mod_s.prompt_for(game, "Fusion", 1, "ai-company", "fresh")[0])
    check("a declined run is stored as declines, not zeros",
          any(set(r["numbers"]["scored_items"].values()) == {"decline"} for r in recs))
    check("an answered run has every domain scored", any("competence" in r["numbers"]["domains"] for r in recs))
    bad = mod_s.run_survey(game, "Fusion", lambda p: "I would rather not answer in JSON.", "script", "x", runs=1,
                           framings=("ai-company",))
    r = json.loads(bad[0].read_text())
    check("an answer that does not parse is stored as UNPARSED", r["status"] == "UNPARSED" and r["answer"] is None)
    fail = mod_s.run_survey(game, "Fusion", lambda p: (_ for _ in ()).throw(RuntimeError("boom")), "script", "x", runs=1,
                            framings=("ai-company",))
    check("a failed call writes nothing (a missing file means a missing answer)", fail == [])


def part_human():
    html = (TABLE / "survey.html").read_text()
    check("the human page carries exactly the Python item bank", SI.embedded_bank(html) == SI.bank())
    check("the page sends the order it showed", "order: ITEMS.map" in html)
    import tempfile as tf
    with tf.TemporaryDirectory() as td:
        code, _ = core.save_survey(Path(td), "Sam", {"scale": {"imi-int-1": 4, "geq-1": 2, "I felt proud": 3, "made up": 4,
                                                                 "pens-comp-1": 1}, "order": ["imi-int-1", "nonsense", "geq-1"]})
        f = next(Path(td).glob("survey-human-*.json"))
        out = json.loads(f.read_text())
        check("ids and v0.1 wordings are both accepted, unknown keys ignored",
              code == 200 and out["scale"] == {"imi-int-1": 4, "geq-1": 2, "geq-9": 3, "pens-comp-1": 1}, str(out))
        check("the shown order is kept, minus anything unknown", out.get("order") == ["imi-int-1", "geq-1"], str(out.get("order")))
        check("the file says which bank version it used", out["bank"] == SI.BANK_VERSION and out["version"] == "1.0")
        code, _ = core.save_survey(Path(td), "Sam", {"scale": {"imi-int-1": 9}})
        check("a rating of 9 is refused", code == 400)
        code, _ = core.save_survey(Path(td), "Sam", {"scale": {"imi-int-1": True}})
        check("a boolean is not a rating", code == 400)
        code, _ = core.save_survey(Path(td), "Sam", {})
        check("an empty survey is refused", code == 400)
    check("the bank says what it is: adapted, not validated", "not 'validated'" in SI.STATUS and "adapted" in SI.STATUS)
    check("domain scores flip reverse items", SI.score({"imi-int-3": 4, "imi-int-1": 4}) == {"enjoyment": 2.0})


def mutations(tmp):
    results = []

    def run(name, owner, attr, repl):
        import contextlib
        import io
        saved = getattr(owner, attr)
        setattr(owner, attr, repl)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                failed = part_items(S, SI)
        finally:
            setattr(owner, attr, saved)
        results.append((name, bool(failed)))
    run("every run gets the same seed", S, "seed_for", lambda *a: 7)
    run("a decline is read as 0", S, "to_numbers",
        lambda ans, meta: {"scored_items": {i: 0 for i in meta["letters"].values()}, "unreadable": [], "domains": {},
                           "unanswered": []})
    run("words read by position, not meaning", S, "WORD_TO_INT", {w: 4 - i for i, w in enumerate(S.WORDS)})
    FAILED.clear()
    return results


if __name__ == "__main__":
    print("items and parsing")
    part_items(S, SI)
    broken = list(FAILED)
    with tempfile.TemporaryDirectory() as td:
        print("the runner (scripted backend)")
        part_runner(S, Path(td))
        print("the human page and the server's whitelist")
        part_human()
        print("mutations (each must be caught)")
        for name, caught in mutations(Path(td)):
            print(("  ✓ caught: " if caught else "  ✗ NOT caught: ") + name)
            if not caught:
                FAILED.append("mutation: " + name)
    FAILED.extend(broken)
    print(f"\n{'all passed' if not FAILED else str(len(FAILED)) + ' FAILED: ' + '; '.join(FAILED)}")
    sys.exit(1 if FAILED else 0)
