# HUD and enforced turns: plan (2026-10-08, for review before any code)

Michael, after the first remote game on table.divinci.ai: "we can't do the honor system, especially for the remote games". Three requirements,
his words condensed:

1. **One interface on every tab.** Your hand as an arc along the bottom, like a HUD. Under it, on every tab, the buttons to move through the
   stages and to auto-pass.
2. **Real decks too.** For a physical deck, the hand shown on a player's own pages comes from a photo they take, or from the cards they say they
   hold, privately, to the AI judge. A toggle slides the hand below the bottom of the screen to keep it private.
3. **No honor system.** Turns, phases and passing are enforced by the server and TESTED by playing games through the real UI: each turn passes
   between players and phases in order, and the auto-pass toggle really works.

## Revision 1 (after the Opus 5.5 review, 2026-10-09: it read the code, it ran nothing)

The review found the plan right in direction and wrong in order and in how much it trusted the current server. **The first four items are exploitable in a live remote game today**
(any seated player's key + a browser console), so they are patched before any UI work. Each gets a failing test first.

**Patches first (live-game breakers):**
1. `POST /api/brain/begin` never checks whose turn it is (`server.py` ~3052, `VP.begin_turn()` in `player.py`): it untaps, draws and resets the land drop, and `hand.html` shows "Begin my turn" all the time.
   One misclick during someone else's turn takes their turn. Guard: only the active seat, only at the untap step, only once per turn.
2. `fair-reveal` is not blocked for pilots (`server.py` ~3176): it publishes every virtual deck's seed and shuffled order through `GET /api/fair`. Pilots: refused.
3. The pilot action list is open: `draw n`, `search`, `peek`/`topdown`, `put` (free battlefield), `token`/`counter`/`animate` with any values, `cast` with a client-supplied `discount`.
   Replace with an allowlist per seat kind, applied in the shared code path so the old alias endpoints are covered too. Untap joins this list.
4. A pilot cannot pass priority from most of the UI: pilots are in `VPS`, so the 30 s AI timeout passes for them (and ignores Hold and open questions); `/api/autopass` is human-only; the red Pass bar posts to
   `/api/phase/next`, which refuses a pilot; the `/hand` priority alert's "Pass" only closes the alert. Give pilots a real pass path.
5. `/me` presses NEXT by itself when a request fails (`checklistFirst` returns true on `c.error || !c.items`, then `sendPass()`): a network blip moves your own step on. Remove.

**Design changes:**
- **Stale-step token.** Every advance request carries `{player, step, round}` as the client saw it; the server answers 409 if it is stale. One fix for double taps, a second tab, replays, and item 5.
- **One turn state machine first.** Today human turns run on `PASS` + `finish_step`, while pilot turns are walked forward as side effects of actions (`ai_advance`). A pilot on its own turn is not in the pass round. Give every seat a kind
  (ai, pilot, phone, real-deck), take pilots out of the `VPS` turn path, and make each response say what it did: passed, stepped, or waiting. `ai_passed` must also check that it is that seat's turn to pass (it only checks membership).
- **BACK in a remote game:** active player only, within their own turn, one step at a time, only if nothing has been logged in that step (no land, cast, attack); back into the previous player's turn needs that player's
  confirmation within ~15 s; anyone else gets "request BACK"; never back past a pilot's `begin`; rate-limited so it cannot stall. Likewise Hold, Windows and question-answering must stop accepting any seat's key
  (`REMOTE_SEAT_POSTS`): the owner, or the seat the question was asked of.
- **Auto-pass `others-no-combat`** today stops in all four combat steps whether or not you are involved. The narrower version needs the board, which a real-deck board (self-reported, stale) cannot supply.
  It must also stop for anything new to respond to, and at every end step while the player holds an instant or flash card. The engine has no stack: a human's spoken/typed cast never restarts the priority round
  (`priority_reset` is called from two places only), so auto-pass can pass straight through a spell you wanted to answer. Fix the reset first.
- **Seats shared by accident.** The device fingerprint is a hash of IP + User-Agent; two identical phones behind one router share a seat, and `seat.js` auto-claims when `this_device` holds exactly one seat. Needs a per-browser nonce
  in the claim, and an unclaimed seat must not go to whoever claims first without the person at the table confirming.
- **Hide toggle and the shared stage:** all pages share one `localStorage`, so "hide the hand" set on `/me` would show it on the host's `/stage`. Store the setting per page, and the hand never renders on `/stage` or `/xr` unless that page is the seat's own.
- **Rooms share one cookie**: opening a second room in the same browser silently moves the first tab. The HUD must show the room id and refuse to act if the page's room differs from the cookie's.
- **Real-deck hand:** a typed list first; photo last. The photo route must not be `/api/chat/photo` (it posts a public chat event with a publicly served URL). The private vision route is `/api/xr/vision` on the Worker; on a laptop table there is none.
  `HAND_N` (player-set, default 7) and a declared list can disagree: the list replaces it for that seat. The judge's "that isn't in your hand" must not be said in public chat (it leaks information), and a declared hand goes stale after every draw,
  so the check can only be a private nudge to that player, never a refusal. When the judge is unsure it says "unsure" and lets the player correct; no vision output goes to chat or events.
- **Human untaps are open too** (`/api/card-action untap`, `/api/my-board` replaces the whole board incl. tapped state). For a real deck this is honor by nature: say so, and enforce what the server can see (order of play), not what the player does.

**Corrections to "what exists":** the step list has 11 entries (it ends with `cleanup`); the stage also has Hold, step timeouts, BACK and NEXT, `/me` also has step timeouts, and `urgent.js` already puts a Pass button on all six pages;
the human pass timer is one global value (`HUMAN_PASS_SECS`), pilots get the separate 30 s AI timeout; order is already enforced for humans in `next_step` (not for pilots/AI).

**Revised build order:** (1) the patches above, each test-first; (2) one turn state machine + the stale-step token + old endpoints routed through it; (3) a buttons-only dock on all six pages, shipped alone;
(4) the pilot hand arc (reads `/api/brain/state`); (5) real-deck hand from a typed list; (6) photos, only after a private vision route exists.

**Test plan corrections:** (a) every e2e request currently comes from 127.0.0.1, so `_is_local()` is true and the seat guard and LAN block are skipped: give each browser context its own `X-Forwarded-For`; (b) "UI matches `/api/phase`" is circular:
assert on game state (tapped permanents, library count, land played, hand size); (c) driving the game only through the dock never tests the bypasses: each alias and each action above needs a "refused" test; (d) timeouts can stand in for the feature: assert
`auto: true` in the events, not `timeout: true` (the existing check at `pass_ui_e2e.cjs:99` has `|| after.step !== before.step`, and `:131` can never fail: fix both); (e) run WebKit as well as Chromium (the players are on iPhones);
(f) two rooms on two ports are two origins and cannot reproduce the shared cookie and shared `localStorage`: serve both from one origin; (g) count visits per step incl. cleanup so combat windows cannot be "passed" by never reaching them;
(h) hand privacy must also cover `/api/events`, chat photos, judge speech and `/api/board3d`, not just the hand endpoint.

Still open for the owner: which actions a pilot may take with no card behind them (`draw`/`search`/`put` for effects the engine does not model yet: allow with a stated card and log it, or refuse?).

## Revision 2 (after the Hermes review, 2026-10-09: it also ran `phase_test.py` and `pass_ui_e2e.cjs`, both green)

Hermes agreed with Opus on the live-game breakers and added these. Items 1-4 join the first patch set.

1. **Untap has two server paths, not one.** `/api/brain/untap` (pilots) AND `/api/card-action` `tap`/`untap` (`server.py` ~2465, offered by `/board`: real-deck players). One rule for both: untap only in your own untap step; tap stays legal whenever you hold priority.
   There is no tap history anywhere, so "undo my last tap" is NEW work and a later feature, not part of the first patch.
2. **BACK / HOLD / WINDOWS authority** is decided BEFORE the dock ships (the dock multiplies the button). Proposed: BACK within your own turn = active player only; crossing into the previous player's turn = host/room creator only. HOLD = any seated
   player, but visible and logged. WINDOWS = active player or host. (`REMOTE_SEAT_POSTS` ~1069 marks all three "any valid seat key"; on a keyless LAN game they are open to the whole Wi-Fi: document, do not fix now.)
3. **The active player's turn has no timeout** (`_human_timer_applies` excludes them), so a stalled or disconnected active player freezes the table, and the only remedies are host-laptop-only. Needs a server-side answer for remote rooms
   (a visible "turn clock" with a stated consequence the table agrees to, e.g. auto-pass to end step; not a silent forfeit). The pass timer today passes FOR a player with no penalty.
4. **Seat keys:** the picker prints a key-bearing link (`seat.js` ~41) and hand-off links are shown in the same sheet: any screen share or pasted chat is a seat takeover, and release drops ALL of a seat's keys. Same-device key minting is unlimited.
   The cross-room case is still last-writer-wins: the room-2 tab overwrites the shared `localStorage` key, the room-1 tab adopts it through the storage event, and room-1's server answers `need_seat`. **My 2026-10-08 `seat.js` fix is real for same-room
   tabs and premature for two rooms**; test 5 must assert "the room-1 tab can still pass in room 1", not only "no popup on load". Fix: store keys per room (the page learns its room id from the server), not one global entry.
5. **Auto-pass facts that contradict the plan:** `autopass_round` checks neither Hold nor open questions (only `next_step`'s 409s hide that; the `ai_passed` path auto-passes humans during a hold); `/api/autopass` applies immediately
   (`autopass_round()` runs inside the handler), so "toggling mid-step takes effect on the next window only" FAILS on current code: decide which behaviour is wanted, then make code and test agree. "No-combat" cannot be computed for real decks
   (the board is self-declared: not declaring creatures gets you passed through combat). Today's all-four-combat-steps stop is the safer end: define the stop set explicitly and accept both over- and under-asking.
6. **The dock's NEXT/PASS label is chosen by seat kind**, not by server state: a pilot's own turn is driven by engine actions (begin, land, cast, attack, damage, end), NEXT refuses them, so "advance" means pass-in-window or end-of-turn only for pilots.
7. **Smaller:** a real-deck hand is validated against the seat's own deck file first (`seat_deck_names`), the whole `oracle.json` second; the "say it" path is first-class, not a photo fallback; dual-faced cards store both names.

**Test plan additions:** test 3 (untap) must also drive `/api/card-action`; test 4 must assert on the `ai_passed` path during a hold; test 5 as in item 4; the full-round test must press BACK, HOLD and WINDOWS and assert who may; order (test 2) is already green
today, so the dock can pass it with wrong labels: assert the labels per seat kind.

**Build order (unchanged by Hermes, amended):** (1) the patches including both untap paths and the BACK/HOLD/WINDOWS authority, test-first; (2) turn state machine + stale-step token; (3) buttons-only dock; (4) pilot hand arc; (5) typed real-deck hand; (6) photos last.

## What exists today (read, not assumed)

- `table/assets/tablebar.js`: a bar at the TOP of six pages (`/me /stage /xr /board /log /hand`). It already knows "whose turn / who must pass"
  from `urgent.js`. `seat.js` holds the device's seat key in `localStorage` (fixed 2026-10-08: tabs follow it).
- `/me` has NEXT, BACK, auto-pass (off / others / others-no-combat), Hold. Those buttons exist only on `/me`. The stage has its own NEXT.
- `/hand` (virtual-deck "pilot" seats) shows hand, battlefield and actions through `POST /api/brain/<action>`, with the seat key in `X-Seat-Key`.
- Server (`table/server.py`): `next_step()`, `passes_state()`, `AUTOPASS`, `PHASE`, `STEPS` (untap, upkeep, draw, main 1, beginning of combat, declare
  attackers, declare blockers, combat damage, main 2, end step), per-seat pass timers (`--human-pass-secs`), `HOLD`.
- Tests: `pass_ui_e2e.cjs` (real Chromium + real local server on its own port: glow, countdown, Pass from any page, voice) passes; `phase_test.py`,
  `remote_guard_test.py`, `pilot_ctl_test.py`, `pilot_next_test.py` run a real server.

## Holes found tonight (each is a requirement of the plan)

- **Untap is not enforced.** `POST /api/brain/untap` sets `tapped = False` with no check of step or turn, and `/hand` offers Untap on every tapped permanent.
  A player can tap lands to pay, then untap them. (`server.py`, the `action in ("tap","untap")` branch of `_brain`; `hand.html` line ~107.)
- **NEXT is not defined for a pilot seat after the game starts** (`next_step`): it used to answer `need_seat`, which made the claim popup loop. Now a plain 403
  naming `/hand`, but there is still no single "advance my step" control that works the same for a pilot, a human with a phone and a human with a real deck.
- **Controls differ per page.** NEXT/BACK/auto/Hold live on `/me`; the stage has NEXT only; `/hand` has Pass / End turn / Begin; `/xr` and `/board` have none.
- **The seat key is shared across rooms** (one origin). Fixed in `seat.js` 2026-10-08; the e2e should exercise two rooms in one browser profile.
- Open question, not yet a defect: BACK (undo a step) is a table-wide override. In a remote game without a host laptop, who may press it?

## Design

### A. Server: one authority for turns (all enforcement is here; the UI only displays)

1. **Untap** happens only in the untap step (`begin_turn` / the engine) and through effects the engine knows. `untap` from a pilot outside that is refused 403 with
   the reason. A mistaken tap is an `undo` of the LAST tap by that seat in the same step with no spell cast since (the engine tracks it), never a free untap.
2. **One advance action per seat kind**, same endpoint family, same answers: `POST /api/turn/advance {by, key}` meaning "I pass priority / I am done with this step".
   Pilot, phone human and real-deck human all use it. The server decides what it does (pass in a priority window, or move the step on your own turn when everyone
   has passed) and answers 200 or 409 with `{waiting_on, why}`. The old endpoints stay as aliases (cloud rooms in flight, tests).
3. **Auto-pass** is server state per seat: `off`, `others` (never your own turn; passes in other seats' turns), `others-no-combat` (also stops at declare
   attackers / declare blockers if you have anything that could attack or block, or are being attacked). It must never pass for you while you have an unanswered
   question, a hold, or while it is your own turn, and must never pass before the seat BEFORE you in order has passed.
4. **Order is enforced**: a seat may pass only when it is its turn to pass (`passes_state()['next']`); a step ends only when all have passed; the turn passes
   to the next seat in order. Every refusal says who is awaited.
5. **Private hand for real decks**: `POST /api/seat/hand {by, key, cards, source: "photo"|"said"}` stores names per seat; `GET /api/seat/hand?seat=` returns them ONLY to
   that seat's key. Everyone else gets the count (already public via `HAND_N`). Names are validated against the offline card file (`table/.cache/oracle.json`);
   unrecognised ones are returned as "unsure" for the player to fix.
6. **Judge cross-check (soft)**: when a real-deck player announces a cast/play (`/api/card-action`), the judge compares to the declared hand and, if the card is not
   in it, asks in table chat ("Sam, that isn't in the hand you told me about. Draw it?") instead of refusing: a physical game cannot be refused, only questioned.

### B. Client: `table/assets/hud.js`, loaded by `tablebar.js` so six pages get it with one change

1. **Dock, fixed to the bottom**, above the safe area, on every page that loads the bar. Top row: step ribbon (whose turn, step, who is awaited), then the buttons:
   `◂ Back` (only where allowed), `NEXT / PASS` (one button, label from server state), `Auto: off | others | no-combat`, `⏸ Hold`. All call the single advance API.
2. **Hand arc** above the buttons: cards fanned on a circle (CSS transforms; tap a card to enlarge; pilot cards show Play land / Cast; real-deck cards are read-only).
3. **Hide toggle** (👁): slides the hand below the viewport, leaving a small tab; persisted per device; defaults to hidden on the shared stage screen.
4. **Hand source** by seat kind: pilot -> `/api/brain/state` (existing); real deck -> `/api/seat/hand`; spectator or unseated -> no hand, only the buttons disabled.
   A **photo / say it** panel for real decks: take a photo (reuses `photo.js`, the board-reading vision route) or type or speak the list; shows what the judge understood; edit, then Save.
5. Pages must leave room (`body` padding-bottom = dock height, like the bar does for the top), including `/xr` (canvas) and `/stage`.
6. `/hand` keeps the battlefield and the attack/block flow. Its own Pass / End turn / Begin buttons move into the dock and call the advance API.

### C. Rollout

Local server and tests first; no deploy while a room is live (restart drops sessions for a minute). Deploy from a clean worktree on `origin/main` through
`deploy-gate` (see how the 2026-10-08 seat fix shipped). Cloud Worker changes (vision route for hand photos) ride the same deploy.

## Tests: "test games" through the real UI

Real local server (spare port, own research dir) + real Chromium contexts, one per seat (a pilot, a phone human, a real-deck human), like `pass_ui_e2e.cjs`:

1. **Full-round game**: play at least 3 complete rounds (every step, every seat) driven ONLY by clicking the dock; after each click assert the server's `/api/phase`
   matches what the UI shows, the awaited seat is correct, and a click by the wrong seat is refused with the right reason.
2. **Order and no-skip**: a seat cannot pass out of order; a step does not end until all have passed; attackers/blockers windows cannot be skipped; the turn passes to the right seat.
3. **Untap**: lands tapped to cast stay tapped for the rest of the turn (UI and server both); untap occurs at the next untap step; a direct untap request is refused.
4. **Auto-pass**: for each mode, scripted: `off` never passes; `others` passes in others' turns and never in your own; `no-combat` stops in combat when you could act;
   never passes before the seat ahead of you; never passes with an open question or hold; toggling mid-step takes effect on the next window only.
5. **Multi-tab / multi-room**: two tabs for one seat stay in sync; a stale key from another room does not loop the claim popup.
6. **Hand privacy**: another seat's key, and an unseated browser, get only the count; the hide toggle moves the arc out of the viewport (measure) and the setting survives a reload.
7. **Real-deck hand**: from a typed list and from a fixture photo (fake vision), the arc shows the cards; unrecognised names are flagged.
8. **Layout**: dock never covers the page's own controls at 390 / 768 / 1280 px on every page; measured, not eyeballed.
9. **Mutation checks**: for each enforcement rule, break the rule in a copy and confirm a named test fails.

Existing suites (`pass_ui_e2e`, `phase_test`, `remote_guard_test`, `pilot_ctl_test`, `pilot_next_test`, `seat_js_test`) must keep passing.

## Questions for the reviewers

1. Is one advance endpoint for all seat kinds the right seam, or will the real-deck and pilot semantics diverge in ways that make it a lie?
2. Auto-pass `others-no-combat`: what exactly should stop it? List the cases we will miss (triggered abilities, instants, stack responses we do not model).
3. Which rule would you expect a clever or careless player to bypass first, given a phone and a browser console? (Seat key theft between tabs, replaying requests, clock games with `--human-pass-secs`.)
4. Real-deck hand from a photo: what fails first (glare, sleeves, dual-faced cards, many identical lands), and what should the UI do when the judge is unsure?
5. BACK in a remote game: who may press it? Propose a rule.
6. What in the test plan would pass while the product is still wrong?
7. The build order: server rules first, then dock with pilot hand, then real-deck hand. Wrong order?

## Stage 1 notes (implemented 2026-10-09; tests: `table/tests/stage1_guard_test.py`, `table/tests/stage1_ui_e2e.cjs`)

What the server now refuses for a REMOTE caller (a request carrying a forwarding header). Every refusal says why.

1. **`begin`** (`begin_refusal`): only the ACTIVE seat, before main 1, once a turn. "At its untap step" is taken as "before main 1" because a
   pilot's begin walks untap -> upkeep -> draw -> main 1 and waits at each step for the people to pass: the page retries `begin` with the
   step already at upkeep, and that retry must not be refused. `PHASE["begun"]` ends it. A lock serialises a double tap. Before the game
   starts only the host's brain token may begin (brain_e2e plays solo engine turns that way); a pilot doing it would make itself first
   and skip the high roll. `/api/phase` now carries `begun`; `/hand` greys Begin out unless the server would accept it.
2. **`fair-reveal`**: refused to a pilot key (it publishes every deck's seed and order through `GET /api/fair`). The host's token still can.
3. **Pilot allowlist** (`PILOT_ACTIONS`, applied to the last path segment of `/api/brain/<action>` BEFORE `ai_advance`, so a refused action
   cannot walk the turn on as a side effect, and every alias path is covered). Derived from what `hand.html` calls and what
   `pilot_ctl_test.py` does with the pilot mode of `tablectl.py`:

       say  begin  land  cast  turn-up  tap  untap  attack  damage  block  pass  end  life (own only)

   Refused with 403 and a reason: draw, search, peek, topdeck, put, token, counter, animate, bottom, shuffle, mulligan, discard, mill,
   role, destroy, exile, bounce, blink, graveyard-out, manifest, take, new-game, fair-reveal, and any unknown action. `discount` on `cast` is
   dropped for a pilot key. The host's brain token keeps every action. (`tablectl` exposes more verbs than this; on a pilot key they now
   answer 403 "isn't available to a pilot seat". If a pilot legitimately needs one, add it here with the card behind it.)
4. **Untap** (`untap_refusal`): only the active player, in their own untap step. Both paths: `/api/brain/untap` (pilot keys; the host token is
   exempt) and `/api/card-action` `untap` (people with real decks, everyone). `begin` still untaps everything. Tap is not restricted. No
   "undo my last tap". `/hand` and `/board` no longer offer an Untap button the server would refuse. NOT covered: `/api/my-board` replaces a
   whole board including tapped state, so for a real deck this stays honor by nature.
5. **Pilot pass**: `/hand`'s alert Pass and the red bar's Pass call `POST /api/brain/pass` with the step they were looking at. For a pilot key
   the server refuses a pass when the game has not started, when it is the pilot's own turn, when the pilot is not in the round, has
   already passed, is not next in turn order, or names a `player`/`step` the table has left (stale). The host token's pass is unchanged.
   The pass is counted before the answer is sent. `/hand` also read the 409 `waiting` string as an array (`join` is not a function), so a
   begin that had to wait never retried.
6. **`/me`**: `checklistFirst` answers `"error"` (not `true`) when the checklist cannot be read; only the popup's Pass, or the NEXT button
   the person pressed, sends a pass. The NEXT button still proceeds when the list is unreadable: the person asked for it.
7. **BACK / HOLD / WINDOWS** in a room that checks seat keys (cloud, or `STRICT_SEATS=1`), for a caller that is not the host:
   BACK = the active player's own key, one step, refused once the visible log shows a land/board change, a chat play or an attack for
   that player in this step, and never into the previous player's turn. HOLD = any seated key, logged as who really pressed it.
   WINDOWS = the active player's key. "Host" = the laptop itself (no forwarding header) or the brain token, including through the Worker
   with no seat key (`_restricted_room`). **Keyless LAN games (no `STRICT_SEATS`) are unchanged: any device on the network can still
   BACK, HOLD and switch WINDOWS. Documented, not fixed.** Not done (stage 2+): a confirmation from the previous player, rate limits.
