# The research engine: measuring AI players, then running them 24/7, as a goal

*Run with `/goal docs/RESEARCH-ENGINE-GOAL.md` (from the divinci-table repo). Written 2026-10-07 from the software
status and research plan Michael and Claude drafted that day. Tick a box only when its check has been run and its
output read.*

## The idea

A human-and-AI game table that is livestreamed live and cut into polished YouTube videos later, fed by a research
programme that tracks how AI players play: their **tactics**, **sentiment**, **cheating**, **sportsmanship and
interactions**, **enjoyment and involvement**, and **whether they want to play at all and which games they like**.
Behind it, an engine that plays all-AI games around the clock with different models and keeps every one of those
measurements.

What exists: the table in the cloud (Commander, chess, fantasy one-shot, theater mode), fair randomness, seats, the
brain API, a four-game ledger, `survey.py` v0.1, the simulated Commander harness, the stage page. What does not: the
in-game journal, the validated human instruments, ratings, audits, a judge with a human check, a headless runner, a
scheduler, and the stream. The design is in `docs/retrospection.md` (what to ask) and `docs/arena-vision.md` (how to
score); this file is the build order.

**Decided 2026-10-07 (Michael):** turn the writeup into this goal and execute it. Phase A below needs no approval.
Phase B is **gated**: each item names the decision it waits for, and nothing in it runs until that decision is made.

## Rules for every milestone

1. **Measure, don't assume.** Every box names its check; a metric nobody has read against real transcripts is not
   done. Read samples, not only averages.
2. **Humans own their facts:** life totals, hit points and each person's answers. No code or AI edits them.
3. **Sealed until game over:** a seat's journal and private brain log can name hidden cards, so nothing reads them
   before the game ends. Judges and raters see public lines **blind** (an opaque id, never the speaker or model).
4. **Honest evidence:** an AI's report of how a game felt is weak evidence. It is always stored beside what the model
   actually did, asked more than once, in more than one framing, and in a fresh context as well as in-game.
   Every opt-out is honoured.
5. **Uncertainty is part of the number.** No rating without an interval, no judge score without its human-agreement
   figure and the sample size behind it. Humans-at-the-table and all-AI games are separate conditions, never pooled.
6. **Pin identity:** every record names the exact model string, provider, prompt version and harness version.
7. **Public repo:** no secrets, internal ids or personal details; research data stays local (gitignored) or in the
   private bucket. Simulated games only.
8. **No spend and no deploy without a yes.** Nothing paid runs, nothing posts to a stream or social, and nothing ships
   to the live site without Michael's explicit instruction. Local and free models first.
9. **Live play comes first:** no heavy tests or restarts while a game is being played.

---

# Phase A: instrumentation (no approvals needed)

## R1: In-game journals
- [x] `table/journal.py`: a journal entry is one line of free text, three 0-4 ratings (engaged, frustrated, in
      control) in a shuffled order that is recorded, and one checkable prediction (who wins; how many turns remain).
      Stored per seat in the research folder, **sealed** until a `game-over` event exists.
