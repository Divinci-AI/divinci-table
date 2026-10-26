# Two agents playing a card game: a dogfood loop on the real table, streamed (plan v2, 2026-10-24, nothing built)

*v2 applies the Opus 5.5 and Hermes reviews of v1 (same day) and Michael's answers. Review changes are marked **[R]**. Run it later with
`/goal docs/DOGFOOD-LOOP-AND-STREAM-GOAL.md` from the divinci-table repo. Tick a box only when its check has been run and its output read.
**[unverified]** marks a claim I did not check myself (reviewer claims I did spot-check are marked **[checked]**).*

## The idea

Two agentic language-model players play full Commander games against each other on the REAL table server (`table/server.py`, rules in
`table/player.py`), through the same `tablectl` API a remote player uses, one game after another with different decks, cards and
mechanics. The point is to find where the table refuses a legal move, accepts an illegal one, silently does nothing, drifts from the
truth, stalls, or cannot run a card's text. Each distinct problem becomes a failing test first, then a fix. Finished games are then
**streamed to YouTube Live and Twitch at the same time** under the working title **"Divinci Table: AI's Playing Cards"**, on the existing Divinci AI YouTube channel.

Decided by Michael: 12 unattended hours is the ceiling; a relay or OBS may be installed if the ffmpeg test needs one; real card names on
screen; hands shown in all-AI games; the title above. Pilots run on the Orin (Michael 2026-10-25: the Mac lacks the free memory); which model(s) is still open (D1).

## What exists (checked 2026-10-24) and what the reviews corrected

| Piece | State |
|---|---|
| `harness/arena.py` | 61 games on `harness/engine.py`, a separate simplified engine; **never starts `table/server.py`**, so it cannot find table bugs |
| `table/tablectl.py`, `brain_e2e.py`, `harness/dogfood_game.py` | Real-API path exists. Scripted single actions exist (`brain_e2e.py`); **no full-game scripted pilot**. `dogfood_game.py` already starts a local server, claims two seats and writes per-seat `./tc` wrappers for model players |
| `tablectl state` | Lists `castable_now` per hand card **[R, checked by Hermes]**; affordability shares code with cast, so it cannot catch a bug in that code |
| Public board | `/api/stage`, `/api/board3d` are public-only; `/api/ai/hand` is token-gated; `boardview.py` is hand-edited JSON, not a source **[R]** |
| Offline | **Not offline as run today** **[R, checked]**: the opening roll hint is `highroll quantum`, which fetches ANU and drand (`fair.py`, sleeps up to 61 s); dogfood run 1 did this. A fresh worktree may also re-download card data |
| Events | `/api/events` returns at most 300 per call **[R, checked]**, so a long game's saved log is truncated unless paged by `since` |
| Replay | The player takes a seed, but the server passes none, and seeds mix OS entropy **[R, checked in part]**; whole-game replay does not exist yet |
| Engine seats | Opus reports a virtual (engine) seat's casts only speak, targets are narration, removal is routed from table talk, off-turn sorceries are accepted, no stack, no commander damage **[unverified, from the review]**. So the pilots must be external-brain seats and the oracle must check for effects, not only refusals |
| Machine | 24 GB, swap about 86% used, **16 GiB free and swap shares that disk**, other agents share the Mac, qwen3:14b is resident in Ollama. ffmpeg 9 with `h264_videotoolbox` is installed; Chromium is cached; OBS and a relay are not installed |

## Rules for every milestone

