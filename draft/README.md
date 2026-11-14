# draft/: a live web draft for a small table

`draft_server.py` runs a booster draft for up to eight seats (people plus bots and AI agents), builds each seat's deck (`deckbuild.py`),
and hands the finished decks to the table (`match_when_ready.sh`: everyone presses "I'm ready", the tables start, each person gets their own seat link).

```bash
python3 table/fetch_data.py --set trk                      # the set's cards (or DRAFT_CARDS_JSON=<file>)
DRAFT_HUMANS="Ann,Ben" python3 draft/draft_server.py ...     # see --help; links and decks are written under ../live/ (git-ignored)
python3 draft/test_draft.py                                 # the draft tests (also run by table/tests/run_core.py)
```

Card art is never in the repo: the server serves art from a folder you provide, and only to a valid seat key. Remote players come in through a
tunnel; the draft token and seat links are kept in 0600 files. `draft_banter.py` talks through the Anthropic API when a key file exists (0600; never in argv).
The folder layout is the author's, and the scripts are the record of how a night was run. Where things are, and how to point them elsewhere:

| What | Default | Set it with |
|---|---|---|
| the draft's state, links and decks | `live/` beside this folder | `DRAFT_LIVE` (the server, `claude_seat.py` and `fusion_seat.py` all read it) |
| the draft server, for the AI seat clients | `http://127.0.0.1:8820` | `DRAFT_URL` |
| the people drafting | Michael, Sam | `DRAFT_HUMANS="Ann,Ben"` |
| the set's cards | `cards.json` beside this folder, else `table/.cache/cards-trk.json` | `DRAFT_CARDS_JSON` |
| Claude's key for table talk | the author's two key files under `~/.divinci/star-trek-mtg/` | `ANTHROPIC_KEY_FILE` (a 0600 file); `DRAFT_BANTER=off` asks no model at all |

## The match handoff, and the one thing that breaks it

When a match starts, each person's draft page shows **Join the match** with their own seat link (`GET /api/match` with their own draft token;
`match_when_ready.sh` publishes the links with the host-only `POST /api/match/publish`, and `--follow` re-publishes one that changes). A remote
player reaches the draft page through the draft server's own tunnel, so **that tunnel has to stay up while the tables start**. On 2026-11-13 the
play scripts closed every tunnel pidfile in `live/tunnels/`, the draft's included: the remote player's page died, the button with it, and the
link went by hand. `docs/retro-20261113-launch-notes.md` has the diffs for the play scripts (they live outside this repo), the format line
`match_when_ready.sh` now prints before it starts anything (`FORMAT: TWO 1v1 tables: ...` / `ONE four-player table ...`), and
`scripts/notify_link.py`, which sends a player their link from a 0600 file when the page cannot.

```bash
python3 draft/test_match_format.py                          # the format line, in rehearsal mode (nothing is started)
python3 draft/test_banter_guard.py                          # table talk never names a card during a draft (typed lines too)
```

## Restarting the server in the middle of a draft (`--keep`)

`python3 draft/draft_server.py --keep [--bot Sam]` reads the saved state (`draft-state.json`), replays every finished step, and carries
on. If the restart changed who the draft waits on (`--bot Sam`: Sam went to bed after the others had picked) and every seat still
waited on has its pick in, that step is advanced at once: nobody has to send a pick again (before 2026-11-14 the draft sat still
until someone did). The start-up line says so: `restored: pack 1, pick 2, 0 steps replayed; 1 more advanced now (...)`.
`DRAFT_BANTER=off` keeps the AI seats from talking by themselves (no model, local or cloud, is asked for a line).

Every key is the same after `--keep`: the people's links and the broadcast key (`draft-links.txt`) and, since 2026-11-14, the AI
seats' tokens (`draft-agent-tokens.json`). A running `claude_seat.py` or `fusion_seat.py` reads its token once, so it used to be
refused after every restart and had to be restarted too; now it just carries on. Both files are 0600 and replaced whole. A seat
handed to a bot at the restart (`--bot`) loses its key. Two seats never share a key and no seat's key opens the broadcast, even if a
file was edited by hand: the people's links win, then the broadcast's key, and an AI seat whose saved token is in use gets a new one.

```bash
python3 draft/test_keep_restart.py                          # the owed step, in-process and with the real program restarted
```

## The broadcast: the draft as a stream's scene

`/broadcast?k=<broadcast key>` (JSON at `/api/broadcast`) is the page to capture: the named seats, how many cards each has taken, and
the picks the reveal rule allows (`--broadcast-reveal pack-end | delayed | ai-live | draft-end`, `--broadcast-delay N|end`; the top of
`draft_server.py` says what each still leaks to a drafter who is watching). The key is its own secret, the line `Broadcast: ...` of the
0600 links file: it opens no seat route, and no seat key opens it. It never carries a pack's contents, what was passed, a key, a card
id or art, or anything about a pick that is not revealed yet except that it exists. Table talk is repeated only when the line names no card.

A named seat handed to a bot (`--bot Sam`) keeps its column, tagged **bot**, with the number of cards it has taken, each face down
(before 2026-11-14 the column vanished: an all-AI rehearsal showed two seats). Its picks are never shown, during the draft or after
it, unless the host adds `--broadcast-bot-picks`: then they follow the same rule and delay as an AI seat's (`ai-live` treats a bot
seat as an AI seat, never as a person). The top of `draft_server.py` says why that is safe to offer and why it is off by default.

```bash
python3 draft/test_broadcast.py                             # every reveal mode, compared after every pick of a whole draft
python3 draft/test_broadcast_bots.py                        # a --bot seat: four columns, its picks hidden unless asked for
python3 draft/test_broadcast.py --demo --reveal delayed     # a self-driving draft to look at (prints the link)
```
