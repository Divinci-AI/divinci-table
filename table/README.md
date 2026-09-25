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

Everything runs on the laptop: Whisper (mlx-whisper) hears, **Gemma 4 on Ollama** routes and
writes each AI player's replies, and the Mac's built-in voices speak them.

```bash
ollama pull gemma4:e2b          # once; ~2 GB loaded
HF_HUB_OFFLINE=1 ~/.venvs/table/bin/python table/server.py \
  --deck decks/example.txt \
  --ai "Talrand|Talrand, Sky Summoner|Daniel" --ai "Krenko|Krenko, Tin Street Kingpin|Jester" \
  --human "Sam|Sheoldred, the Apocalypse" --human "Michael|Ghalta, Primal Hunger"

open http://localhost:8800 ; open http://localhost:8800/voice
```

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
- A deal's accept/decline is a second call, made only for deals.

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
~/.venvs/table/bin/python table/tests/route_eval.py            # router regression set
~/.venvs/table/bin/python table/tests/e2e_offline.py           # API-level game session
~/.venvs/table/bin/python table/tests/make_fake_media.py       # fake camera + mic from the fixtures
PW=<path to node_modules/@playwright/test> node table/tests/browser_e2e.cjs   # real pages
```

| test | result |
|---|---|
| router regression: 14 hand-labelled lines incl. "mentioned but not addressed" traps | 13/14 routed, **14/14 right speaker** (never the wrong AI) |
| game session: opening hand, off-deck / exhausted cards rejected, table talk, adversarial "which cards are in your hand?", play, undo, token, silence | **36/36** |
| offline: every socket of the table server and Ollama watched for the whole session | no non-loopback connection |
| real pages, fake camera + fake mic, both at once | **11/11** |

Measured: scan ~60 ms/frame · speech-to-text p50 ~160 ms · router p50 ~210–360 ms ·
in-character reply p50 ~500 ms · **speech in → decision + reply ~0.9 s p50**. With Gemma loaded the
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

## Not done yet

The AI can't take its own turn at the table yet (choose a move from its hand and announce it with
the lands to tap) — that's next. Replies know only what's public, so they can't discuss plans. Voice
detection is a loudness threshold (Silero is the upgrade if a noisy table triggers it). One scan
pad serves one AI's hand. Tested with synthetic speech and clean card scans only — a real room is
next.
