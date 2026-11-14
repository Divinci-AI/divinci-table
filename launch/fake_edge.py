#!/usr/bin/env python3
"""A stand-in for Cloudflare's edge, for tests and rehearsals: one loopback HTTP server that forwards a request to whatever table the
stand-in tunnel named in its Host header leads to (launch/fake_tunnel.py lists itself in --edge-dir while it runs).

    python3 launch/fake_edge.py --edge-dir <dir> [--port N]        prints "edge on http://127.0.0.1:<port>"
    TUNNEL_PROBE_VIA=http://127.0.0.1:<port>                        what launch/tunnels.py then probes instead of the real address

Like the real edge it adds the headers a tunnelled request carries (CF-Connecting-IP, X-Forwarded-For) and keeps the tunnel's own Host
name, so the table treats the request as a remote one. An address no running stand-in tunnel has listed answers 530 "error code: 1033",
which is what Cloudflare answers for a quick tunnel that is gone."""
from __future__ import annotations

import argparse
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def make_handler(edge_dir: Path, seen: list | None = None):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _reply(self, code: int, body: bytes, ctype: str = "application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _forward(self, method: str):
            host = (self.headers.get("Host") or "").split(":")[0].lower()
            if seen is not None:
                seen.append((method, host, self.path.split("?")[0]))
            try:
                port = int(json.loads((edge_dir / f"{host}.json").read_text())["port"])
            except (OSError, ValueError, KeyError):
                return self._reply(530, b"error code: 1033", "text/plain")
            n = int(self.headers.get("Content-Length") or 0)
            data = self.rfile.read(n) if n else None
            h = {k: v for k, v in self.headers.items() if k.lower() in ("content-type", "x-seat-key", "x-seat-driver", "user-agent", "accept")}
            h.update({"Host": self.headers.get("Host") or "", "CF-Connecting-IP": "203.0.113.7", "X-Forwarded-For": "203.0.113.7"})
            try:
                with LOCAL.open(urllib.request.Request(f"http://127.0.0.1:{port}{self.path}", data=data, method=method, headers=h), timeout=15) as r:
                    return self._reply(r.status, r.read(), r.headers.get("Content-Type") or "application/json")
            except urllib.error.HTTPError as e:
                return self._reply(e.code, e.read(), e.headers.get("Content-Type") or "application/json")
            except (urllib.error.URLError, OSError):
                return self._reply(502, b"error code: 502", "text/plain")     # the tunnel is up, the table behind it is not

        def do_GET(self):
            self._forward("GET")

        def do_POST(self):
            self._forward("POST")
    return H


def serve(edge_dir, port: int = 0, seen: list | None = None) -> ThreadingHTTPServer:
    """Start the edge on 127.0.0.1 (a free port when 0) in a daemon thread. `.server_address[1]` is the port; `.shutdown()` stops it."""
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(Path(edge_dir), seen))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edge-dir", required=True)
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(Path(a.edge_dir)))
    print(f"edge on http://127.0.0.1:{srv.server_address[1]}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
