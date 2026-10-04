# Divinci Table — roadmap as a goal

*Run with `/goal docs/ROADMAP-GOAL.md` (from the divinci-table repo). Written 2026-10-03, after game 3, the
first public deploy and divinci.ai/table going live. The vision and reasoning live in
[platform-plan.md](platform-plan.md); this file is the ordered work list with a definition of done for each
milestone. Tick a box only when its check has been run and its output read.*

## Where we start (2026-10-03)

- **Laptop table** (`table/server.py`): three games of Commander with people and AIs; seat claiming,
  ordered passing, sidekick checklist, fair randomness, research logs and surveys.
- **Cloud** (`cloud/`): public lobby at https://divinci-table.divinci-ai.workers.dev, one Cloudflare
  Container per room, branded. People-only tables (AI seats wait for a budget).
- **Marketing**: https://divinci.ai/table/ (3D on desktops, 2D on phones) with a waitlist in KV; Buffer
  drafts for LinkedIn and X waiting for approval.

## Rules for every milestone

1. **Measure, don't assume.** Every box below names its check. Run it, read the output, and record what you saw.
2. **Live play comes first.** No restarts and no heavy tests while a game is being played (port exhaustion,
   game 3). The laptop table and the cloud tables must keep working after every change.
3. **Deploys.** The cloud Worker and `staging.divinci.ai` may be deployed as each milestone finishes.
   **Production divinci.ai** deploys build on the last announced production commit (deploy bulletin), go
   through `deploy-gate announce`, and need Michael's OK.
4. **Nothing public without a human:** no social posts, emails or public announcements. Buffer posts stay drafts.
5. **Spend:** AI seats in public rooms stay off until Michael sets a budget. Image or video generation over
   ~$5 per milestone needs a yes.
6. **Security pass** on anything new that is reachable from the internet: authentication, rate limits,
   what a stranger can read, write or store, and whether a secret can leak.
7. **Research integrity:** hidden information stays hidden. Anything an AI sees that it shouldn't goes in
   `integrity-notes.jsonl`. Humans own their life totals.
8. Commits are local unless a milestone says to push; `divinci.ai` changes go to `origin/table-landing`.

---

## Milestone 1 — Rooms survive sleep *(done)*
A room's game must outlive its container: idle sleep, a redeploy, or a crash.
- [x] The table server exports and restores its full state over two token-protected endpoints (the token is per room, set by the Durable Object, and never reaches a browser).
- [x] The room's Durable Object saves the snapshot to R2 after play changes (at most once every ~20s), and again before the container sleeps.
- [x] On wake, the Durable Object restores the snapshot before any player request reaches the container.
- [x] The Worker refuses `/api/room/*` from the internet.
- [x] An admin endpoint can put a room to sleep (`/lobby/admin/sleep?id=`), for tests and operations.
- [x] **Check (sleep):** in a live room, claim a seat, change life, run the high roll and pass a step. Put the room to sleep. Reopen it: life, phase, seat key and high roll all match.
- [x] **Check (redeploy):** the same across a deploy that ships a new container image.
- [x] R2 lifecycle rule: room snapshots expire after 30 days.

## Milestone 2 — The cloud table is playable end to end *(done except the two-person check)*
- [x] Upload the 3D avatars to R2 or Workers assets, so the cloud stage shows avatars like the laptop does.
- [x] Each seat's board can be recorded from the phone without the laptop referee (today that is a host-only API).
- [x] Phone photos: size and type limits; the public lobby states clearly that photos are visible to the room.
- [x] `divinci.ai/table` (no slash) redirects to `/table/`.
- [x] Room lobby: show which seats are open. A closed room can't be joined by new people.
- [x] **Check (scripted):** two simulated devices (separate cookies and user agents) play five full turns through the pass rounds in a live cloud room.
- [ ] **Check (people):** two people on two networks play five full turns using only phones and the stage. *Needs Michael and Sam.*
- [x] Added along the way: an admin seat release (`/lobby/admin/release`) for a player whose device is lost; the "claimed on another device" message no longer points at a host laptop in cloud rooms.

## Milestone 3 — AI seats in public rooms (needs Michael's budget)
- [ ] A spending cap per room and per day, metered from Divinci usage; when the cap is reached the seat becomes a "sleeping" AI that passes.
- [ ] Fusion brains run inside the room container with `DIVINCI_FUSION_API_KEY` and `FUSION_CONFIG` as Worker secrets.
- [ ] A kill switch (`/lobby/admin/ai-off`).
- [ ] **Check:** a person plays a room with one Fusion seat for three turns; the spend is visible and below the cap.

