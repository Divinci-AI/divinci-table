#!/usr/bin/env bash
# Game night: the table server in external-brain mode, with Claude as a virtual AI player.
#   table/play.sh "Michael|Ghalta, Primal Hunger" "Sam|Krenko, Mob Boss"
# then open http://localhost:8800/table, press Start, and say "Claude, your turn." when it is.
#
# NO_GEMMA=1      no local model at all: code routes, the brains decide everything.
# CLAUDE_DECK=…   Claude's deck (default decks/ellivere.json).
# FUSION=1        a second AI seat, "Fusion": a Divinci release (RAG) that plays itself through
#                 table/fusion_brain.py. FUSION_DECK=… picks its deck (default decks/ellivere.json).
# FAIR_SEED=online  seal each AI deck with ANU quantum randomness + Cloudflare's drand (+ players' words).
# LAN=1           players' phones can open http://<this Mac>:8800/me (public info only).
# ORDER=…         turn order, "Fusion,Sam,Claude,Michael": NEXT walks each human's turn step by step, and
#                 the turn passes itself along (AI seats are started for you).
# RESTORE=…       resume a game from its snapshot.pkl (table/.cache/research/<game>/snapshot.pkl).
# FUSION_LOCAL_MODEL=tev1  Fusion's small decisions (instants in others' turns) go to a local Ollama
#                 decision model instead of its Divinci release — free; the turn's real moves stay on the release.
# PRIORITY_WINDOW=3  every step's priority window lasts exactly this many seconds (default 10)
# CAPTAINS=1      each commander speaks a short in-character line now and then, in its own voice
#                 (table/captains.py; needs table/.cache/captains.json — see docs/captains.md).
# TLS=1           also serve HTTPS on :8443 (self-signed, made once for this Mac's LAN address): WebXR —
#                 the AR/VR table at /xr — only runs on secure pages. Phones/headsets accept it once.
# Claude (in Claude Code) follows the table with:  table/tablectl.py --seat Claude watch   — docs/claude-brain.md
set -eu
cd "$(dirname "$0")/.."
[ $# -ge 1 ] || { echo 'usage: table/play.sh "Name|Commander" ["Name|Commander" …]'; exit 2; }
HUMANS=(); for h in "$@"; do HUMANS+=(--human "$h"); done
pid=$(lsof -ti tcp:8800 -sTCP:LISTEN || true); [ -n "$pid" ] && { echo "stopping the server on :8800 ($pid)"; kill -TERM $pid; sleep 3; }
pkill -f "table/fusion_brain.py" 2>/dev/null || true
MODELS=(); [ "${NO_GEMMA:-}" = 1 ] && MODELS=(ROUTER=code REPLIES=template)
commander() { python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['commander'][0]['name'])" "$1"; }
CLAUDE_DECK=${CLAUDE_DECK:-decks/ellivere.json}
AIS=(--ai "Claude|$(commander "$CLAUDE_DECK")|Moira" --ai-deck "$CLAUDE_DECK")
if [ "${FUSION:-}" = 1 ]; then
  FUSION_DECK=${FUSION_DECK:-decks/ellivere.json}
  AIS+=(--ai "Fusion|$(commander "$FUSION_DECK")|Daniel" --ai-deck "$FUSION_DECK")
fi
HOST=(); [ "${LAN:-}" = 1 ] && HOST=(--host 0.0.0.0)
FAIR=(--fair-seed "${FAIR_SEED:-local}")   # FAIR_SEED=online: ANU quantum + drand go into every AI deck's seal
[ -n "${ORDER:-}" ] && FAIR+=(--order "$ORDER")
[ -n "${RESTORE:-}" ] && FAIR+=(--restore "$RESTORE")
[ -n "${PRIORITY_WINDOW:-}" ] && FAIR+=(--priority-window "$PRIORITY_WINDOW")   # seconds every step's window lasts
if [ "${TLS:-}" = 1 ]; then
  IP=$(ipconfig getifaddr en0 || ipconfig getifaddr en1 || echo 127.0.0.1)
  TLSD=table/.cache/tls; mkdir -p "$TLSD"; chmod 700 "$TLSD"
  if [ ! -f "$TLSD/cert-$IP.pem" ]; then        # one per LAN address; the key never leaves this Mac
    openssl req -x509 -newkey rsa:2048 -nodes -days 365 -subj "/CN=divinci-table" \
      -addext "subjectAltName=IP:$IP,IP:127.0.0.1,DNS:localhost" \
      -keyout "$TLSD/key-$IP.pem" -out "$TLSD/cert-$IP.pem" 2>/dev/null
    chmod 600 "$TLSD/key-$IP.pem"
  fi
  FAIR+=(--tls-cert "$TLSD/cert-$IP.pem" --tls-key "$TLSD/key-$IP.pem")
fi
LOG=table/.cache/game-$(date +%Y%m%d-%H%M).log
mkdir -p table/.cache
env -u TYPESAFE_API_KEY ${MODELS[@]+"${MODELS[@]}"} HF_HUB_OFFLINE=1 HF_DEACTIVATE_ASYNC_LOAD=1 ~/.venvs/table/bin/python table/server.py \
  --any-card --brain external "${AIS[@]}" "${HUMANS[@]}" ${HOST[@]+"${HOST[@]}"} "${FAIR[@]}" --port 8800 > "$LOG" 2>&1 &
for _ in $(seq 1 90); do grep -q -E "Gemma loaded|voices:|could not|Traceback" "$LOG" && break; sleep 1; done
grep -E "AI players|Gemma|voices:|Traceback" "$LOG" || true
if [ "${FUSION:-}" = 1 ]; then
  WS=$(grep '^INFISICAL_WORKSPACE_ID=' "${INFISICAL_ENV_FILE:-$HOME/Documents/server/private-keys/production/infisical.env}" | cut -d= -f2- | tr -d '"')
  FLOG=table/.cache/fusion-$(date +%Y%m%d-%H%M).log
  (infisical run --projectId="$WS" --env=prod --path=/ --silent -- /usr/bin/python3 table/fusion_brain.py --seat Fusion > "$FLOG" 2>&1 &)
  echo "fusion: Divinci release playing seat Fusion   log: $FLOG"
fi
if [ "${CAPTAINS:-}" = 1 ] && [ -f table/.cache/captains.json ]; then   # each commander's own voice, now and then
  WS=${WS:-$(grep '^INFISICAL_WORKSPACE_ID=' "${INFISICAL_ENV_FILE:-$HOME/Documents/server/private-keys/production/infisical.env}" | cut -d= -f2- | tr -d '"')}
  pkill -f "table/captains.py" 2>/dev/null || true
  CLOG=table/.cache/captains-$(date +%Y%m%d-%H%M).log
  (infisical run --projectId="$WS" --env=prod --path=/ --silent -- /usr/bin/python3 table/captains.py > "$CLOG" 2>&1 &)
  echo "captains: commander voices on        log: $CLOG"
fi
echo "table:  http://localhost:8800/table      log: $LOG"
echo "stage:  http://localhost:8800/stage      (NEXT: phones at /me, or the stage's NEXT / N key)"
[ "${LAN:-}" = 1 ] && echo "phones: http://$(ipconfig getifaddr en0 || ipconfig getifaddr en1):8800/me"
[ "${TLS:-}" = 1 ] && echo "AR/VR:  https://${IP}:8443/xr   (accept the self-signed certificate once per device)"
echo "brain:  table/tablectl.py --seat Claude watch   (Claude drives with tablectl — docs/claude-brain.md)"
