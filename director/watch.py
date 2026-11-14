"""One table, watched: polls its public state and, for the seats integrity allows, their private state; keeps the delay buffers; and
answers "what may be shown right now" (view()). Network and clock are injected, so tests drive it with a fake table."""
from __future__ import annotations

import re
import time

from . import integrity as ig
from .tableclient import TableClient, TableError

ACTIONS = ("land", "cast", "attack", "block", "equip", "station", "activate", "cycle", "damage", "end", "keep", "mulligan", "choose")
STEPS = ["untap", "upkeep", "draw", "main 1", "beginning of combat", "declare attackers", "declare blockers", "combat damage", "main 2", "end step", "cleanup"]


def _why_for(ev: dict, legal_history: list) -> str | None:
    """The table's own plain reason for the move this public event announces, from the legal lists read just before it. None if no
    listed move matches: the panel then shows only what the public log says. Never invented."""
    act, text = ev.get("action"), (ev.get("text") or "").lower()
    for acts in reversed(legal_history):
        same = [a for a in acts if a.get("action") == act]
        named = [a for a in same if (a.get("body") or {}).get("name") and str(a["body"]["name"]).lower() in text]
        pick = named[0] if named else (same[0] if len(same) == 1 and not (same[0].get("body") or {}).get("name") else None)
        if pick and pick.get("why"):
            return str(pick["why"])[:240]
    return None


