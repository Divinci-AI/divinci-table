# Running the tests: the interpreter, the data, the environment

Added 2026-11-14 (retro T1 to T4). The short version is in `CONTRIBUTING.md`; this is what the suite needs and why, so that a result
on one machine can be reproduced on another.

```bash
python3 -m venv <a folder of yours> && <that folder>/bin/pip install -r table/requirements.txt chess   # once
python3 table/fetch_data.py --set trk                      # once, with network access: card data (~40 MB), then offline for good
<that folder>/bin/python table/tests/run_core.py           # the whole portable suite, three test files at a time
<that folder>/bin/python table/tests/run_core.py retro     # only files whose name contains "retro"
<that folder>/bin/python table/tests/run_core.py --shard 2/4   # the second quarter (every 4th file by name): a full run in pieces
```

## The interpreter

`run_core.py` starts every test with the interpreter it was started with (`sys.executable`), and so do the tests for the tables and bots
they start. **Run it with the virtualenv you installed `table/requirements.txt` into.** A bare `python3` can be a different interpreter
with different packages, also when it reports the same version: the Python version is not what differs, the installed packages are.
The first line of every run says which interpreter is in use, and one more line for each thing it lacks:

```
interpreter: …/bin/python (Python 3.14.6); every test is run with it; it has what the suite uses (numpy, chess, Vision)
   MISSING chess: cloud/test/roomhost_test.py will SKIP (one of its rooms is a chess room). Fix: …/bin/python -m pip install chess
```

| Package | Who needs it | Without it |
|---|---|---|
| Python 3.14 | everything (older versions are untested) | |
| `numpy` | the table itself (`table/voice.py` imports it, audio or not) | the table does not start: every test that starts one **fails** |
| `chess` | `table/chess_server.py`; in the suite only `cloud/test/roomhost_test.py` (one of its rooms is a chess room) | that test **skips** and says so |
| `pyobjc-framework-Vision`, `pyobjc-framework-Cocoa` (macOS) | the scan pad. Today's table starts without them. `restore_old_snapshot_test.py` starts a FROZEN older copy of the table that imported them at start-up | that test **skips** and names the package |

Nothing else in `table/requirements.txt` is used by `run_core.py`: the camera, microphone, Gemma/Whisper and hardware tests are listed
at the end of every run as "not run here", each with its reason.

`restore_old_snapshot_test.py` was the test that "failed under the system python". The cause, exactly: the frozen pre-patch table it
starts runs `from ocr_mac import read_lines`, which runs `import Vision`; under an interpreter without pyobjc-framework-Vision that
table died at start-up and the test reported `Connection refused` a minute later. It now sees the table exit, reads the missing module's
name from its log, and skips with it. It also needs the frozen copy itself (`OLD_TABLE=<dir>`), and skips without one.

## The card data, and why a test never downloads it

The table needs a card-name list to start and reads every card's rules text from `table/.cache/oracle.json`. Both come from one download,
`python3 table/fetch_data.py` (about 40 MB from Scryfall; `--set trk` adds the set most tests build their decks from).

`run_core.py` sets **`TABLE_OFFLINE=1`** for every test. Under it:

* `table/server.py` with no card data exits 77 with a line starting `SKIP:` instead of fetching the name list;
* `table/oracle.py` raises `oracle.Unavailable` instead of fetching the bulk file (it still rebuilds `oracle.json` from an
  `oracle-cards.raw` that is already on disk: that needs no network);
* `table/fetch_data.py`, the download command itself, refuses (unset `TABLE_OFFLINE` to download).

So on a checkout with no `table/.cache` the run says so at its top, the tests that need the data **skip** (exit 77, with the command
that gets it), the rest run, and nothing goes online. Before 2026-11-14 the first test run of a fresh checkout quietly downloaded the
40 MB. `table/tests/offline_test.py` proves the refusal without touching the network. A test that starts a table or reads rules text
calls `cards_path.need_card_data()` at its top.

You can set `TABLE_OFFLINE=1` for a real table too, as a guarantee that it never goes online.

## Environment

What `run_core.py` sets for each test (you do not set these): `TABLE_OFFLINE=1`, `NO_GEMMA=1`, `HF_HUB_OFFLINE=1`, `ROUTER=code`,
`REPLIES=template`, `TABLE_NO_VOICE=1`, `PYTHONDONTWRITEBYTECODE=1`, and a scratch `TABLE_RESEARCH_DIR` and `TABLE_SAVES_DIR` per test
file (removed afterwards). It removes `TYPESAFE_API_KEY`. To run one file by hand, set the same:

```bash
TABLE_OFFLINE=1 NO_GEMMA=1 HF_HUB_OFFLINE=1 ROUTER=code REPLIES=template TABLE_NO_VOICE=1 PYTHONDONTWRITEBYTECODE=1 \
  <that folder>/bin/python table/tests/phase_test.py
```

What you may set:

| Variable | For |
|---|---|
| `TABLE_CARDS_JSON=<file>` | the set's cards for the table's tests (a Scryfall-shaped list), instead of `table/.cache/cards-trk.json` |
| `DRAFT_CARDS_JSON=<file>` | the same for the `draft/` tests (else `cards.json` beside `draft/`, else `table/.cache/cards-trk.json`) |
| `TABLE_TEST_DECKS=<dir>` | a folder of test decks, instead of decks built from the card data |
| `OLD_TABLE=<dir>` | the frozen older table for `restore_old_snapshot_test.py` |
| `BOT_DIR=<dir>` | where `claude_autopilot.py` is, for the tests that run the bot (default: `bots/`) |
| `RETRO_SEED`, `RETRO_ONLY` | `retro_game4_test.py`: the pinned shuffle, and which sections to run |

With `python3 table/fetch_data.py --set trk` done, none of them is needed.

## Ports

Every test's scratch server listens on a port the operating system hands out (`table/tests/freeport.py`: `free_port()`, and
`free_block(n)` for a tool that counts up from a base port), on 127.0.0.1 only, and never on 8800, 8801, 8820, 8830 or 8883, so the
suite is safe beside a live game and two runs on one machine do not disturb each other. Until 2026-11-14 a dozen files had a port number
written into them (six shared 8831), and two runs at once failed each other's tests.

## Reading the result

`✅` passed, `⏭️` skipped (exit 77, with the reason), `🟡` known red (listed in `run_core.py` with what is known: `combat_server_test.py`),
`❌` failed, `⏱️` timed out. The run's exit code is 0 unless something failed or timed out. A full run was about 12 minutes at `-j 3` when
last measured (2026-11-11); `retro_game4_test.py` alone takes about five. With `--shard I/N` the summary says which part ran; N runs (1/N to N/N) cover every file once.

## Writing a test

* Ask for a port (`import freeport; PORT = freeport.free_port()`), never write one down.
* Start the table with its own `--token-file`, `TABLE_RESEARCH_DIR` and `TABLE_SAVES_DIR` (a scratch folder).
* If it starts a table or reads rules text: `import cards_path; cards_path.need_card_data()` first. If it needs anything else a
  clone may not have, `cards_path.skip("needs …: <the one command that gets it>")`. Skipped is not failed; a download is never the fix.
* Wait for a thing to be true with a deadline, not for a number of seconds (`cloud/test/roomhost_test.py` slept 7.5 s for a save that
  takes longer on a busy machine).
* Stop only the processes you started, by pid.
