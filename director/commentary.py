"""Two AI commentators ("play-by-play" and "colour") who take turns and react to each other. Text first (captions on the director page).

Grounding: a commentator sees the feed (feed.py) and nothing else. What it writes is checked before it is shown (check_line): a card name
it marks must be a card the stream can see in this game (a near-miss is repaired, anything else drops the line), a set card that is NOT in
this game drops the line, a long run of a card's rules text drops the line, and it is cut to length. A line that arrives after the game
has moved on is dropped (stale). Every line is labelled as AI on screen.

Cost (cloud backend): the static system prompt and the card glossary are one cached prefix; a per-hour and a per-stream dollar budget and a
per-hour token budget are checked BEFORE each call from the usage the API reported; over budget shows "commentary paused (budget)"; errors
back off exponentially; a kill switch (a file, or the control page) stops calls at once."""
from __future__ import annotations

import difflib
import json
import re
import time
from pathlib import Path

from . import feed as feedmod

# $ per million tokens: (input, output, cache read). Cache writes are 1.25x input (5-minute cache). From the Anthropic price list as cached
# in the claude-api reference on 2026-11-03; put current prices in the config ("prices") before trusting the dollar budget.
PRICES = {"claude-opus-5-5": (4.00, 20.00, 0.20), "claude-sonnet-5-5": (2.00, 10.00, 0.10), "claude-haiku-5-5": (0.10, 0.50, 0.01)}
MAX_CHARS = 220

RULES = """You are one of two AI commentators on a live stream of a card game played at a home-built table: a Magic: The Gathering-style \
game with a Star Trek card set. Players are people and AIs. Viewers see the board, and sometimes a player's hand, on screen while you talk.

How to commentate:
- One or two short sentences, at most 200 characters, that sound natural read aloud. No lists, no emoji, no stage directions, no hashtags.
- Talk only about what the FEED below tells you happened. The feed is the only source of truth: never guess a card in a hand you were not \
shown, never invent a play, a card, a number or a rule, and never predict hidden information as if you knew it.
- When you name a card, write its exact name inside double square brackets, like [[Tricorder]]. Only cards named in the feed's \
"cards you may name" list exist in this game for you. If you are not sure of a name, describe the card instead ("that big ship").
- Never read a card's rules text out. Say what it means for the game in your own few words.
- You have a partner. Do not repeat what they just said: add to it, disagree, joke, or move on to the other table.
- You are an AI and viewers are told so. Do not pretend to be a person, and do not claim to see anything that is not in the feed.
- Be kind about the players. A misplay is "a bold line", not an insult. People at the table may be listening.
- If nothing in the feed is worth a line, answer exactly NOTHING.
"""

ROLES = {
    "play-by-play": "You are the PLAY-BY-PLAY voice: say what just happened and where the game stands, clearly and quickly, like a sports caller.",
    "colour": "You are the COLOUR voice: react to your partner and add what it means: the plan, the risk, the tension, a dry joke.",
}


def glossary(cards_json: str | None) -> tuple[str, set, set]:
    """(the glossary block, every card name in the set, 8-word shingles of every rules text). Names and type lines only: no rules text
    is ever given to a commentator. Without a card file: no glossary; the per-call "cards you may name" list still applies."""
    if not cards_json or not Path(cards_json).expanduser().is_file():
        return "", set(), set()
    cards = json.loads(Path(cards_json).expanduser().read_text())
    seen, lines, shingles = set(), [], set()
    for c in sorted(cards, key=lambda c: c.get("name") or ""):
        n = c.get("name")
        if not n or n in seen:
            continue
        seen.add(n)
        lines.append(f"{n} | {c.get('type_line') or ''}")
        w = re.findall(r"[a-z']+", (c.get("oracle_text") or "").lower())
        shingles.update(" ".join(w[i:i + 8]) for i in range(max(0, len(w) - 7)))
    return ("CARD GLOSSARY (every card in this set: name | type). A card is in THIS game only if the feed says so.\n" + "\n".join(lines)), seen, shingles


