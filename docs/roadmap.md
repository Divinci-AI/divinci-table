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
- **Sensory player goal and test suite:** [goal-sensory-player.md](goal-sensory-player.md) — the AI plays through camera, mic and voice only; 51 scenarios, regression vs goal tiers.

## Camera gantry: two players now, four players next

**Now (CR-6 Max + Canon T5i).** The CR-6 Max is a bed-slinger: the head moves in X, the
bed moves in Y, and the gantry rises in Z. A camera on the head can only cover the bed itself, so the
prototype is a **two-player duel on the 400 × 400 mm bed**: each player gets a 400 × 195 mm half,
which holds two rows of six cards. From the top of Z the 18 mm lens sees about 500 × 330 mm, so one or
two shots cover the whole bed (`gantry.py plan`). Lower Z zooms in on single cards. Y moves are
slowed so the cards don't slide.

Steps:
1. Mount the Canon pointing down — on the X beam, not the carriage (it weighs ~800 g). Parts, diagrams, wiring and
   firmware: [hardware/gantry/README.md](../hardware/gantry/README.md), which also adds a suction head that can pick and turn cards.
2. Measure the rig and write `table/.cache/gantry.json`: `min_z`, `lens_at_z0_mm`,
   `cam_offset_mm`.
3. `gantry.py scan --area all --post` feeds the stitched frame to `/api/board`.

**Next (four players).** A full Commander table is about 1.2 m across, so the head must move in
X *and* Y over a fixed table:
- a CoreXY or H-bot gantry over the table, built from V-slot extrusion running Marlin or Klipper,
  so `gantry.py` drives it unchanged;
- or a fixed overhead camera for the whole table, with the CR-6 rig as the close-up "zoom" station.

## The Jumanji table: cards that move themselves

Same XY plane, upside down. A gantry (the printer's motion system, or a larger CoreXY) sits **under**
a glass tabletop. Its carriage carries an electromagnet (or a servo-lifted permanent magnet). Every
card is in a sleeve with a thin magnet, so the AI moves its own permanents across the glass, and the
board can reset itself at the end of a game. This is how self-moving chess boards already work.

Design questions to settle with a prototype on the CR-6 (glass on spacers above the head):
- **Glass and magnets:** a neodymium disc or a flexible magnet sheet in the sleeve, against how
  thick the glass can be (≤ 6 mm). The pull has to beat sleeve-on-glass friction without lifting
  the card. Felt or a low-friction film under the sleeves helps.
- **Tapping:** turning a card 90° needs two magnet points per sleeve, or a small rotary stage on the
  carriage.
- **Collisions:** cards overlap and cluster, so moves need path planning around the other
  permanents. Moving a card that sits under another card is a lift problem.
- **Whose cards move:** only the AI's permanents. Players' cards stay where the players put them,
  and the overhead camera confirms every move landed.
- **Driver:** `gantry.py` already speaks Marlin with a safe G-code gate. Magnet on/off would be one
  more allowed command, e.g. `M42` on a fan or spare pin, gated the same way.

What the self-moving chess boards and sand tables already worked out (research 2026-10-01):
- **The pattern is proven.** A magnet carriage under a ~3 mm acrylic top, a CoreXY or T-bot gantry,
  NEMA 17 steppers and an ESP32 running FluidNC or GRBL: Imperium, Mags, AndChen153/ChessBoard and
  the Instructables board. Sand tables (Sisyphus, rdudhagra/Sand-Table) do it with a permanent magnet
  under glass.
- **Routing matters more for cards than for chess.** Chess boards move pieces only along square
  edges so they never pass under another piece. For us it's stricter: the carriage magnet passing
  under ANY sleeved card drags it along. Moves must route through the gaps between cards, and the
  magnet must be off (electromagnet) or lowered (servo) when travelling empty.
- **Sensing:** chess boards put one Hall sensor per square. We have the overhead camera, which also
  reads the card. Hall sensors under each playing zone would be a cheap second check that a card
  arrived.
- **Fastest frame:** an open-frame laser engraver with the laser removed is already a flat XY gantry
  that runs GRBL. 400×400 mm models are cheap, and ~900×900 mm frames exist, which is close to a full
  Commander table. That's a better four-player starting point than a bed-slinger printer.
- **What we refuse:** matou/3d-printer-chess drives a gripper with the extruder motor (`M302 P1`
  cold extrusion). Our G-code gate refuses that by design. A magnet on a switched output needs no
  extruder.
- **Prior art (checked 2026-10-01, not legal advice):** the closest patent is Sega's US 6543770,
  "Card inverting device, card game machine": electromagnets under the table move and flip cards with
  ferromagnetic inserts. It **expired on 2020-07-18** (fee-related), so the core idea is public domain.
  US 5942744 and US 6016959 (Mitsubishi, "Card drive apparatus") are magnetic-stripe card *readers*,
  not card movers, and both expired in 2015. Expired prior art cuts both ways: we're free to build it,
  and it limits what we could patent to our specific additions (vision + rules engine + AI player +
  routing). Get a freedom-to-operate search from a patent attorney before filing or selling.

First experiment, no gantry needed: a sleeved card with a 10×1 mm neodymium disc, a 12 V lifting
electromagnet under 3 mm acrylic, moved by hand. Does the card follow at 50–100 mm/s without
lifting, and what does it drag along when it passes under a second card?