class TableWatch:
    def __init__(self, table, cfg, client: TableClient | None = None, clock=time.time):
        self.t, self.cfg, self.clock = table, cfg, clock
        self.c = client or TableClient(table.url)
        self.turns, self.secs = cfg.delay_for(table)
        self.game_id = None
        self.phase: dict = {}
        self.consent: dict = {}
        self.events: list = []
        self.cursor = 0
        self.boundaries: list = []
        self.boards: dict = {}          # boundary event id -> {"board", "life", "turn_of"}
        self.live_board: dict | None = None
        self.summary: dict = {}
        self.match: dict = {}
        self.buf = {s.name: ig.SeatBuffer() for s in table.seats}
        self.legal_hist = {s.name: [] for s in table.seats}
        self.thought_cursor = {s.name: 0 for s in table.seats}
        self.vis = {s.name: ig.Visibility(False, "not polled yet") for s in table.seats}
        self.hidden: set = set()        # seats the operator hid (panic); cleared only by the operator
        self.status = "starting"
        self.private_reads = {s.name: 0 for s in table.seats}

    # ---- polling -------------------------------------------------------------------------------------------------------------------
    def _reset(self, game_id):
        self.game_id, self.events, self.cursor, self.boundaries, self.boards = game_id, [], 0, [], {}
        self.summary, self.live_board = {}, None
        for b in self.buf.values():
            b.purge()
        for k in self.legal_hist:
            self.legal_hist[k] = []; self.thought_cursor[k] = 0

    def hide(self, seat: str | None, on: bool = True):
        for s in ([seat] if seat else list(self.buf)):
            if on:
                self.hidden.add(s); self.buf[s].purge()       # what was held is thrown away: un-hiding starts from nothing
            else:
                self.hidden.discard(s)

    def poll(self):
        now = self.clock()
        try:
            ph = self.c.public("/api/phase")
        except TableError as e:
            self.status = f"table not answering ({e.code})"
            return
        self.status = "ok"
        if ph.get("game_id") != self.game_id:
            self._reset(ph.get("game_id"))
        self.phase = ph
        try:
            co = self.c.public("/api/stream-consent")
            self.consent = dict(co.get("seats") or {}) if co.get("game_id") == self.game_id else {}
        except TableError:
            self.consent = {}                                   # an older table: nobody has consented (fail closed)
        fresh: dict = {}
        for s in self.t.seats:
            v = ig.may_read(s, bool(self.consent.get(s.name)), s.name in self.hidden)
            if not v.read and self.vis[s.name].read:
                self.buf[s.name].purge()                        # consent withdrawn or hidden: drop what was held
            self.vis[s.name] = v
            if not v.read:
                continue
            try:
                st = self.c.private("/api/brain/state", s.name, s.key_file)
                self.private_reads[s.name] += 1
                fresh[s.name] = {"hand": st.get("hand") or []}
                try:
                    lg = self.c.private("/api/seat/legal", s.name, s.key_file)
                    fresh[s.name]["legal"] = lg.get("actions") or []
                except TableError:
                    pass
                try:
                    th = self.c.private("/api/seat/thought", s.name, s.key_file, f"since={self.thought_cursor[s.name]}")
                    fresh[s.name]["thoughts"] = th.get("thoughts") or []
                except TableError:
                    pass                                        # an older table has no thoughts: the panel falls back to "why"
            except TableError as e:
                self.vis[s.name] = ig.Visibility(False, f"the table refused this seat's key ({e.code})" if e.code in (401, 403) else f"not readable ({e.code})")
            except Exception as e:  # noqa: BLE001  (a config error on the key file: say which kind, never the content)
                self.vis[s.name] = ig.Visibility(False, type(e).__name__ + ": " + str(e)[:120])
        # public events AFTER the private reads: `last` is then never earlier than what was read (the tag errs late, which is safe)
        try:
            d = self.c.public("/api/events", f"since={self.cursor}")
        except TableError:
            return
        if d.get("restarted"):
            self.cursor = 0
            return
        new = d.get("events") or []
        last = int(d.get("last") or 0)
        if new:
            self.cursor = max(self.cursor, max(int(e["id"]) for e in new))
            self.events = (self.events + new)[-3000:]
        for name, f in fresh.items():
            b = self.buf[name]
            b.add_hand(last, now, f["hand"])
            for th in f.get("thoughts") or []:
                self.thought_cursor[name] = max(self.thought_cursor[name], int(th["id"]))
                b.add_item(("t", th["id"]), max(last, int(th.get("ev") or 0)), now,
                           {"source": "note" if th.get("kind") == "note" else "thought", "text": str(th.get("text") or "")[:600], "ev": th.get("ev")})
        for e in new:                                           # a move the seat took, with the table's own reason for it
            who = e.get("speaker")
            if e.get("type") == "say" and who in fresh and e.get("action") in ACTIONS:
                why = _why_for(e, self.legal_hist[who])
                self.buf[who].add_item(("d", e["id"]), max(last, int(e["id"])), now,
                                       {"source": "move", "text": str(e.get("text") or "")[:240], "why": why, "ev": e["id"]})
        for name, f in fresh.items():
            if "legal" in f:
                self.legal_hist[name] = (self.legal_hist[name] + [f["legal"]])[-6:]
        try:
            sp = self.c.public("/api/spectate")
            if sp.get("board") is not None:
                eid = int(sp.get("as_of_event") or 0)
                if not self.boundaries or eid > self.boundaries[-1]:
                    self.boundaries.append(eid); del self.boundaries[:-8]
                self.boards[eid] = {"board": sp["board"], "life": sp.get("life") or {}, "turn_of": sp.get("turn_of")}
                for k in sorted(self.boards)[:-8]:
                    del self.boards[k]
        except TableError:
            pass
        if self.turns <= 0 or self.over:
            try:
                self.live_board = self.c.public("/api/board3d")
            except TableError:
                pass
        if self.over and not self.summary.get("over"):
            try:
                self.summary = self.c.public("/api/game/summary")
            except TableError:
                pass
        try:
            m = self.c.public("/api/match")
            self.match = m if m.get("on") else {}
        except TableError:
            self.match = {}                                     # a table without best-of-three: no match score, nothing else changes

    # ---- what may be shown ---------------------------------------------------------------------------------------------------------
    @property
    def over(self) -> bool:
        return bool(self.phase.get("game_over")) or any(e.get("type") == "game-over" for e in self.events[-40:])

    def bound(self) -> float:
        return ig.boundary(self.turns, self.boundaries, self.over)

    def shown_events(self) -> list:
        """The public log as the stream may see it: everything at a live table; up to the spectator boundary where a person plays."""
        b, cut = self.bound(), self.clock() - self.secs
        return [e for e in self.events if e["id"] <= b and e.get("ts", 0) <= cut]

    def board(self) -> dict | None:
        b = self.bound()
        if b == ig.INF:
            return self.live_board or (self.boards[max(self.boards)]["board"] if self.boards else None)
        return (self.boards.get(b) or {}).get("board")

    def seat_private(self, name: str) -> dict:
        """What of this seat's private state may be shown NOW (already through both gates)."""
        v = self.vis[name]
        if not v.read:
            return {"shown": False, "reason": v.reason, "hand": None, "thinking": []}
        hand, items = self.buf[name].released(self.bound(), self.clock(), self.secs)
        return {"shown": hand is not None, "reason": v.reason if hand is not None else "waiting for the delay", "hand": hand, "thinking": items[-6:]}

    def delay_label(self) -> str:
        if self.over:
            return "game over"
        if self.turns <= 0 and self.secs <= 0:
            return "live (all-AI table)" if not self.t.human_involved else "LIVE"
        parts = ([f"{self.turns} turn{'s' if self.turns != 1 else ''} behind"] if self.turns > 0 else []) + ([f"{self.secs:g}s late"] if self.secs else [])
        return " + ".join(parts)

    def view(self) -> dict:
        """The table as the stream may see it: public board (allow-listed), turn, the log, and per-seat private info that is released."""
        bd = self.board() or {}
        seats = {s.get("name"): ig.public_seat(s) for s in bd.get("seats") or []}
        evs = self.shown_events()
        log = []
        for e in evs[-200:]:
            if e.get("type") == "say" and e.get("speaker"):
                log.append({"id": e["id"], "who": e["speaker"], "text": str(e.get("text") or "")[:200]})
            elif e.get("type") == "life" and isinstance(e.get("delta"), int):
                log.append({"id": e["id"], "who": e.get("player"), "text": f"{'+' if e['delta'] > 0 else ''}{e['delta']} life (now {e.get('life')})"})
            elif e.get("type") == "game-over":
                log.append({"id": e["id"], "who": "table", "text": f"Game over: {e.get('winner')} wins."})
        out_seats = []
        for s in self.t.seats:
            ps = seats.get(s.name) or {"name": s.name, "permanents": [], "graveyard": []}
            out_seats.append({**ps, "kind": s.kind, "player": s.player, "private": self.seat_private(s.name)})
        return {"name": self.t.name, "status": self.status, "game_id": self.game_id, "turn": bd.get("turn"), "step": bd.get("step"),
                "steps": STEPS, "delay": self.delay_label(), "human_involved": self.t.human_involved, "over": self.over,
                "seats": out_seats, "log": log[-14:],
                "match": ({"wins": self.match.get("wins"), "score": self.match.get("score"), "game": (self.match.get("current") or {}).get("n")}
                          if self.match else None),
                "recap": self._recap() if self.over else None}

    def _recap(self) -> dict:
        s = self.summary or {}
        sm = s.get("summary") or {}
        return {"winner": s.get("winner"), "totals": s.get("totals"), "turns": sm.get("turns"), "life_lost": sm.get("life_lost"),
                "key_spells": [{k: x.get(k) for k in ("player", "name", "turn")} for x in (sm.get("key_spells") or [])[:4]]}

    def triggers(self, since_id: int) -> list:
        """Moments worth cutting to, from the events the stream may see, newer than since_id: (event id, seat, kind, weight)."""
        out = []
        for e in self.shown_events():
            if e["id"] <= since_id:
                continue
            t = e.get("type")
            if t == "game-over":
                out.append((e["id"], e.get("winner"), "game-over", 100))
            elif t == "life" and isinstance(e.get("delta"), int) and abs(e["delta"]) >= 4:
                out.append((e["id"], e.get("attacker") or e.get("player"), "life-swing", 60))
            elif t == "say" and e.get("action") == "attack":
                out.append((e["id"], e.get("speaker"), "combat", 40))
            elif t == "cast" and float(e.get("mv") or 0) >= 4:
                out.append((e["id"], e.get("player"), "big-spell", 30))
        return out