1. **Measure, don't assume.** Every box names its check; read samples, not just counts.
2. **Offline on the game path.** Local models and device voices only. The only exception is encoded video leaving for the platforms, after a yes (S4 onward). **[R]** Prove it: run with a dead `HTTPS_PROXY` tripwire and fail the game on any outbound attempt; use a local-entropy roll, never `quantum`.
3. **No public post, spend, install, or cloud call without a yes in chat.** Room `32a2sum5` is not used or resumed; no deploys while a room is live.
4. **Hands may be shown to spectators (decided by Michael 2026-10-25), but only in all-AI games and only through a spectator-only view that no seat can reach.** A seat's reasoning stays sealed until game over. The moment a human sits in, hands go back to hidden live. Test that no seat's token or route can read the spectator view.
5. **Pin identity:** each record names pilot kind, exact model string, prompt version, deck, seed and harness commit. **[R]** The hard-coded seat names "Opus"/"Sonnet" in the dogfood tooling must not label other models.
6. **Credentials never touch argv, env, chat, screenshots, logs or the repo.** Michael creates and launches anything that holds a key.
7. **Resource guards:** one game at a time, `caffeinate`, STOP file, per-game timeout, 3-failures stop, and **[R]** a disk floor of at least 12 GiB, a stop on `memory_pressure` plus swap-out rate (not swap percent alone), and an encoder watchdog.
8. **Public repo:** no keys, no other people's room ids, no personal details.

---

# Part L: the loop

