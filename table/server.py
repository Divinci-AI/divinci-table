"""Table server, first cut: the scan pad for the AI's hidden hand, using this Mac's camera.

  ~/.venvs/table/bin/python table/server.py --deck decks/example.txt
  open http://localhost:8800            # localhost is a secure context, so the camera works

A drawn card is held FACE toward the laptop camera: the person scanning sees only its back, the
server reads the name line, picks the card among those still in the AI's library, and puts it in
the next open numbered slot (the sticky notes on the table).

What anyone at the table can see (the page, this terminal) is slot NUMBERS only. Card names are
revealed only when a slot is played. The AI's brain reads the hand from GET /api/hand with the
token written to table/.brain-token (0600); the page never has it.
"""
from __future__ import annotations

import argparse
import re
import json
import os
import secrets
import sys
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from match import find_cards_in_text, identify, identify_any, near_card_candidates, parse_decklist

HERE = Path(__file__).parent
ap = argparse.ArgumentParser()
ap.add_argument("--deck", help="decklist text file (Moxfield/Archidekt export)")
ap.add_argument("--any-card", action="store_true",
                help="TEST MODE: accept any Magic card (Scryfall name catalog), unlimited copies")
ap.add_argument("--port", type=int, default=8800)
ap.add_argument("--ai", action="append", default=[],
                help='an AI player, "Name|Commander card name|macOS voice" (repeatable), '
                     'e.g. "Talrand|Talrand, Sky Summoner|Daniel"')
ap.add_argument("--ai-deck", help="a virtual deck (decks/*.json) for the FIRST --ai player: it draws, "
                                   "plays and announces its own cards instead of using physical ones")
ap.add_argument("--brain", choices=["gemma", "external"], default="gemma",
                help="who decides for the virtual AI player: the local Gemma, or an EXTERNAL brain driving it "
                     "through /api/brain (tablectl) — then the server never answers or plays for it")
ap.add_argument("--human", action="append", default=[],
                help='a human player, "Name|Commander card name" (repeatable); their names go into the speech hint')
ap.add_argument("--confirm-frames", type=int, default=2, help="same card on N frames in a row")
ap.add_argument("--clear-secs", type=float, default=1.0, help="no card in view this long before the next scan")
ap.add_argument("--token-file", default=None,
                help="where to write the brain token (default table/.brain-token); a second server, e.g. a "
                     "test run, needs its own so it doesn't lock the live game's brain out")
args = ap.parse_args()

if sys.platform != "darwin":
    sys.exit("ocr_mac needs macOS; swap in another read_lines backend on Linux")
from ocr_mac import read_lines  # noqa: E402

if not args.deck and not args.any_card:
    sys.exit("pass --deck FILE, or --any-card to test with any Magic card")


def load_catalog():
    """Scryfall's list of every card name, cached. One request, well inside their rate limits."""
    import urllib.request
    cache = HERE / ".cache" / "card-names.json"
    if not cache.exists():
        cache.parent.mkdir(exist_ok=True)
        req = urllib.request.Request("https://api.scryfall.com/catalog/card-names",
                                     headers={"User-Agent": "divinci-table/0.1", "Accept": "application/json"})
        body = urllib.request.urlopen(req, timeout=30).read().decode()
        cache.write_text(body)
    return json.loads(cache.read_text())["data"]


CATALOG = load_catalog()        # always: spoken plays can name any card, not just the AI's deck
AI_PLAYERS = []
for spec in args.ai or ["Talrand|Talrand, Sky Summoner|Daniel"]:
    name, commander, voice_name = (spec.split("|") + ["", ""])[:3]
    AI_PLAYERS.append({"name": name.strip(), "commander": commander.strip() or None,
                       "voice": voice_name.strip() or None})
VP = None
if args.ai_deck:
    from player import VirtualPlayer
    VP = VirtualPlayer(args.ai_deck, AI_PLAYERS[0]["name"])
    AI_PLAYERS[0]["has_deck"] = True
    AI_PLAYERS[0]["deck"] = VP.deck_name
VP_LOCK = threading.RLock()     # re-entrant: brain actions call helpers that lock again
BRAIN_EXTERNAL = args.brain == "external"

# ── public event stream: what the table has seen and heard (never the AI's hand) ─────────────
EVENTS: list[dict] = []
EV_LOCK = threading.Lock()
_ev_id = [0]


def emit(event_type: str, **fields) -> dict:
    # (not "kind": heard/attention events carry their own "kind" field)
    with EV_LOCK:
        _ev_id[0] += 1
        e = {"id": _ev_id[0], "ts": round(time.time(), 2), "type": event_type, **fields}
        EVENTS.append(e)
        del EVENTS[:-3000]
    return e


LIFE: dict[str, int] = {}             # filled once the human players are parsed, below
REPLIES = os.environ.get("REPLIES", "ollama")    # "ollama": in-character via local Gemma; "template"
HUMANS = []
for spec in args.human:
    name, _, commander = spec.partition("|")
    HUMANS.append({"name": name.strip(), "commander": commander.strip() or None})
LIFE.update({h["name"]: 40 for h in HUMANS})
# Table nicknames: players say "Krenko", the card is "Krenko, Tin Street Kingpin". Only for cards
# known to be in THIS game, because many legendary cards share a first name.
NICKNAMES = {p["commander"].split(",")[0]: p["commander"] for p in AI_PLAYERS + HUMANS if p["commander"]}
DECK = parse_decklist(Path(args.deck).read_text()) if args.deck else []
TOKEN = secrets.token_urlsafe(24)
tok_path = Path(args.token_file) if args.token_file else HERE / ".brain-token"
tok_path.write_text(TOKEN)
os.chmod(tok_path, 0o600)


class Table:
    def __init__(self, deck):
        self.lock = threading.Lock()
        self.library = Counter(deck)
        self.slots: dict[int, str] = {}      # slot number -> card name (hidden)
        self.played: list[tuple[int, str]] = []
        self.pending, self.pending_n = None, 0
        self.last_seen = 0.0                 # last time ANY library card was in view
        self.armed = True                    # False until the scanned card leaves the camera
        self.history: list[int] = []         # slots filled by scans, for undo

    def next_slot(self):
        n = 1
        while n in self.slots:
            n += 1
        return n

    def public(self):
        top = max([7, *self.slots]) if self.slots else 7
        return {"slots": [{"slot": n, "filled": n in self.slots} for n in range(1, top + 1)],
                "hand": len(self.slots), "library": sum(self.library.values()),
                "played": [{"slot": s, "card": c} for s, c in self.played[-10:]]}


T = Table(DECK)


def scan(image: bytes):
    try:
        lines = [l.text for l in read_lines(image, hints=sorted(set(DECK)) or None)]
    except Exception as e:
        raise BadRequest(f"not a readable image ({type(e).__name__})")
    with T.lock:
        if args.any_card:
            name, s1, s2 = identify_any(lines, CATALOG)
        else:
            candidates = [n for n, c in T.library.items() if c > 0]
            name, s1, s2 = identify(lines, candidates)
        now = time.time()
        if name is None:
            if not T.armed and now - T.last_seen >= args.clear_secs:
                T.armed = True               # previous card has left the camera
            T.pending, T.pending_n = None, 0
            return {"status": "ready" if T.armed else "remove card", "score": round(s1, 2)}
        T.last_seen = now
        if not T.armed:
            return {"status": "remove card"}
        T.pending_n = T.pending_n + 1 if name == T.pending else 1
        T.pending = name
        if T.pending_n < args.confirm_frames:
            return {"status": "reading"}
        slot = T.next_slot()
        T.slots[slot] = name
        if not args.any_card:
            T.library[name] -= 1
        T.history.append(slot)
        T.armed, T.pending, T.pending_n = False, None, 0
        print(f"scan -> slot {slot}  (hand {len(T.slots)}, library {sum(T.library.values())})", flush=True)
        return {"status": "added", "slot": slot, **T.public()}


