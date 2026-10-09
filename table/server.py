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
import hashlib
import io
import json
import urllib.parse
import os
import secrets
import sys
import threading
from contextlib import contextmanager, nullcontext
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
ap.add_argument("--host", default="127.0.0.1",
                help="bind address. 0.0.0.0 lets players' phones open /me on the LAN; other devices then reach "
                     "ONLY the public player view (see LAN_OK), never the scan pad, mic, reset or brain")
ap.add_argument("--ai", action="append", default=[],
                help='an AI player, "Name|Commander card name|macOS voice" (repeatable), '
                     'e.g. "Talrand|Talrand, Sky Summoner|Daniel"')
ap.add_argument("--ai-deck", action="append", default=[],
                help="a virtual deck (decks/*.json) for an --ai player, in the same order (repeatable: the "
                     "first deck goes to the first --ai, the second to the second): it draws, plays and "
                     "announces its own cards instead of using physical ones")
ap.add_argument("--brain", choices=["gemma", "external"], default="gemma",
                help="who decides for the virtual AI player: the local Gemma, or an EXTERNAL brain driving it "
                     "through /api/brain (tablectl) — then the server never answers or plays for it")
ap.add_argument("--pilot", action="append", default=[],
                help="an --ai seat with an --ai-deck that a PERSON plays (repeatable): its virtual deck is driven "
                     "from /hand with that person's seat key instead of by a brain. For testing without cards.")
ap.add_argument("--human", action="append", default=[],
                help='a human player, "Name|Commander card name" (repeatable); their names go into the speech hint')
ap.add_argument("--order", default="",
                help='turn order, comma-separated names ("Fusion,Sam,Claude,Michael"); default: humans then AIs')
ap.add_argument("--restore", default="",
                help="resume a game from its snapshot.pkl (written automatically after every change)")
ap.add_argument("--tls-cert", default="", help="also serve HTTPS (WebXR needs a secure page on phones/headsets)")
ap.add_argument("--tls-key", default="")
ap.add_argument("--tls-port", type=int, default=8443)
ap.add_argument("--priority-window", type=float, default=10.0,
                help="every priority window lasts exactly this long (seconds), whoever holds what: passing early "
                     "doesn't shorten it and a seat that hasn't answered by then passes — so timing reveals nothing. "
                     "0 = the older behaviour (a random beat, then wait on seats that could respond)")
ap.add_argument("--priority-beat", default="1.5,3.5",
                help="every priority window lasts a random beat in this range (seconds, 'lo,hi'), whether or not "
                     "anyone could respond — so an open window never tells the table who holds an instant")
ap.add_argument("--priority-secs", type=float, default=45.0,
                help="how long NEXT waits for an AI seat that holds an instant before passing for it")
ap.add_argument("--autopass-default", choices=["off", "others", "others-no-combat"], default="off",
                help="what a person's auto-pass is set to until they change it: 'others-no-combat' passes for them on the "
                     "non-combat steps of someone else's turn (the cloud rooms use it: an AI's turn used to need ~11 passes "
                     "from each person). Each person can switch it off on their /me page, and that choice is kept")
ap.add_argument("--human-pass-secs", type=float, default=0.0,
                help="how long a PERSON has to pass priority in someone else's turn before the table passes for them "
                     "(logged as a timeout, never silent). 0 = never, the default for a table around one laptop; the cloud "
                     "rooms set it. The active player is never timed out, and a hold or an open question pauses it")
ap.add_argument("--fair-seed", choices=["local", "online", "off"], default="local",
                help="provably fair AI decks (fair.py): commit each AI seat's shuffle before the first draw. "
                     "local: OS entropy + players' secret words (offline); online: also ANU quantum + drand")
ap.add_argument("--confirm-frames", type=int, default=2, help="same card on N frames in a row")
ap.add_argument("--clear-secs", type=float, default=1.0, help="no card in view this long before the next scan")
ap.add_argument("--token-file", default=None,
                help="where to write the brain token (default table/.brain-token); a second server, e.g. a "
                     "test run, needs its own so it doesn't lock the live game's brain out")
args = ap.parse_args()

# CLOUD mode (Linux container, e.g. Cloudflare Containers): no Apple Vision, no local Whisper/Gemma/`say`.
# Card scanning and the open mic are off until their cloud backends exist; everything else works.
CLOUD = os.environ.get("TABLE_CLOUD") == "1" or sys.platform != "darwin"
if CLOUD:
    def read_lines(*_a, **_k):                     # no OCR backend in the cloud yet: scanning finds nothing
        return []
else:
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
# ── the virtual AI seats: one VirtualPlayer per --ai-deck ──────────────────────────────────────
# Most code was written for one AI and reads the global VP. VP is a proxy for the seat the current
# request acts for (set with `acting(name)`, per thread), defaulting to the first seat, so a
# one-AI table behaves exactly as before and a two-AI table routes each action to its own seat.
VPS: dict = {}
DECK_OF: dict[str, str] = {}
if args.ai_deck:
    from player import VirtualPlayer
    if len(args.ai_deck) > len(AI_PLAYERS):
        sys.exit("more --ai-deck than --ai players: each deck belongs to an --ai, in order")
    _online = {}
    if args.fair_seed == "online" and not args.restore:
        import fair as _fair
        _online = _fair.online_parts()
        print(f"fair seed: {sorted(_online)}", flush=True)
    for _i, _deck in enumerate(args.ai_deck):
        _n = AI_PLAYERS[_i]["name"]
        VPS[_n] = VirtualPlayer(_deck, _n, **({} if args.fair_seed == "off" or args.restore else
                                              {"fair_words": {}, "fair_online": _online or False}))
        DECK_OF[_n] = _deck
        AI_PLAYERS[_i]["has_deck"] = True
        AI_PLAYERS[_i]["deck"] = VPS[_n].deck_name
# Pilot seats: virtual decks played by a person (from /hand, with their seat key), not by an AI brain
PILOTS: set = set()
for _p in args.pilot:
    _n = next((n for n in VPS if n.lower() == _p.strip().lower()), None)
    if not _n:
        sys.exit(f"--pilot {_p!r}: not an --ai seat with an --ai-deck (seats with decks: {list(VPS)})")
    PILOTS.add(_n)
_SEAT = threading.local()


class _SeatProxy:
    def _vp(self):
        n = getattr(_SEAT, "name", None)
        return VPS[n] if n in VPS else next(iter(VPS.values()))

    def __getattr__(self, a):
        return getattr(self._vp(), a)

    def __setattr__(self, a, v):
        setattr(self._vp(), a, v)

    def __bool__(self):
        return bool(VPS)


VP = _SeatProxy() if VPS else None


def seat(name) -> str | None:
    """The canonical seat name for name (any case), the first seat for None, or None if unknown."""
    if not VPS:
        return None
    if not name:
        return next(iter(VPS))
    return next((n for n in VPS if n.lower() == str(name).strip().lower()), None)


@contextmanager
def acting(name):
    prev = getattr(_SEAT, "name", None)
    _SEAT.name = name
    try:
        yield
    finally:
        _SEAT.name = prev


FAIR = {"game": time.strftime("%Y%m%d-%H%M%S"), "words": {}, "revealed": False}


def reshuffle_all():
    """A new game for every AI seat. With fair seeding, each shuffle is sealed with the players' secret
    words collected since the last game (POST /api/fair/word), and the words are then cleared."""
    from player import VirtualPlayer
    words = dict(FAIR["words"])
    FAIR.update(game=time.strftime("%Y%m%d-%H%M%S"), words={}, revealed=False)
    online = {}
    if args.fair_seed == "online":
        import fair
        online = fair.online_parts()                  # once per game, shared by every seat
    for n in list(VPS):
        VPS[n] = VirtualPlayer(DECK_OF[n], n, **({} if args.fair_seed == "off" else
                                                 {"fair_words": words, "fair_online": online or False}))
    save_fair()


def save_fair():
    import fair
    for v in VPS.values():
        if v.fair:
            fair.save(v.fair, FAIR["game"])


def fair_public() -> dict:
    import fair
    seats = {n: (fair.public(v.fair) if v.fair else None) for n, v in VPS.items()}
    out = {"game": FAIR["game"], "seats": seats, "words_in": sorted(FAIR["words"]), "revealed": FAIR["revealed"]}
    if FAIR["revealed"]:
        out["records"] = {n: v.fair for n, v in VPS.items() if v.fair}
    return out


def fair_announcement() -> list[str]:
    import fair
    return [f"{n}'s deck is sealed with {fair.sources(v.fair)}. Fingerprint {v.fair['fingerprint']}."
            for n, v in VPS.items() if v.fair]


save_fair()
VP_LOCK = threading.RLock()     # re-entrant: brain actions call helpers that lock again
BRAIN_EXTERNAL = args.brain == "external"

# ── public event stream: what the table has seen and heard (never the AI's hand) ─────────────
EVENTS: list[dict] = []
EV_LOCK = threading.Lock()
EV_COND = threading.Condition(EV_LOCK)               # /api/events?wait=N holds a request until emit() wakes it (like core.Events)
_ev_id = [0]

# ── turn structure: NEXT walks a human's turn step by step; each step opens a priority window ──
STEPS = ["untap", "upkeep", "draw", "main 1", "beginning of combat", "declare attackers",
         "declare blockers", "combat damage", "main 2", "end step", "cleanup"]
NO_PRIORITY = {"untap", "cleanup"}                    # the rules give no one priority here
ORDER: list[str] = [x.strip() for x in args.order.split(",") if x.strip()]
PHASE = {"player": None, "step": 0, "begun": False}   # begun: an AI seat has started playing its turn
PRIORITY = {"step": None, "waiting": [], "seats": [], "deadline": 0.0, "beat_until": 0.0}
BEAT = tuple(float(x) for x in args.priority_beat.split(","))
WINDOWS = {"on": True}
HOLD = {"on": False, "by": ""}  # the table freezes NEXT while it sorts something out          # the table can switch step timeouts off: NEXT never waits (AIs still hear every window)
if args.priority_window > 0:                          # fixed windows: the beat IS the window, and the deadline
    BEAT = (args.priority_window, args.priority_window)
    args.priority_secs = args.priority_window
PHASE_LOCK = VP_LOCK   # ONE lock: two took in opposite orders (phase→AI vs AI→phase) deadlocked game 3
HAND_N: dict[str, int] = {}                            # humans' hand sizes (public), set from the stage page


# Humans' boards are physical cards: the table only knows them from photos and what people say. The
# brain keeps them here (POST /api/public-board) so the 3D board can show everyone's battlefield.
PUBLIC_BOARD: dict[str, dict] = {}
# What the physical table has to do to match the game (the brain posts them: "Sam: move Hunted Horror to your
# graveyard"); anyone can tick one off. Public, like everything on the table.
TODOS: list[dict] = []
PLACED: set = set()             # milled cards the table has physically moved: "<log seq>:<index>", from the game log


def split_cards(text: str) -> list[str]:
    """'Decimate, Toski, Bearer of Secrets, Forest' → the card names, keeping names that contain a comma
    whole (checked against the card file)."""
    import oracle
    parts, out, i = [p.strip() for p in text.split(", ")], [], 0
    while i < len(parts):
        for j in range(min(len(parts), i + 3), i, -1):     # the longest run of parts that is a real card name
            cand = ", ".join(parts[i:j])
            if j == i + 1 or oracle.card(cand):
                out.append(cand); i = j; break
    return out


def _card_view(name: str | None, **kw) -> dict:
    """One card as the 3D board draws it. name None = face down: nothing about it but its back."""
    import oracle
    if name is None:
        return {"name": None, "face_down": True, **kw}
    c = oracle.card(name) or {}
    printed = f"{c['power']}/{c['toughness']}" if c.get("power") is not None else None
    if any(t in (c.get("type") or "") for t in ("Spacecraft", "Vehicle")):
        printed = None                                # not a creature until stationed / crewed: no P/T unless told
    pt = kw.pop("pt", None) or printed
    return {"name": name, "cost": c.get("cost", ""), "type": c.get("type") or kw.pop("type", ""),
            "text": (c.get("text") or "")[:400], "pt": pt, **kw}


def board3d() -> dict:
    """Every seat's battlefield, command zone and graveyard — public information only. AI seats come from
    the engine (face-down cards stay nameless); human seats from PUBLIC_BOARD."""
    seats = []
    lt = life_table()
    for h in HUMANS:
        b = PUBLIC_BOARD.get(h["name"], {})
        perms = [_card_view(p.get("name") if not p.get("face_down") else None,
                            **{k: p[k] for k in ("tapped", "token", "counters", "note", "pt", "zone") if k in p})
                 for p in b.get("permanents", [])]
        seats.append({"name": h["name"], "kind": "human", "commander": h["commander"],
                      "commander_card": _card_view(h["commander"]) if h.get("commander") else None, "life": lt.get(h["name"]),
                      "hand": HAND_N.get(h["name"], 7), "commander_in_zone": not b.get("commander_out", False),
                      "permanents": perms, "graveyard": [str(x)[:60] for x in b.get("graveyard", [])][-100:],
                      "graveyard_count": len(b.get("graveyard", [])),
                      "updated": b.get("updated")})
    with VP_LOCK:
        for n, v in VPS.items():
            perms = []
            for p in v.battlefield:
                pw, tg = v.stats(p) if p.is_("Creature") or p.face_down else (None, None)
                pt = f"{pw}/{tg}" if pw is not None else None
                if p.face_down:
                    how = getattr(p, "how", None) or "not recorded"   # the engine records it from cast/manifest/cloak
                    perms.append(_card_view(None, id=p.id, tapped=p.tapped, pt=pt, ward=p.ward2, how=how))
                else:
                    perms.append(_card_view(p.name, id=p.id, tapped=p.tapped, token=p.token, counters=p.counters,
                                            attached_to=p.attached_to, role=p.role, pt=pt,
                                            type=p.card.get("type") or " ".join(p.types)))
            seats.append({"name": n, "kind": "ai", "commander": v.commander.get("name"),
                          "commander_card": _card_view(v.commander.get("name")), "life": v.life,
                          "hand": len(v.hand), "library": len(v.library), "commander_in_zone": v.cmdr_in_zone,
                          "commander_tax": 2 * v.cmdr_casts, "permanents": perms,
                          "graveyard": [c["name"] for c in v.graveyard][-100:], "graveyard_count": len(v.graveyard)})
    for x in seats:
        if is_out(x["name"]):                         # they've lost: every card they own has left the game
            x.update(out=True, permanents=[], graveyard=[], graveyard_count=0, commander_in_zone=False, hand=0)
    order = turn_order()
    seats.sort(key=lambda x: order.index(x["name"]) if x["name"] in order else 99)
    return {"seats": seats, "turn": PHASE.get("player"), "step": STEPS[PHASE["step"]]}


def is_out(name: str) -> bool:
    """A player at 0 life or less has lost: their cards leave the game and their turns are skipped."""
    lt = life_table()
    return name in lt and lt[name] is not None and lt[name] <= 0


def seat_after(name: str, step: int = 1) -> str | None:
    """The next (or, step=-1, previous) seat in turn order that is still in the game."""
    order = turn_order()
    if name not in order:
        alive = [n for n in order if not is_out(n)]
        return alive[0] if alive else None
    i = order.index(name)
    for k in range(1, len(order) + 1):
        n = order[(i + step * k) % len(order)]
        if not is_out(n):
            return n
    return None


def turn_order() -> list[str]:
    names = [h["name"] for h in HUMANS] + list(VPS)
    return [n for n in ORDER if n in names] + [n for n in names if n not in ORDER]


ROOM_ADOPTED = [False]    # cloud rooms: True once this process holds the room's game (restored, or adopted as new)


def snapshot():
    """The whole game, pickled next to the research logs after every change: a crash or a restart
    (new code) resumes from here with --restore. Local only — it holds the AI's hands and libraries."""
    import pickle
    try:
        with VP_LOCK, PHASE_LOCK:
            blob = pickle.dumps({"VPS": VPS, "DECK_OF": DECK_OF, "LIFE": LIFE, "FAIR": FAIR, "PHASE": PHASE,
                                 "ORDER": turn_order(), "EVENTS": list(EVENTS[-1500:]), "HAND_N": HAND_N, "PUBLIC_BOARD": PUBLIC_BOARD, "WINDOWS": WINDOWS, "TODOS": TODOS, "PLACED": sorted(PLACED),
                                 "HIGHROLL": HIGHROLL, "SEAT_KEYS": SEAT_KEYS, "SEAT_DEVICES": SEAT_DEVICES, "AUTOPASS": AUTOPASS, "SEAT_PROXY": SEAT_PROXY, "SEAT_INVITES": SEAT_INVITES, "ev_id": _ev_id[0], "saved": time.time()})
        p = research_dir() / "snapshot.pkl"
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(blob)
        os.chmod(tmp, 0o600)
        tmp.replace(p)
    except Exception as e:                            # never let saving break the table
        print(f"snapshot failed: {e}", flush=True)


