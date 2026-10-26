#!/usr/bin/env python3
"""Serve ONE directory, read-only, on loopback: the only thing a broadcast renderer is ever pointed at.

    python3 harness/broadcast/serve_static.py DIR [--port 8980]

It is its own process with its own directory: it holds no table token and has no route to the table server, so a broadcast page
cannot reach `/api/ai/hand`, the brain token or a control POST. Exact allowlist of names, GET/HEAD only, no directory listing,
no path traversal, no symlink following outside DIR.
"""
import argparse
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ALLOWED = {"replay.html", "replay.json", "favicon.ico"}


def make_handler(root: Path):
    root = root.resolve()

    class H(BaseHTTPRequestHandler):
        def _serve(self, head=False):
            path = self.path.split("?", 1)[0].split("#", 1)[0]
            name = "replay.html" if path in ("/", "/broadcast") else path.lstrip("/")
            if name not in ALLOWED or "/" in name or ".." in name:
                return self._err(404)
            f = (root / name).resolve()
            if f.parent != root or not f.is_file():
                return self._err(404)
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:")
            self.end_headers()
            if not head:
                self.wfile.write(data)

        def _err(self, code):
            self.send_response(code); self.send_header("Content-Length", "0"); self.end_headers()

        def do_GET(self): self._serve()
        def do_HEAD(self): self._serve(head=True)
        def do_POST(self): self._err(405)
        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_POST

        def log_message(self, *a): pass
    return H


def serve(root: Path, port=0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(Path(root)))
    return srv


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("dir"); ap.add_argument("--port", type=int, default=8980); a = ap.parse_args()
    if not Path(a.dir).is_dir():
        sys.exit("not a directory")
    print(f"serving {a.dir} read-only on http://127.0.0.1:{a.port}/", flush=True)
    serve(Path(a.dir), a.port).serve_forever()
