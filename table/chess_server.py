#!/usr/bin/env python3
"""Divinci Table — chess. A real board on the table (or none); each move is confirmed on a phone.

    python table/chess_server.py --white "Ann" --black "ai:Leonardo:6" --minutes 15 --increment 10 --port 8800

A seat is a person's name, or `ai:<name>[:<level 1-20>]` for an AI player. The AI uses Stockfish when the
binary is present (the cloud image installs it) and a small built-in engine otherwise. Its "personality" is
a voice, not a brain: short lines at the moments people talk at a chess board, from templates (no model
spend, nothing secret to leak — it only ever knows the public position).

Shared table pieces (seats, events, talk, photos, cloud persistence) come from core.py, so the cloud room,
seat.js and photo.js work exactly as they do for Magic.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import chess
import chess.pgn

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from core import Events, Room, Seats, clean_text, device_of, jdump, save_photo, save_survey  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--white", default="White")
ap.add_argument("--black", default="ai:Leonardo:6")
ap.add_argument("--minutes", type=float, default=15)
ap.add_argument("--increment", type=float, default=10)
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--port", type=int, default=8800)
args = ap.parse_args()
CLOUD = os.environ.get("TABLE_CLOUD") == "1" or sys.platform != "darwin"
RESEARCH = HERE / ".cache" / "research" / "chess"


def parse_seat(spec: str) -> dict:
    if spec.startswith("ai:"):
        _, name, *lvl = spec.split(":")
        return {"name": name or "Leonardo", "ai": True, "level": max(1, min(20, int(lvl[0]) if lvl else 6))}
    return {"name": spec.strip() or "Player", "ai": False}


PLAYERS = {"white": parse_seat(args.white), "black": parse_seat(args.black)}
HUMANS = [p["name"] for p in PLAYERS.values() if not p["ai"]]
SEATS = Seats(HUMANS)
EVENTS = Events()
LOCK = threading.RLock()
G: dict = {}                                    # the game: see new_game()


def new_game() -> None:
    G.clear()
    G.update(board=chess.Board(), started=False, result=None, reason="", draw_offer=None,
             clock={"white": args.minutes * 60, "black": args.minutes * 60}, running_since=None,
             game_id=time.strftime("%Y%m%d-%H%M%S"), lastmove=None)


new_game()


def color_of(name: str) -> str | None:
    return next((c for c, p in PLAYERS.items() if p["name"].lower() == str(name or "").lower()), None)


def side_to_move() -> str:
    return "white" if G["board"].turn == chess.WHITE else "black"


def clocks_now() -> dict:
    c = dict(G["clock"])
    if G["started"] and not G["result"] and G["running_since"]:
        c[side_to_move()] -= time.time() - G["running_since"]
    return {k: round(max(0.0, v), 1) for k, v in c.items()}


def finish(result: str, reason: str) -> None:
    if G["result"]:
        return
    stop_clock()
    G["result"], G["reason"] = result, reason
    EVENTS.emit("chess", kind="over", result=result, reason=reason)
    save_pgn()
    if any(p["ai"] for p in PLAYERS.values()):
        ai = next(p for p in PLAYERS.values() if p["ai"])
        won = (result == "1-0" and PLAYERS["white"]["ai"]) or (result == "0-1" and PLAYERS["black"]["ai"])
        say(ai["name"], random.choice(LINES["won" if won else ("draw" if result == "1/2-1/2" else "lost")]))


def stop_clock() -> None:
    if G["running_since"]:
        G["clock"][side_to_move()] -= time.time() - G["running_since"]
        G["running_since"] = None


def check_flag() -> None:
    if G["started"] and not G["result"]:
        c = clocks_now()
        side = side_to_move()
        if c[side] <= 0:
            G["clock"][side] = 0
            G["running_since"] = None
            other = "black" if side == "white" else "white"
            insufficient = not G["board"].has_insufficient_material(chess.WHITE if other == "white" else chess.BLACK)
            finish(("1-0" if other == "white" else "0-1") if insufficient else "1/2-1/2", f"{PLAYERS[side]['name']} ran out of time")


def apply_move(move: chess.Move, who: str) -> dict:
    b = G["board"]
    san = b.san(move)
    stop_clock()
    G["clock"][side_to_move()] += args.increment
    b.push(move)
    G["lastmove"] = move.uci()
    G["draw_offer"] = None
    EVENTS.emit("chess", kind="move", by=who, san=san, uci=move.uci(), fen=b.fen())
    if b.is_checkmate():
        finish("1-0" if b.turn == chess.BLACK else "0-1", "checkmate")
    elif b.is_stalemate():
        finish("1/2-1/2", "stalemate")
    elif b.is_insufficient_material():
        finish("1/2-1/2", "insufficient material")
    elif b.can_claim_threefold_repetition():
        finish("1/2-1/2", "threefold repetition")
    elif b.can_claim_fifty_moves():
        finish("1/2-1/2", "fifty-move rule")
    else:
        G["running_since"] = time.time()
    return {"san": san}


# ── the AI seat ────────────────────────────────────────────────────────────────────────────────
LINES = {
    "open": ["A new board, a clean page. Shall we?", "I'll sketch an opening and see where it leads."],
    "capture": ["Mine now, thank you.", "A fair trade? We'll see.", "That piece had been bothering me."],
    "check": ["Check.", "Check — mind your king.", "Check. Take your time."],
    "think": ["Hmm. Interesting.", "Let me look at that from the other side of the board."],
    "won": ["Good game. Your middle game was the hardest part for me.", "That's mate. Thank you for the game."],
    "lost": ["Well played. I didn't see that coming.", "You found it. Good game."],
    "draw": ["A draw, then. Neither of us blinked.", "Honours even. Good game."],
}


def say(name: str, text: str) -> None:
    EVENTS.emit("chat", by=name, text=text, ai=True)


VALUES = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330, chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0}


def builtin_move(board: chess.Board, depth: int = 2) -> chess.Move:
    """A small alpha-beta over material and mobility, for when Stockfish isn't installed."""
    def evaluate(b: chess.Board) -> float:
        if b.is_checkmate():
            return -99999
        s = sum(VALUES[p.piece_type] * (1 if p.color == b.turn else -1) for p in b.piece_map().values())
        return s + 2 * b.legal_moves.count()

    def search(b: chess.Board, d: int, alpha: float, beta: float) -> float:
        if d == 0 or b.is_game_over():
            return evaluate(b)
        best = -1e9
        for m in sorted(b.legal_moves, key=lambda m: not b.is_capture(m)):
            b.push(m)
            v = -search(b, d - 1, -beta, -alpha)
            b.pop()
            best, alpha = max(best, v), max(alpha, v)
            if alpha >= beta:
                break
        return best

    moves = list(board.legal_moves)
    random.shuffle(moves)
    scored = []
    for m in moves:
        board.push(m)
        scored.append((-search(board, depth - 1, -1e9, 1e9), m))
        board.pop()
    return max(scored, key=lambda t: t[0])[1]


