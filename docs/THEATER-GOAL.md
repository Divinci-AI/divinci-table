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
- [ ] Server state: the scene's **zones** (a few named places: "the bar", "the door", "the gallery"), which zone
      each creature is in, who is **engaged** with whom, and cover. Range bands derived from it: *engaged*,
      *near* (same or next zone), *far*.
- [ ] Movement in zones: reaching a next zone takes your move; two zones away needs a Dash; leaving an engaged
      foe is noted (it can make an opportunity attack). The DM's `TABLE:` line sets zones and moves monsters;
      players move themselves on their own turn, as on the grid.
- [ ] **Check:** `dnd_test` cases for each rule; the adversarial DM simulation (`dnd_dm_sim_test.py`) gains zone
      cases (no teleporting a player, no moving someone else's character, no new zones mid-fight); each rule
      mutation-checked.

## T2 — The table speaks
- [ ] The DM's narration goes out **sentence by sentence** as it's written (events per sentence), so the first
      words are heard before the whole reply exists.
- [ ] Each device speaks with **its own built-in voices** (Web Speech API): no audio over the network, no cost.
      Every speaker has a steady voice: the narrator, each NPC, each AI companion, picked from what the device has.
- [ ] Rolls, hit points, turns and conditions are announced in short spoken lines ("Ana: 9 of 13", "Ben's turn").
- [ ] **Same room:** one device is "the table speaker"; the others stay quiet unless their owner wants their own
      copy in earbuds.
- [ ] **Check:** a browser test with a stubbed `speechSynthesis` records what each device says and when: the
      right voice per speaker, nothing said twice, and **time from the player letting go of 🎙 to the first spoken
      word**, measured. Target to beat: **≤ 3 s** with the local model.

## T3 — Theater mode in the room
- [ ] A room setting (chosen at creation, switchable by the DM): no map; one large 🎙 hold-to-talk button; the
      scene card as plain text for anyone looking; the log.
- [ ] Spoken questions answered **by code from the scene card**, not by the model: "where am I?", "what's near
      me?", "whose turn is it?", "how hurt am I?". Consistent every time, and instant.
- [ ] Voice dice: "roll stealth", "roll a d20 with advantage", "I rolled fourteen" (a real die).
- [ ] Eyes-free: works with screen reader on; large targets; nothing needs looking at.
- [ ] **Screen-off risk, tested first:** phones pause web pages when the screen locks, which would stop the voice on
      a walk. Measure on an iPhone and an Android phone with the screen locked; mitigations to try in order: a
      wake lock with the screen dimmed, a media session that keeps audio alive, a home-screen app.
- [ ] **Check:** a browser test plays a whole scene using only spoken input (fake microphone) and spoken output
      (stubbed synthesis), never clicking the map; the answers to the four questions match the scene card.

## T4 — The local DM, measured
- [ ] Ten fixed scenes (a tavern talk, an ambush, a chase, a puzzle door, a death-saving throw…), each with a
      scene card and a player's line. Score each DM reply: **vivid** (does it paint a picture?), **consistent**
      (does it contradict the scene card?), **rules** (does it roll or change a player's numbers? it must not),
      **time to first sentence**.
- [ ] Models: `gemma4:e2b` (installed). Then one larger local model, **only after Michael OKs the download size**.
      Michael scores the replies blind (which model wrote which is hidden).
- [ ] **Check:** the score table in `docs/results/`; pick the model; if none is good enough, say so, and the
      Fusion cloud DM becomes a decision with its cost per session written next to it.

## T5 — Sound instead of pictures
- [ ] CC0 ambience per location (tavern chatter, wind on the road, water in the cave), quiet, ducked under speech.
- [ ] Optional, headphones: positional cues from the zones (the wolf growls on your left).
- [ ] **Check:** every sound's licence listed and credited; ambience never covers speech (measured level drop
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
