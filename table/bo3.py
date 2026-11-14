"""A match: best two out of three games between the same two seats with the same decks, on ONE table process.

This file is the match's own state and rules and nothing else (no server, no engine, no clock of its own: every method that needs
the time is given it), so it can be tested alone. table/server.py holds one Match when it is started with --match bo3 and calls:

    game_started(n?, game_id, first, …)   the first turn of a game is being handed out (game 1: after the roll)
    record(winner, totals, turns, …)      a game ended (winner None = a drawn game: counts for nobody, one more game is played)
    choose / wait / start_now             the people between two games
    due(now)                              is it time to start the next game?
    take_next(now)                        the next game's number, who plays first, who chose and how
    public(now)                           what everyone may see (GET /api/match): never a card, never a key

The rules it holds:
  * a result that should not count (the host's call: void_last) is taken out and that game is played again, same player first;
  * the match ends when a seat has won `need` games (2 of 3), or after `max_games` games (drawn games can make it longer than three:
    the cap stops a match that would never end; then whoever has more wins takes it, and equal wins is a drawn match);
  * who goes first: game 1 is the table's own roll (not this file's business). After that there is NO roll: the loser of the previous
    game chooses to play first or draw first. After a drawn game the chooser is whoever chose (or won the roll) for the drawn game.
    A chooser who says nothing by the time the next game starts plays first.
State is plain JSON (to_json / from_json) and is written next to the table's saves after every change (save)."""
from __future__ import annotations

import json
import os
from pathlib import Path

PLAY, DRAW = "play", "draw"


def score_line(seats: list, wins: dict, over: bool, winner) -> str:
    """"Sam leads 1–0", "Tied 1–1", "Michael wins the match 2–1", "The match is drawn 1–1"."""
    a, b = seats
    wa, wb = int(wins.get(a, 0)), int(wins.get(b, 0))
    hi, lo = max(wa, wb), min(wa, wb)
    if over:
        return f"{winner} wins the match {hi}–{lo}" if winner else f"The match is drawn {wa}–{wb}"
    if wa == wb:
        return f"Tied {wa}–{wb}" if wa else "No games won yet"
    return f"{a if wa > wb else b} leads {hi}–{lo}"


