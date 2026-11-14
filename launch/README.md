# launch/: starting a round of games, and keeping score

The scripts a host runs on a game night. Until 2026-11-14 they lived only beside the live games, so they could not be tested or
reviewed; the first streamed draft night found five things wrong with them (docs: the retro ids M3 to M7 below). They are now here,
with tests, and nothing personal is built in: every folder, interpreter and key file is a setting with a safe default.

| file | what |
|---|---|
| `play_round.sh` | one ROUND of 1v1 matches: a table per pairing, one pairing or several (`A:B C:D …`; `Name=bot` for a seat the autopilot plays) |
| `play_four.sh` | one four-player table (a single game) |
| `tunnels.py` | the remote player's tunnel: open, **prove**, link, watch, close |
| `table_tools.py` | what the two launchers share: the launch lock, the plan for a round, saving a finished game, seat keys, who plays a seat, the stand-in, `round-wait` |
| `tournament.py` | rounds, pairings and standings for a pod; tells `play_round.sh` what to start; a public JSON for a stream overlay |
| `fake_tunnel.py`, `fake_edge.py` | stand-ins for `cloudflared` and for Cloudflare's edge: tests and rehearsals never open a tunnel |
| `test_*.py` | the tests (run by `table/tests/run_core.py`; each runs on its own too) |

`draft/match_when_ready.sh` is the front door on a draft night: it waits for "I'm ready" on the draft page, runs `play_round.sh`, and
puts each person's own link on their draft page.

## A night, start to end

```bash
export LIVE_DIR=~/games/live                 # keys, links, logs, saves: a private folder (0700), never in a repository
export TABLE_PY=~/venvs/table/bin/python     # the interpreter with table/requirements.txt
export TABLE_ART_DIR=~/games/art             # card art, if you have it (it is never in the repository); unset = no art
export DRAFT_CARDS_JSON=~/games/cards.json   # the drafted set, so every card can be looked up (GET /api/card)

# after the draft: wait for "I'm ready", start the round, publish the links, report each game and match, exit when the round is done
draft/match_when_ready.sh --follow Michael:Sam Claude:Fusion

# or let the tournament pair and start every round (it remembers the standings and never repeats a pairing while it can help it)
python3 launch/tournament.py new --state "$LIVE_DIR/tournament.json" --players Michael,Sam,Claude,Fusion
python3 launch/tournament.py run --state "$LIVE_DIR/tournament.json" --one-round      # again for the next round
python3 launch/tournament.py serve --state "$LIVE_DIR/tournament.json"                 # http://127.0.0.1:8890/tournament.json for the overlay
```

A table plays a **match** (best two out of three, the next game starting by itself: `docs/match.md`), so a round is launched ONCE.
Run the same launcher command again at any time: it is safe.

## What a second launch does (M4)

`play_round.sh` decides per table before it touches anything (`table_tools.py plan`):

| the table on that port | what happens |
|---|---|
| nothing is running | started |
| the SAME two players, their match still being played | **left alone** |
| the SAME two players, their match over | **not replayed** (the launcher says the score) |
| other players, their match over | saved to `live/games/…`, then replaced |
| other players, still playing | the whole launch is **refused**; nothing is touched |

`--restart PORT` (or `--force`) is how you ask for a restart. On 2026-11-13 both tables were restarted for every game, and a match
that was already decided was played on.

A table's files are named after its port (`token`, `server.log`, `saves` for :8800; `token-b`, … for :8801; `token-<port>`, …
otherwise), and its processes (the table, the autopilots, the tunnel watcher) are written down in `live/pids/<port>.json` and stopped
each by its own pid. Restarting one table never stops another table's helpers.

## Who plays a seat (M5)

The table keeps, and shows on every page, on `/watch` and in the log, who plays each seat: a person, the rules autopilot, or a model
(`GET /api/played-by`; `table/server.py`, "who plays each seat").

```bash
launch/play_round.sh Michael:Fusion Claude:Sam=bot                     # Sam is not here tonight: the autopilot stands in from the start
python3 launch/table_tools.py stand-in --live "$LIVE_DIR" --seat Sam on    # Sam left mid-match
python3 launch/table_tools.py stand-in --live "$LIVE_DIR" --seat Sam off   # stop the autopilot; the seat is Sam's
```

**The person wins.** At a seat the autopilot is standing in at, the person's first move from their own page (or the "I'm back" button
there) gives them the seat back; the log says "Sam is back", and the autopilot's moves are refused from then on. It stands by, plays
nothing, and plays on only when the host says `stand-in … on` again. The seats in `$BOT_SEATS` (default `Claude,Fusion`) are the
autopilot's own: nothing changes hands there.

