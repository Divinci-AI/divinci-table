#!/bin/bash
# ONE four-player table (free-for-all): the seats in $FOUR_SEATS (default Michael Sam Claude Fusion), drafted decks.
#
#   launch/play_four.sh [--force] [--port N]
# A person plays in the browser (live/seats.html for the players on this laptop; the remote player's link is written and copied only
# after the tunnel is PROVED to reach the table: launch/tunnels.py). The seats in $BOT_SEATS (default Claude,Fusion) are played by the
# rules autopilot, and the table is told so. A four-player table is a single game: never a match.
# Keys go into 0600 files only: never printed, never in argv, never in a URL query (the seat pages take the key after '#').
# The launch lock refuses to replace a game that is still running (--force replaces it); the finished game is saved before the table
# stops; only THIS table's tunnel is touched.
#
# Env: as launch/play_round.sh (LIVE_DIR, TABLE_CODE, TABLE_PY, DECK_DIR, DECK_SUFFIX, TABLE_ART_DIR, TABLE_CARDS_JSON, REMOTE_SEAT,
# BOT_SEATS, BOT_DIR, CLAUDE_BANTER, ANTHROPIC_KEY_FILE, CLAUDE_AUTOPASS, TABLE_HOSTS, TUNNEL_WATCH, NOTIFY_LINK, DRYRUN), and
# FOUR_SEATS, DECK_TOOL (draft/make_deck_json.py: the decks are built from $LIVE_DIR/deck-<name>.txt, which the draft server writes).
set -u
HERE="$(cd "$(dirname "$0")" && pwd -P)"
REPO="$(cd "$HERE/.." && pwd -P)"
L="${LIVE_DIR:-$PWD/live}"
PY="${TABLE_PY:-python3}"
CODE="${TABLE_CODE:-$REPO}"
DECKS="${DECK_DIR:-$L/decks}"
SUFFIX="${DECK_SUFFIX--g5}"
BOTS="${BOT_DIR:-$REPO/bots}"
DRY="${DRYRUN:-0}"
REMOTE="${REMOTE_SEAT-Sam}"
BOT_SEATS=",${BOT_SEATS-Claude,Fusion},"
FOUR="${FOUR_SEATS:-Michael Sam Claude Fusion}"
DECK_TOOL="${DECK_TOOL:-$REPO/draft/make_deck_json.py}"
KEYFILE_ANTHROPIC="${ANTHROPIC_KEY_FILE:-$L/anthropic.key}"
TT="$HERE/table_tools.py"
TUN="$HERE/tunnels.py"
FORCE=""; PORT=8800
while [ $# -gt 0 ]; do
  case "$1" in --force) FORCE=--force ;; --port) PORT="${2:-}"; shift ;; -h|--help) sed -n 2,14p "$HERE/$(basename "$0")"; exit 0 ;; *) echo "usage: $0 [--force] [--port N]"; exit 1 ;; esac
  shift
done
case "$PORT" in *[!0-9]*|"") echo "bad --port"; exit 1 ;; esac
low() { echo "$1" | tr 'A-Z' 'a-z'; }
sfx_of() { case "$1" in 8800) echo "" ;; 8801) echo "-b" ;; *) echo "-$1" ;; esac; }
is_bot() { case "$BOT_SEATS" in *",$1,"*) return 0 ;; esac; return 1; }
U="http://127.0.0.1:$PORT"; sfx="$(sfx_of "$PORT")"
mkdir -p "$L/tunnels" "$DECKS" && chmod 700 "$L/tunnels"
[ "$DRY" = 1 ] && export NO_CLIPBOARD=1 NO_NOTIFY=1

# the launch lock (a refusal stops here, nothing touched), before anything is written or stopped
python3 "$TT" lock --live "$L" --launcher play_four.sh --pid $$ --ports "$PORT" --game "four-player: $FOUR" $FORCE || exit 1
trap 'python3 "$TT" unlock --live "$L" --pid $$ >/dev/null 2>&1' EXIT

# 1. the drafted decks -> table JSON (fails loudly on a short deck or an unknown card)
for S in $FOUR; do
  s=$(low "$S")
  n=$(awk '{s+=$1} END{print s+0}' "$L/deck-$s.txt" 2>/dev/null)
  [ "${n:-0}" -ge 40 ] || { echo "deck-$s.txt has ${n:-0} cards, under 40: not starting"; exit 1; }
  python3 "$DECK_TOOL" "$L/deck-$s.txt" "$DECKS/$s$SUFFIX.json" "$S draft deck" >/dev/null || exit 1
done

# 2. the finished game's public record + snapshot, then this table's own processes, each by its pid
python3 "$TT" save --live "$L" --port "$PORT"
python3 "$TT" helpers-stop --live "$L" --port "$PORT"
P=$(lsof -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null); [ -n "$P" ] && kill $P        # a table started before the pids were written down
for _ in 1 2 3 4 5 6 7 8 9 10; do lsof -t -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 || break; sleep 0.5; done
[ -f "$L/server$sfx.log" ] && cp "$L/server$sfx.log" "$L/server$sfx-$(date +%H%M).log"
mkdir -p "$L/saves$sfx"

