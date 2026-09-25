# Playing as the brain (Claude, or anyone with a terminal)

`table/play.sh "Michael|Ghalta, Primal Hunger" "Sam|Krenko, Mob Boss"` starts the table with the
virtual Ellivere player in **external-brain** mode. The server keeps the deck, the private hand,
the board, life totals and the conversation, and it checks the arithmetic. The brain decides
everything else and acts through `table/tablectl.py`. Every action is announced at the table in
Moira's voice.

## Before the first game: say your names

Each player says one sentence, for example "Hi everyone, this is Michael, I'm playing Ghalta
tonight." The table replies "Hi, Michael." and from then on knows that voice. Speaker ID uses ECAPA
voice embeddings and runs offline; `tests/speaker_eval.py` measured 0 wrong answers in 240 lines.
Knowing who is speaking makes these work:

- "I'm at 35" and "No blocks, I take four" change the right player's life;
- "I'm at 31 and Sam's at 12" changes both, in one breath;
- the AI's own voice coming back through the laptop mic is recognised and ignored.

A voice the table doesn't know that says "I'm at 20" gets the reply "Who's that?". Answering with
your name ("Jess.") enrolls your voice and applies the change.

## The loop

```bash
table/tablectl.py watch          # one line per table event — run it under a live monitor
table/tablectl.py state          # private: hand with rules text, board with #ids, real mana
```

`watch` prints `⚑ FOR YOU` when the table needs the brain:

| attention | what to do |
|---|---|
| `turn` | `begin` → `land X` → `cast …` → `attack "A=Sam" …` → `end` |
| `question` / `deal` | `say "…"` (keep it short; `say` refuses to name a card still in hand) |
| `attacked` → *X for N* | `block REF --amount N --attacker X`, or `block --amount N` to take it |
| `removal` → *spell (effect) on target* | `exile REF` / `destroy REF` / `bounce REF`; for effect `counter`, the target is the spell Claude just cast |
| `hold` | someone said "wait, hold on": stop, and act only when asked again |

**What code handles without asking the brain.** Each item came out of a failing test:

- public questions: life, hand size, library, graveyard, board, open mana, "what does X do?";
- life said out loud, confirmed briefly back ("Jess, 36."), so a missed line gets noticed;
- someone else claiming Claude cast something ("Claude casts Wrath"): "That wasn't me.";
- a question about one specific card in the hand ("is Swords in your hand?"): "I don't talk about my hand.";
- "No, I said Sol Talisman.": replaces the misheard card;
- "Wait, Claude, hold on.": the page stops speaking.

If the brain hasn't acted about 3 s after a `⚑`, the table hears a filler ("One moment."). The page
never starts speaking while someone is talking, and it clears everything queued on "hold on".

## What the engine doesn't know (the brain covers it)

- **Cost reductions:** pass `--discount N` (e.g. Jukai Naturalist).
- **"Choose a color":** `cast "Utopia Sprawl" --on "#2" --color W`. The default is the colour
  its mana lacks.
- **Static effects beyond Auras, Roles and anthems:** say them, e.g. `say "With X, Spinner is 9/10."`
- **Opponents' rules slips** (summoning sickness, wrong mana): call them out; the humans decide.
- **Other players' boards:**
  - With a handheld camera, the board is only what was *heard* or *shown*: `state` lists announced
    cards.
  - With an overhead camera posting to `/api/board`, the table sees every card, which ones are
    tapped, and what enters and leaves. A card counts as having left only after it's been missing
    from two frames in a row, so a hand passing over it doesn't remove it.

Mana Auras on lands, anthems and "+1/+1 for each enchantment" ARE modelled (`tests/engine_rules.py`).

## Tested

`table/tests/run_all.sh` runs everything:

- engine rules;
- misheard-name recovery (fails on a single wrong card);
- speaker ID;
- vision;
- 120+ sensory scenarios (voice and camera in, the AI's own voice heard back);
- whole simulated games with three players who enroll, talk, show cards to the camera and report
  life;
- the browser page and the router.

The brain-mode path was rehearsed end to end on 2026-09-25: Claude played as the brain for five
rounds (`tests/game_sim.py --brain external`).
