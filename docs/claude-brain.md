# Playing as the brain (Claude, or anyone with a terminal)

`table/play.sh "Michael|Ghalta, Primal Hunger" "Sam|Krenko, Mob Boss"` starts the table with the
virtual Ellivere player in **external-brain** mode. The server keeps the deck, the private hand,
the board, life totals and the conversation, and it checks the arithmetic. The brain decides
everything else and acts through `table/tablectl.py`. Every action is announced at the table in
Moira's voice.

**No local model at all:** `NO_GEMMA=1 table/play.sh "Michael|" "Sam|"`. Plain code decides who a
line is for (a name opening or closing it, "your turn", "truce", "I cast"), and everything said to
the AI goes to the brain. Nothing loads into Ollama. Played a whole game this way on 2026-09-30 and
won it. It only starts with `--brain external`, because without a brain nothing would decide.

**Someone joins late:** the page's **+ Add player** button (or `POST /api/players {"name","commander"}`)
adds them at 40 life; they enroll their voice with "This is Jess." like everyone else.

**A board everyone can read:** `~/.venvs/table/bin/python table/boardview.py` serves
http://localhost:8803: each player's battlefield, command zone, graveyard and live life, with every
card's Oracle text from the offline card file. The brain keeps it current by editing
`table/.cache/boardview/board.json` (public information only, never a hand).

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
| `turn` | `begin` → `land X` → `cast …` → `attack "A=Sam" …` → `damage "A=Sam"` → `end` |
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

## What the engine resolves on its own

The Ellivere deck's own triggers (all hand-resolved in the live game of 2026-09-30, now in
`tests/engine_rules.py` sections 7–13):

- **casting:** Enchantress's Presence and Kor Spiritdancer draw;
- **an enchantment or Aura entering**, Roles included (a Role is an Aura):
  - Tanglespan Lookout, Eidolon of Blossoms and Setessan Champion draw (Champion also gets a counter);
  - Archon makes a flying Pegasus and Ajani's Chosen makes a Cat;
  - Siona makes a Soldier when an Aura attaches to its own creature;
- **cost reductions:** Jukai Naturalist, Starfield Mystic, Danitha and Transcendent Envoy;
- **scaling power:** Ancestral Mask, Kor Spiritdancer, Mantle, Sage's Reverie, Aura Gnarlid, Eidolon
  of Countless Battles, Ethereal Armor. Opponents' enchantments count from what's been announced;
- **mana:** Sanctum Weaver (X of one colour), Sol Ring (2), Careful Cultivation's `{T}: Add {G}{G}`;
- **attacking:** Bear Umbra untaps lands, Giant Inheritance's Monster Role (never replacing a
  Virtuous Role), Ox Drover;
- **`damage "Ellivere=Michael" "#14=Sam:3"`** after blocks, for attackers that hit a player
  (the amount defaults to power):
  - the opponent's life and lifelink (Archon gives Pegasi lifelink);
  - Ellivere's draw for each enchanted creature;
  - Pollenbright Wings' Saprolings and Snake Umbra's draw.
  The table hears the new life totals, so players don't need to say them again;
- **upkeep:** Verdant Embrace makes its Saproling on the AI's own upkeep.

Cards drawn by triggers show up under `🔒 private` as `drew`. Anything the engine can't resolve
alone shows up there as `todo`: Sun Titan, Songbirds' Blessing, Siona's look at seven, Gylwain's Role
choice (`tablectl role REF Monster`), Verdant Embrace on each opponent's upkeep, and Ajani's Chosen
moving the Aura. Do those by hand.

## What the engine doesn't know (the brain covers it)

- **Other cost reductions:** pass `--discount N`; the four above are already applied.
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

## The camera gantry (Creality + Canon)

`table/gantry.py` drives a Creality printer as a motion stage for the overhead Canon. It never
heats or extrudes: every G-code line passes one gate that allows only moves, homing and status, and
moves are clamped to the machine's travel (from `MACHINE_TYPE`, or `table/.cache/gantry.json`).

```bash
~/.venvs/table/bin/python table/gantry.py ports                 # printer serial port + Canon
~/.venvs/table/bin/python table/gantry.py info                  # firmware, machine, travel, position
~/.venvs/table/bin/python table/gantry.py home                  # X and Y only
~/.venvs/table/bin/python table/gantry.py scan --cols 3 --rows 2 --post
```

`home` leaves Z alone because homing Z drives the head down, and a camera hanging off it can hit
the table. `scan` shoots a serpentine grid, stitches it with OpenCV, and `--post` sends the
stitched frame to `/api/board`. `tests/gantry_test.py` checks the gate and limits against a fake
Marlin board.
