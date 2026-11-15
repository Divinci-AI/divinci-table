# Three games in three.js — D&D polish, a 3D chess board, poker

*Started 2026-11-14 (branch `three-games-20261114`, off `main` at `d74c4a2`). Michael: "polish and test our D&D Divinci
Table game and Chess and Poker all in three.js". Where it began: D&D already had a three.js stage (`assets/dnd-scene.js`,
D4–D7 of `docs/DND-3D-GOAL.md`); chess was a 2D page with no tests at all; poker existed only in `docs/platform-plan.md`.
Tick a box only when its check has been run and its output read.*

## Rules (same as the D&D goal)
- Never touch the live tables' ports (8800, 8801, 8820, 8830, 8883); tests take a port from `table/tests/test_port.cjs`.
- Offline: no CDN, no model calls; three.js only from `/vendor/`. Every mesh is drawn from primitives in code, every card
  face on a canvas; no downloaded art.
- No commit, push or deploy without Michael's word. Cloud rooms for poker need a Worker deploy: proposed, not done.

## Shared
- [x] **Browser tests could land on a live table.** Nine `.cjs` tests picked `8000 + random(900) + n`, i.e. 8030–8995,
      which includes 8800/8801/8820/8830/8883 (the full-suite runner restarts 8800). Now `test_port.cjs`: 9100–9899, with
      the reserved set checked anyway.
- [x] **Every D&D and chess page logged a 404** (`/api/phase`): `seat.js` asked Magic's phase route for the game id on
      every table. Now a game-neutral `GET /api/game/id` on all three servers (Magic, D&D, chess; poker too).
      *Check:* `dnd_page_e2e` and `dnd_xr_e2e` were 10/11 and 7/8 ("no page errors"); now 11/11 and 8/8.
- [x] **A stale seat key was never detected on chess or D&D.** `seat.js` marks a key dead only on
      `403 "not a seated player"`; chess had no `/api/seat/check` (its POST guard answered 403 with other words) and
      D&D answered "no such seat key". Both now answer as Magic does. *Check:* `chess_test` "…refuses a stranger's".
