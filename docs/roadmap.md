# Roadmap: from the harness to a real table

## 1. Pick the model (harness)

Bar to beat, same 32 pairs with the land rule on: `jev-latest` 6/32 wins, outlasted the heuristic in
11 seats (shorter in 8). Candidates, in the order we can run them:

| candidate | where | status |
|---|---|---|
| so1 + Qwen3.6-27B (8-bit) | Colab A100-40GB | loads there, matches Jev on the Divinci classifier replay; not yet at the table |
| djev, INT4 AWQ (`cyankiwi/diffusiongemma-26B-A4B-it-AWQ-INT4`, 17.2 GB) | Colab A100 or Jetson | untested: does the pinned vLLM diffusion commit load it? |
| either | Jetson AGX Orin 64 GB (Ampere: no NVFP4, no FP8) | hardware arriving |


## 2. The table (PWA + table server)

**Status 2026-09-24:** the laptop version works end to end offline — see `table/README.md`
(scan pad, open mic, local so1 router, 33/33 + 11/11 tests). The phone PWA is next.

The phones are the AI's eyes, ears, and voice; the table server (a laptop now, the Jetson later) is
its brain and holds the game state.

- **PWA, one app, three roles** chosen on join via a QR code: *scan pad* (reads the AI's drawn
  cards), *table cam* (the public board), *voice* (hears announcements, speaks its moves).
- **Hidden hand:** a drawn card goes face-down onto a scan box: the phone at the bottom, camera up,
  with the card about 10 cm above it and a light. The card face is never shown on screen. It goes
  into the next open numbered sticky-note slot. Identifying it is a choice over the cards left in
  the AI's library, not open-ended OCR.
- **Browser constraints to design around:** camera/mic need HTTPS, and an HTTPS page can't open a
  `ws://` connection to the LAN, so use a Cloudflare Tunnel behind Access. iOS Safari torch control
  is uncertain, so use an LED light. There is no on-device text recognition in the browser, so the
  server reads cards. Web speech recognition goes through Apple/Google servers.
- **Rules:** start table-enforced (code offers rough options, humans overrule anything illegal);
  Forge is the path to full rules for real 100-card decks.
- The land rule (`Game(auto_land=True)`) stays on for every seat.

## 3. Divinci: each AI player is a Release

Each AI seat is backed by a Divinci **Release**, which gives it:

- a **personality**: the Release's instructions shape its play style and table talk (aggressive
  Krenko goblin boss, cautious control mage)
- a **TTS voice**: it announces moves in its own voice through Divinci TTS
- (later) **memory** of the playgroup and past games

The decision model (Jev / djev / so1) still chooses the move. The Release decides who the player is
and how it speaks, so a table can seat several distinct AI opponents.

Open questions for that step: which Divinci TTS provider and voices, how a Release's persona is
given to the decision model without hurting its choices (put persona in the state? only in speech?),
and whether table talk goes through the Release's chat endpoint.
