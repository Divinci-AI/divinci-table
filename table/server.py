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
    lines = [l.text for l in read_lines(image, hints=sorted(set(DECK)) or None)]
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
    names += CONVO["announced"][-15:] + sorted(set(DECK))[:40]
    seen, out = set(), []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return "Magic: The Gathering Commander game. Names: " + "; ".join(out) + "."


def handle_utterance(wav: bytes):
    import voice
    t0 = time.time()
    audio = voice.wav_to_float32(wav)
    text, nsp = voice.transcribe(audio, whisper_hint())
    t_stt = round((time.time() - t0) * 1000)
    if voice.is_noise(text, nsp):
        return {"heard": text, "ignored": "no speech", "stt_ms": t_stt}
    with CONVO_LOCK:
        recent = list(CONVO["recent"])
    r = voice.route(text, recent, AI_PLAYERS, HUMANS)
    with CONVO_LOCK:
        CONVO["recent"].append(text)
    cards = []
    if r["kind"] == "play":
        spoken = text
        for nick, full in NICKNAMES.items():
            spoken = re.sub(r"\b" + re.escape(nick) + r"\b(?!,)", full, spoken)
        cards = find_cards_in_text(spoken, CATALOG)
        if not cards:                                 # a mangled name: choose among close candidates
            cands = near_card_candidates(spoken, CATALOG)
            if cands:
                pick, conf = voice.pick_heard_card(text, cands)
                if pick and conf >= 0.6:
                    cards = [pick]
        with CONVO_LOCK:
            CONVO["announced"] += cards
    speaker, reply = voice.decide_reply(r, [p["name"] for p in AI_PLAYERS])
    emit("heard", text=text, kind=r["kind"], addressee=r["addressee"], cards=cards,
         for_ai=bool(reply) and speaker is not None)
    if BRAIN_EXTERNAL and reply and VP and speaker == VP.name:
        emit("attention", kind=r["kind"], text=text, addressee=speaker)
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
            return {"heard": text, "route": r, "cards": cards, "speaker": speaker, "reply": " ".join(turn["said"]),
                    "reply_source": "turn", "turn": turn, "blocked": False, "stt_ms": t_stt,
                    "total_ms": round((time.time() - t0) * 1000), "announced": CONVO["announced"][-10:]}
    if reply and REPLIES == "ollama":
        ai = next(p for p in AI_PLAYERS if p["name"] == speaker)
        decision = ({"Deal.": "You ACCEPT the offer.", "No deal.": "You DECLINE the offer."}.get(reply)
                    or "Answer in character without revealing your cards or committing to a plan.")
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
    blocked = None
    if reply:
        with T.lock:
            hidden = list(T.slots.values())
        if VP:
            with VP_LOCK:
                hidden += VP.private_hand()
        blocked = voice.leaks_hand(reply, hidden)
        if blocked:
            print("reply blocked: it named a card in the hidden hand", flush=True)
            reply = None
    return {"heard": text, "route": r, "cards": cards, "speaker": speaker, "reply": reply,
            "reply_source": reply_source, "reply_ms": reply_ms, "blocked": bool(blocked), "stt_ms": t_stt, "total_ms": round((time.time() - t0) * 1000),
            "announced": CONVO["announced"][-10:]}


def voice_leak(text, hidden):
    import voice
    return voice.leaks_hand(text, hidden)


def run_ai_turn():
    """Play one full turn for the virtual AI player and return what it announces."""
    from ai_turn import take_turn
    with CONVO_LOCK:
        board = list(CONVO["announced"])
    with VP_LOCK:
        result = take_turn(VP, HUMANS, board)
    print(f"{VP.name} turn {VP.turn}: {len(result['said'])} announcements in {result['ms']} ms", flush=True)
    return result


