# Dogfood game: player brief (identical for every seat)

You are one of two players in a 1v1 Commander game on a local test table. You play it the way a REMOTE player would, through the table's own command-line client, `./tc`, which talks to the same server API the web pages use. The point of the game is to
find out whether the interface and the rules enforcement hold up when a real, thinking player uses them for a whole game. So play to win, play honestly, and write down everything that confuses, refuses or breaks.

## Your tools
- Your seat folder: `RUN/SEAT/` (given to you). Use ONLY `./tc` there (run it as `RUN/SEAT/tc ...`), and write your notes to `RUN/SEAT/findings.md`.
- Do NOT read the server's source code, the other seat's folder, any key file, or the host token. Do NOT call the server any other way (no curl, no raw requests, no other endpoints), and do NOT try to get around a refusal: a refusal is a finding, not an obstacle.
- Run `RUN/SEAT/tc --help` once to see the commands. Useful ones: `state` (your private view: hand with rules text, board, mana, life), `phase` (whose turn, which step, who must pass), `events` (what just happened), `begin`, `land "Name"`,
  `cast "Name" [--on X] [--targets ...]`, `attack "Perm=Opponent"`, `damage ...`, `block [CREATURE] [--attacker NAME]` (the amount comes from the table; with no creature you take the hit), `pass`, `end "text"`, `tap REF`, `untap REF`, `say "text"`, `life me +3`.

## How a turn works here
- On your turn: `begin` (untap, draw), then play at most one land, cast spells, attack (`attack "Creature=Opponent"`; the table reads the creatures' power and deals the damage itself, so there is nothing to type for it; run `damage` afterwards to run on-damage triggers), then `end`. Each step you must wait for the opponent to pass priority: `begin` and the others retry for you while the table waits.
- If `phase` says the game has not started, it also says how to start it. You can see the other player's board in `state` (`opponents`) and who is attacking you (`incoming`).
- During the OPPONENT's turn you get priority at each step: when `phase` says you must pass, run `pass` (you may cast an instant first). If you do not answer for a few minutes the table passes for you (and logs it).
- To wait for the opponent: run `phase` (or `state`), then `sleep 8` and run it again; never loop for more than about a minute inside one command.
- The engine checks mana, the one land per turn, and legal targets. If a rule or card effect needs something the interface will not let you do, say so in findings.md, play on without it, and do not invent workarounds.

## Limits
- Play your first TURNCAP turns (the turn cap for this run), then stop: after your last `end`, finish your findings and report. Also stop after about 450 tool calls in total. Win earlier if you can (an opponent at 0 life).
- Keep table talk short. Do not narrate every move.

## Report back (your final message)
1. Final life totals as the table shows them, and a one-line log of what you did each turn (land, spells, attacks).
2. How many times the interface refused or confused you, and the five most important problems, each with the exact command and the exact answer.
3. Whether the table's state ever disagreed with what you expected (mana, land drop, life, hand), with the evidence.
4. What you could not do that a real game needs.
Do not claim anything you did not do or see. If you ran out of calls, say where you were.
