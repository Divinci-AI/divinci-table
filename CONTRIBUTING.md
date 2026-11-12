# Contributing

Thanks for looking. This is a small project that grew out of game nights, so the bar is "does it hold up at a real table".

## Run the tests (about 12 minutes with the card data)

```bash
pip install -r table/requirements.txt     # once: Python 3.14 + numpy
python3 table/fetch_data.py --set trk     # once: card names, rules text and one set's cards from Scryfall (~40 MB), then fully offline
python3 table/tests/run_core.py           # everything a plain clone can run
python3 table/tests/run_core.py retro     # only tests whose file name contains "retro"
```

You need Python 3.14 and numpy (`pip install -r table/requirements.txt`); nothing else for the server and its tests. A test that needs something your clone
doesn't have (the set's card data, an older frozen copy of the table) says so and counts as **skipped**,
not failed; the summary lists exactly what was skipped and how to get it. `run_core.py` never touches ports
8800/8801/8820/8830/8883, so it is safe beside a live game. The browser suites (`*.cjs`, Playwright), the Gemma/Whisper
suites and the hardware tests need more setup and are not in `run_core.py`; `table/tests/run_all.sh` is the author's full run.

## Changing the engine

- Write the test first and **watch it fail on the old code**. A test that passes before the fix proves nothing. The
  security tests marked "mutation-checked" in `docs/security.md` were verified that way (break the guard, see red), and
  `remote_authority_test.py` fails on the commit before its fix; hold new ones to the same standard.
- A hidden-information rule (hands, private reveals, face-down cards) gets a test that reads every response a
  non-owner can reach: state, events, error bodies, logs.
- Don't change the engine mid-game on someone's table; apply between games.

## Never commit

- **Card art.** It is copyrighted. `table/assets/art/` is git-ignored; the table serves art from `TABLE_ART_DIR`.
- **Real game folders, seat keys, tokens, invite codes, or a game's research data** (private hands, full logs).
  `table/.cache/`, `live/`, `*.key`, `*.pkl` and friends are git-ignored; check `git status` before `git add`.
- Card rules text copied from the printed cards. The engine reads rules text from Scryfall's public data on your machine.

## Fan content

divinci-table is unofficial Fan Content permitted under the Fan Content Policy. Not approved or endorsed by Wizards of the
Coast. Card names are used to identify cards. Star Trek is a trademark of its owner; nothing here is affiliated with it.

By contributing you agree your contribution is licensed under the repository's Apache-2.0 license.