# The "show" camera: public cards held up for the AI to see. Separate state from the hidden hand.
SHOW = {"armed": True, "pending": None, "n": 0, "last_seen": 0.0, "misses": 0, "vision_tried": False}
SHOW_LOCK = threading.Lock()

CONVO = {"recent": [], "announced": []}
CONVO_LOCK = threading.Lock()


def whisper_hint():
    """Names Whisper would otherwise mishear. Whisper's prompt window is short (~224 tokens), so
    this lists the game's own names first and stops well before that."""
    everyone = AI_PLAYERS + HUMANS
    names = [p["name"] for p in everyone] + [p["commander"] for p in everyone if p["commander"]]
    if VP:                                   # its own permanents: what removal will be aimed at
        names += [p.name for p in VP.battlefield if not p.is_("Land") and not p.token]
    names += CONVO["announced"][-15:] + sorted(set(DECK))[:40]
    seen, out = set(), []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return "Magic: The Gathering Commander game: mana, life, graveyard, library. Names: " + "; ".join(out) + "."


def expand_nicknames(text: str) -> str:
    for nick, full in NICKNAMES.items():
        text = re.sub(r"\b" + re.escape(nick) + r"\b(?!,)", full, text)
    return text


def handle_utterance(wav: bytes):
    import voice
    t0 = time.time()
    try:
        audio = voice.wav_to_float32(wav)
    except Exception as e:                        # not a 16 kHz mono WAV: the caller's fault, not ours
        raise BadRequest(f"utterance must be 16 kHz mono 16-bit WAV ({type(e).__name__})")
    text, nsp = voice.transcribe(audio, whisper_hint())
    t_stt = round((time.time() - t0) * 1000)
    if voice.is_noise(text, nsp):
        return {"heard": text, "ignored": "no speech", "stt_ms": t_stt}
    second = None
    if voice.WHISPER_SECOND:                          # "I cast <something>" that names no card: ask a bigger ear
        from match import name_phrase, recognise_spoken
        if name_phrase(text) and not recognise_spoken(expand_nicknames(text), CATALOG)[0]:
            text2, _ = voice.transcribe(audio, whisper_hint(), voice.WHISPER_SECOND)
            if recognise_spoken(expand_nicknames(text2), CATALOG)[0]:
                second, text = text, text2
            t_stt = round((time.time() - t0) * 1000)
    who, margin, scores = speakers().identify(audio) if speakers().names() else (None, 0.0, {})
    CURRENT_AUDIO["a"] = audio
    ai_names = [p["name"] for p in AI_PLAYERS]
    human_voice = bool(who) and who not in ai_names and margin >= 0.1
    if (not human_voice and is_echo(text)) or (who in ai_names and scores.get(who, 0) >= 0.5):
        return {"heard": text, "ignored": "echo", "speaker_id": who, "stt_ms": t_stt}
    import tablefacts
    enrolling = tablefacts.enrollment(text, [h["name"] for h in HUMANS])
    pend = UNKNOWN_VOICE if time.time() - UNKNOWN_VOICE.get("at", 0) < 45 else {}
    if not enrolling and pend:                         # "Who's that?" → "Jess."
        enrolling = tablefacts.bare_name(text, [h["name"] for h in HUMANS])
    if enrolling and pend:
        speakers().enroll(enrolling, pend["audio"])    # the line it couldn't place was theirs too
        for how, n in pend["changes"]:
            change_life(enrolling, None if how == "set" else n, enrolling, set_to=n if how == "set" else None)
        UNKNOWN_VOICE.clear()
        speakers().enroll(enrolling, audio)
        hi = f"Got it, {enrolling} — you're at {life_table()[enrolling]}."
        emit("heard", text=text, kind="enroll", addressee=None, cards=[], for_ai=False, by=enrolling)
        if BRAIN_EXTERNAL:
            emit("say", speaker=VP.name if VP else ai_names[0], text=hi, action="rules", speech=spoken(hi))
            return {"heard": text, "enrolled": enrolling, "reply": None, "speaker_id": enrolling, "stt_ms": t_stt}
        return {"heard": text, "enrolled": enrolling, "reply": hi, "speech": spoken(hi), "reply_source": "rules",
                "speaker": ai_names[0], "speaker_id": enrolling, "stt_ms": t_stt}
    if enrolling:
        speakers().enroll(enrolling, audio)
        emit("heard", text=text, kind="enroll", addressee=None, cards=[], for_ai=False, by=enrolling)
        hi = f"Hi, {enrolling}."
        if BRAIN_EXTERNAL:
            emit("say", speaker=VP.name if VP else ai_names[0], text=hi, action="rules", speech=spoken(hi))
            return {"heard": text, "enrolled": enrolling, "reply": None, "speaker_id": enrolling, "stt_ms": t_stt}
        return {"heard": text, "enrolled": enrolling, "reply": hi, "speech": spoken(hi), "reply_source": "rules",
                "speaker": ai_names[0], "speaker_id": enrolling, "stt_ms": t_stt}
    if who and who not in ai_names and margin >= 0.2:
        speakers().enroll(who, audio)                  # a confident match sharpens that voice
    if VP and tablefacts.is_hold(text, VP.name):       # "Wait, Claude, hold on." — stop talking, don't act
        emit("hush", by=who)
        if BRAIN_EXTERNAL:
            emit("attention", kind="hold", text=text, addressee=VP.name, by=who)
        return {"heard": text, "hold": True, "reply": None, "speaker_id": who, "stt_ms": t_stt}
    fix = tablefacts.correction(text)
    if fix and LAST_PLAY["cards"] and time.time() - LAST_PLAY["at"] < 120:
        from match import recognise_spoken
        new = recognise_spoken(expand_nicknames("I cast " + fix), CATALOG)[0]
        if new:                                        # "No, I said Sol Talisman." replaces the misheard card
            with CONVO_LOCK:
                for c in LAST_PLAY["cards"]:
                    if c in CONVO["announced"]:
                        CONVO["announced"].reverse(); CONVO["announced"].remove(c); CONVO["announced"].reverse()
                CONVO["announced"] += new
            emit("heard", text=text, kind="correction", addressee=None, cards=new, replaced=LAST_PLAY["cards"], by=who)
            LAST_PLAY.update(cards=new, at=time.time())
            return {"heard": text, "cards": new, "corrected": True, "reply": None, "speaker_id": who, "stt_ms": t_stt}

    with CONVO_LOCK:
        recent = list(CONVO["recent"])
    r = voice.route(text, recent, AI_PLAYERS, HUMANS)
    with CONVO_LOCK:
        CONVO["recent"].append(text)
    cards = []
    from match import name_phrase
    if r["kind"] != "play" and name_phrase(text) and not re.search(r"\?\s*$", text) \
            and not voice.spoken_to(text, [p["name"] for p in AI_PLAYERS]):
        # code rule: "I cast <a real card>" IS a play, whatever the router thought — Gemma called
        # "Archastristic Study" (I cast Rhystic Study) chatter. Only if a card is actually recognised.
        from match import recognise_spoken
        if recognise_spoken(expand_nicknames(text), CATALOG)[0]:
            r = {**r, "kind": "play", "addressee": "nobody in particular", "overridden": "action+card"}
    if r["kind"] == "play":
        expanded = expand_nicknames(text)
        import tablefacts
        is_report = bool(tablefacts.parse_life(text, list(life_table()))) and not re.search(r"\b(cast|play|plays|casting)\b", text, re.I)
        from match import recognise_spoken
        # exact names first; a misheard one by sound + what people actually play (match.py) —
        # measured on every mishearing seen: 33/43 recovered, 0 wrong (tests/hearing_names.py)
        cards = [] if is_report else recognise_spoken(expanded, CATALOG)[0]
        with CONVO_LOCK:
            CONVO["announced"] += cards
        if cards:
            LAST_PLAY.update(cards=list(cards), at=time.time())
    speaker, reply = voice.decide_reply(r, [p["name"] for p in AI_PLAYERS])
    emit("heard", text=text, kind=r["kind"], addressee=r["addressee"], cards=cards,
         for_ai=bool(reply) and speaker is not None, by=who)
    rules = table_rules(text, r, cards, who)
    if rules is not None:
        base = {"heard": text, "route": r, "cards": cards, "speaker": VP.name if VP else speaker,
                "blocked": False, "stt_ms": t_stt, "total_ms": round((time.time() - t0) * 1000),
                "announced": CONVO["announced"][-10:], "rules": {k: v for k, v in rules.items() if k not in ("reply", "source")}}
        if rules.get("awaiting"):
            return {**base, "reply": None, "awaiting": "brain", "reply_source": "brain"}
        if rules.get("reply"):
            if BRAIN_EXTERNAL:
                emit("say", speaker=VP.name, text=rules["reply"], action="rules", speech=spoken(rules["reply"]))
                return {**base, "reply": None, "reply_source": "rules-event"}
            return {**base, "reply": rules["reply"], "speech": spoken(rules["reply"]), "reply_source": "rules"}
    if BRAIN_EXTERNAL and reply and VP and speaker == VP.name:
        ev = emit("attention", kind=r["kind"], text=text, addressee=speaker)
        hold_the_floor(ev["id"])
        return {"heard": text, "route": r, "cards": cards, "speaker": speaker, "reply": None,
                "awaiting": "brain", "blocked": False, "stt_ms": t_stt,
                "total_ms": round((time.time() - t0) * 1000), "announced": CONVO["announced"][-10:]}
    reply_source, reply_ms = "template", 0
    turn = None
    if reply == voice.TURN:
        if VP is None or speaker != VP.name:
            reply = None                              # no virtual deck: the humans play its cards
        else:
            turn = run_ai_turn()
            turn["speech"] = spoken(turn["said"])
            return {"heard": text, "route": r, "cards": cards, "speaker": speaker, "reply": " ".join(turn["said"]),
                    "reply_source": "turn", "turn": turn, "blocked": False, "stt_ms": t_stt,
                    "total_ms": round((time.time() - t0) * 1000), "announced": CONVO["announced"][-10:]}
    that_card = None
    if reply and REPLIES == "ollama":
        ai = next(p for p in AI_PLAYERS if p["name"] == speaker)
        decision = ({"Deal.": "You ACCEPT the offer.", "No deal.": "You DECLINE the offer."}.get(reply)
                    or "Answer in character without revealing your cards or committing to a plan.")
        if re.search(r"\b(that|this|the) card\b|\bthat one\b", text, re.I):
            import oracle
            with CONVO_LOCK:
                last = CONVO["announced"][-1] if CONVO["announced"] else None
            c = oracle.card(last) if last else None
            if c:
                decision += f' "That card" is {last} ({c["type"]}: {c["text"][:200]}). Name it and react to what it does.'
                that_card = last
        with CONVO_LOCK:
            board = list(CONVO["announced"])
        t1 = time.time()
        try:
            own = VP.public()["battlefield"] if (VP and ai["name"] == VP.name) else None
            said = voice.persona_reply(ai, r["kind"], text, recent, board, decision, own)
            if said:
                reply, reply_source = said, "persona"
        except Exception as e:                       # the template still gets said
            print(f"persona reply failed, using template: {type(e).__name__}", flush=True)
        reply_ms = round((time.time() - t1) * 1000)
    if that_card and reply and reply_source == "persona" and that_card.lower() not in reply.lower():
        import oracle
        import tablefacts
        fact = tablefacts.say_card(that_card, oracle.card(that_card)["text"], limit=1)
        reply = f"{fact} {reply}"                      # which card, stated by code; the opinion is Gemma's
    if r["kind"] == "deal" and reply and reply_source == "persona":
        verdict = "Deal." if r.get("accept_deal", 0) >= 0.5 else "No deal."
        if not re.match(r"^\W*(deal|no deal|yes|no|agreed|you're on|sure|nope)\b", reply, re.I):
            reply = f"{verdict} {reply}"                   # code decided; the words must say so first
    blocked = None
    if reply:
        with T.lock:
            hidden = list(T.slots.values())
        if VP:
            with VP_LOCK:
                hidden += VP.private_hand()
        blocked = voice.leaks_hand(reply, hidden, partial=reply_source == "persona")
        if blocked:
            print("reply blocked: it named a card in the hidden hand", flush=True)
            reply, reply_source = "Not telling.", "template"      # asked something: say SOMETHING, safely
    return {"heard": text, "route": r, "cards": cards, "speaker": speaker, "reply": reply,
            "speech": spoken(reply) if reply else None,
            "reply_source": reply_source, "reply_ms": reply_ms, "blocked": bool(blocked), "stt_ms": t_stt, "total_ms": round((time.time() - t0) * 1000),
            "announced": CONVO["announced"][-10:]}