def restore(path: str):
    import pickle
    import player
    d = pickle.loads(Path(path).read_bytes())
    VPS.clear(); VPS.update(d["VPS"]); DECK_OF.update(d["DECK_OF"])
    LIFE.update(d["LIFE"]); FAIR.update(d["FAIR"]); PHASE.update(d["PHASE"]); HAND_N.update(d.get("HAND_N", {}))
    PUBLIC_BOARD.update(d.get("PUBLIC_BOARD", {})); WINDOWS.update(d.get("WINDOWS", {})); TODOS.extend(d.get("TODOS", [])); PLACED.update(d.get("PLACED", []))
    HIGHROLL.update(d.get("HIGHROLL") or {"mode": None})
    SEAT_KEYS.update(d.get("SEAT_KEYS") or {})
    SEAT_DEVICES.update(d.get("SEAT_DEVICES") or {})
    AUTOPASS.update(d.get("AUTOPASS") or {})
    SEAT_PROXY.update(d.get("SEAT_PROXY") or {})
    SEAT_INVITES.update(d.get("SEAT_INVITES") or {})
    if d.get("ORDER") and (not ORDER or HIGHROLL.get("winner")):
        ORDER[:] = d["ORDER"]                         # a high roll's order beats the --order the table was started with
    EVENTS.extend(d.get("EVENTS") or [])              # the visible log survives a restart or a sleep (it did not until 2026-10-07)
    _ev_id[0] = max(d.get("ev_id", 0), max((e["id"] for e in EVENTS), default=0))   # pages keep their cursors across the restart
    top = max([p.id for v in VPS.values() for p in v.battlefield] + [0])
    import itertools
    player._ids = itertools.count(top + 1)            # new permanents never reuse a restored id
    print(f"restored {path}: seats {list(VPS)}, life {life_table()}, turn {PHASE}", flush=True)


def instant_speed(vp) -> list[str]:
    """Spells this seat could cast right now outside its own turn: instants and flash."""
    out = []
    for _label, c, _pay, is_cmdr in vp.castable():
        if is_cmdr:
            continue
        if "Instant" in (c.get("types") or []) or "Flash" in (c.get("keywords") or []):
            out.append(c["name"])
    return out


def open_priority(step: str) -> list[str]:
    """Every AI seat other than the active player gets the same window, worded the same way, and every
    window lasts at least a random beat. Which seats could actually respond (hold a castable instant or
    flash) is known only to the server: the table can't tell a seat with nothing from a seat that
    thought and passed. A seat with nothing passes on its own; one that holds something is waited on
    until it answers or the window runs out — a long pause is a tell, as it is for a person."""
    active = PHASE["player"]
    seats, holders = [], []
    if step not in NO_PRIORITY:
        with VP_LOCK:
            for n, v in VPS.items():
                if n != active and not is_out(n):
                    seats.append(n)
                    if instant_speed(v):
                        holders.append(n)
    now = time.time()
    if not WINDOWS["on"]:                             # timeouts off: everyone still hears the window, nothing waits
        for n in seats:
            emit("attention", kind="priority", addressee=n,
                 text=f"{active}: {step}. You may cast an instant now, or pass.", step=step, active=active)
        PRIORITY.update(step=step, waiting=[], seats=[], deadline=0.0, beat_until=0.0)
        return seats
    beat = secrets.SystemRandom().uniform(*BEAT) if seats else 0.0
    PRIORITY.update(step=step, waiting=holders, seats=seats, deadline=now + args.priority_secs, beat_until=now + beat)
    for n in seats:
        emit("attention", kind="priority", addressee=n,
             text=f"{active}: {step}. You may cast an instant now, or pass.", step=step, active=active)
    if holders:                                       # private: for the research record, never the event stream
        _append("brain.jsonl", {"ts": round(now, 2), "priority": step, "holders": holders})
    return seats


def priority_done(seat_name: str):
    """A seat answered (cast or pass). With fixed windows the window still runs to its end: an early
    answer must look exactly like no answer."""
    with PHASE_LOCK:
        if seat_name in PRIORITY["waiting"]:
            PRIORITY["waiting"].remove(seat_name)


def priority_open() -> bool:
    """The window, as the table sees it: open during the beat, and after it while a holder thinks."""
    now = time.time()
    return bool(PRIORITY["seats"]) and (now < PRIORITY["beat_until"] or
                                        (bool(PRIORITY["waiting"]) and now < PRIORITY["deadline"]))


PASS = {"key": None, "passed": []}   # who has passed priority this step (humans; the AIs answer their window)


def passes_needed() -> list[str]:
    """The people who must pass before a person's step ends, in turn order from the active player
    (rule 117.3: priority goes round in turn order; the step ends once everyone passes in a row).
    Untap and cleanup give no one priority, so only the active player moves those on."""
    active = PHASE["player"]
    if active is None:
        return []
    order = turn_order()
    i = order.index(active) if active in order else 0
    if active in VPS:                                 # an AI's turn: everyone else gets every step's round
        if STEPS[PHASE["step"]] in NO_PRIORITY:
            return []
        return [n for n in order[i + 1:] + order[:i] if not is_out(n)]
    if STEPS[PHASE["step"]] in NO_PRIORITY:
        return [active]
    return [n for n in order[i:] + order[:i] if not is_out(n)]   # people AND AI seats, in turn order


HUMAN_PASS_SECS = max(0.0, float(args.human_pass_secs))   # see --human-pass-secs
AI_PASS_TIMEOUT = float(os.environ.get("TABLE_AI_PASS_SECS") or 30.0)   # an AI that never answers is passed for (logged), never a freeze; a model player thinks longer than 30 s, so the dogfood harness raises it


def passes_state() -> dict:
    key = (PHASE["player"], PHASE["step"])
    if PASS["key"] != key:                        # a new step (or BACK, or a new turn): nobody has passed yet
        PASS.update(key=key, passed=[], waiting_on=None, since=time.time())
    need = passes_needed()
    passed = [n for n in PASS["passed"] if n in need]
    nxt = next((n for n in need if n not in passed), None)
    if nxt != PASS.get("waiting_on"):
        PASS.update(waiting_on=nxt, since=time.time())
    elif nxt in VPS and time.time() - PASS.get("since", time.time()) > AI_PASS_TIMEOUT:
        PASS["passed"].append(nxt)                # an AI seat that didn't answer: pass for it, say so
        emit("pass", by=nxt, ai=True, timeout=True, player=PHASE["player"], step=STEPS[PHASE["step"]])
        _append("brain.jsonl", {"ts": round(time.time(), 2), "ai_pass_timeout": nxt, "step": STEPS[PHASE["step"]]})
        return passes_state()
    if HUMAN_PASS_SECS and nxt and nxt not in VPS and (HOLD["on"] or any(t.get("kind") == "question" and not t["done"] for t in TODOS)):
        PASS["since"] = time.time()               # a hold or an open question pauses the timer: the full time again afterwards
    if _human_timer_applies(nxt) and time.time() - PASS.get("since", time.time()) > HUMAN_PASS_SECS:
        PASS["passed"].append(nxt)                # a person who didn't pass in time: pass for them, and say so in the log
        emit("pass", by=nxt, human=True, timeout=True, player=PHASE["player"], step=STEPS[PHASE["step"]])
        return passes_state()
    out = {"need": need, "passed": passed, "next": nxt}
    if _human_timer_applies(nxt):                 # the page shows a countdown and goes red as it runs out
        out.update(deadline=round(PASS["since"] + HUMAN_PASS_SECS, 2), secs=HUMAN_PASS_SECS, now=round(time.time(), 2))
    return out


def _human_timer_applies(name) -> bool:
    """The pass timer runs for a PERSON whose turn it is to pass in someone else's turn. Never for the active player
    (their own turn is theirs to take), never while the table is on hold or has an open question, never before the
    game starts. PHASE_LOCK is held by the callers (or this is a read of a few plain values)."""
    return bool(HUMAN_PASS_SECS and name and name not in VPS and PHASE["player"] is not None and name != PHASE["player"]
                and not HOLD["on"] and not any(t.get("kind") == "question" and not t["done"] for t in TODOS))


def ai_passed(seat_name: str):
    """An AI seat answered its window (passed) — or responded and the round restarted. Count its pass;
    if that completes a person's step, the step ends just as when the last person passes."""
    with PHASE_LOCK:
        ps = passes_state()
        if seat_name not in ps["need"] or seat_name in ps["passed"]:
            return
        PASS["passed"].append(seat_name)
        ps = passes_state()
        emit("pass", by=seat_name, ai=True, player=PHASE["player"], step=STEPS[PHASE["step"]],
             left=[n for n in ps["need"] if n not in ps["passed"]])
        autopass_round()
        ps = passes_state()
        if not ps["next"] and PHASE["player"] is not None and PHASE["player"] not in VPS:
            finish_step(seat_name)


AUTOPASS: dict = {}   # person → "off" | "others" | "others-no-combat" (only what they chose; --autopass-default covers the rest)
AUTOPASS_DEFAULT = args.autopass_default
COMBAT_STEPS = ("beginning of combat", "declare attackers", "declare blockers", "combat damage")


def autopass_round() -> list[str]:
    """Pass for every person next in the round who switched auto-pass on — only in OTHER players'
    turns (never their own), and with "others-no-combat" not in combat. PHASE_LOCK is held."""
    done = []
    ps = passes_state()
    while ps["next"]:
        n = ps["next"]
        mode = AUTOPASS.get(n, AUTOPASS_DEFAULT)
        if n in VPS or mode == "off" or n == PHASE["player"] or (mode == "others-no-combat" and STEPS[PHASE["step"]] in COMBAT_STEPS):
            break                                       # (an AI seat answers its own window; never auto-pass for one here)
        PASS["passed"].append(n)
        done.append(n)
        ps = passes_state()
        emit("pass", by=n, auto=True, player=PHASE["player"], step=STEPS[PHASE["step"]],
             left=[x for x in ps["need"] if x not in ps["passed"]])
    return done


def priority_reset(why: str):
    """Something new happened (a spell, an ability): priority goes round again, from the active player."""
    with PHASE_LOCK:
        if PASS["passed"]:
            PASS["passed"] = []
            emit("pass", kind="reset", why=why[:120])
            step, active = STEPS[PHASE["step"]], PHASE["player"]
            for n in VPS:                             # the AIs answer the new round too
                if n != active and not is_out(n):
                    emit("attention", kind="priority", addressee=n,
                         text=f"{active}: {step}. Something new happened — you may respond, or pass.", step=step, active=active)


def untap_refusal(owner: str) -> str | None:
    """Why `owner` may not untap a permanent by hand right now, or None: only the ACTIVE player, in their own untap step (the
    start of the turn untaps everything; this is the manual one). Tapping is not checked: tap is legal whenever you hold
    priority. Used by /api/brain/untap (a pilot's key) and /api/card-action (a person's real deck)."""
    if PHASE["player"] is None:
        return "the game hasn't started: nothing untaps yet"
    if PHASE["player"] != owner:
        return f"you can only untap in your own untap step: it's {PHASE['player']}'s turn, not {owner}'s"
    if STEPS[PHASE["step"]] != "untap":
        return f"you can only untap in your untap step: {owner}'s turn is at {STEPS[PHASE['step']]} now"
    return None


PASS_LOCK = threading.Lock()                         # one pilot pass at a time, for the same reason


def pilot_pass_refusal(seat_name: str, b: dict) -> str | None:
    """Why a pilot's pass does not count right now, or None. The page names the step it was looking at (`player`, `step`): a
    pass for a step that has already moved on is stale and must not pass the NEXT one. Then the round's own rules, which people
    already obey in next_step: you pass once, in turn order, in a step you are in the round for, and never in your own turn.
    PHASE_LOCK is the caller's."""
    if PHASE["player"] is None:
        return "the game hasn't started"
    here = STEPS[PHASE["step"]]
    said_p, said_s = b.get("player"), b.get("step")
    if (said_p is not None and said_p != PHASE["player"]) or (said_s is not None and said_s != here):
        return f"stale pass: it was for {said_p or PHASE['player']}'s {said_s or here}, the table is at {PHASE['player']}'s {here}"
    if PHASE["player"] == seat_name:
        return "it's your own turn: there is nothing to pass; end it with End my turn"
    ps = passes_state()
    if seat_name not in ps["need"]:
        return f"you aren't in {here}'s priority round"
    if seat_name in ps["passed"]:
        return f"you already passed {here}: waiting on {ps['next']}"
    if ps["next"] != seat_name:
        return f"{ps['next']} passes first (turn order), then you"
    return None


BEGIN_LOCK = threading.Lock()                        # one begin at a time: a double tap must not untap and draw twice


def begin_refusal(seat_name: str, host: bool = False) -> str | None:
    """Why `begin` (untap, draw, reset the land drop) is not legal for this seat right now, or None. Only the ACTIVE seat,
    before main 1 (its own walk through upkeep and draw is a retry of the same begin), and once per turn. PHASE_LOCK/BEGIN_LOCK
    are the caller's. Before the game starts only the host's brain may begin (a solo engine game with no turn order, as
    brain_e2e plays); a pilot doing so would make itself the first player and skip the high roll."""
    if PHASE["player"] is None:
        return None if host else "the game hasn't started"
    if PHASE["player"] != seat_name:
        return f"it's {PHASE['player']}'s turn, not {seat_name}'s: begin is for the start of your own turn"
    if PHASE.get("begun"):
        return f"{seat_name} already began this turn"
    if PHASE["step"] >= STEPS.index("main 1"):
        return f"begin is for the start of the turn (untap); {seat_name}'s turn is already at {STEPS[PHASE['step']]}"
    return None


AI_STEP_OF = {"begin": "main 1", "attack": "declare attackers", "damage": "combat damage", "end": "end step"}
AI_MAIN_ACTIONS = {"land", "cast", "turn-up", "manifest", "put", "blink", "token"}
AI_OPENED: dict = {"key": None}                      # (player, step) whose round has been announced


def ai_advance(seat_name: str, action: str) -> str | None:
    """An AI's turn walks the same steps as a person's: before it acts, every step between here and the
    step that action belongs to opens its round (AI windows + each person passes). Returns None to go
    ahead, or who is still to pass — the AI retries until the round is done."""
    if PHASE["player"] != seat_name or not WINDOWS.get("ai_steps", True):
        return None
    target = AI_STEP_OF.get(action)
    cur = PHASE["step"]
    if action in AI_MAIN_ACTIONS and STEPS[cur] in ("beginning of combat", "declare attackers",
                                                     "declare blockers", "combat damage"):
        target = "main 2"
    if target is None:
        return None
    goal = STEPS.index(target)
    while PHASE["step"] < goal:
        step = STEPS[PHASE["step"]]
        if step not in NO_PRIORITY:
            key = (PHASE["player"], PHASE["step"])
            if AI_OPENED["key"] != key:               # announce the round once per step
                AI_OPENED["key"] = key
                open_priority(step)
            autopass_round()
            ps = passes_state()
            waiting = [n for n in ps["need"] if n not in ps["passed"]]
            if waiting or priority_open():
                return ", ".join(waiting + (list(PRIORITY["seats"]) if priority_open() else []))
        PRIORITY.update(waiting=[], seats=[])
        PHASE["step"] += 1
        emit("phase", player=PHASE["player"], step=STEPS[PHASE["step"]], index=PHASE["step"])
    if PHASE["step"] == goal and target in ("end step", "declare attackers") and STEPS[goal] not in NO_PRIORITY:
        key = (PHASE["player"], PHASE["step"])
        if target == "end step":                       # the end step's own round, before the turn passes on
            if AI_OPENED["key"] != key:
                AI_OPENED["key"] = key
                open_priority(STEPS[goal])
            autopass_round()
            ps = passes_state()
            waiting = [n for n in ps["need"] if n not in ps["passed"]]
            if waiting or priority_open():
                return ", ".join(waiting + (list(PRIORITY["seats"]) if priority_open() else []))
    return None


# What a pilot's key may do through /api/brain/<action> (the last path segment, so every alias is covered): exactly what
# /hand and the pilot mode of tablectl use. Everything else either makes a card or a permanent from nothing (draw, search,
# peek, topdeck, put, token, counter, animate, ...) or is the table host's (new-game, take, fair-reveal). The host's brain
# token keeps all of it. Checked BEFORE ai_advance: a refused action must not walk the turn on as a side effect.
PILOT_ACTIONS = frozenset({"say", "begin", "land", "cast", "turn-up", "tap", "untap", "attack", "damage", "block",
                           "pass", "end", "life"})


