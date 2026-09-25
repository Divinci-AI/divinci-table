#!/usr/bin/env bash
# Every test the table has, in one go (~15 min, fully offline once fixtures exist).
#   table/tests/run_all.sh            everything
#   table/tests/run_all.sh quick      engine + sensory regressions + one simulated game
# Uses port 8801 for the sensory suites (a live game on 8800 is left alone) EXCEPT the older
# suites below, which restart a server on 8800 — so run the full set between games, not during one.
set -u
cd "$(dirname "$0")/../.."
PY=~/.venvs/table/bin/python
PW=${PW:-$HOME/Documents/server/workspace/clients/tests/node_modules/@playwright/test}
LOG=$(mktemp -d)/table-server.log
declare -a SUMMARY
run() {            # name, command…
  local name=$1; shift
  local t0=$SECONDS out
  out=$("$@" 2>&1); local rc=$?
  local last=$(printf '%s\n' "$out" | grep -E "passed|pass$|problems|regressions|goals met|route correct" | tail -2 | tr '\n' ' ')
  SUMMARY+=("$( [ $rc = 0 ] && echo "✅" || echo "❌" ) $(printf '%-16s' "$name") $(printf '%4ss' $((SECONDS - t0)))  $last")
  [ $rc = 0 ] || printf '%s\n' "$out" | grep -E "❌|Traceback|Error" | head -15
}
start8800() {      # the older suites expect a particular server config on :8800
  local pid; pid=$(lsof -ti tcp:8800 -sTCP:LISTEN); [ -n "$pid" ] && kill -TERM $pid
  for _ in $(seq 1 30); do lsof -ti tcp:8800 -sTCP:LISTEN >/dev/null || break; sleep 1; done   # unloading Gemma takes a moment
  (env -u TYPESAFE_API_KEY HF_HUB_OFFLINE=1 $PY table/server.py "$@" --port 8800 > "$LOG" 2>&1 &)
  for _ in $(seq 1 120); do grep -q -E "voices:|speaker ID unavailable|Traceback" "$LOG" && break; sleep 1; done
}

run engine        $PY table/tests/engine_rules.py
run hearing-names $PY table/tests/hearing_names.py
if [ "${1:-}" = quick ]; then
  run sensory     $PY table/tests/sense_run.py --tier regression
  run game-sim    $PY table/tests/game_sim.py --games 1 --rounds 6
else
  run sensory     $PY table/tests/sense_run.py
  run game-sim    $PY table/tests/game_sim.py --games 3 --rounds 8
  run speaker-id  $PY table/tests/speaker_eval.py 4
  run vision      $PY table/tests/vision_eval.py
  start8800 --any-card --brain external --ai "Claude|Ellivere of the Wild Court|Moira" --ai-deck decks/ellivere.json --human "Michael|" --human "Player 2|"
  run brain-api   $PY table/tests/brain_e2e.py
  run brain-page  env PW=$PW node table/tests/brain_page_e2e.cjs
  start8800 --ai "Ellivere|Ellivere of the Wild Court|Moira" --ai-deck decks/ellivere.json --human "Michael|Ghalta, Primal Hunger" --any-card
  run gemma-turns $PY table/tests/ai_turn_e2e.py
  start8800 --deck decks/example.txt --ai "Talrand|Talrand, Sky Summoner|Daniel" --human "Michael|Ghalta, Primal Hunger"
  run session     $PY table/tests/e2e_offline.py
  run table-page  env PW=$PW node table/tests/table_e2e.cjs
  run router      $PY table/tests/route_eval.py
  pid=$(lsof -ti tcp:8800 -sTCP:LISTEN); [ -n "$pid" ] && kill -TERM $pid
fi
echo; echo "═══ summary"; printf '%s\n' "${SUMMARY[@]}"
FAILED=0; for l in "${SUMMARY[@]}"; do case "$l" in ✅*) ;; *) FAILED=$((FAILED + 1));; esac; done
echo "$FAILED suite(s) failed"
[ "$FAILED" = 0 ]