def life_table():
    t = dict(LIFE)
    if VP:
        t[VP.name] = VP.life
    return t


def change_life(player, delta, by, set_to=None):
    """Anyone's life, by button, brain or voice. set_to: "Michael's at 31" states a total."""
    if set_to is None and (not isinstance(delta, int) or isinstance(delta, bool)):
        return {"error": "delta must be an integer"}
    lt = life_table()
    if VP and player.lower() in (VP.name.lower(), "me"):
        player = VP.name
    elif player not in LIFE:
        return {"error": f"unknown player '{player}' (players: {list(lt)})"}
    if set_to is not None:
        delta = set_to - lt[player]
    if VP and player == VP.name:
        with VP_LOCK:
            VP.life += delta
    else:
        LIFE[player] += delta
    emit("life", player=player, delta=delta, by=by, life=life_table()[player])
    return life_table()


# ── who is speaking (speakers.py: ECAPA voice embeddings, enrolled by "This is Michael.") ───────
SPEAKERS = None
LAST_PLAY = {"cards": [], "at": 0.0}          # the latest cards heard, for "No, I said …"
UNKNOWN_VOICE: dict = {}
CURRENT_AUDIO: dict = {}                       # "I'm at 35" from a voice it couldn't place, until they say who


def speakers():
    global SPEAKERS
    if SPEAKERS is None:
        from speakers import Speakers
        SPEAKERS = Speakers()
    return SPEAKERS