def check_line(raw: str, allowed: set, feed_text: str = "", universe: set = frozenset(), shingles: set = frozenset()) -> tuple[str | None, str]:
    """(the line to show, or None; why). Pure."""
    t = re.sub(r"\*[^*]*\*|\([^)]*\)", "", raw or "").replace("\n", " ").strip().strip('"“”').strip()
    t = re.sub(r"\s{2,}", " ", t)
    if not t or t.upper().startswith("NOTHING"):
        return None, "chose silence"
    low_allowed = {a.lower(): a for a in allowed}
    ft = feed_text.lower()
    bad = []

    def fix(m):
        name = m.group(1).strip()
        if name.lower() in low_allowed:
            return low_allowed[name.lower()]
        if len(name) >= 4 and name.lower() in ft:
            return name
        near = difflib.get_close_matches(name.lower(), list(low_allowed), n=1, cutoff=0.82)
        if near:
            return low_allowed[near[0]]
        bad.append(name)
        return name
    t = re.sub(r"\[\[([^\]]{1,80})\]\]", fix, t)
    if bad:
        return None, f"named a card that is not in this game: {bad[0][:40]}"
    if "[[" in t or "]]" in t:
        return None, "broken card mark-up"
    tl = t.lower()
    for n in universe:                      # an unmarked mention of a real card that the stream cannot see in this game
        nl = n.lower()
        if nl in low_allowed or nl in ft or (" " not in nl and len(nl) < 8):
            continue
        if re.search(r"(?<![a-z])" + re.escape(nl) + r"(?![a-z])", tl):
            return None, f"named a card that is not in this game: {n[:40]}"
    w = re.findall(r"[a-z']+", tl)
    if shingles and any(" ".join(w[i:i + 8]) in shingles for i in range(max(0, len(w) - 7))):
        return None, "read rules text aloud"
    if len(t) > MAX_CHARS:
        cut = max(t.rfind(p, 0, MAX_CHARS) for p in (". ", "! ", "? "))
        t = t[:cut + 1] if cut > MAX_CHARS // 3 else t[:MAX_CHARS].rsplit(" ", 1)[0] + "…"
    return t, "ok"


class Budget:
    """Dollars and tokens, from what the backend reported. All three limits are hard: reaching one pauses commentary."""
    def __init__(self, usd_per_hour=1.0, usd_per_stream=4.0, tokens_per_hour=1_500_000, prices=None):
        self.usd_per_hour, self.usd_per_stream, self.tokens_per_hour = usd_per_hour, usd_per_stream, tokens_per_hour
        self.prices = {**PRICES, **{k: tuple(v) for k, v in (prices or {}).items()}}
        self.spent: list = []        # (ts, usd, tokens)
        self.total_usd = 0.0
        self.calls = 0

    def cost(self, model: str, u: dict) -> float:
        p = self.prices.get(model)
        if p is None:
            p = max(self.prices.values()) if self.prices else (0, 0, 0)     # an unknown model is priced as the dearest one known: never as free
        if model.startswith("ollama:"):
            return 0.0
        return (u.get("input", 0) * p[0] + u.get("cache_write", 0) * p[0] * 1.25 + u.get("cache_read", 0) * p[2] + u.get("output", 0) * p[1]) / 1e6

    def record(self, now: float, model: str, u: dict):
        usd = self.cost(model, u)
        self.spent.append((now, usd, sum(int(u.get(k, 0)) for k in ("input", "cache_write", "cache_read", "output"))))
        self.total_usd += usd
        self.calls += 1

    def hour(self, now: float) -> tuple[float, int]:
        self.spent = [x for x in self.spent if now - x[0] < 3600]
        return sum(x[1] for x in self.spent), sum(x[2] for x in self.spent)

    def blocked(self, now: float) -> str | None:
        usd, tok = self.hour(now)
        if self.total_usd >= self.usd_per_stream:
            return f"this stream's budget is spent (${self.total_usd:.2f} of ${self.usd_per_stream:.2f})"
        if usd >= self.usd_per_hour:
            return f"this hour's budget is spent (${usd:.2f} of ${self.usd_per_hour:.2f})"
        if tok >= self.tokens_per_hour:
            return f"this hour's token budget is spent ({tok} of {self.tokens_per_hour})"
        return None


class BackendError(Exception):
    def __init__(self, what: str, retry_after: float | None = None, fatal: bool = False):
        super().__init__(what)
        self.retry_after, self.fatal = retry_after, fatal