## Milestone 4 — One core, many games: chess *(chess done; Magic not yet moved onto the core)*
- [ ] Extract the game-independent parts (rooms, seats, acknowledgement rounds, talk, photos, fair randomness, event log, research log, surveys) from the Magic server into a core module, so Magic becomes one game module.
  - [x] `table/core.py` exists (seats and keys, event log, room persistence, talk, photos) and chess is built on it, serving the same HTTP contract, so the cloud room, seat.js and photo.js work unchanged.
  - [ ] Port the Magic server onto it (it still has its own copies), plus acknowledgement rounds, fair randomness and surveys. Do this between games: the Magic server is the one people play on.
- [x] Chess module: python-chess for rules, a physical board on camera with moves confirmed on the phone, clocks, and an AI seat (a Stockfish level or an LLM) with a personality.
- [x] The lobby offers the game type.
- [x] **Check:** no Magic regressions (quick suite at HEAD vs a pre-today baseline: same failures, none new), and a full chess game plays to checkmate in a cloud room.
- [ ] Follow-up: update the older `phases` and sensory tests for game 3's ordered passing (they fail the same way at the baseline).

## Milestone 5 — Open mic v2 (tev1 as the judge) *(built; the 30-minute rated session needs people)*
- [x] Wake word (a seat's name or "Hey Divinci") always gets a reply.
- [x] Per utterance, tev1 judges: is it about the game; is it addressed to an AI seat; should an AI speak unprompted (four levels).
- [x] Code holds the policy: a threshold, cooldown and budget per personality, no talking over people, never twice in a row. Conservative by default.
- [x] 👍/🙄 rating on every unprompted line, logged with its scores (never audio).
- [x] Privacy: a mic-on indicator, mute per device, non-game talk is never stored.
- [ ] **Check:** a 30-minute session with ratings; thresholds are set from those ratings (`openmic.calibrate()` is ready). *Needs a game night with `OPENMIC_V2=1`.*

## Milestone 6 — D&D with an AI Dungeon Master *(built; the 2-hour one-shot needs people)*
- [x] The DM is a Divinci release whose knowledge base holds SRD 5.1 (CC-BY 4.0). *Campaign notes: add per adventure.*
- [x] Character sheets (four level-3 SRD pregens), fair dice or a checked physical die, initiative. Minis and map: phone photos into the scene log; no dedicated map view yet.
- [x] AI party members with personalities, built on Milestone 5's open mic (voiced in the DM's reply, no extra model call).
- [x] Cloud: the lobby offers a D&D one-shot. A person DMs in public rooms until the AI DM's budget is set (`DND_CLOUD_AI=1`); every room caps AI DM requests at 150.
- [ ] **Check:** a one-shot (about 2 hours) with two people and one AI companion; the post-session survey is filled. *Needs Michael and Sam.*

## Milestone 7 — Arena and research *(done; the Magic table gets the people's survey with the M4 core port)*
- [x] Results ledger per game: seats, models and personalities, decks or roles, finishing order, integrity flags (`docs/results/ledger.json`, `table/ledger.py check`).
- [x] Public leaderboard page (named models), with the confounds stated: https://divinci-table.divinci-ai.workers.dev/leaderboard
- [x] Post-game surveys run when a result is recorded (`ledger.py finish`), plus an optional form for people (`/survey` on chess and D&D tables; Magic after the core port).
- [x] Waitlist: `scripts/export-waitlist.py` → CSV for Attio import. Invitations go out in batches, sent by a person. *0 sign-ups so far, so nothing to import yet.*

---