def enroll_ai_voices():
    """The AI players' own TTS voices, enrolled at startup: hearing that voice through the mic is
    an echo, whatever the words."""
    import subprocess
    import tempfile
    import voice
    for p in AI_PLAYERS:
        v = p.get("voice") or "Moira"
        for line in (f"{p['name']}, turn three. I play a Forest.", "No blocks. I take four; I'm at thirty-six.",
                     "That's my turn. One moment, let me think."):
            with tempfile.NamedTemporaryFile(suffix=".wav") as f:
                subprocess.run(["say", "-v", v, "-o", f.name, "--data-format=LEI16@16000", line], check=True)
                speakers().enroll(p["name"], voice.wav_to_float32(f.read()))


# ── what the AI has said, for the echo filter and for speech ─────────────────────────────────
SPOKEN: list[tuple[float, float, str]] = []     # (its batch starts, this line ends — estimated, text)     # (starts, ends — estimated, text)
WORDS_PER_S = 2.3            # Moira measured 2.0–3.2 words/s (2.48 overall), so this errs long


def spoken(texts):
    """Record lines the AI is about to say, queued one after another as the page speaks them.
    Returns the same lines as they should SOUND (pronounce.py)."""
    import pronounce
    now = time.time()
    batch = end = max(now, SPOKEN[-1][1] if SPOKEN else now)     # queued behind whatever is still being said
    for t in texts if isinstance(texts, list) else [texts]:
        if t:
            end += 0.3 + len(t.split()) / WORDS_PER_S      # + the pause between queued lines
            SPOKEN.append((batch, end, t))
    del SPOKEN[:-60]
    return [pronounce.for_speech(t) for t in texts] if isinstance(texts, list) else pronounce.for_speech(texts)


def is_echo(text: str) -> bool:
    """The laptop's mic hears the laptop's speaker. A transcript that closely matches something the
    AI is saying right now is its own voice coming back, not a player."""
    import tablefacts
    def words(s):
        s = tablefacts.words_to_numbers(s.lower().replace("\u2019", "'"))
        return [re.sub(r"'s$|s$", "", w) for w in re.findall(r"[a-z0-9']+", s)]
    t = words(text)
    if len(t) < 3:
        return False
    now = time.time()
    # only lines that could still be coming out of the speaker (+ the mic's end-of-speech wait and
    # transcription): a player saying "I play a Forest" a minute later is a player
    # the echo reaches us ~1.5 s after the line ends (end-of-speech wait + transcription); 3 s of slack
    lines = [line for start, end, line in SPOKEN if start - 1 <= now <= end + 5]
    live = [w for line in lines for w in words(line)]
    if not live:
        return False
    if len(t) <= 4:
        # a short line is an echo only if it IS one of the AI's lines, word for word: "Claude, your
        # turn." shares words with "Claude's turn 3." but is a player handing over the turn
        return any(words(line) == t for line in lines)
    # echo = nearly every word heard is one the AI was saying. "Claude, your turn." against the AI's
    # "Claude's turn 2." shares only "claude" and "turn" — a player, not an echo (measured: a looser
    # whole-string ratio swallowed exactly that line and the AI never took its turn).
    from difflib import get_close_matches
    pool = list(live)
    hit = 0
    for w in t:                                   # "planes" heard back for the "Plains" it said
        m = w if w in pool else next(iter(get_close_matches(w, pool, n=1, cutoff=0.75)), None)
        if m:
            pool.remove(m)
            hit += 1
    return hit / len(t) >= 0.8


# ── the table rules the code owns (tablefacts.py): life, attacks, removal, public questions ──
def table_rules(text: str, r: dict, cards: list[str], who: str | None = None) -> dict | None:
    """Returns {"reply": str|None, "source": "rules", ...} when the code handled the line, or None."""
    import tablefacts as F
    import oracle
    import voice
    players = list(life_table())
    ai = VP.name if VP else None
    addressed = voice.spoken_to(text, [ai]) if ai else None
    said, notes = [], {}
    if ai and cards and F.claims_ai_did(text, ai):     # "Claude casts Wrath of God" — not from Claude
        with CONVO_LOCK:                              # and those cards aren't someone's board either
            for c in cards:
                if c in CONVO["announced"]:
                    CONVO["announced"].remove(c)
        return {"reply": "That wasn't me.", "source": "rules", "claim": True}
    mentioned = cards or find_cards_in_text(expand_nicknames(text), CATALOG)
    if ai and addressed == ai and F.hand_probe(text, mentioned):
        return {"reply": "I don't talk about my hand.", "source": "rules", "probe": True}
    if VP and VP.last_cast and time.time() - VP.last_cast["at"] < 180 and \
            (F.is_counter(text) or any(oracle.effect(c) == "counter" for c in cards)):
        lc = VP.last_cast
        notes["removal"] = {"spell": next((c for c in cards if oracle.effect(c) == "counter"), None),
                            "effect": "counter", "target": lc["name"]}
        if BRAIN_EXTERNAL:
            emit("attention", kind="removal", text=text, addressee=ai, spell=notes["removal"]["spell"],
                 effect="counter", target=lc["name"])
            return {"reply": None, "source": "brain", "awaiting": "brain", **notes}
        with VP_LOCK:
            p = next((x for x in VP.battlefield if x.id == lc["perm"]), None) if lc["perm"] else None
            if p:
                VP.move(f"#{p.id}", "graveyard")
            VP.last_cast = None
        return {"reply": f"{lc['name']} is countered." + (" Back to the command zone." if lc.get("commander") else ""),
                "source": "rules", **notes}
    for p_, n, how in F.parse_life(text, players, addressed=addressed, speaker=who):
        if p_ == F.I_UNRESOLVED:                      # "I'm at 35" from a voice it doesn't know
            if "unknown_speaker" not in notes:
                said.append("Who's that?")            # their answer ("Jess.") enrolls the voice and applies it
            UNKNOWN_VOICE.update(at=time.time(), audio=CURRENT_AUDIO.get("a"))
            UNKNOWN_VOICE.setdefault("changes", []).append((how, n))
            notes["unknown_speaker"] = True
            continue
        change_life(p_, None if how == "set" else n, "voice" if not who else who, set_to=n if how == "set" else None)
        notes.setdefault("life", []).append({"player": p_, "amount": n, "how": how})
        if p_ == ai:
            said.append(f"I'm at {VP.life}.")
    others = [x["player"] for x in notes.get("life", []) if x["player"] != ai]
    if others:                                        # "Jess, 36." — a missed line is then obvious to the table
        lt = life_table()
        said.append(", ".join(f"{p_} {lt[p_]}" for p_ in dict.fromkeys(others)) + ".")
    if VP:
        atk = F.parse_attack(text, ai)
        if atk:
            attacker = next((c for c in cards if (oracle.card(c) or {}).get("power") is not None), None)
            amount = atk["amount"] or F.attack_total(text) or (oracle.power(attacker) if attacker else None)
            notes["attack"] = {"attacker": attacker, "amount": amount, "trample": atk["trample"]}
            if amount is None:
                said.append("Attacking me with what, and for how much?")
            elif BRAIN_EXTERNAL:
                emit("attention", kind="attacked", text=text, addressee=ai, attacker=attacker, amount=amount,
                     trample=atk["trample"])
                return {"reply": None, "source": "brain", "awaiting": "brain", **notes}
            else:
                from ai_turn import decide_block
                with CONVO_LOCK:
                    board = list(CONVO["announced"])
                with VP_LOCK:
                    life0 = VP.life
                    res = decide_block(VP, amount, attacker, atk["trample"], HUMANS, board)
                if VP.life != life0:
                    emit("life", player=ai, delta=VP.life - life0, by="combat", life=VP.life)
                forget_dead(res)
                said += res["said"]
        own = [p.name for p in VP.battlefield if not p.is_("Land")] if VP else []
        for c in dict.fromkeys(cards):                 # each spell once; its own permanents are targets, not spells
            if c in own:
                continue
            if "removal" in notes:                     # one spell per line
                break
            eff = oracle.effect(c)
            if eff in ("destroy", "exile", "bounce", "shuffle", "damage", "aura"):
                target = F.removal_target(text, ai, own)
                if not target:
                    continue
                notes["removal"] = {"spell": c, "effect": eff, "target": target}
                if BRAIN_EXTERNAL:
                    tp = next((x for x in VP.battlefield if x.name == target), None)
                    emit("attention", kind="removal", text=text, addressee=ai, spell=c, effect=eff, target=target,
                         illegal=cant_be_targeted(tp, eff) if tp else None)
                    return {"reply": None, "source": "brain", "awaiting": "brain", **notes}
                said += apply_removal(c, eff, target)
            elif eff in ("wipe", "exile-all", "bounce-all", "damage-all"):
                notes["removal"] = {"spell": c, "effect": eff, "target": "all"}
                if BRAIN_EXTERNAL:
                    emit("attention", kind="removal", text=text, addressee=ai, spell=c, effect=eff, target="all")
                    return {"reply": None, "source": "brain", "awaiting": "brain", **notes}
                said += apply_removal(c, eff, None)
        if VP and "removal" not in notes:              # aimed at its permanent, but no known spell
            target = F.removal_target(text, ai, own)
            verb = re.search(r"\b(destroy|destroys|kill|kills|exile|exiles|bounce|bounces|return|returns)\b", text, re.I)
            if target and (verb or re.search(r"\b(cast|casting|bolt|target|targeting)\b", text, re.I)):
                eff = None
                if verb:
                    v = verb.group(1).lower()
                    eff = "exile" if v.startswith("exile") else "bounce" if v.startswith(("bounce", "return")) else "destroy"
                notes["removal"] = {"spell": None, "effect": eff, "target": target}
                if BRAIN_EXTERNAL:
                    emit("attention", kind="removal", text=text, addressee=ai, spell=None, effect=eff, target=target)
                    return {"reply": None, "source": "brain", "awaiting": "brain", **notes}
                said += apply_removal("(spoken)", eff, target) if eff else [f"What does that do to {target}?"]
    if VP and addressed == ai and r.get("kind") in ("question", "chatter", "play", "deal"):
        with CONVO_LOCK:
            last = CONVO["announced"][-1] if CONVO["announced"] else None
        which = F.card_question(text, cards, last, VP.commander["name"])
        c = oracle.card(which) if which else None
        if c and which not in VP.private_hand():          # never read out a card it's holding
            said.append(F.say_card(which, c["text"]))
            notes["card"] = which
    if VP and addressed == ai and r.get("kind") in ("question", "chatter", "play", "deal") and "card" not in notes:
        kind = F.public_question(text)
        if kind:
            with VP_LOCK:
                said.append(F.answer_public(kind, VP.public(), life_table(), ai))
            notes["public"] = kind
    if not said and not notes and ai and r.get("kind") in ("play", "chatter") and ai.lower() in text.lower() \
            and re.search(r"\b(attack|attacks|attacking|swing|swings|damage|hits?)\b", text, re.I) \
            and not re.search(r"\bI (?:attack|play|cast)\b", text) \
            and not voice.spoken_to_someone_else(text, [ai]):
        said.append("Sorry, say that again? Who's attacking me, and for how much?")
        notes["unclear"] = True
    if not said and not notes:
        return None
    return {"reply": " ".join(said) or None, "source": "rules", **notes}