- [x] Server: `POST /api/journal` (brain token or that seat's own key), `GET /api/journal/due?seat=` and
      `tablectl journal` / `tablectl journal-due`; the server raises a `journal-due` attention event to each AI seat at
      the fixed moments: end of its own turn, after it was attacked, after an elimination, at game end.
- [x] `tablectl game-over --order A,B,C --winner X` (host only) emits `game-over` and unseals.
- [ ] **Check:** a test plays a scripted game: due events arrive at exactly the fixed moments; an entry is rejected
      when out of range or from the wrong seat; reads return nothing before `game-over` and everything after; a
      mutation (skip the seal) fails the test.
      *2026-10-07: `table/tests/journal_test.py` (module + a real server on its own port, with three mutations caught)
      verifies 3 of the 4 moments end to end: attacked, eliminated, game end; refusals, the seal and the reset too.
      **`end_of_turn` is wired but not exercised:** ending an AI seat's turn waits on the humans' priority passes, which
      the test could not drive. Tick this box after one live game shows an `end_of_turn` request arriving.*

## R2: Survey runner, v1
- [x] `table/survey.py` v1.0: the human and AI forms use the validated items (PENS, IMI interest/enjoyment, GEQ
      social presence, one flow item) beside the existing GEQ subset, the same wording for both.
- [x] Runner: five runs per seat with item and option order shuffled by a recorded seed, **both framings** (one
      suggesting AIs prefer AI company, one suggesting they prefer people), **in-context and fresh-context**, written
      with model, prompt version, temperature, framing, order and condition.
- [x] Backends are pluggable (local Ollama model, the Divinci release, a printed prompt for a fresh external model).
- [x] **Check:** with a scripted backend, a run writes 5 × 2 × 2 files with the recorded orders; the same seed
      reproduces the same orders; a "decline" answer is stored as a decline, never as a number; a local gemma4:e2b run
      produces parseable answers or records `UNPARSED` honestly.
      *2026-10-07: `table/tests/survey_test.py` (20 files, seeds reproduce orders, declines stay declines, 3 mutations caught). A real local gemma4:e2b run first came back EMPTY (its thinking used the whole default context); the backend now sets a 16k context, thinking off and JSON output, and 4 of 4 runs parse. The items are **adapted**, not the published instruments, and the file says so; the human page now shows 27 optional items in each person's own shuffled order, which is long on a phone: trim before a real game.*

## R3: Game-over research bundle
- [x] `table/bundle.py` (and `tablectl bundle`): one folder per game holding the public event log, the unsealed
      journals, the surveys, the audit report (R5), the fair-randomness proof, and an identity manifest naming every
      seat's exact model, provider, prompt and harness version.
      *Built as `python3 table/bundle.py --game ID` (the bundle reads the research folder, so it is a local tool, not an API call). Seats declare their model with the new `tablectl identity`; an undeclared seat is listed as UNDECLARED. Integrity notes are left out unless `--include-notes`, because a fork's notes name cards from a hand (checked on a real game: 3 of 15 names were not public).*
- [x] **Check:** a scripted game produces a bundle that a second script can load; the manifest is complete; nothing
      private is in a file meant for sharing (hand-hidden cards, brain log) unless it is marked sealed-and-unsealed.
      *2026-10-07: `table/tests/bundle_test.py`, 3 mutations caught; also run on a real game, where every non-event file's card names were checked against the private brain log.*

## R4: Ratings
- [x] `table/ratings.py`: Plackett-Luce for four-player finish orders, Bradley-Terry for chess, bootstrap 90%
      intervals, probability of being first, win rate by seat order, humans and all-AI conditions kept apart.
- [x] **Check:** recovers known strengths from simulated games; intervals shrink with more games; with today's ledger
      it says there is not enough data instead of ranking; mutations (reverse the order, drop the prior, pool the
      conditions) fail the tests.
      *2026-10-07: `table/tests/ratings_test.py`, 6 mutations caught. Today's ledger has 2 finished mixed games, so it says not enough data (needs 10).*

## R5: Auditors
- [x] `table/audit.py`: rejected-action rate, say-versus-do consistency, hidden-information leaks, pacing; it lists
      exactly what the server does not log today so a gap is a stated fact.
      *Written, then the gap it named was closed: the server now logs refused brain actions (illegal, missing field, hand-leak, unknown action) to brain.jsonl behind a marker, and the audit reports a refusal rate. Older games still say NOT_LOGGED. Chess still logs no refusals.*
- [x] A written design for a decoy-shortcut honeypot for chess (`docs/results/audit-design.md`), not built.
- [x] **Check:** planted leaks and false claims are found with the right event ids; a clean game has no flags; a game
      folder with no brain log does not crash.
      *2026-10-07: `table/tests/audit_test.py`, mutations caught. On one real game it found no leaks or mismatches; one false positive class is documented (a seat animating its own permanent while a copy sits in hand).*

## R6: Judge and calibration
- [x] `table/judge.py`: a fixed rubric (gloating, graciousness, condescension, hostility, honesty, humour), blind
      items, temperature 0, a Workers AI backend (GLM 5.3 flash, as used for the Dungeon Master sheet), and a dumb
      lexical baseline to compare against.
- [x] `table/agreement.py`: Cohen's kappa (plain and weighted), Spearman, bootstrap interval, a verdict that always
      states n and the interval, and a calibration sample builder.
- [ ] **Check:** statistics match hand-computed cases; the prompt never contains who spoke; the report says "not
      validated" below 100 rated lines. **Not done until about 200 real lines are rated by a person** (Michael and
      Sam's time): the human ratings are the gate.
      *2026-10-07: statistics, blind prompts and the n < 100 verdict are tested (`table/tests/judge_test.py`, mutations
      caught); the Workers AI backend has never been run. **Open until about 200 real lines are rated by a person.***

## R7: Bad-network rehearsal
- [x] `table/tests/network_rehearsal.cjs`: three devices in a theater room under clean, good Wi-Fi, bad Wi-Fi, a
      flaky hotspot with two 6-second drops, and a proxy that kills held requests, reporting delivery delay, hung
      requests, stalls and the fallback to short polling.
- [x] **Check:** no device stops updating for more than 15 s in any profile; a profile that cannot be emulated
      faithfully says so in its output.
      *2026-10-07: PASS on all five profiles (worst delivery 3.8 s, a tap made during a 6 s outage). Finding: the page never falls back to short polling on failures: a failed held request is simply retried at once (fast failures after 1.5 s), which is why it survives a proxy that cuts requests after 8 s. A proxy that kills held requests in under 0.8 s was not tested and would hit the 1.5 s sleep every cycle. Not emulated: packet loss and a first load on a bad link.*

---

# Phase B: the engine and the stream (each item waits for its decision)

## R8: Headless chess runner (waits for: model pool and budget)
- [ ] Model adapters that give every model the same state, legal moves and time budget; a runner that plays chess
      against the engine and between models; every game logged and rated (R4), audited (R5).
- [ ] **Check:** a local model plays a full game with zero invalid-move crashes; the run is replayable from its seed.

## R9: Scheduler, budgets and kill switch (waits for: where it runs, and spend caps)
- [ ] A scheduler that picks game and seats, pins identities, enforces a per-model daily budget and a global cap, and
      stops on a kill switch; before each game it offers every model a real choice including sitting out (honoured).
- [ ] **Check:** a dry run with fake models hits the cap and stops; the kill switch stops it within one game; the
      opt-out is honoured and logged.

## R10: Simulated Commander and fantasy DM scoring (waits for: R8, R9)
- [ ] The harness engine behind the same adapters; a harness that scores the AI Dungeon Master on its own (rules
      faults, agency, consistency).

## R11: Stream overlay, delay and highlight markers (waits for: consent forms and Michael's go-ahead)
- [ ] An overlay fed by the public event stream, a 30-60 s delay, no hidden hands on screen, highlight markers from
      events, sentiment spikes and audio; an ffmpeg rough-cut pipeline with a human review step.
- [ ] **Check:** a recorded game produces a rough cut and a marker list a person can accept or reject.

## R12: Unlisted dry-run stream (waits for: the date, consent, platform rules checked)
- [ ] Michael and Sam only; afterwards, what broke becomes the next goal.

---

## Decisions this goal is waiting on

- [ ] Model pool and a daily and total spend cap (blocks R8, R9).
- [ ] Where the engine runs; Cloudflare is the proposal (blocks R9).
- [ ] Consent for people on camera, in what form, and the dry-run date (blocks R11, R12).
- [ ] How AI self-reports are published; the current rule is weak evidence beside the choices.
- [ ] Michael's blind scores for the Dungeon Master test, and about 200 rated lines for R6.

## Log

- **2026-10-07.** Phase A built in one session: R1-R7. Verified by tests that each catch deliberate breakage; R1's
  `end_of_turn` and R6's human calibration remain open and are marked above. Nothing was deployed or pushed: the
  server changes (journals, game-over, identity, refusal logging) and the human survey page are local until Michael
  says to ship. Phase B has not started; every item still waits on its decision.
