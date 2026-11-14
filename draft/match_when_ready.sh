#!/bin/bash
# Wait until everyone in the round has pressed "I'm ready" on the draft page, start the game table(s) with launch/play_round.sh (or
# launch/play_four.sh), then hand each human their OWN seat link through the draft page ("Join the match"): nobody has to be sent a link by hand.
#
#   ./match_when_ready.sh Michael:Sam Claude:Fusion        two 1v1 tables, as play_round.sh (ports 8800,8801)
#   ./match_when_ready.sh Michael:Claude                    ONE table: a round is one pairing or several (2026-11-14)
#   ./match_when_ready.sh Michael:Fusion Claude:Sam=bot     Sam is not here: the rules autopilot plays his seat (the table says so), and
#                                                           this script does not wait for his "I'm ready" and publishes no link for him
#   ./match_when_ready.sh --four                            one four-player table, as play_four.sh (port 8800)
#   ./match_when_ready.sh --publish-only Michael:Sam Claude:Fusion   re-publish the links now on disk (e.g. after Sam's tunnel moved)
#   ./match_when_ready.sh --dry-run --draft-port 8897 Michael:Sam Claude:Fusion     rehearsal, see below
# Options: --force (replace games still running: passed to the play script)   --ports A,B,… / --port N (table ports)
#   --restart PORT[,PORT] (restart that table although its match is still being played or is over: passed to play_round.sh, which
#     otherwise leaves such a table alone: a finished match is never replayed and a match in progress is never interrupted)
#   --draft-port N (the draft server, default 8820)   --wait-secs N (give up after N s; default: wait until ^C)   --poll N (seconds)
#   --follow (after publishing, keep re-publishing if a link file changes: the tunnel watcher rewrites sam-link.txt when the host moves;
#     and, since 2026-11-14, FOLLOW THE MATCHES: print each game's result and each match's result as the tables report them
#     (GET /api/match on 127.0.0.1, public, no key), put the match score beside each player's Join the match button, and exit 0 when
#     every table's match is over)
#   --single (one game per table, as before 2026-11-14)   --countdown N (seconds between two games of a match; default 25)
#
# A 1v1 table plays a MATCH: best two out of three, on the same table process. When a game ends the table itself starts the next one
# (TABLE_MATCH=bo3 is exported to the play script, and table/server.py reads it): same seat keys, same links, no new tunnel, nobody
# re-joins; the loser chooses to play or draw first and there is no roll after game 1. --four is one free-for-all game, as before.
#
# What it does, in order:
#   1. withdraws any match link left on the draft page from an earlier round, then polls the draft server (host-only
#      GET /api/ready/state) until every HUMAN named in the pairings is ready. Claude and Fusion (and any agent named) are marked
#      ready by this script with THEIR OWN draft tokens (live/draft-agent-tokens.json), so it never waits on an agent; bots never block.
#   2. converts live/deck-<name>.txt (which the draft server writes) to live/decks/<name>-g5.json for each seat, as play_four.sh does
#      (play_round.sh expects them to exist; play_four.sh converts its own).
#   3. runs play_round.sh / play_four.sh with the same pairings; stops if it fails or refuses (its launch lock, a short deck).
#   4. reads the seat links that run wrote (live/<seat>-seat.key for a local seat, live/<seat>-link.txt for the remote one: Sam's tunnel link;
#      each must be newer than this launch, so an old game's link is never handed out) and publishes each human's own link with the
#      host-only POST /api/match/publish. A player's draft page then shows "Join the match" with just their link.
#      The remote player's link is published ONLY if its address answers from his table right now (launch/tunnels.py check): on
#      2026-11-13 a link to a dead tunnel address was published twice. No proof, no link on the page, and this script says so (exit 4).
# Keys are read from 0600 files inside python and sent to 127.0.0.1 only: never printed, never in argv, never in a query string.
# A link lands only on its own player's page (GET /api/match?k=<that player's draft token>).
#
# --dry-run: for rehearsal. Needs LIVE_DIR (a scratch dir, never the real live/) and an explicit --draft-port (a scratch draft server,
#   never 8820); table ports default to 8861,8862 and the ports 8800 8801 8820 8830 8883 are refused. Sets DRYRUN=1 NO_CLIPBOARD=1
#   NO_NOTIFY=1 TUNNEL_WATCH=0 CLAUDE_BANTER=0 for the play script. play_four.sh has no dry mode (it opens a real tunnel), so --four
#   with --dry-run needs PLAY_FOUR=<a stand-in>. PLAY_ROUND / PLAY_FOUR / DECK_TOOL / PLAY_DIR / TUNNELS override where the pieces live
#   (default: play_round.sh and play_four.sh beside this script if they are there, else ../launch/ of this repository).
#   LIVE_DIR is the live folder (default: live/ beside this script if it exists, else live/ beside the draft folder, where the draft
#   server keeps its own).
# Exit: 0 published   1 usage/refused   2 gave up waiting   3 draft server problem   4 the table started but a link could not be published
set -u
HERE="$(cd "$(dirname "$0")" && pwd -P)"
PLAY_DIR="${PLAY_DIR:-$HERE}"
cd "$PLAY_DIR" || exit 1
if [ -n "${LIVE_DIR:-}" ]; then L="$LIVE_DIR"; elif [ -d "$PWD/live" ]; then L="$PWD/live"; else L="$(cd "$HERE/.." && pwd -P)/live"; fi
DRY=0; FOUR=0; FORCE=""; DPORT=""; PORTS=""; WAIT=0; POLL=3; FOLLOW=0; PUBONLY=0; SINGLE=0; COUNTDOWN=""; RESTART=""; ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --four) FOUR=1 ;;
    --force) FORCE=--force ;;
    --follow) FOLLOW=1 ;;
    --publish-only) PUBONLY=1 ;;
    --single) SINGLE=1 ;;
    --restart) RESTART="${2:-}"; shift ;;
    --countdown) COUNTDOWN="${2:-}"; shift ;;
    --draft-port) DPORT="${2:-}"; shift ;;
    --ports|--port) PORTS="${2:-}"; shift ;;
    --wait-secs) WAIT="${2:-}"; shift ;;
    --poll) POLL="${2:-}"; shift ;;
    -h|--help) sed -n 2,48p "$HERE/$(basename "$0")"; exit 0 ;;
    -*) echo "unknown option $1 (see --help)"; exit 1 ;;
    *) ARGS+=("$1") ;;
  esac
  shift