SEAT_KEYS: dict = {}   # person → [sha256 of each key a device of theirs was given]
SEAT_DEVICES: dict = {}   # person → [device fingerprints that claimed it]


def _keyhash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


SEAT_INVITES: dict = {}   # sha256(invite) → seat: an open-market link; the first device to open it takes the seat
SEAT_PROXY: dict = {}   # seat → the player who plays it tonight (e.g. {"Sam": "Michael"}): their key counts for it


def board_from_body(b: dict) -> tuple[list, list]:
    """A human seat's public board from a request: permanents (name, tapped, token, face_down, counters,
    note, pt, zone) and graveyard, trimmed to safe sizes. Shared by the referee's /api/public-board and a
    player's own /api/my-board."""
    def clean(p):
        p = p if isinstance(p, dict) else {"name": str(p)}
        out = {"name": " ".join(str(p.get("name", "")).split())[:80]}
        for k, t in (("tapped", bool), ("token", bool), ("face_down", bool)):
            if k in p:
                out[k] = t(p[k])
        if "counters" in p:
            try:
                out["counters"] = max(-99, min(999, int(p["counters"])))
            except (TypeError, ValueError):
                pass
        for k in ("note", "pt", "zone"):
            if p.get(k):
                out[k] = " ".join(str(p[k]).split())[:60]
        return out
    perms = [c for c in (clean(p) for p in (b.get("permanents") or [])[:120]) if c["name"]]
    grave = [" ".join(str(x).split())[:80] for x in (b.get("graveyard") or [])][:200]
    return perms, grave


def seat_deck_names(name: str) -> list[str]:
    """The card names a seat's deck can show on the table — a pilot's virtual deck, or the deck file whose commander is
    the person's commander — so a board photo is read against real candidates. Empty when unknown."""
    path = DECK_OF.get(name)
    commander = next((h["commander"] for h in HUMANS if h["name"] == name), None)
    if not path and commander:
        for f in sorted((HERE.parent / "decks").glob("*.json")):
            try:
                d = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            if commander in ({c.get("name") for c in d.get("commander") or []} | set(d.get("eligibleCommanders") or [])):
                path = str(f)
                break
    if not path:
        return []
    try:
        d = json.loads(Path(path if Path(path).is_absolute() else HERE.parent / path).read_text())
        return sorted({c["name"] for c in (d.get("mainBoard") or []) + (d.get("commander") or []) if c.get("name")})[:200]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def seat_key_ok(name: str, key: str) -> bool:
    if SEAT_PROXY.get(name) and SEAT_PROXY[name] != name and seat_key_ok(SEAT_PROXY[name], key):
        return True
    hs = SEAT_KEYS.get(name) or []
    if isinstance(hs, str):                           # an older snapshot stored one hash
        hs = SEAT_KEYS[name] = [hs]
    return bool(key) and any(secrets.compare_digest(h, _keyhash(key)) for h in hs)


HIGHROLL: dict = {"mode": None}   # who goes first: {"mode", "sides", "round", "contenders", "rolls", "winner", ...}


def highroll_public() -> dict:
    h = dict(HIGHROLL)
    if h.get("mode") == "physical" and not h.get("winner"):     # a die not yet rolled shows as waiting
        h["waiting_on"] = [n for n in h.get("contenders", []) if n not in h["rolls"].get(str(h["round"]), {})]
    return h


def highroll_start(mode: str, sides: int, by: str) -> tuple[int, dict]:
    """A high roll for who goes first. "physical": each player rolls a real die and enters it (a person
    rolls for an AI seat). "quantum": one fresh ANU quantum draw, and every roll derives from it by
    fair.die_roll — published, so anyone can recompute it. Ties re-roll among the tied players."""
    if HIGHROLL.get("winner"):                        # one roll per game: no re-rolling until a result suits
        return 409, {**highroll_public(), "error": f"already rolled — {HIGHROLL['winner']} goes first"}
    if PHASE["player"] is not None:
        return 409, {**highroll_public(), "error": "the game has already started — high roll is before the first turn"}
    if HIGHROLL.get("mode") == "physical":
        return 409, {**highroll_public(), "error": "a real-dice roll is under way — enter the dice"}
    if mode not in ("physical", "quantum") or not 2 <= sides <= 100:
        return 400, {"error": "mode is physical or quantum; sides 2-100"}
    names = [n for n in turn_order() if not is_out(n)]
    HIGHROLL.clear()
    HIGHROLL.update(mode=mode, sides=sides, round=1, contenders=names, rolls={}, winner=None, by=by[:30],
                    seating=names, started=round(time.time(), 2))
    if mode == "quantum":
        import fair
        parts = fair.online_parts()                  # ANU (rate-limited: may wait up to a minute) + drand
        if not parts.get("anu_qrng"):                 # ANU allows one request a minute: wait it out and try
            first = parts.get("anu_qrng_error")       # once more before settling for drand
            parts = fair.online_parts()
            parts.setdefault("anu_first_error", first)
        src = parts.get("anu_qrng") or parts.get("drand_randomness")
        if not src:
            HIGHROLL.clear(); HIGHROLL["mode"] = None
            return 503, {"error": "no quantum (ANU) or drand randomness reachable — use a physical roll"}
        HIGHROLL.update(entropy=src, source="ANU quantum (qrng.anu.edu.au)" if parts.get("anu_qrng")
                        else f"drand round {parts.get('drand_round')} (ANU unreachable twice: "
                             f"{parts.get('anu_first_error')}, {parts.get('anu_qrng_error')})",
                        formula="fair.die_roll(entropy, 'highroll|<round>|<name>', sides)")
        while not HIGHROLL["winner"]:
            r = str(HIGHROLL["round"])
            HIGHROLL["rolls"][r] = {n: fair.die_roll(src, f"highroll|{r}|{n}", sides) for n in HIGHROLL["contenders"]}
            _highroll_settle()
    emit("highroll", **{k: v for k, v in highroll_public().items() if k != "started_turn"})
    return 200, highroll_public()


def _highroll_settle():
    r = str(HIGHROLL["round"])
    got = HIGHROLL["rolls"].get(r, {})
    if any(n not in got for n in HIGHROLL["contenders"]):
        return
    top = max(got.values())
    best = [n for n in HIGHROLL["contenders"] if got[n] == top]
    if len(best) > 1:                                 # a tie: only the tied players roll again
        HIGHROLL.update(round=HIGHROLL["round"] + 1, contenders=best)
        return
    win = best[0]
    seats = HIGHROLL["seating"]
    i = seats.index(win)
    order = seats[i:] + seats[:i]                     # the winner goes first; play continues around the table
    ORDER[:] = order
    HIGHROLL.update(winner=win, order=order)
    _append("fair-highroll.jsonl", {k: v for k, v in HIGHROLL.items()})
    HIGHROLL["started_turn"] = True                   # the roll decides who's first, so start that turn now
    start_turn(win)                                   # (as START would; an AI winner begins playing)


def highroll_enter(name: str, value, by: str) -> tuple[int, dict]:
    if HIGHROLL.get("mode") != "physical" or HIGHROLL.get("winner"):
        return 409, {"error": "no physical high roll waiting for dice"}
    who = next((n for n in HIGHROLL["contenders"] if n.lower() == name.lower()), None)
    if not who:
        return 400, {"error": f"{name} isn't rolling this round (rolling: {', '.join(HIGHROLL['contenders'])})"}
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 400, {"error": "the roll is a number"}
    if not 1 <= v <= HIGHROLL["sides"]:
        return 400, {"error": f"a d{HIGHROLL['sides']} shows 1-{HIGHROLL['sides']}"}
    r = str(HIGHROLL["round"])
    if who in HIGHROLL["rolls"].get(r, {}):
        return 409, {"error": f"{who} already rolled this round"}
    HIGHROLL["rolls"].setdefault(r, {})[who] = v
    HIGHROLL.setdefault("entered_by", {}).setdefault(r, {})[who] = by[:30] or who
    _highroll_settle()
    emit("highroll", **highroll_public())
    return 200, highroll_public()


def phase_public() -> dict:
    with PHASE_LOCK:
        now, is_open = time.time(), priority_open()
        left = 0.0
        if is_open:
            left = PRIORITY["beat_until"] - now if now < PRIORITY["beat_until"] else PRIORITY["deadline"] - now
        return {"player": PHASE["player"], "step": STEPS[PHASE["step"]], "index": PHASE["step"], "steps": STEPS,
                "begun": bool(PHASE.get("begun")),
                "order": turn_order(), "waiting": list(PRIORITY["seats"]) if is_open else [],
                "seconds_left": round(max(0.0, left), 1), "ai": list(VPS),
                "windows": WINDOWS["on"], "window_secs": args.priority_window, "hold": HOLD["on"],
                "open_questions": sum(1 for t in TODOS if t.get("kind") == "question" and not t["done"]),
                "passes": passes_state(), "autopass": dict(AUTOPASS), "autopass_default": AUTOPASS_DEFAULT}


def start_turn(name: str):
    """Hand the turn to a seat: an AI seat plays its whole turn; a human starts at untap."""
    PHASE.update(player=name, step=0, begun=False)
    PRIORITY.update(step=None, waiting=[], seats=[], deadline=0.0, beat_until=0.0)
    emit("phase", player=name, step=STEPS[0], index=0)
    with VP_LOCK:                                     # Seedborn Muse: "untap all permanents you control during
        for n, v in VPS.items():                      # each other player's untap step"
            if n != name and any(p.name == "Seedborn Muse" and not p.face_down for p in v.battlefield):
                tapped = [p for p in v.battlefield if p.tapped]
                for p in tapped:
                    p.tapped = False
                if tapped:
                    emit("say", speaker=n, text=f"Seedborn Muse untaps my permanents during {name}'s untap step.",
                         action="untap", speech=f"Seedborn Muse untaps my permanents.")
    if name in VPS:
        if BRAIN_EXTERNAL:
            ev = emit("attention", kind="turn", text="(NEXT)", addressee=name)
            hold_the_floor(ev["id"], name)


def next_step(by: str | None = None, shared: bool = False, confirm: bool = False, key: str = "") -> tuple[int, dict]:
    """NEXT: move the active human's turn on one step. Refused while an AI still holds priority
    (until its window times out); the last step passes the turn to the next seat in order."""
    person = next((h["name"] for h in HUMANS if by and h["name"].lower() == by.lower()), None)
    pilot = next((n for n in PILOTS if by and n.lower() == by.lower()), None)
    if not person and pilot and seat_key_ok(pilot, key) and PHASE["player"] is None:
        with PHASE_LOCK:                              # a virtual-deck seat may START the game (its turns run from /hand)
            if PHASE["player"] is None:
                start_turn(turn_order()[0])
            return 200, phase_public()
    if not person and pilot and seat_key_ok(pilot, key):
        # A virtual-deck seat passes and ends its turns with the buttons on /hand. NEXT used to answer need_seat here, so the page
        # opened the claim popup, reloaded, and the next press failed the same way: an endless loop for the person playing it.
        return 403, {**phase_public(), "error": f"{pilot} plays a virtual deck: pass and end your turn with the buttons on /hand, not NEXT"}   # 403, no need_seat
    if not person or not seat_key_ok(person, key):    # only a device that claimed this seat passes for it
        return 403, {**phase_public(), "error": ("claim your seat first (👤 at the top)" if not person or person not in SEAT_KEYS
                                                 else f"this device isn't {person}'s — claim your own seat (👤)"),
                     "need_seat": True}
    by = person
    with PHASE_LOCK:
        if PHASE["player"] is None:
            start_turn(turn_order()[0])
            return 200, phase_public()
        if PHASE["player"] in VPS:                   # an AI's turn: NEXT is the person passing this step's round
            ps = passes_state()
            who = next((n for n in ps["need"] if n.lower() == by.lower()), None)
            if not ps["next"]:
                return 409, {**phase_public(), "error": f"it's {PHASE['player']}'s turn — it moves on when it's ready"}
            if not who:
                return 409, {**phase_public(), "error": f"{by} isn't in this step's priority round"}
            if who in ps["passed"]:
                return 409, {**phase_public(), "error": f"{who} already passed — waiting on {ps['next']}"}
            if who != ps["next"]:
                return 409, {**phase_public(), "error": f"{ps['next']} passes first (turn order), then {who}"}
            PASS["passed"].append(who)
            left = [n for n in ps["need"] if n not in PASS["passed"]]
            emit("pass", by=who, player=PHASE["player"], step=STEPS[PHASE["step"]], left=left)
            autopass_round()
            return 200, phase_public()
        if HOLD["on"]:
            return 409, {**phase_public(), "error": f"on hold ({HOLD['by'] or 'the table'}) — release ⏸ Hold to go on"}
        q = next((t for t in TODOS if t.get("kind") == "question" and not t["done"]), None)
        if q:                                         # the table must settle what a player asked first
            return 409, {**phase_public(), "error": f"waiting on {q.get('ask')}'s question: {q['text'][:90]}"}
        ps = passes_state()
        if ps["next"]:                                # people still to pass: this press is one of them passing
            who = next((n for n in ps["need"] if n.lower() == by.lower()), None)
            if who is None:
                return 409, {**phase_public(), "error": f"{by} isn't in this step's priority round"}
            if who in ps["passed"]:
                return 409, {**phase_public(), "error": f"{who} already passed — waiting on {ps['next']}"}
            if who != ps["next"]:
                return 409, {**phase_public(), "error": f"{ps['next']} passes first (turn order), then {who}"}
            PASS["passed"].append(who)
            left = [n for n in ps["need"] if n not in PASS["passed"]]
            emit("pass", by=who, player=PHASE["player"], step=STEPS[PHASE["step"]], left=left)
            autopass_round()
            left = [n for n in ps["need"] if n not in PASS["passed"]]
            if left:
                return 200, phase_public()
        return finish_step(by)


def finish_step(by: str | None) -> tuple[int, dict]:
    """Everyone has passed in a person's step: end it (unless held, a question is open, or a timed AI
    window is still running). PHASE_LOCK is held by the caller."""
    if True:
        if HOLD["on"]:
            return 409, {**phase_public(), "error": f"on hold ({HOLD['by'] or 'the table'}) — release ⏸ Hold to go on"}
        q = next((t for t in TODOS if t.get("kind") == "question" and not t["done"]), None)
        if q:
            return 409, {**phase_public(), "error": f"waiting on {q.get('ask')}'s question: {q['text'][:90]}"}
        if priority_open():
            return 409, {**phase_public(), "error": "priority: waiting on " + ", ".join(PRIORITY["seats"])}
        if PRIORITY["waiting"]:                       # a holder's window ran out: it passes (said privately —
            _append("brain.jsonl", {"ts": round(time.time(), 2),   # naming it would say it held something)
                                    "priority_timeout": PRIORITY["step"], "seats": PRIORITY["waiting"]})
        PRIORITY.update(waiting=[], seats=[])
        if PHASE["step"] >= len(STEPS) - 1:
            order = turn_order()
            nxt = seat_after(PHASE["player"]) or order[0]   # players who are out are skipped
            emit("phase", player=PHASE["player"], step="turn over", index=len(STEPS))
            start_turn(nxt)
            return 200, phase_public()
        PHASE["step"] += 1
        step = STEPS[PHASE["step"]]
        emit("phase", player=PHASE["player"], step=step, index=PHASE["step"], by=by)
        open_priority(step)
        return 200, phase_public()


def step_logged() -> bool:
    """Has the active player already DONE something in the step the table is at (a land or any card on their board, a spell,
    an attack)? BACK can undo a NEXT pressed too soon; it can not undo the play. Looks at the visible log after the event that
    opened this step; an unknown start (the log was cut) counts as nothing logged."""
    cur, idx = PHASE["player"], PHASE["step"]
    with EV_LOCK:
        evs = list(EVENTS)
    since = []
    for e in reversed(evs):
        if e.get("type") == "phase" and not e.get("kind") and e.get("player") == cur and e.get("index") == idx:
            break
        since.append(e)
    else:
        return False
    for e in since:
        t = e.get("type")
        if t == "declare" and e.get("by") == cur:
            return True
        if t == "board3d" and cur in (e.get("owner"), e.get("seat")):
            return True
        if t == "chat" and e.get("by") == cur and not e.get("talk"):
            return True
        if t == "say" and e.get("speaker") == cur and e.get("action") in ("land", "cast", "attack", "turn-up", "manifest"):
            return True
    return False