## L0. Loop-safety pass (do first) **[R]**
- [x] (done 2026-10-25: `tablectl highroll table` already rolled offline; added it to tablectl's choices) Local-entropy opening roll for loop games; a dead-proxy tripwire; the card data cached locally.
- [~] (`dogfood_game.py stop` now pages; other readers not audited) Page `/api/events` by `since` so a full game is kept; attribute a refusal made before a seat acts to the right seat (Opus: it is logged as the first seat).
- [x] (2026-10-25 night: `harness/replay_diff.py` plays one seed twice and diffs the public event logs with timestamps/ids/clocks removed: 171 events each, identical, every seat's own sequence identical; different seeds diverge at the roll. Short (6 turns) with a deterministic pilot; a long random game is not yet replayed.) [earlier: flag done 2026-10-25: `server.py --seed N`, `dogfood_game.py start --seed N`; two servers on seed 42 gave the same roll entropy and winner; the full scripted-game replay diff is NOT done) A server flag to pass a seed so a game can be replayed; **Check:** play one scripted game, replay from the seed, diff the event logs.
- [x] (2026-10-25) `scripts/table_loop.sh` -> `harness/table_loop.py` (+ `caffeinate -i`). Guards: STOP file (stops the running game within seconds), hours, games, disk floor 12 GiB, free-memory %, swap-out RATE (two breaches in a row: one noisy sample on a shared laptop is not a reason to stop), per-game hard timeout, 3 failed games in a row, a game that leaves a port behind (killed, counted failed). Backends: `fake` (random-legal pilot, no Orin, a table fuzzer) and `orin` (needs `--orin-clear` and an already-open tunnel; never opens one; /health before every game; stops at the first pilot or health failure, no retry). Clean games' run folders (private hands) are deleted; games with a finding or failure are kept (0700). `table/tests/table_loop_test.py`, 37 checks, includes a mutation (cap raised -> no stop). The original check text follows. ~~`scripts/table_loop.sh`, modelled on `arena_night.sh`, with the guards in rule 7.~~ **Check:** the STOP file stops cleanly with no stray process or port; raising the disk floor above free space refuses to start (do not fill a volume).

## L1. The oracle comes before the pilot **[R]**
_Update 2026-10-25 (late), **battlefield checks built**: from the public snapshots, (a) a cast permanent or played land must appear on that seat's battlefield, or visibly die/bounce, within 4 s (no sample on both sides = no claim); (b) hand + library + graveyard + non-token non-commander permanents may not EXCEED the seat's starting total for two samples in a row. Tests plant each bug, each detector fails when switched off; coverage is reported per game (`board_coverage`: plays seen vs checked: 14 of 14 on the real 14-turn game, 0 findings). NOT checked: exile, counters/tokens created by spells, destroy/bounce effects, that a spell's text happened (only draw and life gain). **Second opinion on the simple rules, `harness/engine_diff.py`**: 11,900 generated combats through `harness/engine.py` and the table's `resolve_block`: they agree on every plain case including trample; all 4,380 divergences involve DEATHTOUCH (the table is handed only the attacker's power and a trample flag, so it cannot kill a bigger blocker with a deathtouch attacker, nor kill an attacker with a deathtouch blocker). Real finding, NOT fixed (table rules code, shared checkout): to fix it, carry the attacker's keywords into `COMBAT["pending"]` (server.py ~3108, ~3778) and use them in `ai_turn.resolve_block`; `table/tests/engine_diff_test.py` pins the gap and will say when it closes. Other real finding: the table keeps applying combat damage to a player already at 0 life (down to -27) because ending a game is the host's job. A literal same-seed replay against `engine.py` is not meaningful (different card set and shuffle).
_Status 2026-10-25: `harness/oracle.py` built (log-based: reads a game's `events.jsonl` + `brain.jsonl`). Kinds: refused-but-offered, accepted-but-illegal, no-effect (draw / life gain, spells only, looks ahead for a host-style resolve), drift (hand continuity, life), leak, stall, unrunnable-text, plus `pilot-error` (a refusal for a missing choice: shown, not failed on). Signatures are normalised. `table/tests/oracle_test.py`: one planted bug per kind, a clean game, every detector fails its test when switched off, and a guard for each false positive found. Run over all 39 saved real games it reported 15 findings at first; every one was a false positive (second copies of a card, face-down reveals, commanders, host `take`, reset hands, overnight pauses, trigger/cycling text) and each is now a test; the corpus is clean. Zero true positives on real games so far, so its power rests on the planted tests only. NOT done: battlefield/token/counter effects (the logs do not carry the battlefield), the `harness/engine.py` replay diff, and `table/audit.py` still flags a bounce land played face up as a leak._
A pilot's own legality check is half the oracle, so write it first. A finding is any of:
- [ ] **Refused-but-offered:** `castable_now` says yes and the cast is refused.
- [ ] **No effect:** a resolved spell or ability whose oracle text predicts a state change (exile, destroy, draw, token, counter, life) produced none. This catches the silent no-op class, which refusal-counting cannot see.
- [ ] **Accepted-but-illegal:** second land, unpaid cost, wrong timing for the card type, target outside its text.
- [ ] **Drift:** zones vs deck size (allow for exile being capped strings), life vs logged deltas, negative mana, a card in two zones.
- [ ] **Leak** (reuse `audit.py`), **Stall** (a seat owes a pass with no change for N s; no game past the turn cap), **Unrunnable text** (refused whole).
- [ ] A second opinion on the simple rules: replay the same seed through `harness/engine.py` and diff lands, combat and life.
- [ ] **Signatures** are normalised before hashing (strip ids, source lists, names of other seats) **[R]**, so one bug is one signature, not thousands.
- [ ] **Check:** one planted bug per kind (including a spell that silently does nothing) is caught with the right event id; a clean game has no findings; each detector fails its test when switched off.

## L2. Two model pilots, over tablectl
_Status 2026-10-25 (late): **3 and 4 seat games run.** `pilot_game.py --decks a,b,c[,d]` (2-6 decks; seats are named for the commander). A 3-seat 12-turn game (212 s) and a 4-seat 16-turn game (290 s) ran clean on the real server with a random-legal pilot: no pilot failure, no oracle finding, 0 path-health failures. They are short (4 rounds); politics, commander damage and long games are untested. Eliminations: `pilot_game.py` declares game-over as host when at most one seat is alive. Board snapshots (public `/api/board3d`, once a second) are saved as `state.jsonl` for the oracle._
_Status 2026-10-25 (night): first 20-turn real game on the Orin (gpt-oss-20b both seats, `--seed 11`), third attempt: 689 events, 41 decisions = 33 model calls + 8 forced moves (one legal option, model skipped), 0 errors, every response's `model` verified. Latency p50 2.4 s, max 8.2 s, first call 0.95 s; it tracks completion length (avg 80 tokens at about 34 tok/s), not prompt length (708-1501). Attempts 1 and 2 stalled on the first call (18-22 s, reset, ssh "Operation timed out"); a curl with the exact body, the same request through the Python client, and an offline game with a 1 Hz path poll were all healthy, so the cause is NOT established. Hypothesis (one success): an idle tunnel stalls on the first packet; the runner now polls the router `/health` every 2 s during every game (Orin-approved) and records failures. Per-decision log: `harness/results/2026-10-25-orin-long3/`. Oracle on that game: 1 table finding (`after-elimination`: combat damage kept landing on a player already at 0 life, down to -27; the table leaves ending a game to its host, so `pilot_game.py` now declares game-over as host when at most one seat is alive) and 3 `pilot-error` (modal spells cast without a mode; the pilot now chooses a mode). Rule with the Orin: message it first, wait for "clear", never run anything else there, stop on any reset._
_Status 2026-10-25 (earlier): first slice built differently from the sketch below. `harness/pilot_game.py` runs two AI seats on the real server (`--brain external`, `--seed`), each played by `table/fusion_brain.py` with `FUSION_BACKEND=local` -> `table/local_pilot.py` -> gpt-oss-20b on the Orin over `ssh -L`. A 4-turn `--dry` game and a 6-turn real game both finished with no refusals; the real game made only 2 model calls (1.7 s and 4.3 s), both chose option 1. Not yet: tablectl-driven seats, 3-4 seat games, the L1 oracle._
_Review 2026-10-25 (design, tests, security), before the first long game: offline end-to-end test `table/tests/pilot_e2e_test.py` plays real games through `harness/pilot_game.py` against `harness/fake_router.py` (a loopback stand-in for the Orin): random-legal pilot completes with no table finding and no prompt carrying a card only the other seat holds; a router returning garbage or HTTP 500 flags the game (exit 3, brain stops, nothing plays on with a default); the STOP file ends a game; no server or brain is left running. Hardening: the model call ignores proxy variables and redirects; children get an environment allowlist (no cloud keys) and `TABLE_HIGHROLL=table`; the run dir and every file are mode 700/600; SIGTERM cleans up; exit codes 0 clean / 2 table finding / 3 pilot failed / 4 no oracle. Known limits: an opponent's words never reach a model prompt only because `on_talk` has one option (the model is skipped); a second option there would open an injection path. A random-legal pilot via the fake router is a free fuzzer for the table and needs no Orin._
- [ ] A pilot runner (extend `dogfood_game.py`) that runs two model seats, each using `./tc`, with no pass-timeout stalls (use the repo's walk-the-state pattern; a blocking `act()` waits the full pass timeout every window) **[R]**.
- [ ] Start with **2-seat games**, then 3, then 4 **[R]**; pilot seats cannot mulligan, make tokens or add counters today, which limits what a 2-seat game can reach.
- [ ] The model pilot logs its own legality reasoning; a refusal the pilot should have foreseen is pilot-error, a refusal after `castable_now` is table-bug; a person reads one example per new signature.
- [ ] **Check:** 10 two-seat games reach `game-over` with no hang; findings are labelled; the models' fallback or garbage-answer rate is reported.

## L3. Decks, mechanics, and when to stop
- [ ] `harness/coverage.py`: classify every card ability as modelled, `effect`-only, or unsupported; rank by decks containing it.
- [ ] A probe deck. **[R]** Put the rare cards in hand with the host's `search`/`put` rather than hoping to draw them, and do not use the permissive `effect` channel to "succeed".
- [ ] **Stopping rule [R]:** a deck set is "clean" only when every card has resolved at least 3 times, the estimated unseen-failure mass (singleton findings / games, Good-Turing) is under 1%, and it holds across 3 batches on one commit. 300 games is about 15-20 h in-process and far more through `tablectl` (a 2 s retry wait), so budget for it.
- [ ] **Check:** coverage matches a hand audit of 20 cards; every probe mechanic is exercised, counted from events.

## L4. Run and read
- [ ] 50 games unattended inside the 12-hour ceiling; the report lists new signatures, refusal and completion rates, stalls, and per-deck/seat results with `ratings.py` intervals.
- [ ] **The report never prints `server.log`** (it records private peeks) **[R]**.

---

# Part S: the stream

## Decision: replay first, live later **[R, Opus]**
Stream **rendered replays of finished games** first. Because the game is over, hands and reasoning can be shown, any delay is fine, there is
no memory contention with the model, and a bug cannot reveal hidden information live. A playlist of replays still reads as a live channel.
Live play of a running game comes only after the replay stream has run cleanly for a week.

## What viewers see
`/broadcast`, 1280x720: boards, life, phase, table talk, a "now playing" card (decks, models and versions; **no seeds**, since arena ids
embed them) **[R]**, a banner "AI-controlled players, simulated games" repeated in the channel description. **Cut for now [R]:** the
findings ticker (it names hand cards and makes unverified "fixed in Y" claims), the scoreboard, voices/TTS, music, and chat.

_Status 2026-10-25 (late), stream work built and tested OFFLINE; nothing was sent to any platform and no key was touched. **S1** `harness/broadcast/`: `build_replay.py` (finished game -> `replay.json`; `--mode public` or `spectator`; captions cleaned; no seeds/entropy/refusals/journals), `replay.html` (1280x720, text only via textContent), `serve_static.py` (its own process, exact allowlist of two files, GET only: a broadcast page has NO route to the table), `render.py` (deterministic headless Playwright -> ffmpeg MP4; samples memory/swap/disk). Measured on this Mac, 252 s of video: 505 frames in 27.6 s, free memory never under 57%, 0 swap-outs, 31 MB. (A 10-s-interval run once showed 117k swap-outs/min: that was other activity on the shared Mac, not the render.) `table/tests/broadcast_leak_test.py` plants canaries in the private logs and checks replay.json, the static server (API routes, traversal, POST all refused) and the rendered frames with Apple Vision OCR, with positive controls (a visible name IS read; a canary planted on screen IS found). The model is on the Orin, so the Mac never holds it: "model resident vs unloaded" is moot. **S2**: `ffmpeg -/rtmp_playpath FILE` does NOT exist on this build (tested), so option B is out. `stream_supervisor.py` pushes from one container per platform with the key bind-mounted read-only (`-rtmp_playpath "$(cat /run/key)"` inside the container), so no host argv/env holds it; `docker inspect` does not either. Residual: `docker top`/`docker exec` by the same user show the container argv. **S3**: watchdog (restart with backoff), frozen-sink detection (bytes sent, from `docker stats`: ffmpeg's `frame=` progress never reaches `docker logs`, and the FIRST version of this check used it, so it flagged by 'no counter yet'; the test passed for the wrong reason until a healthy-sinks-are-not-flagged assertion exposed it), give-up + ALERT.txt + local notification, STOP file -> "back soon" card -> clean end, caption filter in the builder. `table/tests/stream_supervisor_test.py` (22 checks, with a positive control for the key-in-argv filter, fake platforms + fake keys on loopback, ~3 min): both sinks receive on the right key, no key in any host process list/inspect/log, kill one -> the other keeps streaming and the dead one returns, a paused container detected as frozen and restarted while healthy ones over 25 s are not, STOP shows the card and ends with no container left, an unreachable sink is given up on (exit 2). NOT done: S4 and S5 (gated: need Michael's yes, his stream keys and his launch), a playlist of several replays, replay audio, "now playing" for several games, YouTube/Twitch policy reading (Wizards fan-content, AI-content disclosure), the real platform ingest behaviour (untested by design). The render sets a keyframe every 2 s (`-g 60`, no B-frames; ffprobe: gaps of exactly 2.00 s) because the pushers copy the stream as it is. Real YouTube/Twitch ingest has never been contacted, so its acceptance of this stream is unverified._
## S1. Render and measure (early, right after L1) **[R]**
- [ ] `/broadcast` served through a **public-only proxy** that cannot reach `/api/ai/hand`, the brain token or the control POSTs. A local renderer counts as "this laptop" to the server, so it must not call the server directly **[R]**.
- [ ] A 10-minute headless render to MP4; sample `memory_pressure`, swap-out rate and disk every 10 s. **Check:** peak recorded with the model both resident and unloaded; that number decides whether live play is ever allowed.
- [ ] **Check (leak):** plant a hidden card name in the private log and in a refusal message; OCR frames (`table/ocr_mac.py`) with a **positive control** (a visible name is found) and confirm the hidden name never appears. The detector must also stop the pusher, not only blank the page.

## S2. Keys without argv, and two outputs
`ffmpeg` shows output URLs in its command line, banner and errors, and any process of this user can read argv and env **[R, checked in part: the CLAUDE.md notes say several agents share this Mac]**. Options to test, in order:
- [ ] **A.** One encode to a local relay (MediaMTX, install approved) that holds the keys in its own 0600 config and pushes to both platforms; `ffmpeg` pushes keyless to 127.0.0.1 (Hermes).
- [ ] **B.** One encode to local MPEG-TS, then one `-c copy` pusher per platform reading the key from a file with `-/rtmp_playpath FILE` (Opus) **[unverified]**.
- [ ] **Check:** against a local listener, with fake keys: `ps eww`, the relay config, and every log are grepped for the key fingerprint and show none; stderr is redacted; killing one output leaves the other running (`use_fifo`/`onfail` behaviour is unknown on this build; this is the test); Michael launches the real thing; keys are rotated after each session.

## S3. Safety layer
- [ ] Encoder watchdog (restart on exit, alert if it cannot), kill switch (STOP -> "back soon" card -> clean end), caption filter, and a frozen-board detector, since a stopped loop would otherwise keep streaming a still image **[R]**.
- [ ] Chat **off**. Any later chat is untrusted data behind a strict allowlist and never reaches a pilot, a prompt, or `tablectl`.
- [ ] **Check:** each safeguard has a test that trips it deliberately.

## S4. Private test (GATED)
- [ ] YouTube: an **Unlisted or Private** event with archive handling set deliberately (archives are on by default and a buggy run would stay up) **[R]**. Michael creates it, sets AI/synthetic disclosure, and reports the dashboard's health numbers.
- [ ] **Twitch has no unlisted mode [R, Opus, unverified]**, so any Twitch test is public. Do YouTube first; Twitch only after YouTube is clean, starting with a 15-minute supervised stream with disclosure on.
- [ ] YouTube live may not be enabled on the Divinci channel yet; Michael checks (Studio -> Create -> Go live) and starts verification now if needed.

## S5. Public, in steps (GATED, a separate yes each)
15 minutes supervised -> 1 hour -> 4 hours, each reviewed; only then unattended up to the 12-hour ceiling. First public week: no step longer than one supervised hour.

## Platform facts NOT checked (verify before relying)
Live eligibility and any first-enable wait; stream-length and archive limits; each platform's AI/synthetic-content disclosure; non-partner
Twitch bitrate and VOD expiry; whether a repeated or replayed feed counts as "pre-recorded" and needs labelling; the Wizards fan content
policy for card names, text and art on screen (the title avoids the trademark but real card names still appear); the macOS voice licence
(reportedly personal use); Content ID claims on any "CC0" audio; the home uplink for two pushes.

---

## Questions for the next review (v2)
1. Is "replay first, live later" the right ordering, or does it make the stream dishonest ("live" when it is not)? How should it be labelled?
2. Is the self-consistency plus effect-check plus engine-diff oracle now enough, and what is the cheapest way to hand-write golden tests for the probe cards?
3. Which key-handling option (A relay, B `-/rtmp_playpath`) survives a real test, and what else leaks a key (crash reports, shell history, `launchctl`)?
4. What does a 2-seat game NOT reach that a 4-seat game would (politics, multiple opponents, commander damage), and is it worth the wait?
5. What can still go wrong in an unattended 12 hours on a shared laptop that this plan does not guard?

## Decisions for Michael
- **D1. HOST DECIDED, MODEL OPEN.** Michael decided on 2026-10-25 that the pilots run on the Orin, because the Mac does not have the free gigabytes to run these models smoothly. Still to do before the first model game: ask the `orin` session when the Jetson is free of Quake round 3 and TrustBench, confirm quants, and measure structured-JSON play. Orin's reply (2026-10-25): idle now; Michael's go is needed first, then message `orin` before each run and it will say whether the box is clear and warn before it starts Quake or TrustBench. Quants are not in its notes; read `/etc/llama/models.ini` read-only, never edit it or restart the router. CORRECTED by `orin` the same day: use `ssh -L` (127.0.0.1:<port> to the router), NOT the nginx/Cloudflare bearer route, because the table path is fully offline (Michael, 2026-09-24) and the bearer route goes over the internet; the bearer route would need a fresh yes from Michael. Re-pin the Jetson host key first (expected SHA256:e39/0+PVUo0NGrAAK7GZv1yE+SG1Jdn6jUNdZbfNmNQ). No sudo on the Jetson; never edit or restart the router; load no model until `orin` confirms the box is clear. `orin` is sending its own brain design (numbered legal options only, constrained JSON, one resident model); build against that once settled. Which model(s) play? The Mac has `gemma4:e2b` and `qwen3:14b`. The `orin` session answered on 2026-10-25 (from its notes, not live state; quants unconfirmed): the Jetson router serves tev1-4b (about 0.3 s per short answer), gpt-oss-20b, gpt-oss-120b (about 1.1 s, low effort), qwen3.8-27b, gemma-4-31b, nemotron-3.5-lightning, muse-glimmer-30b and qwen3.5-122b; nemotron-3-super does not fit. It keeps ONE model resident, so two different big models swap every turn: use one model for both seats, or two small ones. Nobody has measured structured game play on them; tev1-4b once ran to the token cap, so set max_tokens and a JSON schema. Reach it by `ssh -L` or the nginx bearer route. It is shared with Quake round 3 and TrustBench, so check with the `orin` session before each run. Local only unless Michael says otherwise; a cloud model needs a yes each time.
- **D2. DECIDED 2026-10-25:** real card names on screen. Still to do: read the Wizards fan-content policy before the first public step.
- **D3. DECIDED 2026-10-25:** hands are shown, in all-AI games, through a spectator-only view (rule 4). The reviewers had preferred hidden-live; the cost of showing them is the extra barrier and its test, and the rule that humans at the table switch it off.
- **D4. DECIDED 2026-10-25:** it goes on the existing Divinci AI YouTube channel (no new channel); working title **"Divinci Table: AI's Playing Cards"** (no trademarked word in the title), with the AI-controlled banner in the description.

---
## Test commands (all offline; 2026-10-25, all green)
`python3 table/tests/<name>.py` for: `local_pilot_test` (model-call hardening: explicit model, loopback only, no proxy, wrong-model stops), `fusion_land_test` (bounce lands, modal spells, unaffordable turn-up), `oracle_test` (54 checks: a planted bug per kind, board checks, a guard per false positive), `pilot_game_test`, `replay_diff_test`, `engine_diff_test` (11,900 combats), `table_loop_test` (33), `broadcast_leak_test` (16, needs node+playwright+Vision OCR), `stream_supervisor_test` (22, needs Docker/OrbStack, ~3 min), `pilot_e2e_test` (14, ~7 min, real games on the real server against `harness/fake_router.py`).
Orin: nothing here touches it. Any real-model run needs the `orin` session's "clear" first (see L2 status).
