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

## Scorecard: 2026-09-25, second pass (M4 Pro, Gemma 4 e2b, Whisper small+medium, Moira)

`table/tests/run_all.sh`, final run: **11 of 12 suites green**. The 12th failed on one scenario: a macOS `say` hung for 38 minutes, then was killed. `say` now has a timeout and a retry, and that scenario passes on rerun.

| suite | result |
|---|---|
| engine rules (`engine_rules.py`) | 16/16 (adds hexproof, shroud, indestructible) |
| misheard card names (`hearing_names.py`) | 36/49 recovered, **0 wrong** (includes a noisy-room case that used to read as Mana Vault) |
| speaker ID (`speaker_eval.py`, 6 voices × clean/room/noisy) | 238/240 right, **0 wrong**, 2 "not sure" |
| vision (`vision_eval.py`) | **0 wrong reads**; 28 % of the frame 7–8/8; two cards 4/4 |
| sensory regressions (`sense_run.py`, 126 scenarios) | 118/119 in the final sweep. The one crash was the hung `say`; that scenario passes on rerun |
| whole games (`game_sim.py`, 3 humans + AI, camera shows, speaker-ID life) | final run: 3 games, 243 lines, **0 problems**; plus the 2-hour soak below |
| brain API · brain page · Gemma turns · session · /table · router | 19/19 · 9/9 · 14/14 · 41/41 · 10/10 · 22/23 routed, 23/23 right speaker |
| offline | the table's processes: no non-loopback connection in any run. Ollama.app: see below |

**Of the 11 goals open after the first pass, 9 are met and promoted.** The other two are
resolution-bound and stay open. The 125 scenarios now include 5 open goals:

| goal | today | what would close it |
|---|---|---|
| `hear-far-field-4of5` | 2–4 of 5 at the far end of a big table (the regression bar is 2/5) | a mic nearer the players; the software is at Whisper's limit |
| `hear-far-and-noisy` | 2 of 5 far away in a loud room | the same |
| `see-tiny` | a card at 22 % of a 720p frame: 0–1 of 8 | a higher-resolution camera; readable from ~150 px of card width |
| `see-tilted-far` | a card at 28 % and tilted: 2 of 8 | the same, or deskewing before cropping |
| `turn-card-names-audible` | depends on the draw: Whisper hears Moira's "Aura Gnarlid" as "Orinulid" whatever the spelling or rate | a human listening test; Whisper is only a proxy for the table's ears |

## What the second pass built (each piece measured before it was trusted)

### Ears
- **Speaker ID** (`speakers.py`): ECAPA voice embeddings, offline.
  - Players enroll once by saying "This is Michael".
  - Results: 238/240 right, **0 wrong**, 2 "not sure" (`speaker_eval.py`), across 6 voices,
    clean, room and noisy audio.
  - Plain MFCC statistics were tried first: 72–94 % right, and wrong with confidence.
- **What speaker ID made possible:**
  - "I'm at 35" and "No blocks, I take four" go to the right player; two players' totals in one
    breath.
  - An unknown voice gets "Who's that?". The reply "Jess." enrolls that voice and applies the change.
  - The AI's own voice is enrolled at startup, so hearing it counts as an echo whatever the
    words. A player repeating the AI's words while it's still talking is still heard.
- **Misheard card names** (`match.recognise_spoken`). The name after "I cast" is matched by
  **sound** (a phonetic key plus word alignment) against all 35k cards, and weighed by
  **what people actually play** (EDHREC rank, from the offline Oracle).
  - It's accepted only when a player would be sure too; otherwise "didn't catch that".
  - Every word of the chosen card must match something heard. The average alone let "mana relts" become Mana Vault.
  - On every mishearing seen in this project's runs: **36/49 recovered, 0 wrong**
    (`hearing_names.py`, which fails on a single wrong card).
  - Gemma was tried as the picker and removed. It chose "Ballroom" for "I play Boris" (Forest).
- **A second Whisper pass.** When small hears "I cast …" but names no card, medium re-transcribes
  that clip. Across 60 degraded plays (`stt_bench.py`):

  | setup | recognized | p50 latency |
  |---|---|---|
  | small alone | 44 | 118 ms |
  | small + new recognizer | 50 | 120 ms |
  | small + medium second pass | **54** | 145 ms |

  Turbo was no better than medium, and denoising or loudness normalization didn't help.
- **Rules the code now owns:**
  - "Wait, Claude, hold on" (a `hush` event; the page stops speaking);
  - "…three goblins, that's nine" (counted damage);
  - "In response, I cast Counterspell" (its last spell is countered);
  - "No, I said Sol Talisman" (the misheard card is replaced);
  - "I cast X" with a real card is a play, whatever the router thought;
  - life confirmed out loud ("Jess, 36.") so a missed line gets noticed.