def ai_move_now(color: str) -> None:
    p = PLAYERS[color]
    with LOCK:
        if G["result"] or not G["started"] or side_to_move() != color:
            return
        board = G["board"].copy()
    time.sleep(random.uniform(0.6, 1.6))                 # a person-like beat, not an instant reply
    move = None
    exe = shutil.which("stockfish") or ("/usr/games/stockfish" if Path("/usr/games/stockfish").exists() else None)
    if exe:
        try:
            import chess.engine
            with chess.engine.SimpleEngine.popen_uci(exe) as eng:
                eng.configure({"Skill Level": p["level"]})
                move = eng.play(board, chess.engine.Limit(time=0.25 + p["level"] * 0.05)).move
        except Exception as e:                           # noqa: BLE001 — fall back to the built-in engine
            print(f"stockfish failed ({type(e).__name__}: {e}); using the built-in engine", flush=True)
    if move is None:
        move = builtin_move(board, depth=2 if p["level"] < 10 else 3)
    with LOCK:
        if G["result"] or side_to_move() != color or G["board"].fen() != board.fen():
            return
        capture, check = G["board"].is_capture(move), G["board"].gives_check(move)
        apply_move(move, p["name"])
    if not G["result"]:
        if check:
            say(p["name"], random.choice(LINES["check"]))
        elif capture and random.random() < 0.5:
            say(p["name"], random.choice(LINES["capture"]))


def maybe_ai() -> None:
    color = side_to_move()
    if PLAYERS[color]["ai"] and G["started"] and not G["result"]:
        threading.Thread(target=ai_move_now, args=(color,), daemon=True).start()


