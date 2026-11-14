"""Which seat is on screen. A schedule (each seat for dwell_secs), cuts to where the action is, a recap card when a game ends, and the
operator's overrides. Pure: no network, the clock is passed in (director_rotation_test.py).

Priority, highest first: blank > pinned > recap > action cut > schedule."""
from __future__ import annotations


class Rotation:
    def __init__(self, slots: list, dwell=45.0, min_hold=10.0, action_hold=18.0, recap_secs=20.0, now=0.0):
        self.slots = list(slots)                 # [(table index, seat name)]
        self.dwell, self.min_hold, self.action_hold, self.recap_secs = dwell, min_hold, action_hold, recap_secs
        self.i = 0                               # the schedule's place
        self.since = now                         # when the schedule's current slot began
        self.cut = None                          # {"slot", "until", "kind"} an action cut
        self.recap = None                        # {"table", "until"}
        self.pinned = None
        self.blank = False
        self.last_cut = -1e9
        self.recapped: set = set()               # game ids already given their recap
        self.reason = "schedule"

    def featured(self, now: float):
        """(slot, mode). mode: schedule | action:<kind> | recap | pinned | blank."""
        self.tick(now)
        if self.blank:
            return None, "blank"
        if self.pinned is not None:
            return self.pinned, "pinned"
        if self.recap:
            return (self.recap["table"], None), "recap"
        if self.cut:
            return self.cut["slot"], "action:" + self.cut["kind"]
        return (self.slots[self.i] if self.slots else None), "schedule"

    def tick(self, now: float):
        if self.recap and now >= self.recap["until"]:
            self.recap = None
            self._advance(now)
        if self.cut and now >= self.cut["until"]:
            self.cut = None
            self.since = now                     # the schedule resumes where it was, with a fresh dwell
        if not (self.pinned or self.recap or self.cut or self.blank) and self.slots and now - self.since >= self.dwell:
            self._advance(now)

    def _advance(self, now):
        if self.slots:
            self.i = (self.i + 1) % len(self.slots)
        self.since = now

    def on_trigger(self, now: float, table: int, seat, kind: str, weight: int, game_id=None):
        if kind == "game-over":
            if game_id in self.recapped:
                return False
            self.recapped.add(game_id)
            self.cut = None
            self.recap = {"table": table, "until": now + self.recap_secs}
            self.last_cut = now
            return True
        if self.recap or self.pinned is not None or self.blank:
            return False
        slot = (table, seat) if (table, seat) in self.slots else next((s for s in self.slots if s[0] == table), None)
        if slot is None:
            return False
        cur = self.cut["slot"] if self.cut else (self.slots[self.i] if self.slots else None)
        if slot == cur:
            return False                          # already there
        if now - self.last_cut < self.min_hold and not (self.cut and weight > self.cut.get("weight", 0)):
            return False                          # no ping-pong: a cut holds at least min_hold, unless something bigger happens
        self.cut = {"slot": slot, "until": now + self.action_hold, "kind": kind, "weight": weight}
        self.last_cut = now
        return True

    # ---- the operator ------------------------------------------------------------------------------------------------------------
    def pin(self, slot):
        self.pinned = slot if slot in self.slots else None
        return self.pinned is not None

    def unpin(self, now):
        self.pinned = None
        self.since = now

    def next(self, now):
        self.pinned, self.cut, self.recap = None, None, None
        self._advance(now)

    def set_blank(self, on: bool):
        self.blank = bool(on)
