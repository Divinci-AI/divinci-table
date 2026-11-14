# A match: best two out of three on one table

Added 2026-11-14. A draft pairing (Michael:Sam, Claude:Fusion) plays a **match**: the first seat to win two games. When a game ends and
the match is not decided, the table deals the next game **by itself, on the same process**: the same seat keys and links, no new tunnel,
nobody re-joins. Before this, "game 2" meant relaunching by hand: a new table process, new keys, new links, the remote player's tunnel
restarted.

```
python3 table/server.py … --ai "A|" --ai-deck a.json --ai "B|" --ai-deck b.json --pilot A --pilot B --match bo3
TABLE_MATCH=bo3 …                       the same, from the environment (what match_when_ready.sh exports)
--match-countdown 25                    seconds between a game's end and the next game's start (TABLE_MATCH_COUNTDOWN)
--match-max-wait 300                    the longest a player's "Wait" can hold the next game back (TABLE_MATCH_MAX_WAIT)
TABLE_MATCH_RECAP_SECS=90               how long the recap card stays up on a seat's page once the next game has begun
TABLE_MATCH_WATCH_RECAP_SECS=20         …and on /watch (three times that for the final card)
```

**The default is `--match single`: one game per table, exactly as before.** The public lobby's agent tables (`table.divinci.ai`) do not pass
the flag, so nothing changes there: one game per table, and `/agents/results` reports that game. A match needs exactly two virtual-deck
seats and nobody playing physical cards (only then does the table itself know a game is over, and can it deal the next one); a table
started with `--match bo3` and anything else says so on start-up and plays single games. `draft/match_when_ready.sh` turns matches ON for
1v1 rounds (`--single` turns them off; `--four` is never a match).

## The rules (table/bo3.py)

* The match ends when a seat has two wins. A drawn game counts for nobody and another game is played; after five games the match is
  called (more wins takes it; equal wins is a drawn match), so a match can never go on for ever. The engine itself only ends a game as a
  draw when both seats are at 0 or less in the same damage step; the host can call one: `POST /api/game-over {"draw": true}`.
* **Who plays first.** ONE roll, before game 1. After that there is no roll: the **loser of the previous game chooses** to play first or
  draw first. After a drawn game the chooser is whoever chose (or won the roll) for the drawn game. A person gets two buttons on the
  recap card; an AI seat plays first; no answer by the time the next game starts is "play first", and the log says it was the default.
  The public log says who chose and what, and the first line of each later game ends "No roll." `POST /api/highroll/start` and START are
  refused for games 2 and 3. (Seen live on 2026-11-14: game 2 of Michael vs Sam rolled dice instead of asking the loser.)
* Same decks: each seat is rebuilt from its deck file. No sideboarding (see "Not done").

## What everyone can read: `GET /api/match` (public, no seat key)

For the pages, the launcher and a stream overlay. Never a card, never a key.

```json
{"on": true, "best_of": 3, "need": 2, "seats": ["Michael", "Sam"], "wins": {"Michael": 0, "Sam": 1}, "score": "Sam leads 1–0",
 "game": 2, "games_played": 1, "over": false, "winner": null,
 "games": [{"n": 1, "winner": "Sam", "draw": false, "turns": 11, "totals": {"Michael": -2, "Sam": 7}, "first": "Sam",
            "chooser": "Sam", "how": "roll", "lines": ["The last blow: Michael took 5 from Sam (Horta)."]}],
 "current": null,
 "next": {"n": 2, "chooser": "Michael", "why": "lost", "choice": null, "plays_first": null, "default": "play", "holds": [],
          "seconds_left": 18.4}}
```

`{"on": false}` on a single-game table. `/api/phase`, `/api/seat/legal`, `/api/spectate` and `/api/game/summary` carry a short `match`
(`game`, `wins`, `score`, `over`, `winner`, `continues`). The log has `match` events (`kind`: `game`, `choice`, `wait`, `now`, `start`).

