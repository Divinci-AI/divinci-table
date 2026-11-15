#!/usr/bin/env python3
"""Divinci Table — Texas Hold'em, play chips only. Everyone sits at a 3D felt (2D on phones); your own two cards
reach only your device.

    python table/poker_server.py --players "Ana,Ben" --ai "ai:Leonardo,ai:Fusion" --chips 1000 --blinds 10/20 --port 8812

A seat is a person's name (--players) or `ai:<name>` (--ai). The AI is rule-based (Chen preflop score, then
Monte-Carlo equity against pot odds): no model spend, and it only ever sees its own cards. --seed makes the
shuffles and the AI repeatable, for tests; without it every hand's seed comes from `secrets`.

Hidden information: GET /api/poker (and every event) carries public information only. A player's own cards come
from GET /api/poker/me with that seat's key in X-Seat-Key. Each hand publishes commit = SHA-256(seed) before the
deal; when the GAME is over (one stack left, or a player pressed End game: POST /api/poker/end) GET
/api/poker/verify?hand=N gives each hand's seed and the deck it makes (poker_eval.ALGORITHM). Seeds wait for the end
of the game because a seed rebuilds the whole deck: revealed after each hand, it would show everyone the folded cards.

Seats, events, talk, photos and cloud persistence come from core.py, as for chess.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from core import Events, Room, Seats, clean_text, device_of, jdump, save_photo, save_survey  # noqa: E402
from poker_eval import Game, Illegal  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--players", default="Player 1,Player 2", help="the people, comma-separated")
ap.add_argument("--ai", default="", help="AI seats, comma-separated: ai:Leonardo,ai:Fusion")
ap.add_argument("--chips", type=int, default=1000)
ap.add_argument("--blinds", default="10/20", help="small/big, e.g. 10/20")
ap.add_argument("--seed", type=int, default=None, help="repeatable shuffles and AI (tests only)")
ap.add_argument("--pause", type=float, default=6.0, help="seconds between hands")
ap.add_argument("--ai-think", type=float, default=1.1, help="the AI's average pause before acting, in seconds")
ap.add_argument("--ai-iters", type=int, default=300, help="Monte-Carlo deals per AI decision (at most 300)")
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--port", type=int, default=8812)
args = ap.parse_args()
CLOUD = os.environ.get("TABLE_CLOUD") == "1" or sys.platform != "darwin"
if os.environ.get("TABLE_CLOUD") == "1" and args.seed is not None:              # a seeded table's every deck follows from the number: anyone who knows it sees every hand
    sys.exit("poker: --seed is for tests only and is refused on a cloud table (TABLE_CLOUD=1)")
RESEARCH = Path(os.environ.get("TABLE_RESEARCH_DIR") or HERE / ".cache" / "research") / "poker"
ITERS = max(10, min(300, args.ai_iters))


def parse_seats() -> list:
    out = [{"name": clean_text(n, 24), "ai": False} for n in args.players.split(",") if clean_text(n, 24)]
    for spec in args.ai.split(","):
        spec = spec.strip()
        if spec:
            out.append({"name": clean_text(spec[3:] if spec.startswith("ai:") else spec, 24).split(":")[0] or "AI", "ai": True})
    return out


SEAT_LIST = parse_seats()
HUMANS = [p["name"] for p in SEAT_LIST if not p["ai"]]
sb, bb = (int(x) for x in args.blinds.split("/"))
SEATS = Seats(HUMANS)
EVENTS = Events()
LOCK = threading.RLock()
TALK = random.Random(f"poker-talk:{args.seed}") if args.seed is not None else random.Random()
GAME_ID = time.strftime("%Y%m%d-%H%M%S")


def on_event(kind: str, **kw) -> None:
    EVENTS.emit("poker", kind=kind, **kw)


G = Game(SEAT_LIST, chips=args.chips, blinds=(sb, bb), seed=args.seed, emit=on_event)
NEXT_AT = {"t": None}                                   # when the next hand deals itself (between hands)

# ── the AI seats' voice: a few lines at the moments people talk at a card table, never about their cards ──
LINES = {
    "open": ["Shuffle up and deal.", "Chips only, honour everything. Let's play."],
    "raise": ["Let's make it a little more interesting.", "I'll raise.", "Let's see who means it."],
    "allin": ["All of it. Your move.", "I'm all in."],
    "fold": ["Not this one.", "I'll let this one go."],
    "won": ["I'll take that, thank you.", "Nicely played, everyone.", "The cards were kind."],
    "lost": ["Well played.", "You had it. Good hand."],
}


def say(name: str, key: str, p: float = 1.0) -> None:
    if TALK.random() < p:
        EVENTS.emit("chat", by=name, text=TALK.choice(LINES[key]), ai=True)


def save_hand() -> None:
    """The finished hand's public record (and its now-public seed), for research. Local only (gitignored)."""
    try:
        d = RESEARCH / GAME_ID
        d.mkdir(parents=True, exist_ok=True)
        pub = G.public()
        rec = {"hand": G.h["no"], "commit": G.h["commit"], "seed": G.history[G.h["no"]]["seed"], "board": pub["board"],
               "result": pub["result"], "chips": {s["name"]: s["chips"] for s in G.seats}, "ts": round(time.time(), 1)}
        with open(d / "hands.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError as e:
        print(f"hand not saved: {e}", flush=True)


def after_change(prev_done: bool = False) -> None:
    """Call with LOCK held after anything moved the game: the AI's turn, the next hand's timer, a finished hand's talk."""
    h = G.h
    if not h:
        return
    if h["street"] == "done":
        if not prev_done:
            save_hand()
            res = h.get("result") or {}
            winners = {w for p in res.get("pots", []) for w in p["winners"]}
            for s in G.seats:
                if s["ai"] and s["in_hand"] and not s["folded"]:
                    say(s["name"], "won" if s["name"] in winners else "lost", 0.45)
        if not G.over and NEXT_AT["t"] is None:
            NEXT_AT["t"] = time.time() + args.pause
            no = h["no"]
            threading.Thread(target=deal_later, args=(no, NEXT_AT["t"]), daemon=True).start()
        return
    i = h["to_act"]
    if i is not None and G.seats[i]["ai"]:
        threading.Thread(target=ai_turn, args=(i, h["no"], h["version"]), daemon=True).start()


def deal_now() -> None:
    NEXT_AT["t"] = None
    G.start_hand()
    if G.hand_no == 1:
        ai = next((s for s in G.seats if s["ai"]), None)
        if ai:
            say(ai["name"], "open")
    after_change()


def deal_later(no: int, at: float) -> None:
    time.sleep(max(0.0, at - time.time()))
    with LOCK:
        if G.h and G.h["no"] == no and G.h["street"] == "done" and not G.over and NEXT_AT["t"] == at:
            deal_now()


def ai_turn(i: int, no: int, version: int) -> None:
    """The AI thinks off the lock (its Monte-Carlo never holds up the table) and acts only if nothing moved meanwhile."""
    time.sleep(args.ai_think * random.uniform(0.5, 1.5))
    with LOCK:
        if not G.h or G.h["no"] != no or G.h["version"] != version or G.h["to_act"] != i:
            return
        view, rng = G.ai_view(i), G.ai_rng[G.seats[i]["name"]]
    from poker_eval import ai_decide
    action, amount = ai_decide(view, rng, ITERS)
    with LOCK:
        if not G.h or G.h["no"] != no or G.h["version"] != version or G.h["to_act"] != i:
            return
        name = G.seats[i]["name"]
        try:
            G.act(name, action, amount)
        except Illegal as e:                              # never stall the table on an AI slip: the safe action instead
            print(f"AI {name}: {action} {amount} refused ({e}); checking or folding", flush=True)
            G.act(name, "check" if G.legal(i).get("check") else "fold")
        if action in ("raise", "bet"):
            say(name, "raise", 0.3)
        elif action == "allin":
            say(name, "allin", 0.6)
        elif action == "fold":
            say(name, "fold", 0.15)
        after_change()


def public_state() -> dict:
    with LOCK:
        d = G.public()
        d.update(game="poker", game_id=GAME_ID, next_hand_at=NEXT_AT["t"], pause=args.pause,
                 humans=HUMANS, rev=EVENTS.next_id - 1)
        if d["to_act"] is not None:
            d["to_act_name"] = G.seats[d["to_act"]]["name"]
        return d


def my_state(name: str) -> dict:
    with LOCK:
        i = G.index(name)
        s = G.seats[i]
        hole = list(s["hole"]) if s["in_hand"] else []
        out = {"name": s["name"], "seat": i, "hole": hole, "legal": G.legal(i), "folded": s["folded"],
               "can_show": bool(G.h) and G.h["street"] == "done" and s["in_hand"] and not s["folded"] and not s["shown"]}
        if hole and (len(G.h.get("board", [])) >= 3):
            from poker_eval import evaluate, hand_name
            out["hand_name"] = hand_name(evaluate(hole + G.h["board"]))
        return out


def dump() -> dict:
    with LOCK:
        return {"game": G.export(), "game_id": GAME_ID, "seats": SEATS.state(), "events": EVENTS.state(), "saved": time.time()}


def load(d: dict) -> None:
    global G, GAME_ID
    with LOCK:
        G = Game.restore(d["game"], emit=on_event)
        GAME_ID = d.get("game_id", GAME_ID)
        SEATS.load(d.get("seats") or {})
        EVENTS.load(d.get("events") or {})
        NEXT_AT["t"] = None
        after_change(prev_done=True)                      # the AI's turn or the next hand carry on where they were


ROOM = Room(dump, load)


# ── HTTP ─────────────────────────────────────────────────────────────────────────────────────
STATIC = {"/survey": "survey.html", "/": "poker.html", "/watch": "poker.html", "/stage": "poker.html", "/me": "poker.html",
          "/poker": "poker.html"}
ASSET_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, obj=None, body: bytes | None = None, ctype: str = "application/json"):
        body = body if body is not None else jdump(obj if obj is not None else {})
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if 0 < n <= 9_000_000 else b""

    def _json(self) -> dict:
        try:
            d = json.loads(self._body() or b"{}")
            return d if isinstance(d, dict) else {}
        except ValueError:
            return {}

    def _key(self, b: dict | None = None) -> str:
        return unquote(self.headers.get("X-Seat-Key", "")) or str((b or {}).get("key") or "")

    def _seat_of_key(self, key: str):
        return next((n for n in HUMANS if key and SEATS.ok(n, key)), None)

    def _room(self, method: str, p: str) -> bool:
        if not p.startswith("/api/room/"):
            return False
        if not ROOM.token_ok(self.headers.get("X-Room-Token", "")):    # the snapshot holds every card: server to server only
            if method == "POST":
                self._body()
            self._send(404, {"error": "not found"})
            return True
        if p == "/api/room/status":
            self._send(200, {"adopted": ROOM.adopted, "rev": EVENTS.next_id - 1})
        elif p == "/api/room/adopt" and method == "POST":
            ROOM.adopted = True
            self._send(200, {"ok": True})
        elif p == "/api/room/snapshot":
            blob = ROOM.snapshot()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(blob)))
            self.send_header("X-Snapshot-Rev", str(EVENTS.next_id - 1))
            self.end_headers()
            self.wfile.write(blob)
        elif p == "/api/room/restore" and method == "POST":
            code, out = ROOM.restore(self._body())
            self._send(code, out)
        elif p == "/api/room/release" and method == "POST":
            name = str(self._json().get("seat", ""))
            ok = SEATS.release(name)
            if ok:
                EVENTS.emit("seat", name=name, kind="released")
            self._send(200 if ok else 404, {"ok": ok, "released": name} if ok else {"error": "no such human seat"})
        else:
            self._send(404, {"error": "not found"})
        return True

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        if self._room("GET", p):
            return
        if p in STATIC:
            return self._send(200, body=(HERE / STATIC[p]).read_bytes(), ctype="text/html; charset=utf-8")
        if p == "/api/poker":
            return self._send(200, public_state())
        if p == "/api/poker/me":                          # your own cards: only with your seat's key
            who = self._seat_of_key(self._key())
            want = parse_qs(u.query).get("player", [""])[0]
            if not who or (want and SEATS.canonical(want) != who):
                return self._send(403, {"error": "claim your seat first (👤): your cards go only to your own device", "need_seat": True})
            return self._send(200, my_state(who))
        if p == "/api/poker/verify":
            try:
                no = int(parse_qs(u.query).get("hand", ["0"])[0])
            except ValueError:
                return self._send(400, {"error": "hand=N, a hand number"})
            with LOCK:
                code, out = G.verify(no)
            return self._send(code, out)
        if p == "/api/game/id":                           # which game this is: seat.js tells a restarted game from the one a key was for
            return self._send(200, {"game": "poker", "game_id": GAME_ID})
        if p == "/api/seat/claims":
            return self._send(200, SEATS.public(device_of(self)))
        if p == "/api/events":                            # ?wait=N: a long poll, so an action reaches the other screens at once
            qs = parse_qs(u.query)
            q = qs.get("since", ["0"])[0]
            try:
                n = EVENTS.next_id - 1 if q == "latest" else int(q or 0)
                wait = float(qs.get("wait", ["0"])[0])
            except ValueError:
                return self._send(400, {"error": "since=N"})
            return self._send(200, EVENTS.wait_since(n, wait) if wait > 0 and q != "latest" else EVENTS.since(n))
        if p == "/api/voice-config":
            return self._send(200, {"humans": [{"name": n} for n in HUMANS], "ai_players": [{"name": s["name"]} for s in SEAT_LIST if s["ai"]]})
        for pre, root in (("/assets/", HERE / "assets"), ("/vendor/", HERE / "vendor")):   # page code; three.js for the 3D table
            if p.startswith(pre):
                f = (root / unquote(p[len(pre):])).resolve()
                if f.is_file() and root.resolve() in f.parents and f.suffix in ASSET_TYPES:
                    return self._send(200, body=f.read_bytes(), ctype=ASSET_TYPES[f.suffix])
        if p.startswith("/photos/"):
            base = (RESEARCH / GAME_ID / "photos").resolve()
            f = (base / p[8:]).resolve()
            if f.is_file() and base in f.parents and f.suffix in (".jpg", ".png"):
                return self._send(200, body=f.read_bytes(), ctype=ASSET_TYPES[f.suffix])
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        p = urlparse(self.path).path
        if self._room("POST", p):
            return
        if p == "/api/seat/claim":
            b = self._json()
            code, out = SEATS.claim(str(b.get("name", "")), str(b.get("key") or ""), device_of(self), CLOUD)
            if code == 200:
                EVENTS.emit("seat", name=out["name"], kind="claimed")
            return self._send(code, out)
        if p == "/api/seat/check":                         # seat.js (and the room's Worker) ask whether a key is still good here
            self._body()
            who = self._seat_of_key(self.headers.get("X-Seat-Key", ""))
            return self._send(200, {"seat": who}) if who else self._send(403, {"error": "not a seated player"})
        if p == "/api/chat/photo":
            by = unquote(self.headers.get("X-By", ""))[:30]
            data = self._body()
            if not SEATS.ok(by, self._key()):
                return self._send(403, {"error": "claim your seat first (👤) to share photos"})
            code, out = save_photo(data, RESEARCH / GAME_ID / "photos")
            if code == 200:
                EVENTS.emit("chat", by=SEATS.canonical(by), text=clean_text(unquote(self.headers.get("X-Caption", "")), 300) or "📷 a photo of the table", photo=out["photo"])
            return self._send(code, out)
        b = self._json()
        by = str(b.get("by", ""))
        if not SEATS.ok(by, self._key(b)):
            return self._send(403, {"error": "claim your seat first (👤): only you can act for your seat", "need_seat": True})
        by = SEATS.canonical(by)
        if p == "/api/survey":
            code, out = save_survey(RESEARCH / GAME_ID, by, b)
            if code == 200:
                EVENTS.emit("survey", by=by)
            return self._send(code, out)
        if p == "/api/chat":
            text = clean_text(b.get("text"), 400)
            if not text:
                return self._send(400, {"error": "say something"})
            EVENTS.emit("chat", by=by, text=text)
            return self._send(200, {"ok": True})
        with LOCK:
            prev_done = bool(G.h) and G.h["street"] == "done"
            try:
                if p == "/api/poker/start":                # the first hand; later ones deal themselves
                    if not G.started:
                        deal_now()
                elif p == "/api/poker/next":               # don't wait out the pause
                    if G.over:
                        raise Illegal("the game is over")
                    if G.h and G.h["street"] != "done":
                        raise Illegal("this hand isn't over yet")
                    deal_now()
                elif p == "/api/poker/act":
                    G.act(by, str(b.get("action", "")), b.get("amount", 0))
                    after_change(prev_done)
                elif p == "/api/poker/show":
                    G.show(by)
                elif p == "/api/poker/end":                # stop play: bets in a live hand go back, then every seed is revealed
                    G.end_game(by)
                    NEXT_AT["t"] = None
                else:
                    return self._send(404, {"error": "not found"})
            except Illegal as e:
                code = 409 if "turn" in str(e) or "over" in str(e) or "no betting" in str(e) else 400
                return self._send(code, {**public_state(), "error": str(e)})
            out = public_state()
        out["me"] = my_state(by)
        return self._send(200, out)


if __name__ == "__main__":
    class Server(ThreadingHTTPServer):
        request_queue_size = 128                  # several screens open at once, each holding a long poll
    srv = Server((args.host, args.port), H)
    srv.daemon_threads = True
    print(f"poker: {', '.join(s['name'] + (' (AI)' if s['ai'] else '') for s in SEAT_LIST)} — "
          f"{args.chips} chips, blinds {sb}/{bb} — http://localhost:{args.port}/", flush=True)
    srv.serve_forever()
