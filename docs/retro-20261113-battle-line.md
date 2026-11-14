# The battle line: what is built, and what the stream still needs

Asked on 2026-11-13: "Can we have a better visual representation of creatures that are attacking and blocking? Maybe moving them up to
the edge of the battle-line?"

## Built (the seat page, `/hand`)

`table/assets/battleline.js` (no page in it; tested in node) turns the combat the table already publishes into a plan:

* `plan(combat, has)`: which creatures stand on the line. Input is exactly `combat` from `/api/phase` (the same object is in a seat's own
  state as `combat`): declared attackers, each with its defender, `answered`, `blocked` and its declared blockers. Nothing else is read.
  An attack whose stage is `done` (damage dealt) puts nobody on the line; a creature that is no longer on its battlefield is dropped (and
  an attacker that has left takes its blockers off the line with it).
* `fan(groups, cardWidth, minX, maxX)`: where a row of blockers stands so that each group is centred on its attacker, side by side,
  groups pushed apart when they would overlap, slid back inside the row at its ends, and overlapped evenly (never outside the row) when
  there are more than fit.
* `label(entry, me)`: "⚔ → Claude", "⚔ unblocked", "⚔ ⛨2", "⛨ Horta".

`table/hand.html` uses it in two places:

* **The mat (900 px and wider).** A declared attacker leaves its row and steps towards the other side (my attackers up, theirs down),
  keeping its place along the row. A declared blocker steps towards the attacker from its own side and is moved along the row to stand
  opposite it. The step is a 0.28 s slide (`@keyframes blmove` on the `translate` property, so a tapped card's quarter turn is untouched);
  `prefers-reduced-motion: reduce` switches it off. Labels are separate elements next to the card (not inside it), so they stay upright
  beside a turned card. While a card is on the line its saved spot (a card the player dragged somewhere) is not used and not lost. A card
  on the line is still the same element with the same handlers: it opens its menu (tested) and it can be dragged. It is also still a
  drop target for the equip drag by construction, but that pairing cannot happen in a game (Equip is sorcery speed: never during combat),
  so it is not tested.
* **The list (under 900 px, phones).** There is no mat there, so the line is a strip above the Battlefield list: one lane per attacker
  with its blockers under it, lanes wrapping (nothing can be pushed off the screen), "unblocked" marked once the blocks are in.

Nothing was added to any payload: `/api/phase` and the seat state already carried all of it.

## Not built: `/board` and `/watch` (the page on the stream)

Both are `table/board.html`. Two separate reasons.

### `/watch` has no combat to show, by design

`/watch` draws `/api/spectate`, and `spectate_snapshot()` (server.py) takes ONE snapshot per turn, when the turn ends: the board as it
stands then, and the log up to then. That is the one-turn delay. Combat is over by the time the snapshot is taken, so a snapshot never
contains an attacker or a blocker, and a creature that died in that combat is already off the snapshot's board. There is nothing for a
battle line to read, and adding `combat` to the snapshot would add an empty list.

To show combat on `/watch` without weakening the delay, the previous turn's combat has to be **replayed** one turn late:

1. During a turn, record a frame each time the combat changes: attackers declared; blocks in; damage dealt. A frame is
   `{event_id, combat: combat_public(), board: board3d()}` (the board too: the cards on the line must still be on it). `combat_line()` is
   the helper the table speaks through as it moves combat on, so it is the likely place to call a `spectate_frame()` (to be checked: that
   all three moments pass through it); frames go into a list for the CURRENT turn that no endpoint serves.
2. `spectate_snapshot()` moves that list into the snapshot it is taking (`"frames": [...]`) and empties it. The frames therefore become
   visible at exactly the moment the log lines of the same combat do ("X attacks Y with …" is already in the released log), not earlier:
   the delay is unchanged, and nothing is published that the released log does not already say in words.
3. `board.html` in watch mode plays the frames of the newest snapshot once (a few seconds each: attackers on the line, blockers
   opposite, then the final board), then rests on the snapshot's board as today.

Why this was not done here: it changes what spectators are shown and when within the delayed turn (a replay is a new behaviour, and the
owner should decide the pacing, and whether a replay may cover the final board for those seconds), and it touches the live combat path
(`combat_line`) of a server that was running a streamed game while this was written. My estimate is about a day's work with tests,
including one that proves a frame of the turn in progress is never served.

### `/board` has the data but not the layout

`/board` is live and could read `combat` from `/api/phase` today. But the page is one panel per seat, each a wrapped flex list of cards,
laid out by CSS grid (`repeat(auto-fit, minmax(330px, 1fr))`): two columns side by side in a 1v1 at 1280 px. There is no shared
geometry between the two panels, so "opposite the attacker it blocks" has nothing to line up against.

What it needs:

* In a two-seat game, a **line band between the two panels** (a third grid column in the middle at 1280 px; a row between them when the
  panels stack on a phone). The band is drawn from `plan()`: one lane per attacker, the attacker on its controller's side of the band
  and its blockers on the other side, exactly the lane structure the phone strip in `hand.html` already uses (`drawBattleStrip`), turned
  sideways. Cards on the band are taken out of their panel's "Creatures" list while they are there.
* With three or four seats the panels do not face each other; the lanes then go into each attacking seat's panel as a "Battle line"
  group above "Creatures" (attacker → defender, blockers listed under it).
* `battleline.js` must be let through the `/watch` script filter (server.py serves `board.html` with every `/assets/` script except
  `art.js` stripped, because the helper scripts read the LIVE game; `battleline.js` fetches nothing, so it is safe to allow by name).
* The slide: `board.html` rebuilds its HTML on every change, so it needs the same "where was this card last time" map the mat uses
  (`BL_PREV` in `hand.html`) to start the animation from the old place.

My estimate is half a day for `/board`, sharing `plan()` and the lane markup; it only reaches the stream once the `/watch` frames above
exist.

## Tests

* `table/tests/battleline_js_test.cjs` (node): `plan`, `fan`, `label`.
* `table/tests/retro_1113_battleline_e2e.cjs` (Playwright, headless Chromium): the mat at 1512x860 and 1280x1080, the list at 390x844.
  It says at its top what it does not cover.
