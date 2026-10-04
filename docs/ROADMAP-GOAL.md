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

## Milestone 5 — Open mic v2 (tev1 as the judge)
- [ ] Wake word (a seat's name or "Hey Divinci") always gets a reply.
- [ ] Per utterance, tev1 judges: is it about the game; is it addressed to an AI seat; should an AI speak unprompted (four levels).
- [ ] Code holds the policy: a threshold, cooldown and budget per personality, no talking over people, never twice in a row. Conservative by default.
- [ ] 👍/🙄 rating on every unprompted line, logged with its scores (never audio).
- [ ] Privacy: a mic-on indicator, mute per device, non-game talk is never stored.
- [ ] **Check:** a 30-minute session with ratings; thresholds are set from those ratings.

## Milestone 6 — D&D with an AI Dungeon Master
- [ ] The DM is a Divinci release whose knowledge base holds SRD 5.1 (CC-BY 4.0) and the campaign notes.
- [ ] Character sheets, fair dice, initiative, and minis and map on the table camera.
- [ ] AI party members with personalities, built on Milestone 5's open mic.
- [ ] **Check:** a one-shot (about 2 hours) with two people and one AI companion; the post-session survey is filled.

## Milestone 7 — Arena and research
- [ ] Results ledger per game: seats, models and personalities, decks or roles, finishing order, integrity flags.
- [ ] Public leaderboard page (named models), with the confounds stated.
- [ ] Post-game surveys run automatically for AI seats, plus an optional form for people.
- [ ] Waitlist: export to Attio; invitations go out in batches (a human sends them).

---

## Done log
*(append: date · milestone · what was checked · the evidence)*
- 2026-10-03 · M1 · Server: `/api/room/snapshot` → `/api/room/restore` across a process restart restored life 33 (fresh start showed 40); no token → 404. Live (zp4cqzp2): after admin sleep, Bo 34, high roll (Ann, entropy e8390e9728…) and Ann's seat key all came back. The first live attempt had LOST state, which shows sleep really replaces the container. It also found two bugs, both fixed: a save could run against an old image during rollout, and comparing the event counter skipped real changes (seat claims, high rolls don't move it), so the save now hashes the bytes. Public `/api/room/snapshot` → 404. R2 `divinci-table-rooms` with a 30-day expiry rule.
- 2026-10-03 · M1 · Redeploy: during the scripted game a rollout swapped the container mid-run ("Container suddenly disconnected" at 18:41:22) and play continued from the saved state (Ann 32 → 31). Found and fixed on the way: (1) onStart can fire for a running container, and restoring then rewound live play (life read 35, 34, 34, 34, 35); the DO now asks /api/room/status and only fills a fresh process. (2) A change during a save is followed by another save. (3) The save window is now 5 s (was 20 s): a swap inside the window lost Bo's seat claim. (4) The admin release path booted the container without ROOM_TOKEN (a default "Player 1/2" game); every start now goes through one helper. (5) A request in flight during a swap is retried once; an unrecoverable room answers 503, not error 1101.
- 2026-10-03 · M2 · Avatars: 12 v2 models in R2 `divinci-table-assets`, `/avatars/index.json` 200, Fusion.v2.glb 16.9 MB served; the cloud stage showed "Bo" with a pooled avatar (Chrome). My board: Ann 200; Bo with Ann's key 403; Bo with his own key 200; board3d listed both boards after two sleep/wake cycles. Photos: no seat key → 403, with key → 200. Lobby: "2 seats open: Ann, Bo" → "1 seat open: Bo" after Ann claimed. /table → 301 /table/ on staging and production. Scripted play: 5 turns, 90 presses. A board read OOM-killed the 1 GiB container (Scryfall bulk JSON parsed in memory); the image now bakes the 12 MB oracle.json and the read returns 200.
- 2026-10-03 · M4 · Local: Scholar's Mate to checkmate (1-0) with PGN saved; refused: move before Start, out of turn, with another seat's key (403), illegal "e5". AI (built-in engine) answered e4 e6 Nf3 Qf6 Bc4 Bc5; save/restore kept moves, clocks and Ann's key, and a second restore was refused (409). Docker: Stockfish at /usr/games/stockfish answered d4 with e6 (no fallback). Cloud room rh6gij3y created from the lobby with game=chess: e4 e5 Bc4 Nc6 Qh5 Nf6 Qxf7# → "1-0 checkmate", PGN `1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0`; the board page rendered the mate (Chrome). Magic quick suite: engine 61/61, game-sim 0 problems; phases ❌ and sensory 116/118 — the pre-today baseline (2ee2c0b) fails phases with more failures and sensory 115/118, so nothing new.