def forget_dead(block_result: dict):
    """An attacker that died blocking-wise leaves the table's list of its owner's cards, so the brain
    doesn't keep planning around a creature that's gone."""
    dead = block_result.get("attacker_died")
    if dead:
        with CONVO_LOCK:
            for i in range(len(CONVO["announced"]) - 1, -1, -1):
                if CONVO["announced"][i] == dead:
                    del CONVO["announced"][i]
                    break


def cant_be_targeted(p, eff: str) -> str | None:
    import tablefacts
    return tablefacts.cant_be_targeted(p.card, p.tapped, eff)


def apply_removal(spell: str, eff: str, target: str | None) -> list[str]:
    """Resolve an opponent's removal on the AI's board (gemma brain). Code, from Oracle text."""
    import oracle
    said = []
    with VP_LOCK:
        if target is None:                             # mass effects hit its creatures
            txt = (oracle.card(spell) or {}).get("text", "").lower()
            nonland = "nonland permanents" in txt or "all permanents" in txt
            victims = [p for p in list(VP.battlefield) if p.attached_to is None and not p.is_("Land")
                       and (nonland or p.is_("Creature"))]
            if eff == "damage-all":
                m = re.search(r"deals? (\d+) damage to each creature", txt)
                n = int(m.group(1)) if m else 0
                victims = [p for p in victims if p.is_("Creature") and VP.stats(p)[1] <= n]
            to = {"wipe": "graveyard", "exile-all": "exile", "bounce-all": "hand", "damage-all": "graveyard"}[eff]
            for p in victims:
                if p in VP.battlefield:
                    VP.move(f"#{p.id}", to)
            names = [p.name for p in victims]
            said.append(("I lose " + ", ".join(names) + ".") if names else "That doesn't touch my board.")
            return said
        p = next((x for x in VP.battlefield if x.name == target), None)
        if p is None:
            return [f"{target} isn't on my battlefield."]
        illegal = cant_be_targeted(p, eff)
        if illegal:
            return [illegal]
        if eff == "damage":
            txt = (oracle.card(spell) or {}).get("text", "").lower()
            m = re.search(r"deals? (\d+) damage", txt)
            if not m or VP.stats(p)[1] > int(m.group(1)):
                return [f"{target} survives that."]
            eff = "destroy"
        if eff == "aura":
            p.counters = 0
            return [f"{target} is enchanted with {spell}. Noted."]
        to = {"destroy": "graveyard", "exile": "exile", "bounce": "hand", "shuffle": "library"}[eff]
        if to == "library":
            VP.move(f"#{p.id}", "graveyard")
            gone = VP.graveyard.pop() if VP.graveyard and VP.graveyard[-1]["name"] == target else None
            if gone:
                import random
                VP.library.insert(random.randrange(len(VP.library) + 1), gone)
            return [f"{target} shuffles into my library."]
        VP.move(f"#{p.id}", to)
        return [{"graveyard": f"{target} dies.", "exile": f"{target} is exiled.", "hand": f"{target} returns to my hand."}[to]]


def voice_leak(text, hidden):
    import voice
    return voice.leaks_hand(text, hidden)


BRAIN_LAST = [0.0]
FILLERS = ["One moment.", "Hmm, let me think.", "Give me a second.", "Thinking.", "Hold on."]


def hold_the_floor(attention_id: int, after: float = 3.0):
    """External brain: if nothing has come back a few seconds after the table spoke to the AI,
    say so — silence at a table reads as not having heard."""
    asked = time.time()

    def later():
        time.sleep(after)
        if BRAIN_LAST[0] < asked:
            line = FILLERS[attention_id % len(FILLERS)]
            emit("say", speaker=VP.name, text=line, action="filler", speech=spoken(line))
    threading.Thread(target=later, daemon=True).start()