def back_refusal(key: str) -> str | None:
    """A remote room: who may press BACK. The active player (their key), one step within their own turn, and not once they have
    done something in that step. Going back into the previous player's turn is the host's. PHASE_LOCK is the caller's."""
    cur = PHASE["player"]
    if cur is None:
        return None                                    # prev_step says the game hasn't started
    if not seat_key_ok(cur, key):
        return f"only {cur}, whose turn it is, can go back a step: ask them (or the table's host)"
    if cur in VPS or PHASE["step"] == 0:
        return "going back into the previous player's turn takes the table's host, not a player's key"
    if step_logged():
        return f"something was already logged in {STEPS[PHASE['step']]} (a land, a spell or an attack): BACK can't undo that; ask the table's host"
    return None


def prev_step(by: str | None = None) -> tuple[int, dict]:
    """BACK, for a NEXT pressed too soon: one step back within a person's turn (no priority window
    reopens), or from the very start of a turn back to the previous player's cleanup — but never into an
    AI's turn, and never once an AI has started its own (its moves are already made)."""
    with PHASE_LOCK:
        cur = PHASE["player"]
        if cur is None:
            return 409, {**phase_public(), "error": "the game hasn't started"}
        if cur in VPS and PHASE.get("begun"):
            return 409, {**phase_public(), "error": f"{cur} has already started its turn — too late to go back"}
        if PHASE["step"] > 0 and cur not in VPS:
            PHASE["step"] -= 1
            PRIORITY.update(waiting=[], seats=[], beat_until=0.0, deadline=0.0)
            emit("phase", player=cur, step=STEPS[PHASE["step"]], index=PHASE["step"], by=by, back=True)
            return 200, phase_public()
        order = turn_order()
        prev = seat_after(cur, -1) if cur in order else None
        if prev is None or prev in VPS:
            return 409, {**phase_public(), "error": f"can't go back into {prev}'s turn — it already played it"}
        PHASE.update(player=prev, step=len(STEPS) - 1, begun=False)
        PRIORITY.update(waiting=[], seats=[], beat_until=0.0, deadline=0.0)
        if cur in VPS:                                # tell that seat's brain its turn was taken back
            emit("attention", kind="turn-cancelled", addressee=cur, text=f"Not yet — {prev}'s turn isn't over.")
        emit("phase", player=prev, step=STEPS[-1], index=len(STEPS) - 1, by=by, back=True)
        return 200, phase_public()


RESEARCH = Path(os.environ.get("TABLE_RESEARCH_DIR") or HERE / ".cache" / "research")   # every game, kept for
# research (gitignored, local only). Test servers point TABLE_RESEARCH_DIR at a temp dir so simulated games
# never land in the real research data.


def research_dir() -> Path:
    game = globals().get("FAIR", {}).get("game") or time.strftime("%Y%m%d-%H%M%S")
    d = RESEARCH / game
    if not d.exists():
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)                            # private: brain.jsonl and snapshots hold the AI's hands
    return d


_BRAIN_MARKED: set = set()


def _append(name: str, rec: dict):
    try:
        d = research_dir()
        with open(d / name, "a") as fh:
            if name == "brain.jsonl" and d not in _BRAIN_MARKED:
                _BRAIN_MARKED.add(d)                  # tells table/audit.py this log records refusals too, so zero
                fh.write(json.dumps({"ts": round(time.time(), 2), "meta": "refusals-logged"}) + "\n")   # means zero
            fh.write(json.dumps(rec, default=str) + "\n")
    except OSError as e:                              # logging must never stop the table
        print(f"research log failed: {e}", flush=True)


def emit(event_type: str, **fields) -> dict:
    # (not "kind": heard/attention events carry their own "kind" field)
    with EV_LOCK:
        _ev_id[0] += 1
        e = {**fields, "id": _ev_id[0], "ts": round(time.time(), 2), "type": event_type}   # a field can never
        # overwrite the event's own id/ts/type (a todo's "id" once did: every page's cursor jumped back)
        EVENTS.append(e)
        del EVENTS[:-3000]
        EV_COND.notify_all()
        _append("events.jsonl", e)                    # the public stream, persisted
    if event_type == "attention" and fields.get("kind") == "attacked":
        journal_due(fields.get("addressee"), "attacked")
    elif event_type == "life" and isinstance(fields.get("delta"), int) and fields["delta"] < 0 \
            and isinstance(fields.get("life"), int) and fields["life"] <= 0:
        journal_due(fields.get("player"), "eliminated")
    return e


def _read_research(name: str) -> list[dict]:
    try:
        return [json.loads(l) for l in open(research_dir() / name) if l.strip()]
    except OSError:
        return []


import journal as journal_mod                         # in-game journals (docs/RESEARCH-ENGINE-GOAL.md R1)
GAME_OVER: dict = {"over": False, "order": [], "winner": None}
JOURNALS = journal_mod.Journal(lambda: globals().get("FAIR", {}).get("game") or "", _append, _read_research)


def journal_due(seat_name, moment: str):
    """Ask an AI seat for a journal entry at a fixed moment. Its own event type (not "attention"), so no page
    pops an alert for it; the brain finds it with `tablectl journal-due`."""
    if seat_name in VPS and JOURNALS.fire(seat_name, moment):
        emit("journal-due", seat=seat_name, moment=moment)


IDENTITY: dict = {}                                    # seat -> what the seat declared about itself (docs/RESEARCH-ENGINE-GOAL.md R3)
ID_FIELDS = ("model", "provider", "prompt_version", "notes")


def identity_set(seat_name: str, b: dict) -> tuple[int, dict]:
    """A seat declares which model it is, so every record can pin it. The server cannot know what an external brain
    is, and 'Grok' alone is useless six months later (docs/arena-vision.md). Written whole to identity.json."""
    rec = {}
    for k in ID_FIELDS:
        v = b.get(k)
        if v is not None:
            if not isinstance(v, str) or len(v) > 120:
                return 400, {"error": f"{k} is a string of at most 120 characters"}
            rec[k] = v.strip()
    if not rec.get("model"):
        return 400, {"error": "model is required: the exact model string, e.g. the API id"}
    try:
        from build_info import build_sha
        harness = build_sha()
    except Exception:                                    # noqa: BLE001
        harness = "dev"
    IDENTITY[seat_name] = {**rec, "declared_ts": round(time.time(), 2)}
    try:
        with open(research_dir() / "identity.json", "w") as fh:
            json.dump({"harness": harness, "seats": IDENTITY}, fh, indent=1)
    except OSError as e:
        print(f"identity log failed: {e}", flush=True)
    return 200, {"seat": seat_name, **IDENTITY[seat_name], "harness": harness}


def journal_reset():
    IDENTITY.clear()
    JOURNALS.reset()
    GAME_OVER.update(over=False, order=[], winner=None)


LIFE: dict[str, int] = {}             # filled once the human players are parsed, below
REPLIES = os.environ.get("REPLIES", "ollama")    # "ollama": in-character via local Gemma; "template"
# ROUTER=code + REPLIES=template: no local model at all — the brain (Claude) makes every judgment.
NO_GEMMA = os.environ.get("ROUTER", "ollama") == "code" and REPLIES == "template"
if NO_GEMMA and not BRAIN_EXTERNAL:
    sys.exit("ROUTER=code needs --brain external: with no local model, the brain makes every decision")
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


# State-changing requests from another device need a seat key (Handler._seat_guard): path → (the body field naming
# the acting seat, OWN). OWN: that seat must be the key's seat (your life, your hand count, your fair word, your
# attacks, your words, your cards). Otherwise: any seated player, recorded under their own name. On in cloud rooms;
# on a home network set STRICT_SEATS=1. Pages send the key automatically (assets/seat.js adds X-Seat-Key).
REMOTE_SEAT_POSTS = {
    "/api/life": ("player", True), "/api/stage/hand": ("player", True), "/api/fair/word": ("player", True),
    "/api/declare/attack": ("by", True), "/api/chat": ("by", True), "/api/card-action": ("seat", True),
    "/api/highroll/start": ("by", False), "/api/highroll/roll": ("by", False),
    "/api/phase/back": ("by", False), "/api/phase/hold": ("by", False), "/api/phase/windows": ("by", False),
    "/api/todo/answer": ("by", False), "/api/todo/done": ("by", False), "/api/placed": ("by", False),
}
STRICT_SEATS = CLOUD or os.environ.get("STRICT_SEATS") == "1"
# What another device on the LAN may reach: the player view and public table state. Nothing that
# reveals or changes the AI's cards, resets the game, or feeds the mic/camera.
LAN_OK = {("GET", "/me"), ("GET", "/api/events"), ("GET", "/api/life"), ("GET", "/api/fair"),
          ("GET", "/api/fair/verify"), ("GET", "/api/voice-config"), ("GET", "/api/card"),
          ("POST", "/api/fair/word"), ("POST", "/api/life"), ("POST", "/api/seat/check"),
          ("GET", "/api/phase"), ("GET", "/api/checklist"), ("GET", "/api/seat/claims"), ("POST", "/api/seat/claim"), ("POST", "/api/seat/handoff"), ("POST", "/api/autopass"), ("GET", "/api/highroll"), ("POST", "/api/highroll/start"), ("POST", "/api/highroll/roll"), ("POST", "/api/phase/next"), ("POST", "/api/phase/back"), ("POST", "/api/phase/windows"), ("POST", "/api/phase/hold"), ("GET", "/api/todos"), ("POST", "/api/todo/done"), ("POST", "/api/todo/answer"), ("POST", "/api/declare/attack"), ("POST", "/api/my-board"), ("POST", "/api/openmic/rate"), ("POST", "/api/chat"), ("POST", "/api/chat/photo"), ("POST", "/api/card-action"), ("GET", "/api/history"), ("GET", "/log"), ("GET", "/api/placed"), ("POST", "/api/placed"), ("GET", "/xr"), ("GET", "/api/board3d"),
          ("GET", "/table"), ("GET", "/api/ai/state"), ("GET", "/board"),     # the table page as a viewer: its mic, camera, reset
                                                          # and AI-turn controls POST to routes still local-only
          ("GET", "/stage"), ("GET", "/api/stage"), ("POST", "/api/stage/hand"), ("GET", "/hand"),
          ("GET", "/vendor/three.module.min.js"), ("GET", "/vendor/three.core.min.js"),
          ("GET", "/avatars/index.json")}
LAN_PREFIXES = ("/vendor/", "/assets/", "/avatars/", "/captains/", "/photos/")   # static, public: stage code, light probe, models, captain lines
CAPTAIN_AUDIO = HERE / ".cache" / "captains" / "audio"     # mp3s made by table/captains.py
VENDOR = {"three.module.min.js", "three.core.min.js"}      # three.js r185, vendored so the table stays offline


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
    for _v in VPS.values():                  # the AIs' own permanents: what removal will be aimed at
        names += [p.name for p in _v.battlefield if not p.is_("Land") and not p.token and not p.face_down]
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
    return respond_text(text, who, t0, t_stt)


MIC = None                                            # open mic v2 (table/openmic.py), when OPENMIC_V2=1


def open_mic():
    """OPENMIC_V2=1 turns on the tev1-judged open mic. OPENMIC_CHATTY="Fusion=chatty,Claude=quiet" sets each
    AI seat's appetite (default normal)."""
    global MIC
    if MIC is None and os.environ.get("OPENMIC_V2") == "1" and AI_PLAYERS:
        import openmic
        chatty = dict(x.split("=", 1) for x in os.environ.get("OPENMIC_CHATTY", "").split(",") if "=" in x)
        MIC = openmic.OpenMic([openmic.Seat(p["name"], p.get("commander", ""), chatty.get(p["name"], "normal")) for p in AI_PLAYERS],
                              "A Magic: The Gathering Commander game with people and AI players at one table.",
                              log_path=str(research_dir() / "openmic" / "decisions.jsonl"))
    return MIC


def respond_text(text: str, who: str | None, t0: float, t_stt: int = 0):
    """Everything after hearing a line: holds, corrections, routing, table rules, AI replies. Spoken lines
    (handle_utterance) and typed ones (the chat box, /api/chat) both come through here."""
    import tablefacts
    import voice
    ai_names = [p["name"] for p in AI_PLAYERS]
    if VP and tablefacts.is_hold(text, VP.name):       # "Wait, Claude, hold on." — stop talking, don't act
        emit("hush", by=who)
        if BRAIN_EXTERNAL:
            for _n in (VPS or [ai_names[0]]):
                emit("attention", kind="hold", text=text, addressee=_n, by=who)
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
    mic_d = None
    if open_mic():                                     # one judge request per line: who should speak, and is it a move
        import openmic
        mic_d = MIC.heard(text, who, recent, may_speak=r["kind"] != "play")
        gate = openmic.move_gate(r["kind"], mic_d.scores)
        if gate == "chatter":                          # "I cast a glance at the menu": the router saw a play; it isn't one
            r = {**r, "kind": "chatter", "vetoed": "open mic: not a move"}
        elif gate == "maybe_move" and who:             # sure it changed the game, but no move was read: ask the speaker
            emit("openmic", kind="maybe_move", by=who, text=text[:200])
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
    side_talk = False
    if mic_d is not None and r["kind"] != "play":
        side_talk = mic_d.about_game < 0.5 and mic_d.reason == "none"
        if mic_d.seat and not reply:
            speaker = mic_d.seat
            reply = "Let me think about that." if mic_d.reason in ("wake", "addressed") else "\x00interject"
            if mic_d.reason == "unprompted":
                ev = emit("openmic", kind="interject", seat=mic_d.seat, level=round(mic_d.scores.get(f"speak:{mic_d.seat}", 0), 2))
                MIC.link(ev["id"], mic_d)
                r = {**r, "kind": "interject"}
    emit("heard", text="(side conversation)" if side_talk else text, kind=r["kind"], addressee=r["addressee"],
         cards=cards, for_ai=bool(reply) and speaker is not None, by=who)
    rules = table_rules(text, r, cards, who)
    if rules is not None:
        rseat = rules.get("seat") or (VP.name if VP else speaker)
        base = {"heard": text, "route": r, "cards": cards, "speaker": rseat,
                "blocked": False, "stt_ms": t_stt, "total_ms": round((time.time() - t0) * 1000),
                "announced": CONVO["announced"][-10:], "rules": {k: v for k, v in rules.items() if k not in ("reply", "source")}}
        if rules.get("awaiting"):
            return {**base, "reply": None, "awaiting": "brain", "reply_source": "brain"}
        if rules.get("reply"):
            if BRAIN_EXTERNAL:
                emit("say", speaker=rseat, text=rules["reply"], action="rules", speech=spoken(rules["reply"]))
                return {**base, "reply": None, "reply_source": "rules-event"}
            return {**base, "reply": rules["reply"], "speech": spoken(rules["reply"]), "reply_source": "rules"}
    if BRAIN_EXTERNAL and reply and speaker in VPS:
        ev = emit("attention", kind=r["kind"], text=text, addressee=speaker)
        hold_the_floor(ev["id"], speaker)
        return {"heard": text, "route": r, "cards": cards, "speaker": speaker, "reply": None,
                "awaiting": "brain", "blocked": False, "stt_ms": t_stt,
                "total_ms": round((time.time() - t0) * 1000), "announced": CONVO["announced"][-10:]}
    if reply == "\x00interject":                   # unprompted and no external brain to word it: stay quiet
        reply = None
    reply_source, reply_ms = "template", 0
    turn = None
    if reply == voice.TURN:
        if speaker not in VPS:
            reply = None                              # no virtual deck: the humans play its cards
        else:
            with acting(speaker):
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
            own = VPS[ai["name"]].public()["battlefield"] if ai["name"] in VPS else None
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
        with VP_LOCK:
            for _v in VPS.values():
                hidden += _v.private_hand()
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
    for n, v in VPS.items():
        t[n] = v.life
    return t


def change_life(player, delta, by, set_to=None):
    """Anyone's life, by button, brain or voice. set_to: "Michael's at 31" states a total."""
    if set_to is None and (not isinstance(delta, int) or isinstance(delta, bool)):
        return {"error": "delta must be an integer"}
    lt = life_table()
    if VP and player.lower() == "me":
        player = VP.name                              # the seat the brain is acting for
    elif seat(player) and player.lower() in (n.lower() for n in VPS):
        player = seat(player)
    elif player not in LIFE:
        return {"error": f"unknown player '{player}' (players: {list(lt)})"}
    if set_to is not None:
        delta = set_to - lt[player]
    if player in VPS:
        with VP_LOCK:
            VPS[player].life += delta
    else:
        LIFE[player] += delta
    emit("life", player=player, delta=delta, by=by, life=life_table()[player])
    return life_table()


