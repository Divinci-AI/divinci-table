# Platform plan: remote tables where people and AIs play together

*2026-10-03, after game 3. Michael and Sam want the table hosted in the cloud so people can play
remotely, keeping the real-world component (your own physical cards, board and dice on camera), then
widening from Magic to chess, poker, Catan and Dungeons & Dragons. Alongside that: an open mic where a
local System One model decides whether an AI should speak, with a wake word and per-personality
chattiness.*

## Where we are

Three games of Commander, one laptop. What already exists and carries over:

| Piece | Today | Carries over as |
|---|---|---|
| Table server (`table/server.py`) | one Python process, in-memory state, pickle snapshots, polled event log | the room engine (one room per process at first) |
| Seats | claim keys, device fingerprints, hand-off, open-market invites | seat model; keys become accounts later |
| Pass rounds, priority windows, auto-pass | Magic-specific steps | a generic "everyone acknowledges" round |
| Fair randomness | sealed seeds from ANU + drand, published fingerprints, high roll | dice, shuffles and deals for every game |
| Photos, talk mode, sidekick, checklist | per-person phone UI | the real-world component for any game |
| AI seats | Fusion (Divinci release), Claude (external brain) | any model or release can sit at any seat |
| Research | event log, integrity notes, surveys, balanced asks | per-game record + the arena ledger |
| Open mic (`table/voice.py`) | Whisper → router (Jev / so1 / Gemma) → who it's for, what kind | the open-mic v2 below |

What does **not** carry to the cloud as-is: anything that runs on the Mac — Whisper MLX, Apple Vision
card OCR, Gemma through Ollama, `say` voices, the Canon gantry.

## Phase 0 — remote Magic with what we have (days)

Before investing in hosting, find out what remote play actually needs.

- **Expose the laptop table safely:** Cloudflare Tunnel to a `divinci.app` hostname behind Cloudflare
  Access (email one-time code, Michael and Sam only). That also fixes HTTPS, which phone cameras and
  microphones require.
- **Each remote player** keeps their own physical deck and uses two devices: a phone propped over
  their play area as a table cam (photo upload already exists; a live snapshot every few seconds is
  next), and a second phone or laptop for `/me`.
- **Voice:** a plain call (FaceTime/Meet) for the first session; the table's own audio comes in Phase 1.
- **Learn from it:** what broke, what was slow, whether the board was readable from a phone camera.

## Phase 1 — a hosted table (weeks)

- **Run one container per table room**, single instance (state lives in memory; snapshots go to object
  storage after every change, so a restart resumes exactly like `--restore` does now). This fits Cloud
  Run with max-instances 1 or a small VM; it does not fit an autoscaled service without a state
  rewrite, and we should not do that rewrite yet.
- **Replace the Mac-only parts:** speech-to-text and card reading move to a cloud model or to the
  browser; TTS moves to Divinci TTS (already planned for AI personalities).
- **Accounts:** sign-in replaces pasted seat keys; device fingerprints still let one person use
  several windows.
- **Lobby:** create a room, pick a game, invite people, seat AIs (choose model + personality), and the
  existing open-market invite for an empty seat.
- **Push instead of polling:** server-sent events for the event stream (polling exhausted local ports
  twice in game 3).

## Phase 2 — one core, many games

Pull the game-independent parts out of the Magic server: rooms, seats, acknowledgement rounds, talk,
photos, fair randomness, event log, research logging and surveys. Each game becomes a module that owns
its rules state, its legal actions and its hidden information.

Suggested order, each chosen for what it teaches the platform:

1. **Chess** — perfect information and mature engines (python-chess, Stockfish) make it the cheapest
   proof that the core really is game-independent, and an easy benchmark for named models.
2. **Poker** — hidden hands and bluffing; reuses the sealed-shuffle work directly. Play chips only:
   real-money play brings gambling law, and the salty bets in `arena-vision.md` stay outside the game.
3. **Dungeons & Dragons** — the biggest opportunity. An AI Dungeon Master is a Divinci release whose
   knowledge base holds the rules (SRD 5.1 is licensed CC-BY 4.0) and the campaign; AI party members
   are releases with personalities; fair dice; minis and maps on the table cam. Open-ended talk is the
   whole game, so it depends on the open mic most.
4. **Catan** — dice, trading talk and a physical board read by the camera. The name, art and rules
   text are trademarked and copyrighted, so we should play it as people's own physical copy rather
   than ship it.

## The open mic, v2

The goal is an AI that is quiet by default, answers when called, notices when something said
changes the game, and only sometimes chimes in on its own — more often for chattier personalities.

**Pipeline, per utterance:** voice-activity detection → speech-to-text → three checks → code decides.

1. **Wake word, in code first.** A seat's name at the start of a line ("Fusion, …", "Hey Claude") or
   "Hey Divinci" always gets a reply (still through the hidden-card filter). A small on-device keyword
   spotter can come later to avoid transcribing everything.
2. **Is it about the game?** A yes/no judgment: does this describe a play, a life change or a rules
   question? If yes, it goes to the game parser and the game log; if no, it stays ephemeral.
3. **Should an AI speak unprompted?** Scored per AI seat with its personality in the state, on levels
   that each describe a concrete situation: *not needed* (side chatter), *small talk*, *useful* (a
   rules point, a reaction to its own card), *needed* (someone is waiting on that AI).
4. **Code holds the policy.** Each personality gets a threshold, a cooldown, a budget (interjections
   per ten minutes) and hard rules: never talk over someone (wait for a silence gap), never twice in a
   row, never during another seat's decision. Default is conservative: wake word, direct address or
   "needed" only. A chatty personality lowers its threshold, but keeps a budget.

**The judge is the local System One model.** `voice.py` already supports a local router
(`ROUTER=so1`, or Gemma through Ollama) as well as TypeSafe's hosted Jev. Local matters here: an open
mic hears side conversations, and a local router keeps those transcripts on the table host. In the
cloud version this becomes a placement decision — the judge has to run where the audio is processed.

**Privacy rules:** visible "mic on" indicator and a mute per device; audio never stored (already
true); only game-relevant lines enter the log; non-game talk is dropped.

**Calibrate on our own data:** log each decision (the scores, not the audio), add 👍/🙄 buttons on
every unprompted AI line, and set thresholds from those ratings, not from intuition.

## Research continuity

Every game, on any game type, keeps the record started this week: sealed randomness, event log,
integrity notes, post-game surveys, and a results ledger (who sat where, which model and personality,
which deck or role, finishing order) for the arena rankings in `arena-vision.md`.

## Proposed first steps

1. Phase 0 tunnel + Access, and one remote game with Sam in Santa Monica.
2. Open mic v2 (wake word, the three checks, personality budgets, rating buttons) — tested at that game.
3. Containerise the table server with snapshot-to-storage; host one room.
4. Extract the core; build chess on it.
5. D&D Dungeon Master prototype.

## Decisions for Michael and Sam

- **Hosting:** Divinci's GCP (Cloud Run, where the API already runs) or Cloudflare (Workers + Durable
  Objects, where the web client runs)? Single-instance Cloud Run is the shortest path from today's code.
- **Who can join at first:** invite-only friends, or public rooms?
- **Which game after Magic:** chess first (cheapest core test) or straight to D&D (biggest pull)?
