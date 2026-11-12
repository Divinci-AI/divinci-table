#!/bin/bash
# Wait until everyone in the match has pressed "I'm ready" on the draft page, start the game table(s) with play_round.sh (or play_four.sh),
# then hand each human their OWN seat link through the draft page ("Join the match"): nobody has to be sent a link by hand.
#
#   ./match_when_ready.sh Michael:Sam Claude:Fusion        two 1v1 tables, as play_round.sh (ports 8800,8801)
#   ./match_when_ready.sh --four                            one four-player table, as play_four.sh (port 8800)
#   ./match_when_ready.sh --publish-only Michael:Sam Claude:Fusion   re-publish the links now on disk (e.g. after Sam's tunnel moved)
#   ./match_when_ready.sh --dry-run --draft-port 8897 Michael:Sam Claude:Fusion     rehearsal, see below
# Options: --force (replace games still running: passed to the play script)   --ports A,B / --port N (table ports)
#   --draft-port N (the draft server, default 8820)   --wait-secs N (give up after N s; default: wait until ^C)   --poll N (seconds)
#   --follow (after publishing, keep re-publishing if a link file changes: the tunnel watcher rewrites sam-link.txt when the host moves)
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
# Keys are read from 0600 files inside python and sent to 127.0.0.1 only: never printed, never in argv, never in a query string.
# A link lands only on its own player's page (GET /api/match?k=<that player's draft token>).
#
# --dry-run: for rehearsal. Needs LIVE_DIR (a scratch dir, never the real live/) and an explicit --draft-port (a scratch draft server,
#   never 8820); table ports default to 8861,8862 and the ports 8800 8801 8820 8830 8883 are refused. Sets DRYRUN=1 NO_CLIPBOARD=1
#   NO_NOTIFY=1 TUNNEL_WATCH=0 CLAUDE_BANTER=0 for the play script. play_four.sh has no dry mode (it opens a real tunnel), so --four
#   with --dry-run needs PLAY_FOUR=<a stand-in>. PLAY_ROUND / PLAY_FOUR / DECK_TOOL / PLAY_DIR override where the pieces live.
# Exit: 0 published   1 usage/refused   2 gave up waiting   3 draft server problem   4 the table started but a link could not be published
set -u
HERE="$(cd "$(dirname "$0")" && pwd -P)"
PLAY_DIR="${PLAY_DIR:-$HERE}"
cd "$PLAY_DIR" || exit 1
L="${LIVE_DIR:-$PWD/live}"
DRY=0; FOUR=0; FORCE=""; DPORT=""; PORTS=""; WAIT=0; POLL=3; FOLLOW=0; PUBONLY=0; ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --four) FOUR=1 ;;
    --force) FORCE=--force ;;
    --follow) FOLLOW=1 ;;
    --publish-only) PUBONLY=1 ;;
    --draft-port) DPORT="${2:-}"; shift ;;
    --ports|--port) PORTS="${2:-}"; shift ;;
    --wait-secs) WAIT="${2:-}"; shift ;;
    --poll) POLL="${2:-}"; shift ;;
    -h|--help) sed -n 2,34p "$0"; exit 0 ;;
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
case "$POLL" in ""|*[!0-9.]*) echo "bad --poll"; exit 1 ;; esac

