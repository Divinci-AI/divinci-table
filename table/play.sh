#!/usr/bin/env bash
# Game night: the table server in external-brain mode, with Claude as the virtual Ellivere player.
#   table/play.sh "Michael|Ghalta, Primal Hunger" "Sam|Krenko, Mob Boss"
# then open http://localhost:8800/table, press Start, and say "Claude, your turn." when it is.
# Claude (in Claude Code) follows the table with:  table/tablectl.py watch   — see docs/claude-brain.md
set -eu
cd "$(dirname "$0")/.."
[ $# -ge 1 ] || { echo 'usage: table/play.sh "Name|Commander" ["Name|Commander" …]'; exit 2; }
HUMANS=(); for h in "$@"; do HUMANS+=(--human "$h"); done
pid=$(lsof -ti tcp:8800 -sTCP:LISTEN || true); [ -n "$pid" ] && { echo "stopping the server on :8800 ($pid)"; kill -TERM $pid; sleep 3; }
LOG=table/.cache/game-$(date +%Y%m%d-%H%M).log
mkdir -p table/.cache
env -u TYPESAFE_API_KEY HF_HUB_OFFLINE=1 HF_DEACTIVATE_ASYNC_LOAD=1 ~/.venvs/table/bin/python table/server.py \
  --any-card --brain external --ai "Claude|Ellivere of the Wild Court|Moira" --ai-deck decks/ellivere.json \
  "${HUMANS[@]}" --port 8800 > "$LOG" 2>&1 &
for _ in $(seq 1 90); do grep -q -E "Gemma loaded|could not|Traceback" "$LOG" && break; sleep 1; done
grep -E "AI players|Gemma|Traceback" "$LOG" || true
echo "table:  http://localhost:8800/table      log: $LOG"
echo "brain:  table/tablectl.py watch   (Claude drives with tablectl — docs/claude-brain.md)"
