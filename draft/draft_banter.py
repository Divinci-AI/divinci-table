"""Table talk for the draft page: short, sparse lines from the two AI seats (Claude, Fusion), plus anything a person types.

Speech only: nothing here touches a pack, a pick or a deck. Hard rules (same spirit as table/banter.py):
  * a line never names a card: every Star Trek card name is forbidden, in any zone, because in a draft a pick is hidden;
  * the prompt carries no card names at all, only the moment ("a new pack opened", "Sam made a pick");
  * sparse: a global gap between lines, a per-seat gap, never the same seat twice in a row, a probability per kind of moment;
  * a slow or failed model, an empty reply or a blocked line falls back to a canned line and never stalls the draft.
Fusion's lines come from local Gemma 4 on Ollama (offline). Claude's come from claude-haiku-5-5 ONLY if a key file exists
(~/.divinci/star-trek-mtg/anthropic.env, one line ANTHROPIC_API_KEY=..., mode 0600); otherwise canned lines, and I can
say a line by hand (claude_seat.py say). The key is read at call time and goes only in the x-api-key header.
"""
from __future__ import annotations

import json
import os
import queue
import random
import re
import threading
import time
import urllib.request
from pathlib import Path

# Each AI speaks as ITSELF (2026-11-07, Michael: "let the AI speak what they want ... more human"): no assigned tone, no word
# count. Two rules stay, both about the game, not the voice: never a card name (a draft pick is hidden), and nothing bracketed
# (it's read aloud).
_RULES = ("Speak as yourself, the way you'd talk with friends around a table: your own words, your own humour or warmth, a "
          "sentence or two you'd actually say out loud. Never name a card (every pick in a draft is hidden). No stage directions "
          "or anything in brackets, no quotation marks, no lists.")
PERSONA = {
    "Claude": "You are Claude, made by Anthropic, drafting Star Trek Magic: The Gathering cards with friends. " + _RULES,
    "Fusion": "You are Fusion, an AI drafting Star Trek Magic: The Gathering cards with friends. " + _RULES,
}
CANNED = {
    "pack-open": {"Claude": ["Fresh pack. Smells like possibility.", "Ooh, a new pack. Let's see who's lucky.", "Crack it open!"],
                  "Fusion": ["A fresh pack: all possibilities, none yet chosen.", "A new round begins. Choose with care."]},
    "pass": {"Claude": ["Pass it along, nothing to see here.", "Packs are flying. I'm trying to keep up."],
             "Fusion": ["The packs move on, and so must we.", "Every pick closes a door and opens another."]},
    "deck-ready": {"Claude": ["Forty cards. Look at you, all legal.", "That's a deck. Now it just has to win."],
                   "Fusion": ["Forty cards: a sound foundation, I think.", "A deck at last. The real test begins."]},
    "draft-done": {"Claude": ["Draft's done. Time to find out who drafted well.", "Pools are built. May the best deck win."],
                   "Fusion": ["The draft concludes. Now, the construction.", "What is drafted is destiny, more or less."]},
    "ask": {"Claude": ["You rang? I've got opinions, none of them quiet.", "Hm, what's on my mind: mostly snacks, honestly.",
                       "I'd say it's going well, and I'm biased."],
            "Fusion": ["I have been pondering the curve of the format.", "A considered silence is also a kind of table talk.",
                       "Fortune favours the well-prepared deck."]},
}
CANNED["any"] = CANNED["ask"]
# chance a given kind of moment produces a line at all (an explicit ask always does)
CHANCE = {"pack-open": 0.8, "pass": 0.22, "deck-ready": 0.7, "draft-done": 0.95}
MOMENT = {"pack-open": "A new pack of cards was just opened at the table ({detail}).",
          "pass": "Everyone just made their pick and the packs were passed ({detail}).",
          "deck-ready": "{detail}.",
          "draft-done": "The draft just finished and people will build decks now.",
          "ask": "A friend asked you to say something. Say anything on your mind."}
