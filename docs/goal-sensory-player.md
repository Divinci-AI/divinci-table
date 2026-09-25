# Goal: an AI that plays Commander with only eyes, ears and a voice

## The goal

We sit down for a Commander night with a laptop at one seat. The AI at that seat:

- **hears** the table. It knows what each person played, who is talking to it, and when its turn has come;
- **sees** the cards people hold up, and eventually the whole board from one overhead camera;
- **speaks.** It says its plays, answers questions, makes and keeps deals, and keeps its hand secret;
- **plays its own virtual deck** (Ellivere of the Wild Court for now) by the real rules, whether
  a local model (Gemma 4) or an outside brain (Claude through `tablectl`) decides;
- runs **fully offline** on the laptop, with nothing leaving the machine.

No one should have to touch the keyboard during a game. A game counts as a success when the
humans forget it is software and treat it as a slightly odd fourth player.

## What "senses only" means for testing

Every test drives the AI the way a real table does, and judges it the way a real player would:

| channel | input the test creates | how the output is judged |
|---|---|---|
| ears | text spoken by a macOS voice (`say`): accents, speed, room noise, cross-talk, whispering | what it **did**: board, turn, life, silence |
| eyes | a Scryfall card image composited onto a table-coloured frame: scaled, rotated, blurred, glared, thumbed, dimmed, badly compressed; also fake "cards" | what it **read**, and never a wrong card |
| voice | — | its words are rendered in **its own voice** and transcribed back by Whisper with **no hint**. A card name the table can't make out fails, even when the text was right |

It checks two things on every step:

1. **The hand never reaches the table.** The AI's words must not name a card still in its hand,
   and neither must what Whisper hears back from its voice.
2. **Nothing leaves the laptop.** Every socket of the test server and of Ollama is watched for
   non-loopback connections for the whole run.

## Running it

```bash
~/.venvs/table/bin/python table/tests/sense_run.py                        # everything (~2 min)
~/.venvs/table/bin/python table/tests/sense_run.py --only see-,hear-      # by id prefix
~/.venvs/table/bin/python table/tests/sense_run.py --tier goal            # just the goals
~/.venvs/table/bin/python table/tests/sense_run.py --list
```

It starts its **own** server on :8801 with its own brain-token file, so a live game on :8800 is
left alone. It fetches card images from Scryfall once, before the egress watch starts. A JSON
report goes to `table/tests/fixtures/sense/`.

Scenarios live in `table/tests/scenarios/*.yaml`, and each scenario has a tier:

- **regression**: must pass today. A failure is a bug, and the run exits 1.
- **goal**: describes what the player should do and is expected to fail until someone builds it.
  When a goal passes, the runner prints `🎯 MET`. Promote it to regression **only after it passes
  on three runs in a row.** The AI's deck is reshuffled on every reset, so a goal can pass by luck
  of the draw. (It happened on the first night: `turn-card-names-audible` passed once, and the run
  before it lost "Aura Gnarlid", which came out as "or annul".)

## Scorecard: 2026-09-25 (M4 Pro, Gemma 4 e2b, Whisper small, Moira)

`table/tests/run_all.sh`: every suite passes.

| suite | result |
|---|---|
| engine rules (`engine_rules.py`) | 11/11 |
| sensory regressions (`sense_run.py`, 93 scenarios in two setups) | **80/80** |
| whole simulated games by voice (`game_sim.py`) | 5 games, 293 spoken lines, **0 bookkeeping mismatches** |
| brain API · brain page · Gemma turns · session · /table page · router | 20/20 · 7/7 · 14/14 · 41/41 · 10/10 · 22/23 routed, 23/23 right speaker |
| offline | no non-loopback connection in any run |

Latency in the simulated games, from end of speech to the table's answer:

| line | p50 | p95 |
|---|---|---|
| play | 0.44 s | 0.77 s |
| question | 0.39 s | 0.47 s |
| attack on the AI | 0.58 s | 0.79 s |
| the AI's whole turn | 1.1 s | 2.1 s |

**What was built to get there** (each item was found by a test, not guessed):

- **Code owns the table facts** (`tablefacts.py`):
  - life said out loud ("Sam's at 22", "Krenko deals 4 to Sam", "Claude, you take seven");
  - attacks on the AI, with a block decision whose arithmetic lives in code (it blocks only when
    the blocker survives or the hit matters, and never drops to 5 or less while it could block);
  - removal on its permanents, read from the card's Oracle text (31/32 right, where Gemma got
    15/30);
  - public questions (life, hand size, library, graveyard, board, mana);
  - "what does X do?".