def add_human(name: str, commander: str = "") -> tuple[int, dict]:
    """A player joining after the server started. They start at 40, and enroll their voice the
    same way as everyone else ("This is Jess.")."""
    name, commander = name.strip(), commander.strip()[:80]
    if not re.fullmatch(r"[A-Za-z][A-Za-z'-]{0,19}", name):
        return 400, {"error": "a name is one word of letters, up to 20"}
    taken = [h["name"].lower() for h in HUMANS] + [p["name"].lower() for p in AI_PLAYERS]
    if name.lower() in taken:
        return 409, {"error": f"{name} is already at the table"}
    if len(HUMANS) >= 6:
        return 400, {"error": "the table is full (6 human players)"}
    HUMANS.append({"name": name, "commander": commander or None})
    LIFE[name] = 40
    if commander:
        NICKNAMES[commander.split(",")[0]] = commander
    emit("player-added", player=name, commander=commander or None)
    return 200, {"humans": HUMANS, "life": life_table()}


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
                subprocess.run(["say", "-v", v, "-o", f.name, "--data-format=LEI16@16000", line], check=True,
                               timeout=30)                       # macOS speech can stall
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
    """With two or more AI seats, decide which AI a line is about, then apply the one-AI rules as
    that seat: the AI it addresses, else the AI it names, else the AI owning a permanent it names."""
    if len(VPS) <= 1:
        return _table_rules(text, r, cards, who)
    import voice
    names = list(VPS)
    low = text.lower()
    pick = voice.spoken_to(text, names)
    if pick is None:
        hits = sorted((low.find(n.lower()), n) for n in names if n.lower() in low)
        pick = hits[0][1] if hits else None
    if pick is None:
        for n, v in VPS.items():
            if any(p.name in cards or p.name.lower() in low for p in v.battlefield if not p.is_("Land")):
                pick = n
                break
    pick = pick or names[0]
    with acting(pick):
        res = _table_rules(text, r, cards, who)
    if res is not None:
        res["seat"] = pick                            # who answers: the AI the line was about
    return res


def _table_rules(text: str, r: dict, cards: list[str], who: str | None = None) -> dict | None:
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


def filler_is_pointless(name: str) -> bool:
    """No "Hold on." for a pilot seat (a person or agent driving it over the API: it is not thinking aloud), and none while the
    table is waiting on a PERSON's pass: the AI is not the slow one, and the line read as "Claude is doing something"
    (2026-10-07: two fillers in a game while two people had not yet passed)."""
    if name in PILOTS:
        return True
    with PHASE_LOCK:
        nxt = passes_state()["next"]
    return bool(nxt and nxt not in VPS)