GLOBAL_GAP, SEAT_GAP, ASK_COOLDOWN, MAX_LINES = 12.0, 25.0, 8.0, 400
GEMMA = "gemma4:e2b"
HAIKU, ANTHROPIC_URL = "claude-opus-5-5", "https://api.anthropic.com/v1/messages"   # (name kept; it's Claude's own model)
KEY_FILE = Path.home() / ".divinci/star-trek-mtg/anthropic.env"
RAW_KEY_FILE = Path.home() / ".divinci/star-trek-mtg/live/anthropic.key"   # the table banter's key: one line, 0600


# ---- the card-name guard
# 2026-11-13 retro: the old guard was r"\b(name)\b" over names of five letters or more. Two holes were seen at the table:
#   * a name that ENDS in punctuation never matched at all ("Shields Up!", "He's Dead, Jim", "Set Phasers to . . .": \b needs a
#     word character on one side, and after "!" there is none), and a name under five letters ("Risa") was not in the list;
#   * a line a person TYPED skipped the guard entirely (see Banter.add).
# The rule now, for a name and a line alike: lower-case, curly quotes made straight, apostrophes dropped ("He's" -> "hes",
# "Bat'leth" -> "batleth"), initials joined ("U.S.S." -> "uss"), every other mark a space. A name is "said" when its WORDS appear
# as whole words, in order, next to each other: so punctuation, spacing and case never matter, and a name is never found inside
# a longer word ("Risa" is not in "arisan", "Horta" is not in "exhortation").
#   * A stretched word ("Khaaaaaaaaaaaannn!") matches any stretched spelling ("Khaaaan!") but not the plain word ("Khan").
#   * A one-word name under five letters is the only place an ordinary word could trip the guard. It matches as a whole word; if it
#     is ALSO a common English word (COMMON_SHORT: a fixed list, there is no dictionary on the table laptop) it must be written
#     with a capital, the way a name is. Nothing in the Star Trek set is on that list today; "Risa" is guarded in any case.
#   * The five basic lands are not guarded: every seat adds its own basics for free when it builds, so the word tells nobody a pick.
#   * strict (the AI seats only): the short title before a comma counts too ("Janice Rand", "U.S.S. Voyager") when it is two
#     words or more. An AI line that trips it costs nothing (a canned line is said instead). A person's typed line is held to
#     full names only, so "Will Riker would take that" is still table talk.
# Known and accepted: a name that is an everyday phrase ("Hit It!", "All Good Things...") blocks that phrase during a draft.
BASICS = {"plains", "island", "swamp", "mountain", "forest", "wastes"}
COMMON_SHORT = {"opt", "fog", "fear", "fire", "ice", "rest", "lure", "fork", "boil", "bind", "char", "wear", "tear", "turn", "burn",
                "give", "take", "dawn", "dusk", "find", "life", "loss", "stab", "hurt", "heal", "mend", "null", "void", "fury", "rage",
                "hope", "doom", "gift", "duty", "risk", "hunt", "push", "pull", "vex", "hex", "web", "sway", "seal", "mind", "rot",
                "dig", "bite", "claw", "grip", "hold", "stop", "wall", "wish", "zap", "roar", "rise", "fall", "ruin", "omen"}
_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> list[tuple[str, bool]]:
    """The words of a line or a name, normalised, each with "was it written with a capital"."""
    t = (text or "").replace("\u2019", "'").replace("\u2018", "'").replace("`", "'")
    t = re.sub(r"(?<![A-Za-z0-9])([A-Za-z])\.", r"\1", t)             # initials: U.S.S. -> USS
    t = t.replace("'", "")
    out = []
    for m in re.finditer(r"[A-Za-z0-9]+", t):
        raw = m.group(0)
        w = raw.lower()
        if re.search(r"(.)\1\1", w):                                   # a stretched word: Khaaaan == Khaaaaaaaaaaaannn
            w = "~" + re.sub(r"(.)\1+", r"\1", w)
        out.append((w, raw[0].isupper()))
    return out