def show_card(image: bytes, to: str, shown_by: str):
    """A public card held up to the camera. OCR (fast) must agree on 2 frames; if a card is clearly in
    view but OCR can't read it after 2 frames, ask Gemma's vision ONCE and accept its answer only if
    it is a real card name. Re-arms after the card has been out of view for --clear-secs."""
    import voice
    from difflib import get_close_matches
    t0 = time.time()
    lines = [l.text for l in read_lines(image)]
    name, _, _ = identify_any(lines, CATALOG)
    via = "ocr"
    now = time.time()
    with SHOW_LOCK:
        if name is None:
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
            SHOW["n"] = SHOW["n"] + 1 if name == SHOW["pending"] else 1
            SHOW["pending"] = name
            if SHOW["n"] < 2:
                return {"status": "reading"}
    if name is None:                                  # outside the lock: this call takes ~0.6 s
        guess = voice.gemma_read_card(image)
        norm_index = getattr(show_card, "idx", None) or {n.lower(): n for n in CATALOG}
        show_card.idx = norm_index
        hit = norm_index.get(guess.lower()) or next(
            (norm_index[m] for m in get_close_matches(guess.lower(), list(norm_index), n=1, cutoff=0.9)), None)
        if not hit:
            return {"status": "unreadable", "vision_guess": guess[:60]}
        name, via = hit, "vision"
    with SHOW_LOCK:
        SHOW.update(armed=False, pending=None, n=0, misses=0)
    ai = next((p for p in AI_PLAYERS if p["name"] == to), AI_PLAYERS[0])
    with CONVO_LOCK:
        CONVO["announced"].append(name)
        recent, board = list(CONVO["recent"]), list(CONVO["announced"])
    emit("shown", card=name, by=shown_by, to=ai["name"], via=via)
    if BRAIN_EXTERNAL:
        return {"status": "seen", "card": name, "via": via, "speaker": ai["name"], "reply": None,
                "reply_source": "brain", "ms": round((time.time() - t0) * 1000), "board": board[-10:]}
    reply, src = f"{name}. Noted.", "template"
    try:
        said = voice.persona_react(ai, name, shown_by, recent, board)
        if said:
            reply, src = said, "persona"
    except Exception as e:
        print(f"persona reaction failed, using template: {type(e).__name__}", flush=True)
    with T.lock:
        hidden = list(T.slots.values())
    if voice.leaks_hand(reply, [c for c in hidden if c != name]):
        reply, src = f"{name}. Noted.", "template"
    print(f"shown to {ai['name']}: {name} (via {via})", flush=True)
    return {"status": "seen", "card": name, "via": via, "speaker": ai["name"], "reply": reply,
            "reply_source": src, "ms": round((time.time() - t0) * 1000), "board": board[-10:]}


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj=None, body=None, ctype="application/json"):
        data = body if body is not None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

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
                    return self._send(413, {"error": "image must be 1 byte to 8 MB"})
                return self._send(200, scan(self.rfile.read(n)))
            if self.path.startswith("/api/show"):
                n = int(self.headers.get("Content-Length", 0))
                if not 0 < n <= 8_000_000:
                    return self._send(413, {"error": "image must be 1 byte to 8 MB"})
                to = self.headers.get("X-Show-To", "")
                by = self.headers.get("X-Shown-By", "") or "Someone"
                return self._send(200, show_card(self.rfile.read(n), to, by[:40]))
            if self.path == "/api/utterance":
                n = int(self.headers.get("Content-Length", 0))
                if not 0 < n <= 2_000_000:              # ~60 s of 16 kHz mono 16-bit
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
                    emit("attention", kind="turn", text="(turn button)", addressee=VP.name)
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
        except Exception as e:                             # never crash the table over one request
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, *a):
        pass

    def _brain_ok(self):
        return VP is not None and secrets.compare_digest(self.headers.get("X-Brain-Token", ""), TOKEN)

    def _life_table(self):
        t = dict(LIFE)
        if VP:
            t[VP.name] = VP.life
        return t

    def _change_life(self, player, delta, by):
        if not isinstance(delta, int) or isinstance(delta, bool):
            return {"error": "delta must be an integer"}
        if VP and player.lower() in (VP.name.lower(), "me"):
            with VP_LOCK:
                VP.life += delta
            player = VP.name
        elif player in LIFE:
            LIFE[player] += delta
        else:
            return {"error": f"unknown player '{player}' (players: {list(self._life_table())})"}
        emit("life", player=player, delta=delta, by=by, life=self._life_table()[player])
        return self._life_table()

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
                                          x=int(b.get("x") or 0), discount=int(b.get("discount") or 0))
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
                    said = ["New game. I shuffle up and draw seven."]
                else:
                    return self._send(400, {"error": f"unknown action '{action}'"})
        except IllegalAction as e:
            return self._send(400, {"error": str(e)})
        except KeyError as e:
            return self._send(400, {"error": f"missing field {e}"})
        for line in said:
            if speak and line:
                emit("say", speaker=VP.name, text=line, action=action)
        with VP_LOCK:
            state = VP.public()
        return self._send(200, {"ok": True, "said": said, "private": private, "public": state})


def _warm():
    import numpy as np
    import voice
    voice.transcribe(np.zeros(16000, dtype=np.float32), "warm-up")
    if os.environ.get("ROUTER", "ollama") == "ollama" or os.environ.get("REPLIES", "ollama") == "ollama":
        try:
            t0 = time.time()
            voice.ollama_load([voice.OLLAMA_MODEL, voice.REPLY_MODEL])
            print(f"Gemma loaded and pinned ({voice.OLLAMA_MODEL}, {time.time() - t0:.1f}s)", flush=True)
        except Exception as e:
            print(f"could not preload Gemma ({type(e).__name__}); first line will be slower", flush=True)


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
