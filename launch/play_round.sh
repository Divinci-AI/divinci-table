#!/bin/bash
# One ROUND of 1v1 matches: one table per pairing, as many pairings as you name (one is fine).
#
#   launch/play_round.sh Michael:Sam Claude:Fusion         two tables: the first pairing on :8800, the second on :8801
#   launch/play_round.sh Michael:Claude                    one table
#   launch/play_round.sh Michael:Fusion Claude:Sam=bot     Sam is not here: the rules autopilot plays Sam's seat, and the table SAYS so
#   launch/play_round.sh --wait A:B C:D                    …and stay until the round is done, reporting each game and each match
# Options:
#   --ports 8800,8801,…   the n-th pairing plays on the n-th port (default: 8800, 8801, 8802, …)
#   --restart 8801        restart that table even though its match is still being played, or is over (several: 8800,8801)
#   --force               restart every table named, whatever it is doing
#   --single              one game per table (default: a match, best two out of three, the next game starting by itself: docs/match.md)
#   --deck Name=FILE      that seat's deck file (default: $DECK_DIR/<name>$DECK_SUFFIX.json, which draft/match_when_ready.sh writes)
#   --wait                after the launch, follow the tables and exit 0 when every match is over (table_tools.py round-wait)
#
# WHAT IT NEVER DOES (2026-11-13: both tables were restarted for every game, and a match that was already decided was played on):
#   a table where the SAME two players' match is still being played is left alone; a table where their match is OVER is not replayed.
#   Either only with --restart PORT or --force. A table that has finished ANOTHER pairing's match is saved and replaced. A table where
#   other players are still playing stops the launch (nothing is touched).
#
# Who plays a seat. A person plays in the browser (the seat's link: live/seats.html for the players on this laptop; the remote player's
# link is written and copied only after the tunnel is PROVED to reach the table: launch/tunnels.py). The seats in $BOT_SEATS (default
# Claude,Fusion) and every seat written Name=bot are played by the rules autopilot (bots/claude_autopilot.py: no model). The table is told
# so (it shows it on every page, on /watch, and in the log); a Name=bot seat is "standing in": the person takes it back by playing.
# Mid-match:  launch/table_tools.py stand-in --live "$LIVE_DIR" --seat Sam on|off
#
# Keys go into 0600 files only: never printed, never in argv, never in a URL query (the seat pages take the key after '#').
# Before a table is stopped, its finished game is saved to live/games/<date>-<HHMM>-<port>-<players>/ (0700).
#
# Env: LIVE_DIR (./live), TABLE_CODE (this repository), TABLE_PY (python3: the interpreter with table/requirements.txt), DECK_DIR
# ($LIVE_DIR/decks), DECK_SUFFIX (-g5), TABLE_ART_DIR (card art folder, never in the repository; unset = no art), TABLE_CARDS_JSON
# (the set file, so every card can be looked up; default $DRAFT_CARDS_JSON), REMOTE_SEAT (Sam; "" = nobody joins through a tunnel),
# BOT_SEATS (Claude,Fusion), BOT_DIR (bots/), CLAUDE_BANTER (1) with ANTHROPIC_KEY_FILE ($LIVE_DIR/anthropic.key: it must already
# exist, 0600; this script never fetches a key), CLAUDE_AUTOPASS (1), TABLE_HOSTS (.trycloudflare.com), TUNNEL_WATCH (1),
# NOTIFY_LINK=1 (send the remote player the proved link: scripts/notify_link.py), DRYRUN=1 (a rehearsal: no clipboard, no
# notification, no banter, and no tunnel unless TUNNEL_CMD names a stand-in: launch/fake_tunnel.py).
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
KEYFILE_ANTHROPIC="${ANTHROPIC_KEY_FILE:-$L/anthropic.key}"
TT="$HERE/table_tools.py"
TUN="$HERE/tunnels.py"
# A table's own files are named after its PORT, so a table keeps its files whichever launch started it: :8800 has the plain names
# (token, server.log, saves), :8801 the "-b" names (as before), any other port "-<port>" (token-8802, server-8802.log, saves-8802).
sfx_of() { case "$1" in 8800) echo "" ;; 8801) echo "-b" ;; *) echo "-$1" ;; esac; }
FORCE=""; PORTS=""; RESTART=""; WAIT=0; MATCH="${TABLE_MATCH:-bo3}"; PAIRS=(); DECKOPT=()
while [ $# -gt 0 ]; do
  case "$1" in
    --force) FORCE=--force ;;
    --ports) PORTS="${2:-}"; shift ;;
    --ports=*) PORTS="${1#*=}" ;;
    --restart) RESTART="${2:-}"; shift ;;
    --restart=*) RESTART="${1#*=}" ;;
    --single) MATCH=single ;;
    --match) MATCH="${2:-}"; shift ;;
    --deck) DECKOPT+=("${2:-}"); shift ;;
    --wait) WAIT=1 ;;
    -h|--help) sed -n 2,33p "$HERE/$(basename "$0")"; exit 0 ;;
    -*) echo "unknown option $1 (see --help)"; exit 1 ;;
    *) PAIRS+=("$1") ;;
  esac
  shift
