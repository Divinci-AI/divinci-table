# The table server: scan pad + open mic, fully offline

The first real-table piece. One Python server on the laptop (the Jetson later) that:

- **Scan pad** (`/`) — the AI's hidden hand. A drawn card is held face toward the laptop camera
  (the person holding it sees only the back); the server reads the name line with Apple's Vision
  OCR, picks the card among those **still in the AI's library** (a choice, not open-ended OCR), and
  puts it in the next numbered slot — the sticky notes on the table. Pages and terminal show slot
  numbers only; a name is revealed when the slot is played.
- **Open mic** (`/voice`) — in-browser voice detection → local Whisper (with the game's player and
  card names as a hint) → a System One router decides what was said and to whom → the addressed AI
  answers by voice; play announcements update the board; chatter is ignored.

## Run it offline

```bash
# 1. the local Jev (open-alternative-jev) — first run needs HF_HUB_OFFLINE=0 to download the model
harness/run-so1-guarded.sh --model Qwen/Qwen3.5-4B --port 8792

# 2. the table server
ROUTER=so1 HF_HUB_OFFLINE=1 ~/.venvs/table/bin/python table/server.py \
  --deck decks/example.txt \
  --ai "Talrand|Talrand, Sky Summoner" --ai "Krenko|Krenko, Tin Street Kingpin" \
  --human "Sam|Sheoldred, the Apocalypse" --human "Michael|Ghalta, Primal Hunger"

open http://localhost:8800 ; open http://localhost:8800/voice
```

`ROUTER` picks the brain that classifies speech: `so1` (local; the default), `ollama` (a
local Gemma via Ollama; one call per question, so ~3 s), or `typesafe` (TypeSafe's hosted
`jev-latest`; needs `TYPESAFE_API_KEY`, billed per call, **sends transcripts off the machine**).
`localhost` is a secure context, so camera and mic work without HTTPS. Needs macOS for the OCR
(`ocr_mac.py`); the Jetson needs a Linux OCR backend behind the same `read_lines()`.

Setup: `python3 -m venv ~/.venvs/table && ~/.venvs/table/bin/pip install pyobjc-framework-Vision
pyobjc-framework-Quartz mlx-whisper`, and the so1 venv described in `harness/README.md`.

## What it protects

- **The hand.** Only `GET /api/hand` with the token in `table/.brain-token` (0600, regenerated each
  start) lists card names. A reply that would name a hand card is blocked in code before it is spoken.
- **Speech.** Audio is processed in memory and never written. The server binds 127.0.0.1.
- **Offline, verified.** `tests/e2e_offline.py` watches every socket of both server processes for
  the whole session and fails on any non-loopback peer — which is how it found transformers keeping
  a connection to Hugging Face open after model load (now closed by `HF_HUB_OFFLINE=1`).

## Tests (2026-09-24, M4 Pro, ROUTER=so1 with Qwen3.5-4B)

```bash
~/.venvs/table/bin/python table/tests/e2e_offline.py          # API-level game session
~/.venvs/table/bin/python table/tests/make_fake_media.py       # fake camera + mic from the fixtures
PW=<path to node_modules/@playwright/test> node table/tests/browser_e2e.cjs   # real pages
```

| test | result |
|---|---|
| game session: opening hand, off-deck and exhausted cards rejected, table talk, play, undo, token, silence, offline watch | **33/33** |
| real pages, fake camera + fake mic, both running at once | **11/11** |
| harness games against the same so1 server | 52 decisions, 0 fallbacks |

Measured: scan ~60 ms/frame; speech-to-text p50 ~230 ms; **router p50 ~2.6 s** (Qwen3.5's
linear-attention layers run on reference PyTorch kernels on Apple GPUs); utterance-to-decision
~2.8 s. With both servers loaded this laptop sat at 25–38 % free memory — workable, no headroom.

Routing matched TypeSafe's jev-latest on who speaks and what for 8/8 scripted lines. The one
router miss the tests found ("Ghalta attacks Sam" read as a *deal*) was the option wording — "deal"
included "threat"; it now describes an agreement, and attacks sit under "play".

Fixtures (card photos from Scryfall, speech from macOS `say`) are built on first run into
`tests/fixtures/` and are not committed: card images are Wizards of the Coast's.

## Not done yet

Replies are templates ("Let me think about that.", "Deal.") — next is each AI player's Divinci
Release writing them in character, with its own Divinci TTS voice. Voice detection is a loudness
threshold (Silero is the upgrade if a noisy table triggers it). One scan pad serves one AI's hand.