# ── state for pages and persistence ──────────────────────────────────────────────────────────
def public_state() -> dict:
    with LOCK:
        check_flag()
        b = G["board"]
        return {"game": "chess", "fen": b.fen(), "turn": side_to_move(), "started": G["started"],
                "result": G["result"], "reason": G["reason"], "draw_offer": G["draw_offer"],
                "clock": clocks_now(), "increment": args.increment, "lastmove": G["lastmove"],
                "check": b.is_check(), "players": PLAYERS,
                "moves": [m.uci() for m in b.move_stack],
                "san": san_list(b), "legal": [m.uci() for m in b.legal_moves] if not G["result"] else []}


def san_list(b: chess.Board) -> list[str]:
    t, out = chess.Board(), []
    for m in b.move_stack:
        out.append(t.san(m))
        t.push(m)
    return out


def save_pgn() -> None:
    try:
        RESEARCH.mkdir(parents=True, exist_ok=True)
        game = chess.pgn.Game.from_board(G["board"])
        game.headers.update(Event="Divinci Table", Date=time.strftime("%Y.%m.%d"), White=PLAYERS["white"]["name"],
                            Black=PLAYERS["black"]["name"], Result=G["result"] or "*", Termination=G["reason"] or "")
        (RESEARCH / f"{G['game_id']}.pgn").write_text(str(game) + "\n")
    except OSError as e:
        print(f"pgn not saved: {e}", flush=True)


def dump() -> dict:
    with LOCK:
        clk = clocks_now()
        return {"moves": [m.uci() for m in G["board"].move_stack], "started": G["started"], "result": G["result"],
                "reason": G["reason"], "clock": clk, "game_id": G["game_id"], "draw_offer": G["draw_offer"],
                "seats": SEATS.state(), "events": EVENTS.state(), "saved": time.time()}


def load(d: dict) -> None:
    with LOCK:
        new_game()
        for u in d.get("moves") or []:
            G["board"].push_uci(u)
        G.update(started=d.get("started", False), result=d.get("result"), reason=d.get("reason", ""),
                 clock=dict(d.get("clock") or G["clock"]), game_id=d.get("game_id", G["game_id"]),
                 draw_offer=d.get("draw_offer"), lastmove=(G["board"].move_stack[-1].uci() if G["board"].move_stack else None))
        G["running_since"] = time.time() if G["started"] and not G["result"] else None
        SEATS.load(d.get("seats") or {})
        EVENTS.load(d.get("events") or {})
    maybe_ai()


ROOM = Room(dump, load)


