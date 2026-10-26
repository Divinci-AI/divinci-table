#!/usr/bin/env python3
"""A fake OpenAI-compatible router on loopback, for exercising the whole pilot path with no Orin and no model.

    python3 harness/fake_router.py --mode random --seed 3        # prints the base URL, serves until killed
modes:  first (always option 1) | random (a seeded random legal option) | garbage (not JSON) | wrongmodel | http500 | slow (sleeps 5 s)
As a library: `srv = start(mode, seed)`; `srv.base` is the URL for JETSON_BASE, `srv.seen` the request bodies it got.
A random-legal pilot is also a free fuzzer for the table: it takes every legal option the model might, with no model.
"""
import argparse, json, random, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer


def start(mode="first", seed=0):
    rng = random.Random(seed)
    seen = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):                                    # /health, like the real router
            self._send(200 if self.path == "/health" else 404, b'{"status":"ok"}')

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(body)
            if mode == "http500":
                return self._send(500, b'{"error":"boom"}')
            if mode == "slow":
                time.sleep(5)
            n = len(body["response_format"]["json_schema"]["schema"]["properties"]["choice"]["enum"])
            if mode == "garbage":
                content = "I think option two."
            else:
                content = json.dumps({"choice": rng.randint(1, n) if mode == "random" else 1, "say": ""})
            self._send(200, json.dumps({"model": "other-model" if mode == "wrongmodel" else body["model"], "choices": [{"message": {"content": content}}],
                                        "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode())

        def _send(self, code, data):
            self.send_response(code); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.base = f"http://127.0.0.1:{srv.server_port}/v1"
    srv.seen = seen
    return srv


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--mode", default="first"); ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(); s = start(a.mode, a.seed); print(s.base, flush=True)
    while True:
        time.sleep(3600)