class Match:
    def __init__(self, seats, best_of: int = 3, max_games: int | None = None, countdown: float = 25.0, max_wait: float = 300.0, path=None):
        if len(seats) != 2 or seats[0] == seats[1]:
            raise ValueError("a match is between exactly two seats")
        if best_of < 1 or best_of % 2 == 0:
            raise ValueError("best_of is an odd number (3 = best two out of three)")
        self.seats = list(seats)
        self.best_of = int(best_of)
        self.need = self.best_of // 2 + 1
        self.max_games = int(max_games or self.best_of + 2)          # room for two drawn games, then the match is called
        self.countdown = max(0.0, float(countdown))
        self.max_wait = max(self.countdown, float(max_wait))
        self.path = Path(path) if path else None
        self.wins = {s: 0 for s in self.seats}
        self.games: list[dict] = []                                  # finished games, in order
        self.current: dict | None = None                             # the game being played (None between games and before game 1)
        self.next: dict | None = None                                # between two games: the countdown and the choice
        self.over = False
        self.winner = None

    # ── the games ────────────────────────────────────────────────────────────────────────────────
    def other(self, seat):
        return self.seats[1] if seat == self.seats[0] else self.seats[0]

    def game_started(self, game_id: str, first: str, chooser: str | None = None, choice: str | None = None, how: str = "roll") -> dict:
        """The first turn of a game is being handed out. Idempotent per game id (the table calls it before AND after the mulligans)."""
        if self.current and self.current.get("game_id") == game_id:
            return self.current
        if self.over or first not in self.seats:
            return self.current or {}
        self.current = {"n": len(self.games) + 1, "game_id": game_id, "first": first, "chooser": chooser or first, "choice": choice, "how": how}
        self.next = None
        self.save()
        return self.current

    def record(self, winner, totals: dict | None = None, turns: int = 0, lines: list | None = None, now: float = 0.0, game_id: str = "") -> dict:
        """A game ended. winner None: a drawn game. Returns the game's record; sets up the next game's countdown, or ends the match."""
        if self.over:
            return self.games[-1] if self.games else {}
        cur = self.current or {"n": len(self.games) + 1, "game_id": game_id, "first": None, "chooser": None, "choice": None, "how": "unknown"}
        if winner not in self.seats:
            winner = None
        if winner:
            self.wins[winner] += 1
        g = {"n": cur["n"], "game_id": cur.get("game_id") or game_id, "winner": winner, "draw": winner is None, "turns": int(turns or 0),
             "totals": {s: (totals or {}).get(s) for s in self.seats}, "first": cur.get("first"), "chooser": cur.get("chooser"),
             "choice": cur.get("choice"), "how": cur.get("how"), "lines": [str(x)[:240] for x in (lines or [])][:4], "ended": round(now, 2)}
        self.games.append(g)
        self.current = None
        lead = max(self.wins.values())
        if lead >= self.need or len(self.games) >= self.max_games:
            self.over = True
            top = [s for s in self.seats if self.wins[s] == lead]
            self.winner = top[0] if len(top) == 1 else None
            self.next = None
        else:
            # the loser chooses; after a drawn game, whoever chose (or won the roll) for the drawn game
            chooser = self.other(winner) if winner else (g.get("chooser") or g.get("first") or self.seats[0])
            self.next = {"n": g["n"] + 1, "chooser": chooser, "why": "lost" if winner else "drawn", "choice": None, "how": None,
                         "since": round(now, 2), "due": round(now + self.countdown, 2), "hard_due": round(now + self.max_wait, 2), "holds": [], "go": False}
        self.save()
        return g

    # ── between two games ────────────────────────────────────────────────────────────────────────
    def choose(self, seat: str, choice: str, how: str = "chose") -> tuple[bool, str]:
        nx = self.next
        if not nx:
            return False, "no game is waiting to start"
        if seat != nx["chooser"]:
            return False, f"{nx['chooser']} chooses who plays first (the loser of the last game chooses)"
        if choice not in (PLAY, DRAW):
            return False, "choose play or draw"
        if nx["choice"]:
            return False, f"{seat} already chose to {nx['choice']} first"
        nx.update(choice=choice, how=how)
        self.save()
        return True, ""

    def wait(self, seat: str, now: float) -> tuple[bool, str]:
        """"Wait": this seat is not ready for the next game. The countdown stops for it, until it presses Start now or max_wait has passed."""
        nx = self.next
        if not nx or seat not in self.seats:
            return False, "no game is waiting to start"
        if seat not in nx["holds"]:
            nx["holds"].append(seat)
        nx["go"] = False
        self.save()
        return True, ""

    def start_now(self, seat: str | None, now: float) -> tuple[bool, str]:
        """"Start now": this seat's own Wait is lifted; with nobody else waiting the next game starts at once. seat None: the host (lifts every Wait)."""
        nx = self.next
        if not nx or (seat is not None and seat not in self.seats):
            return False, "no game is waiting to start"
        if seat is None:
            nx["holds"] = []
        elif seat in nx["holds"]:
            nx["holds"].remove(seat)
        if nx["holds"]:
            self.save()
            return True, f"waiting for {', '.join(nx['holds'])}"
        nx["go"] = True
        self.save()
        return True, ""

    def void_last(self, now: float) -> dict | None:
        """The host says the last finished game does not count (a life total reached 0 by a slip of the finger): it is taken out of the match
        and PLAYED AGAIN, with the same player first as before. Only between two games or after the match's end: once the next game has begun
        it is too late (None). Returns the game that was taken out."""
        if not self.games or self.current is not None:
            return None
        g = self.games.pop()
        if g.get("winner") in self.wins:
            self.wins[g["winner"]] = max(0, self.wins[g["winner"]] - 1)
        self.over, self.winner = False, None
        chooser = g.get("chooser") if g.get("chooser") in self.seats else g.get("first") if g.get("first") in self.seats else self.seats[0]
        first = g.get("first") if g.get("first") in self.seats else chooser
        self.next = {"n": g["n"], "chooser": chooser, "why": "replay", "choice": PLAY if first == chooser else DRAW, "how": "replay",
                     "since": round(now, 2), "due": round(now + self.countdown, 2), "hard_due": round(now + self.max_wait, 2), "holds": [], "go": False}
        self.save()
        return g

    def restart_clock(self, now: float):
        """The table came back from a save between two games: the countdown starts again (the old deadline was another process's)."""
        if self.next:
            self.next.update(since=round(now, 2), due=round(now + self.countdown, 2), hard_due=round(now + self.max_wait, 2), go=False)
            self.save()

    def due(self, now: float) -> bool:
        nx = self.next
        if not nx or self.over:
            return False
        if nx["go"] or now >= nx["hard_due"]:
            return True
        return not nx["holds"] and now >= nx["due"]

    def take_next(self, now: float) -> dict | None:
        """The next game starts: its number, who plays first, who chose and how. A chooser who said nothing plays first ("default")."""
        nx = self.next
        if not nx or self.over:
            return None
        if not nx["choice"]:
            nx.update(choice=PLAY, how="default")
        first = nx["chooser"] if nx["choice"] == PLAY else self.other(nx["chooser"])
        out = {"n": nx["n"], "first": first, "chooser": nx["chooser"], "choice": nx["choice"], "how": nx["how"], "why": nx["why"]}
        self.next = None
        self.save()
        return out

    # ── what everyone may see ────────────────────────────────────────────────────────────────────
    def score(self) -> str:
        return score_line(self.seats, self.wins, self.over, self.winner)

    def public(self, now: float = 0.0) -> dict:
        nx = self.next
        out = {"on": True, "best_of": self.best_of, "need": self.need, "max_games": self.max_games, "seats": list(self.seats),
               "wins": dict(self.wins), "score": self.score(), "games_played": len(self.games),
               "game": (self.current or {}).get("n") or (nx or {}).get("n") or len(self.games) or 1,
               "games": [dict(g) for g in self.games], "current": dict(self.current) if self.current else None,
               "over": self.over, "winner": self.winner, "next": None}
        if nx:
            waiting = bool(nx["holds"]) and not nx["go"]
            left = (nx["hard_due"] if waiting else nx["due"]) - now
            out["next"] = {"n": nx["n"], "chooser": nx["chooser"], "why": nx["why"], "choice": nx["choice"], "how": nx["how"],
                           "plays_first": (nx["chooser"] if nx["choice"] == PLAY else self.other(nx["chooser"])) if nx["choice"] else None,
                           "default": PLAY, "holds": list(nx["holds"]), "seconds_left": round(max(0.0, 0.0 if nx["go"] else left), 1),
                           "countdown": self.countdown, "max_wait": self.max_wait}
        elif not self.over and self.current:
            out["next_first"] = "the loser of this game chooses (no roll)"
        return out

    # ── kept next to the table's saves ───────────────────────────────────────────────────────────
    def to_json(self) -> dict:
        return {"v": 1, "seats": self.seats, "best_of": self.best_of, "max_games": self.max_games, "countdown": self.countdown, "max_wait": self.max_wait,
                "wins": self.wins, "games": self.games, "current": self.current, "next": self.next, "over": self.over, "winner": self.winner}

    @classmethod
    def from_json(cls, d: dict, path=None) -> "Match":
        m = cls(d["seats"], best_of=d.get("best_of", 3), max_games=d.get("max_games"), countdown=d.get("countdown", 25.0),
                max_wait=d.get("max_wait", 300.0), path=path)
        m.wins = {s: int((d.get("wins") or {}).get(s, 0)) for s in m.seats}
        m.games = list(d.get("games") or [])
        m.current = d.get("current") or None
        m.next = d.get("next") or None
        m.over = bool(d.get("over"))
        m.winner = d.get("winner") if d.get("winner") in m.seats else None
        return m

    def save(self):
        """Whole file, written beside and renamed: a reader never sees half a match. A failure to save never stops a game."""
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                json.dump(self.to_json(), fh, indent=1)
            os.replace(tmp, self.path)
        except OSError as e:
            print(f"match save failed: {e}", flush=True)


def load(path) -> Match | None:
    try:
        return Match.from_json(json.loads(Path(path).read_text()), path=path)
    except (OSError, ValueError, KeyError, TypeError):
        return None
