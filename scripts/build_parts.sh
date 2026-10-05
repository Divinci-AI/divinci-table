#!/usr/bin/env bash
# Render the printed parts' STLs from their OpenSCAD sources (hardware/gantry/parts/*.scad).
# Needs OpenSCAD 2025+ (`brew install --cask openscad@snapshot`; the plain cask is disabled on macOS).
# Edit a part's parameters (card size, sleeve thickness, bed edge) at the top of its .scad, then re-run.
set -euo pipefail
cd "$(dirname "$0")/../hardware/gantry/parts"
mkdir -p stl
render() {                                    # name, output, extra args…  (output captured: grep -q under
  local name=$1 out=$2; shift 2               # pipefail would kill openscad with SIGPIPE and "fail" it)
  local log; log=$(openscad --backend=manifold "$@" -o "$out" 2>&1) || { echo "$name: openscad failed" >&2; echo "$log" >&2; exit 1; }
  grep -q "Status: *NoError" <<<"$log" || { echo "$name: not a clean solid" >&2; echo "$log" >&2; exit 1; }
  echo "  $out"
}
for f in magnet_swivel deck_box discard_chute privacy_wall; do render "$f" "stl/$f.stl" "$f.scad"; done
render "deck_box follower" stl/deck_box_follower.stl -D 'part="follower"' deck_box.scad
