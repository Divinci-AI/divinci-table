# Playing as the brain (Claude, or anyone with a terminal)

`table/play.sh "Michael|Ghalta, Primal Hunger" "Sam|Krenko, Mob Boss"` starts the table with the
virtual Ellivere player in **external-brain** mode: the server keeps the deck, the private hand, the
board, life and the conversation, and checks the arithmetic; the brain decides everything else and
acts through `table/tablectl.py`. Every action is announced at the table in Moira's voice.

## The loop

```bash
table/tablectl.py watch          # one line per table event — run it under a live monitor
table/tablectl.py state          # private: hand with rules text, board with #ids, real mana
```

`watch` prints `⚑ FOR YOU` when the table needs the brain:

| attention | what to do |
|---|---|
| `turn` | `begin` → `land X` → `cast …` → `attack "A=Sam" …` → `end` |
| `question` / `deal` | `say "…"` (keep it short; never name a card in hand — `say` refuses) |
| `attacked` → *X for N* | `block REF --amount N --attacker X`, or `block --amount N` to take it |
| `removal` → *spell (effect) on target* | `exile REF` / `destroy REF` / `bounce REF` |

Code answers public questions (life, hand size, library, graveyard, board, mana, what a card does)
and life said out loud ("Sam's at 22", "Claude, you take seven") **without** asking the brain. If
the brain hasn't acted ~3 s after a `⚑`, the table hears a filler ("One moment.").

## What the engine doesn't know (the brain covers it)

- **Cost reductions** — pass `--discount N` (e.g. Jukai Naturalist).
- **"Choose a color"** — `cast "Utopia Sprawl" --on "#2" --color W` (default: the colour its mana lacks).
- **Static effects beyond Auras/Roles/anthems** — say them: `say "With X, Spinner is 9/10."`
- **Opponents' rules slips** — call them out (summoning sickness, wrong mana); the humans decide.
- **Other players' boards** are only what was *heard*: `state` lists announced cards; dead
  attackers the AI killed while blocking are removed, others are not.

Mana Auras on lands (Fertile Ground, Utopia Sprawl, Wild Growth, Overgrowth) and "creatures you
control get +N/+N" anthems ARE modelled (`tests/engine_rules.py`).

## Tested

`table/tests/run_all.sh` — engine rules, 90+ sensory scenarios (voice/camera in, the AI's own voice
heard back), whole simulated games by voice, the browser page, the router. The brain-mode path was
rehearsed end to end on 2026-09-25: five rounds with two simulated players, Claude as the brain,
zero bookkeeping mismatches (`tests/game_sim.py --brain external`).