def run_ai_turn():
    """Play one full turn for the virtual AI player and return what it announces."""
    from ai_turn import take_turn
    with CONVO_LOCK:
        board = list(CONVO["announced"])
    with VP_LOCK:
        result = take_turn(VP, HUMANS, board)
    print(f"{VP.name} turn {VP.turn}: {len(result['said'])} announcements in {result['ms']} ms", flush=True)
    return result


def cards_in_view(image: bytes, lines: list[str]) -> list[str]:
    """Every card readable in a frame: each card found as a rectangle and read on its own crop
    (vision.py — cards across the table, two held up together), plus a full-frame read of title
    lines for a card that fills the view. Keyword/rules lines never count as names."""
    import vision
    ident = lambda ls: identify_any(ls, CATALOG)[0]
    tok = token_card(lines)                                      # printed "Token Creature — Goblin"
    if tok:
        return [tok]
    names = list(dict.fromkeys(c["name"] for c in vision.read_cards(image, ident, read_lines)))   # a card's
    full = ident(vision.title_lines(lines))                      # outline and its frame can both look like cards
    if full and full not in names:
        names.append(full)
    if not names:                                                # a token? ("Soldier", "Treasure")
        t = token_in_view(vision.title_lines(lines))
        if t:
            names.append(t)
    return names


def token_card(lines: list[str]) -> str | None:
    """A token says so on its type line ("Token Creature — Goblin"): report it as "Goblin token"
    rather than matching its title against real cards."""
    for i, l in enumerate(lines[:4]):
        if re.match(r"^\W*token\b", l, re.I):
            title = next((x for x in lines[:i] if len(x.strip()) >= 3), None)
            t = re.sub(r"[^A-Za-z' -]", "", title or "").strip() or re.sub(r".*[-—]\s*", "", l).strip()
            return f"{t.title()} token" if t else None
    return None


def token_in_view(lines: list[str]) -> str | None:
    import oracle
    from difflib import get_close_matches
    from match import norm
    idx = getattr(token_in_view, "idx", None) or {norm(n): n for n in oracle.token_names()}
    token_in_view.idx = idx
    for l in lines[:3]:                                          # the title is among the first lines read
        m = get_close_matches(norm(l), list(idx), n=1, cutoff=0.9)
        if m and len(m[0]) >= 4:
            return f"{idx[m[0]]} token"
    return None


# ── the overhead board camera: every card on the table, tapped or not, and what changed ────────
BOARD = {"cards": [], "missing": {}}
BOARD_LOCK = threading.Lock()


def read_board(image: bytes) -> dict:
    """All cards in an overhead frame. A card counts as having LEFT only after it's been missing
    from 2 reads in a row, so a hand passing over the board doesn't destroy anything."""
    import vision
    from collections import Counter
    ident = lambda ls: identify_any(ls, CATALOG)[0]
    try:
        seen = vision.read_cards(image, ident, read_lines)      # no de-dup: two Forests are two Forests
    except Exception as e:
        raise BadRequest(f"not a readable image ({type(e).__name__})")
    now = Counter(c["name"] for c in seen)
    with BOARD_LOCK:
        before = Counter(BOARD["cards"])
        entered = list((now - before).elements())
        gone = now.__class__(before - now)
        left = []
        for n, k in gone.items():
            BOARD["missing"][n] = BOARD["missing"].get(n, 0) + 1
            if BOARD["missing"][n] >= 2:
                left += [n] * k
        for n in list(BOARD["missing"]):
            if n not in gone:
                del BOARD["missing"][n]
        kept = Counter(BOARD["cards"]) - Counter(left)             # missing once: still on the board
        BOARD["cards"] = list((kept | now).elements())
        for n in left:
            BOARD["missing"].pop(n, None)
    if entered or left:
        emit("board", entered=entered, left=left)
    return {"cards": seen, "entered": entered, "left": left, "board": BOARD["cards"]}


def show_card(image: bytes, to: str, shown_by: str):
    """Public cards held up to the camera. The same set of cards must read on 2 frames in a row; if
    print is clearly in view but nothing reads after 2 frames, Gemma's vision gets ONE try and is
    believed only for a real card name. Re-arms once the cards have been out of view --clear-secs."""
    import voice
    from difflib import get_close_matches
    t0 = time.time()
    try:
        lines = [l.text for l in read_lines(image)]
    except Exception as e:                            # not an image Vision can open
        raise BadRequest(f"not a readable image ({type(e).__name__})")
    names = cards_in_view(image, lines)
    via = "ocr"
    now = time.time()
    key = tuple(sorted(names))
    with SHOW_LOCK:
        if not names:
            text_in_view = sum(len(x) for x in lines) >= 25          # something with print on it
            if not text_in_view:
                if not SHOW["armed"] and now - SHOW["last_seen"] >= args.clear_secs:
                    SHOW.update(armed=True, vision_tried=False, misses=0)
                SHOW.update(pending=None, n=0)
                return {"status": "ready" if SHOW["armed"] else "remove card"}
            SHOW["last_seen"] = now
            if not SHOW["armed"]:
                return {"status": "remove card"}
            SHOW["misses"] += 1
            if SHOW["vision_tried"]:                   # vision already had its one try on this card
                return {"status": "unreadable"}
            if SHOW["misses"] < 2:
                return {"status": "reading"}
            SHOW["vision_tried"] = True
        else:
            SHOW["last_seen"] = now
            if not SHOW["armed"]:
                return {"status": "remove card"}
            SHOW["n"] = SHOW["n"] + 1 if key == SHOW["pending"] else 1
            SHOW["pending"] = key
            if SHOW["n"] < 2:
                return {"status": "reading"}
    if not names:                                     # outside the lock: this call takes ~0.6 s
        guess = voice.gemma_read_card(image)
        norm_index = getattr(show_card, "idx", None) or {n.lower(): n for n in CATALOG}
        show_card.idx = norm_index
        hit = norm_index.get(guess.lower()) or next(
            (norm_index[m] for m in get_close_matches(guess.lower(), list(norm_index), n=1, cutoff=0.9)), None)
        if not hit:
            return {"status": "unreadable", "vision_guess": guess[:60]}
        names, via = [hit], "vision"
    with SHOW_LOCK:
        SHOW.update(armed=False, pending=None, n=0, misses=0)
    ai = next((p for p in AI_PLAYERS if p["name"] == to), AI_PLAYERS[0])
    shown = " and ".join(names)
    with CONVO_LOCK:
        CONVO["announced"] += names
        CONVO["recent"].append(f"({shown_by} showed {shown})")
        recent, board = list(CONVO["recent"]), list(CONVO["announced"])
    for n in names:
        emit("shown", card=n, by=shown_by, to=ai["name"], via=via)
    base = {"status": "seen", "card": names[0], "cards": names, "via": via, "speaker": ai["name"],
            "ms": round((time.time() - t0) * 1000), "board": board[-10:]}
    if BRAIN_EXTERNAL:
        return {**base, "reply": None, "reply_source": "brain"}
    opinion, src = "Noted.", "template"
    try:
        said = voice.persona_react(ai, shown, shown_by, recent, board)
        if said:
            opinion, src = said, "persona"
    except Exception as e:
        print(f"persona reaction failed, using template: {type(e).__name__}", flush=True)
    with T.lock:
        hidden = list(T.slots.values())
    if VP:
        with VP_LOCK:
            hidden += VP.private_hand()
    if voice.leaks_hand(opinion, [c for c in hidden if c not in names]):
        opinion, src = "Noted.", "template"
    # The table must hear WHICH card it saw: code says the name, the persona adds the opinion
    # (measured: persona reactions dropped or mangled the name — "Sarah Angel", "a little spark").
    reply = opinion if all(n.lower() in opinion.lower() for n in names) else f"{shown}. {opinion}"
    print(f"shown to {ai['name']}: {shown} (via {via})", flush=True)
    return {**base, "reply": reply, "speech": spoken(reply), "reply_source": src}