def hold_the_floor(attention_id: int, speaker: str | None = None, after: float = 3.0):
    """External brain: if nothing has come back a few seconds after the table spoke to the AI,
    say so — silence at a table reads as not having heard."""
    asked = time.time()

    def later():
        time.sleep(after)
        if BRAIN_LAST[0] < asked and not filler_is_pointless(speaker or (VP.name if VP else "")):
            line = FILLERS[attention_id % len(FILLERS)]
            emit("say", speaker=speaker or VP.name, text=line, action="filler", speech=spoken(line))
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
    # The rectangle finder loses cards on a patterned table: on wood grain it found 1 of 8 (Canon
    # T5i, 2026-10-01) while reading every title line in the frame got 6 of 8, 0 wrong. So the
    # frame's own title lines count too, one card per title line (two Forests read as two lines).
    # Tapped state is known only for cards the rectangle finder saw.
    by_rect = Counter(c["name"] for c in seen)
    by_text = Counter(n for n in (ident([t]) for t in vision.title_lines([l.text for l in read_lines(image)])) if n)
    for n, k in by_text.items():
        seen += [{"name": n, "tapped": None, "via": "text"}] * max(0, k - by_rect[n])
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
    if not names and NO_GEMMA:                        # code-only table: no vision model to ask
        return {"status": "unreadable"}
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
    # Keep-alive: phones, the headset and the tests poll /api/events and /api/phase all game. Under HTTP/1.0 every
    # poll was a new connection the server then closed, and each closed one holds a port in TIME_WAIT (the phase
    # suite filled all 16k of them). Every response sends Content-Length (_send, the snapshot) and every POST body is
    # read in full before its handler runs (do_POST), so the connection is always at the next request.
    protocol_version = "HTTP/1.1"
    timeout = 75                                         # an idle kept-alive connection is closed after this

    def send_response(self, *a, **k):
        self._answered = True
        super().send_response(*a, **k)

    def handle_one_request(self):
        self._answered = False
        super().handle_one_request()
        if self.command and not self._answered:          # a path that returned without answering: under HTTP/1.0
            self.close_connection = True                 # the close told the client; under keep-alive it would wait

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

    JSON_MAX = 256_000                                  # every JSON body is small; audio/images use their own routes

    def _json(self):
        if getattr(self, "_jcache", None) is not None:      # read once per request (the seat guard reads it first)
            return self._jcache
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            raise BadRequest("bad Content-Length")
        if n < 0 or n > self.JSON_MAX:
            self._drain(n)
            raise BadRequest(f"body too large (over {self.JSON_MAX} bytes)")
        try:
            b = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            raise BadRequest("body is not JSON")
        if not isinstance(b, dict):
            raise BadRequest("body must be a JSON object")
        self._jcache = b
        return b

    def _room_token_ok(self) -> bool:
        """Cloud rooms only: the room's Durable Object holds ROOM_TOKEN (set at container start) and is the only
        caller of /api/room/*. Without the env var these endpoints don't exist (laptop tables)."""
        want = os.environ.get("ROOM_TOKEN", "")
        got = self.headers.get("X-Room-Token", "")
        return bool(want) and secrets.compare_digest(want, got)

    def _room_api(self, method: str) -> bool:
        """GET /api/room/snapshot → the latest pickled game; POST /api/room/restore ← one, replacing the game.
        Returns True when it answered."""
        p0 = self.path.split("?")[0]
        if not p0.startswith("/api/room/"):
            return False
        if not self._room_token_ok():
            if method == "POST":
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self._send(404, {"error": "not found"})
            return True
        if method == "GET" and p0 == "/api/room/status":     # is this a fresh process the room should fill?
            self._send(200, {"adopted": ROOM_ADOPTED[0], "rev": _ev_id[0]})
            return True
        if method == "POST" and p0 == "/api/room/adopt":     # nothing saved yet: keep this fresh game as the room's
            ROOM_ADOPTED[0] = True
            self._send(200, {"ok": True})
            return True
        if method == "POST" and p0 == "/api/room/release":    # admin, via the room: free a seat whose device is lost
            b = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            name = next((h["name"] for h in HUMANS if h["name"].lower() == str(b.get("seat", "")).lower()), None)
            if not name:
                self._send(404, {"error": "no such human seat"})
                return True
            SEAT_KEYS.pop(name, None); SEAT_DEVICES.pop(name, None); SEAT_PROXY.pop(name, None)
            emit("seat", name=name, kind="released")
            snapshot()
            self._send(200, {"ok": True, "released": name})
            return True
        if method == "GET" and p0 == "/api/room/snapshot":
            snapshot()
            p = research_dir() / "snapshot.pkl"
            body = p.read_bytes() if p.exists() else b""
            self.send_response(200 if body else 204)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Snapshot-Rev", str(_ev_id[0]))
            self.end_headers()
            self.wfile.write(body)
            return True
        if method == "POST" and p0 == "/api/room/restore":
            blob = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            tmp = HERE / ".cache" / "room-restore.pkl"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(blob)
            os.chmod(tmp, 0o600)
            if ROOM_ADOPTED[0]:                            # never overwrite a game already in play here
                tmp.unlink(missing_ok=True)
                self._send(409, {"error": "this process already holds the room's game"})
                return True
            restore(str(tmp))
            ROOM_ADOPTED[0] = True
            tmp.unlink(missing_ok=True)
            snapshot()                                     # re-save under the restored game's research folder
            self._send(200, {"ok": True, "rev": _ev_id[0], "phase": PHASE})
            return True
        self._send(404, {"error": "not found"})
        return True

    def do_GET(self):
        if self.headers.get("Content-Length", "0") not in ("", "0") or self.headers.get("Transfer-Encoding"):
            self.close_connection = True                   # a GET with a body: we never read it, so don't reuse
        if self._room_api("GET"):
            return
        if self._lan_blocked("GET"):
            return
        if self.path.split("?")[0] == "/api/version":      # which commit this room runs (scripts/cloud_smoke.cjs)
            from build_info import build_sha
            return self._send(200, {"sha": build_sha(), "game": "magic"})
        if self.path.split("?")[0] == "/me":               # a player's phone / glasses view (public info only)
            return self._send(200, body=(HERE / "me.html").read_bytes(), ctype="text/html; charset=utf-8")
        if self.path.startswith("/api/card"):              # Oracle text for one card, by name (public)
            import oracle
            from urllib.parse import parse_qs, urlparse
            name = parse_qs(urlparse(self.path).query).get("name", [""])[0]
            c = oracle.card(name) if name else None
            if not c:
                return self._send(404, {"error": f"no card named '{name}'"})
            return self._send(200, {"name": name, **{k: c.get(k) for k in ("cost", "type", "text", "power", "toughness")}})
        if self.path == "/":
            return self._send(200, body=(HERE / "scan.html").read_bytes(), ctype="text/html; charset=utf-8")
        if self.path == "/table":
            return self._send(200, body=(HERE / "table.html").read_bytes(), ctype="text/html; charset=utf-8")
        if self.path.split("?")[0] == "/xr":               # AR/VR: the avatars around your real table
            return self._send(200, body=(HERE / "xr.html").read_bytes(), ctype="text/html; charset=utf-8")
        if self.path.split("?")[0] == "/log":              # the game log on its own (pop-out window / second screen)
            return self._send(200, body=(HERE / "log.html").read_bytes(), ctype="text/html; charset=utf-8")
        if self.path.split("?")[0] == "/board":            # the whole board in plain HTML: any device, no 3D
            return self._send(200, body=(HERE / "board.html").read_bytes(), ctype="text/html; charset=utf-8")
        if self.path.split("?")[0] == "/hand":             # a pilot seat's hand and buttons (its data needs the seat key)
            return self._send(200, body=(HERE / "hand.html").read_bytes(), ctype="text/html; charset=utf-8")
        if self.path.split("?")[0] == "/stage":            # the 3D avatar stage: public info only
            return self._send(200, body=(HERE / "stage.html").read_bytes(), ctype="text/html; charset=utf-8")
        p0 = self.path.split("?")[0]
        for pre, root in (("/vendor/", HERE / "vendor"), ("/assets/", HERE / "assets"),
                          ("/avatars/", HERE / ".cache" / "avatars"), ("/captains/", CAPTAIN_AUDIO),
                          ("/photos/", research_dir() / "photos")):
            if p0.startswith(pre) and not p0.endswith("/index.json"):
                fp = (root / p0[len(pre):]).resolve()
                if fp.is_file() and root.resolve() in fp.parents and fp.suffix in (".js", ".css", ".hdr", ".glb", ".usdz", ".png", ".mp3", ".jpg"):
                    return self._send(200, body=fp.read_bytes(), ctype={".js": "text/javascript; charset=utf-8",
                                      ".css": "text/css; charset=utf-8",
                                      ".glb": "model/gltf-binary", ".usdz": "model/vnd.usdz+zip", ".png": "image/png",
                                      ".mp3": "audio/mpeg", ".jpg": "image/jpeg"}
                                      .get(fp.suffix, "application/octet-stream"))
        if p0 == "/avatars/index.json":
            d = HERE / ".cache" / "avatars"
            return self._send(200, sorted(x.stem for x in d.glob("*.glb")) if d.exists() else [])
        if self.path == "/api/phase":
            return self._send(200, phase_public())
        if self.path == "/api/placed":                    # which milled cards are physically in the graveyard
            return self._send(200, sorted(PLACED))
        if self.path == "/api/todos":                     # "at the table, please…": open ones first, then the last done
            return self._send(200, {"open": [t for t in TODOS if not t["done"]],
                                    "done": [t for t in TODOS if t["done"]][-8:]})
        if self.path == "/api/board3d":                   # everyone's battlefield, public only
            return self._send(200, board3d())
        if self.path == "/api/stage":                      # seats, commanders, life, hand SIZES — never cards
            with VP_LOCK:
                lt = life_table()
                seats = [{"name": h["name"], "commander": h["commander"], "kind": "human",
                          "life": lt.get(h["name"]), "hand": HAND_N.get(h["name"], 7)} for h in HUMANS]
                seats += [{"name": n, "commander": v.commander.get("name"), "kind": "ai", "life": v.life,
                           "hand": len(v.hand)} for n, v in VPS.items()]
            return self._send(200, {"seats": seats})
        if self.path == "/show":
            return self._send(200, body=(HERE / "show.html").read_bytes(), ctype="text/html; charset=utf-8")
        if self.path == "/voice":
            return self._send(200, body=(HERE / "voice.html").read_bytes(), ctype="text/html; charset=utf-8")
        if self.path == "/api/voice-config":
            return self._send(200, {"ai_players": AI_PLAYERS, "humans": HUMANS, "brain": args.brain})
        if self.path.split("?")[0] == "/api/ai/state":     # ?seat=Name with two AIs (default: the first)
            if VP is None:
                return self._send(404, {"error": "no virtual AI deck (start with --ai-deck)"})
            sn = self._seat_q()
            if sn is None:
                return self._send(404, {"error": f"no AI seat by that name (seats: {list(VPS)})"})
            with VP_LOCK:
                return self._send(200, VPS[sn].public())
        if self.path.split("?")[0] == "/api/ai/hand":      # the AI's own brain only
            if not secrets.compare_digest(self.headers.get("X-Brain-Token", ""), TOKEN):
                return self._send(403, {"error": "brain token required"})
            sn = self._seat_q()
            with VP_LOCK:
                return self._send(200, {"hand": VPS[sn].private_hand() if sn else []})
        if self.path.split("?")[0] == "/api/history":     # the whole game's public log, from the research file
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            start = max(0, int((q.get("from") or ["0"])[0] or 0))
            p = research_dir() / "events.jsonl"
            try:
                lines = p.read_text().splitlines() if p.exists() else []
            except OSError:
                lines = []
            out = []
            for i, line in enumerate(lines[start:start + 2000], start):
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("type") in ("board3d", "hand", "heard") or (e.get("type") == "attention" and e.get("kind") == "priority"):
                    continue                              # noise for a reader; everything else is the game
                e["seq"] = i
                if e.get("type") == "say" and e.get("action") == "mill":
                    m = re.match(r"I mill (\d+): (.*?)\.?$", e.get("text") or "")
                    if m:
                        e["cards"] = split_cards(m.group(2))
                out.append(e)
            return self._send(200, {"next": min(len(lines), start + 2000), "events": out})
        if self.path.startswith("/api/events"):
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            try:
                wait = min(25.0, max(0.0, float((q.get("wait") or ["0"])[0] or 0)))    # a held request: 1 request per 20 s idle, not per second
            except ValueError:
                wait = 0.0
            deadline = time.time() + wait
            with EV_COND:
                while True:
                    last = EVENTS[-1]["id"] if EVENTS else 0
                    since = last if q.get("since", ["0"])[0] == "latest" else int(q.get("since", ["0"])[0] or 0)
                    # A page left open across a server restart holds a cursor from the OLD server, ahead
                    # of every new id, and would wait for ever (seen 2026-09-30: the page went silent).
                    restarted = since > last
                    out = [e for e in EVENTS if e["id"] > (0 if restarted else since)][:300]
                    left = deadline - time.time()
                    if out or restarted or left <= 0 or q.get("since", ["0"])[0] == "latest":
                        break
                    EV_COND.wait(left)
            return self._send(200, {"last": last, "events": out, "restarted": restarted})
        if self.path == "/api/life":
            return self._send(200, self._life_table())
        if self.path == "/api/highroll":
            return self._send(200, highroll_public())
        if self.path.split("?")[0] == "/api/checklist":   # ?player=Michael → what on their board wants attention now
            import checklist
            from urllib.parse import parse_qs, urlparse
            who = (parse_qs(urlparse(self.path).query).get("player") or [""])[0]
            with PHASE_LOCK:
                active, step = PHASE["player"], STEPS[PHASE["step"]] if PHASE["player"] else ""
            seat_ = next((s for s in board3d()["seats"] if s["name"] == who), None)
            its = checklist.items(seat_["permanents"], who, active, step) if seat_ and active else []
            return self._send(200, {"player": who, "active": active, "step": step, "items": its})
        if self.path == "/api/seat/claims":
            fp = self._device()
            return self._send(200, {"humans": {n: n in SEAT_KEYS or n in SEAT_PROXY for n in [h["name"] for h in HUMANS] + sorted(PILOTS)},
                                    "proxy": dict(SEAT_PROXY),
                                    "this_device": [n for n, fps in SEAT_DEVICES.items() if fp in fps]})
        if self.path == "/api/fair":                       # public: commits now, full records once revealed
            with VP_LOCK:
                return self._send(200, fair_public())
        if self.path == "/api/fair/verify":                # recompute every revealed record on the server
            import fair
            with VP_LOCK:
                if not FAIR["revealed"]:
                    return self._send(409, {"error": "not revealed yet: the proof is published after the game"})
                res = {n: dict(zip(("ok", "message"), fair.verify(v.fair))) for n, v in VPS.items() if v.fair}
            return self._send(200, {"game": FAIR["game"], "results": res})
        if self.path.split("?")[0] in ("/api/journal", "/api/journal/due"):
            sn = self._seat_q()
            if not (self._brain_ok() or self._pilot_ok(sn)):
                return self._send(403, {"error": "brain token required"})
            if sn is None:
                return self._send(404, {"error": f"no AI seat by that name (seats: {list(VPS)})"})
            if self.path.split("?")[0].endswith("/due"):
                return self._send(200, {"seat": sn, "due": JOURNALS.due(sn)})
            try:
                return self._send(200, {"seat": sn, "entries": JOURNALS.entries(sn, GAME_OVER["over"])})
            except journal_mod.Sealed as e:
                return self._send(409, {"error": str(e)})
        if self.path.split("?")[0] == "/api/brain/state":
            sn = self._seat_q()
            if not (self._brain_ok() or self._pilot_ok(sn)):
                return self._send(403, {"error": "brain token required"})
            from player import brain_view
            if sn is None:
                return self._send(404, {"error": f"no AI seat by that name (seats: {list(VPS)})"})
            with VP_LOCK:
                v = brain_view(VPS[sn])
            v["seat"] = sn
            v["seats"] = list(VPS)
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

    BODY_MAX = 32_000_000                                # as _drain: bigger than this, the connection is closed

    def do_POST(self):
        self._jcache = None                                # keep-alive: one handler object serves many requests
        sock = self.rfile
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            n = -1
        if self.headers.get("Transfer-Encoding") or not 0 <= n <= self.BODY_MAX:
            self.close_connection = True                   # can't tell where this body ends: never reuse
        else:
            # Read the whole body now, so whatever a handler reads (or leaves unread when it refuses), the socket
            # is at the start of the next request.
            self.rfile = io.BytesIO(sock.read(n) if n else b"")
        try:
            return self._post()
        finally:
            self.rfile = sock

    def _post(self):
        if self._room_api("POST"):
            return
        try:
            return self._do_post()
        finally:
            if not self.path.startswith(("/api/board", "/api/show", "/api/utterance", "/api/scan", "/api/chat/photo")):   # camera/mic/photos: too often, and nothing to restore
                snapshot()

    def _do_post(self):
        global T                                           # /api/reset replaces the table (AI seats: reshuffle_all)
        if self._lan_blocked("POST"):
            return
        if self._seat_guard():
            return
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
                sn = seat(self._json().get("seat")) if self.headers.get("Content-Length") else seat(None)
                if sn is None:
                    return self._send(404, {"error": f"no AI seat by that name (seats: {list(VPS)})"})
                if BRAIN_EXTERNAL:                         # the button hands the turn to that seat's brain
                    ev = emit("attention", kind="turn", text="(turn button)", addressee=sn)
                    hold_the_floor(ev["id"], sn)
                    return self._send(200, {"awaiting": "brain", "seat": sn})
                with acting(sn):
                    return self._send(200, run_ai_turn())
            if self.path == "/api/fair/word":              # {"player": "Sam", "word": "..."}: sealed into the NEXT shuffle
                b = self._json()
                who, word = str(b.get("player", "")).strip()[:30], str(b.get("word", "")).strip()[:64]
                if not who or not word:
                    return self._send(400, {"error": "player and word are required"})
                FAIR["words"][who] = word
                emit("fair", kind="word", player=who, words_in=sorted(FAIR["words"]))   # never the word
                return self._send(200, {"words_in": sorted(FAIR["words"]),
                                        "note": "sealed into the next new game's shuffle"})
            if self.path == "/api/players":                # someone joins mid-game: {"name": "Jess", "commander": "…"}
                b = self._json()
                return self._send(*add_human(str(b.get("name", "")), str(b.get("commander") or "")))
            if self.path == "/api/todo":                   # the brain asks the physical table to do something
                if not secrets.compare_digest(self.headers.get("X-Brain-Token", ""), TOKEN):
                    return self._send(403, {"error": "brain token required"})
                b = self._json()
                items = b.get("items") or [b]
                made = []
                for it in items[:20]:
                    text = " ".join(str(it.get("text", "")).split())[:240]
                    if not text:
                        continue
                    t = {"id": (TODOS[-1]["id"] + 1) if TODOS else 1, "text": text, "for": str(it.get("for", ""))[:30],
                         "ts": round(time.time(), 2), "done": False, "by": ""}
                    if it.get("ask"):                     # a question from a player (an AI seat) to the table
                        t.update(kind="question", ask=str(it["ask"])[:30], answers=[],
                                 options=[str(o)[:60] for o in (it.get("options") or [])][:6])
                    TODOS.append(t); made.append(t)
                del TODOS[:-200]
                emit("todo", kind="added", items=made)
                return self._send(200, {"added": made})
            if self.path == "/api/placed":                 # {"key": "120:3", "on": true}: anyone at the table
                b = self._json()
                key = str(b.get("key", ""))
                if not re.fullmatch(r"\d{1,6}:\d{1,3}", key):
                    return self._send(400, {"error": "key is '<log seq>:<card index>'"})
                (PLACED.add if b.get("on", True) else PLACED.discard)(key)
                return self._send(200, {"key": key, "on": key in PLACED})
            if self.path == "/api/card-action":            # a person acts with one of their own cards on /board
                b = self._json()                           # {"owner","index","action","ability","target"}
                owner = str(b.get("seat", ""))
                if owner not in [h["name"] for h in HUMANS]:
                    return self._send(400, {"error": "card actions are for people's cards; the AI seats play their own"})
                board = PUBLIC_BOARD.setdefault(owner, {"permanents": [], "graveyard": [], "commander_out": False})
                perms = board.setdefault("permanents", [])
                try:
                    i = int(b.get("index"))
                    p = perms[i]
                except (TypeError, ValueError, IndexError):
                    return self._send(404, {"error": "that card isn't on the board any more — refresh"})
                if str(b.get("name", "")) and b.get("name") != p.get("name"):
                    return self._send(409, {"error": "the board changed — refresh and try again"})
                action = str(b.get("action", ""))
                nm = "a face-down card" if p.get("face_down") else p.get("name", "a card")
                target = " ".join(str(b.get("target", "")).split())[:80]
                if action == "activate":
                    ability = " ".join(str(b.get("ability", "")).split())[:240]
                    cost = ability.split(":")[0] if ":" in ability else ""
                    if "{T}" in cost:
                        if p.get("tapped"):
                            return self._send(409, {"error": f"{nm} is already tapped"})
                        p["tapped"] = True
                    text = f"{owner} activates {nm}: {ability}" + (f" — targeting {target}" if target else "")
                elif action in ("tap", "untap"):
                    if action == "untap":
                        with PHASE_LOCK:
                            why = untap_refusal(owner)
                        if why:
                            return self._send(409, {"error": why})
                    p["tapped"] = action == "tap"
                    text = f"{owner} {action}s {nm}."
                elif action in ("counter+", "counter-"):
                    p["counters"] = int(p.get("counters") or 0) + (1 if action == "counter+" else -1)
                    text = f"{owner} puts a counter {'on' if action == 'counter+' else 'off'} {nm} (now {p['counters']})."
                elif action in ("graveyard", "exile", "hand"):
                    perms.pop(i)
                    if action == "graveyard" and not p.get("token"):
                        board.setdefault("graveyard", []).append(p.get("name", ""))
                    text = f"{owner} moves {nm} to {'their graveyard' if action == 'graveyard' else action}" + (" (a token: it's gone)." if p.get("token") else ".")
                else:
                    return self._send(400, {"error": "unknown action"})
                board["updated"] = round(time.time(), 2)
                emit("chat", by=owner, text=text, card_action=action)
                emit("board3d", owner=owner, n=len(perms))
                if action == "activate":
                    priority_reset(text)
                return self._send(200, {"ok": True, "text": text})
            if self.path.split("?")[0] == "/api/chat/photo":   # a photo from a phone: raw JPEG body; X-By, X-Caption headers
                n = int(self.headers.get("Content-Length", 0) or 0)
                if not 0 < n <= 8_000_000:
                    self._drain(n)
                    return self._send(413, {"error": "a photo must be under 8 MB"})
                data = self.rfile.read(n)
                if not (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"):
                    return self._send(400, {"error": "send a JPEG or PNG"})
                from urllib.parse import unquote
                by = unquote(self.headers.get("X-By", ""))[:30] or "someone"
                caption = " ".join(unquote(self.headers.get("X-Caption", "")).split())[:300]
                d = research_dir() / "photos"
                if CLOUD:                                  # a public room: only seated players upload, and not forever
                    if not seat_key_ok(by, unquote(self.headers.get("X-Seat-Key", ""))):
                        return self._send(403, {"error": "claim your seat first (👤) to share photos"})
                    if d.exists() and sum(1 for _ in d.iterdir()) >= 300:
                        return self._send(429, {"error": "this table has reached its photo limit (300)"})
                d.mkdir(exist_ok=True)
                os.chmod(d, 0o700)
                ext = ".jpg" if data[:3] == b"\xff\xd8\xff" else ".png"
                name = f"{time.strftime('%H%M%S')}-{secrets.token_hex(4)}{ext}"
                (d / name).write_bytes(data)
                os.chmod(d / name, 0o600)
                emit("chat", by=by, text=caption or "📷 a photo of the table", photo="/photos/" + name)
                if caption:
                    try:
                        respond_text(caption, by if by in [h["name"] for h in HUMANS] else None, time.time())
                    except Exception as e:
                        print(f"photo caption handling failed: {e}", flush=True)
                return self._send(200, {"ok": True, "photo": "/photos/" + name})
            if self.path == "/api/chat":                   # typed table talk from a phone or the board: {"by": "Sam", "text": "..."}
                b = self._json()
                by = str(b.get("by", ""))[:30]
                text = " ".join(str(b.get("text", "")).split())[:400]
                if not text:
                    return self._send(400, {"error": "say something"})
                who = by if by in [h["name"] for h in HUMANS] else None
                if b.get("talk") or text.startswith("~"):     # just talking: everyone sees it, nothing reads it as a move
                    emit("chat", by=by or "someone", text=text.lstrip("~ ").strip() or text, talk=True)
                    return self._send(200, {"ok": True, "talk": True})
                if not b.get("spoken"):                   # typed (or pushed-to-talk): everyone sees it as said
                    emit("chat", by=by or "someone", text=text)      # the AIs hear it as if said aloud
                # spoken on an open mic: only "heard" (below) shows it, and side conversation shows as a placeholder
                try:
                    out = respond_text(text, who, time.time())
                except Exception as e:                    # the line is in the log either way
                    print(f"chat handling failed: {type(e).__name__}: {e}", flush=True)
                    out = {"heard": text}
                return self._send(200, {"ok": True, **{k: v for k, v in (out or {}).items() if k in ("heard", "reply", "awaiting", "cards")}})
            if self.path == "/api/declare/attack":         # a player declares attacks from a phone or the board:
                b = self._json()                           # {"by": "Sam", "attacks": [{"attacker", "target", "power", "trample"}]}
                by = str(b.get("by", ""))[:30]
                seats = [h["name"] for h in HUMANS] + list(VPS)
                if by not in seats:
                    return self._send(400, {"error": "who is attacking?"})
                out = []
                for a in (b.get("attacks") or [])[:20]:
                    attacker, target = str(a.get("attacker", ""))[:80], str(a.get("target", ""))[:30]
                    if not attacker or target not in seats or target == by:
                        continue
                    try:
                        power = int(a.get("power")) if a.get("power") not in (None, "") else None
                    except (TypeError, ValueError):
                        power = None
                    trample = bool(a.get("trample"))
                    text = f"{by} attacks {target} with {attacker}" + (f" for {power}" if power is not None else "") + (", trample" if trample else "") + "."
                    out.append({"attacker": attacker, "target": target, "power": power, "trample": trample})
                    if target in VPS:                     # an AI seat decides its blocks, as when the attack is said aloud
                        emit("attention", kind="attacked", text=text, addressee=target, attacker=attacker, amount=power,
                             trample=trample, by=by)
                if not out:
                    return self._send(400, {"error": "no attack: pick an attacker and someone to attack"})
                emit("declare", kind="attack", by=by, attacks=out)
                return self._send(200, {"declared": out})
            if self.path == "/api/todo/answer":            # anyone answers a player's question: {"id": 7, "text": "...", "by": "Sam"}
                b = self._json()
                t = next((x for x in TODOS if x["id"] == b.get("id") and x.get("kind") == "question"), None)
                if not t:
                    return self._send(404, {"error": "no such question"})
                text = " ".join(str(b.get("text", "")).split())[:300]
                if not text:
                    return self._send(400, {"error": "an answer needs some text"})
                a = {"text": text, "by": str(b.get("by", ""))[:30], "ts": round(time.time(), 2)}
                t["answers"].append(a)
                del t["answers"][:-20]
                if b.get("resolve"):
                    t["done"] = True
                emit("todo", kind="answered", todo_id=t["id"], ask=t.get("ask"), question=t["text"], answer=text, by=a["by"],
                     resolved=t["done"])
                return self._send(200, t)
            if self.path == "/api/todo/done":              # anyone at the table ticks one off: {"id": 3, "by": "Sam"}
                b = self._json()
                t = next((x for x in TODOS if x["id"] == b.get("id")), None)
                if not t:
                    return self._send(404, {"error": "no such item"})
                t["done"] = not t["done"] if b.get("toggle") else True
                t["by"] = str(b.get("by", ""))[:30]
                emit("todo", kind="done" if t["done"] else "reopened", todo_id=t["id"], by=t["by"])
                return self._send(200, t)
            if self.path == "/api/openmic/rate":           # 👍/🙄 on an AI's unprompted line: {event, rating, by, key}
                b = self._json()
                who = str(b.get("by", ""))
                if who not in [h["name"] for h in HUMANS] or not seat_key_ok(who, str(b.get("key", ""))):
                    return self._send(403, {"error": "claim your seat first (👤) to rate"})
                import openmic
                out = openmic.rate(str(research_dir() / "openmic"), int(b.get("event", 0)), str(b.get("rating", "")), who)
                return self._send(400 if "error" in out else 200, out)
            if self.path == "/api/seat/check":             # the room's Worker asks before it transcribes or reads a photo
                key = str(self.headers.get("X-Seat-Key", ""))[:200]
                who = next((n for n in [h["name"] for h in HUMANS] + sorted(PILOTS) if key and seat_key_ok(n, key)), None)
                if not who:
                    return self._send(403, {"error": "not a seated player"})
                return self._send(200, {"seat": who, "deck": seat_deck_names(who)})   # card names help a photo read
            if self.path == "/api/my-board":               # a person records their OWN board from their phone
                b = self._json()
                who = str(b.get("by", ""))
                if who not in [h["name"] for h in HUMANS] or not seat_key_ok(who, str(b.get("key", ""))):
                    return self._send(403, {"error": "claim your seat first (👤): only you can record your board"})
                prev = PUBLIC_BOARD.get(who) or {}
                if b.get("merge"):                         # a photo's reading ADDS to my board; it never removes a card
                    perms = [dict(p) for p in prev.get("permanents", [])]
                    added = updated = 0
                    for c in (b.get("seen") or [])[:60]:
                        if not isinstance(c, dict):
                            continue
                        name = " ".join(str(c.get("name", "")).split())[:80]
                        if not name:
                            continue
                        try:
                            n = max(1, min(20, int(c.get("count") or 1)))
                        except (TypeError, ValueError):
                            n = 1
                        same = [p for p in perms if p.get("name") == name and not p.get("face_down")]
                        for p in same[:n]:                 # seen: its tapped state is what the photo shows
                            if bool(p.get("tapped")) != bool(c.get("tapped")):
                                p["tapped"] = bool(c.get("tapped")); updated += 1
                        for _ in range(n - len(same)):
                            perms.append({"name": name, "tapped": bool(c.get("tapped"))}); added += 1
                    PUBLIC_BOARD[who] = {**prev, "permanents": perms[:120], "graveyard": list(prev.get("graveyard", [])),
                                         "updated": round(time.time(), 2)}
                    emit("board3d", seat=who, n=len(perms))
                    return self._send(200, {"ok": True, "seat": who, "added": added, "updated": updated, "permanents": len(perms)})
                perms, grave = board_from_body(b)
                if "graveyard" not in b:                   # a photo of the battlefield doesn't empty the graveyard
                    grave = list(prev.get("graveyard", []))
                PUBLIC_BOARD[who] = {"permanents": perms, "graveyard": grave,
                                     "commander_out": bool(b.get("commander_out", prev.get("commander_out"))),
                                     "updated": round(time.time(), 2)}
                emit("board3d", seat=who, n=len(perms))
                return self._send(200, {"ok": True, "seat": who, "permanents": len(perms)})
            if self.path == "/api/public-board":           # the brain records a human's board (from photos/speech)
                if not secrets.compare_digest(self.headers.get("X-Brain-Token", ""), TOKEN):
                    return self._send(403, {"error": "brain token required"})
                b = self._json()
                who = str(b.get("seat", ""))
                if who not in [h["name"] for h in HUMANS]:
                    return self._send(400, {"error": "public-board is for human seats; AI boards come from the engine"})
                perms, grave = board_from_body(b)
                PUBLIC_BOARD[who] = {"permanents": perms, "graveyard": grave,
                                     "commander_out": bool(b.get("commander_out")), "updated": round(time.time(), 2)}
                emit("board3d", seat=who, n=len(perms))
                return self._send(200, {"ok": True, "seat": who, "permanents": len(perms)})
            if self.path == "/api/captain":                # a commander's line (table/captains.py): text + its mp3
                if not secrets.compare_digest(self.headers.get("X-Brain-Token", ""), TOKEN):
                    return self._send(403, {"error": "brain token required"})
                b = self._json()
                cseat, audio = str(b.get("seat", ""))[:30], str(b.get("audio", ""))
                text = " ".join(str(b.get("text", "")).split())[:200]
                if not text or not re.fullmatch(r"[0-9a-f]{16}\.mp3", audio) or not (CAPTAIN_AUDIO / audio).is_file():
                    return self._send(400, {"error": "need text and an mp3 made by captains.py"})
                if cseat in VPS:                           # an AI seat's captain must not name its hidden cards
                    with VP_LOCK:
                        leak = voice_leak(text, VPS[cseat].private_hand())
                    if leak:
                        return self._send(409, {"error": "that line names a card still in the seat's hand"})
                return self._send(200, emit("captain", seat=cseat, captain=str(b.get("captain", ""))[:60],
                                            text=text, audio="/captains/" + audio))
            if self.path == "/api/phase/hold":             # {"on": true, "by": "Sam"}: freeze NEXT for everyone
                b = self._json()
                HOLD.update(on=bool(b.get("on", not HOLD["on"])), by=str(b.get("by", ""))[:30])
                emit("phase", kind="hold", on=HOLD["on"], by=HOLD["by"])
                return self._send(200, phase_public())
            if self.path == "/api/phase/windows":          # {"on": false}: step timeouts off for the table
                b = self._json()
                if self._restricted_room():                # a remote room: the active player or the host
                    with PHASE_LOCK:
                        cur = PHASE["player"]
                        ok = cur is not None and seat_key_ok(cur, self._seat_key(b))
                    if not ok:
                        return self._send(403, {"error": (f"only {cur}, whose turn it is, or the table's host can switch step timeouts"
                                                          if cur else "only the table's host can switch step timeouts before the game starts")})
                with PHASE_LOCK:
                    WINDOWS["on"] = bool(b.get("on", not WINDOWS["on"]))
                    if not WINDOWS["on"]:
                        PRIORITY.update(waiting=[], seats=[], deadline=0.0, beat_until=0.0)
                emit("phase", kind="windows", on=WINDOWS["on"], by=str(b.get("by", ""))[:30])
                snapshot()
                return self._send(200, phase_public())
            if self.path == "/api/phase/back":             # BACK: a NEXT pressed too soon
                if self._restricted_room():                # a remote room: not a table-wide override any seated key may use
                    with PHASE_LOCK:
                        why = back_refusal(self._seat_key(self._json()))
                    if why:
                        return self._send(403 if "logged" not in why else 409, {**phase_public(), "error": why})
                code, out = prev_step(str(self._json().get("by", ""))[:30] if self.headers.get("Content-Length") else None)
                snapshot()
                return self._send(code, out)
            if self.path == "/api/seat/claim":             # {"name": "Sam"} or {"name": "Sam", "key": "<from Sam's other device>"}
                b = self._json()
                name = next((n for n in [h["name"] for h in HUMANS] + sorted(PILOTS)
                             if n.lower() == str(b.get("name", "")).lower()), None)
                if not name:
                    return self._send(400, {"error": "only a person's seat can be claimed"})
                key = str(b.get("key") or "")
                fp = self._device()
                inv = str(b.get("invite") or "")
                if inv and SEAT_INVITES.get(_keyhash(inv)) == name:   # an open-market invite: the seat changes hands
                    SEAT_INVITES.pop(_keyhash(inv))
                    SEAT_KEYS[name], SEAT_DEVICES[name] = [], []
                    SEAT_PROXY.pop(name, None)
                    key = secrets.token_urlsafe(18)
                    SEAT_KEYS[name].append(_keyhash(key))
                    SEAT_DEVICES[name].append(fp)
                    emit("seat", name=name, kind="claimed", via="invite")
                    snapshot()
                    return self._send(200, {"name": name, "key": key})
                if key and seat_key_ok(name, key):
                    SEAT_DEVICES.setdefault(name, []).append(fp) if fp not in SEAT_DEVICES.get(name, []) else None
                    return self._send(200, {"name": name, "key": key})          # another device of the same player
                if name in SEAT_KEYS and fp not in SEAT_DEVICES.get(name, []):
                    return self._send(409, {"error": f"{name}'s seat is claimed on another device — open the link from "
                                                     f"that device (👤), or " + ("ask the table's host to release it"
                                                                                 if CLOUD else "release it from the host laptop")})
                key = secrets.token_urlsafe(18)              # a new window on the same device gets its own key
                SEAT_KEYS.setdefault(name, [])
                if isinstance(SEAT_KEYS[name], str):
                    SEAT_KEYS[name] = [SEAT_KEYS[name]]
                SEAT_KEYS[name].append(_keyhash(key))
                SEAT_DEVICES.setdefault(name, []).append(fp) if fp not in SEAT_DEVICES.get(name, []) else None
                emit("seat", name=name, kind="claimed")
                snapshot()
                return self._send(200, {"name": name, "key": key})
            if self.path == "/api/autopass":               # {"by": "Sam", "key": …, "mode": "off"|"others"|"others-no-combat"}
                b = self._json()
                person = next((h["name"] for h in HUMANS if h["name"].lower() == str(b.get("by", "")).lower()), None)
                if not person or not seat_key_ok(person, str(b.get("key", ""))):
                    return self._send(403, {"error": "claim your seat first (👤)", "need_seat": True})
                mode = str(b.get("mode", "off"))
                if mode not in ("off", "others", "others-no-combat"):
                    return self._send(400, {"error": "mode: off, others, others-no-combat"})
                with PHASE_LOCK:
                    AUTOPASS[person] = mode
                    emit("autopass", by=person, mode=mode)
                    autopass_round()                  # if it's my turn to pass right now, pass
                snapshot()
                return self._send(200, phase_public())
            if self.path == "/api/seat/link":              # host laptop only: {"name": "Michael"} → a key for one more device
                if not self._is_local():
                    return self._send(403, {"error": "device links are made on the host laptop"})
                want = str(self._json().get("name", "")).lower()   # read the body ONCE (a second read waits forever)
                name = next((h["name"] for h in HUMANS if h["name"].lower() == want), None)
                if not name:
                    return self._send(400, {"error": "only a person's seat"})
                key = secrets.token_urlsafe(18)
                SEAT_KEYS.setdefault(name, [])
                if isinstance(SEAT_KEYS[name], str):
                    SEAT_KEYS[name] = [SEAT_KEYS[name]]
                SEAT_KEYS[name].append(_keyhash(key))
                snapshot()
                return self._send(200, {"name": name, "key": key})
            if self.path == "/api/seat/handoff":           # the seat's own player: {"by","key","to": "<player>"|"open"|"back"}
                b = self._json()
                seat_ = next((h["name"] for h in HUMANS if h["name"].lower() == str(b.get("by", "")).lower()), None)
                if not seat_ or not seat_key_ok(seat_, str(b.get("key", ""))):
                    return self._send(403, {"error": "claim your seat first (👤)", "need_seat": True})
                to = str(b.get("to", ""))
                names = {h["name"] for h in HUMANS}
                if to == "back":                              # take it back: no proxy, no open invites
                    SEAT_PROXY.pop(seat_, None)
                    for k in [k for k, v in SEAT_INVITES.items() if v == seat_]:
                        SEAT_INVITES.pop(k)
                    emit("seat", name=seat_, kind="handoff", to="")
                    snapshot()
                    return self._send(200, {"seat": seat_, "to": ""})
                if to == "open":                              # the open market: a one-use invite link
                    inv = secrets.token_urlsafe(12)
                    SEAT_INVITES[_keyhash(inv)] = seat_
                    emit("seat", name=seat_, kind="handoff", to="an open invite")
                    snapshot()
                    return self._send(200, {"seat": seat_, "invite": inv,
                                            "path": f"/me?player={urllib.parse.quote(seat_)}&invite={inv}"})
                if to not in names or to == seat_:
                    return self._send(400, {"error": "hand off to another person at the table, 'open', or 'back'"})
                SEAT_PROXY[seat_] = to
                emit("seat", name=seat_, kind="handoff", to=to)
                snapshot()
                return self._send(200, {"seat": seat_, "to": to})
            if self.path == "/api/seat/proxy":             # host laptop only: {"seat": "Sam", "to": "Michael", "autopass": "others"}
                if not self._is_local():
                    return self._send(403, {"error": "set from the host laptop"})
                b = self._json()
                names = {h["name"] for h in HUMANS}
                seat_, to = str(b.get("seat", "")), str(b.get("to", ""))
                if seat_ not in names or (to and to not in names):
                    return self._send(400, {"error": "seat and to must be people at the table"})
                if to:
                    SEAT_PROXY[seat_] = to
                else:
                    SEAT_PROXY.pop(seat_, None)
                if b.get("autopass") in ("off", "others", "others-no-combat"):
                    with PHASE_LOCK:
                        AUTOPASS[seat_] = b["autopass"]
                        emit("autopass", by=seat_, mode=b["autopass"])
                        autopass_round()
                emit("seat", name=seat_, kind="proxy", to=to)
                snapshot()
                return self._send(200, {"proxy": dict(SEAT_PROXY), "autopass": dict(AUTOPASS)})
            if self.path == "/api/seat/release":           # host laptop only: {"name": "Sam"} (a lost phone)
                if not self._is_local():
                    return self._send(403, {"error": "release a seat from the host laptop"})
                name = str(self._json().get("name", ""))
                SEAT_KEYS.pop(name, None)
                SEAT_DEVICES.pop(name, None)
                emit("seat", name=name, kind="released")
                snapshot()
                return self._send(200, {"released": name})
            if self.path == "/api/highroll/start":         # {"mode": "physical"|"quantum", "sides": 20, "by": "Sam"}
                b = self._json()
                with PHASE_LOCK:
                    code, out = highroll_start(str(b.get("mode", "")), int(b.get("sides") or 20), str(b.get("by", "")))
                snapshot()
                return self._send(code, out)
            if self.path == "/api/highroll/roll":          # a real die: {"player": "Fusion", "value": 17, "by": "Sam"}
                b = self._json()
                with PHASE_LOCK:
                    code, out = highroll_enter(str(b.get("player", "")), b.get("value"), str(b.get("by", "")))
                snapshot()
                return self._send(code, out)
            if self.path == "/api/phase/next":             # the NEXT button: {"by": "Michael"} (optional)
                b = self._json() if self.headers.get("Content-Length") else {}
                code, out = next_step(str(b.get("by", ""))[:30] or None, bool(b.get("shared")), bool(b.get("confirm")),
                                      str(b.get("key", ""))[:100])
                pn = next((h["name"] for h in HUMANS if h["name"].lower() == str(b.get("by", "")).lower()), None)
                if pn and seat_key_ok(pn, str(b.get("key", ""))) and self._device() not in SEAT_DEVICES.get(pn, []):
                    SEAT_DEVICES.setdefault(pn, []).append(self._device())   # remember this device for its other windows
                snapshot()
                return self._send(code, out)
            if self.path == "/api/stage/hand":             # {"player": "Sam", "n": 6} or {"player": "Sam", "delta": -1}
                b = self._json()
                p = next((h["name"] for h in HUMANS if h["name"].lower() == str(b.get("player", "")).lower()), None)
                if not p:
                    return self._send(400, {"error": "humans only — the AI seats' hands are counted by the engine"})
                n = b["n"] if isinstance(b.get("n"), int) else HAND_N.get(p, 7) + int(b.get("delta", 0))
                HAND_N[p] = max(0, min(30, n))
                emit("hand", player=p, n=HAND_N[p])
                return self._send(200, {"player": p, "hand": HAND_N[p]})
            if self.path == "/api/life":                   # anyone at the table: {"player": name, "delta": -3}
                b = self._json()
                return self._send(200, self._change_life(str(b.get("player", "")), b.get("delta"), b.get("by", "table")))
            if self.path == "/api/journal":                # an AI seat's journal entry at a fixed moment
                b = self._json()
                sn = seat(b.get("seat"))
                if not (self._brain_ok() or self._pilot_ok(sn)):
                    return self._send(403, {"error": "brain token required"})
                if sn is None:
                    return self._send(404, {"error": f"no AI seat '{b.get('seat')}' (seats: {list(VPS)})"})
                rec, why = JOURNALS.add(sn, b)
                return self._send(200 if rec else 400, {"entry": rec} if rec else {"error": why})
            if self.path == "/api/identity":               # an AI seat declares its model, provider and prompt version
                b = self._json()
                sn = seat(b.get("seat"))
                if not (self._brain_ok() or self._pilot_ok(sn)):
                    return self._send(403, {"error": "brain token required"})
                if sn is None:
                    return self._send(404, {"error": f"no AI seat '{b.get('seat')}' (seats: {list(VPS)})"})
                code, out = identity_set(sn, b)
                return self._send(code, out)
            if self.path == "/api/game-over":              # the host says the game is over: unseals the journals
                b = self._json()
                if not self._brain_ok():
                    return self._send(403, {"error": "only the table's host can do that"})
                order = [str(x)[:40] for x in (b.get("order") or [])][:8]
                winner = str(b.get("winner") or (order[0] if order else ""))[:40]
                names = list(life_table())
                if set(order) != set(names) or len(order) != len(names) or winner not in order:
                    return self._send(400, {"error": f"order is the finish order, first to last, of every player {names}; winner is in it"})
                if GAME_OVER["over"]:
                    return self._send(409, {"error": "the game is already over"})
                GAME_OVER.update(over=True, order=order, winner=winner)
                emit("game-over", order=order, winner=winner, by="table")
                for n in VPS:
                    journal_due(n, "game_end")
                return self._send(200, {"over": True, "order": order, "winner": winner})
            if self.path.startswith("/api/brain/"):
                b = self._json()
                sn = seat(b.get("seat"))
                pilot = not self._brain_ok()
                if pilot and not self._pilot_ok(sn):
                    return self._send(403, {"error": "brain token required"})
                if sn is None:
                    return self._send(404, {"error": f"no AI seat '{b.get('seat')}' (seats: {list(VPS)})"})
                act = self.path.rsplit("/", 1)[-1]
                if pilot and act in ("new-game", "take"):  # a person plays their seat: no reset, no reaching into a hand
                    return self._send(403, {"error": "only the table's host can do that" if act == "new-game"
                                            else "taking a card from another hand is done by the table's host"})
                if pilot and act == "life" and str(b.get("player", "me")).lower() not in ("me", sn.lower()):
                    return self._send(403, {"error": "you can only change your own life total"})
                if pilot and act == "damage":
                    b["no_life"] = True                     # the players hit say their own life; only my lifelink counts
                if pilot and act == "fair-reveal":           # it publishes every deck's seed and shuffled order
                    return self._send(403, {"error": "only the table's host can publish the fairness proof: it shows every deck's order"})
                if pilot and act not in PILOT_ACTIONS:
                    self._refused(act, "pilot-not-allowed", "")
                    return self._send(403, {"error": f"'{act}' isn't available to a pilot seat: it has no card behind it "
                                                     f"(a pilot plays: {', '.join(sorted(PILOT_ACTIONS))}); the table's host can do it"})
                if pilot and act == "untap":
                    with PHASE_LOCK:
                        why = untap_refusal(sn)
                    if why:
                        self._refused(act, "untap-not-now", why)
                        return self._send(409, {**phase_public(), "error": why})
                if pilot and act == "cast":
                    b.pop("discount", None)                 # a client-named cost reduction is not checked by the engine: host only
                lock = BEGIN_LOCK if act == "begin" else PASS_LOCK if (pilot and act == "pass") else nullcontext()
                with lock:                                  # a double tap queues behind the first press and sees its effect
                    if act == "begin":
                        with PHASE_LOCK:
                            why = begin_refusal(sn, host=not pilot)
                        if why:
                            self._refused(act, "not-your-begin", why)
                            return self._send(409, {**phase_public(), "error": why})
                    if pilot and act == "pass":
                        with PHASE_LOCK:
                            why = pilot_pass_refusal(sn, b)
                            if why is None:
                                ai_passed(sn)               # counted BEFORE the answer goes out: the page reads the round next
                        if why:
                            self._refused(act, "pass-not-now", why)
                            return self._send(409, {**phase_public(), "error": why})
                    with PHASE_LOCK:
                        blocked = ai_advance(sn, act)
                    if blocked:
                        return self._send(409, {**phase_public(), "waiting": blocked,
                                                "error": f"waiting on passes: {blocked} (retry)"})
                    with acting(sn):
                        out = self._brain(act, b)
                    if act in ("cast", "turn-up", "manifest") and PHASE["player"] != sn:
                        priority_reset(f"{sn} responded ({act})")   # an AI answered in a person's step: round again
                        ai_passed(sn)                               # …and it has had its say in the new round
                    elif act == "pass" and PHASE["player"] != sn:
                        ai_passed(sn)                               # (a no-op after the pilot's own, counted above)
                return out
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
                journal_reset()
                emit("new-game")
                SPOKEN.clear()
                with BOARD_LOCK:
                    BOARD.update(cards=[], missing={})
                if VPS:
                    with VP_LOCK:
                        reshuffle_all()                            # every AI seat: a new game
                    emit("fair", kind="sealed", **fair_public())
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

    PROXY_HEADERS = ("X-Forwarded-For", "Forwarded", "X-Real-IP", "CF-Connecting-IP", "True-Client-IP")

    def _device(self) -> str:
        """Which device is asking: its network address (this laptop under any of its own addresses counts as
        one — localhost and its LAN IP are the same machine) plus its browser. Two windows of one browser on
        one device share it, so a player keeps their seat across tabs and addresses."""
        ip = self.client_address[0].removeprefix("::ffff:")
        try:
            here = self.connection.getsockname()[0].removeprefix("::ffff:")
        except OSError:
            here = ""
        if ip in ("127.0.0.1", "::1") or ip == here:
            ip = "this-laptop"
        if any(self.headers.get(h) for h in self.PROXY_HEADERS):
            ip = "proxied:" + (self.headers.get("X-Forwarded-For") or "?").split(",")[0].strip()
        return hashlib.sha256(f"{ip}|{self.headers.get('User-Agent', '')}".encode()).hexdigest()[:24]

    def _is_local(self) -> bool:
        """This laptop itself. A tunnel or reverse proxy (cloudflared, ngrok, Tailscale Funnel) also connects
        from 127.0.0.1, so a request carrying any forwarding header is someone else, wherever it says it is
        from: it gets only what a phone on the Wi-Fi gets."""
        if self.client_address[0] not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return False
        return not any(self.headers.get(h) for h in self.PROXY_HEADERS)

    def _lan_blocked(self, method: str) -> bool:
        """A request from another device to anything outside LAN_OK: refuse it (draining a POST body
        first, so the phone gets the 403 rather than a broken pipe)."""
        if self._is_local():
            return False
        if (method, self.path.split("?")[0]) in LAN_OK:
            return False
        if method == "GET" and self.path.split("?")[0].startswith(LAN_PREFIXES):
            return False
        if PILOTS and self.path.startswith("/api/brain/") and self.headers.get("X-Seat-Key"):
            return False                                  # a pilot's own seat: _pilot_ok checks the key next
        if method == "POST":
            self._drain(int(self.headers.get("Content-Length", 0) or 0))
        self._send(403, {"error": "this page is only available on the table's laptop"})
        return True

    def _restricted_room(self) -> bool:
        """A remote caller in a room that checks seat keys (a cloud room, or STRICT_SEATS), who is not the host. The laptop itself
        and the host's brain token (even through the Worker) are never restricted; neither is a keyless LAN game, where BACK,
        HOLD and WINDOWS stay open to every device on the network (documented in docs/hud-plan.md, not changed)."""
        return STRICT_SEATS and not self._is_local() and not self._brain_ok()

    def _seat_key(self, b: dict) -> str:
        return str(b.get("key") or self.headers.get("X-Seat-Key", ""))[:200]

    def _brain_ok(self):
        return bool(VPS) and secrets.compare_digest(self.headers.get("X-Brain-Token", ""), TOKEN)

    def _seat_guard(self) -> bool:
        """Another device changing the table must hold a seat key (REMOTE_SEAT_POSTS). OWN actions name the seat
        they act for, and it must be the key's seat; TABLE actions are recorded under the key's seat. Returns
        True when it refused (and answered). The laptop itself is never asked."""
        rule = REMOTE_SEAT_POSTS.get(self.path)
        if not STRICT_SEATS or rule is None or self._is_local():
            return False
        if self.path in ("/api/phase/back", "/api/phase/hold", "/api/phase/windows") and self._brain_ok():
            try:                                           # the host working through the Worker: no seat key, recorded as "host"
                b = self._json() if int(self.headers.get("Content-Length", 0) or 0) else {}
            except (ValueError, BadRequest):
                b = {}
            b["by"] = str(b.get("by") or "host")[:30]
            self._jcache = b
            return False
        field, own = rule
        try:
            b = self._json() if int(self.headers.get("Content-Length", 0) or 0) else {}
        except (ValueError, BadRequest):
            b = {}
        self._jcache = b
        key = str(b.get("key") or self.headers.get("X-Seat-Key", ""))[:200]
        seats = [h["name"] for h in HUMANS] + sorted(PILOTS)
        holder = next((n for n in seats if seat_key_ok(n, key)), None) if key else None
        if not holder:
            self._send(403, {"error": "claim your seat first (👤): only a seated player can change the table", "need_seat": True})
            return True
        if own:                                           # a key can hold two seats (a hand-off): ask about THIS one
            named = next((n for n in seats if n.lower() == str(b.get(field, "")).strip().lower()), None)
            if not named or not seat_key_ok(named, key):
                self._send(403, {"error": f"you can only do that for your own seat ({holder})"})
                return True
            b[field] = named
            if field == "player":
                b["by"] = named
        else:
            b["by"] = holder                                  # recorded under who really did it
        return False

    def _pilot_ok(self, sn) -> bool:
        """A person driving THEIR pilot seat: the seat key in X-Seat-Key, for exactly that seat."""
        return bool(sn) and sn in PILOTS and seat_key_ok(sn, self.headers.get("X-Seat-Key", ""))

    def _seat_q(self):
        from urllib.parse import parse_qs, urlparse
        return seat(parse_qs(urlparse(self.path).query).get("seat", [None])[0])

    def _life_table(self):
        return life_table()

    def _change_life(self, player, delta, by):
        return change_life(player, delta, by)

    def _refused(self, action, kind, why):
        """An action the table refused, for the integrity audit (table/audit.py): the rate of refusals is the
        nearest thing we log to 'tried something it should not'. `why` is the engine's message, cut short."""
        _append("brain.jsonl", {"ts": round(time.time(), 2), "seat": getattr(VP, "name", None), "action": action,
                                "refused": kind, "why": str(why)[:160]})

    def _brain(self, action, b):
        """The external brain's actions. Engine-checked; each announcement is spoken as the AI."""
        from player import IllegalAction
        speak = not b.get("quiet")
        private = {}
        try:
            with VP_LOCK:
                VP.recent_draws.clear()
                VP.todo.clear()
                if action == "pass":                       # no response in this priority window
                    said = []
                elif action == "say":
                    text = str(b.get("text", "")).strip()
                    hidden = VP.private_hand()
                    leak = voice_leak(text, hidden)
                    if leak and not b.get("force"):
                        self._refused(action, "hand-leak", "")       # never the card's name: that is the secret
                        return self._send(400, {"error": f"that names '{leak}', which is still in your hand "
                                                         f"(pass force to say it anyway)"})
                    said = [text]
                elif action == "begin":
                    said, drew = VP.begin_turn()
                    private["drew"] = drew
                elif action == "land":
                    said = VP.manual_land(b["name"])
                elif action == "cast" and b.get("face_down"):
                    said = VP.cast_face_down(b["name"])
                elif action == "turn-up":
                    said = VP.turn_up(b["ref"], free=bool(b.get("free")))
                elif action == "manifest":
                    said = VP.manifest(int(b.get("n") or 1), cloak=bool(b.get("cloak")))
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
                elif action == "damage":                 # its attackers that hit a player: triggers + life
                    hits = {ref: (v[0], v[1]) for ref, v in b["hits"].items()}
                    said, deltas = VP.combat_damage(hits)
                    if b.get("no_life"):                   # the players state their own life: triggers only
                        said = [x for x in said if not re.match(r".+ deals \d+ to ", x)]
                        deltas = {k: v for k, v in deltas.items() if k == VP.name}   # its lifelink still counts
                    for who, d in deltas.items():
                        r = self._change_life(who, d, "combat")
                        if "error" in r:
                            raise IllegalAction(r["error"])
                    lt = life_table()
                    said += [f"{who} is at {lt[who]}." for who in deltas if who != VP.name and who in lt]
                elif action == "role":
                    said = VP.add_role(VP.perm(b["ref"]), b["kind"].strip().title())
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
                    said = VP.make_tokens(b["name"], int(b["power"]), int(b["toughness"]), b.get("keywords") or [],
                                          n=int(b.get("n") or 1), tapped=bool(b.get("tapped")))
                elif action == "peek":                     # PRIVATE: only the brain sees the top cards
                    private["top"] = VP.peek(int(b.get("n", 1)))
                    said = [f"I look at the top {int(b.get('n', 1))} of my library."] if b.get("announce") else []
                elif action == "topdeck":
                    said = VP.topdeck(b["name"])
                elif action == "bottom":
                    said = VP.bottom(b["name"], from_top=bool(b.get("from_top")))
                elif action == "shuffle":
                    said = VP.shuffle_library()
                elif action == "mulligan":                 # before the game: hand back, shuffle, seven new
                    try:
                        said = VP.mulligan()
                    except ValueError as e:
                        return self._send(409, {"error": str(e)})
                elif action == "graveyard-out":            # a card leaves my graveyard (stolen, exiled, returned)
                    name = str(b.get("name", "")).strip().lower()
                    hit = next((c for c in reversed(VP.graveyard) if c["name"].lower() == name), None)
                    if hit is None:
                        return self._send(400, {"error": f"no '{b.get('name')}' in your graveyard"})
                    VP.graveyard.remove(hit)
                    to = str(b.get("to", ""))[:60]
                    said = [f"{hit['name']} leaves my graveyard" + (f" — {to}." if to else ".")]
                elif action == "animate":                  # "becomes an X/Y artifact creature" (Depthshaker Titan, vehicles)
                    p = VP.perm(b["ref"])
                    card = dict(p.card)
                    card["types"] = list(dict.fromkeys((card.get("types") or []) + ["Creature"]))
                    card["power"], card["toughness"] = str(b["power"]), str(b["toughness"])
                    card["keywords"] = list(dict.fromkeys((card.get("keywords") or []) + list(b.get("keywords") or [])))
                    p.card = card
                    said = [f"{p.name} becomes a {b['power']}/{b['toughness']} artifact creature."]
                elif action == "take":                     # "cast a card from an opponent's hand" (Silent-Blade Oni)
                    src = VPS.get(b.get("from") or "")
                    if src is None or src is VP:
                        raise ValueError("take needs --from another engine seat")
                    i = next((i for i, c in enumerate(src.hand) if c["name"].lower() == b["name"].lower()), None)
                    if i is None:
                        raise ValueError(f"{b['from']} has no {b['name']} in hand")
                    VP.hand.append(src.hand.pop(i))
                    said = [f"I take {VP.hand[-1]['name']} from {b['from']}'s hand to cast it."]
                elif action == "put":                      # ninjutsu / "put onto the battlefield" from hand
                    said = VP.put(b["name"], tapped=bool(b.get("tapped")))
                elif action == "blink":
                    said = VP.blink(b["ref"])
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
                    reshuffle_all()
                    for k in LIFE:
                        LIFE[k] = 40
                    journal_reset()
                    emit("new-game")
                    emit("fair", kind="sealed", **fair_public())
                    SPOKEN.clear()
                    said = ["New game. I shuffle up and draw seven."] + fair_announcement()
                elif action == "fair-reveal":              # after the game: publish seeds, words and orders
                    FAIR["revealed"] = True
                    emit("fair", kind="revealed", **fair_public())
                    said = ["The fairness proof is published. Anyone can check that the deck order was fixed "
                            "before the first draw."]
                else:
                    self._refused(action, "unknown-action", "")
                    return self._send(400, {"error": f"unknown action '{action}'"})
        except IllegalAction as e:
            self._refused(action, "illegal", str(e))
            return self._send(400, {"error": str(e)})
        except KeyError as e:
            self._refused(action, "missing-field", str(e))
            return self._send(400, {"error": f"missing field {e}"})
        BRAIN_LAST[0] = time.time()
        if action in ("pass", "cast", "turn-up") and VP.name in PRIORITY["waiting"]:
            priority_done(VP.name)                    # answered (cast or pass): the window can close
        if action == "end" and PHASE["player"] == VP.name:   # an AI ends its turn: on to the next seat
            journal_due(VP.name, "end_of_turn")
            order = turn_order()
            with PHASE_LOCK:
                PHASE["step"] = len(STEPS) - 1
                nxt = seat_after(VP.name) or order[(order.index(VP.name) + 1) % len(order)]
            threading.Timer(0.5, lambda: (start_turn(nxt), snapshot())).start()
        if action == "begin":                         # the turn button or NEXT handed this seat its turn
            with PHASE_LOCK:                          # (the step is where ai_advance walked it: main 1)
                PHASE.update(player=VP.name, begun=True)
                if not WINDOWS.get("ai_steps", True):
                    PHASE["step"] = 0
        _append("brain.jsonl", {"ts": round(time.time(), 2), "seat": VP.name, "action": action,
                                "body": {k: v for k, v in b.items() if k != "seat"}, "said": said,
                                "drew": list(VP.recent_draws), "todo": list(VP.todo),
                                "hand_after": VP.private_hand(), "life": VP.life})   # PRIVATE: hands
        if VP.recent_draws:                         # trigger draws: the brain sees them, the table doesn't
            private["drew"] = list(VP.recent_draws) if action != "begin" else private.get("drew")
            if action == "begin" and len(VP.recent_draws) > 1:
                private["drew_all"] = list(VP.recent_draws)
        if VP.todo:
            private["todo"] = list(VP.todo)
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
    if CLOUD:
        os._exit(0)
    try:
        import voice
        voice.ollama_unload([voice.OLLAMA_MODEL, voice.REPLY_MODEL])
        print("Gemma unloaded", flush=True)
    finally:
        os._exit(0)


import signal  # noqa: E402
signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT, _shutdown)