# ── HTTP ─────────────────────────────────────────────────────────────────────────────────────
STATIC = {"/survey": "survey.html", "/": "chess.html", "/stage": "chess.html", "/me": "chess.html", "/chess": "chess.html"}
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
            return json.loads(self._body() or b"{}")
        except ValueError:
            return {}

    def _room(self, method: str, p: str) -> bool:
        if not p.startswith("/api/room/"):
            return False
        if not ROOM.token_ok(self.headers.get("X-Room-Token", "")):
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
        if p == "/api/chess":
            return self._send(200, public_state())
        if p == "/api/seat/claims":
            return self._send(200, SEATS.public(device_of(self)))
        if p == "/api/events":
            q = parse_qs(u.query).get("since", ["0"])[0]
            return self._send(200, EVENTS.since(EVENTS.next_id - 1 if q == "latest" else int(q or 0)))
        if p == "/api/voice-config":
            return self._send(200, {"humans": [{"name": n} for n in HUMANS], "ai_players": [{"name": x["name"]} for x in PLAYERS.values() if x["ai"]]})
        if p.startswith("/assets/"):
            f = (HERE / "assets" / p[8:]).resolve()
            if f.is_file() and (HERE / "assets") in f.parents and f.suffix in ASSET_TYPES:
                return self._send(200, body=f.read_bytes(), ctype=ASSET_TYPES[f.suffix])
        if p.startswith("/photos/"):
            f = (RESEARCH / G["game_id"] / "photos" / p[8:]).resolve()
            if f.is_file() and RESEARCH in f.parents and f.suffix in (".jpg", ".png"):
                return self._send(200, body=f.read_bytes(), ctype=ASSET_TYPES[f.suffix])
        if p == "/api/pgn":
            game = chess.pgn.Game.from_board(G["board"])
            game.headers.update(White=PLAYERS["white"]["name"], Black=PLAYERS["black"]["name"], Result=G["result"] or "*")
            return self._send(200, body=str(game).encode(), ctype="text/plain; charset=utf-8")
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
        if p == "/api/chat/photo":
            by = unquote(self.headers.get("X-By", ""))[:30]
            data = self._body()
            if not SEATS.ok(by, unquote(self.headers.get("X-Seat-Key", ""))):
                return self._send(403, {"error": "claim your seat first (👤) to share photos"})
            code, out = save_photo(data, RESEARCH / G["game_id"] / "photos")
            if code == 200:
                EVENTS.emit("chat", by=by, text=clean_text(unquote(self.headers.get("X-Caption", "")), 300) or "📷 a photo of the board", photo=out["photo"])
            return self._send(code, out)
        b = self._json()
        by = str(b.get("by", ""))
        if p == "/api/survey":
            if not SEATS.ok(by, str(b.get("key", ""))):
                return self._send(403, {"error": "claim your seat first (👤)"})
            code, out = save_survey(RESEARCH / G["game_id"], SEATS.canonical(by), b)
            if code == 200:
                EVENTS.emit("survey", by=SEATS.canonical(by))
            return self._send(code, out)
        if p == "/api/chat":
            text = clean_text(b.get("text"), 400)
            if not text:
                return self._send(400, {"error": "say something"})
            if not SEATS.ok(by, str(b.get("key", ""))):
                return self._send(403, {"error": "claim your seat first (👤) to talk"})
            EVENTS.emit("chat", by=SEATS.canonical(by), text=text)
            return self._send(200, {"ok": True})
        # every chess action: a claimed seat, acting for itself
        color = color_of(by)
        if not color or PLAYERS[color]["ai"] or not SEATS.ok(by, str(b.get("key", ""))):
            return self._send(403, {"error": "claim your seat first (👤): only you can move your pieces", "need_seat": True})
        with LOCK:
            check_flag()
            if p == "/api/chess/start":
                if G["started"]:
                    return self._send(200, public_state())
                G["started"], G["running_since"] = True, time.time()
                EVENTS.emit("chess", kind="start", by=by)
                ai = next((x for x in PLAYERS.values() if x["ai"]), None)
                if ai:
                    say(ai["name"], random.choice(LINES["open"]))
                out = public_state()
            elif G["result"]:
                return self._send(409, {**public_state(), "error": f"the game is over ({G['result']}, {G['reason']})"})
            elif p == "/api/chess/move":
                if not G["started"]:
                    return self._send(409, {**public_state(), "error": "press Start first — the clocks begin with White"})
                if side_to_move() != color:
                    return self._send(409, {**public_state(), "error": f"it's {PLAYERS[side_to_move()]['name']}'s move"})
                raw = str(b.get("move", "")).strip()
                try:
                    mv = G["board"].parse_uci(raw) if len(raw) in (4, 5) and raw[1].isdigit() else G["board"].parse_san(raw)
                except ValueError:
                    return self._send(400, {**public_state(), "error": f"'{raw[:12]}' isn't a legal move here"})
                apply_move(mv, PLAYERS[color]["name"])
                out = public_state()
            elif p == "/api/chess/resign":
                finish("0-1" if color == "white" else "1-0", f"{PLAYERS[color]['name']} resigned")
                out = public_state()
            elif p == "/api/chess/draw":
                act = str(b.get("action", "offer"))
                other = "black" if color == "white" else "white"
                if act == "offer":
                    G["draw_offer"] = color
                    EVENTS.emit("chess", kind="draw-offer", by=by)
                    if PLAYERS[other]["ai"]:                 # the AI accepts only a level position, late
                        bal = sum(VALUES[x.piece_type] * (1 if x.color == chess.WHITE else -1) for x in G["board"].piece_map().values())
                        if abs(bal) < 150 and G["board"].fullmove_number > 30:
                            finish("1/2-1/2", "draw agreed")
                        else:
                            G["draw_offer"] = None
                            say(PLAYERS[other]["name"], "Thank you, but I'd like to play on.")
                elif act == "accept" and G["draw_offer"] == other:
                    finish("1/2-1/2", "draw agreed")
                else:
                    G["draw_offer"] = None
                out = public_state()
            else:
                return self._send(404, {"error": "not found"})
        maybe_ai()
        return self._send(200, out)


if __name__ == "__main__":
    srv = ThreadingHTTPServer((args.host, args.port), H)
    srv.daemon_threads = True
    print(f"chess: {PLAYERS['white']['name']} (white) vs {PLAYERS['black']['name']} (black), "
          f"{args.minutes:g}+{args.increment:g} — http://localhost:{args.port}/", flush=True)
    srv.serve_forever()
