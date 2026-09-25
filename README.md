# divinci-table

An AI player at a real four-player Commander (Magic: The Gathering) table.

The model plays one typed decision at a time: code knows the rules and lists the legal moves; a
System One model (TypeSafe's `jev-latest`, a self-hosted DiffusionGemma-Jev "djev", or an open
model through open-alternative-jev) picks one and says how sure it is.

## Layout

| path | what |
|---|---|
| `harness/` | simulated four-player games: rules engine, seat agents, the paired comparison, replay viewer. Start with `harness/README.md` — it has every result so far |
| `table/` | the real-table server: webcam scan pad for the AI's hidden hand + open-mic voice, fully offline (`table/README.md`) |
| `scripts/fetch-games.sh` | pull a run's game logs from R2 and verify them |
| `docs/roadmap.md` | the real-table plan: phones, scan pad, Jetson, and Divinci Releases as player personalities |

## Where things stand (2026-09-24)

- `jev-latest` plays at roughly heuristic strength: 6/32 wins, and outlasted the heuristic in 11 of
  32 paired seats (shorter in 8), 0 invalid answers, ~0.35 s per decision.
- `so1` + Qwen3.5-4B is not a table opponent: 2/32 wins, lasted longer than Jev in 3 of 32 pairs
  (Jev 26); it passes 66% of turns it could cast a spell.
- djev and Qwen3.6-27B are next — on a Colab A100 or the Jetson AGX Orin 64 GB.
- The table server runs **fully offline** on a MacBook: scan pad + open mic with open-alternative-jev as
  the local router. 33/33 session checks and 11/11 real-browser checks pass, with a socket watch
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