## Done log
*(append: date · milestone · what was checked · the evidence)*
- 2026-10-03 · M1 · Server: `/api/room/snapshot` → `/api/room/restore` across a process restart restored life 33 (fresh start showed 40); no token → 404. Live (zp4cqzp2): after admin sleep, Bo 34, high roll (Ann, entropy e8390e9728…) and Ann's seat key all came back. The first live attempt had LOST state, which shows sleep really replaces the container. It also found two bugs, both fixed: a save could run against an old image during rollout, and comparing the event counter skipped real changes (seat claims, high rolls don't move it), so the save now hashes the bytes. Public `/api/room/snapshot` → 404. R2 `divinci-table-rooms` with a 30-day expiry rule.
- 2026-10-03 · M1 · Redeploy: during the scripted game a rollout swapped the container mid-run ("Container suddenly disconnected" at 18:41:22) and play continued from the saved state (Ann 32 → 31). Found and fixed on the way: (1) onStart can fire for a running container, and restoring then rewound live play (life read 35, 34, 34, 34, 35); the DO now asks /api/room/status and only fills a fresh process. (2) A change during a save is followed by another save. (3) The save window is now 5 s (was 20 s): a swap inside the window lost Bo's seat claim. (4) The admin release path booted the container without ROOM_TOKEN (a default "Player 1/2" game); every start now goes through one helper. (5) A request in flight during a swap is retried once; an unrecoverable room answers 503, not error 1101.
- 2026-10-03 · M2 · Avatars: 12 v2 models in R2 `divinci-table-assets`, `/avatars/index.json` 200, Fusion.v2.glb 16.9 MB served; the cloud stage showed "Bo" with a pooled avatar (Chrome). My board: Ann 200; Bo with Ann's key 403; Bo with his own key 200; board3d listed both boards after two sleep/wake cycles. Photos: no seat key → 403, with key → 200. Lobby: "2 seats open: Ann, Bo" → "1 seat open: Bo" after Ann claimed. /table → 301 /table/ on staging and production. Scripted play: 5 turns, 90 presses. A board read OOM-killed the 1 GiB container (Scryfall bulk JSON parsed in memory); the image now bakes the 12 MB oracle.json and the read returns 200.
- 2026-10-03 · M4 · Local: Scholar's Mate to checkmate (1-0) with PGN saved; refused: move before Start, out of turn, with another seat's key (403), illegal "e5". AI (built-in engine) answered e4 e6 Nf3 Qf6 Bc4 Bc5; save/restore kept moves, clocks and Ann's key, and a second restore was refused (409). Docker: Stockfish at /usr/games/stockfish answered d4 with e6 (no fallback). Cloud room rh6gij3y created from the lobby with game=chess: e4 e5 Bc4 Nc6 Qh5 Nf6 Qxf7# → "1-0 checkmate", PGN `1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0`; the board page rendered the mate (Chrome). Magic quick suite: engine 61/61, game-sim 0 problems; phases ❌ and sensory 116/118 — the pre-today baseline (2ee2c0b) fails phases with more failures and sensory 115/118, so nothing new.
- 2026-10-03 · M5 · `table/tests/openmic_test.py` 19/19 (wake words, addressed vs not, quiet/normal/chatty thresholds, cooldown, a 6-line budget, never twice in a row, judge outage → silence, side talk logged without words, calibrate matches a 👍 to its score). tev1 on 12 labelled lines (`openmic_tev1_eval.py`): about-game 10/12, addressee 8/12 (misses are "whole table" vs "nobody", which decide the same), expects-answer 9/12, 1.8 s/line; unprompted scores stayed ≤2.2 on these lines, so a normal seat would not have interrupted. Live server with OPENMIC_V2=1: "Talrand, are you going to attack me?" → wake reply; a wedding remark → "(side conversation)" in the public log and no words in decisions.jsonl; a rules question → no interruption. The mic page already had a listening indicator and Mute.
- 2026-10-03 · M6 · SRD 5.1 (CC-BY-4.0 PDF, 403 pages) → text, whitespace cleaned (1.38M chars), six attributed parts in a new Qdrant vector (gemini-embedding-001@1536): 1,456 chunks; test-retrieval top scores 0.66–0.72 for grappled / fireball / owlbear / opportunity attack. DM release forked from a Fusion release with the vector attached and its own system prompt. **Found on the way:** a fork shares its parent's prompt document, and `divinci release update --thread-prefix` edits that shared document in place: it overwrote Fusion Tuvasa's prompt with the DM's. Restored from the document's version history within minutes and verified ("You are Fusion…"); the DM now has its own document and no other release in the workspace shares one. `table/tests/dnd_test.py` 53/53 (dice ranges and fairness over 20k d20s, advantage, physical dice checked, seat ownership of HP/conditions, TABLE line can't touch a player's numbers, malformed TABLE line never shown, initiative order, companion cooldown, DM cap, keys never in the prompt, snapshot round trip). Live local AI DM (3 requests): opened a lighthouse smugglers' cave, asked for rolls instead of rolling, voiced Leonardo when Sam named him, and set two Lizardfolk at AC 15 / HP 22 (the SRD values); its first replies had a mermaid diagram, `[7]` citations and markdown bold, now stripped and forbidden in the prompt. Cloud room zrs2zmsb (lobby, game=dnd, Jordan DM): seat theft 409; initiative Ann 15, Bo 13 (physical die), goblins 9 and 6; Bo changing Ann's HP 403; Ann narrating 403; Ann 23/28 HP, scene and order all came back after an admin sleep; public /api/room/snapshot 404.
- 2026-10-03 · M7 · Ledger entered from the research folders by a read-only pass with file citations: g1 Claude won (3 players); g2 Sam won, Fusion 2nd, Claude 4th; g3 human game unfinished (paused at Michael's turn 8); g3-fork won by Sam's seat while Claude piloted it (marked assisted, so it doesn't count). Board: Claude (claude-opus-5-5) finished 2, won 1, mean place 2.5; Fusion (gemini-3-5-flash) finished 1, mean place 2.0. The model behind the Claude seats was read from the session transcripts, not assumed. `ledger_test.py` 10/10; `dnd_test.py` 59/59 including the survey (no key 403, rating 9 → 400, unknown questions dropped, public log records who answered and nothing else). Chess survey: 403 without key, 200 with. Live: /leaderboard 200, leaderboard.json matches the ledger, lobby link present. Waitlist: production and staging KV both empty (an invalid email returns 400 on production, so the endpoint is live); the export writes 0600 CSVs under table/.cache.