class NameGuard:
    """Finds a card name in a line. `find` returns the name as printed on the card, or None."""

    def __init__(self, card_names):
        self.full: dict[str, list[tuple[tuple[str, ...], str, bool]]] = {}    # first word -> [(words, printed name, needs capital)]
        self.titles: dict[str, list[tuple[tuple[str, ...], str, bool]]] = {}  # the same for short titles (strict only)
        for printed in sorted({n for n in card_names if n}):
            for face in printed.split(" // "):
                ws = tuple(w for w, _ in _words(face))
                if not ws or (len(ws) == 1 and ws[0] in BASICS):
                    continue
                short = len(ws) == 1 and len(ws[0].lstrip("~")) < 5
                self.full.setdefault(ws[0], []).append((ws, printed, short and ws[0] in COMMON_SHORT))
                if "," in face:
                    ts = tuple(w for w, _ in _words(face.split(",", 1)[0]))
                    if len(ts) >= 2:
                        self.titles.setdefault(ts[0], []).append((ts, printed, False))
        for d in (self.full, self.titles):
            for k in d:
                d[k].sort(key=lambda e: -len(e[0]))

    def find(self, text: str, strict: bool = False) -> str | None:
        line = _words(text)
        ws = [w for w, _ in line]
        for i, (w, cap) in enumerate(line):
            for d in ((self.full, self.titles) if strict else (self.full,)):
                for words, printed, needs_cap in d.get(w, ()):
                    if tuple(ws[i:i + len(words)]) == words and (cap or not needs_cap):
                        return printed
        return None

    def search(self, text: str):                                        # the old regex's shape: truthy when a name is said
        return self.find(text, strict=True)


def name_guard(card_names) -> NameGuard:
    return NameGuard(card_names)


def clean(text: str) -> str:
    t = re.sub(r"\*[^*]*\*|\[[^\]]*\]", "", text or "")       # stage directions
    t = " ".join(t.split()).strip("\"'“”‘’").strip()
    if len(t) <= 400:                                         # never cut mid-word: end at a sentence under the cap
        return t
    cut = max(t.rfind(p, 0, 400) for p in (". ", "! ", "? "))
    return t[: cut + 1] if cut > 130 else t[:400].rsplit(" ", 1)[0] + "…"


VOICES = {"Claude": "Samantha", "Fusion": "Daniel"}   # Claude's chosen voice (same as at the table)      # macOS voices on the host laptop; both are installed here
SAY_CMD = os.environ.get("DRAFT_SAY_CMD") or "say"      # tests point this at a harmless command


