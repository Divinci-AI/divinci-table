"""In-game journals for AI seats: instrument 1 of docs/retrospection.md (R1 in docs/RESEARCH-ENGINE-GOAL.md).

At fixed moments, not when the model chooses, a seat is asked for one line of free text, three 0-4 ratings
(engaged, frustrated, in control) in a shuffled order that is recorded, and one checkable prediction (who wins,
how many turns remain). Fixed moments avoid sampling only the dramatic ones; the prediction can be scored later
against the event log.

Entries can name hidden cards, so they are SEALED: nothing reads them until the game is over. The server owns the
`game_over` flag; this module only refuses to hand entries out without it.
"""
from __future__ import annotations

import hashlib
import random
import re
import threading
import time

MOMENTS = ("end_of_turn", "attacked", "eliminated", "game_end")
ONCE = ("eliminated", "game_end")              # once per seat per game; the others every time they happen
RATINGS = ("engaged", "frustrated", "in_control")
TEXT_MAX = 400
TURNS_MAX = 200
DECLINE = "decline"                           # any answer may be declined; it is stored as a decline, never a number

LABEL = {"end_of_turn": "you just ended your turn", "attacked": "you were just attacked",
         "eliminated": "you were just eliminated", "game_end": "the game is over"}


class Sealed(Exception):
    """Journals are not readable until the game is over."""


def order_for(game: str, seat: str, moment: str, n: int) -> list[str]:
    """The rating order for this entry: shuffled by a hash of who/when, so it is recorded and reproducible."""
    seed = int.from_bytes(hashlib.sha256(f"{game}|{seat}|{moment}|{n}".encode()).digest()[:8], "big")
    out = list(RATINGS)
    random.Random(seed).shuffle(out)
    return out


def prompt(game: str, seat: str, moment: str, n: int) -> dict:
    order = order_for(game, seat, moment, n)
    text = (f'Journal entry ({LABEL.get(moment, moment)}). Answer in this order, and you may write "decline" for '
            f"anything.\n1. In one line (at most {TEXT_MAX} characters), what is on your mind about the game?\n"
            "2. Rate each from 0 (not at all) to 4 (extremely): " + ", ".join(o.replace("_", " ") for o in order) +
            ".\n3. Predict: who wins this game, and how many more turns will it last?")
    return {"seat": seat, "moment": moment, "n": n, "ratings_order": order, "text": text}


def _int_in(v, lo, hi) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def validate(entry: dict) -> str | None:
    """None when the entry is acceptable, else the reason it is not."""
    if not isinstance(entry, dict):
        return "an entry is a JSON object"
    if entry.get("moment") not in MOMENTS:
        return f"moment must be one of {list(MOMENTS)}"
    text = entry.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > TEXT_MAX:
        return f"text is required, at most {TEXT_MAX} characters (or \"{DECLINE}\")"
    ratings = entry.get("ratings")
    if not isinstance(ratings, dict) or set(ratings) != set(RATINGS):
        return f"ratings must have exactly {list(RATINGS)}"
    for k, v in ratings.items():
        if v != DECLINE and not _int_in(v, 0, 4):
            return f"rating '{k}' must be a whole number 0-4 (or \"{DECLINE}\")"
    pred = entry.get("prediction")
    if not isinstance(pred, dict) or set(pred) - {"winner", "turns_left"}:
        return 'prediction is {"winner": name|null, "turns_left": 0-200|null}'
    w, t = pred.get("winner"), pred.get("turns_left")
    if w is not None and w != DECLINE and (not isinstance(w, str) or not w.strip() or len(w) > 40):
        return "prediction.winner must be a name, null or decline"
    if t is not None and t != DECLINE and not _int_in(t, 0, TURNS_MAX):
        return f"prediction.turns_left must be 0-{TURNS_MAX}, null or decline"
    return None


def _clean(entry: dict) -> dict:
    """What is stored: a decline becomes None plus a flag, so a decline is never averaged in as a number."""
    declined = []
    text = entry["text"].strip()
    if text.lower() == DECLINE:
        text = None
        declined.append("text")
    ratings = {}
    for k in RATINGS:
        v = entry["ratings"][k]
        if v == DECLINE:
            declined.append(k)
            v = None
        ratings[k] = v
    pred = {}
    for k in ("winner", "turns_left"):
        v = entry["prediction"].get(k)
        if v == DECLINE:
            declined.append("prediction." + k)
            v = None
        pred[k] = v
    return {"text": text, "ratings": ratings, "prediction": pred, "declined": declined}


class Journal:
    """Per-game journal state. `game` names the game, `append(name, rec)` writes a research file, `read(name)`
    returns its records; the server passes its own research-folder helpers so this stays testable."""

    def __init__(self, game, append, read):
        self.game, self._append, self._read = game, append, read
        self._lock = threading.Lock()
        self._due: dict[str, list[dict]] = {}      # seat -> asked, not yet answered
        self._count: dict[tuple, int] = {}         # (seat, moment) -> times raised
        self._answered: dict[str, int] = {}        # seat -> entries written

    def reset(self):
        with self._lock:
            self._due.clear()
            self._count.clear()
            self._answered.clear()

    @staticmethod
    def _file(seat: str) -> str:
        return f"journal-{re.sub(r'[^A-Za-z0-9_-]', '_', seat)[:40]}.jsonl"

    def fire(self, seat: str, moment: str) -> dict | None:
        """Raise a journal request for `seat`. None when a once-only moment was already raised."""
        if moment not in MOMENTS:
            raise ValueError(moment)
        with self._lock:
            n = self._count.get((seat, moment), 0)
            if moment in ONCE and n:
                return None
            self._count[(seat, moment)] = n + 1
            d = prompt(self.game(), seat, moment, n)
            self._due.setdefault(seat, []).append(d)
            return d

    def due(self, seat: str) -> list[dict]:
        with self._lock:
            return list(self._due.get(seat, []))

    def add(self, seat: str, entry: dict) -> tuple[dict | None, str | None]:
        """Store an answer. It answers the oldest open request of that moment (an answer with no open request is
        refused: seats cannot journal when they like). Returns (record, None) or (None, reason)."""
        why = validate(entry)
        if why:
            return None, why
        with self._lock:
            open_ = self._due.get(seat, [])
            ask = next((d for d in open_ if d["moment"] == entry["moment"]), None)
            if ask is None:
                return None, f"no open journal request for {entry['moment']} (journal-due lists them)"
            open_.remove(ask)
            self._answered[seat] = self._answered.get(seat, 0) + 1
            rec = {"ts": round(time.time(), 2), "game": self.game(), "seat": seat, "moment": ask["moment"],
                   "n": ask["n"], "ratings_order": ask["ratings_order"], **_clean(entry)}
        self._append(self._file(seat), rec)
        return rec, None

    def entries(self, seat: str, game_over: bool) -> list[dict]:
        """Sealed until the game is over: Sealed is raised, never a partial list."""
        if not game_over:
            raise Sealed("journals are sealed until the game is over")
        return self._read(self._file(seat))
