#!/usr/bin/env bash
# Start so1_server.py and kill it if system free memory drops below FLOOR% (default 15),
# so a model load can't push this shared, swap-full laptop into a freeze.
FLOOR=${FLOOR:-15}
~/.venvs/so1/bin/python -X faulthandler "$(dirname "$0")/so1_server.py" "$@" &
PID=$!
echo "server pid $PID, memory floor ${FLOOR}%"
while kill -0 $PID 2>/dev/null; do
  FREE=$(memory_pressure | awk -F': ' '/free percentage/{gsub("%","",$2); print $2}')
  if [ -n "$FREE" ] && [ "$FREE" -lt "$FLOOR" ]; then
    echo "WATCHDOG: free memory ${FREE}% < ${FLOOR}% — killing server $PID"; kill $PID; sleep 2; kill -9 $PID 2>/dev/null
    exit 3
  fi
  sleep 2
done
wait $PID; echo "server exited $?"
