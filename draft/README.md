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
Defaults point at `~/.divinci/star-trek-mtg/live`; the folder layout is the author's, and the scripts are the record of how a night was run.

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

## The broadcast: the draft as a stream's scene

`/broadcast?k=<broadcast key>` (JSON at `/api/broadcast`) is the page to capture: the named seats, how many cards each has taken, and
the picks the reveal rule allows (`--broadcast-reveal pack-end | delayed | ai-live | draft-end`, `--broadcast-delay N|end`; the top of
`draft_server.py` says what each still leaks to a drafter who is watching). The key is its own secret, the line `Broadcast: ...` of the
0600 links file: it opens no seat route, and no seat key opens it. It never carries a pack's contents, what was passed, a key, a card
id or art, or anything about a pick that is not revealed yet except that it exists. Table talk is repeated only when the line names no card.

```bash
python3 draft/test_broadcast.py                             # every reveal mode, compared after every pick of a whole draft
python3 draft/test_broadcast.py --demo --reveal delayed     # a self-driving draft to look at (prints the link)
```