class Commentary:
    def __init__(self, backend, voices: list, watches: list, budget: Budget | None = None, policy="auto", min_gap=14.0, idle_gap=120.0,
                 stale_secs=30.0, stale_events=8, caption_secs=10.0, cards_json=None, kill_file=None, clock=time.time):
        self.backend, self.voices, self.watches, self.clock = backend, voices, watches, clock
        self.budget = budget or Budget()
        self.policy, self.min_gap, self.idle_gap, self.stale_secs, self.stale_events, self.caption_secs = policy, min_gap, idle_gap, stale_secs, stale_events, caption_secs
        self.gloss, self.universe, self.shingles = glossary(cards_json)
        self.kill_file = Path(kill_file).expanduser() if kill_file else None
        self.killed = False
        self.turn = 0
        self.cursor: dict = {}
        self.lines: list = []          # shown captions
        self.dropped: list = []        # (why, raw[:80]) for the control page and the tests
        self.fails = 0
        self.backoff_until = 0.0
        self.last_at = -1e9
        self.state = "idle"
        self.n = 0

    def status(self, now=None) -> dict:
        now = self.clock() if now is None else now
        usd, tok = self.budget.hour(now)
        return {"state": self.state, "usd_hour": round(usd, 4), "usd_stream": round(self.budget.total_usd, 4), "tokens_hour": tok,
                "calls": self.budget.calls, "limits": {"usd_per_hour": self.budget.usd_per_hour, "usd_per_stream": self.budget.usd_per_stream,
                                                       "tokens_per_hour": self.budget.tokens_per_hour},
                "dropped": len(self.dropped), "last_drop": self.dropped[-1][0] if self.dropped else None,
                "voices": [{"name": v["name"], "role": v["role"], "model": v["model"]} for v in self.voices]}

    def caption(self, now=None) -> dict | None:
        now = self.clock() if now is None else now
        if self.lines and now - self.lines[-1]["at"] <= self.caption_secs:
            return self.lines[-1]
        return None

    def prompt(self, voice: dict, fd: dict) -> str:
        by_table: dict = {}
        for f in fd["facts"]:
            by_table.setdefault(f["table"], []).append(("  now: " if f.get("state") else "  - ") + f["text"])
        feed_txt = "\n".join(f"{t}:\n" + "\n".join(ls) for t, ls in by_table.items())
        said = "\n".join(f"  {l['voice']}: {l['text']}" for l in self.lines[-4:]) or "  (nothing yet)"
        return (f"{ROLES[voice['role']]} Your name on screen is {voice['name']}.\n\nFEED (the only facts you have):\n{feed_txt}\n\n"
                f"Cards you may name: {', '.join(sorted(fd['names'])) or '(none yet)'}\n\n"
                f"What has been said on air (oldest first):\n{said}\n\nYour next line (or NOTHING):")

    def step(self) -> str:
        """One tick. Returns what happened (for the log and the tests)."""
        now = self.clock()
        if self.killed or (self.kill_file and self.kill_file.exists()):
            self.state = "off (kill switch)"
            return self.state
        why = self.budget.blocked(now)
        if why:
            self.state = "paused (budget)"
            return f"{self.state}: {why}"
        if now < self.backoff_until:
            self.state = "waiting (backing off after an error)"
            return self.state
        self.state = "on"
        if now - self.last_at < self.min_gap:
            return "gap"
        fd = feedmod.build(self.watches, self.policy, self.cursor)
        if not fd["fresh"] and now - self.last_at < self.idle_gap:
            return "nothing new"
        voice = self.voices[self.turn % len(self.voices)]
        user = self.prompt(voice, fd)
        try:
            res = self.backend.generate(voice, [RULES, self.gloss] if self.gloss else [RULES], user)
        except BackendError as e:
            self.fails += 1
            wait = e.retry_after if e.retry_after else min(300.0, 5.0 * 2 ** (self.fails - 1))
            self.backoff_until = self.clock() + wait
            if e.fatal:
                self.killed = True
            self.state = "waiting (backing off after an error)"
            return f"error: {e} (next try in {wait:g}s)"
        self.fails = 0
        after = self.clock()
        self.budget.record(after, res.get("model") or voice["model"], res.get("usage") or {})
        self.cursor = fd["cursor"]
        self.last_at = after
        self.turn += 1
        moved = sum(len([e for e in w.shown_events() if e["id"] > fd["cursor"].get(w.t.name, 0)]) for w in self.watches)
        if after - now > self.stale_secs or moved >= self.stale_events:
            self.dropped.append(("stale", (res.get("text") or "")[:80]))
            return "dropped: stale"
        feed_text = "\n".join(f["text"] for f in fd["facts"])
        line, verdict = check_line(res.get("text") or "", fd["names"], feed_text, self.universe, self.shingles)
        if line is None:
            self.dropped.append((verdict, (res.get("text") or "")[:80]))
            return f"dropped: {verdict}"
        self.n += 1
        self.lines.append({"id": self.n, "voice": voice["name"], "role": voice["role"], "model": res.get("model") or voice["model"], "text": line,
                           "at": after, "hand_informed": fd["hand_informed"], "ai": True})
        del self.lines[:-30]
        return "line"