class BadRequest(Exception):
    pass


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj=None, body=None, ctype="application/json"):
        data = body if body is not None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _drain(self, n):
        """Read (and drop) a refused body up to 32 MB, so the client gets our 413 rather than a
        broken pipe. Anything larger: close the connection."""
        if 0 < n <= 32_000_000:
            while n > 0:
                n -= len(self.rfile.read(min(n, 1 << 20)) or b"x" * n)
        else:
            self.close_connection = True

    def _json(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            b = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            raise BadRequest("body is not JSON")
        if not isinstance(b, dict):
            raise BadRequest("body must be a JSON object")
        return b

    def do_GET(self):
        if self.path == "/":
            return self._send(200, body=(HERE / "scan.html").read_bytes(), ctype="text/html; charset=utf-8")
        if self.path == "/table":
            return self._send(200, body=(HERE / "table.html").read_bytes(), ctype="text/html; charset=utf-8")
        if self.path == "/show":
            return self._send(200, body=(HERE / "show.html").read_bytes(), ctype="text/html; charset=utf-8")
        if self.path == "/voice":
            return self._send(200, body=(HERE / "voice.html").read_bytes(), ctype="text/html; charset=utf-8")
        if self.path == "/api/voice-config":
            return self._send(200, {"ai_players": AI_PLAYERS, "humans": HUMANS, "brain": args.brain})
        if self.path == "/api/ai/state":
            if VP is None:
                return self._send(404, {"error": "no virtual AI deck (start with --ai-deck)"})
            with VP_LOCK:
                return self._send(200, VP.public())
        if self.path == "/api/ai/hand":                    # the AI's own brain only
            if not secrets.compare_digest(self.headers.get("X-Brain-Token", ""), TOKEN):
                return self._send(403, {"error": "brain token required"})
            with VP_LOCK:
                return self._send(200, {"hand": VP.private_hand() if VP else []})
        if self.path.startswith("/api/events"):
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            with EV_LOCK:
                last = EVENTS[-1]["id"] if EVENTS else 0
                since = last if q.get("since", ["0"])[0] == "latest" else int(q.get("since", ["0"])[0] or 0)
                out = [e for e in EVENTS if e["id"] > since][:300]
            return self._send(200, {"last": last, "events": out})
        if self.path == "/api/life":
            return self._send(200, self._life_table())
        if self.path == "/api/brain/state":
            if not self._brain_ok():
                return self._send(403, {"error": "brain token required"})
            from player import brain_view
            with VP_LOCK:
                v = brain_view(VP) if VP else {}
            with CONVO_LOCK:
                v["announced_by_others"] = list(CONVO["announced"][-30:])
            v["life_table"] = self._life_table()
            v["humans"] = HUMANS
            with EV_LOCK:
                v["recent_events"] = [e for e in EVENTS if e["type"] in ("heard", "shown", "attention", "life")][-15:]
            return self._send(200, v)
        if self.path == "/api/state":
            with T.lock:
                return self._send(200, T.public())
        if self.path == "/api/hand":                       # the AI's brain only
            if not secrets.compare_digest(self.headers.get("X-Brain-Token", ""), TOKEN):
                return self._send(403, {"error": "brain token required"})
            with T.lock:
                return self._send(200, {"hand": [{"slot": s, "card": c} for s, c in sorted(T.slots.items())],
                                        "library": dict(+T.library)})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        global T, VP                                       # /api/reset replaces the table and the AI's game
        try:
            if self.path == "/api/scan":
                n = int(self.headers.get("Content-Length", 0))
                if not 0 < n <= 8_000_000:
                    self._drain(n)
                    return self._send(413, {"error": "image must be 1 byte to 8 MB"})
                return self._send(200, scan(self.rfile.read(n)))
            if self.path == "/api/board":
                n = int(self.headers.get("Content-Length", 0))
                if not 0 < n <= 8_000_000:
                    self._drain(n)
                    return self._send(413, {"error": "image must be 1 byte to 8 MB"})
                return self._send(200, read_board(self.rfile.read(n)))
            if self.path.startswith("/api/show"):
                n = int(self.headers.get("Content-Length", 0))
                if not 0 < n <= 8_000_000:
                    self._drain(n)
                    return self._send(413, {"error": "image must be 1 byte to 8 MB"})
                to = self.headers.get("X-Show-To", "")
                by = self.headers.get("X-Shown-By", "") or "Someone"
                return self._send(200, show_card(self.rfile.read(n), to, by[:40]))
            if self.path == "/api/utterance":
                n = int(self.headers.get("Content-Length", 0))
                if not 0 < n <= 2_000_000:              # ~60 s of 16 kHz mono 16-bit
                    self._drain(n)
                    return self._send(413, {"error": "utterance must be 1 byte to 2 MB of WAV"})
                return self._send(200, handle_utterance(self.rfile.read(n)))
            if self.path == "/api/play":                   # a slot's card is revealed as it is played
                raw = self._json().get("slot")
                if not isinstance(raw, int) or isinstance(raw, bool):
                    return self._send(400, {"error": "slot must be a slot number"})
                slot = raw
                with T.lock:
                    if slot not in T.slots:
                        return self._send(400, {"error": f"slot {slot} is empty"})
                    card = T.slots.pop(slot)
                    T.played.append((slot, card))
                    print(f"slot {slot} played: {card}", flush=True)
                    return self._send(200, {**T.public(), "revealed": card})   # public() has its own "played" list
            if self.path == "/api/ai/turn":
                if VP is None:
                    return self._send(404, {"error": "no virtual AI deck (start with --ai-deck)"})
                if BRAIN_EXTERNAL:                         # the button hands the turn to the brain
                    ev = emit("attention", kind="turn", text="(turn button)", addressee=VP.name)
                    hold_the_floor(ev["id"])
                    return self._send(200, {"awaiting": "brain"})
                return self._send(200, run_ai_turn())
            if self.path == "/api/life":                   # anyone at the table: {"player": name, "delta": -3}
                b = self._json()
                return self._send(200, self._change_life(str(b.get("player", "")), b.get("delta"), b.get("by", "table")))
            if self.path.startswith("/api/brain/"):
                if not self._brain_ok():
                    return self._send(403, {"error": "brain token required"})
                return self._brain(self.path.rsplit("/", 1)[-1], self._json())
            if self.path == "/api/reset":                  # new game: empty hand, full library, no history
                with T.lock:
                    T = Table(DECK)
                with CONVO_LOCK:
                    CONVO["recent"].clear()
                    CONVO["announced"].clear()
                with SHOW_LOCK:
                    SHOW.update(armed=True, pending=None, n=0, last_seen=0.0, misses=0, vision_tried=False)
                for k in LIFE:
                    LIFE[k] = 40
                emit("new-game")
                SPOKEN.clear()
                with BOARD_LOCK:
                    BOARD.update(cards=[], missing={})
                if VP is not None:
                    from player import VirtualPlayer
                    with VP_LOCK:
                        VP = VirtualPlayer(args.ai_deck, VP.name)      # reshuffle: a new game
                print("table reset", flush=True)
                return self._send(200, T.public())
            if self.path == "/api/undo":                   # mis-scan: card goes back to the library
                with T.lock:
                    while T.history and T.history[-1] not in T.slots:
                        T.history.pop()
                    if not T.history:
                        return self._send(400, {"error": "nothing to undo"})
                    slot = T.history.pop()
                    T.library[T.slots.pop(slot)] += 1
                    T.armed = True
                    print(f"undo -> slot {slot} emptied", flush=True)
                    return self._send(200, {"undone": slot, **T.public()})
            self._send(404, {"error": "not found"})
        except BadRequest as e:
            self._send(400, {"error": str(e)})
        except Exception as e:                             # never crash the table over one request
            import traceback
            traceback.print_exc()                          # into the server log: a 500 must be diagnosable
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, *a):
        pass

    def _brain_ok(self):
        return VP is not None and secrets.compare_digest(self.headers.get("X-Brain-Token", ""), TOKEN)

    def _life_table(self):
        return life_table()

    def _change_life(self, player, delta, by):
        return change_life(player, delta, by)

    def _brain(self, action, b):
        """The external brain's actions. Engine-checked; each announcement is spoken as the AI."""
        from player import IllegalAction
        speak = not b.get("quiet")
        private = {}
        try:
            with VP_LOCK:
                if action == "say":
                    text = str(b.get("text", "")).strip()
                    hidden = VP.private_hand()
                    leak = voice_leak(text, hidden)
                    if leak and not b.get("force"):
                        return self._send(400, {"error": f"that names '{leak}', which is still in your hand "
                                                         f"(pass force to say it anyway)"})
                    said = [text]
                elif action == "begin":
                    said, drew = VP.begin_turn()
                    private["drew"] = drew
                elif action == "land":
                    said = VP.manual_land(b["name"])
                elif action == "cast":
                    said = VP.manual_cast(b["name"], on=b.get("on"), role_on=b.get("role_on"), modes=b.get("modes"),
                                          targets=b.get("targets"), commander=bool(b.get("commander")),
                                          x=int(b.get("x") or 0), discount=int(b.get("discount") or 0),
                                          color=b.get("color"))
                elif action == "block":                  # someone attacked the AI; the brain blocks (or not)
                    from ai_turn import resolve_block
                    blk = VP.perm(b["blocker"]) if b.get("blocker") else None
                    life0 = VP.life
                    res = resolve_block(VP, blk, int(b["amount"]), bool(b.get("trample")), b.get("attacker"))
                    said = res["said"]
                    if VP.life != life0:
                        emit("life", player=VP.name, delta=VP.life - life0, by="combat", life=VP.life)
                    forget_dead(res)
                elif action == "attack":
                    said = VP.manual_attack(b["assign"], role_on=b.get("role_on"))
                elif action == "end":
                    said = [b.get("text") or "That's my turn."]
                elif action in ("destroy", "exile", "bounce"):
                    said = VP.move(b["ref"], {"destroy": "graveyard", "exile": "exile", "bounce": "hand"}[action])
                elif action in ("tap", "untap"):
                    p = VP.perm(b["ref"])
                    p.tapped = action == "tap"
                    said = [f"{p.name} is {action}ped."] if b.get("announce") else []
                elif action == "counter":
                    p = VP.perm(b["ref"])
                    p.counters += int(b.get("n", 1))
                    said = [f"{p.name} gets {int(b.get('n', 1)):+d}/{int(b.get('n', 1)):+d} counters."]
                elif action == "token":
                    said = VP.make_token(b["name"], int(b["power"]), int(b["toughness"]), b.get("keywords") or [])
                elif action == "draw":
                    before = len(VP.hand)
                    VP.draw(int(b.get("n", 1)))
                    private["drew"] = [c["name"] for c in VP.hand[before:]]
                    said = [f"I draw {len(private['drew'])} card{'s' if len(private['drew']) != 1 else ''}."]
                elif action == "discard":
                    said = VP.discard(b["name"])
                elif action == "mill":
                    said = VP.mill(int(b.get("n", 1)))
                elif action == "search":
                    said = VP.search_library(b["name"], to=b.get("to", "hand"), tapped=bool(b.get("tapped")))
                elif action == "life":
                    r = self._change_life(str(b.get("player", "me")), b.get("delta"), VP.name)
                    if "error" in r:
                        return self._send(400, r)
                    said = []
                elif action == "new-game":
                    from player import VirtualPlayer
                    globals()["VP"] = VirtualPlayer(args.ai_deck, VP.name)
                    for k in LIFE:
                        LIFE[k] = 40
                    emit("new-game")
                    SPOKEN.clear()
                    said = ["New game. I shuffle up and draw seven."]
                else:
                    return self._send(400, {"error": f"unknown action '{action}'"})
        except IllegalAction as e:
            return self._send(400, {"error": str(e)})
        except KeyError as e:
            return self._send(400, {"error": f"missing field {e}"})
        BRAIN_LAST[0] = time.time()
        for line in said:
            if speak and line:
                emit("say", speaker=VP.name, text=line, action=action, speech=spoken(line))
        with VP_LOCK:
            state = VP.public()
        return self._send(200, {"ok": True, "said": said, "private": private, "public": state})


