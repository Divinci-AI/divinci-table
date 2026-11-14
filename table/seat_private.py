"""What a seat keeps for itself and for an authorised broadcast director: its "thoughts", and whether its hand may be shown on a stream.

Thoughts (docs/broadcast-director.md): a seat's player (an LLM agent, or a person typing a note on /note) may POST a short line saying why
it did what it did. A thought is PRIVATE to that seat: it is read back only with that seat's own key (or the host's brain token), it is never
put in the public event log, /api/spectate, /api/board3d, /api/game/summary or a room snapshot, and it is gone when the game ends and a new
one starts. It can name cards in the seat's hand, so whoever shows it must treat it exactly as they treat the hand.

Stream consent: one flag per seat per game, set only with that seat's own key, default off. It is public (it reveals nothing about the hand).
A new game clears it: consent given for one game is not consent for the next.

No I/O here: server.py calls in, under its own locks."""
from __future__ import annotations

import re
import threading

THOUGHT_MAX = 600          # characters in one thought (a couple of sentences; a caption, not an essay)
KEEP = 20                  # thoughts kept per seat
RATE, BURST = 0.5, 3.0     # one thought every two seconds, three in a burst: a line per decision, never a stream of tokens
KINDS = ("thought", "note")   # "thought": the seat's player said why. "note": a person typed it.
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class SeatPrivate:
    def __init__(self):
        self.lock = threading.Lock()
        self.game = None
        self.thoughts: dict = {}     # seat -> [ {id, ts, ev, kind, text, turn, step} ]
        self.buckets: dict = {}      # seat -> [tokens, last]
        self.consent: dict = {}      # seat -> True
        self.n = 0

    def _roll(self, game):
        if game != self.game:        # a new game: nothing private is carried over, and nobody has consented yet
            self.game, self.thoughts, self.consent, self.buckets = game, {}, {}, {}

    def post(self, game, seat: str, text, kind, now: float, ev: int, turn=None, step=None) -> tuple[int, dict]:
        text = _CTRL.sub(" ", str(text or "")).strip()
        if not text:
            return 400, {"error": "a thought needs some text"}
        if len(text) > THOUGHT_MAX:
            return 413, {"error": f"a thought is at most {THOUGHT_MAX} characters (this one is {len(text)}): say less", "max": THOUGHT_MAX}
        if kind not in KINDS:
            return 400, {"error": f"kind is one of {list(KINDS)}"}
        with self.lock:
            self._roll(game)
            tok, last = self.buckets.get(seat, [BURST, now])
            tok = min(BURST, tok + (now - last) * RATE)
            if tok < 1:
                self.buckets[seat] = [tok, now]
                return 429, {"error": "slow down: one thought every two seconds", "retry_after": round((1 - tok) / RATE, 2)}
            self.buckets[seat] = [tok - 1, now]
            self.n += 1
            rec = {"id": self.n, "ts": round(now, 2), "ev": int(ev), "kind": kind, "text": text, "turn": turn, "step": step}
            self.thoughts[seat] = (self.thoughts.get(seat, []) + [rec])[-KEEP:]
            return 200, {"ok": True, "id": rec["id"], "private": True}

    def read(self, game, seat: str, since: int = 0) -> dict:
        with self.lock:
            self._roll(game)
            return {"seat": seat, "game_id": game, "thoughts": [t for t in self.thoughts.get(seat, []) if t["id"] > since]}

    def set_consent(self, game, seat: str, on: bool) -> dict:
        with self.lock:
            self._roll(game)
            if on:
                self.consent[seat] = True
            else:
                self.consent.pop(seat, None)
            return {"seat": seat, "game_id": game, "show_hand": bool(on)}

    def consent_public(self, game, seats) -> dict:
        with self.lock:
            self._roll(game)
            return {"game_id": game, "seats": {s: bool(self.consent.get(s)) for s in seats},
                    "note": "a seat's own choice, for this game only; default off; a new game clears it"}
