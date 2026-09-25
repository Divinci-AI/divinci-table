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

## Scorecard — 2026-09-24, first runs (M4 Pro, Gemma 4 e2b, Whisper small, Moira voice)

**Regressions: 35/35.** No non-loopback connection. The whole suite takes about 2 minutes.

| area | passes today |
|---|---|
| hearing plays | plain, two cards at once, commander nicknames, Australian/Indian/South African voices, fast talker, 15 dB room noise, a quieter talker over the top, a mangled name ("Lanour Elves") |
| who it's for | chatter ignored; talking *about* the AI is not talking *to* it; "Sam, your turn" doesn't start its turn; a long story that names Sol Ring isn't a play |
| its turn | "your turn" / "you're up" / "go ahead" each play one legal turn; a question about its turn is answered, not played; three full voice-driven rounds with no rule slips |
| privacy | asked for its hand, asked to spell it, a spoken "developer override", a printed injection "card" held to the camera — nothing leaks in text or in sound |
| eyes | clean, sideways, upside down, tilted, arm's length, sleeve glare, thumb over the text, dim room, heavy JPEG, the same card twice. A blurred card is read correctly or not at all, never as a wrong card. A grocery list is not a card |

**Goals: open, in rough order of value at a real table**

| goal (scenario id) | what happens today |
|---|---|
| `life-hit-by-voice`, `life-human-reports` | life only changes by button or `tablectl`; "Ghalta hits Claude for twelve" does nothing |
| `removal-on-its-creature` | removal named on its creature is heard as a play, and its board doesn't change |
| `ask-deal` | a deal gets "We shall see what you bring" and no yes or no. Code decides ACCEPT/DECLINE, but the spoken line drops it |
| `ask-life-total`, `ask-hand-size`, `ask-own-board` | public information gets dodged: "my life total is whatever the Wild Court decides" |
| `ask-commander-rules`, `see-then-ask` | no rules knowledge in the persona reply; "that card" isn't linked to the card it just saw |
| `turn-announcement-brief` | a turn-5 announcement takes 24–29 s to say |
| `turn-card-names-audible`, `see-name-audible` | depends on the draw. Moira says "Clawd", "Sarah Angel", "Lana or Elvis" (Llanowar Elves), "or annul" (Aura Gnarlid) |
| `hear-loud-room` (5 dB), `hear-quiet-voice` | the play isn't heard at all |
| `see-very-far`, `see-two-cards` | a card at 28 % scale isn't read; with two cards held up only one is read |

## Problems the first runs found

1. **The table can't make out its own name and its cards.** "Claude" → "Clawd"; "Llanowar Elves"
   → "Lana or Elvis". Fix it on the speaking side: a pronunciation lexicon, or phonetic respelling
   for TTS only. Don't touch the text used for rules.
2. **Deals aren't answered.** Code decides the verdict, and the persona writes around it. Rule
   that code should own: the spoken reply to a deal must start with the verdict.
3. **Public information is treated as secret.** The persona is told to be cagey about the hand and
   applies that to everything. Code should answer life, hand size and board directly from state.
4. **Turn announcements grow long.** After turn 5 they run over 20 s. Say less: skip lands after the
   first mention, and group the Auras.

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
- every card in its deck, spoken in its voice and transcribed back: score how many names are
  audible, and track it as a number per voice (Moira vs Daniel vs Samantha);
- its announcements are at most N words, and hand every decision (target, mode, attack) to the
  table by name;
- it never talks over a human: speech must wait for a gap in the mic input.

**Whole games**
- a scripted 10-turn game for 4 players, entirely by voice and camera: its board, life and
  graveyard must match an independently tracked ground truth at the end;
- the same game with Claude as the outside brain (`--brain external`), with the runner pausing on
  `attention` events for the brain to act;
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