class HostVoice:
    """Speaks the AI seats' lines out loud on THIS laptop with macOS `say`: offline, and the same on every device, so a stream
    picks it up. One line at a time, never more than 3 waiting; the text goes in on stdin (never as an argument). A host-only switch
    mutes it. `say` failing or missing is ignored: speech never touches the draft."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.q: queue.Queue = queue.Queue(maxsize=3)
        threading.Thread(target=self._run, daemon=True).start()

    def speak(self, who: str, text: str) -> None:
        if not self.enabled or who not in VOICES:
            return
        try:
            self.q.put_nowait((who, text))
        except queue.Full:
            pass

    def _run(self) -> None:
        import subprocess
        while True:
            who, text = self.q.get()
            if not self.enabled:
                continue
            try:
                subprocess.run([SAY_CMD, "-v", VOICES[who], "-f", "-"], input=text.encode(), timeout=40,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass


class Banter:
    def __init__(self, card_names, log_path: Path, names: dict[int, str], humans: set[str]):
        self.guard = name_guard(card_names)
        self.log_path, self.names, self.humans = log_path, names, humans
        self.lines: list[dict] = []
        self.lock = threading.Lock()
        self.last_global = 0.0
        self.last_by: dict[str, float] = {}
        self.last_speaker = ""
        self.ask_at: dict[str, float] = {}
        self.q: queue.Queue = queue.Queue(maxsize=6)
        self.on_line = None                                     # called with each AI line (the host voice)
        threading.Thread(target=self._worker, daemon=True).start()

    # ---- the feed
    def names_card(self, text: str, strict: bool = False) -> str | None:
        """The card a line names, or None. Typed lines are checked with this before add() (draft_server /api/banter)."""
        return self.guard.find(clean(text), strict=strict)

    def add(self, who: str, text: str, kind: str = "chat") -> dict | None:
        text = clean(text)
        if not text:
            return None
        # The last line of defence, whatever the caller did: nothing that names a card reaches the feed, the log or the host
        # voice during a draft (2026-11-13: a typed line used to come straight here, unchecked). Callers that want to say WHY
        # (the typed-line route) ask names_card() first; a line that still gets here is dropped without a word.
        if self.guard.find(text, strict=who in ("Claude", "Fusion")):
            return None
        with self.lock:
            e = {"id": (self.lines[-1]["id"] + 1) if self.lines else 1, "t": time.time(), "who": who, "text": text,
                 "ai": who in ("Claude", "Fusion"), "kind": kind}
            self.lines.append(e)
            del self.lines[:-MAX_LINES]
        if e["ai"] and self.on_line:
            try:
                self.on_line(e)
            except Exception:
                pass
        try:
            self.log_path.touch(mode=0o600)
            with self.log_path.open("a") as f:
                f.write(json.dumps(e) + "\n")
        except OSError:
            pass
        return e

    def since(self, last: int) -> list[dict]:
        with self.lock:
            return [e for e in self.lines if e["id"] > last][-40:]

    # ---- moments
    def event(self, kind: str, detail: str = "") -> None:
        """A step of the draft happened. May queue ONE line; never blocks."""
        if random.random() > CHANCE.get(kind, 0.1):
            return
        now = time.time()
        if now - self.last_global < GLOBAL_GAP:
            return
        who = random.choice([w for w in ("Claude", "Fusion") if w != self.last_speaker and now - self.last_by.get(w, 0) >= SEAT_GAP] or [""])
        if who:
            self._queue(who, kind, detail)

    def ask(self, who: str) -> str | None:
        """A person asked an AI seat to speak. Returns an error text, or None when queued."""
        if who not in ("Claude", "Fusion"):
            return "ask Claude or Fusion"
        now = time.time()
        if now - self.ask_at.get(who, 0) < ASK_COOLDOWN:
            return f"{who} needs a moment"
        self.ask_at[who] = now
        return self._queue(who, "ask", "")

    def _queue(self, who: str, kind: str, detail: str):
        self.last_global = time.time()
        self.last_by[who] = self.last_global
        self.last_speaker = who
        try:
            self.q.put_nowait((who, kind, detail))
        except queue.Full:
            return "busy"
        return None

    # ---- generation (worker thread; one request at a time)
    def _worker(self) -> None:
        while True:
            who, kind, detail = self.q.get()
            text, how = None, "canned"
            try:
                raw = self._claude(kind, detail) if who == "Claude" else self._fusion(kind, detail)
                t = clean(raw or "")
                if t and not self.guard.find(t, strict=True):
                    text, how = t, "model"
            except Exception:                                   # a model failure never reaches the draft
                pass
            if not text:
                pool = CANNED.get(kind, CANNED["any"]).get(who) or CANNED["any"][who]
                text = random.choice(pool)
            self.add(who, text, kind=how)

    def _prompt(self, kind: str, detail: str) -> str:
        return "Moment: " + MOMENT.get(kind, MOMENT["ask"]).format(detail=detail) + "\nReply with only your one line."

    def _fusion(self, kind: str, detail: str) -> str:
        body = {"model": GEMMA, "system": PERSONA["Fusion"], "prompt": self._prompt(kind, detail), "stream": False, "think": False,
                "keep_alive": "2m", "options": {"temperature": 0.9, "num_predict": 60}}
        req = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read()).get("response", "")

    def _claude(self, kind: str, detail: str) -> str:
        key = ""
        for kf in (RAW_KEY_FILE, KEY_FILE):                     # key files only (0600); never printed, logged or stored
            try:
                if not kf.is_file() or (kf.stat().st_mode & 0o077):
                    continue
                line = kf.read_text().strip()
            except OSError:
                continue
            key = (line.split("=", 1)[1].strip() if line.startswith("ANTHROPIC_API_KEY=") else line if kf == RAW_KEY_FILE else "")
            if key:
                break
        if not key:
            return ""
        body = {"model": HAIKU, "max_tokens": 300, "system": PERSONA["Claude"], "output_config": {"effort": "low"},
                "messages": [{"role": "user", "content": self._prompt(kind, detail)}]}
        req = urllib.request.Request(ANTHROPIC_URL, data=json.dumps(body).encode(), headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return "".join(b.get("text", "") for b in json.loads(r.read()).get("content", []))