- [x] `table/tests/shots_3d.cjs dnd|chess|poker [dir]`: screenshots of each 3D table for a person to look at
      (tests can't tell whether a scene reads well). Writes to a temp folder; nothing is committed.

## D&D
- [x] **Tokens hidden behind walls.** In the tavern both players' characters stood in the back room, out of the camera's
      sight. Every token now has a name tag above it, drawn through walls (`depthTest: false`, never takes a tap),
      sized by camera distance so it stays readable, bigger on its turn, dimmed when down; on a mini it sits over the
      model's head. *Check:* screenshots of tavern, cave, crypt and bridge plus the headset preview, looked at.
- [x] **Check:** `dnd_test` 130, `dnd_zones` 36, `dnd_voice` 37, `dnd_assets` 130 (`--built` 130), `dnd_dm_sim` 17,
      `events_longpoll` 12; browser: page 11/11, round 9/9, xr 8/8, room-load 5/5, theater 18/18, voice 14/14, sound 10/10.
- Frame time (headless software GL, two tokens): 22–25 ms before the tags, 28–30 ms after. Software GL's fill cost says
  little about a GPU; worth re-reading on the Quest (D6's 72 fps check).

## Chess
- [x] **A 3D board** (`assets/chess-scene.js`): wooden board in a walnut frame with gold trim and a–h / 1–8 (the right way
      up for whoever sits there), turned pieces from lathe profiles (the knight an extruded, bevelled head, turned so
      each player sees its profile), the 2D board's marks (selected, legal targets as dots, captures as rings, last
      move, a pulsing check), moves that glide (a knight hops), captures that shrink away, castling, promotion.
- [x] `chess.html`: 2D/3D toggle like D&D (3D by default on a desktop with WebGL2; 2D on phones and with reduced motion;
      remembered); Black sits on Black's side; one `tapSquare()` for both boards; out-of-turn and spectator taps say why
      instead of drawing a server refusal. **Draws only when something changes** (a move, the camera, an animation):
      a still board costs nothing.
- [x] Server: serves `/vendor/` (traversal-checked), **long-polls** `/api/events?wait=N` (a move reached the other screen
      on the next 1 s poll; D&D's playtest found that too slow), accept backlog 128.
- [x] **Check:** `chess_test.py` **34 passed** (seats and keys, turn order, illegal/UCI/SAN, castling FEN, clocks and
      increment, snapshot round-trip, seat.js routes, `/vendor` traversal, long poll, draw/resign/after-the-end, the
      built-in AI answering, nobody moving for the AI). Mutation-checked: without the long poll 2 checks fail; without
      the seat-key rule 6 fail. `chess_page_e2e.cjs` **21/21** (3D raycast select/marks/move, the phone sees it within a
      second, the phone's move walks on the 3D board, a capture leaves 31 pieces, Black's side, reduced motion → 2D and
      back by choice, an idle board isn't redrawn, no page errors, no horizontal scroll at 390 px). Both in `run_all.sh`.

## Poker (new)
- [x] `table/poker_eval.py` (engine, stdlib only), `table/poker_server.py` (port 8812; core.py seats/events/rooms like
      chess), `table/poker.html` + `assets/poker-scene.js` (three.js felt table, chips, canvas-drawn cards and backs,
      animated deals and pots; 2D on phones and with reduced motion), `/watch` for spectators.
- [x] No-limit Hold'em, 2–6 seats, **play chips only**: button and blinds (heads-up rules), min-raise = last full raise,
      a short all-in doesn't reopen betting, side pots, uncalled bets returned, split pots (odd chip left of the button),
      showdown order with mucking, folded hands never shown, bust-outs sit out, game over at one stack.
- [x] **Hidden cards:** the public state, events and `/watch` never hold an unshown hole card; your cards only from
      `/api/poker/me` with your seat key. **Fair shuffle:** each hand publishes `sha256(seed)` before the deal (the
      seed is `secrets.token_hex(32)` per hand). **Every seed is sealed until the GAME ends** (one stack left, or a
      player presses End game, twice, which hands back the bets of a hand in progress): a seed rebuilds the whole deck,
      so revealing it after each hand would show everyone the folded cards, which real poker never does. Michael left
      this call to me (2026-11-14). At the end the page checks every hand in the browser, against the commits that
      screen saw before each deal. `--seed` (tests) is refused on a cloud table (`TABLE_CLOUD=1`).
- [x] AI seats: no model spend; starting-hand score preflop, Monte-Carlo equity (≤300 deals, in a thread) vs pot odds.
- [x] **Check:** `poker_test.py` **119 passed** on python 3.9 and 3.14 (111 before the end-of-game seal; the new
      checks: a finished hand stays sealed mid-game, End game needs a key, chips are conserved through a called-off
      hand, every seed opens after it) (evaluator, side pots, betting rules, hidden info,
      commit/reveal, chips conserved over 200 AI hands, snapshot mid-hand). Mutation-checked five ways (cards in the
      public state, cards in the deal event, every player in every pot, odd chip, short all-in reopening): each fails.
      `poker_page_e2e.cjs` **20/20** (End game, then the browser checks every hand). An independent probe (two people and an AI, every street): Ana's cards appeared in
      none of public state, all events, `/watch`, `/me` without a key or with Ben's, verify mid-hand, room snapshot
      without a token; the seed stayed sealed until the hand ended, then matched; acting without a key or with another
      seat's: 403. Screenshots looked at (start, mid-hand, showdown, 6 seats, phone, watch).
- Left out on purpose: turn timer, dead-button rule, rematch without a restart, player-contributed randomness in the seed
  (the commit proves the deck wasn't changed mid-hand, not that the server didn't choose it).

## Poker in the public lobby (Michael: "Poker in the cloud: yes", 2026-11-14)
- [x] Lobby form: Texas Hold'em, 1–6 people plus 0–5 house players (2–6 seats in all), stacks 500 / 1,000 / 2,000,
      blinds 10/20, "play chips only: nothing is staked or paid out". `createPoker` in `cloud/src/index.ts`; the room
      config carries `bots` (not `ai`), so no AI key ever reaches a poker room; `cloud/entrypoint.py` starts
      `poker_server.py` with `TABLE_CLOUD=1`.
- [x] **Check:** `cloud/test/agents_e2e.py` (wrangler dev + the room host, on this laptop) **all passed, 93 checks**,
      10 of them new for poker (the room starts, bad tables are refused, seats, house player, 500 chips, a hand dealt
      through the lobby, cards only with the key, the seed sealed, the lobby lists it as play chips).
      `roomhost_test.py` all passed; `policy.test.ts` 34/34; `tsc --noEmit` clean.
- Still open from the launch audit, for every game, not poker's alone: room traffic through the Worker, polling, and
  moderation of table talk and names.
- A room that expires before its game ends never reveals its seeds (the game was not finished). Acceptable for now.

## Not done / for Michael
- The chess AI without Stockfish (the laptop has none) is weak; the cloud image installs Stockfish.
- Headset (Quest) frame-rate check for the D&D name tags; nothing here measures a real GPU.
