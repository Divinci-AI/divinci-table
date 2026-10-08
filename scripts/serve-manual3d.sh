#!/bin/bash
# Serve the 3D assembly manual at http://localhost:8765/manual3d/ (the STL files load over http, not file://).
cd "$(dirname "$0")/../hardware/gantry" && echo "open http://localhost:8765/manual3d/" && exec python3 -m http.server 8765 --bind 127.0.0.1