if args.restore:
    restore(args.restore)
if not CLOUD:
    threading.Thread(target=_warm, daemon=True).start()   # first Whisper load takes seconds
mode = f"TEST MODE, any of {len(CATALOG)} card names" if args.any_card else f"deck: {len(DECK)} cards, {len(set(DECK))} distinct"
print(f"{mode}. Table (camera + mic): http://localhost:{args.port}/table   "
      f"Scan pad (AI's hand): http://localhost:{args.port}/   Show: /show   Voice: /voice", flush=True)
print("AI players: " + ", ".join(p["name"] for p in AI_PLAYERS)
      + (f" — brain: EXTERNAL (drive with table/tablectl.py)" if BRAIN_EXTERNAL else " — brain: local Gemma")
      + (" — no local model (ROUTER=code)" if NO_GEMMA else ""), flush=True)
ThreadingHTTPServer.request_queue_size = 128   # pages import ~20 modules at once; the default 5 drops some
if args.tls_cert:                              # a second, HTTPS listener: WebXR runs only on secure pages
    import ssl
    _ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    _ctx.load_cert_chain(args.tls_cert, args.tls_key or None)
    _tls = ThreadingHTTPServer((args.host, args.tls_port), H)
    _tls.socket = _ctx.wrap_socket(_tls.socket, server_side=True)
    threading.Thread(target=_tls.serve_forever, daemon=True).start()
    print(f"HTTPS (WebXR): https://<this Mac>:{args.tls_port}/xr", flush=True)
ThreadingHTTPServer((args.host, args.port), H).serve_forever()
