# HUD and enforced turns: plan (2026-10-08, for review before any code)

Michael, after the first remote game on table.divinci.ai: "we can't do the honor system, especially for the remote games". Three requirements,
his words condensed:

1. **One interface on every tab.** Your hand as an arc along the bottom, like a HUD. Under it, on every tab, the buttons to move through the
   stages and to auto-pass.
2. **Real decks too.** For a physical deck, the hand shown on a player's own pages comes from a photo they take, or from the cards they say they
   hold, privately, to the AI judge. A toggle slides the hand below the bottom of the screen to keep it private.
3. **No honor system.** Turns, phases and passing are enforced by the server and TESTED by playing games through the real UI: each turn passes
   between players and phases in order, and the auto-pass toggle really works.

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