## The remote player's link (M3)

A link is written to `live/<seat>-link.txt`, copied to the clipboard and published on the draft page **only after the tunnel is proved
to reach this table**: the table's own word (`GET /api/instance`, a random word made when the table process started) is read on
127.0.0.1 and asked for again through the tunnel's address. No proof: no link, exit 4, and the launcher says what to run. The top of
`tunnels.py` has the story of the bug this replaces (a dead address, published twice). Only the tunnels to this round's table ports are
ever touched; the draft server's tunnel is not.

```bash
python3 launch/tunnels.py status --live "$LIVE_DIR"                                   # what is recorded, and whether each still answers
python3 launch/tunnels.py open --live "$LIVE_DIR" --port 8800 --fresh                 # a new tunnel (a new address)
python3 launch/tunnels.py link --live "$LIVE_DIR" --port 8800 --seat Sam              # prove it, then write the link
```

A rehearsal without any tunnel: `TUNNEL_CMD="python3 launch/fake_tunnel.py --port {port} --edge-dir /tmp/edge"`,
`python3 launch/fake_edge.py --edge-dir /tmp/edge`, and `TUNNEL_PROBE_VIA=http://127.0.0.1:<the edge's port>`.

## Settings

| Env | Default | What |
|---|---|---|
| `LIVE_DIR` | `./live` | keys (0600), links, logs, saves, `round.json`, `pids/`, `tunnels/` |
| `TABLE_CODE` | this repository | the checkout the tables run from |
| `TABLE_PY` | `python3` | the interpreter for `table/server.py` (and for `claude_banter.py`, which needs `anthropic`) |
| `DECK_DIR`, `DECK_SUFFIX` | `$LIVE_DIR/decks`, `-g5` | a seat's deck is `<name><suffix>.json`; `--deck Name=FILE` names one outright |
| `TABLE_ART_DIR` | unset | card art folder (never in the repository) |
| `TABLE_CARDS_JSON` | `$DRAFT_CARDS_JSON` | the set file for the card lookup |
| `REMOTE_SEAT` | `Sam` | the player who joins through a tunnel; `""` for nobody |
| `BOT_SEATS`, `BOT_DIR` | `Claude,Fusion`, `bots/` | the autopilot's own seats; where `claude_autopilot.py` is |
| `CLAUDE_BANTER`, `ANTHROPIC_KEY_FILE` | `1`, `$LIVE_DIR/anthropic.key` | table talk for the seat named Claude, only if that 0600 file already exists. **No launcher fetches a key.** |
| `TABLE_HOSTS` | `.trycloudflare.com` | the host names a remote request may use |
| `TUNNEL_CMD` | `cloudflared tunnel --no-autoupdate --url http://127.0.0.1:{port}` | the tunnel command |
| `TUNNEL_WATCH`, `NOTIFY_LINK` | `1`, `0` | the watcher; send the proved link with `scripts/notify_link.py` |
| `DRYRUN=1` | | a rehearsal: no clipboard, no notification, no table talk, no tunnel unless `TUNNEL_CMD` is a stand-in |

## Where the old scripts went

| ran on 2026-11-13, beside the live games | now |
|---|---|
| `play_round.sh` | `launch/play_round.sh` |
| `play_four.sh` | `launch/play_four.sh` |
| `play_vs_michael.sh` (a person against Claude's seat) | `launch/play_round.sh --single --ports 8800 --deck Michael=<file> --deck Claude=<file> Michael:Claude` |
| `restart_game.sh` (bot against bot) | `launch/play_round.sh --single --ports 8800 --deck … Michael=bot:Claude` |
| `match_when_ready.sh` | `draft/match_when_ready.sh` |
| `tunnel_watch.py` | `launch/tunnels.py` (`watch`, and `open` / `link` / `check` / `close`) |
| `table_tools.py` | `launch/table_tools.py` |
| `claude_autopilot.py`, `claude_autopass.py`, `claude_banter.py`, `llm_agent.py` | `bots/` |
| the draft server folder | `draft/` |

Not brought over: `seat_advocate.py` and `llm_game.sh` (not part of a game night), and the two launchers' step that fetched an API key
from a secrets manager through a path under a home directory. Put the key file in place yourself, once.

## Tests

```bash
python3 launch/test_tunnels.py        # the proof, the watcher, one log per process (and the old watcher's rule picking the dead address)
python3 launch/test_play_round.py     # the launcher itself: real tables on free ports, stand-in tunnel
python3 launch/test_match_round.py    # draft/match_when_ready.sh: any number of pairings, Name=bot, no unproved link published
python3 launch/test_tournament.py     # pairings, standings, results from the tables
```
