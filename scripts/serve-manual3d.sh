#!/bin/bash
# Serve the 3D assembly manual at http://localhost:8765/manual3d/ (the STL files load over http, not file://).
# Sends Cache-Control: no-store so an edited manual is never shown from a stale browser cache.
cd "$(dirname "$0")/../hardware/gantry" || exit 1
echo "open http://localhost:8765/manual3d/"
exec python3 -c '
import http.server
class NoStore(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()
http.server.test(HandlerClass=NoStore, port=8765, bind="127.0.0.1")
'