done
low() { echo "$1" | tr 'A-Z' 'a-z'; }
[ ${#PAIRS[@]} -ge 1 ] || { echo "usage: $0 [--force] [--restart PORT] [--ports A,B,…] [--single] [--wait] A:B [C:D …]   (a seat written Name=bot is played by the autopilot)"; exit 1; }
case "$MATCH" in bo3|single) ;; *) echo "--match is bo3 or single"; exit 1 ;; esac

# the pairings: names, and which seats the autopilot plays
NA=(); NB=(); ALL=" "; STANDIN=" "
for pair in "${PAIRS[@]}"; do
  [[ "$pair" =~ ^[A-Za-z][A-Za-z0-9]*(=bot)?:[A-Za-z][A-Za-z0-9]*(=bot)?$ ]] || { echo "bad pairing '$pair' (want Name:Name; Name=bot for a seat the autopilot plays)"; exit 1; }
  for side in "${pair%%:*}" "${pair##*:}"; do
    n="${side%=bot}"
    case "$ALL" in *" $n "*) echo "$n is in two pairings"; exit 1 ;; esac
    ALL="$ALL$n "
    [ "$side" = "$n" ] || STANDIN="$STANDIN$n "
  done
  a="${pair%%:*}"; b="${pair##*:}"; NA+=("${a%=bot}"); NB+=("${b%=bot}")
done
is_bot() { case "$BOT_SEATS" in *",$1,"*) return 0 ;; esac; case "$STANDIN" in *" $1 "*) return 0 ;; esac; return 1; }
deck_of() {
  local d
  for d in ${DECKOPT[@]+"${DECKOPT[@]}"}; do [ "${d%%=*}" = "$1" ] && { echo "${d#*=}"; return; }; done
  echo "$DECKS/$(low "$1")$SUFFIX.json"
}
for n in $ALL; do [ -s "$(deck_of "$n")" ] || { echo "missing deck for $n: $(deck_of "$n")"; exit 1; }; done

# the ports: the n-th pairing on the n-th port
N=${#PAIRS[@]}
if [ -z "$PORTS" ]; then PORTS=8800; i=1; while [ $i -lt $N ]; do PORTS="$PORTS,$((8800 + i))"; i=$((i + 1)); done; fi
IFS=, read -r -a PORT <<< "$PORTS"
[ ${#PORT[@]} -ge $N ] || { echo "--ports names ${#PORT[@]} ports for $N pairings"; exit 1; }
for p in "${PORT[@]}"; do case "$p" in *[!0-9]*|"") echo "bad --ports '$PORTS'"; exit 1 ;; esac; done
[ "$(printf '%s\n' "${PORT[@]:0:$N}" | sort -u | wc -l | tr -d ' ')" = "$N" ] || { echo "--ports needs $N different ports"; exit 1; }
mkdir -p "$L/tunnels" && chmod 700 "$L/tunnels"
[ "$DRY" = 1 ] && export NO_CLIPBOARD=1 NO_NOTIFY=1

# what this round starts, and what it leaves alone (nothing has been touched yet)
PLANARGS=(); i=0; while [ $i -lt $N ]; do PLANARGS+=(--pair "${NA[$i]}:${NB[$i]}"); i=$((i + 1)); done
PLAN="$(python3 "$TT" plan --ports "$(IFS=,; echo "${PORT[*]:0:$N}")" "${PLANARGS[@]}" $FORCE ${RESTART:+--restart "$RESTART"})"; prc=$?
ACT=(); STARTPORTS=""
while read -r action port a b why; do
  [ -n "$action" ] || continue
  ACT+=("$action")
  case "$action" in
    "start")  echo "table :$port  $a vs $b: starting ($why)"; STARTPORTS="${STARTPORTS:+$STARTPORTS,}$port" ;;
    "keep")   echo "table :$port  $a vs $b: LEFT ALONE, $why" ;;
    "done")   echo "table :$port  $a vs $b: NOT REPLAYED, $why" ;;
    "refuse") echo "table :$port  $a vs $b: REFUSED, $why" ;;
  esac
