# divinci-table

An AI player at a real four-player Commander (Magic: The Gathering) table.

The model plays one typed decision at a time: code knows the rules and lists the legal moves; a
System One model (TypeSafe's `jev-latest`, a self-hosted DiffusionGemma-Jev "djev", or an open
model through open-alternative-jev) picks one and says how sure it is.

It is also a game table you can sit at: `table/` is a server where people (on a phone or laptop) and AI players
share one Commander or 40-card game. An AI seat is driven through an HTTP API by whatever you point at it: a
script, a model, an agent. The server keeps every hand private, checks every move against the rules it models,
and lets only the card's rules show a hand to the player it may be shown to.

## Layout

| path | what |
|---|---|
| `harness/` | simulated four-player games: rules engine, seat agents, the paired comparison, replay viewer. Start with `harness/README.md` — it has every result so far |
| `table/` | the real-table server: webcam scan pad for the AI's hidden hand + open-mic voice, fully offline (`table/README.md`) |
| `table/tests/run_core.py` | every test a plain clone can run (see "Run the tests" below) |
| `table/fetch_data.py` | one-time download of card names, rules text and a set's cards from Scryfall; after it the table is offline |
| `docs/security.md`, `SECURITY.md` | who may do what at a table, which test holds each rule, and how to report a problem |
| `scripts/fetch-games.sh` | pull a run's game logs from R2 and verify them |
| `docs/roadmap.md` | the real-table plan: phones, scan pad, Jetson, and Divinci Releases as player personalities |

## Run it

```bash
pip install -r table/requirements.txt                # Python 3.14 + numpy; the rest of that file is optional (scan pad, voice, cameras)
python3 table/fetch_data.py                          # once: card data from Scryfall (~40 MB); after it the table is offline
NO_GEMMA=1 ROUTER=code REPLIES=template python3 table/server.py --any-card --brain external \
  --ai "Claude|Ellivere of the Wild Court|" --ai-deck decks/ellivere.json --human "Michael|" --human "Sam|"
# then open http://localhost:8800/table
```

No model is needed for the table itself (`NO_GEMMA=1 ROUTER=code REPLIES=template` runs it on rules and templates).
The voice, scan pad and in-character replies use local models (Whisper, Gemma on Ollama) on a Mac; see `table/README.md`.
The card data stays on your machine and is never committed; card art is not in this repo and never should be.

## Run the tests

```bash
python3 table/tests/run_core.py            # every test a plain clone can run, three at a time
```

Python 3.14 and numpy are enough (`pip install -r table/requirements.txt`); the scan pad, voice and cameras are optional extras. A test that needs something your
clone doesn't have (the set's card data: `python3 table/fetch_data.py --set trk`; the Claude bot; an older frozen copy of the
table) is **skipped** with the command that fixes it, and the summary lists what was skipped. Tests that are red today are
named in `run_core.py` (`KNOWN_RED`) with what is known, so a new failure is the only thing that turns the run red.

## Where things stand (2026-11-11)

- **The table** plays whole games with people and AI players at once (Commander and 40-card sealed). It keeps hands
  private, checks every move an AI seat makes, and refuses another seat's actions without that seat's key. It is played on
  real game nights with friends and AI players, and the engine, its tests and `docs/security.md` are the record of what broke
  and was fixed.
- **Tests, measured 2026-11-11 with `table/tests/run_core.py`.** On the author's Mac: 79 pass, 4 skipped (they need the Claude bot or
  3D assets, which this repo doesn't ship), 4 known red, 0 failed. On a bare clone (a virtualenv with only numpy, an empty home
  folder, no network, the set's card data fetched): 76 pass, 7 skipped, 4 known red, 0 failed. The four known-red suites
  are older ones the engine outgrew; each is named in `run_core.py` with what is known.
- **Security, 2026-11-11.** An independent review attacked a throwaway server and a second one tried 69 one-line breakages of the
  engine to see whether the tests notice. Fixed from them, each with a test that fails on the old code: a seat's typed text
  or photo caption could change other seats' life totals, local trust depended on a proxy header, the opening-hand step let
  a player change their commander after seeing their hand, and a refused cast cost a player their response window. Still
  open and written down in `docs/security.md`: a held spell names the seats that may respond.
- **Not tested in a browser yet:** the private-reveal question, a concede control, and the "who is the table waiting on" banner.

## Harness results (2026-09-24)

- `jev-latest` plays at roughly heuristic strength: 6/32 wins, and outlasted the heuristic in 11 of
  32 paired seats (shorter in 8), 0 invalid answers, ~0.35 s per decision.
- `so1` + Qwen3.5-4B is not a table opponent: 2/32 wins, lasted longer than Jev in 3 of 32 pairs
  (Jev 26); it passes 66% of turns it could cast a spell.
- djev and Qwen3.6-27B are next — on a Colab A100 or the Jetson AGX Orin 64 GB.
- The table server runs **fully offline** on a MacBook: webcam scan pad + open mic, Whisper for
  hearing, Gemma 4 on Ollama for routing and in-character replies, macOS voices for speaking.
  ~0.9 s from speech to reply; 36/36 session and 11/11 real-browser checks, with a socket watch
  proving no connection leaves the machine.

## Data

Game logs are not in git (179 MB for one day of runs). Divinci keeps them in a private R2 bucket,
restored by `scripts/fetch-games.sh` (Divinci Cloudflare access required). Every result in
`harness/README.md` can be regenerated with `paired.py`: the rules engine and deck shuffles are deterministic per seed
(model answers can vary run to run). The logs are simulated games only, with no user data.

## Credentials

The only secret the harness uses is `TYPESAFE_API_KEY` (a [TypeSafe](https://typesafe.ai) key), and
only for `jev-latest` seats. Heuristic, random, `so1` and `djev` seats need no key.

```bash
cd harness
python3 paired.py --agent heuristic --seeds 2          # no key, no network: smoke test
TYPESAFE_API_KEY=… python3 paired.py --agent jev --seeds 8 --auto-land
```

The key is read once and removed from the environment; it reaches `curl` on stdin, never argv.

`JEV_MAX_CALLS` caps calls per run; TypeSafe keys have no spend limit of their own.

## License and Magic: The Gathering

Code: Apache-2.0 (see `LICENSE`).

divinci-table is unofficial Fan Content permitted under the Fan Content Policy. Not
approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the
Coast. ©Wizards of the Coast LLC. Card names are used to identify cards; the rules text in
`harness/cards.py` is our own simplified paraphrase of what the engine models, not the printed
Oracle text.
