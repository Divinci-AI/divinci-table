#!/bin/bash
# one photo every N seconds (default 60) into ./watch/, until /tmp/printer-watch-stop exists
cd "$(dirname "$0")"; mkdir -p watch; rm -f /tmp/printer-watch-stop
while [ ! -e /tmp/printer-watch-stop ]; do
  ../camera-snap.sh "watch/$(date +%H%M%S).jpg" 1400 >/dev/null 2>&1
  sleep "${1:-60}"
done