done <<< "$PLAN"
[ $prc -eq 0 ] || { echo "nothing was started or stopped"; exit 1; }
[ ${#ACT[@]} -eq $N ] || { echo "could not plan the round: $PLAN"; exit 1; }

if [ -n "$STARTPORTS" ]; then
  # the launch lock: refuses while another launcher is mid-launch (the plan above already decided which running games may be replaced)
  python3 "$TT" lock --live "$L" --launcher play_round.sh --pid $$ --ports "$STARTPORTS" --game "${PAIRS[*]}" --replace-ok $FORCE || exit 1
  trap 'python3 "$TT" unlock --live "$L" --pid $$ >/dev/null 2>&1' EXIT
fi

SEATS=(); TABLES=(); REMOTE_PORT=""; REMOTE_FRESH=0
i=0
while [ $i -lt $N ]; do
  A="${NA[$i]}"; B="${NB[$i]}"; port="${PORT[$i]}"; sfx="$(sfx_of "$port")"; U="http://127.0.0.1:$port"
  botlist=""; for S in "$A" "$B"; do is_bot "$S" && botlist="${botlist:+$botlist+}$S"; done
  remote=""; for S in "$A" "$B"; do [ -n "$REMOTE" ] && [ "$S" = "$REMOTE" ] && ! is_bot "$S" && remote="$S"; done
  [ -n "$remote" ] && REMOTE_PORT="$port"
  for S in "$A" "$B"; do if is_bot "$S"; then SEATS+=("$port:$S:bot"); elif [ "$S" != "$remote" ]; then SEATS+=("$port:$S"); fi; done
  if [ "${ACT[$i]}" != start ]; then
    TABLES+=("$port:$A:$B:bots=$botlist:remote=$remote:token=token$sfx:kept")
    i=$((i + 1)); continue
  fi
  TABLES+=("$port:$A:$B:bots=$botlist:remote=$remote:token=token$sfx")
  [ -n "$remote" ] && REMOTE_FRESH=1

  # the finished game's public record and snapshot, BEFORE anything is stopped; then this table's own processes, each by its pid
  python3 "$TT" save --live "$L" --port "$port"
  python3 "$TT" helpers-stop --live "$L" --port "$port"
  P=$(lsof -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null); [ -n "$P" ] && kill $P    # a table started before the pids were written down
  for _ in 1 2 3 4 5 6 7 8 9 10; do lsof -t -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 || break; sleep 0.5; done
  [ -f "$L/server$sfx.log" ] && cp "$L/server$sfx.log" "$L/server$sfx-$(date +%H%M).log"
  mkdir -p "$L/saves$sfx"

  # the table: remote seats allowed only through the tunnel's host names, and every remote call needs that seat's key
  ( export TABLE_CHECKIN=off STRICT_SEATS=1 TABLE_HOSTS="${TABLE_HOSTS:-.trycloudflare.com}" TABLE_BEHIND_CLOUDFLARE=1 \
      HF_HUB_OFFLINE=1 ROUTER=code REPLIES=template NO_GEMMA=1 TABLE_MATCH="$MATCH" \
      TABLE_RESEARCH_DIR="$L/research" TABLE_SAVES_DIR="$L/saves$sfx" TABLE_CARDS_JSON="${TABLE_CARDS_JSON:-${DRAFT_CARDS_JSON:-}}"
    unset TYPESAFE_API_KEY
    [ -n "${TABLE_ART_DIR:-}" ] && export TABLE_ART_DIR
    python3 "$TT" spawn --live "$L" --port "$port" --name table --sig server.py --log "$L/server$sfx.log" --cwd "$CODE" -- \
      "$PY" table/server.py --any-card --port "$port" --brain external --token-file "$L/token$sfx" --fair-seed off --decklists declared \
      --ai "$A|" --ai-deck "$(deck_of "$A")" --ai "$B|" --ai-deck "$(deck_of "$B")" --pilot "$A" --pilot "$B" ) || exit 1
  up=0; for _ in $(seq 1 120); do curl -s -o /dev/null "$U/api/phase" && { up=1; break; }; sleep 0.5; done
  [ $up = 1 ] || { echo "the table on :$port did not come up: see $L/server$sfx.log"; tail -3 "$L/server$sfx.log"; exit 1; }
  python3 "$TT" claim --live "$L" --port "$port" --seat "$A" --seat "$B" || exit 1

  stamp="=== 1v1 $A vs $B $(date +%H:%M) on :$port ==="
  starter=""
  for S in "$A" "$B"; do
    is_bot "$S" || continue
    # the table is told who plays the seat BEFORE the game starts: the autopilot's own seat, or standing in for a person (Name=bot)
    case "$STANDIN" in *" $S "*) python3 "$TT" played-by --live "$L" --port "$port" --seat "$S" --kind autopilot --token-file "$L/token$sfx" ;;
                       *)        python3 "$TT" played-by --live "$L" --port "$port" --seat "$S" --kind autopilot --own --token-file "$L/token$sfx" ;; esac
    BOT_DIR="$BOTS" python3 "$TT" autopilot --live "$L" --port "$port" --seat "$S" >/dev/null
    if [ "$S" = Claude ]; then
      for f in autopass.log claude-banter.log; do echo "$stamp" >> "$L/$f"; done
      [ "${CLAUDE_AUTOPASS:-1}" = 1 ] && TABLE_URL="$U" CLAUDE_KEY_FILE="$L/claude-seat.key" \
        python3 "$TT" spawn --live "$L" --port "$port" --name autopass-Claude --log "$L/autopass.log" -- python3 -u "$BOTS/claude_autopass.py"
      [ "$DRY" != 1 ] && [ "${CLAUDE_BANTER:-1}" = 1 ] && [ -s "$KEYFILE_ANTHROPIC" ] && TABLE_URL="$U" CLAUDE_KEY_FILE="$L/claude-seat.key" ANTHROPIC_KEY_FILE="$KEYFILE_ANTHROPIC" \
        python3 "$TT" spawn --live "$L" --port "$port" --name banter-Claude --log "$L/claude-banter.log" -- "$PY" -u "$BOTS/claude_banter.py"
    fi
    [ -z "$starter" ] && starter="$S"
  done
  [ -z "$starter" ] && starter="$A"
  sleep 1
  python3 "$TT" begin --live "$L" --port "$port" --by "$starter" || exit 1
  i=$((i + 1))