# the table ports and the pairings
if [ "$FOUR" = 1 ]; then
  [ $# -eq 0 ] || { echo "--four takes no pairings"; exit 1; }
  [ -n "$PORTS" ] || { [ "$DRY" = 1 ] && PORTS=8861 || PORTS=8800; }
  isnum "$PORTS" || { echo "--port needs one number"; exit 1; }
  P0=$PORTS; P1=$PORTS; SEATLIST="Michael Sam Claude Fusion"
else
  [ $# -eq 2 ] || { echo "usage: $0 [options] A:B C:D     (or --four)   see --help"; exit 1; }
  for pair in "$1" "$2"; do [[ "$pair" =~ ^[A-Za-z]+:[A-Za-z]+$ ]] || { echo "bad pairing '$pair' (want Name:Name)"; exit 1; }; done
  [ -n "$PORTS" ] || { [ "$DRY" = 1 ] && PORTS=8861,8862 || PORTS=8800,8801; }
  P0=${PORTS%%,*}; P1=${PORTS##*,}
  isnum "$P0" && isnum "$P1" && [ "$P0" != "$P1" ] || { echo "--ports needs two different numbers"; exit 1; }
  SEATLIST="${1%%:*} ${1##*:} ${2%%:*} ${2##*:}"
fi

# a rehearsal can never touch the live draft, the live tables, or the real live/ directory
if [ "$DRY" = 1 ]; then
  [ -n "${LIVE_DIR:-}" ] || { echo "--dry-run needs LIVE_DIR set to a scratch directory"; exit 1; }
  REAL_LIVE="$(cd "$HOME/.divinci/star-trek-mtg/live" 2>/dev/null && pwd -P)"
  SCRATCH="$(cd "$L" 2>/dev/null && pwd -P)" || { echo "LIVE_DIR $L does not exist"; exit 1; }
  { [ "$SCRATCH" = "$REAL_LIVE" ] || [ "$SCRATCH" = "$HERE/live" ]; } && { echo "--dry-run refuses the real live/ directory"; exit 1; }
  for p in $DPORT $P0 $P1; do case "$p" in 8800|8801|8820|8830|8883) echo "--dry-run refuses port $p (a real service's port)"; exit 1 ;; esac; done
  if [ "$FOUR" = 1 ] && [ -z "${PLAY_FOUR:-}" ]; then echo "--four --dry-run needs PLAY_FOUR=<stand-in script>: play_four.sh has no dry mode (it opens a real tunnel)"; exit 1; fi
  export DRYRUN=1 NO_CLIPBOARD=1 NO_NOTIFY=1 TUNNEL_WATCH=0 CLAUDE_BANTER=0
  echo "DRY RUN: scratch live dir $SCRATCH, draft server :$DPORT, tables :$P0$([ "$P0" != "$P1" ] && echo ",:$P1"); no real tunnel, no clipboard."
fi
export LIVE_DIR="$L"
PLAY_ROUND="${PLAY_ROUND:-$PLAY_DIR/play_round.sh}"
PLAY_FOUR="${PLAY_FOUR:-$PLAY_DIR/play_four.sh}"
DECK_TOOL="${DECK_TOOL:-$PLAY_DIR/draft/make_deck_json.py}"
[ -f "$DECK_TOOL" ] || DECK_TOOL="$HERE/make_deck_json.py"
DECKS="${DECK_DIR:-$L/decks}"
export DRAFT_URL="http://127.0.0.1:$DPORT" L SEATLIST WAIT POLL FOLLOW
export REMOTE="${REMOTE_SEAT:-Sam}"
START=$(date +%s)

# the draft's own view of who is a human, an agent or a bot comes from the server; here only the seat names
if [ "$FOUR" = 1 ]; then TABLES=""; for s in $SEATLIST; do TABLES="$TABLES $s=$P0"; done; else TABLES="${1%%:*}=$P0 ${1##*:}=$P0 ${2%%:*}=$P1 ${2##*:}=$P1"; fi
export TABLES

if [ "$PUBONLY" = 0 ]; then
  # 1. withdraw an old match link, then wait for the humans (and ready the agents with their own tokens)
  python3 - <<'E' || exit $?
import json, os, sys, time, urllib.error, urllib.request

base, L = os.environ["DRAFT_URL"], os.environ["L"]
want = list(dict.fromkeys(os.environ["SEATLIST"].split()))
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
            waiting = [n for n in want if not players[n]["ready"]]
            if not waiting:
                time.sleep(0.5)                                 # one more look: a last-second Unready or deck edit must not start a match
                c2, st2 = call("GET", "/api/ready/state")
                if c2 == 200 and all(st2["players"].get(n, {}).get("ready") for n in want if st2["players"].get(n, {}).get("kind") != "agent"):
                    print("everyone is ready: " + ", ".join(want), flush=True)
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
  if [ "$FOUR" = 1 ]; then
    "$PLAY_FOUR" ${FORCE:+$FORCE} --port "$P0"
  else
    DECK_DIR="$DECKS" "$PLAY_ROUND" ${FORCE:+$FORCE} --ports "$P0,$P1" "$1" "$2"
  fi
  rc=$?
  [ $rc -eq 0 ] || { echo "the table launcher exited $rc: not publishing any link"; exit 1; }
fi

# 4. each human's own link, from the files the launcher wrote
export START FRESH=$([ "$PUBONLY" = 1 ] && echo 0 || echo 1)
python3 - <<'E'
import json, os, sys, time, urllib.error, urllib.parse, urllib.request

base, L = os.environ["DRAFT_URL"], os.environ["L"]
remote = os.environ["REMOTE"].lower()
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
                out[n] = read_private(f"{n.lower()}-link.txt")
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
humans = [n for n in dict.fromkeys(os.environ["SEATLIST"].split()) if players.get(n, {}).get("kind") == "human"]
links, missing = links_now(humans)
if not links:
    print("no link could be read (" + "; ".join(missing) + "): nothing published", flush=True)
    sys.exit(4)
names = publish(links)
print("published the match link for: " + ", ".join(names) + " (each sees only their own on the draft page)", flush=True)
rc = 0
if missing:
    print("NOT published: " + "; ".join(missing) + ". Fix it, then run this script again with --publish-only.", flush=True)
    rc = 4
if os.environ["FOLLOW"] == "1":
    fresh = False                                               # the watcher rewrites a link file after launch: whatever is on disk now is current
    print("following: will re-publish if a link file changes (^C to stop)", flush=True)
    try:
        while True:
            time.sleep(20)
            now, _ = links_now(humans)
            if now and now != links:
                links = now
                print("a link changed: re-published for " + ", ".join(publish(links)), flush=True)
    except KeyboardInterrupt:
        pass
sys.exit(rc)
E
