#!/usr/bin/env bash
# Run the arena unattended, with local models only.
#
#   scripts/arena_night.sh [HOURS=8] [SEATS]       start it (detach it yourself: nohup scripts/arena_night.sh 8 &)
#   touch table/.cache/research/ARENA.STOP          stop after the current turn (it is a file, not a signal)
#   python3 harness/arena_report.py                 the morning report
#
# It keeps the Mac awake (caffeinate), starts the local model server if none is running and stops it again if it
# started it, plays one game at a time with the seats rotating, and obeys the arena's own limits: an hour cap, a
# per-game timeout, a disk floor, a stop file, and a stop after three failed games in a row. It spends nothing.
set -u
cd "$(dirname "$0")/.."
HOURS=${1:-8}
SEATS=${2:-human-sim,heuristic,random,gemma4:e2b}
RESEARCH=${TABLE_RESEARCH_DIR:-table/.cache/research}
mkdir -p "$RESEARCH"; rm -f "$RESEARCH/ARENA.STOP"
started_ollama=0
if ! curl -s --max-time 3 http://127.0.0.1:11434/api/tags >/dev/null; then
  case "$SEATS" in *gemma*|*ollama:*) (nohup ollama serve > "$RESEARCH/ollama-serve.log" 2>&1 &); started_ollama=1
    for _ in $(seq 1 30); do curl -s --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null && break; sleep 1; done;; esac
fi
echo "arena night: $HOURS h, seats $SEATS ($(date))"
caffeinate -i python3 harness/arena.py --seats "$SEATS" --games 100000 --hours "$HOURS" --rotate
rc=$?
[ "$started_ollama" = 1 ] && pkill -f "ollama serve"
echo "arena night finished (exit $rc, $(date))"
python3 harness/arena_report.py --last-hours "$HOURS" | head -40