- **Whisper's spellings, normalized:** "Ad 31" → at 31, "SAMS" → Sam's, "Gaines" → gains,
  "R-Take" → I take, "Tax" → attacks, "Claudeville" → Claude, commas inside an attack.

### Eyes
- **A reader per card** (`vision.py`): each card is found as a rectangle, straightened, scaled up,
  and its title band read (`vision_eval.py`).
  - A card at 28 % of the frame: 7–8/8, up from 0/8.
  - Sideways or upside down: 8/8.
  - Two cards held up together: 4/4.
  - Wrong reads: **0**.
- **Titles only.** Keyword and rules lines never count as a name. "Flying" misread as "Fling" (a
  real card) produced 3 wrong reads before this.
- **Tokens** read as tokens ("Goblin token") from their type line. Before, a Goblin token read as
  the Unfinity card "_____ Goblin".
- **Other cases:**
  - double-faced cards read by either face;
  - on a name collision, the card people play wins ("Rampant, Growth" is a playtest card);
  - foils, proxies, a Japanese printing (read right or not at all), a phone screen (decided: it
    counts; the card is public either way), a hand sweeping across mid-read.
- **Overhead board** (`/api/board`):
  - every card with its tapped state, two copies counted as two;
  - what entered and what left, where leaving needs 2 missing frames in a row, so a hand passing
    over is not removal.
- **Robustness:** Apple Vision's Neural Engine path fails transiently under load, so it retries
  on the CPU.

### Voice
- Turn lines say "Claude, turn 4." because Moira's "Claude's turn" came out "Clouds turn".
- Every attack names its player, and every Aura names what it enchants (checked).
- When a card is shown, code says its name and the persona adds the opinion. For "that card",
  code states what it does first.
- The page never starts speaking while someone is talking, and "hold on" clears its queue
  (`brain_page_e2e.cjs`).

### Adversarial
- A leak found and closed: asked "is Swords to Plowshares in your hand?", Gemma said "Swords are
  certainly in my possession". Code now answers card-specific hand questions with "I don't talk
  about my hand". For persona replies, the leak guard also blocks distinctive *parts* of a hand
  card's name.
- "Claude casts Wrath of God", said by someone else, changes nothing ("That wasn't me.").
- Several turns of social engineering: nothing is named, nothing is confirmed.

### Whole games
`game_sim.py`: three humans plus the AI. Players enroll, report their own life with "I take N",
show cards to the camera about 30 % of the time, attack and cast removal, and repeat themselves
when the table doesn't confirm. `--minutes N` runs a soak that samples memory, sockets, Gemma
still loaded, and latency drift, game by game.

## Found by the harness, not fixed in code
- **The Ollama desktop app contacts `ollama.com`** (34.36.133.15:443) during long runs. None of the
  table's processes do. The egress watch now names the process. To keep the table fully offline,
  turn on Ollama's airplane-mode or auto-update setting, or run `ollama serve` directly. That's
  the machine owner's setting to change.

## Still to do
- **Real voices.** `say: {wav: path}` steps are ready for recordings of the four of us; synthetic
  voices are too clean to trust alone.
- **A real overhead camera**, validated against a real table. Everything so far is synthetic
  frames.

## Two-hour soak — 2026-09-25 (`game_sim.py --minutes 120`)

**24 games, 2,062 spoken lines, 121 minutes, no non-loopback connection.**

| health | result |
|---|---|
| server memory | 87–1,078 MB, cycling with model loads. No upward trend: 256 MB at the start, 874 MB at the end, 87 MB at minute 108 |
| open sockets | 2 throughout |
| Gemma pinned | loaded the whole time |
| latency p95 per game | 1.2–2.9 s, no trend (1.6 s at minute 117) |

It found 13 bookkeeping problems, now fixed. Each went into a regression check:

- **Whisper writes "Claude's" as "Claude,"** ("on Claude, Setessan Champion"), so removal missed
  its target, 5 times.
- **Whisper wrote "for twelve" as "VIII"**, and the roman-numeral rule added the same day turned it
  into 8 damage. A roman numeral is now treated as a mangled number: code falls back to the
  attacker's power, or asks.
- **Karen's "No blocks, I take 5" arrived as "Note Logs, R-Tank 5"** (also "R-Tag", "R-Tek"). The
  rule now: a "no blocks … N" line from a known voice, naming no other player, is that player's
  damage. From an unknown voice, it asks "Who's that?".

## Principles (carried over from building it)

- **Code owns the rules it knows, and the model owns judgment.** Whenever a test finds the model
  breaking a rule, move that rule into code; don't tune the prompt.
- **Judge what the table experiences, not what the API returned.** That is why the voice
  round-trip exists: the text was right every time, and the sound was not.
- **A goal passing once is a data point; three passes in a row is a regression test.**
