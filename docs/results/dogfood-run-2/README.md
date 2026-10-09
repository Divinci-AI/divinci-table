# Dogfood run 2: Sonnet 5.5 (Tuvasa) vs Opus 5.5 (Kaust), decks swapped, 8 turns each, build `48ab779`

Opus played all 8 turns (final: Sonnet 5, Opus 32). Sonnet reached its turn 8 and then waited ~25 min for an answer to its attack:
Opus had stopped at its own cap, so the last combat was never resolved by the other seat. (Sonnet's final "server unreachable" was me stopping the server afterwards, not a crash.) Cost: Opus 242k tokens / 68 calls, Sonnet 251k / 101 calls.
Raw notes: `findings-Opus.md`, `findings-Sonnet.md`; table log: `events.txt` (300 events, 75 passes, every one marked `ai: true`, i.e. still not distinguishing a real pass from a timeout).

Fixed by run 1's changes and confirmed working: the table read the attackers' power (nobody typed damage), unblocked damage landed, no land before the game.

## Ranked findings (this run)
1. `pass` refused ~20 times with "you already passed main 1: waiting on None" while `phase` showed the seat as the only one needed.
2. Aura/pump power ignored in combat: Kestia + Righteous Authority blocked Yedora as 4/4 and died; `*/*` creatures (Tuvasa) show 1/1. A player cannot correct it (`life` on the opponent is refused).
3. Triggered effects with no action behind them: Ainok's destroy, Whisperwood's manifest, Ransom Note's surveil, Kestia's draw, Righteous Authority's extra draw. And no way to activate abilities (Mirror Entity, Ransom Note, Kessig Wolf Run).
4. `tablectl state` printed neither `opponents` nor `incoming` (the server sends them; the client dropped them). Both players noticed.
5. A creature narrated as dead stayed on its owner's board (Ainok, after blocking): the block result was applied to the blocker's side only.
6. The first player drew on turn 1 of a 2-player game (confirmed by Opus's `begin`).
7. `block --attacker Kaust` refused (partial name); a bare `block` takes the first attacker, in list order.
8. Mana: the engine picks which lands to tap (wasted 1 mana, blocked a following cast) and which land a bounce land returns.
9. Silent seat: priority windows of 5-7 min; the other seat's turn took ~25 min with an unresponsive seat. No visible countdown, auto-passes unmarked.
10. Journal due, remote: refused ("only available on the table's laptop").
11. The phase never left "declare attackers" while damage was applied; `end` jumped to cleanup.

## Fixed after this run (commit following 49529a6)
Findings 1 (message), 4, 5, 6, 7 (partial names); 2 (aura pumps, own-name P/T); 3 in part (Righteous Authority draw, Kestia draw, and an `effect` action for draw/surveil/scry/manifest/destroy/exile/bounce that the source card must say); 9 (the pass clock is shown and auto-passes are marked). Still open: activated abilities (Mirror Entity, Wolf Run), 8 (choosing which land to tap/bounce), 10 (remote journal), 11 (phase stays at declare attackers).
