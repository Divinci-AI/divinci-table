#!/bin/bash
# Take one still from the USB-connected Canon (gphoto2), working around macOS's ptpcamerad, which grabs the camera and
# makes gphoto2 fail with "Could not claim the USB device". The helper is stopped right before each attempt (it is
# restarted by macOS on its own, so nothing is changed permanently). Prints the path of the saved JPEG.
#
#   scripts/camera-snap.sh out.jpg            full-size frame
#   scripts/camera-snap.sh out.jpg 1400       also writes out-small.jpg, longest side 1400 px (for looking at)
set -u
out="${1:?usage: camera-snap.sh out.jpg [small-size]}"
small="${2:-}"
rm -f "$out"
for try in 1 2 3 4 5 6; do
  killall ptpcamerad 2>/dev/null
  sleep 0.4
  if gphoto2 --capture-image-and-download --filename "$out" --force-overwrite >/dev/null 2>&1 && [ -s "$out" ]; then
    if [ -n "$small" ]; then sips -Z "$small" "$out" --out "${out%.jpg}-small.jpg" >/dev/null 2>&1; fi
    echo "$out"
    exit 0
  fi
  sleep 1
done
echo "camera-snap: could not take a photo after 6 tries (camera off, asleep, or another app holds it)" >&2
exit 1