`POST /api/match/next {"seat": "Michael", "action": …}` with that seat's own key (`X-Seat-Key`):
`play` / `draw` (the chooser only, once), `now` (Start now: lifts this seat's own Wait; with nobody else waiting the game starts at once),
`wait` (holds the next game for this seat, up to `--match-max-wait`). The host's `now` (the brain token, on the laptop) lifts every Wait.
The host cannot choose play or draw for a player.

The match is saved with the game (the snapshot) and in `TABLE_SAVES_DIR/match.json` (0600; `match-p<port>.json` in the research folder
when no saves folder is set). A table restarted with `--restore <snapshot> --match bo3` carries on with the match. A new table (no
`--restore`) starts a new match and keeps the old file under a dated name.

## For agents

Between games the legal list's note is **`match continues: the next game is starting`**, with no actions. It is never `the game is over`
until the MATCH is over. An agent that stops on "the game is over" therefore stays; keep polling, the same seat key plays the next game.
If your seat lost, `match.help` in the legal list tells you how to choose (`POST /api/match/next`); say nothing and you play first.

## The recap card (assets/matchrecap.js)

On every seat page and on `/watch`: the winner, final life totals, turns, the match score, a line or two from the server's summary of the
public log, and the countdown. It has no backdrop and blocks nothing. The next game starts in the background and the card stays up, shrunk
to two lines, over the new game's opening hand until it is closed (✕, Escape, a tap outside) or closes by itself. While it is asking the
loser to choose, a tap outside does not close it (✕ and Escape do; a pill brings it back). On `/watch` it has no buttons and always closes
by itself. When the match is over it says so, lists each game, and offers nothing to start.

## Per-game state, and how each piece is reset (`table_reset_for_new_game` in table/server.py)

| State | Reset |
|---|---|
| Each seat's library, hand, battlefield (counters, auras, equipment, tokens), graveyard, exile and impulse zones, life, mana pool, land drop, turn counter, "until end of turn" changes, pending choices / surveil / scry, held spell, mulligan marks | `reshuffle_all()` builds a new `VirtualPlayer` from the seat's deck file. Life is the deck's own `startingLife` (20 for a 40-card deck, 40 for Commander): the "life = 40" in the old `new-game` action only ever applied to physical (`--human`) seats |
| The mulligan step (`MULL`) | off and not done, in `reshuffle_all()`; `start_turn` opens it again |
| The fairness commit (`FAIR`) | a new game id and newly sealed shuffles; the finished game's proof is published (`fair` / `revealed`) before its seeds are replaced |
| Whose turn, the step (`PHASE`) | nobody's: the first turn is handed out again (and the first player skips the draw again: `GAME_FIRST`) |
| Priority windows and passes (`PRIORITY`, `PASS`), a host's hold (`HOLD`) | emptied; hold off |
| Combat (`COMBAT` incl. `decl`, `ATTACKED`, `DAMAGED`, `COMBAT_BATCH`), 2HG marks (`TEAM_TURN`) | emptied |
| Once-per-step and once-per-turn marks (`STEP_FIRED`, `HAND_TAPS`, `AI_OPENED`, `REMOVAL_APPLIED`, `EFFECT_USES`, `TURN_END`, the engine's "died this turn") | emptied |
| The roll (`HIGHROLL`, `CEREMONY`) | cleared, then written as `{"mode": "match", …}`: who is first, who chose, "no roll after game 1" |
| A spell held for responses (`HELD`), stall notes (`WAIT_SINCE`, `WAIT_SAID`, `REFUSED_SEEN`) | emptied |
| BACK's saved points (`REWIND`, `BACK_REQ`, `ACT_STARTS`) | emptied. Without this BACK at the start of game 2 restored game 1's board (the test proves it) |
| The spectator buffer (`SPECTATE`: `/watch` is one turn behind) | emptied; the new game's first `start_turn` writes a fresh snapshot of empty boards |
| To-dos and "by hand" notes (`TODOS`, `PLACED`, `MANUAL_SAID`, `NOTED`), physical boards (`PUBLIC_BOARD`, `HAND_N`) | emptied |
| Journals (`JOURNALS`), concessions (`CONCEDED`), game over (`GAME_OVER`) | reset |
| The turn order (`ORDER`) | the seat that plays first, then the other |

Kept on purpose: seat keys, devices and proxies (`SEAT_*`), each person's auto-pass, the windows setting, declared decks, declared
identities, the public event log (a `new-game` event marks the boundary; `/api/game/summary` and the hand page read only the current game)
and the event ids (every page's cursor stays valid). The hand page forgets its own per-game memory on `table-new-game` (used-once marks,
mulligan picks, chosen attackers).

## Not done

* **Sideboarding.** The draft page does let a player edit a deck after "I'm ready" (it cancels ready). The table re-reads each seat's deck
  file when it deals a game, so a sideboard step would be: (1) a pause between games in which the draft server accepts edits again for the
  two players of that table and shows their sideboard, (2) the launcher re-running `make_deck_json.py` for a changed deck before the
  countdown ends (40-card check included), (3) the table's "Wait" held until both have pressed ready again. None of it is built.
* **Four-player and Two-Headed Giant tables** are single games.
* The public lobby does not offer matches. To offer them, `cloud/roomhost.py` must report a result per MATCH instead of at the first
  `over` it sees, and `/agents/results` would need a score field.

## Installing it at the live table

Nothing here has been applied to the live launchers or the running engine: they were only read. Do it between two rounds, when no table
is running (the pages are read from disk on every request, so changing the engine checkout under a running table serves it new pages
with an old server).

| File | What to do | Needed for |
|---|---|---|
| the engine checkout (`TABLE_CODE`, `~/.divinci/trek-engine-wt`) | move it to a commit that has this branch: `table/server.py`, `table/bo3.py` (new), `table/assets/matchrecap.js` (new), `table/assets/gameover.js`, `table/hand.html`, `me.html`, `board.html`, `table.html`, `table3d.html` | everything |
| `claude_autopilot.py` beside the launchers | replace with `bots/claude_autopilot.py` | Claude and Fusion playing games 2 and 3 (the old copy stops at the first game over: the seat would then sit idle and the match would wait on it) |
| `claude_banter.py` beside the launchers | replace with `bots/claude_banter.py` | table talk after game 1 (the old copy exits at the first game over) |
| `match_when_ready.sh` beside the launchers | replace with `draft/match_when_ready.sh`; run it with `--follow` | matches by default (it exports `TABLE_MATCH=bo3`), the per-game lines, exit when all matches are over |
| `play_round.sh` | no change needed: it starts `table/server.py` with its own environment, so the exported `TABLE_MATCH` reaches the table. (The tunnel diffs in `docs/retro-20261113-launch-notes.md` are separate and still worth applying.) | |
| `play_four.sh`, `tunnel_watch.py`, `table_tools.py`, `claude_autopass.py` | no change | |
| the draft server (`draft/draft_server.py`) | optional, and only at the START of a draft (restarting it mid-evening is not worth it): the score beside "Join the match". An older draft server refuses the score and the launcher carries on without it | the score on the draft page |

`play_round.sh`, `play_four.sh`, `tunnel_watch.py` and `table_tools.py` have no copy in this repository, so they could not be changed or
tested here; the launcher's test uses a stand-in play script.

Can it go in between two matches of the same draft? Yes: the draft server is its own process and is not touched (leave it running), the
decks are already built, and a match is started by a fresh `match_when_ready.sh` run. Not in the middle of a match: a table already
running was started without `--match` and stays a single game until it is replaced.

To go back: `--single` on `match_when_ready.sh` (or `TABLE_MATCH=single`) is exactly the old behaviour with the new code.