def _warm():
    import numpy as np
    import voice
    voice.transcribe(np.zeros(16000, dtype=np.float32), "warm-up")
    if voice.WHISPER_SECOND:
        try:
            voice.transcribe(np.zeros(16000, dtype=np.float32), "warm-up", voice.WHISPER_SECOND)
        except Exception as e:                        # model not downloaded: first pass only
            print(f"second-pass Whisper unavailable ({type(e).__name__}); using one model", flush=True)
            voice.WHISPER_SECOND = ""
    if os.environ.get("ROUTER", "ollama") == "ollama" or os.environ.get("REPLIES", "ollama") == "ollama":
        try:
            t0 = time.time()
            voice.ollama_load([voice.OLLAMA_MODEL, voice.REPLY_MODEL])
            print(f"Gemma loaded and pinned ({voice.OLLAMA_MODEL}, {time.time() - t0:.1f}s)", flush=True)
        except Exception as e:
            print(f"could not preload Gemma ({type(e).__name__}); first line will be slower", flush=True)
    try:
        t0 = time.time()
        enroll_ai_voices()
        print(f"voices: AI enrolled ({time.time() - t0:.1f}s); players enroll by saying \"This is <name>.\"", flush=True)
    except Exception as e:
        print(f"speaker ID unavailable ({type(e).__name__}: {e})", flush=True)


def _shutdown(signum, frame):
    """Unpin Gemma so its memory comes back, then exit."""
    try:
        import voice
        voice.ollama_unload([voice.OLLAMA_MODEL, voice.REPLY_MODEL])
        print("Gemma unloaded", flush=True)
    finally:
        os._exit(0)


import signal  # noqa: E402
signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT, _shutdown)


threading.Thread(target=_warm, daemon=True).start()   # first Whisper load takes seconds
mode = f"TEST MODE, any of {len(CATALOG)} card names" if args.any_card else f"deck: {len(DECK)} cards, {len(set(DECK))} distinct"
print(f"{mode}. Table (camera + mic): http://localhost:{args.port}/table   "
      f"Scan pad (AI's hand): http://localhost:{args.port}/   Show: /show   Voice: /voice", flush=True)
print("AI players: " + ", ".join(p["name"] for p in AI_PLAYERS)
      + (f" — brain: EXTERNAL (drive with table/tablectl.py)" if BRAIN_EXTERNAL else " — brain: local Gemma"), flush=True)
ThreadingHTTPServer(("127.0.0.1", args.port), H).serve_forever()
