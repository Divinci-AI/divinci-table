"""Open mic v2: when should an AI at the table speak?

Quiet by default. An AI seat answers when it is called (wake word: its name at the start of a line, or
"Hey Divinci"), answers when it is clearly addressed and an answer is expected, and only sometimes chimes
in on its own: a chattier personality more often, within a budget.

    tev1 JUDGES (one request per line, local through Ollama — transcripts stay on the table host):
      about_game      noul    is the line about the game?
      addressee       choice  which AI seat it is for (or the table, or nobody)
      expects_answer  noul    does the speaker expect a spoken reply from an AI seat?
      speak:<seat>    score   how welcome an unprompted line from <seat> would be (4 levels)
    CODE DECIDES (this file): thresholds, cooldowns and budgets per personality, never talking over
    someone, never the same AI twice in a row. Changing a threshold never needs a new model call.

Logged per line (decisions.jsonl in the research folder): the scores and the decision, never audio,
and the line's text only when it was about the game. People rate unprompted lines 👍/🙄, and the
thresholds are tuned from those ratings, not from intuition.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass, field

LEVELS = [
    "Not needed: people are talking among themselves, or about something unrelated; an AI line would interrupt.",
    "Small talk: a light, friendly remark would be fine but adds nothing to the game.",
    "Useful: a rules point, a reaction to something that just happened to this AI's own pieces, or a fact the table is missing.",
    "Needed: someone is waiting on this AI, or the table is stuck without its answer.",
]

# A personality's appetite for talking. min_level is the expected score (0–3) an unprompted line needs.
PRESETS = {
    "quiet":  {"min_level": 2.6, "cooldown_s": 240, "budget": 2, "window_s": 600},
    "normal": {"min_level": 2.2, "cooldown_s": 120, "budget": 3, "window_s": 600},
    "chatty": {"min_level": 1.6, "cooldown_s": 45,  "budget": 6, "window_s": 600},
}
ADDRESSED_P = 0.6          # addressee probability needed to count a line as "for" that seat
EXPECTS_P = 0.5
QUIET_GAP_S = 1.5          # don't start talking until people have been quiet this long


@dataclass
class Seat:
    name: str
    persona: str = ""
    chattiness: str = "normal"
    spoke_at: list[float] = field(default_factory=list)


@dataclass
class Decision:
    seat: str | None
    reason: str                     # wake | addressed | unprompted | none
    scores: dict
    about_game: float

    def asdict(self) -> dict:
        return {"seat": self.seat, "reason": self.reason, "about_game": round(self.about_game, 3),
                "scores": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in self.scores.items()}}


def wake_word(text: str, names: list[str]) -> str | None:
    """A seat's name at the start of the line ("Fusion, …", "Hey Claude …"), or "Hey Divinci" (→ the first
    AI seat). Code, not a model: it must never miss and never needs a network."""
    t = text.strip().lower()
    t = re.sub(r"^(hey|ok|okay|yo|hi)\s*,?\s+", "", t)
    for n in names:
        if re.match(rf"{re.escape(n.lower())}\b", t):
            return n
    if re.match(r"divinci\b", t) and names:
        return names[0]
    return None


class OpenMic:
    def __init__(self, seats: list[Seat], game: str, log_path: str | None = None, model: str | None = None,
                 url: str | None = None, judge=None):
        self.seats = {s.name: s for s in seats}
        self.game = game
        self.log_path = log_path
        self.model = model or os.environ.get("OPENMIC_MODEL", "tev1")
        self.url = (url or os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")).rstrip("/") + "/v1/systemone"
        self.judge_fn = judge or self.judge
        self.last_human_at = 0.0
        self.last_speaker: str | None = None     # the last AI that spoke, until a person speaks again

    # ── the model's part ──────────────────────────────────────────────────────────────────────
    def judge(self, text: str, recent: list[str], speaker: str | None) -> dict:
        names = list(self.seats)
        addr = {n: f"The AI player {n}." for n in names}
        addr["the whole table"] = "Everyone at the table, not one AI player in particular."
        addr["nobody"] = "A person, or nobody in particular (people talking among themselves)."
        state = {"game": self.game, "ai_players": [{"name": s.name, "personality": s.persona} for s in self.seats.values()],
                 "recent_lines": recent[-6:], "speaker": speaker or "someone at the table", "line": text}
        qs = {"about_game": {"type": "noul", "instructions": "Is the `line` about the game being played (moves, rules, the "
                                                             "state of the game, deals between players)?"},
              "addressee": {"type": "choice", "criteria": addr,
                            "instructions": "Who is the `line` addressed to? A name at the start usually marks who is spoken to."},
              "expects_answer": {"type": "noul", "instructions": "Does the speaker of the `line` expect a spoken reply from one "
                                                                 "of the AI players in `ai_players`?"}}
        for n in names:
            qs[f"speak:{n}"] = {"type": "score", "criteria": LEVELS,
                                "instructions": f"Suppose the AI player {n} (see its `personality`) says something right after "
                                                f"the `line`, without being asked. How welcome would that be at this table?"}
        body = json.dumps({"model": self.model, "keep_alive": "30m", "state": json.dumps(state), "questions": qs}).encode()
        req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
        a = json.loads(urllib.request.urlopen(req, timeout=60).read())["answers"]
        out = {"about_game": a["about_game"]["noul"], "addressee": a["addressee"]["choice"],
               "addressee_p": a["addressee"]["probabilities"].get(a["addressee"]["choice"], 0.0),
               "expects_answer": a["expects_answer"]["noul"]}
        for n in names:
            out[f"speak:{n}"] = a[f"speak:{n}"]["score"]
        return out

    # ── code's part ───────────────────────────────────────────────────────────────────────────
    def heard(self, text: str, speaker: str | None, recent: list[str], now: float | None = None) -> Decision:
        """A person said (or typed) `text`. Decide whether an AI seat speaks, and which."""
        now = now or time.time()
        self.last_human_at = now
        self.last_speaker = None                 # a person spoke: any AI may answer again
        names = list(self.seats)
        w = wake_word(text, names)
        scores = {}
        if w:
            d = Decision(w, "wake", {"wake": w}, 1.0)
        else:
            try:
                scores = self.judge_fn(text, recent, speaker)
            except Exception as e:               # noqa: BLE001 — a judge outage means silence, never a crash
                scores = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
            d = self._decide(scores, now)
        self._log(text, speaker, d)
        if d.seat:
            self.seats[d.seat].spoke_at.append(now)
            self.last_speaker = d.seat
        return d

    def _decide(self, s: dict, now: float) -> Decision:
        about = float(s.get("about_game", 0.0))
        if "error" in s:
            return Decision(None, "none", s, about)
        who = s.get("addressee")
        if who in self.seats and s.get("addressee_p", 0) >= ADDRESSED_P and s.get("expects_answer", 0) >= EXPECTS_P:
            return Decision(who, "addressed", s, about)
        best = None
        for n, seat in self.seats.items():
            p = PRESETS.get(seat.chattiness, PRESETS["normal"])
            lvl = float(s.get(f"speak:{n}", 0.0))
            if lvl < p["min_level"] or n == self.last_speaker:
                continue
            recent = [t for t in seat.spoke_at if now - t < p["window_s"]]
            if len(recent) >= p["budget"] or (recent and now - recent[-1] < p["cooldown_s"]):
                continue
            if best is None or lvl > best[1]:
                best = (n, lvl)
        return Decision(best[0], "unprompted", s, about) if best else Decision(None, "none", s, about)

    def link(self, event_id: int, d: Decision) -> None:
        """The table emitted the AI line for decision `d` as event `event_id`: record the pair, so a later
        👍/🙄 on that line can be matched to the scores that produced it (calibrate())."""
        if self.log_path and d.seat:
            with open(self.log_path, "a") as fh:
                fh.write(json.dumps({"ts": round(time.time(), 2), "event": int(event_id), **d.asdict()}) + "\n")

    def may_speak_now(self, now: float | None = None) -> bool:
        """Never talk over someone: wait until people have been quiet for a moment."""
        return (now or time.time()) - self.last_human_at >= QUIET_GAP_S

    def ai_spoke(self, seat: str) -> None:
        self.last_speaker = seat

    def _log(self, text: str, speaker: str | None, d: Decision) -> None:
        if not self.log_path:
            return
        rec = {"ts": round(time.time(), 2), "speaker": speaker, **d.asdict()}
        if d.about_game >= 0.5 or d.reason in ("wake", "addressed"):
            rec["line"] = text[:300]              # game talk is research data; side conversation is not kept
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        with open(self.log_path, "a") as fh:
            fh.write(json.dumps(rec) + "\n")


def rate(log_dir: str, event_id: int, rating: str, by: str) -> dict:
    """👍 ('up') or 🙄 ('roll') on an unprompted AI line. Appended to ratings.jsonl; thresholds come from these."""
    if rating not in ("up", "roll"):
        return {"error": "rating is 'up' or 'roll'"}
    os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, "ratings.jsonl"), "a") as fh:
        fh.write(json.dumps({"ts": round(time.time(), 2), "event": int(event_id), "rating": rating, "by": by[:30]}) + "\n")
    return {"ok": True}


def calibrate(decisions_path: str, ratings_path: str) -> dict:
    """From ratings: for unprompted lines, the share of 👍 at each score band — the evidence for moving
    min_level. Returns {band: {"up": n, "roll": n}}."""
    try:
        decs = [json.loads(l) for l in open(decisions_path)]
        rats = [json.loads(l) for l in open(ratings_path)]
    except FileNotFoundError:
        return {}
    by_event = {r["event"]: r["rating"] for r in rats}
    bands: dict = {}
    for d in decs:
        if d.get("reason") != "unprompted" or d.get("event") not in by_event:
            continue
        lvl = d["scores"].get(f"speak:{d['seat']}", 0)
        band = f"{int(lvl * 2) / 2:.1f}"
        bands.setdefault(band, {"up": 0, "roll": 0})[by_event[d["event"]]] += 1
    return bands
