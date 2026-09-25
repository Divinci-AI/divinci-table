# The table server: scan pad + open mic, fully offline

The first real-table piece. One Python server on the laptop (the Jetson later). **Start at
`/table`**: camera and mic on one page — hold cards up, talk to the AI, and it answers and reacts
out loud, with the public board and the conversation side by side. The pages below are the same
pieces on their own:

- **Scan pad** (`/`) — the AI's hidden hand. A drawn card is held face toward the laptop camera
  (the person holding it sees only the back); the server reads the name line with Apple's Vision
  OCR, picks the card among those **still in the AI's library** (a choice, not open-ended OCR), and
  puts it in the next numbered slot — the sticky notes on the table. Pages and terminal show slot
  numbers only; a name is revealed when the slot is played.
- **Show a card** (`/show`) — public cards held up for the AI to see: it recognises any card (all
  ~35k names), reacts out loud in character, and the card goes on the public board. The preview is
  visible here, so never use this page for the AI's own draws.
- **Open mic** (`/voice`) — in-browser voice detection → local Whisper (with the game's player and
  card names as a hint) → a System One router decides what was said and to whom → the addressed AI
  answers by voice; play announcements update the board; chatter is ignored.

## An AI opponent with its own virtual deck

The AI can play **its own virtual deck** instead of physical cards: it shuffles, draws, keeps its
hand private, and announces every play out loud. Currently: **Ellivere of the Wild Court**
(*Virtue and Valor*, Wilds of Eldraine Commander), from MTGJSON's deck data in `decks/ellivere.json`.

```bash
HF_HUB_OFFLINE=1 ~/.venvs/table/bin/python table/server.py --any-card \
  --ai "Ellivere|Ellivere of the Wild Court|Moira" --ai-deck decks/ellivere.json \
  --human "Michael|Ghalta, Primal Hunger"
open http://localhost:8800/table        # "▶ Ellivere's turn", or just say "Ellivere, your turn."
```

- **Code owns the arithmetic** (`player.py`): library, private hand, one land a turn (code picks the
  land), mana and colours from its lands and mana creatures, which spells it can afford, Auras only on
  what their "Enchant …" line allows, Role tokens (Virtuous = +1/+1 per enchantment you control),
  simple creature tokens, commander tax, and never casting a board wipe onto its own creatures.
