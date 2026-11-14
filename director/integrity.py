"""The rules about private information, in one place, as plain functions (director_integrity_test.py tests each).

Private = a seat's hand, its thoughts, and anything derived from them (the "why" of a move it took, hand-informed commentary).
Everything private passes through exactly two gates:

  1. may_read():   may the director fetch this seat's private state at all? A person's seat: only if that person switched it on for the
                   CURRENT game with their own key (the table records it), default off. An AI seat: yes, unless configured off. A seat
                   the operator hid with the panic key: no, and what was buffered is thrown away.
  2. SeatBuffer:   what was fetched is held back. At a table with a person, nothing private is released until the table's own spectator
                   boundary (/api/spectate: one turn behind) has passed the moment it was observed, plus any extra seconds. At an all-AI
                   table it may be released at once.

Nothing downstream (the pages, the picture-in-picture, the commentators' feed) is given the buffers: they get released() and nothing else."""
from __future__ import annotations

from dataclasses import dataclass

INF = float("inf")
HAND_FIELDS = ("name", "cost", "type", "text", "pt")                       # what of a hand card may reach a page (no ids, no targets, no slots)
PERM_FIELDS = ("name", "type", "tapped", "pt", "counters", "token", "sick", "attached_names", "damage", "keywords")
SEAT_FIELDS = ("name", "life", "hand", "library", "graveyard_count", "commander", "out")


@dataclass(frozen=True)
class Visibility:
    read: bool
    reason: str


def may_read(seat, consent: bool, hidden: bool) -> Visibility:
    """Gate 1. `seat` is a config.Seat; `consent` is what the TABLE says this seat chose for the current game."""
    if hidden:
        return Visibility(False, "hidden by the operator")
    if not seat.show:
        return Visibility(False, "kept off the stream in the config")
    if not seat.key_file:
        return Visibility(False, "no key file configured")
    if seat.kind == "human":
        if not consent:
            return Visibility(False, "this player has not switched it on for this game")
        return Visibility(True, "the player switched it on for this game")
    return Visibility(True, "an AI seat")


def boundary(turns: int, boundaries: list, game_over: bool) -> float:
    """The newest event id private information may be shown up to. `boundaries` are the table's spectator snapshots' event ids (one per
    turn start), oldest first. turns=1 is exactly what /watch shows. turns=0 is live. A finished game holds no secrets worth keeping."""
    if turns <= 0 or game_over:
        return INF
    if len(boundaries) < turns:
        return -1                       # not a full turn behind yet: nothing
    return boundaries[-turns]


class SeatBuffer:
    """Gate 2 for one seat. Observations are tagged with the table's last public event id read AFTER the private fetch, so the tag is
    never earlier than what the observation contains (late is safe; early would leak)."""
    KEEP = 600

    def __init__(self):
        self.snaps: list = []      # (tag, ts, hand)   only when the hand changed
        self.items: list = []      # (tag, ts, item)   thoughts and decisions
        self._seen: set = set()

    def purge(self):
        self.snaps.clear(); self.items.clear(); self._seen.clear()

    def add_hand(self, tag: int, ts: float, hand: list):
        clean = [{k: c.get(k) for k in HAND_FIELDS} for c in hand]
        if self.snaps and self.snaps[-1][2] == clean:
            return
        self.snaps.append((tag, ts, clean))
        del self.snaps[:-self.KEEP]

    def add_item(self, key, tag: int, ts: float, item: dict):
        if key in self._seen:
            return
        self._seen.add(key)
        self.items.append((tag, ts, item))
        del self.items[:-self.KEEP]

    def released(self, bound: float, now: float, delay_secs: float = 0.0) -> tuple:
        """(hand or None, [items]) that may be shown now."""
        ok = lambda tag, ts: tag <= bound and ts <= now - delay_secs      # noqa: E731
        hand = next((h for tag, ts, h in reversed(self.snaps) if ok(tag, ts)), None)
        return hand, [it for tag, ts, it in self.items if ok(tag, ts)]


def public_seat(s: dict) -> dict:
    """One seat of a PUBLIC board (from /api/spectate or /api/board3d), reduced to an allow-list. A face-down card keeps no name."""
    out = {k: s.get(k) for k in SEAT_FIELDS}
    out["permanents"] = [{k: p.get(k) for k in PERM_FIELDS} for p in s.get("permanents") or []]
    out["graveyard"] = [str(x)[:60] for x in (s.get("graveyard") or [])][-6:]
    return out


def commentary_may_use_private(table, policy: str) -> bool:
    """May hand-informed lines be written about this table? `policy`: "public-only" (never), "released" (whatever the hand view itself
    shows, i.e. already delayed and consented), default "auto": released at all-AI tables, public-only where a person is playing (a
    one-turn delay does not protect a card held for three turns, and a line read aloud reaches the room)."""
    if policy == "public-only":
        return False
    if policy == "released":
        return True
    return not table.human_involved