done
set -- ${ARGS[@]+"${ARGS[@]}"}
isnum() { case "${1:-}" in ""|*[!0-9]*) return 1 ;; *) return 0 ;; esac; }
low() { echo "$1" | tr 'A-Z' 'a-z'; }

[ -n "$DPORT" ] || { [ "$DRY" = 1 ] && { echo "--dry-run needs an explicit --draft-port (a scratch draft server, never the live one)"; exit 1; }; DPORT=8820; }
isnum "$DPORT" || { echo "bad --draft-port"; exit 1; }
isnum "$WAIT" || { echo "bad --wait-secs"; exit 1; }
[ -z "$COUNTDOWN" ] || isnum "$COUNTDOWN" || { echo "bad --countdown"; exit 1; }
case "$POLL" in ""|*[!0-9.]*) echo "bad --poll"; exit 1 ;; esac

# the table ports and the pairings (a seat written Name=bot is played by the rules autopilot: nobody waits for it, nobody gets its link)
PAIRS=(); BOTLIST=""; PORTLIST=()
if [ "$FOUR" = 1 ]; then
  [ $# -eq 0 ] || { echo "--four takes no pairings"; exit 1; }
  [ -n "$PORTS" ] || { [ "$DRY" = 1 ] && PORTS=8861 || PORTS=8800; }
  isnum "$PORTS" || { echo "--port needs one number"; exit 1; }
  P0=$PORTS; SEATLIST="Michael Sam Claude Fusion"; PORTLIST=("$P0")
else
  [ $# -ge 1 ] || { echo "usage: $0 [options] A:B [C:D …]     (or --four)   see --help"; exit 1; }
  SEATLIST=""
  for pair in "$@"; do
    [[ "$pair" =~ ^[A-Za-z]+(=bot)?:[A-Za-z]+(=bot)?$ ]] || { echo "bad pairing '$pair' (want Name:Name; Name=bot for a seat the autopilot plays)"; exit 1; }
    PAIRS+=("$pair")
    for side in "${pair%%:*}" "${pair##*:}"; do
      n="${side%=bot}"
      case " $SEATLIST " in *" $n "*) echo "$n is in two pairings"; exit 1 ;; esac
      SEATLIST="${SEATLIST:+$SEATLIST }$n"
      [ "$side" = "$n" ] || BOTLIST="${BOTLIST:+$BOTLIST }$n"
    done
  done
  if [ -z "$PORTS" ]; then
    base=$([ "$DRY" = 1 ] && echo 8861 || echo 8800); PORTS=$base; i=1
    while [ $i -lt ${#PAIRS[@]} ]; do PORTS="$PORTS,$((base + i))"; i=$((i + 1)); done
  fi
  IFS=, read -r -a PORTLIST <<< "$PORTS"
  [ ${#PORTLIST[@]} -ge ${#PAIRS[@]} ] || { echo "--ports needs ${#PAIRS[@]} different numbers (one per pairing)"; exit 1; }
  PORTLIST=("${PORTLIST[@]:0:${#PAIRS[@]}}")
  for p in "${PORTLIST[@]}"; do isnum "$p" || { echo "--ports needs ${#PAIRS[@]} different numbers (one per pairing)"; exit 1; }; done
  [ "$(printf '%s\n' "${PORTLIST[@]}" | sort -u | wc -l | tr -d ' ')" = "${#PAIRS[@]}" ] || { echo "--ports needs ${#PAIRS[@]} different numbers (one per pairing)"; exit 1; }
  PORTS="$(IFS=,; echo "${PORTLIST[*]}")"
  P0="${PORTLIST[0]}"
fi

# a rehearsal can never touch the live draft, the live tables, or the real live/ directory
if [ "$DRY" = 1 ]; then
  [ -n "${LIVE_DIR:-}" ] || { echo "--dry-run needs LIVE_DIR set to a scratch directory"; exit 1; }
  REAL_LIVE="$(cd "$HOME/.divinci/star-trek-mtg/live" 2>/dev/null && pwd -P)"
  SCRATCH="$(cd "$L" 2>/dev/null && pwd -P)" || { echo "LIVE_DIR $L does not exist"; exit 1; }
  { [ "$SCRATCH" = "$REAL_LIVE" ] || [ "$SCRATCH" = "$HERE/live" ]; } && { echo "--dry-run refuses the real live/ directory"; exit 1; }
  for p in $DPORT "${PORTLIST[@]}"; do case "$p" in 8800|8801|8820|8830|8883) echo "--dry-run refuses port $p (a real service's port)"; exit 1 ;; esac; done
  if [ "$FOUR" = 1 ] && [ -z "${PLAY_FOUR:-}" ]; then echo "--four --dry-run needs PLAY_FOUR=<stand-in script>: play_four.sh has no dry mode (it opens a real tunnel)"; exit 1; fi
  export DRYRUN=1 NO_CLIPBOARD=1 NO_NOTIFY=1 TUNNEL_WATCH=0 CLAUDE_BANTER=0
  # a rehearsal has no tunnel, so a remote link cannot be proved: it is published (to the SCRATCH draft server) unproved, and this
  # says so. With a stand-in edge (TUNNEL_PROBE_VIA: launch/fake_edge.py) the proof is rehearsed too.
  [ -n "${TUNNEL_PROBE_VIA:-}" ] || export PROVE_LINKS=0
  echo "DRY RUN: scratch live dir $SCRATCH, draft server :$DPORT, tables :$(echo "$PORTS" | sed 's/,/,:/g'); no real tunnel, no clipboard."
fi
export LIVE_DIR="$L"
LAUNCH="$PLAY_DIR"; [ -f "$LAUNCH/play_round.sh" ] || LAUNCH="$(cd "$HERE/.." && pwd -P)/launch"   # beside this script (the old flat layout), else the repository's launch/
PLAY_ROUND="${PLAY_ROUND:-$LAUNCH/play_round.sh}"
PLAY_FOUR="${PLAY_FOUR:-$LAUNCH/play_four.sh}"
export TUNNELS="${TUNNELS:-$LAUNCH/tunnels.py}"
DECK_TOOL="${DECK_TOOL:-$PLAY_DIR/draft/make_deck_json.py}"
[ -f "$DECK_TOOL" ] || DECK_TOOL="$HERE/make_deck_json.py"
DECKS="${DECK_DIR:-$L/decks}"
export DRAFT_URL="http://127.0.0.1:$DPORT" L SEATLIST BOTLIST WAIT POLL FOLLOW
export FOLLOW_POLL="${FOLLOW_POLL:-5}"
export REMOTE="${REMOTE_SEAT:-Sam}"
START=$(date +%s)

# the draft's own view of who is a human, an agent or a bot comes from the server; here only the seat names
TABLES=""; ROUNDTEXT=""
if [ "$FOUR" = 1 ]; then for s in $SEATLIST; do TABLES="$TABLES $s=$P0"; done
else
  i=0
  for pair in "${PAIRS[@]}"; do
    a="${pair%%:*}"; b="${pair##*:}"; pt="${PORTLIST[$i]}"
    TABLES="$TABLES ${a%=bot}=$pt ${b%=bot}=$pt"
    one="${a%=bot}$([ "$a" = "${a%=bot}" ] || echo " (autopilot)") vs ${b%=bot}$([ "$b" = "${b%=bot}" ] || echo " (autopilot)") on :$pt"
    i=$((i + 1))
    if [ $i -eq 1 ]; then ROUNDTEXT="$one"; elif [ $i -eq ${#PAIRS[@]} ]; then ROUNDTEXT="$ROUNDTEXT$([ ${#PAIRS[@]} -gt 2 ] && echo ",") and $one"; else ROUNDTEXT="$ROUNDTEXT, $one"; fi
  done
fi
export TABLES

# 2026-11-13 retro: the host started ONE four-player table when the players had agreed on 1v1. This script has no default format (it wants
# --four or two pairings), but it also never SAID which it was about to start, and it then waits, unattended, for the last "I'm ready".
# Say it at the top (while ^C costs nothing) and again in the line that starts the tables.
if [ "$FOUR" = 1 ]; then PLAN="ONE four-player table (free-for-all): $(echo $SEATLIST | sed 's/ /, /g') on :$P0"
else
  case ${#PAIRS[@]} in 1) HOWMANY="ONE 1v1 table" ;; 2) HOWMANY="TWO 1v1 tables" ;; 3) HOWMANY="THREE 1v1 tables" ;; 4) HOWMANY="FOUR 1v1 tables" ;; *) HOWMANY="${#PAIRS[@]} 1v1 tables" ;; esac
  PLAN="$HOWMANY: $(echo "$ROUNDTEXT" | sed 's/ and /, and /; s/,, and /, and /')"
fi
# 2026-11-14: a 1v1 table plays a match (best two out of three) unless --single. The table reads TABLE_MATCH; the play script passes the
# environment on to it. A four-player table is never a match (the table itself would say so and play one game).
if [ "$FOUR" = 0 ] && [ "$SINGLE" = 0 ]; then
  export TABLE_MATCH=bo3
  [ -z "$COUNTDOWN" ] || export TABLE_MATCH_COUNTDOWN="$COUNTDOWN"
  PLAN="$PLAN. $([ ${#PAIRS[@]} -eq 1 ] && echo "It is" || echo "Each is") a MATCH: best two out of three, the next game starting by itself on the same table"
else
  export TABLE_MATCH=single
  [ "$FOUR" = 1 ] || PLAN="$PLAN. ONE game $([ ${#PAIRS[@]} -eq 1 ] && echo "only" || echo "each") (--single)"
fi
[ "$PUBONLY" = 1 ] || echo "FORMAT: $PLAN. They start as soon as everyone is ready (^C now if that is not what was agreed)."

if [ "$PUBONLY" = 0 ]; then
  # 1. withdraw an old match link, then wait for the humans (and ready the agents with their own tokens)
  python3 - <<'E' || exit $?
import json, os, sys, time, urllib.error, urllib.request

base, L = os.environ["DRAFT_URL"], os.environ["L"]
want = list(dict.fromkeys(os.environ["SEATLIST"].split()))
absent = set(os.environ.get("BOTLIST", "").split())    # Name=bot: the autopilot plays that seat; its "I'm ready" is not waited for
limit, poll = float(os.environ["WAIT"]), float(os.environ["POLL"])
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))       # 127.0.0.1 never goes through a proxy (it would look tunnelled)


def call(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path + (("?k=" + token) if token else ""), data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data is not None else {})
    try:
        with opener.open(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, OSError, ValueError):
        return 0, {}


def die(msg, code=3):
    print(msg, flush=True)
    sys.exit(code)


try:
    tokens = json.load(open(os.path.join(L, "draft-agent-tokens.json")))
except (OSError, ValueError):
    tokens = {}
code, _ = call("POST", "/api/match/publish", {"clear": True})
if code == 403:
    die("the draft server refused a host route: --draft-port must be the local draft server itself, not a tunnel")
if code == 0:
    print("the draft server is not answering yet; will keep trying", flush=True)
t0, fails, last = time.time(), 0, None
while True:
    code, st = call("GET", "/api/ready/state")
    if code == 403:
        die("the draft server refused a host route: --draft-port must be the local draft server itself, not a tunnel")
    if code != 200:
        fails += 1
        if fails > 40:
            die("the draft server stopped answering: not starting anything")
        msg = "the draft server is not answering"
    else:
        fails = 0
        players = st["players"]
        unknown = [n for n in want if n not in players]
        if unknown:
            die(f"not a seat in this draft: {', '.join(unknown)} (a bot seat has no deck to play): not starting anything", 1)
        if not st["draft_done"]:
            msg = "the draft is not over yet"
        else:
            for n in want:
                if players[n]["kind"] == "agent" and not players[n]["ready"]:
                    if not tokens.get(n):
                        die(f"no draft token for {n} in draft-agent-tokens.json: cannot mark it ready")
                    c, r = call("POST", "/api/ready", {"ready": True}, token=tokens[n])
                    if c != 200:
                        die(f"{n} could not be marked ready: {r.get('error') or 'HTTP %s' % c}")
                    players[n]["ready"] = True
            waiting = [n for n in want if not players[n]["ready"] and n not in absent]
            if not waiting:
                time.sleep(0.5)                                 # one more look: a last-second Unready or deck edit must not start a match
                c2, st2 = call("GET", "/api/ready/state")
                if c2 == 200 and all(st2["players"].get(n, {}).get("ready") for n in want if st2["players"].get(n, {}).get("kind") != "agent" and n not in absent):
                    print("everyone is ready: " + ", ".join(n + (" (autopilot: not waited for)" if n in absent else "") for n in want), flush=True)
                    sys.exit(0)
                continue
            msg = "waiting on " + ", ".join(f"{n} ({players[n]['total']} cards)" for n in waiting)
    if msg != last:
        print(msg, flush=True)
        last = msg
    if limit and time.time() - t0 > limit:
        die("gave up waiting: nobody was started", 2)
    time.sleep(poll)
E

  # 2. decks: live/deck-<name>.txt -> $DECKS/<name>-g5.json (the draft server writes the .txt; play_round.sh wants the .json)
  if [ "$FOUR" = 0 ]; then
    mkdir -p "$DECKS"
    for s in $SEATLIST; do
      n=$(awk '{s+=$1} END{print s+0}' "$L/deck-$(low "$s").txt" 2>/dev/null)
      [ "${n:-0}" -ge 40 ] || { echo "deck-$(low "$s").txt has ${n:-0} cards, under 40: not starting"; exit 1; }
      python3 "$DECK_TOOL" "$L/deck-$(low "$s").txt" "$DECKS/$(low "$s")-g5.json" "$s draft deck" >/dev/null || { echo "could not convert $s's deck: not starting"; exit 1; }
    done
  fi

  # 3. the table(s)
  echo "STARTING: $PLAN"
  if [ "$FOUR" = 1 ]; then
    "$PLAY_FOUR" ${FORCE:+$FORCE} --port "$P0"
  else
    DECK_DIR="$DECKS" "$PLAY_ROUND" ${FORCE:+$FORCE} ${RESTART:+--restart "$RESTART"} --ports "$PORTS" "${PAIRS[@]}"
  fi
  rc=$?
  # (4: the tables are up but the remote player's tunnel could not be proved: his link is not published below, everyone else's is)
  [ $rc -eq 0 ] || [ $rc -eq 4 ] || { echo "the table launcher exited $rc: not publishing any link"; exit 1; }
fi

# 4. each human's own link, from the files the launcher wrote
export START FRESH=$([ "$PUBONLY" = 1 ] && echo 0 || echo 1)
python3 - <<'E'
import json, os, subprocess, sys, time, urllib.error, urllib.parse, urllib.request

base, L = os.environ["DRAFT_URL"], os.environ["L"]
remote = os.environ["REMOTE"].lower()
absent = set(os.environ.get("BOTLIST", "").split())
unproved = set()
start, fresh = float(os.environ["START"]) - 1, os.environ["FRESH"] == "1"
port = dict(t.split("=") for t in os.environ["TABLES"].split())
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def state():
    with opener.open(base + "/api/ready/state", timeout=10) as r:
        return json.loads(r.read())["players"]


def read_private(name):
    f = os.path.join(L, name)
    if not os.path.exists(f):
        raise LookupError(f"{name} does not exist")
    if fresh and os.path.getmtime(f) < start:
        raise LookupError(f"{name} is older than this launch")
    return open(f).read().strip()


def links_now(humans):
    out, missing = {}, []
    for n in humans:
        try:
            if n.lower() == remote:
                link = read_private(f"{n.lower()}-link.txt")
                # never a link whose address does not answer from this player's table right now (a dead tunnel, another table)
                tun = os.environ.get("TUNNELS") or ""
                if os.environ.get("PROVE_LINKS") == "0":
                    if n not in unproved:
                        unproved.add(n)
                        print(f"DRY RUN: {n}'s link is not proved (a rehearsal has no tunnel)", flush=True)
                elif os.path.isfile(tun):
                    r = subprocess.run([sys.executable, tun, "check", "--live", L, "--port", str(port[n]), "--seat", n], capture_output=True, text=True)
                    if r.returncode != 0:
                        raise LookupError("the link's address does not reach this table (" + (r.stdout.strip().splitlines() or ["no answer"])[-1][:160] + ")")
                out[n] = link
            else:
                out[n] = f"http://127.0.0.1:{port[n]}/me?player={urllib.parse.quote(n)}#key={urllib.parse.quote(read_private(f'{n.lower()}-seat.key'))}"
        except LookupError as e:
            missing.append(f"{n}: {e}")
    return out, missing


def publish(links):
    req = urllib.request.Request(base + "/api/match/publish", method="POST", data=json.dumps(
        {"links": links, "note": "Your match is ready. Press Join the match."}).encode(), headers={"Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=10) as r:
            return json.loads(r.read())["published"]
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error")
        except ValueError:
            msg = None
        print(f"the draft server refused the links: {msg or 'HTTP %s' % e.code}", flush=True)
        sys.exit(4)
    except (urllib.error.URLError, OSError, ValueError):
        print("the draft server did not answer: the table is up but no link was published", flush=True)
        sys.exit(4)


try:
    players = state()
except (urllib.error.URLError, OSError, ValueError, KeyError):
    print("the draft server did not answer: the table is up but no link was published", flush=True)
    sys.exit(4)
humans = [n for n in dict.fromkeys(os.environ["SEATLIST"].split()) if players.get(n, {}).get("kind") == "human" and n not in absent]
links, missing = links_now(humans)
if not humans:
    print("no person plays in this round (every seat is an AI's or the autopilot's): no link to publish", flush=True)
elif not links:
    print("no link could be read (" + "; ".join(missing) + "): nothing published", flush=True)
    sys.exit(4)
else:
    names = publish(links)
    print("published the match link for: " + ", ".join(names) + " (each sees only their own on the draft page)", flush=True)
rc = 0
if missing:
    print("NOT published: " + "; ".join(missing) + ". Fix it, then run this script again with --publish-only.", flush=True)
    rc = 4
if os.environ["FOLLOW"] == "1":
    fresh = False                                               # the watcher rewrites a link file after launch: whatever is on disk now is current
    print("following: will re-publish if a link file changes, and report each game and match (^C to stop)", flush=True)
    # The tables, by port, with who sits at each. A table tells everyone its match at GET /api/match (public, no key, loopback here).
    seats_at = {}
    for n, p in port.items():
        seats_at.setdefault(p, []).append(n)
    seen = {p: {"games": 0, "over": False, "single": None, "score": None, "down": 0} for p in seats_at}
    poll = max(0.2, float(os.environ.get("FOLLOW_POLL") or 5))

    def table(p, path):
        try:
            with opener.open(f"http://127.0.0.1:{p}{path}", timeout=4) as r:
                return json.loads(r.read() or b"{}")
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def push_scores(scores):
        req = urllib.request.Request(base + "/api/match/publish", method="POST", data=json.dumps({"scores": scores}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with opener.open(req, timeout=10):
                return True
        except (urllib.error.URLError, OSError, ValueError):   # an older draft server has no scores: the match goes on without them
            return False

    def follow_tables():
        """One look at every table. Prints what is new; returns True when every table has finished (a match over, or a single game over)."""
        changed = {}
        for p, who in seats_at.items():
            st = seen[p]
            if st["over"]:
                continue
            m = table(p, "/api/match")
            if m is None:
                st["down"] += 1
                if st["down"] == 6:
                    print(f"table :{p} ({' vs '.join(who)}) has not answered for a while: is it still running?", flush=True)
                continue
            st["down"] = 0
            if not m.get("on"):                                 # a single game (--single, a four-player table, or an older table)
                if st["single"] is None:
                    st["single"] = True
                    print(f"table :{p} ({' vs '.join(who)}) plays a single game", flush=True)
                g = table(p, "/api/game/summary") or {}
                if g.get("over"):
                    st["over"] = True
                    print(f"table :{p}: GAME OVER: {g.get('winner') or 'nobody'} wins" + (f" in {(g.get('summary') or {}).get('turns')} turns" if (g.get("summary") or {}).get("turns") else ""), flush=True)
                continue
            for g in (m.get("games") or [])[st["games"]:]:
                res = "a draw" if g.get("draw") else f"{g.get('winner')} wins"
                print(f"table :{p}: game {g.get('n')}: {res} in {g.get('turns')} turns ({', '.join(f'{n} {v}' for n, v in (g.get('totals') or {}).items())})", flush=True)
            st["games"] = len(m.get("games") or [])
            if m.get("score") != st["score"] and st["games"]:
                st["score"] = m.get("score")
                for n in who:
                    if n in humans:
                        changed[n] = st["score"]
            if m.get("over"):
                st["over"] = True
                print(f"table :{p}: MATCH OVER: {m.get('score')}", flush=True)
            elif m.get("next") and (m["next"].get("n"), "next") != st.get("said"):
                st["said"] = (m["next"].get("n"), "next")
                print(f"table :{p}: {m.get('score')}; game {m['next'].get('n')} starts by itself on the same table ({m['next'].get('chooser')} chooses play or draw)", flush=True)
        if changed:
            push_scores(changed)
        return all(st["over"] for st in seen.values())

    try:
        last_links = time.time()
        while True:
            time.sleep(poll)
            if follow_tables():
                print("every table has finished: " + "; ".join(f":{p} {st['score'] or 'game over'}" for p, st in seen.items()), flush=True)
                break
            if time.time() - last_links >= 20:
                last_links = time.time()
                now, _ = links_now(humans)
                if now and now != links:
                    links = now
                    print("a link changed: re-published for " + ", ".join(publish(links)), flush=True)
    except KeyboardInterrupt:
        pass
sys.exit(rc)
E
