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