done

python3 "$TT" round --live "$L" --match "$MATCH" $(printf -- '--table %s ' "${TABLES[@]}")

# The remote player's tunnel: to THEIR table only, and only the tunnels to this round's table ports are ever touched (never the draft
# server's). A tunnel that is still up is kept (it points at the port, so the address survives a table restart). The link is written,
# copied and (by match_when_ready.sh) published ONLY after the tunnel is proved to reach this table.
i=0; while [ $i -lt $N ]; do
  p="${PORT[$i]}"; [ "$p" = "$REMOTE_PORT" ] || [ "${ACT[$i]}" != start ] || python3 "$TUN" close --live "$L" --port "$p" >/dev/null
  i=$((i + 1))
done
rc=0
if [ -n "$REMOTE_PORT" ] && [ "$REMOTE_FRESH" = 1 ]; then
  if [ "$DRY" = 1 ] && [ -z "${TUNNEL_CMD:-}" ]; then
    echo "DRYRUN: no tunnel is opened for $REMOTE (set TUNNEL_CMD to a stand-in to rehearse it): no link is written"
  else
    if python3 "$TUN" open --live "$L" --port "$REMOTE_PORT" && python3 "$TUN" link --live "$L" --port "$REMOTE_PORT" --seat "$REMOTE"; then
      [ "${NOTIFY_LINK:-0}" = 1 ] && [ "$DRY" != 1 ] && { python3 "$REPO/scripts/notify_link.py" --to "$REMOTE" --link-file "$L/$(low "$REMOTE")-link.txt" --what "your seat link for this round" --log-file "$L/notify.log" \
        || echo "NOTE: $REMOTE was not sent the link automatically (notify_link exit $?): it is on the draft page (Join the match)"; }
      if [ "${TUNNEL_WATCH:-1}" = 1 ]; then
        python3 "$TT" spawn --live "$L" --port "$REMOTE_PORT" --name tunnel-watch --sig "tunnels.py watch" --log "$L/tunnels/watch.log" -- \
          python3 -u "$TUN" watch --live "$L" --port "$REMOTE_PORT" --seat "$REMOTE"
      fi
    else
      echo "WARNING: $REMOTE HAS NO LINK. The table is up; fix the tunnel, then: python3 $TUN open --live \"$L\" --port $REMOTE_PORT --fresh && python3 $TUN link --live \"$L\" --port $REMOTE_PORT --seat $REMOTE"
      rc=4
    fi
  fi
fi

# every local seat on one page
python3 "$TT" seats-page --live "$L" ${SEATS[@]+$(printf -- '--seat %s ' "${SEATS[@]}")} ${REMOTE_PORT:+--remote "$REMOTE"}

if [ "$WAIT" = 1 ]; then
  python3 "$TT" unlock --live "$L" --pid $$ >/dev/null 2>&1
  python3 "$TT" round-wait --live "$L" --poll "${FOLLOW_POLL:-5}" || rc=$?
fi
exit $rc
