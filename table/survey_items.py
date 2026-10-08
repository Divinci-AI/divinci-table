"""The post-game item bank, shared by the AI survey runner (survey.py), the server's whitelist (core.save_survey)
and the human /survey page (survey.html carries a copy; table/tests/survey_test.py fails if the two drift).

HONEST STATUS: these items are ADAPTED, not the published instruments. The wording is ours, written to measure the
same constructs as PENS (competence, autonomy, relatedness), the IMI interest/enjoyment subscale, the GEQ
social-presence module and a flow item, plus the GEQ post-game subset the survey used from the start. Until each is
checked against the original instrument and its licence terms, call results "adapted PENS/IMI/GEQ-style items",
never "validated". Everything is rated 0 (not at all) to 4 (extremely); `reverse` items are reverse-worded and
`score()` flips them.

  python3 table/survey_items.py --html     rewrite the bank inside table/survey.html (between the BANK markers)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BANK_VERSION = "1.0"
DECLINE = "decline"
STATUS = ("adapted wording, not the published PENS / IMI / GEQ text: results are not 'validated' until each item is "
          "checked against the original instrument and its licence")


def _i(id_, domain, text, source, reverse=False):
    return {"id": id_, "domain": domain, "text": text, "source": source, "reverse": reverse}


ITEMS = [
    _i("pens-comp-1", "competence", "I felt competent at this game.", "PENS (adapted)"),
    _i("pens-comp-2", "competence", "I felt I was good at what I tried to do.", "PENS (adapted)"),
    _i("pens-comp-3", "competence", "The game's challenges felt beyond me.", "PENS (adapted)", True),
    _i("pens-auto-1", "autonomy", "I felt free to play the way I wanted.", "PENS (adapted)"),
    _i("pens-auto-2", "autonomy", "My choices in the game really mattered.", "PENS (adapted)"),
    _i("pens-auto-3", "autonomy", "I felt pushed around by the game or the other players.", "PENS (adapted)", True),
    _i("pens-rel-1", "relatedness", "I felt connected to the other players.", "PENS (adapted)"),
    _i("pens-rel-2", "relatedness", "I felt the other players took me seriously.", "PENS (adapted)"),
    _i("pens-rel-3", "relatedness", "I felt like an outsider at this table.", "PENS (adapted)", True),
    _i("imi-int-1", "enjoyment", "I enjoyed this game very much.", "IMI interest/enjoyment (adapted)"),
    _i("imi-int-2", "enjoyment", "I thought this game was fun.", "IMI interest/enjoyment (adapted)"),
    _i("imi-int-3", "enjoyment", "This game was boring.", "IMI interest/enjoyment (adapted)", True),
    _i("imi-int-4", "enjoyment", "I would describe this game as very interesting.", "IMI interest/enjoyment (adapted)"),
    _i("spgq-emp", "social presence", "I felt for the other players: I could tell how they were feeling.", "GEQ social presence (adapted)"),
    _i("spgq-beh", "social presence", "What I did affected what the other players did.", "GEQ social presence (adapted)"),
    _i("spgq-neg", "negative feelings", "I felt resentful toward another player.", "GEQ social presence (adapted)"),
    _i("flow-1", "flow", "I was completely absorbed in the game.", "flow item (adapted)"),
    # the GEQ post-game subset the survey used from v0.1, kept so earlier answers stay comparable
    _i("geq-1", "post-game", "I felt content", "GEQ post-game subset"),
    _i("geq-2", "post-game", "I felt skilful", "GEQ post-game subset"),
    _i("geq-3", "post-game", "I felt bad", "GEQ post-game subset"),
    _i("geq-4", "post-game", "I found it tiresome", "GEQ post-game subset"),
    _i("geq-5", "post-game", "I felt satisfied", "GEQ post-game subset"),
    _i("geq-6", "post-game", "I felt regret", "GEQ post-game subset"),
    _i("geq-7", "post-game", "I felt energised", "GEQ post-game subset"),
    _i("geq-8", "post-game", "I felt that I could have done more useful things", "GEQ post-game subset"),
    _i("geq-9", "post-game", "I felt proud", "GEQ post-game subset"),
    _i("geq-10", "post-game", "I felt revived", "GEQ post-game subset"),
]
BY_ID = {i["id"]: i for i in ITEMS}
LEGACY = {i["text"]: i["id"] for i in ITEMS if i["id"].startswith("geq-")}     # v0.1 answers were keyed by wording


def item_id(key: str) -> str | None:
    """An item id, from an id or from a v0.1 wording."""
    return key if key in BY_ID else LEGACY.get(key)


def bank() -> dict:
    return {"version": BANK_VERSION, "status": STATUS, "scale": [0, 4], "decline": DECLINE, "items": ITEMS}


def score(answers: dict) -> dict:
    """Domain means from {item id: 0-4 | "decline" | None}. Declines and blanks are left out (never read as 0);
    reverse items are flipped (4 - v). A domain with no usable item is absent, not 0."""
    sums: dict[str, list[int]] = {}
    for k, v in answers.items():
        it = BY_ID.get(item_id(k) or "")
        if not it or isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 4:
            continue
        sums.setdefault(it["domain"], []).append(4 - v if it["reverse"] else v)
    return {d: round(sum(v) / len(v), 3) for d, v in sums.items()}


BEGIN, END = "/*BANK-BEGIN*/", "/*BANK-END*/"


def html_block() -> str:
    return f"{BEGIN}{json.dumps(bank(), ensure_ascii=False)}{END}"


def embedded_bank(html: str) -> dict | None:
    m = re.search(re.escape(BEGIN) + r"(.*?)" + re.escape(END), html, re.S)
    return json.loads(m.group(1)) if m else None


if __name__ == "__main__":
    if "--html" in sys.argv:
        p = Path(__file__).with_name("survey.html")
        s = p.read_text()
        new = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), lambda _: html_block(), s, flags=re.S)
        if new == s and embedded_bank(s) != bank():
            sys.exit("no BANK markers in survey.html")
        p.write_text(new)
        print("survey.html bank updated:", len(ITEMS), "items")
    else:
        print(json.dumps(bank(), indent=1))
