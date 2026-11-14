"""Table server, first cut: the scan pad for the AI's hidden hand, using this Mac's camera.

  python3 table/server.py --deck decks/example.txt
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

import snapfile

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
ap.add_argument("--decklists", choices=["auto", "declared"], default="auto",
                help="auto: a virtual seat's deck file is public as its declared list (Commander-style, the default); declared: "
                     "only what a seat chooses to declare is public (agent tables, where the full list is an edge)")
ap.add_argument("--donate-url", default="", help="optional https link to a charity's OWN donation page, shown to spectators "
                "on /watch. The table never handles the money. Off unless set (Michael picks the cause).")
ap.add_argument("--donate-name", default="", help="the charity's name for the /watch link")
ap.add_argument("--seat-rate", type=float, default=10.0,
                help="requests per second one seat key may make to the seat routes (bursts up to 3x); 0 turns it off")
ap.add_argument("--entry-fee", type=float, default=0.0,
                help="x402-style table fee in USD for claiming a seat. TEST MODE ONLY (x402_gate.py): no real money.")
ap.add_argument("--team", action="append", default=[],
                help='Two-Headed Giant: "A:Michael,Claude" (repeat per team). Teammates share one life total (30). Virtual seats only.')
ap.add_argument("--team-life", type=int, default=30, help="a team's starting life (Two-Headed Giant: 30)")
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
ap.add_argument("--pilot-auto-begin", choices=["on", "off"], default="on",
                help="a pilot seat's turn begins by itself at its untap step (untap, upkeep, draw), as soon as the others have passed")
ap.add_argument("--ping-damage", choices=["apply", "announce"], default="apply",
                help="damage or life loss to a player that isn't combat (Landfall, \"each opponent\"). apply: on a virtual-deck seat the table takes it "
                     "off, exactly as it does for combat, and says so; announce: it only says so, and the seat's owner marks it. A person's own "
                     "(real-deck) seat is always announce: that life total is theirs alone.")
ap.add_argument("--pilot-autopass", choices=["smart", "off"], default="smart",
                help="'smart': in someone else's step, a pilot seat that holds nothing it could do right now (no castable instant or "
                     "flash, no affordable activated ability, no face-down card it can turn up) is passed for at once")
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
ap.add_argument("--seed", type=int, default=None,
                help="replayable games for the dogfood loop: seat shuffles and the opening roll derive from this integer "
                     "(no fair-seed commit, so NOT provably fair; never set on a table with people). Same seed + same actions = same game")
ap.add_argument("--confirm-frames", type=int, default=2, help="same card on N frames in a row")
ap.add_argument("--clear-secs", type=float, default=1.0, help="no card in view this long before the next scan")
ap.add_argument("--token-file", default=None,
                help="where to write the brain token (default table/.brain-token); a second server, e.g. a "
                     "test run, needs its own so it doesn't lock the live game's brain out")
args = ap.parse_args()

# Every held request (a page's long-poll) is an open file. macOS starts a process at 256, so a few hundred idle connections from the
# tunnel would make the next request, localhost included, fail until they close (review 2026-11-11). Raise the soft limit.
try:
    import resource
    _soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    _want = 8192 if _hard == resource.RLIM_INFINITY else min(_hard, 8192)
    if _soft < _want:
        resource.setrlimit(resource.RLIMIT_NOFILE, (_want, _hard))
except (ImportError, ValueError, OSError):
    pass

# CLOUD mode (Linux container, e.g. Cloudflare Containers): no Apple Vision, no local Whisper/Gemma/`say`.
# Card scanning and the open mic are off until their cloud backends exist; everything else works.
CLOUD = os.environ.get("TABLE_CLOUD") == "1" or sys.platform != "darwin"
if CLOUD:
    def read_lines(*_a, **_k):                     # no OCR backend in the cloud yet: scanning finds nothing
        return []
else:
    try:
        from ocr_mac import read_lines  # noqa: E402
    except ImportError as e:                       # a Mac without pyobjc: the scan pad is off, the game itself still runs
        print(f"scan pad off ({e}); to turn it on: pip install pyobjc-framework-Vision pyobjc-framework-Cocoa", file=sys.stderr)

        def read_lines(*_a, **_k):
            return []

if not args.deck and not args.any_card:
    sys.exit("pass --deck FILE, or --any-card to test with any Magic card")


def load_catalog():
    """Every card name, cached. Order: the cached Scryfall name list; else the names in the Oracle file when that is already
    here (no network); else one request to Scryfall, well inside their rate limits. A first run with no cache and no
    network stops with the one command that fixes it, not a traceback."""
    import urllib.request
    cache = HERE / ".cache" / "card-names.json"
    if not cache.exists():
        oracle_file = HERE / ".cache" / "oracle.json"
        if oracle_file.exists():
            import oracle
            names = sorted(oracle.db())
            cache.write_text(json.dumps({"data": names}))      # same shape as Scryfall's, so later runs (and the tests) read it
            return names
        cache.parent.mkdir(exist_ok=True)
        req = urllib.request.Request("https://api.scryfall.com/catalog/card-names",
                                     headers={"User-Agent": "divinci-table/0.1", "Accept": "application/json"})
        try:
            body = urllib.request.urlopen(req, timeout=30).read().decode()
        except OSError as e:                       # URLError, timeouts, refused connections
            sys.exit(f"first run needs the card-name list and there is no network ({e}).\n"
                     "Run once with internet access: python3 table/fetch_data.py   (about 40 MB from Scryfall; after that the table is offline)")
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
        VPS[_n] = VirtualPlayer(_deck, _n, **({"seed": args.seed * 1000 + _i} if args.seed is not None else
                                              {} if args.fair_seed == "off" or args.restore else
                                              {"fair_words": {}, "fair_online": _online or False}))
        DECK_OF[_n] = _deck
        AI_PLAYERS[_i]["has_deck"] = True
        AI_PLAYERS[_i]["deck"] = VPS[_n].deck_name
FEE_GATE = None
if args.entry_fee:
    import x402_gate
    FEE_GATE = x402_gate.Gate(args.entry_fee)
    print(f"table fee: {args.entry_fee:.2f} USD per seat, TEST MODE (no real money)", flush=True)

# Two-Headed Giant teams: {seat: team}. Teammates share one life total (step 1 of 2HG; shared turn and team combat come next).
TEAMS: dict = {}
for _t in args.team:
    _tn, _, _members = _t.partition(":")
    _names = [next((n for n in VPS if n.lower() == m.strip().lower()), None) for m in _members.split(",") if m.strip()]
    if not _tn.strip() or len(_names) < 2 or None in _names:
        sys.exit(f'--team "{_t}": a team is "Name:Seat,Seat" of two or more virtual seats (--ai with --ai-deck)')
    for _n in _names:
        if _n in TEAMS:
            sys.exit(f"--team: {_n} is on two teams")
        TEAMS[_n] = _tn.strip()
    if not args.restore:
        VPS[_names[0]].share_life([VPS[n] for n in _names[1:]], args.team_life)


def teammates(name: str) -> list[str]:
    t = TEAMS.get(name)
    return [n for n, tt in TEAMS.items() if tt == t and n != name] if t else []


TEAM_TURN: dict = {"begun": set(), "done": set()}    # 2HG: which teammates have begun / ended the team's shared turn
# (seat, "upkeep" | "end step"): that seat's "at the beginning of each upkeep / each end step" already went this turn. A turn has ONE upkeep and ONE
# end step even in Two-Headed Giant, where each teammate begins and ends its half: without this, each half fired every other seat's again.
STEP_FIRED: set = set()


SEAT_BUCKETS: dict = {}        # seat -> [tokens, last refill]: one agent can't flood the table (fairness and scale)
BUCKET_LOCK = threading.Lock()


def seat_rate_ok(sn: str) -> tuple[bool, float]:
    """Token bucket per seat: args.seat_rate per second, bursts up to 3x. (ok, seconds until the next request is allowed)."""
    rate = args.seat_rate
    if rate <= 0 or not sn:
        return True, 0.0
    cap = rate * 3
    with BUCKET_LOCK:
        tok, last = SEAT_BUCKETS.get(sn, [cap, time.time()])
        now = time.time()
        tok = min(cap, tok + (now - last) * rate)
        if tok < 1:
            SEAT_BUCKETS[sn] = [tok, now]
            return False, round((1 - tok) / rate, 2)
        SEAT_BUCKETS[sn] = [tok - 1, now]
        return True, 0.0


def attacker_perm(entry: dict):
    """The attacking permanent behind a COMBAT entry, or None (a human's declared attacker has no engine permanent)."""
    try:
        v = VPS.get(entry["attacker"])
        return v.perm(entry["ref"]) if v is not None and entry.get("ref") else None
    except Exception:  # noqa: BLE001 — it left the battlefield
        return None


def owner_of(perm):
    """The seat whose battlefield holds this permanent."""
    for v in VPS.values():
        if any(x is perm for x in v.battlefield):
            return v
    return None


def hidden_names() -> dict:
    """The real name of every face-down permanent on the table -> how the board describes it ("a face-down 2/2"). A name that is also public
    (a face-up permanent, a graveyard card) is left alone: replacing it would misname the public card, and the hidden one is then ambiguous anyway."""
    public, out = set(), {}
    for v in VPS.values():
        public |= {p.name for p in v.battlefield if not p.face_down} | {c["name"] for c in v.graveyard}
    for v in VPS.values():
        for p in v.battlefield:
            if p.face_down and p.name and p.name not in public:
                out[p.name] = v.shown(p)
    return out


def public_text(text):
    """A line the table writes where everyone can read it (the event log, a table note, an announcement): never a hidden card's name.
    The engine says a face-down permanent as the board shows it; this is the backstop for any line that slipped past."""
    if not isinstance(text, str) or not text:
        return text
    for name, shown in sorted(hidden_names().items(), key=lambda kv: -len(kv[0])):
        text = re.sub(r"(?<![\w'])" + re.escape(name) + r"(?![\w'])", shown, text)
    return text


def attacker_keywords(attacker) -> list:
    v = owner_of(attacker) if attacker is not None else None
    return v.kws(attacker, my_turn=True) if v is not None else (attacker.keywords if attacker is not None else [])


def block_refusal(blocker, attacker) -> str | None:
    """Why this creature can't block that attacker, or None. Evasion the card says: flying/reach, "can't be blocked" (Combadge's rider,
    "by creatures with power 2 or less", "by artifact creatures"), and the blocker's own "can't block"."""
    bv = owner_of(blocker)
    bk = bv.kws(blocker) if bv is not None else blocker.keywords
    # The refusal goes to the defender: the attacker (and, said back, the blocker) as the BOARD shows them, never a face-down card's name
    bname = bv.shown(blocker) if bv is not None else ("a face-down creature" if blocker.face_down else blocker.name)
    if not blocker.face_down and re.search(r"\b(?:This creature|This token|It) can't block\b(?! alone)", blocker.card.get("text") or ""):
        return f"{bname} can't block"
    if blocker.no_blocking_tick and blocker.no_blocking_tick == __import__("player").DEATH_TICK[0]:
        return f"{bname} can't block this turn"
    if attacker is None:
        return None
    av = owner_of(attacker)
    ak = attacker_keywords(attacker)
    aname = av.shown(attacker) if av is not None else ("a face-down creature" if attacker.face_down else attacker.name)
    if attacker.no_block_turn is not None and av is not None and attacker.no_block_turn == av.turn:
        return f"{aname} can't be blocked this turn"
    if "Flying" in ak and "Flying" not in bk and "Reach" not in bk:
        return f"{aname} flies, and a blocker needs flying or reach"
    text = "" if attacker.face_down else (attacker.card.get("text") or "")
    m = re.search(r"can't be blocked by creatures with power (\d+) or less", text)
    if m and bv is not None and bv.stats(blocker)[0] <= int(m.group(1)):
        return f"{aname} can't be blocked by creatures with power {m.group(1)} or less"
    if "can't be blocked by artifact creatures" in text and blocker.is_("Artifact"):
        return f"{aname} can't be blocked by artifact creatures"
    return None


def can_block(blocker, attacker) -> bool:
    return block_refusal(blocker, attacker) is None


def aim_candidates(seat_name: str, slot: dict) -> list[dict]:
    """Everything that slot may legally aim at: public information, as [{at, ref, label}] (players too, for "any target")."""
    if slot.get("zone") == "option":                   # a choice that isn't a target (a mode, a colour, pay or not): its own listed options
        return [{"at": seat_name, "ref": f"opt:{i}", "label": lab} for i, lab in enumerate(slot.get("choices") or []) if f"opt:{i}" not in (slot.get("taken") or [])]
    if slot.get("zone"):
        return zone_candidates(seat_name, slot)
    v = VPS[seat_name]
    out = []
    team = {seat_name, *teammates(seat_name)}
    if slot.get("players_only"):                       # "target opponent" / "target player": players, nothing on the battlefield
        return [{"player": n, "label": n + (" (you)" if n == seat_name else "")} for n in turn_order()
                if not is_out(n) and ((slot.get("any_player") and slot.get("side") != "opp") or n not in team)]
    for n, o in VPS.items():
        if is_out(n):
            continue
        if (slot.get("side") == "own" and n != seat_name) or (slot.get("side") == "opp" and n in team):
            continue
        for p in o.battlefield:
            if aim_refusal(seat_name, slot, o, p) is None:
                out.append({"at": n, "ref": f"#{p.id}", "label": (("your " if n == seat_name else f"{n}'s ") + o.shown(p)
                                                                    + (f" {'%d/%d' % o.stats(p)}" if p.is_("Creature") and not p.face_down else ""))})
    if slot.get("player_ok"):                          # "any target" is every player, yourself and a teammate included; "an opponent" is not
        for n in turn_order():
            if not is_out(n) and (n not in team or slot.get("side") != "opp"):
                out.append({"player": n, "label": n + (" (you)" if n == seat_name else "")})
        out.sort(key=lambda o: o.get("player") in team if "player" in o else False)     # the opponents first, as before
    return out


def ward_due(seat_name: str, aim: list) -> int:
    """M29: the ward the seat owes for what it aims at: every opponent's permanent in `aim` that has ward {N}. Ward is "counter it unless you
    pay": at this table it is paid as part of aiming (with the spell's cost, or when a trigger's or ability's target is named), and a
    permanent whose ward the seat can't pay can't be aimed at. Declining is not aiming there."""
    import player as _pl
    team = {seat_name, *teammates(seat_name)}
    return sum(_pl.ward_cost(a["perm"]) for a in aim or [] if "perm" in a and a.get("owner") is not None and a["owner"].name not in team)


def pay_ward(seat_name: str, aim: list) -> list[str]:
    """Pay the ward owed for a trigger's or an ability's target, or refuse (nothing is paid, the question stays)."""
    from player import IllegalAction
    n = ward_due(seat_name, aim)
    if not n:
        return []
    v = VPS[seat_name]
    pay = v.plan_payment("{%d}" % n)
    if pay is None:
        raise IllegalAction(f"that has ward {{{n}}} and you can't pay it ({v.mana_words()}): name something else. Nothing was paid")
    v.spend(pay)
    return [f"Ward: I pay {{{n}}}."]


def spell_fits(slot: dict, card: dict) -> bool:
    """May a counterspell with this slot name that spell? ("noncreature spell", "spell with mana value 2 or less")"""
    if slot.get("noncreature") and "Creature" in (card.get("types") or []):
        return False
    return slot.get("mv") is None or float(card.get("manaValue") or 0) <= slot["mv"]


def zone_candidates(seat_name: str, slot: dict) -> list[dict]:
    """Cards in a zone a slot can name. Graveyards are public; a hand is only ever offered to its own seat (this answers that seat's own routes only)."""
    import player as _pl
    out = []
    if slot.get("zone") == "stack":                    # a spell that is cast and waiting to resolve (a held spell): public, by name
        team = {seat_name, *teammates(seat_name)}
        for n, o in VPS.items():
            h = o.__dict__.get("held_spell")
            if h and n not in team and not is_out(n) and spell_fits(slot, h["card"]):
                out.append({"at": n, "card": h["card"]["name"], "label": f"{n}'s {h['card']['name']} (being cast)"})
        return out
    seats = [seat_name] if slot.get("side") == "own" or slot.get("zone") in ("hand", "library") else list(VPS)
    for n in seats:
        o = VPS[n]
        zone = {"graveyard": o.graveyard, "hand": o.hand, "library": o.library}[slot["zone"]]
        seen = set()
        for c in zone:
            if c["name"] not in seen and _pl.card_fits(c, slot.get("phrase") or "", slot.get("mv")):
                seen.add(c["name"])
                where = "" if slot["zone"] in ("hand", "library") else (" (your graveyard)" if n == seat_name else f" ({n}'s graveyard)")
                out.append({"at": n, "card": c["name"], "label": c["name"] + where})
    return out


def aim_refusal(seat_name: str, slot: dict, owner, p) -> str | None:
    """Why this permanent can't be aimed at by that slot, or None. A face-down creature is a creature; a hexproof one only its controller's."""
    kinds = slot.get("kinds") or ["Creature"]
    is_ship = "Spacecraft" in (p.card.get("subtypes") or [])
    fits = any((k == "Creature") if p.face_down else (p.is_(k) or (k == "Spacecraft" and is_ship)) for k in kinds)   # face down it is a creature and nothing else: never the card's real type
    if not fits:
        return f"{owner.shown(p)} isn't {' or '.join(kinds).lower()}"
    if any(((k == "Creature") if p.face_down else p.is_(k)) for k in slot.get("not_kinds") or []):
        return f"{owner.shown(p)} is {slot['not_kinds'][0].lower()}: that can't be aimed at it"
    if slot.get("power_min") is not None and owner.stats(p)[0] < slot["power_min"]:
        return f"{owner.shown(p)} has power {owner.stats(p)[0]}: this needs {slot['power_min']} or more"
    if slot.get("attacking") and not any(e.get("ref") == f"#{p.id}" and e.get("attacker") == owner.name for e in COMBAT["pending"] + COMBAT["hits"]):
        return f"{owner.shown(p)} isn't attacking"
    if slot.get("in_combat") and not in_combat(owner, p):
        return f"{owner.shown(p)} isn't attacking or blocking"
    if owner.name != seat_name and "Hexproof" in owner.kws(p):
        return f"{owner.shown(p)} has hexproof"
    if owner.name != seat_name and owner.name not in teammates(seat_name) and seat_name in VPS:
        w_ = __import__("player").ward_cost(p)
        if w_ and VPS[seat_name].plan_payment("{%d}" % w_) is None:
            return f"{owner.shown(p)} has ward {{{w_}}}: you can't pay it right now"
    if slot.get("not_id") is not None and p.id == slot["not_id"]:
        return f"{owner.shown(p)} is the card asking: it says another"
    mv_ = 0.0 if (p.token or p.face_down) else float(p.card.get("manaValue") or 0)      # a token or a face-down card has mana value 0
    if slot.get("mv_max") is not None and mv_ > slot["mv_max"]:
        return f"{owner.shown(p)} has mana value {mv_:g}: this needs {slot['mv_max']} or less"
    if slot.get("subtype") and slot["subtype"].lower() not in [x.lower() for x in (p.card.get("subtypes") or [])] and not p.face_down:
        return f"{owner.shown(p)} isn't a {slot['subtype']}"
    if slot.get("token_only") and not p.token:
        return f"{owner.shown(p)} isn't a token"
    if slot.get("nontoken") and p.token:
        return f"{owner.shown(p)} is a token"
    return None


def first_legal_aim(seat_name: str, slots: list) -> list | None:
    """A targets list the table will accept, one entry per slot, never naming the same permanent twice; {} for an optional slot left out.
    None: a required slot has nothing left (golden path: Common Goal's two "your creature" slots both named the first one, and following
    the list retried a refused cast 1,400 times)."""
    out, used = [], set()
    for sl in slots:
        if sl.get("optional"):
            out.append({})
            continue
        pick = next((o for o in aim_candidates(seat_name, sl) if (o.get("at"), o.get("ref"), o.get("player"), o.get("card")) not in used), None)
        if pick is None:
            return None
        used.add((pick.get("at"), pick.get("ref"), pick.get("player"), pick.get("card")))
        out.append({k: v for k, v in pick.items() if k != "label"})
    return out


def resolve_aim(seat_name: str, slots: list, wanted: list, pilot: bool):
    """A cast's `targets` list, checked before anything is paid. Returns the aim list the engine carries out; raises when it is illegal."""
    from player import IllegalAction
    if not isinstance(wanted, list):
        raise IllegalAction("targets is a list: [{\"at\": SEAT, \"ref\": \"#id\"}], one per target, in the order the card names them")
    aim, picked = [], []
    wanted = list(wanted)
    for slot in slots:
        w = wanted.pop(0) if wanted else None
        if w is None or (isinstance(w, dict) and not w):
            if slot.get("optional"):
                continue
            raise IllegalAction(f"name what this aims at: {slot.get('label')}. Nothing was paid. You can: "
                                + (", ".join(c["label"] for c in aim_candidates(seat_name, slot)) or "(no legal target right now)"))
        if not isinstance(w, dict):
            raise IllegalAction("each target is {\"at\": SEAT, \"ref\": \"#id\"} or {\"player\": SEAT}")
        if slot.get("zone") == "option":
            m_ = re.fullmatch(r"opt:(\d+)", str(w.get("ref") or ""))
            if not m_ or int(m_.group(1)) >= len(slot.get("choices") or []):
                raise IllegalAction("choose one of: " + " | ".join(slot.get("choices") or []))
            if str(w.get("ref")) in (slot.get("taken") or []):
                raise IllegalAction("that one is already chosen: choose another")
            if any(a_.get("option") == int(m_.group(1)) for a_ in aim):
                raise IllegalAction("that one is already chosen: choose another")
            aim.append({"option": int(m_.group(1))})
            continue
        if slot.get("zone"):
            at_ = seat(str(w.get("at") or seat_name))
            nm_ = str(w.get("card") or "")
            if at_ is None or not any(c_.get("at") == at_ and c_.get("card") == nm_ for c_ in zone_candidates(seat_name, slot)):
                raise IllegalAction(f"{nm_ or 'that'} isn't something this can name. Nothing was paid")
            o_ = VPS[at_]
            if slot["zone"] == "stack":
                aim.append({"spell": o_.__dict__["held_spell"]["card"], "owner": o_})
                continue
            zone_ = {"graveyard": o_.graveyard, "hand": o_.hand, "library": o_.library}[slot["zone"]]
            aim.append({"card": next(c_ for c_ in zone_ if c_["name"] == nm_), "owner": o_})
            continue
        if w.get("player"):
            if not slot.get("player_ok"):
                raise IllegalAction(f"{slot.get('label')}: this can't aim at a player")
            nm = seat(str(w["player"])) or next((h["name"] for h in HUMANS if h["name"].lower() == str(w["player"]).lower()), None)
            if nm is None:
                raise IllegalAction(f"no player '{w['player']}'")
            if slot.get("players_only") and not any(c_.get("player") == nm for c_ in aim_candidates(seat_name, slot)):
                raise IllegalAction(f"{slot.get('label')}: {nm} isn't a player this can name")
            aim.append({"player": nm})
            continue
        if slot.get("players_only"):
            raise IllegalAction(f"{slot.get('label')}: name a player ({{\"player\": SEAT}})")
        at = seat(str(w.get("at") or seat_name))
        if at is None:
            raise IllegalAction(f"no seat '{w.get('at')}'")
        o = VPS[at]
        p = o.perm(str(w.get("ref", "")))
        why = aim_refusal(seat_name, slot, o, p)
        if why is None and slot.get("side") == "own" and at != seat_name:
            why = f"{o.shown(p)} isn't yours"
        if why is None and slot.get("side") == "opp" and (at == seat_name or at in teammates(seat_name)):
            why = f"{o.shown(p)} isn't an opponent's"
        if why is None and p in picked:
            why = f"{o.shown(p)} is already named"
        if why:
            raise IllegalAction(f"{why}. Nothing was paid")
        picked.append(p)
        aim.append({"perm": p, "owner": o})
    return aim


def legal_actions(sn: str) -> dict:
    """What seat `sn` may do right now, each with the exact request that does it (POST /api/brain/<action> with `body`). Built
    from the engine's own checks (castable(), land drop, untapped/summoning sickness, Station text, incoming attackers, the
    pass round), so an outside agent never has to guess a name format or a timing rule. The server still checks every action
    when it is sent: this list is a guide, not a permission."""
    v = VPS[sn]
    step = STEPS[PHASE["step"]] if PHASE.get("player") else None
    out = {"seat": sn, "player": PHASE.get("player"), "step": step, "my_turn": on_turn(sn), "actions": []}
    if MULL["on"] and not GAME_OVER.get("over"):       # T15: the opening-hand step: keep (with what goes on the bottom) or mulligan
        mv_ = mull_private(sn)
        out["mulligan"] = {k: x for k, x in mv_.items() if k != "rule_bottom"}
        if mv_["kept"]:
            out["note"] = "you have kept; waiting for " + (", ".join(mv_["waiting"]) or "the first turn")
            return out
        out["actions"].append({"action": "keep", "body": {"seat": sn, **({"bottom": mv_["rule_bottom"]} if mv_["bottom"] else {})},
                               "why": f"keep {mv_['keeps']}: {mv_['advice']['text']}" + (f" (bottom {mv_['bottom']}: these are the rule's picks, choose your own)" if mv_["bottom"] else "")})
        if mv_["can_mulligan"]:
            out["actions"].append({"action": "mulligan", "body": {"seat": sn}, "why": f"a new seven, keeping {max(0, mv_['keeps'] - 1)}"})
        out["note"] = "opening hand: keep or mulligan (London: a new seven, then one card on the bottom per mulligan when you keep)"
        return out
    if PHASE.get("player") is None or GAME_OVER.get("over") or is_out(sn):
        out["note"] = "the game hasn't started" if PHASE.get("player") is None else "the game is over" if GAME_OVER.get("over") else "you have lost"
        return out
    A = out["actions"]
    add = lambda action, body, why: A.append({"action": action, "body": {"seat": sn, **body}, "why": why})
    if v.__dict__.get("pending_choices"):
        settle_choices()                               # (what it was asked about may have gone since)
    pend_ = v.__dict__.get("pending_choices") or []
    if pend_:                                          # the table is waiting on this seat's answer: that is all it can do
        it_ = pend_[0]
        for o_ in aim_candidates(sn, it_["slot"]):
            add("choose", {"id": it_["id"], "target": {k: x for k, x in o_.items() if k != "label"}}, f"{it_['source']}: {o_['label']}")
        if it_["slot"].get("optional"):
            add("choose", {"id": it_["id"], "target": {}}, f"{it_['source']}: no target")
        out["note"] = f"{it_['source']} is waiting on your choice: {it_['text'][:100]}"
        return out
    import re as _re
    import player as _pl
    castable = {o[1]["name"]: o[1] for o in v.castable()}
    instant = lambda c: "Instant" in (c.get("types") or []) or "Flash" in _pl.printed_keywords(c)
    modal = lambda c: bool(_re.search(r"choose one\b", c.get("text") or "", _re.I))
    def offer_cast(n, c, xb_):
        """The cast entries for one castable card: one per mode / per legal Aura target, with the first legal targets, or none when it has no legal target."""
        add = (lambda action, body, why, _x=xb_: A.append({"action": action, "body": {"seat": sn, **body, **_x},
                                                            "why": why + (" (from exile)" if _x else "")}))
        extra = {"mode": 1} if modal(c) else {}
        if modal(c) and ("Instant" in c["types"] or "Sorcery" in c["types"]):
            extra = {"modes": [1]}                     # the engine's field is `modes`, 1-based (an agent that sent `mode` was refused)
            sp = _pl.modal_options(c)
            if sp and sp[0] == 1:                      # one entry per mode the table can aim, or carry out, right now
                for i, mtext in enumerate(sp[1]):
                    tg_ = first_legal_aim(sn, _pl.card_slots(c, mtext))
                    if tg_ is not None:
                        add("cast", {"name": n, "modes": [i + 1], **({"targets": tg_} if tg_ else {})}, f"mode {i + 1}: {mtext[:60]}")
                return
        # 2026-11-13 retro (C2): "Choose two —" (Austere Command) was listed as a plain cast with no modes, and following the list got
        # 400 "is modal — choose 2". `modal` above only knows "choose one". One entry per legal pair (or triple), `modes` as the engine
        # wants them (1-based, in the card's order), with the first legal targets when a chosen mode aims at something; a combination
        # with a mode that has nothing legal to aim at is left out. No entry at all if there is none: it can't be cast right now.
        sp = _pl.modal_options(c) if ("Instant" in c["types"] or "Sorcery" in c["types"]) else None
        if sp and sp[0] > 1:
            import itertools as _it
            for combo in _it.combinations(range(len(sp[1])), sp[0]):
                tg_ = first_legal_aim(sn, [s_ for i in combo for s_ in _pl.card_slots(c, sp[1][i])])
                if tg_ is not None:
                    add("cast", {"name": n, "modes": [i + 1 for i in combo], **({"targets": tg_} if tg_ else {})},
                        "modes " + " + ".join(str(i + 1) for i in combo) + ": " + " / ".join(sp[1][i][:40] for i in combo))
            return
        if "Aura" not in (c.get("subtypes") or []) and not (modal(c) and ("Instant" in c["types"] or "Sorcery" in c["types"])):
            sl = _pl.card_slots(c)
            tg_ = first_legal_aim(sn, sl)
            if tg_ is None:
                return                                 # a spell with no legal target can't be cast
            if sl:
                add("cast", {"name": n, "targets": tg_}, "you can pay for it; targets are the first legal ones: choose your own")
                return
        if "Aura" in (c.get("subtypes") or []):        # an Aura needs a target: one entry per legal one (golden-path test:
            if _pl.is_hostile_aura(c):                 # without it an agent retried an untargeted Aura 1,489 times)
                # 2026-11-13 retro (C3): one entry per enemy creature, virtual seat or not. (This used to read "seats that are not on turn",
                # which is the caster only on the caster's own turn; and it was never reached at an all-virtual table: see hostile_targets.)
                seen_ = set()
                for o, x in foe_creatures(sn):
                    if (o, x.name) not in seen_:               # the cast names its target by name: two of the same creature are one entry
                        seen_.add((o, x.name))
                        add("cast", {"name": n, "on": x.name, **extra}, f"put it on {o}'s {x.name}")
                for oc in v.opponents_cards("Creature"):       # a real-deck player's announced creatures (the table knows them by name only)
                    if not any(nm == oc.get("name") for _, nm in seen_) and oc.get("name"):
                        seen_.add(("", oc["name"]))
                        add("cast", {"name": n, "on": oc["name"], **extra}, f"put it on {oc['name']} (announced by another player)")
            else:
                for x in v.aura_targets(c):
                    add("cast", {"name": n, "on": f"#{x.id}", **extra}, f"enchant your {v.shown(x)}")
            return
        add("cast", {"name": n, **extra}, "modal: pick mode 1..n" if modal(c) else "you can pay for it")

    def offer_kicked(n, c, xb_):
        """Kicker is an optional extra: a second entry, `kicked: true` (and `kick_sac` for a sacrifice kicker), when it can be paid."""
        k = _pl.kicker_of(c)
        if not k:
            return
        if k[0] == "mana":
            if v.plan_payment((c.get("manaCost") or "") + k[1], -v.auto_discount(c)) is None:
                return
            body = {"kicked": True}
        else:
            fits = [q for q in v.battlefield if any(q.is_(w.strip().capitalize()) for w in k[1].split(" or "))]
            if not fits:
                return
            body = {"kicked": True, "kick_sac": f"#{min(fits, key=lambda q: v.stats(q)[0] if q.is_('Creature') else 0).id}"}
        sl = _pl.card_slots({**c, "text": _pl.kicked_text(c.get("text") or "", True)})
        tg_ = first_legal_aim(sn, sl)
        if tg_ is None:
            return
        A.append({"action": "cast", "body": {"seat": sn, "name": n, **body, **({"targets": tg_} if sl else {}), **xb_},
                  "why": f"kicked ({k[1]}): " + ("sacrifice " + k[1] if k[0] == "sac" else "pay it too")})

    if on_turn(sn):
        auto = sn in PILOTS and args.pilot_auto_begin == "on"          # the table begins a pilot's turn itself: don't offer it
        early = STEPS.index(step) < STEPS.index("main 1")
        if early and not auto and not (PHASE.get("begun") if PHASE["player"] == sn else sn in TEAM_TURN["begun"]):
            add("begin", {}, "start your turn: untap, draw")
        if early:                                     # nothing to do before main 1; offering `end` here made a model skip whole
            out["note"] = "your turn is starting (untap, upkeep, draw): wait for main 1"   # turns (Gemma, game 2)
            return out
        main = step in ("main 1", "main 2")
        if main and not v.land_played:
            def offer_land(c_, extra_, why_):
                """One land's entries. A bounce land (Dimir Aqueduct: "return a land you control to its owner's hand") is refused without
                its choice, so it is listed once per land it could return (one per name and tapped state: four Islands are one entry, a
                tapped one first, since it has already made its mana) and last for itself. A listed move must be sendable as it stands:
                the plain entry was refused every time (the first public agent game, 2026-11-12)."""
                if "return a land you control to its owner's hand" not in (c_.get("text") or ""):
                    return add("land", {"name": c_["name"], **extra_}, why_)
                others_, seen_ = [p_ for p_ in v.battlefield if p_.is_("Land") and not p_.face_down], set()
                if not others_:                            # the only land it can return is itself: the engine does that unasked
                    return add("land", {"name": c_["name"], **extra_}, why_ + " (it returns itself to your hand: you control no other land)")
                for p_ in sorted(others_, key=lambda p_: (not p_.tapped, p_.name, p_.id)):
                    if (p_.name, p_.tapped) not in seen_:
                        seen_.add((p_.name, p_.tapped))
                        add("land", {"name": c_["name"], "bounce": f"#{p_.id}", **extra_}, why_ + f"; it returns {p_.name}{' (tapped)' if p_.tapped else ''} to your hand")
                add("land", {"name": c_["name"], "bounce": "self", **extra_}, why_ + "; it returns itself to your hand (no land gained)")
            for n in sorted({c["name"] for c in v.hand if "Land" in c["types"]}):
                offer_land(next(c for c in v.hand if c["name"] == n), {}, "your land for the turn")
            for e_ in v.impulse_zone():                    # a land it may play from exile (Reckless Impulse): the same land drop
                if "Land" in (e_["card"].get("types") or []) and not any(a["action"] == "land" and a["body"].get("from_exile") and a["body"]["name"] == e_["card"]["name"] for a in A):
                    offer_land(e_["card"], {"from_exile": True}, f"from exile ({e_.get('by') or 'impulse'}, {v.impulse_window(e_)}): your land for the turn")
        for hc_ in {c_["name"]: c_ for c_ in v.hand}.values():
            cc_ = _pl.cycle_cost(hc_)
            if cc_ and v.plan_payment(cc_) is not None:
                add("cycle", {"name": hc_["name"]}, f"basic landcycling {cc_}")
        for bc in (v.graveyard if main else []):         # Beam me up: from the graveyard, returning a creature
            cost_ = _pl.beam_cost(bc)
            mine_c = [p for p in v.battlefield if p.is_("Creature")]
            if cost_ and mine_c and v.plan_payment(cost_) is not None:
                sl_ = _pl.card_slots(bc)
                tg_ = first_legal_aim(sn, sl_)
                if tg_ is not None and not any(a["action"] == "cast" and a["body"].get("beam") and a["body"].get("name") == bc["name"] for a in A):
                    add("cast", {"name": bc["name"], "beam": f"#{mine_c[0].id}", **({"targets": tg_} if sl_ else {})}, f"Beam me up {cost_}: from your graveyard, returning {v.shown(mine_c[0])}")
        xcast_ = {o[1]["name"]: o[1] for o in v.exile_castable()}       # cards it may play from exile: cast as from the hand, marked from_exile
        for n, c, xb_ in [*((n_, c_, {}) for n_, c_ in sorted(castable.items())), *((n_, c_, {"from_exile": True}) for n_, c_ in sorted(xcast_.items()))]:
            if not (main or instant(c)):
                continue
            offer_cast(n, c, xb_)
            offer_kicked(n, c, xb_)
        add = lambda action, body, why: A.append({"action": action, "body": {"seat": sn, **body}, "why": why})
        tc_ = v.top_castable() if v.top_sources(_pl.LOOK_TOP) else None   # Zefram Cochrane: the top card, once each turn
        if tc_ and (main or instant(tc_[0])) and "Aura" not in (tc_[0].get("subtypes") or []):
            sl_ = _pl.card_slots(tc_[0])
            tg_ = first_legal_aim(sn, sl_)
            if tg_ is not None:
                add("cast", {"name": tc_[0]["name"], "from": "library-top", **({"targets": tg_} if sl_ else {})}, "from the top of your library (once each turn)")
        for za_ in v.zone_abilities():                 # a card in the graveyard or hand with an ability used from there
            if za_["payable"] and za_["runs"] and (main or not za_["sorcery"]):
                tg_ = first_legal_aim(sn, za_.get("slots") or [])
                if tg_ is not None:
                    add("activate", {"card": za_["name"], "zone": za_["zone"], **({"targets": tg_} if za_.get("slots") else {})},
                        f"{za_['cost']}, {za_['how'].lower()}: {za_['effect'][:70]}")
        if main:
            crew = [p for p in v.battlefield if p.is_("Creature") and not p.tapped]
            for ship in v.battlefield:
                th = _pl.station_top(ship.card)
                if not ship.face_down and _pl.has_station(ship.card) and th is not None and ship.charge < th:
                    for c in crew:
                        if c.id != ship.id:
                            add("station", {"ship": f"#{ship.id}", "crew": f"#{c.id}"}, f"{v.stats(c)[0]} charge onto {ship.name} ({ship.charge}/{th})")
            for gear in v.battlefield:                     # Equip {N}: only what the mana will pay for, and never onto the creature it is already on
                cost = _pl.equip_cost(gear.card) if _pl.is_equipment(gear.card) and not gear.face_down else None
                if cost and v.plan_payment(cost) is not None:
                    for c in v.battlefield:
                        if (c.is_("Creature") or c.face_down) and gear.attached_to != c.id:
                            add("equip", {"equipment": f"#{gear.id}", "creature": f"#{c.id}"}, f"pay {cost}: {gear.name} onto {v.shown(c)}")
        if step in ("main 1", "beginning of combat", "declare attackers"):
            foes = sorted({TEAMS.get(n) or n for n in turn_order() if not on_turn(n) and not is_out(n)})
            rep_ = v.attack_report()                   # T16: the engine's own rule, "can't attack alone" included
            ready = [p for p in v.battlefield if (rep_.get(p.id) or {}).get("can_attack")]
            ready = [p for p in ready if p.id not in (ATTACKED.get("ids") or ()) or ATTACKED.get("key") != (sn, v.turn)]
            if ready and foes:
                add("attack", {"assign": {f"#{p.id}": foes[0] for p in ready}}, f"attack with any of these; defenders: {', '.join(foes)}")
        # `damage` once per turn, after this seat attacked, when every attacker has been answered (blocked or let through) or the
        # defenders had DEFENDER_GRACE seconds: offering it at once let a list-following agent skip the defenders' window
        mine_ = [e for e in COMBAT["pending"] if e["attacker"] == sn]
        attacked = ATTACKED.get("key") == (sn, v.turn) and bool(ATTACKED.get("ids"))
        if attacked and DAMAGED.get("key") != (sn, v.turn):
            if unanswered_by_people(sn):
                out["note"] = "waiting for " + ", ".join(sorted({e["defender"] for e in unanswered_by_people(sn)})) + " to block or take it"
            elif not mine_ or all(time.time() - e.get("at", 0) > DEFENDER_GRACE for e in mine_):
                add("damage", {}, "deal your attackers' damage")
            else:
                out["note"] = f"waiting for the defenders to block (or not); damage opens in {DEFENDER_GRACE:g} s at most"
        # While the defenders still have their time, neither Next nor End is on the list: following the list must not take their block away
        # (golden path: an attacker's listed End dealt the damage while the defender's listed block was on its way, and the block was refused).
        waiting_blocks = any(time.time() - e.get("at", 0) <= DEFENDER_GRACE for e in mine_) or bool(unanswered_by_people(sn))
        if not waiting_blocks:
            if step != "cleanup" and step != "end step":
                add("next", {}, f"on to {STEPS[min(STEPS.index(step) + 1, len(STEPS) - 1)]}")
            add("end", {}, "end your turn" + (" (your teammate ends too)" if teammates(sn) else ""))
    else:
        for e in combat_incoming(sn):
            ref = e.get("ref") or e["creature"]
            ap_ = attacker_perm(next((x for x in COMBAT["pending"] if (x.get("ref") or x["name"]) == ref), {"attacker": None}))
            able = [p for p in v.battlefield if p.is_("Creature") and not p.tapped and p.id not in COMBAT["used"] and can_block(p, ap_)]
            if e.get("menace"):                            # menace: two or more blockers, or none
                if len(able) >= 2:
                    add("block", {"attacker": ref, "blockers": [f"#{able[0].id}", f"#{able[1].id}"]},
                        f"block {e['creature']} ({e['power']}) with {v.shown(able[0])} and {v.shown(able[1])}: it has menace")
            else:
                for p in able:
                    add("block", {"attacker": ref, "blocker": f"#{p.id}"}, f"block {e['creature']} ({e['power']}) with {v.shown(p)}")
            add("block", {"attacker": ref}, f"no block: take {e['power']}")
        for n, c in sorted(castable.items()):
            if instant(c):                                 # with its targets or mode, as on your own turn (a bare name was refused: "name what this aims at")
                offer_cast(n, c, {})
                offer_kicked(n, c, {})
        ps = passes_state()
        if sn in ps.get("need", []) and sn not in ps.get("passed", []):
            add("pass", {"player": PHASE["player"], "step": step}, "pass priority" + (" (it's your turn to answer)" if ps.get("next") == sn else ""))
    return out


_legal_actions_before_v6c = legal_actions


def legal_actions(sn: str) -> dict:                  # noqa: F811 — v6c: the same list, put right for combat (combat_legal)
    return combat_legal(sn, _legal_actions_before_v6c(sn))


def on_turn(name: str | None) -> bool:
    """Is it this seat's turn? In Two-Headed Giant a team shares its turn: PHASE["player"] is the team's lead seat and every
    teammate is on turn with it. Without teams this is exactly `name == PHASE["player"]`."""
    cur = PHASE.get("player")
    return name is not None and cur is not None and (name == cur or (bool(TEAMS.get(name)) and TEAMS.get(name) == TEAMS.get(cur)))


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


def lobby_public() -> dict:
    """T5: this table and, if the host listed them (TABLE_SIBLINGS="http://127.0.0.1:8801,…"), the other tables on this laptop: who sits where,
    whose turn, life, whether it's over, and the /watch link. Public information only; a sibling that doesn't answer in a second is "not running"."""
    import urllib.request as _ur
    lt = life_table()
    me = {"url": "", "game_id": FAIR.get("game"), "turn": PHASE.get("player"), "step": STEPS[PHASE["step"]] if PHASE.get("player") else None,
          "over": bool(GAME_OVER.get("over")), "winner": GAME_OVER.get("winner"),
          "seats": [{"name": n, "kind": "person" if n in persons() else "bot", "life": lt.get(n),
                     "claimed": n in SEAT_KEYS or n in SEAT_PROXY} for n in turn_order() or list(lt)]}
    others = []
    for u in [x.strip().rstrip("/") for x in os.environ.get("TABLE_SIBLINGS", "").split(",") if x.strip()][:8]:
        try:
            with _ur.urlopen(u + "/api/lobby?self=1", timeout=1.0) as r:
                d = json.loads(r.read() or b"{}")
            others.append({**(d.get("table") or {}), "url": u, "running": True})
        except Exception:                              # noqa: BLE001
            others.append({"url": u, "running": False})
    return {"table": me, "others": others}


def new_game_id() -> str:
    """T3: one id per game, unique even for two tables started in the same second (the start time alone was shared: two tables then wrote into
    the same research and save folder). It names that folder, and /api/phase and /api/events carry it so a tool can tell a new game from the last."""
    return f"{time.strftime('%Y%m%d-%H%M%S')}-p{args.port}-{secrets.token_hex(2)}"


FAIR = {"game": new_game_id(), "words": {}, "revealed": False}


SEED_GAME = [0]                                   # games started under --seed, so each reshuffle differs but replays the same


def reshuffle_all():
    """A new game for every AI seat. With fair seeding, each shuffle is sealed with the players' secret
    words collected since the last game (POST /api/fair/word), and the words are then cleared."""
    from player import VirtualPlayer
    words = dict(FAIR["words"])
    MULL.update(on=False, done=False, first=None, since=0.0)       # a new game has new opening hands
    FAIR.update(game=new_game_id(), words={}, revealed=False)
    online = {}
    if args.fair_seed == "online":
        import fair
        online = fair.online_parts()                  # once per game, shared by every seat
    SEED_GAME[0] += 1
    for _i, n in enumerate(list(VPS)):
        VPS[n] = VirtualPlayer(DECK_OF[n], n, **({"seed": args.seed * 1000 + SEED_GAME[0] * 10 + _i} if args.seed is not None else
                                                 {} if args.fair_seed == "off" else
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


_SET_CARDS: dict | None = None


def set_card(name: str) -> dict | None:
    """A card of the set being played, by name, from the set's own card file ($TABLE_CARDS_JSON, else table/.cache/cards-trk.json: the Scryfall-
    shaped list the decks were built from), in the shape oracle.card gives. For /api/card when the Oracle bulk file does not have the card (a
    set newer than the download). It is a STATIC file of every card in the set: it is not the decks at this table and knows nothing about the
    game, so an answer says only what a card anyone can name says, never whether (or where) a copy of it is in play, in a hand or in a library."""
    global _SET_CARDS
    if _SET_CARDS is None:
        _SET_CARDS = {}
        for f in (os.environ.get("TABLE_CARDS_JSON"), HERE / ".cache" / "cards-trk.json"):
            try:
                if f and Path(f).expanduser().is_file():
                    for c in json.loads(Path(f).expanduser().read_text()):
                        f0 = (c.get("card_faces") or [c])[0]
                        _SET_CARDS.setdefault(c["name"], {"cost": c.get("mana_cost") or f0.get("mana_cost") or "", "type": c.get("type_line") or f0.get("type_line") or "",
                                                          "text": "\n//\n".join(x.get("oracle_text") or "" for x in (c.get("card_faces") or [c])),
                                                          "power": f0.get("power"), "toughness": f0.get("toughness")})
                    break
            except Exception as e:                      # noqa: BLE001 — a preview's text must never stop the table
                print(f"set_card: {f}: {e}", file=sys.stderr, flush=True)
    return _SET_CARDS.get(name)


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
            "text": c.get("text") or "", "pt": pt, **kw}


def _by_hand_public(card: dict) -> list:
    try:
        import card_coverage as _cv
        return _cv.by_hand_lines(card)
    except Exception:                                  # noqa: BLE001 — a note must never stop the board
        return []


def board3d() -> dict:
    """Every seat's battlefield, command zone and graveyard — public information only. AI seats come from
    the engine (face-down cards stay nameless); human seats from PUBLIC_BOARD."""
    seats = []
    lt = life_table()
    for h in HUMANS:
        b = PUBLIC_BOARD.get(h["name"], {})
        perms = [_card_view(p.get("name") if not p.get("face_down") else None,
                            **{k: p[k] for k in ("tapped", "token", "counters", "note", "pt", "zone", "by", "from") if k in p})
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
                    perms.append(_card_view(p.name, id=p.id, tapped=p.tapped, token=p.token, counters=p.counters, charge=p.charge,
                                            attached_to=p.attached_to, role=p.role, pt=pt, stun=p.stun, damage=p.damage, lore=p.lore, quest=p.quest,
                                            keywords=v.kws(p, my_turn=PHASE.get("player") == n), sick=p.sick and p.is_("Creature"),
                                            attached_names=[v.shown(a) for a in v.battlefield if a.attached_to == p.id],
                                            by_hand=_by_hand_public(p.card), text=p.card.get("text") or "",
                                            equipment=p.card.get("subtypes") and "Equipment" in p.card["subtypes"] or False,
                                            type=p.card.get("type") or " ".join(p.types)))
            seats.append({"name": n, "kind": "ai", "commander": (v.commander or {}).get("name"),
                          "commander_card": _card_view((v.commander or {}).get("name")) if v.commander else None, "life": v.life,
                          "hand": len(v.hand), "library": len(v.library), "commander_in_zone": v.cmdr_in_zone,
                          "commander_tax": 2 * v.cmdr_casts, "permanents": perms,
                          "graveyard": [c["name"] for c in v.graveyard][-100:], "graveyard_count": len(v.graveyard),
                          "exile": [c["name"] for c in v.exile_zone()][-100:],          # face up from its library: public (impulse draw)
                          "playable_from_exile": [{"name": e["card"]["name"], "until": v.impulse_window(e)} for e in v.impulse_zone()]})
    for x in seats:
        if is_out(x["name"]):                         # they've lost: every card they own has left the game
            x.update(out=True, permanents=[], graveyard=[], graveyard_count=0, commander_in_zone=False, hand=0, playable_from_exile=[])
    order = turn_order()
    seats.sort(key=lambda x: order.index(x["name"]) if x["name"] in order else 99)
    return {"seats": seats, "turn": PHASE.get("player"), "step": STEPS[PHASE["step"]]}


REFUSED_SEEN: dict = {}          # (seat, action, kind) -> when a public `refused` event last said so (R24)
CONCEDED: set = set()            # seats that conceded (R15): out of the game with their life untouched. Cleared by a new game.


def is_out(name: str) -> bool:
    """A player at 0 life or less has lost, and so has one who conceded: their cards leave the game and their turns are skipped."""
    if name in CONCEDED:
        return True
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
        if not is_out(n) and not (TEAMS.get(n) and TEAMS.get(n) == TEAMS.get(name)):   # 2HG: the turn goes team to team
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
            PHASE["combat_save"] = combat_save()      # v6c (M17): the combat, who attacked, damage dealt, game over: saved with the turn
            snapfile.SNAP_SEQ[0] += 1
            seq = snapfile.SNAP_SEQ[0]
            blob = pickle.dumps({"VPS": VPS, "DECK_OF": DECK_OF, "LIFE": LIFE, "FAIR": FAIR, "PHASE": PHASE,
                                 "ORDER": turn_order(), "EVENTS": list(EVENTS[-1500:]), "HAND_N": HAND_N, "PUBLIC_BOARD": PUBLIC_BOARD, "WINDOWS": WINDOWS, "TODOS": TODOS, "PLACED": sorted(PLACED),
                                 "HIGHROLL": HIGHROLL, "SEAT_KEYS": SEAT_KEYS, "SEAT_DEVICES": SEAT_DEVICES, "AUTOPASS": AUTOPASS, "SEAT_PROXY": SEAT_PROXY, "SEAT_INVITES": SEAT_INVITES, "DECKDECL": DECKDECL, "MULL": dict(MULL), "CONCEDED": sorted(CONCEDED), "ev_id": _ev_id[0], "saved": time.time()})
        snapfile.write_snapshot(research_dir() / "snapshot.pkl", blob, seq)
    except Exception as e:                            # never let saving break the table
        print(f"snapshot failed: {e}", flush=True)


def restore(path: str):
    import pickle
    import player
    d = pickle.loads(Path(path).read_bytes())
    VPS.clear(); VPS.update(d["VPS"]); DECK_OF.update(d["DECK_OF"])
    LIFE.update(d["LIFE"]); FAIR.update(d["FAIR"]); PHASE.update(d["PHASE"]); HAND_N.update(d.get("HAND_N", {}))
    combat_restore(PHASE.pop("combat_save", None))    # v6c (M17); a game saved before it: plain defaults
    PUBLIC_BOARD.update(d.get("PUBLIC_BOARD", {})); WINDOWS.update(d.get("WINDOWS", {})); TODOS.extend(d.get("TODOS", [])); PLACED.update(d.get("PLACED", []))
    HIGHROLL.update(d.get("HIGHROLL") or {"mode": None})
    SEAT_KEYS.update(d.get("SEAT_KEYS") or {})
    SEAT_DEVICES.update(d.get("SEAT_DEVICES") or {})
    AUTOPASS.update(d.get("AUTOPASS") or {})
    SEAT_PROXY.update(d.get("SEAT_PROXY") or {})
    SEAT_INVITES.update(d.get("SEAT_INVITES") or {})
    MULL.update(d.get("MULL") or {"on": False, "first": None, "since": 0.0,                 # a snapshot from before the step existed: a game
                                  "done": d["PHASE"].get("player") is not None})                # already under way never opens it
    if MULL["on"]:
        MULL["since"] = time.time()                   # the clock starts again with the table
    CONCEDED.clear(); CONCEDED.update(d.get("CONCEDED") or [])      # (a snapshot from before concede: nobody has)
    DECKDECL.update(d.get("DECKDECL") or {})          # declared commanders and deck lists (the check-in), and the seats' commanders with them
    for h in HUMANS:
        c = (DECKDECL.get(h["name"]) or {}).get("commander")
        if c:
            h["commander"] = c
            NICKNAMES[c.split(",")[0]] = c
    if d.get("ORDER") and (not ORDER or HIGHROLL.get("winner")):
        ORDER[:] = d["ORDER"]                         # a high roll's order beats the --order the table was started with
    EVENTS.extend(d.get("EVENTS") or [])              # the visible log survives a restart or a sleep (it did not until 2026-10-07)
    _ev_id[0] = max(d.get("ev_id", 0), max((e["id"] for e in EVENTS), default=0))   # pages keep their cursors across the restart
    top = max([p.id for v in VPS.values() for p in v.battlefield] + [0])
    import itertools
    player._ids = itertools.count(top + 1)            # new permanents never reuse a restored id
    for v_ in VPS.values():                           # a spell that was waiting on a response when the table stopped: the window is gone, it resolves
        if v_.__dict__.get("held_spell"):
            print(f"restore: {v_.name}'s held {v_.__dict__['held_spell']['card']['name']} resolves: {v_.release_held()}", flush=True)
    migrate_charge_lines()
    WAIT_SINCE.clear(); WAIT_SAID.clear(); PASS.update(key=None, passed=[], waiting_on=None, since=time.time())      # nothing is "still waiting" from before the stop
    emit("table-note", kind="host", text=f"The table was restored from a save ({time.strftime('%H:%M:%S')}). Anything said or waited on before it is gone.")      # R12
    print(f"restored {path}: seats {list(VPS)}, life {life_table()}, turn {PHASE}", flush=True)


def migrate_charge_lines():
    """A game saved before Station was real kept its charge counters in the log ("… put 4 charge counters on Class-F Shuttlecraft #9 (now 4)"),
    which assets/charge.js added up. The engine owns them now and that file reads only the engine, so on restore a permanent named by id in the
    log gets the log's total if the engine has fewer. Once: max, not a sum, so a host's own fix-up and a second restore change nothing."""
    tot: dict = {}
    rx = re.compile(r"put (\d+) charge counters? on .+? #(\d+)(?: \(|\.|$)", re.I)
    for e in EVENTS:
        m = e.get("type") == "say" and rx.search(e.get("text") or "")
        if m:
            k = (e.get("speaker"), int(m.group(2)))
            tot[k] = tot.get(k, 0) + int(m.group(1))
    for (who, pid), n in tot.items():
        v = VPS.get(who)
        p = next((x for x in v.battlefield if x.id == pid), None) if v else None
        if p is not None and p.charge < n:
            print(f"restore: {who} #{pid} {p.name}: charge {p.charge} -> {n} (from the game log)", flush=True)
            p.charge = n


def instant_speed(vp) -> list[str]:
    """Spells this seat could cast right now outside its own turn: instants and flash."""
    out = []
    for _label, c, _pay, is_cmdr in [*vp.castable(), *vp.exile_castable()]:      # (an instant it may cast from exile counts too)
        if is_cmdr:
            continue
        if "Instant" in (c.get("types") or []) or "Flash" in __import__("player").printed_keywords(c):
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
        return [n for n in order[i + 1:] + order[:i] if not is_out(n) and not on_turn(n)]
    if STEPS[PHASE["step"]] in NO_PRIORITY:
        return [active]
    return [n for n in order[i:] + order[:i] if not is_out(n)]   # people AND AI seats, in turn order


HUMAN_PASS_SECS = max(0.0, float(args.human_pass_secs))   # see --human-pass-secs
AI_PASS_TIMEOUT = float(os.environ.get("TABLE_AI_PASS_SECS") or 30.0)   # an AI that never answers is passed for (logged), never a freeze; a model player thinks longer than 30 s, so the dogfood harness raises it


def passes_state() -> dict:
    if GAME_OVER.get("over"):                     # M24: nobody owes a pass once the game is over (and no timer passes for anyone)
        return {"need": [], "passed": [], "next": None}
    key = (PHASE["player"], PHASE["step"])
    if PASS["key"] != key:                        # a new step (or BACK, or a new turn): nobody has passed yet
        PASS.update(key=key, passed=[], waiting_on=None, since=time.time())
    need = passes_needed()
    passed = [n for n in PASS["passed"] if n in need]
    nxt = next((n for n in need if n not in passed), None)
    if nxt != PASS.get("waiting_on"):
        PASS.update(waiting_on=nxt, since=time.time())
    elif nxt in PILOTS and real_decision(nxt):
        PASS["since"] = time.time()               # v6c: a person with something real to decide is never passed for by the clock
    elif nxt in VPS and time.time() - PASS.get("since", time.time()) > AI_PASS_TIMEOUT:
        PASS["passed"].append(nxt)                # an AI seat that didn't answer: pass for it, say so
        emit("pass", by=nxt, ai=is_bot(nxt), driven=True, auto=True, timeout=True, secs=AI_PASS_TIMEOUT, player=PHASE["player"], step=STEPS[PHASE["step"]])
        _append("brain.jsonl", {"ts": round(time.time(), 2), "ai_pass_timeout": nxt, "step": STEPS[PHASE["step"]]})
        return passes_state()
    if HUMAN_PASS_SECS and nxt and nxt not in VPS and (HOLD["on"] or any(t.get("kind") == "question" and not t["done"] for t in TODOS)):
        PASS["since"] = time.time()               # a hold or an open question pauses the timer: the full time again afterwards
    if _human_timer_applies(nxt) and time.time() - PASS.get("since", time.time()) > HUMAN_PASS_SECS:
        PASS["passed"].append(nxt)                # a person who didn't pass in time: pass for them, and say so in the log
        emit("pass", by=nxt, human=True, auto=True, timeout=True, secs=HUMAN_PASS_SECS, player=PHASE["player"], step=STEPS[PHASE["step"]])
        return passes_state()
    out = {"need": need, "passed": passed, "next": nxt}
    if nxt in PILOTS and real_decision(nxt):
        out["no_clock"] = True                        # v6c: a real decision is theirs for as long as it takes (no countdown to show)
        out["decision"] = "blocks" if combat_owed(nxt) else "choice" if (VPS[nxt].__dict__.get("pending_choices")) else "window"   # (blocks: the block panel is the prompt, not the pass chip)
    elif nxt in VPS and PHASE["player"] is not None:   # a virtual-deck seat has a clock too, and everyone can see it
        out.update(deadline=round(PASS["since"] + AI_PASS_TIMEOUT, 2), secs=AI_PASS_TIMEOUT, now=round(time.time(), 2),
                   seconds_left=round(max(0.0, PASS["since"] + AI_PASS_TIMEOUT - time.time()), 1))
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
        emit("pass", by=seat_name, ai=is_bot(seat_name), driven=True, player=PHASE["player"], step=STEPS[PHASE["step"]],      # R24: `ai` is true only for a seat nobody is driving
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
        if n in PILOTS and real_decision(n):            # v6c, the rule: blocks owed, a question open, or something castable after blocks
            break
        if n in PILOTS and n != PHASE["player"]:        # a person's virtual seat: pass only when there is truly nothing to do
            if AUTOPASS.get(n, args.pilot_autopass) == "off" or pilot_can_respond(n):
                break
        elif n in VPS or mode == "off" or n == PHASE["player"] or (mode == "others-no-combat" and STEPS[PHASE["step"]] in COMBAT_STEPS):
            break                                       # (an AI seat answers its own window; never auto-pass for one here)
        PASS["passed"].append(n)
        done.append(n)
        ps = passes_state()
        # A pilot's "smart" pass happens only when its hand holds nothing it could cast, so a public auto=True told every reader
        # (and every spectator) "no instant in that hand" (G3-8). Such a pass looks like any other pass. A person's own opt-in
        # auto-pass ("others") passes whatever the hand holds, so saying so reveals nothing and stays.
        smart = n in PILOTS
        emit("pass", by=n, **({} if smart else {"auto": True}), player=PHASE["player"], step=STEPS[PHASE["step"]],
             left=[x for x in ps["need"] if x not in ps["passed"]])
    return done


def pilot_can_respond(n: str) -> bool:
    """Could pilot seat `n` do anything in someone else's step right now? A castable instant or flash spell, an affordable
    activated ability (not "activate only as a sorcery", source untapped if it taps), or a face-down card it can turn up.
    When unsure, say yes: a wrong 'no' passes for a person who wanted to act."""
    v = VPS.get(n)
    if v is None or v.__dict__.get("pending_choices"):
        return True
    try:
        for o in [*v.castable(), *v.exile_castable()]:
            c = o[1]
            if "Instant" in (c.get("types") or []) or "Flash" in __import__("player").printed_keywords(c):
                return True
        for p in v.battlefield:
            if p.face_down and getattr(p, "turn_up_cost", None) and v.plan_payment(p.turn_up_cost) is not None:
                return True
            for cost, eff, _ in v.ability_list(p):
                if re.search(r"activate only as a sorcery", eff, re.I) or re.search(r"Station\b", cost):
                    continue
                if re.match(r"\s*Add\b", eff or ""):          # a mana ability: tapping a land for mana is not a response (it only pays for one)
                    continue
                if re.search(r"\{T\}|\{Q\}", cost) and p.tapped:
                    continue
                mana = "".join(re.findall(r"\{(?!T\}|Q\})[^}]+\}", cost))
                if v.plan_payment(mana) is not None:
                    return True
        return False
    except Exception:                                   # noqa: BLE001 — unsure: let the person decide
        return True


def pilot_auto_begin_loop():
    """--pilot-auto-begin: when a pilot seat's turn is at its untap step (or walking upkeep/draw) and nobody has begun it, begin it
    through the table's own brain route, retrying while the others pass. The engine checks it exactly as a Begin press."""
    import urllib.request
    while True:
        time.sleep(1.0)
        try:
            who = PHASE.get("player")
            # 2HG: once the lead has begun, each pilot teammate begins its own half (untap, draw) of the shared turn
            mate = next((m for m in teammates(who or "") if m in PILOTS and m not in TEAM_TURN["begun"] and not is_out(m)), None) \
                if PHASE.get("begun") and TEAMS else None
            if mate and args.pilot_auto_begin == "on" and not GAME_OVER.get("over") and not HOLD.get("on"):
                who = mate
            elif args.pilot_auto_begin != "on" or GAME_OVER.get("over") or who not in PILOTS or PHASE.get("begun") \
                    or PHASE["step"] >= STEPS.index("main 1") or HOLD.get("on") or is_out(who):
                continue
            r = urllib.request.Request(f"http://127.0.0.1:{args.port}/api/brain/begin", method="POST",
                                       data=json.dumps({"seat": who, "quiet": False}).encode(),
                                       headers={"Content-Type": "application/json", "X-Brain-Token": TOKEN})
            try:
                urllib.request.urlopen(r, timeout=10).read()
            except urllib.error.HTTPError:
                pass                                    # 409 waiting on passes / already begun: next second
        except Exception:                               # noqa: BLE001 — never let the loop die
            pass


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


HAND_TAPS: dict = {}   # (seat, permanent id) -> (active player, step index) of a pilot's own tap: undoable in that same step


def untap_refusal(owner: str, perm_id=None) -> str | None:
    """Why `owner` may not untap a permanent by hand right now, or None: only the ACTIVE player, in their own untap step (the
    start of the turn untaps everything; this is the manual one). Tapping is not checked: tap is legal whenever you hold
    priority. Used by /api/brain/untap (a pilot's key) and /api/card-action (a person's real deck)."""
    if PHASE["player"] is None:
        return "the game hasn't started: nothing untaps yet"
    if perm_id is not None and HAND_TAPS.get((owner, perm_id)) == (PHASE["player"], PHASE["step"]):
        return None                                   # undoing your own tap, in the same step it was made (a misclick, or a land tapped by habit)
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
        return START_HINT
    here = STEPS[PHASE["step"]]
    said_p, said_s = b.get("player"), b.get("step")
    if (said_p is not None and said_p != PHASE["player"]) or (said_s is not None and said_s != here):
        return f"stale pass: it was for {said_p or PHASE['player']}'s {said_s or here}, the table is at {PHASE['player']}'s {here}"
    if PHASE["player"] == seat_name:
        return "it's your own turn: there is nothing to pass; end it with End my turn"
    if combat_owed(seat_name):                       # v6c: blocks are declared (or "no blocks" said), not passed
        return "you are attacked: declare your blocks (or no blocks) first; the window to act comes after them"
    ps = passes_state()
    if seat_name not in ps["need"]:
        return f"you aren't in {here}'s priority round"
    if seat_name in ps["passed"]:
        if ps["next"] is None:
            return f"you already passed {here}, and so has everyone else: {PHASE['player']} moves the turn on (the step ends when they act)"
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
        return None if host else START_HINT
    if PHASE["player"] != seat_name and on_turn(seat_name):   # 2HG: the lead's teammate begins its own untap/draw, once
        if seat_name in TEAM_TURN["begun"]:
            return f"{seat_name} already began this turn"
        return None
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
    if action == "next":                                # one step on, through that step's round like any other
        target = STEPS[min(cur + 1, len(STEPS) - 1)]
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
        if STEPS[PHASE["step"]] == "combat damage":
            combat_flush(PHASE["player"])
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
def cast_pool(v, b) -> list:
    """The cards a cast request may name: the hand, and the library's top card when it says {"from": "library-top"}."""
    return [*v.hand, *(v.library[-1:] if b.get("from") == "library-top" else []), *v.impulse_cards()]   # (and what it may play from exile)


PILOT_ACTIONS = frozenset({"say", "begin", "land", "cast", "turn-up", "tap", "untap", "attack", "damage", "block",
                           "pass", "end", "life", "effect", "activate", "station", "equip", "next", "surveil", "choose", "cycle", "manual", "scry", "swap",
                           "keep", "mulligan"})

# Combat the SERVER computes. A pilot says which creatures attack whom; the engine knows their power. The defender never types
# an amount: `block` takes the number from here, and whatever nobody blocked is dealt when the turn moves on. (Before this, the
# defender typed how much it took, so the table was trusting whoever was being hit.)
PRESTART_ACTIONS = AI_MAIN_ACTIONS | {"attack", "damage", "block", "next", "equip"}
START_HINT = ("the game hasn't started: start it with `highroll quantum` (tablectl) or POST /api/highroll/start "
              "{\"mode\": \"quantum\"}; the winner of the roll goes first")
COMBAT: dict = {"pending": [], "hits": [], "human": [], "used": set(), "announced": {}, "dealt": {}}
DAMAGED: dict = {"key": None}                        # (seat, its turn) that already dealt combat damage (legal list: once a turn)
ATTACKED: dict = {"key": None, "ids": set()}         # (seat, its turn) -> creature ids already declared as attackers: once each


def cur_power(e: dict) -> int:
    """An attack's damage is the attacker's power WHEN DAMAGE IS DEALT (a counter after the declaration counts), not when declared."""
    try:
        a = VPS.get(e["attacker"])
        if a is not None and e.get("ref"):
            return a.stats(a.perm(e["ref"]))[0]
    except Exception:                                  # noqa: BLE001 — the attacker left the battlefield: what was declared
        pass
    return e["power"]
EFFECT_WORDS = {"draw": r"\bdraws? ", "surveil": r"\bsurveil\b", "scry": r"\bscry\b", "manifest": r"\bmanifest",
                "destroy": r"\bdestroy", "exile": r"\bexile", "bounce": r"(owner's hand|return target|return it to)"}
REMOVAL_APPLIED: set = set()                         # removal attention events a seat has already applied to its board
EFFECT_USES: dict = {}                               # (seat, turn, source, kind) -> times used this turn
GAME_FIRST: dict = {"seat": None}                    # who took the first turn: in a two-player game that seat skips its first draw


def hit_power(e: dict) -> int:
    """What this attacker deals right now. Double strike hits twice, so an unanswered double striker is twice its power."""
    n = cur_power(e)
    a = attacker_perm(e)
    return n * 2 if a is not None and "Double strike" in attacker_keywords(a) else n


def in_combat(owner, p) -> bool:
    """Is this permanent attacking or blocking right now? Attacking: declared this turn by its controller (the attack is on the record until the
    turn moves on), still waiting to be blocked, or it hit. Blocking: it blocked in this combat. Outside the combat steps, nothing is."""
    step = STEPS[PHASE["step"]] if PHASE.get("player") else None
    if step not in ("declare attackers", "declare blockers", "combat damage", "end of combat"):
        return False
    ref = f"#{p.id}"
    if any(e.get("ref") == ref and e.get("attacker") == owner.name for e in COMBAT["pending"] + COMBAT["hits"]):
        return True
    if ATTACKED.get("key") == (owner.name, owner.turn) and p.id in (ATTACKED.get("ids") or ()):
        return True
    return p.id in COMBAT["used"]


def combat_reset():
    COMBAT.update(pending=[], hits=[], human=[], used=set(), announced={}, dealt={})


def combat_flush(attacker_seat: str | None = None):
    """Whatever is still attacking and has not been dealt is dealt now, in ONE damage step per attacking seat (v6c): declared blocks count,
    an attacker nobody answered is unblocked. The safety net behind the ordinary route (combat_tick): a turn that ends, or is walked on by
    the host, with a combat still open. PHASE_LOCK/VP_LOCK are the caller's."""
    if GAME_OVER.get("over"):
        return
    for sn in list(dict.fromkeys(e["attacker"] for e in COMBAT["pending"] if attacker_seat is None or e["attacker"] == attacker_seat)):
        combat_damage_step(sn)
    for sn, d in (COMBAT.get("decl") or {}).items():
        if attacker_seat is None or sn == attacker_seat:
            d["stage"] = "done"


def combat_dealt(attacker_seat: str, defender: str, n: int):
    """What an attack really did to a player (T1): kept per attacker and defender, so the damage step can say it next to what was announced."""
    if n > 0:
        k = f"{attacker_seat}>{defender}"
        COMBAT.setdefault("dealt", {})[k] = COMBAT.setdefault("dealt", {}).get(k, 0) + n


def combat_account(attacker_seat: str) -> list[str]:
    """The damage step's line per defender (T1): what the attack actually took off each player's life, and, when that isn't what the
    declaration said, why it can differ. The declaration is a forecast (every attacker unblocked, its power then); this is the result."""
    out = []
    ann, dealt = COMBAT.setdefault("announced", {}), COMBAT.setdefault("dealt", {})
    for k in sorted(set(ann) | set(dealt)):
        a_, _, d_ = k.partition(">")
        if a_ != attacker_seat:
            continue
        n, f = dealt.pop(k, 0), ann.pop(k, 0)
        if n == f:
            out.append(f"Combat: {d_} took {n}.")
        else:
            out.append(f"Combat: {d_} took {n} (the attack was announced at {f}: blocks, first strike and power changing since make the difference).")
    return out


def combat_step_after_block(attacker_seat: str):
    """(Before v6c a block dealt its attacker's damage at once and this moved the step on. A block is a declaration now and combat_tick
    moves the steps; the host's older typed-amount block route still calls this, and there is nothing left for it to do.)"""
    return


# ── v6c: one combat, in the rules' order ─────────────────────────────────────────────────────────────────────────────────────────
# (game 5 retro: M1, M5, M6, M16, M17, M19, M22, M23, M24.) Declare attackers, once and locked → the attack's triggers are answered →
# the defender declares every block (one answer may cover all of them) → a window in the declare-blockers step in which both players may
# act → ONE combat damage step for all attackers together → only then is the game checked for its end.
#
# State: COMBAT["decl"][attacker seat] = {"turn", "stage", "passed", "at"}; stage is triggers → blocks → window → done. Each entry of
# COMBAT["pending"] stays there until damage, with "answered", "blockers" (the defender's permanent ids), "blocker_seat" and "blocked".
# COMBAT["used"] is every creature that is blocking. Nothing in here changes a life total before combat_damage_step.
COMBAT_BATCH = [0]                                   # > 0 while a damage step is being dealt: the game-over check waits for the whole step (M23)
_COMBAT_TL = threading.local()                       # .said: what the table said while moving combat on inside the current request
COMBAT_LOOP = {"on": False, "busy": 0.0}              # busy: when a seat's request last began (the loop leaves that request its own order of lines)


def combat_decl(sn: str | None):
    """This seat's attack declaration of ITS CURRENT turn, or None. (A declaration of an earlier turn is history.)"""
    d = (COMBAT.get("decl") or {}).get(sn)
    v = VPS.get(sn)
    return d if d and v is not None and d.get("turn") == v.turn else None


def combat_entries(sn: str) -> list:
    return [e for e in COMBAT["pending"] if e["attacker"] == sn]


def combat_line(speaker: str, text: str, action: str = "block"):
    """A line the table says for a seat while it moves combat on. Also kept for the request it happened in (its `said`)."""
    emit("say", speaker=speaker, text=text, action=action, auto=True, speech=text)
    got = getattr(_COMBAT_TL, "said", None)
    if got is not None:
        got.append(text)


def cant_block_now(p) -> bool:
    """"Target creature can't block this turn" (La'An Noonien-Singh; M22). The effect, or the host's `cant-block`, marks the permanent with
    the turn tick it holds for; a permanent from a game saved before the mark existed has none."""
    tick = getattr(p, "no_blocking_tick", 0)
    return bool(tick) and tick == __import__("player").DEATH_TICK[0]


def block_refusal_now(blocker, attacker) -> str | None:
    """block_refusal, and "can't block this turn" (M22): the one check every block goes through (a person's, a bot's, the legal list's)."""
    if cant_block_now(blocker):
        bv = owner_of(blocker)
        return f"{bv.shown(blocker) if bv is not None else blocker.name} can't block this turn"
    return block_refusal(blocker, attacker)


def combat_blockers_for(sn: str, e: dict) -> list:
    """`sn`'s creatures that may block this attacker right now: untapped, not blocking already, and nothing on either card forbids it."""
    v = VPS.get(sn)
    if v is None:
        return []
    ap = attacker_perm(e)
    return [p for p in v.battlefield if p.is_("Creature") and not p.tapped and p.id not in COMBAT["used"] and block_refusal_now(p, ap) is None]


def combat_owed(sn: str) -> list:
    """The attackers this seat still has to answer (declare a block, or none): asked of it and not answered. In 2HG its teammates' too.
    Not yet asked while the attack's triggers are being answered (M5)."""
    mine = {sn, *teammates(sn)}
    out = []
    for e in COMBAT["pending"]:
        if e["defender"] in mine and not e.get("answered"):
            d = combat_decl(e["attacker"])
            if d is None or d.get("stage") == "blocks":
                out.append(e)
    return out


def combat_trigger_holders(sn: str) -> list[str]:
    """Whose answer the attack's triggers are waiting on: an aimed trigger of the attacker (Hologram Janeway's counter, La'An's "can't block"),
    its surveil or scry (Tricorder), or a choice the attack put to another seat. Blocks are neither asked nor accepted until these are in."""
    out = []
    s0, k0 = (combat_decl(sn) or {}).get("look0") or (0, 0)     # a surveil or scry it already owed before attacking is not the attack's
    for n, v in VPS.items():
        if is_out(n):
            continue
        look = n == sn and (v.__dict__.get("pending_surveil", 0) > s0 or v.__dict__.get("pending_scry", 0) > k0)
        if v.__dict__.get("pending_choices") or look:
            out.append(n)
    return out


def combat_incoming(seat_name: str) -> list:
    """Who is attacking this seat and still waits for its answer. In 2HG a team is attacked as one: each defender sees (and may block) its
    teammates' attackers. v6c: every attacker of the combat is listed in one go, each with the creatures that may block it (`can_block`,
    the engine's rule: flying, menace, "can't block this turn"); an attacker that has been answered, or whose trigger is still being
    aimed, is not in the list."""
    out = []
    for e in combat_owed(seat_name):
        able = combat_blockers_for(seat_name, e)
        out.append({"attacker": e["attacker"], "creature": e["name"], "ref": e.get("ref"), "power": cur_power(e), "trample": e["trample"],
                    "defender": e["defender"], "menace": bool(e.get("menace")), "unblockable": bool(e.get("unblockable")),
                    "damage": hit_power(e), "can_block": [p.id for p in able]})
    return out


def combat_public() -> dict:
    """The combat on the table, as everyone may see it (attackers and declared blocks are public): for /api/phase, the pages and a saved game."""
    out = []
    for sn in VPS:
        d = combat_decl(sn)
        if d is None:
            continue
        rows = []
        for e in combat_entries(sn):
            bs = VPS.get(e.get("blocker_seat") or e["defender"])
            rows.append({"ref": e.get("ref"), "name": e["name"], "defender": e["defender"], "power": cur_power(e), "damage": hit_power(e),
                         "trample": e["trample"], "menace": bool(e.get("menace")), "answered": bool(e.get("answered")),
                         "blocked": bool(e.get("blocked")),
                         "blockers": [{"id": p.id, "name": bs.shown(p), "seat": bs.name} for p in (bs.battlefield if bs else []) if p.id in (e.get("blockers") or [])]})
        wait = []
        if d["stage"] == "triggers":
            wait = combat_trigger_holders(sn)
        elif d["stage"] == "blocks":
            wait = sorted({e["defender"] for e in combat_entries(sn) if not e.get("answered")})
        elif d["stage"] == "window":
            ps = passes_state() if on_turn(sn) and STEPS[PHASE["step"]] == "declare blockers" else {"need": [], "passed": []}
            wait = [n for n in ps["need"] if n not in ps["passed"]]
            if not wait and not d.get("passed"):
                wait = [sn]
        out.append({"attacker": sn, "stage": d["stage"], "locked": True, "attacker_passed": bool(d.get("passed")), "waiting_on": wait,
                    "attackers": rows, "damage_dealt": DAMAGED.get("key") == (sn, VPS[sn].turn)})
    return {"attacks": out}


def combat_view(sn: str) -> dict:
    """What a seat's own page needs about combat (added to /api/brain/state): the attackers it must answer with what may block each, the
    public combat, and what the table is waiting on this seat for."""
    owed = combat_owed(sn)
    inc = combat_incoming(sn)
    pub = combat_public()
    mine = combat_decl(sn)
    window = next((a for a in pub["attacks"] if a["stage"] == "window"), None)
    you = {"blocks_due": bool(owed), "take": sum(i["damage"] for i in inc),
           "triggers_first": [a["attacker"] for a in pub["attacks"] if a["stage"] == "triggers"
                              and any(r["defender"] in {sn, *teammates(sn)} for r in a["attackers"])],
           "window": bool(window and sn in window["waiting_on"] and window["attacker"] != sn),
           # the attacker's own press is wanted only when it could still do something (otherwise the table passes for it, as for anyone)
           "deal": bool(mine and mine["stage"] == "window" and not mine.get("passed")
                        and (sn not in PILOTS or sn in (mine.get("acted") or ()) or pilot_can_respond(sn))),
           "waiting_on": (next((a["waiting_on"] for a in pub["attacks"] if a["attacker"] == sn and a["stage"] != "done"), []) if mine else [])}
    return {"incoming": inc, "block_options": {(i.get("ref") or i["creature"]): i["can_block"] for i in inc}, "combat": {**pub, "you": you}}


def real_decision(n: str) -> str | None:
    """THE RULE (game 5 retro, M2): a person is never passed for by the table at a point where they have something real to decide, whatever
    the response-windows setting and whatever the pass timer says. What that is, or None: blocks they have been asked to declare; a question
    the table is waiting on them for (an aimed trigger); the window after blocks while they hold an instant or a flash card they can pay for,
    or an ability they can use. A window with nothing in it for them is trivial and may be passed for."""
    v = VPS.get(n)
    if v is None or is_out(n):
        return None
    if combat_owed(n):
        return "blocks"
    if v.__dict__.get("pending_choices"):
        return "a choice"
    if n in PILOTS and PHASE.get("player") and STEPS[PHASE["step"]] == "declare blockers":
        for s, d in (COMBAT.get("decl") or {}).items():
            if d.get("stage") == "window" and combat_decl(s) is d:
                if n in (d.get("acted") or ()):          # it did something after blocks (what it cast may have a part done by hand): its own pass ends it
                    return "the window after blocks (you acted: pass when you are done)"
                if pilot_can_respond(n):
                    return "the window after blocks"
    return None


def combat_block(sn: str, b: dict) -> list[str]:
    """A defender's block DECLARATION (v6c, M1/M6): which creatures block which attackers. Nothing is dealt here. Three ways to say it:
      {"blocks": {"<attacker ref or name>": ["#7", "#9"], ...}}   every attacker in one answer; one not named gets no block
      {"none": true}                                             no blocks at all
      {"attacker": ref, "blocker": "#7"} / {"attacker": ref, "blockers": [...]} / {"attacker": ref}   one attacker (the older way)
    Every check comes before anything is recorded: a refused declaration leaves the combat exactly as it was."""
    from player import IllegalAction
    import player as _pl
    v = VPS[sn]
    mine = combat_owed(sn)
    if not mine:
        hold = [a for a in COMBAT["pending"] if (a["defender"] == sn or a["defender"] in teammates(sn)) and not a.get("answered")]
        if hold:
            who = combat_trigger_holders(hold[0]["attacker"])
            raise IllegalAction(f"attack triggers come first: {', '.join(who) or hold[0]['attacker']} is still choosing for one; blocks are declared after that")
        raise IllegalAction("nothing is attacking you right now (or it has already been dealt with)")

    def find(want: str):
        want = str(want or "").strip().lower()
        e = next((x for x in mine if want and want in (str(x.get("ref") or "").lower(), x["name"].lower())), None)
        if e is None and want:                       # "Kaust" for "Kaust, Eyes of the Glade": one attacker whose name starts with it
            part = [x for x in mine if x["name"].lower().startswith(want) or x["name"].lower().split(",")[0] == want]
            e = part[0] if len(part) == 1 else None
        if e is None and want:
            raise IllegalAction("no attacker '" + want + "' is attacking you: " + ", ".join(f"{x['name']} {x.get('ref') or ''} ({cur_power(x)})" for x in mine))
        return e

    plan: list = []                                  # (entry, [blocking permanents])
    if b.get("blocks") is not None and not isinstance(b["blocks"], dict):
        raise IllegalAction('`blocks` is {"<attacker>": ["#blocker", ...]}: one entry per attacker that is blocked')   # (never read as "no block")
    whole = isinstance(b.get("blocks"), dict) or bool(b.get("none"))
    if whole:
        named = {}
        for k, refs in (b.get("blocks") or {}).items():
            e = find(k)
            if e is None:
                raise IllegalAction("name the attacker each block is for")
            refs = [refs] if isinstance(refs, (str, int)) else list(refs or [])
            named.setdefault(id(e), (e, []))[1].extend(v.perm(str(r)) for r in refs)
        for e in mine:
            plan.append((e, named.get(id(e), (e, []))[1]))
    else:
        many = [v.perm(str(x)) for x in (b.get("blockers") or [])]
        if b.get("blocker") and not many:
            many = [v.perm(str(b["blocker"]))]
        plan.append((find(b.get("attacker")) or mine[0], many))
    seen: set = set()
    total_blockers = sum(len(bl) for _e, bl in plan) + int((COMBAT.get("blocked") or {}).get(sn, 0))
    for e, bl in plan:
        ap = attacker_perm(e)
        for one in bl:
            if one.id in seen:
                raise IllegalAction(f"{v.shown(one)} can block only one attacker: name each blocker once")
            seen.add(one.id)
            if not one.is_("Creature") or one.tapped:
                raise IllegalAction(f"{one.name} can't block: it must be an untapped creature")
            why = block_refusal_now(one, ap)
            if why:
                raise IllegalAction(f"{one.name} can't block {e['name']}: {why}")
            if one.id in COMBAT["used"]:
                raise IllegalAction(f"{one.name} already blocked this combat")
            alone = getattr(_pl, "blocks_alone_banned", None)      # T16 ("can't block alone"), where the engine has it: with another blocker of this combat
            if alone is not None and alone(one) and total_blockers < 2:
                raise IllegalAction(f"{one.name} can't block alone: block together with another creature")
        if bl and e.get("menace") and len(bl) < 2:
            raise IllegalAction(f"{e['name']} has menace: it needs two or more blockers (blockers: [\"#a\", \"#b\"])")
    said, none_on = [], []
    for e, bl in plan:                               # every check passed: the declaration is recorded
        e.update(answered=True, blockers=[p.id for p in bl], blocker_seat=sn, blocked=bool(bl))
        for p in bl:
            COMBAT["used"].add(p.id)
        if bl:
            COMBAT.setdefault("blocked", {})[sn] = COMBAT.setdefault("blocked", {}).get(sn, 0) + len(bl)
            said.append(f"I block {e['name']} with " + " and ".join(v.shown(p) for p in bl) + ".")
        else:
            none_on.append(e)
    if none_on:
        n = sum(hit_power(e) for e in none_on)
        said.append(("No blocks" if len(none_on) == len(plan) else "No block") + " on " + ", ".join(e["name"] for e in none_on)
                    + f": {n} damage is coming at the damage step.")
    return said


def combat_ask(sn: str, d: dict):
    """The attack's triggers are answered: NOW the defenders are asked, once each, for every attacker together (M5, M6)."""
    d["stage"] = "blocks"
    now = time.time()
    by_def: dict = {}
    for e in combat_entries(sn):
        e["at"] = now
        by_def.setdefault(e["defender"], []).append(e)
    for dn, es in by_def.items():
        total = sum(hit_power(e) for e in es)
        cant = [e["name"] for e in es if not any(combat_blockers_for(m, e) for m in (dn, *teammates(dn)))]
        text = (f"{sn} attacks you with " + ", ".join(f"{e['name']} ({cur_power(e)})" for e in es) + f": {total} damage if nothing blocks. "
                + ("Declare your blocks: one answer for all of them (`block` with `blocks`, or `none`)." if len(cant) < len(es)
                   else "Nothing of yours can block: answer `block` with `none` to take it (or act first, if you can)."))
        emit("attention", kind="attacked", addressee=dn, attacker=", ".join(e["name"] for e in es), amount=total,
             trample=any(e["trample"] for e in es), menace=any(e.get("menace") for e in es), unblockable=len(cant) == len(es),
             attackers=[{"ref": e.get("ref"), "name": e["name"], "power": cur_power(e), "damage": hit_power(e), "trample": e["trample"],
                         "menace": bool(e.get("menace")), "blockable": e["name"] not in cant} for e in es], text=text)


def combat_open_window(sn: str):
    """Every block is declared: the turn is at declare blockers, and its round of priority opens. Both players may act now (a flash creature,
    an instant) before any damage. PHASE_LOCK is the caller's."""
    if not on_turn(sn):
        return
    goal = STEPS.index("declare blockers")
    if STEPS.index("main 1") <= PHASE["step"] < goal:
        PRIORITY.update(waiting=[], seats=[])
        PHASE["step"] = goal
        emit("phase", player=PHASE["player"], step=STEPS[goal], index=goal, by="block")
    if PHASE["step"] == goal:
        AI_OPENED["key"] = (PHASE["player"], PHASE["step"])
        open_priority(STEPS[goal])
        autopass_round()


def combat_window_closed(sn: str, d: dict) -> bool:
    """Has everyone had their say after blocks? Every other seat has passed in a row (a cast in between starts the round again), no question
    is open, no timed window is running, and the attacker has passed too: by pressing Damage (or Next, or End), or because there is
    nothing it could do."""
    if any(v_.__dict__.get("pending_choices") for n_, v_ in VPS.items() if not is_out(n_)) or HELD.get("seat"):
        return False                                 # (a question is open, or a spell is held for answers: that window is first)
    if not (on_turn(sn) and STEPS[PHASE["step"]] == "declare blockers"):
        return True                                  # the turn is somewhere else (the host moved it): nothing left to wait for
    if AI_OPENED["key"] != (PHASE["player"], PHASE["step"]):
        AI_OPENED["key"] = (PHASE["player"], PHASE["step"])     # a table restarted in the middle of the window: it opens again
        open_priority(STEPS[PHASE["step"]])
    autopass_round()
    ps = passes_state()
    if any(n not in ps["passed"] for n in ps["need"]) or priority_open():
        return False
    return bool(d.get("passed")) or sn not in PILOTS or (sn not in (d.get("acted") or ()) and not pilot_can_respond(sn))


def combat_tick():
    """Move every open combat on as far as it can go right now. Called after each seat action and by a slow loop of its own (a timed window
    running out, a defender nobody plays). Doing it twice changes nothing. Takes VP_LOCK (= PHASE_LOCK)."""
    if GAME_OVER.get("over") or PHASE.get("player") is None:
        return
    with VP_LOCK:
        for sn, d in list((COMBAT.get("decl") or {}).items()):
            if combat_decl(sn) is not d or d.get("stage") == "done":
                continue
            for e in combat_entries(sn):             # M16: an attacker that has left the battlefield is out of the combat
                if e.get("ref") and attacker_perm(e) is None:
                    COMBAT["pending"].remove(e)
                    k = f"{sn}>{e['defender']}"
                    ann = COMBAT.setdefault("announced", {})
                    if k in ann:
                        ann[k] = max(0, ann[k] - int(e.get("forecast", e["power"])))
                    combat_line(sn, f"{e['name']} has left the battlefield: it is no longer attacking and deals no damage.", "attack")
                    if d["stage"] == "blocks" and not e.get("answered"):
                        emit("attention", kind="combat", addressee=e["defender"], text=f"{e['name']} is no longer attacking: nothing to answer for it.")
            mine = combat_entries(sn)
            if d["stage"] == "triggers":
                if combat_trigger_holders(sn):
                    continue
                combat_ask(sn, d)
            if d["stage"] == "blocks":
                for e in mine:                       # a seat nobody plays (an engine seat) has no answer to give: unblocked after the grace, as before
                    if not e.get("answered") and (e["defender"] not in PILOTS or is_out(e["defender"])) \
                            and (is_out(e["defender"]) or time.time() - e.get("at", 0) > DEFENDER_GRACE):
                        e.update(answered=True, blockers=[], blocked=False, auto=True)
                if any(not e.get("answered") for e in mine):
                    continue
                if not mine:                         # nothing is attacking a virtual seat (any more): no window, no step of ours
                    d["stage"] = "done"
                    continue
                d.update(stage="window", passed=False)
                combat_open_window(sn)
            if d["stage"] == "window":
                if not combat_window_closed(sn, d):
                    continue
                if on_turn(sn) and STEPS[PHASE["step"]] == "declare blockers":
                    PRIORITY.update(waiting=[], seats=[])
                    PHASE["step"] = STEPS.index("combat damage")
                    emit("phase", player=PHASE["player"], step=STEPS[PHASE["step"]], index=PHASE["step"], by="combat")
                d["stage"] = "done"
                combat_damage_step(sn)
        for sn in list(dict.fromkeys(e["attacker"] for e in COMBAT["pending"] if e["attacker"] not in VPS)):
            # A person's real-deck seat declared these (the board, or aloud): it has no declaration here and its turn's steps are its own.
            # Those aimed at a driven seat: nothing is dealt until that seat has answered every one of them; then all together. (Those
            # aimed at an engine seat are its brain's to answer, as before: left for it, or for the turn's end.)
            loose = [e for e in combat_entries(sn) if e["defender"] in PILOTS]
            if loose and all(e.get("answered") or is_out(e["defender"]) for e in loose):
                combat_damage_step(sn, only=loose)


def combat_damage_step(sn: str, claims: dict | None = None, only: list | None = None) -> list[str]:
    """THE combat damage step of seat `sn`'s attack (M1): every attacker's damage in one batch, from the board as it is NOW.
      - an attacker that left the battlefield deals nothing (M16); a blocker that left neither deals nor takes any, and its attacker stays
        blocked (only trample reaches the player);
      - within a fight, ai_turn.resolve_block: a first-strike step when anyone in it has first or double strike, then the regular one;
      - then what the attack's own triggers do with the hits ("deals combat damage to a player", lifelink), and the account line (T1);
      - the game-over check runs once, after all of it: a seat that goes to 0 in the step still takes the rest of it (M23).
    `claims` ({ref: [player, amount]}) is for attackers sent at a person's real-deck seat, whose life is theirs to mark: the pilot's `damage`.
    Returns what the attacker said (already in the log). VP_LOCK is taken here."""
    from ai_turn import resolve_block
    from player import IllegalAction
    a = VPS.get(sn)                                  # None: a person's real-deck seat attacked (its creatures are not the engine's)
    key = (sn, a.turn) if a is not None else None
    said: list = []
    with VP_LOCK:
        mine = combat_entries(sn) if only is None else [e for e in only if e in COMBAT["pending"]]
        human = [e for e in COMBAT["human"] if e["attacker"] == sn]
        if not mine and (a is None or (not human and not any(h["attacker"] == sn for h in COMBAT["hits"]) and DAMAGED.get("key") == key)):
            return []
        out0 = {n for n in VPS if is_out(n)}         # who had lost BEFORE this step: nothing more happens to them
        COMBAT_BATCH[0] += 1
        try:
            for e in mine:
                COMBAT["pending"].remove(e)
                ap = attacker_perm(e)
                if e.get("ref") and ap is None:
                    combat_line(sn, f"{e['name']} has left the battlefield: it deals no damage.", "damage")
                    continue
                dn = e.get("blocker_seat") or e["defender"]
                d = VPS.get(dn)
                if d is None or dn in out0 or e["defender"] in out0:
                    continue
                blockers = [q for q in d.battlefield if q.id in (e.get("blockers") or [])]
                life0 = d.life
                if e.get("blocked") and not blockers:
                    n = hit_power(e) if e["trample"] else 0
                    d.life -= n
                    res = {"said": [f"{e['name']} was blocked, and what blocked it has left the battlefield: "
                                    + (f"{n} tramples over; I'm at {d.life}." if n else "no damage.")], "through": n, "attacker_lifelink": 0}
                elif not blockers:
                    res = resolve_block(d, None, hit_power(e), e["trample"], e["name"])
                else:                                # one strike's power: resolve_block deals a double striker's two steps itself
                    res = resolve_block(d, blockers[0], cur_power(e), e["trample"], e["name"], atk=ap, atk_owner=a if ap is not None else None,
                                        more=blockers[1:])
                if d.life != life0:
                    emit("life", player=d.name, delta=d.life - life0, by="combat", life=d.life, source=e["name"], attacker=sn)
                for line in res["said"]:
                    combat_line(d.name, line, "block")
                if res.get("attacker_lifelink") and a is not None:   # the attacker's lifelink on what it dealt to its BLOCKERS (what reaches the player is the hit below)
                    a.life += res["attacker_lifelink"]
                    a.gained_life(res["attacker_lifelink"])
                    emit("life", player=a.name, delta=res["attacker_lifelink"], by="lifelink", life=a.life)
                    combat_line(a.name, f"Lifelink: I gain {res['attacker_lifelink']}.", "damage")
                through = int(res.get("through") or 0)   # what reached the player: never "life lost", which a blocker's lifelink would shrink
                combat_dealt(sn, d.name, through)
                if e.get("ref") and through > 0 and a is not None:
                    hit = {"attacker": sn, "ref": e["ref"], "defender": d.name, "amount": through}
                    if res.get("attacker_died") and ap is not None:   # it dies in the step it hit in: its "deals combat damage to a player" still happens, as it was
                        hit["lki"] = {"perm": ap, "attached": [x for x in a.battlefield if x.attached_to == ap.id], "kws": a.kws(ap, my_turn=True)}
                    COMBAT["hits"].append(hit)
                forget_dead(res)
                if e.get("ref") and res.get("attacker_died") and ap is not None:
                    try:                             # the attacker that died to the block leaves ITS owner's board
                        a.move(e["ref"], "graveyard", cause="combat")
                    except Exception:                # noqa: BLE001
                        pass
            if a is not None:                        # (a real-deck attacker's own triggers and life are its player's)
                DAMAGED["key"] = key
                hits_ = [h for h in COMBAT["hits"] if h["attacker"] == sn]
                said_hits = {}
                for h in hits_:
                    COMBAT["hits"].remove(h)
                    said_hits[h["ref"]] = (h["defender"], h["amount"])
                for e in human:                          # a person's own life, a person's own blocks: the most it can be is the creature's power
                    COMBAT["human"].remove(e)
                    claim = (claims or {}).get(e["ref"]) or (claims or {}).get(e["name"])
                    if claim:
                        said_hits[e["ref"]] = (e["defender"], min(int(claim[1] if claim[1] is not None else e["power"]), e["power"]))
                lki = {h["ref"]: h["lki"] for h in hits_ if h.get("lki")}
                try:
                    lines, deltas = a.combat_damage(said_hits, gone=lki)
                except IllegalAction as ex:              # (a hit whose creature is nowhere any more: said, never a stuck combat)
                    lines, deltas = [f"Combat damage triggers: {ex}"], {}
                said += [x for x in lines if not re.match(r".+ deals \d+ to ", x)]   # the hits are already off their life, and said
                if deltas.get(sn):                       # its own lifelink on what reached a player
                    change_life(sn, deltas[sn], "combat")
                said += combat_account(sn)
                for line in said:
                    combat_line(sn, line, "damage")
        finally:
            COMBAT_BATCH[0] -= 1
    if not COMBAT_BATCH[0]:
        auto_game_over()
    return said


def combat_gate(sn: str, act: str, b: dict) -> tuple | None:
    """Why this action must wait for combat, or is refused by it: (http code, payload), or None to go ahead. For every caller (a seat's own
    key and the host's). PHASE_LOCK is the caller's.
      attack   one declaration a turn, locked once made (M19), and only up to the declare-attackers step (M17);
      damage, next, end, and anything that would walk the turn out of combat (a land, a sorcery-speed cast): not while the attack's
               triggers or the defenders' blocks are still owed; in the window after blocks they are the attacker's PASS, and the damage is
               dealt as soon as everyone else has passed too."""
    if sn not in VPS or PHASE.get("player") is None or not on_turn(sn):
        return None
    v = VPS[sn]
    d = combat_decl(sn)
    if act == "attack":
        if d is not None or DAMAGED.get("key") == (sn, v.turn):
            again = []
            for ref in (b.get("assign") or {}) if isinstance(b.get("assign"), dict) else []:
                try:
                    p = v.perm(ref)
                except Exception:                    # noqa: BLE001 — not a permanent of this seat: the general refusal covers it
                    continue
                if (ATTACKED.get("key") == (sn, v.turn) and p.id in (ATTACKED.get("ids") or ())) or p.attacked_turn == v.turn:
                    again.append(v.shown(p))
            return 409, {**phase_public(), "error": (f"{', '.join(again)} is already attacking this turn. " if again else "")
                         + "Your attackers are declared: one declaration a turn, and it is locked once made "
                           "(a mistake is taken back with BACK, to the start of the step)"}
        if PHASE["step"] > STEPS.index("declare attackers") and not TEAMS:
            return 409, {**phase_public(), "error": f"attackers are declared before blocks: it is now {STEPS[PHASE['step']]}"}
        return None
    if d is None or d["stage"] == "done":
        return None
    fast = act == "cast" and not b.get("face_down") and any(
        c.get("name", "").lower() == str(b.get("name", "")).lower() and ("Instant" in (c.get("types") or []) or "Flash" in __import__("player").printed_keywords(c))
        for c in cast_pool(v, b))
    if not (act in ("damage", "next", "end") or (act in ("land", "cast", "turn-up") and not fast)):
        return None
    combat_tick()
    if d["stage"] == "triggers":
        who = combat_trigger_holders(sn)
        return 409, {**phase_public(), "error": f"your attack's triggers first: {', '.join(who)} still has a choice to make; blocks and damage come after"}
    if d["stage"] == "blocks":
        wait = [e for e in combat_entries(sn) if not e.get("answered")]
        return 409, {**phase_public(), "error": f"waiting for {', '.join(sorted({e['defender'] for e in wait}))} to declare blocks "
                                                f"({', '.join(e['name'] for e in wait)}): the defender always gets to answer"}
    if d["stage"] == "window":
        d["passed"] = True                           # the attacker has nothing more to add: its pass
        (d.get("acted") or set()).discard(sn)
        combat_tick()
    if d["stage"] != "done":
        ps = passes_state()
        waiting = [n for n in ps["need"] if n not in ps["passed"]] or combat_trigger_holders(sn)
        return 409, {**phase_public(), "waiting": ", ".join(waiting),
                     "error": f"waiting on passes: {', '.join(waiting)} (blocks are in; combat damage is dealt as soon as they pass: retry)"}
    return None


def cast_timing_refusal(sn: str, b: dict) -> str | None:
    """M18: a spell that is neither an instant nor has flash is cast only in its controller's own main phase. Checked by the cast ACTION (the
    page's button is only a hint; a stale page, a bot or the command line come through here). None when the timing is fine, or when this
    is not a hand/exile/command-zone cast (Beam me up and the top of the library have their own checks in the cast itself). On its own turn
    a cast made during combat is let through: it walks the turn on to main 2 once combat is done (combat_gate)."""
    import player as _pl
    v = VPS.get(sn)
    if v is None or PHASE.get("player") is None or b.get("beam") or b.get("from") == "library-top":
        return None
    try:
        card = v.commander if b.get("commander") else v.hand_or_exile(str(b.get("name") or ""), bool(b.get("from_exile")))[0]
    except Exception:                                # noqa: BLE001 — not a card it holds: the cast itself says so
        card = None
    if not card or "Land" in (card.get("types") or []):
        return None
    if not b.get("face_down") and ("Instant" in (card.get("types") or []) or "Flash" in _pl.printed_keywords(card)):
        return None
    what = "a face-down spell" if b.get("face_down") else card["name"]
    stp = STEPS[PHASE["step"]]
    if not on_turn(sn):
        return (f"sorcery speed: {what} is cast only in your own main phase (it is {PHASE['player']}'s {stp}; only an instant or a card with "
                f"flash now). Nothing was paid")
    if PHASE["step"] < STEPS.index("main 1") or stp in ("end step", "cleanup"):
        return f"sorcery speed: {what} is cast only in your main phase (it is your {stp}). Nothing was paid"
    return None


def combat_legal(sn: str, out: dict) -> dict:
    """The legal list, put right for the v6c combat (after legal_actions has built it): no second attack once the declaration is locked; the
    attacker's `damage` only once blocks are in, never Next or End around an open combat; for the defender no block that "can't block
    this turn" forbids, and one entry that answers every attacker at once."""
    if sn not in VPS or not isinstance(out.get("actions"), list) or out.get("note") in ("the game is over", "you have lost"):
        return out
    A = out["actions"]
    if on_turn(sn) and turn_is_ending():             # End has been taken: nothing more is offered for the half second the hand-over takes
        A[:] = []
        out["note"] = "your turn is ending: the next turn starts in a moment"
        return out
    if VPS[sn].__dict__.get("pending_choices"):      # the table is waiting on this seat's answer: that is all it can do (legal_actions said so)
        return out
    d = combat_decl(sn)
    if d is not None:
        A[:] = [a for a in A if a["action"] != "attack"]
    if d is not None and d["stage"] != "done":
        A[:] = [a for a in A if a["action"] not in ("damage", "next", "end")]
        if d["stage"] == "window":
            A.append({"action": "damage", "body": {"seat": sn}, "why": "blocks are in: deal combat damage (your pass; it is dealt once everyone has passed)"})
            out["note"] = "blocks are in: act now if you want to, then deal the damage"
        elif d["stage"] == "blocks":
            wait = sorted({e["defender"] for e in combat_entries(sn) if not e.get("answered")})
            out["note"] = "waiting for " + ", ".join(wait) + " to declare blocks"
        else:
            out["note"] = "your attack's triggers are answered first"
    owed = combat_owed(sn)
    if owed:
        ok = {e.get("ref") or e["name"]: {p.id for p in combat_blockers_for(sn, e)} for e in owed}

        def allowed(a):
            if a["action"] != "block":
                return True
            refs = ([a["body"]["blocker"]] if a["body"].get("blocker") else []) + list(a["body"].get("blockers") or [])
            ids = ok.get(a["body"].get("attacker"), set())
            return all(str(r).lstrip("#").isdigit() and int(str(r).lstrip("#")) in ids for r in refs)
        A[:] = [a for a in A if allowed(a) and a["action"] != "pass"]      # (blocks are declared, not passed)
        A.append({"action": "block", "body": {"seat": sn, "none": True},
                  "why": f"no blocks at all: take {sum(hit_power(e) for e in owed)} from " + ", ".join(e["name"] for e in owed)})
        out["note"] = "you are attacked: declare your blocks (one `block` with `blocks` {attacker: [blockers]} covers every attacker)"
    return out


def combat_save() -> dict:
    """What a saved game needs so that a restart in the middle of a turn knows the combat (M17): the attackers, the blocks, who has attacked
    and whether damage has been dealt, with the turn tick that "this turn" marks are measured against, and whether the game is over."""
    import player as _pl
    return {"COMBAT": COMBAT, "ATTACKED": ATTACKED, "DAMAGED": DAMAGED, "TEAM_TURN": TEAM_TURN, "STEP_FIRED": set(STEP_FIRED),
            "GAME_OVER": dict(GAME_OVER), "tick": _pl.DEATH_TICK[0], "died": _pl.DIED_THIS_TURN[0]}


def combat_restore(d: dict | None):
    """The other half of combat_save. A game saved before it existed has none of this: plain defaults (no combat, nobody has attacked)."""
    import player as _pl
    if not isinstance(d, dict):
        return
    for g, k in ((COMBAT, "COMBAT"), (ATTACKED, "ATTACKED"), (DAMAGED, "DAMAGED"), (TEAM_TURN, "TEAM_TURN"), (GAME_OVER, "GAME_OVER")):
        if isinstance(d.get(k), dict):
            g.clear()
            g.update(d[k])
    for k, dflt in (("pending", []), ("hits", []), ("human", []), ("used", set()), ("announced", {}), ("dealt", {}), ("decl", {})):
        COMBAT.setdefault(k, dflt)
    ATTACKED.setdefault("key", None); ATTACKED.setdefault("ids", set()); DAMAGED.setdefault("key", None)
    TEAM_TURN.setdefault("begun", set()); TEAM_TURN.setdefault("done", set())
    GAME_OVER.setdefault("over", False); GAME_OVER.setdefault("order", []); GAME_OVER.setdefault("winner", None)
    STEP_FIRED.clear(); STEP_FIRED.update(d.get("STEP_FIRED") or ())
    if isinstance(d.get("tick"), int):
        _pl.DEATH_TICK[0] = d["tick"]
    if "died" in d:
        _pl.DIED_THIS_TURN[0] = d["died"]
    if any(x.get("stage") != "done" for x in (COMBAT.get("decl") or {}).values()):
        combat_loop_start()


def combat_loop_start():
    """The slow loop behind combat_tick, started the first time anything attacks."""
    if COMBAT_LOOP["on"]:
        return
    COMBAT_LOOP["on"] = True

    def run():
        while True:
            time.sleep(0.5)
            try:
                if time.time() - COMBAT_LOOP["busy"] < 0.4:
                    continue                         # a seat is acting right now: its request moves combat on itself, lines in order
                if not GAME_OVER.get("over") and any(x.get("stage") != "done" for x in (COMBAT.get("decl") or {}).values()):
                    before = [(k, x.get("stage")) for k, x in (COMBAT.get("decl") or {}).items()]
                    combat_tick()
                    if before != [(k, x.get("stage")) for k, x in (COMBAT.get("decl") or {}).items()]:
                        snapshot()                   # (a damage step dealt between requests is saved like any other change)
            except Exception as e:                   # noqa: BLE001 — never let the loop die
                print(f"combat loop: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
    threading.Thread(target=run, daemon=True).start()


def game_over_cleanup():
    """M24: once the game is over nothing is owed any more. Open questions, a combat in progress and any priority window are dropped, with
    one public line when there was something to drop."""
    with VP_LOCK:
        n = 0
        for v in VPS.values():
            n += len(v.__dict__.get("pending_choices") or []) + bool(v.__dict__.get("pending_surveil")) + bool(v.__dict__.get("pending_scry"))
            v.__dict__["pending_choices"] = []
            v.__dict__["pending_surveil"] = 0
            v.__dict__["pending_scry"] = 0
        n += len([e for e in COMBAT["pending"] if not e.get("answered")])
        COMBAT["pending"].clear(); COMBAT["hits"].clear(); COMBAT["human"].clear()
        for d in (COMBAT.get("decl") or {}).values():
            d["stage"] = "done"
        PRIORITY.update(waiting=[], seats=[], deadline=0.0, beat_until=0.0)
    if n:
        emit("say", speaker="Table", text=f"The game is over: {n} thing{'s' if n != 1 else ''} the table was waiting on "
                                          f"(a choice, a block) {'are' if n != 1 else 'is'} dropped.", action="game-end", auto=True, speech="")


TURN_END: dict = {"key": None, "at": 0.0}             # (seat, its turn) seen at its cleanup step, and when it was first seen there


def turn_is_ending() -> bool:
    """End has been taken for the turn the table is in, and the next turn starts in a moment. A virtual seat's turn reaches the cleanup step
    only that way (End sets it, then hands the turn on half a second later). A second End in that half second (a double tap, a bot that reads
    the list again at once) used to run the end step again and schedule a second hand-over, which started the next turn twice: it is
    refused, and the legal list offers nothing meanwhile. Only for 3 s from when it is first seen: End can never stay refused."""
    p = PHASE.get("player")
    v = VPS.get(p)
    if v is None or PHASE["step"] != len(STEPS) - 1 or GAME_OVER.get("over"):
        return False
    if TURN_END.get("key") != (p, v.turn):
        TURN_END.update(key=(p, v.turn), at=time.time())
    return time.time() - TURN_END["at"] < 3.0


AFTER_GAME_OK = frozenset({"say", "life", "new-game", "fair-reveal", "peek"})   # what the HOST may still do through a seat once the game is over


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


ANNOUNCE_LOCK = threading.Lock()
ANNOUNCED_AT: dict = {}        # (seat, the line, normalised) → when it last put cards on that seat's board
LAST_ANNOUNCED = {"seat": None, "names": [], "at": 0.0}   # what the latest announcement added, for "No, I said …"
_PERMANENT_TYPES = ("Land", "Creature", "Artifact", "Enchantment", "Planeswalker", "Battle")


def stays_on_battlefield(name: str) -> bool:
    """A card that is a permanent when played: a land, creature, artifact, enchantment, planeswalker or battle, by its
    type in the card file (the FRONT face for a two-faced card). Instants and sorceries are not; neither is a card the
    file doesn't know."""
    import oracle
    front = ((oracle.card(name) or {}).get("type") or "").split(" // ")[0]
    if not front or re.search(r"\b(Instant|Sorcery)\b", front):
        return False
    return any(re.search(rf"\b{t}\b", front) for t in _PERMANENT_TYPES)


def record_announced(who: str | None, text: str, cards: list[str]) -> list[str]:
    """A person's play, said aloud or typed at the table ("I play Silverbluff Bridge and pass"), goes on THAT person's
    public board — a cloud room has no brain to write it there for them, so the line was the only record. Only cards the
    speaker named as the thing they played (the words after "I play/cast", not a target: "Path to Exile on your Llanowar
    Elves" adds nothing), only permanents, only for a person's own seat (a pilot's board is the engine's), only once the
    game has started. Commander is singleton, so a card already on that board isn't added again; a basic land is, unless
    the same line was just recorded (said twice, or said and typed). Returns the names added."""
    if not who or who in VPS or who not in [h["name"] for h in HUMANS] or PHASE.get("player") is None or not cards:
        return []
    from match import name_phrase, norm
    phrase = name_phrase(text)
    if not phrase:
        return []
    ph = norm(phrase)
    named = [c for c in cards if norm(c.split(" // ")[0]) in ph and stays_on_battlefield(c)]
    if not named:
        return []
    import oracle
    now = time.time()
    line = (who, norm(text))
    commander = next((h.get("commander") for h in HUMANS if h["name"] == who), None)
    with ANNOUNCE_LOCK:
        if now - ANNOUNCED_AT.get(line, 0) < 120:
            return []
        prev = PUBLIC_BOARD.get(who) or {}
        perms = [dict(p) for p in prev.get("permanents", [])]
        added = []
        for c in named:
            basic = "Basic" in ((oracle.card(c) or {}).get("type") or "")
            if not basic and any(p.get("name") == c and not p.get("face_down") for p in perms):
                continue                                   # already on the board: one copy of each card in Commander
            if len(perms) >= 120:
                break
            perms.append({"name": c[:80], "tapped": False})
            added.append(c)
        if not added:
            return []
        ANNOUNCED_AT[line] = now
        for k in [k for k, t in ANNOUNCED_AT.items() if now - t > 600]:
            del ANNOUNCED_AT[k]
        LAST_ANNOUNCED.update(seat=who, names=list(added), at=now)
        PUBLIC_BOARD[who] = {**prev, "permanents": perms, "graveyard": list(prev.get("graveyard", [])),
                             "commander_out": bool(prev.get("commander_out")) or (commander in added),
                             "updated": round(now, 2)}
    emit("board3d", seat=who, n=len(perms), announced=added)
    return added


def pilot_records_board(pilot: str, who: str, b: dict) -> tuple[int, dict]:
    """A pilot (an AI seat playing over the API with its own key) ADDS what it read from a person's photo or words to
    that person's board. It never replaces or removes anything — the person's own /api/my-board does that, and wins. Each
    card it adds carries who recorded it and from what; a non-basic card already on the board isn't added again (one copy
    of each card in Commander); 120 permanents / 200 graveyard cards, as everywhere. The game log says what was recorded,
    so the person can see it and correct it."""
    import oracle
    perms_in, grave_in = board_from_body(b)
    src = " ".join(str(b.get("from") or "photo or chat").split())[:40]
    with ANNOUNCE_LOCK:
        prev = PUBLIC_BOARD.get(who) or {}
        perms = [dict(p) for p in prev.get("permanents", [])]
        grave = list(prev.get("graveyard", []))
        added, gy_added, removed = [], [], []
        for name in [str(x)[:80] for x in (b.get("remove") or [])][:20]:     # a correction: only what THIS pilot recorded, the last copy of each
            i = next((i for i in range(len(perms) - 1, -1, -1) if perms[i].get("name") == name and perms[i].get("by") == pilot), None)
            if i is not None:
                perms.pop(i)
                removed.append(name)
        for p in perms_in:
            basic = "Basic" in ((oracle.card(p["name"]) or {}).get("type") or "")
            if not p.get("face_down") and not basic and any(q.get("name") == p["name"] and not q.get("face_down") for q in perms):
                continue
            if len(perms) >= 120:
                break
            perms.append({**p, "by": pilot, "from": src})
            added.append("a face-down card" if p.get("face_down") else p["name"])
        for g in grave_in:
            if len(grave) >= 200:
                break
            grave.append(g)
            gy_added.append(g)
        cmd_back = b.get("commander_out") is False and bool(prev.get("commander_out"))     # a correction: the commander is still in the command zone
        if not added and not gy_added and not removed and not b.get("commander_out") and not cmd_back:
            return 200, {"ok": True, "seat": who, "added": [], "graveyard_added": [], "removed": [], "permanents": len(perms)}
        PUBLIC_BOARD[who] = {**prev, "permanents": perms, "graveyard": grave,
                             "commander_out": (False if cmd_back else bool(prev.get("commander_out")) or bool(b.get("commander_out"))),
                             "updated": round(time.time(), 2)}
    if cmd_back and not added and not gy_added and not removed:
        emit("chat", by=pilot, text=f"{pilot} put {who}'s commander back in the command zone (it had marked it as cast in error)", recorded_for=who)
        emit("board3d", seat=who, n=len(perms), by=pilot)
        return 200, {"ok": True, "seat": who, "by": pilot, "added": [], "removed": [], "graveyard_added": [], "commander_out": False, "permanents": len(perms)}
    if removed and not added and not gy_added:
        emit("chat", by=pilot, text=f"{pilot} took {', '.join(removed)} off {who}'s board (it had recorded it in error)", recorded_for=who, removed=removed)
        emit("board3d", seat=who, n=len(perms), by=pilot)
        return 200, {"ok": True, "seat": who, "by": pilot, "added": [], "removed": removed, "graveyard_added": [], "permanents": len(perms)}
    what = ", ".join(added) + (("; to the graveyard: " + ", ".join(gy_added)) if gy_added else "")
    emit("chat", by=pilot, text=f"{pilot} recorded {who}'s {what or 'commander as cast'} from {who}'s {src} — "
                                f"{who}, fix your board if that's wrong", recorded_for=who, recorded=added, recorded_from=src)
    emit("board3d", seat=who, n=len(perms), by=pilot)
    return 200, {"ok": True, "seat": who, "by": pilot, "added": added, "removed": removed, "graveyard_added": gy_added, "permanents": len(perms)}


def unrecord_announced(who: str | None) -> list[str]:
    """ "No, I said Sol Talisman.": take the misheard cards the latest announcement put on that person's board off it
    again (the last copy of each), so the correction replaces them instead of adding beside them."""
    with ANNOUNCE_LOCK:
        if not who or LAST_ANNOUNCED["seat"] != who or time.time() - LAST_ANNOUNCED["at"] > 120:
            return []
        prev = PUBLIC_BOARD.get(who) or {}
        perms = [dict(p) for p in prev.get("permanents", [])]
        gone = []
        for c in LAST_ANNOUNCED["names"]:
            i = next((i for i in range(len(perms) - 1, -1, -1) if perms[i].get("name") == c), None)
            if i is not None:
                perms.pop(i)
                gone.append(c)
        LAST_ANNOUNCED.update(seat=None, names=[], at=0.0)
        for k in [k for k in ANNOUNCED_AT if k[0] == who]:
            del ANNOUNCED_AT[k]                            # the corrected line may be said again
        if gone:
            PUBLIC_BOARD[who] = {**prev, "permanents": perms, "updated": round(time.time(), 2)}
    return gone


def seat_deck_names(name: str) -> list[str]:
    """The card names a seat's deck can show on the table — a pilot's virtual deck, or the deck file whose commander is
    the person's commander — so a board photo is read against real candidates. Empty when unknown."""
    path = DECK_OF.get(name)
    declared = [c["name"] for c in (DECKDECL.get(name) or {}).get("cards", []) if c.get("known")]
    if not path and declared:                          # the list this person declared at the check-in
        return sorted(set(declared))[:200]
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
    h = {k: v for k, v in HIGHROLL.items() if not str(k).startswith("_")}
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
    if mode not in ("physical", "quantum", "table") or not 2 <= sides <= 100:
        return 400, {"error": "mode is physical, quantum or table; sides 2-100"}
    names = [n for n in turn_order() if not is_out(n)]
    HIGHROLL.clear()
    HIGHROLL.update(mode=mode, sides=sides, round=1, contenders=names, rolls={}, winner=None, by=by[:30],
                    seating=names, started=round(time.time(), 2))
    if mode == "table":                               # no network: this table's own fresh randomness, published with the roll
        import fair
        import secrets as _secrets
        src = (hashlib.sha256(f"seed|{args.seed}|{SEED_GAME[0]}|highroll".encode()).hexdigest() if args.seed is not None
               else _secrets.token_hex(32))
        HIGHROLL.update(entropy=src, source=("--seed (replayable, not fair)" if args.seed is not None
                                             else "this table's own randomness (no network needed)"),
                        formula="fair.die_roll(entropy, 'highroll|<round>|<name>', sides)")
        while not HIGHROLL["winner"]:
            r = str(HIGHROLL["round"])
            HIGHROLL["rolls"][r] = {n: fair.die_roll(src, f"highroll|{r}|{n}", sides) for n in HIGHROLL["contenders"]}
            _highroll_settle()
    elif mode == "quantum":
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
    if not HIGHROLL.pop("_emitted", False):
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
    emit("highroll", **{k: v for k, v in highroll_public().items() if k != "started_turn"})   # the log shows the roll BEFORE the first turn it decided
    HIGHROLL["_emitted"] = True
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
    if not HIGHROLL.pop("_emitted", False):
        emit("highroll", **highroll_public())
    return 200, highroll_public()


CEREMONY = {"on": False}
READY: set = set()                                   # seats that pressed Ready at the check-in (cleared with a new game)


def checkin_public() -> dict:
    """Who is at the table before the game starts: each seat is empty (nobody has claimed it), claimed, or ready. A seat run by the table's
    own brain (an AI with a release behind it) is always ready; a person's seat and a virtual-deck pilot's seat are ready when
    that player says so. START waits until every seat is ready."""
    seats = []
    for n in turn_order():
        kind = "pilot" if n in PILOTS else "ai" if n in VPS else "human"
        claimed = n in SEAT_KEYS or kind == "ai"
        seats.append({"name": n, "kind": kind, "claimed": bool(claimed), "ready": kind == "ai" or n in READY, **seat_declaration(n)})
    return {"seats": seats, "all_ready": all(x["ready"] for x in seats), "waiting": [x["name"] for x in seats if not x["ready"]],
            "undeclared": [x["name"] for x in seats if not x["declared"]]}


# ── the commander and deck list each seat declares at the check-in (deckdecl.py) ──────────────────────────────────────────
# name → {"commander", "cards": [{"count", "name", "known"}], "total", "recognised", "url", "ts"}. A person's seat declares its
# commander (required to press Ready when the check-in is required) and may add a deck list or a link to one; a virtual-deck
# seat (pilot or table AI) is declared by its deck file. Public: deck lists are public in Commander once declared. Kept in the
# snapshot, so a room that restarts keeps it.
DECKDECL: dict[str, dict] = {}
DECL_LOCK = threading.Lock()


def _deck_file_list(n: str) -> dict:
    """A virtual-deck seat's list from its deck file: {"cards", "total", "recognised", "deck_name"}; {} if unreadable."""
    path = DECK_OF.get(n)
    if not path:
        return {}
    try:
        d = json.loads(Path(path if Path(path).is_absolute() else HERE.parent / path).read_text())
        cards = [{"count": int(c.get("count", 1)), "name": c["name"], "known": True}
                 for c in (d.get("commander") or []) + (d.get("mainBoard") or []) if c.get("name")]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    total = sum(c["count"] for c in cards)
    return {"cards": cards, "total": total, "recognised": total, "deck_name": d.get("name")}


def seat_declaration(n: str) -> dict:
    """What the check-in shows for a seat: its commander, how many cards its declared list has, its list link, declared or not."""
    decl = DECKDECL.get(n) or {}
    if n in VPS:
        auto = {} if decl.get("cards") or decl.get("url") else _deck_file_list(n)
        return {"commander": (VPS[n].commander or {}).get("name"), "deck_cards": decl.get("total") or auto.get("total") or 0,
                "deck_url": decl.get("url"), "deck_name": getattr(VPS[n], "deck_name", None), "declared": True, "auto": not decl}
    commander = next((h.get("commander") for h in HUMANS if h["name"] == n), None)
    return {"commander": commander, "deck_cards": decl.get("total") or 0, "deck_url": decl.get("url"),
            "declared": bool(commander), "auto": bool(commander) and not decl.get("commander")}


def deckdecl_public(n: str) -> dict:
    """GET /api/deckdecl?seat=Name: the seat's declared commander and deck list (card names) or link. Public."""
    decl = DECKDECL.get(n) or {}
    out = {"seat": n, **seat_declaration(n)}
    if decl.get("url"):
        out["url"] = decl["url"]
    elif decl.get("cards"):
        out.update(cards=decl["cards"], total=decl["total"], recognised=decl["recognised"])
    elif n in VPS and args.decklists == "auto":
        f = _deck_file_list(n)
        out.update(cards=f.get("cards", []), total=f.get("total", 0), recognised=f.get("recognised", 0), from_deck_file=True)
    return out


def declare_deck(nm: str, b: dict) -> tuple[int, dict]:
    """A seat declares (or changes, before the game starts) its commander and/or deck list. b: {"commander"?, "decklist"?}.
    The commander is matched against the card file and becomes the seat's commander everywhere (board, 3D board, nicknames),
    exactly as --human 'Name|Commander' would have. Each change is one public line in the game log."""
    import oracle
    import deckdecl as D
    # Started = the first turn is under way, OR START has run (the high roll has a winner) OR the opening-hand step is open: with the
    # mulligan step PHASE["player"] stays None until everyone keeps, so it alone would let a seat swap its commander after seeing its hand.
    if PHASE["player"] is not None or HIGHROLL.get("winner") or MULL.get("on"):
        return 409, {"error": "the game has already started: commanders and deck lists are declared before it"}
    has_cmd = str(b.get("commander") or "").strip() != ""
    has_list = "decklist" in b and b.get("decklist") is not None
    if not has_cmd and not has_list:
        return 400, {"error": "name your commander (and, if you like, your deck list)"}
    try:
        canon = D.match_commander(b["commander"], oracle.db()) if has_cmd else None
        if canon and nm in VPS and canon != (VPS[nm].commander or {}).get("name"):
            raise D.DeclareError(f"{nm} plays a virtual deck led by {(VPS[nm].commander or {}).get('name')}: its commander is fixed by the deck")
        newlist = None
        if has_list:
            got = D.read_decklist(b.get("decklist"))
            newlist = ({"url": got["url"]} if "url" in got else
                       D.parse_list(got["list"], oracle.db()) if "list" in got else {})
    except D.DeclareError as e:
        return 400, {"error": str(e), "suggestions": e.suggestions}
    with DECL_LOCK:
        cur = DECKDECL.get(nm) or {}
        before = (cur.get("commander"), cur.get("url"), cur.get("total"), cur.get("recognised"), [c["name"] for c in cur.get("cards", [])])
        new = dict(cur)
        if canon:
            new["commander"] = canon
        if has_list:
            for k in ("cards", "total", "recognised", "url"):
                new.pop(k, None)
            new.update(newlist or {})
        after = (new.get("commander"), new.get("url"), new.get("total"), new.get("recognised"), [c["name"] for c in new.get("cards", [])])
        changed = before != after
        new["ts"] = time.time()
        DECKDECL[nm] = new
        if canon and nm not in VPS:
            for h in HUMANS:
                if h["name"] == nm:
                    h["commander"] = canon                    # the board, board3d and voice matching all read this
            NICKNAMES[canon.split(",")[0]] = canon
    if changed:
        commander = new.get("commander") or seat_declaration(nm).get("commander")
        text = (f"{nm} declares {commander} as commander" if commander else f"{nm} declares a deck list") + D.summary(new)
        emit("chat", by=nm, text=text, deckdecl=nm)
        snapshot()
    return 200, {"declared": deckdecl_public(nm), "changed": changed, "checkin": checkin_public()}


def checkin_required() -> bool:
    return STRICT_SEATS and os.environ.get("TABLE_HIGHROLL") != "first" and os.environ.get("TABLE_CHECKIN") != "off"


# ── T15: the pre-game mulligan step. Between the high roll and the first turn every virtual-deck seat sees its opening seven and answers
# Keep or Mulligan (London: a new seven each time, then one card on the bottom per mulligan taken when it keeps). The first turn begins when
# every seat has kept. People (pilot seats) answer from their hand page; an engine seat decides by the suggestion's rule at once; a pilot seat
# that never answers (a bot with a key that doesn't know the step) is decided by the same rule after TABLE_MULLIGAN_SECS, so the start can't hang.
MULL: dict = {"on": False, "done": False, "first": None, "since": 0.0}
MULL_SECS = float(os.environ.get("TABLE_MULLIGAN_SECS") or 180.0)      # 0: wait for a person for ever


def mull_enabled() -> bool:
    """On by default. TABLE_HIGHROLL=first (tests only: no ceremony, the first seat simply starts) skips it too, unless TABLE_MULLIGAN=on."""
    want = (os.environ.get("TABLE_MULLIGAN") or "").lower()
    if want in ("on", "off"):
        return want == "on"
    return os.environ.get("TABLE_HIGHROLL") != "first"


def mull_seats() -> list[str]:
    return [n for n in (turn_order() or list(VPS)) if n in VPS and not is_out(n)]


def mull_waiting() -> list[str]:
    return [n for n in mull_seats() if not VPS[n].__dict__.get("mull_kept")]


def mull_public() -> dict:
    """What everyone may know: who has kept, and at how many cards. Never a card."""
    return {"on": bool(MULL["on"]), "first": MULL.get("first"), "waiting": mull_waiting() if MULL["on"] else [],
            "taken": {n: int(VPS[n].__dict__.get("mull_n", 0)) for n in mull_seats()} if MULL["on"] else {},
            **({"seconds_left": round(max(0.0, MULL["since"] + MULL_SECS - time.time()), 1)} if MULL["on"] and MULL_SECS else {})}


def mull_private(sn: str) -> dict | None:
    """This seat's own view of the step (its hand is already in its state): what it has taken, what it would owe, and the suggestion."""
    if not MULL["on"] or sn not in VPS:
        return None
    v = VPS[sn]
    n = int(v.__dict__.get("mull_n", 0))
    return {"open": True, "kept": bool(v.__dict__.get("mull_kept")), "taken": n, "bottom": n, "keeps": max(0, 7 - n),
            "can_mulligan": n < 7 and not v.__dict__.get("mull_kept"), "advice": v.mull_advice(), "waiting": mull_waiting(),
            "rule_bottom": __import__("player").mulligan_bottoms(v.hand, n, *v.deck_shape()) if n and not v.__dict__.get("mull_kept") else []}


def mull_say(n: str, sizes: list, auto: str = ""):
    """The public record of a seat's answers: sizes only ("X mulligans to six", "X keeps six"), never a card."""
    for w in sizes[:-1]:
        emit("say", speaker=n, text=f"{n} mulligans to {w}.", action="mulligan", auto=True)
    emit("say", speaker=n, text=f"{n} keeps {sizes[-1]}." + (f" ({auto})" if auto else ""), action="mulligan", auto=True)


def mull_gate(first: str) -> bool:
    """Called where the first turn would begin. True: the mulligan step is open (now, or still) and the turn must wait."""
    if MULL["done"] or not mull_enabled() or not VPS or PHASE.get("player") is not None:
        return False
    with VP_LOCK:
        if not MULL["on"]:
            if any(v.turn or v.battlefield for v in VPS.values()):     # a game somebody already set up by hand: not an opening hand any more
                MULL["done"] = True
                return False
            MULL.update(on=True, first=first, since=time.time())
            for n in mull_seats():
                VPS[n].__dict__.update(mull_n=0, mull_kept=False)
            emit("mulligan", kind="open", first=first, seats=mull_seats(),
                 text="Opening hands: every player keeps or mulligans before the first turn.")
            for n in mull_seats():
                if is_bot(n):                                           # nobody drives it: the rule decides, at once
                    mull_say(n, VPS[n].mull_by_rule())
                else:
                    emit("attention", kind="mulligan", addressee=n, text="Your opening hand: keep it, or mulligan.")
        if mull_waiting():
            return True
        MULL.update(on=False, done=True)
        emit("mulligan", kind="closed", first=MULL.get("first"), text="Every player has kept.")
        return False


def mull_check_done():
    """A seat just kept: when it was the last one, the first turn begins (as the roll's winner would have begun it)."""
    with VP_LOCK:
        if not MULL["on"] or mull_waiting():
            return
        first = MULL.get("first") or (turn_order() or list(VPS))[0]
    threading.Timer(0.3, lambda: (start_turn(first), snapshot())).start()


def mull_loop():
    """A seat that hasn't answered in MULL_SECS is decided by the rule, and the log says the table did it."""
    while True:
        time.sleep(1.0)
        try:
            if not MULL["on"] or not MULL_SECS or HOLD.get("on") or time.time() - MULL["since"] < MULL_SECS:
                continue
            with VP_LOCK:
                late = mull_waiting()
                for n in late:
                    mull_say(n, VPS[n].mull_by_rule(floor=7 - int(VPS[n].__dict__.get("mull_n", 0))), auto=f"no answer in {MULL_SECS:g} s: the table kept for them")
            if late:
                snapshot()
                mull_check_done()
        except Exception as e:                              # noqa: BLE001 — never let the loop die
            print(f"mull_loop: {e}", file=sys.stderr, flush=True)


def begin_game(by: str) -> tuple[int, dict]:
    """START / the first NEXT: every game opens with the high roll for who goes first (recorded in the game log, with the
    randomness it came from). A cloud room draws from ANU quantum / drand and falls back to the table's own randomness; a table at
    home never leaves the laptop. TABLE_HIGHROLL=first skips it (tests only: the first seat in the order simply starts)."""
    mode = os.environ.get("TABLE_HIGHROLL") or ("quantum" if CLOUD else "table")
    with PHASE_LOCK:
        if PHASE["player"] is not None:
            return 200, phase_public()
        if mode == "first":
            start_turn(turn_order()[0])
            return 200, phase_public()
        if checkin_required() and not checkin_public()["all_ready"]:
            ci = checkin_public()
            return 409, {**phase_public(), "error": "waiting for " + ", ".join(ci["waiting"]) + " to check in (press Ready)", "checkin": ci}
        if checkin_required() and checkin_public()["undeclared"]:
            ci = checkin_public()
            return 409, {**phase_public(), "error": "waiting for " + ", ".join(ci["undeclared"]) + " to declare a commander", "checkin": ci}
        if HIGHROLL.get("mode") == "physical" and not HIGHROLL.get("winner"):
            return 409, {**phase_public(), "highroll": highroll_public(), "error": "a real-dice roll is under way: enter the dice"}
        if CEREMONY["on"]:
            return 202, {**phase_public(), "highroll": highroll_public(), "rolling": True}
        CEREMONY["on"] = True

    def run():
        try:
            code, d = highroll_start(mode, 20, by or "table")
            if code != 200 and mode == "quantum":              # no quantum source reachable: the table's own randomness, said so in the log
                code, d = highroll_start("table", 20, by or "table")
            if code != 200:
                emit("highroll", error=str(d.get("error", "the roll failed")), mode=mode)
        finally:
            CEREMONY["on"] = False
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(15)                                              # a quantum draw usually answers in a second or two
    return 200, {**phase_public(), "highroll": highroll_public()}


WAIT_SINCE: dict = {}              # (seat, what) -> when the table began waiting on it (R12)
WAIT_SAID: dict = {}               # (seat, what) -> the last whole 30 s (rounded up in steps) the stall was said at


def waiting_on() -> list[dict]:
    """Who the table is waiting on, and for what, public words only: [{seat, what, since, secs}]. what: "pass" (priority), "blocks", "a choice
    (<source>)", "a response to <spell>", "the mulligan". R12/R24: a silent stall (111 s, 353 s, 289 s with no event) used to be
    indistinguishable from a person thinking or the table being down. After 20 s of the same wait (before the 30 s a pass clock allows) the log says so (a `table-note`, then every 2 min)."""
    now = time.time()
    cur: dict = {}
    if not GAME_OVER.get("over"):
        try:
            for n, v in list(VPS.items()):
                if is_out(n):
                    continue
                if combat_owed(n):
                    cur[(n, "blocks")] = now
                for it in list(v.__dict__.get("pending_choices") or []):
                    cur[(n, f"a choice ({it.get('source')})")] = now
                if MULL.get("on") and not v.__dict__.get("mull_kept"):
                    cur[(n, "the mulligan")] = now
            if HELD.get("seat"):
                for n in held_audience():
                    cur[(n, f"a response to {HELD.get('name')}")] = now
            nxt = PASS.get("waiting_on")
            if nxt and PHASE.get("player") is not None and nxt != PHASE["player"] and not MULL.get("on"):
                cur[(nxt, "pass")] = PASS.get("since", now)
        except Exception as e:                           # noqa: BLE001 — a report must never break the phase route
            print(f"waiting_on: {e}", file=sys.stderr, flush=True)
    for k in list(WAIT_SINCE):
        if k not in cur:
            WAIT_SINCE.pop(k, None)
            WAIT_SAID.pop(k, None)
    out = []
    for k, t0 in cur.items():
        since = WAIT_SINCE.setdefault(k, t0)
        secs = now - since
        out.append({"seat": k[0], "what": k[1], "since": round(since, 2), "secs": int(secs)})
        step_ = 0 if secs < 20 else 1 + int((secs - 20) // 120)
        if step_ and WAIT_SAID.get(k) != step_:
            WAIT_SAID[k] = step_
            emit("table-note", kind="wait", seat=k[0], what=k[1], secs=int(secs), text=f"Waiting on {k[0]} for {k[1]} ({int(secs)} s).")
    return out


def phase_public() -> dict:
    out = _phase_public0()
    out["waiting_on"] = waiting_on()                  # R12: public, with the seat, what, and since when
    out["bots"] = [n for n in VPS if is_bot(n)]       # R24: `ai` lists every seat the table runs; these are the ones nobody drives
    out["driven"] = list(PILOTS)                      # …and these answer through a seat key (a person, or a bot such as the autopilot)
    return out


def _phase_public0() -> dict:
    with PHASE_LOCK:
        now, is_open = time.time(), priority_open()
        left = 0.0
        if is_open:
            left = PRIORITY["beat_until"] - now if now < PRIORITY["beat_until"] else PRIORITY["deadline"] - now
        return {"player": PHASE["player"], "step": STEPS[PHASE["step"]], "index": PHASE["step"], "steps": STEPS, "game_id": FAIR.get("game"),
                "begun": bool(PHASE.get("begun")),
                "combat": combat_public(),            # v6c: attackers, declared blocks, the stage of the combat (public)
                "order": turn_order(), "waiting": list(PRIORITY["seats"]) if is_open else [],
                "seconds_left": round(max(0.0, left), 1), "ai": list(VPS),
                "windows": WINDOWS["on"], "window_secs": args.priority_window, "hold": HOLD["on"],
                **({"back_request": {"by": BACK_REQ.get("by"), "to": BACK_REQ.get("to")}} if BACK_REQ and time.time() - BACK_REQ.get("at", 0) < 180 else {}),
                **({"teams": dict(TEAMS)} if TEAMS else {}),
                **({"game_over": True, "winner": GAME_OVER.get("winner"), "finish": list(GAME_OVER.get("order") or [])}
                   if GAME_OVER.get("over") else {}),
                "open_questions": sum(1 for t in TODOS if t.get("kind") == "question" and not t["done"]),
                "passes": passes_state(), "autopass": dict(AUTOPASS), "autopass_default": AUTOPASS_DEFAULT,
                **({"mulligan": mull_public()} if MULL["on"] else {}),
                **({"held": held_public()} if HELD.get("seat") else {}),
                **({"start": START_HINT, "checkin": checkin_public(), "checkin_required": checkin_required()} if PHASE["player"] is None else {})}


def unanswered_by_people(attacker_seat: str) -> list:
    """T6: this seat's attackers that a PERSON (a pilot seat) hasn't answered yet. A person blocks, or says they take it: the table waits for that
    answer however long it takes (no grace period, no auto-pass). Bots and agents keep DEFENDER_GRACE."""
    return [e for e in COMBAT["pending"] if e["attacker"] == attacker_seat and not e.get("answered") and e["defender"] in PILOTS
            and not is_out(e["defender"]) and (not e.get("ref") or attacker_perm(e) is not None)]      # (an attacker that has left the battlefield needs no answer)


DEFENDER_GRACE = 20.0      # an agent's `damage` is offered once every attacker is answered, or this long after the attack
SAY_MAX = 500              # one line of table talk, in characters (a body may be 256 KB; a line read aloud may not)
SPECTATE: list = []        # spectator snapshots, one per turn start: what /watch shows while the NEXT turn is played


def spectate_snapshot():
    """The public board and the log as they stand when a turn ends. Spectators see only these, a turn behind the players, so a
    viewer can't relay a live tell (who paused, what was just drawn and played) to someone at the table."""
    with VP_LOCK:
        with EV_LOCK:
            last = EVENTS[-1]["id"] if EVENTS else 0
        SPECTATE.append({"event_id": last, "ts": round(time.time(), 2), "board": board3d(), "life": life_table(),
                         "turn_of": PHASE.get("player")})
        del SPECTATE[:-20]


def start_turn(name: str):
    """Hand the turn to a seat: an AI seat plays its whole turn; a human starts at untap."""
    if GAME_OVER.get("over"):                          # M24: a finished game hands out no more turns
        return
    try:
        spectate_snapshot()
    except Exception as e:                             # noqa: BLE001 — a spectator view must never stop the game
        print(f"spectate snapshot failed: {e}", file=sys.stderr, flush=True)
    if PHASE["player"] is None:
        GAME_FIRST["seat"] = name
        if mull_gate(name):                            # T15: opening hands first; the turn starts when every seat has kept (mull_check_done)
            snapshot()
            return
    with VP_LOCK:
        combat_flush(None)                             # an unblocked attacker whose turn ended without a `damage` still hits
        combat_reset()
    with VP_LOCK:
        settle_and_drain()                             # (a death on the way out of combat can have left something behind)
        prev_ = PHASE.get("player")
        ended_ = ({prev_, *teammates(prev_)} if prev_ else set())   # whose turn just ended: "until the end of your next turn" counts their own
        for v_ in VPS.values():
            for line_ in v_.clear_until_eot(ended_) or []:  # "until end of turn" ends with the turn
                emit("say", speaker=v_.name, text=line_, action="trigger", auto=True)
    PHASE.update(player=name, step=0, begun=False)
    TEAM_TURN["begun"], TEAM_TURN["done"] = set(), set()
    STEP_FIRED.clear()
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


def hand_turn_on(ender: str, nxt: str):
    """The scheduled hand-over after an AI/pilot seat's End: only if the table is still at the end of `ender`'s turn. `end` is offered (and accepted)
    again while the half-second wait runs, and every press scheduled a hand-over of its own: the second one restarted the NEXT player's turn after
    they had begun it ("Bo / main 1" became "Bo / untap" under a listed Station: golden path, 1 run in ~6). One hand-over per turn end."""
    with PHASE_LOCK:
        if GAME_OVER.get("over") or PHASE.get("player") != ender or PHASE["step"] != len(STEPS) - 1:
            return
        start_turn(nxt)
    snapshot()


def next_step(by: str | None = None, shared: bool = False, confirm: bool = False, key: str = "") -> tuple[int, dict]:
    """NEXT: move the active human's turn on one step. Refused while an AI still holds priority
    (until its window times out); the last step passes the turn to the next seat in order."""
    if GAME_OVER.get("over"):                          # M24
        return 409, {**phase_public(), "error": "the game is over"}
    person = next((h["name"] for h in HUMANS if by and h["name"].lower() == by.lower()), None)
    pilot = next((n for n in PILOTS if by and n.lower() == by.lower()), None)
    if not person and pilot and seat_key_ok(pilot, key) and PHASE["player"] is None:
        return begin_game(pilot)                       # a virtual-deck seat may START the game (its turns run from /hand)
    if not person and pilot and seat_key_ok(pilot, key):
        # A virtual-deck seat passes and ends its turns with the buttons on /hand. NEXT used to answer need_seat here, so the page
        # opened the claim popup, reloaded, and the next press failed the same way: an endless loop for the person playing it.
        return 403, {**phase_public(), "error": f"{pilot} plays a virtual deck: pass and end your turn with the buttons on /hand, not NEXT"}   # 403, no need_seat
    if not person or not seat_key_ok(person, key):    # only a device that claimed this seat passes for it
        return 403, {**phase_public(), "error": ("claim your seat first (👤 at the top)" if not person or person not in SEAT_KEYS
                                                 else f"this device isn't {person}'s — claim your own seat (👤)"),
                     "need_seat": True}
    by = person
    if PHASE["player"] is None:
        return begin_game(by)                         # the opening ceremony: the high roll (outside the lock: its thread starts the turn)
    with PHASE_LOCK:
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
            combat_tick()                              # v6c: that may have been the last pass after blocks
            return 200, phase_public()
        if HOLD["on"]:
            return 409, {**phase_public(), "error": f"on hold ({HOLD['by'] or 'the table'}) — release ⏸ Hold to go on"}
        q = next((t for t in TODOS if t.get("kind") == "question" and not t["done"]), None)
        if q:                                         # the table must settle what a player asked first
            return 409, {**phase_public(), "error": f"waiting on {q.get('ask')}'s question: {q['text'][:90]}"}
        if priority_open():                           # an AI still holds priority in this step: NEXT is refused until it answers
            holders = PRIORITY["waiting"] or PRIORITY["seats"]   # (or its window times out), as the design says
            return 409, {**phase_public(), "error": "priority: waiting on " + ", ".join(holders)}
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


# ── BACK as a real rewind (Michael's call, 2026-11-09): the players control the game. A ring of the game as it stood at the start of each
# recent step; BACK on a person's turn (a pilot seat) restores one, so life, damage, the board, hands, libraries (same order: no reshuffle,
# no redraw) and pending questions are as they were. Never into or across a bot's moves. The log is not rewound: BACK is said in it.
from collections import deque
REWIND: deque = deque(maxlen=30)
BACK_REQ: dict = {}
ACT_STARTS: deque = deque(maxlen=200)
VOID: list = []                                         # (from, to] event ids a BACK undid: those moves no longer happened                  # the event id at the start of each action (an action's own phase steps come after it)                                    # a person's BACK waiting for another person's yes: {by, at, to}


def persons() -> list[str]:
    """The people at the table: human seats and pilot seats (a person playing a virtual deck)."""
    return [h["name"] for h in HUMANS] + sorted(PILOTS)


def is_bot(name) -> bool:
    return name in VPS and name not in PILOTS


def _rewind_state() -> dict:
    import player as _pl
    return {"VPS": VPS, "LIFE": LIFE, "PHASE": dict(PHASE), "COMBAT": COMBAT, "ATTACKED": ATTACKED, "DAMAGED": DAMAGED, "TEAM_TURN": TEAM_TURN,
            "STEP_FIRED": STEP_FIRED, "HAND_N": HAND_N, "PUBLIC_BOARD": PUBLIC_BOARD, "PRIORITY": dict(PRIORITY),
            "died": _pl.DIED_THIS_TURN[0], "tick": _pl.DEATH_TICK[0]}


def rewind_mark(ev: dict):
    """The game at the start of the step this phase event opens. Local memory only (it holds hands and libraries); never sent anywhere."""
    import pickle
    try:
        with VP_LOCK:
            if not PHASE.get("player") or GAME_OVER.get("over"):
                return
            if PHASE["step"] > 0 and not PHASE.get("begun") and PHASE["player"] in VPS:
                return                                 # a virtual seat's begin walking to main 1 before its untap/draw has run: not a resting point
            REWIND.append({"player": PHASE["player"], "step": PHASE["step"], "ev": ev["id"], "blob": pickle.dumps(_rewind_state())})
    except Exception as e:                            # noqa: BLE001 — a rewind point must never stop the game
        print(f"rewind mark failed: {e}", file=sys.stderr, flush=True)


def rewind_target() -> tuple[dict | None, str | None]:
    """(the point BACK returns to, or None and why). The start of this step if anything happened in it, else the start of the one before.
    Refused if that point is in a bot's turn, or a bot did anything since it (its moves are already made: undoing them is not ours)."""
    cur = PHASE.get("player")
    if cur is None:
        return None, "the game hasn't started"
    marks = list(REWIND)
    if not marks:
        return None, "there is no saved point to go back to yet"
    with EV_LOCK:
        moves = [e["id"] for e in EVENTS if e["type"] in ("say", "life") and e.get("action") != "back"
                 and not any(lo < e["id"] <= hi for lo, hi in VOID)]
    last = moves[-1] if moves else 0
    if marks[-1]["ev"] < last:
        # something happened since the last saved point: back to the start of the step that action began in. An action's own steps (a block
        # that walks combat on to its damage) come AFTER its first events, so the action's START is the cut, not its last line.
        start = max((a for a in ACT_STARTS if a < last), default=last - 1)
        i = max((k for k, m in enumerate(marks) if m["ev"] <= start), default=-1)
    else:
        i = len(marks) - 2                             # nothing since the last saved point: one step further back
    # A pilot's untap with auto-begin on: restoring it would begin the turn again at once (the same draw), so it is no place to stop:
    # the point before it (the previous player's last step) is where the turn can really be taken back to.
    while i >= 0 and marks[i]["step"] == 0 and marks[i]["player"] in PILOTS and args.pilot_auto_begin == "on":
        i -= 1
    if i < 0:
        return None, "there is no earlier saved point (the table keeps the last 30 steps)"
    t = marks[i]
    if is_bot(t["player"]):
        return None, f"can't go back into {t['player']}'s turn: a bot's moves are already made"
    with EV_LOCK:
        bot_moves = [e for e in EVENTS if e["id"] > t["ev"] and e["type"] == "say" and is_bot(e.get("speaker"))]
    if bot_moves:
        return None, f"{bot_moves[0]['speaker']} (a bot) has acted since then: BACK can't undo a bot's moves"
    return t, None


def rewind_to(t: dict, by: str) -> tuple[int, dict]:
    """Restore the point t. The log keeps everything and says what was undone, in its own public words (nothing hidden is listed)."""
    import pickle
    import player as _pl
    with VP_LOCK:
        d = pickle.loads(t["blob"])
        with EV_LOCK:
            undone = [e for e in EVENTS if e["id"] > t["ev"] and e["type"] == "say" and e.get("action") not in ("say", "chat", "back")
                      and not any(lo < e["id"] <= hi for lo, hi in VOID)]
        VPS.clear(); VPS.update(d["VPS"])
        for g, k in ((LIFE, "LIFE"), (COMBAT, "COMBAT"), (ATTACKED, "ATTACKED"), (DAMAGED, "DAMAGED"), (TEAM_TURN, "TEAM_TURN"),
                     (HAND_N, "HAND_N"), (PUBLIC_BOARD, "PUBLIC_BOARD"), (PRIORITY, "PRIORITY")):
            g.clear(); g.update(d[k])
        STEP_FIRED.clear(); STEP_FIRED.update(d["STEP_FIRED"])
        PHASE.clear(); PHASE.update(d["PHASE"])
        PRIORITY.update(waiting=[], seats=[], beat_until=0.0, deadline=0.0)
        PASS.update(key=None, passed=[])               # v6c: the round of the step it returns to starts again (the window after blocks reads it),
        AI_OPENED["key"] = None                        # and is announced again
        _pl.DIED_THIS_TURN[0], _pl.DEATH_TICK[0] = d["died"], d["tick"]
        while REWIND and REWIND[-1] is not t:          # the points after it are a future that no longer happened
            REWIND.pop()
        BACK_REQ.clear()
    what = "; ".join(f"{e.get('speaker')}: {str(e.get('text'))[:60]}" for e in undone[-3:]) or "nothing yet"
    emit("phase", player=PHASE["player"], step=STEPS[PHASE["step"]], index=PHASE["step"], by=by, back=True, rewound=True)
    ev_ = emit("say", speaker="Table", text=f"Back to {PHASE['player']}, {STEPS[PHASE['step']]} (undoing: {what}).", action="back", auto=True, speech="")
    VOID.append((t["ev"], ev_["id"]))
    for n in VPS:
        emit("attention", kind="rewound", addressee=n, text=f"The table went back to {PHASE['player']}, {STEPS[PHASE['step']]}: read your state again.")
    try:
        snapshot()
    except Exception:                                 # noqa: BLE001
        pass
    return 200, {**phase_public(), "rewound": True}


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


PUBLIC_TEXT_FIELDS = ("text", "speech", "attacker", "card", "creature", "line")


def emit(event_type: str, **fields) -> dict:
    # (not "kind": heard/attention events carry their own "kind" field)
    if event_type == "phase" and not fields.get("kind") and GAME_OVER.get("over"):
        return {**fields, "id": _ev_id[0], "ts": round(time.time(), 2), "type": event_type, "dropped": True}   # M24: a finished game has no more steps
    if event_type in ("table-note", "attention", "say") and fields.get("action") != "say":
        # what the TABLE writes into the public log (a player's own words, the `say` action, are theirs): no face-down card's name
        try:
            for k_ in PUBLIC_TEXT_FIELDS:
                if isinstance(fields.get(k_), str):
                    fields[k_] = public_text(fields[k_])
        except Exception as e:                        # noqa: BLE001 — a log line must never stop the game
            print(f"public_text failed: {e}", file=sys.stderr, flush=True)
    with EV_LOCK:
        _ev_id[0] += 1
        e = {**fields, "id": _ev_id[0], "ts": round(time.time(), 2), "type": event_type}   # a field can never
        # overwrite the event's own id/ts/type (a todo's "id" once did: every page's cursor jumped back)
        EVENTS.append(e)
        del EVENTS[:-3000]
        EV_COND.notify_all()
        _append("events.jsonl", e)                    # the public stream, persisted
    if event_type == "phase" and not fields.get("rewound"):
        rewind_mark(e)                                 # the start of a step: a point BACK can return to
    if event_type == "attention" and fields.get("kind") == "attacked":
        journal_due(fields.get("addressee"), "attacked")
    elif event_type == "life" and isinstance(fields.get("delta"), int) and fields["delta"] < 0 \
            and isinstance(fields.get("life"), int) and fields["life"] <= 0:
        journal_due(fields.get("player"), "eliminated")
        if not COMBAT_BATCH[0]:                        # (inside a combat damage step the check waits for the whole step: M23)
            auto_game_over()
    return e


def _read_research(name: str) -> list[dict]:
    try:
        return [json.loads(l) for l in open(research_dir() / name) if l.strip()]
    except OSError:
        return []


import journal as journal_mod                         # in-game journals (docs/RESEARCH-ENGINE-GOAL.md R1)
GAME_OVER: dict = {"over": False, "order": [], "winner": None}


def auto_game_over():
    """Every seat is virtual and only one player (or 2HG team) is left above 0: the game is over. The table says so, records
    the finish order and stops handing out turns (game 3's bot played turns 6-157 against an empty seat; a 2HG table handed
    the turn on after a whole team was out). A table with people at it is never ended for them: they own their life totals
    and end it with /api/game-over."""
    if GAME_OVER["over"] or not VPS or HUMANS:
        return
    lt = life_table()
    side = lambda n: TEAMS.get(n) or n
    alive = {side(n) for n, v in lt.items() if isinstance(v, int) and v > 0 and n not in CONCEDED}
    if len(alive) != 1:
        return
    win = alive.pop()
    order = sorted(lt, key=lambda n: (side(n) != win, -(lt[n] if isinstance(lt[n], int) else 0)))
    winner = order[0]
    finish_game(order, winner, by="table", auto=True)


def game_summary() -> dict:
    """What happened, from the PUBLIC event log only (what /api/events already shows everyone): turns, each life total over time, life lost,
    damage dealt on each player's turn, creatures that died, spells cast (the biggest first), and the finishing blow. Nothing from a hand or a
    library can be in it, because nothing from a hand or a library is in the public log."""
    import re as _re
    import oracle
    with EV_LOCK:
        evs = [e for e in EVENTS if e.get("type") in ("phase", "life", "say", "new-game", "died", "cast")]
    last_new = max((i for i, e in enumerate(evs) if e["type"] == "new-game"), default=-1)
    evs = evs[last_new + 1:]                              # this game only
    totals = life_table()
    turns: dict = {}
    life_lines: dict = {n: [] for n in totals}
    lost: dict = {n: 0 for n in totals}
    dealt: dict = {n: 0 for n in totals}
    died: list = []
    casts: list = []
    cur, n_turn, finishing, step_from = None, 0, None, 0
    structured_died = structured_cast = False
    legacy_casts, legacy_died = [], []                      # a log from before the structured events: the old reading of the words, as a fallback only
    for e in evs:
        if e["type"] == "phase" and not e.get("kind"):
            step_from = e.get("id", 0)                    # where the current step began: a damage step's hits all come after it
        if e["type"] == "phase" and e.get("index") == 0 and e.get("player"):
            cur, n_turn = e["player"], n_turn + 1
            turns[cur] = turns.get(cur, 0) + 1
        elif e["type"] == "life" and e.get("player") in totals and isinstance(e.get("delta"), int):
            if isinstance(e.get("life"), int):
                life_lines[e["player"]].append({"turn": n_turn, "life": e["life"]})
            if e["delta"] < 0:
                lost[e["player"]] += -e["delta"]
                if cur and cur != e["player"] and cur in dealt:
                    dealt[cur] += -e["delta"]
                if isinstance(e.get("life"), int) and e["life"] <= 0 and finishing is None:
                    finishing = {"player": e["player"], "amount": -e["delta"], "by": str(e.get("by") or ""), "turn": n_turn, "on_turn_of": cur,
                                 "_from": step_from}
        elif e["type"] == "died":                         # R3: the engine's own record of a death
            died.append({"name": e.get("name"), "turn": n_turn, "owner": e.get("owner"), "cause": e.get("cause"), "token": bool(e.get("token"))})
            structured_died = True
        elif e["type"] == "cast":                         # R4: the card's own name and mana value
            casts.append({"player": e.get("player"), "name": e.get("name"), "turn": n_turn, "mv": e.get("mv") or 0})
            structured_cast = True
        elif e["type"] == "say" and e.get("action") == "concede":
            if finishing is None:                          # R15: nobody dealt it; life is untouched
                finishing = {"player": e.get("speaker"), "amount": 0, "by": "concede", "turn": n_turn, "on_turn_of": cur, "_from": step_from,
                             "dealt_by": None, "line": str(e.get("text") or "")[:200]}
        elif e["type"] == "say":
            text = str(e.get("text") or "")
            for m in _re.finditer(r"I cast ([^.]+?)(?: from exile| from the command zone)?\.", text):
                legacy_casts.append({"player": e.get("speaker"), "name": m.group(1).strip(), "turn": n_turn})
            for m in _re.finditer(r"(?:^|[.:] )([A-Z][^.:]{1,60}?) dies\.", text):
                legacy_died.append({"name": m.group(1).strip(), "turn": n_turn})
    if finishing is not None and finishing.get("by") == "combat":
        # M23: combat damage is one step: the blow is ALL of it (5 + 4 + 2 at a seat on 5 is 11, to -6), dealt by the attacker, with these
        blow = [e for e in evs if e["type"] == "life" and e.get("player") == finishing["player"] and e.get("by") == "combat"
                and isinstance(e.get("delta"), int) and e["delta"] < 0 and e.get("id", 0) > finishing["_from"]]
        if blow:
            finishing.update(amount=-sum(e["delta"] for e in blow), final=blow[-1].get("life"),
                             dealt_by=next((e.get("attacker") for e in blow if e.get("attacker")), None) or finishing.get("on_turn_of"),
                             **{"with": [{"name": e.get("source"), "amount": -e["delta"]} for e in blow if e.get("source")]})
    if finishing is not None:
        finishing["to"] = finishing["player"]             # who took it (`player`, kept for older pages, is the same seat)
        finishing.setdefault("dealt_by", finishing.get("on_turn_of") if finishing.get("on_turn_of") != finishing["player"] else None)
        finishing.pop("_from", None)
    if finishing is not None and finishing.get("by") != "concede":                              # the last thing the player whose turn it was said before the blow
        before = [e for e in evs if e["type"] == "say" and e.get("speaker") == finishing.get("on_turn_of")
                  and e.get("action") in (("attack",) if finishing.get("by") == "combat" else ("attack", "damage", "cast", "activate", "trigger"))
                  and (finishing.get("by") != "combat" or str(e.get("text") or "").startswith("I attack"))]
        fin_id = next((e["id"] for e in evs if e["type"] == "life" and e.get("player") == finishing["player"] and isinstance(e.get("life"), int) and e["life"] <= 0), None)
        before = [e for e in before if fin_id is None or e["id"] <= fin_id]
        if before:
            finishing["line"] = str(before[-1].get("text") or "")[:200]
    if not structured_cast:
        casts = legacy_casts
    if not structured_died:
        died = legacy_died
    mv = lambda c: float(c.get("mv") or 0)                 # the real mana value, carried by the cast event (oracle.card() has no cmc)
    key = sorted(casts, key=lambda c: -mv(c))[:6]          # (sorted() is stable: equal mana values stay in the order cast)
    for n, pts in life_lines.items():                      # where each started: the final total less everything that changed it
        start = totals.get(n) if not pts else None
        if pts:
            ch = sum(e["delta"] for e in evs if e["type"] == "life" and e.get("player") == n and isinstance(e.get("delta"), int))
            start = (totals.get(n) or 0) - ch if isinstance(totals.get(n), int) else None
        life_lines[n] = [{"turn": 0, "life": start}] + pts
    return {"turns": n_turn, "turns_by_player": turns, "life": life_lines, "life_lost": lost, "damage_dealt_on_their_turn": dealt,
            "died": died[-30:], "casts": len(casts), "key_spells": key, "finishing_blow": finishing}


def finish_game(order, winner, by="table", auto=False):
    """The game is over: the winner, the final life totals and a summary of the public log go to every page (the `game-over` event and
    /api/game/summary), the log is saved one last time for the retro, and, with voices on, the table says who won. A rematch is the
    host's call (/api/reset): nothing restarts by itself."""
    totals = life_table()
    try:
        summary = game_summary()
    except Exception as e:                                # noqa: BLE001 — a summary must never stop the game ending
        print(f"game summary failed: {e}", file=sys.stderr, flush=True)
        summary = {}
    GAME_OVER.update(over=True, order=order, winner=winner, totals=totals, summary=summary, team=TEAMS.get(winner), at=round(time.time(), 2))
    emit("game-over", order=order, winner=winner, team=TEAMS.get(winner), totals=totals, summary=summary, by=by, **({"auto": True} if auto else {}))
    game_over_cleanup()                                   # M24: open questions, a combat in progress, a priority window: all dropped
    who = TEAMS.get(winner) or winner
    line = f"Game over. {who} wins" + (f", at {totals.get(winner)} life." if isinstance(totals.get(winner), int) else ".")
    if os.environ.get("TABLE_NO_VOICE") != "1":            # one line, spoken by the table's voice pages; an agent table stays quiet
        emit("say", speaker="Table", text=line, action="game-over", auto=True, speech=line)
    try:                                                  # the final save, for the retro: the public log and the summary
        with EV_LOCK:
            log_ = list(EVENTS)
        with open(research_dir() / "game-final.json", "w") as fh:
            json.dump({"winner": winner, "team": TEAMS.get(winner), "order": order, "totals": totals, "summary": summary, "by": by,
                       "ended": time.strftime("%Y-%m-%d %H:%M:%S"), "events": log_}, fh, indent=1)
    except Exception as e:                                # noqa: BLE001
        print(f"final log save failed: {e}", file=sys.stderr, flush=True)
    try:
        snapshot()
    except Exception as e:                                # noqa: BLE001
        print(f"final snapshot failed: {e}", file=sys.stderr, flush=True)
    for n in VPS:
        journal_due(n, "game_end")


def do_concede(name: str, by: str = "seat") -> tuple[int, dict]:
    """R15: a seat gives up. It is logged as "X concedes." (never as a life loss: its life total is untouched), the seat is out, and when one side
    is left the game is over with that side the winner. VP_LOCK is taken here."""
    with VP_LOCK:
        if GAME_OVER.get("over"):
            return 409, {"error": "the game is already over"}
        if name in CONCEDED or name not in life_table():
            return 409, {"error": f"{name} has already conceded" if name in CONCEDED else f"no seat '{name}'"}
        CONCEDED.add(name)
        emit("say", speaker=name, text=f"{name} concedes.", action="concede", auto=True)
        lt = life_table()
        side = lambda n: TEAMS.get(n) or n
        alive = [n for n in turn_order() if not is_out(n) and isinstance(lt.get(n), int) and lt[n] > 0]
        sides = {side(n) for n in alive}
        if len(sides) <= 1:
            winner = alive[0] if alive else next((n for n in lt if n != name), name)
            order = [winner, *[n for n in sorted(lt, key=lambda n: -(lt[n] if isinstance(lt[n], int) else 0)) if n not in (winner, name)], name]
            finish_game(order, winner, by="concede")
            return 200, {"over": True, "winner": winner, "conceded": name, "totals": GAME_OVER.get("totals")}
        if PHASE.get("player") == name:                    # it was its turn: the turn goes on without it
            try:
                nxt = seat_after(name)
                if nxt and nxt in VPS:
                    emit("say", speaker="Table", text=f"{name} is out; the turn passes on.", action="effect", auto=True)
            except Exception:                              # noqa: BLE001
                pass
        return 200, {"over": False, "conceded": name, "alive": alive}


def game_over_public() -> dict:
    """What every page shows when the game is over (public: the winner, the final totals, the summary of the public log)."""
    if not GAME_OVER.get("over"):
        return {"over": False}
    return {"over": True, **{k: GAME_OVER.get(k) for k in ("winner", "team", "order", "totals", "summary", "at")},
            "rematch": "the host starts a new game (Reset); nothing restarts by itself"}
JOURNALS = journal_mod.Journal(lambda: globals().get("FAIR", {}).get("game") or "", _append, _read_research)


def journal_due(seat_name, moment: str):
    """Ask an AI seat for a journal entry at a fixed moment. Its own event type (not "attention"), so no page
    pops an alert for it; the brain finds it with `tablectl journal-due`."""
    if seat_name in VPS and JOURNALS.fire(seat_name, moment):
        emit("journal-due", seat=seat_name, moment=moment)


IDENTITY: dict = {}                                    # seat -> what the seat declared about itself (docs/RESEARCH-ENGINE-GOAL.md R3)
ID_FIELDS = ("model", "provider", "prompt_version", "notes",
             "voice", "pronouns", "presents_as")        # how the AI wants to sound and be presented: its own call
ID_NUMBERS = {"voice_rate": (0.8, 1.25), "voice_pitch": (0.8, 1.2)}   # pace/pitch it asks for, kept in a human range


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
    for k, (lo, hi) in ID_NUMBERS.items():
        v = b.get(k)
        if v is not None:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return 400, {"error": f"{k} is a number ({lo} to {hi})"}
            rec[k] = round(min(hi, max(lo, float(v))), 2)
    if not rec.get("model"):
        return 400, {"error": "model is required: the exact model string, e.g. the API id"}
    try:
        from build_info import build_sha
        harness = build_sha()
    except Exception:                                    # noqa: BLE001
        harness = "dev"
    IDENTITY[seat_name] = {**rec, "declared_ts": round(time.time(), 2)}
    emit("identity", seat=seat_name, **{k: rec[k] for k in ("presents_as", "pronouns", "voice") if k in rec})   # pages re-voice
    try:
        with open(research_dir() / "identity.json", "w") as fh:
            json.dump({"harness": harness, "seats": IDENTITY}, fh, indent=1)
    except OSError as e:
        print(f"identity log failed: {e}", flush=True)
    return 200, {"seat": seat_name, **IDENTITY[seat_name], "harness": harness}


def journal_reset():
    IDENTITY.clear()
    JOURNALS.reset()
    CONCEDED.clear()
    GAME_OVER.clear()
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
    "/api/phase/back": ("by", False), "/api/phase/back/confirm": ("by", False), "/api/phase/hold": ("by", False), "/api/phase/windows": ("by", False),
    "/api/todo/answer": ("by", False), "/api/todo/done": ("by", False), "/api/placed": ("by", False),
}
STRICT_SEATS = CLOUD or os.environ.get("STRICT_SEATS") == "1"
# What another device on the LAN may reach: the player view and public table state. Nothing that
# reveals or changes the AI's cards, resets the game, or feeds the mic/camera.
LAN_OK = {("GET", "/me"), ("GET", "/api/events"), ("GET", "/watch"), ("GET", "/api/spectate"), ("GET", "/3d"), ("GET", "/api/life"), ("GET", "/api/fair"),
          ("GET", "/api/fair/verify"), ("GET", "/api/voice-config"), ("GET", "/api/card"), ("GET", "/lobby"), ("GET", "/api/lobby"), ("GET", "/api/game/summary"),
          ("POST", "/api/fair/word"), ("POST", "/api/life"), ("POST", "/api/seat/check"),
          ("GET", "/api/phase"), ("GET", "/api/checklist"), ("GET", "/api/seat/claims"), ("POST", "/api/seat/claim"), ("POST", "/api/seat/handoff"), ("POST", "/api/autopass"), ("POST", "/api/ready"), ("POST", "/api/declare-deck"), ("GET", "/api/deckdecl"), ("GET", "/api/cardnames"), ("GET", "/api/highroll"), ("POST", "/api/highroll/start"), ("POST", "/api/highroll/roll"), ("POST", "/api/phase/next"), ("POST", "/api/phase/back"), ("POST", "/api/phase/back/confirm"), ("POST", "/api/phase/windows"), ("POST", "/api/phase/hold"), ("GET", "/api/todos"), ("POST", "/api/todo/done"), ("POST", "/api/todo/answer"), ("POST", "/api/declare/attack"), ("POST", "/api/my-board"), ("POST", "/api/public-board"), ("POST", "/api/openmic/rate"), ("POST", "/api/chat"), ("POST", "/api/chat/photo"), ("POST", "/api/card-action"), ("GET", "/api/history"), ("GET", "/log"), ("GET", "/api/placed"), ("POST", "/api/placed"), ("GET", "/xr"), ("GET", "/api/board3d"),
          ("GET", "/table"), ("GET", "/api/ai/state"), ("GET", "/board"),     # the table page as a viewer: its mic, camera, reset
                                                          # and AI-turn controls POST to routes still local-only
          ("GET", "/api/journal/due"), ("GET", "/api/journal"), ("POST", "/api/journal"), ("POST", "/api/identity"),   # a pilot's own journal and identity: the handlers need that seat's key
          ("GET", "/stage"), ("GET", "/api/stage"), ("POST", "/api/stage/hand"), ("GET", "/hand"),
          ("GET", "/vendor/three.module.min.js"), ("GET", "/vendor/three.core.min.js"),
          ("GET", "/avatars/index.json")}
def host_allowed(host: str) -> bool:
    """A Host (or Origin host) this table answers to: localhost names, any IP literal (a LAN address; rebinding needs a NAME),
    and the names in TABLE_HOSTS (comma-separated; a leading dot allows a suffix, e.g. ".trycloudflare.com" for a quick tunnel)."""
    import ipaddress
    h = (host or "").strip().lower()
    if h.startswith("["):
        h = h[1:].split("]", 1)[0]
    elif h.count(":") == 1:
        h = h.split(":", 1)[0]
    if not h:
        return False
    if h in ("localhost", "127.0.0.1", "::1") or h.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        pass
    for x in [x.strip().lower() for x in os.environ.get("TABLE_HOSTS", "").split(",") if x.strip()]:
        if h == x or (x.startswith(".") and h.endswith(x)):
            return True
    return False


LAN_PREFIXES = ("/art/", "/vendor/", "/assets/", "/avatars/", "/captains/", "/photos/")   # static, public: stage code, light probe, models, captain lines
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
            if unrecord_announced(who):                # the misheard card came off this person's board: the right one goes on
                record_announced(who, "I cast " + fix, new)
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
            record_announced(who, expanded, cards)    # a person's own play goes on their public board
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
        ai = {**ai, **{k: v for k, v in IDENTITY.get(speaker, {}).items() if k in ("presents_as", "pronouns")}}   # its own words
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
            if delta > 0:
                VPS[player].gained_life(delta)
    else:
        LIFE[player] += delta
    emit("life", player=player, delta=delta, by=by, life=life_table()[player])
    return life_table()


def opponents_of(seat_name: str) -> list[str]:
    """Who "each opponent" is: everyone still in, not this seat and not its team."""
    team = {seat_name, *teammates(seat_name)}
    return [n for n in turn_order() if n not in team and not is_out(n)]


def hit_someone(source_seat: str, who: str, n: int, source: str, verb: str = "deals"):
    """Damage or life loss to a player that isn't combat. One writer per life total, always: a virtual-deck seat's is the table's (it takes
    combat off the same way, and the page shows it with no press needed); a person's real-deck seat is theirs alone, so it is only said."""
    words = f"{source} deals {n} damage to {who}" if verb == "deals" else f"{who} loses {n} life ({source})"
    if who in VPS and args.ping_damage == "apply":
        change_life(who, -n, source[:40])
        emit("attention", kind="effect", addressee=who, text=f"{words}. The table has taken it off your life (you're at {life_table().get(who)}): no need to change it.")
    else:
        emit("attention", kind="effect", addressee=who, text=f"{words}. Your life total is yours: mark it yourself.")
        emit("say", speaker=source_seat, text=f"{words}: {who}, mark it yourself.", action="effect", speech="")


# ── Counterspells: a response window for one spell. The engine resolves a spell the moment it is cast (there is no stack), so a counterspell had
# no moment to be cast in. Now, when a PILOT casts a spell and an opposing PILOT holds a counterspell it could cast right now at that spell (the
# card's own words: "Counter target [noncreature] spell [with mana value N or less] [unless its controller pays {N}]"), the spell is HELD: paid
# for, off the hand, not yet resolved. Those opponents are told and answer: cast the counterspell at it, or Pass. When they have all passed (or
# RESPOND_SECS ran out) it resolves exactly as it would have; countered, it goes to the graveyard unresolved. One spell at a time, no nesting:
# what is cast during a hold resolves at once (a counterspell can't be countered back), and nothing else at the table moves until it is settled.
HELD: dict = {"seat": None, "name": None, "waiting": set(), "until": 0.0, "id": 0, "why": []}
RESPOND_SECS = float(os.environ.get("TABLE_RESPOND_SECS") or 20.0)


def counter_holders(caster: str, card: dict) -> list[str]:
    """The pilot seats, opponents of `caster`, that hold a card they could cast right now to counter this spell."""
    import player as _pl
    if os.environ.get("TABLE_RESPOND") == "off" or "Land" in (card.get("types") or []):
        return []
    out = []
    team = {caster, *teammates(caster)}
    for n, v in VPS.items():
        if n in team or n not in PILOTS or is_out(n):
            continue
        for o in [*v.castable(), *v.exile_castable()]:
            c = o[1]
            fast = "Instant" in (c.get("types") or []) or "Flash" in _pl.printed_keywords(c)
            if not fast or o[3]:
                continue
            texts = [m_ for m_ in (_pl.modal_options(c) or (0, []))[1]] or [re.sub(r"\s*\([^)]*\)", "", x) for x in (c.get("text") or "").split("\n")]
            k_ = _pl.kicker_of(c)
            if k_ and k_[0] == "mana" and v.plan_payment((c.get("manaCost") or "") + k_[1]) is not None:
                texts.append(_pl.kicked_text(c.get("text") or "", True))       # Highly Illogical kicked: any spell
            if any(sl.get("zone") == "stack" and spell_fits(sl, card) for t_ in texts for sl in _pl.card_slots({"name": c["name"], "types": ["Instant"]}, t_)):
                out.append(n)
                break
    return out


def held_audience(caster: str | None = None) -> list[str]:
    """What the TABLE is told about who may answer a held spell: every opponent seat that could be asked, never just the ones that hold a
    castable answer (that is hidden information: it would say who has the counterspell). Only the real holders are asked (private
    `attention`); a window with no holder is never opened, so "someone may answer" is all this can reveal."""
    caster = caster or HELD.get("seat")
    if not caster or not HELD.get("waiting"):
        return []
    team = {caster, *teammates(caster)}
    return sorted(n for n in VPS if n not in team and n in PILOTS and not is_out(n))


def held_public() -> dict:
    v_ = VPS.get(HELD["seat"])
    paying = bool(v_ and any(x.get("kind") == "option" and (x.get("then") or {}).get("tax") for x in v_.__dict__.get("pending_choices") or []))
    return {"by": HELD["seat"], "spell": HELD["name"], "waiting": held_audience() or ([HELD["seat"]] if paying else []), "mv": HELD.get("mv", 0),
            **({"paying": True, "note": f"{HELD['seat']} decides whether to pay; at zero the spell is countered"} if paying else {}),
            "seconds_left": round(max(0.0, HELD["until"] - time.time()), 1)}


def held_open(caster: str, card: dict, holders: list, after: dict):
    """A pilot's spell is held for these seats' answer. `after`: what the cast route still owes once it resolves (the by-hand note, a wipe's attention)."""
    HELD.update(mv=float(card.get("manaValue") or 0))
    HELD.update(seat=caster, name=card["name"], waiting=set(holders), until=time.time() + RESPOND_SECS, id=HELD["id"] + 1, after=after, responded=set())
    hid = HELD["id"]
    for n in holders:
        emit("attention", kind="respond", addressee=n, by=caster, spell=card["name"], seconds=RESPOND_SECS, mv=float(card.get("manaValue") or 0),
             text=f"{caster} casts {card['name']}. You may respond now (cast a counterspell at it), or Pass to let it resolve.")
    emit("held", by=caster, spell=card["name"], waiting=held_audience(caster), seconds=RESPOND_SECS)

    def late():
        with VP_LOCK:
            if HELD["seat"] != caster or HELD["id"] != hid:
                return
            left = HELD["until"] - time.time()
            if left > 0.05:                             # the clock was set again (a response came in: its controller has time to answer it)
                threading.Timer(left + 0.2, late).start()
                return
            held_progress(force=True)
    threading.Timer(RESPOND_SECS + 0.2, late).start()


def held_progress(force: bool = False):
    """Move a held spell on if it can: countered (it is gone), its controller still owes "pay or not" (wait; when the time runs out, not paid: countered), every
    holder has answered or the time is up (it resolves). VP_LOCK is the caller's. Everything it does is said at the table."""
    caster = HELD.get("seat")
    if caster is None:
        return
    v = VPS.get(caster)
    after = HELD.get("after") or {}

    def close():
        HELD.update(seat=None, name=None, waiting=set(), until=0.0, after={}, responded=set())

    if v is None or v.__dict__.get("held_spell") is None:           # countered (or the seat is gone)
        emit("held", by=caster, spell=HELD.get("name"), countered=True)
        close()
        settle_and_drain()
        return
    tax = [x for x in v.__dict__.get("pending_choices") or [] if x.get("kind") == "option" and (x.get("then") or {}).get("tax")]
    if tax and not force:
        return
    for it in tax:                                                   # the time is up and it didn't pay: not paying is the answer, so the spell is countered
        for line in answer_choice(caster, it, {"at": caster, "ref": "opt:1"}):
            emit("say", speaker=caster, text=line + " (no answer in time: it was not paid)", action="trigger", auto=True)
    if v.__dict__.get("held_spell") is None:
        close()
        settle_and_drain()
        return
    if HELD["waiting"] and not force and time.time() < HELD["until"]:
        return
    if not force and any(VPS[n].__dict__.get("pending_choices") for n in HELD.get("responded") or () if n in VPS and not is_out(n)):
        return                                          # a response is still being made (Wesley's modes, a counterspell's target)
    name = HELD.get("name")
    close()
    for line in v.release_held():
        emit("say", speaker=caster, text=line, action="cast", speech=spoken(line))
    if after.get("note"):
        note_manual(v, name)
    if after.get("wipe"):
        for n in list(VPS):
            emit("attention", kind="removal", text=f"{caster} casts {name}.", addressee=n, spell=name, effect=after["wipe"], target="all")
    settle_and_drain()
    for v_ in list(VPS.values()):
        for t_ in list(v_.todo):
            if (v_.name, t_, v_.turn) not in NOTED:
                NOTED.add((v_.name, t_, v_.turn))
                emit("table-note", seat=v_.name, text=f"By hand: {t_}")
        v_.todo.clear()
    emit("held", by=caster, spell=name, resolved=True)


def held_gate(sn: str, act: str):
    """While a spell is held: the seats asked may answer (cast, activate, pass); its controller answers "pay or not" if asked; talk and the
    table's own questions go on. Anything else waits. Returns (status, body) to send instead of acting, or None to go ahead."""
    caster = HELD.get("seat")
    if caster is None:
        return None
    with VP_LOCK:
        if VPS.get(caster) is None or VPS[caster].__dict__.get("held_spell") is None:
            held_progress()
            return None
        if act == "pass" and sn in HELD["waiting"]:
            HELD["waiting"].discard(sn)
            emit("pass", by=sn, response=True, spell=HELD["name"], player=PHASE.get("player"), step=STEPS[PHASE["step"]])
            held_progress()
            snapshot()
            return 200, {"ok": True, "said": [], "responded": "pass", "public": VPS[sn].public()}
        if act in ("say", "choose", "life", "manual", "surveil", "scry", "swap"):
            return None
        if sn != caster and sn not in teammates(caster) and sn in PILOTS and act in ("cast", "activate", "turn-up"):
            return None                                 # an opponent's response (a counterspell names the held spell as its target)
        who = ", ".join(held_audience()) or caster
        return 409, {**phase_public(), "waiting": who,
                     "error": f"waiting on passes: {who} may respond to {caster}'s {HELD['name']} ({max(0.0, HELD['until'] - time.time()):.0f} s at most)"}


def annotate_legality(sn: str, v: dict):
    """T18: every button of a seat's own state says whether it can be pressed NOW and, if not, why, from the engine's own checks (the ones the
    action itself makes): each activated ability (`abilities[i].can / why_not`), Equip and Station per permanent, each card it could cast
    (`can_cast / why_not`: mana, timing, a legal target), cycling, and the graveyard / hand abilities. The page and a bot read these."""
    import player as _pl
    vp = VPS[sn]
    started = PHASE.get("player") is not None and not GAME_OVER.get("over")
    mine = started and on_turn(sn)
    main = bool(mine and STEPS[PHASE["step"]] in ("main 1", "main 2"))
    no_target = lambda slots: any(not s_.get("optional") and not aim_candidates(sn, s_) for s_ in slots or [])
    for x, p in zip(v.get("permanents") or [], vp.battlefield):
        for a in x.get("abilities") or []:
            why = None if started else "the game hasn't started"
            why = why or vp.ability_why_not(p, a["index"], sorcery_ok=main)
            if why is None and not a.get("modes"):
                eff_ = _pl.ACT_ONLY_RX.sub("", _pl.ACT_LESS_RX.sub("", a["effect"])).strip()
                if no_target(_pl.card_slots({"name": p.name, "types": ["Instant"]}, eff_)):
                    why = "no legal target right now"
            a["can"], a["why_not"] = why is None, why
        if x.get("equip_cost"):
            why = None if main else "Equip is sorcery speed: only in your own main phase"
            why = why or (None if vp.plan_payment(x["equip_cost"]) is not None else f"needs {x['equip_cost']}: {vp.mana_words()}")
            why = why or (None if any((c.is_("Creature") or c.face_down) and p.attached_to != c.id for c in vp.battlefield) else "no creature to equip")
            x["equip_can"], x["equip_why"] = why is None, why
            # the creatures it may go onto, whether or not it can be paid for right now (the page lights these up for a drag or a tap; the
            # action itself still checks everything): this seat's creatures and face-down cards, not itself, not the one it is on
            x["equip_targets"] = [c.id for c in vp.battlefield if (c.is_("Creature") or c.face_down) and c.id != p.id and p.attached_to != c.id]
        if x.get("has_station") and not p.face_down:
            top = _pl.station_top(p.card)
            why = None if main else "Station is sorcery speed: only in your own main phase"
            why = why or (None if top is None or p.charge < top else "it already has every charge counter it uses")
            crew_ = [c.id for c in vp.battlefield if c.is_("Creature") and not c.tapped and c.id != p.id]
            why = why or (None if crew_ else "no untapped creature to tap")
            x["station_can"], x["station_why"] = why is None, why
            x["station_crew"] = crew_                      # M8: who may crew it, by the engine's own rule (a creature token, a Spacecraft that is a creature now)
    def card_flags(c_, card, castable_names, from_top=False):
        if card is None:
            return
        fast = "Instant" in (card.get("types") or []) or "Flash" in _pl.printed_keywords(card)
        why = None if started else "the game hasn't started"
        if why is None and "Land" in (card.get("types") or []):
            why = None if main else "a land is played in your own main phase"
            why = why or ("you already played a land this turn" if vp.land_played else None)
            c_["can_play"], c_["why_not"] = why is None, why
            return
        if why is None and not (main or fast) or (why is None and from_top and not mine):
            why = "sorcery speed: only in your own main phase" if not fast else "only during your own turn"
        if why is None and card["name"] not in castable_names:
            why = f"needs {card.get('manaCost') or 'its cost'}: {vp.mana_words()}"
        if why is None:
            spec = _pl.modal_options(card) if ("Instant" in card["types"] or "Sorcery" in card["types"]) else None
            if spec:
                if all(no_target(_pl.card_slots(card, m_)) for m_ in spec[1]):
                    why = "no legal target right now"
            elif "Aura" not in (card.get("subtypes") or []) and no_target(_pl.card_slots(card)) and ("Instant" in card["types"] or "Sorcery" in card["types"]):
                why = "no legal target right now"
        c_["can_cast"], c_["why_not"] = why is None, why
        if "castable_now" in c_:                           # the page's Cast buttons read this one: now it means "can be cast right now", timing and targets included
            c_["castable_now"] = bool(c_["castable_now"] and why is None)
        if c_.get("cycle_cost"):
            cw = None if started else "the game hasn't started"
            cw = cw or (None if vp.plan_payment(c_["cycle_cost"]) is not None else f"needs {c_['cycle_cost']}: {vp.mana_words()}")
            c_["cycle_can"], c_["cycle_why"] = cw is None, cw
    castable = {o[1]["name"] for o in vp.castable()}
    for c_, card in zip(v.get("hand") or [], vp.hand):
        card_flags(c_, card, castable)
    xcast = {o[1]["name"] for o in vp.exile_castable()}
    for c_, e_ in zip(v.get("exile_playable") or [], vp.impulse_zone()):
        card_flags(c_, e_["card"], xcast)
    if v.get("library_top") and vp.library:
        tc_ = vp.top_castable()
        card_flags(v["library_top"], vp.library[-1], {vp.library[-1]["name"]} if tc_ else set(), from_top=True)
    for za in v.get("zone_abilities") or []:
        why = None if started else "the game hasn't started"
        why = why or ("sorcery speed: only in your own main phase" if za["sorcery"] and not main else None)
        why = why or (None if za["payable"] else f"needs {za['cost']}: {vp.mana_words()}")
        why = why or (None if za["runs"] else "the table can't run this ability yet")
        why = why or ("no legal target right now" if no_target(za.get("slots")) else None)
        za["can"], za["why_not"] = why is None, why
        for sl in za.get("slots") or []:
            sl["options"] = aim_candidates(sn, sl)


def mark_driven_seats():
    """A pilot seat is driven through its key, by a person or a bot, and can answer: its triggers ask. An engine-run seat can't, and picks for itself."""
    for n in PILOTS:
        if n in VPS:
            VPS[n].__dict__["asks"] = True


def _is_fight(item: dict) -> bool:
    import player as _pl
    sh = _pl.effect_shape(item.get("text") or "") if item.get("kind") == "effect" else None
    return bool(sh and sh[0] == "fight")


def _pick_still_legal(seat_name: str, slot: dict, pick: dict) -> bool:
    """An answer already given for one slot of a two-target question: may it still be aimed at (it can have left play since)?"""
    from player import IllegalAction
    if not pick:
        return True
    try:
        resolve_aim(seat_name, [{**slot, "optional": False}], [pick], True)
        return True
    except (IllegalAction, KeyError, ValueError, TypeError):
        return False


def recheck_picks(seat_name: str, item: dict) -> str | None:
    """A question asked one target at a time can lose an EARLIER answer before the last is in (Amok Time: the fighter is destroyed while the
    table waits for what it fights). Per the rules a fight with a creature gone does nothing (701.12b): the question ends, and says so. Any
    other: the gone answer is asked again. Returns the line to say, or None when every earlier answer still stands. Nothing changes otherwise."""
    v = VPS[seat_name]
    slots = item.get("slots") or [item["slot"]]
    picked = item.get("picked") or []
    gone = [i for i, pk in enumerate(picked) if i < len(slots) and not _pick_still_legal(seat_name, slots[i], pk)]
    if not gone:
        return None
    if _is_fight(item):
        v.__dict__["pending_choices"] = [x for x in v.__dict__.get("pending_choices", []) if x["id"] != item["id"]]
        return f"{item['source']}: the creature that was to fight has left the battlefield: no fight."
    k = gone[0]
    item["picked"] = list(picked[:k])
    item["slot"] = slots[k]
    return f"{item['source']}: what was named for \"{slots[k].get('label', 'a target')}\" has gone: choose it again."


def answer_choice(seat_name: str, item: dict, target: dict) -> list:
    """One answer to a parked trigger. A sentence with two targets (a fight) is asked one at a time; it runs when the last is in.
    Every check comes BEFORE anything is recorded: a refused answer leaves the question exactly as it was (it once kept growing `picked` by
    one with every refused retry, and the question could then never be answered)."""
    from player import IllegalAction
    v = VPS[seat_name]
    line = recheck_picks(seat_name, item)        # an earlier answer has gone since: that is settled first (the answer sent was for a question that changed)
    if line is not None:
        return [line]
    if not target and not item["slot"].get("optional"):
        raise IllegalAction(f"{item['source']} needs a target: " + ", ".join(c["label"] for c in aim_candidates(seat_name, item["slot"])))
    slots = item.get("slots") or [item["slot"]]
    picked = [*(item.get("picked") or []), target]
    if target:
        resolve_aim(seat_name, [{**item["slot"], "optional": False}], [target], True)    # refused here if it's not legal, and the choice still waits
        if len(picked) == len(slots):            # the whole set, as the engine will carry it out (e.g. never the same creature twice)
            resolve_aim(seat_name, [{**s_, "optional": True} for s_ in slots], picked, True)
    item["picked"] = picked                      # only now: every check passed
    if len(picked) < len(slots):
        item["slot"] = slots[len(picked)]
        if item["slot"].get("zone") == "option":         # "choose two": what is already picked is not offered again
            item["slot"] = {**item["slot"], "taken": [p_.get("ref") for p_ in picked if p_]}
        return []
    if not any(picked):
        return v.resolve_choice(item, [])
    if item["kind"] == "effect" and len(slots) == 2 and not all(picked):      # the fight's opponent was declined
        v.__dict__["pending_choices"] = [x for x in v.__dict__.get("pending_choices", []) if x["id"] != item["id"]]
        return [f"{item['source']}: no fight."]
    aim_ = resolve_aim(seat_name, [{**s_, "optional": True} for s_ in slots], picked, True)
    ward_ = pay_ward(seat_name, aim_)            # M29 (a target whose ward can't be paid was already refused: aim_refusal)
    return ward_ + v.resolve_choice(item, aim_)


def settle_choices():
    """Parked triggers with nothing to decide. No legal target: it does nothing, and the table says so. Exactly one, and it isn't "up to": that's the target.
    A seat that has lost owes nothing: its questions go (a lost seat's question once held every other seat's turn for good)."""
    for n, v in list(VPS.items()):
        if is_out(n):
            for it in list(v.__dict__.get("pending_choices") or []):
                emit("say", speaker=n, text=f"{it['source']}: {n} has lost; nothing is asked of them.", action="trigger", auto=True)
            v.__dict__["pending_choices"] = []
            v.__dict__["pending_surveil"] = 0
            v.__dict__["pending_scry"] = 0
            continue
        for it in list(v.__dict__.get("pending_choices") or []):
            line = recheck_picks(n, it)          # an earlier answer has gone: a fight ends; anything else is asked again
            if line is not None:
                emit("say", speaker=n, text=line, action="trigger", auto=True)
                if it not in (v.__dict__.get("pending_choices") or []):
                    continue
            opts = aim_candidates(n, it["slot"])
            line = None
            if not opts and not it.get("picked"):
                v.__dict__["pending_choices"].remove(it)
                line = f"{it['source']}: no legal target."
            elif not opts:                             # the fight's second target has gone: no fight
                v.__dict__["pending_choices"].remove(it)
                line = f"{it['source']}: nothing to fight."
            elif len(opts) == 1 and not it["slot"].get("optional") and not it.get("reveal"):     # (a look at a hand is never settled for the seat: it must be seen)
                try:
                    said = answer_choice(n, it, {k: x for k, x in opts[0].items() if k != "label"})
                    line = " ".join(said)
                except Exception as e:                  # noqa: BLE001 — leave it for the seat to answer
                    print(f"settle_choices: {e}", file=sys.stderr, flush=True)
            if line and not it.get("picked") and it not in (v.__dict__.get("pending_choices") or []):
                more_ = v.resume_deferred()              # what waited behind the question it had no answer to, in order
                if more_:
                    line += " " + " ".join(more_)
            if line:
                emit("say", speaker=n, text=line, action="trigger", auto=True)


def settle_and_drain():
    """Each can feed the other: an opponent asked to discard (drain) may have an empty hand (settle), and a settled answer can do something to someone (drain).
    A question left with no possible answer would hold the turn for good: the seat's legal list is empty, and nobody can move."""
    for _ in range(4):
        settle_choices()
        drain_outboxes()
        if not any(v.__dict__.get("outbox") for v in VPS.values()):
            break
    settle_choices()


def drain_outboxes():
    """Everything the engine did to someone other than itself goes out here. VP_LOCK is the caller's."""
    for v in list(VPS.values()):
        for it in v.drain():
            k = it.get("kind")
            if k == "life":                            # already changed by the engine on its own seat: this is the event for it
                emit("life", player=it["player"], delta=it["delta"], by=it.get("by", "card"), life=v.life)
            elif k == "each-opponent":
                for o in opponents_of(v.name)[:1 if it.get("first_only") else None]:
                    hit_someone(v.name, o, it["n"], it.get("source") or v.name, it.get("verb", "deals"))
            elif k == "one-player":
                hit_someone(v.name, it["player"], it["n"], it.get("source") or v.name, it.get("verb", "deals"))
            elif k == "target-opponent":               # "Target opponent loses N life" (Vengeance Mission): the controller names one when there is a choice
                opps = opponents_of(v.name)
                if len(opps) > 1 and v.__dict__.get("asks"):
                    for x in v.ask_option(None, f"{it.get('source') or v.name}: target opponent loses {it['n']} life",
                                          [{"label": o, "do": {"kind": "call", "method": "opponent_hit", "args": [o, it["n"], it.get("source") or v.name, it.get("verb", "loses")]}} for o in opps],
                                          label="the opponent who loses life", then={"source": it.get("source") or v.name}):
                        emit("say", speaker=v.name, text=x, action="trigger", auto=True)
                elif opps:
                    hit_someone(v.name, opps[0], it["n"], it.get("source") or v.name, it.get("verb", "loses"))
            elif k == "each-player":                   # everyone, this seat too: each life total still has its one writer
                for o in turn_order():
                    if not is_out(o):
                        hit_someone(v.name, o, it["n"], it.get("source") or v.name, it.get("verb", "deals"))
            elif k == "each-player-draws":
                for o in turn_order():
                    if o in VPS and not is_out(o):
                        VPS[o].draw(1)
                    elif not is_out(o):
                        emit("attention", kind="effect", addressee=o, text=f"{v.name}'s card: each player draws a card. Draw yours.")
            elif k == "each-opponent-choice":          # theirs to choose: a driven seat is asked, an engine-run one picks, a person's real deck is told
                import player as _pl
                for o in opponents_of(v.name)[:1 if it.get("first_only") else None]:
                    if o in VPS:
                        said_, left_ = VPS[o].run_effects(None, it["sentence"])
                        for x in said_:
                            emit("say", speaker=o, text=f"{it.get('source') or v.name}: {x}", action="trigger", auto=True)
                    else:
                        emit("attention", kind="effect", addressee=o, text=f"{it.get('source') or v.name}: {it['sentence']}. That's yours to do.")
            elif k == "others-run":                    # the same sentence on every OTHER seat's board ("All other creatures get -1/-1")
                for o in turn_order():
                    if it.get("opponents_only") and o in teammates(v.name):
                        continue
                    if o != v.name and o in VPS and not is_out(o):
                        said_, _left = VPS[o].run_effects(None, it["sentence"])
                        for x in said_:
                            emit("say", speaker=o, text=f"{it.get('source') or v.name}: {x}", action="trigger", auto=True)
                    elif o != v.name and not is_out(o):
                        emit("attention", kind="effect", addressee=o, text=f"{it.get('source') or v.name}: {it['sentence']}. That's yours to do.")
            elif k == "player-run":                    # sentences one named player carries out ("Target player draws two cards, then discards two")
                o = it["player"]
                if o in VPS and not is_out(o):
                    for sent_ in it["sentences"]:
                        said_, _left = VPS[o].run_effects(None, sent_)
                        for x in said_:
                            emit("say", speaker=o, text=f"{it.get('source') or v.name}: {x}", action="trigger", auto=True)
                else:
                    emit("attention", kind="effect", addressee=o, text=f"{it.get('source') or v.name}: {'; '.join(it['sentences'])}. That's yours to do.")
            elif k == "cast":                          # a spell was cast: public, with its mana value (R4)
                emit("cast", player=v.name, name=it["name"], mv=it.get("mv", 0))
            elif k == "peek-hand":                     # "look at target opponent's hand": THIS seat alone is shown it
                reveal_hand(v, it)
            elif k == "take-from-hand":                # …and its answer: the card taken from that hand
                take_from_hand(v, it)
            elif k == "notice":
                emit("attention", kind="effect", addressee=it["seat"], text=it["text"])
            elif k == "say":
                emit("say", speaker=v.name, text=it["text"], action="effect", auto=True)


def reveal_hand(v, it):
    """`v` looks at an opponent's hand (Stray from History, Salt Vampire, Zero, V'Ger). The hand goes into v's OWN pending question and nowhere
    else: the public line says who looked, never what was there. VP_LOCK is the caller's."""
    src = it.get("source") or v.name
    tgt = it.get("target") or next(iter(opponents_of(v.name)), None)
    if not tgt or tgt == v.name or is_out(tgt) or tgt in teammates(v.name):
        emit("say", speaker=v.name, text=f"{src}: there is no opponent whose hand to look at.", action="effect", auto=True)
        return
    emit("say", speaker=v.name, text=f"{src}: {v.name} looks at {tgt}'s hand.", action="effect", auto=True)
    tv = VPS.get(tgt)
    if tv is None:                                     # a person's real deck: the cards are in their hand, not in this table
        emit("attention", kind="effect", addressee=v.name, text=f"{src}: {tgt} plays a real deck: ask them to show you their hand.")
        return
    for x in v.reveal_begin(tgt, list(tv.hand), it.get("mode") or "look", src):
        emit("say", speaker=v.name, text=f"{src}: {x}", action="effect", auto=True)


def take_from_hand(v, it):
    """`v` took `card` from `target`'s hand: it is exiled face up (so, by the rules, everyone may see it now) and the card says what else happens.
    The card is looked up again here: the hand may have changed since v was shown it. VP_LOCK is the caller's."""
    src, tgt, name, mode = it.get("source") or v.name, it.get("target"), it.get("card"), it.get("mode")
    tv = VPS.get(tgt)
    c = next((x for x in (tv.hand if tv else []) if x["name"] == name and "Land" not in (x.get("types") or [])), None)
    if c is None:
        emit("say", speaker=v.name, text=f"{src}: {name} is no longer in {tgt}'s hand: nothing is taken.", action="effect", auto=True)
        return
    tv.hand.remove(c)
    tv.exile_zone().append(c)
    emit("say", speaker=v.name, text=f"{src}: {v.name} chooses {name} from {tgt}'s hand: it is exiled.", action="effect", auto=True)
    if mode == "exile-draw":
        tv.draw(1)
        emit("say", speaker=tgt, text=f"{src}: I draw a card.", action="effect", auto=True)
    elif mode == "exile-play":
        tv.impulse_zone().append({"card": c, "until": "play", "until_turn": 10 ** 9, "by": src, "tax": 2})
        emit("say", speaker=tgt, text=f"{src}: I may play {name} from exile; it costs {{2}} more.", action="effect", auto=True)


def on_creature_died(owner, dead):
    """A creature died on `owner`'s board: every seat's "whenever a creature dies" hears it (Sarcophagus, Gowron). The public `died` event is the
    record of it (R3): the game summary reads this, never the words of the log. A face-down creature is named as the table says it."""
    emit("died", name=owner.shown(dead), owner=owner.name, token=bool(dead.token), cause=dead.__dict__.get("death_cause") or "dies")
    for v in list(VPS.values()):
        for line in v.creature_died(dead, owner):
            emit("say", speaker=v.name, text=line, action="trigger", auto=True)


__import__("player").DEATH_HOOK[0] = on_creature_died
__import__("player").STEP_KEY[0] = lambda: (PHASE.get("player"), PHASE.get("step"))   # mana in a pool empties when the step changes


def foe_creatures(seat: str) -> list:
    """(owner, permanent) for every face-up creature a seat that is not `seat` (nor its teammate, nor out of the game) controls: what an Aura
    meant for an opponent's creature may go on. One list for "may it be cast" (player.hostile_targets) and "what the legal list offers"."""
    mates = set(teammates(seat)) if TEAMS.get(seat) else set()
    return [(o, x) for o, ov in list(VPS.items()) if o != seat and o not in mates and not is_out(o) for x in list(ov.battlefield)
            if x.is_("Creature") and not x.face_down]


__import__("player").FOE_CREATURES[0] = lambda seat: [x.name for _, x in foe_creatures(seat)]
MANUAL_SAID: set = set()
NOTED: set = set()


def note_manual(v, card_name: str):
    """A card just landed with abilities the table can't carry out: say so, once per card per seat, and only those. coverage.py is the single
    list (the same one RETRO-CARD-COVERAGE.md is written from), read against the real engine."""
    try:
        import card_coverage as _cv
        c = next((x for x in [*v.graveyard[-3:], *(p.card for p in v.battlefield[-3:] if not p.face_down)] if x["name"].lower().startswith(str(card_name).lower())), None)
        if c is None or (v.name, c["name"]) in MANUAL_SAID:
            return
        MANUAL_SAID.add((v.name, c["name"]))
        for line in _cv.by_hand_lines(c):
            emit("table-note", seat=v.name, card=c["name"], text=f"By hand: {c['name']}: {line}")
    except Exception as e:                              # noqa: BLE001 — a note must never stop a cast
        print(f"coverage note failed: {e}", file=sys.stderr, flush=True)


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
        which = F.card_question(text, cards, last, VP.commander["name"] if VP.commander else "")
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
        said_ = VP.move(f"#{p.id}", to, destroy=to == "graveyard")
        if p in VP.battlefield:                         # Dr. McCoy's replacement: not destroyed after all
            return said_
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
        said = voice.persona_react({**ai, **{k: v for k, v in IDENTITY.get(ai["name"], {}).items() if k in ("presents_as", "pronouns")}},
                                   shown, shown_by, recent, board)
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
        self._last_code = code                             # what this request answered (the pilot route asks: did the action happen?)
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
            self._send(200, {"ok": True, "rev": _ev_id[0], "phase": phase_public()})      # PHASE itself holds sets now (not JSON): the reply died and the Worker saw a failed restore
            return True
        self._send(404, {"error": "not found"})
        return True

    def do_GET(self):
        if self.headers.get("Content-Length", "0") not in ("", "0") or self.headers.get("Transfer-Encoding"):
            self.close_connection = True                   # a GET with a body: we never read it, so don't reuse
        if self._room_api("GET"):
            return
        if self._origin_refused("GET"):
            return
        if self._lan_blocked("GET"):
            return
        if self.path.split("?")[0] == "/api/version":      # which commit this room runs (scripts/cloud_smoke.cjs)
            from build_info import build_sha
            return self._send(200, {"sha": build_sha(), "game": "magic"})
        if self.path.split("?")[0] == "/lobby":            # T5: where people wait between rounds (public info only)
            return self._send(200, body=(HERE / "lobby.html").read_bytes(), ctype="text/html; charset=utf-8")
        if self.path.split("?")[0] == "/api/lobby":
            if "self=1" in self.path:                  # a sibling asking: this table only (no fan-out, so two tables never ask each other forever)
                with VP_LOCK:
                    lt = life_table()
                    return self._send(200, {"table": {"game_id": FAIR.get("game"), "turn": PHASE.get("player"), "over": bool(GAME_OVER.get("over")),
                                                      "winner": GAME_OVER.get("winner"), "seats": [{"name": n, "kind": "person" if n in persons() else "bot",
                                                      "life": lt.get(n)} for n in turn_order() or list(lt)]}})
            return self._send(200, lobby_public())
        if self.path.split("?")[0] == "/me":               # a player's phone / glasses view (public info only)
            return self._send(200, body=(HERE / "me.html").read_bytes(), ctype="text/html; charset=utf-8")
        if self.path.split("?")[0] == "/api/cardnames":    # name completion for the check-in's commander box (public, offline)
            import oracle
            import deckdecl as D
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query).get("q", [""])[0][:80]
            return self._send(200, {"q": q, "names": D.suggest(q, oracle.db(), n=8) if len(q.strip()) >= 2 else []})
        if self.path.split("?")[0] == "/api/deckdecl":     # a seat's declared commander and deck list (public, as in Commander)
            from urllib.parse import parse_qs, urlparse
            want = parse_qs(urlparse(self.path).query).get("seat", [""])[0].strip().lower()
            if not want:
                return self._send(200, {"seats": [deckdecl_public(n) for n in turn_order()]})
            n = next((x for x in turn_order() if x.lower() == want), None)
            if not n:
                return self._send(404, {"error": f"no seat named '{want}'"})
            return self._send(200, deckdecl_public(n))
        if self.path.startswith("/api/card"):              # Oracle text for one card, by name (public)
            import oracle
            from urllib.parse import parse_qs, urlparse
            name = parse_qs(urlparse(self.path).query).get("name", [""])[0]
            c = (oracle.card(name) or set_card(name)) if name else None      # (the Oracle file, then the set's own card file: both static, neither knows the game)
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
        if self.path.split("?")[0] == "/3d":               # the board in 3D with physics (live, or ?watch=1 for the delayed feed)
            return self._send(200, body=(HERE / "table3d.html").read_bytes(), ctype="text/html; charset=utf-8")
        if self.path.split("?")[0] == "/watch":            # the board for spectators: a turn behind (/api/spectate), no actions
            # The helper scripts (life bar, history, todos, questions…) read the LIVE game; on /watch they would undo the delay.
            # (kept: art.js, a static art lookup, and mana.js, which only redraws "{2}{R}" as pictures in text already on the page: neither fetches game data)
            page = re.sub(r'<script src="/assets/(?!art\.js|mana\.js)[^"]+"[^>]*></script>\n?', "", (HERE / "board.html").read_text())
            return self._send(200, body=page.encode(), ctype="text/html; charset=utf-8")
        if self.path.split("?")[0] == "/board":            # the whole board in plain HTML: any device, no 3D
            return self._send(200, body=(HERE / "board.html").read_bytes(), ctype="text/html; charset=utf-8")
        if self.path.split("?")[0] == "/hand":             # a pilot seat's hand and buttons (its data needs the seat key)
            return self._send(200, body=(HERE / "hand.html").read_bytes(), ctype="text/html; charset=utf-8")
        if self.path.split("?")[0] == "/stage":            # the 3D avatar stage: public info only
            return self._send(200, body=(HERE / "stage.html").read_bytes(), ctype="text/html; charset=utf-8")
        p0 = self.path.split("?")[0]
        art_root = Path(os.environ["TABLE_ART_DIR"]).expanduser() if os.environ.get("TABLE_ART_DIR") else None   # card art: outside the repo (S4)
        if art_root and p0.startswith("/art/"):
            fp = (art_root / p0[len("/art/"):]).resolve()
            if fp.is_file() and art_root.resolve() in fp.parents and fp.suffix in (".jpg", ".js"):
                return self._send(200, body=fp.read_bytes(), ctype="image/jpeg" if fp.suffix == ".jpg" else "text/javascript; charset=utf-8")
            return self._send(404, {"error": "no such card art"})
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
        if self.path == "/api/game/summary":           # public: the winner, the final totals, and the summary of the public log
            return self._send(200, game_over_public())
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
                seats += [{"name": n, "commander": (v.commander or {}).get("name"), "kind": "ai", "life": v.life,
                           "hand": len(v.hand)} for n, v in VPS.items()]
            return self._send(200, {"seats": seats})
        if self.path == "/show":
            return self._send(200, body=(HERE / "show.html").read_bytes(), ctype="text/html; charset=utf-8")
        if self.path == "/voice":
            return self._send(200, body=(HERE / "voice.html").read_bytes(), ctype="text/html; charset=utf-8")
        if self.path == "/api/voice-config":            # an AI's declared voice and presentation (/api/identity) win
            keep = ("voice", "voice_rate", "voice_pitch", "pronouns", "presents_as")
            ais = [{**p, **{k: IDENTITY[p["name"]][k] for k in keep if k in IDENTITY.get(p["name"], {})},
                    "agent": bool(IDENTITY.get(p["name"], {}).get("model"))} for p in AI_PLAYERS]   # a model plays it (vs a person piloting)
            return self._send(200, {"ai_players": ais, "humans": HUMANS, "brain": args.brain})
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
            try:
                start = max(0, int((q.get("from") or ["0"])[0] or 0))
            except ValueError:
                return self._send(400, {"error": "from must be a number"})
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
            if q.get("since", ["0"])[0] != "latest":
                try:
                    int(q.get("since", ["0"])[0] or 0)
                except ValueError:
                    return self._send(400, {"error": "since must be a number, or 'latest'"})
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
            return self._send(200, {"last": last, "events": out, "restarted": restarted, "game_id": FAIR.get("game")})
        if self.path.startswith("/api/spectate"):        # /watch: a turn behind, public information only
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            snap = SPECTATE[-1] if SPECTATE else None
            if snap is None:
                return self._send(200, {"board": None, "events": [], "note": "the game hasn't finished a turn yet"})
            try:
                since = int((q.get("since") or ["0"])[0] or 0)
            except ValueError:
                since = 0
            with EV_LOCK:
                out = [e for e in EVENTS if since < e["id"] <= snap["event_id"]][-300:]
            donate = ({"url": args.donate_url, "name": args.donate_name or "a charity"}
                      if args.donate_url.startswith("https://") else None)
            return self._send(200, {"board": snap["board"], "life": snap["life"], "as_of_event": snap["event_id"],
                                    "turn_of": snap["turn_of"], "events": out, "delay": "one turn",
                                    "teams": dict(TEAMS) or None, "donate": donate,
                                    **({"game_over": True, "winner": GAME_OVER.get("winner")} if GAME_OVER.get("over") else {})})
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
        if self.path.split("?")[0] == "/api/seat/legal":     # an outside agent's guide: what it may do now, ready to send
            sn = self._seat_q()
            if not (self._brain_ok() or self._pilot_ok(sn)):
                return self._send(403, {"error": "this seat's key required"})
            if not self._brain_ok():
                ok_, wait_ = seat_rate_ok(sn)
                if not ok_:
                    return self._send(429, {"error": f"slow down: {args.seat_rate:g} requests a second per seat", "retry_after": wait_})
            if sn is None:
                return self._send(404, {"error": f"no seat by that name (seats: {list(VPS)})"})
            with VP_LOCK:
                return self._send(200, legal_actions(sn))
        if self.path.split("?")[0] == "/api/brain/state":
            sn = self._seat_q()
            if not (self._brain_ok() or self._pilot_ok(sn)):
                return self._send(403, {"error": "brain token required"})
            if not self._brain_ok():
                ok_, wait_ = seat_rate_ok(sn)
                if not ok_:
                    return self._send(429, {"error": f"slow down: {args.seat_rate:g} requests a second per seat", "retry_after": wait_})
            from player import brain_view
            if sn is None:
                return self._send(404, {"error": f"no AI seat by that name (seats: {list(VPS)})"})
            with VP_LOCK:
                v = brain_view(VPS[sn])
            v["seat"] = sn
            v["seats"] = list(VPS)
            with VP_LOCK:
                mv_ = mull_private(sn)
                if mv_:
                    v["mulligan"] = mv_                    # PRIVATE to this seat: its own opening-hand step (its hand is in this same answer)
                v["incoming"] = combat_incoming(sn)        # who is attacking this seat, with the table's own power numbers
                v["opponents"] = {n: {**o.public(), "lands_list": [p.name for p in o.battlefield if p.is_("Land") and not p.face_down],
                                      "tapped_lands": sum(1 for p in o.battlefield if p.is_("Land") and not p.face_down and p.tapped),
                                      "creatures": [{"name": o.shown(p), "pt": "%d/%d" % o.stats(p), "tapped": p.tapped,
                                                     "sick": p.sick} for p in o.battlefield if p.is_("Creature") or p.face_down]}
                                  for n, o in VPS.items() if n != sn}
                for c_ in [*(v.get("hand") or []), *(v.get("exile_playable") or [])]:   # what each card can aim at right now: public cards only
                    for sl in c_.get("slots") or []:
                        sl["options"] = aim_candidates(sn, sl)
                    for ms in c_.get("mode_slots") or []:
                        for sl in ms:
                            sl["options"] = aim_candidates(sn, sl)
                if v.get("pending_surveil"):
                    v["surveil_cards"] = VPS[sn].peek(v["pending_surveil"])
                lt_ = v.get("library_top")
                if lt_:                                    # PRIVATE to this seat, as the hand is: castable on its own turn, at a spell's speed
                    for sl in lt_.get("slots") or []:
                        sl["options"] = aim_candidates(sn, sl)
                    fl_ = "Flash" in __import__("player").printed_keywords(VPS[sn].library[-1])
                    lt_["castable_now"] = bool(lt_["castable_now"] and on_turn(sn) and (fl_ or STEPS[PHASE["step"]] in ("main 1", "main 2")))
                if v.get("pending_scry"):
                    v["scry_cards"] = VPS[sn].peek(v["pending_scry"])
                for bc_ in v.get("beam_cards") or []:
                    for sl in bc_.get("slots") or []:
                        sl["options"] = aim_candidates(sn, sl)
                v["pending_choices"] = [{**x, "slot": {**x["slot"], "options": aim_candidates(sn, x["slot"])}} for x in v.get("pending_choices") or []]
                v["attacked_ids"] = sorted(ATTACKED.get("ids") or []) if ATTACKED.get("key") == (sn, VPS[sn].turn) else []
                try:
                    annotate_legality(sn, v)                # T18: can / why_not on every button of this seat's own state
                except Exception as e_:                     # noqa: BLE001 — a flag must never take the hand away
                    print(f"annotate_legality: {e_}", file=sys.stderr, flush=True)
                v["block_options"] = {}                    # which of my creatures may block each attacker: the engine's rule, not the page's
                for e_ in COMBAT["pending"]:
                    if e_["defender"] == sn or e_["defender"] in teammates(sn):
                        ap_ = attacker_perm(e_)
                        v["block_options"][e_.get("ref") or e_["name"]] = [
                            p_.id for p_ in VPS[sn].battlefield if p_.is_("Creature") and not p_.tapped and p_.id not in COMBAT["used"] and can_block(p_, ap_)]
                try:
                    import card_coverage as _cv
                    for x_, p_ in zip(v.get("permanents") or [], VPS[sn].battlefield):
                        x_["by_hand"] = [] if p_.face_down else _cv.by_hand_lines(p_.card)
                except Exception as e:                     # noqa: BLE001
                    print(f"coverage in state failed: {e}", file=sys.stderr, flush=True)
            v["public_boards"] = {k: x for k, x in PUBLIC_BOARD.items() if k != sn}
            with VP_LOCK:
                v.update(combat_view(sn))                  # v6c: incoming (every attacker, what may block it), block_options, combat
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
        if self._origin_refused("POST"):
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
                elif action in ("library-shuffle", "library-top", "library-bottom"):   # Chaos Warp: into the OWNER's library
                    perms.pop(i)                           # (no library count is kept for people: nothing to bump)
                    spot = {"library-shuffle": f"into {owner}'s library (shuffled in)",
                            "library-top": f"on top of {owner}'s library",
                            "library-bottom": f"on the bottom of {owner}'s library"}[action]
                    text = f"{owner} puts {nm} {spot}" + (" (a token: it's gone)." if p.get("token") else ".")
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
                remote = STRICT_SEATS and not self._is_local()   # another device (a phone, a tunnel): a seat key, and it is only talking (below)
                if CLOUD or remote:                        # a public room or a tunnel: only seated players upload, as themselves, and not forever
                    key_ = unquote(self.headers.get("X-Seat-Key", ""))[:200]
                    seats_ = [h["name"] for h in HUMANS] + sorted(PILOTS)
                    holder_ = next((n for n in seats_ if seat_key_ok(n, key_)), None) if key_ else None
                    if not holder_:
                        return self._send(403, {"error": "claim your seat first (👤) to share photos"})
                    by = next((n for n in seats_ if n.lower() == by.lower() and seat_key_ok(n, key_)), holder_)   # X-By cannot speak for another seat
                    if d.exists() and sum(1 for _ in d.iterdir()) >= 300:
                        return self._send(429, {"error": "this table has reached its photo limit (300)"})
                d.mkdir(exist_ok=True)
                os.chmod(d, 0o700)
                ext = ".jpg" if data[:3] == b"\xff\xd8\xff" else ".png"
                name = f"{time.strftime('%H%M%S')}-{secrets.token_hex(4)}{ext}"
                (d / name).write_bytes(data)
                os.chmod(d / name, 0o600)
                emit("chat", by=by, text=caption or "📷 a photo of the table", photo="/photos/" + name)
                if caption and not (remote and by in PILOTS):   # a pilot's caption from another device is table talk, never a move (see /api/chat)
                    try:
                        respond_text(caption, by if by in [h["name"] for h in HUMANS] else None, time.time())
                    except Exception as e:
                        print(f"photo caption handling failed: {e}", flush=True)
                return self._send(200, {"ok": True, "photo": "/photos/" + name})
            if self.path == "/api/chat":                   # typed table talk from a phone or the board: {"by": "Sam", "text": "..."}
                b = self._json()
                if b.get("game") and str(b.get("game")) != FAIR.get("game"):   # T3: a message meant for an earlier game never lands in this one
                    return self._send(409, {"error": f"that was for game {str(b.get('game'))[:40]}; this table is playing {FAIR.get('game')}", "game_id": FAIR.get("game")})
                by = str(b.get("by", ""))[:30]
                text = " ".join(str(b.get("text", "")).split())[:400]
                if not text:
                    return self._send(400, {"error": "say something"})
                who = by if by in [h["name"] for h in HUMANS] else None
                # Typed text is read as if said aloud at the table ("Fusion is at 0" changes a life total, "I cast ..." removes a card). For a plain
                # human seat that IS the game: their cards are physical, so their announcements move the table, as at a real one. A PILOT's moves
                # are the engine's, checked there; from another device under STRICT_SEATS a pilot's text is only talk. The seat guard proved WHO is
                # typing, not that they may change anyone else's numbers: own-seat-only on /api/life and the card actions must not be walked around.
                if b.get("talk") or text.startswith("~") or (STRICT_SEATS and not self._is_local() and by in PILOTS):   # just talking: everyone sees it, nothing reads it as a move
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
                if by in VPS:                              # a seat with a deck attacks with the engine (`attack`): it knows the power
                    return self._send(409, {"error": f"{by} plays a deck: attack with the Attack action, the table reads the creatures' power itself"})
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
                        if power is not None:             # the defender will block against THIS number, not one it types
                            with VP_LOCK:
                                COMBAT["pending"].append({"attacker": by, "defender": target, "ref": None, "name": attacker,
                                                          "power": power, "trample": trample})
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
                try:                                       # (a non-number here answered 500 with a traceback in the log)
                    ev_ = int(b.get("event", 0))
                except (TypeError, ValueError, OverflowError):
                    return self._send(400, {"error": "event is the number of the line you are rating"})
                rf_ = research_dir() / "openmic" / "ratings.jsonl"
                if rf_.exists() and rf_.stat().st_size > 500_000:      # ~6,000 ratings: far past any real game; a seat can't fill the disk with them
                    return self._send(429, {"error": "this game has all the ratings it can keep"})
                out = openmic.rate(str(research_dir() / "openmic"), ev_, str(b.get("rating", "")), who)
                return self._send(400 if "error" in out else 200, out)
            if self.path == "/api/host/rule":              # T4: a house rule, applied by the host at the laptop (never from another device)
                if not self._is_local():
                    return self._send(403, {"error": "house rules are the host's, at the table's own laptop"})
                b = self._json()
                sn_ = seat(b.get("seat"))
                if sn_ is None:
                    return self._send(404, {"error": f"no virtual seat '{b.get('seat')}' (seats: {list(VPS)}); a person's real cards are theirs to move"})
                kind_ = str(b.get("action", ""))
                from player import IllegalAction as _IA
                try:
                    with VP_LOCK:
                        v_ = VPS[sn_]
                        p_ = v_.perm(str(b.get("ref", "")))
                        nm_ = v_.shown(p_)
                        if kind_ == "damage":
                            n_ = max(1, min(int(b.get("n") or 1), 99))
                            out_ = v_.deal_damage(p_, n_, source="House rule")
                            what_ = f"{n_} damage to {sn_}'s {nm_}"
                        elif kind_ in ("destroy", "exile"):
                            out_ = v_.move(f"#{p_.id}", "graveyard" if kind_ == "destroy" else "exile")
                            what_ = f"{'destroy' if kind_ == 'destroy' else 'exile'} {sn_}'s {nm_}"
                        else:
                            return self._send(400, {"error": "action: damage (with n) | destroy | exile"})
                        settle_and_drain()
                except _IA as e:
                    return self._send(400, {"error": str(e)})
                why_ = " ".join(str(b.get("why", "")).split())[:120]
                emit("say", speaker="Table", text=f"House rule (by host): {what_}" + (f" — {why_}" if why_ else "") + ". " + " ".join(out_), action="house-rule", auto=True)
                snapshot()
                return self._send(200, {"ok": True, "said": out_})
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
                b = self._json()
                who = str(b.get("seat", ""))
                if not secrets.compare_digest(self.headers.get("X-Brain-Token", ""), TOKEN):
                    # a cloud room has no vision for the people: a PILOT (an AI seat with its own key) may read a person's
                    # photo or words and ADD what it saw to that person's board — marked as its record, said in the log
                    key = self._seat_key(b)
                    pilot = next((n for n in sorted(PILOTS) if key and seat_key_ok(n, key)), None)
                    if not pilot:
                        return self._send(403, {"error": "brain token or a pilot seat's key required"})
                    if who not in [h["name"] for h in HUMANS] or who in VPS or who in PILOTS or who == pilot:
                        return self._send(403, {"error": "a pilot records only a PERSON's board; AI boards come from the engine"})
                    return self._send(*pilot_records_board(pilot, who, b))
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
            if self.path in ("/api/phase/back", "/api/phase/back/confirm") and PHASE.get("player") in PILOTS:
                # a person's turn on a virtual deck: BACK is a real rewind. The laptop (or the host token) alone; a seated person asks and
                # another person at the table says yes (POST /api/phase/back/confirm with their own key).
                b = self._json()
                key_ = self._seat_key(b)
                who_ = next((n for n in persons() if seat_key_ok(n, key_)), None) or (
                    next((n for n in persons() if n.lower() == str(b.get("by", "")).lower()), None) if not STRICT_SEATS else None)
                host_ = self._is_local() or self._brain_ok()
                with PHASE_LOCK:
                    t_, why_ = rewind_target()
                    if t_ is None:
                        return self._send(409, {**phase_public(), "error": why_})
                    to_ = f"{t_['player']}, {STEPS[t_['step']]}"
                    if self.path.endswith("/confirm"):
                        req_ = dict(BACK_REQ)
                        if not req_:
                            return self._send(409, {**phase_public(), "error": "nobody has asked to go back"})
                        if not (host_ or (who_ and who_ != req_["by"])):
                            return self._send(403, {**phase_public(), "error": f"another person at the table confirms {req_['by']}'s BACK, not {who_ or 'an unseated device'}"})
                        code, out = rewind_to(t_, f"{req_['by']}, confirmed by {who_ or 'the host'}")
                        return self._send(code, out)
                    others_ = [n for n in persons() if n != who_ and not is_out(n)]
                    if host_ or (who_ and not others_):
                        code, out = rewind_to(t_, str(b.get("by") or who_ or "host")[:30])
                        return self._send(code, out)
                    if not who_:
                        return self._send(403, {**phase_public(), "error": "only a person seated at this table can ask to go back (with their seat key)"})
                    BACK_REQ.update(by=who_, at=time.time(), to=to_)
                emit("attention", kind="back-request", text=f"{who_} asks to go back to {to_}. {', '.join(others_)}: confirm on your page.", by=who_, to=to_)
                return self._send(202, {**phase_public(), "back_request": {"by": who_, "to": to_, "confirm_by": others_}})
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
                receipt = None
                if FEE_GATE is not None and name not in SEAT_KEYS:             # a NEW claim pays the table fee (test mode)
                    ok, why, receipt = FEE_GATE.verify(self.headers.get("X-PAYMENT"))
                    if not ok:
                        return self._send(402, FEE_GATE.requirements(why))
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
                emit("seat", name=name, kind="claimed", **({"paid": "test"} if receipt else {}))
                snapshot()
                return self._send(200, {"name": name, "key": key, **({"payment": receipt} if receipt else {})})
            if self.path == "/api/x402/test-payment":     # TEST MODE wallet stand-in: a one-use test proof, from this laptop only
                if FEE_GATE is None:
                    return self._send(404, {"error": "this table charges no fee"})
                if not self._brain_ok() and not self._is_local():   # (a tunnelled request is not local)
                    return self._send(403, {"error": "test payments come from the table's own laptop only"})
                b = self._json()
                return self._send(200, {"x_payment": FEE_GATE.test_payment(str(b.get("payer") or "test-agent")),
                                        "note": "TEST MODE: worth nothing, usable once, on this table only"})
            if self.path == "/api/ready":                  # {"by": "Sam", "key": …, "ready": true}: a seat checks in before the game starts
                b = self._json()
                nm = next((n for n in turn_order() if n.lower() == str(b.get("by", "")).lower()), None)
                if nm is None or nm in VPS and nm not in PILOTS:
                    return self._send(400, {"error": "no seat of yours by that name"})
                if not seat_key_ok(nm, str(b.get("key", ""))):
                    return self._send(403, {"error": "claim your seat first (👤)", "need_seat": True})
                if PHASE["player"] is not None:
                    return self._send(409, {"error": "the game has already started"})
                declared = None
                if str(b.get("commander") or "").strip() or b.get("decklist") is not None:   # {"commander", "decklist"}: declare with it
                    code, declared = declare_deck(nm, b)
                    if code != 200:
                        return self._send(code, declared)
                want = bool(b.get("ready", True))
                if want and checkin_required() and nm not in VPS and not seat_declaration(nm)["declared"]:
                    return self._send(409, {"error": f"{nm}: declare your commander before you press Ready", "need_commander": True,
                                            "checkin": checkin_public()})
                with PHASE_LOCK:
                    (READY.add if want else READY.discard)(nm)
                emit("checkin", seat=nm, ready=nm in READY, waiting=checkin_public()["waiting"])
                return self._send(200, {"checkin": checkin_public(), **({"declared": declared["declared"]} if declared else {})})
            if self.path == "/api/declare-deck":           # {"by": "Michael", "key": …, "commander": "Inspirit", "decklist": "1 Sol Ring\n…" | "https://…"}
                b = self._json()                           # the same seat rules as /api/ready: only your own seat, with its key
                nm = next((n for n in turn_order() if n.lower() == str(b.get("by", "")).lower()), None)
                if nm is None or nm in VPS and nm not in PILOTS:
                    return self._send(400, {"error": "no seat of yours by that name"})
                if not seat_key_ok(nm, str(b.get("key") or self.headers.get("X-Seat-Key", ""))):
                    return self._send(403, {"error": "claim your seat first (👤)", "need_seat": True})
                code, d = declare_deck(nm, b)
                return self._send(code, d)
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
                                            "path": f"/me?player={urllib.parse.quote(seat_)}#invite={inv}"})   # after "#": never sent to a server or a log
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
                finish_game(order, winner, by="host")
                return self._send(200, {"over": True, "order": order, "winner": winner, "totals": GAME_OVER.get("totals")})
            if self.path.startswith("/api/brain/"):
                b = self._json()
                sn = seat(b.get("seat"))
                pilot = not self._brain_ok()
                if pilot and not self._pilot_ok(sn):
                    return self._send(403, {"error": "brain token required"})
                if pilot:
                    ok_, wait_ = seat_rate_ok(sn)
                    if not ok_:
                        return self._send(429, {"error": f"slow down: {args.seat_rate:g} requests a second per seat", "retry_after": wait_})
                if sn is None:
                    return self._send(404, {"error": f"no AI seat '{b.get('seat')}' (seats: {list(VPS)})"})
                act = self.path.rsplit("/", 1)[-1]
                if GAME_OVER.get("over") and act != "say" and (pilot or act not in AFTER_GAME_OK):   # M24: nothing is played after the end
                    self._refused(act, "game-over", "")
                    lost_ = [n for n in life_table() if is_out(n)]     # (409 and `stale`, as for any listed move the game has since overtaken: read the list again)
                    return self._send(409, {**phase_public(), "game_over": True, "stale": True,
                                            "error": "the game is over" + (f" ({', '.join(lost_)} has lost)" if lost_ else "") + ": nothing more is played (talk still works)"})
                if act == "concede":                      # R15: the host (any seat) or the seat's own key (its own seat only: _pilot_ok checked it)
                    code_, out_ = do_concede(sn, by="host" if not pilot else "seat")
                    return self._send(code_, out_)
                if pilot and act in ("new-game", "take"):  # a person plays their seat: no reset, no reaching into a hand
                    return self._send(403, {"error": "only the table's host can do that" if act == "new-game"
                                            else "taking a card from another hand is done by the table's host"})
                if pilot and act == "life" and str(b.get("player", "me")).lower() not in ("me", sn.lower()):
                    return self._send(403, {"error": "you can only change your own life total"})
                if pilot and act in ("damage", "end") and PHASE.get("player") is not None and on_turn(sn) and unanswered_by_people(sn):
                    wait_ = unanswered_by_people(sn)       # T6: a person answers an attack (block, or take it) before its damage is dealt
                    self._refused(act, "blocks-not-in", "")
                    return self._send(409, {**phase_public(), "error": f"waiting for {', '.join(sorted({e['defender'] for e in wait_}))} to block or take it "
                                                                     f"({', '.join(e['name'] for e in wait_)}): a person always gets to answer"})
                if pilot and act == "damage":
                    b["no_life"] = True                     # the players hit say their own life; only my lifelink counts
                if pilot and act == "fair-reveal":           # it publishes every deck's seed and shuffled order
                    return self._send(403, {"error": "only the table's host can publish the fairness proof: it shows every deck's order"})
                b.pop("_pilot", None)
                if pilot:
                    b["_pilot"] = True
                if pilot and act in PRESTART_ACTIONS and PHASE["player"] is None:
                    self._refused(act, "game-not-started", "")
                    return self._send(409, {**phase_public(), "error": START_HINT})
                if pilot and act not in PILOT_ACTIONS:
                    self._refused(act, "pilot-not-allowed", "")
                    return self._send(403, {"error": f"'{act}' isn't available to a pilot seat: it has no card behind it "
                                                     f"(a pilot plays: {', '.join(sorted(PILOT_ACTIONS))}); the table's host can do it"})
                if pilot and act in ("land", "attack", "end", "next") and PHASE["player"] is not None and not on_turn(sn):
                    self._refused(act, "not-your-turn", "")
                    return self._send(409, {**phase_public(), "error": f"it's {PHASE['player']}'s turn: {act} is only for your own turn"})
                if pilot and act == "untap":
                    with PHASE_LOCK:
                        try:
                            pid = VPS[sn].perm(str(b.get("ref", ""))).id
                        except Exception:                   # noqa: BLE001 — an unknown ref is refused by the action itself
                            pid = None
                        why = untap_refusal(sn, pid)
                    if why:
                        self._refused(act, "untap-not-now", why)
                        return self._send(409, {**phase_public(), "error": why})
                if pilot and act == "cast":
                    b.pop("discount", None)                 # a client-named cost reduction is not checked by the engine: host only
                # An aimed trigger is waiting on somebody's answer: that seat answers first, and the turn doesn't move past it (as an aimed spell can't be
                # cast until it's aimed). Reading, talking, and passing nothing is unaffected.
                mark_driven_seats()
                if pilot and HELD.get("seat"):               # a spell is waiting on a response: only the answers to it go through
                    hg_ = held_gate(sn, act)
                    if hg_ is not None:
                        return self._send(*hg_)
                if pilot and sn in VPS and act not in ("choose", "say", "life", "tap", "untap", "surveil", "effect", "manual", "scry", "swap") and VPS[sn].__dict__.get("pending_choices"):
                    self._refused(act, "choice-pending", "")
                    first_ = VPS[sn].__dict__["pending_choices"][0]
                    return self._send(409, {**phase_public(), "error": f"choose first: {first_['source']}: {first_['text'][:100]}", "choose": True, "code": "choice-pending",
                                            "pending_choice": {"seat": sn, "id": first_["id"], "source": first_["source"], "label": str((first_.get("slot") or {}).get("label") or "")[:300]}})
                if any(is_out(n) and v_.__dict__.get("pending_choices") for n, v_ in VPS.items()):
                    with VP_LOCK:
                        settle_choices()                # a seat that has lost owes nothing: its questions go before anyone waits on them
                other_ = [n for n, v_ in VPS.items() if n != sn and v_.__dict__.get("pending_choices") and not is_out(n)]
                if pilot and other_ and act in ("next", "end", "attack", "damage", "begin"):
                    return self._send(409, {**phase_public(), "waiting": ", ".join(other_), "code": "choice-pending-other",
                                            "pending_choice": {"seat": other_[0], "source": VPS[other_[0]].__dict__["pending_choices"][0]["source"]},
                                            "error": f"waiting on {', '.join(other_)} to choose a target ({VPS[other_[0]].__dict__['pending_choices'][0]['source']})"})
                if act == "next" and PHASE["player"] is not None and on_turn(sn):
                    with PHASE_LOCK:
                        stp = STEPS[PHASE["step"]]
                        if stp in ("end step", "cleanup"):
                            act = "end"                 # the last press of the bar is End turn
                        elif stp in ("untap", "upkeep", "draw") and not PHASE.get("begun") and args.pilot_auto_begin == "on":
                            return self._send(409, {**phase_public(), "error": "your turn is starting (untap, upkeep, draw): main 1 comes by itself"})
                        elif stp in ("declare attackers", "declare blockers"):
                            wait = [e for e in COMBAT["pending"] if e["attacker"] == sn and not e.get("answered") and (time.time() - e.get("at", 0) <= DEFENDER_GRACE or e["defender"] in PILOTS)]
                            if wait:
                                self._refused(act, "blocks-not-in", "")
                                return self._send(409, {**phase_public(), "error": f"waiting for the defenders to block ({', '.join(e['name'] for e in wait)}): at most {DEFENDER_GRACE:g} s"})
                    b["_from_step"] = stp
                if pilot and act in AI_MAIN_ACTIONS and on_turn(sn) and PHASE["player"] is not None:
                    # A main-phase action walks the turn on past combat. Pressed at the start of combat, before attacking, that would skip the
                    # attack without a word: refuse it and say what to do. (After the attack it's main 2, and goes through.)
                    with PHASE_LOCK:
                        stp = STEPS[PHASE["step"]]
                        v_ = VPS.get(sn)
                        attacked_ = ATTACKED.get("key") == (sn, v_.turn) and bool(ATTACKED.get("ids")) if v_ else False
                        speedy = act == "cast" and v_ is not None and not b.get("face_down") and any(
                            c.get("name", "").lower() == str(b.get("name", "")).lower() and ("Instant" in (c.get("types") or []) or "Flash" in __import__("player").printed_keywords(c)) for c in cast_pool(v_, b))
                    if stp in ("beginning of combat", "declare attackers") and not attacked_ and not speedy:
                        self._refused(act, "not-in-combat", "")
                        return self._send(409, {**phase_public(), "error": f"it's {stp}: that waits for main 2 (only instants now). Attack first, or press Next step"})
                _COMBAT_TL.said = []                        # v6c: what the table says while it moves combat on in this request
                COMBAT_LOOP["busy"] = time.time()
                if act == "end" and on_turn(sn) and turn_is_ending():
                    self._refused(act, "turn-ending", "")
                    return self._send(409, {**phase_public(), "error": "your turn is already ending: the next turn starts in a moment"})
                if PHASE["player"] is not None and not (act == "cast" and not pilot and b.get("any_time")):
                    with PHASE_LOCK:                        # v6c: a spell's speed (M18), then combat's own order (M1, M5, M17, M19)
                        why_ = cast_timing_refusal(sn, b) if act == "cast" else None
                        was_ = (combat_decl(sn) or {}).get("stage")
                        gate_ = None if why_ else combat_gate(sn, act, b)
                        dealt_ = act == "next" and gate_ is None and was_ == "window" and (combat_decl(sn) or {}).get("stage") == "done"
                    if why_:
                        self._refused(act, "sorcery-speed", why_)
                        return self._send(400, {"error": why_})
                    if gate_:
                        self._refused(act, "combat-order", gate_[1].get("error", ""))
                        return self._send(*gate_)
                    if dealt_:                              # Next at declare blockers was the attacker's pass: the damage step it led to is this press
                        return self._send(200, {"ok": True, "said": list(_COMBAT_TL.said), "private": {}, **phase_public()})
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
                    if act == "end" and TEAMS and on_turn(sn):
                        with PHASE_LOCK:            # 2HG: a teammate that ends first waits for its partner; the step stays put
                            lead = PHASE["player"]
                            fresh = sn not in TEAM_TURN["done"]
                            TEAM_TURN["done"].add(sn)
                            still = [n for n in [lead, *teammates(lead)] if n not in TEAM_TURN["done"] and not is_out(n)]
                        if still:
                            if fresh:                   # said once, however often a bot presses End while it waits
                                emit("say", speaker=sn, text=b.get("text") or "I'm done; over to my teammate.", action="end")
                            with PHASE_LOCK:            # done after combat: the shared turn goes on to main 2 for the partner
                                m2 = STEPS.index("main 2")   # (4-bot 2HG deadlock: the lead ended at combat damage, the partner
                                if STEPS.index("declare attackers") <= PHASE["step"] < m2:   # waited for a main phase)
                                    combat_flush(PHASE["player"])
                                    for t_ in teammates(PHASE["player"]):
                                        combat_flush(t_)
                                    PHASE["step"] = m2
                                    PRIORITY.update(waiting=[], seats=[])
                                    emit("phase", player=PHASE["player"], step=STEPS[m2], index=m2, by="team")
                            return self._send(200, {"said": [f"done for this turn; waiting on {', '.join(still)}"], "waiting_on": still})
                    with PHASE_LOCK:
                        # An instant cast at the start of combat is cast THERE: it must not walk the turn past the attack.
                        instant_now = act == "cast" and on_turn(sn) and sn in VPS and STEPS[PHASE["step"]] in ("beginning of combat", "declare attackers", "declare blockers", "combat damage") \
                            and any(c.get("name", "").lower() == str(b.get("name", "")).lower() and ("Instant" in (c.get("types") or []) or "Flash" in __import__("player").printed_keywords(c)) for c in cast_pool(VPS[sn], b))
                        # v6c: the host's own fix-up actions (a permanent made from nothing) happen where the turn IS while this seat's combat
                        # is open: walking it on to main 2 from here used to deal the attack unblocked, behind the defender's back
                        fixup_now = not pilot and act in ("put", "blink", "token", "manifest") and (combat_decl(sn) or {}).get("stage") not in (None, "done")
                        blocked = None if (instant_now or fixup_now) else ai_advance(sn, act)
                        if act == "next" and not blocked and not WINDOWS.get("ai_steps", True) and PHASE["player"] == sn:
                            # response windows off: nothing to wait for, but the step still moves (combat's unblocked damage goes as it leaves)
                            if STEPS[PHASE["step"]] == "combat damage":
                                combat_flush(sn)
                            if PHASE["step"] < len(STEPS) - 1:
                                PHASE["step"] += 1
                                emit("phase", player=sn, step=STEPS[PHASE["step"]], index=PHASE["step"], by="next")
                    if blocked:
                        return self._send(409, {**phase_public(), "waiting": blocked,
                                                "error": f"waiting on passes: {blocked} (retry)"})
                    if act not in ("pass", "say", "peek", "choose"):
                        ACT_STARTS.append(_ev_id[0])       # where this action's events begin: BACK goes to before the step it happened in
                    self._last_code = None
                    with acting(sn):
                        out = self._brain(act, b)
                    did = self._last_code is not None and 200 <= self._last_code < 300   # a REFUSED cast (a bad target, no mana) changed nothing: it must not
                    if did and act in ("cast", "turn-up", "manifest") and not on_turn(sn):   # restart the round or count the caster as passed
                        priority_reset(f"{sn} responded ({act})")   # an AI answered in a person's step: round again
                        if not any(d_.get("stage") == "window" for d_ in (COMBAT.get("decl") or {}).values()):
                            ai_passed(sn)                           # …and it has had its say in the new round (v6c: not after blocks: there it passes itself)
                    elif did and act == "pass" and not on_turn(sn):
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
                READY.clear()
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

    # S1 (review 2026-11-07): the laptop is trusted, so a web page open in its browser must not be able to act as the laptop.
    # Cross-site POSTs (CSRF) always carry a foreign (or null) Origin; DNS rebinding carries a foreign Host name.
    def _origin_refused(self, method: str) -> bool:
        if CLOUD:                                          # cloud rooms sit behind their Worker, which has its own token
            return False
        host = (self.headers.get("Host") or "").strip().lower()
        if not host_allowed(host):
            if method == "POST":
                self._drain(int(self.headers.get("Content-Length", 0) or 0))
            self._send(403, {"error": "this table answers only requests addressed to it"})
            return True
        if method == "POST":                               # browsers always send Origin on a cross-site POST; scripts and tools send none
            origin = (self.headers.get("Origin") or "").strip().lower()
            o_auth = origin.split("://", 1)[-1].split("/", 1)[0]
            # same origin only: this table's page sends Origin = the address it was served from (the Host header), port included, so another
            # page on 127.0.0.1:9999 (or a sibling tunnel name) is as foreign as evil.example
            foreign = origin == "null" or (origin and (not host_allowed(o_auth) or o_auth != host))
            if foreign or (self.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
                self._send(403, {"error": "requests from other web pages are refused"})
                return True
        return False

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
        if any(self.headers.get(h) for h in self.PROXY_HEADERS):
            return False
        host = (self.headers.get("Host") or "").strip().lower()      # the laptop is addressed as itself. A tunnel keeps its own Host name, so
        if host:                                                       # trust never rests on a proxy remembering to add a forwarding header
            name = host[:host.index("]") + 1] if host.startswith("[") and "]" in host else host.split(":")[0]
            if name not in ("localhost", "127.0.0.1", "[::1]") and not name.endswith(".localhost"):
                return False
        return True

    def _lan_blocked(self, method: str) -> bool:
        """A request from another device to anything outside LAN_OK: refuse it (draining a POST body
        first, so the phone gets the 403 rather than a broken pipe)."""
        if self._is_local():
            return False
        if (method, self.path.split("?")[0]) in LAN_OK:
            return False
        if method == "GET" and self.path.split("?")[0].startswith(LAN_PREFIXES):
            return False
        if PILOTS and self.path.startswith(("/api/brain/", "/api/seat/legal")) and self.headers.get("X-Seat-Key"):
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
        # R24: the public log can say that a seat was refused (never why: the engine's message can name a card in a hand), once a minute per
        # (seat, action, kind), so "never tried" and "tried and was told no" are different things in the retro
        key_ = (getattr(VP, "name", None), action, kind)
        now_ = time.time()
        if now_ - REFUSED_SEEN.get(key_, 0) > 60:
            REFUSED_SEEN[key_] = now_
            emit("refused", seat=getattr(VP, "name", None), action=action, kind=kind)

    def _audit_entered(self, before, ev0, action):
        """R18: every permanent that enters the acting seat's battlefield is named in the public log by the end of the request that put it there
        (an action that raised half-way, after the card had left the hand and landed, once left Dahar Master on the board with no "I cast" line and no
        enters trigger). A name the log does not carry is said now, plainly; nothing is changed."""
        if VP is None or VP.name not in before:
            return
        try:
            v_ = VPS[VP.name]
            fresh = [p for p in v_.battlefield if p.id not in before[VP.name] and not p.token and not p.face_down]
            if not fresh:
                return
            with EV_LOCK:
                said_ = " ".join(str(e.get("text") or "") for e in EVENTS if e.get("id", 0) > ev0 and e.get("type") in ("say", "table-note"))
            for p in fresh:
                if p.name not in said_:
                    emit("say", speaker=VP.name, text=f"{p.name} is on {VP.name}'s battlefield, but the table did not announce it ({action}). Check its enters trigger.",
                         action="table", auto=True)
        except Exception as e:                         # noqa: BLE001 — an audit must never stop an action
            print(f"enter audit failed: {e}", file=sys.stderr, flush=True)

    def _combat_damage(self, b) -> list:
        """A seat's attackers that hit a player: triggers + life. The `damage` action, and the Next step off combat damage."""
        from player import IllegalAction
        b.setdefault("hits", {})
        if b.get("_pilot"):                  # v6c: ONE damage step (combat_damage_step); it has usually been dealt already, when the last pass came in
            combat_damage_step(VP.name, claims=b.get("hits") or {})
            d_ = (COMBAT.get("decl") or {}).get(VP.name)
            if d_:
                d_["stage"] = "done"
            return []                        # (its lines are in the log, and in this request's `said`)
        DAMAGED["key"] = (VP.name, VP.turn)
        lki = {}
        hits = {ref: (v[0], v[1]) for ref, v in b["hits"].items()}
        said, deltas = VP.combat_damage(hits, gone=lki)
        if b.get("no_life"):                   # the players state their own life: triggers only
            said = [x for x in said if not re.match(r".+ deals \d+ to ", x)]
            deltas = {k: v for k, v in deltas.items() if k == VP.name}   # its lifelink still counts
        for who, d in deltas.items():
            r = self._change_life(who, d, "combat")
            if "error" in r:
                raise IllegalAction(r["error"])
        lt = life_table()
        said += [f"{who} is at {lt[who]}." for who in deltas if who != VP.name and who in lt]
        return said

    def _brain(self, action, b):
        """The external brain's actions. Engine-checked; each announcement is spoken as the AI."""
        from player import IllegalAction
        speak = not b.get("quiet")
        private = {}
        audit_before = {n: {p.id for p in v_.battlefield} for n, v_ in VPS.items()} if speak else {}
        audit_ev0 = _ev_id[0]
        try:
            with VP_LOCK:
                VP.recent_draws.clear()
                VP.todo.clear()
                if action == "pass":                       # no response in this priority window
                    said = []
                elif action == "say":
                    text = str(b.get("text", "")).strip()
                    if len(text) > SAY_MAX:                 # table talk is spoken aloud and kept in every page's log
                        return self._send(400, {"error": f"keep table talk under {SAY_MAX} characters (this was {len(text)})"})
                    hidden = VP.private_hand()
                    leak = voice_leak(text, hidden)
                    if leak and not b.get("force"):
                        self._refused(action, "hand-leak", "")       # never the card's name: that is the secret
                        return self._send(400, {"error": f"that names '{leak}', which is still in your hand "
                                                         f"(pass force to say it anyway)"})
                    said = [text]
                elif action == "begin":
                    first = GAME_FIRST.get("seat")
                    if TEAMS:   # 2HG (rule 810): the TEAM that plays first skips the draw of its first turn, both its players
                        on_the_play = VP.turn == 0 and first is not None and TEAMS.get(VP.name) == TEAMS.get(first) \
                            and len(set(TEAMS.values())) == 2
                    else:
                        on_the_play = VP.turn == 0 and first == VP.name and len(turn_order()) == 2
                    real_ = VPS[VP.name]
                    real_.__dict__["skip_each_upkeep"] = (VP.name, "upkeep") in STEP_FIRED   # a teammate's half already ran my "each upkeep"
                    try:
                        said, drew = VP.begin_turn(skip_draw=on_the_play)
                    finally:
                        real_.__dict__.pop("skip_each_upkeep", None)
                    STEP_FIRED.add((VP.name, "upkeep"))
                    private["drew"] = drew
                    for o_ in [x for n_, x in VPS.items() if n_ != VP.name]:        # "each upkeep" on the other seats: once per turn, not once per half
                        if (o_.name, "upkeep") in STEP_FIRED:
                            continue
                        STEP_FIRED.add((o_.name, "upkeep"))
                        for line_ in o_.step_triggers("each upkeep"):
                            emit("say", speaker=o_.name, text=line_, action="trigger", auto=True)
                elif action == "land":
                    life0 = VP.life
                    real_ = VPS[VP.name]                    # (VP is the per-request proxy: its __dict__ is not the player's)
                    outside = sum(i.get("delta", 0) for i in real_.__dict__.get("outbox") or [] if i.get("kind") == "life")
                    said = VP.manual_land(b["name"], bounce=b.get("bounce"), pay_life=bool(b.get("pay_life")), from_exile=bool(b.get("from_exile")))
                    # (life the land itself changed; a Landfall's own gain comes through the outbox with its own line)
                    own = VP.life - life0 - (sum(i.get("delta", 0) for i in real_.__dict__.get("outbox") or [] if i.get("kind") == "life") - outside)
                    if own:
                        emit("life", player=VP.name, delta=own, by="land", life=VP.life)
                        if own > 0:
                            real_.gained_life(own)
                elif action == "cast" and b.get("face_down"):
                    said = VP.cast_face_down(b["name"])
                elif action == "turn-up":
                    said = VP.turn_up(b["ref"], free=bool(b.get("free")))
                elif action == "manifest":
                    said = VP.manifest(int(b.get("n") or 1), cloak=bool(b.get("cloak")))
                elif action == "cast":
                    import player as _pl
                    aim, legacy = None, b.get("targets")
                    hc = None
                    top_ = b.get("from") == "library-top"
                    if top_ and (b.get("beam") or b.get("commander")):
                        raise IllegalAction("a cast from the top of your library is a plain cast: no beam or commander")
                    if top_ and not on_turn(VP.name):
                        raise IllegalAction("you can cast from the top of your library only during your own turn")
                    if top_:
                        VP.top_cast_card(str(b.get("name", "")))       # refused here, before anything is paid, if it can't be
                        if not ("Flash" in _pl.printed_keywords(VP.library[-1]) or STEPS[PHASE["step"]] in ("main 1", "main 2")):
                            raise IllegalAction("that waits for your main phase: only a spell with flash is cast now")
                    if b.get("beam") and not (on_turn(VP.name) and STEPS[PHASE["step"]] in ("main 1", "main 2")):
                        gy_ = _pl._match(str(b["name"]), VP.graveyard, lambda c: c["name"])
                        if not (gy_ and "Instant" in gy_["types"]):
                            raise IllegalAction("Beam me up casts it as it would be cast: a sorcery waits for your main phase")
                    if not b.get("commander"):
                        if b.get("beam") or top_:
                            hc = _pl._match(str(b["name"]), VP.graveyard if b.get("beam") else VP.library[-1:], lambda c: c["name"])
                        else:                               # the hand, or a card it may play from exile (as manual_cast picks it)
                            try:
                                hc = VP.hand_or_exile(str(b["name"]), bool(b.get("from_exile")))[0]
                            except IllegalAction:
                                hc = None
                        if hc is not None and "Land" not in hc["types"] and "Aura" not in (hc.get("subtypes") or []):
                            spec_ = _pl.modal_options(hc)
                            modes_ = b.get("modes")
                            is_spell = "Instant" in hc["types"] or "Sorcery" in hc["types"]
                            if spec_ and is_spell and modes_:
                                try:
                                    mode_texts = [spec_[1][int(m_) - 1] for m_ in modes_]
                                except (ValueError, IndexError, TypeError):
                                    raise IllegalAction(f"{hc['name']} has modes 1..{len(spec_[1])}")
                                slots_ = [s_ for t_ in mode_texts for s_ in _pl.card_slots(hc, t_)]
                            elif spec_ and is_spell:
                                slots_ = []                 # the engine's own refusal says choose a mode
                            elif b.get("kicked") and _pl.kicker_of(hc):
                                slots_ = _pl.card_slots({**hc, "text": _pl.kicked_text(hc.get("text") or "", True)})      # "instead counter target spell": no mana-value limit
                            else:
                                slots_ = _pl.card_slots(hc)
                            if isinstance(legacy, list):
                                aim = resolve_aim(VP.name, slots_, legacy, bool(b.get("_pilot")))
                                legacy = None
                            elif slots_ and b.get("_pilot") and any(not s_.get("optional") for s_ in slots_):
                                aim = resolve_aim(VP.name, slots_, [], True)      # refuses, naming what it could aim at
                    hc_ = VPS[VP.name].commander if b.get("commander") else hc
                    holders_ = counter_holders(VP.name, hc_) if (b.get("_pilot") and hc_ is not None and not HELD.get("seat")) else []
                    said = VP.manual_cast(b["name"], on=b.get("on"), role_on=b.get("role_on"), modes=b.get("modes"),
                                          targets=legacy, commander=bool(b.get("commander")),
                                          x=int(b.get("x") or 0), discount=int(b.get("discount") or 0),
                                          color=b.get("color"), aim=aim, beam=b.get("beam"), from_exile=bool(b.get("from_exile")), **({"top": True} if top_ else {}),
                                          pay_with=[str(x) for x in b.get("pay_with")] if isinstance(b.get("pay_with"), list) and b.get("pay_with") else None,
                                          hold=bool(holders_), surcharge=ward_due(VP.name, aim),
                                          kicked=bool(b.get("kicked")), kick_sac=b.get("kick_sac"))
                    if ward_due(VP.name, aim):
                        said = [f"Ward: I pay {{{ward_due(VP.name, aim)}}} more. " + (said[0] if isinstance(said, list) else said), *(said[1:] if isinstance(said, list) else [])]
                    held_ = VPS[VP.name].__dict__.get("held_spell") if holders_ else None
                    if held_ is None:
                        note_manual(VP, b["name"])
                    cast_card_ = held_["card"] if held_ else next((x for x in [*VP.graveyard[-2:], *(p_.card for p_ in VP.battlefield[-3:] if not p_.face_down)] if x["name"].lower().startswith(str(b["name"]).lower())), None)
                    if cast_card_:
                        for o_ in list(VPS.values()):                                  # "whenever a player casts"
                            for line_ in o_.someone_cast(cast_card_):
                                emit("say", speaker=o_.name, text=line_, action="trigger", auto=True)
                    import oracle
                    if held_ is not None:                    # it waits for a response: what follows its resolution is owed when it resolves
                        eff_ = oracle.effect(str(b["name"]))
                        held_open(VP.name, held_["card"], holders_, {"note": True, "wipe": eff_ if eff_ in ("wipe", "exile-all", "bounce-all") else None})
                        private["held"] = held_public()
                    elif oracle.effect(str(b["name"])) in ("wipe", "exile-all", "bounce-all"):
                        # A wipe cast by one seat hits EVERY seat's board, its own too. The AI seats are virtual, so each is told
                        # (as a human's spoken wipe tells them) and applies it to its own permanents: nothing else removes them.
                        for n in list(VPS):
                            emit("attention", kind="removal", text=f"{VP.name} casts {b['name']}.", addressee=n, spell=str(b["name"]),
                                 effect=oracle.effect(str(b["name"])), target="all")
                elif action == "block" and b.get("_pilot"):   # v6c: a seat DECLARES its blocks; the damage is the damage step's (combat_tick)
                    said = combat_block(VP.name, b)
                elif action == "block":                  # someone attacked the AI; the brain blocks (or not)
                    from ai_turn import resolve_block
                    blk = VP.perm(b["blocker"]) if b.get("blocker") else None
                    many = [VP.perm(str(x)) for x in (b.get("blockers") or [])]    # menace: two or more together
                    if many and blk is None:
                        blk = many[0]
                    entry = None
                    if b.get("_pilot"):                  # a pilot says only WHICH creature blocks WHICH attacker: the number is ours
                        mine = [e for e in COMBAT["pending"] if e["defender"] == VP.name or e["defender"] in teammates(VP.name)]
                        if not mine:
                            raise IllegalAction("nothing is attacking you right now (or it has already been dealt with)")
                        want = str(b.get("attacker") or "").strip().lower()
                        entry = next((e for e in mine if want and want in (str(e.get("ref") or "").lower(), e["name"].lower())), None)
                        if entry is None and want:                    # "Kaust" for "Kaust, Eyes of the Glade": one attacker whose name starts with it
                            part = [e for e in mine if e["name"].lower().startswith(want) or e["name"].lower().split(",")[0] == want]
                            entry = part[0] if len(part) == 1 else None
                        if entry is None and want:
                            raise IllegalAction("no attacker '" + str(b.get("attacker")) + "' is attacking you: "
                                                + ", ".join(f"{e['name']} {e.get('ref') or ''} ({e['power']})" for e in mine))
                        entry = entry or mine[0]
                        ap_ = attacker_perm(entry)
                        if blk is not None:
                            if len(set(x.id for x in many)) != len(many):
                                raise IllegalAction("name each blocker once")
                            for one in (many or [blk]):
                                if not one.is_("Creature") or one.tapped:
                                    raise IllegalAction(f"{one.name} can't block: it must be an untapped creature")
                                why = block_refusal(one, ap_)
                                if why:
                                    raise IllegalAction(f"{one.name} can't block {entry['name']}: {why}")
                                if one.id in COMBAT["used"]:
                                    raise IllegalAction(f"{one.name} already blocked this combat")
                            if entry.get("menace") and len(many or [blk]) < 2:
                                raise IllegalAction(f"{entry['name']} has menace: it needs two or more blockers (blockers: [\"#a\", \"#b\"])")
                            for one in (many or [blk]):
                                COMBAT["used"].add(one.id)
                        # one strike's power: resolve_block deals double strike as its two steps itself (hit_power doubles it only for an UNblocked attacker)
                        b["amount"], b["trample"], b["attacker"] = (cur_power(entry) if ap_ is not None else hit_power(entry)), entry["trample"], entry["name"]
                        COMBAT["pending"].remove(entry)
                    life0 = VP.life
                    ap_ = attacker_perm(entry) if entry is not None else None
                    res = resolve_block(VP, blk, int(b["amount"]), bool(b.get("trample")), b.get("attacker"), atk=ap_,
                                        atk_owner=VPS.get(entry["attacker"]) if entry is not None else None, more=many[1:])
                    said = res["said"]
                    if VP.life != life0:
                        emit("life", player=VP.name, delta=VP.life - life0, by="combat", life=VP.life)
                    if res.get("attacker_lifelink") and entry is not None and entry["attacker"] in VPS:
                        ao = VPS[entry["attacker"]]         # the attacker's own lifelink, on what it dealt to its BLOCKERS (what tramples over is the
                        ao.life += res["attacker_lifelink"]  # hit below, whose lifelink `damage` gives: once, not twice)
                        ao.gained_life(res["attacker_lifelink"])
                        emit("life", player=ao.name, delta=res["attacker_lifelink"], by="lifelink", life=ao.life)
                        emit("say", speaker=ao.name, text=f"Lifelink: I gain {res['attacker_lifelink']}.", action="damage", auto=True)
                    through_ = int(res.get("through") or 0)   # what reached the player: never "life lost", which a blocker's lifelink would shrink
                    if entry is not None:
                        combat_dealt(entry["attacker"], VP.name, through_)
                    if entry is not None and entry.get("ref") and through_ > 0:
                        hit_ = {"attacker": entry["attacker"], "ref": entry["ref"], "defender": VP.name, "amount": through_}
                        if res.get("attacker_died") and ap_ is not None and entry["attacker"] in VPS:
                            av_ = VPS[entry["attacker"]]    # it dies in the same damage step it hit in: its "deals combat damage to a player" still
                            hit_["lki"] = {"perm": ap_, "attached": [x for x in av_.battlefield if x.attached_to == ap_.id],   # happens, as it was
                                           "kws": av_.kws(ap_, my_turn=True)}
                        COMBAT["hits"].append(hit_)
                    forget_dead(res)
                    if entry is not None:                            # blocks and damage are one event here: the step says so
                        combat_step_after_block(entry["attacker"])
                    if entry is not None and entry.get("ref") and res.get("attacker_died") and entry["attacker"] in VPS:
                        with VP_LOCK:                                 # the attacker that died to the block leaves ITS owner's board
                            try:
                                VPS[entry["attacker"]].move(entry["ref"], "graveyard", cause="combat")
                            except Exception:
                                pass
                elif action == "attack":
                    out_now = life_table()                      # a seat at 0 or less has lost: nothing may attack it (a combat step deals all its damage at once, but the NEXT one must not)
                    if TEAMS:                                   # 2HG: you attack the other TEAM; a team's name means its first seat still in
                        tnames = {t.lower(): t for t in TEAMS.values()}
                        for ref, who in list(dict(b["assign"]).items()):
                            if str(who).lower() in tnames:
                                t = tnames[str(who).lower()]
                                b["assign"][ref] = next((n for n, tt in TEAMS.items() if tt == t and not is_out(n)), who)
                        for who in dict(b["assign"]).values():
                            dn = seat(who)
                            if dn == VP.name or dn in teammates(VP.name):
                                raise IllegalAction(f"{dn} is on your team: attack the other team")
                    for who in dict(b["assign"]).values():
                        dn = seat(who) or next((h["name"] for h in HUMANS if h["name"].lower() == str(who).lower()), None)
                        if dn in out_now and dn != VP.name and isinstance(out_now[dn], int) and out_now[dn] <= 0:
                            raise IllegalAction(f"{dn} has lost (at {out_now[dn]}); attack a player still in the game")
                    key = (VP.name, VP.turn)
                    if ATTACKED.get("key") != key:
                        ATTACKED.update(key=key, ids=set())
                    for ref in dict(b["assign"]):
                        c0 = VP.perm(ref)
                        if c0.id in ATTACKED["ids"] or (VP.turn and c0.attacked_turn == VP.turn):   # (M17: the permanent remembers, across a restart)
                            raise IllegalAction(f"{VP.shown(c0)} is already attacking this turn" if c0.id in ATTACKED["ids"]
                                                else f"{VP.shown(c0)} has already attacked this turn")
                    look0_ = (VPS[VP.name].__dict__.get("pending_surveil", 0), VPS[VP.name].__dict__.get("pending_scry", 0))   # (owed from before the attack)
                    combat_lines = VP.begin_combat()
                    said = VP.manual_attack(b["assign"], role_on=b.get("role_on"))
                    said = combat_lines + (said if isinstance(said, list) else [said])
                    for ref in dict(b["assign"]):
                        ATTACKED["ids"].add(VP.perm(ref).id)
                    trig = VP.total_power_triggers()
                    for ref, who in b["assign"].items():
                        c = VP.perm(ref)
                        defender = seat(who) or next((h["name"] for h in HUMANS if h["name"].lower() == str(who).lower()), str(who))
                        entry = {"attacker": VP.name, "defender": defender, "ref": f"#{c.id}", "name": VP.shown(c),
                                 "power": VP.stats(c)[0], "trample": "Trample" in VP.kws(c, my_turn=True), "at": time.time(),
                                 "menace": "Menace" in VP.kws(c, my_turn=True), "unblockable": c.no_block_turn == VP.turn}
                        if defender in VPS and defender != VP.name:
                            COMBAT["pending"].append(entry)
                            k_ = f"{VP.name}>{defender}"      # the forecast the declaration line gives (T1): unblocked, double strike twice
                            COMBAT.setdefault("announced", {})[k_] = COMBAT.setdefault("announced", {}).get(k_, 0) + hit_power(entry)
                            entry["forecast"] = hit_power(entry)
                        else:
                            COMBAT["human"].append(entry)
                    # v6c: THE declaration of this turn, locked (M19). The defenders are asked once its triggers are answered (combat_tick, M5)
                    COMBAT.setdefault("decl", {})[VP.name] = {"turn": VP.turn, "stage": "triggers", "passed": False, "at": time.time(), "look0": look0_}
                    combat_loop_start()
                    if isinstance(said, list):
                        said += trig
                    elif trig:
                        said = [said] + trig
                elif action == "damage":                 # its attackers that hit a player: triggers + life
                    said = self._combat_damage(b)
                elif action == "activate" and (b.get("card") or b.get("zone")):
                    # an ability of a card in this seat's graveyard or hand, used from there: {card, zone: graveyard | hand, targets?}
                    import player as _pl
                    zone_ = str(b.get("zone") or "graveyard").lower()
                    if zone_ not in ("graveyard", "hand"):
                        raise IllegalAction("zone is graveyard or hand")
                    real_ = VPS[VP.name]
                    ab_ = next((a_ for a_ in real_.zone_abilities() if a_["zone"] == zone_ and a_["name"].lower().startswith(str(b.get("card") or "").strip().lower())), None)
                    aim_ = None
                    if ab_ is not None and isinstance(b.get("targets"), list):
                        aim_ = resolve_aim(VP.name, ab_.get("slots") or [], b["targets"], bool(b.get("_pilot")))
                    elif ab_ is not None and any(not s_.get("optional") and not aim_candidates(VP.name, s_) for s_ in ab_.get("slots") or []):
                        raise IllegalAction(f"{ab_['name']}: there is no legal target for it right now. Nothing was paid")
                    said = real_.activate_zone(str(b.get("card") or ""), zone=zone_, aim=aim_,
                                               sorcery_ok=on_turn(VP.name) and STEPS[PHASE["step"]] in ("main 1", "main 2"))
                elif action == "activate":                # pay and run an activated ability; the table checks the cost and what it does
                    tgt = None
                    tgt_owner = None
                    if b.get("target"):
                        owner = seat(b.get("at") or VP.name)
                        if owner is None:
                            raise IllegalAction(f"no seat '{b.get('at')}'")
                        tgt_owner = VPS[owner]
                        tgt = tgt_owner.perm(str(b["target"]))
                    mode = b.get("mode")
                    if mode not in (None, ""):
                        if not str(mode).strip().isdigit():
                            raise IllegalAction("mode is the number of the mode (0, 1, 2…), as the refusal lists them")
                        mode = int(str(mode).strip())
                    else:
                        mode = None
                    ward_said_ = pay_ward(VP.name, [{"perm": tgt, "owner": tgt_owner}]) if tgt is not None and tgt_owner is not None and \
                        VPS[VP.name].ability_why_not(VP.perm(str(b.get("source", ""))), 0 if b.get("index") in (None, "") else int(b["index"]),
                                                     sorcery_ok=on_turn(VP.name) and STEPS[PHASE["step"]] in ("main 1", "main 2")) is None else []
                    said = ward_said_ + VP.activate(str(b.get("source", "")), x=int(b.get("x") or 0),
                                       index=None if b.get("index") in (None, "") else int(b["index"]), target=tgt,
                                       pick=b.get("pick"), put=b.get("put"), bottom=b.get("bottom"), mode=mode,
                                       sac_ref=str(b["sac"]) if b.get("sac") not in (None, "") else None, target_owner=tgt_owner,
                                       tap_refs=[str(x) for x in b["tap"]] if isinstance(b.get("tap"), list) and b.get("tap") else None,
                                       sorcery_ok=on_turn(VP.name) and STEPS[PHASE["step"]] in ("main 1", "main 2"))
                    if getattr(VP, "last_peek", None):
                        private["top"], VP.last_peek = VP.last_peek, None
                elif action == "effect" and str(b.get("kind", "")).lower() == "apply-removal":
                    # Someone cast removal or a wipe at this seat (the table raised a 'removal' attention for it): this seat applies it to ITS
                    # OWN permanents, exactly what the spell says, once per event, within 15 minutes. Nothing else can be destroyed this way.
                    import oracle
                    with EV_LOCK:
                        evs = [e for e in EVENTS if e.get("type") == "attention" and e.get("kind") == "removal"
                               and str(e.get("addressee", "")).lower() == VP.name.lower() and e["id"] not in REMOVAL_APPLIED
                               and time.time() - e.get("ts", 0) < 900]
                    # Which removal: the event id when the caller names it (`event`), else the newest one this seat can apply. One the table can't apply
                    # (damage-type, "do it by hand") is marked done when named, and is skipped by the no-id default: a Plasma Cascade aimed at a bot
                    # once blocked every older destroy behind it for 15 minutes.
                    APPLIABLE = ("destroy", "exile", "bounce", "wipe", "exile-all", "bounce-all")
                    if b.get("event") not in (None, ""):
                        ev = next((e for e in evs if str(e["id"]) == str(b.get("event"))), None)
                        if ev is None:
                            raise IllegalAction(f"no unapplied removal event {b.get('event')} aimed at you (open ones: {[e['id'] for e in evs] or 'none'})")
                        if ev.get("effect") not in APPLIABLE:
                            REMOVAL_APPLIED.add(ev["id"])        # nothing the table can apply: marked done, so it never blocks another
                            raise IllegalAction(f"the table can't apply a '{ev.get('effect')}' effect yet ({ev.get('spell')}): do it by hand (event {ev['id']} is now closed)")
                    else:
                        for e_ in [e for e in evs if e.get("effect") not in APPLIABLE]:
                            REMOVAL_APPLIED.add(e_["id"])       # expired from the default: by hand, said when it was raised
                        evs = [e for e in evs if e.get("effect") in APPLIABLE]
                        if not evs:
                            raise IllegalAction("no unapplied removal the table can apply was aimed at you in the last 15 minutes")
                        ev = evs[-1]
                    eff, tgt, spell = ev.get("effect"), str(ev.get("target") or ""), ev.get("spell")
                    if eff not in APPLIABLE:
                        raise IllegalAction(f"the table can't apply a '{eff}' effect yet ({spell}): do it by hand")
                    wtext = ((oracle.card(spell) or {}).get("text") or "").lower() if spell else ""
                    zone = {"destroy": "graveyard", "exile": "exile", "bounce": "hand", "exile-all": "exile", "bounce-all": "hand"}.get(eff) \
                        or ("exile" if "exile all" in wtext else "graveyard")
                    if tgt == "all":
                        import wipes
                        text = ((oracle.card(spell) or {}).get("text") or "").lower() if spell else ""
                        victims = wipes.victims(VP.battlefield, text)
                        if victims is None:
                            raise IllegalAction(f"{spell}: the table can't tell which of your permanents it hits: do it by hand")
                    else:
                        victims = [VP.perm(tgt)]
                    said = [f"{spell or 'The removal'}: " + ("; ".join(VP.move(f"#{p.id}", zone, destroy=zone == "graveyard")[0] for p in sorted(victims, key=lambda q: bool(__import__("player").MCCOY_RX.search(q.card.get("text") or ""))) if p in VP.battlefield) or "nothing of mine is hit") + "."]
                    REMOVAL_APPLIED.add(ev["id"])
                elif action == "effect" and str(b.get("kind", "")).lower() == "opponent-mill":
                    # "Whenever a Horror you control deals combat damage to a player, that player mills that many cards" (Captain N'ghathrod),
                    # "defending player mills ten cards" (Nemesis of Reason): the card is on ANOTHER seat's recorded board and its text says so.
                    # A fixed number comes from the text; "that many" / X comes from `n` (at most 30). Once per card per ten minutes.
                    import oracle
                    srcname = str(b.get("source", "")).strip()
                    holder = next((h for h, bd in PUBLIC_BOARD.items() if h != VP.name and any(p.get("name", "").lower() == srcname.lower() for p in bd.get("permanents", []))), None)
                    if holder is None:
                        raise IllegalAction(f"{srcname} is not on another player's board (say who played it, or have a pilot record it first)")
                    text = (oracle.card(srcname) or {}).get("text") or ""
                    m = re.search(r"mills? (that many|an? |one|two|three|four|five|six|seven|eight|nine|ten|x|\d+)", text, re.I)
                    if not m:
                        raise IllegalAction(f"{srcname} doesn't say anything that mills. Its text: {text[:140]}")
                    word = m.group(1).strip().lower()
                    words = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
                    if word in words or word.isdigit():
                        n = words.get(word) or int(word)
                    else:
                        n = int(b.get("n") or 0)
                        if not 1 <= n <= 30:
                            raise IllegalAction(f"{srcname} mills 'that many' / X: say how many with --n (1 to 30)")
                    use = ("opp-mill", VP.name, srcname.lower())
                    if time.time() - EFFECT_USES.get(use, 0) < 600:
                        raise IllegalAction(f"you already milled for {srcname} in the last ten minutes")
                    EFFECT_USES[use] = time.time()
                    said = [f"{holder}'s {srcname}: " + VP.mill(n)[0]]
                elif action == "effect" and str(b.get("kind", "")).lower() == "opponent-token":
                    # "When this creature enters, target opponent creates two 3/3 Centaur tokens" (Hunted Horror): the card is on ANOTHER
                    # seat's recorded board, its text must say so, and the tokens are made on this seat once per card per ten minutes.
                    import oracle
                    srcname = str(b.get("source", "")).strip()
                    holder = next((h for h, bd in PUBLIC_BOARD.items() if h != VP.name and any(p.get("name", "").lower() == srcname.lower() for p in bd.get("permanents", []))), None)
                    if holder is None:
                        raise IllegalAction(f"{srcname} is not on another player's board (say who played it, or have a pilot record it first)")
                    text = (oracle.card(srcname) or {}).get("text") or ""
                    m = re.search(r"target opponent creates (an?|one|two|three|four|\d+) (\d+)/(\d+) ([\w -]+?) creature tokens?", text)
                    if not m:
                        raise IllegalAction(f"{srcname} doesn't say 'target opponent creates … tokens'. Its text: {text[:140]}")
                    n = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4}.get(m.group(1)) or int(m.group(1))
                    use = ("opp-token", VP.name, srcname.lower())
                    if time.time() - EFFECT_USES.get(use, 0) < 600:
                        raise IllegalAction(f"you already took {srcname}'s tokens in the last ten minutes")
                    EFFECT_USES[use] = time.time()
                    kws = ["Protection"] if "protection from" in text else []
                    said = [f"{holder}'s {srcname}: I create {n} {m.group(2)}/{m.group(3)} {m.group(4).strip().title()} tokens."]
                    VP.make_tokens(m.group(4).strip().split()[-1].title(), int(m.group(2)), int(m.group(3)), kws, n=n)
                elif action == "effect" and str(b.get("kind", "")).lower() in ("search-basic", "own-token"):
                    # An enters trigger on this seat's OWN permanent that the engine missed (game 3: Warp Core's land search). The
                    # card's own "When … enters" sentence must say it, and each permanent gets it once, ever. The log says what was done.
                    kind = str(b.get("kind")).lower()
                    src = VP.perm(str(b.get("source", "")))
                    if src.face_down:
                        raise IllegalAction("a face-down permanent has no effects: turn it face up first")
                    if kind == "search-basic":                   # (the trigger now asks by itself: answering this way is the same answer)
                        pc_ = VPS[VP.name].__dict__.get("pending_choices") or []
                        VPS[VP.name].__dict__["pending_choices"] = [x for x in pc_ if x.get("source_id") != src.id]
                    full = src.card.get("text") or ""
                    etb = " ".join(s for s in re.split(r"(?<=[.])\s+", full) if re.search(r"\benters\b", s, re.I))
                    use = ("own-trigger", VP.name, src.id, kind)
                    if use in EFFECT_USES or (src.id, kind) in getattr(VP, "etb_done", set()):
                        raise IllegalAction(f"{src.name}'s enters trigger was already applied")
                    if kind == "search-basic":
                        m = re.search(r"search your library for (?:a|up to one) basic land card", etb, re.I)
                        name = str(b.get("name", "")).strip().title()
                        if not m:
                            raise IllegalAction(f"{src.name} has no enters trigger that searches for a basic land: {full[:160]}")
                        if name not in ("Plains", "Island", "Swamp", "Mountain", "Forest"):
                            raise IllegalAction("name the basic land: Plains, Island, Swamp, Mountain or Forest")
                        to = "battlefield" if re.search(r"onto the battlefield", etb[m.end():], re.I) else "hand"
                        said = [f"{src.name} enters: "] + VP.search_library(name, to=to, tapped=to == "battlefield" and "tapped" in etb[m.end():].lower())
                        said = [" ".join(said)]
                    else:
                        made = []                                            # tokens only: the rest of the trigger is not re-run
                        for m in re.finditer(r"create (a|two|three) (\d+)/(\d+) ([\w ,]+?) creature tokens?", etb, re.I):
                            n = {"a": 1, "two": 2, "three": 3}[m.group(1).lower()]
                            kind_name = " ".join(w for w in m.group(4).replace(",", " ").split()
                                                 if w.lower() not in ("white", "blue", "black", "red", "green", "colorless", "and", "artifact"))
                            made += VP.make_tokens(kind_name or m.group(4), int(m.group(2)), int(m.group(3)), [], n=n)
                        if not made:
                            raise IllegalAction(f"{src.name} has no enters trigger that creates a creature token: {full[:160]}")
                        said = [f"{src.name} enters: " + " ".join(made)]
                    EFFECT_USES[use] = time.time()
                elif action == "effect":                  # a trigger or ability the engine does not model: allowed only when the SOURCE card says so
                    kind = str(b.get("kind", "")).lower()
                    if kind not in EFFECT_WORDS:
                        raise IllegalAction(f"effect kinds: {', '.join(EFFECT_WORDS)}")
                    src = VP.perm(str(b.get("source", "")))
                    if src.face_down:
                        raise IllegalAction("a face-down permanent has no effects: turn it face up first")
                    text = (src.card.get("text") or "").lower()
                    if not re.search(EFFECT_WORDS[kind], text):
                        raise IllegalAction(f"{src.name} doesn't say anything that {kind}s: it reads: {(src.card.get('text') or '')[:160]}")
                    use = (VP.name, VP.turn, src.id, kind)
                    EFFECT_USES[use] = EFFECT_USES.get(use, 0) + 1
                    if EFFECT_USES[use] > (1 if kind in ("destroy", "exile", "bounce") else 3):
                        raise IllegalAction(f"{src.name} has already done that as often as it can this turn")
                    n = max(1, min(int(b.get("n") or 1), 3))
                    if kind == "draw":
                        before = len(VP.hand)
                        VP.draw(n)
                        private["drew"] = [c["name"] for c in VP.hand[before:]]
                        said = [f"{src.name}: I draw {len(private['drew'])} card{'s' if len(private['drew']) != 1 else ''}."]
                    elif kind in ("surveil", "scry"):
                        top = VP.peek(1)
                        private["top"] = top
                        put = str(b.get("put") or "top")
                        if top and put == "graveyard" and kind == "surveil":
                            VP.mill(1)
                        elif top and put == "bottom" and kind == "scry":
                            VP.bottom(top[0], from_top=True)
                        elif put not in ("top", "graveyard", "bottom"):
                            raise IllegalAction("put: top | bottom (scry) | graveyard (surveil)")
                        said = [f"{src.name}: I {kind} 1" + (f" and put it on the {put}." if put != "top" else " and leave it on top.")]
                    elif kind == "manifest":
                        said = VP.manifest(n)
                    else:
                        owner = seat(b.get("at") or VP.name)
                        if owner is None:
                            raise IllegalAction(f"no seat '{b.get('at')}'")
                        tv = VPS[owner]
                        tp = tv.perm(str(b.get("target", "")))
                        if kind == "destroy" and "Indestructible" in tv.kws(tp):
                            raise IllegalAction(f"{tv.shown(tp)} is indestructible")
                        said = [f"{src.name}: " + tv.move(f"#{tp.id}", {"destroy": "graveyard", "exile": "exile", "bounce": "hand"}[kind], destroy=kind == "destroy")[0]]
                        if owner != VP.name:
                            emit("attention", kind="effect", addressee=owner, text=f"{VP.name}'s {src.name} {kind}s your {tp.name}.")
                elif action == "station":                # {ship, crew}: Station only as a sorcery, on your own turn
                    if not on_turn(VP.name) or STEPS[PHASE["step"]] not in ("main 1", "main 2"):
                        raise IllegalAction("Station only as a sorcery: in your own main phase")
                    said = VP.station(str(b.get("ship", "")), str(b.get("crew", "")))
                elif action == "equip":                  # {equipment, creature}: Equip only as a sorcery, on your own turn
                    if not on_turn(VP.name) or STEPS[PHASE["step"]] not in ("main 1", "main 2"):
                        raise IllegalAction("Equip only as a sorcery: in your own main phase")
                    said = VP.equip(str(b.get("equipment", "")), str(b.get("creature", "")))
                elif action == "cycle":                  # {name}: landcycling, from the hand, at instant speed
                    said = VPS[VP.name].landcycle(str(b.get("name", "")))
                elif action == "choose":                 # {id, target}: the answer to an aimed trigger the table is waiting on
                    real_ = VPS[VP.name]
                    pend_ = real_.__dict__.get("pending_choices") or []
                    if not pend_:
                        raise IllegalAction("nothing is waiting on your choice")
                    item_ = next((x for x in pend_ if str(x["id"]) == str(b.get("id"))), None) if b.get("id") not in (None, "") else pend_[0]
                    if item_ is None:
                        raise IllegalAction(f"no choice {b.get('id')} is waiting")
                    tg_ = b.get("target") or {}
                    if isinstance(tg_, dict) and tg_.get("ref") and str(tg_["ref"]).startswith("#") and item_["slot"].get("zone") != "option":
                        own_ = VPS.get(seat(str(tg_.get("at") or VP.name)) or "")
                        if own_ is not None and not any(f"#{p_.id}" == str(tg_["ref"]) for p_ in own_.battlefield):
                            # The option was on the list when it was read and its permanent has left play since (another seat acted in between):
                            # the question has changed, which is not the asker's mistake. Settle it (one option left: that one; none: nothing
                            # happens) and say "read it again", as for any answer that arrives after the table moved on.
                            settle_and_drain()
                            return self._send(409, {**phase_public(), "choose": True, "stale": True,
                                                    "error": f"{tg_['ref']} has left play since that list was made: read your choices again"})
                    said = answer_choice(VP.name, item_, tg_)
                elif action == "manual" and str(b.get("op", "")) == "temp":
                    # 2026-11-13 retro (C4): the host undoes or moves a mistaken "until end of turn" change. {"op": "temp", "ref": "#id",
                    # "power": ±n, "toughness": ±n} on ANY virtual seat's creature (the seat named is its controller). The HOST only: the brain
                    # token, never a seat's key (a pilot's request carries _pilot; `manual` is otherwise a pilot's own-cards tool, and a
                    # player must not be able to resize a creature in the middle of combat).
                    if b.get("_pilot"):
                        self._refused(action, "host-only", "")
                        return self._send(403, {"error": "only the table's host can correct an until-end-of-turn change (ask the host; both players should agree)"})
                    said = VPS[VP.name].host_temp(str(b.get("ref", "")), b.get("power") or 0, b.get("toughness") or 0)
                elif action == "manual":                 # T10: a person's own cards, by hand, logged in public words (pilot seats; never a bot)
                    if VP.name not in PILOTS:
                        raise IllegalAction("manual moves are for a person's own seat (a pilot); a bot plays its cards through the engine")
                    said = VPS[VP.name].manual(str(b.get("op", "")), ref=str(b.get("ref", "")), to=str(b.get("to", "")), kind=str(b.get("kind") or "+1/+1"),
                                               n=int(b.get("n") or 1), name=str(b.get("name") or ""), power=int(b.get("power") or 1),
                                               toughness=int(b.get("toughness") or 1), tapped=bool(b.get("tapped")))
                elif action == "swap":                   # {from, to}: a source the table tapped for a payment in this step, for another that makes the same mana
                    said = VPS[VP.name].swap_payment(str(b.get("from", "")), str(b.get("to", "")))
                elif action in ("surveil", "scry"):     # {graveyard|bottom: [names], top_order?: [names]}: the look the table asked for (private.surveil /
                    real_ = VPS[VP.name]                 # private.scry). The rest go back on top in the order named (top first), or as they were
                    n_ = real_.__dict__.get("pending_" + action, 0)
                    if not n_:
                        raise IllegalAction(f"nothing is waiting on a {action}")
                    away_ = b.get("graveyard" if action == "surveil" else "bottom") or []
                    order_ = b.get("top_order")
                    if not isinstance(away_, list) or (order_ is not None and not isinstance(order_, list)):
                        raise IllegalAction(("graveyard" if action == "surveil" else "bottom") + " and top_order are lists of card names")
                    said = real_.finish_look(action, n_, [str(x) for x in away_], None if order_ is None else [str(x) for x in order_])
                    said += real_.resume_deferred()      # what the spell or trigger still had to do after the look ("…, then draw a card")
                elif action == "next":                   # the step moved in ai_advance (or just above, with windows off); what the new step does
                    said = []
                    frm = b.get("_from_step")
                    if frm == "combat damage" and ATTACKED.get("key") == (VP.name, VP.turn) and ATTACKED.get("ids") and DAMAGED.get("key") != (VP.name, VP.turn):
                        b["no_life"] = True              # the hits are already off their life (block / flush): triggers and lifelink only
                        said += self._combat_damage(b)   # exactly as Finish combat damage does
                    if STEPS[PHASE["step"]] == "beginning of combat":
                        said += VP.begin_combat()
                elif action == "role":
                    said = VP.add_role(VP.perm(b["ref"]), b["kind"].strip().title())
                elif action == "end":
                    real_ = VPS[VP.name]
                    real_.__dict__["skip_each_end"] = (VP.name, "end step") in STEP_FIRED   # a teammate's End already ran my "each end step"
                    try:
                        said = VP.end_step()
                        for o_ in list(VPS.values()):          # "at the beginning of the next end step": every seat's delayed returns, this one's said here
                            back_ = o_.return_delayed()
                            if o_.name == VP.name:
                                said += back_
                            else:
                                for line_ in back_:
                                    emit("say", speaker=o_.name, text=line_, action="trigger", auto=True)
                        said += [b.get("text") or "That's my turn."]
                    finally:
                        real_.__dict__.pop("skip_each_end", None)
                    STEP_FIRED.add((VP.name, "end step"))
                    for o_ in [x for n_, x in VPS.items() if n_ != VP.name]:        # "each end step" on the other seats: once per turn
                        if (o_.name, "end step") in STEP_FIRED:
                            continue
                        STEP_FIRED.add((o_.name, "end step"))
                        for line_ in o_.step_triggers("each end step"):
                            emit("say", speaker=o_.name, text=line_, action="trigger", auto=True)
                elif action in ("destroy", "exile", "bounce"):
                    said = VP.move(b["ref"], {"destroy": "graveyard", "exile": "exile", "bounce": "hand"}[action], destroy=action == "destroy")
                elif action == "to-library":               # its own permanent into its library (Chaos Warp): top | bottom | shuffle
                    said = VP.move(b["ref"], "library", where=str(b.get("where") or ""))
                elif action in ("tap", "untap"):
                    p = VP.perm(b["ref"])
                    p.tapped = action == "tap"
                    if action == "tap" and b.get("_pilot"):
                        HAND_TAPS[(VP.name, p.id)] = (PHASE["player"], PHASE["step"])
                    else:
                        HAND_TAPS.pop((VP.name, p.id), None)
                    said = [f"{p.name} is {action}ped."] if b.get("announce") else []
                elif action == "cant-block":             # host: "target creature can't block this turn" on this seat's permanent (M22)
                    p = VP.perm(b["ref"])
                    p.no_blocking_tick = __import__("player").DEATH_TICK[0]
                    said = [f"{VP.shown(p)} can't block this turn."]
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
                elif action == "keep":                     # T15: keep this opening hand; {bottom: [names]} one per mulligan taken
                    if not MULL["on"]:
                        raise IllegalAction("there is no opening hand to keep right now" + ("" if PHASE.get("player") is None else ": the game is under way"))
                    bot_ = b.get("bottom") or []
                    if not isinstance(bot_, list):
                        raise IllegalAction("bottom is a list of card names from your hand")
                    size_ = VPS[VP.name].mull_keep([str(x) for x in bot_])
                    said = [f"{VP.name} keeps {size_}."]
                elif action == "mulligan" and (MULL["on"] or b.get("_pilot")):   # T15: London mulligan, in the opening-hand step
                    if not MULL["on"]:
                        raise IllegalAction("mulligans are only in the opening-hand step, before the first turn")
                    size_ = VPS[VP.name].mull_again()
                    said = [f"{VP.name} mulligans to {size_}."]
                    private["mulligan"] = True
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
                    said = [str(b["text"]).strip()[:200]] if str(b.get("text") or "").strip() else []   # the reason, said aloud
                elif action == "new-game":
                    reshuffle_all()
                    for k in LIFE:
                        LIFE[k] = 40
                    journal_reset()
                    READY.clear()
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
            self._audit_entered(audit_before, audit_ev0, action)
            if GAME_OVER.get("over") or " has lost (at " in str(e) or str(e).startswith("no permanent '#"):
                # The list the caller read was true when it was read: the game ended, a player lost, or the permanent a listed move names (an
                # attacker, a blocker) left play, in between, by another seat's action. That is the table having moved on, not a bad request:
                # 409 and "read it again", the same answer as for any stale list.
                return self._send(409, {**phase_public(), "stale": True, "error": str(e)})
            return self._send(400, {"error": str(e)})
        except KeyError as e:
            self._refused(action, "missing-field", str(e))
            return self._send(400, {"error": f"missing field {e}"})
        BRAIN_LAST[0] = time.time()
        if action in ("keep", "mulligan"):
            snapshot()                                # a restart in the middle of the step keeps every answer given so far
        if action == "keep":
            mull_check_done()                         # the last keep starts the first turn
        if action in ("pass", "cast", "turn-up") and VP.name in PRIORITY["waiting"]:
            priority_done(VP.name)                    # answered (cast or pass): the window can close
        ender = VP.name                               # whose end hands the turn on
        if action == "end" and TEAMS and on_turn(VP.name):   # 2HG: the turn passes when every teammate on turn has ended
            TEAM_TURN["done"].add(VP.name)
            lead = PHASE["player"]
            waiting = any(n not in TEAM_TURN["done"] and not is_out(n) for n in [lead, *teammates(lead)])
            ender = None if waiting else lead
        if action == "end" and ender is not None and PHASE["player"] == ender:   # an AI ends its turn: on to the next seat
            journal_due(ender, "end_of_turn")
            order = turn_order()
            with PHASE_LOCK:
                PHASE["step"] = len(STEPS) - 1
                nxt = seat_after(ender) or order[(order.index(ender) + 1) % len(order)]
            if not GAME_OVER.get("over"):             # a finished game hands out no more turns
                threading.Timer(0.5, lambda: hand_turn_on(ender, nxt)).start()
        if action == "begin" and TEAMS and on_turn(VP.name) and PHASE["player"] != VP.name:
            TEAM_TURN["begun"].add(VP.name)           # 2HG: a teammate began its half; the lead keeps the turn
        elif action == "begin":                       # the turn button or NEXT handed this seat its turn
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
            settle_and_drain()
            if speak:
                self._audit_entered(audit_before, audit_ev0, action)
            if HELD.get("seat") and not (action == "cast" and private.get("held")):
                if action in ("cast", "activate", "turn-up") and VP.name in HELD["waiting"]:
                    HELD["waiting"].discard(VP.name)     # it responded: that was its answer
                    HELD.setdefault("responded", set()).add(VP.name)
                    HELD["until"] = max(HELD["until"], time.time() + RESPOND_SECS)    # (time for "pay or not", if that is now asked)
                held_progress()
            for v_ in list(VPS.values()):              # what the table did not do says so, where everyone can read it (never silently nothing)
                for t_ in list(v_.todo):
                    if (v_.name, t_, v_.turn) not in NOTED:
                        NOTED.add((v_.name, t_, v_.turn))
                        emit("table-note", seat=v_.name, text=f"By hand: {t_}")
                v_.todo.clear()
            if MULL["on"]:
                private["mulligan"] = mull_private(VP.name)
            pc_ = VPS[VP.name].__dict__.get("pending_choices")
            if pc_:
                private["choices"] = [{**x, "slot": {**x["slot"], "options": aim_candidates(VP.name, x["slot"])}} for x in pc_]
            ps_ = VPS[VP.name].__dict__.get("pending_surveil", 0)
            if ps_:
                private["surveil"] = {"n": ps_, "cards": VP.peek(ps_)}
            pk_ = VPS[VP.name].__dict__.get("pending_scry", 0)
            if pk_:
                private["scry"] = {"n": pk_, "cards": VP.peek(pk_)}
            for d_ in (COMBAT.get("decl") or {}).values():   # v6c: the window after blocks
                if d_.get("stage") != "window":
                    continue
                if action in ("cast", "activate", "turn-up", "cycle"):
                    d_["passed"] = False                 # something new: the attacker's earlier pass no longer stands,
                    d_.setdefault("acted", set()).add(VP.name)   # and whoever acted ends its own say with its own pass (never passed for)
                    if on_turn(VP.name):
                        priority_reset(f"{VP.name} acted after blocks ({action})")   # the others may answer it
                    else:                                # asked again, for a seat that answers prompts rather than reading the phase
                        emit("attention", kind="priority", addressee=VP.name, step=STEPS[PHASE["step"]], active=PHASE["player"],
                             text=f"{PHASE['player']}: {STEPS[PHASE['step']]}. You acted after blocks: do more, or pass when you are done.")
                elif action == "pass":
                    (d_.get("acted") or set()).discard(VP.name)
            combat_tick()                                # v6c: triggers answered -> blocks asked -> window -> the damage step
            said = [*said, *(getattr(_COMBAT_TL, "said", None) or [])]
            _COMBAT_TL.said = None
            state = VP.public()
        return self._send(200, {"ok": True, "said": said, "private": private, "public": state})


def _warm():
    import numpy as np
    import voice
    try:
        voice.transcribe(np.zeros(16000, dtype=np.float32), "warm-up")
    except ImportError as e:                          # no Whisper on this machine (not an Apple-silicon Mac): the table runs, the open mic is off
        print(f"open mic off ({e}); to turn it on: pip install mlx-whisper (Apple silicon)", flush=True)
        voice.WHISPER_SECOND = ""
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
if not CLOUD and os.environ.get("TABLE_NO_VOICE") != "1":   # agent tables (TABLE_NO_VOICE=1) skip the speech models:
    threading.Thread(target=_warm, daemon=True).start()   # ~500 MB per table with them (multi_table_probe); loaded on first use

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
threading.Thread(target=pilot_auto_begin_loop, daemon=True).start()
threading.Thread(target=mull_loop, daemon=True).start()
ThreadingHTTPServer((args.host, args.port), H).serve_forever()
