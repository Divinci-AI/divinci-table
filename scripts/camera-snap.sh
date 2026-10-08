#!/bin/bash
# Take one still from the USB-connected Canon (gphoto2), working around macOS's ptpcamerad, which grabs the camera and
# makes gphoto2 fail with "Could not claim the USB device". macOS restarts the helper within a second of every kill (even
# after `launchctl disable`, which did not hold), so a single kill before the shot loses the race. This keeps killing it
# every 10 ms for the duration of the attempt, which wins. Nothing on the Mac is changed permanently. Prints the JPEG path.
#
#   scripts/camera-snap.sh out.jpg            full-size frame
#   scripts/camera-snap.sh out.jpg 1400       also writes out-small.jpg, longest side 1400 px (for looking at)
set -u
out="${1:?usage: camera-snap.sh out.jpg [small-size]}"
small="${2:-}"
rm -f "$out"
for try in 1 2 3 4 5 6; do
  ( while true; do pkill -9 -x ptpcamerad 2>/dev/null; sleep 0.01; done ) &
  killer=$!
  sleep 0.5
  if gphoto2 --capture-image-and-download --filename "$out" --force-overwrite >/dev/null 2>&1 && [ -s "$out" ]; then
    kill "$killer" 2>/dev/null; wait "$killer" 2>/dev/null
    if [ -n "$small" ]; then sips -Z "$small" "$out" --out "${out%.jpg}-small.jpg" >/dev/null 2>&1; fi
    echo "$out"
    exit 0
  fi
  kill "$killer" 2>/dev/null; wait "$killer" 2>/dev/null
  sleep 1
done
echo "camera-snap: could not take a photo after 6 tries (camera off, asleep, or another app holds it)" >&2
exit 1