# 3. the table: remote seats allowed only through the tunnel's host names, and every remote call needs that seat's key
ARGS=(); for S in $FOUR; do ARGS+=(--ai "$S|" --ai-deck "$DECKS/$(low "$S")$SUFFIX.json"); done
for S in $FOUR; do ARGS+=(--pilot "$S"); done
( export TABLE_CHECKIN=off STRICT_SEATS=1 TABLE_HOSTS="${TABLE_HOSTS:-.trycloudflare.com}" TABLE_BEHIND_CLOUDFLARE=1 \
    HF_HUB_OFFLINE=1 ROUTER=code REPLIES=template NO_GEMMA=1 TABLE_MATCH=single \
    TABLE_RESEARCH_DIR="$L/research" TABLE_SAVES_DIR="$L/saves$sfx" TABLE_CARDS_JSON="${TABLE_CARDS_JSON:-${DRAFT_CARDS_JSON:-}}"
  unset TYPESAFE_API_KEY
  [ -n "${TABLE_ART_DIR:-}" ] && export TABLE_ART_DIR
  python3 "$TT" spawn --live "$L" --port "$PORT" --name table --sig server.py --log "$L/server$sfx.log" --cwd "$CODE" -- \
    "$PY" table/server.py --any-card --port "$PORT" --brain external --token-file "$L/token$sfx" --fair-seed off --decklists declared "${ARGS[@]}" ) || exit 1
up=0; for _ in $(seq 1 120); do curl -s -o /dev/null "$U/api/phase" && { up=1; break; }; sleep 0.5; done
[ $up = 1 ] || { echo "the table on :$PORT did not come up: see $L/server$sfx.log"; tail -3 "$L/server$sfx.log"; exit 1; }

# 4. claim the seats; keys into 0600 files
python3 "$TT" claim --live "$L" --port "$PORT" $(printf -- '--seat %s ' $FOUR) || exit 1

# 5. the autopilot's seats: the table is told, then the bots start (Claude's seat also gets its helper and its table talk)
SEATS=(); starter=""; remote=""
for S in $FOUR; do
  if is_bot "$S"; then
    python3 "$TT" played-by --live "$L" --port "$PORT" --seat "$S" --kind autopilot --own --token-file "$L/token$sfx"
    BOT_DIR="$BOTS" python3 "$TT" autopilot --live "$L" --port "$PORT" --seat "$S" >/dev/null
    if [ "$S" = Claude ]; then
      [ "${CLAUDE_AUTOPASS:-1}" = 1 ] && TABLE_URL="$U" CLAUDE_KEY_FILE="$L/claude-seat.key" \
        python3 "$TT" spawn --live "$L" --port "$PORT" --name autopass-Claude --log "$L/autopass.log" -- python3 -u "$BOTS/claude_autopass.py"
      [ "$DRY" != 1 ] && [ "${CLAUDE_BANTER:-1}" = 1 ] && [ -s "$KEYFILE_ANTHROPIC" ] && TABLE_URL="$U" CLAUDE_KEY_FILE="$L/claude-seat.key" ANTHROPIC_KEY_FILE="$KEYFILE_ANTHROPIC" \
        python3 "$TT" spawn --live "$L" --port "$PORT" --name banter-Claude --log "$L/claude-banter.log" -- "$PY" -u "$BOTS/claude_banter.py"
    fi
    SEATS+=("$PORT:$S:bot"); [ -z "$starter" ] && starter="$S"
  elif [ -n "$REMOTE" ] && [ "$S" = "$REMOTE" ]; then remote="$S"
  else SEATS+=("$PORT:$S"); fi
done
[ -z "$starter" ] && starter="${FOUR%% *}"

# 6. response windows off, and the game's first step
sleep 1
python3 "$TT" begin --live "$L" --port "$PORT" --by "$starter" || exit 1

# 7. the remote player's tunnel, to this table only; the link only after the tunnel is proved to reach this table
rc=0
if [ -n "$remote" ]; then
  if [ "$DRY" = 1 ] && [ -z "${TUNNEL_CMD:-}" ]; then
    echo "DRYRUN: no tunnel is opened for $remote (set TUNNEL_CMD to a stand-in to rehearse it): no link is written"
  elif python3 "$TUN" open --live "$L" --port "$PORT" && python3 "$TUN" link --live "$L" --port "$PORT" --seat "$remote"; then
    [ "${NOTIFY_LINK:-0}" = 1 ] && [ "$DRY" != 1 ] && { python3 "$REPO/scripts/notify_link.py" --to "$remote" --link-file "$L/$(low "$remote")-link.txt" --what "your seat link for the four-player game" --log-file "$L/notify.log" \
      || echo "NOTE: $remote was not sent the link automatically (notify_link exit $?): it is on the draft page (Join the match)"; }
    [ "${TUNNEL_WATCH:-1}" = 1 ] && python3 "$TT" spawn --live "$L" --port "$PORT" --name tunnel-watch --sig "tunnels.py watch" --log "$L/tunnels/watch.log" -- \
      python3 -u "$TUN" watch --live "$L" --port "$PORT" --seat "$remote"
  else
    echo "WARNING: $remote HAS NO LINK. The table is up; fix the tunnel, then: python3 $TUN open --live \"$L\" --port $PORT --fresh && python3 $TUN link --live \"$L\" --port $PORT --seat $remote"
    rc=4
  fi
fi

# 8. every local seat on one page
python3 "$TT" seats-page --live "$L" ${SEATS[@]+$(printf -- '--seat %s ' "${SEATS[@]}")} ${remote:+--remote "$remote"}
exit $rc
