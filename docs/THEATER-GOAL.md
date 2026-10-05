# Theater of the mind: D&D with no board, as a goal

*Run with `/goal docs/THEATER-GOAL.md` (from the divinci-table repo). Written 2026-10-05. Tick a box only when its
check has been run and its output read.*

## The idea

D&D the way it was mostly played before battle maps: the DM describes, the players say what they do, and the
picture lives in everyone's head. A **"theater" mode of the existing D&D room**, not a new product: no map, the
table speaks, everyone talks, positions are kept as zones. It must be fully playable **with every screen off**:
eyes closed, out on a walk, in a car, and by blind or low-vision players.

**Decided 2026-10-05 (Michael):**
- **The AI DM runs it; a person can take the DM seat at any time** (and hand it back).
- **Three settings:** remote, each person on their own phone (earbuds); the same room around a table (one
  speaker); and **solo** with the AI DM and AI companions.
- **Local model first, measured** before paying for anything: Whisper and a Gemma model on this Mac, voices on
  each device.

What exists already: hold-to-talk on phones (Whisper on this Mac, `/api/xr/stt`), the AI DM that already voices
AI companions in its replies, fair public dice, player-owned sheets, seats, initiative, the survey and the ledger.
What's missing: **the room can't speak** (narration is text only), there's no way to keep positions without the
grid, and nothing has been measured for a conversation's pace.

## Rules for every milestone

1. **Measure, don't assume.** Every box names its check.
2. **The table owns the facts:** dice, hit points (each player's own), conditions, initiative, **and zones**. The
   DM describes them and moves monsters; it never rolls, never changes a player's numbers, and never moves a
   creature somewhere the zone rules don't allow. Same principle as the grid (D7), same adversarial tests.
3. **Screens optional:** every action has a spoken way to do it, and every result is spoken. Buttons stay for
   whoever wants them, with screen-reader labels.
4. **Offline on the table path:** Whisper, a local model, on-device voices. No cloud model call without asking.
5. **Live play comes first:** no heavy tests or restarts during a session; the map mode keeps working unchanged.
6. **Open source:** voices and sounds must be device-built-in or CC0, credited.

---

## T1 — The scene card: positions without a grid
- [x] Server state: the scene's **zones** (a few named places: "the bar", "the door", "the gallery"), which zone
      each creature is in, who is **engaged** with whom, and cover. Range bands derived from it: *engaged*,
      *near* (same or next zone), *far*.
- [x] Movement in zones: reaching a next zone takes your move; two zones away needs a Dash; leaving an engaged
      foe is noted (it can make an opportunity attack). The DM's `TABLE:` line sets zones and moves monsters;
      players move themselves on their own turn, as on the grid.
- [x] **Check:** `dnd_test` cases for each rule; the adversarial DM simulation (`dnd_dm_sim_test.py`) gains zone
      cases (no teleporting a player, no moving someone else's character, no new zones mid-fight); each rule
      mutation-checked.

## T2 — The table speaks
- [x] The DM's narration goes out **sentence by sentence** as it's written (events per sentence), so the first
      words are heard before the whole reply exists.
- [x] Each device speaks with **its own built-in voices** (Web Speech API): no audio over the network, no cost.
      Every speaker has a steady voice: the narrator, each NPC, each AI companion, picked from what the device has.
- [x] Rolls, hit points, turns and conditions are announced in short spoken lines ("Ana: 9 of 13", "Ben's turn").
- [x] **Same room:** one device is "the table speaker"; the others stay quiet unless their owner wants their own
      copy in earbuds.
- [x] **Check:** a browser test with a stubbed `speechSynthesis` records what each device says and when: the
      right voice per speaker, nothing said twice, and **time from the player letting go of 🎙 to the first spoken
      word**, measured. Target to beat: **≤ 3 s** with the local model.

## T3 — Theater mode in the room
- [x] A room setting (chosen at creation, switchable by the DM): no map; one large 🎙 hold-to-talk button; the
      scene card as plain text for anyone looking; the log.
- [x] Spoken questions answered **by code from the scene card**, not by the model: "where am I?", "what's near
      me?", "whose turn is it?", "how hurt am I?". Consistent every time, and instant.
- [x] Voice dice: "roll stealth", "roll a d20 with advantage", "I rolled fourteen" (a real die).
- [ ] Eyes-free: works with screen reader on; large targets; nothing needs looking at.
- [ ] **Screen-off risk, tested first:** phones pause web pages when the screen locks, which would stop the voice on
      a walk. Measure on an iPhone and an Android phone with the screen locked; mitigations to try in order: a
      wake lock with the screen dimmed, a media session that keeps audio alive, a home-screen app.
- [x] **Check:** a browser test plays a whole scene using only spoken input (fake microphone) and spoken output
      (stubbed synthesis), never clicking the map; the answers to the four questions match the scene card.

## T4 — The local DM, measured
- [x] Ten fixed scenes (a tavern talk, an ambush, a chase, a puzzle door, a death-saving throw…), each with a
      scene card and a player's line. Score each DM reply: **vivid** (does it paint a picture?), **consistent**
      (does it contradict the scene card?), **rules** (does it roll or change a player's numbers? it must not),
      **time to first sentence**.
- [ ] Models: `gemma4:e2b` (installed). Then one larger local model, **only after Michael OKs the download size**.
      Michael scores the replies blind (which model wrote which is hidden).
- [ ] **Check:** the score table in `docs/results/`; pick the model; if none is good enough, say so, and the
      Fusion cloud DM becomes a decision with its cost per session written next to it.

## T5 — Sound instead of pictures
- [x] CC0 ambience per location (tavern chatter, wind on the road, water in the cave), quiet, ducked under speech.
- [ ] Optional, headphones: positional cues from the zones (the wolf growls on your left).
- [x] **Check:** every sound's licence listed and credited; ambience never covers speech (measured level drop
      while a voice speaks).

## T6 — Solo, with AI companions
- [ ] One player, the AI DM and two AI companions with their own voices and personalities; the companions speak
      when addressed or when their chattiness says so (`openmic.py`, already in the D&D room).
- [ ] **Check:** Michael plays a 20-minute solo story on a walk, screen off (if T3 found a way). Survey after.

## T7 — The same room
- [ ] One table speaker; everyone talks on their own phone's 🎙 (no shared room microphone in this version: one
      room mic with crosstalk is a later problem).
- [ ] **Check:** a 3-person session at one table; count talking over each other, misheard lines, and the T2 delay.

## T8 — Playtest 2: the theater edition, remote
- [ ] Like Playtest 1 (`docs/PLAYTEST-1.md`), but no board: 3–4 people on their own phones from different places,
      the AI DM running it, a person taking the DM seat for one scene.
- [ ] **Check:** the survey, the bug list, the delay numbers, the cost; what broke becomes the next goal.

## Done log

- **2026-10-05 · T5 done (the optional positional cues aside).** Instead of CC0 recordings, the ambience is
  **synthesized in the browser** (`table/assets/ambience.js`, Web Audio: noise, filters, oscillators): the table's
  own code, nothing to license or download, works offline. A mix per place (tavern murmur, fire and clinks; wind
  and birds on the road and in the forest; echoing drips over a low rumble in the cave; water at the bridge…),
  following the location; 🎵 switch, on by default in theater mode; credited at `/api/dnd/credits`.
  `dnd_sound_e2e.cjs` 10/10, measured on the actual output: about −39 dBFS, **−12.6 dB under speech** (design 12,
  floor 9), back after; the DM's move to the cave changes it; the switch turns it off.

- **2026-10-05 · T4, the measurable half.** `t4_dm_eval.py` + `docs/results/t4/` (replies, a blind scoring sheet,
  the hidden key, a summary). gemma4:e2b: first sentence 0.4–1.3 s but 1–3 rule faults in 10 (moving a player in
  TABLE, echoing the state, **missing the death saving throw**, once speaking for a player); +think: 0–1 fault (the
  death save) but 6–10 s to speak. Waiting on: Michael's blind scores, and his OK for a larger local model's
  download before trying it.
  Then two prompt rules from the table's own state (who the players are; a downed character's death save):
  gemma4:e2b over 4 runs asks for the death save every time and writes nothing for players; only TABLE-line
  hygiene faults remain (0–2 in 10), which the table already drops.