- **Gemma decides** (`ai_turn.py`): which affordable spell next, what an Aura/Role goes on, the modes of
  a modal spell, whom each creature attacks, and which of YOUR announced cards a hostile Aura
  (Kenrith's Transformation) goes on. There is no "stop casting" option on its own turn — the harness
  showed small models pass far too often.
- **The table resolves the rest.** Effects on your cards (destroy, exile, draw) are read out with the
  card's text; you apply them. Its board may drift from what those effects would do — correct it by
  voice later (not built yet).
- **"Your turn" is a code rule**: a turn is taken only when the words hand it over ("your turn",
  "you're up", "go ahead") and never for a question — "Ellivere, what cards are in your hand?" once
  made her play a whole extra turn.
- **Misheard card names** ("Lanour Elves"): when a play names no recognisable card, the closest
  catalog names are offered to Gemma with a "none of these" option; a confident pick goes on the board.

Simulated: 3 shuffles × 10 turns with zero rule violations (lands, mana, cards accounted for, Aura
targets, no self-wipe); `tests/ai_turn_e2e.py` 14/14 against the live server. Turn ≈ 0.5–1.8 s.

## Run it offline

Everything runs on the laptop: Whisper (mlx-whisper) hears, **Gemma 4 on Ollama** routes and
writes each AI player's replies, and the Mac's built-in voices speak them.

```bash
ollama pull gemma4:e2b          # once; ~2 GB loaded
HF_HUB_OFFLINE=1 ~/.venvs/table/bin/python table/server.py \
  --deck decks/example.txt \
  --ai "Talrand|Talrand, Sky Summoner|Daniel" \
  --human "Michael|Ghalta, Primal Hunger"          # one AI opponent; repeat --ai / --human for more

open http://localhost:8800/table        # camera + mic together
open http://localhost:8800              # the AI's secret draws (scan pad) — never on /table
```

Gemma is loaded at startup and **pinned while the server runs** (Ollama otherwise unloads it after 5
idle minutes, and the reload makes the next line take ~3 s); stopping the server (Ctrl-C / SIGTERM)
unloads it so the memory comes back. `OLLAMA_KEEP_ALIVE` overrides (e.g. `30m`).

`--ai "Name|Commander|Voice"` — the voice is any installed macOS voice (`say -v '?'`): Daniel,
Karen, Moira, Fred, Jester, Grandpa…. `localhost` is a secure context, so camera and mic work
without HTTPS. The OCR needs macOS (`ocr_mac.py`); the Jetson needs a Linux OCR backend behind the
same `read_lines()`.

Options: `ROUTER=ollama` (default) · `so1` (open-alternative-jev, `harness/run-so1-guarded.sh`;
slower on Apple GPUs) · `typesafe` (hosted jev-latest — billed, **sends transcripts off the
machine**). `REPLIES=ollama` (default, in character) · `template`. `ROUTER_OLLAMA_MODEL` /
`REPLY_OLLAMA_MODEL` pick other Ollama models.

⚠️ Ollama's desktop app may listen on all interfaces (`*:11434`), which exposes your models to the
local network. This server only talks to `127.0.0.1`; set `OLLAMA_HOST=127.0.0.1` for Ollama itself
on shared Wi-Fi.

Setup: `python3 -m venv ~/.venvs/table && ~/.venvs/table/bin/pip install pyobjc-framework-Vision
pyobjc-framework-Quartz mlx-whisper`; first Whisper run downloads the small model (then run with
`HF_HUB_OFFLINE=1`).

## How it decides

The router asks Gemma **one** question whose options combine what was said and to whom ("question
for Talrand", "deal for Krenko", "a play", "chatter") and reads the probability of each option
letter — one call (~0.2–0.4 s) instead of four. Known rules stay in code:

- **Direct address is a rule.** "Krenko, truce?" is addressed to Krenko, so only Krenko's options are
  offered. Without this, the model sent that line to Talrand.
- **Naming an AI and asking something** ("Talrand, who are you attacking?") is a question or offer,
  never an announcement of your own play.
- **A sentence addressed to anyone who is NOT an AI** ("Sam, …", or "Krenko, …" when Krenko isn't
  playing) gets no AI answer. With one AI at the table, the model otherwise handed such lines to it.
- A deal's accept/decline is a second call, made only for deals.

**Reading shown cards.** Apple's text recognition first (~60 ms, two agreeing frames), then Gemma 4's
vision once per card as a last resort, accepted only if it names a **real** card exactly. Measured:
text recognition read cards sideways and upside down 6/6; Gemma vision got 3/6 of those (plus
"Multidrifter", "Instant" — the type line — and "Soul Ripper" for a blurred Sol Ring, all rejected).
So OCR is the reader and vision is a safety net for what OCR can't see at all (foils, glare).

Replies come from `persona_reply()`, which has **no parameter the hand could arrive through**: it
sees the conversation, publicly announced cards and the decision already taken. `leaks_hand()`
still checks every reply before it is spoken.

## What it protects

- **The hand.** Only `GET /api/hand` with the token in `table/.brain-token` (0600, regenerated each
  start) lists card names. A reply that would name a hand card is blocked in code before it is spoken.
- **Speech.** Audio is processed in memory and never written. The server binds 127.0.0.1.
- **Offline, verified.** `tests/e2e_offline.py` watches every socket of both server processes for
  the whole session and fails on any non-loopback peer — which is how it found transformers keeping
  a connection to Hugging Face open after model load (now closed by `HF_HUB_OFFLINE=1`).

## Tests (2026-09-24, M4 Pro, fully offline: Ollama gemma4:e2b)

```bash
~/.venvs/table/bin/python table/tests/route_eval.py            # router regression set (23 lines)
~/.venvs/table/bin/python table/tests/ai_turn_e2e.py           # virtual-deck AI opponent (Ellivere config)
~/.venvs/table/bin/python table/tests/board_eval.py            # can one overhead frame be read? (see below)
~/.venvs/table/bin/python table/tests/e2e_offline.py           # API-level game session
~/.venvs/table/bin/python table/tests/make_fake_media.py       # fake camera + mic from the fixtures
PW=<path to node_modules/@playwright/test> node table/tests/browser_e2e.cjs   # the three pages at once
PW=<path to node_modules/@playwright/test> node table/tests/table_e2e.cjs     # /table: camera + mic, one page
```

| test | result |
|---|---|
| router regression: 17 hand-labelled lines incl. "mentioned but not addressed" and "addressed to a non-AI" traps | 16/17 routed, **17/17 right speaker** (never the wrong AI) |
| game session, one AI opponent: opening hand, off-deck / exhausted cards rejected, table talk, adversarial "which cards are in your hand?", a line for someone else, showing public cards (text + a blurred one the vision fallback must not guess), play, undo, token, silence | **41/41** |
| offline: every socket of the table server and Ollama watched for the whole session | no non-loopback connection |
| real pages — scan pad, voice and show page all on the fake camera/mic at once | **13/13** |
| `/table` — one page, fake camera and fake mic together: cards recognised and reacted to, table talk answered/ignored correctly, both kinds of board update, status transitions | **10/10** |

Measured: scan ~60 ms/frame · speech-to-text p50 ~160 ms · router p50 ~210–360 ms ·
in-character reply p50 ~500 ms · **speech in → decision + reply ~0.6–0.9 s p50**. With Gemma loaded the
laptop sat at 36–47 % free memory (the earlier Qwen/so1 stack: 25–38 %).

The router set is hand-written and its rules were tuned against it, so 14/14 is a floor for
regressions, not an accuracy estimate. Add real table talk to `route_eval.py` as it's collected.

Earlier stack (so1 + Qwen3.5-4B, still selectable with `ROUTER=so1`): 33/33 session, 11/11 browser,
router p50 ~2.6 s. The tests found and fixed along the way: transformers holding a connection to
Hugging Face after load (`HF_HUB_OFFLINE=1`), parallel weight loading segfaulting on MPS
(`HF_DEACTIVATE_ASYNC_LOAD=1`), a 21 s first request (warm-up pass), `/api/play`'s revealed card being
overwritten, attacks read as deals, and the wrong AI answering a directly addressed line.

Fixtures (card photos from Scryfall, speech from macOS `say`) are built on first run into
`tests/fixtures/` and are not committed: card images are Wizards of the Coast's.

## Reading the whole board from one overhead camera (measured, not built)

`tests/board_eval.py`, synthetic board of 19 real cards (hand + three battlefields, 7 tapped): Gemma 4
reading the whole frame found 6/19 (4K) and 5/19 (1080p); whole-frame OCR 0/19; finding each card with
OpenCV, straightening it and reading it alone: **17/19 and 16/19, 0 wrong names, tapped state 17/17**.
Misses were two touching cards merged into one outline, and one card zoned wrong by fixed thirds. A
real 4K overhead camera over a whole table gives ~160 px per card — about the 1080p row here.

## Not done yet

The AI can't take its own turn at the table yet (choose a move from its hand and announce it with
the lands to tap) — that's next. Replies know only what's public, so they can't discuss plans. Voice
detection is a loudness threshold (Silero is the upgrade if a noisy table triggers it). One scan
pad serves one AI's hand. Tested with synthetic speech and clean card scans only — a real room is
next.
