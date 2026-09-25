#!/usr/bin/env bash
# Start so1_server.py and kill it if system free memory drops below FLOOR% (default 15),
# so a model load can't push this shared, swap-full laptop into a freeze.
FLOOR=${FLOOR:-15}
# transformers 5 loads weight shards on a 4-thread pool; on MPS that segfaulted at
# "Loading weights 0/N" in 4 of 7 loads. Sequential loading: 3/3 clean (2026-09-24).
export HF_DEACTIVATE_ASYNC_LOAD=1
# Once the model is cached, never talk to the Hub: at load, transformers checks model files over
# the network and keeps that connection open for the server's lifetime (seen 2026-09-24 as an
# ESTABLISHED socket to Hugging Face's CDN). Set HF_HUB_OFFLINE=0 only for a first download.
export HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-1}
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