- **2026-10-05 · T3 built; two items wait for real phones.** Theater mode in the room: no board, a large 🎙, a
  "Where things are" panel from the scene card, a mode switch (the DM's seat, or anyone seated when the AI DM runs
  it), and in the cloud lobby a new "D&D, theater of the mind" choice (Worker + entrypoint; **not deployed**).
  "Where am I?", "what's near me?", "whose turn is it?", "how hurt am I?" are answered by code to the asker only,
  and the DM isn't called; dice by voice ("roll stealth with advantage", "I rolled fourteen for perception").
  `dnd_theater_e2e.cjs` 18/18 (a scene with no board touched; one question spoken into a fake mic and transcribed by
  Whisper, answered aloud); `dnd_voice_test.py` 32/32 (commands, and eight ordinary lines that must stay actions).
  **Still open:** eyes-free with a real screen reader, and the **locked-screen test on an iPhone and an Android
  phone** (a "☀ Keep awake" wake-lock switch is in, the first mitigation; whether it's enough needs the phones).

- **2026-10-05 · T2 done.** The DM's reply streams (`--dm-backend ollama:<model>[+think]`, or `script:<file>` for
  tests and demos) and becomes `dm_part` events, one per finished sentence, each with its voice (narrator, an AI
  companion's line, an `NPC Name:` line), never the TABLE line, however the stream is chopped (`dnd_voice_test.py`
  15/15). Each device speaks with its own voices, one steady voice per speaker; rolls, hit points, turns, conditions
  and zone moves are announced; others' actions are voiced, your own isn't read back; a page that opens later
  doesn't read the history out; the same room picks one table speaker (`dnd_voice_e2e.cjs` 14/14).
  **Measured** (`table/tests/measure_voice_loop.cjs`, real Whisper + gemma4:e2b, 5 trials): release 🎙 → first
  spoken word **median 2.65 s** (target ≤ 3 s; STT ~0.3 s, the DM's first sentence 0.65–1.07 s, the page's poll
  0.7–1.9 s). With gemma4's thinking on it was **6.4 s**: it thinks 4.5–5.3 s before its first word, so thinking is
  off by default (`+think` turns it on; T4 judges whether it writes better). The poll is now the largest piece:
  long-polling (DND-3D-GOAL D10 #1) would take the median to about 1.6 s.

- **2026-10-05 · T1 done.** `table/dnd_zones.py` (zones, range bands, move/Dash, engagement, "where am I?");
  `dnd_server.py --mode theater`: the scene card in the state and the DM's prompt (theater instructions instead of
  the grid), `zones` / `zone` / `engage` in the TABLE line, `/api/dnd/zone/move` and `/zone/engage` for players.
  `dnd_zones_test.py` 36/36; `dnd_dm_sim_test.py` gains a 120-line randomized zone DM (20 accepted, 209 refused,
  every rule held). Mutation-checked: the DM moving people (2 + sim), no per-turn limit (9), zones mid-fight (5),
  engaging across zones (4), moving out of turn (sim). Map mode unchanged (all D&D suites green).
