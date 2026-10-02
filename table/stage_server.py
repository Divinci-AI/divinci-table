#!/usr/bin/env python3
"""The 3D avatar stage, served beside a game that is already running — no restart, no lost game.

    table/stage_server.py --human "Michael|Kilo, Apogee Mind" --human "Sam|Captain N'ghathrod"
    open http://localhost:8801/stage

It reads only public state from the table server (life, AI hand SIZES, the event stream) and keeps
the humans' hand sizes itself. A server started after this change serves /stage on its own port too.
"""
import argparse
import json
import urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

HERE = Path(__file__).parent
ap = argparse.ArgumentParser()
ap.add_argument("--table", default="http://127.0.0.1:8800")
ap.add_argument("--human", action="append", default=[], help='"Name|Commander"')
ap.add_argument("--port", type=int, default=8801)
ap.add_argument("--host", default="127.0.0.1")
args = ap.parse_args()
HUMANS = [dict(zip(("name", "commander"), (x.strip() for x in h.split("|", 1)))) for h in args.human]
HAND_N = {h["name"]: 7 for h in HUMANS}
VENDOR = {"three.module.min.js", "three.core.min.js"}


def get(path):
    with urllib.request.urlopen(args.table + path, timeout=5) as r:
        return json.loads(r.read())


def stage():
    life = get("/api/life")
    seats = [{"name": h["name"], "commander": h["commander"], "kind": "human",
              "life": life.get(h["name"]), "hand": HAND_N[h["name"]]} for h in HUMANS]
    for name in life:
        if name in HAND_N:
            continue
        s = get(f"/api/ai/state?seat={urllib.parse.quote(name)}")       # public fields only are used
        seats.append({"name": name, "commander": s.get("commander"), "kind": "ai",
                      "life": s.get("life"), "hand": s.get("hand")})
    return {"seats": seats}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj=None, body=None, ctype="application/json"):
        body = body if body is not None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        try:
            if p in ("/", "/stage"):
                return self._send(200, body=(HERE / "stage.html").read_bytes(), ctype="text/html; charset=utf-8")
            if p.startswith("/vendor/") and p[8:] in VENDOR:
                return self._send(200, body=(HERE / "vendor" / p[8:]).read_bytes(), ctype="text/javascript; charset=utf-8")
            if p == "/api/stage":
                return self._send(200, stage())
            if p == "/api/events":
                return self._send(200, get(self.path))
        except OSError as e:
            return self._send(502, {"error": f"table server unreachable: {e}"})
        self._send(404, {"error": "not here"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        b = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/stage/hand":
            p = next((h for h in HAND_N if h.lower() == str(b.get("player", "")).lower()), None)
            if not p:
                return self._send(400, {"error": "humans only"})
            HAND_N[p] = max(0, min(30, b["n"] if isinstance(b.get("n"), int) else HAND_N[p] + int(b.get("delta", 0))))
            return self._send(200, {"player": p, "hand": HAND_N[p]})
        self._send(404, {"error": "not here"})


import urllib.parse  # noqa: E402  (used in stage())

print(f"stage: http://localhost:{args.port}/stage   (reading {args.table})", flush=True)
ThreadingHTTPServer((args.host, args.port), H).serve_forever()
