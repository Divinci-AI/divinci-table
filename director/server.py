"""The director process: watches the tables, decides what is on screen, and serves the pages OBS shows.

    python3 -m director --config director.json [--port 8930] [--i-know-live-human-hands]

Pages (this laptop only):
    /director            the rotating broadcast: ONE browser source in OBS. No key and no token in its URL.
    /pov?seat=Claude     one seat's point of view, fixed (add &table=Table%202 if two tables have that seat name)
    /control#t=<token>   the operator's keys (pin a seat, hide hands, blank). The token is in the fragment: never sent in a URL, never logged.

Who may read what:
    The server binds 127.0.0.1 and nothing else, and refuses any request that came through a tunnel or proxy (forwarding headers) or
    under another Host name. /api/program and /api/pov need the session cookie the pages are given (HttpOnly) or the control token;
    /api/control needs the token. A request without them is refused and COUNTED (the control page shows the count), so a bot that
    starts reading the director shows up. On one laptop under one user this is a fence, not a wall: any process running as the same
    user could read the seat key files themselves."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import backends, config as cfg, speech
from .commentary import Budget, Commentary
from .rotation import Rotation
from .watch import TableWatch

HERE = Path(__file__).resolve().parent
PROXY_HEADERS = ("X-Forwarded-For", "X-Forwarded-Host", "X-Real-IP", "CF-Connecting-IP", "CF-Ray", "Forwarded", "Via", "X-Forwarded-Proto")


class Director:
    def __init__(self, c: cfg.Config, clients: dict | None = None, clock=time.time, backend=None, voices=None, state_dir: str | None = None):
        self.c, self.clock = c, clock
        self.watches = [TableWatch(t, c, (clients or {}).get(t.name), clock) for t in c.tables]
        slots = [(i, s.name) for i, t in enumerate(c.tables) for s in t.seats if s.show]
        self.rot = Rotation(slots, c.dwell_secs, c.min_hold_secs, c.action_hold_secs, c.recap_secs, now=clock())
        self.trig = {i: 0 for i in range(len(self.watches))}
        self.primed = {i: None for i in range(len(self.watches))}
        self.lock = threading.RLock()
        self.unauth = {"count": 0, "last_ua": None, "last_path": None}
        self.speaker = None
        self.spoken: dict = {}
        cm = c.commentary or {}
        if backend is None and cm.get("backend", "off") != "off":
            backend, voices = backends.make(cm)
        self.com = None
        if backend is not None:
            self.com = Commentary(backend, voices, self.watches, Budget(float(cm.get("usd_per_hour", 1.0)), float(cm.get("usd_per_stream", 4.0)),
                                                                        int(cm.get("tokens_per_hour", 1_500_000)), cm.get("prices")),
                                  policy=str(cm.get("private", "auto")), min_gap=float(cm.get("min_gap_secs", 14)), idle_gap=float(cm.get("idle_gap_secs", 120)),
                                  stale_secs=float(cm.get("stale_secs", 30)), caption_secs=float(cm.get("caption_secs", 10)),
                                  cards_json=cm.get("cards_json"), kill_file=cm.get("kill_file") or (str(Path(state_dir) / "commentary.off") if state_dir else None),
                                  clock=clock)
            sp = cm.get("speech") or {}
            if sp.get("enabled") and state_dir:
                self.speaker = speech.Say(str(Path(state_dir) / "speech"), sp.get("voices"))

    # ---- the loop ------------------------------------------------------------------------------------------------------------------
    def poll(self):
        for i, w in enumerate(self.watches):
            w.poll()
            with self.lock:
                if self.primed[i] != w.game_id:               # a table first seen (or a new game): what already happened is not news
                    evs = w.shown_events()
                    self.trig[i] = max([0] + [e["id"] for e in evs])
                    self.primed[i] = w.game_id
                    if w.over:
                        self.rot.recapped.add(w.game_id)
                    continue
                for eid, seat, kind, weight in w.triggers(self.trig[i]):
                    self.trig[i] = max(self.trig[i], eid)
                    self.rot.on_trigger(self.clock(), i, seat, kind, weight, game_id=w.game_id)

    def step_commentary(self):
        if self.com is None:
            return None
        out = self.com.step()
        if out == "line" and self.speaker is not None:
            line = self.com.lines[-1]
            if speech.may_speak(line, any(w.t.human_involved and not w.over for w in self.watches)):
                f = self.speaker.render(line)
                if f:
                    self.spoken[line["id"]] = f
        return out

    # ---- what the pages get ----------------------------------------------------------------------------------------------------------
    def _table_index(self, table: str | None, seat: str | None):
        for i, t in enumerate(self.c.tables):
            if (table is None or t.name == table) and (seat is None or any(s.name == seat for s in t.seats)):
                return i
        return None

    def _pip(self, w) -> dict:
        v = w.view()
        return {"name": v["name"], "turn": v["turn"], "step": v["step"], "delay": v["delay"], "over": v["over"], "status": v["status"],
                "seats": [{"name": s["name"], "life": s.get("life"), "hand": s.get("hand"), "kind": s["kind"],
                           "board": [p.get("name") or "face-down" for p in s.get("permanents") or [] if not str(p.get("type") or "").startswith("Basic Land")][:8],
                           "lands": sum(1 for p in s.get("permanents") or [] if "Land" in str(p.get("type") or ""))} for s in v["seats"]]}

    def _stage(self, ti: int, seat: str | None, mode: str) -> dict:
        w = self.watches[ti]
        v = w.view()
        me = next((s for s in v["seats"] if s["name"] == seat), None) if seat else None
        opp = [s for s in v["seats"] if s is not me]
        for s in opp:                                          # the POV is ONE seat's: the other seats' private info is not on this view
            s["private"] = {"shown": False, "reason": "not the featured seat", "hand": None, "thinking": []}
        lower = None
        if me:
            lower = {"name": me["name"], "kind": me["kind"], "player": me.get("player") or ("a person" if me["kind"] == "human" else "AI"),
                     "table": v["name"], "delay": v["delay"], "hand": "shown" if me["private"]["shown"] else me["private"]["reason"],
                     "game": (v.get("match") or {}).get("game"), "score": (v.get("match") or {}).get("score") or (v.get("match") or {}).get("wins")}
        return {"table": v["name"], "status": v["status"], "turn": v["turn"], "step": v["step"], "steps": v["steps"], "delay": v["delay"],
                "over": v["over"], "me": me, "opponents": opp, "log": v["log"], "lower_third": lower, "match": v.get("match"),
                "recap": v["recap"] if (mode == "recap" or v["over"]) else None}

    def program(self) -> dict:
        with self.lock:
            now = self.clock()
            slot, mode = self.rot.featured(now)
            out = {"now": round(now, 2), "mode": mode, "names_only": bool(self.c.names_only or not self.c.art_dir),
                   "slots": [{"n": k + 1, "table": self.c.tables[i].name, "seat": s} for k, (i, s) in enumerate(self.rot.slots)],
                   "hidden": sorted({f"{w.t.name}/{s}" for w in self.watches for s in w.hidden})}
            if mode == "blank" or slot is None:
                out.update(stage=None, pip=[])
            else:
                ti, seat = slot
                out["stage"] = self._stage(ti, seat, mode)
                out["pip"] = [self._pip(w) for i, w in enumerate(self.watches) if i != ti]
            self._commentary_into(out, now)
            return out

    def pov(self, seat: str, table: str | None = None) -> dict | None:
        with self.lock:
            ti = self._table_index(table, seat)
            if ti is None:
                return None
            out = {"now": round(self.clock(), 2), "mode": "pov", "names_only": bool(self.c.names_only or not self.c.art_dir), "slots": [],
                   "hidden": sorted({f"{w.t.name}/{s}" for w in self.watches for s in w.hidden}),
                   "stage": self._stage(ti, seat, "pov"), "pip": [self._pip(w) for i, w in enumerate(self.watches) if i != ti]}
            self._commentary_into(out, self.clock())
            return out

    def _commentary_into(self, out: dict, now: float):
        if self.com is None:
            out.update(caption=None, commentary={"state": "off"})
            return
        cap = self.com.caption(now)
        if cap and cap["id"] in self.spoken:
            cap = {**cap, "audio": f"/speech/{self.spoken[cap['id']]}"}
        st = self.com.status(now)
        out.update(caption=cap, commentary={"state": st["state"], "voices": st["voices"]})

    def control(self, b: dict) -> tuple[int, dict]:
        cmd = b.get("cmd")
        with self.lock:
            now = self.clock()
            if cmd == "pin":
                ti = self._table_index(b.get("table"), b.get("seat")) if b.get("seat") else None
                slot = self.rot.slots[int(b["n"]) - 1] if str(b.get("n", "")).isdigit() and 0 < int(b["n"]) <= len(self.rot.slots) else ((ti, b.get("seat")) if ti is not None else None)
                if slot is None or not self.rot.pin(slot):
                    return 404, {"error": "no such seat on the rotation"}
            elif cmd == "unpin":
                self.rot.unpin(now)
            elif cmd == "next":
                self.rot.next(now)
            elif cmd == "blank":
                self.rot.set_blank(bool(b.get("on", True)))
            elif cmd in ("hide", "unhide"):                    # hide: every hand (or one seat's) off the stream NOW, and its buffer thrown away
                for w in self.watches:
                    if b.get("table") in (None, w.t.name):
                        for s in ([b["seat"]] if b.get("seat") else [x.name for x in w.t.seats]):
                            if s in w.buf:
                                w.hide(s, cmd == "hide")
            elif cmd == "commentary":
                if self.com is None:
                    return 409, {"error": "commentary is not configured"}
                self.com.killed = not bool(b.get("on", False))
            else:
                return 400, {"error": "cmd: pin, unpin, next, blank, hide, unhide, commentary"}
            return 200, self.control_state()

    def control_state(self) -> dict:
        with self.lock:
            slot, mode = self.rot.featured(self.clock())
            return {"mode": mode, "featured": None if not slot else {"table": self.c.tables[slot[0]].name, "seat": slot[1]},
                    "slots": [{"n": k + 1, "table": self.c.tables[i].name, "seat": s} for k, (i, s) in enumerate(self.rot.slots)],
                    "tables": [{"name": w.t.name, "status": w.status, "delay": w.delay_label(), "game_id": w.game_id, "requests": w.c.requests,
                                "seats": [{"name": s.name, "kind": s.kind, "read": w.vis[s.name].read, "reason": w.vis[s.name].reason,
                                           "hidden": s.name in w.hidden, "consent": bool(w.consent.get(s.name)), "private_reads": w.private_reads[s.name]}
                                          for s in w.t.seats]} for w in self.watches],
                    "commentary": self.com.status() if self.com else {"state": "off"},
                    "unknown_readers": dict(self.unauth)}


def make_handler(d: Director, token: str, session: str):
    art_root = Path(d.c.art_dir).expanduser().resolve() if d.c.art_dir and not d.c.names_only else None
    speech_root = d.speaker.dir.resolve() if d.speaker else None

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "director"

        def log_message(self, *a):                             # no access log: nothing a request carries is worth writing down
            pass

        def _send(self, code, obj=None, body=None, ctype="application/json", cookie=False):
            data = body if body is not None else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if cookie:
                self.send_header("Set-Cookie", f"dsession={session}; HttpOnly; SameSite=Strict; Path=/")
            self.end_headers()
            self.wfile.write(data)

        def _local(self) -> bool:
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return False
            if any(self.headers.get(h) for h in PROXY_HEADERS):
                return False                                   # a tunnel pointed at this port by mistake: it gets nothing
            host = (self.headers.get("Host") or "").split(":")[0].lower()
            return host in ("127.0.0.1", "localhost")

        def _authed(self, need_token=False) -> bool:
            if secrets.compare_digest(self.headers.get("X-Director-Token", ""), token):
                return True
            if need_token:
                return False
            ck = dict(p.strip().split("=", 1) for p in (self.headers.get("Cookie") or "").split(";") if "=" in p)
            return secrets.compare_digest(ck.get("dsession", ""), session)

        def _refuse(self, path):
            d.unauth["count"] += 1
            d.unauth["last_ua"] = (self.headers.get("User-Agent") or "")[:80]
            d.unauth["last_path"] = path[:60]
            self._send(403, {"error": "the director's data is for its own pages"})

        def do_GET(self):
            try:
                self._get()
            except Exception as e:  # noqa: BLE001  (an error page says a kind of error, never a value)
                print(f"director: GET failed: {type(e).__name__}", file=sys.stderr, flush=True)
                self._send(500, {"error": "the director had a problem with that request"})

        def _get(self):
            if not self._local():
                return self._send(403, {"error": "this laptop only"})
            u = urlparse(self.path)
            p, q = u.path, parse_qs(u.query)
            if p in ("/director", "/pov"):
                return self._send(200, body=(HERE / "pages" / "stage.html").read_bytes(), ctype="text/html; charset=utf-8", cookie=True)
            if p == "/control":
                return self._send(200, body=(HERE / "pages" / "control.html").read_bytes(), ctype="text/html; charset=utf-8")
            if p in ("/assets/stage.js", "/assets/stage.css"):
                return self._send(200, body=(HERE / "pages" / p.rsplit("/", 1)[1]).read_bytes(),
                                  ctype="text/javascript; charset=utf-8" if p.endswith(".js") else "text/css; charset=utf-8")
            if p.startswith("/art/"):
                if p == "/art/index.js" and art_root is None:    # names only: the page is told there is no art, and falls back to text cards
                    return self._send(200, body=b"window.CARD_ART={};", ctype="text/javascript; charset=utf-8")
                if art_root is not None:
                    fp = (art_root / p[len("/art/"):]).resolve()
                    if fp.is_file() and art_root in fp.parents and fp.suffix in (".jpg", ".js"):
                        return self._send(200, body=fp.read_bytes(), ctype="image/jpeg" if fp.suffix == ".jpg" else "text/javascript; charset=utf-8")
                if p == "/art/index.js":
                    return self._send(200, body=b"window.CARD_ART={};", ctype="text/javascript; charset=utf-8")
                return self._send(404, {"error": "no such card art"})
            if p.startswith("/speech/") and speech_root is not None:
                if not self._authed():
                    return self._refuse(p)
                fp = (speech_root / p[len("/speech/"):]).resolve()
                if fp.is_file() and speech_root in fp.parents and fp.suffix == ".wav":
                    return self._send(200, body=fp.read_bytes(), ctype="audio/wav")
                return self._send(404, {"error": "no such line"})
            if p == "/api/program":
                if not self._authed():
                    return self._refuse(p)
                return self._send(200, d.program())
            if p == "/api/pov":
                if not self._authed():
                    return self._refuse(p)
                out = d.pov((q.get("seat") or [""])[0], (q.get("table") or [None])[0])
                return self._send(200, out) if out else self._send(404, {"error": "no such seat"})
            if p == "/api/control/state":
                if not self._authed(need_token=True):
                    return self._refuse(p)
                return self._send(200, d.control_state())
            return self._send(404, {"error": "not a director page"})

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(n) if 0 < n <= 20_000 else b""
                if not self._local():
                    return self._send(403, {"error": "this laptop only"})
                origin = self.headers.get("Origin")
                if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
                    return self._send(403, {"error": "not from another site"})
                if urlparse(self.path).path != "/api/control":
                    return self._send(404, {"error": "not a director page"})
                if not self._authed(need_token=True):
                    return self._refuse("/api/control")
                code, out = d.control(json.loads(raw or b"{}"))
                return self._send(code, out)
            except Exception as e:  # noqa: BLE001
                print(f"director: POST failed: {type(e).__name__}", file=sys.stderr, flush=True)
                self._send(400, {"error": "bad request"})
    return H


def serve(d: Director, port: int, token: str, stop: threading.Event | None = None):
    """Bind 127.0.0.1 only (never configurable), start the poll and commentary threads, serve until `stop` is set."""
    stop = stop or threading.Event()
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(d, token, secrets.token_urlsafe(24)))
    srv.daemon_threads = True

    def poller():
        while not stop.is_set():
            try:
                d.poll()
            except Exception as e:  # noqa: BLE001
                print(f"director: poll failed: {type(e).__name__}", file=sys.stderr, flush=True)
            stop.wait(d.c.poll_secs)

    def talker():
        while not stop.is_set():
            try:
                d.step_commentary()
            except Exception as e:  # noqa: BLE001
                print(f"director: commentary failed: {type(e).__name__}", file=sys.stderr, flush=True)
            stop.wait(1.0)
    for f in (poller, talker, srv.serve_forever):
        threading.Thread(target=f, daemon=True).start()
    return srv, stop


def main(argv=None):
    ap = argparse.ArgumentParser(prog="director", description="The broadcast director (docs/broadcast-director.md).")
    ap.add_argument("--config", required=True, help="JSON: tables, seats, key FILE paths, delays, commentary")
    ap.add_argument("--port", type=int, default=8930)
    ap.add_argument("--state-dir", default=str(Path.home() / ".divinci" / "director"), help="where the control token (0600) and the kill switch live")
    ap.add_argument("--i-know-live-human-hands", action="store_true",
                    help="allow a table with a PERSON at it to be configured with no delay. Their opponent's cards go out live. You were warned.")
    a = ap.parse_args(argv)
    if a.port < 1024:
        sys.exit("--port: 1024 or higher")
    try:
        c = cfg.load(a.config, i_know_live=a.i_know_live_human_hands)
    except cfg.ConfigError as e:
        sys.exit(f"config: {e}")
    sd = Path(a.state_dir).expanduser()
    sd.mkdir(parents=True, exist_ok=True)
    os.chmod(sd, 0o700)
    token = secrets.token_urlsafe(24)
    tf = sd / "control.token"
    fd = os.open(tf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    d = Director(c, state_dir=str(sd))
    srv, stop = serve(d, a.port, token)
    print(f"director on http://127.0.0.1:{a.port}/director  (OBS browser source: exactly that URL)", flush=True)
    print(f"control page: python3 -m director.ctl --state-dir {sd} --port {a.port}   (opens it with the token; the token is in {tf}, 0600)", flush=True)
    for t in c.tables:
        turns, secs = c.delay_for(t)
        print(f"  {t.name}: {'a person plays here' if t.human_involved else 'all AI'}; private info {('LIVE' if turns == 0 and secs == 0 else f'{turns} turn(s) + {secs:g}s behind')}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop.set(); srv.shutdown()


if __name__ == "__main__":
    main()