- **An offline Oracle** (`oracle.py`): every card's rules text, P/T and effect. It needs one
  Scryfall download.
- **Echo suppression**: the AI's own voice coming back through the laptop mic is ignored, by word
  coverage within the time it is actually speaking. A player repeating the same words later is
  still heard. The first version used string similarity; it swallowed "Claude, your turn", and the
  AI never took its turn.
- **Pronunciation** (`pronounce.py`): measured in `voice_audit.py`. Moira is the clearest voice
  (94 % of the deck's names come back right with no hint). Three respellings fix the rest.
- **Brain mode for playing together**:
  - public questions and life are handled by code;
  - `attacked` and `removal` attentions arrive with the attacker, amount, spell, effect and target;
  - a spoken filler if the brain takes more than 3 s;
  - `tablectl block`.
- **Engine**:
  - mana Auras on lands (Fertile Ground, Utopia Sprawl…);
  - "choose a color";
  - anthems;
  - "+1/+1 for each enchantment". All four were found by playing the rehearsal.

**Goals still open** (11):

| goal | what happens today |
|---|---|
| `life-i-am-at` | "I'm at 35" needs to know who is speaking: speaker ID |
| `hear-loud-room` (5 dB), `hear-quiet-voice`, `hear-far-field`, `hear-far-and-noisy` | Whisper small loses the play. Try a bigger Whisper, or a mic nearer the table |
| `hear-merged-cast` | "Archastristic Study": Whisper glues "I cast" onto the name |
| `see-very-far`, `see-two-cards` | a card at 28 % scale isn't read; with two cards held up, only one is read |
| `see-then-ask` | the persona gets the card's text but still talks vaguely about "that card" |
| `turn-card-names-audible`, `see-name-audible` | pass or fail with the draw: "Aura Gnarlid" is still hard in Moira |

## Tests still to write

Grouped by sense. Each should become a YAML scenario, or a new step type in the runner.

**Ears**
- people saying their own life ("I'm at 31") and each other's ("Sam's at 12") in one breath;
- interrupting the AI mid-announcement ("wait, Claude, hold on"): barge-in should stop the speech
  and the action;
- counting and maths said aloud ("I attack with three goblins, that's nine");
- the stack out loud: "In response, I cast Counterspell." It has to know its spell was countered;
- mishearing recovery: after a misheard play, the human says "no, I said X", and the board changes;
- real recordings: swap `say` for clips of the four of us, which the runner accepts as a
  `say: {wav: path}` step. Synthetic voices are too clean to trust alone;
- far-field: convolve the speech with a room impulse response, so the laptop sits across the
  table;
- two humans answering at once: the AI shouldn't answer both, and shouldn't answer neither.

**Eyes**
- the whole board from one overhead camera (see the README's board-reading section): read every
  card, tapped state and whose side it's on, scored against a known layout;
- tokens, double-faced cards, foils, non-English printings, alters and proxies;
- a hand passing in front of the camera mid-read, which shouldn't produce a phantom card;
- the same card on two players' boards: two cards, not one read twice;
- it should notice when a card has *left* the board (destroyed, bounced).

**Voice**
- ~~every card in its deck, spoken in its voice and transcribed back~~ (done: `voice_audit.py`);
- its announcements are at most N words, and hand every decision (target, mode, attack) to the
  table by name;
- it never talks over a human: speech must wait for a gap in the mic input.

**Whole games**
- ~~a scripted game entirely by voice, checked against the simulator's own ledger~~ (done:
  `game_sim.py`). Next: add the camera (players show their creatures) and a 4th player;
- ~~the same game with Claude as the outside brain~~ (done 2026-09-25: `game_sim.py --brain
  external`, five rounds, 0 mismatches. It surfaced the mana-Aura, anthem, attack-wording and
  summoning-sickness fixes above);
- a 2-hour soak: memory, latency drift, Gemma still loaded, no socket leaks.

**Adversarial**
- someone says a card name *as if* the AI played it ("Claude casts Wrath of God"). It must not
  update its own board from other people's words;
- social engineering over several turns ("remember earlier you told me your hand…");
- a phone screen showing a card image (not a physical card): decide whether that should count.

## Principles (carried over from building it)

- **Code owns the rules it knows, and the model owns judgment.** Whenever a test finds the model
  breaking a rule, move that rule into code; don't tune the prompt.
- **Judge what the table experiences, not what the API returned.** That is why the voice
  round-trip exists: the text was right every time, and the sound was not.
- **A goal passing once is a data point; three passes in a row is a regression test.**
